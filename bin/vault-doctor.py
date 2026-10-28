#!/usr/bin/env python3
"""vault-doctor.py — the data group of `mnemon doctor`, the research document (phase 3b) and the
line of `mnemon status --brief` (docs/specs/2026-10-16-vault-store.md S3–S5, S7;
docs/specs/2026-10-27-research-interface.md §8–§9).

    vault-doctor.py [--json [--research] | --envelope | --tsv | --list <check> | --brief [--json]] [--budget <s>]
                    [--today YYYY-MM-DD] [--vault <path> [--archive <dir>] [--charter <path>] [--reader-context <path>]
                     [--state-dir <dir>] [--instance <name>] [--writer-host <host>] [--guide-version <n>]
                     [--thin-words N] [--stub-words N] [--arxiv-words N] [--rating-hi-share N]
                     [--wiki-pages-per-collection N] [--wiki-words N]]

Nineteen checks with stable names — 11 of the store (I1–I11), 5 of data quality (Q1–Q5), 3 of
the human loop (H1–H3) — on one read-only snapshot of the vault (bin/lib_vault.py), and on the
same snapshot the research document `mnemon-doctor-research/1`: ten checks `research/<name>` —
the collection schemas and grammar, the age of open dossiers, the wiki pages' schema, binding
(`unbound` · `suppressed`), staleness, inputs, evidence (`uncited` · `weak-evidence`), orphans,
budget, and the research sections of the index — the states derived by lib_research.validate_wiki
and never stored. Severity by cause (S4): `fail` = a managed-schema or graph violation, an
unbound or vetoed page, `warn` = a legacy mismatch, a stale derivative or a threshold passed; a
check that cannot run here says so (`probed: false` + `reason`) and is never a `0 fail`. Tool
problems are not checks: they go to `diagnostics` (`radius-parse` warn · `error` fail · `budget`
warn), printed as `<level> doctor/<code> — …`. Nothing is fixed, written or created — the vault
lock is probed, never taken.

Output: `ok|warn|fail <group>/<name> — <detail>` (the dispatcher's line; the 19 data checks, their
diagnostics, then the 10 research checks and theirs), `--tsv` the same as `status<TAB>name<TAB>detail`
(what bin/mnemon routes through `_check`), `--json` the frozen `mnemon-doctor-data/1` document
(always all 19 checks; with `--research` the research document instead), `--envelope` both as
`{"data": …, "research": …}` (the dispatcher's input), `--list <check>` every item of one check
(`<id>\t<reason or class>\t<rating>\t<words>`; `research/<name>` for those), `--brief` the one
line of `mnemon status --brief` with its `research: N pages · K fail · M warn` part (`--json`: the
counters and the summaries).

`--budget <s>` (the default for `--brief`: 10) is one deadline around the whole run: the scan and
the checks execute in a child worker (this script with `--worker`) that prints the data document
as soon as it is built and the research one after it, one JSON per line; the parent waits at most
the budget, terminates then kills the worker when it expires, keeps the document that arrived and
marks the one the deadline hit `not probed` — `data: not probed (budget 10 s exceeded)` /
`research: not probed (…)`, a `budget` diagnostic, exit 0 — rather than guess.

Without `--vault` the configuration comes from `lib_config.load_config()` (the instance the
dispatcher resolved); with `--vault` the tool runs on defaults, for tests. Exit 0 unless an
exception (the dispatcher derives its exit from the fail lines); `--today` fixes the clock.
Test hooks, honoured only under MNEMON_TEST_HOOKS=1: MNEMON_TEST_DOCTOR_TODAY=<YYYY-MM-DD> (the
clock when no --today is given — the dispatcher passes none), MNEMON_TEST_DOCTOR_STALL=<s> (the worker
sleeps before scanning; with MNEMON_TEST_DOCTOR_PIDFILE it writes its pid first),
MNEMON_TEST_DOCTOR_STALL_RESEARCH=<s> (the worker sleeps after printing the data document, before the
research one) and MNEMON_TEST_DOCTOR_RAISE=<check> (that check raises; a research check too). Stdlib
only, python 3.9.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_vault as lv  # noqa: E402
import lib_research as lr  # noqa: E402

SCHEMA = "mnemon-doctor-data/1"
# the 19 stable names (S3) and the door each one prints
FIX = (
    ("store/source-integrity", "operator"), ("store/identity", "mnemon audit --canonical"),
    ("store/index", "mnemon reindex"), ("store/supersedes", "operator"), ("store/archive", "mnemon audit"),
    ("store/completeness", "mnemon add --recapture"), ("store/ledger", "mnemon add --resume"),
    ("store/status-vocabulary", "operator"), ("store/context", "operator"), ("store/extract-consistency", "operator"),
    ("store/guide-version", "operator"),
    ("data/thin", "mnemon add --recapture"), ("data/arxiv-abstract", "mnemon add --recapture"), ("data/stubs", "operator"),
    ("data/rating-scale", "operator"), ("data/reader-context", "operator"),
    ("human/ratification", "operator"), ("human/seeds", "operator"), ("human/queues", "operator"),
)
CHECK_NAMES = tuple(name for name, _ in FIX)
FIX_OF = dict(FIX)
DEFAULTS = {"thin_words": 300, "stub_words": 40, "arxiv_words": 600, "rating_hi_share": 20}
# the checks that do not read Sources/ — still probed when the folder listing itself fails
FOLDER_FREE_CHECKS = ("store/guide-version", "data/reader-context", "human/ratification", "human/seeds", "human/queues")
RATING_HI = 7
READER_CONTEXT_DEFAULT_DAYS = 14
COMPLETENESS_VALUES = ("complete", "partial", "blocked", "unknown")
# S5 — one form per key; integers; days. Any other line of the section is a radius-parse diagnostic.
RADIUS_RES = (
    ("seeds", re.compile(r"^seeds:\s*(\d+)\s+open\s*[·,;]\s*(\d+)\s+days?\s*$")),
    ("drafts", re.compile(r"^drafts:\s*(\d+)\s+unratified\s*[·,;]\s*(\d+)\s+days?\s*$")),
    ("queues", re.compile(r"^queues:\s*(\d+)\s+days?\s*$")),
)
RADIUS_DEFAULTS = {"seeds": (10, 30), "drafts": (5, 45), "queues": (None, 30)}
SHOW = 5           # items named in a text detail; --json and --list carry them all
DEFAULT_BRIEF_BUDGET = 10.0


class Check:
    def __init__(self, name: str):
        self.name = name
        self.status = "ok"
        self.probed = True
        self.reason: Optional[str] = None
        self.detail = ""
        self.counts: Dict[str, object] = {}
        self.threshold: Dict[str, object] = {}
        self.fix = FIX_OF[name]
        self.items: List[dict] = []

    def raise_to(self, status: str) -> None:
        order = {"ok": 0, "warn": 1, "fail": 2}
        if order[status] > order[self.status]:
            self.status = status

    def not_probed(self, reason: str, status: str = "ok") -> "Check":
        self.probed = False
        self.reason = reason
        self.status = status
        self.detail = "not probed: " + reason
        return self

    def add(self, status: str, item: dict) -> None:
        self.items.append(item)
        self.raise_to(status)

    def as_json(self) -> dict:
        return {"name": self.name, "status": self.status, "probed": self.probed, "reason": self.reason,
                "detail": self.detail, "counts": self.counts, "threshold": self.threshold, "fix": self.fix,
                "items": self.items}


def item(rec: dict, reason: Optional[str] = None, klass: Optional[str] = None, **extra) -> dict:
    out = {"id": rec["name"], "rating": rec.get("rating"), "words": rec.get("body_words")}
    if klass is not None:
        out["class"] = klass
    if reason is not None:
        out["reason"] = reason
    out.update(extra)
    return out


def names(seq, n: int = SHOW) -> str:
    seq = list(seq)
    return ", ".join(str(x) for x in seq[:n]) + (", …" if len(seq) > n else "")


def plural(n: int, one: str, many: Optional[str] = None) -> str:
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def age_days(today: datetime.date, when: datetime.date) -> int:
    return (today - when).days


# ---------------------------------------------------------------------------------------------
# Thresholds: config keys (data) and the charter's `## Radius` (human loop)
# ---------------------------------------------------------------------------------------------

def parse_radius(lines: List[str]) -> Tuple[Dict[str, Tuple[Optional[int], int]], List[str], List[str]]:
    """(radius, defaulted keys, unparsable lines): S5 — exactly one form per key; a line of the
    section that matches none of the three is reported (`radius-parse`), never silently defaulted;
    a key without a valid line takes the engine default (marked `(defaults)`)."""
    radius: Dict[str, Tuple[Optional[int], int]] = {}
    errors: List[str] = []
    for raw in lines:
        line = raw.strip()
        for key, rx in RADIUS_RES:
            m = rx.match(line)
            if m:
                if key not in radius:
                    radius[key] = (None, int(m.group(1))) if key == "queues" else (int(m.group(1)), int(m.group(2)))
                else:
                    errors.append('duplicate %s: line "%s" (first kept)' % (key, line))
                break
        else:
            errors.append('cannot parse "%s" (defaults used)' % line)
    defaulted = [k for k in ("seeds", "drafts", "queues") if k not in radius]
    for k in defaulted:
        radius[k] = RADIUS_DEFAULTS[k]
    return radius, defaulted, errors


def radius_label(key: str, radius: dict, defaulted: List[str]) -> str:
    count, days = radius[key]
    text = ("(radius %d / %d d)" % (count, days)) if count is not None else ("(radius %d d)" % days)
    return text + (" (defaults)" if key in defaulted else "")


# ---------------------------------------------------------------------------------------------
# I — the store
# ---------------------------------------------------------------------------------------------

def check_source_integrity(scan: dict, cfg: dict) -> Check:
    c = Check("store/source-integrity")
    managed_ok = 0
    bad: List[str] = []
    damaged: List[str] = []
    legacy = 0
    unclosed: List[str] = []
    empty: List[str] = []
    underscore: List[str] = []
    for r in scan["folders"]:
        if not r["has_source"] or r["source_state"] == "unreadable":
            continue
        k = r["klass"]
        if k == "managed":
            reasons = []
            if r["body_sha256"] is None:
                reasons.append("no body under the header (nothing to hash)")
            elif r["body_sha256"] != r["text_sha256"]:
                reasons.append("body sha256 %s… ≠ capture.text_sha256 %s…" % (r["body_sha256"][:12], (r["text_sha256"] or "")[:12]))
            for u in r["underscore_keys"]:
                reasons.append("foreign key `%s`" % u)
            if reasons:
                bad.append(r["name"])
                c.add("fail", item(r, "; ".join(reasons), "managed"))
            else:
                managed_ok += 1
        elif k == "damaged":
            damaged.append(r["name"])
            c.add("fail", item(r, "partial managed schema (missing: %s)" % ", ".join(r["managed_missing"]), "damaged"))
        elif k == "legacy":
            legacy += 1
            if r["source_state"] == "malformed":
                unclosed.append(r["name"])
                c.add("warn", item(r, "header not closed within %d KB" % (lv.kr.HEADER_CAP // 1024), "legacy"))
            elif not r["body_words"]:
                empty.append(r["name"])
                c.add("warn", item(r, "empty body", "legacy"))
            if r["underscore_keys"]:
                underscore.append(r["name"])
                c.add("warn", item(r, "foreign key " + ", ".join("`%s`" % u for u in r["underscore_keys"]), "legacy"))
    c.counts = {"managed_ok": managed_ok, "managed_bad": len(bad), "damaged": len(damaged), "legacy": legacy,
                "legacy_unclosed": len(unclosed), "legacy_empty": len(empty), "legacy_underscore": len(underscore)}
    parts = ["%d managed bodies match text_sha256" % managed_ok + (", %d do not (%s)" % (len(bad), names(bad)) if bad else "")]
    parts.append("%d damaged" % len(damaged) + (" (%s)" % names(damaged) if damaged else ""))
    if unclosed or empty or underscore:
        legacy_parts = []
        if unclosed:
            legacy_parts.append("%s not closed (%s)" % (plural(len(unclosed), "header"), names(unclosed)))
        if empty:
            legacy_parts.append("%s (%s)" % (plural(len(empty), "empty body", "empty bodies"), names(empty)))
        if underscore:
            legacy_parts.append("%d with `_` keys (%s)" % (len(underscore), names(underscore)))
        parts.append("%d legacy: " % legacy + " · ".join(legacy_parts))
    else:
        parts.append("%d legacy headers closed, bodies present, no `_` keys" % legacy)
    c.detail = "; ".join(parts)
    return c


def check_identity(scan: dict, cfg: dict) -> Check:
    c = Check("store/identity")
    current = [r for r in scan["folders"] if r["current"] and r["source_state"] != "unreadable"]
    by_sha: Dict[str, List[str]] = {}
    by_canon: Dict[str, List[str]] = {}
    unkeyed = 0
    for r in current:
        if r["klass"] == "managed" and r["resource_sha256"]:
            by_sha.setdefault(r["resource_sha256"], []).append(r["name"])
        if r["canonical"]:
            by_canon.setdefault(r["canonical"], []).append(r["name"])
        if not r["resource_sha256"] and not r["canonical"]:
            unkeyed += 1
    groups: List[Tuple[str, List[str]]] = []
    seen: set = set()
    for label, index in (("resource_sha256", by_sha), ("canonical", by_canon)):
        for key, members in sorted(index.items()):
            if len(members) >= 2 and frozenset(members) not in seen:
                seen.add(frozenset(members))
                groups.append((label + " " + key, members))
    # severity by cause (S4): a duplicate that includes a managed revision bypassed the engine's dedup
    # (fail); a group of legacy revisions only is a pre-contract capture (legacy nonconformity, warn)
    klass = {r["name"]: r["klass"] for r in current}
    legacy_only = 0
    for key, members in groups:
        if any(klass.get(m) == "managed" for m in members):
            c.add("fail", {"id": " + ".join(members), "reason": "duplicate (%s)" % key, "members": members,
                           "rating": None, "words": None})
        else:
            legacy_only += 1
            c.add("warn", {"id": " + ".join(members), "reason": "duplicate (%s) — legacy-only, pre-contract captures" % key,
                           "members": members, "rating": None, "words": None})
    # managed revisions sharing a resource_sha256 must agree on the canonical URL (any revision)
    all_sha: Dict[str, set] = {}
    for r in scan["folders"]:
        if r["klass"] == "managed" and r["resource_sha256"] and r["canonical_url"]:
            all_sha.setdefault(r["resource_sha256"], set()).add(r["canonical_url"])
    inconsistent = sorted(k for k, urls in all_sha.items() if len(urls) > 1)
    for key in inconsistent:
        members = [r["name"] for r in scan["folders"] if r["resource_sha256"] == key]
        c.add("warn", {"id": " + ".join(members), "reason": "inconsistent canonical_url for resource_sha256 %s…" % key[:12],
                       "members": members, "rating": None, "words": None})
    c.counts = {"current": len(current), "duplicate_groups": len(groups), "legacy_only_groups": legacy_only,
                "inconsistent": len(inconsistent), "unkeyed": unkeyed}
    share = "0 share a resource" if not groups else "%s share a resource: %s" % (
        plural(len(groups), "group"), names([" + ".join(m) for _, m in groups], 3))
    c.detail = "%d current revisions, %s (resource_sha256 · canonical URL v1)" % (len(current), share)
    if legacy_only:
        c.detail += "; legacy-only %d" % legacy_only
    if inconsistent:
        c.detail += "; inconsistent %d" % len(inconsistent)
    c.detail += "; unkeyed %d (non-url legacy)" % unkeyed
    return c


def check_index(scan: dict, cfg: dict) -> Check:
    c = Check("store/index")
    sources_dir = Path(scan["vault"]) / "Sources"
    successors = scan["graph"]["successors"]
    regenerated: Dict[str, str] = {}
    for r in scan["folders"]:
        if not r["has_extract"]:
            continue
        try:
            row = lv.kr.build_row(sources_dir / r["name"], successors, sources_dir)
        except OSError:
            continue
        if row:
            regenerated[r["name"]] = row
    existing = lv.kr.parse_existing_entries(scan["index_text"] or "")
    differ = sorted(f for f, rows in existing.items() if f in regenerated and any(row != regenerated[f] for row in rows))
    # the same correct row twice is drift too: regeneration writes every row once
    duplicated = sorted(f for f, rows in existing.items() if f in regenerated and len(rows) > 1 and f not in differ)
    missing = sorted(f for f in regenerated if f not in existing)
    extra = sorted(f for f in existing if f not in regenerated)
    for f in differ:
        c.add("warn", {"id": f, "reason": "row differs from the regenerated one", "rating": None, "words": None})
    for f in duplicated:
        c.add("warn", {"id": f, "reason": "row present %d times (regeneration writes it once)" % len(existing[f]),
                       "rating": None, "words": None})
    for f in missing:
        c.add("warn", {"id": f, "reason": "row missing", "rating": None, "words": None})
    for f in extra:
        c.add("warn", {"id": f, "reason": "extra row (no extract.md, or the folder is gone)", "rating": None, "words": None})
    he = scan["inputs"]["hand_edits"]
    c.counts = {"rows": len(regenerated), "differ": len(differ), "missing": len(missing), "extra": len(extra),
                "duplicated": len(duplicated), "hand_edits_rows": he["rows"] if he else 0}
    if scan["index_text"] is None:
        if regenerated:
            c.raise_to("warn")
        c.detail = "index.md missing; %d rows to generate" % len(regenerated)
    else:
        c.detail = "%d rows differ, %d missing, %d extra" % (len(differ), len(missing), len(extra))
        if duplicated:
            c.detail += ", %d duplicated" % len(duplicated)
        if differ:
            c.detail += " (differ: %s)" % names(differ, 3)
    c.detail += "; hand-edits report %s: %d rows" % (he["date"], he["rows"]) if he else "; no hand-edits report"
    return c


def check_supersedes(scan: dict, cfg: dict) -> Check:
    c = Check("store/supersedes")
    g = scan["graph"]
    for new, old in g["dangling"]:
        c.add("fail", {"id": new, "reason": "supersedes a folder that does not exist: %s" % old, "rating": None, "words": None})
    for cyc in g["cycles"]:
        c.add("fail", {"id": " → ".join(cyc + [cyc[0]]), "reason": "cycle", "rating": None, "words": None})
    for name in g.get("self_edges", []):
        c.add("fail", {"id": name, "reason": "supersedes itself (a cycle of one)", "rating": None, "words": None})
    cycles_n = len(g["cycles"]) + len(g.get("self_edges", []))
    for new, old in g["cross_resource"]:
        c.add("warn", {"id": "%s → %s" % (new, old), "reason": "cross-resource edge", "rating": None, "words": None})
    stored = sorted(r["name"] for r in scan["folders"] if r["has_extract"] and r["status"] == "superseded")
    for name in stored:
        c.add("warn", {"id": name, "reason": "extract.md carries a stored `superseded` (legacy word)", "rating": None, "words": None})
    c.counts = {"edges": g["edges"], "dangling": len(g["dangling"]), "cycles": cycles_n,
                "self_edges": len(g.get("self_edges", [])),
                "cross_resource": len(g["cross_resource"]), "incomparable": g.get("incomparable", 0),
                "fan_out": len(g["fan_out"]), "stored_superseded": len(stored),
                "current_incomplete": g["current_incomplete"]}
    parts = ["%d edges, %d dangling, %d cycles" % (g["edges"], len(g["dangling"]), cycles_n)]
    if g["current_incomplete"]:
        parts[0] += " — current count incomplete"
    if g["cross_resource"]:
        parts.append("%s (%s)" % (plural(len(g["cross_resource"]), "cross-resource edge"),
                                   names(["%s → %s" % e for e in g["cross_resource"]], 3)))
    if g["fan_out"]:
        parts.append("fan-out %d (%s)" % (len(g["fan_out"]), names(g["fan_out"], 3)))
    if g.get("incomparable"):
        parts.append("%d edge(s) between unkeyed revisions (identity incomparable)" % g["incomparable"])
    if stored:
        parts.append("%d extract.md carry a stored `superseded` (legacy word)" % len(stored))
    c.detail = "; ".join(parts)
    return c


def check_archive(scan: dict, cfg: dict) -> Check:
    c = Check("store/archive")
    a = scan["archive"]
    if not a["configured"]:
        return c.not_probed("archive_dir not configured")
    if not a["present"]:
        return c.not_probed("archive_dir not accessible: %s" % a["dir"], "warn")
    entries = set(a["entries"])
    owned: set = set()
    resolved = 0
    legacy_form = 0
    unarchivable = 0
    unresolved: List[str] = []
    missing_raw: List[str] = []
    for r in scan["folders"]:
        if not r["has_source"] or r["source_state"] == "unreadable":
            continue
        name, form = lv.archive_reference(r["archive"])
        if form == "unarchivable":
            unarchivable += 1
        elif form == "legacy-suffix":
            legacy_form += 1
        if name:
            if name in entries:
                resolved += 1
                owned.add(name)
            elif r["klass"] == "managed" and r["raw_store"] == "archive":
                missing_raw.append(r["name"])
                c.add("fail", item(r, "capture.raw_store is archive but %s is not in the archive" % name, "managed"))
            else:
                unresolved.append(r["name"])
                c.add("warn", item(r, "archive: %s does not resolve" % name, r["klass"]))
        elif r["klass"] == "managed" and r["raw_store"] == "archive":
            missing_raw.append(r["name"])
            c.add("fail", item(r, "capture.raw_store is archive but source.md names no archive", "managed"))
        owned.add(r["name"] + ".report.json")
        owned.add(r["name"] + ".site")
    unowned = sorted(entries - owned)
    for name in unowned:
        c.add("warn", {"id": name, "reason": "unowned archive entry", "rating": None, "words": None})
    c.counts = {"resolved": resolved, "legacy_form": legacy_form, "unarchivable": unarchivable,
                "unresolved": len(unresolved), "missing_raw": len(missing_raw), "entries": len(entries), "unowned": len(unowned)}
    parts = ["%d references resolve (%d legacy suffix form, %d unarchivable)" % (resolved, legacy_form, unarchivable)]
    if missing_raw:
        parts.append("%d managed without raw (%s)" % (len(missing_raw), names(missing_raw)))
    if unresolved:
        parts.append("%d unresolved (%s)" % (len(unresolved), names(unresolved)))
    parts.append("%d archive files unowned" % len(unowned) + (": %s" % names(unowned) if unowned else ""))
    c.detail = "; ".join(parts)
    return c


def check_completeness(scan: dict, cfg: dict) -> Check:
    c = Check("store/completeness")
    dist: Dict[str, int] = {k: 0 for k in COMPLETENESS_VALUES}
    invalid: List[str] = []
    blocked: List[str] = []
    legacy_url = 0
    managed_url = 0
    for r in scan["folders"]:
        if not r["has_source"] or r["source_state"] == "unreadable" or not r["is_url"]:
            continue
        if r["klass"] == "managed":
            managed_url += 1
            v = r["completeness"]
            if v is None:
                invalid.append(r["name"])
                c.add("fail", item(r, "capture.completeness missing", "managed"))
            elif v not in COMPLETENESS_VALUES:
                invalid.append(r["name"])
                c.add("fail", item(r, "capture.completeness `%s` is not complete·partial·blocked·unknown" % v, "managed"))
            else:
                dist[v] += 1
                if v == "blocked":
                    blocked.append(r["name"])
                    c.add("warn", item(r, "completeness: blocked (a paywall teaser)", "managed"))
        elif r["klass"] == "legacy":
            legacy_url += 1
    c.counts = {"managed_url": managed_url, "invalid": len(invalid), "legacy_url_without_record": legacy_url}
    c.counts.update(dist)
    parts = ["managed url revisions: " + " · ".join("%s %d" % (k, dist[k]) for k in COMPLETENESS_VALUES)]
    if invalid:
        parts.append("%d missing or invalid (%s)" % (len(invalid), names(invalid)))
    parts.append("%d legacy url revisions without a record" % legacy_url)
    c.detail = "; ".join(parts)
    return c


def check_ledger(scan: dict, cfg: dict) -> Check:
    c = Check("store/ledger")
    ledger = scan["ledger"]
    managed = [r for r in scan["folders"] if r["klass"] == "managed"]
    if not ledger["present"]:
        # S4: the writer evidence is the instance's writer_host alone — no ledger there is a warning
        # (state lost?); a reader host or an unset writer_host is simply not probed
        writer = cfg.get("writer_host") or ""
        where = os.path.join(ledger["state_dir"] or "?", "captures")
        if not managed:
            return c.not_probed("no ledger yet — nothing captured on this host (%s)" % where)
        if not writer:
            return c.not_probed("no ledger on this host (%s); writer_host unset" % where)
        if writer == cfg.get("this_host"):
            return c.not_probed("no ledger on the writer host — state lost? (%s)" % where, "warn")
        return c.not_probed("no ledger on this host (%s); not the writer (writer_host %s)" % (where, writer))
    by_name = scan["by_name"]
    consistent = 0
    contradict: List[str] = []
    integrity: List[str] = []
    incomplete = 0
    named: set = set()
    for rc in ledger["receipts"]:
        state = rc["state"]
        if rc["revision"]:
            named.add(rc["revision"])
        if state in ("started", "failed", "extract-failed"):
            incomplete += 1
            continue
        if state == "integrity-error":
            # a terminal verdict of the engine (contracts §3.6): the published source was edited or
            # missing when it last looked — named, counted apart, never silently dropped
            integrity.append(rc["revision"] or rc["id"])
            c.add("warn", {"id": rc["revision"] or rc["id"], "reason": "receipt %s in integrity-error (the engine found the published source.md edited or missing)" % rc["id"],
                           "rating": None, "words": None})
            continue
        if state not in ("extracted", "source-published") or not rc["revision"]:
            continue
        rec = by_name.get(rc["revision"])
        if rec is None or not rec["has_source"]:
            contradict.append(rc["id"])
            c.add("fail", {"id": rc["revision"], "reason": "receipt %s (%s) names a revision that is missing" % (rc["id"], state),
                           "rating": None, "words": None})
        elif rc["text_sha256"] and rec["body_sha256"] != rc["text_sha256"]:
            contradict.append(rc["id"])
            c.add("fail", item(rec, "receipt %s text_sha256 %s… ≠ source.md body %s…" % (
                rc["id"], rc["text_sha256"][:12], (rec["body_sha256"] or "none")[:12]), "managed"))
        else:
            consistent += 1
    without: List[str] = []
    for r in managed:
        if r["name"] not in named:
            without.append(r["name"])
            c.add("warn", item(r, "managed revision without a receipt (state lost; replay restores it)", "managed"))
    c.counts = {"managed": len(managed), "receipts": len(ledger["receipts"]), "consistent": consistent,
                "contradict": len(contradict), "integrity_error": len(integrity), "without_receipt": len(without),
                "incomplete": incomplete, "unreadable": ledger["unreadable"]}
    parts = ["%d managed revisions, %d receipts consistent" % (len(managed), consistent)]
    if contradict:
        parts.append("%d contradict the files (%s)" % (len(contradict), names(contradict)))
    if integrity:
        parts.append("integrity-error %d (%s)" % (len(integrity), names(integrity)))
    if without:
        parts.append("%d without a receipt (%s)" % (len(without), names(without)))
    parts.append("%d incomplete" % incomplete)
    if ledger["unreadable"]:
        parts.append("%d unreadable" % ledger["unreadable"])
    c.detail = "; ".join(parts)
    return c


def check_status_vocabulary(scan: dict, cfg: dict) -> Check:
    c = Check("store/status-vocabulary")
    st = scan["counters"]["status"]
    sy = scan["counters"]["synthesis_status"]
    empty_managed = 0
    for r in scan["folders"]:
        if not r["has_extract"] or r["extract_state"] == "unreadable":
            continue
        s = r["status"]
        if s in lv.EXTRACT_STATUS_STORED:
            continue
        if s in lv.EXTRACT_STATUS_LEGACY:
            c.add("warn", item(r, "legacy status `%s`" % s))
        elif not s:
            if r["klass"] == "managed":
                empty_managed += 1          # a schema violation: store/extract-consistency fails it
                c.items.append(item(r, "empty status on a managed extract (schema extract/1 → store/extract-consistency)"))
            else:
                c.add("warn", item(r, "empty status"))
        else:
            c.add("warn", item(r, "unknown status `%s`" % s))
    for n in scan["synthesis"]["notes"]:
        if n["status"] in lv.SYNTHESIS_STATUS:
            continue
        reason = "empty status" if not n["status"] else "unknown status `%s`" % n["status"]
        c.add("warn", {"id": "Synthesis/" + n["name"], "reason": reason, "rating": None, "words": None})
    c.counts = {"extract": dict(st, empty_managed=empty_managed), "synthesis": sy}
    legacy = "legacy words %d" % st["legacy_words"]
    if st["legacy"]:
        legacy += " (%s)" % ", ".join("%s %d" % (w, n) for w, n in sorted(st["legacy"].items(), key=lambda x: (-x[1], x[0])))
    empty = "empty %d" % st["empty"] + (" (%d managed → schema)" % empty_managed if empty_managed else "")
    unknown = "unknown %d" % st["unknown"]
    if st["unknown_words"]:
        unknown += " (%s)" % ", ".join("%s %d" % (w, n) for w, n in sorted(st["unknown_words"].items()))
    syn_unknown = "unknown %d" % sy["unknown"]
    if sy["unknown_words"]:
        syn_unknown += " (%s)" % ", ".join("%s %d" % (w, n) for w, n in sorted(sy["unknown_words"].items()))
    c.detail = ("extract: extracted %d · dismissed %d · %s · %s · %s; synthesis: seed %d · draft %d · stable %d · empty %d · %s"
                % (st["extracted"], st["dismissed"], legacy, empty, unknown,
                   sy["seed"], sy["draft"], sy["stable"], sy["empty"], syn_unknown))
    return c


def check_context(scan: dict, cfg: dict) -> Check:
    c = Check("store/context")
    declared = scan["charter"]["contexts"]
    inside = 0
    outside: List[str] = []
    legacy_vaults: Dict[str, int] = {}
    managed = 0
    for r in scan["folders"]:
        if not r["has_source"] or r["source_state"] == "unreadable":
            continue
        if r["klass"] == "managed":
            managed += 1
            if r["context"] and r["context"] in declared:
                inside += 1
            else:
                outside.append(r["name"])
                reason = ("context `%s` not declared in the charter" % r["context"]) if r["context"] else "no context"
                c.add("warn", item(r, reason, "managed"))
        elif r["cc_vault"]:
            legacy_vaults[r["cc_vault"]] = legacy_vaults.get(r["cc_vault"], 0) + 1
    c.counts = {"managed": managed, "inside": inside, "outside": len(outside), "declared": declared, "legacy_vaults": legacy_vaults}
    charter_note = "" if scan["charter"]["exists"] else "; no charter (default personal)"
    legacy = "legacy vault names not compared" + (
        " (%s)" % ", ".join("%s %d" % (k, v) for k, v in sorted(legacy_vaults.items(), key=lambda x: -x[1])) if legacy_vaults else "")
    if outside:
        c.detail = "%d of %d managed contexts ∈ charter (%s); %d outside: %s%s; %s" % (
            inside, managed, ", ".join(declared), len(outside), names(outside), charter_note, legacy)
    else:
        c.detail = "%d managed contexts ∈ charter (%s)%s; %s" % (managed, ", ".join(declared), charter_note, legacy)
    return c


def alias_verdict(scan: dict, r: dict, ex_cid: str, src_cid: str) -> str:
    """alias · unknown · mismatch — is extract.md's capture_id a recovery alias of source.md's? After
    `mnemon add --resume <revision>` without the owner receipt the alias writes its own id into
    extract.md while the immutable source.md keeps the original (contracts §12 A4); the ledger's
    alias receipt (`resolved_to_capture_id` / `resolved_to_revision`) is the evidence."""
    ledger = scan["ledger"]
    if not ledger["present"]:
        return "unknown"
    for rec in ledger["receipts"]:
        if rec["capture_id"] == ex_cid and (rec.get("resolved_to_capture_id") == src_cid
                                            or rec.get("resolved_to_revision") == r["name"]):
            return "alias"
    return "mismatch"


def check_extract_consistency(scan: dict, cfg: dict) -> Check:
    c = Check("store/extract-consistency")
    with_source = 0
    orphans: List[str] = []
    pairs = 0
    mismatch: List[str] = []
    no_status: List[str] = []
    url_differs: List[str] = []
    no_sections: List[str] = []
    aliased: List[str] = []
    alias_unknown: List[str] = []
    for r in scan["folders"]:
        if not r["has_extract"] or r["extract_state"] == "unreadable":
            continue
        if not r["has_source"]:
            orphans.append(r["name"])
            c.add("fail", item(r, "extract.md without source.md"))
            continue
        with_source += 1
        if r["klass"] == "managed":
            pairs += 1
            bad = []
            if (r["ex_revision"] or "") != (r["src"].get("revision") or ""):
                bad.append("revision")
            src_cid = r["src"].get("capture_id") or ""
            ex_cid = r["ex_capture_id"] or ""
            if ex_cid != src_cid:
                verdict = alias_verdict(scan, r, ex_cid, src_cid)
                if verdict == "alias":
                    aliased.append(r["name"])
                elif verdict == "unknown":
                    alias_unknown.append(r["name"])
                    c.add("warn", item(r, "capture_id differs from source.md (%s vs %s) — a recovery alias? no ledger here to confirm"
                                       % (ex_cid[:28], src_cid[:28]), "managed"))
                else:
                    bad.append("capture_id")
            if bad:
                mismatch.append(r["name"])
                c.add("fail", item(r, "extract.md and source.md disagree on " + "/".join(bad), "managed"))
            if not r["status"]:
                no_status.append(r["name"])
                c.add("fail", item(r, "extract.md without status (schema extract/1 requires it)", "managed"))
            if r["ex_url"] and r["url"] and r["ex_url"] != r["url"]:
                url_differs.append(r["name"])
                c.add("warn", item(r, "url differs between extract.md and source.md", "managed"))
        if not r["ex_sections"]:
            no_sections.append(r["name"])
            c.add("warn", item(r, "none of the five sections (Summary, Executive Summary, Key Ideas, Connections, Raw Quotes)"))
    c.counts = {"with_source": with_source, "orphans": len(orphans), "managed_pairs": pairs, "mismatch": len(mismatch),
                "aliases": len(aliased), "alias_unknown": len(alias_unknown),
                "no_status": len(no_status), "url_differs": len(url_differs), "no_sections": len(no_sections)}
    parts = ["%d extracts have a source" % with_source + (", %d do not (%s)" % (len(orphans), names(orphans)) if orphans else "")]
    agree = pairs - len(mismatch) - len(alias_unknown)
    parts.append("%d managed pairs agree on capture_id/revision" % agree + (
        ", %d disagree (%s)" % (len(mismatch), names(mismatch)) if mismatch else ""))
    if aliased:
        parts.append("%s (%s)" % (plural(len(aliased), "recovery alias", "recovery aliases"), names(aliased)))
    if alias_unknown:
        parts.append("%d capture_id differ without a ledger to confirm an alias (%s)" % (len(alias_unknown), names(alias_unknown)))
    if no_status:
        parts.append("%d managed without status (%s)" % (len(no_status), names(no_status)))
    if url_differs:
        parts.append("%d url differ (%s)" % (len(url_differs), names(url_differs)))
    parts.append("%d without sections" % len(no_sections) + (" (%s)" % names(no_sections) if no_sections else ""))
    c.detail = "; ".join(parts)
    return c


def check_guide_version(scan: dict, cfg: dict) -> Check:
    c = Check("store/guide-version")
    engine = cfg.get("guide_version")
    if engine is None:
        return c.not_probed("the engine's vault-template carries no mnemon_guide stamp", "warn")
    copies = (("vault CLAUDE.md", scan["guide"]["claude_md"]), ("_meta/Protocol.md", scan["guide"]["protocol"]))
    states: List[str] = []
    for label, stamp in copies:
        v = stamp["version"]
        if not stamp["exists"]:
            states.append("%s missing" % label)
            c.add("warn", {"id": label, "reason": "missing", "rating": None, "words": None})
        elif v is None:
            states.append("%s unstamped" % label)
            c.add("warn", {"id": label, "reason": "unstamped", "rating": None, "words": None})
        elif v < engine:
            states.append("%s %d (behind)" % (label, v))
            c.add("warn", {"id": label, "reason": "guide %d behind engine %d" % (v, engine), "rating": None, "words": None})
        elif v > engine:
            states.append("%s %d (ahead)" % (label, v))
            c.add("warn", {"id": label, "reason": "guide %d ahead of engine %d" % (v, engine), "rating": None, "words": None})
        else:
            states.append("%s %d" % (label, v))
    c.counts = {"engine": engine, "claude_md": scan["guide"]["claude_md"]["version"], "protocol": scan["guide"]["protocol"]["version"]}
    if c.status == "ok":
        c.detail = "vault CLAUDE.md and _meta/Protocol.md stamped %d (engine guide %d)" % (engine, engine)
    elif all(s.endswith("unstamped") for s in states):
        c.detail = "vault CLAUDE.md and _meta/Protocol.md unstamped (engine guide %d)" % engine
    else:
        c.detail = "%s (engine guide %d)" % (", ".join(states), engine)
    return c


# ---------------------------------------------------------------------------------------------
# Q — data quality
# ---------------------------------------------------------------------------------------------

def thin_features(r: dict, stub_words: int) -> List[str]:
    f: List[str] = []
    if r["archive"] is not None and r["archive"].strip() == "unarchivable":
        f.append("unarchivable")
    if r["body_words"] is not None and r["body_words"] < stub_words:
        f.append("stub")
    if r["host"] in lv.ARXIV_HOSTS:
        f.append("arxiv")
    if r["source_type"] in lv.MEDIA_TYPES:
        f.append("media")
    if r["source_type"] in lv.SITE_TYPES:
        f.append("site-type")
    if r["klass"] == "managed" and r["is_url"]:
        if r["completeness"] in ("partial", "blocked"):
            f.append("completeness:" + r["completeness"])
    elif r["is_url"] or r["url"]:
        f.append("completeness:none")
    return f


def thin_class(features: List[str]) -> str:
    """S7: the suspected class by priority; `unknown` when the class features conflict."""
    if "unarchivable" in features or "stub" in features:
        return "landing/stub"
    kinds = [k for k in ("arxiv", "media", "site-type") if k in features]
    if len(kinds) > 1:
        return "unknown"
    if kinds == ["arxiv"]:
        return "arxiv-abstract"
    if kinds == ["media"]:
        return "media"
    if kinds == ["site-type"]:
        return "website/repo/document"
    return "article-short"


def rank(items: List[dict]) -> List[dict]:
    """Rating descending (unrated last), words ascending."""
    return sorted(items, key=lambda i: (-(i["rating"] if i["rating"] is not None else -1),
                                        i["words"] if i["words"] is not None else 10 ** 9, i["id"]))


def check_thin(scan: dict, cfg: dict) -> Check:
    c = Check("data/thin")
    thin_words, stub_words = cfg["thin_words"], cfg["stub_words"]
    by_class: Dict[str, int] = {}
    thin: List[dict] = []
    completeness: List[dict] = []
    for r in scan["folders"]:
        if not r["current"] or r["source_state"] == "unreadable" or r["body_words"] is None:
            continue
        if r["tier"] == "bookmark":
            continue
        features = thin_features(r, stub_words)
        incomplete = [f for f in features if f in ("completeness:partial", "completeness:blocked")]
        short = r["source_type"] in lv.SHORT_TYPES
        if not short and r["body_words"] < thin_words:
            klass = thin_class(features)
            by_class[klass] = by_class.get(klass, 0) + 1
            thin.append(item(r, klass=klass, features=features, source_type=r["source_type"]))
        elif incomplete:
            completeness.append(item(r, klass="completeness", features=features, source_type=r["source_type"]))
    thin = rank(thin)
    hi = [i for i in thin if i["rating"] is not None and i["rating"] >= RATING_HI]
    for i in thin + rank(completeness):
        c.add("warn", i)
    c.counts = {"thin": len(thin), "by_class": by_class, "rated_hi": len(hi), "completeness": len(completeness)}
    c.threshold = {"thin_words": thin_words, "stub_words": stub_words, "rating_hi": RATING_HI}
    classes = " · ".join("%s %d" % (k, v) for k, v in sorted(by_class.items(), key=lambda x: (-x[1], x[0])))
    c.detail = "%d current bodies < %d words" % (len(thin), thin_words) + (" (%s)" % classes if classes else "")
    c.detail += "; %d rated ≥ %d" % (len(hi), RATING_HI)
    if hi:
        c.detail += " — top: " + names(["%s ★%d %dw" % (i["id"], i["rating"], i["words"] or 0) for i in hi], 3)
    if completeness:
        c.detail += "; completeness partial/blocked %d (%s)" % (len(completeness), names([i["id"] for i in completeness], 3))
    return c


def check_arxiv(scan: dict, cfg: dict) -> Check:
    c = Check("data/arxiv-abstract")
    limit = cfg["arxiv_words"]
    suspects: List[dict] = []
    superseded = 0
    for r in scan["folders"]:
        if not r["has_source"] or r["source_state"] == "unreadable" or r["body_words"] is None:
            continue
        if r["host"] not in lv.ARXIV_HOSTS or r["body_words"] >= limit:
            continue
        if r["tier"] == "bookmark" or r["src_status"] == "bookmark":
            continue
        if not r["current"]:
            superseded += 1
            continue
        suspects.append(item(r, klass="arxiv-abstract", url=r["canonical_url"] or r["url"]))
    for i in rank(suspects):
        c.add("warn", i)
    c.counts = {"suspects": len(suspects), "superseded": superseded}
    c.threshold = {"arxiv_words": limit}
    c.detail = "%d current arXiv revisions below %d words (%d superseded)" % (len(suspects), limit, superseded)
    if suspects:
        c.detail += ": " + names([i["id"] for i in rank(suspects)], 3)
    return c


def check_stubs(scan: dict, cfg: dict) -> Check:
    c = Check("data/stubs")
    stub_words = cfg["stub_words"]
    counts = {"fetch_status failed": 0, "bookmark": 0, "< %d words" % stub_words: 0}
    stubs: List[dict] = []
    for r in scan["folders"]:
        if not r["current"] or r["source_state"] == "unreadable":
            continue
        features: List[str] = []
        if r["fetch_status"].startswith("failed"):
            features.append("fetch_status failed")
        if r["src_status"] == "bookmark":
            features.append("bookmark")
        if r["body_words"] is not None and r["body_words"] < stub_words:
            features.append("< %d words" % stub_words)
        if not features:
            continue
        for f in features:
            counts[f] += 1
        stubs.append(item(r, klass="stub", features=features))
    for i in rank(stubs):
        c.add("warn", i)
    c.counts = {"stubs": len(stubs), "features": counts}
    c.threshold = {"stub_words": stub_words}
    c.detail = "%d placeholders" % len(stubs)
    if stubs:
        c.detail += ": " + " · ".join("%s %d" % (k, v) for k, v in counts.items())
    return c


def check_rating_scale(scan: dict, cfg: dict) -> Check:
    c = Check("data/rating-scale")
    share_limit = cfg["rating_hi_share"]
    current = [r for r in scan["folders"] if r["current"] and r["source_state"] != "unreadable"]
    rated = [r for r in current if r["rating"] is not None]
    hi = [r for r in rated if r["rating"] >= RATING_HI]
    share = (100.0 * len(hi) / len(rated)) if rated else 0.0
    for r in rank([item(r, klass="rated ≥ %d" % RATING_HI) for r in hi]):
        c.items.append(r)
    if rated and share > share_limit:
        c.raise_to("warn")
    c.counts = {"current": len(current), "rated": len(rated), "hi": len(hi), "share_pct": round(share, 1)}
    c.threshold = {"rating_hi": RATING_HI, "rating_hi_share": share_limit}
    c.detail = "%d %% of rated current revisions ≥ %d (%d/%d, coverage %d/%d); threshold %d %%" % (
        round(share), RATING_HI, len(hi), len(rated), len(rated), len(current), share_limit)
    return c


def check_reader_context(scan: dict, cfg: dict) -> Check:
    c = Check("data/reader-context")
    rc = scan["reader_context"]
    today = scan["today"]
    label = rc["path"] or "reader-context.md"
    threshold = READER_CONTEXT_DEFAULT_DAYS
    note = ""
    if rc["exists"]:
        t = lv.parse_int(rc["threshold"]) if rc["threshold"] is not None else None
        if rc["threshold"] is not None and (t is None or t < 0):
            note = " (staleness_threshold_days `%s` invalid, default %d used)" % (rc["threshold"], READER_CONTEXT_DEFAULT_DAYS)
        elif t is not None:
            threshold = t
    c.threshold = {"staleness_threshold_days": threshold}
    if not rc["exists"]:
        c.raise_to("warn")
        c.detail = "no freshness data (missing %s)" % label
        c.counts = {"age_days": None}
        return c
    synced = lv.parse_date(rc["synced"])
    if synced is None:
        c.raise_to("warn")
        c.detail = "no freshness data (`synced:` missing or invalid in %s)%s" % (label, note)
        c.counts = {"age_days": None}
        return c
    age = age_days(today, synced)
    if age < 0:
        c.raise_to("warn")
        c.detail = "no freshness data (`synced: %s` is in the future, %s)%s" % (synced.isoformat(), label, note)
        c.counts = {"age_days": age}
        return c
    c.counts = {"age_days": age}
    if age > threshold:
        c.raise_to("warn")
    c.detail = "synced %d d ago, threshold %d d (%s)%s" % (age, threshold, label, note)
    return c


# ---------------------------------------------------------------------------------------------
# H — the human loop
# ---------------------------------------------------------------------------------------------

def note_age(note: dict, today: datetime.date) -> Tuple[int, str]:
    """(age in days, note): by `created:`; an invalid or missing date falls back to mtime, marked."""
    created = lv.parse_date(note["created"])
    if created is not None:
        return age_days(today, created), ""
    return age_days(today, note["mtime"]), " (mtime)"


def _human_items(notes: List[dict], today: datetime.date, label: str) -> List[dict]:
    items = []
    for n in notes:
        age, mark = note_age(n, today)
        items.append({"id": "Synthesis/" + n["name"], "reason": "%s %d d%s" % (label, age, mark), "age_days": age,
                      "mtime_fallback": bool(mark), "status": n["status"], "drafted_by": n["drafted_by"],
                      "rating": None, "words": None})
    return sorted(items, key=lambda i: (-i["age_days"], i["id"]))


def check_ratification(scan: dict, cfg: dict) -> Check:
    c = Check("human/ratification")
    today = scan["today"]
    radius, defaulted = cfg["radius"], cfg["radius_defaulted"]
    count_limit, days_limit = radius["drafts"]
    drafts = [n for n in scan["synthesis"]["notes"] if lv.unratified(n)]
    unknown = [n for n in scan["synthesis"]["notes"] if lv.unknown_authorship(n)]
    items = _human_items(drafts, today, "unratified draft")
    oldest = items[0]["age_days"] if items else 0
    over = len(items) > count_limit or oldest > days_limit
    for i in items:
        c.add("warn" if over else "ok", i)
    c.counts = {"unratified": len(items), "oldest_days": oldest, "unknown_authorship": len(unknown),
                "mtime_fallback": sum(1 for i in items if i["mtime_fallback"])}
    c.threshold = {"count": count_limit, "days": days_limit, "defaults": "drafts" in defaulted}
    c.detail = "%d unratified agent drafts" % len(items)
    if items:
        c.detail += ", oldest %d d" % oldest
        if c.counts["mtime_fallback"]:
            c.detail += " (%d by mtime)" % c.counts["mtime_fallback"]
    c.detail += " %s; unknown authorship %d" % (radius_label("drafts", radius, defaulted), len(unknown))
    if unknown:
        c.detail += " (%s)" % names(["Synthesis/" + n["name"] for n in unknown], 3)
    return c


def check_seeds(scan: dict, cfg: dict) -> Check:
    c = Check("human/seeds")
    today = scan["today"]
    radius, defaulted = cfg["radius"], cfg["radius_defaulted"]
    count_limit, days_limit = radius["seeds"]
    seeds = [n for n in scan["synthesis"]["notes"] if n["status"] == "seed"]
    items = _human_items(seeds, today, "seed")
    oldest = items[0]["age_days"] if items else 0
    over = len(items) > count_limit or oldest > days_limit
    for i in items:
        c.add("warn" if over else "ok", i)
    c.counts = {"seeds": len(items), "oldest_days": oldest, "mtime_fallback": sum(1 for i in items if i["mtime_fallback"])}
    c.threshold = {"count": count_limit, "days": days_limit, "defaults": "seeds" in defaulted}
    c.detail = "%d seeds" % len(items)
    if items:
        c.detail += ", oldest %d d" % oldest
        if c.counts["mtime_fallback"]:
            c.detail += " (%d by mtime)" % c.counts["mtime_fallback"]
    c.detail += " %s" % radius_label("seeds", radius, defaulted)
    return c


def check_queues(scan: dict, cfg: dict) -> Check:
    c = Check("human/queues")
    today = scan["today"]
    radius, defaulted = cfg["radius"], cfg["radius_defaulted"]
    days_limit = radius["queues"][1]
    inputs = scan["inputs"]
    parked = 0
    over: List[str] = []
    ages: List[int] = []
    for q in inputs["queues"]:
        qid = "_inputs/" + q["name"]
        perr = lv.queue_parking_error(q)
        if perr:
            c.add("warn", {"id": qid, "reason": perr, "items": q["items"], "age_days": None, "rating": None, "words": None})
        if lv.queue_is_parked(q, today):
            parked += 1
            until = lv.parse_date(q.get("parked_until"))
            c.items.append({"id": qid, "reason": "parked" + (" until %s" % until.isoformat() if until else " (indefinitely)"),
                            "items": q["items"], "age_days": None, "rating": None, "words": None})
            continue
        since = lv.parse_date(q.get("since"))
        if since is None:
            over.append(q["name"])
            c.add("warn", {"id": qid, "reason": "declared queue without a valid `since:`", "items": q["items"],
                           "age_days": None, "rating": None, "words": None})
            continue
        age = age_days(today, since)
        ages.append(age)
        status = "warn" if age > days_limit else "ok"
        if status == "warn":
            over.append(q["name"])
        c.add(status, {"id": qid, "reason": "%d items · %d d" % (q["items"], age), "items": q["items"], "age_days": age,
                       "rating": None, "words": None})
    # S3 H3 / S4: an undeclared _inputs/*.md is a coverage gap the vault must close — a warning
    untyped = [q["name"] for q in inputs["untyped"]]
    for name in untyped:
        c.add("warn", {"id": "_inputs/" + name, "reason": "undeclared — declare (type: queue, since) or remove",
                       "rating": None, "words": None})
    legacy = inputs["pending_writes"]
    if legacy:
        c.items.append({"id": "_inputs/pending-writes", "reason": "%d legacy entries" % legacy, "rating": None, "words": None})
    c.counts = {"declared": len(inputs["queues"]), "parked": parked, "over_radius": len(over),
                "oldest_days": max(ages) if ages else 0, "untyped": len(untyped), "pending_writes_legacy": legacy}
    c.threshold = {"days": days_limit, "defaults": "queues" in defaulted}
    parts = ["%d declared queues" % len(inputs["queues"])]
    if ages:
        parts[0] += ", oldest %d d" % max(ages)
    if over:
        parts.append("%d over radius (%s)" % (len(over), names(over)))
    if parked:
        parts.append("parked %d" % parked)
    parts.append("untyped %d" % len(untyped) + (" (%s) — declare (type: queue, since) or remove" % names(untyped) if untyped else ""))
    parts.append("pending-writes legacy %d" % legacy)
    c.detail = "; ".join(parts) + " %s" % radius_label("queues", radius, defaulted)
    return c


CHECKS = (
    check_source_integrity, check_identity, check_index, check_supersedes, check_archive, check_completeness,
    check_ledger, check_status_vocabulary, check_context, check_extract_consistency, check_guide_version,
    check_thin, check_arxiv, check_stubs, check_rating_scale, check_reader_context,
    check_ratification, check_seeds, check_queues,
)


# ---------------------------------------------------------------------------------------------
# R — the research document (mnemon-doctor-research/1, interface §8): the collection schemas and
# grammar, the age of open dossiers, the derived states of the wiki pages (§9), the index sections
# ---------------------------------------------------------------------------------------------

RESEARCH_SCHEMA = "mnemon-doctor-research/1"
RESEARCH_FIX = (
    ("research/collections", "operator"), ("research/collection-age", "operator"),
    ("research/wiki-schema", "research-update"), ("research/wiki-binding", "research-update"),
    ("research/wiki-stale", "research-update --propose"), ("research/wiki-inputs", "research-update"),
    ("research/wiki-evidence", "research-update"), ("research/wiki-orphan", "operator"),
    ("research/wiki-budget", "operator"), ("research/index", "mnemon reindex"),
)
RESEARCH_CHECK_NAMES = tuple(name for name, _ in RESEARCH_FIX)
RESEARCH_FIX_OF = dict(RESEARCH_FIX)
FIX_OF.update(RESEARCH_FIX_OF)
RESEARCH_DEFAULTS = {"wiki_pages_per_collection": lr.DEFAULT_WIKI_PAGES_PER_COLLECTION, "wiki_words": lr.DEFAULT_WIKI_WORDS}
WIKI_CHECKS = ("research/wiki-schema", "research/wiki-binding", "research/wiki-stale", "research/wiki-inputs",
               "research/wiki-evidence", "research/wiki-orphan", "research/wiki-budget")
COLLECTION_CHECKS = ("research/collections", "research/collection-age", "research/index")


def page_item(p: dict, reason: str, **extra) -> dict:
    out = {"id": "Wiki/" + p["name"], "reason": reason, "rating": None, "words": p["body"]["words"] if p.get("body") else None,
           "states": list(p.get("states") or []), "kind": p.get("kind")}
    out.update(extra)
    return out


def collection_item(c: dict, reason: str, **extra) -> dict:
    out = {"id": "Collections/" + c["name"], "reason": reason, "rating": None, "words": None, "status": c.get("status")}
    out.update(extra)
    return out


def check_research_collections(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/collections")
    cols = scan["collections"]["notes"]
    fail: List[str] = []
    unreadable: List[str] = []
    unresolved_total = 0
    malformed_total = 0
    for col in cols:
        if col["state"] == "unreadable":
            unreadable.append(col["name"])
            c.add("fail", collection_item(col, "unreadable (never a clean document)"))
            continue
        if col["state"] == "fail":
            fail.append(col["name"])
            reasons = list(col["errors"]) + ["line %d: %s" % (m["line"], m["reason"]) for m in col["malformed"]]
            malformed_total += len(col["malformed"])
            c.add("fail", collection_item(col, "; ".join(reasons[:4]) + ("; …" if len(reasons) > 4 else ""),
                                          errors=len(col["errors"]), malformed=len(col["malformed"])))
        u = col["counts"].get("unresolved") or 0
        if u:
            unresolved_total += u
            revs = [it["rev"] for it in col["items"] if it["exists"] is False]
            c.add("warn", collection_item(col, "%s: %s" % (plural(u, "unresolved item"), names(revs, 3)), unresolved=u))
    c.counts = {"documents": len(cols), "fail": len(fail), "unreadable": len(unreadable), "malformed_lines": malformed_total,
                "unresolved_items": unresolved_total, "status": scan["counters"]["collections_status"]}
    parts = ["%d documents" % len(cols)]
    if fail:
        parts.append("%d fail (%s)" % (len(fail), names(fail, 3)))
    if unreadable:
        parts.append("%d unreadable (%s)" % (len(unreadable), names(unreadable, 3)))
    if malformed_total:
        parts.append("%s" % plural(malformed_total, "malformed line"))
    parts.append("%s" % plural(unresolved_total, "unresolved item"))
    c.detail = "; ".join(parts)
    return c


def check_collection_age(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/collection-age")
    today = scan["today"]
    cols = scan["collections"]["notes"]
    items = []
    for col in cols:
        if col.get("status") != "open":
            continue
        when = lv.parse_date(col.get("updated")) or lv.parse_date(col.get("created"))
        if when is None:
            items.append(collection_item(col, "open, no valid date", age_days=None))
            continue
        age = age_days(today, when)
        items.append(collection_item(col, "open %d d (since %s)" % (age, when.isoformat()), age_days=age))
    items.sort(key=lambda i: (-(i["age_days"] if i["age_days"] is not None else -1), i["id"]))
    for i in items:
        c.items.append(i)                       # informational: never raises the status
    ages = [i["age_days"] for i in items if i["age_days"] is not None]
    st = scan["counters"]["collections_status"]
    c.counts = {"open": len(items), "oldest_days": max(ages) if ages else 0, "inbox": st["inbox"], "closed": st["closed"]}
    c.detail = "%d open dossiers" % len(items) + (", oldest %d d (%s)" % (max(ages), items[0]["id"]) if ages else "")
    c.detail += "; inbox %d · closed %d" % (st["inbox"], st["closed"])
    return c


def check_wiki_schema(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-schema")
    fail: List[str] = []
    not_probed: List[str] = []
    for p in pages:
        if p["state"] == "unreadable":
            not_probed.append(p["name"])
            c.add("warn", page_item(p, "unreadable — not probed (never a clean page)", probed=False))
        elif p["state"] == "fail":
            fail.append(p["name"])
            c.add("fail", page_item(p, "; ".join(p["errors"][:4]) + ("; …" if len(p["errors"]) > 4 else ""), errors=len(p["errors"])))
        elif p.get("not_probed"):
            not_probed.append(p["name"])
            c.add("warn", page_item(p, "not probed: " + "; ".join(p["not_probed"][:3]), probed=False))
    c.counts = {"pages": len(pages), "fail": len(fail), "not_probed": len(not_probed),
                "kinds": {k: sum(1 for p in pages if p.get("kind") == k) for k in lr.WIKI_KINDS}}
    c.detail = "%d pages" % len(pages)
    if fail:
        c.detail += ", %d fail the schema (%s)" % (len(fail), names(fail, 3))
    if not_probed:
        c.detail += ", %d not probed (%s)" % (len(not_probed), names(not_probed, 3))
    if not fail and not not_probed:
        c.detail += ", every page read (wiki/1)"
    return c


def _state_check(c: Check, pages: List[dict], states: Tuple[str, ...], level: str) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {s: [] for s in states}
    for p in pages:
        if p["state"] != "ok":
            continue
        hit = [s for s in states if s in p["states"]]
        if not hit:
            continue
        reasons = []
        for s in hit:
            found[s].append(p["name"])
            reasons += ["%s: %s" % (s, r) for r in p["reasons"].get(s, [])]
        c.add(level, page_item(p, "; ".join(reasons[:4]) + ("; …" if len(reasons) > 4 else ""), hit=hit))
    return found


def check_wiki_binding(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-binding")
    found = _state_check(c, pages, ("unbound", "suppressed"), "fail")
    c.counts = {"unbound": len(found["unbound"]), "suppressed": len(found["suppressed"]),
                "locators": sum(p["counts"]["locators"] for p in pages if p["state"] == "ok"),
                "locators_verified": sum(1 for p in pages if p["state"] == "ok" for l in p["locators"] if l["verified"])}
    c.detail = "%d unbound" % len(found["unbound"]) + (" (%s)" % names(found["unbound"], 3) if found["unbound"] else "")
    c.detail += "; %d suppressed" % len(found["suppressed"]) + (" (%s)" % names(found["suppressed"], 3) if found["suppressed"] else "")
    c.detail += "; %d of %d locators verified" % (c.counts["locators_verified"], c.counts["locators"])
    return c


def check_wiki_stale(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-stale")
    if scan["graph"]["current_incomplete"] and any(p["state"] == "ok" and p["based_on"] for p in pages):
        return c.not_probed("the supersedes graph is incomplete (dangling targets or cycles): no page is current for sure", "warn")
    found = _state_check(c, pages, ("stale",), "warn")
    c.counts = {"stale": len(found["stale"])}
    c.detail = "%d pages rest on a superseded revision" % len(found["stale"]) + (
        " (%s)" % names(found["stale"], 3) if found["stale"] else "")
    return c


def check_wiki_inputs(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-inputs")
    found = _state_check(c, pages, ("edited-outside-update", "inputs-changed"), "warn")
    c.counts = {"edited_outside_update": len(found["edited-outside-update"]), "inputs_changed": len(found["inputs-changed"])}
    c.detail = "%d edited outside research-update" % len(found["edited-outside-update"]) + (
        " (%s)" % names(found["edited-outside-update"], 3) if found["edited-outside-update"] else "")
    c.detail += "; %d with changed inputs" % len(found["inputs-changed"]) + (
        " (%s)" % names(found["inputs-changed"], 3) if found["inputs-changed"] else "")
    return c


def pages_by_collection(pages: List[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for p in pages:
        if p["state"] == "unreadable":
            continue
        for cname in p["collections"]:
            out.setdefault(cname, []).append(p)
    return out


def check_wiki_evidence(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-evidence")
    by_col = pages_by_collection(pages)
    uncited: List[str] = []
    covered = 0
    for col in scan["collections"]["notes"]:
        its = by_col.get(col["name"])
        if not its or col["state"] == "unreadable":
            continue
        covered += 1
        cited: set = set()
        for p in its:
            cited.update(p.get("cited") or [])
        for it in col["items"]:
            if it["excluded"] or it["rev"] in cited or it["exists"] is False:
                continue          # an unresolved item is research/collections' finding, not an uncited one
            uncited.append("%s · %s" % (col["name"], it["rev"]))
            c.add("warn", {"id": "Collections/%s · %s" % (col["name"], it["rev"]), "reason": "uncited: no verified locator on any of the %s" % plural(len(its), "page"),
                           "rating": None, "words": None, "role": it["role"]})
    found = _state_check(c, pages, ("weak-evidence",), "warn")
    for p in pages:
        for note in (p.get("reasons") or {}).get("info", []):
            c.items.append(page_item(p, "info: " + note))
    c.counts = {"collections_with_pages": covered, "uncited": len(uncited), "weak_evidence": len(found["weak-evidence"])}
    c.detail = "%d dossiers have pages; %s" % (covered, plural(len(uncited), "uncited item")) + (
        " (%s)" % names(uncited, 3) if uncited else "")
    c.detail += "; %d pages on weak evidence" % len(found["weak-evidence"]) + (
        " (%s)" % names(found["weak-evidence"], 3) if found["weak-evidence"] else "")
    return c


def check_wiki_orphan(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-orphan")
    found = _state_check(c, pages, ("orphan",), "warn")
    c.counts = {"orphan": len(found["orphan"])}
    c.detail = "%d pages without an open supporting dossier" % len(found["orphan"]) + (
        " (%s)" % names(found["orphan"], 3) if found["orphan"] else "")
    return c


def check_wiki_budget(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/wiki-budget")
    limit_pages, limit_words = cfg["wiki_pages_per_collection"], cfg["wiki_words"]
    by_col = pages_by_collection(pages)
    over: List[str] = []
    for cname, its in sorted(by_col.items()):
        if len(its) > limit_pages:
            over.append(cname)
            c.add("warn", {"id": "Collections/" + cname, "reason": "%d pages under one dossier (budget %d)" % (len(its), limit_pages),
                           "rating": None, "words": None, "pages": len(its)})
    found = _state_check(c, pages, ("budget",), "warn")
    c.counts = {"collections_over": len(over), "pages_over": len(found["budget"])}
    c.threshold = {"pages_per_collection": limit_pages, "words": limit_words}
    c.detail = "%d dossiers over %d pages" % (len(over), limit_pages) + (" (%s)" % names(over, 3) if over else "")
    c.detail += "; %d pages over %d words" % (len(found["budget"]), limit_words) + (
        " (%s)" % names(found["budget"], 3) if found["budget"] else "")
    return c


def _index_section(index_text: Optional[str], heading: str) -> Optional[List[str]]:
    """The `- ` rows of one `## <heading>` section of index.md; None when the section is absent."""
    if index_text is None:
        return None
    lines = index_text.split("\n")
    try:
        start = lines.index("## " + heading)
    except ValueError:
        return None
    rows: List[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            rows.append(line)
    return rows


def check_research_index(scan: dict, cfg: dict, pages: List[dict]) -> Check:
    c = Check("research/index")
    vault = Path(scan["vault"])
    want = {}
    if scan["collections"]["present"]:
        want["Collections"] = lv.kr.collection_rows(vault)
    if scan["wiki"]["present"]:
        want["Wiki"] = lv.kr.wiki_rows(vault)
    differ = 0
    missing_sections: List[str] = []
    if scan["index_text"] is None:
        if want:
            c.raise_to("warn")
        c.counts = {"sections": len(want), "rows": sum(len(v) for v in want.values()), "differ": 0, "missing_sections": len(want)}
        c.detail = "index.md missing; %d research rows to generate" % c.counts["rows"]
        return c
    for heading, rows in want.items():
        have = _index_section(scan["index_text"], heading)
        if have is None:
            missing_sections.append(heading)
            c.add("warn", {"id": "## " + heading, "reason": "section missing (%d rows to generate)" % len(rows), "rating": None, "words": None})
            continue
        if have != rows:
            differ += 1
            c.add("warn", {"id": "## " + heading, "reason": "%d rows in index.md, %d regenerated — the section differs" % (len(have), len(rows)),
                           "rating": None, "words": None})
    status_rows = {}
    for key in ("Collections", "Wiki"):
        m = re.search(r"^\| %s \| ([^|]+) \|" % key, scan["index_text"], re.M)
        status_rows[key] = m.group(1).strip() if m else None
        present = scan["collections"]["present"] if key == "Collections" else scan["wiki"]["present"]
        if status_rows[key] is None and not present:
            continue          # an index from before the row existed, on a vault without the folder: nothing to say
        count = scan["counters"]["collections"] if key == "Collections" else scan["counters"]["wiki_pages"]
        unlisted = scan["snapshot"].get("collections_unlisted" if key == "Collections" else "wiki_unlisted")
        expected = "?" if unlisted else str(count)
        if status_rows[key] != expected:
            c.add("warn", {"id": "| %s |" % key, "reason": "Status row says %s, the vault has %s" % (status_rows[key] or "nothing", expected),
                           "rating": None, "words": None})
    c.counts = {"sections": len(want), "rows": sum(len(v) for v in want.values()), "differ": differ,
                "missing_sections": len(missing_sections), "status_rows": status_rows}
    c.detail = "%d sections, %d rows; %d differ, %d missing" % (len(want), c.counts["rows"], differ, len(missing_sections))
    return c


RESEARCH_CHECKS = (
    check_research_collections, check_collection_age, check_wiki_schema, check_wiki_binding, check_wiki_stale,
    check_wiki_inputs, check_wiki_evidence, check_wiki_orphan, check_wiki_budget, check_research_index,
)


def run_research_checks(scan: dict, cfg: dict) -> Tuple[List[Check], List[dict], List[dict]]:
    """The ten research checks on the snapshot: the pages validated once (lib_research.validate_wiki),
    an unlistable folder makes its checks not probed and a fail diagnostic, a check that raises is
    not probed with the error named."""
    diagnostics: List[dict] = []
    ctx = lv.research_context(scan, budget_words=cfg["wiki_words"])
    pages = [lr.validate_wiki(p, ctx) for p in scan["wiki"]["notes"]]
    wiki_unlisted = scan["snapshot"].get("wiki_unlisted")
    col_unlisted = scan["snapshot"].get("collections_unlisted")
    if wiki_unlisted:
        diagnostics.append({"code": "error", "level": "fail", "message": "Wiki/ could not be listed (%s): the wiki checks were not probed" % wiki_unlisted})
    if col_unlisted:
        diagnostics.append({"code": "error", "level": "fail", "message": "Collections/ could not be listed (%s): the collection checks were not probed" % col_unlisted})
    checks: List[Check] = []
    raise_in = test_hook("MNEMON_TEST_DOCTOR_RAISE")
    for fn, name in zip(RESEARCH_CHECKS, RESEARCH_CHECK_NAMES):
        if wiki_unlisted and (name in WIKI_CHECKS or name == "research/index"):
            checks.append(Check(name).not_probed("Wiki/ could not be listed: %s" % wiki_unlisted, "warn"))
            continue
        if col_unlisted and name in COLLECTION_CHECKS + ("research/wiki-evidence", "research/wiki-binding", "research/wiki-orphan"):
            checks.append(Check(name).not_probed("Collections/ could not be listed: %s" % col_unlisted, "warn"))
            continue
        try:
            if raise_in == name:
                raise RuntimeError("test hook")
            checks.append(fn(scan, cfg, pages))
        except Exception as exc:
            checks.append(Check(name).not_probed("error: %s: %s" % (type(exc).__name__, exc), "warn"))
            diagnostics.append({"code": "error", "level": "fail", "message": "%s: %s: %s" % (name, type(exc).__name__, exc)})
            scan["snapshot"]["complete"] = False
    return checks, diagnostics, pages


def research_counters(scan: dict) -> dict:
    c = scan["counters"]
    return {k: c[k] for k in c if k.startswith("collection") or k.startswith("wiki")}


def build_research_document(scan: dict, cfg: dict, today: datetime.date) -> dict:
    checks, diagnostics, pages = run_research_checks(scan, cfg)
    checks_json = [c.as_json() for c in checks]
    snapshot = {"today": today.isoformat(), "budget_s": cfg.get("budget"), "complete": scan["snapshot"]["complete"],
                "unreadable": scan["snapshot"]["unreadable"], "collections_unlisted": scan["snapshot"].get("collections_unlisted"),
                "wiki_unlisted": scan["snapshot"].get("wiki_unlisted"), "pages_probed": sum(1 for p in pages if p["severity"] is not None),
                "pages_not_probed": sum(1 for p in pages if p["severity"] is None)}
    summary = summary_of(checks_json, diagnostics)
    summary["coverage"] = {"pages": len(pages), "collections": len(scan["collections"]["notes"])}
    summary["pages"] = {"fail": sum(1 for p in pages if p["severity"] == "fail"), "warn": sum(1 for p in pages if p["severity"] == "warn"),
                        "ok": sum(1 for p in pages if p["severity"] == "ok"), "not_probed": snapshot["pages_not_probed"]}
    return {"schema": RESEARCH_SCHEMA, "instance": cfg["instance"], "vault": scan["vault"], "generated_at": utc_now(),
            "snapshot": snapshot, "counters": research_counters(scan),
            "pages": [{"name": p["name"], "kind": p["kind"], "severity": p["severity"], "states": p["states"],
                       "usable": p["usable"], "collections": p["collections"], "updated": p["updated"]} for p in pages],
            "checks": checks_json, "diagnostics": diagnostics, "summary": summary}


def not_probed_research_document(cfg: dict, today: datetime.date, reason: str, diagnostic: dict) -> dict:
    checks = [Check(name).not_probed(reason).as_json() for name in RESEARCH_CHECK_NAMES]
    snapshot = {"today": today.isoformat(), "budget_s": cfg.get("budget"), "complete": False, "unreadable": 0,
                "collections_unlisted": None, "wiki_unlisted": None, "pages_probed": 0, "pages_not_probed": 0}
    summary = summary_of(checks, [diagnostic])
    summary["coverage"] = {"pages": None, "collections": None}
    summary["pages"] = {"fail": 0, "warn": 0, "ok": 0, "not_probed": None}
    return {"schema": RESEARCH_SCHEMA, "instance": cfg["instance"], "vault": cfg["vault"], "generated_at": utc_now(),
            "snapshot": snapshot, "counters": {}, "pages": [], "checks": checks, "diagnostics": [diagnostic], "summary": summary}


# ---------------------------------------------------------------------------------------------
# Running: the document, its renderings, the budgeted worker
# ---------------------------------------------------------------------------------------------

def test_hook(name: str) -> Optional[str]:
    """A test hook's value, honoured only under MNEMON_TEST_HOOKS=1."""
    if os.environ.get("MNEMON_TEST_HOOKS") != "1":
        return None
    return os.environ.get(name) or None


