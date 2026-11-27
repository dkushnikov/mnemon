"""capture.proc — the bounded runner, the pid kill, the group check and the own-group parent watch
(capture layer A20, section 21.6). Hermetic: every child is this python or /bin/sh. Stdlib only, python 3.9.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from _helpers import BIN, PY, pid_alive, wait_gone

from capture import proc


def py(code: str) -> list:
    return [PY, "-c", code]


def wait_file(path: str, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.02)
    return False


def read_pids(path: str) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        return [int(x) for x in fh.read().split()]


class BoundedRunnerTests(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mnemon-proc-")
        self.to_kill = []

    def tearDown(self):
        for pid in self.to_kill:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def test_streams_and_exit_status(self):
        res = proc.run_bounded(["/bin/sh", "-c", "echo out; echo err >&2; exit 3"])
        self.assertIsInstance(res, proc.BoundedResult)
        self.assertEqual((res.rc, res.stdout, res.stderr), (3, b"out\n", b"err\n"))
        self.assertTrue(res.started)
        self.assertFalse(res.timed_out)
        self.assertFalse(res.by_deadline)
        self.assertIsNone(res.capped)
        self.assertFalse(res.members_left)

    def test_stdin_default_is_dev_null(self):
        res = proc.run_bounded(py("import sys; sys.stdout.write(repr(sys.stdin.read()))"))
        self.assertEqual((res.rc, res.stdout), (0, b"''"))

    def test_stdin_bytes_reach_the_child(self):
        data = b"x" * 300000 + b"\n"
        res = proc.run_bounded(py("import sys; d = sys.stdin.buffer.read(); sys.stdout.write(str(len(d)))"), stdin=data)
        self.assertEqual((res.rc, res.stdout), (0, str(len(data)).encode("ascii")))

    def test_a_child_that_never_reads_its_stdin_does_not_block_the_runner(self):
        start = time.monotonic()
        res = proc.run_bounded(py("print('done')"), stdin=b"y" * 2000000)
        self.assertEqual((res.rc, res.stdout.strip()), (0, b"done"))
        self.assertLess(time.monotonic() - start, 5.0)

    def test_stdout_over_the_cap_ends_the_child_and_keeps_the_cap(self):
        code = "import sys, time\nwhile True:\n    sys.stdout.write('a' * 65536)\n    sys.stdout.flush()\n"
        start = time.monotonic()
        res = proc.run_bounded(py(code), max_stdout=200000, timeout=30)
        self.assertEqual(res.capped, "stdout")
        self.assertEqual(len(res.stdout), 200000)
        self.assertFalse(res.timed_out)
        self.assertIsNotNone(res.rc)
        self.assertNotEqual(res.rc, 0)
        self.assertLess(time.monotonic() - start, 10.0)

    def test_stderr_over_the_cap(self):
        code = "import sys\nwhile True:\n    sys.stderr.write('b' * 65536)\n    sys.stderr.flush()\n"
        res = proc.run_bounded(py(code), max_stderr=100000, timeout=30)
        self.assertEqual(res.capped, "stderr")
        self.assertEqual(len(res.stderr), 100000)

    def test_output_at_the_cap_is_not_over_it(self):
        res = proc.run_bounded(py("import sys; sys.stdout.write('c' * 1000)"), max_stdout=1000)
        self.assertEqual((res.rc, len(res.stdout), res.capped), (0, 1000, None))

    def test_a_flood_that_ends_by_itself_is_still_capped(self):
        res = proc.run_bounded(py("import sys; sys.stdout.write('d' * 500000)"), max_stdout=1000)
        self.assertEqual(res.capped, "stdout")
        self.assertEqual(len(res.stdout), 1000)

    def test_own_timeout(self):
        start = time.monotonic()
        res = proc.run_bounded(py("import time; time.sleep(60)"), timeout=0.5)
        took = time.monotonic() - start
        self.assertTrue(res.timed_out)
        self.assertFalse(res.by_deadline)
        self.assertLess(took, 0.5 + proc.TERM_GRACE + 2.0)
        self.assertIsNotNone(res.rc)

    def test_the_deadline_is_named_when_it_is_the_bound_that_passed(self):
        res = proc.run_bounded(py("import time; time.sleep(60)"), timeout=30, deadline=proc.Deadline(0.5))
        self.assertTrue(res.timed_out)
        self.assertTrue(res.by_deadline)

    def test_the_own_timeout_is_named_under_a_longer_deadline(self):
        res = proc.run_bounded(py("import time; time.sleep(60)"), timeout=0.5, deadline=proc.Deadline(30))
        self.assertTrue(res.timed_out)
        self.assertFalse(res.by_deadline)

    def test_a_passed_deadline_starts_nothing(self):
        mark = os.path.join(self.work, "started")
        res = proc.run_bounded(py("open(%r, 'w').close()" % mark), deadline=proc.Deadline(0))
        self.assertFalse(res.started)
        self.assertTrue(res.timed_out)
        self.assertTrue(res.by_deadline)
        self.assertFalse(os.path.exists(mark))

    def test_a_command_that_cannot_start(self):
        res = proc.run_bounded([os.path.join(self.work, "no-such-command")])
        self.assertFalse(res.started)
        self.assertIsNone(res.rc)
        self.assertFalse(res.timed_out)
        self.assertIn(b"cannot start", res.stderr)

    def test_a_new_group_by_default_and_the_callers_group_on_request(self):
        res = proc.run_bounded(py("import os; print(os.getpgrp() == os.getpid())"))
        self.assertEqual(res.stdout.strip(), b"True")
        res = proc.run_bounded(py("import os; print(os.getpgrp())"), own_group=True)
        self.assertEqual(int(res.stdout.strip()), os.getpgrp())

    def test_the_vault_lock_variables_never_reach_the_child_and_extra_ones_do(self):
        old = os.environ.get("MNEMON_VAULT_LOCK_FD")
        os.environ["MNEMON_VAULT_LOCK_FD"] = "9"
        try:
            code = "import os; print(os.environ.get('MNEMON_VAULT_LOCK_FD'), os.environ.get('CAPTURE_PROC_EXTRA'))"
            res = proc.run_bounded(py(code), env={"CAPTURE_PROC_EXTRA": "yes"}, own_group=True)
        finally:
            if old is None:
                del os.environ["MNEMON_VAULT_LOCK_FD"]
            else:
                os.environ["MNEMON_VAULT_LOCK_FD"] = old
        self.assertEqual(res.stdout.strip(), b"None yes")

    def test_an_own_group_child_over_its_limit_is_ended_by_its_pid_and_a_pipe_holder_does_not_hold_the_runner(self):
        # the child starts a grandchild that inherits the pipes and sleeps: the runner ends the child by pid,
        # the grandchild lives on in the caller's group, and the runner still returns
        pids = os.path.join(self.work, "pids")
        code = ("import os, subprocess, sys, time\n"
                "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "open(%r, 'w').write('%%d %%d' %% (os.getpid(), g.pid))\n"
                "time.sleep(60)\n" % pids)
        start = time.monotonic()
        res = proc.run_bounded(py(code), timeout=1.0, own_group=True)
        took = time.monotonic() - start
        child, grandchild = read_pids(pids)
        self.to_kill.append(grandchild)
        self.assertTrue(res.timed_out)
        self.assertLess(took, 1.0 + proc.TERM_GRACE + proc.KILL_GRACE * 2 + 2.0)
        self.assertTrue(wait_gone(child, 3.0), "the child is gone")
        self.assertTrue(pid_alive(grandchild), "the grandchild was not signalled: it shares the caller's group")
        self.assertTrue(pid_alive(os.getpid()))

    def test_a_group_with_a_member_left_is_torn_down_and_reported(self):
        pids = os.path.join(self.work, "pids")
        code = ("import os, subprocess, sys\n"
                "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
                "                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "open(%r, 'w').write('%%d %%d' %% (os.getpid(), g.pid))\n" % pids)
        res = proc.run_bounded(py(code), timeout=30, check_group=True)
        child, grandchild = read_pids(pids)
        self.to_kill.append(grandchild)
        self.assertEqual(res.rc, 0)
        self.assertTrue(res.members_left)
        self.assertFalse(res.timed_out)
        self.assertTrue(wait_gone(grandchild, 3.0), "the member left behind was ended")

    def test_a_clean_group_is_not_reported(self):
        res = proc.run_bounded(py("print('x')"), check_group=True)
        self.assertEqual((res.rc, res.members_left), (0, False))

    def test_a_member_that_ignores_sigterm_is_killed(self):
        pids = os.path.join(self.work, "pids")
        inner = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        code = ("import os, subprocess, sys\n"
                "g = subprocess.Popen([sys.executable, '-c', %r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "open(%r, 'w').write('%%d %%d' %% (os.getpid(), g.pid))\n" % (inner, pids))
        res = proc.run_bounded(py(code), timeout=30, check_group=True)
        child, grandchild = read_pids(pids)
        self.to_kill.append(grandchild)
        self.assertTrue(res.members_left)
        self.assertTrue(wait_gone(grandchild, 3.0))

    def test_a_group_killed_for_its_timeout_is_not_called_left_behind(self):
        code = ("import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "time.sleep(60)\n")
        res = proc.run_bounded(py(code), timeout=0.5, check_group=True)
        self.assertTrue(res.timed_out)
        self.assertFalse(res.members_left)

    def test_run_keeps_its_behaviour(self):
        res = proc.run(py("import os; print(os.getpgrp() == os.getpid())"))
        self.assertIsInstance(res, proc.Result)
        self.assertNotIsInstance(res, proc.BoundedResult)
        self.assertEqual((res.rc, res.stdout.strip()), (0, b"True"))


SUPERVISOR = r"""
import os, subprocess, sys, time
sys.path.insert(0, {bin!r})
worker = subprocess.Popen([sys.executable, "-c", {worker!r}, str(os.getpid()), {pids!r}], start_new_session=True)
time.sleep(600)
"""

WORKER = r"""
import os, subprocess, sys, time
sys.path.insert(0, {bin!r})
from capture import proc
parent = int(sys.argv[1])
proc.watch_parent(parent_pid=parent, own_group=True)
command = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
with open(sys.argv[2] + ".tmp", "w") as fh:
    fh.write("%d %d" % (os.getpid(), command.pid))
