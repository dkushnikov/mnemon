#!/usr/bin/env python3
"""lib_vault.py — the one read-only scanner of a Mnemon vault.

docs/specs/2026-10-16-vault-store.md: `mnemon doctor --data`, `mnemon status --brief` and
`mnemon status` read the vault through this module and nothing else — one walk of
`Sources/*/`, `Synthesis/*.md`, `_inputs/*.md`, `_meta/`, the capture ledger and the archive
listing; per-folder records with the folder class (managed · damaged · legacy, S1), the
supersedes graph and the derived axes (S2), the Synthesis link resolver, the declared queues
(S5), the guide stamps (I11) and the counters of the `mnemon-doctor-data/1` document (S4).

Observational by construction: nothing is written, no directory is created, no lock is taken
(the vault lock is probed on a private descriptor that is closed at once), no network. A file
that vanishes or cannot be read between the listing and the read is counted
(`snapshot.unreadable`), never raised. The scan has no deadline of its own: the doctor's
`--budget` wraps the whole run in a worker process it can kill.

    python3 lib_vault.py --vault <path> [--state-dir <dir>] [--archive <dir>] [--charter <path>]
                         [--reader-context <path>] [--today YYYY-MM-DD] [--json | --status-counts]

Headers are read whole through the closing `---` (cap 256 KB) with the reindexer's own reader
(`knowledge-reindex.read_header`); the body hash follows the gateway's rule (`cap_py
body-sha256`: the bytes after the first `\\n---\\n` of a file that starts with `---\\n`).
Stdlib only, python 3.9.
"""

from __future__ import annotations

import argparse
import datetime
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BIN_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_ROOT = os.path.dirname(BIN_DIR)


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kr = _load_module("knowledge_reindex", "knowledge-reindex.py")   # read_header, scan_sources, build_row, …
vl = _load_module("vault_lock", "vault-lock.py")                  # vault_state_dir (the D6 rule)
if BIN_DIR not in sys.path:
    sys.path.insert(0, BIN_DIR)
from capture import resolve  # noqa: E402  (canonicalize — the one identity rule)

# S1: a managed source.md carries all four; some of them = damaged; none = legacy
MANAGED_KEYS = ("capture_id", "revision", "resource_sha256", "capture.text_sha256")
# S7: short by nature — excluded from the length test only
SHORT_TYPES = frozenset(("idea", "message", "conversation", "voice-note", "x"))
# S2 / I8: the stored vocabulary of extract.md `status:` and the tolerated legacy words
EXTRACT_STATUS_STORED = ("extracted", "dismissed")
EXTRACT_STATUS_LEGACY = ("fetch-failed", "superseded", "integrated", "processed", "bookmark")
SYNTHESIS_STATUS = ("seed", "draft", "stable")
MEDIA_TYPES = frozenset(("video", "podcast"))
SITE_TYPES = frozenset(("website", "repo", "document"))
ARXIV_HOSTS = frozenset(("arxiv.org", "www.arxiv.org"))
SECTION_RE = re.compile(r"^#{2,3}[ \t]+(Summary|Executive Summary|Key Ideas|Connections|Raw Quotes)[ \t]*$", re.M)
HAND_EDITS_RE = re.compile(r"^index-hand-edits-(\d{4}-\d{2}-\d{2})\.md$")
WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
GUIDE_COMMENT_RE = re.compile(r"<!--\s*mnemon_guide:\s*(\d+)\s*-->")
KEY_RE = re.compile(r"^([^\s:#][^:]*?):(.*)$")
NESTED_RE = re.compile(r"^[ \t]+([^\s:#\-][^:]*?):(.*)$")
ARCHIVE_SUFFIX_RE = re.compile(r"^(.*?)\s*\(in archive_dir:[^)]*\)\s*$")
ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------------------------------------
# Primitive readers
# ---------------------------------------------------------------------------------------------

