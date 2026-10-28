#!/usr/bin/env python3
"""vault-lock.py — the one vault lock (docs/specs/2026-10-15-capture-contracts.md D10, §3.5, §12 A1).

    vault-lock.py --vault <path> -- <command> [args…]   run the command under the lock
    vault-lock.py --vault <path> --probe                 print `held` or `free` (for `mnemon doctor`)
    vault-lock.py --vault <path> --locked-result <capture_id|""> -- <command> [args…]
        the same, and when the lock is held print the gateway's §3.6 RESULT block on stdout
        (status locked · error_class locked · retryable true · extract none; revision, path,
        title and hash empty; the capture_id as given) before exit 75 — so `mnemon add` and the
        gateway's own re-exec answer a RESULT parser instead of nothing (GH #30). Nothing else
        changes: the command is still exec'd, the PID stays the capture's.

The lock file is `<vault state>/lock`, where the vault state dir is
`${XDG_STATE_HOME:-$HOME/.local/state}/mnemon/vaults/<sha256(physical vault path)[:16]>/`
(the physical path = realpath, no trailing slash; the dir is created with mode 0700 — the
same rule as `mnemon_vault_state_dir` in bin/mnemon-config.sh). Acquisition is
`fcntl.flock(LOCK_EX | LOCK_NB)` on that file; `EWOULDBLOCK` → exit 75 with
`another writer holds the vault lock`. On success the descriptor's FD_CLOEXEC flag is cleared,
`MNEMON_VAULT_LOCK_FD=<fd>` and `MNEMON_VAULT_LOCK_INODE=<st_ino>` are exported, and the command
is exec'd (os.execvp) — this process *becomes* the command, so no parent survives to release
anything early. Every child that inherits the descriptor keeps the lock alive; the kernel
releases it only when the last process holding the descriptor exits.

Already under the lock (§12 A1) — the proof, applied here before acquiring and by every
participant (`cap_under_lock` in bin/lib-capture.sh, `inherited_lock_fd` in bin/mnemon-ingest.py):
`MNEMON_VAULT_LOCK_FD` names a descriptor whose `fstat` has the same `(st_dev, st_ino)` as
`stat(<vault state of THIS vault>/lock)`, and re-asserting `flock(LOCK_EX | LOCK_NB)` on it
succeeds — a no-op on the open file description that already holds the lock. `EWOULDBLOCK`
there means another description holds it → exit 75. A descriptor inherited for another vault,
a stale environment or a number recycled by a shell redirection fails the stat comparison and
is no proof: the lock is acquired afresh. The exported inode is a hint for humans, never the
proof. When the proof holds, the command is exec'd with that same descriptor (kept inheritable)
and the two variables re-exported — nothing is acquired and nothing is released.

Participants never call LOCK_UN and never unlink or replace the lock file (unlocking a shared
description would release it for every descendant); the lock ends when the last descriptor
closes. `--probe` opens a private descriptor, tries the lock and closes the descriptor at once
(closing releases whatever it took); it never execs and never keeps anything.

Exit codes: 75 lock held · 2 usage · 126/127 the command could not be exec'd · otherwise the
command's own status (it replaces this process). Stdlib only, python 3.9.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import sys

LOCK_HELD_EXIT = 75


def vault_state_dir(vault: str) -> str:
    """`$XDG_STATE_HOME/mnemon/vaults/<sha256(physical vault path)[:16]>` — not created here."""
    phys = os.path.realpath(vault)
    while len(phys) > 1 and phys.endswith("/"):
        phys = phys[:-1]
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state_home, "mnemon", "vaults", hashlib.sha256(phys.encode("utf-8")).hexdigest()[:16])


def lock_path(vault: str) -> str:
    return os.path.join(vault_state_dir(vault), "lock")


def ensure_state_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def open_lock(vault: str) -> int:
    state = vault_state_dir(vault)
    ensure_state_dir(state)
    return os.open(os.path.join(state, "lock"), os.O_RDWR | os.O_CREAT, 0o600)


def try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return False
        raise
    return True


def inherited_descriptor(path: str):
    """The §12 A1 proof for the lock file at `path`.

    Returns (fd, True) when the environment's descriptor holds this vault's lock, (fd, False) when
    the descriptor is open on this lock file but another description holds the lock (EWOULDBLOCK),
    and (None, False) when the descriptor is no proof at all (unset, closed, another file).
    """
    raw = os.environ.get("MNEMON_VAULT_LOCK_FD", "")
    if not raw:
        return None, False
    try:
        fd = int(raw)
        st = os.fstat(fd)
        on_disk = os.stat(path)
    except (ValueError, OSError):
        return None, False
    if (st.st_dev, st.st_ino) != (on_disk.st_dev, on_disk.st_ino):
        return None, False
    return fd, try_lock(fd)


def keep_descriptor(fd: int) -> None:
    """Keep the descriptor across exec and tell the command where it is."""
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
    os.environ["MNEMON_VAULT_LOCK_FD"] = str(fd)
    os.environ["MNEMON_VAULT_LOCK_INODE"] = str(os.fstat(fd).st_ino)


def held_message(vault: str) -> str:
    return f"vault-lock: another writer holds the vault lock ({lock_path(vault)}); retry when it finishes"


def locked_result(capture_id: str) -> None:
    """The gateway's RESULT block for a refusal on the vault lock (capture contracts §3.6, GH #30)."""
    cid = "".join(ch for ch in (capture_id or "") if ch >= " " and ch != "\x7f")
    for line in ("RESULT:capture_id=" + cid, "RESULT:revision=", "RESULT:path=", "RESULT:status=locked",
                 "RESULT:title=", "RESULT:source_sha256=", "RESULT:extract=none", "RESULT:error_class=locked",
                 "RESULT:retryable=true"):
        print(line)
    sys.stdout.flush()


