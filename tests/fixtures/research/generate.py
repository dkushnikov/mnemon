#!/usr/bin/env python3
"""generate.py — the expected readings of the research compatibility fixture vault.

    python3 tests/fixtures/research/generate.py --check    compare the tools' current output with expected/ (exit 1 on a difference)
    python3 tests/fixtures/research/generate.py --write    regenerate expected/ (a deliberate contract change — the spec changes in the same PR)

What is pinned (docs/specs/2026-10-27-research-interface.md §11):
- expected/research.json        — `lib_research.py --vault <fixture> --json`: every collection/1 record
- expected/counters.json        — the `lib_vault.counters()` keys the interface freezes (relations, collections)
- expected/index.md             — the Status table and the `## Collections` section `knowledge-reindex.py` writes
- expected/search-default.json  — `mnemon-search.py --query trust` (Sources only: collections are opt-in)
- expected/search-all.json      — `--query trust --include-source --include-synthesis --include-collections --limit 20`
- expected/search-domain.json   — `--query trust --include-collections --domain mc/ai`

Stdlib only, python 3.9; runs the tools as subprocesses with the interpreter that runs this script.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BIN = ROOT / "bin"
VAULT = HERE / "vault"
EXPECTED = HERE / "expected"
COUNTER_KEYS = ("folders", "extracted", "captured", "current", "superseded", "synthesis", "seeds", "stable",
                "drafts_unratified", "integrated", "filed", "links_unresolved",
                "collections", "collections_fail", "collections_unreadable", "collections_status",
                "collection_items", "collection_items_excluded", "collection_items_malformed",
                "collection_items_unresolved")


def run(*argv: str, cwd: str | None = None) -> str:
    env = dict(os.environ)
    for key in ("MNEMON_CONFIG", "MNEMON_INSTANCE", "MNEMON_CONFIG_DIR", "MNEMON_VAULT_PATH"):
        env.pop(key, None)
    p = subprocess.run([sys.executable, *argv], capture_output=True, text=True, env=env, cwd=cwd)
    if p.returncode not in (0, 1):
        sys.exit("%s exited %d:\n%s" % (argv[0], p.returncode, p.stderr))
    return p.stdout


def readings() -> dict[str, object]:
    out: dict[str, object] = {}
    out["research.json"] = json.loads(run(str(BIN / "lib_research.py"), "--vault", str(VAULT), "--json"))
    counters = json.loads(run(str(BIN / "lib_vault.py"), "--vault", str(VAULT), "--json"))["counters"]
    out["counters.json"] = {k: counters[k] for k in COUNTER_KEYS}
    tmp = tempfile.mkdtemp(prefix="mnemon-research-fixture-")
    try:
        copy = Path(tmp) / "vault"
        shutil.copytree(VAULT, copy)
        run(str(BIN / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report")
        text = (copy / "index.md").read_text(encoding="utf-8")
        lines = text.split("\n")
        status = lines[lines.index("## Status"):lines.index("## Recent Sources")]
        cols = lines[lines.index("## Collections"):lines.index("## Synthesis Notes")]
        out["index.md"] = "\n".join(status + cols).rstrip("\n") + "\n"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    search = str(BIN / "mnemon-search.py")
    out["search-default.json"] = json.loads(run(search, "--vault", str(VAULT), "--query", "trust"))
    out["search-all.json"] = json.loads(run(search, "--vault", str(VAULT), "--query", "trust", "--include-source",
                                            "--include-synthesis", "--include-collections", "--limit", "20"))
    out["search-domain.json"] = json.loads(run(search, "--vault", str(VAULT), "--query", "trust",
                                               "--include-collections", "--domain", "mc/ai"))
    return out


def dump(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("--check", "--write"):
        print(__doc__)
        return 2
    current = readings()
    if argv[1] == "--write":
        EXPECTED.mkdir(exist_ok=True)
        for name, value in current.items():
            (EXPECTED / name).write_text(dump(value), encoding="utf-8")
            print("wrote", EXPECTED / name)
        return 0
    bad = 0
    for name, value in current.items():
        path = EXPECTED / name
        if not path.is_file():
            print("MISSING %s" % path)
            bad += 1
            continue
        want = path.read_text(encoding="utf-8")
        if dump(value) != want:
            print("DIFFERS %s" % name)
            if name.endswith(".json"):
                a, b = json.loads(want), value
                for key in sorted(set(_keys(a)) | set(_keys(b))):
                    if _get(a, key) != _get(b, key):
                        print("  %s: expected %r, got %r" % (key, _get(a, key), _get(b, key)))
            else:
                import difflib
                print("".join(difflib.unified_diff(want.splitlines(True), dump(value).splitlines(True), "expected", "current")))
            bad += 1
        else:
            print("ok %s" % name)
    return 1 if bad else 0


def _keys(obj: object, prefix: str = "") -> list[str]:
    if isinstance(obj, dict):
        out: list[str] = []
        for k, v in obj.items():
            out += _keys(v, "%s%s." % (prefix, k))
        return out or [prefix.rstrip(".")]
    if isinstance(obj, list):
        out = []
        for i, v in enumerate(obj):
            out += _keys(v, "%s%d." % (prefix, i))
        return out or [prefix.rstrip(".")]
    return [prefix.rstrip(".")]


def _get(obj: object, key: str) -> object:
    cur = obj
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part, "<absent>")
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return "<absent>"
    return cur


if __name__ == "__main__":
    sys.exit(main(sys.argv))