def literal(value: str) -> str:
    """A frontmatter scalar as the engine reads it (capture contracts §4.4): a double-quoted value
    decodes `\\"` and `\\\\` only; a single-quoted one is verbatim to its closing quote; `#…` is
    empty; an unquoted one loses a trailing ` # comment` and surrounding whitespace."""
    v = value.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return kr.decode_dq(v[1:-1])
    if v.startswith("'"):
        return v[1:].split("'", 1)[0]
    if v.startswith("#"):
        return ""
    return re.sub(r"\s#.*$", "", v).rstrip()


def parse_block(block: str) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]], List[str]]:
    """(top, nested, keys): the top-level `key: value` scalars of a header block (the first
    occurrence wins), the `  key: value` scalars under each top-level key, and every top-level
    key in order (duplicates included — that is how a foreign `_width` is seen)."""
    top: Dict[str, str] = {}
    nested: Dict[str, Dict[str, str]] = {}
    keys: List[str] = []
    section = None
    for line in block.split("\n"):
        if not line.strip():
            continue
        if line[0] not in " \t":
            m = KEY_RE.match(line)
            if not m:
                section = None
                continue
            key = m.group(1).strip()
            keys.append(key)
            if key not in top:
                top[key] = literal(m.group(2))
                nested[key] = {}
            section = key
        elif section is not None:
            m = NESTED_RE.match(line)
            if m:
                sub = m.group(1).strip()
                if sub not in nested[section]:
                    nested[section][sub] = literal(m.group(2))
    return top, nested, keys


def split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """(block, body) of a note: the lines between the opening `---` and the closing `---`/`...`,
    and the text after it; (None, text) without a closed header."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


def body_bytes(data: bytes) -> Optional[bytes]:
    """The gateway's body (cap_py body-sha256, byte for byte): the bytes after the first `\\n---\\n`
    of a file that starts with `---\\n`; None when the rule finds no body."""
    if not data.startswith(b"---\n"):
        return None
    i = data.find(b"\n---\n", 3)
    if i < 0:
        return None
    return data[i + 5:]


def canonical_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        return resolve.canonicalize(url).url
    except resolve.InvalidURL:
        return None


def host_of(url: Optional[str]) -> str:
    if not url:
        return ""
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", url.strip())
    if not m:
        return ""
    host = m.group(1).lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    return host.split(":", 1)[0]


def parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    v = value.strip()
    return int(v) if re.fullmatch(r"-?\d+", v) else None


def parse_date(value: Optional[str]) -> Optional[datetime.date]:
    """The ISO date a frontmatter scalar starts with, or None."""
    if not value:
        return None
    m = ISO_DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def archive_reference(value: Optional[str]) -> Tuple[Optional[str], str]:
    """(name, form) of an `archive:` value: `unarchivable` (the legacy marker: raw not kept),
    the legacy suffix form `<name> (in archive_dir: …)` reduced to its name, or the plain name."""
    if value is None:
        return None, "none"
    v = value.strip()
    if not v:
        return None, "none"
    if v == "unarchivable":
        return None, "unarchivable"
    m = ARCHIVE_SUFFIX_RE.match(v)
    if m:
        return m.group(1).strip(), "legacy-suffix"
    return v, "plain"


def resolve_links(text: str) -> Tuple[List[str], int]:
    """The S2 resolver: `[[Sources/<rev>/extract…]]`, `[[Sources/<rev>/source…]]`, `[[Sources/<rev>]]`
    (and the `![[…]]` embeds) resolve to `<rev>`; every other wikilink is unresolved."""
    resolved: List[str] = []
    unresolved = 0
    for m in WIKILINK_RE.finditer(text):
        target = m.group(1).strip().lstrip("/")
        if target.startswith("Sources/"):
            parts = target[len("Sources/"):].split("/")
            rev = parts[0].strip()
            rest = "/".join(p.strip() for p in parts[1:])
            if rev and rest in ("", "extract", "extract.md", "source", "source.md"):
                resolved.append(rev)
                continue
        unresolved += 1
    return resolved, unresolved


# ---------------------------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------------------------

def _read_bytes(path: str, snapshot: dict) -> Optional[bytes]:
    """The file's bytes, or None (counted as unreadable) when it vanished or cannot be read."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        snapshot["unreadable"] += 1
        return None


