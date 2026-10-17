"""§7.6 (converter order on the fixtures), §7.8 (dated candidates), the capture-text.py CLI and its
`mnemon-capture-text/2` report against the frozen fixture."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from _helpers import BIN, HTML_FIX, PY, assert_same_keys, clean_env, expected_shapes, pid_alive, read_fixture, read_json, \
    run_cli, tmpdir, wait_gone, write_stub

from capture import convert

HAVE_PANDOC = bool(shutil.which("pandoc"))
HAVE_DEFUDDLE = bool(shutil.which("defuddle"))
EXPECTED = read_json(os.path.join(HTML_FIX, "expected.json"))
CT = os.path.join(BIN, "capture-text.py")


def note(msg: str) -> None:
    sys.stderr.write("  note: %s\n" % msg)


class _Env:
    """os.environ edits scoped to one test (the converter reads the hooks from the environment)."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for k, v in self.values.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_fixture(name: str, mode: str = "rendered"):
    """One fixture through convert(). `injected`: the fixture's canned defuddle candidate (raw_kind
    response); `real`: the defuddle on PATH, nothing injected (raw_kind response); `rendered`: defuddle
    skipped (raw_kind rendered). pandoc is hidden when the fixture says so."""
    e = EXPECTED[name]
    raw = read_fixture(name, binary=True)
    env = {"MNEMON_TEST_HOOKS": "1", "MNEMON_TEST_DEFUDDLE_OUTPUT": None}
    raw_kind = "rendered"
    if mode == "injected":
        env["MNEMON_TEST_DEFUDDLE_OUTPUT"] = os.path.join(HTML_FIX, e["defuddle"])
        raw_kind = "response"
    elif mode == "real":
        raw_kind = "response"
    if e.get("without_pandoc"):
        env["PATH"] = "/usr/bin:/bin"
    with _Env(**env):
        return convert.convert(raw, "https://example.com/" + name, raw_kind, e["kind"], None)


