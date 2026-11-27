"""capture.proc — supervised children and the one cancellation hierarchy (spec D11, §12 A9).

An owner starts every direct child in a new session (its own process group) and tracks it. On its own
deadline it SIGTERMs the child's group, waits a 2 s grace, SIGKILLs what remains and reaps it. On SIGTERM
from its parent the installed handler tears every tracked group down the same way and only then exits —
so site → fetch → Chrome and depth → probe → renderer are torn down from the top, never orphaned.

`Deadline` is the budget an owner passes down; `run` bounds one child by the smaller of its timeout and the
remaining deadline. `watch_parent` is the worker's side of the hierarchy: a SIGKILLed owner (whose handler
never ran) leaves no worker behind — the worker notices its parent is gone and exits. The inherited vault-lock descriptor never reaches a child (close_fds) and the
`MNEMON_VAULT_LOCK_*` variables are scrubbed from its environment: capture-layer children never write into
the vault (D11).

The image worker (capture layer A20, section 21.6) needs three more things, each a new argument or a new
function, with the behaviour above as the default. `run_bounded` reads a child's stdout and stderr through
pipes in chunks and keeps a bounded number of bytes of each; a child over a cap is ended. It can start the
child inside the caller's own process group (`own_group=True`) and then ends it by its pid, never by a
group. With `check_group=True` it looks at the child's process group after the child ended and tears down
what is left. `watch_parent(own_group=True)` is for a worker that leads a process group shared with the
commands it runs: on the loss of its supervisor it SIGKILLs its own group. Stdlib only, python 3.9.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

TERM_GRACE = 2.0
KILL_GRACE = 2.0
BOUNDED_MAX = 1024 * 1024   # bytes kept of one stream by run_bounded
GROUP_SETTLE = 0.5          # how long a just-ended child's group may still answer before it counts as left behind
READ_CHUNK = 65536

_children = {}      # pid → Popen
_own_group = set()  # pids of tracked children that share this process's group: ended by pid, never by group
_lock = threading.Lock()
_installed = False


class Deadline:
    """An absolute point in monotonic time; `None` seconds means no deadline."""

    def __init__(self, seconds=None, end=None):
        if end is not None:
            self.end = end
        elif seconds is None:
            self.end = None
        else:
            self.end = time.monotonic() + max(0.0, float(seconds))

    @classmethod
    def never(cls) -> "Deadline":
        return cls(None)

    def remaining(self) -> Optional[float]:
        if self.end is None:
            return None
        return max(0.0, self.end - time.monotonic())

    def expired(self) -> bool:
        return self.end is not None and time.monotonic() >= self.end

    def bound(self, seconds) -> Optional[float]:
        """The smaller of a timeout and the remaining budget (None = unbounded)."""
        rem = self.remaining()
        if seconds is None:
            return rem
        if rem is None:
            return float(seconds)
        return min(float(seconds), rem)

    def sub(self, seconds) -> "Deadline":
        """A child deadline: at most `seconds` from now, never past this one."""
        b = self.bound(seconds)
        return Deadline(None) if b is None else Deadline(b)


class Result:
    __slots__ = ("rc", "stdout", "stderr", "timed_out", "elapsed_ms", "started")

    def __init__(self, rc, stdout, stderr, timed_out, elapsed_ms, started=True):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.elapsed_ms = elapsed_ms
        self.started = started


class BoundedResult(Result):
    """The outcome of `run_bounded`. `by_deadline`: the bound that passed was the deadline's, not the
    command's own `timeout`. `capped`: the stream ("stdout" or "stderr") that passed its cap, else None.
    `members_left`: the child ended by itself and its process group still had a member (`check_group`)."""
    __slots__ = ("by_deadline", "capped", "members_left")

    def __init__(self, rc, stdout, stderr, timed_out, elapsed_ms, started=True, by_deadline=False, capped=None,
                 members_left=False):
        Result.__init__(self, rc, stdout, stderr, timed_out, elapsed_ms, started)
        self.by_deadline = by_deadline
        self.capped = capped
        self.members_left = members_left


def child_env(extra=None) -> dict:
    """A child's environment: the vault lock variables scrubbed (D11), `extra` applied on top."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MNEMON_VAULT_LOCK_")}
    if extra:
        env.update(extra)
    return env


