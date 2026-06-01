#!/usr/bin/env python3
"""kb-probe-deeper — deterministic depth discovery for Mnemon capture.

Given a posted URL, decide whether the captured landing is thin/SPA and, if so,
discover same-entity deeper targets (llms.txt / GitHub repo / docs) one hop away,
classify each by same-entity confidence + evidence, and emit JSON. NO LLM, NO
content folding — discovery + classification only. The gateway preflight consumes
this JSON to fetch high-confidence targets and build the composite source.md.

Design: docs/specs/2026-06-01-depth-aware-capture.md (D1/D2/D3/D6b).
- SPA landings are rendered (render-url.sh --html) before link-scan, so JS-injected
  repo/docs links are visible (the openviking case).
- Probe targets are content-verified, not just HEAD-200: a SPA that returns 200 for
  every path would otherwise yield false llms.txt/docs candidates.
- SSRF policy: refuses private/loopback/link-local/reserved hosts; redirect + byte
  + timeout caps. Best-effort: every probe failure is recorded, never fatal.

Usage:  kb-probe-deeper.py <url> [--landing-file PATH] [--thin-words N]
Output: JSON to stdout.
"""
from __future__ import annotations
import sys
import os
import re
import json
import socket
import ipaddress
import argparse
import subprocess
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_url  # noqa: E402

try:
    import requests