def run_checks(scan: dict, cfg: dict) -> Tuple[List[Check], List[dict]]:
    """All 19 checks (one broken check never hides the others: it is `not probed` with the
    error as its reason, and a fail-level `error` diagnostic says so) and the diagnostics."""
    diagnostics: List[dict] = [{"code": "radius-parse", "level": "warn", "message": msg} for msg in cfg["radius_errors"]]
    if scan["snapshot"].get("lock") == "error":
        diagnostics.append({"code": "error", "level": "warn",
                            "message": "vault lock not probed: the lock file could not be opened or flocked (lock_held: null)"})
    checks: List[Check] = []
    raise_in = test_hook("MNEMON_TEST_DOCTOR_RAISE")
    if scan["snapshot"].get("unreadable"):
        # a file that could not be read is coverage lost: the snapshot is incomplete and the text says so
        scan["snapshot"]["complete"] = False
        diagnostics.append({"code": "unreadable", "level": "warn",
                            "message": "%d file(s) could not be read (the checks count them as unreadable; coverage incomplete)"
                                       % scan["snapshot"]["unreadable"]})
    unlisted = scan["snapshot"].get("sources_unlisted")
    if unlisted:
        diagnostics.append({"code": "error", "level": "fail",
                            "message": "Sources/ could not be listed (%s): the folder checks were not probed" % unlisted})
    for fn, name in zip(CHECKS, CHECK_NAMES):
        if unlisted and name not in FOLDER_FREE_CHECKS:
            checks.append(Check(name).not_probed("Sources/ could not be listed: %s" % unlisted, "warn"))
            continue
        try:
            if raise_in == name:
                raise RuntimeError("test hook")
            checks.append(fn(scan, cfg))
        except Exception as exc:
            checks.append(Check(name).not_probed("error: %s: %s" % (type(exc).__name__, exc), "warn"))
            diagnostics.append({"code": "error", "level": "fail", "message": "%s: %s: %s" % (name, type(exc).__name__, exc)})
            scan["snapshot"]["complete"] = False
    return checks, diagnostics


