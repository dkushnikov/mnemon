#!/usr/bin/env python3
"""
Fix broken attachment links in routed inbox items.

After execute_routing.py copies dump items to target vault inboxes, wikilinks
to attachments break because the attachments stayed in the source vault's
`_attachments/` folder. This script fixes that by:

1. Scanning all .md files in the target vault's From Dump inbox
2. Extracting attachment references (both `![[name.ext]]` wikilinks and
   `![](name.ext)` markdown images)
3. Finding each referenced attachment in the source vault's _attachments/
4. Copying referenced attachments into the target vault's
   `_attachments/From Dump/` folder

After this runs, Obsidian resolves wikilinks by filename vault-wide and
attachments display correctly.

Usage:
    fix_attachments.py --source ~/Obsidian/Dump \\
                       --target ~/Obsidian/Personal \\
                       --inbox "00 — Inbox/From Dump"
"""

import argparse
import re
import shutil
import sys
from pathlib import Path


# Wikilink image: ![[name.png]] or [[name.png]]
# Markdown image: ![](name.png) or ![alt](name%20with%20space.png)
WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")
MARKDOWN_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


ATTACHMENT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".heic",
    ".tiff", ".tif", ".bmp", ".ico",
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".ods", ".odt",
    ".mp4", ".mov", ".m4v", ".webm", ".avi",
    ".mp3", ".wav", ".m4a", ".flac",
    ".zip", ".tar", ".gz",
    ".p12", ".pem",
}


def is_attachment(name: str) -> bool:
    """Check if a filename looks like an attachment (not a markdown link)."""
    if not name or name.endswith(".md"):
        return False
    # URL-decode basic cases
    from urllib.parse import unquote
    name = unquote(name)
    lower = name.lower()
    return any(lower.endswith(ext) for ext in ATTACHMENT_EXTS)


def extract_attachment_refs(md_text: str) -> set[str]:
    """Return set of attachment filenames referenced in markdown."""
    from urllib.parse import unquote
    refs = set()

    for m in WIKILINK_RE.finditer(md_text):
        target = m.group(1).strip()
        if is_attachment(target):
            # Take just the filename, drop any path prefix
            refs.add(Path(target).name)

    for m in MARKDOWN_IMG_RE.finditer(md_text):
        target = unquote(m.group(1).strip())
        # Skip URLs
        if target.startswith("http://") or target.startswith("https://"):
            continue
        if is_attachment(target):
            refs.add(Path(target).name)

    return refs


def build_attachment_index(source_attachments: Path) -> dict[str, Path]:
    """Index all attachments in source vault by filename."""
    index = {}
    if not source_attachments.exists():
        return index
    for p in source_attachments.rglob("*"):
        if p.is_file() and is_attachment(p.name):
            # Multiple files with same name → keep first (should be rare)
            index.setdefault(p.name, p)
    return index


def main():
    parser = argparse.ArgumentParser(description="Copy referenced attachments from source vault to target vault inbox")
    parser.add_argument("--source", required=True, help="Source vault (e.g. ~/Obsidian/Dump)")
    parser.add_argument("--target", required=True, help="Target vault (e.g. ~/Obsidian/Personal)")
    parser.add_argument("--inbox", required=True, help="Inbox path relative to target vault")
    parser.add_argument("--attachments-dir", default="_attachments/From Dump",
                        help="Destination within target vault (default: '_attachments/From Dump')")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    target = Path(args.target).expanduser()
    inbox = target / args.inbox
    dest_attachments = target / args.attachments_dir

    if not inbox.exists():
        print(f"ERROR: inbox not found: {inbox}", file=sys.stderr)
        sys.exit(1)

    # Index all attachments in source vault
    source_attachments = source / "_attachments"
    print(f"Indexing attachments in {source_attachments}...")
    index = build_attachment_index(source_attachments)
    print(f"  indexed {len(index)} files")

    # Scan inbox for referenced attachments
    referenced = set()
    md_files = list(inbox.rglob("*.md"))
    print(f"Scanning {len(md_files)} markdown files in {inbox}...")
    for md in md_files:
        try:
            text = md.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  ! failed to read {md.name}: {e}", file=sys.stderr)
            continue
        refs = extract_attachment_refs(text)
        referenced.update(refs)

    print(f"  found {len(referenced)} unique attachment references")

    # Find which are resolvable
    resolvable = []
    missing = []
    for name in sorted(referenced):
        if name in index:
            resolvable.append((name, index[name]))
        else:
            missing.append(name)

    print(f"  resolvable: {len(resolvable)}")
    print(f"  missing:    {len(missing)}")

    if missing:
        print("\nMissing attachments (first 10):")
        for name in missing[:10]:
            print(f"  - {name}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    # Copy resolvable attachments
    if args.dry_run:
        print(f"\n[DRY RUN] Would copy {len(resolvable)} files to {dest_attachments}")
        return

    if not resolvable:
        print("\nNothing to copy.")
        return

    dest_attachments.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped_existing = 0
    errors = 0
    bytes_copied = 0
    for name, src_path in resolvable:
        dest = dest_attachments / name
        if dest.exists() and dest.stat().st_size == src_path.stat().st_size:
            skipped_existing += 1
            continue
        try:
            shutil.copy2(src_path, dest)
            copied += 1
            bytes_copied += src_path.stat().st_size
        except Exception as e:
            print(f"  ! failed to copy {name}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nCopied: {copied} files ({bytes_copied / 1024 / 1024:.1f} MB)")
    if skipped_existing:
        print(f"Skipped (already exist): {skipped_existing}")
    if errors:
        print(f"Errors: {errors}")
    print(f"Destination: {dest_attachments}")


if __name__ == "__main__":
    main()
