#!/usr/bin/env python3
"""mnemon-ingest.py — the inbox v1 consumer behind `mnemon ingest`.

    mnemon [--instance <name>] ingest <inbox-dir> [--dry-run] [--only <envelope_id>]
           [--limit N] [--max-attempts N] [--retry <envelope_id>] [--json]

Contract: docs/specs/2026-10-15-capture-contracts.md §5 (envelope schema §5.1, the
pass §5.2, the normative envelope → gateway mapping §5.3, the outcome matrix §5.4,
the transition table §5.5); the operator's guide is docs/inbox.md.

One pass reads every `<envelope_id>.json` in the inbox, validates it, snapshots its
payload under the vault state dir, persists the exact dispatch request, claims the
envelope (the only place an attempt is spent) and runs `mnemon add` for it. The
result record `<vault state>/ingest/<inbox-key>/results/<envelope_id>.json` is the
only authority; envelopes are never moved, edited or deleted. The whole pass runs
under the vault lock (`fcntl.flock` on `<vault state>/lock`, inherited by every
dispatched child); a held lock exits 75. The consumer has no timeout of its own.

Stdlib only, python 3.9. The configuration comes from bin/mnemon-config.sh through
lib_config; the engine root is this file's own checkout, never the environment.
Test hooks (honoured only with MNEMON_TEST_HOOKS=1): MNEMON_INGEST_ADD_CMD replaces
the `mnemon add` call (same argv after `add`, same stdin, same environment);
MNEMON_TEST_CRASH_AT=after-snapshot | after-dispatch-saved | after-claim kills the
process at that boundary.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat as statmod
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lib_config  # noqa: E402

# bin/ -> engine root, from this file (symlinks resolved), never from $MNEMON_ROOT.
ENGINE_ROOT = Path(__file__).resolve().parent.parent
MNEMON_BIN = ENGINE_ROOT / "bin" / "mnemon"

SCHEMA_ENVELOPE = "mnemon-envelope/1"
SCHEMA_RESULT = "mnemon-ingest-result/1"
SCHEMA_DISPATCH = "mnemon-dispatch/1"
SCHEMA_PROVENANCE = "mnemon-provenance/1"
SCHEMA_PASS = "mnemon-ingest-pass/1"

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:~-]{0,199}$")
PRODUCER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SUFFIX_RE = re.compile(r"^(.*)~([0-9]+)$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

ENVELOPE_MAX = 2 * 1024 * 1024
TEXT_MAX = 2 * 1024 * 1024
FILE_MAX = 20 * 1024 * 1024
DEFER_SECONDS = 15 * 60          # exit 3 / 75, predecessor-pending, incomplete re-check
BACKOFF_BASE = 15 * 60           # 15 min × 2^(attempts-1) …
BACKOFF_CAP = 24 * 3600          # … capped at 24 h
EXPIRY_SECONDS = 24 * 3600       # incomplete-expired / supersedes-unknown, since first_seen
DEFAULT_MAX_ATTEMPTS = 5
STDERR_TAIL = 4000

ORIGINS = (None, "text", "url", "file", "pdf", "audio")
FIDELITIES = ("exact", "embed", "model-transcribed", "url-only")
TIERS = ("full", "bookmark")
RESULT_STATUSES = ("extracted", "captured", "skipped-duplicate", "failed", "integrity-error")
EXIT1_RETRYABLE = {"fetch": True, "convert": True, "unsupported": False, "no-text": False,
                   "invalid-utf8": False, "archive-exists": False, "input-missing": False}
COUNT_KEYS = ("processed", "captured", "failed", "needs_input", "waiting", "deferred", "done-before",
              "other-instance", "unsupported", "quarantined")

EX_USAGE = 2
EX_TEMPFAIL = 75


class UsageError(Exception):
    pass


class LockHeld(Exception):
    pass


# --- small helpers ----------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_iso(value):
    """ISO-8601 → aware datetime (a trailing Z accepted; naive = UTC); None when unparsable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str):
    """(hex digest, size) of a file, streamed."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def short_host() -> str:
    name = socket.gethostname() or "unknown"
    return name.split(".")[0] or "unknown"


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


def write_atomic(path: str, data: bytes, mode: int = 0o600) -> None:
    """<name>.tmp in the destination directory, fsync, rename, fsync the directory."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    fsync_dir(os.path.dirname(path) or ".")


def json_bytes(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def write_json_atomic(path: str, obj) -> bytes:
    data = json_bytes(obj)
    write_atomic(path, data)
    return data


def read_json(path: str):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def mkdir_private(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)


def test_hooks() -> bool:
    return os.environ.get("MNEMON_TEST_HOOKS", "") == "1"


def maybe_crash(point: str) -> None:
    """Crash injection for the recovery tests: honoured only with MNEMON_TEST_HOOKS=1."""
    if test_hooks() and os.environ.get("MNEMON_TEST_CRASH_AT", "") == point:
        sys.stdout.flush()
        sys.stderr.write("mnemon ingest: test crash at %s (MNEMON_TEST_CRASH_AT)\n" % point)
        sys.stderr.flush()
        os._exit(99)


def vault_state_dir(vault_path: str, cfg=None) -> str:
    """D6: ${XDG_STATE_HOME:-~/.local/state}/mnemon/vaults/<sha256(physical vault path)[:16]>/.

    When bin/mnemon-config.sh exports MNEMON_VAULT_STATE_DIR (read back through
    lib_config, or inherited from the dispatcher that resolved the instance for us)
    that value wins; the derivation below is the same rule.
    """
    exported = (cfg or {}).get("MNEMON_VAULT_STATE_DIR", "") or os.environ.get("MNEMON_VAULT_STATE_DIR", "")
    if exported:
        return exported.rstrip("/") or "/"
    physical = os.path.realpath(vault_path.rstrip("/") or "/")
    physical = physical.rstrip("/") or "/"
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state_home, "mnemon", "vaults", sha256_bytes(physical.encode("utf-8"))[:16])


def single_line(value, maxlen=None) -> bool:
    return isinstance(value, str) and not CONTROL_RE.search(value) and (maxlen is None or len(value) <= maxlen)


def sort_key(stem: str):
    """(base id, numeric ~n suffix): `~2` precedes `~10`; the bare id precedes both."""
    m = SUFFIX_RE.match(stem)
    if m:
        return (m.group(1), int(m.group(2)), stem)
    return (stem, 0, stem)


def stderr_tail(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-20:])
    if len(tail) > STDERR_TAIL:
        tail = tail[-STDERR_TAIL:]
    return tail


