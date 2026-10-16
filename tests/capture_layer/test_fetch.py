"""§7.3/§7.4 (the chain with a local server and stubs), §7.9 (the arXiv fetch half), §7.11 (renderer and
acquire timeouts, the A9 teardown), `--expect pdf|audio`, the vendor adapters on recorded responses (A11),
and the mnemon-fetch/1 report against the frozen fixtures."""
from __future__ import annotations

import json
import os
import shutil
import signal
import struct
import subprocess
import time
import unittest

from _helpers import BIN, LocalServer, PY, assert_same_keys, clean_env, expected_shapes, pid_alive, run_cli, tmpdir, \
    wait_gone, write_stub

from capture import fetch as F

CF = os.path.join(BIN, "capture-fetch.py")
WORDS = " ".join("word%d" % i for i in range(300))
RICH = "<html><head><title>Rich page</title><link rel=\"canonical\" href=\"https://example.com/rich\"></head><body><h1>Rich</h1><p>%s</p></body></html>" % WORDS


def wav_bytes() -> bytes:
    data = b"\x00\x00" * 100
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


class FetchBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer()
        cls.stubs = tmpdir("mnemon-stubs-")
        cls.render_ok = write_stub(cls.stubs, "render-ok.sh", "#!/usr/bin/env bash\nprintf '%s'\n" % RICH.replace("Rich page", "Rendered page").replace("'", ""))
        cls.render_thin = write_stub(cls.stubs, "render-thin.sh", "#!/usr/bin/env bash\nprintf '<html><body><p>rendered but thin</p></body></html>'\n")
        cls.render_fail = write_stub(cls.stubs, "render-fail.sh", "#!/usr/bin/env bash\necho 'ERROR: rendered output suspiciously small' >&2\nexit 11\n")
        cls.render_nochrome = write_stub(cls.stubs, "render-nochrome.sh", "#!/usr/bin/env bash\necho 'ERROR: Chrome/Chromium not found.' >&2\nexit 10\n")
        cls.render_wall = write_stub(cls.stubs, "render-wall.sh", "#!/usr/bin/env bash\nprintf '<html><body><p>Just a moment...</p></body></html>'\n")
        cls.render_login = write_stub(cls.stubs, "render-login.sh", "#!/usr/bin/env bash\nprintf '<html><body><p>Please sign in to continue</p></body></html>'\n")
        cls.external = write_stub(cls.stubs, "external.sh",
                                  "#!/usr/bin/env bash\necho \"$1 $2\" >> \"$EXT_LOG\"\n"
                                  "case \"${EXT_MODE:-ok}\" in\n  ok) printf '%s' ;;\n  wall) printf '<html><body><p>Verify you are human</p></body></html>' ;;\n"
                                  "  login) printf '<html><body><p>Log in to view this</p></body></html>' ;;\n  fail) exit 3 ;;\nesac\n" % RICH.replace("Rich page", "Vendor page").replace("'", ""))

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        shutil.rmtree(cls.stubs, ignore_errors=True)

    def setUp(self):
        self.server.reset()
        self.server.routes.clear()
        self.dir = tmpdir()
        self.out = os.path.join(self.dir, "raw.html")
        self.rep = os.path.join(self.dir, "fetch.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fetch(self, path, extra=(), render=None, env_extra=None, url=None):
        for stale in (self.out, self.rep):
            if os.path.exists(stale):
                os.unlink(stale)
        env = clean_env(env_extra)
        env["RENDER_URL_CMD"] = render or self.render_fail
        argv = [CF, url or self.server.url(path), "--out", self.out, "--report", self.rep, "--acquire-timeout", "30",
                "--render-timeout", "10"] + list(extra)
        rc, so, se = run_cli(argv, env=env)
        report = None
        if os.path.exists(self.rep):
            with open(self.rep, encoding="utf-8") as fh:
                report = json.load(fh)
        raw = None
        if os.path.exists(self.out):
            with open(self.out, "rb") as fh:
                raw = fh.read()
        return rc, report, raw, se

    def stages(self, report):
        return [(s["stage"], s["outcome"]) for s in report["stages"]]


class Chain(FetchBase):
    def test_ok_one_stage_and_the_report_shape(self):
        self.server.add("/ok", RICH)
        rc, report, raw, se = self.fetch("/ok")
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "ok")])
        self.assertEqual((report["method"], report["raw_kind"], report["http_status"], report["egress"]), ("direct", "response", 200, "direct"))
        self.assertEqual(raw, RICH.encode("utf-8"))
        self.assertEqual(report["final_url"], self.server.url("/ok"))
        self.assertEqual(report["metadata"]["title"], "Rich page")
        self.assertEqual(report["metadata"]["declared_canonical_url"], "https://example.com/rich")
        self.assertEqual(report["acquisition"], {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 301})
        self.assertEqual((report["charset"], report["content_type"], report["bytes"]), ("utf-8", "text/html", len(raw)))
        fixture = expected_shapes("fetch-ok.json")
        assert_same_keys(self, report, fixture, "mnemon-fetch/1")
        assert_same_keys(self, report["stages"][0], fixture["stages"][0], "stages[0]")
        assert_same_keys(self, report["acquisition"], fixture["acquisition"], "acquisition")
        assert_same_keys(self, report["metadata"], fixture["metadata"], "metadata")
        assert_same_keys(self, report["hops"][0], fixture["hops"][0], "hops[0]")
        assert_same_keys(self, report["policy"], fixture["policy"], "policy")
        self.assertEqual(report["hops"][0]["address"], "127.0.0.1")

    def test_403_with_marker_renders(self):
        self.server.add("/wall", "<html><body><h1>Access Denied</h1></body></html>", status=403)
        rc, report, raw, se = self.fetch("/wall", render=self.render_ok)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "bot-wall"), ("render", "ok")])
        self.assertEqual((report["method"], report["raw_kind"], report["http_status"], report["final_url"]), ("render", "rendered", None, None))
        self.assertIn(b"Rendered page", raw)
        self.assertEqual(report["acquisition"]["outcome"], "ok")

    def test_js_shell_renderer_failing_external_off_is_environment(self):
        self.server.add("/app", '<html><body><div id="root"></div><script src="/app.js"></script></body></html>')
        rc, report, raw, se = self.fetch("/app", render=self.render_fail)
        self.assertEqual(rc, 3, se)
        self.assertEqual(report["error_class"], "environment")
        self.assertEqual(self.stages(report), [("direct", "js-shell"), ("render", "failed")])
        self.assertIsNone(raw)
        rc, report, raw, se = self.fetch("/app", render=self.render_nochrome)
        self.assertEqual(rc, 3, se)
        self.assertEqual(self.stages(report), [("direct", "js-shell"), ("render", "unavailable")])
        self.assertEqual(report["acquisition"]["kind"], "js-shell")

    def test_external_with_key_and_stub_vendor(self):
        self.server.add("/app", '<html><body><div id="root"></div><script src="/app.js"></script></body></html>')
        log = os.path.join(self.dir, "ext.log")
        env = {"MNEMON_EXTERNAL_FETCH_CMD": self.external, "EXT_LOG": log, "FIRECRAWL_API_KEY": "k"}
        rc, report, raw, se = self.fetch("/app", ["--external", '{"mode": "auto", "fetchers": ["firecrawl", "jina"]}'],
                                         render=self.render_fail, env_extra=env)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "js-shell"), ("render", "failed"), ("external:firecrawl", "ok")])
        self.assertEqual((report["method"], report["raw_kind"], report["egress"], report["http_status"]), ("firecrawl", "response", "external", None))
        self.assertIn(b"Vendor page", raw)
        with open(log) as fh:
            self.assertEqual(fh.read().strip(), "firecrawl " + self.server.url("/app"))

    def test_external_without_key_skipped_and_no_external_never_called(self):
        self.server.add("/app", '<html><body><div id="root"></div><script src="/app.js"></script></body></html>')
        log = os.path.join(self.dir, "ext.log")
        env = {"MNEMON_EXTERNAL_FETCH_CMD": self.external, "EXT_LOG": log}
        rc, report, raw, se = self.fetch("/app", ["--external", '{"mode": "auto", "fetchers": ["firecrawl", "jina"]}'], env_extra=env)
        self.assertEqual(rc, 3, se)
        notes = [s["note"] for s in report["stages"] if s["stage"].startswith("external")]
        self.assertEqual(len(notes), 2)
        self.assertTrue(all("no key" in n for n in notes), notes)
        self.assertFalse(os.path.exists(log))
        env["FIRECRAWL_API_KEY"] = "k"
        rc, report, raw, se = self.fetch("/app", ["--external", '{"mode": "auto"}', "--no-external"], env_extra=env)
        self.assertEqual(rc, 3)
        self.assertFalse(any(s["stage"].startswith("external") for s in report["stages"]))
        self.assertFalse(os.path.exists(log))

    def test_vendor_wall_goes_to_next_fetcher_and_vendor_login_is_terminal(self):
        self.server.add("/wall", "<html><body><p>Access Denied</p></body></html>", status=403)
        log = os.path.join(self.dir, "ext.log")
        env = {"MNEMON_EXTERNAL_FETCH_CMD": self.external, "EXT_LOG": log, "FIRECRAWL_API_KEY": "k", "JINA_API_KEY": "j", "EXT_MODE": "wall"}
        rc, report, raw, se = self.fetch("/wall", ["--external", '{"mode": "auto", "fetchers": ["firecrawl", "jina"]}'], env_extra=env)
        self.assertEqual(rc, 1)
        self.assertEqual(report["error_class"], "blocked")
        self.assertEqual(self.stages(report), [("direct", "bot-wall"), ("render", "failed"), ("external:firecrawl", "bot-wall"), ("external:jina", "bot-wall")])
        env["EXT_MODE"] = "login"
        rc, report, raw, se = self.fetch("/wall", ["--external", '{"mode": "auto", "fetchers": ["firecrawl", "jina"]}'], env_extra=env)
        self.assertEqual(rc, 1)
        self.assertEqual(self.stages(report)[-1], ("external:firecrawl", "login"))
        self.assertEqual(len(report["stages"]), 3, "a vendor login is terminal: no next vendor")

    def test_429_is_blocked_rate_limit_retryable_no_escalation(self):
        self.server.add("/busy", "<html><body>slow down</body></html>", status=429)
        log = os.path.join(self.dir, "ext.log")
        env = {"MNEMON_EXTERNAL_FETCH_CMD": self.external, "EXT_LOG": log, "FIRECRAWL_API_KEY": "k"}
        rc, report, raw, se = self.fetch("/busy", ["--external", '{"mode": "auto"}'], render=self.render_ok, env_extra=env)
        self.assertEqual(rc, 1)
        self.assertEqual((report["error_class"], report["retryable"], report["acquisition"]["kind"]), ("blocked", True, "rate-limit"))
        self.assertEqual(self.stages(report), [("direct", "rate-limit")])
        self.assertFalse(os.path.exists(log))

    def test_paywall_teaser_published_as_blocked(self):
        self.server.add("/pay", "<html><body><p>%s</p><div class=\"paywall\">Subscribe to continue reading</div></body></html>" % WORDS)
        rc, report, raw, se = self.fetch("/pay", render=self.render_ok)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "paywall")])
        self.assertEqual(report["acquisition"]["kind"], "paywall")
        self.assertIsNotNone(raw)

    def test_login_page_fails_without_escalation_and_matches_the_failed_fixture(self):
        self.server.add("/members", "<html><body><p>Sign in to continue</p></body></html>")
        rc, report, raw, se = self.fetch("/members", render=self.render_ok)
        self.assertEqual(rc, 1)
        self.assertEqual(self.stages(report), [("direct", "login")])
        self.assertEqual((report["error_class"], report["retryable"], report["acquisition"]["kind"]), ("blocked", False, "login"))
        self.assertIsNone(raw)
        assert_same_keys(self, report, expected_shapes("fetch-failed.json"), "fetch-failed")
        self.assertIn("capture-fetch: blocked:", se)

    def test_thin_page_rendered_once_richer_kept(self):
        self.server.add("/thin", "<html><head><title>Thin</title></head><body><p>only a few words here</p></body></html>")
        rc, report, raw, se = self.fetch("/thin", render=self.render_ok)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "thin"), ("render", "ok")])
        self.assertEqual((report["method"], report["raw_kind"]), ("render", "rendered"))
        self.assertIn(b"Rendered page", raw)
        rc, report, raw, se = self.fetch("/thin", render=self.render_thin)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "thin"), ("render", "thin")])
        self.assertEqual((report["method"], report["acquisition"]["outcome"]), ("direct", "thin"))
        self.assertIn(b"only a few words here", raw)
        self.assertIn("kept", report["stages"][-1]["note"])
        rc, report, raw, se = self.fetch("/thin", ["--no-render"], render=self.render_ok)
        self.assertEqual(self.stages(report), [("direct", "thin")])
        self.assertEqual(report["method"], "direct")

    def test_forced_render_without_chrome_is_environment(self):
        self.server.add("/x", RICH)
        rc, report, raw, se = self.fetch("/x", ["--render"], render=self.render_nochrome)
        self.assertEqual(rc, 3)
        self.assertEqual(report["error_class"], "environment")
        self.assertEqual(self.server.count("/x"), 0, "a forced render starts at stage 2")
        rc, report, raw, se = self.fetch("/x", ["--render"], render=self.render_ok)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("render", "ok")])

    def test_pdf_served_as_a_page_ends_the_chain(self):
        self.server.add("/doc", b"%PDF-1.7\nbinary\n%%EOF\n", content_type="application/pdf")
        rc, report, raw, se = self.fetch("/doc")
        self.assertEqual(rc, 0, se)
        self.assertEqual((report["raw_kind"], report["content_type"], report["ext"]), ("file", "application/pdf", "pdf"))

    def test_kind_passthrough_and_invalid_url(self):
        self.server.add("/v", RICH)
        rc, report, raw, se = self.fetch("/v", ["--kind", "youtube"])
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["kind"], "youtube")
        rc, report, raw, se = self.fetch("", url="ftp://example.com/x")
        self.assertEqual(rc, 2)
        self.assertEqual(report["error_class"], "invalid-url")

    def test_ssrf_blocked_without_the_loopback_hook(self):
        self.server.add("/ok", RICH)
        env = clean_env(hooks=False)
        env["RENDER_URL_CMD"] = self.render_fail
        rc, so, se = run_cli([CF, self.server.url("/ok"), "--out", self.out, "--report", self.rep], env=env)
        self.assertEqual(rc, 1)
        with open(self.rep) as fh:
            report = json.load(fh)
        self.assertEqual(report["error_class"], "ssrf-blocked")
        self.assertEqual(self.server.count("/ok"), 0)


