#!/usr/bin/env python3
r"""
Light Extract — generate a readable index card for each dump item.

Sends the FULL body of each markdown file to Claude (Sonnet by default) and
enriches frontmatter with structured metadata: what is this note, who's
mentioned, what links are inside, and how rich is the content.

This is NOT a full Mnemon extraction (source.md + extract.md). It's a
lightweight triage pass that gives enough signal to route, prioritize, and
decide whether a note deserves full extraction or interactive absorption.

Usage:
    light_extract.py --dir ~/Obsidian/Dump/Apple\ Notes \\
                     --reader-context ~/Obsidian/Knowledge/reader-context.md \\
                     --recursive

    light_extract.py --dir ~/Obsidian/Dump/Apple\ Notes \\
                     --reader-context ~/Obsidian/Knowledge/reader-context.md \\
                     --limit 10 --dry-run

Requires: claude CLI (Claude Code).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

EXTRACT_PROMPT = """You are generating a lightweight index card for each note in a personal dump vault.

=== READER CONTEXT ===
{reader_context}
=== END READER CONTEXT ===

For each item below you receive: ID, FOLDER (original Apple Notes folder path — use as context hint), TITLE, and BODY (full text content of the note).

For each item, output ONE JSON object per line (JSONL) with these fields:

- id: the item ID as given
- extract_type: one of [meeting-notes, framework, link-dump, image-ref, todo, reflection, reference, snippet, conversation, presentation, document, list, empty]
- extract_summary: ONE sentence (max 25 words) describing what this note IS and what it's about. Be specific — "meeting notes from 1:1 with Mike about Q3 OKRs" not "meeting notes". Include the core topic.
- extract_people: array of people names mentioned (first name + last name if available, or first name only). Empty array if none.
- extract_links: array of URLs found in body (max 5 most important). Empty array if none.
- extract_content_quality: one of [rich, moderate, sparse, image-only, empty]
  - rich: substantial text with ideas, frameworks, or detailed notes (>500 chars of meaningful text)
  - moderate: some useful content but brief or incomplete (100-500 chars)
  - sparse: very little text, mostly stubs or fragments (<100 chars)
  - image-only: body is only image/attachment references with no real text
  - empty: no meaningful content at all
- extract_destination_hint: one of [mc, personal, private, knowledge, skip] — your best guess where this note belongs, given the reader context and folder path. This is a HINT, not a decision.
- extract_value: integer 1-5, re-assessed based on FULL content (not just title). Same scale: 5=must-read for this reader, 1=noise.

IMPORTANT:
- Read the FULL body, not just the first line
- For notes with only image references ([[name.jpeg]]), set extract_type=image-ref, quality=image-only, and describe what you can infer from the filename
- For notes with URLs, extract the most important ones (max 5)
- People names: extract real names, not handles or emails
- The FOLDER path is a strong signal: "Work/Hiring" → probably mc, "Personal/Finance" → probably personal or private
- Be honest about content quality — many old notes are stubs or fragments

Output ONLY JSONL. No prose, no markdown, no commentary. One JSON object per line.

