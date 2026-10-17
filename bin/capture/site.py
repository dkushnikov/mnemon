"""capture.site — map · fetch-bundle · verify · assemble (spec §3.6, D9, §12 A5, A10, A12).

`map_site` discovers same-site pages (the landing's links — rendered when the landing is a JavaScript
shell — the sitemaps named by robots.txt and /sitemap.xml with their indexes followed two levels deep under
one budget of 25 sitemaps and 20 000 URLs, the landing's section's sub-sitemaps first; /llms.txt; a docs
table of contents; the linked GitHub README) and writes a reviewable plan (`mnemon-site-plan/1`) with a
role, an include decision and a reason per page; host eligibility is explicit (no public-suffix guessing),
robots.txt is honoured per RFC 9309 (product token `mnemon`, longest match over path?query, the rules of
each origin a page belongs to — fetched on first use — unreachable = everything disallowed, 4xx = no rules),
roles are anchored path rules (`en` and `en-*` are never `localized`; `product` is scoped to the landing's
first path segment unless the landing is the site root), `--include` restricts the plan to the landing and
the matching pages, `--exclude` then removes, and the cap keeps pages under the landing's section first,
then by role priority, then in discovery order. `fetch_bundle` owns the
page loop and its deadline: every included page through the chain of §3.3 (host and robots eligibility
checked per hop, against the destination origin's robots), ≥ 1 s
between requests to one host, `NN.html` + `NN.fetch.json` per page, `plan.json` (a byte copy) and
`manifest.json` last — the manifest binds every member with its size and hash and carries the acquisition
dispositions (`captured`, `fetch-failed`, `skipped-timeout`). `verify_bundle` recomputes the manifest hash
and every member (no symlink, no escaping path). `assemble` converts every captured page in process and
writes the composite with the `mnemon-site/1` report (conversion dispositions, the aggregate completeness).
Stdlib only, python 3.9.
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from . import check, convert, fetch, proc, resolve
from . import policy as policymod
from . import report as reportmod

PRODUCT_TOKEN = "mnemon"
DEFAULT_MAX_PAGES = 40
PACE_S = 1.0
MAP_PAGE_CAP = 1000000
# sitemap indexes are followed two levels deep (an index → an index → a urlset), at most this many
# sitemaps and URLs read per map (G22)
SITEMAP_MAX_DEPTH = 2
SITEMAP_MAX_SITEMAPS = 25
SITEMAP_MAX_URLS = 20000
ELIGIBLE_SUBDOMAINS = ("docs", "help", "developer", "developers", "blog", "api", "learn", "support")
INCLUDED_ROLES = frozenset(("landing", "product", "docs", "llms_txt", "blog", "pricing", "careers", "job", "github_readme"))
ROLE_PRIORITY = ("landing", "product", "docs", "llms_txt", "pricing", "blog", "careers", "job", "github_readme")
NON_HTML_EXT = frozenset(("pdf", "zip", "gz", "tgz", "tar", "bz2", "xz", "7z", "dmg", "exe", "msi", "pkg", "deb", "rpm",
                          "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "mp3", "mp4", "m4a", "wav", "ogg", "webm",
                          "mov", "avi", "css", "js", "mjs", "json", "xml", "rss", "atom", "woff", "woff2", "ttf", "otf",
                          "eot", "csv", "xls", "xlsx", "doc", "docx", "ppt", "pptx"))
LOCALE_LANGS = frozenset(("ar", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr", "he", "hi", "hr",
                          "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb", "nl", "no", "pl", "pt", "ro", "ru", "sk", "sl",
                          "sr", "sv", "th", "tr", "uk", "vi", "zh"))
LEGAL_SEGMENTS = frozenset(("privacy", "privacy-policy", "terms", "terms-of-service", "terms-of-use", "legal", "cookie",
                            "cookies", "cookie-policy", "imprint", "eula", "gdpr"))
HOWTO_SEGMENTS = frozenset(("troubleshooting", "faq", "changelog", "release-notes"))
BLOG_SEGMENTS = frozenset(("blog", "posts", "news"))
DOCS_SEGMENTS = frozenset(("docs", "help", "guide", "manual", "reference"))
HOWTO_TITLES = ("how to", "install", "getting started with git")

# The discovery patterns are bounded and nesting-free (G26 applied to the mapper): an `<a` tag's attribute
# block is at most 4 000 characters and never crosses `<` or `>`, an attribute value at most 4 000 characters
# without a newline, a `<loc>` value likewise — so 70 000 `<a ` without a `>` (210 KB) or 50 000 `<loc>`
# without a `</loc>` cost one bounded scan per occurrence, never a rescan to the end of the document (the
# old `[^>]*` and `.*?` were quadratic: minutes for the links, 100 s for the sitemap).
_HREF = re.compile(r"<a\b[^<>]{0,4000}?\shref\s{0,20}=\s{0,20}(?:\"([^\"\n]{0,4000})\"|'([^'\n]{0,4000})'|([^\s<>\"']{1,4000}))",
                   re.IGNORECASE)
_LOC = re.compile(r"<loc>\s{0,100}([^<>\n]{0,4000}?)\s{0,100}</loc>", re.IGNORECASE)
_GITHUB = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[#?].*)?$")
_LOCALE = re.compile(r"^([a-z]{2})(?:-[a-z]{2})?$")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- hosts and roles ---------------------------------------------------------------------------------------

def strip_www(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def host_eligible(host: str, landing_host: str, extra_hosts=()) -> bool:
    """Same-site by rule, never by public-suffix guessing (§3.6)."""
    h, base = strip_www(host), strip_www(landing_host)
    if not h or not base:
        return False
    if h == base:
        return True
    for sub in ELIGIBLE_SUBDOMAINS:
        if h == "%s.%s" % (sub, base):
            return True
    return h in set(x.lower() for x in extra_hosts)


def _segments(path: str) -> List[str]:
    return [s for s in path.split("/") if s]


def is_locale_segment(segment: str) -> bool:
    m = _LOCALE.match(segment.lower())
    return bool(m) and m.group(1) in LOCALE_LANGS


def is_localized_segment(segment: str) -> bool:
    """A locale tag that marks a translated variant: `en` and `en-*` are exempt (an English docs tree under
    /en/ or /en-us/ is the site's content, not a localization of it)."""
    s = segment.lower()
    return is_locale_segment(s) and not (s == "en" or s.startswith("en-"))


def landing_section(landing_url: str) -> str:
    """The landing's section: the landing URL's directory — `/help/air/` for a docs page, `/air/` for a
    product landing (`/air` too), `/` for a site root (G22)."""
    path = urlsplit(landing_url).path or "/"
    last = path.rsplit("/", 1)[-1]
    if "." in last:
        return path[:path.rfind("/") + 1]
    return path if path.endswith("/") else path + "/"


def in_section(url: str, landing_url: str, section: str) -> bool:
    """A page is under the landing's section when it is on the landing's host and its path starts with the
    section (every page is, for a root landing)."""
    if strip_www(urlsplit(url).hostname or "") != strip_www(urlsplit(landing_url).hostname or ""):
        return False
    return (urlsplit(url).path or "/").startswith(section)


def shares_section(sitemap_url: str, section: str) -> bool:
    """A sub-sitemap shares the landing's section when its path lies under the section or its directory is
    an ancestor of it (`/help/sitemap.xml` for the section `/help/air/`)."""
    path = urlsplit(sitemap_url).path or "/"
    directory = path[:path.rfind("/") + 1]
    return path.startswith(section) or section.startswith(directory)


def _landing_locales(landing_url: str, landing_final: Optional[str]) -> set:
    locales = set()
    for lu in (landing_url, landing_final):
        lsegs = [s.lower() for s in _segments(urlsplit(lu).path or "/")] if lu else []
        if lsegs and is_locale_segment(lsegs[0]):
            locales.add(lsegs[0])
    return locales


def _role_segments(path: str, landing_locales: set) -> List[str]:
    """The path segments a role is read from: an exempt locale prefix (/en/, /en-us/, the landing's own
    locale) is not a segment."""
    segs = [s.lower() for s in _segments(path)]
    if segs and is_locale_segment(segs[0]) and (not is_localized_segment(segs[0]) or segs[0] in landing_locales):
        segs = segs[1:]
    return segs


def role_for(url: str, title: Optional[str], landing_url: str, landing_final: Optional[str] = None) -> str:
    """The role by anchored path and title rules, in the order of §3.6. The landing's own locale prefix (of
    the given URL or of its final URL after redirects) is never `localized`. `product` is scoped to the
    landing's first path segment when the landing is not the site root: a same-site page outside it is
    `other` unless its role is docs, blog, pricing, careers or job (G23)."""
    parts = urlsplit(url)
    host = strip_www(parts.hostname or "")
    lhost = strip_www(urlsplit(landing_url).hostname or "")
    path = parts.path or "/"
    segs = [s.lower() for s in _segments(path)]
    if len(segs) == 2 and segs[0] in ("jobs", "careers"):
        return "job"
    if len(segs) == 1 and segs[0] in ("jobs", "careers"):
        return "careers"
    if any(s in LEGAL_SEGMENTS for s in segs):
        return "legal"
    landing_locales = _landing_locales(landing_url, landing_final)
    for i, s in enumerate(segs):
        if is_localized_segment(s) and not (i == 0 and s in landing_locales):
            return "localized"
    sub = host.split(".")[0] if host != lhost else ""
    if sub and sub != lhost.split(".")[0] and is_localized_segment(sub) and host.endswith(lhost):
        return "localized"
    segs = _role_segments(path, landing_locales)
    t = (title or "").strip().lower()
    if any(s in HOWTO_SEGMENTS for s in segs) or any(t.startswith(p) for p in HOWTO_TITLES):
        return "howto"
    if segs and segs[0] in BLOG_SEGMENTS or (sub == "blog"):
        return "blog"
    if segs and segs[0] in DOCS_SEGMENTS or sub in ("docs", "help", "developer", "developers", "api", "learn", "support"):
        return "docs"
    if segs and segs[0] == "pricing":
        return "pricing"
    if 1 <= len(segs) <= 2:
        lsegs = _role_segments(urlsplit(landing_url).path or "/", landing_locales)
        if lsegs and (host != lhost or segs[0] != lsegs[0]):
            return "other"
        return "product"
    return "other"


# --- robots (RFC 9309) ------------------------------------------------------------------------------------

class Robots:
    def __init__(self):
        self.groups: List[Tuple[List[str], List[Tuple[bool, str]]]] = []
        self.sitemaps: List[str] = []
        self.state = "none"         # ok | none (no rules) | unreachable (everything disallowed)
        self.status: Optional[int] = None
        self.fetched = False

    @classmethod
    def parse(cls, text: str) -> "Robots":
        r = cls()
        r.state = "ok"
        agents: List[str] = []
        rules: List[Tuple[bool, str]] = []
        collecting_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not collecting_agents and (agents or rules):
                    r.groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                collecting_agents = True
            elif key in ("allow", "disallow"):
                collecting_agents = False
                if agents:
                    rules.append((key == "allow", value))
            elif key == "sitemap":
                r.sitemaps.append(value)
            else:
                collecting_agents = False
        if agents or rules:
            r.groups.append((agents, rules))
        return r

    @classmethod
    def unreachable(cls, status=None) -> "Robots":
        r = cls()
        r.state = "unreachable"
        r.status = status
        return r

    @classmethod
    def no_rules(cls, status=None) -> "Robots":
        r = cls()
        r.state = "none"
        r.status = status
        r.fetched = status is not None
        return r

    def applied(self, token: str = PRODUCT_TOKEN) -> Tuple[List[str], List[Tuple[bool, str]]]:
        """The merged groups whose user-agent matches the product token, else the `*` groups."""
        token = token.lower()
        matched = [(a, r) for a, r in self.groups if any(x == token or x.startswith(token) for x in a)]
        if not matched:
            matched = [(a, r) for a, r in self.groups if "*" in a]
        agents, rules = [], []
        for a, r in matched:
            agents.extend(x for x in a if x not in agents)
            rules.extend(r)
        return agents, rules

    def allowed(self, path: str, token: str = PRODUCT_TOKEN) -> bool:
        if self.state == "unreachable":
            return False
        if self.state != "ok":
            return True
        _, rules = self.applied(token)
        best = None      # (length, allow)
        for allow, pattern in rules:
            if not pattern:
                if allow:
                    continue
                continue            # an empty Disallow allows everything: no rule
            if _robots_match(pattern, path):
                length = len(pattern)
                if best is None or length > best[0] or (length == best[0] and allow and not best[1]):
                    best = (length, allow)
        return True if best is None else best[1]

    def summary(self) -> dict:
        agents, rules = self.applied()
        return {"fetched": self.fetched, "status": self.status, "groups": agents,
                "rules_applied": ["%s: %s" % ("Allow" if a else "Disallow", p) for a, p in rules]}


def _robots_match(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in core)
    if anchored:
        regex += "$"
    return re.match(regex, path) is not None


def robots_path(url: str) -> str:
    """What the rules are matched against (RFC 9309): the path plus the query when there is one."""
    parts = urlsplit(url)
    path = parts.path or "/"
    return path + "?" + parts.query if parts.query else path


def path_query(url: str) -> str:
    """What `--include` / `--exclude` regexes are searched in: the URL's path plus `?query`, never the
    scheme or the host — so an anchored `^/blog/` does what it says (G10)."""
    return robots_path(url)


def robots_origin(url: str) -> Tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, (parts.hostname or "").lower(), port or (443 if scheme == "https" else 80)


def fetch_robots(policy: policymod.Policy, url: str, deadline: proc.Deadline) -> Robots:
    """The robots.txt of the origin of `url` (scheme, host, port)."""
    parts = urlsplit(url)
    robots_url = "%s://%s/robots.txt" % (parts.scheme, parts.netloc)
    try:
        resp = policy.get(robots_url, deadline=deadline)
    except policymod.PolicyError as exc:
        if exc.kind in ("timeout", "connection", "tls", "too-many-redirects"):
            return Robots.unreachable()
        return Robots.no_rules()
    if 500 <= resp.status <= 599:
        return Robots.unreachable(resp.status)
    if resp.status != 200:
        return Robots.no_rules(resp.status)
    r = Robots.parse(resp.text())
    r.status = 200
    r.fetched = True
    return r


class RobotsCache:
    """`(scheme, host, port) → Robots`, fetched lazily the first time an origin is consulted (A10: the rules
    of every origin a site page lands on, not the landing's applied to all); matched against path?query."""

    def __init__(self, policy: policymod.Policy, deadline: proc.Deadline):
        self.policy = policy
        self.deadline = deadline
        self.by_origin: Dict[Tuple[str, str, int], Robots] = {}

    def seed(self, url: str, robots: Robots) -> None:
        self.by_origin[robots_origin(url)] = robots

    def for_url(self, url: str) -> Robots:
        key = robots_origin(url)
        r = self.by_origin.get(key)
        if r is None:
            r = fetch_robots(self.policy, url, self.deadline)
            self.by_origin[key] = r
        return r

    def allowed(self, url: str) -> bool:
        return self.for_url(url).allowed(robots_path(url))


# --- discovery ---------------------------------------------------------------------------------------------

def links_of(html: str, base_url: str) -> List[str]:
    out = []
    for m in _HREF.finditer(html):
        href = htmlmod.unescape(m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        out.append(urljoin(base_url, href))
    return out


def github_repo(url: str) -> Optional[Tuple[str, str]]:
    m = _GITHUB.match(url)
    if not m or m.group(1).lower() in ("orgs", "sponsors", "settings", "marketplace", "topics", "search", "features", "login"):
        return None
    return m.group(1), m.group(2)


class SitemapBudget:
    """What one map may read from sitemaps: at most SITEMAP_MAX_SITEMAPS fetched and SITEMAP_MAX_URLS
    collected, across every sitemap and index the map follows (G22)."""

    def __init__(self, max_sitemaps: int = SITEMAP_MAX_SITEMAPS, max_urls: int = SITEMAP_MAX_URLS):
        self.max_sitemaps = max_sitemaps
        self.max_urls = max_urls
        self.sitemaps = 0
        self.urls = 0

    def exhausted(self) -> bool:
        return self.sitemaps >= self.max_sitemaps or self.urls >= self.max_urls


def sitemap_urls(policy: policymod.Policy, sitemap_url: str, deadline: proc.Deadline, depth: int = 0,
                 budget: Optional[SitemapBudget] = None, section: str = "/") -> Tuple[List[str], Optional[str]]:
    """The page URLs of a sitemap: an index is followed two levels deep (an index → an index → a urlset),
    its sub-sitemaps that share the landing's section fetched first, under one budget of sitemaps and URLs."""
    budget = budget if budget is not None else SitemapBudget()
    if budget.exhausted():
        return [], "%s: the sitemap budget (%d sitemaps, %d urls) is spent" % (sitemap_url, budget.max_sitemaps, budget.max_urls)
    budget.sitemaps += 1
    try:
        resp = policy.get(sitemap_url, deadline=deadline)
    except policymod.PolicyError as exc:
        return [], "%s: %s" % (sitemap_url, exc.kind)
    if resp.status != 200:
        return [], "%s: HTTP %d" % (sitemap_url, resp.status)
    text = resp.text()
    locs = [htmlmod.unescape(l) for l in _LOC.findall(text)]
    if "<sitemapindex" in text.lower():
        if depth >= SITEMAP_MAX_DEPTH:
            return [], "%s: a sitemap index nested %d deep is not followed" % (sitemap_url, depth + 1)
        out: List[str] = []
        notes: List[str] = []
        children = sorted(locs, key=lambda u: 0 if shares_section(u, section) else 1)      # stable: the index's order within a rank
        for child in children:
            if budget.exhausted():
                notes.append("%s: the sitemap budget (%d sitemaps, %d urls) is spent" % (child, budget.max_sitemaps, budget.max_urls))
                break
            urls, note = sitemap_urls(policy, child, deadline, depth + 1, budget, section)
            out.extend(urls)
            if note:
                notes.append(note)
        return out, ("; ".join(notes) or None)
    room = max(0, budget.max_urls - budget.urls)
    taken = locs[:room]
    budget.urls += len(taken)
    note = None
    if len(locs) > room:
        note = "%s: %d of %d urls read (the sitemap budget of %d urls is spent)" % (sitemap_url, len(taken), len(locs), budget.max_urls)
    return taken, note


def helptoc_urls(doc: object, base_url: str) -> List[str]:
    out: List[str] = []

    def walk(o):
        if isinstance(o, dict):
            u = o.get("url")
            if isinstance(u, str) and u and not u.startswith(("#", "javascript:")):
                out.append(urljoin(base_url, u))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return out


def is_html_body(resp: policymod.Response) -> bool:
    ct = resp.content_type or ""
    if ct.startswith("text/html") or ct.startswith("application/xhtml"):
        return True
    head = resp.body[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


class _Candidate:
    __slots__ = ("url", "canonical", "discovered_by", "role", "include", "reason", "title", "render")

    def __init__(self, url: str, canonical: str, discovered_by: str):
        self.url = url
        self.canonical = canonical
        self.discovered_by = discovered_by
        self.role = "other"
        self.include = False
        self.reason = ""
        self.title = None
        self.render = None


def map_site(landing_url: str, policy: policymod.Policy, *, max_pages: int = DEFAULT_MAX_PAGES, includes=(), excludes=(),
             deadline: Optional[proc.Deadline] = None, fetch_opts: Optional[fetch.Options] = None, extra_hosts=(),
             tmp_dir: Optional[str] = None) -> dict:
    """Discover the same-site pages of a landing and build the plan (`mnemon-site-plan/1`). The landing raw is
    staged in `tmp_dir` (a private temporary directory allocated and removed here when none is given)."""
    own_tmp = tmp_dir is None
    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="mnemon-map-")
    try:
        return _map_site(landing_url, policy, max_pages, includes, excludes, deadline, fetch_opts, extra_hosts, tmp_dir)
    finally:
        if own_tmp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _map_site(landing_url, policy, max_pages, includes, excludes, deadline, fetch_opts, extra_hosts, tmp_dir) -> dict:
    deadline = deadline or proc.Deadline.never()
    landing_url = resolve.canonicalize(landing_url).url
    landing_host = urlsplit(landing_url).hostname or ""
    hosts = [landing_host.lower()] + [h.lower() for h in extra_hosts if h.lower() != landing_host.lower()]
    section = landing_section(landing_url)
    sources: List[dict] = []
    # the landing through the chain (rendered when it is a JavaScript shell)
    raw_path = os.path.join(tmp_dir, ".mnemon-map-landing-%d.html" % os.getpid())
    opts = fetch_opts or fetch.Options(policy, deadline=deadline)
    opts.deadline = deadline
    opts.policy = policy
    code, landing_report = fetch.acquire(landing_url, raw_path, opts)
    landing_html = ""
    if code == 0:
        try:
            with open(raw_path, "rb") as fh:
                landing_html = fh.read().decode("utf-8", "replace")
        finally:
            try:
                os.unlink(raw_path)
            except OSError:
                pass
    landing_title = landing_report.get("metadata", {}).get("title") or check.page_title(landing_html)
    landing_final = landing_report.get("final_url") or landing_url
    # the landing origin's robots is the plan's summary; every other origin's is consulted on first use
    robots = fetch_robots(policy, landing_url, deadline)
    robots_cache = RobotsCache(policy, deadline)
    robots_cache.seed(landing_url, robots)
    candidates: Dict[str, _Candidate] = {}
    order: List[_Candidate] = []

    def add(url: str, source: str) -> bool:
        try:
            canon = resolve.canonicalize(url)
        except resolve.InvalidURL:
            return False
        if canon.url == landing_url or canon.url in candidates:
            return False
        host = urlsplit(canon.url).hostname or ""
        if not host_eligible(host, landing_host, hosts):
            return False
        c = _Candidate(canon.url, canon.url, source)
        candidates[canon.url] = c
        order.append(c)
        return True

    base_for_links = landing_final if host_eligible(urlsplit(landing_final).hostname or "", landing_host, hosts) else landing_url
    n = sum(1 for u in links_of(landing_html, base_for_links) if add(u, "landing-links"))
    sources.append({"name": "landing-links", "yield": n, "note": None if code == 0 else "landing: %s" % landing_report.get("error_class")})
    # sitemaps
    parts = urlsplit(landing_url)
    origin = "%s://%s" % (parts.scheme, parts.netloc)
    sitemap_list = [origin + "/sitemap.xml"] + [s for s in robots.sitemaps if s not in (origin + "/sitemap.xml",)]
    n, notes = 0, []
    budget = SitemapBudget()
    for sm in sitemap_list[:10]:
        urls, note = sitemap_urls(policy, sm, deadline, budget=budget, section=section)
        if note:
            notes.append(note)
        n += sum(1 for u in urls if add(u, "sitemap"))
    sources.append({"name": "sitemap", "yield": n, "note": "; ".join(notes) if notes else None})
    # llms.txt
    n, note = 0, None
    try:
        resp = policy.get(origin + "/llms.txt", deadline=deadline)
        if resp.status == 200 and not is_html_body(resp) and len([l for l in resp.text().splitlines() if l.strip()]) >= 3:
            if add(origin + "/llms.txt", "llms.txt"):
                n = 1
        elif resp.status == 200:
            note = "served the landing shell" if is_html_body(resp) else "too short"
        else:
            note = "HTTP %d" % resp.status
    except policymod.PolicyError as exc:
        note = exc.kind
    sources.append({"name": "llms.txt", "yield": n, "note": note})
    # docs table of contents: HelpTOC.json next to the first docs page, and the links of that page
    n, note = 0, None
    docs_pages = [c for c in order if role_for(c.url, None, landing_url, landing_final) == "docs" and robots_cache.allowed(c.url)]
    if docs_pages:
        first = docs_pages[0].url
        docs_base = first if first.endswith("/") else first.rsplit("/", 1)[0] + "/"
        try:
            resp = policy.get(urljoin(docs_base, "HelpTOC.json"), deadline=deadline)
            if resp.status == 200:
                try:
                    n += sum(1 for u in helptoc_urls(json.loads(resp.text()), docs_base) if add(u, "docs-toc"))
                except ValueError:
                    note = "HelpTOC.json is not JSON"
        except policymod.PolicyError as exc:
            note = "HelpTOC.json: %s" % exc.kind
        try:
            resp = policy.get(first, deadline=deadline)
            if resp.status == 200 and is_html_body(resp):
                docs_pages[0].title = check.page_title(resp.text())
                n += sum(1 for u in links_of(resp.text(), resp.final_url) if add(u, "docs-toc"))
        except policymod.PolicyError as exc:
            note = (note + "; " if note else "") + "docs page: %s" % exc.kind
    else:
        note = "no docs page discovered"
    sources.append({"name": "docs-toc", "yield": n, "note": note})
    # the linked GitHub README
    n, note = 0, None
    repo = None
    for u in links_of(landing_html, base_for_links):
        repo = github_repo(u)
        if repo:
            break
    if repo:
        readme = "https://raw.githubusercontent.com/%s/%s/HEAD/README.md" % repo
        try:
            resp = policy.get(readme, deadline=deadline)
            if resp.status == 200 and not is_html_body(resp) and resp.body.strip():
                if "raw.githubusercontent.com" not in hosts:
                    hosts.append("raw.githubusercontent.com")
                c = _Candidate(readme, readme, "github-readme")
                c.role = "github_readme"
                candidates[readme] = c
                order.append(c)
                n = 1
            else:
                note = "README: HTTP %d" % resp.status
        except policymod.PolicyError as exc:
            note = "README: %s" % exc.kind
    else:
        note = "no repository linked from the landing"
    sources.append({"name": "github-readme", "yield": n, "note": note})

    # decisions: roles, robots, content type, then the include/exclude regexes (searched in the URL's path
    # plus ?query — never the scheme or host — so an anchored `^/blog/` works; G10): with any --include the
    # plan keeps the landing and only the pages matching some include regex (a matching page the rules
    # excluded by role is re-admitted; robots and content-type exclusions stand), --exclude then removes,
    # and the cap applies last (G21)
    inc_re = [re.compile(r) for r in includes]
    exc_re = [re.compile(r) for r in excludes]
    for c in order:
        path = urlsplit(c.url).path or "/"
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if c.role != "github_readme":
            c.role = "llms_txt" if c.discovered_by == "llms.txt" else role_for(c.url, c.title, landing_url, landing_final)
        page_robots = robots_cache.for_url(c.url)
        if page_robots.state == "unreachable":
            c.include, c.reason = False, "excluded: robots-unreachable"
        elif not page_robots.allowed(robots_path(c.url)):
            c.include, c.reason = False, "excluded: robots"
        elif ext in NON_HTML_EXT and c.role not in ("github_readme", "llms_txt"):
            c.include, c.reason = False, "excluded: content-type"
        elif c.role in INCLUDED_ROLES:
            c.include, c.reason = True, "%s page" % c.role if c.role == "product" else c.role
        else:
            c.include, c.reason = False, "excluded: %s" % c.role
        if inc_re:
            matched = any(r.search(path_query(c.url)) for r in inc_re)
            if c.reason.startswith("excluded: ") and c.reason not in ("excluded: robots", "excluded: robots-unreachable", "excluded: content-type"):
                if matched:
                    c.include, c.reason = True, "%s (--include)" % c.role
            elif c.include and not matched:
                c.include, c.reason = False, "excluded: include-regex"
        if c.include and any(r.search(path_query(c.url)) for r in exc_re):
            c.include, c.reason = False, "excluded: exclude-regex"
    # the included pages are fetched once, in cap order — (1) the pages under the landing's section
    # (docs-toc pages count as in it), (2) role priority, (3) discovery order (G22): title, final host,
    # content type; a page the fetch excludes (off-site, non-HTML) frees its slot; past the cap the rest
    # is `excluded: cap` unfetched
    included = [c for c in order if c.include]
    rank = {r: i for i, r in enumerate(ROLE_PRIORITY)}
    included.sort(key=lambda c: (0 if c.discovered_by == "docs-toc" or in_section(c.url, landing_url, section) else 1,
                                 rank.get(c.role, 99), order.index(c)))
    slots = max(0, max_pages - 1)
    kept = 0
    for c in included:
        if kept >= slots:
            c.include, c.reason = False, "excluded: cap"
            continue
        if deadline.expired():
            kept += 1
            continue
        if c.title is not None and c.role == "github_readme":
            kept += 1
            continue
        def hop(url, _c=c):
            h = urlsplit(url).hostname or ""
            if not host_eligible(h, landing_host, hosts):
                return "redirect-off-site"
            if url != _c.url and not robots_cache.allowed(url):
                return "robots"
            return None
        try:
            resp = policy.with_max_bytes(MAP_PAGE_CAP).get(c.url, deadline=deadline, hop_check=hop)
        except policymod.PolicyError as exc:
            if exc.kind == "hop-rejected":
                c.include, c.reason = False, "excluded: %s" % exc.message
                continue
            kept += 1          # a transient failure keeps the page: fetch-bundle records the disposition
            continue
        if c.role in ("github_readme", "llms_txt"):
            kept += 1
            continue
        if not is_html_body(resp):
            c.include, c.reason = False, "excluded: content-type"
            continue
        c.title = check.page_title(resp.text())
        new_role = role_for(c.url, c.title, landing_url, landing_final)
        if new_role != c.role and new_role not in INCLUDED_ROLES and not c.reason.endswith("(--include)"):
            c.role, c.include, c.reason = new_role, False, "excluded: %s" % new_role
            continue
        kept += 1
    pages = [{"n": 1, "url": landing_url, "canonical_url": landing_url, "role": "landing", "include": True, "reason": "landing",
              "title": landing_title, "discovered_by": "landing", "render": None}]
    for i, c in enumerate(order, start=2):
        pages.append({"n": i, "url": c.url, "canonical_url": c.canonical, "role": c.role, "include": c.include,
                      "reason": c.reason, "title": c.title, "discovered_by": c.discovered_by, "render": c.render})
    return {"schema": reportmod.SITE_PLAN, "landing": landing_url, "created": now_iso(),
            "discovery": {"sources": sources, "robots": robots.summary()}, "hosts": hosts, "pages": pages,
            "caps": {"max_pages": max_pages}}


# --- fetch-bundle ------------------------------------------------------------------------------------------

def _safe_member(path: str) -> bool:
    return bool(path) and not path.startswith(("/", "\\")) and ".." not in path.split("/") and os.path.normpath(path) == path \
        and "\\" not in path


def fetch_bundle(plan: dict, plan_bytes: bytes, out_dir: str, policy: policymod.Policy, *, site_timeout: float = 900.0,
                 acquire_timeout: float = 300.0, render_timeout: float = 90.0, render: str = "auto", external_mode: str = "off",
                 fetchers=("firecrawl", "jina"), render_cmd: Optional[str] = None, render_max_bytes: int = 20000000,
                 thin_words: int = 60) -> Tuple[int, dict, dict]:
    """The page loop under one deadline; (exit code, the manifest, the small fetch-bundle report)."""
    deadline = proc.Deadline(site_timeout)
    landing_url = plan["landing"]
    landing_host = urlsplit(landing_url).hostname or ""
    hosts = list(plan.get("hosts") or [landing_host])
    os.makedirs(out_dir, exist_ok=True)
    policy.min_interval_s = max(policy.min_interval_s, PACE_S)
    robots_cache = RobotsCache(policy, deadline)       # every origin's robots, fetched on first use (A10)
    included = [p for p in plan["pages"] if p.get("include")]
    included.sort(key=lambda p: (0 if p.get("role") == "landing" else 1, int(p["n"])))
    pages: List[dict] = []
    members: List[dict] = []
    report = {"schema": "mnemon-site-fetch/1", "landing": landing_url, "bundle": out_dir, "raw_sha256": None, "raw_bytes": None,
              "pages": [], "error_class": None, "error": None, "retryable": None, "stage": None, "elapsed_ms": 0}
    start = time.monotonic()

    def write_member(name: str, data: bytes) -> dict:
        reportmod.write_bytes(os.path.join(out_dir, name), data)
        m = {"path": name, "bytes": len(data), "sha256": reportmod.sha256_bytes(data)}
        members.append(m)
        return m

    def finish(code: int, error_class=None, error=None, retryable=None, stage=None) -> Tuple[int, dict, dict]:
        report.update({"error_class": error_class, "error": error, "retryable": retryable, "stage": stage,
                       "elapsed_ms": int((time.monotonic() - start) * 1000), "pages": [
                           {"n": p["n"], "url": p["url"], "disposition": p["disposition"], "error_class": p["error_class"]} for p in pages]})
        return code, {}, report

    for page in included:
        nn = "%02d" % int(page["n"])
        is_landing = page.get("role") == "landing" or page is included[0]
        entry = {"n": int(page["n"]), "url": page["url"], "role": page.get("role"), "file": None, "fetch_report": None,
                 "sha256": None, "bytes": None, "disposition": None, "error_class": None, "error": None, "raw_kind": None,
                 "http_status": None}
        if deadline.expired():
            entry.update({"disposition": "skipped-timeout", "error_class": "timeout", "error": "site_timeout reached before this page"})
            pages.append(entry)
            if is_landing:
                return finish(fetch.EXIT_TIMEOUT, "timeout", "site_timeout reached before the landing", True, "landing")
            continue
        page_url = page["url"]

        def hop(url, _first=page_url, _landing=is_landing):
            h = urlsplit(url).hostname or ""
            if not host_eligible(h, landing_host, hosts):
                return "redirect-off-site"
            if (url != _first or not _landing) and not robots_cache.allowed(url):
                return "robots"
            return None

        page_render = render
        if render != "off" and page.get("render") is True:
            page_render = "force"
        opts = fetch.Options(policy, render=page_render, external_mode=external_mode if render != "off" else "off",
                             fetchers=fetchers, acquire_timeout=acquire_timeout, render_timeout=render_timeout,
                             render_cmd=render_cmd, render_max_bytes=render_max_bytes, thin_words=thin_words,
                             hop_check=hop, deadline=deadline.sub(acquire_timeout))
        raw_path = os.path.join(out_dir, nn + ".html")
        code, fetch_report = fetch.acquire(page_url, raw_path, opts)
        write_member(nn + ".fetch.json", reportmod.canonical_bytes(fetch_report))
        entry["fetch_report"] = nn + ".fetch.json"
        entry["http_status"] = fetch_report.get("http_status")
        if code == 0:
            with open(raw_path, "rb") as fh:
                data = fh.read()
            m = {"path": nn + ".html", "bytes": len(data), "sha256": reportmod.sha256_bytes(data)}
            members.append(m)
            entry.update({"file": nn + ".html", "sha256": m["sha256"], "bytes": m["bytes"], "disposition": "captured",
                          "raw_kind": fetch_report.get("raw_kind")})
        else:
            try:
                os.unlink(raw_path)
            except OSError:
                pass
            if code == fetch.EXIT_TIMEOUT and deadline.expired():
                entry["disposition"] = "skipped-timeout"
            else:
                entry["disposition"] = "fetch-failed"
            entry["error_class"] = fetch_report.get("error_class") or "fetch"
            entry["error"] = fetch_report.get("error")
            if is_landing:
                pages.append(entry)
                return finish(fetch.EXIT_TIMEOUT if code == fetch.EXIT_TIMEOUT else fetch.EXIT_FAILED,
                              entry["error_class"], "landing: %s" % entry["error"], fetch_report.get("retryable"), "landing")
        pages.append(entry)
    write_member("plan.json", plan_bytes)
    manifest = {"schema": reportmod.SITE_BUNDLE, "landing": landing_url, "created": now_iso(), "pages": pages, "members": members}
    manifest_bytes = reportmod.canonical_bytes(manifest)
    reportmod.write_bytes(os.path.join(out_dir, "manifest.json"), manifest_bytes)
    reportmod.fsync_dir(out_dir)
    report["raw_sha256"] = reportmod.sha256_bytes(manifest_bytes)
    report["raw_bytes"] = sum(m["bytes"] for m in members)
    report["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    report["pages"] = [{"n": p["n"], "url": p["url"], "disposition": p["disposition"], "error_class": p["error_class"]} for p in pages]
    return 0, manifest, report


# --- verify ------------------------------------------------------------------------------------------------

def verify_bundle(directory: str, expected_sha256: str) -> Tuple[bool, Optional[str]]:
    """(ok, reason): `evidence-missing <member>` or `evidence-mismatch <member|manifest>` on failure."""
    manifest_path = os.path.join(directory, "manifest.json")
    try:
        st = os.lstat(manifest_path)
    except OSError:
        return False, "evidence-missing manifest"
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        return False, "evidence-mismatch manifest"
    with open(manifest_path, "rb") as fh:
        data = fh.read()
    if reportmod.sha256_bytes(data) != (expected_sha256 or "").lower():
        return False, "evidence-mismatch manifest"
    try:
        manifest = json.loads(data.decode("utf-8"))
    except ValueError:
        return False, "evidence-mismatch manifest"
    members = manifest.get("members") if isinstance(manifest, dict) else None
    if not isinstance(members, list):
        return False, "evidence-mismatch manifest"
    seen = set()
    for m in members:
        path = m.get("path") if isinstance(m, dict) else None
        if not isinstance(path, str) or not _safe_member(path):
            return False, "evidence-mismatch %s" % path
        seen.add(path)
        full = os.path.join(directory, path)
        try:
            st = os.lstat(full)
        except OSError:
            return False, "evidence-missing %s" % path
        if os.path.islink(full) or not os.path.isfile(full):
            return False, "evidence-mismatch %s" % path
        if st.st_size != m.get("bytes"):
            return False, "evidence-mismatch %s" % path
        if reportmod.sha256_file(full) != m.get("sha256"):
            return False, "evidence-mismatch %s" % path
    for root, dirs, files in os.walk(directory):
        for d in dirs:
            if os.path.islink(os.path.join(root, d)):
                return False, "evidence-mismatch %s" % os.path.relpath(os.path.join(root, d), directory)
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), directory)
            if rel == "manifest.json":
                continue
            if rel not in seen:
                return False, "evidence-mismatch %s" % rel
    return True, None


# --- assemble ----------------------------------------------------------------------------------------------

CAPTURE_TEXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "capture-text.py")


