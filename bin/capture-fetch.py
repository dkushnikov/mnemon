#!/usr/bin/env python3
"""capture-fetch.py — URL → raw file + fetch report (spec §3.1, §3.3, §3.4, D4, D10, D11).

    capture-fetch.py <url> --out <staged raw path> --report <json> [--expect page|pdf|audio]
                     [--kind page|arxiv|youtube|github] [--render | --no-render] [--no-external]
                     [--policy <json>] [--external <json>] [--acquire-timeout S] [--render-timeout S]
                     [--fetch-timeout S] [--render-cmd <path>] [--render-max-bytes N] [--thin-words N]
    capture-fetch.py --canonical-only <url>
    capture-fetch.py --canonical-only --stdin        # one URL per line, one object per line

Acquires one URL under the network policy (bin/capture/policy.py) through the chain direct → rendered →
external (bin/capture/fetch.py), writes the raw to --out (tmp + fsync + rename, only on success) and the
report `mnemon-fetch/1` to --report, and never converts. `--expect pdf|audio` is one policy GET with its
own status mapping (the gateway's URL PDF and URL audio downloads). `--canonical-only` prints the
`mnemon-canonical/1` object of §3.1 and touches no network.

`--policy` is a JSON object with the instance keys (`fetch_user_agent`, `fetch_timeout`, `fetch_max_bytes`,
optionally `thin_words`, `render_timeout`, `render_max_bytes`, `acquire_timeout`) or a report's `policy`
block; `--external` is `{"mode": "off|auto", "fetchers": ["firecrawl", "jina"]}` (keys from the
environment: FIRECRAWL_API_KEY, JINA_API_KEY). Precedence: flag > --policy > default.

Exit codes: 0 fetched · 1 failed (`error_class`, `retryable` in the report) · 2 usage or invalid-url ·
3 environment (no renderer where one was required and nothing else allowed) · 124 timeout (`error_class:
timeout`, `stage`). The renderer and the external stub run in their own process groups and die with this
process (A9). Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import fetch, proc, resolve  # noqa: E402
from capture import policy as policymod  # noqa: E402
from capture import report as reportmod  # noqa: E402


def canonical_only(args) -> int:
    if args.stdin:
        for line in sys.stdin:
            url = line.strip()
            if not url:
                continue
            try:
                obj = resolve.canonical_object(url)
            except resolve.InvalidURL as exc:
                obj = {"schema": "mnemon-canonical/1", "url": url, "error_class": "invalid-url", "error": str(exc)}
            print(json.dumps(obj, ensure_ascii=False))
        return 0
    try:
        obj = resolve.canonical_object(args.canonical_only)
    except resolve.InvalidURL as exc:
        print(json.dumps({"error_class": "invalid-url", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def load_json_arg(text, what: str):
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except ValueError as exc:
        print("capture-fetch: %s is not valid JSON: %s" % (what, exc), file=sys.stderr)
        sys.exit(2)
    if not isinstance(obj, dict):
        print("capture-fetch: %s must be a JSON object" % what, file=sys.stderr)
        sys.exit(2)
    return obj


def number(value, default, name: str):
    if value is None:
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        print("capture-fetch: %s must be a number, got %r" % (name, value), file=sys.stderr)
        sys.exit(2)
    if n != n or n < 0:
        print("capture-fetch: %s must be a non-negative finite number" % name, file=sys.stderr)
        sys.exit(2)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="URL → raw file + fetch report (the capture layer's acquisition CLI)")
    ap.add_argument("url", nargs="?", help="the URL to acquire")
    ap.add_argument("--out", help="the staged raw path to write on success")
    ap.add_argument("--report", help="where to write the mnemon-fetch/1 report")
    ap.add_argument("--expect", choices=["page", "pdf", "audio"], default="page")
    ap.add_argument("--kind", choices=["page", "arxiv", "youtube", "github"], default=None,
                    help="the kind from --canonical-only (youtube and github fetch as page)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--render", action="store_true", help="start at the render stage (forced)")
    group.add_argument("--no-render", action="store_true", help="never render (implies --no-external)")
    ap.add_argument("--no-external", action="store_true")
    ap.add_argument("--policy", default=None, help="JSON: fetch_user_agent, fetch_timeout, fetch_max_bytes, …")
    ap.add_argument("--external", default=None, help='JSON: {"mode": "off|auto", "fetchers": ["firecrawl", "jina"]}')
    ap.add_argument("--acquire-timeout", default=None, help="the whole acquisition's budget in seconds (default 300)")
    ap.add_argument("--render-timeout", default=None, help="the render stage's budget in seconds (default 90)")
    ap.add_argument("--fetch-timeout", default=None, help="the direct stage's connect+read budget (default 60)")
    ap.add_argument("--render-cmd", default=None, help="the renderer (default: $RENDER_URL_CMD, else bin/render-url.sh)")
    ap.add_argument("--render-max-bytes", default=None)
    ap.add_argument("--thin-words", default=None)
    ap.add_argument("--canonical-only", default=None, metavar="URL", nargs="?", const="",
                    help="print the mnemon-canonical/1 object and exit (no network)")
    ap.add_argument("--stdin", action="store_true", help="with --canonical-only: one URL per line")
    args = ap.parse_args()

    proc.close_lock_fd()
    policymod.warn_ignored_hooks()
    if args.canonical_only is not None:
        if args.stdin:
            return canonical_only(args)
        if not args.canonical_only and args.url:
            args.canonical_only = args.url
        if not args.canonical_only:
            ap.error("--canonical-only needs a URL or --stdin")
        return canonical_only(args)
    if not args.url or not args.out or not args.report:
        ap.error("<url>, --out and --report are required")

    proc.install_term_handler()
    config = load_json_arg(args.policy, "--policy")
    if args.fetch_timeout is not None:
        config = dict(config, fetch_timeout=number(args.fetch_timeout, 60, "--fetch-timeout"))
        config.pop("connect_timeout", None)
        config.pop("read_timeout", None)
    policy = policymod.Policy.from_config(config)
    external = load_json_arg(args.external, "--external")
    mode = str(external.get("mode", "off")).lower()
    if mode not in ("off", "auto"):
        print("capture-fetch: --external mode must be off or auto", file=sys.stderr)
        return 2
    fetchers = external.get("fetchers") or ["firecrawl", "jina"]
    if args.no_external or args.no_render:
        mode = "off"
    render = "force" if args.render else "off" if args.no_render else "auto"
    opts = fetch.Options(
        policy, expect=args.expect, render=render, external_mode=mode, fetchers=fetchers,
        acquire_timeout=number(args.acquire_timeout, config.get("acquire_timeout", 300), "--acquire-timeout"),
        render_timeout=number(args.render_timeout, config.get("render_timeout", 90), "--render-timeout"),
        render_cmd=args.render_cmd, render_max_bytes=int(number(args.render_max_bytes, config.get("render_max_bytes", 20000000), "--render-max-bytes")),
        thin_words=int(number(args.thin_words, config.get("thin_words", 60), "--thin-words")), kind=args.kind)
    code, report = fetch.acquire(args.url, args.out, opts)
    reportmod.write_report(args.report, report)
    if code != 0:
        print("capture-fetch: %s: %s" % (report.get("error_class"), report.get("error")), file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
