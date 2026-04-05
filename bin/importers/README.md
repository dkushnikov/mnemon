# Mnemon Importers

Tools for bulk-importing existing content into a Mnemon knowledge base.

The Mnemon import flow is a **three-stage pipeline**:

```
External sources (Safari, Apple Notes, Telegram, ...)
       ↓  importer scripts
Dump vault (Obsidian markdown)
       ↓  categorize.py (fast LLM + reader-context)
Classified dump (value 1-5, category, domain)
       ↓  triage (Obsidian Base dashboard)
       ↓  batch gateway → knowledge-gateway.sh --manifest
Knowledge vault (curated extracts)
```

**Principle:** "A dump is never processed in one session." Importers are fast,
cheap, and exhaustive. Triage and extraction are slow, human-curated, and
value-selective.

## Why a Dump vault?

Direct import into the Knowledge vault would pollute it with noise. The Dump
vault is a staging area: everything lands there first, gets classified, and
only curated items get promoted to Knowledge. Same Obsidian, different vault —
browse freely, decide what survives.

## Available Importers

### `safari.py` — Safari data sources

Exports from four Safari data stores:

- **Reading List** — `~/Library/Safari/Bookmarks.plist`
- **Bookmarks** — same plist, different branches
- **Open Tabs** — `~/Library/Containers/com.apple.Safari/.../SafariTabs.db`
- **Cloud Tabs** — `~/Library/Containers/com.apple.Safari/.../CloudTabs.db` (tabs from iPhone, iPad, other Macs via iCloud)

```bash
safari.py --out ~/Obsidian/Dump/Safari              # all sources
safari.py --out ~/Obsidian/Dump/Safari --tabs-only  # only open + cloud tabs
safari.py --out ~/Obsidian/Dump/Safari --limit 50   # test mode
```

Each item becomes a markdown file with frontmatter:

```yaml
---
type: dump-item
source: safari-reading-list
url: "https://..."
title: "..."
date_added: "2025-12-15"
status: unclassified
category:
domain:
value:
reason:
---
```

Idempotent: re-running picks up new items without duplicating existing ones
(filename is `<url-hash>-<slug>.md`).

### Apple Notes (via Obsidian Importer plugin)

Apple Notes doesn't need a custom script — use the official
[Obsidian Importer plugin](https://github.com/obsidianmd/obsidian-importer)
(supports Apple Notes natively). Import into your Dump vault's `Apple Notes/`
folder, then run `categorize.py` to classify.

**Known gotchas:**
- Attachments end up in vault root — move them to `_attachments/` manually or
  with `find ~/Obsidian/Dump -maxdepth 1 -type f ! -name "*.md" -exec mv {} _attachments/ \;`
- Password-protected notes are skipped (plugin cannot decrypt)
- Some notes fail with `ZIDENTIFIER` error (plugin bug with orphaned attachment rows)

### `categorize.py` — fast LLM categorizer

Scans a directory of dump items, batches them, and classifies each using a
fast LLM (Haiku by default) framed by `reader-context.md` — the same file
Mnemon uses for extraction.

```bash
categorize.py --dir ~/Obsidian/Dump/Safari --recursive \
              --reader-context ~/Obsidian/Knowledge/reader-context.md

categorize.py --dir ~/Obsidian/Dump/Apple\ Notes --recursive \
              --reader-context ~/Obsidian/Knowledge/reader-context.md
```

Works on two input styles:

1. **Items with frontmatter** (from `safari.py`) — updates classification
   fields in place, marks `status: classified`.
2. **Raw markdown notes** (from Obsidian Importer or similar) — adds
   frontmatter with classification, infers title from filename.

Each item gets:

- `category` — one of: tech, ai, product, management, growth, health, learning, culture, tool, reference, personal, other
- `domain` — matches reader's domain taxonomy (e.g. `mc/ai`, `career`, `learning`, `inner-work`)
- `value` — 1 (noise) to 5 (must-read for this specific reader)
- `reason` — one-phrase rationale

**Personalization comes from reader-context.md.** Same reader → same taxonomy
for extraction and triage. Different reader → different valuations.

**Cost:** Haiku is ~$0.005 per 50-item batch. 2,000 items ≈ $0.20.

## Triage with Obsidian Bases

After classification, use an Obsidian Base (`.base` file) with filters on the
`value` field to browse items by importance. Example view configuration:

```yaml
filters:
  and:
    - 'status == "classified"'
    - 'type == "dump-item"'

views:
  - type: table
    name: "Must Read (5)"
    filters:
      and:
        - 'value == 5'
    order:
      - title
      - source
      - domain
      - reason
    groupBy:
      property: domain
```

## Promotion to Knowledge vault

Selected items (typically `value >= 4` after human review) are promoted to the
Knowledge vault via the gateway's batch mode:

```bash
# Build a manifest of selected items
cat > /tmp/promote.json <<'EOF'
[
  {"origin": "url", "url": "https://...", "title": "..."},
  {"origin": "ref:vault", "ref_path": "/path/to/dump/note.md"}
]
EOF

# Batch import
~/Mnemon/bin/knowledge-gateway.sh source-add \
  --manifest /tmp/promote.json \
  --context personal \
  --import-source safari \
  --import-batch "2026-04-05-safari-triage"
```

Each item goes through full Mnemon extraction: source.md (immutable) +
extract.md (framed by reader-context).

## Planned importers

- **telegram.py** — Telegram Saved Messages from Desktop export JSON
- **youtube.py** — YouTube Watch Later / Liked from Google Takeout
- **apple-notes.py** — AppleScript-based recovery for notes that failed Obsidian Importer

Contributions welcome.
