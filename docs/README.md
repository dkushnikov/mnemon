# Mnemon

> *From Greek μνήμων (mnemon) — "mindful, remembering."*

**AI-powered personal knowledge extraction system.** Feed it articles, videos, podcasts, books — get structured, searchable knowledge artifacts framed by your personal context.

Fabric extracts wisdom. **Mnemon remembers it.**

## What It Does

You read an article. Mnemon creates:
- **source.md** — immutable captured content (what you read)
- **extract.md** — structured knowledge (what it means for YOU)
  - Summary, Executive Summary (personalized), Key Ideas (domain-tagged), Connections, Raw Quotes

Every extract is framed by YOUR context — your role, domains, goals. Two people read the same article, get different extracts.

## Quick Start

```bash
# 1. Clone and setup (scaffolds the vault, generates config, installs the Claude Code plugin)
git clone https://github.com/dkushnikov/mnemon.git ~/Mnemon
cd ~/Mnemon
./setup.sh ~/path/to/your/vault

# 2. Build your reader profile (personalizes extractions; setup.sh placed mnemon-seed.md in your vault)
cd ~/path/to/your/vault && claude
# Then say: "Follow mnemon-seed.md"

# 3. Add your first source
/source-add https://example.com/interesting-article
```

Time from clone to first extract: **under 15 minutes.**

## Features

| Feature | Description |
|---------|-------------|
| **7 source types** | Articles, YouTube, podcasts, books, papers, ideas, conversations |
| **Personal context** | Every extract framed by your role, domains, goals |
| **Structured output** | YAML frontmatter + Key Ideas + domain tags = searchable |
| **Immutable sources** | source.md never changes. Re-extract with new context anytime |
| **Local & yours** | Markdown files + git. No cloud, no lock-in |
| **Pluggable search** | grep (zero deps) or QMD (semantic + keyword) |
| **Fabric-inspired** | Two-pass extraction, 16-word discipline, anti-sycophancy |

## How It Works

```
You → /source-add URL → knowledge-gateway.sh → claude -p → Sources/
                              ↓                              ├── source.md
                        mnemon.yaml                          └── extract.md
                        reader-context.md
                        templates/core/*.md
```

1. **Capture**: Gateway fetches content (WebFetch for URLs, yt-dlp for YouTube, whisper for audio)
2. **Extract**: Claude applies a source-type-specific template with your reader context embedded
3. **Store**: Structured source.md + extract.md in `Sources/YYYY-MM-DD_{hash8}/`
4. **Search**: `/source-search` queries your library via grep or QMD

## Requirements

- **Claude Code** (required) — any subscription (Pro, Max, API key)
- **yt-dlp** (optional) — for YouTube transcript extraction
- **whisper** (optional) — for audio transcription
- **QMD** (optional) — for hybrid semantic + keyword search

## Commands

| Command | Description |
|---------|-------------|
| `/source-add <url>` | Capture and extract a source |
| `/source-search <query>` | Search your knowledge library |
| `/source-status` | Dashboard: totals, breakdowns, recent |

## Configuration

After `setup.sh`, edit two files:

**`reader-context.md`** — Who you are, what domains you care about, your current goals. This personalizes every extraction.

**`mnemon.yaml`** — Vault path, search provider, model, whisper settings. Usually no changes needed after setup.

## Templates

Mnemon ships 7 extraction templates in `templates/core/`:

| Template | For | Special Features |
|----------|-----|-----------------|
| `article.md` | Web articles, blog posts | Two-pass IDEAS→INSIGHTS extraction |
| `video.md` | YouTube videos | Transcript-based, format-value note |
| `podcast.md` | Podcasts, audio content | Speaker attribution, signal-to-noise |
| `book.md` | Books (notes/highlights) | Central Thesis, framework extraction |
| `paper.md` | Academic papers | Methodology Note, evidence grading |
| `idea.md` | Quick thoughts, insights | Lightweight, connection-focused |
| `conversation.md` | Meetings, discussions | Decisions, Action Items sections |

All templates follow the Fabric-inspired format: IDENTITY → STEPS → OUTPUT INSTRUCTIONS → INPUT.

## Project Structure

```
mnemon/
├── setup.sh              # One-command installation
├── bin/                   # Core scripts
│   ├── knowledge-gateway.sh
│   └── media-extract.py
├── templates/core/       # 7 extraction templates
├── protocols/            # Schema and convention docs
├── vault-template/       # Copied into vault by setup.sh
└── docs/                 # Documentation
```

> **Note:** Claude Code skills and commands (`/source-add`, `/source-search`, `/source-status`) live in a separate repo: [`dkushnikov/mnemon-plugin`](https://github.com/dkushnikov/mnemon-plugin). Mnemon is the tool; the plugin is the Claude Code integration layer on top of it.

## License

[MIT](../LICENSE)

## Author

Dima Kushnikov — [@dkushnikov](https://github.com/dkushnikov)
