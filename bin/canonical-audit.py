#!/usr/bin/env python3
"""canonical-audit.py — `mnemon audit --canonical` (spec §3.10, D5): existing folders that coincide under
canonicalization v1.

    canonical-audit.py <vault path> [--json]
    canonical-audit.py --vault <vault path> [--json]     # the form `mnemon audit --canonical` dispatches

Groups `Sources/*/source.md` by the v1 canonical form of `url:` (and of `canonical_url:` when present),
prints the groups of two or more folders with their revisions, `supersedes:` links (a list when one
revision supersedes several — the block-list form of `source.md`; `a,b` in the text output) and extract
status, exits 0 and never writes anything — nothing is merged. Each header is read through its closing `---` or
`...` (cap 256 KB, the rule the lookup and the reindexer share); a folder without a readable header is
reported on stderr as `malformed-frontmatter` and skipped. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import resolve  # noqa: E402

HEADER_CAP = 256 * 1024
_KEYS = ("url", "canonical_url", "supersedes", "revision", "capture_id")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
        if value and "\\" in value:
            value = value.replace('\\"', '"').replace("\\\\", "\\")
    return value


def supersedes_text(value: object) -> str:
    """The `supersedes=` column: `-` for none, the revision, or the list comma-joined."""
    if not value:
        return "-"
    return ",".join(value) if isinstance(value, list) else str(value)


def read_header(path: str) -> Optional[Dict[str, object]]:
    """The top-level keys of a frontmatter header that the audit reads — scalars, except
    `supersedes`, which is a list when it is a block list of several revisions — or None when
    malformed."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(HEADER_CAP + 1)
    except OSError:
        return None
    if not data.startswith(b"---\n"):
        return None
    # the one header rule of the lookup and the reindexer (§12 A2): line by line through the closing
    # `---` or `...` within the cap
    lines = data[:HEADER_CAP].decode("utf-8", "replace").split("\n")
    body: List[str] = []
    closed = False
    for line in lines[1:]:
        if line == "---" or line == "...":
            closed = True
            break
        body.append(line)
    if not closed:
        return None
    out: Dict[str, object] = {}
    items: List[str] = []
    in_list = False
    for line in body:
        if in_list and line[:1] in (" ", "\t"):
            m = re.match(r"^\s*-\s*(.*)$", line)
            if m and _unquote(m.group(1)):
                items.append(_unquote(m.group(1)))
            continue
        if not line.strip():
            continue
        in_list = False
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if not m or m.group(1) not in _KEYS:
            continue
        value = _unquote(m.group(2))
        if m.group(1) == "supersedes" and not value:
            in_list = True          # an empty key: the block list of revisions follows
            continue
        out[m.group(1)] = value
    if items:
        out["supersedes"] = items[0] if len(items) == 1 else items
    return out


def canonical_of(url: str) -> Optional[str]:
    try:
        return resolve.canonicalize(url).url
    except resolve.InvalidURL:
        return None


def audit(vault: str) -> List[dict]:
    sources = os.path.join(vault, "Sources")
    groups: Dict[str, List[dict]] = {}
    alias: Dict[str, str] = {}     # canonical → group key
    try:
        folders = sorted(d for d in os.listdir(sources) if os.path.isdir(os.path.join(sources, d)))
    except OSError:
        return []
    for folder in folders:
        src = os.path.join(sources, folder, "source.md")
        if not os.path.isfile(src):
            continue
        header = read_header(src)
        if header is None:
            print("malformed-frontmatter %s" % folder, file=sys.stderr)
            continue
        keys = []
        for name in ("url", "canonical_url"):
            value = header.get(name)
            canon = canonical_of(value) if value else None
            if canon and canon not in keys:
                keys.append(canon)
        if not keys:
            continue
        group_key = None
        for k in keys:
            if k in alias:
                group_key = alias[k]
                break
        if group_key is None:
            group_key = keys[0]
        for k in keys:
            alias[k] = group_key
        groups.setdefault(group_key, []).append({
            "revision": folder, "url": header.get("url"), "canonical_url": header.get("canonical_url"),
            "canonical": keys[0], "supersedes": header.get("supersedes") or None,
            "extract": "ok" if os.path.isfile(os.path.join(sources, folder, "extract.md")) else "missing",
        })
    return [{"canonical_url": key, "revisions": members} for key, members in sorted(groups.items()) if len(members) >= 2]


def main() -> int:
    ap = argparse.ArgumentParser(description="Folders that coincide under canonicalization v1 (read-only)")
    ap.add_argument("vault", nargs="?", help="the vault path (holds Sources/)")
    ap.add_argument("--vault", dest="vault_opt", metavar="PATH", help="the vault path (the dispatcher's form)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    vault = args.vault_opt or args.vault
    if not vault or (args.vault_opt and args.vault):
        ap.error("one vault path, positional or --vault")
    groups = audit(vault)
    if args.json:
        print(json.dumps({"schema": "mnemon-canonical-audit/1", "canonical_version": resolve.VERSION, "groups": groups},
                         ensure_ascii=False, indent=1))
        return 0
    if not groups:
        print("no canonical duplicates (canonicalization v%d)" % resolve.VERSION)
        return 0
    for g in groups:
        print(g["canonical_url"])
        for r in g["revisions"]:
            print("  %s  url=%s  supersedes=%s  extract=%s" % (r["revision"], r["url"], supersedes_text(r["supersedes"]), r["extract"]))
    print("%d group(s) coincide under canonicalization v%d; nothing was merged" % (len(groups), resolve.VERSION))
    return 0


if __name__ == "__main__":
    sys.exit(main())
