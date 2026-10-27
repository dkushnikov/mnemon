#!/usr/bin/env python3
"""Unit tests for bin/lib_research.py (collection/1) and the research seams of lib_vault.py,
knowledge-reindex.py and mnemon-search.py — docs/specs/2026-10-27-research-interface.md.

Run: `python3 tests/test_research.py` (tests/test_research.sh wraps it and adds the dispatcher
and the fixture goldens). Stdlib only — unittest + tempfile + subprocess; python 3.9.

Test classes
------------
- FrontmatterSubsetTests  — scalars, inline and block lists, duplicate keys, nested mappings
- LinkTests               — every target form the grammar accepts and rejects
- ItemTailTests           — role / note / excluded / the three dashes / trailing punctuation
- GrammarTests            — the `## Items` table of the interface, line by line
- SchemaTests             — required keys, enums, dates, lists, sections
- FixtureVaultTests       — the compatibility vault through lib_research, lib_vault, reindex, search
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BIN_DIR = TESTS_DIR.parent / "bin"
FIXTURE = TESTS_DIR / "fixtures" / "research" / "vault"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lr = _load("lib_research", "lib_research.py")
lv = _load("lib_vault", "lib_vault.py")

A, B, C, C2, D = "2026-01-10_aaaaaaaa", "2026-02-11_bbbbbbbb", "2026-03-12_cccccccc", "2026-03-12_cccccccc-2", "2026-04-13_dddddddd"
HEAD = "---\ntype: collection\nstatus: open\nquestion: \"q\"\ncurated_by: owner\ncreated: 2026-10-01\n---\n"


def doc(items: str, head: str = HEAD, extra: str = "") -> dict:
    return lr.read_collection_text(head + "\n## Items\n" + items + extra, "t", existing={A, B, C, C2, D})


# ---------------------------------------------------------------------------
# The YAML subset
# ---------------------------------------------------------------------------

class FrontmatterSubsetTests(unittest.TestCase):
    def test_scalars(self):
        data, err = lr.parse_flat_frontmatter('a: plain value # comment\nb: "quoted \\"x\\" \\\\"\nc: \'single\'\nd:\n')
        self.assertEqual(err, [])
        self.assertEqual(data, {"a": "plain value", "b": 'quoted "x" \\', "c": "single", "d": ""})

    def test_inline_and_block_lists(self):
        data, err = lr.parse_flat_frontmatter('x: [a, "b, c", d]\ny:\n  - one\n  - "two"\nz: []\nw:\n- col0\n')
        self.assertEqual(err, [])
        self.assertEqual(data, {"x": ["a", "b, c", "d"], "y": ["one", "two"], "z": [], "w": ["col0"]})

    def test_duplicate_key_is_an_error_first_value_kept(self):
        data, err = lr.parse_flat_frontmatter("status: open\nstatus: closed\n")
        self.assertEqual(data["status"], "open")
        self.assertEqual(err, ["duplicate key `status`"])

    def test_nested_mapping_is_an_error(self):
        _, err = lr.parse_flat_frontmatter("capture:\n  text_sha256: abc\n")
        self.assertEqual(err, ["nested mapping under `capture` (flat scalars and lists only)"])

    def test_comments_and_blank_lines_are_skipped(self):
        data, err = lr.parse_flat_frontmatter("# a comment\n\na: 1\n")
        self.assertEqual((data, err), ({"a": "1"}, []))

    def test_comments_after_lists_and_inside_block_lists(self):
        data, err = lr.parse_flat_frontmatter("domains: [learning, mc/ai] # context\nsuppressed_pages: []    # names\n"
                                              "tags:\n  # the first tag\n  - a\n  - b # trailing\nwiki: on # on | off\n")
        self.assertEqual(err, [])
        self.assertEqual(data, {"domains": ["learning", "mc/ai"], "suppressed_pages": [], "tags": ["a", "b"], "wiki": "on"})
        # a bracket inside a quoted scalar is not a list
        data, _ = lr.parse_flat_frontmatter('question: "[draft] why?"\n')
        self.assertEqual(data["question"], "[draft] why?")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

class LinkTests(unittest.TestCase):
    def test_accepted_forms(self):
        for raw, file, frag, disp in (
            ("Sources/%s/extract" % A, "extract", None, None),
            ("Sources/%s/extract.md" % A, "extract", None, None),
            ("Sources/%s/source" % A, "source", None, None),
            ("Sources/%s/source.md" % A, "source", None, None),
            ("Sources/%s" % A, None, None, None),
            ("Sources/%s/extract#Summary|Shown" % A, "extract", "Summary", "Shown"),
            ("Sources/%s/extract|Shown" % C2, "extract", None, "Shown"),
        ):
            link, reason = lr.classify_link(raw)
            self.assertIsNone(reason, raw)
            self.assertEqual((link["file"], link["fragment"], link["display"]), (file, frag, disp), raw)

    def test_rejected_forms(self):
        for raw, reason in (
            ("Synthesis/Note", "link outside Sources/"),
            ("Other/Sources/%s/extract" % A, "link outside Sources/"),
            ("Sources/../x/extract", "path traversal"),
            ("Sources/./x/extract", "path traversal"),
            ("/Sources/%s/extract" % A, "absolute path"),
            ("Sources/%s/notes.md" % A, "unknown file"),
            ("Sources/not-a-rev/extract", "revision name malformed"),
            ("Sources/", "names no revision"),
            ("", "empty"),
            ("Sources\\%s\\extract" % A, "backslash"),
        ):
            link, why = lr.classify_link(raw)
            self.assertIsNone(link, raw)
            self.assertIn(reason, why, raw)


class ItemTailTests(unittest.TestCase):
    def test_role_and_note(self):
        self.assertEqual(lr.parse_item_tail(" — **Answer (A)** — the note"), ("answer (a)", "the note"))
        self.assertEqual(lr.parse_item_tail(" - **excluded** - why"), ("excluded", "why"))
        self.assertEqual(lr.parse_item_tail(" – **role only**"), ("role only", ""))

    def test_no_role_is_a_note(self):
        self.assertEqual(lr.parse_item_tail(" (★8) — Gamma, no role here."), (None, "(★8) — Gamma, no role here."))
        self.assertEqual(lr.parse_item_tail(""), (None, ""))

    def test_trailing_punctuation_after_the_link(self):
        self.assertEqual(lr.parse_item_tail(". — **r** — n"), ("r", "n"))
        self.assertEqual(lr.parse_item_tail(";"), (None, ""))


# ---------------------------------------------------------------------------
# The `## Items` grammar, line by line
# ---------------------------------------------------------------------------

class GrammarTests(unittest.TestCase):
    def test_item_with_role_display_fragment_and_md(self):
        rec = doc("- [[Sources/%s/extract.md#Summary|Beta]] — **answer (b)** — note\n" % B)
        self.assertEqual(rec["state"], "ok")
        it = rec["items"][0]
        self.assertEqual((it["rev"], it["file"], it["fragment"], it["display"], it["role"], it["note"], it["excluded"]),
                         (B, "extract", "Summary", "Beta", "answer (b)", "note", False))

    def test_star_marker_and_bare_revision(self):
        rec = doc("* [[Sources/%s]] trailing text\n" % A)
        self.assertEqual(rec["state"], "ok")
        self.assertEqual((rec["items"][0]["file"], rec["items"][0]["role"], rec["items"][0]["note"]), (None, None, "trailing text"))

    def test_item_without_role(self):
        rec = doc("- [[Sources/%s/extract]] (★8) — just a note\n" % A)
        self.assertEqual(rec["state"], "ok")
        self.assertEqual((rec["items"][0]["role"], rec["items"][0]["note"]), (None, "(★8) — just a note"))

    def test_excluded(self):
        rec = doc("- [[Sources/%s/extract]] — **excluded** — considered\n" % C)
        self.assertTrue(rec["items"][0]["excluded"])
        self.assertEqual(rec["counts"]["excluded"], 1)

    def test_nested_continuation(self):
        rec = doc("- [[Sources/%s/extract]] — **r** — first\n  - second\n\tthird\n" % A)
        self.assertEqual(rec["state"], "ok")
        self.assertEqual(rec["items"][0]["note"], "first\nsecond\nthird")

    def test_two_links_fail(self):
        rec = doc("- [[Sources/%s/extract]] and [[Sources/%s/extract]]\n- [[Sources/%s/extract]]\n" % (A, B, C))
        self.assertEqual(rec["state"], "fail")
        self.assertEqual([m["reason"] for m in rec["malformed"]], ["two or more links on one line"])
        self.assertEqual([i["rev"] for i in rec["items"]], [C], "the other items still count")

    def test_no_link_fails(self):
        rec = doc("- just text\n")
        self.assertEqual(rec["malformed"][0]["reason"], "list line without a Sources/ link")

    def test_other_folder_traversal_absolute_other_instance_fail(self):
        rec = doc("- [[Synthesis/X]]\n- [[Sources/../x/extract]]\n- [[/Sources/%s/extract]]\n- [[Other/Sources/%s/extract]]\n" % (A, A))
        self.assertEqual([m["reason"].split(" (")[0].split(":")[0] for m in rec["malformed"]],
                         ["link outside Sources/", "path traversal in link target", "absolute path in link target", "link outside Sources/"])
        self.assertEqual(rec["items"], [])

    def test_duplicate_revision_fails_the_second(self):
        rec = doc("- [[Sources/%s/extract]]\n- [[Sources/%s/source]]\n" % (A, A))
        self.assertEqual(len(rec["items"]), 1)
        self.assertIn("duplicate revision %s (first at line" % A, rec["malformed"][0]["reason"])

    def test_free_text_fails_and_fences_are_skipped(self):
        rec = doc("- [[Sources/%s/extract]]\nfree text\n```\n- [[Sources/%s/extract]]\n```\n- [[Sources/%s/extract]]\n" % (A, B, C))
        self.assertEqual([m["reason"] for m in rec["malformed"]], ["free text between items (no list marker)"])
        self.assertEqual([i["rev"] for i in rec["items"]], [A, C])

    def test_duplicate_items_heading_fails(self):
        rec = doc("- [[Sources/%s/extract]]\n" % A, extra="\n## Items\n- [[Sources/%s/extract]]\n" % B)
        self.assertEqual(rec["state"], "fail")
        self.assertTrue(any(e.startswith("duplicate `## Items` heading") for e in rec["errors"]))
        self.assertEqual(len(rec["items"]), 1, "the first section is read")

    def test_blank_lines_and_other_sections_are_fine(self):
        rec = doc("\n- [[Sources/%s/extract]]\n\n" % A, extra="\n## Open questions\n- q [[Synthesis/X]]\n\n## Notes (owner)\ntext\n")
        self.assertEqual(rec["state"], "ok")
        self.assertEqual(rec["sections"], ["Items", "Open questions", "Notes (owner)"])
        self.assertEqual(rec["counts"]["links_outside_items"], 1)

    def test_continuation_before_any_item_fails(self):
        rec = doc("  - nested first\n- [[Sources/%s/extract]]\n" % A)
        self.assertEqual(rec["malformed"][0]["reason"], "list line without a Sources/ link")

    def test_existing_marks_resolution(self):
        rec = doc("- [[Sources/%s/extract]]\n- [[Sources/2026-05-01_eeeeeeee/extract]]\n" % A)
        self.assertEqual([i["exists"] for i in rec["items"]], [True, False])
        self.assertEqual(rec["counts"]["unresolved"], 1)
        rec2 = lr.read_collection_text(HEAD + "## Items\n- [[Sources/%s/extract]]\n" % A, "t")
        self.assertIsNone(rec2["items"][0]["exists"])
        self.assertIsNone(rec2["counts"]["unresolved"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    def test_minimal_inbox(self):
        rec = lr.read_collection_text("---\ntype: collection\nstatus: inbox\nquestion: \"proposed\"\ncurated_by: scanner:x\ncreated: 2026-10-20\n---\n## Items\n", "t")
        self.assertEqual(rec["state"], "ok")
        self.assertEqual((rec["status"], rec["wiki"], rec["suppressed_pages"], rec["recusal"], rec["updated"]), ("inbox", "on", [], None, None))

    def test_missing_required(self):
        rec = lr.read_collection_text("---\ntype: collection\n---\n## Items\n", "t")
        self.assertEqual(rec["errors"], ["missing required `status`", "missing required `question`",
                                         "missing required `curated_by`", "missing required `created`"])

    def test_enums_dates_lists(self):
        rec = lr.read_collection_text("---\ntype: note\nstatus: draft\nquestion: q\ncurated_by: o\ncreated: 2026-13-45\nupdated: soon\nwiki: yes\nrecusal: off\ndomains: x\n---\n## Items\n", "t")
        self.assertEqual(rec["errors"], ["`type` must be `collection` (got 'note')",
                                         "`status` must be one of open|inbox|closed (got 'draft')",
                                         "`created` is not an ISO date (YYYY-MM-DD)",
                                         "`updated` is not an ISO date (YYYY-MM-DD)",
                                         "`wiki` must be on|off (got 'yes')",
                                         "`domains` must be a list"])
        self.assertEqual(rec["recusal"], "off")

    def test_no_frontmatter_and_no_items(self):
        rec = lr.read_collection_text("# just a note\n", "t")
        self.assertEqual(rec["state"], "fail")
        self.assertIn("no frontmatter (an opening `---` line closed by `---`)", rec["errors"])
        self.assertIn("no `## Items` section", rec["errors"])

    def test_unknown_keys_are_kept_as_extra(self):
        rec = lr.read_collection_text("---\ntype: collection\nstatus: open\nquestion: q\ncurated_by: o\ncreated: 2026-10-01\n"
                                      "visibility: personal\nsources: []\n---\n## Items\n", "t")
        self.assertEqual(rec["extra"], {"visibility": "personal", "sources": []})

    def test_unreadable_record(self):
        rec = lr.unreadable_record("gone", "unreadable: x")
        self.assertEqual((rec["state"], rec["errors"], rec["counts"]["items"]), ("unreadable", ["unreadable: x"], 0))


# ---------------------------------------------------------------------------
# The compatibility vault end to end
# ---------------------------------------------------------------------------

class FixtureVaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = {r["name"]: r for r in lr.scan_collections(str(FIXTURE), existing=lr.existing_revisions(str(FIXTURE)))}
        cls.scan = lv.scan_vault(str(FIXTURE), state_dir=tempfile.mkdtemp(), archive_dir=None)

    def test_the_seven_documents(self):
        states = {n: r["state"] for n, r in self.records.items()}
        self.assertEqual(states, {"Trust without identity": "ok", "Inbox minimal": "ok", "Closed dossier": "ok",
                                  "Malformed items": "fail", "Duplicate heading": "fail", "Bad frontmatter": "fail",
                                  "No items section": "fail"})

    def test_the_valid_dossier(self):
        r = self.records["Trust without identity"]
        self.assertEqual([(i["rev"], i["role"], i["excluded"]) for i in r["items"]],
                         [(A, "answer (a): price of action", False), (B, "answer (b): quotas and karma", False),
                          (C, "excluded", True), (C2, None, False)])
        self.assertEqual(r["items"][1]["note"], "a ladder of rights\nthe same channel, read from the operator's side\na second continuation line")
        self.assertEqual((r["migrated_from"], r["aliases"], r["domains"]), ("Synthesis/Trust without identity", ["Trust without identity"], ["learning", "mc/ai"]))
        self.assertEqual(r["sections"], ["Why", "Items", "Open questions", "Notes (owner)"])

    def test_counters(self):
        c = self.scan["counters"]
        self.assertEqual((c["collections"], c["collections_fail"], c["collections_status"]),
                         (7, 4, {"open": 5, "inbox": 1, "closed": 1, "unknown": 0}))
        self.assertEqual((c["collection_items"], c["collection_items_excluded"], c["collection_items_malformed"], c["collection_items_unresolved"]),
                         (12, 1, 11, 1))
        # filed: B (the seed) · C, C2 (the open dossier; A is integrated by the stable note and not counted) · D (inbox)
        self.assertEqual((c["integrated"], c["filed"]), (1, 4))

    def test_filed_ignores_malformed_lines_and_counts_excluded(self):
        c = lv.counters(self.scan)
        self.assertEqual(c["filed"], 4)
        # drop the collections: filed falls back to the seed's link only
        scan2 = dict(self.scan)
        scan2["collections"] = {"present": False, "notes": []}
        self.assertEqual(lv.counters(scan2)["filed"], 1)

    def test_reindex_section(self):
        tmp = tempfile.mkdtemp()
        try:
            copy = Path(tmp) / "v"
            shutil.copytree(FIXTURE, copy)
            p = subprocess.run([sys.executable, str(BIN_DIR / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Collections=7", p.stdout)
            text = (copy / "index.md").read_text(encoding="utf-8")
            self.assertIn("| Collections | 7 |", text)
            self.assertIn("- [[Collections/Trust without identity|Trust without identity]] (open, 4 items, 1 excluded) — How can an agent be trusted when it cannot be identified?", text)
            self.assertIn("(open, 2 items, 11 malformed)", text)
            self.assertIn("(fail: no `## Items` section)", text)
            self.assertLess(text.index("## Recent Sources"), text.index("## Collections"))
            self.assertLess(text.index("## Collections"), text.index("## Synthesis Notes"))
            # a vault without Collections/: no section, the Status row says 0
            shutil.rmtree(copy / "Collections")
            subprocess.run([sys.executable, str(BIN_DIR / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report"], check=True, capture_output=True)
            text = (copy / "index.md").read_text(encoding="utf-8")
            self.assertIn("| Collections | 0 |", text)
            self.assertNotIn("## Collections", text)
        finally:
            shutil.rmtree(tmp)

    def _search(self, *args) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("MNEMON_CONFIG", "MNEMON_INSTANCE", "MNEMON_CONFIG_DIR")}
        p = subprocess.run([sys.executable, str(BIN_DIR / "mnemon-search.py"), "--vault", str(FIXTURE), *args],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_search_schema_and_types(self):
        out = self._search("--query", "trust", "--include-source", "--include-synthesis", "--include-collections", "--limit", "20")
        self.assertEqual(out["schema"], "mnemon-search/2")
        self.assertEqual(sorted(set(r["result_type"] for r in out["results"])), ["collection", "extract", "source", "synthesis"])
        alpha = [r for r in out["results"] if r["path"] == "Sources/%s/" % A][0]
        self.assertEqual((alpha["result_type"], alpha["tier"], alpha["status"]), ("extract", "full", "extracted"))
        bookmark = [r for r in out["results"] if r["path"] == "Sources/%s/" % B][0]
        self.assertEqual((bookmark["result_type"], bookmark["tier"]), ("extract", "bookmark"))
        for r in out["results"]:
            if r["result_type"] in ("synthesis", "collection"):
                self.assertIsNone(r["tier"], r)
        self.assertEqual({r["status"] for r in out["results"] if r["result_type"] == "collection"}, {"open", "inbox", "closed"})
        # three collections in one folder — deduplication is by file
        self.assertEqual(len([r for r in out["results"] if r["result_type"] == "collection"]), 3)
        # the captured-only revision: a source result with tier full and no status
        d = [r for r in out["results"] if r["path"] == "Sources/%s/" % D][0]
        self.assertEqual((d["result_type"], d["tier"], d["status"]), ("source", "full", None))

    def test_collections_are_opt_in_and_filtered_by_domain(self):
        self.assertEqual({r["result_type"] for r in self._search("--query", "trust")["results"]}, {"extract"})
        out = self._search("--query", "trust", "--include-collections", "--domain", "mc/ai")
        self.assertEqual(sorted((r["result_type"], r["path"] + r["file"]) for r in out["results"]),
                         [("collection", "Collections/Trust without identity.md"), ("extract", "Sources/%s/extract.md" % A)])

    def test_search_reads_collections_through_the_shared_reader(self):
        # a `...`-closed frontmatter and a duplicate key: the search result agrees with the reader
        tmp = tempfile.mkdtemp()
        try:
            v = Path(tmp) / "v"
            (v / "Sources").mkdir(parents=True)
            (v / "Collections").mkdir()
            (v / "Collections" / "Dots.md").write_text("---\ntype: collection\nstatus: inbox\nstatus: open\nquestion: \"trust?\"\n"
                                                     "curated_by: o\ncreated: 2026-10-01\ndomains: [mc/ai] # c\n...\n## Items\n", encoding="utf-8")
            env = {k: val for k, val in os.environ.items() if k not in ("MNEMON_CONFIG", "MNEMON_INSTANCE", "MNEMON_CONFIG_DIR")}
            p = subprocess.run([sys.executable, str(BIN_DIR / "mnemon-search.py"), "--vault", str(v), "--query", "trust",
                                "--include-collections", "--domain", "mc/ai"], capture_output=True, text=True, env=env)
            out = json.loads(p.stdout)
            self.assertEqual(out["count"], 1, p.stderr)
            self.assertEqual((out["results"][0]["status"], out["results"][0]["domains"], out["results"][0]["created"]),
                             ("inbox", ["mc/ai"], "2026-10-01"), "the first value of a duplicate key, as the reader keeps it")
        finally:
            shutil.rmtree(tmp)

    def test_unlistable_collections_folder(self):
        if os.geteuid() == 0:
            self.skipTest("root lists everything")
        tmp = tempfile.mkdtemp()
        v = Path(tmp) / "v"
        try:
            (v / "Sources").mkdir(parents=True)
            (v / "Collections").mkdir()
            os.chmod(v / "Collections", 0)
            with self.assertRaises(OSError):
                lr.list_collections(str(v))
            scan = lv.scan_vault(str(v), state_dir=tmp)
            self.assertFalse(scan["snapshot"]["complete"])
            self.assertIn("collections_unlisted", scan["snapshot"])
            self.assertEqual(scan["counters"]["collections"], 0)
        finally:
            os.chmod(v / "Collections", 0o755)
            shutil.rmtree(tmp)

    def test_reader_cli(self):
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--vault", str(FIXTURE)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("collections: 7 · fail 4 · unreadable 0", p.stdout)
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--file", str(FIXTURE / "Collections" / "Closed dossier.md")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--vault", str(FIXTURE), "--json"], capture_output=True, text=True)
        d = json.loads(p.stdout)
        self.assertEqual((d["schema"], len(d["collections"])), ("research-interface/1", 7))


if __name__ == "__main__":
    unittest.main(verbosity=2)