class Expect(FetchBase):
    def test_expect_pdf(self):
        self.server.add("/doc.pdf", b"%PDF-1.7\nbinary\n%%EOF\n", content_type="application/octet-stream")
        rc, report, raw, se = self.fetch("/doc.pdf", ["--expect", "pdf"])
        self.assertEqual(rc, 0, se)
        self.assertEqual((report["raw_kind"], report["content_type"], report["ext"], report["method"]), ("file", "application/pdf", "pdf", "direct"))
        self.server.add("/notpdf", RICH)
        rc, report, raw, se = self.fetch("/notpdf", ["--expect", "pdf"])
        self.assertEqual(rc, 1)
        self.assertEqual((report["error_class"], report["retryable"]), ("not-pdf", False))
        self.assertIsNone(raw)

    def test_expect_fetches_the_literal_url(self):
        """--expect never goes through the resolver's fetch_url: the literal (trimmed) URL is requested, query
        and all, and recorded as fetch_url."""
        self.server.add("/doc.pdf", b"%PDF-1.7\nbinary\n%%EOF\n", content_type="application/pdf")
        rc, report, raw, se = self.fetch("/doc.pdf?utm_source=x&v=2", ["--expect", "pdf", "--kind", "page"])
        self.assertEqual(rc, 0, se)
        self.assertEqual([r[1] for r in self.server.requests()], ["/doc.pdf?utm_source=x&v=2"])
        self.assertEqual(report["fetch_url"], self.server.url("/doc.pdf?utm_source=x&v=2"))
        self.assertEqual(report["canonical_url"], self.server.url("/doc.pdf?v=2"), "the identity is still canonical")

    def test_expect_mapping(self):
        for status, error_class, kind, retryable in ((401, "blocked", "forbidden", False), (403, "blocked", "forbidden", False),
                                                     (404, "not-found", "not-found", False), (429, "blocked", "rate-limit", True),
                                                     (503, "unavailable", "unavailable", True), (500, "server-error", "server-error", True)):
            self.server.add("/s", "<html><body>x</body></html>", status=status)
            rc, report, raw, se = self.fetch("/s", ["--expect", "pdf"])
            self.assertEqual(rc, 1, status)
            self.assertEqual((report["error_class"], report["acquisition"]["kind"], report["retryable"]), (error_class, kind, retryable), status)
        self.server.add("/s", "", status=302)   # a 3xx without Location
        rc, report, raw, se = self.fetch("/s", ["--expect", "pdf"])
        self.assertEqual((rc, report["error_class"]), (1, "http"))

    def test_expect_audio_sniff_and_fallbacks(self):
        self.server.add("/track", wav_bytes(), content_type="application/octet-stream")
        rc, report, raw, se = self.fetch("/track", ["--expect", "audio"])
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["ext"], "wav")
        self.assertIn(report["audio_sniff"], ("audio/x-wav", "audio/wav"))
        self.assertEqual(report["content_type"], report["audio_sniff"])
        self.assertEqual(raw, wav_bytes())
        self.server.add("/song.mp3", b"\x00" * 64, content_type="application/octet-stream")
        rc, report, raw, se = self.fetch("/song.mp3", ["--expect", "audio"])
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["ext"], "mp3", "the URL's extension is the fallback")
        self.server.add("/page", RICH)
        rc, report, raw, se = self.fetch("/page", ["--expect", "audio"])
        self.assertEqual((rc, report["error_class"]), (1, "not-audio"))