def hang() -> None:
    """The test hooks' hang (MNEMON_TEST_CONVERT_HANG=1, MNEMON_TEST_MAP_HANG=1, under MNEMON_TEST_HOOKS=1):
    catastrophic backtracking inside the regex engine — CPU-bound C code that returns to the interpreter,
    its signal handlers and its threads only after an astronomical number of steps, the shape of a quadratic
    scan. The proof that the supervisor's kill, not the worker's cooperation, ends the phase (G25)."""
    re.match(r"(a+)+$", "a" * 64 + "b")


def close_lock_fd() -> None:
    """Close the inherited vault-lock descriptor (MNEMON_VAULT_LOCK_FD) in a capture-layer process."""
    fd = os.environ.get("MNEMON_VAULT_LOCK_FD")
    if not fd:
        return
    try:
        os.close(int(fd))
    except (OSError, ValueError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child's process group, wait the grace, SIGKILL what remains, reap the child."""
    pgid = proc.pid
    for sig, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, KILL_GRACE)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        end = time.monotonic() + grace
        while _group_alive(pgid) and time.monotonic() < end:
            time.sleep(0.05)
        if not _group_alive(pgid):
            break
    try:
        proc.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        pass


def kill_pid(proc: subprocess.Popen) -> None:
    """SIGTERM one child by its pid, wait the grace, SIGKILL it, reap it. For a child that shares the
    caller's process group: a signal to that group would end the caller too."""
    for sig, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, KILL_GRACE)):
        if proc.poll() is not None:
            return
        try:
            proc.send_signal(sig)
        except OSError:
            pass
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def reap_group(pgid: int) -> None:
    """Empty a process group whose leader has ended: SIGTERM, the grace, SIGKILL, until no member answers."""
    for sig, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, KILL_GRACE)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        end = time.monotonic() + grace
        while _group_alive(pgid) and time.monotonic() < end:
            time.sleep(0.05)
        if not _group_alive(pgid):
            return


def terminate_all() -> None:
    """Tear down every tracked child (the owner's own deadline, or a SIGTERM from its parent): a child in a
    group of its own by that group, a child in the caller's group by its pid."""
    with _lock:
        procs = [(proc, proc.pid in _own_group) for proc in _children.values()]
    for proc, own in procs:
        if proc.poll() is None:
            if own:
                kill_pid(proc)
            else:
                kill_group(proc)
        with _lock:
            _children.pop(proc.pid, None)
            _own_group.discard(proc.pid)


def watch_parent(interval: float = 0.25, exit_code: int = 1, parent_pid: Optional[int] = None,
                 own_group: bool = False) -> None:
    """A worker never outlives its supervisor (D11): a daemon thread polls the parent pid every `interval`
    seconds (at most one second) and, when it is not the supervisor's — the supervisor died without its own
    teardown (a SIGKILL; a SIGTERM runs the handler above) and the worker, in its own session, was reparented
    — SIGKILLs every tracked child group and exits the process at once. `parent_pid` is the supervisor's pid
    as the supervisor passed it: with it the check is exact and runs once before the thread starts, so a
    worker whose supervisor died while the worker was still importing (the race a loaded CI runner hit: the
    reparented pid would have been read as "the parent" and never changed) exits at once; without it the
    pid read now is taken for the supervisor's. The thread needs the interpreter: python code and subprocess
    waits yield to it within milliseconds; C code that never returns (the regex hang of `hang`) does not,
    and that case is the supervisor's kill to end.

    `own_group=True` is for a worker that leads a process group and runs its commands inside that group
    (the image worker, capture layer section 21.6): its tracked children lead no group, so a signal to
    `child.pid` as a group would reach nobody and the commands would outlive the worker. On the loss of the
    parent the caller's own group gets SIGKILL — the worker and everything it started end together. The
    caller must lead its group: in any other process the group may be the supervisor's or the gateway's, and
    this function raises ValueError instead of arming a signal to it."""
    parent = parent_pid if parent_pid is not None else os.getppid()
    if own_group and os.getpgrp() != os.getpid():
        raise ValueError("watch_parent(own_group=True): this process does not lead its process group")

    def _orphaned() -> None:
        if own_group:
            try:
                os.killpg(os.getpgrp(), signal.SIGKILL)     # this process is a member: nothing runs after it
            except OSError:
                pass
            os._exit(exit_code)
        with _lock:
            procs = [(child, child.pid in _own_group) for child in _children.values()]
        for child, own in procs:
            try:
                if own:
                    os.kill(child.pid, signal.SIGKILL)
                else:
                    os.killpg(child.pid, signal.SIGKILL)
            except OSError:
                pass
        os._exit(exit_code)

    if os.getppid() != parent:
        _orphaned()

    def _watch():
        while True:
            time.sleep(interval)
            if os.getppid() != parent:
                _orphaned()

    threading.Thread(target=_watch, name="parent-watch", daemon=True).start()


