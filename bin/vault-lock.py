#!/usr/bin/env python3
"""vault-lock.py — the one vault lock (docs/specs/2026-10-15-capture-contracts.md D10, §3.5).

    vault-lock.py --vault <path> -- <command> [args…]   acquire, then exec the command under the lock
    vault-lock.py --vault <path> --probe                 print `held` or `free` (for `mnemon doctor`)

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

How a process checks that it is already under the lock (the inherited-descriptor rule):
`MNEMON_VAULT_LOCK_FD` is set and `os.fstat(int(fd)).st_ino == int(MNEMON_VAULT_LOCK_INODE)`
(the descriptor is open on the lock file, so the lock the kernel attached to it is this vault's
and is still held). A mismatch — a stale environment, a descriptor that belongs to another vault,
a number recycled by a shell redirection — means the process is NOT under the lock and must
acquire on its own. bash does the same check with one python call; bin/lib-capture.sh keeps it as
`capture_under_lock`. Nothing else is a valid proof: not the presence of the variable, not a pid
file, not the age of anything.

`--probe` takes the lock non-blocking and releases it at once; it never execs and never keeps the
descriptor. Exit 0 either way; the word on stdout is the answer.

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


def ensure_state_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def open_lock(vault: str) -> int:
    state = vault_state_dir(vault)
    ensure_state_dir(state)
    fd = os.open(os.path.join(state, "lock"), os.O_RDWR | os.O_CREAT, 0o600)
    return fd


def try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return False
        raise
    return True


def usage(msg: str = "") -> int:
    if msg:
        print(f"vault-lock: {msg}", file=sys.stderr)
    print("usage: vault-lock.py --vault <path> -- <command> [args…] | vault-lock.py --vault <path> --probe",
          file=sys.stderr)
    return 2


def main(argv: list) -> int:
    vault = ""
    probe = False
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
        elif a == "--":
            command = argv[i + 1:]
            break
        else:
            return usage(f"unknown argument: {a}")
    if not vault:
        return usage("--vault is required")
    if probe and command:
        return usage("--probe takes no command")
    if not probe and not command:
        return usage("a command after -- is required")

    fd = open_lock(vault)
    if probe:
        if try_lock(fd):
            fcntl.flock(fd, fcntl.LOCK_UN)
            print("free")
        else:
            print("held")
        os.close(fd)
        return 0

    if not try_lock(fd):
        print(f"vault-lock: another writer holds the vault lock ({os.path.join(vault_state_dir(vault), 'lock')}); "
              "retry when it finishes", file=sys.stderr)
        return LOCK_HELD_EXIT

    # Keep the descriptor across exec and tell the command where it is.
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
    os.environ["MNEMON_VAULT_LOCK_FD"] = str(fd)
    os.environ["MNEMON_VAULT_LOCK_INODE"] = str(os.fstat(fd).st_ino)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"vault-lock: cannot exec {command[0]}: {exc.strerror}", file=sys.stderr)
        return 127 if exc.errno == errno.ENOENT else 126
    return 0  # unreachable: exec replaced this process


if __name__ == "__main__":
    sys.exit(main(sys.argv))
