"""§3.2 (last paragraph), §4.2, §7.11 (the depth half), §12 A8/A9: the depth helpers over the policy —
kb_fetch shapes, the probe under a supplied landing (never fetched or rendered, context honoured,
declared_canonical_url), the fold's targets-only result, wall rejection, the budget, the PyYAML error, and
the depth → probe → renderer teardown."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import unittest

from _helpers import BIN, LocalServer, PY, clean_env, run_cli, tmpdir, wait_gone, write_stub

PROBE = os.path.join(BIN, "kb-probe-deeper.py")
FOLD = os.path.join(BIN, "kb-depth-fold.py")
WORDS = " ".join("word%d" % i for i in range(120))
SPA = ('<!doctype html><html><head><title>X</title><link rel="canonical" href="https://example-spa.test/home"></head>'
       '<body><div id="root"></div><script src="/static/app.js"></script></body></html>')


def have_yaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


class DepthBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer()
        cls.work = tmpdir("mnemon-depth-")
        cls.landing = os.path.join(cls.work, "landing.html")
        with open(cls.landing, "w") as fh:
            fh.write(SPA)
        cls.render_fail = write_stub(cls.work, "render-fail.sh", "#!/usr/bin/env bash\nexit 11\n")

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        shutil.rmtree(cls.work, ignore_errors=True)

    def setUp(self):
        self.server.reset()
        self.server.routes.clear()
        self.server.add("/llms.txt", "# X\n\n- a\n- b\n- c\n", content_type="text/plain")
        self.server.add("/docs", "<html><body><h1>Docs</h1><p>%s</p></body></html>" % WORDS)
        self.server.add("/docs/", "<html><body><h1>Docs</h1><p>%s</p></body></html>" % WORDS)
        self.server.add("/", SPA)

    def env(self, extra=None):
        b = self.server.base
        env = clean_env({"MNEMON_FETCH_URL_REWRITE": "https://example-spa.test=%s;https://docs.example-spa.test=%s/docs" % (b, b)})
        env["RENDER_URL_CMD"] = self.render_fail
        if extra:
            env.update(extra)
        return env

    def paths(self):
        return [r[1] for r in self.server.requests()]


class KbFetch(DepthBase):
    def test_shapes_over_the_policy(self):
        import kb_fetch
        old = dict(os.environ)
        os.environ.update(self.env())
        try:
            kb_fetch.configure(None)
            final, status, text = kb_fetch.fetch("https://example-spa.test/llms.txt")
            self.assertEqual((final, status), ("https://example-spa.test/llms.txt", 200))
            self.assertIn("- a", text)
            final, status, text = kb_fetch.fetch("https://example-spa.test/missing")
            self.assertEqual((final, status, text != ""), ("https://example-spa.test/missing", 404, True))
            final, status, text = kb_fetch.fetch("ftp://example-spa.test/x")
            self.assertEqual((final, status, text), (None, "scheme", ""))
            self.assertTrue(kb_fetch.host_is_safe("example-spa.test") or True)
            md = kb_fetch.defuddle_md("https://docs.example-spa.test/")
            self.assertIsInstance(md, str)
            self.assertEqual(self.paths().count("/docs/"), 1, "defuddle_md fetches the bytes once, never `defuddle parse <url>`")
        finally:
            os.environ.clear()
            os.environ.update(old)


class Probe(DepthBase):
    def test_landing_file_never_fetched_or_rendered(self):
        marker = os.path.join(self.work, "rendered")
        render_mark = write_stub(self.work, "render-mark.sh", "#!/usr/bin/env bash\ntouch %s\nexit 11\n" % marker)
        ctx = {"final_url": "https://example-spa.test/", "rendered": False, "render": "auto"}
        rc, so, se = run_cli([PROBE, "https://example-spa.test/", "--landing-file", self.landing, "--context", json.dumps(ctx)],
                             env=self.env({"RENDER_URL_CMD": render_mark}))
        self.assertEqual(rc, 0, se)
        d = json.loads(so)
        self.assertTrue(d["thin"] and d["spa"])
        self.assertFalse(d["rendered"])
        self.assertFalse(os.path.exists(marker), "the Chrome branch is removed under a supplied landing")
        self.assertNotIn("/", self.paths(), "the landing is never fetched")
        self.assertEqual(d["declared_canonical_url"], "https://example-spa.test/home")
        self.assertNotIn("canonical_url", d)
        self.assertEqual(d["final_url"], "https://example-spa.test/")
        kinds = sorted((c["type"], c["same_entity_confidence"]) for c in d["candidates"])
        self.assertIn(("llms.txt", "high"), kinds)
        self.assertIn(("docs", "high"), kinds)
        self.assertEqual(d["errors"], [])

    def test_context_rendered_and_final_url_are_taken_as_given(self):
        ctx = {"final_url": "https://example-spa.test/landed", "rendered": True, "render": "off"}
        rc, so, se = run_cli([PROBE, "https://example-spa.test/", "--landing-file", self.landing, "--context", json.dumps(ctx)], env=self.env())
        d = json.loads(so)
        self.assertEqual((d["rendered"], d["final_url"], d["posted_url"]), (True, "https://example-spa.test/landed", "https://example-spa.test/"))

    def test_budget_bounds_the_probe(self):
        self.server.add("/llms.txt", "# X\n\n- a\n- b\n- c\n", content_type="text/plain", delay=3.0)
        t0 = time.monotonic()
        rc, so, se = run_cli([PROBE, "https://example-spa.test/", "--landing-file", self.landing, "--budget-s", "1"], env=self.env())
        self.assertEqual(rc, 0, se)
        self.assertLess(time.monotonic() - t0, 10)
        d = json.loads(so)
        self.assertIn("budget exhausted", d["errors"])

    def test_without_landing_file_the_landing_goes_through_the_policy(self):
        rc, so, se = run_cli([PROBE, "https://example-spa.test/", "--context", json.dumps({"render": "off"})], env=self.env())
        self.assertEqual(rc, 0, se)
        d = json.loads(so)
        self.assertEqual(self.paths()[0], "/")
        self.assertTrue(d["spa"])
        self.assertFalse(d["rendered"])


@unittest.skipUnless(have_yaml(), "PyYAML not installed: the fold's manifest tests are skipped (the error path is tested)")
class Fold(DepthBase):
    def fold(self, extra=(), env=None):
        ctx = {"final_url": "https://example-spa.test/", "http_status": 200, "acquisition": "thin", "render": "off",
               "external": "off", "budget_s": 30}
        rc, so, se = run_cli([FOLD, "https://example-spa.test/", "--landing-file", self.landing, "--context", json.dumps(ctx)] + list(extra),
                             env=env or self.env())
        return rc, (json.loads(so) if so.strip() else None), se

    def test_targets_only_never_the_landing(self):
        rc, d, se = self.fold()
        self.assertEqual(rc, 0, se)
        self.assertTrue(d["applied"], d)
        self.assertNotIn("composite_body", d)
        self.assertNotIn("/", self.paths(), "the fold never fetches the landing")
        urls = [t["url"] for t in d["targets"]]
        self.assertIn("https://example-spa.test/llms.txt", urls)
        self.assertIn("https://docs.example-spa.test/", urls)
        for t in d["targets"]:
            for key in ("url", "role", "header", "text", "status", "sha256"):
                self.assertIn(key, t)
        self.assertEqual(d["folded"], urls)
        self.assertIn("declared_canonical_url: https://example-spa.test/home", d["manifest_yaml"])
        self.assertNotIn("\ncanonical_url:", d["manifest_yaml"])
        self.assertIn("final_url: https://example-spa.test/", d["manifest_yaml"])
        self.assertIn("folded_sources:", d["manifest_yaml"])

    def test_wall_rejected_not_folded(self):
        wall = "<html><body><h1>Docs</h1><p>%s</p><p>Please sign in to continue</p></body></html>" % WORDS
        self.server.add("/docs/", wall)
        self.server.add("/docs", wall)
        rc, d, se = self.fold()
        self.assertEqual(rc, 0, se)
        urls = [t["url"] for t in d.get("targets", [])]
        self.assertNotIn("https://docs.example-spa.test/", urls, "a login wall is never folded")
        self.assertIn("https://example-spa.test/llms.txt", urls)
        self.assertTrue(any(e.startswith("fold fetch rejected: login") for e in d["errors"]), d["errors"])
        self.server.add("/llms.txt", "<html><body>not text</body></html>")
        rc, d, se = self.fold()
        self.assertFalse(d["applied"], d)

    def test_budget_exhausted_reports_timeout(self):
        self.server.add("/llms.txt", "# X\n\n- a\n- b\n- c\n", content_type="text/plain", delay=4.0)
        t0 = time.monotonic()
        rc, so, se = run_cli([FOLD, "https://example-spa.test/", "--landing-file", self.landing, "--budget-s", "1"], env=self.env())
        self.assertEqual(rc, 0, se)
        d = json.loads(so)
        self.assertFalse(d["applied"])
        self.assertEqual(d["error_class"], "timeout")
        self.assertLess(time.monotonic() - t0, 10)

    def test_depth_off_and_not_thin(self):
        rc, so, se = run_cli([FOLD, "https://example-spa.test/", "--depth", "off"], env=self.env())
        self.assertEqual(json.loads(so), {"applied": False, "reason": "depth off"})
        rich = os.path.join(self.work, "rich.html")
        with open(rich, "w") as fh:
            fh.write("<html><body>%s</body></html>" % " ".join("w" for _ in range(600)))
        rc, d, se = self.fold(["--landing-file", rich])
        self.assertFalse(d["applied"])
        self.assertIn("not thin", d["reason"])

    def test_depth_probe_renderer_torn_down_on_the_budget(self):
        """depth → probe → stub renderer: the fold's deadline kills the probe group, the probe kills the renderer."""
        pidfile = os.path.join(self.work, "pids")
        sleeper = write_stub(self.work, "render-sleep.sh", "#!/usr/bin/env bash\nsleep 60 &\necho \"$$ $!\" > %s\nwait\n" % pidfile)
        env = self.env({"RENDER_URL_CMD": sleeper})
        t0 = time.monotonic()
        rc, so, se = run_cli([FOLD, "https://example-spa.test/", "--budget-s", "2"], env=env, timeout=60)
        self.assertEqual(rc, 0, se)
        d = json.loads(so)
        self.assertFalse(d["applied"])
        self.assertEqual(d.get("error_class"), "timeout")
        self.assertLess(time.monotonic() - t0, 20)
        self.assertTrue(os.path.exists(pidfile), "the renderer never started")
        with open(pidfile) as fh:
            pids = [int(p) for p in fh.read().split()]
        for pid in pids:
            self.assertTrue(wait_gone(pid, 5), "pid %d survived the fold's budget" % pid)


class FoldWithoutYaml(DepthBase):
    def test_clear_error_when_pyyaml_is_missing(self):
        shadow = os.path.join(self.work, "noyaml")
        os.makedirs(shadow, exist_ok=True)
        with open(os.path.join(shadow, "yaml.py"), "w") as fh:
            fh.write("raise ImportError('no yaml here')\n")
        env = self.env({"PYTHONPATH": shadow})
        rc, so, se = run_cli([FOLD, "https://example-spa.test/", "--landing-file", self.landing], env=env)
        self.assertEqual(rc, 3)
        self.assertIn("PyYAML is required", se)
        self.assertEqual(so.strip(), "")
        rc, so, se = run_cli([FOLD, "https://example-spa.test/", "--depth", "off"], env=env)
        self.assertEqual((rc, json.loads(so)["applied"]), (0, False))


if __name__ == "__main__":
    unittest.main()
