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
URL / file / text → mnemon add → knowledge-gateway.sh ─ acquire ──▶ Sources/<revision>/source.md   (the captured text, byte for byte)
                                        │               └ extract ─▶ Sources/<revision>/extract.md  (validated model output)
                          ~/.config/mnemon/                 claude -p --restricted --tools "" … (no tools, structured fields only)
                            instances/<name>.yaml       archive_dir/ (originals, optional)
                          reader-context.md             ~/.local/state/mnemon/vaults/<id>/ (receipts, lock)
                          templates/core/
envelopes in a directory → mnemon ingest <dir> → the same gateway, one result record per envelope
```

- **Dispatcher** (`bin/mnemon`) — the single entry point: `mnemon add | ingest | search | status | init | doctor | fence-probe | …`. The Claude Code skills, your shell and any automation all call it from the installed engine (`$MNEMON_HOME`).
- **Gateway** (`bin/knowledge-gateway.sh`) — behind `mnemon add`: fetches the content itself through the capture layer (a direct fetch under one network policy → headless Chrome when the page needs rendering → an external fetcher only when the instance allows it; `defuddle` / `pandoc` / the DOM text, checked against the saved page; `pdftotext` for PDFs; `yt-dlp` / `whisper` for media), publishes `source.md` with that text byte for byte, then runs the fenced extractor — `claude -p --restricted --tools "" --no-session-persistence --json-schema …`, prompt on stdin, no tools — which returns structured fields that the gateway validates and publishes as `extract.md`. The model never writes a file. Every capture gets a receipt in the vault's ledger under `~/.local/state/mnemon/vaults/`, and every write happens under the vault lock (one writer per vault). Contracts: [`docs/specs/2026-10-15-capture-contracts.md`](docs/specs/2026-10-15-capture-contracts.md) (persistence, the fence, the inbox) and [`docs/specs/2026-10-15-capture-layer.md`](docs/specs/2026-10-15-capture-layer.md) (the capture layer); the operator's guide is [`docs/capture.md`](docs/capture.md).
- **Capture layer** — what every URL capture gets:
  - **Completeness-checked capture** — three converters are checked against the saved page; `capture.completeness: complete | partial | blocked | unknown` says what the conversion kept and `capture.acquisition` what the fetch got (a diagnostic, never a gate).
  - **Site capture** — `mnemon site map <url>` writes a reviewable plan; `mnemon add --url <landing> --site plan.json` captures the included pages into one hashed bundle with a disposition per page.
  - **arXiv full text** — `arxiv.org/abs|pdf|html/<id>[vN]` is one paper: the pinned version's HTML (else PDF) with authors and dates, never the abstract page.
  - **YouTube in the video's language** — captions chosen by the video's own language (manual before automatic, `en`/`ru` after it), whisper last; `transcript_language` and `original_language` recorded.
  - **Chunked extraction** — text above the inline limit is split at headings, extracted in parts and merged under one budget (`extract_max_usd`).
- **Inbox** — automated feeders (a bot, a scanner, an import script) drop one JSON envelope per item into a directory; `mnemon ingest <dir>` captures them through the same gateway with one result record per envelope — see [`docs/inbox.md`](docs/inbox.md).
- **Instances** — one per vault: `~/.config/mnemon/instances/<name>.yaml` holds the vault path, archive directory, search provider and model; `mnemon init` writes it. Several libraries (say, personal and work) coexist on one machine and are selected with `--instance <name>` or `MNEMON_INSTANCE`.
- **Library** (`archive_dir` in the instance config) — saves original source files before extraction. Any directory — iCloud, NAS, local folder. Omit to disable.
- **Research layers** — `Collections/` (a dossier: a question and the revisions that bear on it) and `Wiki/` (what the evidence says now: pages an agent builds with `mnemon research update` from a recorded package, in the same fence as the extractor, with locators into `source.md` and prints the doctor verifies; never by hand). The operations — build, propose, apply, delete, ask, synthesize, triage — are the protocol [`docs/research.md`](docs/research.md); the schemas and the derived states are [`docs/specs/2026-10-27-research-interface.md`](docs/specs/2026-10-27-research-interface.md).
- **Brain** (`Sources/` in vault) — `source.md` (immutable metadata) + `extract.md` (AI-generated, re-generable). Searchable via `mnemon search` (a grep-style token scan over extracts — see below), Obsidian, or the [QMD](https://github.com/tobi/qmd) CLI if you index the vault with it. A **bookmark** (`mnemon add --tier bookmark`) is the cheap tier: a deterministic card in `source.md`, no fetch, no model, no extract — `mnemon triage list | promote | dismiss` is its queue, and a later full `add` of the same URL promotes it ([`docs/capture.md`](docs/capture.md) §15).
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
- **In the shell:** `mnemon search --query "agent orchestration"` returns the same results as JSON — pipe it into whatever you like. The search is a grep-style scan (all query tokens must appear in a file; ranked by rating, then date). It does not use qmd: the JSON reports `provider: "grep"` (what ran) and `configured_provider` (the instance's `search_provider`, which today only drives the post-capture `qmd update && qmd embed` hook and `mnemon doctor`'s qmd check).
- **In Obsidian:** graph view connects sources to your notes. Wikilinks from projects to evidence.
- **On mobile:** Obsidian Sync gives you your full knowledge base offline.
- **For re-extraction:** models improve every few months. Re-run extraction from archived originals — your Library doesn't decay, your Brain gets sharper.

## Install

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 2.1.296 or later (Mnemon runs `claude -p --restricted` as a fenced extractor), `git`, `bash`, `python3`, `jq` and `shasum`. Optional: [`defuddle`](https://github.com/kepano/defuddle) 0.6+ and `pandoc` 3+ (the converter candidates of URL captures; the DOM text is the third), Google Chrome or Chromium (rendering JavaScript shells and bot walls), Poppler (`pdftotext` / `pdfinfo`, required for PDF captures), the Python package `pyyaml` (depth-aware capture), `yt-dlp`, `ffmpeg`, `whisper`, [QMD](https://github.com/tobi/qmd); a Firecrawl or Jina Reader key in the environment (`FIRECRAWL_API_KEY`, `JINA_API_KEY`) only if an instance opts into external fetching (`external_fetch: auto`). `mnemon doctor` checks all of them — required ones fail, optional ones warn — and reports the egress setting without ever printing a key.

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
mnemon fence-probe        # once per Claude Code version (five real claude runs, ≈ $0.20):
                          # records that the extractor fence holds; extraction refuses to run without it

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

`--reader-context` copies the file into the vault; `--reader-context-path <path>` points the instance at it where it is. A vault that already contains `_meta/reader-context.md` or `reader-context.md` is picked up without either flag. `--search-provider qmd` declares the [QMD](https://github.com/tobi/qmd) index for the instance (default `grep`).

Then from any Claude Code session:

```
/source-add https://example.com/article
/source-search "organizational design"
/source-status
/research-update --page "Trust without identity — four answers" --kind contradiction --collection "Trust without identity"
/research-ask "what does the evidence say about trust without identity?"
```

Or from the shell: `mnemon add --url https://example.com/article`, `mnemon search --query "organizational design"`, `mnemon status`. A vault that should always use its own instance sets `"env": {"MNEMON_INSTANCE": "<name>"}` in its `.claude/settings.json`; otherwise the default instance (`~/.config/mnemon/default`) is used.

