#!/usr/bin/env python3
"""capture-text.py — the deterministic text form of a fetched page (spec §6, `url` row).

    capture-text.py <raw-file> --url <url> --out <file> --report <json>
    capture-text.py <raw-file> --url <url> --rendered <file> --out <file> --report <json>

Converts the published raw (the HTML curl saved) into capture-text: `defuddle parse <file>
--markdown` first (method `curl+defuddle`), then `pandoc -f html -t gfm` (method `curl+pandoc`).
With `--rendered <file>` the capture-text is that file's bytes, unchanged (method `render-text`:
the retained artifact of a `--render` capture is the rendered output itself). An empty or failed
conversion is an acquisition failure: exit 1, `error_class: convert` in the report, nothing at
--out — the gateway keeps the raw published and a retry converts it again (never refetches).

The report (`mnemon-capture-text/2`, the frozen shape of tests/fixtures/capture/reports/text.json)
records the `/1` keys — `method` (`<fetch>+<converter>`), `bytes`, `words`, `title` (the page's
`<title>`, normalized per §3.3: control characters removed, whitespace collapsed, trimmed,
≤ 300 characters; null when absent), `completeness`, the tool that produced the text — plus the
phase-2 keys with neutral values (this is the phase-1 converter with the `/2` report shape: no
reference check, `completeness: "unknown"`, empty inventory and repairs; the capture layer's
rewrite replaces it). `--fetch-report`, `--raw-kind` and `--kind` are accepted; the fetch report
supplies the fetch method, the acquisition outcome and the arXiv metadata. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA = "mnemon-capture-text/2"
TITLE_MAX = 300
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def normalize_scalar(value: str, limit: int) -> str:
    value = _CTRL.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def page_title(raw: bytes):
    text = raw.decode("utf-8", errors="replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = normalize_scalar(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))), TITLE_MAX)
    return title or None


def tool_version(cmd: str, flag: str = "--version"):
    try:
        out = subprocess.run([cmd, flag], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (out.stdout or out.stderr).strip().splitlines()
    return line[0].strip() if line else None


def run_defuddle(raw_path: str):
    if not shutil.which("defuddle"):
        return None, "defuddle not installed"
    try:
        out = subprocess.run(["defuddle", "parse", raw_path, "--markdown"], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"defuddle failed: {exc.__class__.__name__}"
    if out.returncode != 0 or not out.stdout.strip():
        return None, "defuddle produced no content"
    return out.stdout, None


def run_pandoc(raw_path: str):
    if not shutil.which("pandoc"):
        return None, "pandoc not installed"
    try:
        out = subprocess.run(["pandoc", "-f", "html", "-t", "gfm", "--wrap=none", raw_path],
                             capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"pandoc failed: {exc.__class__.__name__}"
    if out.returncode != 0 or not out.stdout.strip():
        return None, "pandoc produced no content"
    return out.stdout, None


def published_candidates(raw: bytes) -> list:
    """Dated candidates from the page's meta tags (the gateway chooses by source type, D8)."""
    text = raw.decode("utf-8", errors="replace")
    found = []
    for attr, source in (("property", "article:published_time"), ("name", "citation_date")):
        m = re.search(r'<meta\s+(?:[^>]*?\s)?' + attr + r'=["\']' + re.escape(source) + r'["\'][^>]*?content=["\']([^"\']+)["\']', text, re.IGNORECASE)
        if not m:
            m = re.search(r'<meta\s+(?:[^>]*?\s)?content=["\']([^"\']+)["\'][^>]*?' + attr + r'=["\']' + re.escape(source) + r'["\']', text, re.IGNORECASE)
        if m:
            found.append({"value": normalize_scalar(html.unescape(m.group(1)), 40), "source": source})
    return found