def summary_of(checks: List[dict], diagnostics: List[dict]) -> dict:
    s = {"checks": len(checks), "ok": 0, "warn": 0, "fail": 0, "probed": 0, "not_probed": 0,
         "diagnostics_warn": 0, "diagnostics_fail": 0}
    for c in checks:
        s[c["status"]] += 1
        s["probed" if c["probed"] else "not_probed"] += 1
    for d in diagnostics:
        s["diagnostics_" + d["level"]] += 1
    return s


def build_documents(cfg: dict, today: datetime.date) -> Tuple[dict, dict]:
    """One read-only snapshot, the radius, the 19 checks, the diagnostics — the frozen data document —
    and, on the same snapshot, the research document (phase 3b)."""
    scan = lv.scan_vault(cfg["vault"], state_dir=cfg["state_dir"], archive_dir=cfg["archive"], charter=cfg["charter"],
                         reader_context=cfg["reader_context"], today=today)
    radius, defaulted, errors = parse_radius(scan["charter"]["radius_lines"])
    cfg.update({"radius": radius, "radius_defaulted": defaulted, "radius_errors": errors})
    checks, diagnostics = run_checks(scan, cfg)
    checks_json = [c.as_json() for c in checks]
    # the data snapshot is what v1.3 printed: the research folder listings are reported in the
    # research document, never here (mnemon-doctor-data/1 stays byte for byte)
    snapshot = {k: v for k, v in scan["snapshot"].items() if k != "wiki_unlisted"}
    snapshot["today"] = today.isoformat()
    snapshot["budget_s"] = cfg.get("budget")
    data = {"schema": SCHEMA, "instance": cfg["instance"], "vault": scan["vault"], "generated_at": utc_now(),
            "snapshot": snapshot, "counters": scan["counters"], "checks": checks_json, "diagnostics": diagnostics,
            "summary": summary_of(checks_json, diagnostics)}
    research = build_research_document(scan, cfg, today)
    return data, research