def _read_text(path: str, snapshot: dict) -> Optional[str]:
    data = _read_bytes(path, snapshot)
    return None if data is None else data.decode("utf-8", "replace")


def _header(path: str, data: bytes, snapshot: dict) -> Tuple[Optional[str], str]:
    """The reindexer's header reader (§12 A2) on a file whose bytes were just read; a file that
    vanished in between is unreadable, not malformed."""
    block, state = kr.read_header(Path(path))
    if state == "malformed" and not os.path.isfile(path):
        snapshot["unreadable"] += 1
        return None, "unreadable"
    return block, state


def _new_record(name: str) -> dict:
    return {
        "name": name, "has_source": False, "has_extract": False,
        "source_state": "missing", "extract_state": "missing",
        "src": {}, "src_nested": {}, "src_keys": [], "ex": {}, "ex_nested": {}, "ex_keys": [],
        "body_sha256": None, "body_words": None, "body_bytes": None, "ex_sections": [],
        "klass": None, "managed_missing": [], "underscore_keys": [],
    }


def read_folder(sources: str, name: str, snapshot: dict) -> dict:
    """One `Sources/<name>/` record: both files read once, the class of S1, the derived scalars."""
    rec = _new_record(name)
    folder = os.path.join(sources, name)
    sp = os.path.join(folder, "source.md")
    ep = os.path.join(folder, "extract.md")

    if os.path.isfile(sp):
        rec["has_source"] = True
        data = _read_bytes(sp, snapshot)
        if data is None:
            rec["source_state"] = "unreadable"
        else:
            block, state = _header(sp, data, snapshot)
            rec["source_state"] = state
            if block is not None:
                rec["src"], rec["src_nested"], rec["src_keys"] = parse_block(block)
            body = body_bytes(data)
            if body is not None:
                rec["body_sha256"] = hashlib.sha256(body).hexdigest()
                rec["body_bytes"] = len(body)
                rec["body_words"] = len(body.decode("utf-8", "replace").split())
            elif state == "none":          # no header at all: the whole file is the body
                rec["body_bytes"] = len(data)
                rec["body_words"] = len(data.decode("utf-8", "replace").split())

    if os.path.isfile(ep):
        rec["has_extract"] = True
        data = _read_bytes(ep, snapshot)
        if data is None:
            rec["extract_state"] = "unreadable"
        else:
            block, state = _header(ep, data, snapshot)
            rec["extract_state"] = state
            text = data.decode("utf-8", "replace")
            if state == "unreadable":
                block = None
            elif block is None:
                # the reindexer reads a legacy extract.md without a closing delimiter whole
                block = kr.frontmatter_block(text)
            if block is not None:
                rec["ex"], rec["ex_nested"], rec["ex_keys"] = parse_block(block)
            rec["ex_sections"] = sorted(set(SECTION_RE.findall(text)))

    src, ex = rec["src"], rec["ex"]
    cap = rec["src_nested"].get("capture", {})
    if rec["source_state"] in ("ok", "none"):
        present = [k for k in MANAGED_KEYS[:3] if k in src]
        if "text_sha256" in cap:
            present.append("capture.text_sha256")
        filled = [k for k in present if (cap.get("text_sha256") if k == "capture.text_sha256" else src.get(k))]
        if len(filled) == len(MANAGED_KEYS):
            rec["klass"] = "managed"
        elif present:
            rec["klass"] = "damaged"
            rec["managed_missing"] = [k for k in MANAGED_KEYS if k not in filled]
        else:
            rec["klass"] = "legacy"
    elif rec["source_state"] == "malformed":
        rec["klass"] = "legacy"     # S1: keys the reader cannot see are indistinguishable from legacy

    rec["url"] = src.get("url") or None
    rec["canonical_url"] = src.get("canonical_url") or None
    rec["canonical"] = canonical_of(rec["canonical_url"] or rec["url"])
    rec["resource_sha256"] = src.get("resource_sha256") or None
    rec["supersedes"] = kr.supersedes_target(src.get("supersedes"))
    rec["archive"] = src.get("archive") if "archive" in src else None
    rec["source_type"] = (ex.get("source_type") or src.get("source_type") or "").strip().lower() or None
    rec["status"] = (ex.get("status") or "").strip()
    rec["rating_raw"] = ex.get("rating")
    rec["rating"] = parse_int(rec["rating_raw"])
    rec["tier"] = (src.get("tier") or ex.get("tier") or "").strip().lower() or None
    rec["context"] = (src.get("context") or "").strip()
    rec["cc_vault"] = (rec["ex_nested"].get("capture_context", {}).get("vault") or "").strip()
    rec["completeness"] = cap.get("completeness") if "completeness" in cap else None
    rec["raw_store"] = (cap.get("raw_store") or "").strip()
    rec["text_sha256"] = cap.get("text_sha256")
    rec["origin"] = (src.get("origin") or "").strip()
    rec["fetch_status"] = (src.get("fetch_status") or "").strip()
    rec["src_status"] = (src.get("status") or "").strip()
    rec["underscore_keys"] = [k for k in rec["src_keys"] if k.startswith("_")]
    rec["ex_capture_id"] = ex.get("capture_id")
    rec["ex_revision"] = ex.get("revision")
    rec["ex_url"] = ex.get("url") or None
    rec["is_url"] = bool(rec["canonical_url"]) or rec["origin"] == "url"
    rec["host"] = host_of(rec["canonical_url"] or rec["url"])
    return rec


