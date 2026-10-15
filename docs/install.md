# Installing Mnemon

Mnemon separates the **dev tree** (where you edit and run tests) from the **installed engine** (what Claude Code skills, shell sessions and scheduled jobs run). The engine is a checkout of the `release` branch; `main` is where development lands. Every user-facing entry point goes through the dispatcher `bin/mnemon` of the installed engine.

## Layout

| Piece | Path | What it is |
|---|---|---|
| Dev tree | `~/Code/mnemon` (any path) | clone of the repo on `main`: feature branches, tests |
| Installed engine | `$MNEMON_HOME`, default `~/.local/share/mnemon/engine` | checkout of `release` — the only Mnemon that sessions and jobs run |
| Dispatcher on PATH | `~/.local/bin/mnemon` → `$MNEMON_HOME/bin/mnemon` | symlink; `mnemon <command>` from any shell |
| Instances | `~/.config/mnemon/instances/<name>.yaml`, `~/.config/mnemon/default` | one instance per vault (vault path, archive dir, collection, model); `default` holds the name used when nothing else selects one |
| Plugin | `$MNEMON_HOME/plugins/mnemon`, marketplace `mnemon` | Claude Code skills `/source-add`, `/source-search`, `/source-status` |

The dispatcher takes its global options before the command: `mnemon [--instance <name>] [--config <path>] <command> [flags]`.

## Dependencies

