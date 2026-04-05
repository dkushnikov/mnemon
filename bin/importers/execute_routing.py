#!/usr/bin/env python3
"""
Routing executor — copies routed dump items to their destination targets.

After route.py assigns `destination` to each dump item, this script acts on
each destination according to the config:
  - `knowledge` (reserved): builds a JSON manifest for Mnemon's batch gateway
    instead of copying files. Original items are marked routed.
  - `skip` (reserved): items are marked `status: routed` and left in place,
    no copy.
  - Any other destination: items are copied to the target folder specified
    in the config file. Originals are marked `status: routed` with a
    `routed_to` field for audit/lineage.

Copy semantics (not move): originals remain in Dump as audit trail. Delete
the dump later when you're confident.

Usage:
    execute_routing.py --dump /path/to/dump \\
                       --config /path/to/routing-targets.yaml \\
                       [--dry-run] [--destination <name>]

Config format (YAML):
    work: /path/to/work-vault/Inbox/From Dump
    personal: /path/to/personal-vault/Inbox
    private: /path/to/private-vault/Inbox
    knowledge_manifest: /path/to/manifest.json
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_text = m.group(1)
    body = text[m.end():]
    fm = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        fm[key.strip()] = val
    return fm, body


def serialize_frontmatter(fm: dict, body: str) -> str:
    lines = ["---"]
    for key, val in fm.items():
        if val is None or val == "":
            lines.append(f"{key}: ")
        elif isinstance(val, (int, float)):
            lines.append(f"{key}: {val}")
        elif any(c in str(val) for c in ":\"'#\n"):
            escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.lstrip("\n")


def load_targets(config_path: Path) -> dict:
    """Load routing targets from a simple YAML config.

    Each key is a destination name. Each value is the target folder path
    (for copy destinations) or the manifest file path (for
    `knowledge_manifest`). The special destinations `knowledge` and `skip`
    are reserved for manifest/no-op handling.

    Example:
        work: /Users/me/vaults/work/Inbox
        personal: /Users/me/vaults/personal/Inbox
        knowledge_manifest: /tmp/knowledge-manifest.json
    """
    import yaml
    text = config_path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text) or {}
    return {k: str(Path(v).expanduser()) if v else None for k, v in cfg.items()}


def collect_routed_items(dump_root: Path) -> list[tuple[Path, dict]]:
    """Find all items with destination set and status=classified (not yet routed)."""
    items = []
    for p in sorted(dump_root.rglob("*.md")):
        if p.name.lower() in ("readme.md", "index.md") or "import log" in p.name.lower():
            continue
        if "_meta" in p.parts or "_attachments" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, body = parse_frontmatter(text)
        if not fm:
            continue
        if fm.get("status") != "classified":
            continue
        dest = fm.get("destination", "")
        if not dest:
            continue
        items.append((p, fm))
    return items


def build_inbox_filename(source_path: Path, fm: dict) -> str:
    """Generate target filename in inbox. Preserves original title with source prefix."""
    source = fm.get("source", "unknown").replace("/", "-")
    # Use the file stem — already has useful info (hash-slug or date-title)
    return f"{source}-{source_path.stem}.md"


def copy_to_inbox(source_path: Path, target_root: Path, fm: dict, body: str, dry_run: bool) -> Path:
    """Copy item to destination inbox. Adds routing metadata to copy."""
    target_root.mkdir(parents=True, exist_ok=True)
    target_name = build_inbox_filename(source_path, fm)
    target_path = target_root / target_name

    # Handle collisions
    counter = 2
    while target_path.exists():
        stem = target_name.rsplit(".md", 1)[0]
        target_path = target_root / f"{stem}-{counter}.md"
        counter += 1

    # Add routing context to the copy
    copy_fm = dict(fm)  # preserve all original fields
    copy_fm["routed_from_dump"] = str(source_path)
    copy_fm["routed_on"] = date.today().isoformat()
    copy_fm["status"] = "routed"  # in the copy, so vault Claude knows it's inbox item

    if dry_run:
        return target_path

    content = serialize_frontmatter(copy_fm, body)
    target_path.write_text(content, encoding="utf-8")
    return target_path


def mark_original_routed(source_path: Path, target_path: Path, fm: dict, body: str, dry_run: bool) -> None:
    """Mark the source item in Dump with status=routed and routed_to path."""
    if dry_run:
        return
    fm["status"] = "routed"
    fm["routed_to"] = str(target_path)
    source_path.write_text(serialize_frontmatter(fm, body), encoding="utf-8")


def build_knowledge_manifest(items: list[tuple[Path, dict, str]]) -> list[dict]:
    """Build a manifest for Mnemon batch gateway."""
    manifest = []
    for source_path, fm, body in items:
        url = fm.get("url", "").strip()
        if url and url.startswith("http"):
            # URL item — use url origin
            entry = {
                "origin": "url",
                "url": url,
                "title": fm.get("title", "").strip(),
            }
        else:
            # Content item — use ref:vault origin
            entry = {
                "origin": "ref:vault",
                "ref_path": str(source_path),
                "title": fm.get("title", source_path.stem).strip(),
            }
        # Carry over source_type hint if available
        cat = fm.get("category", "")
        type_hints = {
            "ai": "article",
            "tech": "article",
            "product": "article",
            "tool": "article",
            "reference": "document",
            "learning": "article",
        }
        entry["source_type"] = type_hints.get(cat, "article")
        manifest.append(entry)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Execute routing: copy dump items to destination vaults")
    parser.add_argument("--dump", required=True, help="Dump vault root")
    parser.add_argument("--config", required=True, help="Routing targets config (YAML)")
    parser.add_argument("--destination", help="Only execute for one named destination (e.g. 'knowledge', 'work', 'skip')")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dump_root = Path(args.dump).expanduser()
    config_path = Path(args.config).expanduser()
    if not dump_root.exists():
        print(f"ERROR: dump not found: {dump_root}", file=sys.stderr)
        sys.exit(1)
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    try:
        targets = load_targets(config_path)
    except ImportError:
        print("ERROR: pyyaml required. Install: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    items = collect_routed_items(dump_root)
    print(f"Found {len(items)} items ready for routing execution")
    if args.dry_run:
        print("=== DRY RUN — no files will be modified ===")

    # Group by destination
    from collections import defaultdict
    by_dest = defaultdict(list)
    for p, fm in items:
        by_dest[fm.get("destination", "")].append((p, fm))

    print("\nBreakdown:")
    # Show reserved destinations first, then copy destinations from config, then any extras
    reserved = ["knowledge", "skip"]
    config_dests = [k for k in targets.keys() if k not in ("knowledge_manifest",) and k not in reserved]
    ordered = reserved + config_dests
    for dest in ordered:
        print(f"  {dest:12s}  {len(by_dest.get(dest, [])):5d}")
    for dest, items_list in sorted(by_dest.items()):
        if dest not in ordered:
            print(f"  {dest:12s}  {len(items_list):5d}  (not in config — will be skipped)")

    if args.destination:
        by_dest = {args.destination: by_dest.get(args.destination, [])}
        print(f"\n[Filter: only {args.destination}]")

    # Execute per destination
    stats = defaultdict(int)
    knowledge_items = []

    for dest, dest_items in by_dest.items():
        if not dest_items:
            continue
        print(f"\n--- {dest} ({len(dest_items)} items) ---")

        if dest == "skip":
            # Mark skipped items as routed so they don't reappear
            for source_path, fm in dest_items:
                text = source_path.read_text(encoding="utf-8")
                fm_full, body = parse_frontmatter(text)
                if not args.dry_run:
                    fm_full["status"] = "routed"
                    source_path.write_text(serialize_frontmatter(fm_full, body), encoding="utf-8")
                stats["skip_marked"] += 1
            print(f"  Marked {stats['skip_marked']} items as routed (skip)")
            continue

        if dest == "knowledge":
            # Build manifest for Mnemon gateway
            for source_path, fm in dest_items:
                text = source_path.read_text(encoding="utf-8")
                _, body = parse_frontmatter(text)
                knowledge_items.append((source_path, fm, body))
                # Mark original as routed
                if not args.dry_run:
                    fm["status"] = "routed"
                    fm["routed_to"] = "knowledge-vault (via Mnemon gateway)"
                    source_path.write_text(serialize_frontmatter(fm, body), encoding="utf-8")
                stats["knowledge_queued"] += 1
            print(f"  Queued {stats['knowledge_queued']} items for Mnemon manifest")
            continue

        # Any other destination — copy to target folder from config
        target_root = targets.get(dest)
        if not target_root:
            print(f"  ! No target configured for '{dest}' in config, skipping {len(dest_items)} items")
            continue
        target_root_path = Path(target_root)

        for source_path, fm in dest_items:
            text = source_path.read_text(encoding="utf-8")
            fm_full, body = parse_frontmatter(text)
            try:
                target_path = copy_to_inbox(source_path, target_root_path, fm_full, body, args.dry_run)
                mark_original_routed(source_path, target_path, fm_full, body, args.dry_run)
                stats[f"{dest}_copied"] += 1
            except Exception as e:
                print(f"  ! failed {source_path.name}: {e}", file=sys.stderr)
                stats[f"{dest}_failed"] += 1

        print(f"  Copied {stats[f'{dest}_copied']} items to {target_root_path}")
        if stats.get(f"{dest}_failed"):
            print(f"  Failed: {stats[f'{dest}_failed']}")

    # Write knowledge manifest
    if knowledge_items:
        manifest_path = targets.get("knowledge_manifest")
        if not manifest_path:
            manifest_path = str(dump_root / "_meta" / "knowledge-manifest.json")
        manifest = build_knowledge_manifest(knowledge_items)
        if not args.dry_run:
            Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(manifest_path).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n✓ Knowledge manifest: {manifest_path} ({len(manifest)} items)")
        print(f"  Next step: ~/Mnemon/bin/knowledge-gateway.sh source-add \\")
        print(f"             --manifest {manifest_path} \\")
        print(f"             --context personal \\")
        print(f"             --import-source dump \\")
        print(f"             --import-batch 2026-04-05-dump-triage")

    print("\n=== Summary ===")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