def resource_relation(a: dict, b: dict) -> str:
    """same · different · incomparable — by `resource_sha256` when both carry it (the deciding
    key), else by the canonical URL v1 when both have one (S1)."""
    if a["resource_sha256"] and b["resource_sha256"]:
        return "same" if a["resource_sha256"] == b["resource_sha256"] else "different"
    if a["canonical"] and b["canonical"]:
        return "same" if a["canonical"] == b["canonical"] else "different"
    return "incomparable"


def scan_sources(vault: str, snapshot: dict) -> Tuple[List[dict], dict]:
    """Every `Sources/*/` folder (sorted, dot-names skipped) as a record, and the supersedes graph
    computed by the reindexer's scan (one algorithm for `reindex`, `status` and the doctor)."""
    sources = os.path.join(vault, "Sources")
    records: List[dict] = []
    names: List[str] = []
    if os.path.isdir(sources):
        try:
            names = sorted(n for n in os.listdir(sources)
                           if not n.startswith(".") and os.path.isdir(os.path.join(sources, n)))
        except OSError:
            snapshot["unreadable"] += 1
            names = []
    snapshot["folders_listed"] = len(names)
    for name in names:
        records.append(read_folder(sources, name, snapshot))
    snapshot["folders_scanned"] = len(records)

    ks = kr.scan_sources([Path(sources) / n for n in names]) if names else \
        {"successors": {}, "dangling": [], "cycles": [], "legacy": set(), "malformed": []}
    by_name = {r["name"]: r for r in records}
    superseded = set(ks["successors"])
    for cyc in ks["cycles"]:
        superseded.update(cyc)
    cross: List[Tuple[str, str]] = []
    incomparable = 0
    for old, news in ks["successors"].items():
        for new in news:
            rel = resource_relation(by_name[old], by_name[new])
            if rel == "different":
                cross.append((new, old))
            elif rel == "incomparable":
                incomparable += 1
    edges = sum(len(v) for v in ks["successors"].values()) + len(ks["dangling"]) + sum(len(c) for c in ks["cycles"])
    graph = {
        "successors": ks["successors"], "dangling": ks["dangling"], "cycles": ks["cycles"],
        "malformed": ks["malformed"], "edges": edges, "superseded": superseded,
        "cross_resource": cross, "incomparable": incomparable,
        "fan_out": sorted(old for old, news in ks["successors"].items() if len(news) >= 2),
        "current_incomplete": bool(ks["cycles"] or ks["dangling"]),
    }
    for r in records:
        r["superseded"] = r["name"] in superseded
        r["current"] = r["has_source"] and not r["superseded"]
    return records, graph


