# Mnemon

Your personal library with an AI reader.

You save articles, papers, podcasts. Most rot unread. URLs die. Your "read later" list is a graveyard. And when you do read something — the insights stay in your head, never making it into your knowledge system.

Mnemon fixes this: capture any source → AI extracts what matters *for you* → originals archived forever → knowledge lives in your Obsidian vault, searchable, linkable, yours.

**Library** — originals preserved (PDFs, audio, page snapshots, transcripts). Models improve → re-extract from the original. URL goes offline → you have the copy.

**Brain** — AI-generated extracts framed by your reader context. Same article, different reader → different insights. Your context is your filter.

Everything stays in plain Markdown in your Obsidian vault. Not a SaaS, not a lock-in — your files on your disk.

*Replaces: Readwise, Pocket, Instapaper, Safari Reading List, "save for later" bookmarks, manual note-taking from articles.*

**Principle:** *AI does filing, human does understanding.* Extraction is mechanical; synthesis is not.

## How it works

```
URL / file / text → knowledge-gateway.sh → claude -p → <your vault>/Sources/
                          ↓                    ↓             ├── source.md   (metadata)
                    mnemon.yaml          archive_dir/         └── extract.md  (AI extract)
                    reader-context.md    (originals,
                    templates/core/       optional)
```

- **Gateway** (`bin/knowledge-gateway.sh`) — captures a source, assembles a prompt from the extraction template + your reader context, and invokes `claude -p`. Handles articles, PDFs, YouTube, podcasts, ideas, SPAs. Pass `--render` for JS-heavy sites (Chrome headless pre-rendering).
- **Library** (`archive_dir` in config) — saves original source files before extraction. Any directory — iCloud, NAS, local folder. Omit to disable.
- **Brain** (`Sources/` in vault) — `source.md` (immutable metadata) + `extract.md` (AI-generated, re-generable). Searchable via Obsidian or [QMD](https://github.com/tobi/qmd) hybrid search.
- **Reader context** — a markdown profile of you that personalizes every extract's framing, key ideas, and rating. The same article produces different outputs for different readers.
- **Templates** — Fabric-inspired extraction prompts per source type (article, video, podcast, paper, book, idea, conversation).

## Install

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Mnemon uses `claude` internally for extraction).

```bash
git clone https://github.com/dkushnikov/mnemon.git ~/Mnemon
~/Mnemon/setup.sh ~/path/to/your/vault
```

After setup, build your reader profile (5-10 min conversation with Claude):

```bash
cd ~/path/to/your/vault && claude
# Then say: "Follow mnemon-seed.md"
```

Claude detects existing identity artifacts (from [Obsidian Seed](https://github.com/dkushnikov/obsidian-seed) or your vault) and assembles your profile, or runs a short discovery if starting fresh. Skip this step for generic extracts — you can run it anytime later.

**Already have a reader-context.md?** Pass it directly:

```bash
~/Mnemon/setup.sh ~/path/to/vault --reader-context ~/path/to/existing/reader-context.md
```

`setup.sh` scaffolds the vault, generates config, installs the [Claude Code plugin](https://github.com/dkushnikov/mnemon-plugin), and checks optional dependencies (`yt-dlp`, `whisper`, `ffprobe`, [QMD](https://github.com/tobi/qmd)).

Then from any Claude Code session:

```
/source-add https://example.com/article
/source-search "organizational design"
/source-status
```

Update plugin: `claude plugin marketplace update mnemon-plugin && claude plugin update mnemon@mnemon-plugin`

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

## Works with Obsidian Seed

[Obsidian Seed](https://github.com/dkushnikov/obsidian-seed) builds a personal vault through a discovery conversation — your structure, your conventions, your `reader-context.md`. That reader context is exactly what Mnemon uses to personalize every extract.

**Seed builds the vault. Mnemon fills it with knowledge.**

Without Seed, Mnemon still works — you write `reader-context.md` yourself. With Seed, it's already there from your discovery session, tuned to how you think. Pass it at setup:

```bash
~/Mnemon/setup.sh ~/Obsidian/Knowledge --reader-context ~/Obsidian/Personal/reader-context.md
```

Starting fresh? Seed first (vault setup, 1-2 hours) → Mnemon next (ongoing capture). Already have a vault? Mnemon works standalone.

## License

[MIT](LICENSE)