def build_document(cfg: dict, today: datetime.date) -> dict:
    return build_documents(cfg, today)[0]


def not_probed_document(cfg: dict, today: datetime.date, reason: str, diagnostic: dict) -> dict:
    """The document when nothing could be observed: all 19 checks not probed, one diagnostic."""
    checks = [Check(name).not_probed(reason).as_json() for name in CHECK_NAMES]
    snapshot = {"lock_held": False, "unreadable": 0, "budget_s": cfg.get("budget"), "complete": False,
                "today": today.isoformat()}
    return {"schema": SCHEMA, "instance": cfg["instance"], "vault": cfg["vault"], "generated_at": utc_now(),
            "snapshot": snapshot, "counters": {}, "checks": checks, "diagnostics": [diagnostic],
            "summary": summary_of(checks, [diagnostic])}


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows_of(doc: dict, prefix: str = "doctor/") -> List[Tuple[str, str, str]]:
    """(status, name, detail) of a document's checks, then its diagnostics as doctor/<code>."""
    rows = [(c["status"], c["name"], c["detail"]) for c in doc["checks"]]
    rows += [(d["level"], prefix + d["code"], d["message"]) for d in doc.get("diagnostics", [])]
    return rows


def text_lines(doc: dict, tsv: bool, research: Optional[dict] = None) -> List[str]:
    rows = rows_of(doc) + (rows_of(research) if research is not None else [])
    if tsv:
        return ["%s\t%s\t%s" % (s, n, d.replace("\t", " ").replace("\n", " ")) for s, n, d in rows]
    return ["%s %s — %s" % (s, n, d) for s, n, d in rows]