def scan_synthesis(vault: str, snapshot: dict) -> dict:
    d = os.path.join(vault, "Synthesis")
    out = {"present": os.path.isdir(d), "notes": []}
    if not out["present"]:
        return out
    try:
        names = sorted(os.listdir(d))
    except OSError:
        snapshot["unreadable"] += 1
        return out
    for n in names:
        p = os.path.join(d, n)
        if n.startswith(".") or not n.endswith(".md") or not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            snapshot["unreadable"] += 1
            continue
        text = _read_text(p, snapshot)
        if text is None:
            continue
        block, _ = split_frontmatter(text)
        top = parse_block(block)[0] if block is not None else {}
        resolved, unresolved = resolve_links(text)
        out["notes"].append({
            "name": n[:-3], "status": (top.get("status") or "").strip(), "has_status": "status" in top,
            "drafted_by": (top.get("drafted_by") or "").strip(), "created": top.get("created"),
            "mtime": datetime.date.fromtimestamp(st.st_mtime), "links": resolved, "unresolved": unresolved,
        })
    return out


def scan_inputs(vault: str, snapshot: dict) -> dict:
    d = os.path.join(vault, "_inputs")
    out = {"present": os.path.isdir(d), "queues": [], "untyped": [], "hand_edits": None, "pending_writes": 0}
    if not out["present"]:
        return out
    try:
        names = sorted(os.listdir(d))
    except OSError:
        snapshot["unreadable"] += 1
        return out
    hand: List[Tuple[str, str, int]] = []
    for n in names:
        p = os.path.join(d, n)
        if n.startswith(".") or not n.endswith(".md") or not os.path.isfile(p):
            continue
        text = _read_text(p, snapshot)
        if text is None:
            continue
        m = HAND_EDITS_RE.match(n)
        if m:
            hand.append((m.group(1), n, len(kr.HAND_EDIT_RE.findall(text))))
            continue
        block, body = split_frontmatter(text)
        top = parse_block(block)[0] if block is not None else {}
        q = {"name": n, "type": (top.get("type") or "").strip(), "since": top.get("since"),
             "parked": top.get("parked"), "parked_until": top.get("parked_until"), "parked_why": top.get("parked_why"),
             "items": sum(1 for ln in body.split("\n") if ln.startswith("- "))}
        (out["queues"] if q["type"] == "queue" else out["untyped"]).append(q)
    if hand:
        date, name, rows = max(hand)
        out["hand_edits"] = {"date": date, "name": name, "rows": rows}
    pw = os.path.join(d, "pending-writes")
    if os.path.isdir(pw):
        for root, dirs, files in os.walk(pw):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            out["pending_writes"] += sum(1 for f in files if not f.startswith("."))
    return out


def scan_reader_context(path: Optional[str], snapshot: dict) -> dict:
    out = {"path": path, "exists": False, "synced": None, "threshold": None}
    if not path or not os.path.isfile(path):
        return out
    text = _read_text(path, snapshot)
    if text is None:
        return out
    out["exists"] = True
    block, _ = split_frontmatter(text)
    top = parse_block(block)[0] if block is not None else {}
    out["synced"] = top.get("synced")
    out["threshold"] = top.get("staleness_threshold_days")
    return out


def scan_charter(path: Optional[str], snapshot: dict) -> dict:
    """The charter's `## Contexts` (parsed like cap_charter_contexts: `### <name>`, first token,
    lowercased, deduplicated, fences skipped; `personal` without a charter) and the raw lines of
    `## Radius` (S5) for the doctor to parse."""
    out = {"path": path, "exists": False, "contexts": ["personal"], "radius_lines": [], "has_radius": False}
    if not path or not os.path.isfile(path):
        return out
    text = _read_text(path, snapshot)
    if text is None:
        return out
    out["exists"] = True
    contexts: List[str] = []
    fence = False
    section = None
    for line in text.split("\n"):
        if line.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if line.startswith("## "):
            if re.match(r"^## Contexts[ \t]*$", line):
                section = "contexts"
            elif re.match(r"^## Radius[ \t]*$", line):
                section = "radius"
                out["has_radius"] = True
            else:
                section = None
            continue
        if line.startswith("# "):
            section = None
            continue
        if section == "contexts" and line.startswith("### "):
            parts = line[4:].strip().split()
            name = parts[0].lower() if parts else ""
            if name and name not in contexts:
                contexts.append(name)
        elif section == "radius" and line.strip():
            out["radius_lines"].append(line.rstrip())
    if contexts:
        out["contexts"] = contexts
    return out