class PageTimeout(Exception):
    """The page's conversion worker was killed by the remaining assembly budget."""


def convert_page(raw_path: str, url: str, fetch_report_path: Optional[str], kind: str, work_dir: str,
                 deadline: proc.Deadline) -> convert.Result:
    """One page through the capture-text worker (`capture-text.py --_worker`, its own process group) under
    the remaining budget (G25): the text and the report come back by path; a killed worker — or one that
    cannot start because nothing is left — is PageTimeout, a failed one ConvertError with the report's class."""
    remaining = deadline.remaining()
    out = os.path.join(work_dir, "page.md")
    rep = os.path.join(work_dir, "page.json")
    for stale in (out, rep):
        try:
            os.unlink(stale)
        except OSError:
            pass
    argv = [sys.executable, CAPTURE_TEXT, "--_worker", raw_path, "--url", url, "--out", out, "--report", rep,
            "--raw-kind", "site", "--kind", kind, "--timeout", "%g" % (remaining if remaining is not None else convert.DEFAULT_TIMEOUT)]
    if fetch_report_path:
        argv += ["--fetch-report", fetch_report_path]
    argv += ["--_parent", str(os.getpid())]   # this process is the worker's supervisor; its watch compares against it
    res = proc.run(argv, timeout=remaining, deadline=deadline)
    if res.timed_out or not res.started:
        raise PageTimeout("conversion exceeded the assembly budget (convert_timeout): the worker was killed with its process group")
    try:
        report = reportmod.read_json(rep)
    except (OSError, ValueError):
        raise convert.ConvertError("convert", "the conversion worker wrote no report (exit %s): %s"
                                   % (res.rc, res.stderr.decode("utf-8", "replace").strip()[:200]))
    if res.rc != 0 or report.get("error_class"):
        retryable = report.get("retryable")
        raise convert.ConvertError(report.get("error_class") or "convert", report.get("error") or "conversion failed (exit %s)" % res.rc,
                                   retryable=True if retryable is None else bool(retryable), candidates=report.get("candidates"))
    with open(out, "r", encoding="utf-8") as fh:
        text = fh.read()
    return convert.Result(text, report)


