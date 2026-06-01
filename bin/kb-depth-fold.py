#!/usr/bin/env python3
"""kb-depth-fold — deterministic fold engine for Mnemon depth-aware capture.

Runs kb-probe-deeper (discovery); if the landing is thin (or --depth force) AND
there are HIGH-confidence same-entity targets, fetches them + the landing and
emits a composite source body + provenance manifest as JSON for the gateway to
pass into `claude -p` (verbatim source.md + extract.md). Fail-safe: only
high-confidence folds; medium/low → recorded in manifest, not folded.

Design: docs/specs/2026-06-01-depth-aware-capture.md (D2/D4/D5).
Usage: kb-depth-fold.py <url> [--depth auto|off|force] [--max-targets 4]
       [--cap-bytes 120000]
Output: JSON {applied, reason, composite_body, manifest_yaml, folded, errors}.
"""
from __future__ import annotations
import sys
import os
import json
import hashlib
import argparse
import subprocess
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_url      # noqa: E402
import kb_fetch     # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

PROBE = os.path.join(HERE, "kb-probe-deeper.py")
CAP_BYTES_DEFAULT = 120_000


def run_probe(url: str):
    try:
        r = subprocess.run([sys.executable, PROBE, url],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return json.loads(r.stdout)
        return {"_error": f"probe rc={r.returncode}: {r.stderr.strip()[:200]}"}
    except Exception as e:
        return {"_error": f"probe exec: {type(e).__name__}"}


def gh_readme(repo: str, cap: int):
    for ref in ("main", "master"):
        fu, st, txt = kb_fetch.fetch(
            f"https://raw.githubusercontent.com/{repo}/{ref}/README.md", cap=cap)
        if fu is not None and st == 200 and txt.strip():
            return txt, ref
    return "", ""


def _cap(text: str, cap_bytes: int):
    raw = text.encode("utf-8")
    if len(raw) <= cap_bytes:
        return text, False
    return raw[:cap_bytes].decode("utf-8", "ignore") + f"\n\n[TRUNCATED at {cap_bytes} bytes]", True


def to_yaml(obj) -> str:
    if yaml is not None:
        return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False).rstrip()
    return json.dumps(obj, ensure_ascii=False, indent=2)


def fold(url: str, mode: str, max_targets: int, cap_bytes: int) -> dict:
    if mode == "off":
        return {"applied": False, "reason": "depth off"}
    pj = run_probe(url)
    if pj is None or "_error" in pj:
        return {"applied": False, "reason": "probe failed",
                "errors": [pj.get("_error") if pj else "probe none"]}
    thin = bool(pj.get("thin"))
    if not thin and mode != "force":
        return {"applied": False, "reason": f"landing not thin ({pj.get('word_count')} words)"}
    high = [c for c in pj.get("candidates", []) if c["same_entity_confidence"] == "high"][:max_targets]
    if not high:
        return {"applied": False, "reason": "no high-confidence same-entity (fail-safe: shallow capture)",
                "errors": pj.get("errors", [])}

    now = datetime.now(timezone.utc).isoformat()
    errors = list(pj.get("errors", []))
    landing_md = kb_fetch.defuddle_md(url) or "(landing content unavailable)"
    sections = [f"--- Landing ({url}) ---\n\n{landing_md.strip()}"]
    folded = []

    for c in high:
        cu, ctype = c["url"], c["type"]
        text, ref, status = "", "", None
        if ctype == "github":
            repo = lib_url.github_repo(cu)
            text, ref = gh_readme(repo, cap_bytes)
            status = 200 if text else None
            role, header = "github_repo", f"--- README ({cu} @ {ref or '?'}) ---"
        elif ctype == "docs":
            text = kb_fetch.defuddle_md(cu)
            status = 200 if text else None
            role, header = "docs_index", f"--- Docs ({cu}) ---"
        elif ctype == "llms.txt":
            fu, st, t = kb_fetch.fetch(cu, cap=cap_bytes)
            text, status = (t if fu is not None else ""), st
            role, header = "llms_txt", f"--- llms.txt ({cu}) ---"
        else:
            continue
        if not text.strip():
            errors.append(f"fold fetch empty: {cu}")
            continue
        text, truncated = _cap(text, cap_bytes)
        sections.append(f"{header}\n\n{text.strip()}")
        folded.append({
            "url": cu, "role": role, "same_entity_confidence": "high",
            "evidence": c.get("evidence", []), "fetched_at": now,
            "http_status": status, "ref": ref,
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "truncated": truncated,
        })

    if not folded:
        return {"applied": False, "reason": "high candidates found but all fetches failed",
                "errors": errors}

    external = [c["url"] for c in pj.get("candidates", []) if c["same_entity_confidence"] != "high"]
    body = ("--- Capture provenance ---\n"
            f"Posted: {url}. Landing thin ({pj.get('word_count')} words) → folded "
            f"{len(folded)} high-confidence same-entity target(s).\n"
            f"Folded: {', '.join(f['url'] for f in folded)}.\n"
            + (f"Candidates not folded (confidence<high): {', '.join(external)}.\n" if external else "")
            + "\n" + "\n\n".join(sections) + "\n")

    manifest = {
        "posted_url": pj.get("posted_url"),
        "final_url": pj.get("final_url"),
        "canonical_url": pj.get("canonical_url", ""),
        "folded_sources": folded,
        "external_refs_not_folded": external,
        "depth": {
            "probe_version": "kb-probe-deeper@1.0",
            "triggered_by": "force" if mode == "force" else "thin",
            "rendered": pj.get("rendered", False),
            "candidates_considered": [
                {"url": c["url"], "confidence": c["same_entity_confidence"], "type": c["type"]}
                for c in pj.get("candidates", [])],
            "caps_applied": {"hop": 1, "targets": max_targets, "bytes_per_target": cap_bytes},
            "errors": errors,
        },
    }
    return {"applied": True, "reason": "folded", "composite_body": body,
            "manifest_yaml": to_yaml(manifest), "folded": [f["url"] for f in folded],
            "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--depth", choices=["auto", "off", "force"], default="auto")
    ap.add_argument("--max-targets", type=int, default=4)
    ap.add_argument("--cap-bytes", type=int, default=CAP_BYTES_DEFAULT)
    a = ap.parse_args()
    print(json.dumps(fold(a.url, a.depth, a.max_targets, a.cap_bytes), ensure_ascii=False))


if __name__ == "__main__":
    main()
