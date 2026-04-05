#!/usr/bin/env python3
"""
Dump item router — assigns destination to classified items.

After categorize.py assigns value (1-5), route.py decides WHERE each item
should go. Destinations are user-defined in a profiles markdown file (see
--profiles). Typical destinations: knowledge, work, personal, private, skip.

Two-stage routing:
  1. Auto-skip: items with value below threshold get `destination: skip`
     without any LLM call (cheap, deterministic). Requires a `skip`
     destination in the profiles.
  2. LLM routing: remaining items are batched and classified against the
     destination profiles. File path is passed as a hint (useful when items
     are already organized by folder, e.g. an Apple Notes export).

Output: adds `destination: <name>` field to each item's frontmatter.

Usage:
    route.py --dir /path/to/dump --recursive \\
             --profiles /path/to/route-profiles.md \\
             --min-value 4

The profiles file must use level-2 markdown headings for destination names:

    ## knowledge
    External references only — articles, books, tools...

    ## work
    Your work content — meetings, strategy, org design...

    ## skip
    Noise that should not be routed.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DESTINATION_HEADING_RE = re.compile(r"^##\s+([a-zA-Z][\w-]*)\s*$", re.MULTILINE)

ROUTER_RULES = """You are a routing classifier for a personal knowledge dump.
Your job: decide which destination each item belongs to.

Below are the destination profiles. Each profile describes what belongs in
that destination — read them carefully and match each item to the best fit.

{profiles}

=== ROUTING RULES ===

For each item, output ONE JSON object per line (JSONL) with:
- id: the item ID as given
- destination: one of [{destinations}]
- reason: one short phrase (max 10 words) explaining why

General principles:
- Pick the destination whose profile most specifically matches the item
- When in doubt between two destinations, prefer the one with more precise
  scope match rather than the more general one
- Use all available signals: folder path, title, URL, category, domain, preview
- Only use "skip" (if defined) for obvious noise — not for uncertainty
- When uncertain, pick the best available fit rather than skip

Output ONLY JSONL. No prose, no markdown, no commentary. One line per item.

