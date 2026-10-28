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

    def test_quoted_scalar_followed_by_a_comment(self):
        data, err = lr.parse_flat_frontmatter('q: "x # y" # c\nr: \'it\'\'s\' # c\ns: "unterminated # not a comment\n')
        self.assertEqual(err, [])
        self.assertEqual(data, {"q": "x # y", "r": "it's", "s": '"unterminated'})

    def test_nested_lists_are_errors(self):
        _, err = lr.parse_flat_frontmatter("tags:\n  - a\n    - b\n")
        self.assertEqual(err, ["nested list under `tags` (flat scalars and lists only)"])
        _, err = lr.parse_flat_frontmatter("tags: [a, [b, c]]\n")
        self.assertEqual(err, ["nested list under `tags` (flat scalars and lists only)"])
        data, err = lr.parse_flat_frontmatter('tags: [a, "[b]"]\n')
        self.assertEqual((data, err), ({"tags": ["a", "[b]"]}, []))


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

    def test_decorated_roles(self):
        self.assertEqual(lr.parse_item_tail(" — ***excluded*** — n"), ("excluded", "n"))
        self.assertEqual(lr.parse_item_tail(" — **excluded:** — n"), ("excluded", "n"))
        self.assertEqual(lr.parse_item_tail(" — **Answer (B).**"), ("answer (b)", ""))

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

    def test_nested_line_with_a_link(self):
        rec = doc("- [[Sources/%s/extract]] — **r** — first\n  - [[Sources/%s/extract]] — nested item\n  - see [[Synthesis/X]]\n" % (A, B))
        self.assertEqual([i["rev"] for i in rec["items"]], [A, B], "a nested line with a Sources link is an item of its own")
        self.assertEqual([m["reason"] for m in rec["malformed"]], ["link outside Sources/ (another folder or another instance)"])

    def test_heading_rule(self):
        base = HEAD + "\n%s\n- [[Sources/%s/extract]]\n"
        for heading, ok in (("## Items", True), ("   ## Items", True), ("## Items ##", True), ("## Items   ", True),
                            ("## Items#", False), ("## items", False), ("    ## Items", False), ("##Items", False), ("### Items", False)):
            rec = lr.read_collection_text(base % (heading, A), "t")
            self.assertEqual(rec["state"] == "ok" and rec["counts"]["items"] == 1, ok, heading)

    def test_fences_of_any_length_and_tildes(self):
        rec = doc("- [[Sources/%s/extract]]\n````\n- [[Sources/%s/extract]]\n```\nstill fenced\n````\n~~~\n- [[Sources/%s/extract]]\n~~~\n- [[Sources/%s/extract]]\n" % (A, B, C, D))
        self.assertEqual(rec["state"], "ok")
        self.assertEqual([i["rev"] for i in rec["items"]], [A, D])

    def test_bom_crlf_empty_unclosed(self):
        bom = "\ufeff" + HEAD + "## Items\n- [[Sources/%s/extract]]\n" % A
        self.assertEqual(lr.read_collection_text(bom, "t")["state"], "ok", "a BOM is ignored")
        crlf = (HEAD + "## Items\n- [[Sources/%s/extract]] — **r** — n\n" % A).replace("\n", "\r\n")
        rec = lr.read_collection_text(crlf, "t")
        self.assertEqual((rec["state"], rec["items"][0]["role"], rec["items"][0]["note"]), ("ok", "r", "n"))
        rec = lr.read_collection_text("", "t")
        self.assertEqual(rec["state"], "fail")
        self.assertEqual(rec["errors"][0], "no frontmatter (an opening `---` line closed by `---`)")
        rec = lr.read_collection_text("---\ntype: collection\nstatus: open\n## Items\n", "t")
        self.assertEqual(rec["errors"][0], "no frontmatter (an opening `---` line closed by `---`)")

    def test_targets_resolve_per_file(self):
        existing = {A: {"extract", "source"}, D: {"source"}, "2026-06-01_abababab": set(), "2026-06-02_acacacac": {"source"}}
        rec = lr.read_collection_text(HEAD + "## Items\n- [[Sources/%s/extract]]\n- [[Sources/%s/extract]]\n- [[Sources/2026-06-02_acacacac/source]]\n- [[Sources/2026-06-01_abababab]]\n- [[Sources/2026-06-03_adadadad/source]]\n" % (A, D), "t", existing=existing)
        self.assertEqual([i["exists"] for i in rec["items"]], [True, False, True, True, False])
        self.assertEqual(rec["counts"]["unresolved"], 2)
        rec = lr.read_collection_text(HEAD + "## Items\n- [[Sources/%s/source]]\n- [[Sources/%s]]\n" % (D, A), "t", existing=existing)
        self.assertEqual([i["exists"] for i in rec["items"]], [True, True], "the bare form needs only the folder; /source needs the file")

    def test_suppressed_pages_entries(self):
        rec = lr.read_collection_text("---\ntype: collection\nstatus: open\nquestion: q\ncurated_by: o\ncreated: 2026-10-01\n"
                                      "suppressed_pages: [\"Good page\", \"Bad.md\", \"Wiki/Bad\", \"[[Bad]]\", \"\"]\n---\n## Items\n", "t")
        self.assertEqual(len([e for e in rec["errors"] if e.startswith("`suppressed_pages` entry")]), 4)
        self.assertEqual(rec["suppressed_pages"], ["Good page", "Bad.md", "Wiki/Bad", "[[Bad]]", ""])

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
        self.assertEqual(states, {"Trust without identity": "ok", "Annotated": "ok", "Inbox minimal": "ok", "Closed dossier": "ok",
                                  "Malformed items": "fail", "Duplicate heading": "fail", "Bad frontmatter": "fail",
                                  "No items section": "fail", "Vetoed": "ok", "Veto by name": "ok"})
        ann = self.records["Annotated"]
        self.assertEqual((ann["status"], ann["curated_by"], ann["domains"], ann["tags"], ann["question"]),
                         ("open", "owner", ["learning"], ["annotated", "trust"], "Is trust annotated?"))
        self.assertEqual((ann["items"][0]["role"], ann["items"][0]["note"]), ("bold role", "the stars and the colon are not part of the role"))
        closed = self.records["Closed dossier"]
        self.assertEqual([i["exists"] for i in closed["items"]], [True, True, False], "an item to a captured-only revision's extract is unresolved")

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
                         (10, 4, {"open": 8, "inbox": 1, "closed": 1, "unknown": 0}))
        self.assertEqual((c["collection_items"], c["collection_items_excluded"], c["collection_items_malformed"], c["collection_items_unresolved"]),
                         (16, 1, 11, 2))
        self.assertEqual((c["wiki_pages"], c["wiki_fail"], c["wiki_unreadable"], c["wiki_based_on"]), (10, 1, 0, 12))
        self.assertEqual((c["folders"], c["captured"], c["extracted"]), (6, 2, 4))
        # filed: B (the seed) · C, C2 (the open dossier; A is integrated by the stable note and not counted) · D (inbox)
        self.assertEqual((c["integrated"], c["filed"]), (1, 4))

    def test_filed_ignores_malformed_lines_and_counts_excluded(self):
        c = lv.counters(self.scan)
        self.assertEqual(c["filed"], 4)
        # drop the collections: filed keeps the seed's link and the wiki pages' based_on (B, C, C2 — A is integrated)
        scan2 = dict(self.scan)
        scan2["collections"] = {"present": False, "notes": []}
        self.assertEqual(lv.counters(scan2)["filed"], 3)
        # drop the wiki too: the seed's link only
        scan3 = dict(scan2)
        scan3["wiki"] = {"present": False, "notes": []}
        self.assertEqual(lv.counters(scan3)["filed"], 1)
        # the fixture pages rest only on revisions the collections already file: doctor-data.json stays byte for byte
        self.assertEqual(c["filed"], lv.counters(dict(self.scan, wiki={"present": False, "notes": []}))["filed"])

    def test_reindex_section(self):
        tmp = tempfile.mkdtemp()
        try:
            copy = Path(tmp) / "v"
            shutil.copytree(FIXTURE, copy)
            p = subprocess.run([sys.executable, str(BIN_DIR / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Collections=10, Wiki=10", p.stdout)
            text = (copy / "index.md").read_text(encoding="utf-8")
            self.assertIn("| Collections | 10 |", text)
            self.assertIn("| Wiki | 10 |", text)
            # an unreadable document is one row saying so; a `|` in a question is displayed, not a table break
            (copy / "Collections" / "Piped.md").write_text("---\ntype: collection\nstatus: open\nquestion: \"a | b?\"\ncurated_by: o\ncreated: 2026-10-01\n---\n## Items\n", encoding="utf-8")
            os.chmod(copy / "Collections" / "Closed dossier.md", 0)
            subprocess.run([sys.executable, str(BIN_DIR / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report"], check=True, capture_output=True)
            text = (copy / "index.md").read_text(encoding="utf-8")
            os.chmod(copy / "Collections" / "Closed dossier.md", 0o644)
            self.assertIn("- [[Collections/Closed dossier|Closed dossier]] (unreadable)", text)
            self.assertIn("(open, 0 items) — a / b?", text)
            self.assertIn("| Collections | 11 |", text)
            self.assertIn("- [[Collections/Trust without identity|Trust without identity]] (open, 4 items, 1 excluded) — How can an agent be trusted when it cannot be identified?", text)
            self.assertIn("(open, 2 items, 11 malformed)", text)
            self.assertIn("(fail: no `## Items` section)", text)
            self.assertLess(text.index("## Recent Sources"), text.index("## Collections"))
            self.assertLess(text.index("## Collections"), text.index("## Wiki"))
            self.assertLess(text.index("## Wiki"), text.index("## Synthesis Notes"))
            self.assertIn("- [[Wiki/Stale page|Stale page]] (concept, 1 sources, 1 collections, updated 2026-10-20)", text)
            # an unreadable page is one row saying so
            os.chmod(copy / "Wiki" / "Stale page.md", 0)
            subprocess.run([sys.executable, str(BIN_DIR / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report"], check=True, capture_output=True)
            text = (copy / "index.md").read_text(encoding="utf-8")
            os.chmod(copy / "Wiki" / "Stale page.md", 0o644)
            self.assertIn("- [[Wiki/Stale page|Stale page]] (unreadable)", text)
            # a vault without Collections/ or Wiki/: no sections, the Status rows say 0
            shutil.rmtree(copy / "Collections")
            shutil.rmtree(copy / "Wiki")
            subprocess.run([sys.executable, str(BIN_DIR / "knowledge-reindex.py"), str(copy), "--no-hand-edit-report"], check=True, capture_output=True)
            text = (copy / "index.md").read_text(encoding="utf-8")
            self.assertIn("| Collections | 0 |", text)
            self.assertIn("| Wiki | 0 |", text)
            self.assertNotIn("## Collections", text)
            self.assertNotIn("## Wiki", text)
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
        # four collections in one folder — deduplication is by file
        self.assertEqual(len([r for r in out["results"] if r["result_type"] == "collection"]), 4)
        # the captured-only revision: a source result with tier full and no status
        d = [r for r in out["results"] if r["path"] == "Sources/%s/" % D][0]
        self.assertEqual((d["result_type"], d["tier"], d["status"]), ("source", "full", None))
        # a bookmark revision without an extract: found with --include-source only, a source result
        e = [r for r in out["results"] if r["path"] == "Sources/2026-05-14_ffffffff/"]
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["result_type"], e[0]["tier"], e[0]["status"], e[0]["title"], e[0]["rating"]), ("source", "bookmark", None, "Epsilon bookmark", None))
        self.assertEqual([r["path"] for r in self._search("--query", "trust")["results"] if "ffffffff" in r["path"]], [])

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
        self.assertIn("collections: 10 · fail 4 · unreadable 0", p.stdout)
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--file", str(FIXTURE / "Collections" / "Closed dossier.md")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--vault", str(FIXTURE), "--json"], capture_output=True, text=True)
        d = json.loads(p.stdout)
        self.assertEqual((d["schema"], len(d["collections"]), len(d["wiki"])), ("research-interface/1", 10, 10))
        self.assertNotIn("_body_lines", d["wiki"][0], "the working copy of the body lines is not part of the record")
        self.assertEqual({p["name"]: p["severity"] for p in d["wiki"] if p["severity"] == "fail"},
                         {n: "fail" for n in ("Bad schema", "Suppressed by name", "Unbound binding", "Unbound unlisted", "Vetoed page")})
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--file", str(FIXTURE / "Wiki" / "Stale page.md")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("ok Stale page — concept · 1 based_on · 1 collections · 1 locators", p.stdout)
        self.assertNotIn("stale", p.stdout.split("\n")[0].replace("Stale page", ""), "--file reads the page; no vault, no states")
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_research.py"), "--file", str(FIXTURE / "Wiki" / "Bad schema.md")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        p = subprocess.run([sys.executable, str(BIN_DIR / "lib_vault.py"), "--vault", str(FIXTURE), "--status-counts"], capture_output=True, text=True)
        self.assertIn("collections=10\n", p.stdout)
        self.assertIn("collections_fail=4\n", p.stdout)
        self.assertIn("wiki=10\n", p.stdout)
        self.assertIn("wiki_fail=1\n", p.stdout)

    def test_fixture_wiki_states(self):
        """Every derived state of the interface on the fixture pages (§9), through the one context."""
        ctx = lv.research_context(self.scan)
        pages = {p["name"]: lr.validate_wiki(p, ctx) for p in self.scan["wiki"]["notes"]}
        self.assertEqual({n: (p["severity"], p["states"]) for n, p in pages.items()}, {
            "Trust — four answers": ("ok", []),
            "Stale page": ("warn", ["stale"]),
            "Inputs changed": ("warn", ["inputs-changed"]),
            "Edited outside": ("warn", ["edited-outside-update"]),
            "Orphan page": ("warn", ["orphan"]),
            "Vetoed page": ("fail", ["suppressed"]),
            "Suppressed by name": ("fail", ["suppressed"]),
            "Unbound unlisted": ("fail", ["unbound"]),
            "Unbound binding": ("fail", ["unbound", "inputs-changed"]),
            "Bad schema": ("fail", ["schema"]),
        })
        four = pages["Trust — four answers"]
        self.assertEqual(four["cited"], [A, B], "a verified heading locator and a verified quote locator cite their revisions")
        self.assertEqual([(l["form"], l["verified"]) for l in four["locators"]], [("heading", True), ("quote", True)])
        self.assertEqual(four["counts"]["links_consumable"], 2, "links in ## Log are counted apart and give no state")
        self.assertEqual(four["body"]["cut"], "Atlas reading")
        self.assertTrue(all(p["usable"] is False for n, p in pages.items() if p["severity"] == "fail"))
        self.assertEqual(pages["Unbound unlisted"]["reasons"]["unbound"][2][:18], "line 28: unlisted ")
        self.assertEqual([l["reason"] for l in pages["Unbound binding"]["locators"]],
                         ["heading `Missing heading` not found in %s/source.md" % B, "quote not found in %s/source.md" % B])
        self.assertEqual(pages["Unbound binding"]["reasons"]["unresolved"], ["line 32: [[Some note]] (not a link into Sources/, Collections/, Wiki/ or Synthesis/)"])
        self.assertEqual(pages["Orphan page"]["reasons"]["orphan"], ["`Closed dossier` is closed", "`Inbox minimal` is inbox", "`Missing dossier` does not exist"])

    def test_search_wiki_block(self):
        out = self._search("--query", "trust")
        self.assertIsNone(out["wiki"], "no wiki block without the flag")
        out = self._search("--query", "trust", "--include-wiki")
        self.assertEqual([r["file"] for r in out["wiki"]["results"]],
                         ["Edited outside.md", "Trust — four answers.md", "Inputs changed.md", "Stale page.md"])
        self.assertEqual(out["wiki"]["limit"], 5)
        r = out["wiki"]["results"][1]
        self.assertEqual((r["result_type"], r["tier"], r["status"], r["path"], r["kind"], r["domains"], r["rating"]),
                         ("wiki", None, None, "Wiki/", "contradiction", ["learning", "mc/ai"], None))
        self.assertFalse(any("Mentions trust here too" in r["snippet"] for r in out["wiki"]["results"]), "## Atlas reading is cut")
        out = self._search("--query", "trust", "--include-wiki", "--include-orphans", "--wiki-limit", "2")
        self.assertEqual([r["file"] for r in out["wiki"]["results"]], ["Orphan page.md", "Edited outside.md"])
        out = self._search("--query", "quotas", "--include-wiki", "--include-orphans", "--domain", "mc/ai")
        self.assertEqual([r["file"] for r in out["wiki"]["results"]], ["Trust — four answers.md"], "pages inherit their collections' domains")
        out = self._search("--query", "nothing-matches-this-token-anywhere", "--include-wiki")
        self.assertEqual((out["count"], out["wiki"]["count"]), (0, 0))

    def test_search_refuses_an_unlistable_wiki_folder(self):
        tmp = tempfile.mkdtemp()
        try:
            copy = Path(tmp) / "v"
            shutil.copytree(FIXTURE, copy)
            os.chmod(copy / "Wiki", 0)
            try:
                env = {k: v for k, v in os.environ.items() if k not in ("MNEMON_CONFIG", "MNEMON_INSTANCE", "MNEMON_CONFIG_DIR")}
                p = subprocess.run([sys.executable, str(BIN_DIR / "mnemon-search.py"), "--vault", str(copy), "--query", "trust", "--include-wiki"],
                                   capture_output=True, text=True, env=env)
                self.assertEqual(p.returncode, 2)
                self.assertIn("Wiki/ could not be listed", p.stderr)
                p = subprocess.run([sys.executable, str(BIN_DIR / "mnemon-search.py"), "--vault", str(copy), "--query", "trust"],
                                   capture_output=True, text=True, env=env)
                self.assertEqual(p.returncode, 0, "without the flag the folder is never touched")
            finally:
                os.chmod(copy / "Wiki", 0o755)
        finally:
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# wiki/1 — the nested subset, the reader, the validator (interface §9)
# ---------------------------------------------------------------------------

import hashlib

REV_M = "2026-06-01_11111111"       # a managed revision
REV_L = "2026-06-02_22222222"       # a legacy revision
REV_N = "2026-06-03_33333333"       # a newer revision superseding REV_L
SHA0 = "0" * 64


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def managed_source(body: str, completeness: str = "complete", source_type: str = "article", capture_sha: str | None = None) -> str:
    return ("---\ntype: source\nsource_type: %s\norigin: url\nurl: \"https://example.org/m\"\ncapture_id: cap-1\nrevision: %s\n"
            "resource_sha256: %s\ncapture:\n  text_sha256: %s\n  completeness: %s\n---\n%s"
            % (source_type, REV_M, "1" * 64, capture_sha or sha(body), completeness, body))


def legacy_source(body: str, supersedes: str | None = None) -> str:
    extra = ("supersedes: \"%s\"\n" % supersedes) if supersedes else ""
    return "---\ntype: source\nsource_type: article\norigin: url\nurl: \"https://example.org/l\"\n%s---\n%s" % (extra, body)


def wiki_page(name: str, based_on: list, body: str, collections=("Dossier",), kind: str = "concept",
              inputs_collections: dict | None = None, body_sha: str | None = None, charter: str | None = None,
              template: str | None = None) -> str:
    bo = "".join("  - rev: \"[[Sources/%s/extract]]\"\n    text_sha256: \"%s\"\n    seal: %s\n" % e for e in based_on)
    cols = ", ".join("\"[[Collections/%s]]\"" % c for c in collections)
    ic = ", ".join("\"Collections/%s\": \"%s\"" % (k, v) for k, v in (inputs_collections or {}).items())
    if body_sha is None:
        body_sha = lr.evidential_body(body, 1)["sha256"]
    fm = ("---\ntype: wiki\nkind: %s\ncollections: [%s]\nbased_on:\n%sinputs:\n  page: \"%s\"\n  kind: %s\n  collections: {%s}\n"
          "%s  protocol: \"research/1\"\n  template: \"%s\"\nupdated: 2026-10-01\nbody_sha256: \"%s\"\n---\n"
          % (kind, cols, bo, name, kind, ic, ("  charter_sha256: \"%s\"\n" % charter) if charter else "", template or "wiki/%s@1" % kind, body_sha))
    return fm + body


class _Vault:
    """A temp vault: Sources/, one open dossier, Wiki/; `page(name)` validates one page."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = tempfile.mkdtemp()
        for d in ("Sources", "Collections", "Wiki"):
            (self.root / d).mkdir()
        self.collection("Dossier", "- [[Sources/%s/extract]] — **item** — x\n" % REV_M)

    def collection(self, name: str, items: str, status: str = "open", extra: str = "") -> None:
        (self.root / "Collections" / (name + ".md")).write_text(
            "---\ntype: collection\nstatus: %s\nquestion: \"q\"\ncurated_by: owner\ncreated: 2026-10-01\n%s---\n## Items\n%s" % (status, extra, items),
            encoding="utf-8")

    def source(self, rev: str, text: str, extract: bool = True) -> None:
        (self.root / "Sources" / rev).mkdir(exist_ok=True)
        (self.root / "Sources" / rev / "source.md").write_text(text, encoding="utf-8")
        if extract:
            (self.root / "Sources" / rev / "extract.md").write_text("---\ntype: extract\nstatus: extracted\ntitle: t\n---\n### Summary\n\nx\n", encoding="utf-8")

    def wiki(self, name: str, text: str) -> None:
        (self.root / "Wiki" / (name + ".md")).write_text(text, encoding="utf-8")

    def page(self, name: str, budget_words=None) -> dict:
        scan = lv.scan_vault(str(self.root), state_dir=self.state)
        ctx = lv.research_context(scan, budget_words=budget_words)
        return {p["name"]: lr.validate_wiki(p, ctx) for p in scan["wiki"]["notes"]}[name]

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)


BODY = "# Page\n\n## State of evidence\n\n- A claim — [[Sources/%s/source#Alpha]].\n"
BODY_LINE = 18            # wiki_page() with one based_on entry and no charter: 18 header lines, the body starts at file line 19


class WikiFrontmatterTests(unittest.TestCase):
    def test_nested_subset(self):
        data, errors = lr.parse_frontmatter_tree(
            'type: wiki\nkind: concept\ncollections: ["[[Collections/A]]", "[[Collections/B]]"]\n'
            'based_on:\n  - rev: "[[Sources/%s/extract]]"\n    text_sha256: "%s"\n    seal: observed\n  - rev: "[[Sources/%s]]"\n    seal: capture\n'
            'inputs:\n  page: "P"\n  kind: concept\n  collections: {"Collections/A": "%s", "Collections/B": "%s"}\n  protocol: "research/1"\n'
            'block:\n  "Collections/C": "%s"\n  plain: 1\nempty:\nlist:\n- a\n- b\n' % (A, SHA0, B, SHA0, SHA0, SHA0))
        self.assertEqual(errors, [])
        self.assertEqual(data["collections"], ["[[Collections/A]]", "[[Collections/B]]"])
        self.assertEqual(data["based_on"], [{"rev": "[[Sources/%s/extract]]" % A, "text_sha256": SHA0, "seal": "observed"},
                                            {"rev": "[[Sources/%s]]" % B, "seal": "capture"}])
        self.assertEqual(data["inputs"]["collections"], {"Collections/A": SHA0, "Collections/B": SHA0})
        self.assertEqual(data["block"], {"Collections/C": SHA0, "plain": "1"})
        self.assertEqual((data["empty"], data["list"]), ("", ["a", "b"]))

    def test_errors(self):
        data, errors = lr.parse_frontmatter_tree("kind: a\nkind: b\n  stray: 1\nnot a key line\ninputs:\n  page: x\n  page: y\n")
        self.assertEqual(data["kind"], "a")
        self.assertEqual(errors, ["duplicate key `kind`", "line 4: unexpected indented line: stray: 1",
                                  "line 5: not a `key: value` line: not a key line", "duplicate key `page`"])
        _d, errors = lr.parse_frontmatter_tree('m: {"a": "b", c}\n')
        self.assertEqual(errors, ["line 2: `m`: `c` is not a `key: value` pair"])

    def test_the_collection_reader_is_untouched(self):
        rec = lr.read_collection_text(HEAD + "## Items\n- [[Sources/%s/extract]]\n" % A, "x")
        self.assertEqual(rec["state"], "ok")
        rec = lr.read_collection_text("---\ntype: collection\nstatus: open\nquestion: q\ncurated_by: o\ncreated: 2026-10-01\ninputs:\n  page: x\n---\n## Items\n", "x")
        self.assertIn("nested mapping under `inputs` (flat scalars and lists only)", rec["errors"])


class WikiReaderTests(unittest.TestCase):
    def test_schema(self):
        rec = lr.read_wiki_text(wiki_page("P", [(REV_M, SHA0, "capture")], BODY % REV_M), "P")
        self.assertEqual((rec["state"], rec["kind"], rec["collections"], rec["inputs"]["page"], rec["inputs"]["template"]),
                         ("ok", "concept", ["Dossier"], "P", "wiki/concept@1"))
        self.assertEqual(rec["based_on"], [{"rev": REV_M, "file": "extract", "text_sha256": SHA0, "seal": "capture"}])
        self.assertEqual(rec["body"]["sha256"], rec["body_sha256"])
        rec = lr.read_wiki_text("---\ntype: wiki\n---\n# T\n", "P")
        self.assertEqual(rec["state"], "fail")
        for key in ("kind", "collections", "based_on", "inputs", "body_sha256"):
            self.assertIn("missing required `%s`" % key, rec["errors"])
        rec = lr.read_wiki_text(wiki_page("Other", [(REV_M, "zz", "sealed")], BODY % REV_M, kind="essay"), "P")
        self.assertIn("`kind` must be one of concept|comparison|contradiction|open-question (got 'essay')", rec["errors"])
        self.assertIn("based_on[0]: `text_sha256` is not a sha256 hex digest", rec["errors"])
        self.assertIn("based_on[0]: `seal` must be capture|observed (got 'sealed')", rec["errors"])
        self.assertIn("inputs: `page` 'Other' is not this page's file name 'P'", rec["errors"])
        rec = lr.read_wiki_text(wiki_page("P", [(REV_M, SHA0, "capture"), (REV_M, SHA0, "capture")], BODY % REV_M, collections=("Dossier", "Dossier")), "P")
        self.assertIn("based_on[1]: duplicate revision %s (first at based_on[0])" % REV_M, rec["errors"])
        self.assertIn("duplicate collection `Dossier` in `collections`", rec["errors"])
        rec = lr.read_wiki_text(wiki_page("P", [(REV_M, SHA0, "capture")], BODY % REV_M, collections=("../x", "Synthesis/y")), "P")
        self.assertIn("collections entry outside the instance: [[Collections/../x]]", rec["errors"])
        self.assertIn("collections entry names no collection: [[Collections/Synthesis/y]]", rec["errors"])
        rec = lr.read_wiki_text(wiki_page("P", [(REV_M, SHA0, "capture")], BODY % REV_M, collections=("Dossier",)).replace("[[Collections/Dossier]]", "[[Synthesis/Dossier]]"), "P")
        self.assertIn("collections entry must be [[Collections/<name>]] (got [[Synthesis/Dossier]])", rec["errors"])
        rec = lr.read_wiki_text("---\ntype: wiki\nkind: concept\ncollections: []\nbased_on: []\ninputs: {}\nbody_sha256: \"%s\"\n---\n# T\n" % SHA0, "P")
        self.assertIn("inputs: missing `page`", rec["errors"])
        rec = lr.read_wiki_text("---\ntype: wiki\nkind: concept\ncollections: []\nbased_on: x\ninputs: y\nbody_sha256: \"%s\"\n---\n# T\n" % SHA0, "P")
        self.assertIn("`based_on` must be a list of {rev, text_sha256, seal} entries", rec["errors"])
        self.assertIn("`inputs` must be a mapping", rec["errors"])

    def test_evidential_body(self):
        body = "\r\n# Title  \r\n\r\ntext \r\n\r\n```\r\n## Log\r\n```\r\n\r\n## Open questions\r\n- q\r\n\r\n\r\n## Atlas reading\r\nopinion\r\n## Log\r\nlog\r\n"
        eb = lr.evidential_body(body, 1)
        self.assertEqual(eb["text"], "# Title\n\ntext\n\n```\n## Log\n```\n\n## Open questions\n- q\n")
        self.assertEqual((eb["cut"], eb["start"], eb["words"]), ("Atlas reading", 2, 12), "fenced lines are part of the body, words included")
        self.assertEqual(eb["sha256"], hashlib.sha256(eb["text"].encode("utf-8")).hexdigest())
        eb = lr.evidential_body("no heading at all\n", 1)
        self.assertEqual((eb["sha256"], eb["start"]), (None, None))
        rec = lr.read_wiki_text("---\ntype: wiki\nkind: concept\ncollections: []\nbased_on: []\ninputs:\n  page: P\n  kind: concept\n  collections: {}\n  protocol: a\n  template: b\nbody_sha256: \"%s\"\n---\nno heading\n" % SHA0, "P")
        self.assertIn("no evidential body (no heading after the frontmatter)", rec["errors"])
        # the body ends where ## Log begins even without ## Atlas reading; a ### Log is not a cut
        eb = lr.evidential_body("# T\n### Log\nx\n## Log\ny\n", 1)
        self.assertEqual(eb["text"], "# T\n### Log\nx\n")

    def test_links_and_locators(self):
        body = ("# T\n- [[Sources/%s/source#Alpha]] heading\n> \"a  quote\" — [[Sources/%s/source]]\n- bare [[Sources/%s/source]] and nav [[Sources/%s/extract#Summary|x]]\n"
                "- [[Collections/D]] [[Wiki/W]] [[Synthesis/S]] [[Note]] [[../out]] [[Sources/x/y]]\n```\n[[Sources/%s/source#fenced]]\n```\n## Log\n[[Sources/%s/extract]]\n"
                % (A, A, A, A, A, A))
        rec = lr.read_wiki_text(wiki_page("P", [(A, SHA0, "observed")], body), "P")
        kinds = [(l["kind"], l.get("rev") or l.get("path") or l.get("target")) for l in rec["links"]]
        self.assertEqual(kinds, [("sources", A), ("sources", A), ("sources", A), ("sources", A), ("local", "Collections/D"), ("local", "Wiki/W"),
                                 ("local", "Synthesis/S"), ("unresolved", "Note"), ("outside", "../out"), ("outside", "Sources/x/y")])
        self.assertEqual([(l["form"], l.get("heading") or l.get("quote")) for l in rec["locators"]], [("heading", "Alpha"), ("quote", "a  quote")])
        self.assertEqual(rec["counts"], {"based_on": 1, "collections": 1, "links": 10, "locators": 2, "links_consumable": 1})

    def test_veto_predicate(self):
        cols = {"open-off": {"status": "open", "wiki": "off", "suppressed_pages": []},
                "open-name": {"status": "open", "wiki": "on", "suppressed_pages": ["P"]},
                "open-ok": {"status": "open", "wiki": "on", "suppressed_pages": ["Q"]},
                "inbox-off": {"status": "inbox", "wiki": "off", "suppressed_pages": ["P"]},
                "closed-off": {"status": "closed", "wiki": "off", "suppressed_pages": ["P"]}}
        self.assertEqual(lr.is_suppressed("P", ["open-off"], cols), ["open-off"])
        self.assertEqual(lr.is_suppressed("P", ["open-name"], cols), ["open-name"])
        self.assertEqual(lr.is_suppressed("P", ["open-ok", "closed-off", "inbox-off", "missing"], cols), [], "inbox and closed give no veto")
        self.assertEqual(lr.is_suppressed("P", ["open-ok", "open-off"], cols), ["open-off"], "one veto suffices")
        self.assertEqual(lr.is_suppressed("Q", ["open-ok"], cols), ["open-ok"])


class WikiValidatorTests(unittest.TestCase):
    def setUp(self):
        self.v = _Vault()
        self.body = "# Alpha\n\nThe managed text about trust.\n"
        self.v.source(REV_M, managed_source(self.body))
        self.v.source(REV_L, legacy_source("# Beta\n\nLegacy text.\n"))

    def tearDown(self):
        self.v.close()

    def _page(self, based_on, body=None, budget_words=None, **kw) -> dict:
        self.v.wiki("P", wiki_page("P", based_on, body or BODY % REV_M, **kw))
        return self.v.page("P", budget_words=budget_words)

    def test_capture_seal_on_a_managed_revision(self):
        p = self._page([(REV_M, sha(self.body), "capture")])
        self.assertEqual((p["severity"], p["states"], p["cited"], p["usable"]), ("ok", [], [REV_M], True))

    def test_capture_seal_wrong_hash_or_legacy_is_unbound(self):
        p = self._page([(REV_M, SHA0, "capture")])
        self.assertEqual(p["states"], ["unbound"])
        self.assertIn("seal capture on %s: printed 000000000000… ≠ capture.text_sha256" % REV_M, p["reasons"]["unbound"][0])
        p = self._page([(REV_L, sha("# Beta\n\nLegacy text.\n"), "capture")], body=BODY.replace("#Alpha", "#Beta") % REV_L)
        self.assertEqual(p["reasons"]["unbound"], ["seal capture on %s, which carries no capture.text_sha256 (legacy revision)" % REV_L])

    def test_observed_seal(self):
        legacy_body = "# Beta\n\nLegacy text.\n"
        p = self._page([(REV_L, sha(legacy_body), "observed")], body=BODY.replace("#Alpha", "#Beta") % REV_L)
        self.assertEqual((p["severity"], p["states"]), ("ok", []))
        p = self._page([(REV_L, SHA0, "observed")], body=BODY.replace("#Alpha", "#Beta") % REV_L)
        self.assertEqual(p["states"], ["unbound", "inputs-changed"])
        self.assertIn("seal observed on %s: printed 000000000000… ≠ body" % REV_L, p["reasons"]["unbound"][0])
        p = self._page([(REV_M, sha(self.body), "observed")])
        self.assertEqual(p["reasons"]["unbound"], ["seal observed on the managed revision %s (seal capture required)" % REV_M], "I4: observed on managed is forbidden")

    def test_integrity_error_is_unbound_and_weak(self):
        self.v.source(REV_M, managed_source(self.body + "edited\n", capture_sha=sha(self.body)))
        p = self._page([(REV_M, sha(self.body), "capture")])
        self.assertEqual(p["states"], ["unbound", "weak-evidence"])
        self.assertIn("integrity error", p["reasons"]["unbound"][0])

    def test_weak_evidence(self):
        self.v.source(REV_M, managed_source(self.body, completeness="partial"))
        p = self._page([(REV_M, sha(self.body), "capture")])
        self.assertEqual((p["severity"], p["states"], p["reasons"]["weak-evidence"]), ("warn", ["weak-evidence"], ["%s: completeness partial" % REV_M]))
        self.v.source(REV_M, managed_source(self.body, completeness="blocked"))
        self.assertEqual(self._page([(REV_M, sha(self.body), "capture")])["states"], ["weak-evidence"])
        self.v.source(REV_M, managed_source(self.body, completeness="unknown", source_type="pdf"))
        p = self._page([(REV_M, sha(self.body), "capture")])
        self.assertEqual((p["states"], p["reasons"]["info"]), ([], ["%s: completeness unknown (pdf)" % REV_M]), "unknown on a PDF is info, not a state")

    def test_missing_revision_and_targets(self):
        p = self._page([("2026-01-01_99999999", SHA0, "observed")], body="# T\n- [[Sources/2026-01-01_99999999/source#X]]\n")
        self.assertEqual(p["reasons"]["unbound"], ["based_on names 2026-01-01_99999999, which has no source.md",
                                                   "line %d: [[Sources/2026-01-01_99999999/source#X]] does not exist" % (BODY_LINE + 1)])
        self.v.source(REV_N, legacy_source("# N\n"), extract=False)
        p = self._page([(REV_M, sha(self.body), "capture")], body="# T\n- [[Sources/%s/extract]] has no extract\n" % REV_N)
        self.assertEqual(p["reasons"]["unbound"], ["line %d: [[Sources/%s/extract]] does not exist" % (BODY_LINE + 1, REV_N)])

    def test_stale_and_the_incomplete_graph(self):
        self.v.source(REV_N, legacy_source("# N\n", supersedes=REV_L))
        legacy_body = "# Beta\n\nLegacy text.\n"
        p = self._page([(REV_L, sha(legacy_body), "observed")], body="# T\n- [[Sources/%s/source#Beta]]\n" % REV_L)
        self.assertEqual((p["severity"], p["states"], p["reasons"]["stale"]), ("warn", ["stale"], ["%s is superseded" % REV_L]))
        # a dangling supersedes target elsewhere makes the graph incomplete: a superseded candidate is
        # not probed, never "current" (interface §9); the check research/wiki-stale is not probed as a whole
        self.v.source("2026-06-04_44444444", legacy_source("# D\n", supersedes="2026-01-01_00000000"), extract=False)
        p = self._page([(REV_L, sha(legacy_body), "observed")], body="# T\n- [[Sources/%s/source#Beta]]\n" % REV_L)
        self.assertEqual((p["severity"], p["states"], p["usable"]), (None, [], False))
        self.assertEqual(p["not_probed"], ["stale: the supersedes graph is incomplete (%s)" % REV_L])

    def test_stale_not_probed_only_for_superseded_candidates(self):
        """A revision that nothing supersedes is current whatever the graph's holes elsewhere."""
        self.v.source(REV_N, legacy_source("# N\n", supersedes="2026-01-01_00000000"))
        p = self._page([(REV_M, sha(self.body), "capture")])
        self.assertEqual(p["states"], [])
        self.assertEqual(p["not_probed"], [])

    def test_unreadable_source_is_not_probed(self):
        if os.geteuid() == 0:
            self.skipTest("root reads everything")
        os.chmod(self.v.root / "Sources" / REV_M / "source.md", 0)
        try:
            p = self._page([(REV_M, SHA0, "capture")])
        finally:
            os.chmod(self.v.root / "Sources" / REV_M / "source.md", 0o644)
        self.assertEqual((p["severity"], p["usable"], p["states"]), (None, False, []))
        self.assertEqual(p["not_probed"], ["source.md of %s could not be read" % REV_M,
                                           "line %d: source.md of %s could not be read" % (BODY_LINE + 4, REV_M)], "the seal and the locator both say so")

    def test_edited_outside_update(self):
        p = self._page([(REV_M, sha(self.body), "capture")], body_sha=SHA0)
        self.assertEqual(p["states"], ["edited-outside-update"])
        # a change in ## Atlas reading or ## Log is not a state
        body = BODY % REV_M + "\n## Atlas reading\nchanged opinion\n## Log\n- changed\n"
        self.v.wiki("P", wiki_page("P", [(REV_M, sha(self.body), "capture")], body, body_sha=lr.evidential_body(BODY % REV_M, 1)["sha256"]))
        self.assertEqual(self.v.page("P")["states"], [])

    def test_inputs_changed(self):
        col_sha = hashlib.sha256((self.v.root / "Collections" / "Dossier.md").read_bytes()).hexdigest()
        p = self._page([(REV_M, sha(self.body), "capture")], inputs_collections={"Dossier": col_sha})
        self.assertEqual(p["states"], [])
        p = self._page([(REV_M, sha(self.body), "capture")], inputs_collections={"Dossier": SHA0}, template="wiki/concept@0")
        self.assertEqual(p["reasons"]["inputs-changed"], ["collection `Dossier` changed (%s… ≠ printed 000000000000…)" % col_sha[:12],
                                                          "template `wiki/concept@0` ≠ engine `wiki/concept@1`"])
        (self.v.root / "_meta").mkdir()
        (self.v.root / "_meta" / "charter.md").write_text("# Charter\n", encoding="utf-8")
        p = self._page([(REV_M, sha(self.body), "capture")], charter=sha("# Charter\n"))
        self.assertEqual(p["states"], [])
        p = self._page([(REV_M, sha(self.body), "capture")])
        self.assertEqual(p["reasons"]["inputs-changed"], ["charter changed (%s… ≠ printed none)" % sha("# Charter\n")[:12]])
        p = self._page([(REV_M, sha(self.body), "capture")], kind="comparison")
        self.assertEqual(p["reasons"].get("unbound"), None)
        self.v.wiki("P", wiki_page("P", [(REV_M, sha(self.body), "capture")], BODY % REV_M, charter=sha("# Charter\n")).replace("  kind: concept", "  kind: comparison"))
        self.assertEqual(self.v.page("P")["reasons"]["unbound"], ["kind `concept` ≠ inputs.kind `comparison`"])

    def test_orphan_and_veto(self):
        self.v.collection("Closed", "", status="closed", extra="wiki: off\n")
        p = self._page([(REV_M, sha(self.body), "capture")], collections=("Closed", "Nowhere"), charter=None)
        self.assertEqual((p["states"], p["reasons"]["orphan"]), (["orphan"], ["`Closed` is closed", "`Nowhere` does not exist"]))
        p = self._page([(REV_M, sha(self.body), "capture")], collections=())
        self.assertEqual(p["reasons"]["orphan"], ["no collections listed"])
        self.v.collection("Off", "", extra="wiki: off\n")
        p = self._page([(REV_M, sha(self.body), "capture")], collections=("Dossier", "Off"))
        self.assertEqual((p["severity"], p["states"]), ("fail", ["suppressed"]))
        p = self._page([(REV_M, sha(self.body), "capture")], collections=("Dossier", "Closed"))
        self.assertEqual(p["states"], [], "an open dossier without a veto and a closed one with wiki: off: allowed")

    def test_locators_against_the_source(self):
        self.v.source(REV_M, managed_source("# Alpha\n\n## Twice\n\nx\n\n## Twice\n\ny  z\n"))
        s = sha("# Alpha\n\n## Twice\n\nx\n\n## Twice\n\ny  z\n")
        body = ("# T\n- [[Sources/%s/source#Alpha]]\n- [[Sources/%s/source#Twice]]\n- [[Sources/%s/source#Nope]]\n> \"y z\" — [[Sources/%s/source]]\n"
                "> «x» — [[Sources/%s/source]].\n> \"\" — [[Sources/%s/source]]\n> \"missing\" — [[Sources/%s/source]]\n" % ((REV_M,) * 7))
        p = self._page([(REV_M, s, "capture")], body=body)
        self.assertEqual([(l["form"], l["verified"]) for l in p["locators"]],
                         [("heading", True), ("heading", False), ("heading", False), ("quote", True), ("quote", True), ("quote", False), ("quote", False)])
        self.assertEqual(p["reasons"]["unbound"], ["line %d: heading `Twice` found 2 times in %s/source.md" % (BODY_LINE + 2, REV_M),
                                                   "line %d: heading `Nope` not found in %s/source.md" % (BODY_LINE + 3, REV_M),
                                                   "line %d: empty quote for %s" % (BODY_LINE + 6, REV_M),
                                                   "line %d: quote not found in %s/source.md" % (BODY_LINE + 7, REV_M)])
        self.assertEqual(p["cited"], [REV_M])

    def test_unlisted_and_inputs_subset(self):
        legacy_body = "# Beta\n\nLegacy text.\n"
        p = self._page([(REV_M, sha(self.body), "capture")], body="# T\n- [[Sources/%s/source#Beta]]\n" % REV_L)
        self.assertEqual(p["reasons"]["unbound"], ["line %d: unlisted — [[Sources/%s/source#Beta]] names a revision outside based_on" % (BODY_LINE + 1, REV_L)])
        self.assertEqual(p["cited"], [], "an unlisted locator is never verified")
        p = self._page([(REV_M, sha(self.body), "capture"), (REV_L, sha(legacy_body), "observed")], body="# T\n- [[Sources/%s/source#Beta]]\n" % REV_L)
        self.assertEqual((p["states"], p["cited"]), ([], [REV_L]))
        p = self._page([(REV_M, sha(self.body), "capture")], inputs_collections={"Elsewhere": SHA0})
        self.assertIn("inputs.collections names `Elsewhere`, which is not in `collections`", p["reasons"]["unbound"])

    def test_budget_and_links(self):
        p = self._page([(REV_M, sha(self.body), "capture")], budget_words=3)
        self.assertEqual(p["reasons"]["budget"], ["%d words in the evidential body (budget 3)" % p["body"]["words"]])
        body = BODY % REV_M + "- [[Collections/Dossier]] [[Collections/Gone]] [[Wiki/P]] [[Synthesis/None]] [[Loose]] [[/abs]]\n"
        p = self._page([(REV_M, sha(self.body), "capture")], body=body)
        n = BODY_LINE + 5         # the appended line (line numbers are the file's)
        self.assertEqual(p["reasons"]["unresolved"], ["line %d: [[Collections/Gone]] does not exist" % n, "line %d: [[Synthesis/None]] does not exist" % n,
                                                      "line %d: [[Loose]] (not a link into Sources/, Collections/, Wiki/ or Synthesis/)" % n])
        self.assertEqual(p["reasons"]["unbound"], ["line %d: link outside the instance: /abs" % n])

    def test_schema_fail_and_unreadable_pages(self):
        self.v.wiki("P", "---\ntype: wiki\n---\n# T\n")
        p = self.v.page("P")
        self.assertEqual((p["severity"], p["states"], p["usable"]), ("fail", ["schema"], False))
        if os.geteuid() != 0:
            os.chmod(self.v.root / "Wiki" / "P.md", 0)
            try:
                p = self.v.page("P")
            finally:
                os.chmod(self.v.root / "Wiki" / "P.md", 0o644)
            self.assertEqual((p["state"], p["severity"], p["not_probed"], p["usable"]), ("unreadable", None, ["unreadable"], False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