except ImportError:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mnemon-depth-probe/1.0 (+https://github.com/dkushnikov/mnemon)"
TIMEOUT = 8
MAX_BYTES = 2_000_000
THIN_WORDS_DEFAULT = 400
TAG = re.compile(r"<[^>]+>")


# ---------- SSRF guard + fetch ----------
def _host_is_safe(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for _, _, _, _, sockaddr in infos:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


def fetch(url: str, cap: int = MAX_BYTES):
    """SSRF-guarded GET. Returns (final_url, status:int|str, text)."""
    if requests is None:
        return (None, "no-requests", "")
    if not _host_is_safe(urlsplit(url).hostname or ""):
        return (None, "ssrf-blocked", "")
    try:
        s = requests.Session()
        s.max_redirects = 5
        r = s.get(url, timeout=TIMEOUT, headers={"User-Agent": UA},
                  stream=True, allow_redirects=True)
        if not _host_is_safe(urlsplit(r.url).hostname or ""):
            return (None, "ssrf-redirect", "")
        chunks, total = [], 0
        for c in r.iter_content(8192):
            chunks.append(c)
            total += len(c)
            if total >= cap:
                break
        return (r.url, r.status_code,
                b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore"))
    except Exception as e:
        return (None, f"fetch-error:{type(e).__name__}", "")


def render_html(url: str):
    """Post-JS rendered HTML via render-url.sh --html, or None."""
    rs = os.path.join(HERE, "render-url.sh")
    if not os.path.exists(rs):
        return None
    try:
        r = subprocess.run([rs, url, "--html", "--timeout", "12000"],
                           capture_output=True, text=True, timeout=45)
        return r.stdout if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def _is_html(text: str) -> bool:
    h = text.lstrip()[:256].lower()
    return h.startswith("<!doctype") or h.startswith("<html") or "<head" in h


def _visible(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", TAG.sub(" ", body)).strip()


# ---------- landing analysis ----------
def meta(html: str, *keys: str):
    for k in keys:
        m = (re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(k)
                       + r'["\'][^>]*content=["\']([^"\']+)', html, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']'
                          + re.escape(k), html, re.I))
        if m:
            return m.group(1).strip()
    return ""


def looks_spa(html: str, words: int) -> bool:
    has_root = bool(re.search(r'<div[^>]+id=["\'](root|app|__next)["\']', html, re.I))
    bundles = len(re.findall(r'<script[^>]+src=', html, re.I))
    return words < 200 and (has_root or bundles >= 1)


def links(html: str):
    return (re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I)
            + re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', html, re.I))


# ---------- main probe ----------
def probe(posted_url: str, landing_html, thin_words: int) -> dict:
    out = {"posted_url": posted_url, "final_url": posted_url, "canonical_url": "",
           "thin": False, "spa": False, "word_count": None,
           "rendered": False, "candidates": [], "errors": []}

    if landing_html is None:
        final, status, html = fetch(posted_url)
        if final is None:
            out["errors"].append(f"landing fetch failed: {status}")
            return out
        out["final_url"] = final
    else:
        html = landing_html

    words = len(_visible(html).split())
    out["word_count"] = words
    out["thin"] = words < thin_words
    out["spa"] = looks_spa(html, words)
    out["canonical_url"] = (meta(html, "og:url") or (re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', html, re.I)
        or [None, ""])[1])

    # SPA landing → render to expose JS-injected links (else github link is hidden)
    scan_html = html
    if out["spa"]:
        rendered = render_html(out["final_url"])
        if rendered:
            out["rendered"] = True
            scan_html = rendered
        else:
            out["errors"].append("spa render unavailable (chrome?) — link-scan on shell only")

    landing_dom = lib_url.registrable_domain(out["final_url"])
    landing_host = urlsplit(out["final_url"]).hostname or ""
    landing_sig = _visible(html)[:300]
    seen = set()

    def add(url, ctype, conf, evidence):
        nurl = lib_url.normalize_url(url)
        if not nurl or nurl in seen:
            return
        seen.add(nurl)
        out["candidates"].append({"url": url, "type": ctype,
                                  "same_entity_confidence": conf, "evidence": evidence})

    # 1. GitHub repos linked from the (rendered) page — no search in v1 (D3)
    repos = {}
    for href in links(scan_html):
        if "://" in href and lib_url.github_repo(href):
            repos.setdefault(lib_url.github_repo(href), href)
    for repo in list(repos)[:4]:
        conf, ev = "medium", ["linked_from_landing"]
        api = fetch(f"https://api.github.com/repos/{repo}", cap=200_000)
        homepage = ""
        if api[0] is not None and api[1] == 200:
            try:
                homepage = (json.loads(api[2]).get("homepage") or "")
            except Exception:
                pass
        elif api[1] != 200:
            out["errors"].append(f"github api {repo}: {api[1]}")
        if homepage and lib_url.same_site(homepage, out["final_url"]):
            conf, ev = "high", ["linked_from_landing", "repo_homepage_matches_domain"]
        elif landing_dom and repo.split("/")[-1].lower() in landing_dom.split(".")[0]:
            conf, ev = "high", ["linked_from_landing", "repo_name_matches_domain"]
        add(f"https://github.com/{repo}", "github", conf, ev)

    # 2. llms.txt — content-verified (must be text, not the SPA HTML shell)
    for base in {f"https://{landing_dom}", f"https://{landing_host}"}:
        url = f"{base}/llms.txt"
        ok, status, text = fetch(url, cap=300_000)
        if ok is not None and status == 200 and not _is_html(text) \
                and len([ln for ln in text.splitlines() if ln.strip()]) >= 3:
            add(url, "llms.txt", "high", ["same_domain", "llms_txt_verified"])

    # 3. docs — content-verified (reject SPA catch-all that echoes the landing shell)
    for url in (f"https://docs.{landing_dom}/", f"https://{landing_host}/docs"):
        ok, status, text = fetch(url, cap=500_000)
        if ok is None or status != 200:
            continue
        if _visible(text)[:300] == landing_sig:   # same SPA shell, not real docs
            continue
        if len(_visible(text).split()) < 50:       # empty/echo
            continue
        add(url, "docs", "high", ["same_domain", "docs_verified"])

    rank = {"high": 0, "medium": 1, "low": 2}
    trank = {"github": 0, "llms.txt": 1, "docs": 2}
    out["candidates"].sort(key=lambda c: (rank.get(c["same_entity_confidence"], 9),
                                          trank.get(c["type"], 9)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--landing-file", default=None)
    ap.add_argument("--thin-words", type=int, default=THIN_WORDS_DEFAULT)
    a = ap.parse_args()
    landing = None
    if a.landing_file:
        with open(a.landing_file, encoding="utf-8", errors="ignore") as f:
            landing = f.read()
    print(json.dumps(probe(a.url, landing, a.thin_words), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