Items:
"""


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
                items_str = ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in val)
                lines.append(f"{key}: [{items_str}]")
        elif any(c in str(val) for c in ":\"'#\n"):
            escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.lstrip("\n")


def title_from_filename(path: Path) -> str:
    stem = path.stem
    m = re.match(r"^\d{4}[_-]\d{2}[_-]\d{2}\s+(.+)$", stem)
    return m.group(1) if m else stem


def needs_extraction(fm: dict) -> bool:
    """Check if this item needs light extraction."""
    if fm.get("extract_type"):
        return False  # already extracted
    # Note: we intentionally do NOT skip status=routed items — light extract
    # is a secondary enrichment pass that runs on already-routed items.
    return True


def is_image_only(body: str) -> bool:
    """Quick check if body is only image/attachment references."""
    clean = re.sub(r"!?\[\[[^\]]+\]\]", "", body)  # remove wikilinks
    clean = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", clean)  # remove markdown images
    clean = clean.strip()
    return len(clean) < 10


def collect_items(base: Path, recursive: bool, include_images: bool, limit: int = 0) -> list[Path]:
    pattern = "**/*.md" if recursive else "*.md"
    items = []
    for p in sorted(base.glob(pattern)):
        if p.name.lower() in ("readme.md", "index.md") or "import log" in p.name.lower():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, body = parse_frontmatter(text)
        if not needs_extraction(fm):
            continue
        if not include_images and is_image_only(body):
            continue
        items.append(p)
        if limit and len(items) >= limit:
            break
    return items


def get_folder_path(file_path: Path, base: Path) -> str:
    """Get the Apple Notes folder path relative to base."""
    try:
        rel = file_path.relative_to(base)
        parts = rel.parts[:-1]  # exclude filename
        return "/".join(parts) if parts else "(root)"
    except ValueError:
        return "(unknown)"


def build_batch_input(files: list[Path], base: Path) -> tuple[str, dict[str, Path]]:
    id_to_path = {}
    lines = []
    for i, p in enumerate(files, 1):
        text = p.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        item_id = f"item-{i:04d}"
        id_to_path[item_id] = p

        title = fm.get("title") or title_from_filename(p)
        folder = get_folder_path(p, base)

        # Cap body to avoid token overflow — 4000 chars should capture most notes
        body_trimmed = body.strip()[:4000]

        item_block = f"ID: {item_id}\nFOLDER: {folder}\nTITLE: {title}\nBODY:\n{body_trimmed}\n---END ITEM---"
        lines.append(item_block)

    return "\n\n".join(lines), id_to_path


def load_reader_context(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Reader context not found: {path}")
    return path.read_text(encoding="utf-8")[:6000].strip()


def call_claude(prompt: str, model: str = "sonnet") -> str:
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "text", prompt],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude failed: {result.stderr[:500]}")
    return result.stdout


def parse_jsonl(output: str) -> list[dict]:
    results = []
    for line in output.split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return results


def apply_extraction(path: Path, ext: dict) -> None:
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    # Add extraction fields
    fm["extract_type"] = ext.get("extract_type", "")
    fm["extract_summary"] = ext.get("extract_summary", "")
    fm["extract_people"] = ext.get("extract_people", [])
    fm["extract_links"] = ext.get("extract_links", [])
    fm["extract_content_quality"] = ext.get("extract_content_quality", "")
    fm["extract_destination_hint"] = ext.get("extract_destination_hint", "")

    # Update value if re-assessed
    new_value = ext.get("extract_value")
    if new_value is not None:
        fm["value_original"] = fm.get("value", "")
        fm["value"] = new_value

    path.write_text(serialize_frontmatter(fm, body), encoding="utf-8")


def process_batch(files: list[Path], base: Path, reader_context: str,
                  model: str, dry_run: bool) -> int:
    items_text, id_to_path = build_batch_input(files, base)
    prompt = EXTRACT_PROMPT.format(reader_context=reader_context) + items_text

    if dry_run:
        print(f"[DRY RUN] Would extract {len(files)} items with model={model}")
        print(f"[DRY RUN] Prompt size: {len(prompt)} chars")
        return 0

    print(f"  → calling claude (model={model}, {len(files)} items)...", flush=True)
    output = call_claude(prompt, model=model)
    results = parse_jsonl(output)

    applied = 0
    for ext in results:
        item_id = ext.get("id")
        if item_id in id_to_path:
            try:
                apply_extraction(id_to_path[item_id], ext)
                applied += 1
            except Exception as e:
                print(f"  ! failed to update {id_to_path[item_id].name}: {e}",
                      file=sys.stderr)

    missing = len(files) - applied
    if missing > 0:
        print(f"  ! {missing} items not extracted (model output incomplete)",
              file=sys.stderr)
    return applied


def main():
    parser = argparse.ArgumentParser(
        description="Generate lightweight index cards for dump items")
    parser.add_argument("--dir", required=True, help="Directory of dump items")
    parser.add_argument("--reader-context", required=True,
                        help="Path to reader-context.md")
    parser.add_argument("--recursive", action="store_true", default=True)
    parser.add_argument("--include-images", action="store_true",
                        help="Include image-only items (default: skip them)")
    parser.add_argument("--model", default="sonnet",
                        help="Claude model (default: sonnet)")
    parser.add_argument("--batch-size", type=int, default=15,
                        help="Items per LLM call (default: 15, lower than "
                             "categorize because full body is sent)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Total limit (0 = all)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = Path(args.dir).expanduser()
    if not base.exists():
        print(f"ERROR: directory not found: {base}", file=sys.stderr)
        sys.exit(1)

    reader_context_path = Path(args.reader_context).expanduser()
    try:
        reader_context = load_reader_context(reader_context_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    items = collect_items(base, args.recursive, args.include_images, args.limit)
    total = len(items)
    if total == 0:
        print("No items to extract (all already have extract_type or are image-only).")
        return

    print(f"Found {total} items in {base}")
    print(f"Reader context: {reader_context_path}")
    print(f"Model: {args.model}, batch size: {args.batch_size}")
    if not args.include_images:
        print(f"(image-only items skipped — use --include-images to process them)")

    total_applied = 0
    num_batches = (total + args.batch_size - 1) // args.batch_size
    for start in range(0, total, args.batch_size):
        batch = items[start:start + args.batch_size]
        bnum = start // args.batch_size + 1
        print(f"Batch {bnum}/{num_batches}: items {start + 1}-{start + len(batch)}")
        try:
            applied = process_batch(batch, base, reader_context, args.model,
                                    args.dry_run)
            total_applied += applied
        except Exception as e:
            print(f"  ! batch failed: {e}", file=sys.stderr)
            continue

    print(f"\nDone: {total_applied}/{total} extracted")


if __name__ == "__main__":
    main()