def last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def parse_result_lines(stdout: str) -> dict:
    """`RESULT:<key>=<value>` lines → map (last occurrence wins); nothing else is read."""
    out = {}
    for line in stdout.splitlines():
        if not line.startswith("RESULT:"):
            continue
        key, sep, value = line[len("RESULT:"):].partition("=")
        if sep and key:
            out[key.strip()] = value.strip()
    return out


# --- the vault lock ---------------------------------------------------------

def inherited_lock_fd(lock_path: str):
    """The §12 A1 proof that a parent (vault-lock.py, or an outer ingest) handed down THIS vault's lock.

    MNEMON_VAULT_LOCK_FD must name a descriptor whose fstat has the (st_dev, st_ino) of lock_path,
    and re-asserting flock(LOCK_EX | LOCK_NB) on it must succeed — a no-op on the open file
    description that holds the lock. Returns the descriptor; None when it is no proof (unset,
    closed, another vault's lock: acquire afresh); raises LockHeld when the descriptor is open on
    this lock file but another description holds the lock (EWOULDBLOCK). The exported inode is a
    hint for humans, never the proof.
    """
    fd_text = os.environ.get("MNEMON_VAULT_LOCK_FD", "")
    if not fd_text:
        return None
    try:
        fd = int(fd_text)
        st = os.fstat(fd)
        on_disk = os.stat(lock_path)
    except (ValueError, OSError):
        return None
    if (st.st_dev, st.st_ino) != (on_disk.st_dev, on_disk.st_ino):
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise LockHeld(lock_path)
        return None
    return fd


def acquire_lock(lock_path: str) -> int:
    """fcntl.flock(LOCK_EX | LOCK_NB) on <vault state>/lock; the descriptor is inheritable
    and exported as MNEMON_VAULT_LOCK_FD / MNEMON_VAULT_LOCK_INODE for every child."""
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise LockHeld(lock_path)
        raise
    os.set_inheritable(fd, True)
    os.environ["MNEMON_VAULT_LOCK_FD"] = str(fd)
    os.environ["MNEMON_VAULT_LOCK_INODE"] = str(os.fstat(fd).st_ino)
    return fd


# --- envelope validation (§5.1) ----------------------------------------------

