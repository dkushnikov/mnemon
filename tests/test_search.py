#!/usr/bin/env python3
"""Unit + regression + end-to-end tests for bin/mnemon-search.py.

Run: `python3 tests/test_search.py` (tests/test_search.sh wraps it). Stdlib
only — unittest + tempfile + subprocess.

Test classes
------------
- TokenizeTests           — tokenize() edge cases
- ParseFrontmatterTests   — inline list, block list, nested map, dates as strings, malformed
- ScanFileTests           — matched token set + snippet extraction
- RankResultsTests        — rating DESC, date DESC tiebreak, None handling
- DedupeByFolderTests     — extract > synthesis > source priority
- DomainFilterTests       — exact match, missing field, case handling
- FallbackStrategyTests   — REGRESSION: most-specific existing token, not the longest
- JsonOutputTests         — REGRESSION: `created:` dates serialize as "YYYY-MM-DD"
- ConfigResolutionTests   — MNEMON_INSTANCE / --vault override / missing instance
- EndToEndTests           — full script via subprocess against a fixture vault

Every subprocess runs with a scrubbed environment (no inherited
MNEMON_CONFIG / MNEMON_INSTANCE, XDG dirs under the test's temp dir) and a
temp cwd, so the user's real configuration is never read.
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
SCRIPT = BIN_DIR / "mnemon-search.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mnemon_search", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mnemon_search"] = module
    spec.loader.exec_module(module)
    return module


search = _load_module()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def write_extract(
    folder: Path,
    *,
    title: str,
    rating=None,
    domains=None,
    created="2026-01-01",
    author="",
    url="",
    body="",
    domains_format: str = "inline",  # "inline" or "block"
) -> Path:
    """Write Sources/<folder>/extract.md with the given fields.

    domains_format controls whether `domains:` is written as an inline list
    (`[a, b]`) or a YAML block list (newline + `- a`). Both forms appear in
    real Mnemon extracts, and parsing block lists was a real bug.
    """
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: extract", f'title: "{title}"']
    if author:
        lines.append(f'author: "{author}"')
    if url:
        lines.append(f'url: "{url}"')
    if rating is not None:
        lines.append(f"rating: {rating}")
    if domains is not None:
        if domains_format == "inline":
            lines.append(f"domains: [{', '.join(domains)}]")
        else:
            lines.append("domains:")
            for d in domains:
                lines.append(f"  - {d}")
    lines.append(f"created: {created}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path = folder / "extract.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_source(folder: Path, body: str) -> Path:
    """Write a minimal source.md in a folder (for --include-source tests)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.md"
    path.write_text(f"---\ntype: source\n---\n\n{body}\n", encoding="utf-8")
    return path


def clean_env(tmpdir: Path, **overrides) -> dict:
    """Subprocess environment: no inherited config selection, XDG dirs in tmpdir."""
    env = os.environ.copy()
    for key in ("MNEMON_CONFIG", "MNEMON_INSTANCE", "MNEMON_CONFIG_DIR"):
        env.pop(key, None)
    env["XDG_CONFIG_HOME"] = str(tmpdir / "xdg")
    env["XDG_STATE_HOME"] = str(tmpdir / "state")
    env.update(overrides)
    return env