def scan_ledger(state_dir: Optional[str], snapshot: dict) -> dict:
    out = {"state_dir": state_dir, "present": False, "receipts": [], "unreadable": 0}
    if not state_dir:
        return out
    cdir = os.path.join(state_dir, "captures")
    if not os.path.isdir(cdir):
        return out
    out["present"] = True
    try:
        names = sorted(os.listdir(cdir))
    except OSError:
        snapshot["unreadable"] += 1
        return out
    for n in names:
        if n.startswith(".") or not n.endswith(".json"):
            continue
        p = os.path.join(cdir, n)
        try:
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
        except OSError:
            snapshot["unreadable"] += 1
            continue
        except ValueError:
            out["unreadable"] += 1
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "mnemon-capture-receipt/1":
            out["unreadable"] += 1
            continue

        def s(key):
            v = rec.get(key)
            return v if isinstance(v, str) and v else None

        out["receipts"].append({"id": n[:-5], "capture_id": s("capture_id"), "state": s("state"),
                                "revision": s("revision"), "text_sha256": s("text_sha256"),
                                "resolved_to_revision": s("resolved_to_revision")})
    return out


def scan_archive(archive_dir: Optional[str], snapshot: dict) -> dict:
    out = {"dir": archive_dir or None, "configured": bool(archive_dir), "present": False, "entries": []}
    if archive_dir and os.path.isdir(archive_dir):
        try:
            out["entries"] = sorted(n for n in os.listdir(archive_dir) if not n.startswith("."))
            out["present"] = True
        except OSError:
            snapshot["unreadable"] += 1
    return out


def guide_stamp(path: str, snapshot: dict) -> dict:
    """`mnemon_guide: <n>` of a Guide copy: the frontmatter key, else an HTML comment
    `<!-- mnemon_guide: n -->` anywhere in the file."""
    out = {"path": path, "exists": False, "version": None}
    if not os.path.isfile(path):
        return out
    out["exists"] = True
    text = _read_text(path, snapshot)
    if text is None:
        return out
    block, _ = split_frontmatter(text)
    raw = parse_block(block)[0].get("mnemon_guide") if block is not None else None
    if not raw:
        m = GUIDE_COMMENT_RE.search(text)
        raw = m.group(1) if m else None
    out["version"] = int(raw) if raw and raw.strip().isdigit() else None
    return out


def engine_guide_version(snapshot: Optional[dict] = None) -> Optional[int]:
    """The Guide version this engine ships: the stamp of vault-template/CLAUDE.md."""
    return guide_stamp(os.path.join(ENGINE_ROOT, "vault-template", "CLAUDE.md"), snapshot if snapshot is not None
                       else {"unreadable": 0})["version"]


def probe_lock(state_dir: Optional[str]) -> str:
    """`held` · `free` · `none` (no lock file yet — never created) · `error`: a non-blocking flock
    on a private descriptor that is closed at once (closing releases whatever it took; nothing
    here calls LOCK_UN) — the rule of `mnemon doctor`'s vault-lock line."""
    if not state_dir:
        return "none"
    path = os.path.join(state_dir, "lock")
    if not os.path.exists(path):
        return "none"
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return "error"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return "held"
            return "error"
        return "free"
    finally:
        os.close(fd)


def default_reader_context(vault: str) -> str:
    """The place `mnemon init` looks: `_meta/reader-context.md`, else `reader-context.md`."""
    meta = os.path.join(vault, "_meta", "reader-context.md")
    return meta if os.path.isfile(meta) else os.path.join(vault, "reader-context.md")