Items:
"""


def extract_destinations(profiles_text: str) -> list[str]:
    """Extract destination names from profile markdown (## headings)."""
    return DESTINATION_HEADING_RE.findall(profiles_text)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract frontmatter dict and body from markdown."""
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
    """Render frontmatter back to markdown."""
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


def get_int(fm: dict, key: str) -> int:
    """Safely extract an int from frontmatter."""
    val = fm.get(key, "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def collect_items(base: Path, recursive: bool, reroute: bool = False) -> tuple[list[Path], list[Path]]:
    """Return (to_skip_auto, to_route_via_llm) based on value field.

    If reroute=True, include items that already have a destination (will be
    re-classified and destination will be overwritten).
    """
    pattern = "**/*.md" if recursive else "*.md"
    to_skip = []
    to_route = []
    for p in sorted(base.glob(pattern)):
        if p.name.lower() in ("readme.md", "index.md") or "import log" in p.name.lower() or "playbook" in p.name.lower():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm:
            continue
        if fm.get("status") != "classified":
            continue
        # Already routed — skip unless --reroute
        if fm.get("destination") and not reroute:
            continue
        value = get_int(fm, "value")
        if value < 1:
            continue  # unclassified, leave alone
        to_route.append(p)
    return to_skip, to_route


def auto_skip_low_value(files: list[Path], min_value: int) -> tuple[int, list[Path]]:
    """Set destination=skip for items with value < min_value. Return (skipped, remaining)."""
    skipped = 0
    remaining = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        value = get_int(fm, "value")
        if value < min_value:
            fm["destination"] = "skip"
            fm.setdefault("reason", "below min_value threshold")
            if "destination_reason" not in fm:
                fm["destination_reason"] = f"auto-skip: value {value} < {min_value}"
            p.write_text(serialize_frontmatter(fm, body), encoding="utf-8")
            skipped += 1
        else:
            remaining.append(p)
    return skipped, remaining


def build_batch_input(files: list[Path]) -> tuple[str, dict[str, Path]]:
    """Build LLM prompt input and id → path map. Includes folder path as hint."""
    id_to_path = {}
    lines = []
    for i, p in enumerate(files, 1):
        text = p.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        item_id = f"item-{i:04d}"
        id_to_path[item_id] = p

        title = fm.get("title") or p.stem
        url = fm.get("url", "")
        category = fm.get("category", "")
        domain = fm.get("domain", "")
        # Include relative folder path as hint
        try:
            # Get path relative to the Dump root (assumed 2+ levels up)
            rel_path = "/".join(p.parts[-3:-1]) if len(p.parts) > 2 else p.parts[-2]
        except Exception:
            rel_path = ""

        # Small preview from body
        body_stripped = body.strip()[:200].replace("\n", " ").strip()

        item_line = f"ID: {item_id} | FOLDER: {rel_path} | TITLE: {title[:120]}"
        if url:
            item_line += f" | URL: {url}"
        if category:
            item_line += f" | CAT: {category}"
        if domain:
            item_line += f" | DOMAIN: {domain}"
        if body_stripped:
            item_line += f" | PREVIEW: {body_stripped}"
        lines.append(item_line)

    return "\n".join(lines), id_to_path


def call_claude(prompt: str, model: str = "haiku") -> str:
    """Invoke claude CLI."""
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
    """Extract JSON objects from LLM output."""
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


def apply_routing(path: Path, routing: dict) -> None:
    """Write destination + reason to file's frontmatter."""
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    fm["destination"] = routing.get("destination", "skip")
    fm["destination_reason"] = routing.get("reason", "")
    path.write_text(serialize_frontmatter(fm, body), encoding="utf-8")


def process_batch(files: list[Path], profiles: str, destinations: list[str], model: str, dry_run: bool) -> int:
    items_text, id_to_path = build_batch_input(files)
    prompt = ROUTER_RULES.format(profiles=profiles, destinations=", ".join(destinations)) + items_text

    if dry_run:
        print(f"[DRY RUN] Would route {len(files)} items with model={model}")
        return 0

    print(f"  → calling claude (model={model}, {len(files)} items)...", flush=True)
    output = call_claude(prompt, model=model)
    results = parse_jsonl(output)

    applied = 0
    for routing in results:
        item_id = routing.get("id")
        if item_id in id_to_path:
            try:
                apply_routing(id_to_path[item_id], routing)
                applied += 1
            except Exception as e:
                print(f"  ! failed to update {id_to_path[item_id].name}: {e}", file=sys.stderr)

    missing = len(files) - applied
    if missing > 0:
        print(f"  ! {missing} items not routed (model output incomplete)", file=sys.stderr)
    return applied


def print_distribution(base: Path, recursive: bool, known_destinations: list[str]) -> None:
    """Print final distribution across destinations. Orders by known list, then extras."""
    from collections import Counter
    pattern = "**/*.md" if recursive else "*.md"
    dist = Counter()
    for p in base.glob(pattern):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        dest = fm.get("destination", "")
        if dest:
            dist[dest] += 1

    print("\n=== Routing Distribution ===")
    shown = set()
    for dest in known_destinations:
        print(f"  {dest:12s}  {dist.get(dest, 0):5d}")
        shown.add(dest)
    for dest, n in sorted(dist.items()):
        if dest not in shown:
            print(f"  {dest:12s}  {n:5d}  (not in profiles)")
    print(f"  TOTAL         {sum(dist.values()):5d}")


def main():
    parser = argparse.ArgumentParser(description="Route classified dump items to destination vaults")
    parser.add_argument("--dir", required=True, help="Dump directory root")
    parser.add_argument("--profiles", required=True, help="Path to vault routing profiles markdown")
    parser.add_argument("--recursive", action="store_true", default=True)
    parser.add_argument("--min-value", type=int, default=4,
                        help="Items with value below this get auto-skipped (default: 4)")
    parser.add_argument("--reroute", action="store_true",
                        help="Re-classify items that already have a destination (overwrites)")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = Path(args.dir).expanduser()
    if not base.exists():
        print(f"ERROR: directory not found: {base}", file=sys.stderr)
        sys.exit(1)

    profiles_path = Path(args.profiles).expanduser()
    if not profiles_path.exists():
        print(f"ERROR: profiles file not found: {profiles_path}", file=sys.stderr)
        sys.exit(1)
    profiles = profiles_path.read_text(encoding="utf-8").strip()

    destinations = extract_destinations(profiles)
    if not destinations:
        print(f"ERROR: no destination headings (## name) found in profiles: {profiles_path}", file=sys.stderr)
        print("Add sections like:\n  ## knowledge\n  ## work\n  ## skip", file=sys.stderr)
        sys.exit(1)
    print(f"Destinations from profiles: {', '.join(destinations)}")

    _, all_items = collect_items(base, args.recursive, reroute=args.reroute)
    if not all_items:
        print("No items to route (all already have destination or not classified).")
        print_distribution(base, args.recursive, destinations)
        return

    print(f"Found {len(all_items)} items needing routing")

    # Stage 1: auto-skip low value (only if "skip" is a valid destination)
    if "skip" in destinations:
        skipped, to_route = auto_skip_low_value(all_items, args.min_value)
        print(f"Auto-skipped {skipped} items with value < {args.min_value}")
    else:
        skipped, to_route = 0, all_items
        print("(no 'skip' destination in profiles — auto-skip disabled)")

    if args.limit:
        to_route = to_route[:args.limit]

    total = len(to_route)
    if total == 0:
        print("No high-value items to route.")
        print_distribution(base, args.recursive, destinations)
        return

    print(f"Routing {total} high-value items via LLM")
    print(f"Profiles: {profiles_path}")
    print(f"Batch size: {args.batch_size}, model: {args.model}")

    total_applied = 0
    num_batches = (total + args.batch_size - 1) // args.batch_size
    for start in range(0, total, args.batch_size):
        batch = to_route[start:start + args.batch_size]
        bnum = start // args.batch_size + 1
        print(f"Batch {bnum}/{num_batches}: items {start + 1}-{start + len(batch)}")
        try:
            applied = process_batch(batch, profiles, destinations, args.model, args.dry_run)
            total_applied += applied
        except Exception as e:
            print(f"  ! batch failed: {e}", file=sys.stderr)
            continue

    print(f"\nDone: {total_applied}/{total} routed via LLM, {skipped} auto-skipped")
    print_distribution(base, args.recursive, destinations)


if __name__ == "__main__":
    main()
