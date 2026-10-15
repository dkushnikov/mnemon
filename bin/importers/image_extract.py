#!/usr/bin/env python3
"""
Image Extract — process image-heavy notes through Claude Vision.

Goes through markdown NOTES (not raw image files), finds referenced images,
sends them to Claude Vision for analysis, and enriches the note's frontmatter
with extracted information.

A note might reference multiple images that together tell a story. The note's
context (title, folder path, any existing text) helps interpret the images.
This keeps the pipeline note-centric, consistent with light_extract.py.

Flow:
  1. Scan .md files for image references ([[name.jpeg]], ![](name.png))
  2. Filter to image-only or image-heavy notes (configurable threshold)
  3. For each note: resolve image paths → send note context + images to Vision
  4. Enrich note frontmatter with extract fields (same schema as light_extract)

Usage:
    image_extract.py --notes-dir ~/Obsidian/Dump/Apple\\ Notes \\
                     --attachments-dir ~/Obsidian/Dump/_attachments/Apple\\ Notes \\
                     --recursive

    image_extract.py --notes-dir ~/Obsidian/Dump/Apple\\ Notes \\
                     --attachments-dir ~/Obsidian/Dump/_attachments \\
                     --limit 5 --dry-run

Requires: claude CLI (Claude Code), sips (macOS, for HEIC conversion).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_IMG_RE = re.compile(r"!?\[\[([^\]|#]+?\.(?:jpeg|jpg|png|heic|tiff|tif|gif|webp|bmp))\s*(?:\|[^\]]*)?\]\]", re.IGNORECASE)
MARKDOWN_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+\.(?:jpeg|jpg|png|heic|tiff|tif|gif|webp|bmp))\)", re.IGNORECASE)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".tiff", ".tif", ".gif", ".webp", ".bmp"}
NEEDS_CONVERSION = {".heic", ".tiff", ".tif"}
MAX_IMAGE_SIZE = 1024 * 1024  # 1MB — resize above this


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
        elif isinstance(val, list):
            if not val:
                lines.append(f"{key}: []")
            else:
                items_str = ", ".join(
                    f'"{v}"' if isinstance(v, str) else str(v) for v in val
                )
                lines.append(f"{key}: [{items_str}]")
        elif any(c in str(val) for c in ":\"'#\n"):
            escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.lstrip("\n")


def extract_image_refs(body: str) -> list[str]:
    """Extract image filenames referenced in note body."""
    from urllib.parse import unquote
    refs = []
    for m in WIKILINK_IMG_RE.finditer(body):
        refs.append(Path(m.group(1).strip()).name)
    for m in MARKDOWN_IMG_RE.finditer(body):
        refs.append(Path(unquote(m.group(1).strip())).name)
    return refs


def find_image_file(name: str, search_dirs: list[Path]) -> Optional[Path]:
    """Find an image file by name across search directories."""
    for d in search_dirs:
        # Direct match
        p = d / name
        if p.exists():
            return p
        # Recursive search (slower, fallback)
        for found in d.rglob(name):
            return found
    return None


def build_attachment_index(dirs: list[Path]) -> dict[str, Path]:
    """Index all image files by filename across directories."""
    index = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                index.setdefault(p.name, p)
    return index


def prepare_image(path: Path, tmp_dir: Path) -> Optional[Path]:
    """Convert/resize image if needed. Returns path to processable file."""
    suffix = path.suffix.lower()

    # Convert HEIC/TIFF to PNG
    if suffix in NEEDS_CONVERSION:
        converted = tmp_dir / f"{path.stem}.png"
        try:
            subprocess.run(
                ["sips", "-s", "format", "png", str(path), "--out", str(converted)],
                capture_output=True, timeout=30, check=True,
            )
            path = converted
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None  # can't convert

    # Resize if too large
    if path.stat().st_size > MAX_IMAGE_SIZE:
        resized = tmp_dir / f"resized_{path.name}"
        try:
            shutil.copy2(path, resized)
            subprocess.run(
                ["sips", "-Z", "1024", str(resized)],
                capture_output=True, timeout=30, check=True,
            )
            return resized
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return path  # use original if resize fails

    return path


def is_image_note(body: str, threshold: float = 0.5) -> bool:
    """Check if a note is image-heavy (image refs dominate the content)."""
    refs = extract_image_refs(body)
    if not refs:
        return False
    # Remove image references from body
    clean = re.sub(r"!?\[\[[^\]]+\]\]", "", body)
    clean = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", clean)
    clean = clean.strip()
    # Image-heavy if remaining text is short relative to number of images
    return len(clean) < 100 or len(refs) > 0


def needs_extraction(fm: dict) -> bool:
    """Check if this note needs image extraction."""
    # Already has image extract
    if fm.get("extract_type") == "image-ref" or fm.get("image_extracted"):
        return False
    # If light_extract already ran and it's NOT image-related, skip
    if fm.get("extract_type") and fm.get("extract_content_quality") != "image-only":
        return False
    return True


def collect_notes(notes_dir: Path, recursive: bool, limit: int = 0) -> list[Path]:
    """Find notes that reference images and need extraction."""
    pattern = "**/*.md" if recursive else "*.md"
    items = []
    for p in sorted(notes_dir.glob(pattern)):
        if p.name.lower() in ("readme.md", "index.md") or "import log" in p.name.lower():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, body = parse_frontmatter(text)
        if not needs_extraction(fm):
            continue
        refs = extract_image_refs(body)
        if not refs:
            continue
        items.append(p)
        if limit and len(items) >= limit:
            break
    return items


def process_note(note_path: Path, attachment_index: dict[str, Path],
                 model: str, tmp_dir: Path, dry_run: bool) -> Optional[dict]:
    """Process a single note: find images, send to vision, return extract."""
    text = note_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    title = fm.get("title", note_path.stem)
    refs = extract_image_refs(body)

    # Resolve image paths
    resolved = []
    missing = []
    for ref_name in refs:
        if ref_name in attachment_index:
            resolved.append((ref_name, attachment_index[ref_name]))
        else:
            missing.append(ref_name)

    if not resolved:
        return {"status": "no_images_found", "missing": missing}

    if dry_run:
        return {
            "status": "dry_run",
            "images_found": len(resolved),
            "images_missing": len(missing),
        }

    # Prepare images (convert/resize)
    prepared = []
    for ref_name, img_path in resolved[:5]:  # max 5 images per note
        prepped = prepare_image(img_path, tmp_dir)
        if prepped:
            prepared.append((ref_name, prepped))

    if not prepared:
        return {"status": "all_images_failed_prep"}

    # Build prompt — ask Claude to Read each image
    image_instructions = "\n".join(
        f"- Read the image at: {p}" for _, p in prepared
    )

    # Include note context
    clean_body = re.sub(r"!?\[\[[^\]]+\]\]", "", body).strip()[:500]

    prompt = f"""You are analyzing images referenced by a personal note.

