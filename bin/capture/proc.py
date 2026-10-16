"""capture.proc — supervised children and the one cancellation hierarchy (spec D11, §12 A9).

An owner starts every direct child in a new session (its own process group) and tracks it. On its own
deadline it SIGTERMs the child's group, waits a 2 s grace, SIGKILLs what remains and reaps it. On SIGTERM
from its parent the installed handler tears every tracked group down the same way and only then exits —
so site → fetch → Chrome and depth → probe → renderer are torn down from the top, never orphaned.

`Deadline` is the budget an owner passes down; `run` bounds one child by the smaller of its timeout and the
remaining deadline. The inherited vault-lock descriptor never reaches a child (close_fds) and the
`MNEMON_VAULT_LOCK_*` variables are scrubbed from its environment: capture-layer children never write into
the vault (D11). Stdlib only, python 3.9.
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

_children = {}      # pid → Popen
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


def terminate_all() -> None:
    """Tear down every tracked child group (the owner's own deadline, or a SIGTERM from its parent)."""
    with _lock:
        procs = list(_children.values())
    for proc in procs:
        if proc.poll() is None:
            kill_group(proc)
        with _lock:
            _children.pop(proc.pid, None)


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