class TempVaultMixin:
    """A temp dir, a vault under it, a mnemon.yaml pointing at the vault."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp()).resolve()
        self.vault = self.tmpdir / "vault"
        (self.vault / "Sources").mkdir(parents=True)
        (self.vault / "Synthesis").mkdir()
        (self.tmpdir / "cwd").mkdir()
        self.config = self.tmpdir / "mnemon.yaml"
        self.config.write_text(f"vault_path: {self.vault}\nsearch_provider: grep\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _proc(self, *args, env=None) -> subprocess.CompletedProcess:
        if env is None:
            env = clean_env(self.tmpdir, MNEMON_CONFIG=str(self.config))
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.tmpdir / "cwd"),
        )

    def _run(self, *args, env=None) -> dict:
        result = self._proc(*args, env=env)
        self.assertEqual(result.returncode, 0,
                         f"mnemon-search.py exited {result.returncode}: {result.stderr}")
        return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# TokenizeTests
# ---------------------------------------------------------------------------

class TokenizeTests(unittest.TestCase):
    def test_basic_multi_word(self):
        self.assertEqual(search.tokenize("karpathy knowledge bases"),
                         ["karpathy", "knowledge", "bases"])

    def test_lowercases(self):
        self.assertEqual(search.tokenize("Karpathy KNOWLEDGE"), ["karpathy", "knowledge"])

    def test_drops_punctuation(self):
        self.assertEqual(search.tokenize("hello, world!"), ["hello", "world"])

    def test_drops_tokens_shorter_than_two(self):
        self.assertEqual(search.tokenize("a b ai ml"), ["ai", "ml"])

    def test_unicode(self):
        tokens = search.tokenize("Карпатый knowledge")
        self.assertIn("knowledge", tokens)
        self.assertIn("карпатый", tokens)

    def test_empty(self):
        self.assertEqual(search.tokenize(""), [])

    def test_only_punctuation(self):
        self.assertEqual(search.tokenize("!?;:"), [])


# ---------------------------------------------------------------------------
# ParseFrontmatterTests — stdlib parser
# ---------------------------------------------------------------------------

class ParseFrontmatterTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, content: str) -> Path:
        p = Path(self.tmpdir) / "test.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_inline_list_domains(self):
        p = self._write("---\ntitle: Test\ndomains: [learning, career, mc/ai]\n---\n\nbody\n")
        self.assertEqual(search.parse_frontmatter(p)["domains"], ["learning", "career", "mc/ai"])

    def test_block_list_domains_REGRESSION(self):
        # Real extracts use the block form for domains and tags; a parser
        # that handled only inline lists silently dropped them.
        p = self._write(
            "---\ntitle: Test\ndomains:\n  - career\n  - mc/ai\n  - learning\n---\n\nbody\n"
        )
        self.assertEqual(search.parse_frontmatter(p)["domains"], ["career", "mc/ai", "learning"])

    def test_block_list_unindented_items(self):
        # Valid YAML: sequence items at the key's own column.
        p = self._write(
            "---\ntitle: Test\ndomains:\n- career\n- learning\nrating: 7\ntags:\n- one\n---\n\nbody\n"
        )
        meta = search.parse_frontmatter(p)
        self.assertEqual(meta["domains"], ["career", "learning"])
        self.assertEqual(meta["rating"], 7)
        self.assertEqual(meta["tags"], ["one"])

    def test_block_list_followed_by_more_keys(self):
        p = self._write(
            "---\ntitle: Test\ntags:\n  - one\n  - two\nrating: 8\n---\n\nbody\n"
        )
        meta = search.parse_frontmatter(p)
        self.assertEqual(meta["tags"], ["one", "two"])
        self.assertEqual(meta["rating"], 8)

    def test_nested_map_capture_context(self):
        p = self._write(
            "---\n"
            "title: Test\n"
            "capture_context:\n"
            "  vault: personal\n"
            '  session: "deep work"\n'
            "  import_source: gateway\n"
            "created: 2026-04-04\n"
            "---\n\nbody\n"
        )
        meta = search.parse_frontmatter(p)
        self.assertEqual(meta["capture_context"],
                         {"vault": "personal", "session": "deep work", "import_source": "gateway"})
        self.assertEqual(meta["created"], "2026-04-04")

    def test_date_stays_string(self):
        # No PyYAML: `created: 2026-04-04` is kept as the string the JSON
        # output needs, and the date sort key parses it.
        p = self._write("---\ntitle: Test\ncreated: 2026-04-04\n---\n\nbody\n")
        meta = search.parse_frontmatter(p)
        self.assertEqual(meta["created"], "2026-04-04")
        self.assertEqual(search._date_sort_key(meta["created"]), 20260404)

    def test_integer_rating(self):
        meta = search.parse_frontmatter(self._write("---\ntitle: Test\nrating: 9\n---\n\nbody\n"))
        self.assertEqual(meta["rating"], 9)
        self.assertIsInstance(meta["rating"], int)

    def test_inline_comment_stripped_outside_quotes(self):
        meta = search.parse_frontmatter(
            self._write('---\nrating: 7  # good one\ntitle: "Note: #1 thing"\n---\n\nbody\n')
        )
        self.assertEqual(meta["rating"], 7)
        self.assertEqual(meta["title"], "Note: #1 thing")

    def test_quoted_values_and_url(self):
        meta = search.parse_frontmatter(
            self._write("---\ntitle: 'It''s quoted'\nurl: https://example.com/a?b=c\n---\n\nbody\n")
        )
        self.assertEqual(meta["title"], "It's quoted")
        self.assertEqual(meta["url"], "https://example.com/a?b=c")

    def test_inline_list_with_quoted_items(self):
        meta = search.parse_frontmatter(
            self._write('---\npeople: ["Doe, Jane", Smith]\ntags: []\n---\n\nbody\n')
        )
        self.assertEqual(meta["people"], ["Doe, Jane", "Smith"])
        self.assertEqual(meta["tags"], [])

    def test_booleans_and_null(self):
        meta = search.parse_frontmatter(self._write("---\nflag: true\nnothing: null\n---\n\nbody\n"))
        self.assertIs(meta["flag"], True)
        self.assertIsNone(meta["nothing"])

    def test_no_frontmatter(self):
        self.assertEqual(search.parse_frontmatter(self._write("Just a plain markdown file.\n")), {})

    def test_malformed_frontmatter(self):
        # Unclosed frontmatter — parser should not blow up.
        self.assertEqual(search.parse_frontmatter(self._write("---\ntitle: Test\n\nno closing\n")), {})

    def test_missing_file(self):
        self.assertEqual(search.parse_frontmatter(Path(self.tmpdir) / "does-not-exist.md"), {})

    def test_scalar_domains_normalized_to_list(self):
        # `domains: career` (a scalar) must not be iterated character by character.
        p = self._write("---\ntitle: T\ndomains: career\n---\n\nbody\n")
        res = search.assemble_result(p, "extract", None, Path(self.tmpdir))
        self.assertEqual(res["domains"], ["career"])

    # --- the serialization subset of the capture contracts (spec §4.4): exactly two escapes ---
    def test_dq_decoder_decodes_only_the_two_escapes(self):
        self.assertEqual(search._decode_dq('a \\"quoted\\" \\\\ title'), 'a "quoted" \\ title')
        self.assertEqual(search._decode_dq('C:\\temp\\x'), 'C:\\temp\\x')      # unknown sequences kept
        self.assertEqual(search._decode_dq('back\\\\\\"'), 'back\\"')           # one pass, no re-scan
        self.assertEqual(search._decode_dq('~'), '~')
        self.assertEqual(search._decode_dq('~/Guide'), '~/Guide')

    def test_round_trip_titles(self):
        for title in ('~', '~/Guide', 'a "quoted" \\ title', 'close ]] me | pipe'):
            written = '"' + title.replace('\\', '\\\\').replace('"', '\\"') + '"'
            p = self._write(f"---\ntitle: {written}\nrating: 7\n---\n\nbody\n")
            meta = search.parse_frontmatter(p)
            self.assertEqual(meta["title"], title)
            self.assertEqual(meta["rating"], 7)

    def test_new_format_source_and_extract(self):
        # source.md: flat top-level keys + a nested capture: block that must not break the reader
        src = self._write(
            '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/post"\n'
            'title: "Plain T"\ncapture_id: "c-1"\nresource_sha256: "3f2a"\nrevision: "2026-10-15_c2e16f9b"\n'
            'context: "personal"\ncapture:\n  method: "curl+defuddle"\n  fidelity: converted\n  raw_store: archive\n'
            '  text_sha256: "abcd"\n  text_bytes: 9120\n---\nbody text\n'
        )
        meta = search.parse_frontmatter(src)
        self.assertEqual(meta["title"], "Plain T")
        self.assertEqual(meta["url"], "https://example.com/post")
        self.assertEqual(meta["revision"], "2026-10-15_c2e16f9b")
        self.assertEqual(meta["capture"]["method"], "curl+defuddle")
        self.assertEqual(meta["capture"]["text_bytes"], 9120)
        # extract.md as the publisher writes it: unquoted flow-list elements, nested blocks
        ex = self._write(
            '---\ntype: extract\nstatus: extracted\ncontext: "personal"\ntitle: "T"\nauthor: ""\n'
            'created: 2026-10-15\nlanguage: en\ntags: [a, b/c]\npeople: [Ada Lovelace]\ndomains: [career, learning]\n'
            'rating: 5\ncapture_id: "c-1"\nrevision: "2026-10-15_c2e16f9b"\ncapture_context:\n  vault: knowledge\n'
            '  session: ""\nextractor:\n  schema: extract/1\n  model: sonnet\n  attempts: 1\n---\n### Summary\n'
        )
        meta = search.parse_frontmatter(ex)
        self.assertEqual(meta["tags"], ["a", "b/c"])
        self.assertEqual(meta["people"], ["Ada Lovelace"])
        self.assertEqual(meta["domains"], ["career", "learning"])
        self.assertEqual(meta["rating"], 5)
        self.assertEqual(meta["author"], "")
        self.assertEqual(meta["extractor"]["schema"], "extract/1")
        self.assertEqual(meta["created"], "2026-10-15")


# ---------------------------------------------------------------------------
# ScanFileTests
# ---------------------------------------------------------------------------

class ScanFileTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = Path(self.tmpdir) / "extract.md"
        self.path.write_text(
            "---\ntitle: Karpathy LLM Knowledge Bases\ndomains: [learning]\n---\n\n"
            "Body mentioning knowledge and llm.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_returns_matched_subset(self):
        matched, _ = search.scan_file(self.path, ["karpathy", "knowledge", "bases", "nonexistent"])
        self.assertEqual(matched, {"karpathy", "knowledge", "bases"})

    def test_all_tokens_match(self):
        matched, _ = search.scan_file(self.path, ["karpathy", "knowledge"])
        self.assertEqual(matched, {"karpathy", "knowledge"})

    def test_no_tokens_match(self):
        matched, snippet = search.scan_file(self.path, ["quantumxyz"])
        self.assertEqual(matched, set())
        self.assertIsNone(snippet)

    def test_snippet_prefers_body_over_frontmatter(self):
        _, snippet = search.scan_file(self.path, ["knowledge"])
        self.assertIsNotNone(snippet)
        self.assertIn("Body mentioning knowledge", snippet)

    def test_missing_file(self):
        matched, snippet = search.scan_file(Path(self.tmpdir) / "no-such-file.md", ["anything"])
        self.assertEqual(matched, set())
        self.assertIsNone(snippet)


# ---------------------------------------------------------------------------
# RankResultsTests
# ---------------------------------------------------------------------------

class RankResultsTests(unittest.TestCase):
    def test_rating_desc(self):
        results = [
            {"rating": 5, "created": "2026-01-01", "title": "b"},
            {"rating": 10, "created": "2026-01-01", "title": "a"},
            {"rating": 7, "created": "2026-01-01", "title": "c"},
        ]
        self.assertEqual([r["rating"] for r in search.rank_results(results)], [10, 7, 5])

    def test_date_desc_tiebreak(self):
        results = [
            {"rating": 9, "created": "2026-01-01", "title": "a"},
            {"rating": 9, "created": "2026-03-15", "title": "b"},
            {"rating": 9, "created": "2026-02-10", "title": "c"},
        ]
        self.assertEqual([r["created"] for r in search.rank_results(results)],
                         ["2026-03-15", "2026-02-10", "2026-01-01"])

    def test_none_rating_sinks_to_bottom(self):
        results = [
            {"rating": None, "created": "2026-12-31", "title": "nulled"},
            {"rating": 5, "created": "2026-01-01", "title": "low"},
            {"rating": 8, "created": "2026-01-01", "title": "mid"},
        ]
        self.assertEqual([r["rating"] for r in search.rank_results(results)], [8, 5, None])

    def test_title_final_tiebreak(self):
        results = [
            {"rating": 9, "created": "2026-01-01", "title": "zebra"},
            {"rating": 9, "created": "2026-01-01", "title": "alpha"},
            {"rating": 9, "created": "2026-01-01", "title": "mango"},
        ]
        self.assertEqual([r["title"] for r in search.rank_results(results)],
                         ["alpha", "mango", "zebra"])


# ---------------------------------------------------------------------------
# DedupeByFolderTests
# ---------------------------------------------------------------------------

class DedupeByFolderTests(unittest.TestCase):
    def test_extract_beats_source(self):
        results = [
            {"path": "Sources/2026-01-01_aaa/", "result_type": "source", "title": "raw"},
            {"path": "Sources/2026-01-01_aaa/", "result_type": "extract", "title": "extracted"},
        ]
        deduped = search.dedupe_by_folder(results)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["result_type"], "extract")

    def test_different_folders_both_kept(self):
        results = [
            {"path": "Sources/a/", "result_type": "extract", "title": "a"},
            {"path": "Sources/b/", "result_type": "extract", "title": "b"},
        ]
        self.assertEqual(len(search.dedupe_by_folder(results)), 2)

    def test_synthesis_beats_source(self):
        results = [
            {"path": "X/", "file": "s.md", "result_type": "source", "title": "s"},
            {"path": "X/", "file": "syn.md", "result_type": "synthesis", "title": "syn"},
        ]
        # a synthesis note is keyed by file: it never collapses into a Sources result
        self.assertEqual(sorted(r["result_type"] for r in search.dedupe_by_folder(results)), ["source", "synthesis"])

    def test_synthesis_notes_in_one_folder_are_distinct(self):
        results = [
            {"path": "Synthesis/", "file": "a.md", "result_type": "synthesis", "title": "a", "rating": 3},
            {"path": "Synthesis/", "file": "b.md", "result_type": "synthesis", "title": "b", "rating": 9},
        ]
        deduped = search.dedupe_by_folder(results)
        self.assertEqual(sorted(r["file"] for r in deduped), ["a.md", "b.md"])


# ---------------------------------------------------------------------------
# DomainFilterTests
# ---------------------------------------------------------------------------

class DomainFilterTests(unittest.TestCase):
    def test_exact_match(self):
        results = [
            {"title": "a", "domains": ["career", "learning"]},
            {"title": "b", "domains": ["mc/ai"]},
            {"title": "c", "domains": ["career"]},
        ]
        self.assertEqual([r["title"] for r in search.apply_domain_filter(results, "career")], ["a", "c"])

    def test_case_insensitive(self):
        self.assertEqual(len(search.apply_domain_filter([{"title": "a", "domains": ["Career"]}], "career")), 1)

    def test_empty_domain_returns_all(self):
        results = [{"title": "a", "domains": ["x"]}, {"title": "b", "domains": ["y"]}]
        self.assertEqual(len(search.apply_domain_filter(results, "")), 2)

    def test_missing_domains_field(self):
        results = [{"title": "a"}, {"title": "b", "domains": ["x"]}]
        self.assertEqual([r["title"] for r in search.apply_domain_filter(results, "x")], ["b"])


# ---------------------------------------------------------------------------
# FallbackStrategyTests — REGRESSION: most specific token, not longest
# ---------------------------------------------------------------------------

class FallbackStrategyTests(TempVaultMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # "karpathy" in 2 files (rare), "knowledge" in 4 files (common).
        write_extract(self.vault / "Sources" / "2026-04-04_aaa11111", title="Andrej Karpathy tweet",
                      rating=10, domains=["learning"], created="2026-04-04",
                      body="Karpathy describes his LLM workflow.")
        write_extract(self.vault / "Sources" / "2026-04-05_bbb22222", title="LLM Wiki by Karpathy",
                      rating=8, domains=["learning"], created="2026-04-05",
                      body="Karpathy pattern for personal knowledge.")
        write_extract(self.vault / "Sources" / "2026-03-01_ccc33333", title="Knowledge systems review",
                      rating=7, domains=["learning"], created="2026-03-01",
                      body="Review of knowledge management systems.")
        write_extract(self.vault / "Sources" / "2026-03-02_ddd44444", title="Knowledge Graph primer",
                      rating=6, domains=["learning"], created="2026-03-02",
                      body="Introduction to knowledge graphs and RDF.")

    def test_primary_and_match_returns_intersection(self):
        out = self._run("--query", "karpathy")
        self.assertEqual(out["count"], 2)
        self.assertFalse(out["fallback_applied"])

    def test_fallback_picks_most_specific_existing_token(self):
        # Old longest-token logic would pick "knowledge" (9 > 8 chars) and
        # return 4 results; specificity picks "karpathy" (2 files < 4).
        out = self._run("--query", "karpathy quantumxyzfoo")
        self.assertTrue(out["fallback_applied"])
        self.assertEqual(out["fallback_token"], "karpathy")
        self.assertEqual(out["count"], 2)

    def test_fallback_longest_is_not_picked_when_rarer_exists(self):
        # "zyzzytoken" (10 chars, 0 matches) must lose to "karpathy" (8 chars, 2 matches).
        out = self._run("--query", "karpathy zyzzytoken")
        self.assertTrue(out["fallback_applied"])
        self.assertEqual(out["fallback_token"], "karpathy")
        self.assertEqual(out["count"], 2)

    def test_no_viable_tokens_means_no_fallback(self):
        out = self._run("--query", "quantumxyzfoo flooblegobbledy")
        self.assertEqual(out["count"], 0)
        self.assertFalse(out["fallback_applied"])
        self.assertIsNone(out["fallback_token"])

    def test_single_token_no_fallback(self):
        out = self._run("--query", "quantumxyzfoo")
        self.assertEqual(out["count"], 0)
        self.assertFalse(out["fallback_applied"])


# ---------------------------------------------------------------------------
# JsonOutputTests — REGRESSION: dates must serialize as "YYYY-MM-DD"
# ---------------------------------------------------------------------------

class JsonOutputTests(TempVaultMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        write_extract(self.vault / "Sources" / "2026-04-04_aaa", title="Date test", rating=9,
                      domains=["x"], created="2026-04-04", body="marker-token-here")

    def test_json_output_date_is_a_plain_string(self):
        data = self._run("--query", "marker-token-here")
        self.assertEqual(data["count"], 1)
        created = data["results"][0]["created"]
        self.assertIsInstance(created, str)
        self.assertEqual(created, "2026-04-04")
        self.assertEqual(data["provider"], "grep")
        self.assertEqual(data["configured_provider"], "grep")

    def test_provider_is_what_ran_not_what_is_configured(self):
        # A7: no qmd-backed search exists; the label must not claim otherwise.
        self.config.write_text(f"vault_path: {self.vault}\nsearch_provider: qmd\n")
        data = self._run("--query", "marker-token-here")
        self.assertEqual(data["provider"], "grep")
        self.assertEqual(data["configured_provider"], "qmd")
        # GH #9: the note about the unimplemented qmd search is the doctor's (`qmd-search`), printed
        # once there — never on every search
        human = self._proc("--query", "marker-token-here", "--human")
        self.assertNotIn("search_provider is 'qmd'", human.stdout)
        self.assertNotIn("not implemented", human.stdout)
        self.assertIn("Found 1 result(s)", human.stdout)

    def test_vault_override_reports_no_configured_provider(self):
        data = self._run("--query", "marker-token-here", "--vault", str(self.vault),
                         env=clean_env(self.tmpdir))
        self.assertEqual(data["provider"], "grep")
        self.assertIsNone(data["configured_provider"])


# ---------------------------------------------------------------------------
# ConfigResolutionTests — the engine loader is the only config path
# ---------------------------------------------------------------------------

class ConfigResolutionTests(TempVaultMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        write_extract(self.vault / "Sources" / "2026-01-01_aaa", title="Only one", rating=5,
                      domains=["x"], body="needle-in-vault")
        inst_dir = self.tmpdir / "xdg" / "mnemon" / "instances"
        inst_dir.mkdir(parents=True)
        (inst_dir / "tinst.yaml").write_text(f"instance: tinst\nvault_path: {self.vault}\n")

    def test_mnemon_instance_selects_the_instance_file(self):
        out = self._run("--query", "needle", env=clean_env(self.tmpdir, MNEMON_INSTANCE="tinst"))
        self.assertEqual(out["count"], 1)

    def test_vault_override_needs_no_config(self):
        env = clean_env(self.tmpdir, MNEMON_INSTANCE="does-not-exist")
        out = self._run("--query", "needle", "--vault", str(self.vault), env=env)
        self.assertEqual(out["count"], 1)

    def test_missing_instance_exits_2_with_loader_message(self):
        result = self._proc("--query", "needle", env=clean_env(self.tmpdir, MNEMON_INSTANCE="nosuch"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("instances/nosuch.yaml", result.stderr)
        self.assertIn("mnemon init nosuch", result.stderr)

    def test_missing_vault_directory_exits_2(self):
        bad = self.tmpdir / "bad.yaml"
        bad.write_text(f"vault_path: {self.tmpdir}/nowhere\n")
        result = self._proc("--query", "needle", env=clean_env(self.tmpdir, MNEMON_CONFIG=str(bad)))
        self.assertEqual(result.returncode, 2)
        self.assertIn("vault not found", result.stderr)


# ---------------------------------------------------------------------------
# EndToEndTests — full pipeline via subprocess
# ---------------------------------------------------------------------------

class EndToEndTests(TempVaultMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        write_extract(self.vault / "Sources" / "2026-01-01_aaa", title="Inline list source", rating=10,
                      domains=["career", "learning"], domains_format="inline", created="2026-01-01",
                      body="Content mentioning alpha and beta.")
        write_extract(self.vault / "Sources" / "2026-01-02_bbb", title="Block list source", rating=8,
                      domains=["learning", "mc/ai"], domains_format="block", created="2026-01-02",
                      body="Different content mentioning alpha only.")
        write_source(self.vault / "Sources" / "2026-01-03_ccc",
                     body="This phrase lives ONLY in source.md, not extract.md: unique-raw-marker")
        write_extract(self.vault / "Sources" / "2026-01-03_ccc", title="Paired extract", rating=5,
                      domains=["career"], created="2026-01-03", body="Extract mentions alpha in passing.")
        (self.vault / "Synthesis" / "my-thinking.md").write_text(
            "---\ntype: synthesis\ntitle: My own note\n---\n\n"
            "A synthesis note mentioning alpha from the reader's perspective.\n",
            encoding="utf-8",
        )

    def test_happy_path(self):
        titles = [r["title"] for r in self._run("--query", "alpha")["results"]]
        self.assertIn("Inline list source", titles)
        self.assertIn("Block list source", titles)
        self.assertNotIn("My own note", titles)  # synthesis is opt-in

    def test_block_domains_survive_to_json(self):
        out = self._run("--query", "alpha")
        by_title = {r["title"]: r for r in out["results"]}
        self.assertEqual(by_title["Block list source"]["domains"], ["learning", "mc/ai"])

    def test_include_source_flag(self):
        self.assertEqual(self._run("--query", "unique-raw-marker")["count"], 0)
        out_with = self._run("--query", "unique-raw-marker", "--include-source")
        self.assertEqual(out_with["count"], 1)
        hit = out_with["results"][0]
        self.assertEqual(hit["result_type"], "source")
        self.assertEqual(hit["tier"], "full")       # mnemon-search/2: tier = the capture tier (absent = full)
        self.assertEqual(hit["file"], "source.md")
        # metadata comes from the paired extract; the snippet from the raw file
        self.assertEqual(hit["title"], "Paired extract")
        self.assertEqual(hit["domains"], ["career"])
        self.assertEqual(hit["rating"], 5)
        self.assertIn("unique-raw-marker", hit["snippet"])

    def test_source_hit_passes_the_domain_filter_of_its_extract(self):
        out = self._run("--query", "unique-raw-marker", "--include-source", "--domain", "career")
        self.assertEqual(out["count"], 1)
        self.assertEqual(self._run("--query", "unique-raw-marker", "--include-source", "--domain", "home")["count"], 0)

    def test_source_without_paired_extract_keeps_its_own_metadata(self):
        write_source(self.vault / "Sources" / "2026-01-04_ddd", body="lonely-raw-marker here")
        out = self._run("--query", "lonely-raw-marker", "--include-source")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["title"], "source")
        self.assertEqual(out["results"][0]["domains"], [])

    def test_include_synthesis_flag(self):
        self.assertEqual(self._run("--query", "perspective")["count"], 0)
        out_with = self._run("--query", "perspective", "--include-synthesis")
        self.assertEqual(out_with["count"], 1)
        self.assertEqual(out_with["results"][0]["result_type"], "synthesis")
        self.assertIsNone(out_with["results"][0]["tier"])
        self.assertEqual(out_with["schema"], "mnemon-search/2")

    def test_two_synthesis_notes_in_one_folder_both_returned(self):
        (self.vault / "Synthesis" / "second-note.md").write_text(
            "---\ntype: synthesis\ntitle: Second note\nrating: 9\n---\n\nAnother perspective on alpha.\n",
            encoding="utf-8",
        )
        out = self._run("--query", "perspective", "--include-synthesis")
        self.assertEqual(out["count"], 2)
        self.assertEqual([r["title"] for r in out["results"]], ["Second note", "My own note"])

    def test_domain_filter_end_to_end(self):
        out = self._run("--query", "alpha", "--domain", "career")
        self.assertEqual(out["count"], 2)
        for r in out["results"]:
            self.assertIn("career", r["domains"])

    def test_limit_respected(self):
        self.assertLessEqual(self._run("--query", "alpha", "--limit", "2")["count"], 2)

    def test_human_output_does_not_crash_on_fallback(self):
        result = self._proc("--query", "alpha nonexistenttoken", "--human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fallback", result.stdout.lower())
        self.assertIn("alpha", result.stdout.lower())

    def test_no_usable_tokens_exits_2(self):
        result = self._proc("--query", "a !")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