class ArXiv(FetchBase):
    def arxiv_routes(self):
        abs_v3 = ("<html><head><meta name=\"citation_title\" content=\"A Paper\"><meta name=\"citation_author\" content=\"Author, First\">"
                  "<meta name=\"citation_author\" content=\"Writer, Second\"><meta name=\"citation_date\" content=\"2024/01/15\">"
                  "<meta name=\"citation_pdf_url\" content=\"https://arxiv.org/pdf/2401.00001v3\"><link rel=\"canonical\" href=\"https://arxiv.org/abs/2401.00001v3\"></head>"
                  "<body><p>%s</p></body></html>" % WORDS)
        self.server.add("/abs/2401.00001", abs_v3)
        self.server.add("/abs/2401.00001v1", abs_v3.replace("v3", "v1").replace("2024/01/15", "2023/12/01"))
        self.server.add("/html/2401.00001v1", "<html><head><title>v1</title></head><body><article class=\"ltx_document\"><p>%s</p></article></body></html>" % WORDS)
        self.server.add("/html/2401.00001v3", "<html><body>no html</body></html>", status=404)
        self.server.add("/pdf/2401.00001v3", b"%PDF-1.5\nv3\n", content_type="application/pdf")

    def env(self):
        return {"MNEMON_FETCH_URL_REWRITE": "https://arxiv.org=%s" % self.server.base}

    def test_requested_version_fetched_as_html(self):
        self.arxiv_routes()
        rc, report, raw, se = self.fetch("", url="https://arxiv.org/abs/2401.00001v1", env_extra=self.env())
        self.assertEqual(rc, 0, se)
        m = report["metadata"]
        self.assertEqual((m["arxiv_id"], m["arxiv_version"], m["arxiv_requested_version"]), ("2401.00001", 1, 1))
        self.assertEqual(m["authors"], ["Author, First", "Writer, Second"])
        self.assertEqual((m["published"], m["published_source"], m["title"]), ("2023-12-01", "citation_date", "A Paper"))
        self.assertEqual(m["declared_canonical_url"], "https://arxiv.org/abs/2401.00001v1")
        self.assertEqual((report["canonical_url"], report["kind"], report["requested_version"]), ("https://arxiv.org/abs/2401.00001", "arxiv", 1))
        self.assertEqual(report["fetch_url"], "https://arxiv.org/html/2401.00001v1")
        self.assertEqual(self.stages(report), [("metadata", "ok"), ("direct", "ok")])
        self.assertEqual((report["raw_kind"], report["method"]), ("response", "direct"))
        self.assertIn(b"ltx_document", raw)
        self.assertEqual([r[1] for r in self.server.requests()], ["/abs/2401.00001v1", "/html/2401.00001v1"])

    def test_latest_version_resolved_once_and_pdf_fallback(self):
        self.arxiv_routes()
        rc, report, raw, se = self.fetch("", url="https://arxiv.org/abs/2401.00001", env_extra=self.env())
        self.assertEqual(rc, 0, se)
        m = report["metadata"]
        self.assertEqual((m["arxiv_version"], m["arxiv_requested_version"]), (3, None))
        self.assertEqual(self.stages(report), [("metadata", "ok"), ("direct", "no-html"), ("pdf", "ok")])
        self.assertEqual((report["raw_kind"], report["content_type"], report["ext"]), ("file", "application/pdf", "pdf"))
        self.assertEqual(raw, b"%PDF-1.5\nv3\n")
        self.assertEqual([r[1] for r in self.server.requests()], ["/abs/2401.00001", "/html/2401.00001v3", "/pdf/2401.00001v3"])

    def test_missing_paper_is_not_found(self):
        self.server.add("/abs/2401.99999", "<html><body>no such paper</body></html>", status=404)
        rc, report, raw, se = self.fetch("", url="https://arxiv.org/abs/2401.99999", env_extra=self.env())
        self.assertEqual((rc, report["error_class"], report["stage"]), (1, "not-found", "metadata"))

    def test_expect_pdf_fetches_the_pdf_of_the_requested_or_resolved_version(self):
        """`mnemon add --url https://arxiv.org/pdf/<id>vN.pdf` takes the pdf row with --kind arxiv: the target is
        pdf/<id>vN (the requested version, else the one resolved from the abstract page), never html/<id>vN."""
        self.arxiv_routes()
        self.server.add("/pdf/2401.00001v1", b"%PDF-1.5\nv1\n", content_type="application/pdf")
        rc, report, raw, se = self.fetch("", ["--expect", "pdf", "--kind", "arxiv"], url="https://arxiv.org/pdf/2401.00001v1.pdf",
                                         env_extra=self.env())
        self.assertEqual(rc, 0, se)
        self.assertEqual(raw, b"%PDF-1.5\nv1\n")
        self.assertEqual(report["fetch_url"], "https://arxiv.org/pdf/2401.00001v1")
        self.assertEqual((report["raw_kind"], report["ext"], report["metadata"]["arxiv_version"], report["metadata"]["arxiv_requested_version"]),
                         ("file", "pdf", 1, 1))
        self.assertEqual(report["metadata"]["authors"], ["Author, First", "Writer, Second"], "the abstract page's metadata travels with the pdf row")
        self.assertEqual(self.stages(report), [("metadata", "ok"), ("direct", "ok")])
        self.assertEqual([r[1] for r in self.server.requests()], ["/abs/2401.00001v1", "/pdf/2401.00001v1"])
        self.server.reset()
        rc, report, raw, se = self.fetch("", ["--expect", "pdf"], url="https://arxiv.org/pdf/2401.00001", env_extra=self.env())
        self.assertEqual(rc, 0, se)
        self.assertEqual(report["fetch_url"], "https://arxiv.org/pdf/2401.00001v3", "no version requested: the resolved one")
        self.assertEqual([r[1] for r in self.server.requests()], ["/abs/2401.00001", "/pdf/2401.00001v3"])
        self.assertEqual(raw, b"%PDF-1.5\nv3\n")


