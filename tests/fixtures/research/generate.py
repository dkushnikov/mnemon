#!/usr/bin/env python3
"""generate.py — the expected readings of the research compatibility fixture vault.

    python3 tests/fixtures/research/generate.py --check    compare the tools' current output with expected/ (exit 1 on a difference)
    python3 tests/fixtures/research/generate.py --write    regenerate expected/ (a deliberate contract change — the spec changes in the same PR)

What is pinned (docs/specs/2026-10-27-research-interface.md §11):
- expected/research.json        — `lib_research.py --vault <fixture> --json`: every collection/1 record and every wiki/1 record with its states
- expected/counters.json        — the `lib_vault.counters()` keys the interface freezes (relations, collections)
- expected/index.md             — the Status table, the `## Collections` and `## Wiki` sections `knowledge-reindex.py` writes
- expected/search-default.json  — `mnemon-search.py --query trust` (Sources only: collections are opt-in)
- expected/search-all.json      — `--query trust --include-source --include-synthesis --include-collections --limit 20`
- expected/search-domain.json   — `--query trust --include-collections --domain mc/ai`
- expected/search-wiki.json     — `--query trust --include-wiki` (the wiki block: usable pages only, orphans left out)
- expected/search-wiki-orphans.json — `--query trust --include-wiki --include-orphans --wiki-limit 10 --domain learning`
- expected/doctor-data.json     — `vault-doctor.py --json` on the fixture (guide pinned at the engine's, a temp state dir,
                                  `--today 2026-10-27`), with `generated_at` dropped, the vault and state paths replaced by
                                  `<vault>` / `<state>` and the research counter keys (`collection*`, `wiki*`) removed: the
                                  mnemon-doctor-data/1 document the branches must leave byte-for-byte as it is
- expected/doctor-research.json — `vault-doctor.py --research --json` on the same run (`--wiki-words 80`): the
                                  mnemon-doctor-research/1 document — every derived state of the fixture pages (phase 3b)

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
                "collection_items_unresolved",
                "wiki_pages", "wiki_fail", "wiki_unreadable", "wiki_based_on")
WIKI_WORDS = "80"      # the research budget pinned for the goldens: one fixture page is over it


ISOLATION = tempfile.mkdtemp(prefix="mnemon-research-xdg-")


def run(*argv: str, cwd: str | None = None) -> str:
    env = dict(os.environ)
    for key in ("MNEMON_CONFIG", "MNEMON_INSTANCE", "MNEMON_CONFIG_DIR", "MNEMON_VAULT_PATH", "MNEMON_HOME"):
        env.pop(key, None)
    env["XDG_CONFIG_HOME"] = os.path.join(ISOLATION, "xdg")
    env["XDG_STATE_HOME"] = os.path.join(ISOLATION, "state")
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
    out["search-wiki.json"] = json.loads(run(search, "--vault", str(VAULT), "--query", "trust", "--include-wiki"))
    out["search-wiki-orphans.json"] = json.loads(run(search, "--vault", str(VAULT), "--query", "trust", "--include-wiki",
                                                     "--include-orphans", "--wiki-limit", "10", "--domain", "learning"))
    state = os.path.join(ISOLATION, "doctor-state")
    os.makedirs(state, exist_ok=True)
    guide = run(str(BIN / "lib_vault.py"), "--vault", str(VAULT), "--json")   # warms nothing; the guide comes from the engine
    doc_text = run(str(BIN / "vault-doctor.py"), "--vault", str(VAULT), "--state-dir", state, "--today", "2026-10-27",
                   "--guide-version", str(_engine_guide()), "--json")
    doc_text = doc_text.replace(str(VAULT), "<vault>").replace(state, "<state>")
    doc = json.loads(doc_text)
    doc.pop("generated_at", None)
    # the phase 3a (`collection*`), phase 3b (`wiki*`) and phase 3-capture (`tier`, `triage`, `promoted`)
    # counter keys are additive to mnemon-doctor-data/1 and normalized away: the document the branches
    # must leave as it is
    doc["counters"] = {k: v for k, v in doc["counters"].items()
                       if not (k.startswith("collection") or k.startswith("wiki")) and k not in ("tier", "triage", "promoted")}
    out["doctor-data.json"] = doc
    res_text = run(str(BIN / "vault-doctor.py"), "--vault", str(VAULT), "--state-dir", state, "--today", "2026-10-27",
                   "--guide-version", str(_engine_guide()), "--wiki-words", WIKI_WORDS, "--research", "--json")
    res = json.loads(res_text.replace(str(VAULT), "<vault>").replace(state, "<state>"))
    res.pop("generated_at", None)
    out["doctor-research.json"] = res
    return out


def _engine_guide() -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location("lib_vault", BIN / "lib_vault.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.engine_guide_version()


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