def scan_vault(vault: str, state_dir: Optional[str] = None, archive_dir: Optional[str] = None,
               charter: Optional[str] = None, reader_context: Optional[str] = None,
               today: Optional[datetime.date] = None) -> dict:
    """The whole read-only snapshot of a vault. `state_dir` defaults to the D6 ledger dir of the
    physical vault path; `charter` to `_meta/charter.md`; `reader_context` to the init rule."""
    vault = os.path.abspath(vault)
    snapshot = {"unreadable": 0, "budget_s": None, "complete": True, "folders_listed": 0,
                "folders_scanned": 0, "lock_held": False, "lock": "none"}
    if state_dir is None:
        state_dir = vl.vault_state_dir(vault)
    if charter is None:
        charter = os.path.join(vault, "_meta", "charter.md")
    if reader_context is None:
        reader_context = default_reader_context(vault)
    snapshot["lock"] = probe_lock(state_dir)
    snapshot["lock_held"] = snapshot["lock"] == "held"

    folders, graph = scan_sources(vault, snapshot)
    index_path = os.path.join(vault, "index.md")
    index_text = _read_text(index_path, snapshot) if os.path.isfile(index_path) else None
    scan = {
        "vault": vault, "state_dir": state_dir, "today": today or datetime.date.today(),
        "folders": folders, "by_name": {r["name"]: r for r in folders}, "graph": graph,
        "index_text": index_text,
        "synthesis": scan_synthesis(vault, snapshot),
        "inputs": scan_inputs(vault, snapshot),
        "reader_context": scan_reader_context(reader_context, snapshot),
        "charter": scan_charter(charter, snapshot),
        "ledger": scan_ledger(state_dir, snapshot),
        "archive": scan_archive(archive_dir, snapshot),
        "guide": {"claude_md": guide_stamp(os.path.join(vault, "CLAUDE.md"), snapshot),
                  "protocol": guide_stamp(os.path.join(vault, "_meta", "Protocol.md"), snapshot)},
        "snapshot": snapshot,
    }
    scan["counters"] = counters(scan)
    return scan


# ---------------------------------------------------------------------------------------------
# Derived values and counters (S2, S4)
# ---------------------------------------------------------------------------------------------

def queue_is_parked(q: dict, today: datetime.date) -> bool:
    """S5: `parked:` parks the whole file until `parked_until` (none → indefinitely); a
    `parked_until` in the past ends the parking."""
    if not (q.get("parked") or "").strip():
        return False
    until = parse_date(q.get("parked_until"))
    return until is None or until >= today


def status_breakdown(folders: List[dict]) -> dict:
    """I8 for extract.md: the stored words, the tolerated legacy words, empty, unknown (named)."""
    out = {"extracted": 0, "dismissed": 0, "legacy_words": 0, "legacy": {}, "empty": 0, "unknown": 0, "unknown_words": {}}
    for r in folders:
        if not r["has_extract"] or r["extract_state"] == "unreadable":
            continue
        s = r["status"]
        if s in EXTRACT_STATUS_STORED:
            out[s] += 1
        elif s in EXTRACT_STATUS_LEGACY:
            out["legacy_words"] += 1
            out["legacy"][s] = out["legacy"].get(s, 0) + 1
        elif not s:
            out["empty"] += 1
        else:
            out["unknown"] += 1
            out["unknown_words"][s] = out["unknown_words"].get(s, 0) + 1
    return out


def synthesis_breakdown(notes: List[dict]) -> dict:
    out = {"seed": 0, "draft": 0, "stable": 0, "empty": 0, "unknown": 0, "unknown_words": {}}
    for n in notes:
        s = n["status"]
        if s in SYNTHESIS_STATUS:
            out[s] += 1
        elif not s:
            out["empty"] += 1
        else:
            out["unknown"] += 1
            out["unknown_words"][s] = out["unknown_words"].get(s, 0) + 1
    return out


def unratified(note: dict) -> bool:
    """S2: drafted_by non-empty and status ∈ {draft, empty} (seeds are H2, stable is ratified)."""
    return bool(note["drafted_by"]) and note["status"] in ("draft", "")


def unknown_authorship(note: dict) -> bool:
    return not note["drafted_by"] and not note["status"]


