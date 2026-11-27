#!/usr/bin/env python3
"""claude-wrap.py — the live image gate's stand-in for `claude` (capture layer §21.13: the real client's lifecycle).

The gate passes this file to the worker as `--claude`. Before anything else it writes one JSON record to the
path in IMAGE_GATE_RECORD: its pid, its parent, its process group and session, the NAMES (never the values)
of the `MNEMON_VAULT_LOCK_*` variables it sees, and every descriptor it has open, each with the identity of
the open file (`st_dev`, `st_ino`) and its kind. Then it replaces itself by exec with the real client named in
IMAGE_GATE_CLAUDE — the same pid, the same process group, the same descriptors and the same arguments — with
the two IMAGE_GATE_* variables removed from the environment. It reads nothing of stdin and writes nothing on
stdout: the reader's stream is the real client's alone.

Exit 127, before any exec, when either variable is missing. Stdlib only, python 3.9.
"""
from __future__ import annotations

import json
import os
import stat
import sys

LOCK_PREFIX = "MNEMON_VAULT_LOCK_"


def _kind(mode: int) -> str:
    for test, name in ((stat.S_ISREG, "file"), (stat.S_ISFIFO, "pipe"), (stat.S_ISSOCK, "socket"),
                       (stat.S_ISCHR, "char"), (stat.S_ISDIR, "dir"), (stat.S_ISBLK, "block")):
        if test(mode):
            return name
    return "other"


def open_descriptors():
    """[[fd, st_dev, st_ino, kind], …] of every open descriptor — from /dev/fd (macOS) or /proc/self/fd
    (Linux), else by fstat over the first 1024 numbers. The listing's own directory descriptor is closed by
    the time each entry is checked, so it drops out."""
    numbers = None
    for directory in ("/dev/fd", "/proc/self/fd"):
        try:
            numbers = sorted(int(n) for n in os.listdir(directory) if n.isdigit())
            break
        except OSError:
            continue
    if numbers is None:
        numbers = list(range(1024))
    out = []
    for fd in numbers:
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        out.append([fd, st.st_dev, st.st_ino, _kind(st.st_mode)])
    return out


def main() -> int:
    record = os.environ.pop("IMAGE_GATE_RECORD", "")
    real = os.environ.pop("IMAGE_GATE_CLAUDE", "")
    if not record or not real:
        sys.stderr.write("claude-wrap: IMAGE_GATE_RECORD and IMAGE_GATE_CLAUDE are required\n")
        return 127
    doc = {"pid": os.getpid(), "ppid": os.getppid(), "pgid": os.getpgrp(), "sid": os.getsid(0),
           "lock_vars": sorted(k for k in os.environ if k.startswith(LOCK_PREFIX)),
           "fds": open_descriptors(), "argc": len(sys.argv) - 1}
    tmp = record + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True)
        fh.write("\n")
    os.rename(tmp, record)
    os.execv(real, [real] + sys.argv[1:])
    return 127      # not reached: execv raises on failure


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as exc:
        sys.stderr.write("claude-wrap: cannot start the real client: %s\n" % (exc.strerror or exc))
        sys.exit(127)
