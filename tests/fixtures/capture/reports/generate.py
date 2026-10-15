#!/usr/bin/env python3
"""Generate the shared report fixtures of docs/specs/2026-10-15-capture-layer.md (§3.1, §3.3, §3.5, §3.6,
§3.7, §4.1, §12 A4, §12 A12).

    python3 tests/fixtures/capture/reports/generate.py        # rewrites the JSON files next to this script

One example per schema, including the failure and null shapes. These are the frozen seam contracts between the
capture layer (producers: capture-fetch.py, capture-text.py, capture-site.py, media-extract.py) and the gateway
(consumer): every producer's tests validate its real output against the fixture's key set, every consumer's
tests read the fixture, and the integrator diffs real outputs against these files. Edit this generator, never
the JSON files; the hygiene suite checks the committed files equal the generator's output. Stdlib only, 3.9.

Values are synthetic (example.com, 2026-10-15); hashes are computed from the literal strings below so a
regenerated file is byte-identical.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
NOW = "2026-10-15T12:00:00Z"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonical_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


POLICY = {"user_agent": UA, "max_bytes": 20000000, "max_redirects": 5, "connect_timeout": 15, "read_timeout": 45}
STAGE_OK = {"stage": "direct", "outcome": "ok", "kind": None, "status": 200, "bytes": 48213, "elapsed_ms": 412, "note": None}
ACQ_OK = {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 1830}
META_EMPTY = {"title": None, "authors": [], "published": None, "published_source": None, "arxiv_id": None,
              "arxiv_version": None, "arxiv_requested_version": None, "declared_canonical_url": None}

# --- mnemon-canonical/1 (§3.1) ---------------------------------------------------------------------------------
CANONICAL = {
    "schema": "mnemon-canonical/1",
    "url": "https://Example.com/post?utm_source=x&s=alpha#intro",
    "canonical_url": "https://example.com/post?s=alpha",
    "canonical_version": 1,
    "kind": "page",
    "fetch_url": "https://example.com/post?s=alpha",
    "requested_version": None,
}

# --- mnemon-fetch/1 (§3.3, A12: ext / audio_sniff) ------------------------------------------------------------
FETCH_OK = {
    "schema": "mnemon-fetch/1",
    "url": "https://example.com/post?utm_source=x",
    "fetch_url": "https://example.com/post",
    "canonical_url": "https://example.com/post",
    "canonical_version": 1,
    "kind": "page",
    "requested_version": None,
    "final_url": "https://example.com/post",
    "method": "direct",
    "raw_kind": "response",
    "http_status": 200,
    "content_type": "text/html",
    "charset": "utf-8",
    "bytes": 48213,
    "ext": None,
    "audio_sniff": None,
    "egress": "direct",
    "stages": [STAGE_OK],
    "acquisition": ACQ_OK,
    "metadata": dict(META_EMPTY, title="An example post"),
    "hops": [{"url": "https://example.com/post", "status": 200, "address": "203.0.113.10"}],
    "elapsed_ms": 412,
    "error_class": None,
    "error": None,
    "retryable": None,
    "stage": None,
    "policy": POLICY,
}

FETCH_FAILED = dict(
    FETCH_OK,
    url="https://example.com/members",
    fetch_url="https://example.com/members",
    canonical_url="https://example.com/members",
    final_url="https://example.com/members",
    method=None,
    raw_kind=None,
    http_status=200,
    content_type="text/html",
    charset="utf-8",
    bytes=0,
    stages=[{"stage": "direct", "outcome": "login", "kind": "login", "status": 200, "bytes": 2210, "elapsed_ms": 150,
             "note": "marker: sign in to continue; visible words 41"}],
    acquisition={"outcome": "login", "kind": "login", "evidence": "sign in to continue", "visible_words": 41},
    metadata=dict(META_EMPTY),
    hops=[{"url": "https://example.com/members", "status": 200, "address": "203.0.113.10"}],
    elapsed_ms=150,
    error_class="blocked",
    error="login wall: sign in to continue (no escalation past a login)",
    retryable=False,
    stage="direct",
)

FETCH_TIMEOUT = dict(
    FETCH_OK,
    url="https://example.com/app",
    fetch_url="https://example.com/app",
    canonical_url="https://example.com/app",
    final_url="https://example.com/app",
    method=None,
    raw_kind=None,
    http_status=200,
    bytes=0,
    stages=[{"stage": "direct", "outcome": "js-shell", "kind": "js-shell", "status": 200, "bytes": 1800, "elapsed_ms": 90,
             "note": "empty #root; script bytes 71%"},
            {"stage": "render", "outcome": "timeout", "kind": None, "status": None, "bytes": 0, "elapsed_ms": 90000,
             "note": "render-url.sh killed with its process group after 90 s"}],
    acquisition={"outcome": "js-shell", "kind": "js-shell", "evidence": "empty #root", "visible_words": 12},
    metadata=dict(META_EMPTY),
    hops=[{"url": "https://example.com/app", "status": 200, "address": "203.0.113.10"}],
    elapsed_ms=90090,
    error_class="timeout",
    error="render stage exceeded render_timeout=90",
    retryable=True,
    stage="render",
)

# --- mnemon-capture-text/2 (§3.5) -----------------------------------------------------------------------------
TEXT = {
    "schema": "mnemon-capture-text/2",
    "url": "https://example.com/post?utm_source=x",
    "method": "direct+pandoc",
    "bytes": 9120,
    "words": 1540,
    "title": "An example post",
    "completeness": "complete",
    "tool": {"name": "pandoc", "version": "pandoc 3.9.0.2"},
    "error_class": None,
    "error": None,
    "converter": "pandoc",
    "acquisition": "ok",
    "candidates": [
        {"converter": "defuddle", "missing_count": 2, "glued_count": 0, "headings_missing_count": 1, "chosen": False,
         "note": "dropped the section 'Safety is paramount'"},
        {"converter": "pandoc", "missing_count": 0, "glued_count": 0, "headings_missing_count": 0, "chosen": True, "note": None},
    ],
    "reference_count": 212,
    "eligible_count": 96,
    "missing_count": 0,
    "glued_count": 0,
    "headings_missing_count": 0,
    "missing_examples": [],
    "inventory": {"headings": {"html": 9, "md": 9}, "list_items": {"html": 31, "md": 31}, "tables": {"html": 1, "md": 1},
                  "code_blocks": {"html": 2, "md": 2}, "links": {"html": 44, "md": 40}, "images": {"html": 3, "md": 3}},
    "repairs": {"html": {"svg_diagrams": 1, "svg_icons": 4, "latexml_listings": 0, "data_links": 0, "tables_rewritten": 0},
                "markdown": {"data_uris": 0, "data_links": 0, "next_images": 2, "tcolorbox_residue": 0, "residue_lines": 0,
                             "comment_sections": 1, "tables_unrepaired": 0}},
    "bytes_per_word": 5.9,
    "bloat": False,
    "published_candidates": [{"value": "2026-09-01", "source": "article:published_time"}],
    "authors": [],
    "arxiv_id": None,
    "arxiv_version": None,
    "arxiv_requested_version": None,
    "limits": ["lines under six words that are not headings are not required",
               "table cells, equations and operators are not required"],
    "tools": {"defuddle": "0.8.0", "pandoc": "pandoc 3.9.0.2"},
}

# --- mnemon-site-plan/1 (§3.6) ---------------------------------------------------------------------------------
SITE_PLAN = {
    "schema": "mnemon-site-plan/1",
    "landing": "https://example.com/",
    "created": NOW,
    "discovery": {
        "sources": [{"name": "landing-links", "yield": 6, "note": None},
                    {"name": "sitemap", "yield": 5, "note": "/sitemap.xml via robots.txt Sitemap:"},
                    {"name": "llms.txt", "yield": 0, "note": "served the landing shell"},
                    {"name": "docs-toc", "yield": 2, "note": None},
                    {"name": "github-readme", "yield": 0, "note": None}],
        "robots": {"fetched": True, "status": 200, "groups": ["*"], "rules_applied": ["Disallow: /private"]},
    },
    "hosts": ["example.com", "docs.example.com"],
    "pages": [
        {"n": 1, "url": "https://example.com/", "canonical_url": "https://example.com/", "role": "landing", "include": True,
         "reason": "landing", "title": "Example", "discovered_by": "landing", "render": None},
        {"n": 2, "url": "https://example.com/product", "canonical_url": "https://example.com/product", "role": "product",
         "include": True, "reason": "product page", "title": "Product", "discovered_by": "landing-links", "render": None},
        {"n": 3, "url": "https://docs.example.com/guide", "canonical_url": "https://docs.example.com/guide", "role": "docs",
         "include": True, "reason": "docs", "title": "Guide", "discovered_by": "docs-toc", "render": None},
        {"n": 4, "url": "https://example.com/privacy", "canonical_url": "https://example.com/privacy", "role": "legal",
         "include": False, "reason": "excluded: legal", "title": "Privacy", "discovered_by": "sitemap", "render": None},
        {"n": 5, "url": "https://example.com/private/x", "canonical_url": "https://example.com/private/x", "role": "other",
         "include": False, "reason": "excluded: robots", "title": None, "discovered_by": "sitemap", "render": None},
    ],
    "caps": {"max_pages": 40},
}

# --- mnemon-site-bundle/1 manifest (§3.6, A5: acquisition dispositions only) -----------------------------------
PAGE1_HTML = "<html><head><title>Example</title></head><body><h1>Example</h1><p>landing text</p></body></html>\n"
PAGE2_HTML = "<html><head><title>Product</title></head><body><h1>Product</h1><p>product text</p></body></html>\n"
PLAN_BYTES = canonical_bytes(SITE_PLAN)
FETCH1 = canonical_bytes(dict(FETCH_OK, url="https://example.com/", fetch_url="https://example.com/",
                              canonical_url="https://example.com/", final_url="https://example.com/", bytes=len(PAGE1_HTML)))
FETCH2 = canonical_bytes(dict(FETCH_OK, url="https://example.com/product", fetch_url="https://example.com/product",
                              canonical_url="https://example.com/product", final_url="https://example.com/product",
                              bytes=len(PAGE2_HTML)))
MEMBERS = [
    {"path": "01.html", "bytes": len(PAGE1_HTML), "sha256": sha(PAGE1_HTML)},
    {"path": "01.fetch.json", "bytes": len(FETCH1), "sha256": hashlib.sha256(FETCH1).hexdigest()},
    {"path": "02.html", "bytes": len(PAGE2_HTML), "sha256": sha(PAGE2_HTML)},
    {"path": "02.fetch.json", "bytes": len(FETCH2), "sha256": hashlib.sha256(FETCH2).hexdigest()},
    {"path": "plan.json", "bytes": len(PLAN_BYTES), "sha256": hashlib.sha256(PLAN_BYTES).hexdigest()},
]
SITE_MANIFEST = {
    "schema": "mnemon-site-bundle/1",
    "landing": "https://example.com/",
    "created": NOW,
    "pages": [
        {"n": 1, "url": "https://example.com/", "role": "landing", "file": "01.html", "fetch_report": "01.fetch.json",
         "sha256": sha(PAGE1_HTML), "bytes": len(PAGE1_HTML), "disposition": "captured", "error_class": None, "error": None,
         "raw_kind": "response", "http_status": 200},
        {"n": 2, "url": "https://example.com/product", "role": "product", "file": "02.html", "fetch_report": "02.fetch.json",
         "sha256": sha(PAGE2_HTML), "bytes": len(PAGE2_HTML), "disposition": "captured", "error_class": None, "error": None,
         "raw_kind": "response", "http_status": 200},
        {"n": 3, "url": "https://docs.example.com/guide", "role": "docs", "file": None, "fetch_report": "03.fetch.json",
         "sha256": None, "bytes": None, "disposition": "fetch-failed", "error_class": "server-error", "error": "HTTP 500",
         "raw_kind": None, "http_status": 500},
    ],
    "members": MEMBERS,
}
MANIFEST_BYTES = canonical_bytes(SITE_MANIFEST)
BUNDLE_STDOUT = {"raw_sha256": hashlib.sha256(MANIFEST_BYTES).hexdigest(), "raw_bytes": sum(m["bytes"] for m in MEMBERS)}

# --- mnemon-site/1 assembly report (§3.6, A5: conversion dispositions) ----------------------------------------
SITE_REPORT = {
    "schema": "mnemon-site/1",
    "pages": [
        {"n": 1, "url": "https://example.com/", "role": "landing", "disposition": "converted", "converter": "pandoc",
         "completeness": "complete", "missing_count": 0, "glued_count": 0, "words": 2, "bytes": 24, "error_class": None, "error": None},
        {"n": 2, "url": "https://example.com/product", "role": "product", "disposition": "converted", "converter": "dom-text",
         "completeness": "complete", "missing_count": 0, "glued_count": 0, "words": 2, "bytes": 24, "error_class": None, "error": None},
        {"n": 3, "url": "https://docs.example.com/guide", "role": "docs", "disposition": "fetch-failed", "converter": None,
         "completeness": None, "missing_count": None, "glued_count": None, "words": 0, "bytes": 0, "error_class": "server-error",
         "error": "HTTP 500"},
    ],
    "included": 3,
    "captured": 2,
    "converted": 2,
    "completeness": "partial",
    "words": 4,
    "bytes": 110,
}

# --- mnemon-media/1 (§3.7, D12: flat keys) and mnemon-media-error/1 -------------------------------------------
MEDIA = {
    "schema": "mnemon-media/1",
    "method": "captions:pt:manual",
    "audio_path": None,
    "title": "Um vídeo de exemplo",
    "duration": "0:12:34",
    "upload_date": "20260901",
    "video_id": "abc123xyz00",
    "language": "pt",
    "original_language": "pt",
    "track": {"lang": "pt", "kind": "manual", "orig": False},
    "channel": "Example Channel",
}
MEDIA_ERROR = {"schema": "mnemon-media-error/1", "error_class": "timeout", "stage": "download", "elapsed_s": 3600.4}

# --- receipts (§4.1, A3, A4): the request objects under their own schema ---------------------------------------
def receipt(request: dict, **extra) -> dict:
    req_bytes = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    base = {
        "schema": "mnemon-capture-receipt/1",
        "capture_id": extra.pop("capture_id"),
        "request_sha256": sha(req_bytes),
        "request": request,
        "input_path": extra.pop("input_path", None),
        "metadata": {"title": None, "author": None, "intent": None, "session": None, "import_source": None,
                     "import_ref": None, "import_batch": "", "producer_fidelity": None, "provenance_file": None},
        "resource_sha256": sha(request["resource_kind"] + ":" + request["resource_key"]),
        "resource_id": sha(request["resource_key"])[:8],
        "revision": "2026-10-15_" + sha(request["resource_key"])[:8],
        "instance": "t",
        "vault": "/vault",
        "state": "extracted",
        "attempts": 1,
        "attempt_dir": "/state/runs/<capture_id>/attempt-1",
        "error_class": None, "error": None, "retryable": None,
        "host": "host", "engine": "mnemon v2.1", "claude_code": "2.1.296 (Claude Code)",
        "started_at": NOW, "updated_at": NOW, "finalized_at": NOW,
        "raw_store": "archive", "raw_path": "/archive/<revision>.html", "raw_staged_path": None,
        "raw_kind": "response", "raw_sha256": sha("raw"), "raw_bytes": 48213, "raw_published": True,
        "text_sha256": sha("text"), "text_bytes": 9120, "source_published": True,
        "extract_sha256": sha("extract"), "template_sha256": sha("template"), "reader_context_sha256": sha("reader"),
        "argv": ["claude", "-p", "--restricted", "--tools", "", "--permission-prompts", "none", "--strict-mcp-config",
                 "--no-session-persistence", "--output-format", "json", "--json-schema", "<schema>", "--max-turns", "4",
                 "--model", "sonnet"],
        "cost_usd": 0.012, "duration_ms": 1500,
        "resolved_to_revision": None, "resolved_to_capture_id": None, "legacy_match": False, "replayed": False, "notes": [],
    }
    base.update(extra)
    return base


REQUEST_V1 = {
    "schema": "mnemon-request/1", "origin": "url", "resource_kind": "url",
    "resource_key": "https://example.com/post?utm_source=x", "input_sha256": None,
    "url": "https://example.com/post?utm_source=x", "ref_path": None, "context": "personal",
    "source_type": "article", "recapture": False, "supersedes": None, "render": False, "depth": "auto",
}
REQUEST_V2_PAGE = {
    "schema": "mnemon-request/2", "origin": "url", "resource_kind": "url",
    "resource_key": "https://example.com/post", "input_sha256": None,
    "url": "https://example.com/post?utm_source=x", "ref_path": None, "context": "personal",
    "source_type": "article", "recapture": False, "supersedes": None, "render": "auto", "external": "off",
    "site": None, "requested_version": None, "canonical_version": 1, "depth": "auto",
}
REQUEST_V2_SITE_AUTO = dict(REQUEST_V2_PAGE, resource_key="https://example.com/", url="https://example.com/",
                            source_type="website", site="auto", depth="off")

RECEIPT_V1 = receipt(REQUEST_V1, capture_id="c-20261015T120000Z-" + sha(REQUEST_V1["resource_key"])[:8] + "-0001")
RECEIPT_V2_PAGE = receipt(
    REQUEST_V2_PAGE, capture_id="c-20261015T120100Z-" + sha(REQUEST_V2_PAGE["resource_key"])[:8] + "-0002",
    request_schema="mnemon-request/2", canonical_url="https://example.com/post", canonical_version=1,
    capture_report={"fetch": FETCH_OK, "text": TEXT, "site": None, "media": None},
    report_sha256=sha("report"), report_path="/archive/<revision>.report.json", report_state="published",
    calls=[{"id": "single-1", "kind": "single", "n": 1, "attempt": 1, "state": "done", "reserved_usd": None,
            "started_at": NOW, "cost_usd": 0.012, "duration_ms": 1500, "result": "ok"}],
    cost_unknown=False, media_meta=None, stage=None, published_candidates=[])
RECEIPT_V2_SITE_AUTO = receipt(
    REQUEST_V2_SITE_AUTO, capture_id="c-20261015T120200Z-" + sha(REQUEST_V2_SITE_AUTO["resource_key"])[:8] + "-0003",
    input_path="/state/runs/<capture_id>/attempt-1/plan.json",
    request_schema="mnemon-request/2", canonical_url="https://example.com/", canonical_version=1,
    site_plan_sha256=hashlib.sha256(PLAN_BYTES).hexdigest(),
    raw_path="/archive/<revision>.site", raw_kind="site", raw_sha256=BUNDLE_STDOUT["raw_sha256"],
    raw_bytes=BUNDLE_STDOUT["raw_bytes"],
    capture_report={"fetch": None, "text": None, "site": SITE_REPORT, "media": None},
    report_sha256=sha("site-report"), report_path="/archive/<revision>.report.json", report_state="published",
    calls=[{"id": "single-1", "kind": "single", "n": 1, "attempt": 1, "state": "done", "reserved_usd": None,
            "started_at": NOW, "cost_usd": 0.02, "duration_ms": 2100, "result": "ok"}],
    cost_unknown=False, media_meta=None, stage=None, published_candidates=[])

FILES = {
    "canonical.json": CANONICAL,
    "fetch-ok.json": FETCH_OK,
    "fetch-failed.json": FETCH_FAILED,
    "fetch-timeout.json": FETCH_TIMEOUT,
    "text.json": TEXT,
    "site-plan.json": SITE_PLAN,
    "site-manifest.json": SITE_MANIFEST,
    "site-bundle-stdout.json": BUNDLE_STDOUT,
    "site-report.json": SITE_REPORT,
    "media.json": MEDIA,
    "media-error.json": MEDIA_ERROR,
    "receipts/request-v1.json": RECEIPT_V1,
    "receipts/request-v2-page.json": RECEIPT_V2_PAGE,
    "receipts/request-v2-site-auto.json": RECEIPT_V2_SITE_AUTO,
}


def main() -> None:
    for name, obj in FILES.items():
        path = OUT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "site-manifest.json":
            path.write_bytes(MANIFEST_BYTES)       # canonical bytes: the hash in site-bundle-stdout.json is of these
        else:
            path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(OUT)}")


if __name__ == "__main__":
    main()