class FixtureOutcomes(unittest.TestCase):
    def test_every_fixture(self):
        """Every fixture in every mode it has: the canned defuddle candidate where one is named, else both the
        real defuddle on a response raw and a rendered raw (defuddle skipped). The outcome fields,
        `completeness`, the `present`/`absent` text and the repairs hold in every mode; `converter` is one of
        the fixture's set (`defuddle|pandoc` where the real defuddle legitimately wins) — G9."""
        for name, e in EXPECTED.items():
            if name.startswith("_"):
                continue
            allowed_all = set(e["converter"].split("|"))
            modes = ["injected"] if e.get("defuddle") else ["rendered", "real"]
            for mode in modes:
                if mode == "real" and not HAVE_DEFUDDLE:
                    note("defuddle not on PATH: %s not judged with the real defuddle" % name)
                    continue
                label = "%s [%s]" % (name, mode)
                allowed = set(allowed_all)
                if mode == "rendered":
                    allowed.discard("defuddle")
                if "pandoc" in allowed and not HAVE_PANDOC:
                    allowed.discard("pandoc")
                    allowed.add("dom-text")
                    note("pandoc not on PATH: %s judged on the DOM text instead" % label)
                result = run_fixture(name, mode)
                r = result.report
                self.assertEqual(r["title"], e["title"], label)
                self.assertEqual(r["completeness"], e["completeness"], label)
                self.assertIn(r["converter"], allowed, label)
                if e["completeness"] == "complete":
                    self.assertEqual((r["missing_count"], r["glued_count"], r["headings_missing_count"]), (0, 0, 0), label)
                by_name = {c["converter"]: c for c in r["candidates"]}
                defuddle_ran = "defuddle" in by_name and by_name["defuddle"]["missing_count"] is not None
                if defuddle_ran:
                    for key, field in (("defuddle_missing_min", "missing_count"), ("defuddle_headings_missing_min", "headings_missing_count"),
                                       ("defuddle_glued_min", "glued_count")):
                        if key in e:
                            self.assertGreaterEqual(by_name["defuddle"][field], e[key], "%s: %s" % (label, key))
                            self.assertFalse(by_name["defuddle"]["chosen"], label)
                if "pandoc_glued_min" in e and HAVE_PANDOC:
                    self.assertGreaterEqual(by_name["pandoc"]["glued_count"], e["pandoc_glued_min"], label)
                for key, sub in (e.get("repairs") or {}).items():
                    for k, minimum in sub.items():
                        self.assertGreaterEqual(r["repairs"][key][k], minimum, "%s: repairs.%s.%s" % (label, key, k))
                expected_tool_won = r["converter"] in allowed_all
                if expected_tool_won:
                    for s in e.get("present", []):
                        self.assertIn(s, result.text, "%s: %r expected in the capture-text" % (label, s))
                for s in e.get("absent", []):
                    self.assertNotIn(s, result.text, "%s: %r must be absent" % (label, s))
                if "ordered" in e and expected_tool_won:
                    positions = [result.text.index(s) for s in e["ordered"]]
                    self.assertEqual(positions, sorted(positions), label)
                if "published_candidates" in e:
                    self.assertEqual(r["published_candidates"], e["published_candidates"], label)
                if "authors" in e:
                    self.assertEqual(r["authors"], e["authors"], label)
                if "eligible_min" in e:
                    self.assertGreaterEqual(r["eligible_count"], e["eligible_min"], label)
                self.assertGreater(r["bytes_per_word"], 0, label)
                if name != "cjk.html":      # whitespace words: a CJK page is "bloated" by construction, flagged, never acted on
                    self.assertFalse(r["bloat"], label)

    def test_math_is_compared_and_published_as_its_tex_source(self):
        """G15: a LaTeXML formula is `$<alttext>$` in the reference, in every candidate and in the published
        text — pandoc passes the check (the keys agree on the source's letters) and carries the `$…$` through
        as text (tex_math_dollars off), so the tables of a paper are not lost to the DOM-text fallback."""
        from capture import check, sanitize
        raw = read_fixture("math.html", binary=True)
        sanitized, repairs = sanitize.html(raw.decode("utf-8"), "arxiv")
        self.assertEqual(repairs["math_sources"], 4)
        reference = convert.dom_text(convert.content_subtree(convert.parse(sanitized), "arxiv"), reference=True)
        line = [ln for ln in reference.split("\n") if ln.startswith("The underlying model")][0]
        self.assertIn("$\\theta$", line)
        self.assertIn("theta", check.line_key(line), "the reference key carries the source's letters")
        self.assertEqual(check.classify_line(line, None), "body")
        result = run_fixture("math.html", "injected")
        r = result.report
        self.assertEqual(r["repairs"]["html"]["math_sources"], 4)
        self.assertIn("$\\theta$", result.text)
        self.assertNotIn("\\$", result.text, "the dollar signs are not escaped")
        self.assertIn("$$A", result.text)
        if HAVE_PANDOC:
            self.assertEqual(r["converter"], "pandoc")
            pandoc = [c for c in r["candidates"] if c["converter"] == "pandoc"][0]
            self.assertEqual((pandoc["missing_count"], pandoc["glued_count"], pandoc["chosen"]), (0, 0, True))
            self.assertEqual(r["inventory"]["tables"], {"html": 1, "md": 1}, "the equation table survives")
        self.assertEqual(r["completeness"], "complete")

    def test_dom_text_is_the_fallback_of_last_resort_never_a_failure(self):
        """G17: when no candidate passes the check — the DOM text included — the DOM text is published as
        partial with the counts and examples of its own check and the note `dom-text failed its own check`;
        the "reference is empty" error is raised only when there is no text at all, with the candidates
        recorded."""
        from capture import check
        raw = read_fixture("cookie.html", binary=True)
        failing = check.Conv(["a line the check cannot find in any candidate"], [], ["Heading"], 12, 5)
        with mock.patch.object(check, "conversion", return_value=failing):
            result = convert.convert(raw, "https://example.com/c", "rendered", "page", None)
        r = result.report
        self.assertEqual((r["converter"], r["completeness"]), ("dom-text", "partial"))
        self.assertEqual((r["missing_count"], r["glued_count"], r["headings_missing_count"]), (1, 0, 1))
        self.assertEqual(r["missing_examples"], ["a line the check cannot find in any candidate", "Heading"])
        dom = [c for c in r["candidates"] if c["converter"] == "dom-text"][0]
        self.assertEqual((dom["chosen"], dom["note"], dom["missing_count"]), (True, "dom-text failed its own check", 1))
        self.assertFalse(any(c["chosen"] for c in r["candidates"] if c["converter"] != "dom-text"))
        self.assertIn("The article text is the only content", result.text)
        empty = b"<html><head><title>Empty</title></head><body><nav>menu only</nav></body></html>"
        with self.assertRaises(convert.ConvertError) as ctx:
            convert.convert(empty, "https://example.com/e", "rendered", "page", None)
        self.assertEqual((ctx.exception.error_class, str(ctx.exception)), ("convert", "no candidate produced text (reference is empty)"))
        self.assertEqual([c["converter"] for c in ctx.exception.candidates], ["defuddle", "pandoc", "dom-text"])
        self.assertEqual(ctx.exception.candidates[-1]["note"], "empty after sanitation")

    def test_rendered_site_external_raws_never_run_defuddle(self):
        raw = read_fixture("cookie.html", binary=True)
        for raw_kind in ("rendered", "site", "external"):
            with _Env(MNEMON_TEST_HOOKS="1", MNEMON_TEST_DEFUDDLE_OUTPUT=os.path.join(HTML_FIX, "mojibake.defuddle.md")):
                r = convert.convert(raw, "https://example.com/c", raw_kind, "page", None).report
            self.assertEqual(r["candidates"][0]["converter"], "defuddle")
            self.assertEqual(r["candidates"][0]["note"], "skipped: %s raw" % raw_kind)
            self.assertNotEqual(r["converter"], "defuddle")

    def test_mojibake_candidate_discarded(self):
        raw = read_fixture("dropped-section.html", binary=True)
        with _Env(MNEMON_TEST_HOOKS="1", MNEMON_TEST_DEFUDDLE_OUTPUT=os.path.join(HTML_FIX, "mojibake.defuddle.md")):
            r = convert.convert(raw, "https://example.com/d", "response", "page", None).report
        self.assertEqual(r["candidates"][0]["note"], "discarded: mojibake")
        self.assertNotEqual(r["converter"], "defuddle")

    def test_meta_charset_is_a_bounded_scan(self):
        self.assertEqual(convert.meta_charset(b'<html><META http-equiv="x" CHARSET = "Windows-1252"><p>x</p>'), "windows-1252")
        self.assertEqual(convert.meta_charset(b'<meta name="viewport" content="w"><meta charset=utf-8>'), "utf-8")
        self.assertIsNone(convert.meta_charset(b"<metal charset=x>"))
        t0 = time.monotonic()
        self.assertIsNone(convert.meta_charset(b"<meta " * 100000), "a <meta that never ends ends the search")
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_fetch_report_charset_used_when_bytes_declare_none(self):
        raw = ("<html><head><title>Caf\xe9</title></head><body><p>Un caf\xe9 avec plus de six mots dedans "
               "pour le test.</p></body></html>").encode("windows-1252")
        with self.assertRaises(convert.ConvertError) as ctx:
            convert.convert(raw, "https://example.com/x", "rendered", "page", None)
        self.assertEqual(ctx.exception.error_class, "invalid-utf8")
        result = convert.convert(raw, "https://example.com/x", "rendered", "page", {"charset": "windows-1252"})
        self.assertIn("café", result.text)
        self.assertEqual(result.report["title"], "Café")

    def test_fetch_report_facts_travel_into_the_report(self):
        raw = read_fixture("cookie.html", binary=True)
        fr = {"method": "direct", "charset": "utf-8", "kind": "page",
              "acquisition": {"outcome": "thin", "kind": "thin", "evidence": "visible words 20 < 60", "visible_words": 20},
              "metadata": {"title": None, "authors": ["Someone"], "published": "2026/01/02", "published_source": "citation_date",
                           "arxiv_id": "2601.00001", "arxiv_version": 2, "arxiv_requested_version": None,
                           "declared_canonical_url": None}}
        r = convert.convert(raw, "https://example.com/c", "response", "page", fr).report
        self.assertEqual(r["acquisition"], "thin")
        self.assertEqual(r["authors"], ["Someone"])
        self.assertEqual((r["arxiv_id"], r["arxiv_version"], r["arxiv_requested_version"]), ("2601.00001", 2, None))
        self.assertEqual(r["published_candidates"][0], {"value": "2026-01-02", "source": "citation_date"})
        self.assertTrue(r["method"].startswith("direct+"))

    def test_arxiv_title_comes_from_the_metadata(self):
        """G18: a LaTeXML <title> glues words; for kind arxiv the fetch report's citation_title wins over
        find_title; a page keeps its own <title> first and falls back to the metadata only without one."""
        raw = ("<html><head><title>Become FaultyWhen Updated by LLMs</title></head><body><article class=\"ltx_document\">"
               "<h1>Become Faulty When Updated by LLMs</h1><p>%s</p></article></body></html>" % " ".join("w%d" % i for i in range(80))).encode()
        fr = {"method": "direct", "charset": "utf-8", "kind": "arxiv", "acquisition": {"outcome": "ok"},
              "metadata": {"title": "  Become Faulty  When Updated by LLMs ", "authors": []}}
        self.assertEqual(convert.convert(raw, "https://arxiv.org/abs/2605.00001", "response", "arxiv", fr).report["title"],
                         "Become Faulty When Updated by LLMs")
        self.assertEqual(convert.convert(raw, "https://example.com/p", "response", "page", fr).report["title"],
                         "Become FaultyWhen Updated by LLMs", "a page's own <title> stays first")
        self.assertEqual(convert.convert(raw, "https://arxiv.org/abs/2605.00001", "response", "arxiv", None).report["title"],
                         "Become FaultyWhen Updated by LLMs", "...and without metadata the <title> is all there is")

    def test_dom_text_projection(self):
        root = convert.parse("<html><body><h1>Title here</h1><p>Deploy<span>the</span>company brain</p>"
                             "<pre>a\n  b</pre><ul><li>one</li><li>two</li></ul><p>x<br>y</p><h4>small</h4></body></html>")
        sub = convert.content_subtree(root, "page")
        self.assertEqual(convert.dom_text(sub), "## Title here\nDeploythecompany brain\na\nb\none\ntwo\nx\ny\nsmall",
                         "the published text keeps the page's own word boundaries")
        soft = convert.SOFT
        self.assertEqual(convert.dom_text(sub, reference=True),
                         "## Title here\nDeploy%sthe%scompany brain\na\nb\none\ntwo\nx\ny\nsmall" % (soft, soft),
                         "the reference marks whitespace-free inline boundaries")
        spaced = convert.parse("<p><span>Deploy</span> <span>the</span> <span>brain</span></p><p>a <b>b</b> c<i>d</i></p>")
        self.assertEqual(convert.dom_text(spaced, reference=True), "Deploy the brain\na b c%sd" % soft,
                         "real whitespace stays a space; a SOFT next to whitespace is just whitespace")
        self.assertEqual(convert.dom_text(spaced), "Deploy the brain\na b cd")

    def test_subtree_removals_and_arxiv_root(self):
        root = convert.parse("<html><head><title>t</title></head><body><nav>n</nav><header>h</header><form>f</form>"
                             "<div class='cookie-consent'>c</div><section id='comments'>cm</section><footer>ft</footer>"
                             "<article class='ltx_document'><p>paper</p></article><template>tp</template><noscript>ns</noscript></body></html>")
        self.assertEqual(convert.dom_text(convert.content_subtree(convert.parse(convert.to_html(root)), "page")), "h\npaper")
        self.assertEqual(convert.dom_text(convert.content_subtree(root, "arxiv")), "paper")

    def test_container_pruning_never_inside_code_and_by_whole_tokens(self):
        root = convert.parse("<html><body><pre><code><span class='token comment'># a comment token</span>\nx = 1</code></pre>"
                             "<p>The <span class='comment'>inline span named comment</span> stays.</p>"
                             "<div class='comment-count'>3</div><div class='comments'><p>reader</p></div>"
                             "<section class='nocomments'>kept</section></body></html>")
        text = convert.dom_text(convert.content_subtree(root, "page"))
        self.assertIn("# a comment token", text)
        self.assertIn("inline span named comment", text)
        self.assertIn("3", text)
        self.assertIn("kept", text)
        self.assertNotIn("reader", text)