def list_lines(doc: dict, wanted: str, research: Optional[dict] = None) -> Optional[List[str]]:
    """Every item of one check, by its full name or the part after `/` (the data checks first,
    then `research/<name>`)."""
    checks = list(doc["checks"]) + (list(research["checks"]) if research is not None else [])
    for c in checks:
        if c["name"] == wanted or c["name"].split("/", 1)[1] == wanted:
            out = []
            for i in c["items"]:
                rating = i.get("rating")
                words = i.get("words")
                out.append("%s\t%s\t%s\t%s" % (i["id"], i.get("reason") or i.get("class") or "-",
                                               rating if rating is not None else "-", words if words is not None else "-"))
            return out
    return None


def research_brief(research: Optional[dict]) -> str:
    """The research part of the brief line: `research: N pages · K fail · M warn` (the pages' own
    severities, then the checks' fails and warns when they exceed them), or `not probed (…)`."""
    if research is None:
        return "research: not probed"
    c = research.get("counters") or {}
    stopped = next((d for d in research.get("diagnostics", []) if d["code"] in ("budget", "error") and not c), None)
    if stopped:
        return "research: not probed (%s)" % stopped["message"]
    s = research["summary"]
    return "research: %d pages · %d fail · %d warn" % (c.get("wiki_pages", 0), s["fail"], s["warn"])


