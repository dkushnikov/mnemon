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
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) | extraction (`claude -p`), the plugin | per its docs; log in once interactively |
| `git`, `bash`, `python3` | the engine | system / Homebrew |
| `jq` | gateway JSON handling | `brew install jq` |
| `pandoc` | document conversion on import | `brew install pandoc` |
| [`defuddle`](https://github.com/kepano/defuddle) | clean article capture | `npm install -g defuddle` |
| Python packages `pyyaml`, `requests` | config parsing, fetching | `pip3 install pyyaml requests` (or your preferred Python environment) |
| `yt-dlp` (optional) | YouTube transcripts | `brew install yt-dlp` |
| `ffmpeg` (optional) | audio probing / conversion | `brew install ffmpeg` |
| `whisper` (optional) | audio transcription | `pip3 install openai-whisper` |
| [`qmd`](https://github.com/tobi/qmd) (optional) | hybrid semantic + keyword search | `npm install -g @tobilu/qmd` (see the README's known issues) |

`mnemon doctor` reports what is missing and whether the selected instance is healthy; `mnemon doctor --json` for machines.

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
mnemon version          # which engine answered (should be the release checkout)

# Claude Code plugin, from the engine directory
claude plugin marketplace add "$MNEMON_HOME"
claude plugin install mnemon@mnemon
```

Then build your reader profile: `cd <vault> && claude` and say "Follow mnemon-seed.md". If you already have a `reader-context.md`, pass `--reader-context <path>` to `mnemon init` instead.

Why a worktree rather than a second clone: one object store, `git -C ~/Code/mnemon worktree list` shows exactly which commit the engine runs, and `mnemon release` can fast-forward it in place. A plain clone of `release` works just as well (that is Variant B).

**Update:**

```bash
git -C "$MNEMON_HOME" pull --ff-only
claude plugin marketplace update mnemon && claude plugin update mnemon@mnemon
```

## Variant B — headless node (no dev tree)

```bash
git clone --branch release https://github.com/dkushnikov/mnemon.git ~/.local/share/mnemon/engine
mkdir -p ~/.local/bin && ln -sf ~/.local/share/mnemon/engine/bin/mnemon ~/.local/bin/mnemon
# profile: MNEMON_HOME and PATH as in Variant A — and in the environment of any launchd/cron job, which does not read your profile

# dependencies (Homebrew / npm / pip)
brew install jq pandoc
npm install -g defuddle
pip3 install pyyaml requests
# optional: brew install yt-dlp ffmpeg · pip3 install openai-whisper · npm install -g @tobilu/qmd

mnemon init personal --vault ~/path/to/vault --archive ~/path/to/archive --default
mnemon doctor
```

Nothing is scheduled by the install: the node only runs what you invoke (`mnemon add …`, `mnemon status`, `mnemon search …`). `ingest`, `scan` and `monitor` are stubs until phase 4 wires the automated pipelines. Claude Code is still needed on the node for extraction (`claude -p`) — install it and log in once interactively before the first `mnemon add`. The plugin is optional here; add it only if you run Claude Code sessions on the node.

**Update:** `git -C ~/.local/share/mnemon/engine pull --ff-only`.

## Variant C — a second instance on the same machine

One instance per vault. The second one gets its own vault, archive directory and search collection:

```bash
mnemon init work --vault ~/path/to/work-vault --archive ~/path/to/work-archive --collection obsidian-work
mnemon --instance work doctor
mnemon --instance work status
```

The engine selects an instance in this order — the first that applies wins:

1. `--config <path>` — an explicit instance file
2. `$MNEMON_CONFIG` — same, from the environment
3. `--instance <name>` / `$MNEMON_INSTANCE` — a name under `~/.config/mnemon/instances/`
4. `./mnemon.yaml` in the working directory
5. the name stored in `~/.config/mnemon/default`

Bind a vault to its instance so Claude Code sessions started there never need `--instance`: put this into the vault's `.claude/settings.json`:

```json
{ "env": { "MNEMON_INSTANCE": "work" } }
```

Useful commands: `mnemon config list` (all instances), `mnemon config path` (the file in use), `mnemon config get vault_path`, `mnemon config dump`. `mnemon init <name> … --default` makes an instance the default; in a shell, `MNEMON_INSTANCE=work mnemon status` does the same for one command. Other `init` options: `--reader-context <path>` (reuse an existing profile), `--charter <path>` and `--writer-host <host>` (see `mnemon init --help`).

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

Branch model: `feat/p<N>-<slug>` → PR → `main` → `mnemon release`.

```bash
mnemon release --dry-run    # show what would move from main to release
mnemon release              # fast-forward main → release; the engine worktree at $MNEMON_HOME follows; smoke check
```

The release is a fast-forward only — `release` never carries commits that are not on `main`. After the fast-forward the engine worktree is updated (`pull --ff-only`) and a smoke check (`mnemon doctor`, `mnemon version`) confirms the installed engine answers from the new commit. For `--now`, see `mnemon release --help`. Headless nodes pick the release up with `git -C ~/.local/share/mnemon/engine pull --ff-only`, by hand or from a job.
