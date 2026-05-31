#!/usr/bin/env python3
"""
knowledge-reindex.py — rebuild a Mnemon vault's index.md from current Sources/.

Called from knowledge-gateway.sh as a post-extract hook (mirroring the qmd
update pattern) so index.md doesn't drift when extractions land via
non-interactive paths (`claude -p`, autonomous runs) that silently skip
/source-process step 4.

Behavior:
- Reads every Sources/*/extract.md, generates a reverse-chronological
  catalog entry per (folder, title, source_type, domains, rating, status).
- Preserves existing hand-edited entries (supersedes markers, "captured —
  awaiting content" annotations, etc.) by matching on folder name.
- Counts Sources / Extracted / Synthesis and updates the Status table.
- Lists all Synthesis/*.md notes.
- Updates frontmatter `updated:` to today.

Usage:
    knowledge-reindex.py [VAULT_PATH]

VAULT_PATH defaults to $MNEMON_VAULT_PATH then $PWD. The vault must have
Sources/ and (optionally) Synthesis/. Exits non-zero on missing vault.

Exit codes:
    0  rebuilt
    1  vault path missing / not a Mnemon vault
    2  unexpected error during rebuild
"""

import os
import re
import sys
import datetime
from pathlib import Path


def parse_field(text: str, key: str) -> str | None:
    """Parse 'key: value' from frontmatter. Strips surrounding quotes."""
    m = re.search(rf'^{key}:\s*(.*)$', text, re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    return v or None


def parse_list_field(text: str, key: str) -> list[str]:
    """Parse 'key: [a, b, c]' or 'key:\\n  - a\\n  - b' frontmatter lists."""
    m = re.search(rf'^{key}:\s*\[([^\]]*)\]', text, re.M)
    if m:
        return [x.strip().strip('"').strip("'") for x in m.group(1).split(',') if x.strip()]
    m = re.search(rf'^{key}:\s*\n((?:[ \t]+-[^\n]*\n)+)', text, re.M)
    if m:
        return [
            line.strip()[1:].strip().strip('"').strip("'")
            for line in m.group(1).splitlines() if line.strip().startswith('-')
        ]
    return []


def parse_existing_entries(index_text: str) -> dict[str, str]:
    """Return {folder_name: full_line} for every existing Recent Sources entry."""
    out = {}
    for line in index_text.splitlines():
        m = re.match(r'^- \[\[Sources/([\w-]+)/', line)
        if m:
            out[m.group(1)] = line
    return out


def generate_entry(folder: Path) -> str | None:
    """Generate a fresh index line from a folder's extract.md/source.md."""
    ex_path = folder / 'extract.md'
    if not ex_path.exists():
        return None
    ex = ex_path.read_text(encoding='utf-8', errors='ignore')
    src_path = folder / 'source.md'
    src = src_path.read_text(encoding='utf-8', errors='ignore') if src_path.exists() else ''

    title = parse_field(ex, 'title')
    if not title:
        m = re.search(r'^#\s+(.+)$', src, re.M)
        title = m.group(1).strip() if m else folder.name

    source_type = parse_field(ex, 'source_type') or parse_field(src, 'source_type') or '?'
    rating = parse_field(ex, 'rating')
    status = parse_field(ex, 'status')
    domains = parse_list_field(ex, 'domains')
    domains_str = '/'.join(domains) if domains else 'no-domain'

    parts = [source_type, domains_str]
    if rating and rating not in ('null', 'None'):
        parts.append(f'rating {rating}')
    if status and status not in ('extracted', None):
        parts.append(status)

    return f"- [[Sources/{folder.name}/extract|{title}]] ({', '.join(parts)})"


def reindex(vault: Path) -> tuple[int, int, int, int]:
    """Rebuild vault/index.md. Returns (sources, extracted, synthesis, no_extract)."""
    sources_dir = vault / 'Sources'
    synthesis_dir = vault / 'Synthesis'
    index_path = vault / 'index.md'

    if not sources_dir.is_dir():
        raise FileNotFoundError(f"No Sources/ directory in {vault}")

    all_source_dirs = sorted(d for d in sources_dir.iterdir() if d.is_dir())
    extracted_dirs = sorted(
        (d for d in all_source_dirs if (d / 'extract.md').exists()),
        key=lambda d: d.name,
        reverse=True,
    )
    no_extract = [d for d in all_source_dirs if not (d / 'extract.md').exists()]

    existing = parse_existing_entries(
        index_path.read_text(encoding='utf-8') if index_path.exists() else ''
    )

    entries = []
    for d in extracted_dirs:
        if d.name in existing:
            entries.append(existing[d.name])  # preserve hand-edits
        else:
            line = generate_entry(d)
            if line:
                entries.append(line)

    syn_files = sorted(
        [f for f in synthesis_dir.iterdir() if f.suffix == '.md']
        if synthesis_dir.is_dir() else [],
        key=lambda f: f.name,
    )
    syn_lines = []
    for f in syn_files:
        head = f.read_text(encoding='utf-8', errors='ignore')[:600]
        m = re.search(r'^#\s+(.+)$', head, re.M)
        title = m.group(1).strip() if m else f.stem
        syn_lines.append(f"- [[Synthesis/{f.stem}|{title}]]")

    sources_count = len(all_source_dirs)
    extracted_count = len(extracted_dirs)
    synthesis_count = len(syn_files)
    today = datetime.date.today().isoformat()

    pending_note = (
        f"_{len(no_extract)} source(s) raw-captured without extract.md "
        f"(run `/source-process` to extract)._"
        if no_extract else ""
    )

    out = f"""---
type: index
created: 2026-03-29
updated: {today}
---

# Knowledge Store

Universal reference storage. Sources captured and extracted by AI, synthesis written by human.

## Status

| Metric | Count |
|--------|-------|
| Sources | {sources_count} |
| Extracted | {extracted_count} |
| Synthesis | {synthesis_count} |

{pending_note}

## Recent Sources

{chr(10).join(entries)}

## Synthesis Notes

{chr(10).join(syn_lines)}

## Commands

- `/source-add <URL or title>` — capture a new source
- `/source-process` — extract unprocessed sources
- `/knowledge-status` — show pipeline status
"""

    index_path.write_text(out, encoding='utf-8')
    return sources_count, extracted_count, synthesis_count, len(no_extract)


def main(argv: list[str]) -> int:
    vault_arg = argv[1] if len(argv) > 1 else os.environ.get('MNEMON_VAULT_PATH', os.getcwd())
    vault = Path(vault_arg).expanduser().resolve()

    if not (vault / 'Sources').is_dir():
        print(f"knowledge-reindex: no Sources/ at {vault}", file=sys.stderr)
        return 1

    try:
        s, e, syn, pending = reindex(vault)
    except Exception as exc:
        print(f"knowledge-reindex: failed — {exc}", file=sys.stderr)
        return 2

    print(f"reindex: {vault.name} — Sources={s}, Extracted={e}, Synthesis={syn}"
          + (f", Pending={pending}" if pending else ""))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