os.rename(sys.argv[2] + ".tmp", sys.argv[2])
time.sleep(600)
"""


class OwnGroupWatchTests(unittest.TestCase):

    def test_a_worker_that_leads_its_group_takes_its_commands_with_it_when_the_supervisor_is_killed(self):
        work = tempfile.mkdtemp(prefix="mnemon-proc-")
        pids = os.path.join(work, "pids")
        worker = WORKER.format(bin=BIN)
        sup = subprocess.Popen([PY, "-c", SUPERVISOR.format(bin=BIN, worker=worker, pids=pids)])
        try:
            self.assertTrue(wait_file(pids), "the worker started")
            worker_pid, command_pid = read_pids(pids)
            self.assertTrue(pid_alive(worker_pid) and pid_alive(command_pid))
            os.kill(sup.pid, signal.SIGKILL)
            sup.wait()
            self.assertTrue(wait_gone(worker_pid, 3.0), "the worker is gone")
            self.assertTrue(wait_gone(command_pid, 3.0), "the command in the worker's group is gone")
            with self.assertRaises(ProcessLookupError):
                os.killpg(worker_pid, 0)
        finally:
            for pid in (sup.pid,):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            if os.path.exists(pids):
                for pid in read_pids(pids):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass

    def test_a_process_that_does_not_lead_its_group_is_refused(self):
        code = ("import os, sys\nsys.path.insert(0, %r)\nfrom capture import proc\n"
                "try:\n    proc.watch_parent(own_group=True)\nexcept ValueError as exc:\n    print('refused')\n" % BIN)
        out = subprocess.run([PY, "-c", code], capture_output=True, timeout=30)   # a child shares this process's group
        self.assertEqual(out.stdout.strip(), b"refused", out.stderr)

    def test_a_sigterm_during_the_runners_cleanup_still_tears_the_childs_group_down(self):
        # the child exits at once and leaves a grandchild that holds its pipes: the runner is in its cleanup (the
        # drains joined, the group checked) when the SIGTERM comes; the child must still be tracked there, and the
        # teardown must reach the group of a leader that has already exited
        work = tempfile.mkdtemp(prefix="mnemon-proc-")
        pids = os.path.join(work, "pids")
        child = ("import os, subprocess, sys\n"
                 "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                 "open(%r, 'w').write('%%d %%d' %% (os.getpid(), g.pid))\n"
                 "os.rename(%r, %r)\n" % (pids + ".tmp", pids + ".tmp", pids))
        parent = ("import sys\nsys.path.insert(0, %r)\nfrom capture import proc\n"
                  "proc.install_term_handler()\n"
                  "proc.run_bounded([sys.executable, '-c', %r], timeout=30, check_group=True)\n"
                  "sys.exit(0)\n" % (BIN, child))
        p = subprocess.Popen([PY, "-c", parent], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        grandchild = None
        try:
            self.assertTrue(wait_file(pids), "the child started its grandchild")
            child_pid, grandchild = read_pids(pids)
            self.assertTrue(wait_gone(child_pid, 5.0), "the child exited and was reaped: the runner is in its cleanup")
            self.assertTrue(pid_alive(grandchild), "the grandchild still holds the pipes")
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=20), 143)
            self.assertTrue(wait_gone(grandchild, proc.TERM_GRACE + proc.KILL_GRACE + 1.0),
                            "the grandchild in the ended child's group is gone")
        finally:
            p.kill()
            p.wait()
            if grandchild:
                try:
                    os.kill(grandchild, signal.SIGKILL)
                except OSError:
                    pass

    def test_terminate_all_reaps_the_group_of_a_tracked_child_whose_leader_has_exited(self):
        code = ("import os, subprocess, sys, time\nsys.path.insert(0, %r)\nfrom capture import proc\n"
                "c = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys, time\\n"
                "g = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(60)\"], stderr=subprocess.DEVNULL)\\n"
                "print(g.pid, flush=True)'], stdout=subprocess.PIPE, start_new_session=True)\n"
                "grandchild = int(c.stdout.readline())\n"
                "c.wait()\n"
                "proc._children[c.pid] = c\n"
                "proc.terminate_all()\n"
                "time.sleep(0.2)\n"
                "try:\n    os.kill(grandchild, 0)\n    print('alive', grandchild)\n"
                "except ProcessLookupError:\n    print('gone', grandchild)\n" % BIN)
        out = subprocess.run([PY, "-c", code], capture_output=True, timeout=30)
        words = out.stdout.split()
        if len(words) == 2:
            try:
                os.kill(int(words[1]), signal.SIGKILL)
            except OSError:
                pass
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(words[:1], [b"gone"], "terminate_all tore down the group of a leader that had exited")

    def test_terminate_all_ends_an_own_group_child_by_its_pid(self):
        code = ("import os, subprocess, sys, threading, time\nsys.path.insert(0, %r)\nfrom capture import proc\n"
                "def later():\n    time.sleep(0.5)\n    proc.terminate_all()\n"
                "threading.Thread(target=later, daemon=True).start()\n"
                "res = proc.run_bounded([sys.executable, '-c', 'import time; time.sleep(60)'], own_group=True, timeout=30)\n"
                "print(res.rc, res.timed_out)\n" % BIN)
        start = time.monotonic()
        out = subprocess.run([PY, "-c", code], capture_output=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), [str(-signal.SIGTERM).encode("ascii"), b"False"])
        self.assertLess(time.monotonic() - start, 10.0)


if __name__ == "__main__":
    unittest.main()
