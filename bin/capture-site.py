#!/usr/bin/env python3
"""capture-site.py — map · fetch-bundle · verify · assemble (spec §3.6, D9, §12 A5, A10, A12).

    capture-site.py map <url> [--max-pages N] [--include <regex>]… [--exclude <regex>]… [--out <plan.json>] [--json]
                    [--policy <json>] [--site-timeout S] [--render-cmd <path>] [--no-render] [--external <json>]
    capture-site.py fetch-bundle <plan.json> --out <staged bundle dir> --report <json> [--site-timeout S]
                    [--policy <json>] [--render-cmd <path>] [--external <json>] [--no-render] [--no-external]
                    [--fetch-timeout S] [--render-timeout S] [--acquire-timeout S]
    capture-site.py verify <bundle dir> --sha256 <expected manifest sha256>
    capture-site.py assemble <bundle dir> --out <capture.md> --report <json> [--timeout S]

`map` discovers the same-site pages of a landing and writes the reviewable plan (`mnemon-site-plan/1`).
`fetch-bundle` owns the page loop and its deadline (`--site-timeout`, default 900): every included page
through the chain, `NN.html` + `NN.fetch.json`, `plan.json`, `manifest.json` last; it prints exactly one
JSON line `{"raw_sha256": "<hex>", "raw_bytes": <int>}` on stdout; a landing that fails is fatal (exit 1,
or 124 on a timeout), every other failure is a disposition in the manifest. A global `--no-render`
prohibits rendering everywhere (a plan's `pages[].render: true` cannot override it) and implies
`--no-external`; otherwise `pages[].render: true` forces a render. `verify` exits 0 when the manifest's
canonical bytes hash to the expected value and every member is present with its size and hash (no
symlink, no escaping path), else 1 with `evidence-missing <member>` or `evidence-mismatch <member|manifest>`
on stderr. `assemble` verifies nothing (the gateway verified the bundle): it converts every captured page in
process (`raw_kind: site`, that page's fetch report; README and llms.txt pages as `converter: text`), writes
the composite and the `mnemon-site/1` report; a landing without text is `convert`, exit 1. Stdlib only,
python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import fetch, proc, site  # noqa: E402
from capture import policy as policymod  # noqa: E402
from capture import report as reportmod  # noqa: E402


def load_json_arg(text, what: str):
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except ValueError as exc:
        print("capture-site: %s is not valid JSON: %s" % (what, exc), file=sys.stderr)
        sys.exit(2)
    if not isinstance(obj, dict):
        print("capture-site: %s must be a JSON object" % what, file=sys.stderr)
        sys.exit(2)
    return obj


def number(value, default, name):
    if value is None:
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        print("capture-site: %s must be a number, got %r" % (name, value), file=sys.stderr)
        sys.exit(2)
    if n != n or n < 0:
        print("capture-site: %s must be a non-negative finite number" % name, file=sys.stderr)
        sys.exit(2)
    return n


def common_policy(args):
    config = load_json_arg(getattr(args, "policy", None), "--policy")
    if getattr(args, "fetch_timeout", None) is not None:
        config = dict(config, fetch_timeout=number(args.fetch_timeout, 60, "--fetch-timeout"))
        config.pop("connect_timeout", None)
        config.pop("read_timeout", None)
    policy = policymod.Policy.from_config(config)
    policy.min_interval_s = site.PACE_S
    return policy, config


def external_args(args, config):
    external = load_json_arg(getattr(args, "external", None), "--external")
    mode = str(external.get("mode", config.get("external_fetch", "off"))).lower()
    if mode not in ("off", "auto"):
        print("capture-site: --external mode must be off or auto", file=sys.stderr)
        sys.exit(2)
    fetchers = external.get("fetchers") or ["firecrawl", "jina"]
    if getattr(args, "no_external", False) or getattr(args, "no_render", False):
        mode = "off"
    return mode, fetchers


def cmd_map(args) -> int:
    policy, config = common_policy(args)
    mode, fetchers = external_args(args, config)
    deadline = proc.Deadline(number(args.site_timeout, config.get("site_timeout", 900), "--site-timeout"))
    opts = fetch.Options(policy, render="off" if args.no_render else "auto", external_mode=mode, fetchers=fetchers,
                         acquire_timeout=number(None, config.get("acquire_timeout", 300), "acquire_timeout"),
                         render_timeout=number(args.render_timeout, config.get("render_timeout", 90), "--render-timeout"),
                         render_cmd=args.render_cmd, thin_words=int(number(args.thin_words, config.get("thin_words", 60), "--thin-words")),
                         deadline=deadline)
    work = tempfile.mkdtemp(prefix="mnemon-map-")      # the landing's staging area, whatever --out says
    try:
        plan = site.map_site(args.url, policy, max_pages=int(number(args.max_pages, config.get("site_max_pages", site.DEFAULT_MAX_PAGES), "--max-pages")),
                             includes=args.include or [], excludes=args.exclude or [], deadline=deadline, fetch_opts=opts,
                             extra_hosts=args.host or [], tmp_dir=work)
    except Exception as exc:    # an invalid landing URL or a regex error is usage
        from capture import resolve
        import re
        if isinstance(exc, (resolve.InvalidURL, re.error)):
            print("capture-site: %s" % exc, file=sys.stderr)
            return 2
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.out:
        reportmod.write_report(args.out, plan)
    if args.json or not args.out:
        print(json.dumps(plan, ensure_ascii=False, indent=1))
    return 0


def cmd_fetch_bundle(args) -> int:
    policy, config = common_policy(args)
    mode, fetchers = external_args(args, config)
    try:
        with open(args.plan, "rb") as fh:
            plan_bytes = fh.read()
        plan = json.loads(plan_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        print("capture-site: cannot read the plan: %s" % exc, file=sys.stderr)
        return 2
    if not isinstance(plan, dict) or plan.get("schema") != reportmod.SITE_PLAN or not plan.get("landing"):
        print("capture-site: not a %s plan" % reportmod.SITE_PLAN, file=sys.stderr)
        return 2
    code, manifest, report = site.fetch_bundle(
        plan, plan_bytes, args.out, policy,
        site_timeout=number(args.site_timeout, config.get("site_timeout", 900), "--site-timeout"),
        acquire_timeout=number(args.acquire_timeout, config.get("acquire_timeout", 300), "--acquire-timeout"),
        render_timeout=number(args.render_timeout, config.get("render_timeout", 90), "--render-timeout"),
        render="off" if args.no_render else "auto", external_mode=mode, fetchers=fetchers, render_cmd=args.render_cmd,
        render_max_bytes=int(number(None, config.get("render_max_bytes", 20000000), "render_max_bytes")),
        thin_words=int(number(args.thin_words, config.get("thin_words", 60), "--thin-words")))
    reportmod.write_report(args.report, report)
    if code != 0:
        print("capture-site: %s: %s" % (report.get("error_class"), report.get("error")), file=sys.stderr)
        return code
    print(json.dumps({"raw_sha256": report["raw_sha256"], "raw_bytes": report["raw_bytes"]}))
    return 0


def cmd_verify(args) -> int:
    ok, reason = site.verify_bundle(args.dir, args.sha256)
    if ok:
        return 0
    print(reason, file=sys.stderr)
    return 1


def cmd_assemble(args) -> int:
    deadline = proc.Deadline(number(args.timeout, 300, "--timeout"))
    try:
        code, report = site.assemble(args.dir, args.out, deadline=deadline)
    except (OSError, ValueError) as exc:
        print("capture-site: cannot read the bundle: %s" % exc, file=sys.stderr)
        return 2
    reportmod.write_report(args.report, report)
    if code != 0:
        print("capture-site: %s: %s" % (report.get("error_class"), report.get("error")), file=sys.stderr)
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description="Site capture: map · fetch-bundle · verify · assemble")
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("map", help="discover the same-site pages of a landing and write the plan")
    m.add_argument("url")
    m.add_argument("--max-pages", default=None)
    m.add_argument("--include", action="append", default=[], metavar="REGEX",
                   help="restrict the plan to the landing and the pages whose path plus ?query matches some --include REGEX "
                        "(e.g. ^/help/air/; a matching page the role rules excluded is re-admitted); repeatable")
    m.add_argument("--exclude", action="append", default=[], metavar="REGEX",
                   help="exclude a page when REGEX is found in its path plus ?query (never the host); applied after --include, "
                        "before the cap; repeatable")
    m.add_argument("--host", action="append", default=[], help="an extra eligible host (hand-edited plans may add hosts)")
    m.add_argument("--out", default=None)
    m.add_argument("--json", action="store_true")
    m.add_argument("--policy", default=None)
    m.add_argument("--external", default=None)
    m.add_argument("--site-timeout", default=None)
    m.add_argument("--render-timeout", default=None)
    m.add_argument("--fetch-timeout", default=None)
    m.add_argument("--thin-words", default=None)
    m.add_argument("--render-cmd", default=None)
    m.add_argument("--no-render", action="store_true")
    m.add_argument("--no-external", action="store_true")
    f = sub.add_parser("fetch-bundle", help="fetch every included page of a plan into a bundle directory")
    f.add_argument("plan")
    f.add_argument("--out", required=True)
    f.add_argument("--report", required=True)
    f.add_argument("--site-timeout", default=None)
    f.add_argument("--acquire-timeout", default=None)
    f.add_argument("--render-timeout", default=None)
    f.add_argument("--fetch-timeout", default=None)
    f.add_argument("--thin-words", default=None)
    f.add_argument("--policy", default=None)
    f.add_argument("--external", default=None)
    f.add_argument("--render-cmd", default=None)
    f.add_argument("--no-render", action="store_true")
    f.add_argument("--no-external", action="store_true")
    v = sub.add_parser("verify", help="verify a bundle against the expected manifest hash")
    v.add_argument("dir")
    v.add_argument("--sha256", required=True)
    a = sub.add_parser("assemble", help="convert every captured page and write the composite")
    a.add_argument("dir")
    a.add_argument("--out", required=True)
    a.add_argument("--report", required=True)
    a.add_argument("--timeout", default=None, help="convert_timeout for the whole assembly (default 300)")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 2
    proc.close_lock_fd()
    proc.install_term_handler()
    policymod.warn_ignored_hooks()
    return {"map": cmd_map, "fetch-bundle": cmd_fetch_bundle, "verify": cmd_verify, "assemble": cmd_assemble}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
