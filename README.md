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
URL / file / text → mnemon add → knowledge-gateway.sh → claude -p → <your vault>/Sources/
                                        ↓                    ↓             ├── source.md   (metadata)
                          ~/.config/mnemon/              archive_dir/      └── extract.md  (AI extract)
                            instances/<name>.yaml         (originals,
                          reader-context.md                optional)
                          templates/core/
```

- **Dispatcher** (`bin/mnemon`) — the single entry point: `mnemon add | search | status | init | doctor | …`. The Claude Code skills, your shell and any automation all call it from the installed engine (`$MNEMON_HOME`).
- **Gateway** (`bin/knowledge-gateway.sh`) — behind `mnemon add`: captures a source, assembles a prompt from the extraction template + your reader context, and invokes `claude -p`. Handles articles, PDFs, YouTube, podcasts, ideas, SPAs. Pass `--render` for JS-heavy sites (Chrome headless pre-rendering).
- **Instances** — one per vault: `~/.config/mnemon/instances/<name>.yaml` holds the vault path, archive directory, search provider and model; `mnemon init` writes it. Several libraries (say, personal and work) coexist on one machine and are selected with `--instance <name>` or `MNEMON_INSTANCE`.
- **Library** (`archive_dir` in the instance config) — saves original source files before extraction. Any directory — iCloud, NAS, local folder. Omit to disable.
- **Brain** (`Sources/` in vault) — `source.md` (immutable metadata) + `extract.md` (AI-generated, re-generable). Searchable via `mnemon search`, Obsidian, or [QMD](https://github.com/tobi/qmd) hybrid search.
- **Reader context** — a markdown profile of you that personalizes every extract's framing, key ideas, and rating. The same article produces different outputs for different readers.
- **Templates** — Fabric-inspired extraction prompts per source type (article, video, podcast, paper, book, idea, conversation).

### Same article, two readers

An article about AI agents in customer support, extracted for two different reader profiles:

**CTO reader** → Executive Summary focuses on build-vs-buy, infra costs, team readiness. Key Ideas tagged `engineering`, `career`. Rating: 7 — "directly applicable to your Q3 platform decision."

**Product designer** → Executive Summary focuses on conversation UX, handoff patterns, trust signals. Key Ideas tagged `culture`, `learning`. Rating: 5 — "useful mental model, but no actionable design patterns yet."

Same source. Same extraction template. Different reader context → different knowledge.

### Using your knowledge

Extracts live in your Obsidian vault — searchable, linkable, always available:

- **In Claude Code sessions:** `/source-search "agent orchestration"` finds relevant extracts instantly. Your past reading informs current work.
- **In the shell:** `mnemon search --query "agent orchestration"` returns the same results as JSON — pipe it into whatever you like.
- **In Obsidian:** graph view connects sources to your notes. Wikilinks from projects to evidence.
- **On mobile:** Obsidian Sync gives you your full knowledge base offline.
- **For re-extraction:** models improve every few months. Re-run extraction from archived originals — your Library doesn't decay, your Brain gets sharper.

## Install

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Mnemon uses `claude` internally for extraction), `git`, `python3`, `jq` and [`defuddle`](https://github.com/kepano/defuddle). Optional: `yt-dlp`, `ffmpeg`, `whisper`, [QMD](https://github.com/tobi/qmd). `mnemon doctor` checks all of them.

Mnemon runs from an **installed engine** — a checkout of the `release` branch at `$MNEMON_HOME` (default `~/.local/share/mnemon/engine`) — while development happens in a separate clone (`~/Code/mnemon` by convention; any path works). Sessions, skills and jobs always call the installed engine, never the dev tree. The full recipes — laptop, headless node, a second instance — are in [`docs/install.md`](docs/install.md).

```bash
# 1. Clone the dev tree
git clone https://github.com/dkushnikov/mnemon.git ~/Code/mnemon

# 2. Install the engine as a worktree of the release branch; put the dispatcher on PATH
mkdir -p ~/.local/share/mnemon ~/.local/bin
git -C ~/Code/mnemon worktree add ~/.local/share/mnemon/engine release
ln -sf ~/.local/share/mnemon/engine/bin/mnemon ~/.local/bin/mnemon
# add to your shell profile (~/.zshrc or ~/.bashrc):
export MNEMON_HOME="$HOME/.local/share/mnemon/engine"
export PATH="$HOME/.local/bin:$PATH"

# 3. Create an instance for your vault and check the install
mnemon init personal --vault ~/path/to/your/vault --default
mnemon doctor