def brief_line(doc: dict, research: Optional[dict] = None) -> str:
    name = doc.get("instance") or "vault"
    label = name[:1].upper() + name[1:]
    s = doc["summary"]
    c = doc.get("counters") or {}
    stopped = next((d for d in doc.get("diagnostics", []) if d["code"] in ("budget", "error") and not c), None)
    if stopped:
        return "%s · data: not probed (%s) · %s" % (label, stopped["message"], research_brief(research))
    oldest = {}
    for check in doc["checks"]:
        if check["name"] in ("human/seeds", "human/ratification"):
            oldest[check["name"]] = (check.get("counts") or {}).get("oldest_days")
    seed_part = "%d seeds" % c["seeds"] + (" / %d d" % oldest["human/seeds"] if c["seeds"] and oldest.get("human/seeds") is not None else "")
    draft_part = "%d drafts" % c["drafts_unratified"] + (
        " / %d d" % oldest["human/ratification"] if c["drafts_unratified"] and oldest.get("human/ratification") is not None else "")
    line = ("%s · %d sources (%d managed · %d current · %d superseded) · %d extracted · %d synthesis (%s · %s)"
            " · data: %d fail · %d warn · probed %d/%d · %s"
            % (label, c["folders"], c["managed"], c["current"], c["superseded"], c["extracted"], c["synthesis"],
               seed_part, draft_part, s["fail"], s["warn"], s["probed"], s["probed"] + s["not_probed"], research_brief(research)))
    if s["diagnostics_fail"] or s["diagnostics_warn"]:
        diag = []
        if s["diagnostics_fail"]:
            diag.append("%d fail" % s["diagnostics_fail"])
        if s["diagnostics_warn"]:
            diag.append("%d warn" % s["diagnostics_warn"])
        line += " · diagnostics: " + " · ".join(diag)
    if doc["snapshot"].get("unreadable"):
        line += " (unreadable %d)" % doc["snapshot"]["unreadable"]
    if doc["snapshot"].get("lock_held"):
        line += " (snapshot may be partial)"
    elif doc["snapshot"].get("lock_held") is None and doc["snapshot"].get("lock") == "error":
        line += " (lock not probed)"
    return line


