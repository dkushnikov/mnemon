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
# 1. Install the engine (release branch) and put the dispatcher on PATH — full recipes in install.md
git clone --branch release https://github.com/dkushnikov/mnemon.git ~/.local/share/mnemon/engine
mkdir -p ~/.local/bin && ln -sf ~/.local/share/mnemon/engine/bin/mnemon ~/.local/bin/mnemon
export MNEMON_HOME="$HOME/.local/share/mnemon/engine"     # put this (and ~/.local/bin on PATH) in your shell profile

# 2. Create an instance for your vault, check dependencies, install the Claude Code plugin
mnemon init personal --vault ~/path/to/your/vault --default
mnemon doctor
claude plugin marketplace add "$MNEMON_HOME" && claude plugin install mnemon@mnemon

# 3. Build your reader profile (personalizes extractions)
cd ~/path/to/your/vault && claude
# Then say: "Follow mnemon-seed.md"

# 4. Add your first source — from a Claude Code session or the shell
/source-add https://example.com/interesting-article
mnemon add --url https://example.com/interesting-article
```

Time from clone to first extract: **under 15 minutes.** See [install.md](install.md) for the dev-tree layout, a headless node, and running several instances.

## Features

| Feature | Description |
|---------|-------------|
| **7 source types** | Articles, YouTube, podcasts, books, papers, ideas, conversations |
| **Personal context** | Every extract framed by your role, domains, goals |
| **Structured output** | YAML frontmatter + Key Ideas + domain tags = searchable |
| **Immutable sources** | source.md never changes. Re-extract with new context anytime |
| **Local & yours** | Markdown files + git. No cloud, no lock-in |
| **Multiple instances** | One instance per vault — personal and work libraries side by side |
| **Pluggable search** | grep (zero deps) or QMD (semantic + keyword) |
| **Fabric-inspired** | Two-pass extraction, 16-word discipline, anti-sycophancy |

## How It Works

```
You → /source-add URL → mnemon add → knowledge-gateway.sh → claude -p → Sources/
                                            ↓                              ├── source.md
                          ~/.config/mnemon/instances/<name>.yaml           └── extract.md
                          reader-context.md
                          templates/core/*.md
```

1. **Capture**: the dispatcher resolves the instance and hands the request to the gateway, which fetches the content (defuddle / WebFetch for URLs, yt-dlp for YouTube, whisper for audio)
2. **Extract**: Claude applies a source-type-specific template with your reader context embedded
3. **Store**: Structured source.md + extract.md in `Sources/YYYY-MM-DD_{hash8}/`
4. **Search**: `/source-search` (= `mnemon search`) queries your library via grep or QMD

## Requirements

- **Claude Code** (required) — any subscription (Pro, Max, API key)
- **jq**, **defuddle**, **python3** with `pyyaml` and `requests` (required) — capture and config handling
- **yt-dlp** (optional) — for YouTube transcript extraction
- **ffmpeg** / **whisper** (optional) — for audio detection and transcription
- **QMD** (optional) — for hybrid semantic + keyword search

`mnemon doctor` checks all of them.

## Commands

In a Claude Code session (the plugin):

| Command | Description |
|---------|-------------|
| `/source-add <url>` | Capture and extract a source |
| `/source-search <query>` | Search your knowledge library |
| `/source-status` | Dashboard: totals, breakdowns, recent |

Each skill runs the installed engine's dispatcher. From the shell, the same and more — `mnemon [--instance <name>] [--config <path>] <command>`:

| Command | Description |
|---------|-------------|
| `mnemon add --url <url> \| --file <path>` (text on stdin) | Capture and extract a source. Flags: `--origin --title --author --source-type --context --intent --render --depth --dry-run` |
| `mnemon search --query <q> [--domain <d>] [--limit <n>] [--include-source] [--include-synthesis]` | Search the library; JSON on stdout |
| `mnemon status` | Library dashboard |
| `mnemon reindex` | Rebuild the search index |
| `mnemon triage` | Content-aware URL triage |
| `mnemon audit` | Archive audit |
| `mnemon init <name> --vault <path> [--archive <dir>] [--collection <name>] [--reader-context <path>] [--charter <path>] [--writer-host <host>] [--default]` | Create an instance |
| `mnemon doctor [--json]` | Check dependencies and instance health |
| `mnemon config get <key> \| path \| list \| dump` | Inspect configuration |
| `mnemon release [--dry-run] [--now]` | Maintainers: fast-forward `main` → `release` |
| `mnemon version` | Engine version |
| `mnemon ingest \| scan \| monitor` | Stubs until phase 4 |

## Configuration

**Instances.** One per vault: `~/.config/mnemon/instances/<name>.yaml` — flat YAML with `vault_path`, `reader_context_path`, `archive_dir`, `search_provider`, `qmd_collection`, `default_model` and the media settings — written by `mnemon init`; `~/.config/mnemon/default` names the default instance. The engine selects an instance in this order:

1. `--config <path>`
2. `$MNEMON_CONFIG`
3. `--instance <name>` / `$MNEMON_INSTANCE`
4. `./mnemon.yaml` in the working directory
5. `~/.config/mnemon/default`

Claude Code sessions select an instance by putting `"env": {"MNEMON_INSTANCE": "<name>"}` into the vault's `.claude/settings.json`. The legacy single file `~/.config/mnemon/mnemon.yaml` keeps working with a warning.

**`reader-context.md`** — Who you are, what domains you care about, your current goals. Lives in the vault (`reader_context_path`) and personalizes every extraction.

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

## Known gotchas (capture)

Learned while running Mnemon in production (2026-06 → 2026-09); the model must never be the copy path for content the gateway already holds verbatim.

- **WebFetch is unreliable for faithful capture.** A soft prompt returns a summary; a hard «reproduce verbatim» prompt can hit a copyright refusal. The `url` origin uses WebFetch internally and inherits both. For verbatim web or Telegram capture, fetch deterministically (Telegram: `https://t.me/<channel>/<id>?embed=1` HTML; a plain HTTP fetch or `defuddle` for pages) and pass the result as a text origin.
- **Verify a capture by reading the files, never by the exit code or `RESULT:status`.** `wc -c Sources/<folder>/source.md` and open it: a stub body such as `[Full transcript as provided…]` can ship with `status=extracted`. A batch once reported 6/7 passed while the key docx had failed and a YouTube source was gutted — both invisible at the status layer.
- **Repair a gutted `source.md` from the archive, not by re-running.** The gateway writes the full raw to `archive_dir` before `claude -p` runs; rebuild `source.md` as the frontmatter head plus the archived body. Re-running the gateway on the same input truncates again.
- **Large text and transcripts.** The spec [2026-09-08 extractor fence](specs/2026-09-08-extractor-fence.md) closes the truncation defect: for text-like origins the gateway writes `source.md` itself and the model writes only `extract.md`; oversized content is handed to the extractor as a file to read, not inlined in the prompt.

## Project Structure

```
mnemon/
├── .claude-plugin/
│   └── marketplace.json       # marketplace "mnemon" → plugins/mnemon
├── bin/
│   ├── mnemon                 # dispatcher — the single entry point
│   ├── knowledge-gateway.sh   # capture + extraction behind `mnemon add`
│   ├── mnemon-config.sh       # config loader
│   └── media-extract.py, kb-*.py, triage-urls.py, archive-audit.py, importers/
├── plugins/mnemon/            # Claude Code plugin: /source-add, /source-search, /source-status
├── templates/core/            # 7 extraction templates
├── protocols/                 # Schema and convention docs
├── vault-template/            # Scaffold copied into a new vault
├── docs/                      # Documentation (install.md, specs/)
├── tests/                     # test_*.sh
└── setup.sh                   # Deprecated one-shot installer — use `mnemon init`
```

## License

[MIT](../LICENSE)

## Author

Maintained by [@dkushnikov](https://github.com/dkushnikov).