# 4. Install the Claude Code plugin from the engine directory
claude plugin marketplace add ~/.local/share/mnemon/engine
claude plugin install mnemon@mnemon
```

After that, build your reader profile (5-10 min conversation with Claude):

```bash
cd ~/path/to/your/vault && claude
# Then say: "Follow mnemon-seed.md"  (the profile builder — in your vault root after init, and at $MNEMON_HOME/mnemon-seed.md)
```

Claude detects existing identity artifacts (from [Obsidian Seed](https://github.com/dkushnikov/obsidian-seed) or your vault) and assembles your profile, or runs a short discovery if starting fresh. Skip this step for generic extracts — you can run it anytime later.

**Already have a reader-context.md?** Pass it to `init`:

```bash
mnemon init personal --vault ~/path/to/vault --reader-context ~/path/to/existing/reader-context.md --default
```

Then from any Claude Code session:

```
/source-add https://example.com/article
/source-search "organizational design"
/source-status
```

Or from the shell: `mnemon add --url https://example.com/article`, `mnemon search --query "organizational design"`, `mnemon status`. A vault that should always use its own instance sets `"env": {"MNEMON_INSTANCE": "<name>"}` in its `.claude/settings.json`; otherwise the default instance (`~/.config/mnemon/default`) is used.

### Update

```bash
git -C ~/.local/share/mnemon/engine pull --ff-only                              # engine (release branch)
claude plugin marketplace update mnemon && claude plugin update mnemon@mnemon   # plugin catalog
```

Maintainers publish with `mnemon release`: it fast-forwards `main` → `release`, the engine worktree follows, and a smoke check confirms the installed engine answers from the new commit (see [`docs/install.md`](docs/install.md)).

### Plugin moved in-tree

The Claude Code plugin now ships in this repository (`plugins/mnemon/`, marketplace `mnemon`); the separate `mnemon-plugin` repository is archived. If you installed from it, switch once:

```bash
claude plugin uninstall mnemon@mnemon-plugin && claude plugin marketplace remove mnemon-plugin
claude plugin marketplace add ~/.local/share/mnemon/engine && claude plugin install mnemon@mnemon
```

`setup.sh`, the previous one-shot installer, still works for a single vault but is deprecated in favour of `mnemon init`.

## Documentation

- [`docs/install.md`](docs/install.md) — install recipes: laptop (dev + installed engine), headless node, second instance; dev vs installed; releasing
- [`docs/README.md`](docs/README.md) — full reference (commands, configuration, templates, conventions)
- [`CLAUDE.md`](CLAUDE.md) — developer notes for working on Mnemon itself
- [`protocols/`](protocols/) — schema and convention docs

## Known issues

### `qmd query` crashes with `no such module: vec0` on semantic search

**Symptoms.** Running `qmd query`, `qmd vsearch`, or `qmd embed` against a Mnemon collection crashes with `SQLiteError: no such module: vec0` followed by a Bun segfault. `qmd search` (BM25) and `qmd status` still work.

**Root cause.** qmd 2.0.1's launcher script picks the Bun runtime whenever `$BUN_INSTALL` is set — which bun's own installer does automatically. When qmd is installed via `npm install -g @tobilu/qmd`, the pre-fix launcher still routes it to Bun. Bun's bundled SQLite can't load the sqlite-vec native extension, so any vector/hybrid query crashes. Fixed on [qmd `main`](https://github.com/tobi/qmd/blob/main/bin/qmd) but not yet in a published release as of 2026-04-05. See [tobi/qmd#363](https://github.com/tobi/qmd/issues/363).

**Automatic mitigation.** `setup.sh` (deprecated, still shipped) detects the condition and installs a small wrapper at `~/.local/bin/qmd` that unsets `$BUN_INSTALL` for qmd invocations. The wrapper only takes effect if `~/.local/bin` appears earlier than `/opt/homebrew/bin` in your `$PATH` — if it doesn't, setup.sh prints a one-line instruction to fix your profile.

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

`qmd update` and `qmd embed` have to run for newly added sources to be searchable via the semantic provider. Mnemon's gateway fires a background `qmd update && qmd embed` after every successful `mnemon add` when `search_provider: qmd` is active, so the index stays fresh during normal use. But cron jobs, manual `source.md` edits, or external tools that touch the vault without going through the gateway won't trigger reindexing. If semantic search starts missing obvious content, run `mnemon reindex` (or `qmd update && qmd embed`) manually.

## Works with Obsidian Seed

[Obsidian Seed](https://github.com/dkushnikov/obsidian-seed) builds a personal vault through a discovery conversation — your structure, your conventions, your `reader-context.md`. That reader context is exactly what Mnemon uses to personalize every extract.

**Seed builds the vault. Mnemon fills it with knowledge.**

Without Seed, Mnemon still works — you write `reader-context.md` yourself. With Seed, it's already there from your discovery session, tuned to how you think. Pass it when creating the instance:

```bash
mnemon init knowledge --vault ~/Obsidian/Knowledge --reader-context ~/Obsidian/Personal/reader-context.md --default
```

Starting fresh? Seed first (vault setup, 1-2 hours) → Mnemon next (ongoing capture). Already have a vault? Mnemon works standalone.

## License

[MIT](LICENSE)
