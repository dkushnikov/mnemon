#!/usr/bin/env python3
"""prints.py — the printed observations of the fixture wiki pages, computed from the fixture vault.

    python3 tests/fixtures/research/prints.py            print every fingerprint a Wiki/ page may print
    python3 tests/fixtures/research/prints.py --check    verify the prints the fixture pages carry (exit 1 on a drift)

A page's `based_on[].text_sha256` (seal `observed`: the body of `source.md` by lib_vault.body_bytes —
the exact bytes after the first `\\n---\\n`), its `inputs.collections` (sha256 of the collection file)
and its `body_sha256` (the evidential body by lib_research.evidential_body) are observations of the
fixture files; when a fixture source or collection changes on purpose, this script says which page
prints must follow (and the page named `Edited outside`, `Inputs changed` and `Unbound binding` carry
deliberately wrong prints — listed in WRONG). Stdlib only, python 3.9.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parents[2] / "bin"
VAULT = HERE / "vault"
# page → the prints that are wrong on purpose (the state they exercise)
WRONG = {
    "Edited outside": {"body_sha256": "edited-outside-update"},
    "Inputs changed": {"inputs.collections[Annotated]": "inputs-changed"},
    "Unbound binding": {"based_on[2026-01-10_aaaaaaaa]": "unbound (observed hash ≠ body)"},
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BIN / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    lr = _load("lib_research")
    lv = _load("lib_vault")
    check = len(argv) > 1 and argv[1] == "--check"
    bodies = {}
    for rev in sorted(os.listdir(VAULT / "Sources")):
        p = VAULT / "Sources" / rev / "source.md"
        if p.is_file():
            body = lv.body_bytes(p.read_bytes())
            bodies[rev] = hashlib.sha256(body).hexdigest() if body is not None else None
    cols = {}
    for name in sorted(os.listdir(VAULT / "Collections")):
        if name.endswith(".md"):
            cols[name[:-3]] = hashlib.sha256((VAULT / "Collections" / name).read_bytes()).hexdigest()
    pages = {}
    if (VAULT / "Wiki").is_dir():
        for name in sorted(os.listdir(VAULT / "Wiki")):
            if name.endswith(".md") and not name.startswith("."):
                pages[name[:-3]] = lr.read_wiki_text((VAULT / "Wiki" / name).read_text(encoding="utf-8"), name[:-3])
    if not check:
        print("# source.md bodies (seal observed)")
        for rev, sha in bodies.items():
            print("%s %s" % (rev, sha))
        print("# collections (inputs.collections)")
        for name, sha in cols.items():
            print("%s %s" % (name, sha))
        print("# evidential bodies (body_sha256)")
        for name, rec in pages.items():
            print("%s %s" % (name, rec["body"]["sha256"]))
        return 0
    drift = 0
    for name, rec in pages.items():
        wrong = WRONG.get(name, {})
        if rec["body_sha256"] and rec["body_sha256"] != rec["body"]["sha256"] and "body_sha256" not in wrong:
            print("DRIFT %s: body_sha256 %s ≠ computed %s" % (name, rec["body_sha256"], rec["body"]["sha256"]))
            drift += 1
        for e in rec["based_on"]:
            key = "based_on[%s]" % e["rev"]
            if e["seal"] == "observed" and bodies.get(e["rev"]) and e["text_sha256"] != bodies[e["rev"]] and key not in wrong:
                print("DRIFT %s: %s %s ≠ body %s" % (name, key, e["text_sha256"], bodies[e["rev"]]))
                drift += 1
        for cname, sha in ((rec["inputs"] or {}).get("collections") or {}).items():
            key = "inputs.collections[%s]" % cname
            if cname in cols and sha != cols[cname] and key not in wrong:
                print("DRIFT %s: %s %s ≠ file %s" % (name, key, sha, cols[cname]))
                drift += 1
    print("prints: %d pages checked, %d drift" % (len(pages), drift))
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