def counters(scan: dict) -> dict:
    folders = scan["folders"]
    g = scan["graph"]
    notes = scan["synthesis"]["notes"]
    today = scan["today"]
    existing = set(scan["by_name"])
    integrated: set = set()
    filed: set = set()
    for n in notes:
        if n["status"] == "stable":
            integrated.update(rev for rev in n["links"] if rev in existing)
        elif n["status"] in ("seed", "draft"):
            filed.update(rev for rev in n["links"] if rev in existing)
    queues = scan["inputs"]["queues"]
    c = {
        "folders": len(folders),
        "managed": sum(1 for r in folders if r["klass"] == "managed"),
        "damaged": sum(1 for r in folders if r["klass"] == "damaged"),
        "legacy": sum(1 for r in folders if r["klass"] == "legacy"),
        "unreadable_folders": sum(1 for r in folders if r["source_state"] == "unreadable"),
        "without_source": sum(1 for r in folders if not r["has_source"]),
        "current": sum(1 for r in folders if r["current"]),
        "current_incomplete": g["current_incomplete"],
        "superseded": len(g["superseded"]),
        "extracted": sum(1 for r in folders if r["has_extract"]),
        "captured": sum(1 for r in folders if r["has_source"] and not r["has_extract"]),
        "edges": g["edges"], "dangling": len(g["dangling"]), "cycles": len(g["cycles"]),
        "cross_resource": len(g["cross_resource"]), "fan_out": len(g["fan_out"]),
        "malformed": len(g["malformed"]),
        "synthesis": len(notes),
        "seeds": sum(1 for n in notes if n["status"] == "seed"),
        "drafts_unratified": sum(1 for n in notes if unratified(n)),
        "stable": sum(1 for n in notes if n["status"] == "stable"),
        "unknown_authorship": sum(1 for n in notes if unknown_authorship(n)),
        "integrated": len(integrated), "filed": len(filed - integrated),
        "links_unresolved": sum(n["unresolved"] for n in notes),
        "queues_declared": len(queues),
        "queues_parked": sum(1 for q in queues if queue_is_parked(q, today)),
        "queues_untyped": len(scan["inputs"]["untyped"]),
        "pending_writes_legacy": scan["inputs"]["pending_writes"],
        "receipts": len(scan["ledger"]["receipts"]), "ledger_present": scan["ledger"]["present"],
        "archive_entries": len(scan["archive"]["entries"]),
        "status": status_breakdown(folders),
        "synthesis_status": synthesis_breakdown(notes),
    }
    return c


# ---------------------------------------------------------------------------------------------
# CLI — counters for `mnemon status` (the one Extracted) and for inspection
# ---------------------------------------------------------------------------------------------

def _json_default(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    raise TypeError("not serializable: %r" % (type(o),))


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Read-only scan of a Mnemon vault (counters)")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--archive", default=None)
    ap.add_argument("--charter", default=None)
    ap.add_argument("--reader-context", default=None)
    ap.add_argument("--today", default=None, help="fix the clock (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true", help="the counters and the snapshot as JSON")
    ap.add_argument("--status-counts", action="store_true", help="key=value lines for `mnemon status`")
    args = ap.parse_args(argv[1:])
    today = datetime.date.fromisoformat(args.today) if args.today else None
    scan = scan_vault(args.vault, state_dir=args.state_dir, archive_dir=args.archive, charter=args.charter,
                      reader_context=args.reader_context, today=today)
    c = scan["counters"]
    if args.status_counts:
        st = c["status"]
        for key, value in (("folders", c["folders"]), ("extracted", c["extracted"]), ("captured", c["captured"]),
                           ("current", c["current"]), ("superseded", c["superseded"]), ("managed", c["managed"]),
                           ("status_extracted", st["extracted"]), ("status_dismissed", st["dismissed"]),
                           ("status_legacy_words", st["legacy_words"]), ("status_empty", st["empty"]),
                           ("status_unknown", st["unknown"]), ("unreadable", scan["snapshot"]["unreadable"])):
            print("%s=%s" % (key, value))
        return 0
    print(json.dumps({"vault": scan["vault"], "snapshot": scan["snapshot"], "counters": c},
                     ensure_ascii=False, indent=1, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