class Timeouts(FetchBase):
    def sleeper(self, name="render-sleep.sh"):
        pidfile = os.path.join(self.dir, "pids")
        return write_stub(self.stubs, name, "#!/usr/bin/env bash\nsleep 60 &\necho \"$$ $!\" > %s\nwait\n" % pidfile), pidfile

    def test_renderer_timeout_kills_the_group(self):
        self.server.add("/wall", "<html><body><p>Access Denied</p></body></html>", status=403)
        stub, pidfile = self.sleeper()
        env = clean_env()
        env["RENDER_URL_CMD"] = stub
        t0 = time.monotonic()
        rc, so, se = run_cli([CF, self.server.url("/wall"), "--out", self.out, "--report", self.rep, "--render-timeout", "1",
                              "--acquire-timeout", "30"], env=env)
        elapsed = time.monotonic() - t0
        self.assertEqual(rc, 124, se)
        with open(self.rep) as fh:
            report = json.load(fh)
        self.assertEqual((report["error_class"], report["stage"], report["retryable"]), ("timeout", "render", True))
        self.assertEqual(self.stages(report), [("direct", "bot-wall"), ("render", "timeout")])
        assert_same_keys(self, report, expected_shapes("fetch-timeout.json"), "fetch-timeout")
        with open(pidfile) as fh:
            pids = [int(p) for p in fh.read().split()]
        for pid in pids:
            self.assertTrue(wait_gone(pid, 4), "pid %d survived the renderer timeout" % pid)
        self.assertLess(elapsed, 15)

    def test_thin_trigger_keeps_direct_on_render_timeout(self):
        self.server.add("/thin", "<html><body><p>only a few words here</p></body></html>")
        stub, pidfile = self.sleeper("render-sleep2.sh")
        rc, report, raw, se = self.fetch("/thin", ["--render-timeout", "1"], render=stub)
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.stages(report), [("direct", "thin"), ("render", "timeout")])
        self.assertEqual((report["method"], report["acquisition"]["outcome"]), ("direct", "thin"))

    def test_acquire_timeout_caps_the_direct_stage(self):
        self.server.add("/slow", RICH, delay=3.0)
        rc, report, raw, se = self.fetch("/slow", ["--acquire-timeout", "1"])
        self.assertEqual(rc, 124)
        self.assertEqual((report["error_class"], report["stage"]), ("timeout", "direct"))

    def test_sigterm_tears_down_the_renderer(self):
        self.server.add("/wall", "<html><body><p>Access Denied</p></body></html>", status=403)
        stub, pidfile = self.sleeper("render-sleep3.sh")
        env = clean_env()
        env["RENDER_URL_CMD"] = stub
        proc = subprocess.Popen([PY, CF, self.server.url("/wall"), "--out", self.out, "--report", self.rep, "--render-timeout", "60",
                                 "--acquire-timeout", "60"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        end = time.monotonic() + 10
        while not os.path.exists(pidfile) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(pidfile), "the renderer never started")
        time.sleep(0.2)
        os.kill(proc.pid, signal.SIGTERM)
        rc = proc.wait(timeout=10)
        self.assertEqual(rc, 143)
        with open(pidfile) as fh:
            pids = [int(p) for p in fh.read().split()]
        for pid in pids:
            self.assertTrue(wait_gone(pid, 4), "pid %d survived SIGTERM of its owner" % pid)


class MetadataScans(unittest.TestCase):
    def test_meta_map_and_declared_canonical_are_bounded(self):
        """The metadata scans walk the tags by bounded find loops (G1): the facts of a page, and a page of
        tags that never end, both cost one pass."""
        html = ('<html><head><meta name="citation_title" content="A &amp; B"><META property="og:x" content=" y ">'
                '<link rel="canonical alternate" href="https://e/x?a=1&amp;b=2"><meta name="empty" content=""></head></html>')
        self.assertEqual(F._meta_map(html), {"citation_title": ["A & B"], "og:x": ["y"]})
        self.assertEqual(F._declared_canonical(html), "https://e/x?a=1&b=2")
        hostile = "<meta " * 200000 + "<link " * 200000
        t0 = time.monotonic()
        self.assertEqual(F._meta_map(hostile), {})
        self.assertIsNone(F._declared_canonical(hostile))
        self.assertLess(time.monotonic() - t0, 2.0)


class VendorAdapters(unittest.TestCase):
    def test_firecrawl(self):
        method, host, path, headers, body = F.firecrawl_request("https://example.com/app", "KEY")
        self.assertEqual((method, host, path), ("POST", "api.firecrawl.dev", "/v1/scrape"))
        self.assertEqual(headers["Authorization"], "Bearer KEY")
        self.assertEqual(json.loads(body.decode()), {"url": "https://example.com/app", "formats": ["rawHtml"], "onlyMainContent": False})
        recorded = json.dumps({"success": True, "data": {"rawHtml": "<html><body>raw</body></html>", "markdown": "raw",
                                                         "metadata": {"statusCode": 200, "title": "t"}}}).encode()
        html, status, error = F.firecrawl_parse(200, {"content-type": "application/json"}, recorded)
        self.assertEqual((html, status, error), ("<html><body>raw</body></html>", 200, None))
        html, status, error = F.firecrawl_parse(200, {}, json.dumps({"success": True, "data": {"markdown": "only md"}}).encode())
        self.assertIsNone(html)
        self.assertIn("rawHtml", error)
        self.assertEqual(F.firecrawl_parse(402, {}, b"{}")[2], "firecrawl answered HTTP 402")
        self.assertEqual(F.firecrawl_parse(200, {}, b"not json")[2], "firecrawl answered non-JSON")

    def test_jina(self):
        method, host, path, headers, body = F.jina_request("https://example.com/docs?x=1", "KEY")
        self.assertEqual((method, host, path, body), ("GET", "r.jina.ai", "/https://example.com/docs?x=1", None))
        self.assertEqual((headers["X-Return-Format"], headers["X-Respond-With"], headers["X-No-Cache"]), ("html", "html", "true"))
        method, host, path, headers, body = F.jina_request("https://example.com/app#/page-a", "KEY")
        self.assertEqual((method, path), ("POST", "/"))
        self.assertEqual(json.loads(body.decode()), {"url": "https://example.com/app#/page-a"})
        method, host, path, headers, body = F.jina_request("https://example.com/app#!/x", "KEY")
        self.assertEqual(method, "POST")
        html, status, error = F.jina_parse(200, {"x-status-code": "200", "content-type": "text/html"}, b"<!DOCTYPE html><html><body>page</body></html>")
        self.assertEqual((status, error), (200, None))
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        html, status, error = F.jina_parse(200, {}, b"Title: x\n\nMarkdown Content:\nhello")
        self.assertIsNone(html)
        self.assertIn("non-HTML", error)
        self.assertEqual(F.jina_parse(200, {}, b"<html><body>x</body></html>")[1], None)
        self.assertEqual(F.jina_parse(429, {}, b"")[2], "jina answered HTTP 429")


if __name__ == "__main__":
    unittest.main()
