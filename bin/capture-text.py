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
published and a retry converts it again.

The conversion runs under a killable owner (D11, G25): this process is a thin supervisor that parses its
arguments, computes the deadline (`--timeout`, the instance's `convert_timeout`, default 120 s) and runs the
whole conversion in a child process — a re-exec of itself with the hidden `--_worker` flag, in its own
process group — with the raw and the fetch report passed by path and the report written by the child. On
expiry the supervisor kills the group, reaps it, writes the report `{error_class: "timeout", stage:
"convert", retryable: false, error: "conversion exceeded convert_timeout=N"}` and exits 124: a page that
cannot be converted in the budget will not convert on a retry either (the raw is retained for `--resume`
after raising the budget). Inside the worker the same deadline supervises `defuddle` and `pandoc` (a stuck
converter is skipped for the next candidate) and is checked between the tables the sanitizer rewrites.
The worker watches its parent in turn (proc.watch_parent): when the supervisor is gone — SIGKILLed, so its
own teardown never ran — the worker kills its tracked children and exits within a second, so no conversion
outlives the owner that would have reaped it.
Test hook (honoured only under MNEMON_TEST_HOOKS=1): MNEMON_TEST_CONVERT_HANG=1 makes the worker hang in
C code that no signal handler interrupts, the shape of a quadratic scan — the proof that the supervisor's
kill, not the worker's cooperation, ends the conversion; MNEMON_TEST_CONVERT_HANG=sleep makes it wait in
interruptible python code instead — the proof of the parent watch. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import convert, proc, report as reportmod  # noqa: E402
from capture import policy as policymod  # noqa: E402

EXIT_TIMEOUT = 124


def worker(args) -> int:
    """The conversion itself, in the child process."""
    proc.watch_parent()         # a SIGKILLed supervisor leaves no worker behind
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
    hang = policymod.hook("MNEMON_TEST_CONVERT_HANG")
    if hang == "sleep":
        time.sleep(600)         # interruptible python code: the parent watch can act, as during any python code
    elif hang:
        proc.hang()

    if args.rendered:
        try:
            with open(args.rendered, "rb") as fh:
                text = fh.read()
        except OSError as exc:
            rep["error_class"], rep["error"], rep["retryable"] = "convert", "cannot read rendered text: %s" % exc.strerror, True
            reportmod.write_report(args.report, rep)
            return 1
        if not text.strip():
            rep["error_class"], rep["error"], rep["retryable"] = "convert", "rendered text is empty", True
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
        rep["error_class"], rep["error"], rep["retryable"] = "convert", "cannot read raw: %s" % exc.strerror, True
        reportmod.write_report(args.report, rep)
        return 1
    kind = args.kind or (fetch_report or {}).get("kind") or "page"
    try:
        result = convert.convert(raw, args.url, args.raw_kind, kind, fetch_report, deadline=deadline)
    except convert.ConvertError as exc:
        rep["error_class"], rep["error"], rep["retryable"] = exc.error_class, str(exc), exc.retryable
        if exc.candidates:
            rep["candidates"] = exc.candidates      # what was tried before the failure (G17)
        if exc.error_class == "timeout":
            rep["stage"] = "convert"
        reportmod.write_report(args.report, rep)
        print("capture-text: %s for %s: %s" % (exc.error_class, args.url, exc), file=sys.stderr)
        return EXIT_TIMEOUT if exc.error_class == "timeout" else 1
    except RecursionError:
        # the belt to convert()'s guard (G8): a report, never a traceback; permanent
        rep["error_class"], rep["error"], rep["retryable"] = "convert", "nesting", False
        reportmod.write_report(args.report, rep)
        print("capture-text: convert for %s: nesting" % args.url, file=sys.stderr)
        return 1
    if not result.text.strip():
        rep["error_class"], rep["error"], rep["retryable"] = "convert", "empty conversion output", True
        reportmod.write_report(args.report, rep)
        print("capture-text: convert failed for %s: empty conversion output" % args.url, file=sys.stderr)
        return 1
    reportmod.write_bytes(args.out, result.text.encode("utf-8"))
    reportmod.write_report(args.report, result.report)
    return 0


def supervise(args) -> int:
    """Run the conversion in a worker process under the deadline; kill the group on expiry (G25)."""
    timeout = float(args.timeout)
    argv = [sys.executable, os.path.abspath(__file__), "--_worker"] + sys.argv[1:]
    res = proc.run(argv, timeout=timeout, deadline=proc.Deadline(timeout))
    if res.stdout:
        sys.stdout.buffer.write(res.stdout)
        sys.stdout.flush()
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
        sys.stderr.flush()
    if res.timed_out:
        # the worker (and anything it started) is gone: nothing at --out, the timeout report
        try:
            os.unlink(args.out)
        except OSError:
            pass
        rep = convert.empty_report(args.url)
        rep.update({"error_class": "timeout", "stage": "convert", "retryable": False,
                    "error": "conversion exceeded convert_timeout=%g" % timeout})
        reportmod.write_report(args.report, rep)
        print("capture-text: timeout for %s: conversion exceeded convert_timeout=%g (worker killed with its process group after %d ms)"
              % (args.url, timeout, res.elapsed_ms), file=sys.stderr)
        return EXIT_TIMEOUT
    if not res.started:
        rep = convert.empty_report(args.url)
        rep.update({"error_class": "environment", "retryable": True,
                    "error": "the conversion worker could not start: %s" % res.stderr.decode("utf-8", "replace").strip()})
        reportmod.write_report(args.report, rep)
        return 3
    return res.rc if res.rc is not None else 1


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
    ap.add_argument("--timeout", type=float, default=convert.DEFAULT_TIMEOUT,
                    help="convert_timeout in seconds (default %g): the worker is killed past it, exit 124" % convert.DEFAULT_TIMEOUT)
    ap.add_argument("--_worker", dest="worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    proc.close_lock_fd()
    proc.install_term_handler()
    if args.worker:
        return worker(args)
    policymod.warn_ignored_hooks()
    return supervise(args)


if __name__ == "__main__":
    sys.exit(main())
