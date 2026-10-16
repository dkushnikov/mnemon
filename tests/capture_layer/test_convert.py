"""§7.6 (converter order on the fixtures), §7.8 (dated candidates), the capture-text.py CLI and its
`mnemon-capture-text/2` report against the frozen fixture."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import unittest

from _helpers import BIN, HTML_FIX, assert_same_keys, clean_env, expected_shapes, pid_alive, read_fixture, read_json, \
    run_cli, tmpdir, wait_gone, write_stub

from capture import convert

HAVE_PANDOC = bool(shutil.which("pandoc"))
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


def run_fixture(name: str):
    """One fixture through convert(): defuddle injected when the fixture names a candidate, skipped otherwise
    (raw_kind rendered), pandoc hidden when the fixture says so."""
    e = EXPECTED[name]
    raw = read_fixture(name, binary=True)
    env = {"MNEMON_TEST_HOOKS": "1"}
    raw_kind = "rendered"
    if e.get("defuddle"):
        env["MNEMON_TEST_DEFUDDLE_OUTPUT"] = os.path.join(HTML_FIX, e["defuddle"])
        raw_kind = "response"
    else:
        env["MNEMON_TEST_DEFUDDLE_OUTPUT"] = None
    if e.get("without_pandoc"):
        env["PATH"] = "/usr/bin:/bin"
    with _Env(**env):
        return convert.convert(raw, "https://example.com/" + name, raw_kind, e["kind"], None)


class FixtureOutcomes(unittest.TestCase):
    def test_every_fixture(self):
        for name, e in EXPECTED.items():
            if name.startswith("_"):
                continue
            if e.get("converter") == "pandoc" and not HAVE_PANDOC:
                note("pandoc not on PATH: %s judged on the DOM text instead" % name)
            result = run_fixture(name)
            r = result.report
            label = name
            self.assertEqual(r["title"], e["title"], label)
            self.assertEqual(r["completeness"], e["completeness"], label)
            if HAVE_PANDOC or e.get("converter") != "pandoc":
                self.assertEqual(r["converter"], e["converter"], label)
            else:
                self.assertEqual(r["converter"], "dom-text", label)
            by_name = {c["converter"]: c for c in r["candidates"]}
            if "defuddle_missing_min" in e:
                self.assertGreaterEqual(by_name["defuddle"]["missing_count"], e["defuddle_missing_min"], label)
                self.assertFalse(by_name["defuddle"]["chosen"], label)
            if "defuddle_headings_missing_min" in e:
                self.assertGreaterEqual(by_name["defuddle"]["headings_missing_count"], e["defuddle_headings_missing_min"], label)
            if "defuddle_glued_min" in e:
                self.assertGreaterEqual(by_name["defuddle"]["glued_count"], e["defuddle_glued_min"], label)
                self.assertFalse(by_name["defuddle"]["chosen"], label)
            if "pandoc_glued_min" in e and HAVE_PANDOC:
                self.assertGreaterEqual(by_name["pandoc"]["glued_count"], e["pandoc_glued_min"], label)
            for key, sub in (e.get("repairs") or {}).items():
                for k, minimum in sub.items():
                    self.assertGreaterEqual(r["repairs"][key][k], minimum, "%s: repairs.%s.%s" % (label, key, k))
            if HAVE_PANDOC or e.get("converter") != "pandoc":
                for s in e.get("present", []):
                    self.assertIn(s, result.text, "%s: %r expected in the capture-text" % (label, s))
            for s in e.get("absent", []):
                self.assertNotIn(s, result.text, "%s: %r must be absent" % (label, s))
            if "ordered" in e and (HAVE_PANDOC or e.get("converter") != "pandoc"):
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

    def test_timeout_supervises_the_converters(self):
        stubs = tmpdir()
        pidfile = os.path.join(stubs, "pandoc.pid")
        write_stub(stubs, "pandoc", "#!/usr/bin/env bash\n[[ \"${1:-}\" == \"--version\" ]] && { echo pandoc-stub; exit 0; }\n"
                                    "echo $$ > %s\nsleep 30\n" % pidfile)
        env = clean_env({"MNEMON_TEST_DEFUDDLE_OUTPUT": "/nonexistent/candidate.md"})
        env["PATH"] = stubs + os.pathsep + "/usr/bin:/bin"
        rc, text, report, se = self.run_ct("tabbed.html", extra=["--timeout", "1"], env=env)
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["converter"], "dom-text")
        pandoc = [c for c in report["candidates"] if c["converter"] == "pandoc"][0]
        self.assertIn("timed out", pandoc["note"])
        with open(pidfile) as fh:
            pid = int(fh.read().strip())
        self.assertTrue(wait_gone(pid, 3), "the stub pandoc survived the timeout")
        shutil.rmtree(stubs, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
