"""capture.report — the report schemas and the atomic writers (spec §3.3, §3.5, §3.6, D15).

Every CLI of the capture layer writes its report through `write_report` (tmp + fsync + rename, UTF-8,
`ensure_ascii=False`): a reader never sees a half-written report. `canonical_bytes` is the one byte form of
a manifest (keys sorted, no whitespace, one trailing newline) — `raw_sha256` of a bundle hashes exactly it.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

CANONICAL = "mnemon-canonical/1"
FETCH = "mnemon-fetch/1"
TEXT = "mnemon-capture-text/2"
SITE_PLAN = "mnemon-site-plan/1"
SITE_BUNDLE = "mnemon-site-bundle/1"
SITE = "mnemon-site/1"


def canonical_bytes(obj) -> bytes:
    """The canonical JSON bytes of an object: sorted keys, compact separators, UTF-8, one trailing newline."""
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_bytes(path: str, data: bytes) -> None:
    """Write bytes atomically: a temp file next to the target, fsync, rename, fsync the directory."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    fsync_dir(directory)


def write_report(path: str, report: dict) -> None:
    """The JSON report, human-readable (indent 1), never ASCII-escaped, written atomically."""
    write_bytes(path, (json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
