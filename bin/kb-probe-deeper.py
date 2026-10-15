#!/usr/bin/env python3
"""kb-probe-deeper — deterministic depth discovery for Mnemon capture.

Given a posted URL, decide whether the captured landing is thin/SPA and, if so, discover same-entity deeper
targets (llms.txt / GitHub repo / docs) one hop away, classify each by same-entity confidence + evidence,
and emit JSON. NO LLM, NO content folding — discovery + classification only. The gateway's depth fold
consumes this JSON to fetch high-confidence targets.

Design: docs/specs/2026-06-01-depth-aware-capture.md (D1/D2/D3/D6b); capture layer §3.2, §12 A8:
- every request goes through the capture policy (kb_fetch over capture.policy): SSRF checks per hop, caps,
  no proxies, no credentials; the probe never writes anything;
- with --landing-file the landing is never fetched or rendered (A8): `rendered` and `final_url` come from
  --context; without a landing file the landing is fetched through the policy and an SPA landing is
  rendered (render-url.sh --html) unless the context says render: off;
- the page-declared canonical (<link rel=canonical>, og:url) is `declared_canonical_url` — `canonical_url`
  is reserved for the computed identity (D5);
- --budget-s bounds every probe request; what the budget does not cover is recorded in `errors`.

Usage:  kb-probe-deeper.py <url> [--landing-file PATH] [--thin-words N] [--budget-s S] [--context JSON]
                           [--policy JSON]
Output: JSON to stdout: {posted_url, final_url, declared_canonical_url, thin, spa, word_count, rendered,
        candidates: [{url, type, same_entity_confidence, evidence}], errors}.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_fetch  # noqa: E402
import lib_url  # noqa: E402
from capture import proc  # noqa: E402

THIN_WORDS_DEFAULT = 400
TAG = re.compile(r"<[^>]+>")


def _is_html(text: str) -> bool:
    h = text.lstrip()[:256].lower()
    return h.startswith("<!doctype") or h.startswith("<html") or "<head" in h


def _visible(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", TAG.sub(" ", body)).strip()


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
    has_root = bool(re.search(r'<div[^>]+id=["\'](root|app|__next|__nuxt)["\']', html, re.I))
    bundles = len(re.findall(r'<script[^>]+src=', html, re.I))
    return words < 200 and (has_root or bundles >= 1)


def links(html: str):
    return (re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I)
            + re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', html, re.I))


def probe(posted_url: str, landing_html, thin_words: int, context: dict, deadline: proc.Deadline) -> dict:
    out = {"posted_url": posted_url, "final_url": context.get("final_url") or posted_url, "declared_canonical_url": "",
           "thin": False, "spa": False, "word_count": None, "rendered": bool(context.get("rendered", False)),
           "candidates": [], "errors": []}
    supplied = landing_html is not None
    if not supplied:
        final, status, html = kb_fetch.fetch(posted_url, deadline=deadline)
        if final is None or not isinstance(status, int) or status >= 400:
            out["errors"].append("landing fetch failed: %s" % status)
            return out
        out["final_url"] = final
    else:
        html = landing_html

    words = len(_visible(html).split())
    out["word_count"] = words
    out["thin"] = words < thin_words
    out["spa"] = looks_spa(html, words)
    out["declared_canonical_url"] = (meta(html, "og:url") or (re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', html, re.I)
        or [None, ""])[1])

    # an SPA landing is rendered to expose JS-injected links — never under a supplied landing (A8)
    scan_html = html
    if out["spa"] and not supplied and context.get("render", "auto") != "off":
        rendered = kb_fetch.render_html(out["final_url"], deadline=deadline)
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

    def budget_left() -> bool:
        if deadline.expired():
            if "budget exhausted" not in out["errors"]:
                out["errors"].append("budget exhausted")
            return False
        return True

    # 1. GitHub repos linked from the (rendered) page — no search in v1 (D3)
    repos = {}
    for href in links(scan_html):
        if "://" in href and lib_url.github_repo(href):
            repos.setdefault(lib_url.github_repo(href), href)
    for repo in list(repos)[:4]:
        if not budget_left():
            break
        conf, ev = "medium", ["linked_from_landing"]
        api = kb_fetch.fetch("https://api.github.com/repos/%s" % repo, cap=200_000, deadline=deadline)
        homepage = ""
        if api[0] is not None and api[1] == 200:
            try:
                homepage = (json.loads(api[2]).get("homepage") or "")
            except Exception:
                pass
        elif api[1] != 200:
            out["errors"].append("github api %s: %s" % (repo, api[1]))
        if homepage and lib_url.same_site(homepage, out["final_url"]):
            conf, ev = "high", ["linked_from_landing", "repo_homepage_matches_domain"]
        elif landing_dom and repo.split("/")[-1].lower() in landing_dom.split(".")[0]:
            conf, ev = "high", ["linked_from_landing", "repo_name_matches_domain"]
        add("https://github.com/%s" % repo, "github", conf, ev)

    # 2. llms.txt — content-verified (must be text, not the SPA HTML shell)
    for base in sorted({"https://%s" % landing_dom, "https://%s" % landing_host}):
        if not budget_left():
            break
        url = "%s/llms.txt" % base
        ok, status, text = kb_fetch.fetch(url, cap=300_000, deadline=deadline)
        if ok is not None and status == 200 and not _is_html(text) \
                and len([ln for ln in text.splitlines() if ln.strip()]) >= 3:
            add(url, "llms.txt", "high", ["same_domain", "llms_txt_verified"])

    # 3. docs — content-verified (reject SPA catch-all that echoes the landing shell)
    for url in ("https://docs.%s/" % landing_dom, "https://%s/docs" % landing_host):
        if not budget_left():
            break
        ok, status, text = kb_fetch.fetch(url, cap=500_000, deadline=deadline)
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
    ap.add_argument("--budget-s", type=float, default=None, help="the probe's whole budget in seconds")
    ap.add_argument("--context", default=None, help="JSON: final_url, http_status, acquisition, render, external, budget_s")
    ap.add_argument("--policy", default=None, help="JSON: fetch_user_agent, fetch_timeout, fetch_max_bytes")
    a = ap.parse_args()
    proc.close_lock_fd()
    proc.install_term_handler()
    context = {}
    if a.context:
        try:
            context = json.loads(a.context)
        except ValueError as exc:
            print("kb-probe-deeper: --context is not valid JSON: %s" % exc, file=sys.stderr)
            return 2
    if a.policy:
        try:
            kb_fetch.configure(json.loads(a.policy))
        except ValueError as exc:
            print("kb-probe-deeper: --policy is not valid JSON: %s" % exc, file=sys.stderr)
            return 2
    budget = a.budget_s if a.budget_s is not None else context.get("budget_s")
    deadline = proc.Deadline(budget) if budget is not None else proc.Deadline.never()
    landing = None
    if a.landing_file:
        with open(a.landing_file, encoding="utf-8", errors="ignore") as f:
            landing = f.read()
    print(json.dumps(probe(a.url, landing, a.thin_words, context, deadline), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