def write_report(path: str, report: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture-text conversion for the url origin")
    ap.add_argument("raw", help="the published raw file (HTML), or the rendered text with --rendered")
    ap.add_argument("--url", required=True, help="the URL the raw came from (recorded in the report)")
    ap.add_argument("--rendered", default=None, help="rendered text to use verbatim as capture-text")
    ap.add_argument("--out", required=True, help="where to write the capture-text bytes")
    ap.add_argument("--report", required=True, help="where to write the JSON report")
    ap.add_argument("--fetch-report", default=None, help="the fetch report (mnemon-fetch/1) of the raw")
    ap.add_argument("--raw-kind", default=None, choices=["response", "rendered", "site", "external"])
    ap.add_argument("--kind", default=None, choices=["page", "arxiv", "youtube", "github"])
    args = ap.parse_args()

    fetch = {}
    if args.fetch_report:
        try:
            with open(args.fetch_report, "r", encoding="utf-8") as fh:
                fetch = json.load(fh)
        except (OSError, ValueError):
            fetch = {}
    fmeta = fetch.get("metadata") or {}
    acq = (fetch.get("acquisition") or {}).get("outcome") or "unknown"
    report = {"schema": SCHEMA, "url": args.url, "method": None, "bytes": 0, "words": 0,
              "title": None, "completeness": "unknown", "tool": None, "error_class": None, "error": None,
              "converter": None, "acquisition": acq, "candidates": [], "reference_count": 0, "eligible_count": 0,
              "missing_count": 0, "glued_count": 0, "headings_missing_count": 0, "missing_examples": [],
              "inventory": {}, "repairs": {"html": {}, "markdown": {}}, "bytes_per_word": 0.0, "bloat": False,
              "published_candidates": [], "authors": list(fmeta.get("authors") or []), "arxiv_id": fmeta.get("arxiv_id"),
              "arxiv_version": fmeta.get("arxiv_version"), "arxiv_requested_version": fmeta.get("arxiv_requested_version"),
              "limits": ["phase-1 converter: no reference check (completeness unknown)"],
              "tools": {"defuddle": tool_version("defuddle") if shutil.which("defuddle") else None,
                        "pandoc": tool_version("pandoc") if shutil.which("pandoc") else None}}
    fetch_method = fetch.get("method") or "direct"
    attempts = []

    if args.rendered:
        try:
            with open(args.rendered, "rb") as fh:
                text = fh.read()
        except OSError as exc:
            report["error_class"], report["error"] = "convert", f"cannot read rendered text: {exc.strerror}"
            write_report(args.report, report)
            return 1
        method, tool = "render-text", None
        if not text.strip():
            text = b""
    else:
        try:
            with open(args.raw, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            report["error_class"], report["error"] = "convert", f"cannot read raw: {exc.strerror}"
            write_report(args.report, report)
            return 1
        report["title"] = page_title(raw)
        report["published_candidates"] = published_candidates(raw)
        text, err = run_defuddle(args.raw)
        method, tool = fetch_method + "+defuddle", "defuddle"
        if text is None:
            attempts.append(err)
            report["candidates"].append({"converter": "defuddle", "missing_count": 0, "glued_count": 0,
                                         "headings_missing_count": 0, "chosen": False, "note": err})
            text, err = run_pandoc(args.raw)
            method, tool = fetch_method + "+pandoc", "pandoc"
            if text is None:
                attempts.append(err)
                report["candidates"].append({"converter": "pandoc", "missing_count": 0, "glued_count": 0,
                                             "headings_missing_count": 0, "chosen": False, "note": err})
                text = b""

    if not text or not text.strip():
        report["error_class"] = "convert"
        report["error"] = "; ".join(attempts) if attempts else "empty conversion output"
        write_report(args.report, report)
        print(f"capture-text: convert failed for {args.url}: {report['error']}", file=sys.stderr)
        return 1

    tmp = args.out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, args.out)

    report["method"] = method
    report["bytes"] = len(text)
    report["words"] = len(text.decode("utf-8", errors="replace").split())
    report["tool"] = {"name": tool, "version": tool_version(tool)} if tool else None
    report["converter"] = tool or "render-text"
    report["candidates"].append({"converter": report["converter"], "missing_count": 0, "glued_count": 0,
                                 "headings_missing_count": 0, "chosen": True, "note": None})
    report["bytes_per_word"] = round(report["bytes"] / report["words"], 1) if report["words"] else 0.0
    report["bloat"] = report["bytes_per_word"] > 40
    write_report(args.report, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
