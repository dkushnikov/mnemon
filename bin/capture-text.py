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

The report (`mnemon-capture-text/1`) records `method`, `bytes`, `words`, `title` (the page's
`<title>`, normalized per §3.3: control characters removed, whitespace collapsed, trimmed,
≤ 300 characters; null when absent), `completeness: "unknown"` (phase 2 fills it) and the version
of the tool that produced the text. Stdlib only, python 3.9.
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

SCHEMA = "mnemon-capture-text/1"
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
    args = ap.parse_args()

    report = {"schema": SCHEMA, "url": args.url, "method": None, "bytes": 0, "words": 0,
              "title": None, "completeness": "unknown", "tool": None, "error_class": None, "error": None}
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
        text, err = run_defuddle(args.raw)
        method, tool = "curl+defuddle", "defuddle"
        if text is None:
            attempts.append(err)
            text, err = run_pandoc(args.raw)
            method, tool = "curl+pandoc", "pandoc"
            if text is None:
                attempts.append(err)
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
    write_report(args.report, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