class Invalid(Exception):
    """A schema violation: (reason class, detail)."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def validate_envelope(obj, stem: str, inbox_real: str) -> dict:
    """Every field rule of §5.1. Returns the envelope; raises Invalid."""
    if not isinstance(obj, dict):
        raise Invalid("schema", "the envelope is not a JSON object")

    def req(name):
        if name not in obj:
            raise Invalid("schema", "%s is missing" % name)
        return obj[name]

    if req("schema") != SCHEMA_ENVELOPE:
        raise Invalid("schema", "schema must be %s (got %r)" % (SCHEMA_ENVELOPE, obj.get("schema")))
    eid = req("envelope_id")
    if not isinstance(eid, str) or not ID_RE.match(eid):
        raise Invalid("schema", "envelope_id must match ^[A-Za-z0-9][A-Za-z0-9._:~-]{0,199}$")
    if eid != stem:
        raise Invalid("schema", "envelope_id %r does not equal the file stem %r" % (eid, stem))
    producer = req("producer")
    if not isinstance(producer, dict):
        raise Invalid("schema", "producer must be an object")
    pid = producer.get("id")
    if not isinstance(pid, str) or not PRODUCER_RE.match(pid):
        raise Invalid("schema", "producer.id must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    for key in ("kind", "host", "version"):
        if producer.get(key) is not None and not single_line(producer.get(key), 200):
            raise Invalid("schema", "producer.%s must be a single-line string or null" % key)
    if not eid.startswith(pid + ":"):
        raise Invalid("schema", "envelope_id must be <producer.id>:<item_id> (producer.id %r)" % pid)
    instance = req("instance")
    if not isinstance(instance, str) or not instance or not single_line(instance, 200):
        raise Invalid("schema", "instance must be a non-empty single-line string")
    created = req("created_at")
    if parse_iso(created) is None:
        raise Invalid("schema", "created_at must be an ISO-8601 timestamp")
    supersedes = req("supersedes")
    if supersedes is not None:
        if not isinstance(supersedes, str) or not ID_RE.match(supersedes):
            raise Invalid("schema", "supersedes must be null or an envelope_id")
        if supersedes == eid:
            raise Invalid("schema", "supersedes must not name the envelope itself")

    request = req("request")
    if not isinstance(request, dict):
        raise Invalid("schema", "request must be an object")
    origin = request.get("origin")
    if origin not in ORIGINS:
        raise Invalid("schema", "request.origin must be one of text | url | file | pdf | audio | null")
    url = request.get("url")
    if url is not None and (not single_line(url, 8192) or not url.strip()):
        raise Invalid("schema", "request.url must be null or a non-empty single-line string")
    for key, limit in (("title", 300), ("author", 200), ("intent", 2000), ("context", 200), ("source_type", 64)):
        value = request.get(key)
        if value is not None and not single_line(value, limit):
            raise Invalid("schema", "request.%s must be null or a single-line string of at most %d characters" % (key, limit))
    tier = request.get("tier", "full")
    if tier not in TIERS:
        raise Invalid("schema", "request.tier must be full or bookmark")

    payload = req("payload")
    if not isinstance(payload, dict):
        raise Invalid("schema", "payload must be an object")
    kind = payload.get("kind")
    if kind not in ("text", "file", "none"):
        raise Invalid("schema", "payload.kind must be text | file | none")
    media_type = payload.get("media_type")
    if media_type is not None and not single_line(media_type, 255):
        raise Invalid("schema", "payload.media_type must be null or a single-line string")
    if kind == "text":
        text = payload.get("text")
        if not isinstance(text, str):
            raise Invalid("schema", "payload.text must be a string for kind text")
        size = payload.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise Invalid("schema", "payload.bytes must be a non-negative integer")
        if size > TEXT_MAX:
            raise Invalid("payload-too-large", "payload.bytes %d exceeds the 2 MB text limit" % size)
        if not isinstance(payload.get("sha256"), str) or not SHA_RE.match(payload["sha256"]):
            raise Invalid("schema", "payload.sha256 must be 64 lowercase hex characters")
    elif kind == "file":
        rel = payload.get("path")
        if not isinstance(rel, str) or not rel:
            raise Invalid("schema", "payload.path must be a relative path for kind file")
        if rel.startswith("/") or os.path.isabs(rel):
            raise Invalid("path-escape", "payload.path must be relative to the inbox, not absolute")
        parts = rel.replace("\\", "/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise Invalid("path-escape", "payload.path must not contain '..', '.' or empty components")
        real = os.path.realpath(os.path.join(inbox_real, rel))
        if real != inbox_real and not real.startswith(inbox_real.rstrip("/") + os.sep):
            raise Invalid("path-escape", "payload.path resolves outside the inbox")
        if real == inbox_real:
            raise Invalid("path-escape", "payload.path names the inbox itself")
        size = payload.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise Invalid("schema", "payload.bytes must be a non-negative integer")
        if size > FILE_MAX:
            raise Invalid("payload-too-large", "payload.bytes %d exceeds the 20 MB file limit" % size)
        if not isinstance(payload.get("sha256"), str) or not SHA_RE.match(payload["sha256"]):
            raise Invalid("schema", "payload.sha256 must be 64 lowercase hex characters")
    else:  # none
        size = payload.get("bytes", 0)
        if size not in (0, None):
            raise Invalid("schema", "payload.bytes must be 0 for kind none")
        if payload.get("sha256") is not None:
            raise Invalid("schema", "payload.sha256 must be null for kind none")

    fidelity = req("fidelity")
    if fidelity not in FIDELITIES:
        raise Invalid("schema", "fidelity must be exact | embed | model-transcribed | url-only")
    provenance = req("provenance")
    if not isinstance(provenance, dict):
        raise Invalid("schema", "provenance must be a JSON object")
    if req("complete") is not True:
        raise Invalid("schema", "complete must be true (the producer wrote the envelope early)")
    return obj


def classify_request(env: dict):
    """§5.3: ('needs_input'|'invalid-request', None) or ('ok', (leading argv, uses stdin))."""
    request = env["request"]
    payload = env["payload"]
    kind = payload["kind"]
    origin = request.get("origin")
    url = request.get("url")
    if kind == "text":
        if origin in (None, "text"):
            lead = ["--origin", "text"]
            if url:
                lead += ["--url", url]
            return "ok", (lead, True)
        return "invalid-request", None
    if kind == "none":
        if not url:
            return "needs_input", None
        if origin is None:
            return "ok", (["--url", url], False)
        if origin == "url":
            return "ok", (["--origin", "url", "--url", url], False)
        return "invalid-request", None
    if kind == "file":
        if origin is None:
            return "ok", (["--file", "<snapshot>"], False)
        if origin in ("file", "pdf", "audio"):
            return "ok", (["--origin", origin, "--file", "<snapshot>"], False)
        return "invalid-request", None
    return "invalid-request", None


def build_argv(env: dict, lead: list, provenance_path: str, snapshot_path, supersedes_rev) -> list:
    """The normative argv of §5.3 after `mnemon add` (placeholders resolved)."""
    eid = env["envelope_id"]
    request = env["request"]
    argv = [snapshot_path if a == "<snapshot>" else a for a in lead]
    argv += ["--capture-id", eid, "--import-source", env["producer"]["id"], "--import-ref", eid,
             "--producer-fidelity", env["fidelity"], "--provenance-file", provenance_path]
    if request.get("context") is not None:
        argv += ["--context", request["context"]]
    for key, flag in (("title", "--title"), ("author", "--author"), ("intent", "--intent"), ("source_type", "--source-type")):
        value = request.get(key)
        if value:
            argv += [flag, value]
    if supersedes_rev:
        argv += ["--recapture", "--supersedes", supersedes_rev]
    return argv


# --- the pass ---------------------------------------------------------------

class Ingest:
    def __init__(self, args, cfg: dict):
        self.args = args
        self.cfg = cfg
        self.instance = cfg.get("MNEMON_INSTANCE", "")
        self.vault = cfg.get("VAULT_PATH", "")
        self.dry_run = bool(args.dry_run)
        self.json_out = bool(args.json)
        self.max_attempts = args.max_attempts
        self.limit = args.limit
        self.host = short_host()
        self.inbox = os.path.realpath(args.inbox.rstrip("/") or args.inbox)
        self.vault_state = vault_state_dir(self.vault, cfg)
        self.inbox_key = sha256_bytes(self.inbox.encode("utf-8"))[:8]
        self.state_dir = os.path.join(self.vault_state, "ingest", self.inbox_key)
        self.results_dir = os.path.join(self.state_dir, "results")
        self.payloads_dir = os.path.join(self.state_dir, "payloads")
        self.quarantine_dir = os.path.join(self.state_dir, "quarantine")
        self.captures_dir = os.path.join(self.vault_state, "captures")
        self.lock_path = os.path.join(self.vault_state, "lock")
        self.lock_fd = None
        self.counts = dict((k, 0) for k in COUNT_KEYS)
        self.counts["eligible"] = 0
        self.items = []
        self.claimed = 0
        self.overrides = {}     # dry-run only: records mutated in memory (--retry)
        self.now = now_utc()

    # -- output --

    def say(self, line: str) -> None:
        if not self.json_out:
            print(line)

    def note(self, line: str) -> None:
        sys.stderr.write("mnemon ingest: %s\n" % line)

    def report(self, stem: str, file_name: str, kind: str, detail: str, rec=None, count_as=None) -> None:
        self.counts["processed"] += 1
        key = count_as or kind
        if key in self.counts:
            self.counts[key] += 1
        item = {"envelope_id": stem, "file": file_name, "report": kind, "detail": detail}
        for field in ("state", "error_class", "retryable", "attempts", "attempts_allowed", "revision", "path",
                      "next_attempt_at", "deadline_at", "duplicate"):
            item[field] = rec.get(field) if rec else None
        self.items.append(item)
        self.say("%s %s — %s" % (kind, stem, detail) if detail else "%s %s" % (kind, stem))

    # -- records --

    def result_path(self, stem: str) -> str:
        return os.path.join(self.results_dir, stem + ".json")

    def load_result(self, stem: str):
        if stem in self.overrides:
            return self.overrides[stem]
        path = self.result_path(stem)
        if not os.path.isfile(path):
            return None
        return read_json(path)

    def base_record(self, stem: str, sha, prev) -> dict:
        prev = prev or {}
        retries = prev.get("retries") or 0
        return {
            "schema": SCHEMA_RESULT,
            "envelope_id": stem,
            "envelope_sha256": sha,
            "dispatch_sha256": prev.get("dispatch_sha256"),
            "state": None,
            "error_class": None,
            "error": None,
            "retryable": None,
            "attempts": prev.get("attempts") or 0,
            "attempts_allowed": self.max_attempts * (1 + retries),
            "retries": retries,
            "next_attempt_at": None,
            "deadline_at": None,
            "capture_id": prev.get("capture_id"),
            "revision": prev.get("revision"),
            "path": prev.get("path"),
            "source_sha256": prev.get("source_sha256"),
            "duplicate": bool(prev.get("duplicate")),
            "supersedes_unresolved": bool(prev.get("supersedes_unresolved")),
            "last_exit": prev.get("last_exit"),
            "last_result": prev.get("last_result"),
            "last_error": prev.get("last_error"),
            "history": list(prev.get("history") or []),
            "first_seen": prev.get("first_seen") or iso(self.now),
            "started_at": prev.get("started_at"),
            "updated_at": iso(self.now),
            "writer": self.host,
            "retry_requested_at": prev.get("retry_requested_at"),
            "dispatch_mode": prev.get("dispatch_mode"),
        }

    def write_record(self, rec: dict) -> None:
        rec["updated_at"] = iso(self.now)
        if self.dry_run:
            self.overrides[rec["envelope_id"]] = rec
            return
        write_json_atomic(self.result_path(rec["envelope_id"]), rec)

    def quarantine_copy(self, file_name: str, data) -> None:
        if self.dry_run or data is None:
            return
        write_atomic(os.path.join(self.quarantine_dir, file_name), data)

    def settle(self, stem: str, file_name: str, sha, prev, state: str, error_class, detail: str,
               data=None, extra=None) -> dict:
        """A terminal-or-waiting record written without a claim (no attempt spent)."""
        rec = self.base_record(stem, sha, prev)
        rec["state"] = state
        rec["error_class"] = error_class
        rec["error"] = detail or None
        rec["retryable"] = False if state in ("quarantined", "needs_input", "unsupported") else rec["retryable"]
        if state == "failed":
            rec["retryable"] = False
        if extra:
            rec.update(extra)
        if state == "quarantined":
            self.quarantine_copy(file_name, data)
        self.write_record(rec)
        return rec

    # -- the lock --

    def lock(self) -> None:
        mkdir_private(self.vault_state)
        fd = inherited_lock_fd(self.lock_path) if os.path.exists(self.lock_path) else None
        if fd is not None:
            self.lock_fd = fd
            os.set_inheritable(fd, True)
            return
        self.lock_fd = acquire_lock(self.lock_path)

    # -- the pass --

    def run(self) -> int:
        if not os.path.isdir(self.inbox):
            raise UsageError("inbox is not a directory: %s" % self.args.inbox)
        writer_host = self.cfg.get("WRITER_HOST", "")
        if writer_host and writer_host != self.host and not self.dry_run:
            self.note("this host (%s) must not write vault %s (writer_host %s)" % (self.host, self.vault, writer_host))
            return EX_USAGE
        if not self.dry_run:
            self.lock()
            for d in (self.results_dir, self.payloads_dir, self.quarantine_dir):
                mkdir_private(d)
            inbox_note = os.path.join(self.state_dir, "inbox.json")
            if not os.path.exists(inbox_note):
                write_json_atomic(inbox_note, {"inbox": self.inbox, "instance": self.instance})
        if self.args.retry:
            self.apply_retry(self.args.retry)
        if self.dry_run:
            self.say("dry-run: %s → %s (nothing is written, no lock is taken)" % (self.inbox, self.state_dir))

        entries = self.list_inbox()
        if self.args.only and not any(stem == self.args.only for stem, _ in entries):
            self.note("no envelope %r in %s" % (self.args.only, self.inbox))
        for stem, file_name in entries:
            if self.args.only and stem != self.args.only:
                continue
            self.process(stem, file_name)
        self.summary()
        return 0

    def list_inbox(self):
        entries = []
        for name in os.listdir(self.inbox):
            if name.startswith(".") or name.endswith(".tmp") or not name.endswith(".json"):
                continue
            entries.append((name[:-5], name))
        entries.sort(key=lambda e: sort_key(e[0]))
        return entries

    def summary(self) -> None:
        c = self.counts
        line = ("ingest: processed %d · captured %d · failed %d · needs_input %d · waiting %d · deferred %d · "
                "done-before %d · other-instance %d · unsupported %d · quarantined %d"
                % tuple(c[k] for k in COUNT_KEYS))
        if self.dry_run:
            line += " · eligible %d (dry-run)" % c["eligible"]
        if self.json_out:
            doc = {"schema": SCHEMA_PASS, "inbox": self.inbox, "instance": self.instance, "state_dir": self.state_dir,
                   "vault_state_dir": self.vault_state, "dry_run": self.dry_run, "counts": self.counts,
                   "items": self.items, "exit": 0}
            print(json.dumps(doc, ensure_ascii=False, indent=1))
        else:
            print(line)

    # -- --retry (step 13) --

    def apply_retry(self, eid: str) -> None:
        rec = self.load_result(eid)
        if rec is None:
            raise UsageError("--retry %s: no result record for this envelope in %s" % (eid, self.results_dir))
        state = rec.get("state")
        cls = rec.get("error_class")
        eligible = state == "exhausted" or (state == "failed" and rec.get("retryable") is False
                                            and cls not in ("contract", "invalid-request"))
        if not eligible:
            raise UsageError("--retry %s: a %s%s result is not retryable (allowed: exhausted, or a permanent "
                             "failed whose class is not contract/invalid-request)"
                             % (eid, state, "/" + cls if cls else ""))
        rec["retries"] = (rec.get("retries") or 0) + 1
        rec["attempts_allowed"] = self.max_attempts * (1 + rec["retries"])
        rec["state"] = "failed"
        rec["retryable"] = True
        rec["next_attempt_at"] = iso(self.now)
        rec["deadline_at"] = None
        rec["retry_requested_at"] = iso(self.now)
        self.write_record(rec)
        self.note("--retry %s: reopened (retries %d, attempts %d of %d allowed)"
                  % (eid, rec["retries"], rec["attempts"], rec["attempts_allowed"]))

    # -- one envelope --

    def process(self, stem: str, file_name: str) -> None:
        path = os.path.join(self.inbox, file_name)
        try:
            st = os.lstat(path)
        except OSError as exc:
            self.note("cannot stat %s: %s" % (path, exc))
            return
        if statmod.S_ISLNK(st.st_mode):
            prev = self.safe_load(stem)
            if prev is not None and prev.get("state") == "quarantined" and prev.get("error_class") == "not-regular":
                self.report(stem, file_name, "done-before", "quarantined/not-regular", prev)
                return
            rec = self.settle(stem, file_name, None, prev, "quarantined", "not-regular",
                              "the envelope is a symbolic link, not a regular file")
            self.report(stem, file_name, "quarantined", "not-regular: a symbolic link", rec)
            return
        if not statmod.S_ISREG(st.st_mode):
            return
        if ".sync-conflict-" in file_name:
            self.conflict_copy(stem, file_name, path)
            return
        if st.st_size > ENVELOPE_MAX:
            prev = self.safe_load(stem)
            if prev is not None and prev.get("state") == "quarantined" and prev.get("error_class") == "too-large":
                self.report(stem, file_name, "done-before", "quarantined/too-large", prev)
                return
            rec = self.settle(stem, file_name, None, prev, "quarantined", "too-large",
                              "the envelope is %d bytes, more than the 2 MB limit" % st.st_size)
            self.report(stem, file_name, "quarantined", "too-large: %d bytes" % st.st_size, rec)
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self.note("cannot read %s: %s" % (path, exc))
            return
        sha = sha256_bytes(data)

        # step 3: the replacement check comes first, for every stored state
        prev = self.safe_load(stem)
        if prev is None and os.path.isfile(self.result_path(stem)):
            return  # unreadable record: reported by safe_load, never overwritten blindly
        if prev is not None and prev.get("envelope_sha256") != sha:
            history = list(prev.get("history") or [])
            old = dict(prev)
            old.pop("history", None)
            history.append(old)
            rec = self.settle(stem, file_name, sha, prev, "quarantined", "envelope-replaced",
                              "the envelope's bytes changed under the same envelope_id (previous state %s)" % prev.get("state"),
                              data=data, extra={"history": history, "attempts": 0, "retries": 0, "revision": None,
                                                "path": None, "source_sha256": None, "duplicate": False,
                                                "supersedes_unresolved": False, "capture_id": None,
                                                "dispatch_sha256": None, "dispatch_mode": None,
                                                "last_exit": None, "last_result": None, "last_error": None})
            self.report(stem, file_name, "quarantined", "envelope-replaced (was %s)" % prev.get("state"), rec)
            return
        if prev is not None and self.is_terminal(prev):
            cls = prev.get("error_class")
            self.report(stem, file_name, "done-before", prev.get("state") + ("/" + cls if cls else ""), prev)
            return

        # step 4: parse and validate
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            rec = self.settle(stem, file_name, sha, prev, "quarantined", "invalid-json", "not valid UTF-8 JSON: %s" % exc, data=data)
            self.report(stem, file_name, "quarantined", "invalid-json", rec)
            return
        if isinstance(obj, dict) and isinstance(obj.get("instance"), str) and obj.get("instance") != self.instance:
            self.report(stem, file_name, "other-instance", "instance %r is not %r" % (obj.get("instance"), self.instance))
            return
        try:
            env = validate_envelope(obj, stem, self.inbox)
        except Invalid as exc:
            rec = self.settle(stem, file_name, sha, prev, "quarantined", exc.reason, exc.detail, data=data)
            self.report(stem, file_name, "quarantined", "%s: %s" % (exc.reason, exc.detail), rec)
            return
        if env["request"].get("tier", "full") == "bookmark":
            rec = self.settle(stem, file_name, sha, prev, "unsupported", "bookmark-tier",
                              "bookmark-tier envelopes are refused in phase 1 (D12)")
            self.report(stem, file_name, "unsupported", "bookmark tier (phase 1 refuses it)", rec)
            return
        verdict, lead = classify_request(env)
        if verdict == "needs_input":
            rec = self.settle(stem, file_name, sha, prev, "needs_input", None,
                              "payload.kind none without a request.url: nothing to capture")
            self.report(stem, file_name, "needs_input", "kind none without a url", rec)
            return
        if verdict == "invalid-request":
            rec = self.settle(stem, file_name, sha, prev, "failed", "invalid-request",
                              "payload.kind %r with request.origin %r is not a valid pair (§5.3)"
                              % (env["payload"]["kind"], env["request"].get("origin")))
            self.report(stem, file_name, "failed", "invalid-request: kind %s × origin %s" % (
                env["payload"]["kind"], env["request"].get("origin")), rec)
            return

        # step 5: the transition table
        state = prev.get("state") if prev else None
        if state == "started":
            if self.recover_started(stem, file_name, sha, prev, env, lead):
                return
            # no usable dispatch.json: crash before step 8 → treat as (none)
        elif state in ("incomplete", "deferred"):
            due = parse_iso(prev.get("deadline_at"))
            if due is not None and self.now < due:
                self.report(stem, file_name, "waiting", "%s/%s until %s" % (state, prev.get("error_class"), prev.get("deadline_at")), prev)
                return
        elif state == "failed":
            due = parse_iso(prev.get("next_attempt_at"))
            if due is not None and self.now < due:
                self.report(stem, file_name, "waiting", "failed/%s, next attempt at %s" % (prev.get("error_class"), prev.get("next_attempt_at")), prev)
                return
        self.fresh_request(stem, file_name, sha, prev, env, lead, data)

    def conflict_copy(self, stem: str, file_name: str, path: str) -> None:
        """A `.sync-conflict-` copy: quarantined once under its own file stem, never processed."""
        prev = self.safe_load(stem)
        if prev is not None:
            self.report(stem, file_name, "done-before", "quarantined/conflict-copy", prev)
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self.note("cannot read %s: %s" % (path, exc))
            return
        rec = self.settle(stem, file_name, sha256_bytes(data), None, "quarantined", "conflict-copy",
                          "a sync conflict copy of an envelope is never processed", data=data)
        self.report(stem, file_name, "quarantined", "conflict-copy", rec)

    def safe_load(self, stem: str):
        try:
            return self.load_result(stem)
        except (OSError, ValueError) as exc:
            self.note("cannot read result %s: %s — skipping %s" % (self.result_path(stem), exc, stem))
            return None

    @staticmethod
    def is_terminal(rec: dict) -> bool:
        state = rec.get("state")
        if state in ("captured", "needs_input", "unsupported", "quarantined", "exhausted"):
            return True
        return state == "failed" and rec.get("retryable") is False

    # -- steps 6–8 --

    def payload_dir(self, stem: str) -> str:
        return os.path.join(self.payloads_dir, stem)

    def snapshot(self, stem: str, env: dict):
        """Step 6. Returns ('ok', snapshot path or None) | ('incomplete', detail) | ('quarantined', reason, detail)."""
        payload = env["payload"]
        pdir = self.payload_dir(stem)
        if payload["kind"] == "text":
            raw = payload["text"].encode("utf-8")
            if sha256_bytes(raw) != payload["sha256"] or len(raw) != payload["bytes"]:
                return ("quarantined", "payload-mismatch",
                        "payload.sha256/bytes do not match the UTF-8 bytes of payload.text (%d bytes, %s)"
                        % (len(raw), sha256_bytes(raw)))
            if not self.dry_run:
                mkdir_private(pdir)
                write_atomic(os.path.join(pdir, "stdin"), raw)
            return ("ok", os.path.join(pdir, "stdin"))
        if payload["kind"] == "file":
            rel = payload["path"].replace("\\", "/")
            snap = os.path.join(pdir, *rel.split("/"))
            if os.path.isfile(snap):
                digest, size = sha256_file(snap)
                if digest == payload["sha256"] and size == payload["bytes"]:
                    return ("ok", snap)
                if not self.dry_run:
                    os.unlink(snap)
            src = os.path.join(self.inbox, *rel.split("/"))
            if not os.path.exists(src):
                return ("incomplete", "payload file %s is not in the inbox yet" % rel)
            if not os.path.isfile(src):
                return ("quarantined", "payload-not-regular", "payload.path %s is not a regular file" % rel)
            size = os.path.getsize(src)
            if size > FILE_MAX:
                return ("quarantined", "payload-too-large", "payload file %s is %d bytes, more than the 20 MB limit" % (rel, size))
            if self.dry_run:
                digest, size = sha256_file(src)
                if digest != payload["sha256"] or size != payload["bytes"]:
                    return ("incomplete", "payload file %s does not match its sha256/bytes yet" % rel)
                return ("ok", snap)
            mkdir_private(os.path.dirname(snap))
            tmp = snap + ".tmp"
            try:
                shutil.copyfile(src, tmp)
                with open(tmp, "rb") as fh:
                    os.fsync(fh.fileno())
                digest, size = sha256_file(tmp)
                if digest != payload["sha256"] or size != payload["bytes"]:
                    os.unlink(tmp)
                    return ("incomplete", "payload file %s does not match its sha256/bytes yet (%d bytes, %s)" % (rel, size, digest))
                os.chmod(tmp, 0o600)
                os.replace(tmp, snap)
                fsync_dir(os.path.dirname(snap))
            except OSError as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                return ("incomplete", "cannot snapshot payload file %s: %s" % (rel, exc))
            return ("ok", snap)
        return ("ok", None)

    def write_provenance(self, stem: str, env: dict) -> str:
        pdir = self.payload_dir(stem)
        path = os.path.join(pdir, "provenance.json")
        doc = {"schema": SCHEMA_PROVENANCE, "envelope_id": env["envelope_id"], "producer": env["producer"],
               "instance": env["instance"], "created_at": env["created_at"], "fidelity": env["fidelity"],
               "request_url": env["request"].get("url"), "request": env["request"], "provenance": env["provenance"]}
        if not self.dry_run:
            mkdir_private(pdir)
            write_json_atomic(path, doc)
        return path

    def resolve_predecessor(self, env: dict):
        """Step 7 / §5.5 predecessor rule: ('none',) | ('revision', rev) | ('unresolved',) | ('pending', detail)."""
        target = env.get("supersedes")
        if not target:
            return ("none",)
        rec = self.safe_load(target)
        if rec is None:
            return ("pending", "superseded envelope %s has no result yet" % target)
        state = rec.get("state")
        if state in ("captured", "failed", "exhausted") and rec.get("revision"):
            return ("revision", rec["revision"])        # a published source, whatever happened after it
        if state in ("captured", "exhausted", "needs_input", "unsupported", "quarantined"):
            return ("unresolved",)                      # terminal without a revision
        if state == "failed":
            if rec.get("retryable") is False:
                return ("unresolved",)                  # permanent, nothing published
            return ("pending", "superseded envelope %s is still retrying (failed/%s)" % (target, rec.get("error_class")))
        return ("pending", "superseded envelope %s is %s" % (target, state))

    def expired(self, prev) -> bool:
        first = parse_iso((prev or {}).get("first_seen")) or self.now
        return self.now - first >= timedelta(seconds=EXPIRY_SECONDS)

    def fresh_request(self, stem, file_name, sha, prev, env, lead, data) -> None:
        """Steps 6–9 for an envelope without a recoverable claim, then dispatch."""
        snap = self.snapshot(stem, env)
        if snap[0] == "quarantined":
            rec = self.settle(stem, file_name, sha, prev, "quarantined", snap[1], snap[2], data=data)
            self.report(stem, file_name, "quarantined", "%s: %s" % (snap[1], snap[2]), rec)
            return
        if snap[0] == "incomplete":
            if self.expired(prev):
                rec = self.settle(stem, file_name, sha, prev, "quarantined", "incomplete-expired",
                                  "%s (24 h since first_seen)" % snap[1], data=data)
                self.report(stem, file_name, "quarantined", "incomplete-expired: %s" % snap[1], rec)
                return
            deadline = iso(self.now + timedelta(seconds=DEFER_SECONDS))
            rec = self.settle(stem, file_name, sha, prev, "incomplete", "payload-missing", snap[1],
                              extra={"deadline_at": deadline})
            self.report(stem, file_name, "incomplete", "%s; re-check at %s" % (snap[1], deadline), rec, count_as="deferred")
            return
        snapshot_path = snap[1]
        provenance_path = self.write_provenance(stem, env)
        maybe_crash("after-snapshot")

        supersedes_rev = None
        unresolved = False
        pred = self.resolve_predecessor(env)
        if pred[0] == "pending":
            if self.expired(prev):
                rec = self.settle(stem, file_name, sha, prev, "quarantined", "supersedes-unknown",
                                  "%s (24 h since first_seen)" % pred[1], data=data)
                self.report(stem, file_name, "quarantined", "supersedes-unknown: %s" % pred[1], rec)
                return
            deadline = iso(self.now + timedelta(seconds=DEFER_SECONDS))
            rec = self.settle(stem, file_name, sha, prev, "deferred", "predecessor-pending", pred[1],
                              extra={"deadline_at": deadline})
            self.report(stem, file_name, "deferred", "predecessor-pending: %s; until %s" % (pred[1], deadline), rec)
            return
        if pred[0] == "revision":
            supersedes_rev = pred[1]
        elif pred[0] == "unresolved":
            unresolved = True

        argv = build_argv(env, lead[0], provenance_path, snapshot_path, supersedes_rev)
        stdin_name = "stdin" if lead[1] else None
        dispatch = {"schema": SCHEMA_DISPATCH, "argv": argv, "stdin": stdin_name,
                    "env": {"MNEMON_INSTANCE": self.instance, "MNEMON_CONFIG": self.cfg.get("MNEMON_CONFIG", "")}}
        if self.dry_run:
            self.counts["eligible"] += 1
            self.counts["processed"] += 1
            self.items.append({"envelope_id": stem, "file": file_name, "report": "eligible", "detail": " ".join(shlex.quote(a) for a in argv),
                               "argv": argv, "stdin": stdin_name, "supersedes_unresolved": unresolved})
            self.say("eligible %s — mnemon add %s%s" % (stem, " ".join(shlex.quote(a) for a in argv),
                                                       " < " + os.path.join(self.payload_dir(stem), "stdin") if stdin_name else ""))
            return
        dispatch_bytes = write_json_atomic(os.path.join(self.payload_dir(stem), "dispatch.json"), dispatch)
        maybe_crash("after-dispatch-saved")
        if self.limit is not None and self.claimed >= self.limit:
            self.report(stem, file_name, "waiting", "eligible, beyond --limit %d of this pass" % self.limit, prev)
            return
        rec = self.claim(stem, sha, prev, sha256_bytes(dispatch_bytes), unresolved)
        self.dispatch(stem, file_name, rec, dispatch, prev)

    def recover_started(self, stem, file_name, sha, prev, env, lead) -> bool:
        """§5.5 `started`: a saved dispatch.json → claim again and dispatch it (or replay by id)."""
        dpath = os.path.join(self.payload_dir(stem), "dispatch.json")
        try:
            dispatch = read_json(dpath)
        except (OSError, ValueError):
            return False
        argv = dispatch.get("argv") if isinstance(dispatch, dict) else None
        stdin_name = dispatch.get("stdin") if isinstance(dispatch, dict) else None
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv) or stdin_name not in (None, "stdin"):
            return False
        if stdin_name and not os.path.isfile(os.path.join(self.payload_dir(stem), stdin_name)):
            return False
        if "--file" in argv:
            snap = argv[argv.index("--file") + 1] if argv.index("--file") + 1 < len(argv) else ""
            if not os.path.isfile(snap):
                return False
        if self.dry_run:
            self.counts["eligible"] += 1
            self.counts["processed"] += 1
            self.items.append({"envelope_id": stem, "file": file_name, "report": "eligible", "detail": "recover the saved request",
                               "argv": argv, "stdin": stdin_name})
            self.say("eligible %s — recover started (attempt %d): mnemon add %s" % (
                stem, (prev.get("attempts") or 0) + 1, " ".join(shlex.quote(a) for a in argv)))
            return True
        if self.limit is not None and self.claimed >= self.limit:
            self.report(stem, file_name, "waiting", "started, recoverable, beyond --limit %d of this pass" % self.limit, prev)
            return True
        with open(dpath, "rb") as fh:
            dispatch_sha = sha256_bytes(fh.read())
        rec = self.claim(stem, sha, prev, dispatch_sha, bool(prev.get("supersedes_unresolved")))
        self.dispatch(stem, file_name, rec, dispatch, prev)
        return True

    # -- steps 9–12 --

    def claim(self, stem, sha, prev, dispatch_sha, unresolved) -> dict:
        rec = self.base_record(stem, sha, prev)
        rec["state"] = "started"
        rec["attempts"] = (rec["attempts"] or 0) + 1
        rec["dispatch_sha256"] = dispatch_sha
        rec["capture_id"] = stem
        rec["started_at"] = iso(self.now)
        rec["supersedes_unresolved"] = unresolved
        self.write_record(rec)
        self.claimed += 1
        maybe_crash("after-claim")
        return rec

    def add_command(self):
        hook = os.environ.get("MNEMON_INGEST_ADD_CMD", "")
        if hook:
            if test_hooks():
                return shlex.split(hook)
            self.note("ignoring MNEMON_INGEST_ADD_CMD — a test hook, honoured only with MNEMON_TEST_HOOKS=1; running mnemon add")
        return [str(MNEMON_BIN), "add"]

    def dispatch(self, stem, file_name, rec, dispatch, prev) -> None:
        receipt = os.path.join(self.captures_dir, stem + ".json")
        if os.path.isfile(receipt):
            argv = ["--capture-id", stem]
            stdin_path = None
            rec["dispatch_mode"] = "replay"
        else:
            argv = list(dispatch["argv"])
            stdin_path = os.path.join(self.payload_dir(stem), dispatch["stdin"]) if dispatch.get("stdin") else None
            rec["dispatch_mode"] = "request"
        cmd = self.add_command() + argv
        env = dict(os.environ)
        pass_fds = (self.lock_fd,) if self.lock_fd is not None else ()
        stdin_fh = open(stdin_path, "rb") if stdin_path else None
        try:
            proc = subprocess.run(cmd, stdin=stdin_fh if stdin_fh else subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, pass_fds=pass_fds)
        except OSError as exc:
            proc = None
            err_text = "cannot run %s: %s" % (cmd[0], exc)
        finally:
            if stdin_fh:
                stdin_fh.close()
        if proc is None:
            self.outcome(stem, file_name, rec, prev, 127, "", err_text)
            return
        out_text = proc.stdout.decode("utf-8", errors="replace")
        err_text = proc.stderr.decode("utf-8", errors="replace")
        if err_text:
            sys.stderr.write(err_text if err_text.endswith("\n") else err_text + "\n")
        self.outcome(stem, file_name, rec, prev, proc.returncode, out_text, err_text)

    def outcome(self, stem, file_name, rec, prev, code, out_text, err_text) -> None:
        """§5.4: exit code first, then the RESULT: map."""
        result = parse_result_lines(out_text)
        rec["last_exit"] = code
        rec["last_result"] = result or None
        rec["last_error"] = stderr_tail(err_text) or None
        if result.get("capture_id"):
            rec["capture_id"] = result["capture_id"]
        status = result.get("status")
        well_formed = status in RESULT_STATUSES
        retryable_text = result.get("retryable")
        rec["deadline_at"] = None
        rec["next_attempt_at"] = None

        def record_files():
            for key in ("revision", "path", "source_sha256"):
                value = result.get(key)
                if value:
                    rec[key] = value

        if code == 0:
            if well_formed and status == "extracted":
                record_files()
                rec["duplicate"] = False
                self.finish(stem, file_name, rec, "captured", None, None, result.get("path") or result.get("revision") or "")
                return
            if well_formed and status == "skipped-duplicate":
                record_files()
                rec["duplicate"] = True
                self.finish(stem, file_name, rec, "captured", None, None, "duplicate of %s" % (result.get("path") or result.get("revision") or "?"))
                return
            self.fail(stem, file_name, rec, prev, "malformed-result", True, "exit 0 without a well-formed RESULT:status")
            return
        if code == 4:
            if well_formed and retryable_text in ("true", "false") and result.get("error_class"):
                record_files()
                self.fail(stem, file_name, rec, prev, result["error_class"], retryable_text == "true",
                          "exit 4: %s (%s)" % (result["error_class"], last_line(err_text) or "extract not published"))
                return
            self.fail(stem, file_name, rec, prev, "malformed-result", True, "exit 4 without a well-formed RESULT: block")
            return
        if code == 1:
            if well_formed and status == "failed":
                cls = result.get("error_class") or "unknown"
                if retryable_text in ("true", "false"):
                    retryable = retryable_text == "true"
                else:
                    retryable = EXIT1_RETRYABLE.get(cls, False)
                self.fail(stem, file_name, rec, prev, cls, retryable, "exit 1: %s (%s)" % (cls, last_line(err_text) or "nothing published"))
                return
            if well_formed and status == "integrity-error":
                record_files()
                self.fail(stem, file_name, rec, prev, "integrity-error", False,
                          "exit 1: integrity-error/%s at %s" % (result.get("error_class") or "?", result.get("path") or result.get("revision") or "?"))
                return
            self.fail(stem, file_name, rec, prev, "malformed-result", True, "exit 1 without a well-formed RESULT: block")
            return
        if code == 2:
            self.fail(stem, file_name, rec, prev, "contract", False, "exit 2: %s" % (last_line(err_text) or "usage or contract error"))
            return
        if code == 3:
            self.defer(stem, file_name, rec, "environment", "exit 3: %s" % (last_line(err_text) or "extractor environment unavailable"))
            return
        if code == EX_TEMPFAIL:
            self.defer(stem, file_name, rec, "locked", "exit 75: %s" % (last_line(err_text) or "lock held"))
            return
        once = not (prev and prev.get("error_class") == "unknown-exit")
        self.fail(stem, file_name, rec, prev, "unknown-exit", once, "exit %d: %s" % (code, last_line(err_text) or "no RESULT"))

    def finish(self, stem, file_name, rec, state, error_class, retryable, detail) -> None:
        rec["state"] = state
        rec["error_class"] = error_class
        rec["error"] = None
        rec["retryable"] = retryable
        self.write_record(rec)
        self.report(stem, file_name, state, detail, rec)

    def defer(self, stem, file_name, rec, error_class, detail) -> None:
        rec["state"] = "deferred"
        rec["error_class"] = error_class
        rec["error"] = detail
        rec["retryable"] = True
        rec["attempts"] = max((rec.get("attempts") or 1) - 1, 0)
        rec["deadline_at"] = iso(self.now + timedelta(seconds=DEFER_SECONDS))
        self.write_record(rec)
        self.report(stem, file_name, "deferred", "%s; attempt refunded, until %s" % (detail, rec["deadline_at"]), rec)

    def fail(self, stem, file_name, rec, prev, error_class, retryable, detail) -> None:
        rec["error_class"] = error_class
        rec["error"] = detail
        rec["retryable"] = bool(retryable)
        attempts = rec.get("attempts") or 1
        if retryable:
            if attempts >= rec["attempts_allowed"]:
                rec["state"] = "exhausted"
                rec["next_attempt_at"] = None
                self.write_record(rec)
                self.report(stem, file_name, "exhausted", "%s; %d of %d attempts spent" % (detail, attempts, rec["attempts_allowed"]), rec, count_as="failed")
                return
            delay = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_CAP)
            rec["state"] = "failed"
            rec["next_attempt_at"] = iso(self.now + timedelta(seconds=delay))
            self.write_record(rec)
            self.report(stem, file_name, "failed", "%s; retry %d of %d at %s" % (detail, attempts, rec["attempts_allowed"], rec["next_attempt_at"]), rec)
            return
        rec["state"] = "failed"
        rec["next_attempt_at"] = None
        self.write_record(rec)
        self.report(stem, file_name, "failed", "%s; permanent" % detail, rec)


# --- entry point ------------------------------------------------------------

def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive integer, got %r" % text)
    if value < 1:
        raise argparse.ArgumentTypeError("expected a positive integer, got %r" % text)
    return value


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mnemon ingest", add_help=True,
                                 description="Consume mnemon-envelope/1 files from an inbox directory (docs/inbox.md).")
    ap.add_argument("inbox", metavar="<inbox-dir>", help="directory holding <envelope_id>.json envelopes")
    ap.add_argument("--dry-run", action="store_true", help="print what the pass would do; write nothing, take no lock")
    ap.add_argument("--only", metavar="<envelope_id>", help="process this envelope only")
    ap.add_argument("--limit", type=positive_int, metavar="N", help="claim at most N eligible envelopes in this pass")
    ap.add_argument("--max-attempts", type=positive_int, default=DEFAULT_MAX_ATTEMPTS, metavar="N",
                    help="attempts allowed per envelope before it is exhausted (default %d; × (1 + retries))" % DEFAULT_MAX_ATTEMPTS)
    ap.add_argument("--retry", metavar="<envelope_id>", help="reopen an exhausted or permanently failed envelope")
    ap.add_argument("--json", action="store_true", help="machine-readable pass report on stdout")
    return ap


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EX_USAGE if exc.code not in (0, None) else 0
    for value in (args.only, args.retry):
        if value is not None and not ID_RE.match(value):
            parser.print_usage(sys.stderr)
            sys.stderr.write("mnemon ingest: %r is not a valid envelope_id\n" % value)
            return EX_USAGE
    try:
        cfg = lib_config.load_config()
    except lib_config.ConfigError as exc:
        sys.stderr.write("%s\n" % exc)
        return EX_USAGE
    try:
        return Ingest(args, cfg).run()
    except UsageError as exc:
        sys.stderr.write("mnemon ingest: %s\n" % exc)
        return EX_USAGE
    except LockHeld as exc:
        sys.stderr.write("mnemon ingest: another writer holds the vault lock (%s); retry later\n" % exc)
        return EX_TEMPFAIL


if __name__ == "__main__":
    sys.exit(main())
