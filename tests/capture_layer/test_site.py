"""§7.10 (the halves that need no gateway): map against a stub site (robots groups, anchored roles, discovery
sources, include/exclude), fetch-bundle (dispositions, members, the stdout line, a 500 page, the site
timeout, --no-render precedence, the A9 teardown), verify (modified, missing, symlinked, extra members),
assemble (the composite, the mnemon-site/1 report, a convert-failed page, a landing without text)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from _helpers import BIN, LocalServer, PY, assert_same_keys, clean_env, expected_shapes, run_cli, tmpdir, wait_gone, \
    write_stub

from capture import proc, site
from capture import policy as policymod
from capture import report as reportmod

CS = os.path.join(BIN, "capture-site.py")
WORDS = " ".join("word%d" % i for i in range(120))


def page(title: str, extra: str = "") -> str:
    return "<html><head><title>%s</title></head><body><h1>%s</h1><p>%s</p>%s</body></html>" % (title, title, WORDS, extra)


def build_site(srv: LocalServer) -> None:
    b = srv.base
    links = "\n".join('<a href="%s">%s</a>' % (h, h) for h in [
        "/product", "/docs/intro", "/blog/post-1", "/pricing", "/privacy", "/de-de/x", "/private/x", "/jobs/42", "/jobs",
        "https://other.example/x", "/brochure.pdf", "https://github.com/owner/repo", "/product#intro", "/goes-away",
        "/empty", "/faq"])
    srv.add("/", page("Landing", "<nav>%s</nav>" % links))
    srv.add("/robots.txt", "User-agent: *\nDisallow: /private\nAllow: /private/open\nSitemap: %s/sitemap.xml\n" % b,
            content_type="text/plain")
    srv.add("/sitemap.xml", "<urlset><url><loc>%s/docs/guide</loc></url><url><loc>%s/careers</loc></url>"
            "<url><loc>%s/private/open</loc></url></urlset>" % (b, b, b), content_type="application/xml")
    srv.add("/llms.txt", "# Example\n\n- one\n- two\n- three\n", content_type="text/plain")
    for p, t in [("/product", "Product"), ("/docs/intro", "Docs intro"), ("/blog/post-1", "Post"), ("/pricing", "Pricing"),
                 ("/privacy", "Privacy"), ("/de-de/x", "German"), ("/private/x", "Private"), ("/private/open", "Open"),
                 ("/jobs/42", "Job"), ("/jobs", "Jobs"), ("/careers", "Careers"), ("/faq", "FAQ")]:
        srv.add(p, page(t))
    srv.add("/docs/guide", "<html><body>down</body></html>", status=500)
    srv.add("/brochure.pdf", b"%PDF-1.4\n", content_type="application/pdf")
    srv.add("/goes-away", "", status=302, headers=[("Location", "https://other.example/moved")])
    srv.add("/empty", "<html><head><title>Empty</title></head><body></body></html>")
    srv.add("/gh/owner/repo/HEAD/README.md", "# Repo\n\nA readme with more than six words in it.\n", content_type="text/plain")


class SiteBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer()
        build_site(cls.server)
        cls.work = tmpdir("mnemon-site-")
        cls.stubs = os.path.join(cls.work, "stubs")
        os.makedirs(cls.stubs)
        cls.render_fail = write_stub(cls.stubs, "render-fail.sh", "#!/usr/bin/env bash\nexit 11\n")
        cls.env = clean_env({"MNEMON_FETCH_URL_REWRITE": "https://raw.githubusercontent.com=%s/gh" % cls.server.base})
        cls.env["RENDER_URL_CMD"] = cls.render_fail
        cls.plan_path = os.path.join(cls.work, "plan.json")
        rc, so, se = run_cli([CS, "map", cls.server.base + "/", "--out", cls.plan_path, "--max-pages", "13",
                              "--exclude", "blog"], env=cls.env, timeout=300)
        assert rc == 0, se
        cls.small_plan_path = os.path.join(cls.work, "plan-small.json")
        rc, so, se = run_cli([CS, "map", cls.server.base + "/", "--out", cls.small_plan_path, "--max-pages", "4"], env=cls.env, timeout=300)
        assert rc == 0, se
        with open(cls.small_plan_path, encoding="utf-8") as fh:
            cls.small_plan = json.load(fh)
        with open(cls.plan_path, encoding="utf-8") as fh:
            cls.plan = json.load(fh)
        cls.bundle = os.path.join(cls.work, "bundle")
        cls.bundle_report = os.path.join(cls.work, "bundle.json")
        cls.server.reset()
        rc, so, se = run_cli([CS, "fetch-bundle", cls.plan_path, "--out", cls.bundle, "--report", cls.bundle_report,
                              "--site-timeout", "120"], env=cls.env, timeout=300)
        assert rc == 0, se
        cls.bundle_stdout = so.strip()
        with open(os.path.join(cls.bundle, "manifest.json"), "rb") as fh:
            cls.manifest_bytes = fh.read()
        cls.manifest = json.loads(cls.manifest_bytes.decode("utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        shutil.rmtree(cls.work, ignore_errors=True)

    def pages_by_path(self):
        return {p["url"].replace(self.server.base, "") or "/": p for p in self.plan["pages"]}


class Map(SiteBase):
    def test_plan_shape(self):
        fixture = expected_shapes("site-plan.json")
        assert_same_keys(self, self.plan, fixture, "mnemon-site-plan/1")
        assert_same_keys(self, self.plan["pages"][0], fixture["pages"][0], "pages[0]", nullable=("title",))
        assert_same_keys(self, self.plan["discovery"], fixture["discovery"], "discovery")
        assert_same_keys(self, self.plan["discovery"]["robots"], fixture["discovery"]["robots"], "robots")
        assert_same_keys(self, self.plan["discovery"]["sources"][0], fixture["discovery"]["sources"][0], "sources[0]", nullable=("note",))
        self.assertEqual(self.plan["landing"], self.server.base + "/")
        self.assertEqual(self.plan["caps"], {"max_pages": 13})
        self.assertEqual([p["n"] for p in self.plan["pages"]], list(range(1, len(self.plan["pages"]) + 1)))

    def test_include_and_exclude_match_the_path_and_query(self):
        """G10: --include / --exclude are searched in the page's path plus ?query — an anchored `^/faq$`
        includes, an anchored `^/blog/` excludes, and a regex that would only match the host matches nothing.
        G21: --include restricts — the plan keeps the landing and only the pages matching some include regex
        (a matching page the role rules excluded is re-admitted), --exclude then removes, the cap applies last."""
        self.assertEqual(site.path_query("https://h.example/a/b?x=1&y=2#frag"), "/a/b?x=1&y=2")
        self.assertEqual(site.path_query("https://h.example"), "/")

        def mapped(name, *extra):
            plan_path = os.path.join(self.work, name)
            rc, so, se = run_cli([CS, "map", self.server.base + "/", "--out", plan_path] + list(extra), env=self.env, timeout=300)
            self.assertEqual(rc, 0, se)
            with open(plan_path, encoding="utf-8") as fh:
                plan = json.load(fh)
            return plan, {p["url"].replace(self.server.base, "") or "/": p for p in plan["pages"]}

        plan, by = mapped("plan-docs.json", "--include", "^/docs/")
        self.assertEqual(sorted(p["url"].replace(self.server.base, "") or "/" for p in plan["pages"] if p["include"]),
                         ["/", "/docs/guide", "/docs/intro"], "a plan with --include '^/docs/' holds the landing and /docs/* only")
        self.assertEqual(by["/product"]["reason"], "excluded: include-regex", "a page matching no include regex is out")
        self.assertEqual(by["/llms.txt"]["reason"], "excluded: include-regex")
        plan, by = mapped("plan-anchored.json", "--include", "^/faq$", "--include", "^/docs/", "--exclude", "^/docs/guide$",
                          "--include", "127\\.0\\.0\\.1")
        self.assertEqual((by["/faq"]["include"], by["/faq"]["reason"]), (True, "howto (--include)"), "an anchored ^/faq$ matches the path and re-admits")
        self.assertEqual((by["/docs/intro"]["include"], by["/docs/intro"]["reason"]), (True, "docs"), "a matching page included by its role keeps its reason")
        self.assertEqual(by["/docs/guide"]["reason"], "excluded: exclude-regex", "--exclude removes after --include")
        self.assertEqual(by["/privacy"]["reason"], "excluded: legal", "the host is never searched: a host regex includes nothing; a role exclusion keeps its reason")
        self.assertEqual(by["/de-de/x"]["reason"], "excluded: localized")
        self.assertEqual(sorted(p["url"].replace(self.server.base, "") or "/" for p in plan["pages"] if p["include"]), ["/", "/docs/intro", "/faq"])
        plan, by = mapped("plan-excl.json", "--exclude", "^/blog/")
        self.assertEqual(by["/blog/post-1"]["reason"], "excluded: exclude-regex", "an anchored ^/blog/ excludes")
        self.assertEqual((by["/product"]["include"], by["/privacy"]["reason"]), (True, "excluded: legal"), "without --include the role rules decide")
        plan, by = mapped("plan-readmit.json", "--include", "privacy")
        self.assertEqual((by["/privacy"]["role"], by["/privacy"]["include"], by["/privacy"]["reason"]), ("legal", True, "legal (--include)"))
        self.assertEqual([p["url"].replace(self.server.base, "") or "/" for p in plan["pages"] if p["include"]], ["/", "/privacy"])

    def test_roles_anchored_and_exclusions(self):
        by = self.pages_by_path()
        self.assertEqual((by["/"]["role"], by["/"]["include"], by["/"]["title"]), ("landing", True, "Landing"))
        self.assertEqual((by["/jobs/42"]["role"], by["/jobs"]["role"], by["/careers"]["role"]), ("job", "careers", "careers"))
        self.assertEqual((by["/privacy"]["role"], by["/privacy"]["include"], by["/privacy"]["reason"]), ("legal", False, "excluded: legal"))
        self.assertEqual((by["/de-de/x"]["role"], by["/de-de/x"]["reason"]), ("localized", "excluded: localized"))
        self.assertEqual(by["/private/x"]["reason"], "excluded: robots")
        self.assertEqual((by["/private/open"]["include"], by["/private/open"]["role"]), (True, "product"))
        self.assertEqual(by["/brochure.pdf"]["reason"], "excluded: content-type")
        self.assertEqual((by["/blog/post-1"]["role"], by["/blog/post-1"]["reason"]), ("blog", "excluded: exclude-regex"))
        self.assertEqual((by["/faq"]["role"], by["/faq"]["reason"]), ("howto", "excluded: howto"))
        self.assertEqual(by["/goes-away"]["reason"], "excluded: redirect-off-site")
        self.assertEqual((by["/docs/intro"]["role"], by["/docs/intro"]["title"]), ("docs", "Docs intro"))
        self.assertEqual((by["/llms.txt"]["role"], by["/llms.txt"]["include"], by["/llms.txt"]["discovered_by"]), ("llms_txt", True, "llms.txt"))
        self.assertNotIn("https://other.example/x", [p["url"] for p in self.plan["pages"]], "an off-site link is not a page")
        self.assertNotIn("/product#intro", [p["url"] for p in self.plan["pages"]], "an anchor is the same page")
        readme = [p for p in self.plan["pages"] if p["role"] == "github_readme"]
        self.assertEqual(len(readme), 1)
        self.assertEqual(readme[0]["url"], "https://raw.githubusercontent.com/owner/repo/HEAD/README.md")
        self.assertIn("raw.githubusercontent.com", self.plan["hosts"])
        self.assertEqual(self.plan["hosts"][0], "127.0.0.1")
        self.assertTrue(all(p["render"] is None for p in self.plan["pages"]))

    def test_discovery_sources_and_robots_recorded(self):
        sources = {s["name"]: s for s in self.plan["discovery"]["sources"]}
        self.assertEqual(sorted(sources), ["docs-toc", "github-readme", "landing-links", "llms.txt", "sitemap"])
        self.assertGreaterEqual(sources["landing-links"]["yield"], 10)
        self.assertEqual(sources["sitemap"]["yield"], 3)
        self.assertEqual(sources["llms.txt"]["yield"], 1)
        self.assertEqual(sources["github-readme"]["yield"], 1)
        robots = self.plan["discovery"]["robots"]
        self.assertEqual((robots["fetched"], robots["status"], robots["groups"]), (True, 200, ["*"]))
        self.assertEqual(robots["rules_applied"], ["Disallow: /private", "Allow: /private/open"])

    def test_cap_keeps_pages_by_role_priority(self):
        included = [p for p in self.plan["pages"] if p["include"]]
        self.assertEqual(len(included), 12, "nothing capped at 13 (12 included)")
        self.assertEqual([p for p in self.plan["pages"] if p["reason"] == "excluded: cap"], [])
        small = [p for p in self.small_plan["pages"] if p["include"]]
        self.assertEqual(len(small), 4)
        self.assertEqual([p["role"] for p in small], ["landing", "product", "product", "product"], "kept by role priority")
        capped = [p["role"] for p in self.small_plan["pages"] if p["reason"] == "excluded: cap"]
        self.assertIn("github_readme", capped)
        self.assertIn("careers", capped)
        self.assertIn("docs", capped)
        self.assertNotIn("product", capped)
        self.assertEqual(self.small_plan["caps"], {"max_pages": 4})


class RobotsRules(unittest.TestCase):
    def test_rfc_9309_groups_and_longest_match(self):
        r = site.Robots.parse("User-agent: other\nDisallow: /\n\nUser-agent: Mnemon\nUser-agent: Mnemon-bot\nDisallow: /secret\n"
                              "Allow: /secret/open\n\nUser-agent: *\nDisallow: /private\n")
        self.assertEqual(r.applied()[0], ["mnemon", "mnemon-bot"])
        self.assertTrue(r.allowed("/private/x"), "the * group is not used when a product-token group exists")
        self.assertFalse(r.allowed("/secret/x"))
        self.assertTrue(r.allowed("/secret/open/y"), "Allow wins by longest match")
        self.assertTrue(r.allowed("/"))
        r = site.Robots.parse("User-agent: *\nDisallow: /a*b$\nDisallow: /c\nAllow: /c/d\n")
        self.assertFalse(r.allowed("/axxb"))
        self.assertTrue(r.allowed("/axxbc"))
        self.assertFalse(r.allowed("/c/x"))
        self.assertTrue(r.allowed("/c/d/e"))
        r = site.Robots.parse("User-agent: *\nDisallow:\n")
        self.assertTrue(r.allowed("/anything"), "an empty Disallow allows everything")
        self.assertFalse(site.Robots.unreachable().allowed("/"))
        self.assertTrue(site.Robots.no_rules(403).allowed("/private"))

    def test_fetch_robots_states(self):
        srv = LocalServer()
        try:
            env = clean_env()
            old = dict(os.environ)
            os.environ.update(env)
            try:
                policy = policymod.Policy(connect_timeout=5, read_timeout=5)
                srv.add("/robots.txt", "down", status=500)
                self.assertEqual(site.fetch_robots(policy, srv.base + "/", proc.Deadline(10)).state, "unreachable")
                srv.add("/robots.txt", "forbidden", status=403)
                r = site.fetch_robots(policy, srv.base + "/", proc.Deadline(10))
                self.assertEqual((r.state, r.status), ("none", 403))
                srv.add("/robots.txt", "User-agent: *\nDisallow: /x\n", content_type="text/plain")
                r = site.fetch_robots(policy, srv.base + "/", proc.Deadline(10))
                self.assertEqual((r.state, r.fetched, r.allowed("/x/y")), ("ok", True, False))
            finally:
                os.environ.clear()
                os.environ.update(old)
        finally:
            srv.close()

    def test_host_eligibility_without_suffix_guessing(self):
        self.assertTrue(site.host_eligible("www.example.com", "example.com"))
        self.assertTrue(site.host_eligible("docs.example.com", "www.example.com"))
        self.assertTrue(site.host_eligible("api.example.com", "example.com"))
        self.assertFalse(site.host_eligible("cdn.example.com", "example.com"))
        self.assertFalse(site.host_eligible("example.co", "example.com"))
        self.assertFalse(site.host_eligible("other.github.io", "project.github.io"))
        self.assertTrue(site.host_eligible("raw.githubusercontent.com", "example.com", ["raw.githubusercontent.com"]))

    def test_role_rules(self):
        L = "https://example.com/"
        self.assertEqual(site.role_for("https://example.com/jobs/42", None, L), "job")
        self.assertEqual(site.role_for("https://example.com/jobs/", None, L), "careers")
        self.assertEqual(site.role_for("https://example.com/legal/terms", None, L), "legal")
        self.assertEqual(site.role_for("https://example.com/fr/produit", None, L), "localized")
        self.assertEqual(site.role_for("https://de.example.com/x", None, L), "localized")
        self.assertEqual(site.role_for("https://example.com/go/x", None, L), "product", "`go` is not a locale")
        self.assertEqual(site.role_for("https://example.com/changelog", None, L), "howto")
        self.assertEqual(site.role_for("https://example.com/guides/x", "How to install", L), "howto")
        self.assertEqual(site.role_for("https://example.com/news/2026/x", None, L), "blog")
        self.assertEqual(site.role_for("https://docs.example.com/intro", None, L), "docs")
        self.assertEqual(site.role_for("https://example.com/reference/api", None, L), "docs")
        self.assertEqual(site.role_for("https://example.com/pricing", None, L), "pricing")
        self.assertEqual(site.role_for("https://example.com/a/b/c", None, L), "other")
        self.assertEqual(site.role_for("https://example.com/en/x", None, "https://example.com/en/"), "product",
                         "the landing's own locale prefix is not localized")
        self.assertEqual(site.role_for("https://example.com/en/docs/intro", None, L), "docs", "`en` is exempt from the locale rule")
        self.assertEqual(site.role_for("https://example.com/en-us/docs/intro", None, L), "docs", "...and `en-*`")
        self.assertEqual(site.role_for("https://en.example.com/x", None, L), "product")
        self.assertEqual(site.role_for("https://example.com/de/x", None, L), "localized")
        self.assertEqual(site.role_for("https://example.com/de/x", None, L, "https://example.com/de/"), "product",
                         "the landing's locale is read from its final URL too")
        self.assertEqual(site.role_for("https://example.com/fr/x", None, L, "https://example.com/de/"), "localized")
        # G22: the landing's section and what shares it
        self.assertEqual(site.landing_section("https://example.com/help/air/supported-agents.html"), "/help/air/")
        self.assertEqual(site.landing_section("https://example.com/air/"), "/air/")
        self.assertEqual(site.landing_section("https://example.com/air"), "/air/")
        self.assertEqual(site.landing_section("https://example.com"), "/")
        self.assertTrue(site.in_section("https://www.example.com/help/air/a.html", "https://example.com/help/air/landing.html", "/help/air/"))
        self.assertFalse(site.in_section("https://docs.example.com/help/air/a.html", "https://example.com/help/air/landing.html", "/help/air/"))
        self.assertTrue(site.in_section("https://example.com/anything", "https://example.com/", "/"))
        self.assertTrue(site.shares_section("https://example.com/help/sitemap.xml", "/help/air/"))
        self.assertTrue(site.shares_section("https://example.com/help/air/sitemap.xml", "/help/air/"))
        self.assertTrue(site.shares_section("https://example.com/sitemap_index.xml", "/help/air/"))
        self.assertFalse(site.shares_section("https://example.com/help/idea/sitemap.xml", "/help/air/"))
        self.assertFalse(site.shares_section("https://example.com/products/sitemap.xml", "/help/air/"))

    def test_robots_rules_match_path_and_query(self):
        self.assertEqual(site.robots_path("https://example.com/search?q=x"), "/search?q=x")
        self.assertEqual(site.robots_path("https://example.com/search"), "/search")
        self.assertEqual(site.robots_path("https://example.com"), "/")
        r = site.Robots.parse("User-agent: *\nDisallow: /search?\nDisallow: /*?page=\n")
        self.assertTrue(r.allowed(site.robots_path("https://example.com/search")))
        self.assertFalse(r.allowed(site.robots_path("https://example.com/search?q=x")))
        self.assertFalse(r.allowed(site.robots_path("https://example.com/list?page=2")))
        self.assertTrue(r.allowed(site.robots_path("https://example.com/list?sort=asc")))
        self.assertEqual(site.robots_origin("https://docs.example.com/guide"), ("https", "docs.example.com", 443))
        self.assertEqual(site.robots_origin("http://example.com:8080/x"), ("http", "example.com", 8080))
        self.assertNotEqual(site.robots_origin("https://example.com/"), site.robots_origin("https://docs.example.com/"))


class RobotsPerOrigin(unittest.TestCase):
    """A10 / decision 10: the rules of the origin a page belongs to, fetched on first use, matched against
    path?query — in the mapper's decisions, in its hop checks and in fetch-bundle's."""

    def setUp(self):
        self.server = LocalServer()
        b = self.server.base
        self.work = tmpdir("mnemon-origin-")
        self.env = clean_env({"MNEMON_FETCH_URL_REWRITE": "https://docs.example.test=%s/docs-host;https://example.test=%s" % (b, b)})
        self.env["RENDER_URL_CMD"] = write_stub(self.work, "render-fail.sh", "#!/usr/bin/env bash\nexit 11\n")
        links = '<a href="/search">s</a> <a href="/search?q=x">q</a> <a href="https://docs.example.test/guide">g</a>'
        self.server.add("/", page("Landing", "<nav>%s</nav>" % links))
        self.server.add("/robots.txt", "User-agent: *\nDisallow: /search?\n", content_type="text/plain")
        self.server.add("/search", page("Search"))
        self.server.add("/docs-host/robots.txt", "User-agent: *\nDisallow: /\n", content_type="text/plain")
        self.server.add("/docs-host/guide", page("Guide"))

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.work, ignore_errors=True)

    def plan_path(self):
        plan = {"schema": "mnemon-site-plan/1", "landing": "https://example.test/", "created": "2026-10-15T12:00:00Z",
                "discovery": {"sources": [], "robots": {"fetched": True, "status": 200, "groups": ["*"], "rules_applied": ["Disallow: /search?"]}},
                "hosts": ["example.test", "docs.example.test"], "caps": {"max_pages": 40},
                "pages": [{"n": 1, "url": "https://example.test/", "canonical_url": "https://example.test/", "role": "landing", "include": True,
                           "reason": "landing", "title": None, "discovered_by": "landing", "render": None},
                          {"n": 2, "url": "https://example.test/search", "canonical_url": "https://example.test/search", "role": "product",
                           "include": True, "reason": "product page", "title": None, "discovered_by": "landing-links", "render": None},
                          {"n": 3, "url": "https://example.test/search?q=x", "canonical_url": "https://example.test/search?q=x", "role": "product",
                           "include": True, "reason": "product page", "title": None, "discovered_by": "landing-links", "render": None},
                          {"n": 4, "url": "https://docs.example.test/guide", "canonical_url": "https://docs.example.test/guide", "role": "docs",
                           "include": True, "reason": "docs", "title": None, "discovered_by": "landing-links", "render": None}]}
        path = os.path.join(self.work, "plan.json")
        with open(path, "w") as fh:
            json.dump(plan, fh)
        return path

    def test_fetch_bundle_consults_the_destination_origin(self):
        out = os.path.join(self.work, "bundle")
        rc, so, se = run_cli([CS, "fetch-bundle", self.plan_path(), "--out", out, "--report", os.path.join(self.work, "b.json")],
                             env=self.env, timeout=120)
        self.assertEqual(rc, 0, se)
        with open(os.path.join(out, "manifest.json")) as fh:
            manifest = json.load(fh)
        by = {p["url"]: p for p in manifest["pages"]}
        self.assertEqual(by["https://example.test/search"]["disposition"], "captured")
        self.assertEqual(by["https://example.test/search?q=x"]["disposition"], "fetch-failed", "Disallow: /search? matches the query form")
        self.assertIn("robots", by["https://example.test/search?q=x"]["error"])
        self.assertEqual(by["https://docs.example.test/guide"]["disposition"], "fetch-failed", "the docs origin's own robots disallows it")
        self.assertIn("robots", by["https://docs.example.test/guide"]["error"])
        paths = [r[1] for r in self.server.requests()]
        self.assertEqual(paths.count("/docs-host/robots.txt"), 1, "the docs origin's robots.txt was fetched once")
        self.assertNotIn("/docs-host/guide", paths, "a disallowed destination is never fetched")
        self.assertNotIn("/search?q=x", paths)
        self.assertIn("/search", paths)

    def test_map_excludes_by_the_page_origin_and_the_query(self):
        plan_path = os.path.join(self.work, "mapped.json")
        rc, so, se = run_cli([CS, "map", "https://example.test/", "--out", plan_path, "--host", "docs.example.test"], env=self.env, timeout=120)
        self.assertEqual(rc, 0, se)
        with open(plan_path) as fh:
            plan = json.load(fh)
        by = {p["url"]: p for p in plan["pages"]}
        self.assertEqual((by["https://example.test/search"]["include"], by["https://example.test/search"]["role"]), (True, "product"))
        self.assertEqual(by["https://example.test/search?q=x"]["reason"], "excluded: robots")
        self.assertEqual(by["https://docs.example.test/guide"]["reason"], "excluded: robots")
        self.assertEqual(plan["discovery"]["robots"]["rules_applied"], ["Disallow: /search?"], "the landing's robots stays the plan's summary")
        paths = [r[1] for r in self.server.requests()]
        self.assertNotIn("/docs-host/guide", paths)
        self.assertEqual(paths.count("/docs-host/robots.txt"), 1)


class MapCli(unittest.TestCase):
    def test_map_without_out_prints_the_plan_and_leaves_no_temp_dir(self):
        server = LocalServer()
        work = tmpdir("mnemon-mapcli-")
        try:
            server.add("/", page("Landing", '<a href="/product">p</a>'))
            server.add("/product", page("Product"))
            env = clean_env()
            env["RENDER_URL_CMD"] = write_stub(work, "render-fail.sh", "#!/usr/bin/env bash\nexit 11\n")
            before = set(d for d in os.listdir(tempfile.gettempdir()) if d.startswith("mnemon-map-"))
            rc, so, se = run_cli([CS, "map", server.base + "/", "--json"], env=env, timeout=120)
            self.assertEqual(rc, 0, se)
            plan = json.loads(so)
            self.assertEqual(plan["schema"], "mnemon-site-plan/1")
            self.assertEqual([p["url"] for p in plan["pages"] if p["include"]], [server.base + "/", server.base + "/product"])
            after = set(d for d in os.listdir(tempfile.gettempdir()) if d.startswith("mnemon-map-"))
            self.assertEqual(after - before, set(), "the mapper's work directory is removed on exit")
        finally:
            server.close()
            shutil.rmtree(work, ignore_errors=True)


class Aggregate(unittest.TestCase):
    """Decision 14: text parts (README, llms.txt) and pages without a reference are neutral in the aggregate."""

    def bundle(self, pages):
        work = tmpdir("mnemon-agg-")
        manifest_pages, members = [], []
        for n, (url, role, body) in enumerate(pages, start=1):
            nn = "%02d" % n
            data = body.encode("utf-8")
            fr = {"schema": "mnemon-fetch/1", "method": "direct", "charset": "utf-8", "kind": "page", "metadata": {},
                  "acquisition": {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 100}}
            frb = reportmod.canonical_bytes(fr)
            with open(os.path.join(work, nn + ".html"), "wb") as fh:
                fh.write(data)
            with open(os.path.join(work, nn + ".fetch.json"), "wb") as fh:
                fh.write(frb)
            manifest_pages.append({"n": n, "url": url, "role": role, "file": nn + ".html", "fetch_report": nn + ".fetch.json",
                                   "sha256": reportmod.sha256_bytes(data), "bytes": len(data), "disposition": "captured",
                                   "error_class": None, "error": None, "raw_kind": "response", "http_status": 200})
        manifest = {"schema": "mnemon-site-bundle/1", "landing": pages[0][0], "created": "2026-10-15T12:00:00Z",
                    "pages": manifest_pages, "members": members}
        with open(os.path.join(work, "manifest.json"), "wb") as fh:
            fh.write(reportmod.canonical_bytes(manifest))
        return work

    def test_text_parts_are_neutral(self):
        env = clean_env()
        old = dict(os.environ)
        os.environ.update(env)
        try:
            work = self.bundle([("https://example.test/", "landing", page("Landing")),
                                ("https://raw.githubusercontent.com/o/r/HEAD/README.md", "github_readme", "# Repo\n\nA readme with more than six words.\n")])
            code, report = site.assemble(work, os.path.join(work, "out.md"))
            self.assertEqual(code, 0)
            by = {p["role"]: p for p in report["pages"]}
            self.assertEqual((by["github_readme"]["converter"], by["github_readme"]["completeness"]), ("text", "unknown"))
            self.assertEqual(report["completeness"], "complete", "an unknown text part does not keep the aggregate from complete")
            shutil.rmtree(work, ignore_errors=True)
            work = self.bundle([("https://example.test/", "landing", page("Landing")),
                                ("https://example.test/p", "product", "<html><head><title>P</title></head><body><p>Short only.</p>"
                                 "<table><tr><td>a</td></tr></table></body></html>")])
            code, report = site.assemble(work, os.path.join(work, "out.md"))
            self.assertEqual(code, 0)
            self.assertEqual(report["pages"][1]["completeness"], "unknown", "no eligible line: unknown per part")
            self.assertEqual(report["completeness"], "complete")
            shutil.rmtree(work, ignore_errors=True)
            lost = page("Product") + "<table><tr><td>a</td><td>b</td></tr></table>"
            work = self.bundle([("https://example.test/", "landing", page("Landing")), ("https://example.test/p", "product", lost)])
            code, report = site.assemble(work, os.path.join(work, "out.md"))
            self.assertEqual(code, 0)
            if report["pages"][1]["completeness"] == "partial":
                self.assertEqual(report["completeness"], "partial", "a judged part that is partial still decides")
            shutil.rmtree(work, ignore_errors=True)
        finally:
            os.environ.clear()
            os.environ.update(old)


class Bundle(SiteBase):
    def test_members_dispositions_and_the_stdout_line(self):
        fixture = expected_shapes("site-manifest.json")
        assert_same_keys(self, self.manifest, fixture, "mnemon-site-bundle/1")
        assert_same_keys(self, self.manifest["pages"][0], fixture["pages"][0], "pages[0]")
        assert_same_keys(self, self.manifest["members"][0], fixture["members"][0], "members[0]")
        files = sorted(os.listdir(self.bundle))
        members = sorted(m["path"] for m in self.manifest["members"])
        self.assertEqual(members, [f for f in files if f != "manifest.json"], "members cover every file but the manifest")
        self.assertIn("plan.json", members)
        with open(os.path.join(self.bundle, "plan.json"), "rb") as fh, open(self.plan_path, "rb") as fh2:
            self.assertEqual(fh.read(), fh2.read(), "plan.json is a byte copy of the input plan")
        stdout = json.loads(self.bundle_stdout)
        assert_same_keys(self, stdout, expected_shapes("site-bundle-stdout.json"), "stdout line")
        self.assertEqual(self.bundle_stdout.count("\n"), 0)
        self.assertEqual(stdout["raw_sha256"], reportmod.sha256_bytes(self.manifest_bytes))
        self.assertEqual(stdout["raw_bytes"], sum(m["bytes"] for m in self.manifest["members"]))
        self.assertEqual(self.manifest_bytes, reportmod.canonical_bytes(self.manifest), "canonical JSON bytes")
        by = {p["url"].replace(self.server.base, "") or "/": p for p in self.manifest["pages"]}
        self.assertEqual(by["/"]["disposition"], "captured")
        self.assertEqual(by["/"]["n"], 1)
        self.assertEqual([p["role"] for p in self.manifest["pages"]][0], "landing")
        self.assertEqual((by["/docs/guide"]["disposition"], by["/docs/guide"]["error_class"], by["/docs/guide"]["http_status"]),
                         ("fetch-failed", "server-error", 500))
        self.assertIsNone(by["/docs/guide"]["file"])
        self.assertTrue(by["/docs/guide"]["fetch_report"].endswith(".fetch.json"))
        self.assertIn(by["/docs/guide"]["fetch_report"], members, "a failed page's fetch report is a member")
        for p in self.manifest["pages"]:
            if p["disposition"] == "captured":
                with open(os.path.join(self.bundle, p["file"]), "rb") as fh:
                    data = fh.read()
                self.assertEqual((reportmod.sha256_bytes(data), len(data)), (p["sha256"], p["bytes"]))
                self.assertEqual(p["raw_kind"], "response")
        self.assertEqual(sum(1 for p in self.manifest["pages"]), sum(1 for p in self.plan["pages"] if p["include"]))
        self.assertTrue(all(p["disposition"] in ("captured", "fetch-failed", "skipped-timeout") for p in self.manifest["pages"]))

    def test_requests_paced_per_host(self):
        reqs = self.server.requests()
        self.assertGreaterEqual(len(reqs), 8)

    def test_verify(self):
        sha = reportmod.sha256_bytes(self.manifest_bytes)
        rc, so, se = run_cli([CS, "verify", self.bundle, "--sha256", sha])
        self.assertEqual(rc, 0, se)
        rc, so, se = run_cli([CS, "verify", self.bundle, "--sha256", "0" * 64])
        self.assertEqual((rc, se.strip()), (1, "evidence-mismatch manifest"))
        copy = os.path.join(self.work, "copy")
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(self.bundle, copy)
        with open(os.path.join(copy, "02.html"), "ab") as fh:
            fh.write(b"<!-- edited -->")
        self.assertEqual(site.verify_bundle(copy, sha), (False, "evidence-mismatch 02.html"))
        os.unlink(os.path.join(copy, "02.html"))
        self.assertEqual(site.verify_bundle(copy, sha), (False, "evidence-missing 02.html"))
        shutil.copy(os.path.join(self.bundle, "02.html"), os.path.join(copy, "02.html"))
        self.assertEqual(site.verify_bundle(copy, sha), (True, None))
        with open(os.path.join(copy, "plan.json"), "ab") as fh:
            fh.write(b"\n")
        self.assertEqual(site.verify_bundle(copy, sha), (False, "evidence-mismatch plan.json"), "a changed plan.json is a mismatch")
        shutil.copy(self.plan_path, os.path.join(copy, "plan.json"))
        with open(os.path.join(copy, "stray.txt"), "w") as fh:
            fh.write("x")
        self.assertEqual(site.verify_bundle(copy, sha), (False, "evidence-mismatch stray.txt"))
        os.unlink(os.path.join(copy, "stray.txt"))
        os.unlink(os.path.join(copy, "03.html"))
        os.symlink(os.path.join(self.bundle, "03.html"), os.path.join(copy, "03.html"))
        self.assertEqual(site.verify_bundle(copy, sha), (False, "evidence-mismatch 03.html"))

    def test_assemble(self):
        out = os.path.join(self.work, "capture.md")
        rep = os.path.join(self.work, "site.json")
        rc, so, se = run_cli([CS, "assemble", self.bundle, "--out", out, "--report", rep], env=self.env, timeout=300)
        self.assertEqual(rc, 0, se)
        with open(rep, encoding="utf-8") as fh:
            report = json.load(fh)
        fixture = expected_shapes("site-report.json")
        assert_same_keys(self, report, fixture, "mnemon-site/1")
        assert_same_keys(self, report["pages"][0], fixture["pages"][0], "pages[0]")
        by = {p["url"].replace(self.server.base, "") or "/": p for p in report["pages"]}
        self.assertEqual(by["/"]["disposition"], "converted")
        self.assertEqual(by["/docs/guide"]["disposition"], "fetch-failed")
        self.assertEqual((by["/empty"]["disposition"], by["/empty"]["error_class"]), ("convert-failed", "convert"))
        readme = [p for p in report["pages"] if p["role"] == "github_readme"][0]
        self.assertEqual((readme["converter"], readme["completeness"], readme["disposition"]), ("text", "unknown", "converted"))
        llms = by["/llms.txt"]
        self.assertEqual(llms["converter"], "text")
        self.assertEqual(report["included"], len(self.manifest["pages"]))
        self.assertEqual(report["captured"], sum(1 for p in self.manifest["pages"] if p["disposition"] == "captured"))
        self.assertEqual(report["converted"], sum(1 for p in report["pages"] if p["disposition"] == "converted"))
        self.assertEqual(report["completeness"], "partial")
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Landing — site capture\n\n## Part 1 — %s/\n" % self.server.base))
        parts = [l for l in text.split("\n") if l.startswith("## Part ")]
        self.assertEqual(len(parts), report["converted"])
        self.assertNotIn("/empty", "\n".join(parts), "a convert-failed page is omitted from the composite")
        self.assertIn("(github_readme)", text)
        self.assertIn("A readme with more than six words in it.", text)
        order = [p["n"] for p in self.manifest["pages"] if by[p["url"].replace(self.server.base, "") or "/"]["disposition"] == "converted"]
        self.assertEqual(order, sorted(order), "parts in plan order")
        self.assertEqual(sum(p["words"] for p in report["pages"]), report["words"])

    def test_assemble_fails_when_the_landing_has_no_text(self):
        copy = os.path.join(self.work, "nolanding")
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(self.bundle, copy)
        with open(os.path.join(copy, "01.html"), "w") as fh:
            fh.write("<html><body></body></html>")
        code, report = site.assemble(copy, os.path.join(self.work, "x.md"))
        self.assertEqual((code, report["error_class"]), (1, "convert"))
        self.assertFalse(os.path.exists(os.path.join(self.work, "x.md")))


class BundleLoop(unittest.TestCase):
    def setUp(self):
        self.server = LocalServer()
        self.work = tmpdir("mnemon-loop-")
        self.env = clean_env()
        self.env["RENDER_URL_CMD"] = write_stub(self.work, "render-fail.sh", "#!/usr/bin/env bash\nexit 11\n")

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.work, ignore_errors=True)

    def plan(self, pages, landing=None):
        landing = landing or self.server.base + "/"
        plan = {"schema": "mnemon-site-plan/1", "landing": landing, "created": "2026-10-15T12:00:00Z",
                "discovery": {"sources": [], "robots": {"fetched": False, "status": None, "groups": [], "rules_applied": []}},
                "hosts": ["127.0.0.1"], "pages": [], "caps": {"max_pages": 40}}
        for i, (path, role, render) in enumerate(pages, start=1):
            url = self.server.base + path
            plan["pages"].append({"n": i, "url": url, "canonical_url": url, "role": role, "include": True, "reason": role,
                                  "title": None, "discovered_by": "landing", "render": render})
        path = os.path.join(self.work, "plan.json")
        with open(path, "w") as fh:
            json.dump(plan, fh)
        return path

    def run_bundle(self, plan_path, extra=(), timeout=120):
        out = os.path.join(self.work, "bundle")
        shutil.rmtree(out, ignore_errors=True)
        rep = os.path.join(self.work, "bundle.json")
        rc, so, se = run_cli([CS, "fetch-bundle", plan_path, "--out", out, "--report", rep] + list(extra), env=self.env, timeout=timeout)
        manifest = None
        if os.path.exists(os.path.join(out, "manifest.json")):
            with open(os.path.join(out, "manifest.json")) as fh:
                manifest = json.load(fh)
        report = None
        if os.path.exists(rep):
            with open(rep) as fh:
                report = json.load(fh)
        return rc, so, se, manifest, report, out

    def test_site_timeout_marks_the_rest_skipped(self):
        self.server.add("/", page("Landing"))
        self.server.add("/slow", page("Slow"), delay=3.0)
        self.server.add("/after", page("After"))
        plan = self.plan([("/", "landing", None), ("/slow", "product", None), ("/after", "product", None)])
        rc, so, se, manifest, report, out = self.run_bundle(plan, ["--site-timeout", "3"])
        self.assertEqual(rc, 0, se)
        disp = [p["disposition"] for p in manifest["pages"]]
        self.assertEqual(disp[0], "captured")
        self.assertEqual(disp[2], "skipped-timeout")
        self.assertIn(disp[1], ("skipped-timeout", "fetch-failed"))
        self.assertTrue(os.path.exists(os.path.join(out, "manifest.json")))

    def test_landing_failure_is_fatal(self):
        self.server.add("/", "<html><body>down</body></html>", status=503)
        plan = self.plan([("/", "landing", None)])
        rc, so, se, manifest, report, out = self.run_bundle(plan)
        self.assertEqual(rc, 1, se)
        self.assertIsNone(manifest)
        self.assertEqual((report["error_class"], report["stage"]), ("unavailable", "landing"))
        self.assertEqual(so.strip(), "")

    def test_no_render_prohibits_a_plan_forced_render(self):
        marker = os.path.join(self.work, "rendered")
        self.env["RENDER_URL_CMD"] = write_stub(self.work, "render-mark.sh", "#!/usr/bin/env bash\ntouch %s\nprintf '%s'\n" % (marker, page("Rendered").replace("'", "")))
        self.server.add("/", page("Landing"))
        self.server.add("/p", page("Page"))
        plan = self.plan([("/", "landing", None), ("/p", "product", True)])
        rc, so, se, manifest, report, out = self.run_bundle(plan)
        self.assertEqual(rc, 0, se)
        self.assertTrue(os.path.exists(marker), "pages[].render: true forces a render")
        self.assertEqual(manifest["pages"][1]["raw_kind"], "rendered")
        os.unlink(marker)
        rc, so, se, manifest, report, out = self.run_bundle(plan, ["--no-render"])
        self.assertEqual(rc, 0, se)
        self.assertFalse(os.path.exists(marker), "a global --no-render wins over the plan")
        self.assertEqual(manifest["pages"][1]["raw_kind"], "response")

    def test_hop_rejections_off_site_and_robots(self):
        self.server.add("/", page("Landing"))
        self.server.add("/robots.txt", "User-agent: *\nDisallow: /private\n", content_type="text/plain")
        self.server.add("/away", "", status=302, headers=[("Location", "https://other.example/x")])
        self.server.add("/to-private", "", status=302, headers=[("Location", "/private/x")])
        self.server.add("/private/x", page("Private"))
        plan = self.plan([("/", "landing", None), ("/away", "product", None), ("/to-private", "product", None)])
        rc, so, se, manifest, report, out = self.run_bundle(plan)
        self.assertEqual(rc, 0, se)
        by = {p["url"].replace(self.server.base, ""): p for p in manifest["pages"]}
        self.assertEqual(by["/away"]["disposition"], "fetch-failed")
        self.assertIn("redirect-off-site", by["/away"]["error"])
        self.assertEqual(by["/to-private"]["disposition"], "fetch-failed")
        self.assertIn("robots", by["/to-private"]["error"])
        self.assertEqual(self.server.count("/private/x"), 0, "a disallowed destination is never fetched")

    def test_site_timeout_tears_down_the_renderer(self):
        pidfile = os.path.join(self.work, "pids")
        self.env["RENDER_URL_CMD"] = write_stub(self.work, "render-sleep.sh", "#!/usr/bin/env bash\nsleep 60 &\necho \"$$ $!\" > %s\nwait\n" % pidfile)
        self.server.add("/", '<html><body><div id="root"></div><script src="/a.js"></script></body></html>')
        plan = self.plan([("/", "landing", None)])
        t0 = time.monotonic()
        rc, so, se, manifest, report, out = self.run_bundle(plan, ["--site-timeout", "2", "--render-timeout", "30"], timeout=60)
        self.assertEqual(rc, 124, se)
        self.assertEqual(report["error_class"], "timeout")
        self.assertLess(time.monotonic() - t0, 20)
        with open(pidfile) as fh:
            pids = [int(p) for p in fh.read().split()]
        for pid in pids:
            self.assertTrue(wait_gone(pid, 5), "pid %d survived the site timeout" % pid)


class SectionAndSitemaps(unittest.TestCase):
    """G22 on a docs landing: sitemap indexes two levels deep under one budget, the landing's section's
    sub-sitemaps first; the cap keeps the landing's section first."""

    def setUp(self):
        self.server = LocalServer()
        b = self.server.base
        self.work = tmpdir("mnemon-section-")
        self.env = clean_env()
        self.env["RENDER_URL_CMD"] = write_stub(self.work, "render-fail.sh", "#!/usr/bin/env bash\nexit 11\n")
        links = " ".join('<a href="%s">%s</a>' % (h, h) for h in ["/help/air/intro.html", "/products/p1/", "/store/", "/pricing", "/blog/post-1"])
        self.server.add("/help/air/landing.html", page("Air docs", "<nav>%s</nav>" % links))
        self.server.add("/robots.txt", "User-agent: *\nSitemap: %s/sitemap_index.xml\n" % b, content_type="text/plain")
        self.server.add("/sitemap.xml", "<html><body>no sitemap here</body></html>", status=404)
        self.server.add("/sitemap_index.xml", "<sitemapindex><sitemap><loc>%s/products/sitemap.xml</loc></sitemap>"
                        "<sitemap><loc>%s/help/sitemap.xml</loc></sitemap></sitemapindex>" % (b, b), content_type="application/xml")
        self.server.add("/help/sitemap.xml", "<sitemapindex><sitemap><loc>%s/help/idea/sitemap.xml</loc></sitemap>"
                        "<sitemap><loc>%s/help/air/sitemap.xml</loc></sitemap></sitemapindex>" % (b, b), content_type="application/xml")
        self.server.add("/help/air/sitemap.xml", "<urlset>%s</urlset>" % "".join("<url><loc>%s/help/air/%s.html</loc></url>" % (b, n) for n in "abc"),
                        content_type="application/xml")
        self.server.add("/help/idea/sitemap.xml", "<urlset><url><loc>%s/help/idea/x.html</loc></url><url><loc>%s/help/idea/y.html</loc></url></urlset>" % (b, b),
                        content_type="application/xml")
        self.server.add("/products/sitemap.xml", "<urlset><url><loc>%s/products/p1/</loc></url><url><loc>%s/products/p2/</loc></url></urlset>" % (b, b),
                        content_type="application/xml")
        for p, t in [("/help/air/intro.html", "Intro"), ("/help/air/a.html", "A"), ("/help/air/b.html", "B"), ("/help/air/c.html", "C"),
                     ("/help/idea/x.html", "X"), ("/help/idea/y.html", "Y"), ("/products/p1/", "P1"), ("/products/p2/", "P2"),
                     ("/store/", "Store"), ("/pricing", "Pricing"), ("/blog/post-1", "Post")]:
            self.server.add(p, page(t))

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.work, ignore_errors=True)

    def map(self, *extra):
        out = os.path.join(self.work, "plan-%d.json" % len(os.listdir(self.work)))
        rc, so, se = run_cli([CS, "map", self.server.base + "/help/air/landing.html", "--out", out] + list(extra), env=self.env, timeout=300)
        self.assertEqual(rc, 0, se)
        with open(out, encoding="utf-8") as fh:
            plan = json.load(fh)
        return plan, {p["url"].replace(self.server.base, "") or "/": p for p in plan["pages"]}

    def test_sitemap_indexes_two_levels_deep_section_first(self):
        plan, by = self.map()
        paths = [r[1] for r in self.server.requests()]
        for sm in ("/sitemap_index.xml", "/help/sitemap.xml", "/help/air/sitemap.xml", "/help/idea/sitemap.xml", "/products/sitemap.xml"):
            self.assertIn(sm, paths, "the index is followed two levels deep: %s" % sm)
        self.assertLess(paths.index("/help/sitemap.xml"), paths.index("/products/sitemap.xml"), "the landing's section's sub-sitemap is fetched first")
        self.assertLess(paths.index("/help/air/sitemap.xml"), paths.index("/help/idea/sitemap.xml"))
        sources = {s["name"]: s for s in plan["discovery"]["sources"]}
        self.assertEqual(sources["sitemap"]["yield"], 6, "a, b, c, x, y, p2 (p1 was a landing link, counted once)")
        self.assertEqual((by["/help/air/a.html"]["role"], by["/help/air/a.html"]["include"]), ("docs", True))
        self.assertEqual((by["/help/idea/y.html"]["role"], by["/help/idea/y.html"]["include"]), ("docs", True))

    def test_cap_keeps_the_landing_section_first(self):
        plan, by = self.map("--max-pages", "4")
        included = [p["url"].replace(self.server.base, "") for p in plan["pages"] if p["include"]]
        self.assertEqual(included[0], "/help/air/landing.html")
        self.assertEqual(len(included), 4)
        self.assertTrue(all(u.startswith("/help/air/") for u in included), included)
        self.assertEqual(by["/help/idea/x.html"]["reason"], "excluded: cap", "a docs page outside the section is capped before any section page")
        self.assertEqual(by["/pricing"]["reason"], "excluded: cap")
        self.assertEqual(by["/products/p1/"]["reason"], "excluded: cap", "...and so is a product page outside it, whatever its role priority")

    def test_sitemap_budget(self):
        """At most 25 sitemaps and 20 000 URLs are read per map: an index of 40 sub-sitemaps stops at the
        25th fetch; an index of 1 000-URL sub-sitemaps stops at 20 000 URLs."""
        b = self.server.base
        self.server.add("/small_index.xml", "<sitemapindex>%s</sitemapindex>" % "".join("<sitemap><loc>%s/s/%d.xml</loc></sitemap>" % (b, i) for i in range(40)),
                        content_type="application/xml")
        self.server.add("/wide_index.xml", "<sitemapindex>%s</sitemapindex>" % "".join("<sitemap><loc>%s/w/%d.xml</loc></sitemap>" % (b, i) for i in range(40)),
                        content_type="application/xml")
        for i in range(40):
            self.server.add("/s/%d.xml" % i, "<urlset>%s</urlset>" % "".join("<url><loc>%s/u/%d/%d</loc></url>" % (b, i, j) for j in range(100)),
                            content_type="application/xml")
            self.server.add("/w/%d.xml" % i, "<urlset>%s</urlset>" % "".join("<url><loc>%s/v/%d/%d</loc></url>" % (b, i, j) for j in range(1000)),
                            content_type="application/xml")
        env = clean_env()
        old = dict(os.environ)
        os.environ.update(env)
        try:
            policy = policymod.Policy(connect_timeout=5, read_timeout=5)
            self.server.reset()
            budget = site.SitemapBudget()
            urls, note = site.sitemap_urls(policy, b + "/small_index.xml", proc.Deadline(60), budget=budget, section="/")
            self.assertEqual((budget.sitemaps, len(urls)), (site.SITEMAP_MAX_SITEMAPS, 24 * 100), "the index and 24 sub-sitemaps")
            self.assertEqual(len(self.server.requests()), site.SITEMAP_MAX_SITEMAPS)
            self.assertIn("budget", note or "")
            self.server.reset()
            budget = site.SitemapBudget()
            urls, note = site.sitemap_urls(policy, b + "/wide_index.xml", proc.Deadline(60), budget=budget, section="/")
            self.assertEqual(len(urls), site.SITEMAP_MAX_URLS, "20 000 URLs read, then nothing more")
            self.assertEqual(budget.sitemaps, 21, "the index and the 20 sub-sitemaps that filled the URL budget")
            self.assertIn("budget", note or "")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