def install_term_handler(exit_code: int = 143) -> None:
    """On SIGTERM: tear down the tracked children, then exit (A9). Installed once per process."""
    global _installed
    if _installed:
        return
    _installed = True

    def _handler(signum, frame):
        terminate_all()
        sys.stderr.flush()
        os._exit(exit_code)

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass    # not the main thread: the caller supervises without a handler


def run(argv, *, timeout=None, deadline: Optional[Deadline] = None, stdin: Optional[bytes] = None,
        env=None, cwd=None) -> Result:
    """Run one child in its own process group, bounded by `timeout` and the remaining `deadline`."""
    bound = timeout
    if deadline is not None:
        bound = deadline.bound(timeout)
    if bound is not None and bound <= 0:
        return Result(None, b"", b"", True, 0, started=False)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                env=child_env(env), cwd=cwd, close_fds=True)
    except OSError as exc:
        return Result(None, b"", ("cannot start %s: %s" % (argv[0], exc.strerror or exc)).encode("utf-8"),
                      False, 0, started=False)
    with _lock:
        _children[proc.pid] = proc
    timed_out = False
    try:
        try:
            out, err = proc.communicate(input=stdin, timeout=bound)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_group(proc)
            try:
                out, err = proc.communicate(timeout=KILL_GRACE)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                out, err = b"", b""
    finally:
        with _lock:
            _children.pop(proc.pid, None)
    elapsed = int((time.monotonic() - start) * 1000)
    return Result(proc.returncode, out or b"", err or b"", timed_out, elapsed)


class _Drain(threading.Thread):
    """Read one pipe in chunks to its end; keep at most `cap` bytes; set `over` when the cap is passed."""

    def __init__(self, stream, cap: int, over: threading.Event, name: str):
        threading.Thread.__init__(self, name=name, daemon=True)
        self.stream = stream
        self.cap = cap
        self.over = over
        self.kept = []
        self.size = 0
        self.passed = False

    def run(self) -> None:
        fd = self.stream.fileno()
        try:
            while True:
                chunk = os.read(fd, READ_CHUNK)
                if not chunk:
                    return
                room = self.cap - self.size
                if room > 0:
                    self.kept.append(chunk[:room])
                    self.size += min(len(chunk), room)
                if len(chunk) > room:
                    self.passed = True      # the rest is read and dropped, so the child never blocks on the pipe
                    self.over.set()
        except (OSError, ValueError):
            return

    def data(self) -> bytes:
        return b"".join(self.kept)