| Dependency | Needed for | Install |
|---|---|---|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 2.1.296+ (`claude`) | the fenced extractor (`claude -p --restricted …`), the plugin | per its docs; log in once interactively, then `mnemon fence-probe` once per version |
| `git`, `bash`, `python3` | the engine | system / Homebrew |
| `jq`, `shasum` | gateway JSON handling, source hashes | `brew install jq` (shasum ships with macOS / perl) |
| Poppler (`pdftotext`, `pdfinfo`; optional) | PDF captures (refused before anything is published without it) | `brew install poppler` |
| `pandoc` 3+ (optional) | a converter candidate of URL captures (after defuddle, before the DOM text); document conversion on import | `brew install pandoc` |
| [`defuddle`](https://github.com/kepano/defuddle) 0.6+ (optional) | the first converter candidate of URL captures, URL triage (`mnemon triage`) and depth probing | `npm install -g defuddle` |
| Google Chrome or Chromium (optional) | the rendered stage of the fetch chain (JavaScript shells, bot walls, `--render`): found at its standard application path or as `google-chrome` / `chromium` on PATH; without it such pages fail as `environment` unless an external fetcher is allowed | per its site |
| `FIRECRAWL_API_KEY`, `JINA_API_KEY` (environment, optional) | the external fetchers — used only when the instance sets `external_fetch: auto` and only after the local chain failed on a bot wall, a 403 or a JavaScript shell ([capture.md](capture.md)); `mnemon doctor` reports whether each is set, never its value | export them in the shell profile and in a job's environment |
| Python package `pyyaml` (optional) | depth-aware capture (`--depth auto`, kb-depth-fold); `mnemon doctor` warns when missing. `requests` is no longer used — every request goes through the engine's own policy on the standard library | `pip3 install pyyaml` (or your preferred Python environment) |
| `yt-dlp` (optional) | YouTube transcripts | `brew install yt-dlp` |
| `ffmpeg` (optional) | audio probing / conversion | `brew install ffmpeg` |
| `whisper` (optional) | audio transcription | `pip3 install openai-whisper` |
| [`qmd`](https://github.com/tobi/qmd) (optional) | a hybrid semantic + keyword index of the vault for the qmd CLI; with `search_provider: qmd` the gateway refreshes it after each capture and `mnemon doctor` requires it. `mnemon search` itself is a grep-style scan and does not query qmd yet | `npm install -g @tobilu/qmd` (see the README's known issues) |

`mnemon doctor` reports what is missing (required → `fail`, optional → `warn`) and whether the selected instance is healthy — including the vault lock, the capture ledger and the fence record, the converter chain's tools with versions (`capture-deps`), the egress setting and keys (`egress`), the identity rules (`canonical`); `mnemon doctor --json` for machines.

**State on disk.** Besides the instance files, the engine keeps one ledger per physical vault under `${XDG_STATE_HOME:-~/.local/state}/mnemon/vaults/<id>/` (mode `0700`): capture receipts, attempt directories, the raw store when `archive_dir` is unset, the inbox consumer's state and the vault lock. One account on one host writes a vault (the lock is kernel-held); a second machine runs producers only. The fence probe record lives next to it in `…/mnemon/fence-probes.json`.

## Variant A — laptop: dev tree + installed engine

```bash
# dev tree
git clone https://github.com/dkushnikov/mnemon.git ~/Code/mnemon

# installed engine = a worktree of the release branch
# (git creates the local `release` branch from origin/release on the fly)
mkdir -p ~/.local/share/mnemon ~/.local/bin
git -C ~/Code/mnemon worktree add ~/.local/share/mnemon/engine release
ln -sf ~/.local/share/mnemon/engine/bin/mnemon ~/.local/bin/mnemon

# shell profile (~/.zshrc or ~/.bashrc), then open a new shell
export MNEMON_HOME="$HOME/.local/share/mnemon/engine"
export PATH="$HOME/.local/bin:$PATH"

# first instance, then a health check
mnemon init personal --vault ~/path/to/your/vault --archive ~/path/to/archive --default
mnemon doctor
mnemon fence-probe      # once per Claude Code version (five real claude runs, ≈ $0.20): records that the
                        # extractor fence holds — `mnemon add` refuses to extract without the record (exit 3)
mnemon version          # which engine answered (should be the release checkout)

# Claude Code plugin, from the engine directory
claude plugin marketplace add "$MNEMON_HOME"
claude plugin install mnemon@mnemon
```

Then build your reader profile: `cd <vault> && claude` and say "Follow mnemon-seed.md". If you already have a `reader-context.md`, pass `--reader-context <path>` (copied into the vault) or `--reader-context-path <path>` (referenced where it is) to `mnemon init` instead.

**An existing vault.** `mnemon init` never overwrites vault files. When the vault already holds `_meta/reader-context.md` or `reader-context.md`, `init` points `reader_context_path` at that file instead of placing the template (with neither reader flag given). `--search-provider grep|qmd` (default `grep`) declares the instance's search backend — `qmd` makes `mnemon add` refresh the qmd index after a capture and `mnemon doctor` require qmd (see Dependencies).

Why a worktree rather than a second clone: one object store, `git -C ~/Code/mnemon worktree list` shows exactly which commit the engine runs, and `mnemon release` can fast-forward it in place. A plain clone of `release` works just as well (that is Variant B).

**Update:**

```bash
git -C "$MNEMON_HOME" pull --ff-only
claude plugin marketplace update mnemon && claude plugin update mnemon@mnemon
mnemon fence-probe      # after every Claude Code update — the record is per exact version
```

## Variant B — headless node (no dev tree)

```bash
git clone --branch release https://github.com/dkushnikov/mnemon.git ~/.local/share/mnemon/engine
mkdir -p ~/.local/bin && ln -sf ~/.local/share/mnemon/engine/bin/mnemon ~/.local/bin/mnemon
# profile: MNEMON_HOME and PATH as in Variant A — and in the environment of any launchd/cron job, which does not read your profile

# dependencies (Homebrew / npm / pip)
brew install jq pandoc poppler
npm install -g defuddle
pip3 install pyyaml
# optional: brew install yt-dlp ffmpeg · pip3 install openai-whisper · npm install -g @tobilu/qmd
# optional: Google Chrome or Chromium for rendered pages; FIRECRAWL_API_KEY / JINA_API_KEY in the job's
#           environment only if the instance sets external_fetch: auto (docs/capture.md)

mnemon init personal --vault ~/path/to/vault --archive ~/path/to/archive --default
mnemon doctor
mnemon fence-probe
```

Nothing is scheduled by the install: the node only runs what you invoke — `mnemon add …`, `mnemon status`, `mnemon search …`, and `mnemon ingest <inbox-dir>` for the envelopes an automated producer (a bot, a scanner, an import script) drops into a directory ([`inbox.md`](inbox.md); run it from cron or launchd, one pass per invocation). `scan` and `monitor` are reserved for phase 4. Claude Code is still needed on the node for extraction (`claude -p --restricted …`) — install it, log in once interactively and run `mnemon fence-probe` before the first `mnemon add`. The plugin is optional here; add it only if you run Claude Code sessions on the node.

**Update:** `git -C ~/.local/share/mnemon/engine pull --ff-only`, and `mnemon fence-probe` after a Claude Code update.

## Variant C — a second instance on the same machine

One instance per vault. The second one gets its own vault, archive directory and search collection:

```bash
mnemon init work --vault ~/path/to/work-vault --archive ~/path/to/work-archive --collection obsidian-work
mnemon --instance work doctor
mnemon --instance work status
```

The engine selects an instance in this order — the first that applies wins, and an explicit flag always outranks a variable inherited from the shell or a session:

1. `--config <path>` — an explicit instance file (dispatcher or gateway flag)
2. `--instance <name>` — a name under `~/.config/mnemon/instances/` (flag)
3. `$MNEMON_CONFIG` — a file path, from the environment
4. `$MNEMON_INSTANCE` — an instance name, from the environment
5. `./mnemon.yaml` in the working directory
6. the name stored in `~/.config/mnemon/default`

`--config` and `--instance` on one command line exit 2 (`conflicting selectors`), and so do `$MNEMON_CONFIG` and `$MNEMON_INSTANCE` when they name different configs — a session that inherited `MNEMON_CONFIG` for one vault cannot be silently redirected by a stray `MNEMON_INSTANCE`; `mnemon --instance <name> …` or `mnemon --config <path> …` is the explicit way out. Put the selector before the command (`mnemon --instance work add …`, not `mnemon add --instance work`). `mnemon config dump` prints the resolved pair and `MNEMON_CONFIG_SOURCE`, the selector that won.

Bind a vault to its instance so Claude Code sessions started there never need `--instance`: put this into the vault's `.claude/settings.json`:

```json
{ "env": { "MNEMON_INSTANCE": "work" } }
```

Useful commands: `mnemon config list` (all instances), `mnemon config path` (the file in use), `mnemon config get vault_path`, `mnemon config dump`. `mnemon init <name> … --default` makes an instance the default; in a shell, `MNEMON_INSTANCE=work mnemon status` does the same for one command. Other `init` options: `--search-provider grep|qmd` (default `grep`), `--reader-context <path>` (copies an existing profile into the vault as `reader-context.md`), `--reader-context-path <path>` (points `reader_context_path` at a profile that stays where it is — nothing is copied; not together with `--reader-context`), `--charter <path>` and `--writer-host <host>` (see `mnemon init --help`).

**Legacy config.** A single `~/.config/mnemon/mnemon.yaml` keeps working with a warning. Move to instances with `mnemon init <name> --vault <path> --default` and remove the old file once `mnemon doctor` is clean. `setup.sh` (the previous one-shot installer) still performs a single-vault install but is deprecated in favour of `mnemon init`.

## Dev vs installed

**Sessions, skills and jobs run `$MNEMON_HOME`; the dev tree is for editing and tests.** The dev tree carries unmerged branches and half-finished changes; the engine follows `release`, which only moves through `mnemon release` once `main` is green. The plugin's skills hard-code this — every one of them calls `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"`. `mnemon version` tells you which checkout answered.

Working on the engine itself:

```bash
git -C ~/Code/mnemon switch -c feat/p1-<slug>         # branch from main
for t in ~/Code/mnemon/tests/test_*.sh; do bash "$t" || break; done
~/Code/mnemon/bin/mnemon --config /path/to/test-instance.yaml status   # run the dev tree explicitly, against a test instance
```

## Releasing

Branch model: `feat/p<N>-<slug>` → PR → `main` → `mnemon release`. Run it from the dev tree (`~/Code/mnemon/bin/mnemon release`, or `mnemon release` when `~/.local/bin/mnemon` points at a dev checkout) with `MNEMON_HOME` set to the installed engine worktree on branch `release`; it refuses to run from the installed engine itself.

```bash
mnemon release --dry-run                 # print every step with real paths and shas; nothing changes
mnemon release [--drain-timeout <s>]     # tree checks → lock → drain → ff-merge main into release → smoke → push origin release
mnemon release --rollback <ref>          # reset the engine worktree to <ref> under the same lock + drain
```

What a release does, in order:

0. **Tree checks, before any lock** — the dev tree has no uncommitted tracked changes, and `release` is an ancestor of `main` (otherwise `release has diverged from main`). Both are read-only and refuse at once: nothing is locked and no drain is waited for.
1. **Lock** — writes `${XDG_STATE_HOME:-~/.local/state}/mnemon/engine-release.lock` (pid, timestamp, target sha). While the lock is younger than 30 minutes every other engine command — `mnemon …`, the gateway, hence the skills and jobs — exits **75** (`engine is being updated, retry later`); `mnemon doctor` keeps working and reports `warn engine-lock`. The lock is removed last, also on failure. A stale lock (older than 30 minutes) is reported and ignored by other commands and taken over by the next release.
2. **Drain** — waits up to `--drain-timeout` seconds (default 120) for processes still running from `$MNEMON_HOME/bin/` (`pgrep -f`; same user and host only). On timeout the release fails without touching a file. `pgrep` exit 0 means busy and 1 means idle; any other exit means `pgrep` itself failed, and the release aborts (exit 1, lock removed) rather than read "could not look" as "idle".
3. **Fetch** — `git fetch origin` (a warning when offline; the merge reads local branches).
4. **Fast-forward** — `git -C "$MNEMON_HOME" merge --ff-only main`: the `release` branch and the engine worktree move together; `release` never carries commits that are not on `main`.
5. **Smoke on the updated worktree** — engine health only: `mnemon config path` resolves, `mnemon version` reports the new commit, `tests/test_config.sh` passes, and `mnemon doctor` exits 0, or exits 1 with no `fail` line other than `deps`, `qmd` or `writer` (a maintainer laptop that is a reader of the default instance, or lacks an optional tool, can still release). Any other doctor exit — a crash, a usage error, a missing binary — fails the smoke whatever the output says. A failing smoke never publishes: the release exits 1, nothing is pushed, and it prints the rollback — `mnemon release --rollback <previous sha>`, which is `git -C "$MNEMON_HOME" reset --hard <previous sha>` plus, only if `origin/release` had already moved, `git -C "$MNEMON_HOME" push --force-with-lease origin release`.
6. **Push** — `git -C "$MNEMON_HOME" push origin release`, only after a green smoke. A failed push (offline, no access) exits **3** with `released locally to <engine>, push failed: nodes will not see <sha>`: the local engine stays on the new commit and `origin/release` has not moved — push by hand before nodes pull.
7. `--now` prints `node ping not implemented (program phase 4)` — nodes still pull by hand or from a job: `git -C ~/.local/share/mnemon/engine pull --ff-only`.

`--rollback <ref>` skips the tree checks (a rollback must work from any dev tree), runs steps 1–2, then `git -C "$MNEMON_HOME" reset --hard <ref>` and prints (does not run) the `push --force-with-lease` that publishes the rollback.

**First release ever.** The worktree recipes above assume `origin` already has a `release` branch. Create it once from the dev tree: `git -C ~/Code/mnemon branch release main && git -C ~/Code/mnemon push -u origin release`, then add the worktree.