def usage(msg: str = "") -> int:
    if msg:
        print(f"vault-lock: {msg}", file=sys.stderr)
    print("usage: vault-lock.py --vault <path> [--locked-result <capture_id>] -- <command> [args…] | "
          "vault-lock.py --vault <path> --probe", file=sys.stderr)
    return 2


def main(argv: list) -> int:
    vault = ""
    probe = False
    result_cid = None
    command: list = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--vault":
            if i + 1 >= len(argv):
                return usage("--vault requires a path")
            vault = argv[i + 1]
            i += 2
        elif a == "--probe":
            probe = True
            i += 1
        elif a == "--locked-result":
            if i + 1 >= len(argv):
                return usage("--locked-result requires a capture id (may be empty)")
            result_cid = argv[i + 1]
            i += 2
        elif a == "--":
            command = argv[i + 1:]
            break
        else:
            return usage(f"unknown argument: {a}")
    if not vault:
        return usage("--vault is required")
    if probe and command:
        return usage("--probe takes no command")
    if probe and result_cid is not None:
        return usage("--locked-result goes with a command")
    if not probe and not command:
        return usage("a command after -- is required")

    if probe:
        fd = open_lock(vault)
        print("free" if try_lock(fd) else "held")
        os.close(fd)  # a private descriptor: closing it releases what it took; never LOCK_UN
        return 0

    fd, held = inherited_descriptor(lock_path(vault))
    if fd is not None:
        if not held:
            print(held_message(vault), file=sys.stderr)
            if result_cid is not None:
                locked_result(result_cid)
            return LOCK_HELD_EXIT
    else:
        fd = open_lock(vault)
        if not try_lock(fd):
            print(held_message(vault), file=sys.stderr)
            if result_cid is not None:
                locked_result(result_cid)
            return LOCK_HELD_EXIT
    keep_descriptor(fd)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"vault-lock: cannot exec {command[0]}: {exc.strerror}", file=sys.stderr)
        return 127 if exc.errno == errno.ENOENT else 126
    return 0  # unreachable: exec replaced this process


if __name__ == "__main__":
    sys.exit(main(sys.argv))
