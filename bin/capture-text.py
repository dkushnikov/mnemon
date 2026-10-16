#!/usr/bin/env python3
"""capture-text.py — the capture-text of a retained raw, with the diagnostic completeness check (spec §3.5).

    capture-text.py <raw> --url <url> --out <file> --report <json> [--fetch-report <json>]
                    [--raw-kind response|rendered|site|external] [--kind page|arxiv|youtube|github]
                    [--timeout S]
    capture-text.py <raw> --url <url> --rendered <file> --out <file> --report <json>   # phase-1 legacy text path

Converts a retained raw (the HTML the fetch saved) into capture-text through the capture layer
(bin/capture/convert.py): decode → sanitize → the content subtree → the reference → the candidates
(`defuddle`, `pandoc`, the DOM text) checked against the reference → the winner. It never fetches: what only
the fetch could see (status, classification, charset, egress, resolver metadata) comes in through
`--fetch-report`, the receipt's copy on a reconversion; without it the report records `acquisition: unknown`.
With `--rendered <file>` the capture-text is that file's bytes, unchanged (`render-text`).

The report is `mnemon-capture-text/2` (the phase-1 keys plus the converter, the acquisition, the candidates,
the counts, the inventory, the repairs, the dated candidates). An empty or failed conversion is an
acquisition failure: exit 1, `error_class: convert` (or `invalid-utf8`), nothing at --out — the raw stays
published and a retry converts it again. `--timeout` (convert_timeout, default 300) supervises `defuddle` and
`pandoc` in their own process groups (a stuck converter is skipped for the next candidate) and is checked
between the tables the sanitizer rewrites (past it: exit 124, `error_class: timeout`). Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import convert, proc, report as reportmod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture-text conversion for the url origin (phase 2)")
    ap.add_argument("raw", help="the retained raw file (HTML), or the rendered text with --rendered")
    ap.add_argument("--url", required=True, help="the URL the raw came from (recorded in the report)")
    ap.add_argument("--out", required=True, help="where to write the capture-text bytes")
    ap.add_argument("--report", required=True, help="where to write the JSON report (mnemon-capture-text/2)")
    ap.add_argument("--fetch-report", default=None, help="the fetch report (mnemon-fetch/1) of this raw")
    ap.add_argument("--raw-kind", default="response", choices=["response", "rendered", "site", "external", "file", "composite"])
    ap.add_argument("--kind", default=None, choices=["page", "arxiv", "youtube", "github"])
    ap.add_argument("--rendered", default=None, help="rendered text to use verbatim as capture-text (legacy)")
    ap.add_argument("--timeout", type=float, default=convert.DEFAULT_TIMEOUT, help="convert_timeout in seconds")
    args = ap.parse_args()

    proc.close_lock_fd()
    proc.install_term_handler()
    deadline = proc.Deadline(args.timeout)
    rep = convert.empty_report(args.url)
    fetch_report = None
    if args.fetch_report:
        try:
            fetch_report = reportmod.read_json(args.fetch_report)
        except (OSError, ValueError) as exc:
            print("capture-text: cannot read --fetch-report: %s" % exc, file=sys.stderr)
            return 2
        acq = (fetch_report.get("acquisition") or {}).get("outcome")
        if acq:
            rep["acquisition"] = acq

    if args.rendered:
        try:
            with open(args.rendered, "rb") as fh:
                text = fh.read()
        except OSError as exc:
            rep["error_class"], rep["error"] = "convert", "cannot read rendered text: %s" % exc.strerror
            reportmod.write_report(args.report, rep)
            return 1
        if not text.strip():
            rep["error_class"], rep["error"] = "convert", "rendered text is empty"
            reportmod.write_report(args.report, rep)
            print("capture-text: convert failed for %s: %s" % (args.url, rep["error"]), file=sys.stderr)
            return 1
        reportmod.write_bytes(args.out, text)
        words = len(text.decode("utf-8", "replace").split())
        rep.update({"method": "render-text", "converter": "render-text", "bytes": len(text), "words": words,
                    "completeness": "unknown", "bytes_per_word": round(len(text) / words, 2) if words else 0.0})
        reportmod.write_report(args.report, rep)
        return 0

    try:
        with open(args.raw, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        rep["error_class"], rep["error"] = "convert", "cannot read raw: %s" % exc.strerror
        reportmod.write_report(args.report, rep)
        return 1
    kind = args.kind or (fetch_report or {}).get("kind") or "page"
    try:
        result = convert.convert(raw, args.url, args.raw_kind, kind, fetch_report, deadline=deadline)
    except convert.ConvertError as exc:
        rep["error_class"], rep["error"] = exc.error_class, str(exc)
        reportmod.write_report(args.report, rep)
        print("capture-text: %s for %s: %s" % (exc.error_class, args.url, exc), file=sys.stderr)
        return 124 if exc.error_class == "timeout" else 1
    if not result.text.strip():
        rep["error_class"], rep["error"] = "convert", "empty conversion output"
        reportmod.write_report(args.report, rep)
        print("capture-text: convert failed for %s: empty conversion output" % args.url, file=sys.stderr)
        return 1
    reportmod.write_bytes(args.out, result.text.encode("utf-8"))
    reportmod.write_report(args.report, result.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
