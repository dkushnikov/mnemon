#!/usr/bin/env python3
"""kb-depth-fold — deterministic fold engine for Mnemon depth-aware capture.

Runs kb-probe-deeper (discovery); if the landing is thin (or --depth force) AND there are HIGH-confidence
same-entity targets, fetches them through the capture policy and emits the folded targets + a provenance
manifest as JSON. Fail-safe: only high-confidence folds; medium/low → recorded in the manifest, not folded.

Design: docs/specs/2026-06-01-depth-aware-capture.md (D2/D4/D5); capture layer §3.2, §4.2, §12 A8, A9:
- the gateway runs the fold after the landing was acquired through the chain, with the staged raw
  (--landing-file) and a frozen acquisition context (--context: final_url, http_status, acquisition,
  render auto|off, external off, budget_s); the fold never fetches or renders the landing;
- the result lists only the folded targets (`targets`), never the landing's text: the gateway assembles
  `converted landing + targets` exactly once;
- a target whose response is non-2xx or classified as a wall by check.acquisition is rejected
  (`fold fetch rejected: <class>`), not folded;
- --budget-s (or the context's budget_s) bounds the probe (its own process group) and every fetch; an
  expired budget reports `error_class: timeout`, applied: false;
- the manifest is YAML: PyYAML is required (exit 3 with a clear message when it is missing).

Usage: kb-depth-fold.py <url> [--depth auto|off|force] [--max-targets 4] [--cap-bytes 120000]
                        [--landing-file PATH] [--budget-s S] [--context JSON] [--policy JSON]
Output: JSON {applied, reason, targets: [{url, role, header, text, status, sha256, ref, truncated}],
        manifest_yaml, folded, errors, error_class}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kb_fetch     # noqa: E402
import lib_url      # noqa: E402
from capture import check, convert, proc  # noqa: E402

PROBE = os.path.join(HERE, "kb-probe-deeper.py")
CAP_BYTES_DEFAULT = 120_000
PROBE_VERSION = "kb-probe-deeper@2.0"


def run_probe(url: str, landing_file, context: dict, deadline: proc.Deadline, policy_json):
    argv = [sys.executable, PROBE, url]
    if landing_file:
        argv += ["--landing-file", landing_file]
    if context:
        argv += ["--context", json.dumps(context)]
    rem = deadline.remaining()
    if rem is not None:
        argv += ["--budget-s", "%.3f" % rem]
    if policy_json:
        argv += ["--policy", policy_json]
    res = proc.run(argv, timeout=120, deadline=deadline)
    if res.timed_out:
        return {"_error": "probe timed out", "_timeout": True}
    if res.rc == 0:
        try:
            return json.loads(res.stdout.decode("utf-8"))
        except ValueError:
            return {"_error": "probe output is not JSON"}
    return {"_error": "probe rc=%s: %s" % (res.rc, res.stderr.decode("utf-8", "replace").strip()[:200])}


def gh_readme(repo: str, cap: int, deadline: proc.Deadline):
    for ref in ("main", "master"):
        fu, st, txt = kb_fetch.fetch("https://raw.githubusercontent.com/%s/%s/README.md" % (repo, ref), cap=cap, deadline=deadline)
        if fu is not None and st == 200 and txt.strip():
            return txt, ref, 200
    return "", "", None


def rejected(resp) -> str:
    """Why a target response is not folded (A8): a non-2xx status or a wall classification."""
    if resp.status < 200 or resp.status >= 300:
        return "http %d" % resp.status
    acq = check.acquisition(resp.status, resp.headers, resp.body, thin_words=1)
    if acq.kind in check.WALL_KINDS or acq.kind == "js-shell":
        return acq.kind
    return ""


def _cap(text: str, cap_bytes: int):
    raw = text.encode("utf-8")
    if len(raw) <= cap_bytes:
        return text, False
    return raw[:cap_bytes].decode("utf-8", "ignore") + "\n\n[TRUNCATED at %d bytes]" % cap_bytes, True


def fold(url: str, mode: str, max_targets: int, cap_bytes: int, landing_file, context: dict,
         deadline: proc.Deadline, policy_json, yaml_mod) -> dict:
    if mode == "off":
        return {"applied": False, "reason": "depth off"}
    pj = run_probe(url, landing_file, context, deadline, policy_json)
    if pj is None or "_error" in pj:
        out = {"applied": False, "reason": "probe failed", "errors": [pj.get("_error") if pj else "probe none"]}
        if pj and pj.get("_timeout"):
            out.update({"reason": "probe timed out", "error_class": "timeout"})
        return out
    thin = bool(pj.get("thin"))
    if not thin and mode != "force":
        return {"applied": False, "reason": "landing not thin (%s words)" % pj.get("word_count")}
    high = [c for c in pj.get("candidates", []) if c["same_entity_confidence"] == "high"][:max_targets]
    if not high:
        return {"applied": False, "reason": "no high-confidence same-entity (fail-safe: shallow capture)",
                "errors": pj.get("errors", [])}

    now = datetime.now(timezone.utc).isoformat()
    errors = list(pj.get("errors", []))
    targets = []
    folded = []
    timed_out = False
    for c in high:
        if deadline.expired():
            errors.append("budget exhausted before %s" % c["url"])
            timed_out = True
            break
        cu, ctype = c["url"], c["type"]
        text, ref, status = "", "", None
        if ctype == "github":
            repo = lib_url.github_repo(cu)
            text, ref, status = gh_readme(repo, cap_bytes, deadline)
            role, header = "github_repo", "--- README (%s @ %s) ---" % (cu, ref or "?")
        elif ctype in ("docs", "llms.txt"):
            resp, kind = kb_fetch.fetch_response(cu, cap=max(cap_bytes, 500_000), deadline=deadline)
            if resp is None:
                errors.append("fold fetch failed: %s: %s" % (cu, kind))
                if kind == "timeout":
                    timed_out = True
                continue
            why = rejected(resp)
            if why:
                errors.append("fold fetch rejected: %s (%s)" % (why, cu))
                continue
            status = resp.status
            if ctype == "docs":
                text = kb_fetch.defuddle_bytes(resp.body, deadline)
                if not text.strip():
                    try:
                        html_text, _ = convert.decode(resp.body, resp.charset)
                        text = convert.dom_text(convert.content_subtree(convert.parse(html_text), "page"))
                    except convert.ConvertError:
                        text = ""
                role, header = "docs_index", "--- Docs (%s) ---" % cu
            else:
                text = resp.text("ignore")
                role, header = "llms_txt", "--- llms.txt (%s) ---" % cu
        else:
            continue
        if not text.strip():
            errors.append("fold fetch empty: %s" % cu)
            continue
        text, truncated = _cap(text, cap_bytes)
        targets.append({"url": cu, "role": role, "header": header, "text": text.strip(), "status": status,
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "ref": ref, "truncated": truncated})
        folded.append({
            "url": cu, "role": role, "same_entity_confidence": "high",
            "evidence": c.get("evidence", []), "fetched_at": now,
            "http_status": status, "ref": ref,
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "truncated": truncated,
        })

    if not folded:
        out = {"applied": False, "reason": "high candidates found but all fetches failed", "errors": errors}
        if timed_out:
            out.update({"reason": "timeout", "error_class": "timeout"})
        return out

    external = [c["url"] for c in pj.get("candidates", []) if c["same_entity_confidence"] != "high"]
    manifest = {
        "posted_url": pj.get("posted_url"),
        "final_url": context.get("final_url") or pj.get("final_url"),
        "declared_canonical_url": pj.get("declared_canonical_url", ""),
        "folded_sources": folded,
        "external_refs_not_folded": external,
        "depth": {
            "probe_version": PROBE_VERSION,
            "triggered_by": "force" if mode == "force" else "thin",
            "rendered": pj.get("rendered", False),
            "candidates_considered": [
                {"url": c["url"], "confidence": c["same_entity_confidence"], "type": c["type"]}
                for c in pj.get("candidates", [])],
            "caps_applied": {"hop": 1, "targets": max_targets, "bytes_per_target": cap_bytes},
            "errors": errors,
        },
    }
    manifest_yaml = yaml_mod.safe_dump(manifest, allow_unicode=True, sort_keys=False).rstrip()
    return {"applied": True, "reason": "folded", "targets": targets, "manifest_yaml": manifest_yaml,
            "folded": [f["url"] for f in folded], "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--depth", choices=["auto", "off", "force"], default="auto")
    ap.add_argument("--max-targets", type=int, default=4)
    ap.add_argument("--cap-bytes", type=int, default=CAP_BYTES_DEFAULT)
    ap.add_argument("--landing-file", default=None, help="the staged landing raw: never fetched or rendered again")
    ap.add_argument("--budget-s", type=float, default=None, help="the fold's whole budget in seconds")
    ap.add_argument("--context", default=None, help="JSON: final_url, http_status, acquisition, render, external, budget_s")
    ap.add_argument("--policy", default=None, help="JSON: fetch_user_agent, fetch_timeout, fetch_max_bytes")
    a = ap.parse_args()
    proc.close_lock_fd()
    proc.install_term_handler()
    if a.depth == "off":
        print(json.dumps({"applied": False, "reason": "depth off"}))
        return 0
    try:
        import yaml as yaml_mod
    except ImportError:
        print("kb-depth-fold: PyYAML is required for the depth manifest (pip install pyyaml); no fold applied",
              file=sys.stderr)
        return 3
    context = {}
    if a.context:
        try:
            context = json.loads(a.context)
        except ValueError as exc:
            print("kb-depth-fold: --context is not valid JSON: %s" % exc, file=sys.stderr)
            return 2
    if a.policy:
        try:
            kb_fetch.configure(json.loads(a.policy))
        except ValueError as exc:
            print("kb-depth-fold: --policy is not valid JSON: %s" % exc, file=sys.stderr)
            return 2
    budget = a.budget_s if a.budget_s is not None else context.get("budget_s")
    deadline = proc.Deadline(budget) if budget is not None else proc.Deadline.never()
    result = fold(a.url, a.depth, a.max_targets, a.cap_bytes, a.landing_file, context, deadline, a.policy, yaml_mod)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