def assemble(directory: str, out_path: str, *, deadline: Optional[proc.Deadline] = None) -> Tuple[int, dict]:
    """Convert every captured page through the capture-text worker under the remaining budget and write the
    composite; (exit code, the mnemon-site/1 report)."""
    deadline = deadline or proc.Deadline.never()
    manifest = reportmod.read_json(os.path.join(directory, "manifest.json"))
    work_dir = tempfile.mkdtemp(prefix="mnemon-assemble-")
    try:
        return _assemble(directory, out_path, manifest, deadline, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _assemble(directory: str, out_path: str, manifest: dict, deadline: proc.Deadline, work_dir: str) -> Tuple[int, dict]:
    pages_out: List[dict] = []
    parts: List[str] = []
    landing_title = None
    landing_blocked = False
    landing_converted = False
    landing_timeout = None
    total_words = total_bytes = 0
    for page in manifest.get("pages", []):
        entry = {"n": page["n"], "url": page["url"], "role": page.get("role"), "disposition": page.get("disposition"),
                 "converter": None, "completeness": None, "missing_count": None, "glued_count": None, "words": 0, "bytes": 0,
                 "error_class": page.get("error_class"), "error": page.get("error")}
        is_landing = page.get("role") == "landing"
        if page.get("disposition") != "captured":
            pages_out.append(entry)
            continue
        raw_path = os.path.join(directory, page["file"])
        fetch_report_path = os.path.join(directory, page["fetch_report"]) if page.get("fetch_report") else None
        try:
            with open(raw_path, "rb") as fh:
                raw = fh.read()
            fetch_report = reportmod.read_json(fetch_report_path) if fetch_report_path else {}
        except (OSError, ValueError) as exc:
            entry.update({"disposition": "convert-failed", "error_class": "convert", "error": "cannot read the member: %s" % exc})
            pages_out.append(entry)
            continue
        acq = (fetch_report.get("acquisition") or {}).get("kind")
        if is_landing and acq == "paywall":
            landing_blocked = True
        text = None
        title = None
        if page.get("role") == "github_readme" or urlsplit(page["url"]).path.endswith("/llms.txt"):
            try:
                text = raw.decode("utf-8")
                if not text.endswith("\n"):
                    text += "\n"
                entry.update({"converter": "text", "completeness": "unknown", "missing_count": 0, "glued_count": 0})
            except UnicodeDecodeError:
                entry.update({"disposition": "convert-failed", "error_class": "invalid-utf8", "error": "the text page is not UTF-8"})
        elif page.get("raw_kind") == "file":
            entry.update({"disposition": "convert-failed", "error_class": "unsupported", "error": "a file raw (PDF) cannot be assembled"})
        else:
            try:
                result = convert_page(raw_path, page["url"], fetch_report_path, fetch_report.get("kind") or "page", work_dir, deadline)
                text = result.text
                title = result.report["title"]
                entry.update({"converter": result.report["converter"], "completeness": result.report["completeness"],
                              "missing_count": result.report["missing_count"], "glued_count": result.report["glued_count"]})
            except PageTimeout as exc:
                entry.update({"disposition": "convert-failed", "error_class": "timeout", "error": str(exc)})
                if is_landing:
                    landing_timeout = str(exc)
            except convert.ConvertError as exc:
                entry.update({"disposition": "convert-failed", "error_class": exc.error_class, "error": str(exc)})
        if text is not None and text.strip():
            entry["disposition"] = "converted"
            entry["words"] = len(text.split())
            entry["bytes"] = len(text.encode("utf-8"))
            total_words += entry["words"]
            total_bytes += entry["bytes"]
            if is_landing:
                landing_converted = True
                landing_title = title or landing_title
                parts.append("## Part %d — %s\n\n%s" % (len(parts) + 1, page["url"], text.rstrip("\n")))
            else:
                parts.append("## Part %d — %s (%s)\n\n%s" % (len(parts) + 1, page["url"], page.get("role"), text.rstrip("\n")))
        elif entry["disposition"] == "captured":
            entry.update({"disposition": "convert-failed", "error_class": "convert", "error": "the conversion produced no text"})
        pages_out.append(entry)
    included = len(pages_out)
    captured = sum(1 for p in manifest.get("pages", []) if p.get("disposition") == "captured")
    converted = sum(1 for p in pages_out if p["disposition"] == "converted")
    # the aggregate (A5, decision 14): every included page converted and every converted page that has a
    # reference complete; parts without a reference (README, llms.txt, a page with no eligible line) keep
    # their `unknown` per part and are neutral here
    judged = [p for p in pages_out if p["disposition"] == "converted" and p["completeness"] not in (None, "unknown")]
    if landing_blocked:
        completeness = "blocked"
    elif converted == included and all(p["completeness"] == "complete" for p in judged):
        completeness = "complete"
    else:
        completeness = "partial"
    report = {"schema": reportmod.SITE, "pages": pages_out, "included": included, "captured": captured, "converted": converted,
              "completeness": completeness, "words": total_words, "bytes": total_bytes}
    if landing_timeout:
        # the landing's conversion was killed by the budget: a page that cannot be converted in the budget
        # will not convert on a retry either (G25)
        report.update({"error_class": "timeout", "error": "the landing page: %s" % landing_timeout, "retryable": False, "stage": "convert"})
        return fetch.EXIT_TIMEOUT, report
    if not landing_converted:
        report["error_class"] = "convert"
        report["error"] = "the landing page yielded no text"
        return fetch.EXIT_FAILED, report
    if not landing_title:
        plan_pages = []
        try:
            plan_pages = reportmod.read_json(os.path.join(directory, "plan.json")).get("pages", [])
        except (OSError, ValueError):
            pass
        for p in plan_pages:
            if p.get("role") == "landing" and p.get("title"):
                landing_title = p["title"]
    composite = "# %s — site capture\n\n%s\n" % (landing_title or manifest.get("landing"), "\n\n".join(parts))
    reportmod.write_bytes(out_path, composite.encode("utf-8"))
    return 0, report
