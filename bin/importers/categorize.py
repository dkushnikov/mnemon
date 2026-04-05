#!/usr/bin/env python3
"""
Dump vault categorizer — classifies items using a fast LLM.

Scans a directory of markdown files, batches them, sends to Claude (Haiku by
default) for classification, and updates each file's frontmatter with
category, domain, value (1-5), and reason.

Handles two input styles:
  1. Items with existing frontmatter (e.g. Safari exports from safari.py) —
     updates fields in place, marks status: classified.
  2. Raw markdown notes without frontmatter (e.g. Apple Notes export via
     Obsidian Importer) — adds a new frontmatter block, infers title from
     filename.

Personalization is driven by reader-context.md loaded from the target vault
(same file Mnemon uses for extraction). One source of reader context, two
consumers: extraction and triage.

Usage:
    categorize.py --dir ~/Obsidian/Dump/Safari --recursive \\
                  --reader-context ~/Obsidian/Knowledge/reader-context.md
    categorize.py --dir ~/Obsidian/Dump/Apple\\ Notes --recursive \\
                  --reader-context ~/Obsidian/Knowledge/reader-context.md
    categorize.py --dir ~/Obsidian/Dump --recursive --dry-run

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

CATEGORIZER_RULES = """You are a fast content categorizer for a personal knowledge library.

Below is the reader's profile. Use it to judge what matters to THEM.

=== READER PROFILE ===
{reader_context}
=== END READER PROFILE ===

For each item below, output ONE JSON object per line (JSONL) with these fields:
- id: the item ID as given
- category: one of [tech, ai, product, management, growth, health, learning, culture, tool, reference, personal, other]
- domain: short domain tag matching the reader's domain taxonomy (e.g. "mc/ai", "career", "learning", "health", "inner-work", "tools"). Use their exact domain names.
- value: integer 1-5 (5 = must-read for this reader, 4 = valuable, 3 = interesting, 2 = marginal, 1 = skip/noise)
- reason: one short phrase (max 10 words) explaining the rating from the reader's perspective

Be harsh. Most items are 1-3. A 4-5 must be genuinely valuable to THIS SPECIFIC READER, not generally. Memes, jokes, random listicles, dead links, SEO garbage → 1. Tool READMEs without clear relevance → 2. Generic articles → 2-3. Specific insights matching reader's domains and goals → 4-5.

Output ONLY JSONL. No prose, no markdown, no commentary. One line per item.

Items:
"""


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract frontmatter dict and body from markdown. Simple key: value parser."""
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
        # Strip surrounding quotes
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        fm[key.strip()] = val
    return fm, body


def serialize_frontmatter(fm: dict, body: str) -> str:
    """Render frontmatter back to markdown. Preserves order via dict insertion."""
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


def title_from_filename(path: Path) -> str:
    """Infer a title from filename, stripping date prefix if present.

    Examples:
        '2019_02_08 Ideas from Management 3.0.md' → 'Ideas from Management 3.0'
        'random-note.md' → 'random-note'
    """
    stem = path.stem
    # Strip common date prefixes: YYYY_MM_DD or YYYY-MM-DD
    m = re.match(r"^\d{4}[_-]\d{2}[_-]\d{2}\s+(.+)$", stem)
    return m.group(1) if m else stem


def needs_classification(fm: dict, include_unfronted: bool) -> bool:
    """Decide whether a file should be (re)classified."""
    status = fm.get("status", "")
    if not fm:
        return include_unfronted
    # Already classified → skip
    if status in ("classified", "skip", "keep"):
        return False
    # Explicit unclassified marker or missing status with dump-item type
    if status == "unclassified":
        return True
    # Has frontmatter but no value field and we're including unfronted
    if include_unfronted and "value" not in fm:
        return True
    return False


def collect_items(base: Path, recursive: bool, include_unfronted: bool, limit: int = 0) -> list[Path]:
    """Find markdown files that need classification."""
    pattern = "**/*.md" if recursive else "*.md"
    items = []
    for p in sorted(base.glob(pattern)):
        # Skip obvious meta files
        if p.name.lower() in ("readme.md", "index.md") or "import log" in p.name.lower():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if needs_classification(fm, include_unfronted):
            items.append(p)
            if limit and len(items) >= limit:
                break
    return items


def build_batch_input(files: list[Path]) -> tuple[str, dict[str, Path]]:
    """Build the prompt input and an id → path map."""
    id_to_path = {}
    lines = []
    for i, p in enumerate(files, 1):
        text = p.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        item_id = f"item-{i:04d}"
        id_to_path[item_id] = p

        title = fm.get("title") or title_from_filename(p)
        url = fm.get("url", "")

        # Extract preview — either from Safari-style frontmatter, or from body
        preview = ""
        preview_match = re.search(r"## Preview\s*\n\n(.+?)(?:\n\n|\Z)", body, re.DOTALL)
        if preview_match:
            preview = preview_match.group(1).strip()
        elif not fm:
            # Raw note — use first meaningful chunk of body as preview
            body_stripped = body.strip()
            preview = body_stripped[:400].replace("\n", " ").strip()

        item_line = f"ID: {item_id} | TITLE: {title[:150]}"
        if url:
            item_line += f" | URL: {url}"
        if preview:
            item_line += f" | PREVIEW: {preview[:300]}"
        lines.append(item_line)

    return "\n".join(lines), id_to_path