class CaptureTextCLI(unittest.TestCase):
    def setUp(self):
        self.dir = tmpdir()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_ct(self, raw_name, extra=(), env=None, raw_bytes=None):
        raw = os.path.join(self.dir, "raw.html")
        with open(raw, "wb") as fh:
            fh.write(raw_bytes if raw_bytes is not None else read_fixture(raw_name, binary=True))
        out, rep = os.path.join(self.dir, "capture.md"), os.path.join(self.dir, "report.json")
        rc, so, se = run_cli([CT, raw, "--url", "https://example.com/p", "--out", out, "--report", rep] + list(extra), env=env)
        report = text = None
        if os.path.exists(rep):
            with open(rep, encoding="utf-8") as fh:
                report = json.load(fh)
        if os.path.exists(out):
            with open(out, "rb") as fh:
                text = fh.read()
        return rc, text, report, se

    def test_report_matches_the_frozen_fixture_shape(self):
        env = clean_env({"MNEMON_TEST_DEFUDDLE_OUTPUT": os.path.join(HTML_FIX, "dropped-section.defuddle.md")})
        rc, text, report, se = self.run_ct("dropped-section.html", env=env)
        self.assertEqual(rc, 0, se)
        fixture = expected_shapes("text.json")
        assert_same_keys(self, report, fixture, "mnemon-capture-text/2")
        assert_same_keys(self, report["inventory"], fixture["inventory"], "inventory")
        assert_same_keys(self, report["repairs"]["html"], fixture["repairs"]["html"], "repairs.html")
        assert_same_keys(self, report["repairs"]["markdown"], fixture["repairs"]["markdown"], "repairs.markdown")
        assert_same_keys(self, report["candidates"][0], fixture["candidates"][0], "candidates[0]")
        assert_same_keys(self, report["tools"], fixture["tools"], "tools", nullable=("defuddle", "pandoc"))
        self.assertEqual(report["schema"], "mnemon-capture-text/2")
        self.assertEqual(report["acquisition"], "unknown")
        self.assertIn("Safety is paramount", text.decode("utf-8"))
        self.assertTrue(report["method"].startswith("curl+"), "the phase-1 fetch label without a fetch report")
        self.assertEqual(report["limits"][0], fixture["limits"][0])

    def test_phase1_invocation_and_stub_like_page(self):
        raw = b"<html><head><title> Stub &amp; Page\n  Title </title></head><body><h1>Hello</h1><p>World <b>bold</b> from curl.</p></body></html>"
        stubs = tmpdir()
        write_stub(stubs, "defuddle", '#!/usr/bin/env bash\n[[ "${1:-}" == "--version" ]] && { echo "0.0-stub"; exit 0; }\nprintf \'## Hello\\n\\nWorld **bold** from defuddle.\\n\'\n')
        env = clean_env(hooks=False)
        env["PATH"] = stubs + os.pathsep + env["PATH"]
        rc, text, report, se = self.run_ct(None, env=env, raw_bytes=raw)
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["method"], "curl+defuddle")
        self.assertEqual(report["title"], "Stub & Page Title")
        self.assertEqual(report["completeness"], "unknown", "no eligible line → unknown")
        self.assertEqual(report["tool"], {"name": "defuddle", "version": "0.0-stub"})
        self.assertIn(b"from defuddle", text)
        shutil.rmtree(stubs, ignore_errors=True)

    def test_rendered_legacy_path(self):
        rendered = os.path.join(self.dir, "rendered.txt")
        with open(rendered, "w") as fh:
            fh.write("rendered text content\n\nsecond paragraph\n")
        rc, text, report, se = self.run_ct("cookie.html", extra=["--rendered", rendered])
        self.assertEqual(rc, 0, se)
        self.assertEqual(text, b"rendered text content\n\nsecond paragraph\n")
        self.assertEqual((report["method"], report["converter"], report["completeness"]), ("render-text", "render-text", "unknown"))
        self.assertIsNone(report["tool"])

    def test_empty_page_is_convert_exit_1(self):
        rc, text, report, se = self.run_ct(None, raw_bytes=b"<html><head><title>Empty</title></head><body></body></html>",
                                           env=clean_env({"MNEMON_TEST_DEFUDDLE_OUTPUT": "/nonexistent/candidate.md"}))
        self.assertEqual(rc, 1)
        self.assertIsNone(text)
        self.assertEqual(report["error_class"], "convert")
        self.assertIn("convert", se)
        self.assertEqual([c["converter"] for c in report["candidates"]], ["defuddle", "pandoc", "dom-text"],
                         "the failure report records what was tried (G17)")

    def test_invalid_bytes_and_fetch_report_charset(self):
        raw = "<html><body><p>Un caf\xe9 avec plus de six mots dedans pour le test.</p></body></html>".encode("windows-1252")
        rc, text, report, se = self.run_ct(None, raw_bytes=raw, extra=["--raw-kind", "rendered"])
        self.assertEqual(rc, 1)
        self.assertEqual(report["error_class"], "invalid-utf8")
        fr = os.path.join(self.dir, "fetch.json")
        with open(fr, "w") as fh:
            json.dump({"schema": "mnemon-fetch/1", "method": "direct", "charset": "windows-1252", "kind": "page",
                       "acquisition": {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 12}, "metadata": {}}, fh)
        rc, text, report, se = self.run_ct(None, raw_bytes=raw, extra=["--raw-kind", "rendered", "--fetch-report", fr])
        self.assertEqual(rc, 0, se)
        self.assertIn("café".encode("utf-8"), text)
        self.assertEqual(report["acquisition"], "ok")
        self.assertTrue(report["method"].startswith("direct+"))

    def test_deep_nesting_yields_a_report_not_a_traceback(self):
        """G8: 20 000 nested <div> and 6 000 never-closed <table><tr><td> convert into a report within 5 s —
        the walks are iterative and the tree is depth-capped; the external converters run under --timeout,
        so a tool that chokes on the shape is skipped, never waited for."""
        deep_divs = ("<html><head><title>Deep</title></head><body>" + "<div>" * 20000
                     + "<p>A paragraph nested twenty thousand levels deep with more than six words.</p>"
                     + "</div>" * 20000 + "</body></html>")
        cells = ("<html><head><title>Cells</title></head><body>" + "<table><tr><td>" * 6000
                 + "A cell nested six thousand tables deep with more than six words in it." + "</body></html>")
        for label, raw in (("divs", deep_divs), ("cells", cells)):
            t0 = time.monotonic()
            rc, text, report, se = self.run_ct(None, raw_bytes=raw.encode("utf-8"), extra=["--raw-kind", "rendered", "--timeout", "2"])
            secs = time.monotonic() - t0
            self.assertLess(secs, 5.0, "%s: %.2f s" % (label, secs))
            self.assertNotIn("Traceback", se, label)
            self.assertIsNotNone(report, label)
            self.assertEqual(rc, 0, "%s: %s" % (label, se))
            self.assertIn(b"more than six words", text, label)
            self.assertIsNone(report["retryable"], label)
        root = convert.parse("<div>" * 20000 + "<p>deep</p>" + "</div>" * 20000)
        depth, n = 0, root
        while n.children and not n.children[0].is_text:
            depth += 1
            n = n.children[0]
        self.assertLessEqual(depth, convert.MAX_DEPTH + 1, "the builder caps the tree's depth")
        self.assertIn("deep", convert.dom_text(root), "...and keeps the text of the flattened subtree")

    def test_recursion_error_is_a_permanent_nesting_failure(self):
        """A RecursionError never escapes convert(): it is `convert` / `nesting`, retryable false (G8)."""
        raw = read_fixture("cookie.html", binary=True)
        with mock.patch.object(convert, "content_subtree", side_effect=RecursionError("deep")):
            with self.assertRaises(convert.ConvertError) as ctx:
                convert.convert(raw, "https://example.com/c", "rendered", "page", None)
        self.assertEqual((ctx.exception.error_class, str(ctx.exception), ctx.exception.retryable), ("convert", "nesting", False))
        # the CLI on a JSON-LD document nested past the interpreter's limit: python 3.9's json raises
        # RecursionError, which is the nesting report (exit 1, no traceback); a newer json decodes the
        # document iteratively and the page converts — either way a report, never a traceback
        raw = ('<html><head><title>Ld</title><script type="application/ld+json">' + "[" * 100000 + "]" * 100000
               + '</script></head><body><p>Body text with more than six words in it.</p></body></html>').encode("utf-8")
        t0 = time.monotonic()
        rc, text, report, se = self.run_ct(None, raw_bytes=raw, extra=["--raw-kind", "rendered", "--timeout", "2"])
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertNotIn("Traceback", se)
        self.assertIn(rc, (0, 1), se)
        if rc == 1:
            self.assertEqual((report["error_class"], report["error"], report["retryable"]), ("convert", "nesting", False))
            self.assertIsNone(text)
            self.assertIn("nesting", se)
        else:
            self.assertIsNone(report["retryable"])
        if sys.version_info[:2] == (3, 9):
            self.assertEqual(rc, 1, "on the legacy interpreter json.loads raises RecursionError: the nesting report")

    def test_the_supervisor_kills_a_hung_worker(self):
        """G25: the conversion runs in a worker process (`--_worker`, its own process group) that the
        supervisor kills at --timeout: exit 124, the report `timeout` / stage `convert` / retryable false,
        nothing at --out, no worker left. The hang is CPU-bound C code no signal handler interrupts (the
        shape of a quadratic scan), so only the kill ends it."""
        raw = os.path.join(self.dir, "raw.html")
        env = clean_env({"MNEMON_TEST_CONVERT_HANG": "1"})
        t0 = time.monotonic()
        rc, text, report, se = self.run_ct("cookie.html", extra=["--raw-kind", "rendered", "--timeout", "1"], env=env)
        secs = time.monotonic() - t0
        self.assertEqual(rc, 124, se)
        self.assertLess(secs, 10.0, "%.1f s" % secs)
        self.assertIsNone(text, "nothing at --out")
        self.assertEqual((report["error_class"], report["stage"], report["retryable"]), ("timeout", "convert", False))
        self.assertEqual(report["error"], "conversion exceeded convert_timeout=1")
        self.assertIn("worker killed with its process group", se)
        ps = subprocess.run(["pgrep", "-f", "capture-text.py --_worker %s" % raw], capture_output=True)
        self.assertEqual(ps.stdout.strip(), b"", "no worker of this raw is left")
        rc, text, report, se = self.run_ct("cookie.html", extra=["--raw-kind", "rendered", "--timeout", "30"],
                                           env=clean_env({"MNEMON_TEST_CONVERT_HANG": "1"}, hooks=False))
        self.assertEqual(rc, 0, se)
        self.assertIn("MNEMON_TEST_CONVERT_HANG ignored", se, "the hook is ignored, with a warning, when the test hooks are off")
        self.assertIsNone(report["stage"])
        helptext = subprocess.run([PY, CT, "--help"], capture_output=True).stdout.decode("utf-8", "replace")
        self.assertNotIn("_worker", helptext, "the worker flag is hidden")
        self.assertIn("convert_timeout in seconds (default 120)", helptext)

    def test_the_worker_does_not_outlive_a_sigkilled_supervisor(self):
        """The worker polls its parent (proc.watch_parent): when the supervisor is SIGKILLed — no SIGTERM
        handler runs, and the worker in its own session would be orphaned — the worker is gone within 2 s.
        The hang is the hook's `sleep` mode, interruptible python code where the watch thread runs; the C-code
        hang of proc.hang is the supervisor's kill to end (the test above), not the watch's."""
        raw = os.path.join(self.dir, "raw.html")
        shutil.copy(os.path.join(HTML_FIX, "cookie.html"), raw)
        out, rep = os.path.join(self.dir, "orphan.md"), os.path.join(self.dir, "orphan.json")
        env = clean_env({"MNEMON_TEST_CONVERT_HANG": "sleep"})
        sup = subprocess.Popen([PY, CT, raw, "--url", "https://example.com/orphan", "--out", out, "--report", rep,
                                "--raw-kind", "rendered", "--timeout", "120"], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker_pid = None
        try:
            end = time.monotonic() + 15
            while worker_pid is None and time.monotonic() < end:
                ps = subprocess.run(["pgrep", "-f", "capture-text.py --_worker %s" % raw], capture_output=True)
                pids = [int(p) for p in ps.stdout.split()]
                if pids:
                    worker_pid = pids[0]
                else:
                    time.sleep(0.05)
            self.assertIsNotNone(worker_pid, "the worker did not start")
            self.assertIsNone(sup.poll(), "the supervisor is still waiting on its worker")
            os.kill(sup.pid, signal.SIGKILL)
            sup.wait(timeout=5)
            # the bound: 2 s on a developer machine (the watch polls every 0.25 s). The shared CI runners
            # missed it three runs out of three on macos-15 (process teardown under load), so under CI the
            # bound is 10 s — the property is "the worker dies on its own", not "within 2 s on a loaded VM"
            bound = 10.0 if os.environ.get("CI") else 2.0
            self.assertTrue(wait_gone(worker_pid, bound),
                            "the worker outlived its SIGKILLed supervisor by more than %g s" % bound)
            self.assertFalse(os.path.exists(out))
        finally:
            if worker_pid is not None and pid_alive(worker_pid):
                try:
                    os.killpg(worker_pid, signal.SIGKILL)
                except OSError:
                    pass
            if sup.poll() is None:
                sup.kill()

    def test_bounded_markdown_regexes_are_linear(self):
        """G26: every regex applied to candidate markdown uses bounded, nesting-free character classes, so a
        page of 200 KB of `[`, of `](` or of `<a` converts in under 2 s on either interpreter — the
        old `\\[([^\\]]*)\\]` rescanned to the end of the line from every bracket (quadratic: a 102 KB page of
        `[` held the vault lock 163 s)."""
        from capture import check, sanitize
        shapes = {"[": "[" * 200000, "](": "](" * 100000, "<a": "<a " * 66000}
        for label, text in shapes.items():
            raw = ("<html><head><title>Hostile</title></head><body><p>%s</p><p>A paragraph with more than six words after it.</p>"
                   "</body></html>" % text.replace("<", "&lt;")).encode("utf-8")
            t0 = time.monotonic()
            rc, out, report, se = self.run_ct(None, raw_bytes=raw, extra=["--raw-kind", "rendered", "--timeout", "30"])
            secs = time.monotonic() - t0
            self.assertEqual(rc, 0, "%s: %s" % (label, se))
            self.assertLess(secs, 2.0, "%s: %.2f s" % (label, secs))
            self.assertIn(b"A paragraph with more than six words after it.", out, label)
            self.assertEqual(report["completeness"], "complete", label)
        # the pure functions, on the candidate side (a markdown candidate that is all brackets, parens or tags)
        for label, md in (("[", "[" * 200000), ("](", "](" * 100000), ("<a", "<a " * 66000), ("![", "![" * 100000),
                          ("[]", "[]" * 100000), ("(", "(" * 200000)):
            t0 = time.monotonic()
            check.candidate_text(md)
            check.markdown_counts(md)
            sanitize.markdown(md, "page")
            self.assertLess(time.monotonic() - t0, 2.0, "%s: candidate_text + markdown_counts + sanitize.markdown" % label)
        # the bounded shapes still match what they must
        self.assertEqual(check.candidate_text("see [t](u) and ![a](i \"t\") and [r][1] and <https://h/x>\n\n[1]: https://h/y\n").split(),
                         ["see", "t", "and", "a", "and", "r", "and", "https://h/x"])
        md, counts = sanitize.markdown("![F](data:image/png;base64,%s)\n\n![H](/_next/image?url=%%2Fa.png&w=1)\n" % ("A" * 300000), "page")
        self.assertEqual((counts["data_uris"], counts["next_images"]), (1, 1), "a 300 KB base64 figure is still repaired")
        self.assertNotIn("data:", md)

    def test_timeout_supervises_the_converters(self):
        stubs = tmpdir()
        pidfile = os.path.join(stubs, "pandoc.pid")
        write_stub(stubs, "pandoc", "#!/usr/bin/env bash\n[[ \"${1:-}\" == \"--version\" ]] && { echo pandoc-stub; exit 0; }\n"
                                    "echo $$ > %s\nsleep 30\n" % pidfile)
        env = clean_env({"MNEMON_TEST_DEFUDDLE_OUTPUT": "/nonexistent/candidate.md"})
        env["PATH"] = stubs + os.pathsep + "/usr/bin:/bin"
        # inside the worker a tool gets the remaining budget minus a reserve (G25), so the stuck pandoc is
        # killed at 1.5 s of a 2 s budget and the DOM text, which needs no tool, is still published
        rc, text, report, se = self.run_ct("tabbed.html", extra=["--timeout", "2"], env=env)
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["converter"], "dom-text")
        pandoc = [c for c in report["candidates"] if c["converter"] == "pandoc"][0]
        self.assertIn("timed out", pandoc["note"])
        self.assertEqual(convert.tool_deadline(None), None)
        self.assertAlmostEqual(convert.tool_deadline(convert.proc.Deadline(2)).remaining(), 1.5, delta=0.1)
        self.assertAlmostEqual(convert.tool_deadline(convert.proc.Deadline(120)).remaining(), 110, delta=0.5)
        with open(pidfile) as fh:
            pid = int(fh.read().strip())
        self.assertTrue(wait_gone(pid, 3), "the stub pandoc survived the timeout")
        shutil.rmtree(stubs, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