def _feed(stream, data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def run_bounded(argv, *, timeout=None, deadline: Optional[Deadline] = None, stdin: Optional[bytes] = None,
                env=None, cwd=None, max_stdout: int = BOUNDED_MAX, max_stderr: int = BOUNDED_MAX,
                own_group: bool = False, check_group: bool = False) -> BoundedResult:
    """Run one child with bounded reads (capture layer section 21.6 «Bounded reads»).

    Stdin is `/dev/null`, or a pipe that a thread fills with `stdin` and closes. Stdout and stderr are pipes,
    each read in chunks by its own thread; at most `max_stdout` and `max_stderr` bytes are kept, and a child
    whose stream passes its cap is ended (`capped` names the stream). The child is bounded by the smaller of
    `timeout` and the remaining `deadline`; `by_deadline` says which of the two passed.

    `own_group=False`: the child leads a new session, is tracked, and is ended by its process group.
    `own_group=True`: the child stays in the caller's process group and is ended by its pid alone — the
    image worker runs its commands this way, so one signal to the worker's group ends them all.
    `check_group=True` (a child in a group of its own): when the child ended by itself and its group still
    has a member after a short settling time (an exited member that its new parent has not reaped yet still
    answers a signal for an instant), the group is torn down and `members_left` is set.
    """
    bound = timeout
    by_deadline = False
    if deadline is not None:
        remaining = deadline.remaining()
        bound = deadline.bound(timeout)
        by_deadline = remaining is not None and (timeout is None or remaining <= float(timeout))
    if bound is not None and bound <= 0:
        return BoundedResult(None, b"", b"", True, 0, started=False, by_deadline=by_deadline)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=not own_group,
                                env=child_env(env), cwd=cwd, close_fds=True)
    except OSError as exc:
        return BoundedResult(None, b"", ("cannot start %s: %s" % (argv[0], exc.strerror or exc)).encode("utf-8"),
                             False, 0, started=False)
    with _lock:
        _children[proc.pid] = proc
        if own_group:
            _own_group.add(proc.pid)
    over = threading.Event()
    out = _Drain(proc.stdout, max_stdout, over, "bounded-stdout")
    err = _Drain(proc.stderr, max_stderr, over, "bounded-stderr")
    out.start()
    err.start()
    if stdin is not None:
        threading.Thread(target=_feed, args=(proc.stdin, stdin), name="bounded-stdin", daemon=True).start()
    end = None if bound is None else start + bound
    timed_out = False
    capped = None
    ended_by_runner = False
    try:
        while True:
            if over.is_set():
                capped = "stdout" if out.passed else "stderr"
                break
            wait = 0.05
            if end is not None:
                left = end - time.monotonic()
                if left <= 0:
                    timed_out = True
                    break
                wait = min(wait, left)
            try:
                proc.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        if proc.poll() is None:
            ended_by_runner = True
            if own_group:
                kill_pid(proc)
            else:
                kill_group(proc)
        elif over.is_set() and capped is None:
            capped = "stdout" if out.passed else "stderr"   # the child ended in the same instant its stream passed the cap
    finally:
        with _lock:
            _children.pop(proc.pid, None)
            _own_group.discard(proc.pid)
    # the pipes close when the child and whoever inherited them are gone; a survivor that holds one open must
    # not hold the runner: the threads are daemons and are given a bounded time to finish
    out.join(KILL_GRACE)
    err.join(KILL_GRACE)
    if capped is None and over.is_set():
        capped = "stdout" if out.passed else "stderr"
    members_left = False
    if check_group and not own_group:
        pgid = proc.pid
        settle = time.monotonic() + GROUP_SETTLE
        while _group_alive(pgid) and time.monotonic() < settle:
            time.sleep(0.02)
        if _group_alive(pgid):
            members_left = not ended_by_runner
            reap_group(pgid)
    for thread in (out, err):
        if thread.is_alive():
            continue    # a survivor holds the pipe: the thread still reads it, and a closed descriptor could be reused
        try:
            thread.stream.close()
        except (OSError, ValueError):
            pass
    elapsed = int((time.monotonic() - start) * 1000)
    return BoundedResult(proc.returncode, out.data(), err.data(), timed_out, elapsed,
                         by_deadline=by_deadline and timed_out, capped=capped, members_left=members_left)
