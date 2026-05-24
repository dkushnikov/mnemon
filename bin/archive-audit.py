#!/usr/bin/env python3
"""Mnemon archive consistency audit — sources <-> archived originals.

Reports three gaps:
  A — post-feature non-ref sources missing an `archive:` field (preservation gap)
  B — `archive:` pointing to a file that is not in the archive dir (dangling)
  C — orphan archives: files in the archive dir no source references

Read-only. Usage: archive-audit.py [--config PATH] [--cutoff YYYY-MM-DD]
"""
import os
import re
import sys
import argparse

REF_ORIGINS = {"ref:vault", "ref:mcp"}


def yaml_val(path, key):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.match(rf'^{re.escape(key)}:\s*(.*)$', line)
                if m:
                    v = re.sub(r'\s+#.*$', '', m.group(1)).strip().strip('"').strip("'")
                    return os.path.expanduser(v)
    except OSError:
        pass
    return ""


def parse_frontmatter(path):
    fm = {}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return fm
    if not lines or lines[0].strip() != "---":
        return fm
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', ln)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return fm


def main():
    ap = argparse.ArgumentParser(description="Mnemon archive consistency audit")
    ap.add_argument("--config", default=os.path.expanduser("~/Mnemon/mnemon.yaml"))
    ap.add_argument("--cutoff", default="2026-04-23", help="archival feature ship date")
    args = ap.parse_args()

    vault = yaml_val(args.config, "vault_path")
    archive_dir = yaml_val(args.config, "archive_dir")
    if not vault:
        print(f"ERROR: vault_path not found in {args.config}", file=sys.stderr)
        sys.exit(2)

    sources_dir = os.path.join(vault, "Sources")
    sources = []
    if os.path.isdir(sources_dir):
        for name in sorted(os.listdir(sources_dir)):
            sm = os.path.join(sources_dir, name, "source.md")
            if os.path.isfile(sm):
                fm = parse_frontmatter(sm)
                sources.append({
                    "dir": name,
                    "captured": fm.get("captured", ""),
                    "origin": fm.get("origin", "?"),
                    "archive": fm.get("archive", ""),
                })

    orig_files = set()
    if archive_dir and os.path.isdir(archive_dir):
        orig_files = {f for f in os.listdir(archive_dir) if not f.startswith(".")}

    # An archive value is "real" if it names a file (not the unarchivable marker).
    def real_archive(s):
        return s["archive"] and s["archive"] != "unarchivable"

    gap_a = [s for s in sources
             if s["captured"] >= args.cutoff and s["origin"] not in REF_ORIGINS and not s["archive"]]
    gap_b = [s for s in sources
             if real_archive(s) and os.path.basename(s["archive"]) not in orig_files]
    referenced = {os.path.basename(s["archive"]) for s in sources if real_archive(s)}
    gap_c = sorted(orig_files - referenced)

    print(f"Vault: {vault}")
    print(f"Archive dir: {archive_dir or '(none)'}")
    print(f"Sources: {len(sources)} | Archives: {len(orig_files)} | cutoff: {args.cutoff}\n")

    print(f"GAP A — post-feature non-ref sources missing archive: {len(gap_a)}")
    for s in gap_a[:25]:
        print(f"    {s['captured']}  {s['origin']:8}  {s['dir']}")
    print(f"\nGAP B — archive: -> missing file: {len(gap_b)}")
    for s in gap_b[:25]:
        print(f"    {s['dir']}  ->  {s['archive']}")
    print(f"\nGAP C — orphan archives (no referencing source): {len(gap_c)}")
    for f in gap_c[:25]:
        print(f"    {f}")

    print(f"\nTotal issues: {len(gap_a) + len(gap_b) + len(gap_c)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