def load_reader_context(path: Path) -> str:
    """Load reader-context.md content. Strip comments and whitespace."""
    if not path.exists():
        raise FileNotFoundError(f"Reader context not found: {path}")
    text = path.read_text(encoding="utf-8")
    # Cap at ~8000 chars to keep prompt reasonable
    return text[:8000].strip()


def call_claude(prompt: str, model: str = "haiku") -> str:
    """Invoke claude CLI and return stdout."""
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "text", prompt],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude failed: {result.stderr}")
    return result.stdout


def parse_jsonl(output: str) -> list[dict]:
    """Extract JSON objects from output, one per line. Tolerates preamble."""
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


def apply_classification(path: Path, cls: dict) -> None:
    """Write classification back to file. Adds frontmatter if absent."""
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    # For raw notes (no frontmatter), add minimal structure
    if not fm:
        fm = {
            "type": "dump-item",
            "source": "apple-notes",
            "title": title_from_filename(path),
        }

    fm["category"] = cls.get("category", "other")
    fm["domain"] = cls.get("domain", "")
    fm["value"] = cls.get("value", 1)
    fm["reason"] = cls.get("reason", "")
    fm["status"] = "classified"
    path.write_text(serialize_frontmatter(fm, body), encoding="utf-8")


def process_batch(files: list[Path], reader_context: str, model: str, dry_run: bool) -> int:
    items_text, id_to_path = build_batch_input(files)
    prompt = CATEGORIZER_RULES.format(reader_context=reader_context) + items_text

    if dry_run:
        print("=== DRY RUN ===")
        print(f"Would classify {len(files)} items using model={model}")
        print("=== SAMPLE PROMPT (first 2500 chars) ===")
        print(prompt[:2500])
        return 0

    print(f"  → calling claude (model={model}, {len(files)} items)...", flush=True)
    output = call_claude(prompt, model=model)
    results = parse_jsonl(output)

    applied = 0
    for cls in results:
        item_id = cls.get("id")
        if item_id in id_to_path:
            try:
                apply_classification(id_to_path[item_id], cls)
                applied += 1
            except Exception as e:
                print(f"  ! failed to update {id_to_path[item_id].name}: {e}", file=sys.stderr)

    missing = len(files) - applied
    if missing > 0:
        print(f"  ! {missing} items not classified (model output incomplete)", file=sys.stderr)
    return applied


def main():
    parser = argparse.ArgumentParser(description="Categorize dump items with fast LLM using reader context")
    parser.add_argument("--dir", required=True, help="Directory of markdown files")
    parser.add_argument("--reader-context", required=True,
                        help="Path to reader-context.md (same file Mnemon uses for extraction)")
    parser.add_argument("--recursive", action="store_true", help="Recurse into subdirs")
    parser.add_argument("--include-unfronted", action="store_true", default=True,
                        help="Include files without frontmatter (raw notes). Default: True")
    parser.add_argument("--fronted-only", action="store_true",
                        help="Only classify files with existing frontmatter (skip raw notes)")
    parser.add_argument("--model", default="haiku", help="Claude model (default: haiku)")
    parser.add_argument("--batch-size", type=int, default=50, help="Items per LLM call (default: 50)")
    parser.add_argument("--limit", type=int, default=0, help="Total limit (0 = all)")
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
        print("Hint: run Mnemon setup.sh to create reader-context.md", file=sys.stderr)
        sys.exit(1)

    include_unfronted = not args.fronted_only
    items = collect_items(base, args.recursive, include_unfronted, args.limit)
    total = len(items)
    if total == 0:
        print("No items to classify.")
        return

    print(f"Found {total} items in {base}")
    print(f"Reader context: {reader_context_path}")
    print(f"Batch size: {args.batch_size}, model: {args.model}")

    total_applied = 0
    for start in range(0, total, args.batch_size):
        batch = items[start:start + args.batch_size]
        print(f"Batch {start // args.batch_size + 1}/{(total + args.batch_size - 1) // args.batch_size}: items {start + 1}-{start + len(batch)}")
        try:
            applied = process_batch(batch, reader_context, args.model, args.dry_run)
            total_applied += applied
        except Exception as e:
            print(f"  ! batch failed: {e}", file=sys.stderr)
            continue

    print(f"\nDone: {total_applied}/{total} classified")


if __name__ == "__main__":
    main()