### Update

```bash
git -C ~/.local/share/mnemon/engine pull --ff-only                              # engine (release branch)
claude plugin marketplace update mnemon && claude plugin update mnemon@mnemon   # plugin catalog
mnemon fence-probe                                                              # after a Claude Code update: the gate is per version
```

Maintainers publish with `mnemon release`: under an engine-wide maintenance lock (other engine commands exit 75 with `engine is being updated, retry later` for its duration) it waits for running engine processes to finish, fast-forwards `main` → `release` so the engine worktree follows, runs an engine-health smoke on the updated worktree, and only then tags the released commit `release/YYYY-MM-DD[-n]` and pushes `origin/release` and the tag together; a failing smoke publishes nothing and prints the `mnemon release --rollback <tag or sha>` command; `mnemon version` names the nearest release tag (see [`docs/install.md`](docs/install.md)).

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
- [`docs/capture.md`](docs/capture.md) — the capture layer: the fetch chain and egress, completeness and the `capture:` block, site capture, arXiv and YouTube, timeouts, chunked extraction, index regeneration
- [`docs/inbox.md`](docs/inbox.md) — the inbox: the envelope schema and `mnemon ingest`
- [`docs/specs/2026-10-15-capture-contracts.md`](docs/specs/2026-10-15-capture-contracts.md) — the capture contracts: persistence, the extractor fence, the inbox
- [`docs/specs/2026-10-15-capture-layer.md`](docs/specs/2026-10-15-capture-layer.md) — the capture layer: policy, chain, completeness, site capture, canonical identity, timeouts, chunked extraction, index regeneration
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

`qmd update` and `qmd embed` have to run for newly added sources to be searchable through the qmd CLI. Mnemon's gateway fires a background `qmd update && qmd embed` after every successful `mnemon add` when `search_provider: qmd` is active, so the index stays fresh during normal use. But cron jobs, manual `source.md` edits, or external tools that touch the vault without going through the gateway won't trigger reindexing. If qmd starts missing obvious content, run `qmd update && qmd embed` manually. (`mnemon reindex` rebuilds the vault's `index.md` from `Sources/`; it never touches the qmd index, and `mnemon search` does not read it.)

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
