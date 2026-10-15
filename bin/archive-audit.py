#!/usr/bin/env python3
"""Mnemon archive consistency audit — sources <-> archived originals.

Reports three gaps:
  A — post-feature non-ref sources missing an `archive:` field (preservation gap)
  B — `archive:` pointing to a file that is not in the archive dir (dangling)
  C — orphan archives: files in the archive dir no source references

Phase 2 of the capture layer adds two archive members per revision that no `archive:` field
names: the sidecar `<revision>.report.json` (the frozen capture report) and the bundle
directory `<revision>.site/` of a site capture (named by `archive:`). A sidecar whose revision
folder exists is not an orphan; a bundle is one archive entry like a file.

Read-only. Usage: archive-audit.py [--config PATH] [--cutoff YYYY-MM-DD]
The config is read through the engine's loader (bin/mnemon-config.sh via
lib_config): with --config that file, otherwise the resolved instance
(MNEMON_INSTANCE / MNEMON_CONFIG); `mnemon audit` passes it explicitly. No
second yaml parser lives here.
"""
import os
import re
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lib_config  # noqa: E402

REF_ORIGINS = {"ref:vault", "ref:mcp"}


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
    ap.add_argument("--config", default=None,
                    help="instance config file (default: the engine's resolved config)")
    ap.add_argument("--cutoff", default="2026-04-23", help="archival feature ship date")
    args = ap.parse_args()

    try:
        cfg = lib_config.load_config(args.config)
    except lib_config.ConfigError as e:
        print(f"{e}", file=sys.stderr)
        sys.exit(2)
    args.config = cfg["MNEMON_CONFIG_FILE"]
    vault = cfg["VAULT_PATH"]
    archive_dir = cfg.get("ARCHIVE_DIR", "")

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
    # sidecars belong to their revision folder, never to an `archive:` field
    revisions = {s["dir"] for s in sources}
    sidecars = {f for f in orig_files if f.endswith(".report.json")}
    orig_files -= sidecars
    orphan_sidecars = sorted(f for f in sidecars if f[: -len(".report.json")] not in revisions)

    # An archive value is "real" if it names a file (not the unarchivable marker).
    def real_archive(s):
        return s["archive"] and s["archive"] != "unarchivable"

    gap_a = [s for s in sources
             if s["captured"] >= args.cutoff and s["origin"] not in REF_ORIGINS and not s["archive"]]
    gap_b = [s for s in sources
             if real_archive(s) and os.path.basename(s["archive"]) not in orig_files]
    referenced = {os.path.basename(s["archive"]) for s in sources if real_archive(s)}
    gap_c = sorted(orig_files - referenced) + orphan_sidecars

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
