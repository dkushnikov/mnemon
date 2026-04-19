# Mnemon

AI-assisted personal knowledge extraction. Ingest sources (articles, papers, YouTube, podcasts, ideas); an LLM produces structured extracts under a reader-context profile you control. Your vault stays in plain Markdown under your Obsidian vault — Mnemon is the engine, not a storage silo.

**Principle:** *AI does filing, human does understanding.* Extraction is mechanical; synthesis is not.

## Architecture at a glance

```
URL / file / text → knowledge-gateway.sh → claude -p → <your vault>/Sources/
                          ↓                                 ├── source.md   (immutable raw)
                    mnemon.yaml                              └── extract.md  (AI-generated, structured)
                    reader-context.md
                    templates/core/<type>.md
```

- **Gateway** (`bin/knowledge-gateway.sh`) — shell tool that loads config, assembles a prompt from the extraction template + your reader context, and invokes `claude -p` inside the vault directory. Pass `--render` for client-side-rendered SPAs (React/Vue landings, docs sites) — the gateway pre-renders via Chrome headless before handing content to the extractor.
- **Renderer** (`bin/render-url.sh`) — standalone helper that runs Chrome headless with `--dump-dom`, strips tags via a block-preserving Python extractor, and emits clean text. Callable directly or via the gateway's `--render` flag. Gracefully fails if Chrome/Chromium isn't installed.
- **Vault layout** — `Sources/YYYY-MM-DD_<hash8>/{source.md, extract.md}`. `source.md` is immutable once written; `extract.md` can be re-generated.
- **Templates** — Fabric-inspired extraction prompts per source type (article, video, podcast, paper, book, idea, conversation). Live in `templates/core/`.
- **Reader context** — a markdown profile of the reader that personalizes every extract's framing, key ideas, and ranking.

## Install

```bash
git clone https://github.com/dkushnikov/mnemon.git ~/Mnemon
~/Mnemon/setup.sh ~/path/to/your/vault
```

`setup.sh` scaffolds the vault, generates `mnemon.yaml`, and checks for optional dependencies (`yt-dlp`, `whisper`, `ffprobe`, [QMD](https://github.com/tobi/qmd)).

## Claude Code plugin

Mnemon itself is a tool; the Claude Code integration (slash commands, skills, prompts) lives in a **separate repo**: [`dkushnikov/mnemon-plugin`](https://github.com/dkushnikov/mnemon-plugin).

Install the plugin after `setup.sh`:

```bash
claude plugin marketplace add https://github.com/dkushnikov/mnemon-plugin
claude plugin install mnemon@mnemon-plugin
```

Then from any Claude Code session:

```
/source-add https://example.com/article
/source-search "organizational design"
/source-status
```

## Documentation

- [`docs/README.md`](docs/README.md) — full developer reference (architecture, templates, conventions)
- [`CLAUDE.md`](CLAUDE.md) — developer notes for working on Mnemon itself
- [`protocols/`](protocols/) — schema and convention docs

## Known issues

### `qmd query` crashes with `no such module: vec0` on semantic search

**Symptoms.** Running `qmd query`, `qmd vsearch`, or `qmd embed` against a Mnemon collection crashes with `SQLiteError: no such module: vec0` followed by a Bun segfault. `qmd search` (BM25) and `qmd status` still work.

**Root cause.** qmd 2.0.1's launcher script picks the Bun runtime whenever `$BUN_INSTALL` is set — which bun's own installer does automatically. When qmd is installed via `npm install -g @tobilu/qmd`, the pre-fix launcher still routes it to Bun. Bun's bundled SQLite can't load the sqlite-vec native extension, so any vector/hybrid query crashes. Fixed on [qmd `main`](https://github.com/tobi/qmd/blob/main/bin/qmd) but not yet in a published release as of 2026-04-05. See [tobi/qmd#363](https://github.com/tobi/qmd/issues/363).

**Automatic mitigation.** `setup.sh` detects the condition and installs a small wrapper at `~/.local/bin/qmd` that unsets `$BUN_INSTALL` for qmd invocations. The wrapper only takes effect if `~/.local/bin` appears earlier than `/opt/homebrew/bin` in your `$PATH` — if it doesn't, setup.sh prints a one-line instruction to fix your profile.

**Manual workaround** (if you hit this outside of setup.sh):

```sh
mkdir -p ~/.local/bin
cat > ~/.local/bin/qmd <<'EOF'
#!/bin/sh
BUN_INSTALL= exec /opt/homebrew/bin/qmd "$@"
EOF
chmod +x ~/.local/bin/qmd

# Make sure ~/.local/bin precedes /opt/homebrew/bin in $PATH
# e.g. add to ~/.zshrc: export PATH="$HOME/.local/bin:$PATH"
```

Remove the wrapper once qmd v2.0.2+ is published — the released launcher will detect the install method via lockfiles instead of `$BUN_INSTALL`.

### QMD index can go stale between sessions

`qmd update` and `qmd embed` have to run for newly added sources to be searchable via the semantic provider. Mnemon's gateway fires a background `qmd update && qmd embed` after every successful `source-add` when `search_provider: qmd` is active, so the index stays fresh during normal use. But cron jobs, manual `source.md` edits, or external tools that touch the vault without going through the gateway won't trigger reindexing. If semantic search starts missing obvious content, run `qmd update && qmd embed` manually.

## License

[MIT](LICENSE)