def this_host() -> str:
    try:
        return socket.gethostname().split(".", 1)[0]
    except OSError:
        return "unknown"


def settings(args) -> dict:
    """What the doctor needs beyond the scan: paths, thresholds, identity of this host."""
    cfg: Dict[str, object] = {}
    if args.vault:
        vault = os.path.abspath(args.vault)
        cfg.update({"vault": vault, "archive": args.archive or None, "charter": args.charter,
                    "reader_context": args.reader_context, "state_dir": args.state_dir,
                    "instance": args.instance or os.path.basename(vault.rstrip("/")) or "vault",
                    "writer_host": args.writer_host or ""})
        for key in DEFAULTS:
            cfg[key] = DEFAULTS[key]
    else:
        import lib_config
        loaded = lib_config.load_config()
        cfg.update({"vault": loaded["VAULT_PATH"], "archive": loaded.get("ARCHIVE_DIR") or None,
                    "charter": loaded.get("CHARTER_PATH") or None, "reader_context": loaded.get("READER_CONTEXT_PATH") or None,
                    "state_dir": loaded.get("MNEMON_VAULT_STATE_DIR") or None, "instance": loaded.get("MNEMON_INSTANCE") or "vault",
                    "writer_host": loaded.get("WRITER_HOST") or ""})
        for key, var in (("thin_words", "DOCTOR_THIN_WORDS"), ("stub_words", "DOCTOR_STUB_WORDS"),
                         ("arxiv_words", "DOCTOR_ARXIV_WORDS"), ("rating_hi_share", "DOCTOR_RATING_HI_SHARE")):
            cfg[key] = lv.parse_int(loaded.get(var)) if loaded.get(var) else None
            if cfg[key] is None:
                cfg[key] = DEFAULTS[key]
    for key, value in (("thin_words", args.thin_words), ("stub_words", args.stub_words),
                       ("arxiv_words", args.arxiv_words), ("rating_hi_share", args.rating_hi_share)):
        if value is not None:
            cfg[key] = value
    # the research budget (interface §10): config keys, then the flags
    if args.vault:
        for key in RESEARCH_DEFAULTS:
            cfg[key] = RESEARCH_DEFAULTS[key]
    else:
        for key, var in (("wiki_pages_per_collection", "DOCTOR_WIKI_PAGES_PER_COLLECTION"), ("wiki_words", "DOCTOR_WIKI_WORDS")):
            cfg[key] = lv.parse_int(loaded.get(var)) if loaded.get(var) else None
            if cfg[key] is None:
                cfg[key] = RESEARCH_DEFAULTS[key]
    for key, value in (("wiki_pages_per_collection", args.wiki_pages_per_collection), ("wiki_words", args.wiki_words)):
        if value is not None:
            cfg[key] = value
    cfg["guide_version"] = args.guide_version if args.guide_version is not None else lv.engine_guide_version()
    cfg["this_host"] = this_host()
    cfg["budget"] = args.budget
    return cfg