NOTE CONTEXT:
Title: {title}
Folder: {'/'.join(note_path.parts[-3:-1]) if len(note_path.parts) > 2 else '(root)'}
Text in note (if any): {clean_body if clean_body else '(no text, image-only note)'}

IMAGES TO ANALYZE:
{image_instructions}

Read each image using the Read tool, then output a SINGLE JSON object describing what ALL the images in this note collectively represent:

{{
  "image_type": "screenshot | whiteboard | document | chart | photo | meme | mixed | other",
  "image_count": {len(prepared)},
  "content_quality": "rich | moderate | sparse | unreadable",
  "description": "One clear sentence: what these images show and what information they contain",
  "extracted_text": "Any significant text visible in the images (OCR). Empty string if none readable.",
  "extracted_urls": ["URLs visible in screenshots, if any"],
  "people_visible": ["Names of people if identifiable"],
  "suggested_action": "process-as-link | extract-as-notes | archive | skip",
  "extract_summary": "One sentence: what this NOTE is about, based on images + title + any text"
}}

Output ONLY the JSON object. No markdown, no commentary."""

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model,
             "--allowedTools", "Read",
             "--output-format", "text", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"status": "claude_error", "error": result.stderr[:200]}

        # Parse JSON from output (may have surrounding text)
        output = result.stdout.strip()
        json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", output, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"status": "parse_error", "raw": output[:300]}

    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except json.JSONDecodeError:
        return {"status": "json_error", "raw": output[:300]}


def apply_image_extract(note_path: Path, extract: dict) -> None:
    """Enrich note frontmatter with image extraction results."""
    text = note_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    fm["image_extracted"] = "true"
    fm["extract_type"] = extract.get("image_type", "image-ref")
    fm["extract_summary"] = extract.get("extract_summary", extract.get("description", ""))
    fm["extract_content_quality"] = extract.get("content_quality", "")
    fm["extract_image_count"] = extract.get("image_count", 0)
    fm["extract_description"] = extract.get("description", "")

    extracted_text = extract.get("extracted_text", "")
    if extracted_text:
        fm["extract_text_preview"] = extracted_text[:200]

    urls = extract.get("extracted_urls", [])
    if urls:
        fm["extract_links"] = urls

    people = extract.get("people_visible", [])
    if people:
        fm["extract_people"] = people

    fm["extract_suggested_action"] = extract.get("suggested_action", "")

    note_path.write_text(serialize_frontmatter(fm, body), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Process image-heavy notes through Claude Vision")
    parser.add_argument("--notes-dir", required=True,
                        help="Directory of markdown notes (e.g. ~/Obsidian/Dump/Apple Notes)")
    parser.add_argument("--attachments-dir", required=True, nargs="+",
                        help="Directory(ies) with image files to search "
                             "(e.g. ~/Obsidian/Dump/_attachments/Apple\\ Notes)")
    parser.add_argument("--recursive", action="store_true", default=True)
    parser.add_argument("--model", default="sonnet",
                        help="Claude model with vision support (default: sonnet)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip notes already image-extracted (default: True)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Start at item N (for parallel workers)")
    parser.add_argument("--stride", type=int, default=1,
                        help="Process every Nth item (for parallel workers: "
                             "worker 0: --offset 0 --stride 3, "
                             "worker 1: --offset 1 --stride 3, etc.)")
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir).expanduser()
    attachment_dirs = [Path(d).expanduser() for d in args.attachments_dir]

    if not notes_dir.exists():
        print(f"ERROR: notes directory not found: {notes_dir}", file=sys.stderr)
        sys.exit(1)

    # Build attachment index
    print("Indexing attachments...")
    attachment_index = build_attachment_index(attachment_dirs)
    print(f"  indexed {len(attachment_index)} image files")

    # Collect notes with image references
    items = collect_notes(notes_dir, args.recursive, args.limit)
    total = len(items)
    if total == 0:
        print("No image-heavy notes found that need extraction.")
        return

    # Apply stride/offset for parallel workers
    if args.stride > 1:
        items = [items[i] for i in range(args.offset, len(items), args.stride)]
        total = len(items)
        print(f"Worker offset={args.offset} stride={args.stride}: {total} items")

    print(f"Found {total} notes with image references")
    if args.dry_run:
        print("=== DRY RUN ===")

    # Process
    tmp_dir = Path(tempfile.mkdtemp(prefix="image-extract-"))
    stats = {"processed": 0, "errors": 0, "skipped": 0}
    type_counts = {}
    action_counts = {}

    try:
        for i, note_path in enumerate(items, 1):
            rel = note_path.relative_to(notes_dir) if notes_dir in note_path.parents else note_path.name
            print(f"[{i}/{total}] {str(rel)[:70]}", end="", flush=True)

            extract = process_note(note_path, attachment_index, args.model,
                                   tmp_dir, args.dry_run)

            if not extract or extract.get("status") in ("no_images_found", "all_images_failed_prep"):
                print(f" → skipped ({extract.get('status', 'unknown')})")
                stats["skipped"] += 1
                continue

            if args.dry_run:
                found = extract.get("images_found", 0)
                missing = extract.get("images_missing", 0)
                print(f" → {found} images found, {missing} missing")
                stats["processed"] += 1
                continue

            if extract.get("status") in ("claude_error", "timeout", "parse_error", "json_error"):
                print(f" → error: {extract.get('status')}")
                stats["errors"] += 1
                continue

            # Apply extraction to note
            try:
                apply_image_extract(note_path, extract)
                img_type = extract.get("image_type", "other")
                action = extract.get("suggested_action", "skip")
                type_counts[img_type] = type_counts.get(img_type, 0) + 1
                action_counts[action] = action_counts.get(action, 0) + 1
                desc = extract.get("description", "")[:50]
                print(f" → {img_type}: {desc}")
                stats["processed"] += 1
            except Exception as e:
                print(f" → write error: {e}")
                stats["errors"] += 1

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Summary
    print(f"\n=== Summary ===")
    print(f"Processed: {stats['processed']}")
    print(f"Errors:    {stats['errors']}")
    print(f"Skipped:   {stats['skipped']}")

    if type_counts:
        print(f"\nImage types:")
        for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {t:15s}  {n}")

    if action_counts:
        print(f"\nSuggested actions:")
        for a, n in sorted(action_counts.items(), key=lambda x: -x[1]):
            print(f"  {a:20s}  {n}")


if __name__ == "__main__":
    main()
