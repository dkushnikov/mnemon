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
| **Search** | `mnemon search`: grep-style token scan over extracts, ranked by rating; a qmd collection can be kept alongside for the qmd CLI (not used by `mnemon search` yet) |
| **Fabric-inspired** | Two-pass extraction, 16-word discipline, anti-sycophancy |

## How It Works

```
You → /source-add URL → mnemon add → knowledge-gateway.sh ─ acquire ─▶ Sources/<revision>/source.md
                                            │              └ extract ─▶ Sources/<revision>/extract.md
                          ~/.config/mnemon/instances/<name>.yaml   claude -p --restricted --tools "" … (structured fields only)
                          reader-context.md                        ~/.local/state/mnemon/vaults/<id>/ (receipts, lock)
                          templates/core/*.md
```

1. **Capture**: the dispatcher resolves the instance and hands the request to the gateway, which fetches the content itself — `curl`, then capture-text through `defuddle` (or `pandoc`); `pdftotext` for PDFs; `yt-dlp` / `whisper` for media; `--render` pre-renders SPAs, `--depth` folds thin landings with `defuddle` — and publishes `source.md` with that text byte for byte, before any model runs
2. **Extract**: the fenced extractor (`claude -p --restricted --tools "" --no-session-persistence --json-schema …`, prompt on stdin, no tools) applies the source type's template with your reader context and returns structured fields; the gateway validates them and publishes `extract.md`. The model never writes a file, and `RESULT:` lines and exit codes come from the files
3. **Store**: `source.md` + `extract.md` in `Sources/YYYY-MM-DD_{hash8}/` (`-2`, `-3`… for further revisions of a resource); a receipt per capture in the vault's ledger under `~/.local/state/mnemon/vaults/<id>/`, every write under the vault lock (one writer per vault). Automated feeders go through `mnemon ingest <dir>` ([inbox.md](inbox.md))
4. **Search**: `/source-search` (= `mnemon search`) scans your library — a grep-style token match over `extract.md` (optionally `source.md` and `Synthesis/`), ranked by rating then date. The JSON reports `provider: "grep"` (the method that ran) and `configured_provider` (the instance's `search_provider`); no qmd-backed search exists yet

## Requirements

- **Claude Code** 2.1.296+ (required) — any subscription (Pro, Max, API key); `mnemon fence-probe` once per version records that its `--restricted` fence holds (extraction refuses to run without the record)
- **git**, **bash**, **python3**, **jq**, **shasum** (required) — the engine
- **pyyaml**, **requests** (Python packages, optional) — depth-aware capture (`--depth auto`); without them it degrades to a plain capture
- **defuddle** (optional) — page-to-markdown conversion of URL captures (pandoc is the fallback), URL triage (`mnemon triage`) and depth probing
- **Poppler** (`pdftotext`, `pdfinfo`; optional) — PDF captures; without it a PDF is refused before anything is published
- **pandoc** (optional) — document conversion on import and the HTML fallback
- **yt-dlp** (optional) — for YouTube transcript extraction
- **ffmpeg** / **whisper** (optional) — for audio detection and transcription
- **QMD** (optional) — a hybrid semantic + keyword index for the qmd CLI; required by `mnemon doctor` only when `search_provider: qmd`

`mnemon doctor` checks all of them: a missing required dependency is a `fail`, a missing optional one a `warn` (`deps`, `deps-optional`, `deps-python`, `qmd`).

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
| `mnemon search --query <q> [--domain <d>] [--limit <n>] [--include-source] [--include-synthesis] [--human]` | Grep-style scan of the library; JSON on stdout (`provider: "grep"`, `configured_provider: <search_provider>`) |
| `mnemon status` | Library dashboard |
| `mnemon reindex` | Rebuild the vault's `index.md` from `Sources/` (not the qmd index) |
| `mnemon triage` | Content-aware URL triage |
| `mnemon audit` | Archive audit |
| `mnemon init <name> --vault <path> [--archive <dir>] [--collection <name>] [--search-provider grep\|qmd] [--reader-context <path> \| --reader-context-path <path>] [--charter <path>] [--writer-host <host>] [--default]` | Create an instance and scaffold its vault (never overwrites). `--reader-context` copies a profile into the vault, `--reader-context-path` references one in place; with neither, an existing `_meta/reader-context.md` or `reader-context.md` in the vault is used |
| `mnemon doctor [--json]` | Check dependencies and instance health |
| `mnemon config get <key> \| path \| list \| dump` | Inspect configuration |
| `mnemon release [--dry-run] [--now] [--drain-timeout <s>] [--rollback <ref>]` | Maintainers: lock → drain → fast-forward `main` → `release` → smoke → push; `--rollback` resets the engine under the same lock |
| `mnemon version` | Engine version, branch, commit and root |
| `mnemon ingest <inbox-dir> [--dry-run] [--only <id>] [--limit N] [--max-attempts N] [--retry <id>] [--json]` | Capture the `mnemon-envelope/1` files of a directory, one result record per envelope ([inbox.md](inbox.md)) |
| `mnemon fence-probe` | Probe the extractor fence of the installed Claude Code (five real runs, ≈ $0.20) and record a pass for its version — once per version |
| `mnemon scan \| monitor` | Reserved (program phase 4) |

## Configuration

**Instances.** One per vault: `~/.config/mnemon/instances/<name>.yaml` — flat YAML with `vault_path`, `reader_context_path`, `archive_dir`, `search_provider`, `qmd_collection`, `default_model` and the media settings — written by `mnemon init`; `~/.config/mnemon/default` names the default instance. The engine selects an instance in this order — an explicit selection always outranks an inherited one:

1. `--config <path>` (dispatcher or gateway flag)
2. `--instance <name>` (dispatcher or gateway flag)
3. `$MNEMON_CONFIG`
4. `$MNEMON_INSTANCE`
5. `./mnemon.yaml` in the working directory
6. `~/.config/mnemon/default`

Conflicts fail closed with exit 2: `--config` together with `--instance` on one command line; `$MNEMON_CONFIG` and `$MNEMON_INSTANCE` both set unless they agree (the same file, the file declares `instance: <name>`, or `MNEMON_INSTANCE=adhoc` next to a path). `MNEMON_INSTANCE=adhoc` on its own and an instance name without a file are errors too, never a silent fallback to the default. The dispatcher resolves once and exports the pair `MNEMON_CONFIG=<file>` + `MNEMON_INSTANCE=<name>` to the command, so a child that resolves again lands on the same file; `mnemon config dump` shows the pair and `MNEMON_CONFIG_SOURCE` (which selector won). Selectors are global options — `mnemon add --instance x` is refused, write `mnemon --instance x add`.

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

## The capture contract (phase 1)

Until 2026-09 the model was the copy path: it fetched URLs with WebFetch, wrote `source.md` and reported `RESULT:` lines in its own words — paraphrased, truncated and summarized sources shipped with `status=extracted`. The capture contracts of [`specs/2026-10-15-capture-contracts.md`](specs/2026-10-15-capture-contracts.md) replace that. What holds now:

- **The gateway acquires and publishes; the model only extracts.** `curl` fetches, `defuddle` / `pandoc` / `pdftotext` / `whisper` produce the capture-text, and `source.md` is published with that text byte for byte (`capture.text_sha256` in its frontmatter, the raw retained in `archive_dir` or the vault's state raw store) before the extractor runs. The extractor is `claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config --no-session-persistence --json-schema …` with the prompt on stdin: no tools, no file writes, no session transcript; it returns structured fields that the gateway validates and publishes as `extract.md`.
- **`RESULT:` lines and exit codes come from the files.** `status=extracted` means `source.md` and `extract.md` are in place; `captured` means the source is published and the extract is not (exit 4, `mnemon add --resume <revision>` finishes it); `failed` means nothing was published (exit 1); `integrity-error` means a published `source.md` is missing or was edited (exit 1, operator). Content above `extract_inline_max_bytes` (400,000 bytes) is captured and refused for extraction (`unsupported-size`) — there is no file-reading mode.
- **Captures are idempotent and resumable.** Every capture gets a receipt in the vault's ledger (`~/.local/state/mnemon/vaults/<id>/captures/`); a feeder replays with `--capture-id`, a resource already captured answers `skipped-duplicate`, a crash resumes from the receipt. Every write happens under the vault lock (one writer per vault); `mnemon doctor` shows the lock, the ledger and the fence.
- **Automated feeders use the inbox.** They never call the gateway: `mnemon ingest <dir>` consumes `mnemon-envelope/1` files with one result record per envelope — [inbox.md](inbox.md).
- **The fence is gated per Claude Code version.** `mnemon fence-probe` records that `--restricted` holds for the installed version; without a record extraction exits 3 before publication (exit 4 after). Run it after every Claude Code update.

## Project Structure

```
mnemon/
├── .claude-plugin/
│   └── marketplace.json       # marketplace "mnemon" → plugins/mnemon
├── bin/
│   ├── mnemon                 # dispatcher — the single entry point
│   ├── knowledge-gateway.sh   # capture + extraction behind `mnemon add` (lib-capture.sh: receipts, raw store, source.md)
│   ├── extract-run.py, extract-publish.py   # the fenced extractor run and the validating publisher of extract.md
│   ├── mnemon-ingest.py       # the inbox consumer behind `mnemon ingest`
│   ├── vault-lock.py, fence-probe.sh, capture-text.py
│   ├── mnemon-config.sh       # config loader
│   └── media-extract.py, kb-*.py, triage-urls.py, archive-audit.py, importers/
├── plugins/mnemon/            # Claude Code plugin: /source-add, /source-search, /source-status
├── templates/core/            # 7 extraction templates
├── protocols/                 # Schema and convention docs
├── vault-template/            # Scaffold copied into a new vault
├── docs/                      # Documentation (install.md, inbox.md, specs/)
├── tests/                     # test_*.sh
└── setup.sh                   # Deprecated one-shot installer — use `mnemon init`
```

## License

[MIT](../LICENSE)

## Author

Maintained by [@dkushnikov](https://github.com/dkushnikov).