def worker_argv(args) -> List[str]:
    """The child's command line: this script with --worker and the settings, never the output mode."""
    argv = [sys.executable, os.path.abspath(__file__), "--worker"]
    for flag, value in (("--today", args.today), ("--vault", args.vault), ("--archive", args.archive),
                        ("--charter", args.charter), ("--reader-context", args.reader_context),
                        ("--state-dir", args.state_dir), ("--instance", args.instance), ("--writer-host", args.writer_host),
                        ("--guide-version", args.guide_version), ("--thin-words", args.thin_words),
                        ("--stub-words", args.stub_words), ("--arxiv-words", args.arxiv_words),
                        ("--rating-hi-share", args.rating_hi_share), ("--budget", args.budget),
                        ("--wiki-pages-per-collection", args.wiki_pages_per_collection), ("--wiki-words", args.wiki_words)):
        if value is not None:
            argv += [flag, str(value)]
    return argv


def _worker_documents(text: str) -> Tuple[Optional[dict], Optional[dict]]:
    """(data, research) from the worker's stdout: one JSON document per line, in that order; a
    missing or unreadable line is None (the parent decides what that means)."""
    docs: List[Optional[dict]] = []
    for line in text.split("\n")[:2]:
        if not line.strip():
            docs.append(None)
            continue
        try:
            d = json.loads(line)
            docs.append(d if isinstance(d, dict) else None)
        except ValueError:
            docs.append(None)
    while len(docs) < 2:
        docs.append(None)
    return docs[0], docs[1]


def run_with_budget(args, cfg: dict, today: datetime.date, started: float) -> Tuple[dict, dict, int]:
    """The whole scan in a child worker, killed at the deadline (terminate, then kill): the parent
    never blocks past the budget, whatever the worker is stuck on. One budget covers both documents:
    the worker prints the data document as soon as it is built, then the research one — the
    document the deadline hit is `not probed`, the one already printed is kept."""
    budget = float(args.budget)
    remaining = max(0.0, budget - (time.monotonic() - started))   # 0 = already late: start, then kill at once
    proc = subprocess.Popen(worker_argv(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
    try:
        out, err = proc.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass        # stuck in an uninterruptible call: reparented, it dies when the call returns
        out = b""
        if proc.returncode is not None:
            try:
                out, _err = proc.communicate(timeout=1.0)     # what the worker printed before the deadline
            except (subprocess.TimeoutExpired, ValueError, OSError):
                out = b""
        message = "budget %g s exceeded" % budget
        data, research = _worker_documents((out or b"").decode("utf-8", "replace"))
        if data is None:
            data = not_probed_document(cfg, today, message, {"code": "budget", "level": "warn", "message": message})
        if research is None:
            research = not_probed_research_document(cfg, today, message, {"code": "budget", "level": "warn",
                                                                            "message": "research: " + message})
        return data, research, 0
    text = out.decode("utf-8", "replace")
    if proc.returncode != 0 or not text.strip():
        message = "worker exited %d: %s" % (proc.returncode, err.decode("utf-8", "replace").strip()[-300:] or "no output")
        diag = {"code": "error", "level": "fail", "message": message}
        return (not_probed_document(cfg, today, "error: " + message, diag),
                not_probed_research_document(cfg, today, "error: " + message, dict(diag, message="research: " + message)), 1)
    data, research = _worker_documents(text)
    if data is None or research is None:
        message = "worker printed no readable document"
        diag = {"code": "error", "level": "fail", "message": message}
        if data is None:
            data = not_probed_document(cfg, today, "error: " + message, diag)
        if research is None:
            research = not_probed_research_document(cfg, today, "error: " + message, dict(diag, message="research: " + message))
        return data, research, 1
    return data, research, 0


def main(argv: List[str]) -> int:
    started = time.monotonic()
    ap = argparse.ArgumentParser(description="The data group of mnemon doctor (read-only)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tsv", action="store_true", help="status<TAB>name<TAB>detail per check (the dispatcher's input)")
    ap.add_argument("--list", metavar="CHECK", help="every item of one check: <id>\\t<reason or class>\\t<rating>\\t<words>")
    ap.add_argument("--brief", action="store_true", help="the one line of mnemon status --brief")
    ap.add_argument("--budget", type=float, default=None, help="seconds for the whole run (--brief default 10)")
    ap.add_argument("--today", default=None, help="fix the clock (YYYY-MM-DD)")
    ap.add_argument("--vault", default=None, help="run on this vault without a config (tests)")
    ap.add_argument("--archive", default=None)
    ap.add_argument("--charter", default=None)
    ap.add_argument("--reader-context", default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--writer-host", default=None)
    ap.add_argument("--guide-version", type=int, default=None)
    ap.add_argument("--thin-words", type=int, default=None)
    ap.add_argument("--stub-words", type=int, default=None)
    ap.add_argument("--arxiv-words", type=int, default=None)
    ap.add_argument("--rating-hi-share", type=int, default=None)
    ap.add_argument("--wiki-pages-per-collection", type=int, default=None, help="the research budget: pages under one dossier (default 5)")
    ap.add_argument("--wiki-words", type=int, default=None, help="the research budget: words in a page's evidential body (default 3000)")
    ap.add_argument("--research", action="store_true", help="with --json: the research document (mnemon-doctor-research/1) instead of the data one")
    ap.add_argument("--envelope", action="store_true", help='both documents as one JSON object {"data": …, "research": …} (the dispatcher\'s input)')
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv[1:])
    if args.brief and args.budget is None:
        args.budget = DEFAULT_BRIEF_BUDGET

    try:
        # the clock: --today, else the test hook (the dispatcher passes no --today; a suite that
        # runs it on fixtures with fixed dates pins the clock this way), else the real date
        fixed = args.today or test_hook("MNEMON_TEST_DOCTOR_TODAY")
        today = datetime.date.fromisoformat(fixed) if fixed else datetime.date.today()
    except ValueError:
        print("vault-doctor: --today expects YYYY-MM-DD, got %r" % (args.today or fixed), file=sys.stderr)
        return 2
    rc = 0
    if args.budget is not None and not args.worker:
        # everything that can block — the configuration loader (up to its own 15 s), the guide stamp,
        # the scan, the checks — runs in the worker under the deadline; the parent loads nothing and
        # knows only the names it was given (§6.4)
        vault_hint = os.path.abspath(args.vault) if args.vault else (os.environ.get("VAULT_PATH") or "?")
        instance_hint = args.instance or (os.path.basename(vault_hint.rstrip("/")) if args.vault else os.environ.get("MNEMON_INSTANCE")) or "?"
        stub = {"instance": instance_hint, "vault": vault_hint, "budget": args.budget}
        doc, research, rc = run_with_budget(args, stub, today, started)
    else:
        stall_init = test_hook("MNEMON_TEST_DOCTOR_STALL_INIT")
        if stall_init:
            time.sleep(float(stall_init))
        try:
            cfg = settings(args)
        except Exception as exc:
            print("vault-doctor: %s" % exc, file=sys.stderr)
            return 1
        if not os.path.isdir(cfg["vault"]):
            print("vault-doctor: vault not found: %s" % cfg["vault"], file=sys.stderr)
            return 1
        if args.worker:
            stall = test_hook("MNEMON_TEST_DOCTOR_STALL")
            if stall:
                pidfile = test_hook("MNEMON_TEST_DOCTOR_PIDFILE")
                if pidfile:
                    with open(pidfile, "w") as fh:
                        fh.write(str(os.getpid()))
                time.sleep(float(stall))
            try:
                doc, research = build_documents(cfg, today)
            except Exception as exc:
                print("vault-doctor: error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
                return 1
            # one document per line, the data one first: the parent keeps what arrived before a deadline
            print(json.dumps(doc, ensure_ascii=False, default=lv._json_default))
            sys.stdout.flush()
            stall_research = test_hook("MNEMON_TEST_DOCTOR_STALL_RESEARCH")
            if stall_research:
                time.sleep(float(stall_research))      # a deadline that hits the research document only
            print(json.dumps(research, ensure_ascii=False, default=lv._json_default))
            return 0
        try:
            doc, research = build_documents(cfg, today)
        except Exception as exc:
            message = "%s: %s" % (type(exc).__name__, exc)
            diag = {"code": "error", "level": "fail", "message": message}
            doc = not_probed_document(cfg, today, "error: " + message, diag)
            research = not_probed_research_document(cfg, today, "error: " + message, dict(diag, message="research: " + message))
            print("vault-doctor: error: %s" % message, file=sys.stderr)
            rc = 1

    if args.list is not None:
        lines = list_lines(doc, args.list, research)
        if lines is None:
            print("vault-doctor: no check named %r (names: %s)" % (args.list, ", ".join(CHECK_NAMES + RESEARCH_CHECK_NAMES)), file=sys.stderr)
            return 2
        for line in lines:
            print(line)
        return rc
    if args.brief:
        if args.json:
            out = {k: v for k, v in doc.items() if k != "checks"}
            out["research"] = {k: v for k, v in research.items() if k not in ("checks", "pages")}
            out["brief"] = brief_line(doc, research)
            print(json.dumps(out, ensure_ascii=False, default=lv._json_default))
        else:
            print(brief_line(doc, research))
        return rc
    if args.envelope:
        print(json.dumps({"data": doc, "research": research}, ensure_ascii=False, default=lv._json_default))
        return rc
    if args.json:
        print(json.dumps(research if args.research else doc, ensure_ascii=False, default=lv._json_default))
        return rc
    for line in text_lines(doc, args.tsv, research):
        print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
