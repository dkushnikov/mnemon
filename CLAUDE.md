# Mnemon — Developer Guide

Open-source AI-powered personal knowledge extraction system.
Repo: `dkushnikov/mnemon`. License: MIT. Public repository — see "Public repo hygiene" below.

## Architecture

```
User → /source-add (plugins/mnemon skill) → mnemon (bin/mnemon, dispatcher) → knowledge-gateway.sh ─ acquire ─▶ Sources/<revision>/source.md (immutable)
                                                       ↓                              │              └ extract ─▶ Sources/<revision>/extract.md (validated model output)
                                             instance config                  bin/lib-capture.sh      claude -p --restricted --tools "" … (bin/extract-run.py)
                                  ~/.config/mnemon/instances/<name>.yaml      receipts + lock under ~/.local/state/mnemon/vaults/<id>/
producer → <inbox>/<envelope_id>.json → mnemon ingest <dir> (bin/mnemon-ingest.py) → the same gateway, one result record per envelope
```

- **Dispatcher** (`bin/mnemon`): the single entry point — `mnemon [--instance <name>] [--config <path>] <command>`. Resolves the instance, then runs `add` (= gateway `source-add`, same flags, stdin for text), `ingest <inbox-dir>` (the inbox consumer, `docs/inbox.md`), `fence-probe` (records the extractor fence for the installed Claude Code version), `status`, `search` (JSON on stdout), `reindex`, `triage`, `audit`, `init`, `doctor`, `config`, `release`, `version`; `scan|monitor` are reserved for phase 4.
- **Gateway** (`bin/knowledge-gateway.sh` + `bin/lib-capture.sh`): core engine behind `mnemon add`, the capture contracts of `docs/specs/2026-10-15-capture-contracts.md`. Acquires the content itself — `bin/capture-fetch.py` under the network policy of `bin/capture/` (direct → rendered → external; the acquisition path never runs `curl` — `bin/lib-archive.sh`, the backfill tool for pre-contract folders, is the one remaining `curl` user, outside acquisition) → `bin/capture-text.py` with defuddle / pandoc / the DOM text, checked against the saved page; `pdftotext`; `media-extract.py` — publishes `source.md` by one directory rename before any model runs, then runs the fenced extractor through `bin/extract-run.py` (`claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config --no-session-persistence --json-schema …`, prompt on stdin, in a per-attempt work directory) and publishes the validated structured output as `extract.md` through `bin/extract-publish.py`. Receipts, attempt dirs and the raw store live in the per-vault ledger (`MNEMON_VAULT_STATE_DIR`); every write happens under the vault lock (`bin/vault-lock.py`, `flock` on an inherited descriptor); `RESULT:` lines and exit codes come from the files.
- **Instances** (`~/.config/mnemon/instances/<name>.yaml` + `~/.config/mnemon/default`): flat YAML per vault (vault path, archive dir, search provider, model), written by `mnemon init`. Selection order — explicit beats inherited: `--config` → `--instance` → `$MNEMON_CONFIG` → `$MNEMON_INSTANCE` → `./mnemon.yaml` → `default`. `--config` with `--instance` on one command line, or `$MNEMON_CONFIG` and `$MNEMON_INSTANCE` naming different configs, exit 2 (`conflicting selectors`); the dispatcher resolves once and exports the pair `MNEMON_CONFIG` + `MNEMON_INSTANCE` to the command, so children never re-decide. Legacy `~/.config/mnemon/mnemon.yaml` keeps working with a warning.
- **Templates** (`templates/core/`): extraction prompt templates per source type. Fabric-inspired format.
- **Vault template** (`vault-template/`): scaffold copied into a new vault.
- **Plugin** (`plugins/mnemon/`, manifest `.claude-plugin/marketplace.json`, marketplace `mnemon`): the Claude Code integration layer — skills `/source-add`, `/source-search`, `/source-status`. Skills carry no logic: each runs `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" <command>` and formats the result. Formerly the separate `mnemon-plugin` repository, now archived.

Mnemon is the tool; the plugin is a thin door into it.

## Key Conventions

- **source.md is immutable.** Never modify after creation; its body equals the capture-text artifact byte for byte (`capture.text_sha256`), and the gateway reports an edited or missing one as `integrity-error`.
- **extract.md is mutable.** Re-extraction overwrites it.
- **Folder naming:** `Sources/YYYY-MM-DD_{hash8}[-n]/` where hash8 = the first 8 hex characters of sha256(resource key) — a folder label, never the identity (two resources may share it); the identity is `resource_sha256` in `source.md`, and `-2`, `-3`… name further revisions.
- **Templates produce structured output:** the model returns the fields of the `extract/1` schema (title, author, language, tags, people, domains, rating, body with the template's `### ` headings — Summary, Executive Summary, Key Ideas, Connections, Raw Quotes); the publisher writes the YAML frontmatter.
- **Reader context frames extraction:** Every extract is personalized through `reader-context.md`.
- **One path form for the engine.** Skills and docs call `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"` — never a dev checkout, never a script bundled with the plugin.

## Development

```bash
# Run every suite — one table (passed / failed / not run), exit 1 on any failure; --both adds
# the legacy toolchain (the system bash 3.2 + python 3.9, PATH=/usr/bin:/bin first), -k keeps
# going after a failing suite. The suites never touch your Claude Code plugin registry:
# tests/test-helper.sh exports MNEMON_SKIP_PLUGIN=1 and setup.sh skips its plugin step under
# it; the two tests of that step (tests/test_setup.sh) run it in a temp HOME — one with no
# `claude` on PATH, one against a logging stub `claude`
tests/run-suites.sh --both

# Dry-run a capture through the dispatcher against an explicit instance file
./bin/mnemon --config /path/to/instance.yaml add --url https://example.com --dry-run

# Legacy installer on a test vault (deprecated, kept working). --skip-plugin or
# MNEMON_SKIP_PLUGIN=1 keeps your plugin registry untouched; setup.sh also skips
# the plugin step when CI or CLAUDE_PLUGIN_ROOT is set
./setup.sh /tmp/test-vault --non-interactive --skip-qmd --skip-plugin
```

### Branch model

`main` is the integration branch; the installed engine follows `release`.

1. Branch from `main`: `feat/p<N>-<slug>` (phase N of the program), or `fix/…`, `docs/…`.
2. Open a PR, review, merge into `main`.
3. `mnemon release` (from the dev tree) takes the engine maintenance lock — other engine commands exit 75 meanwhile — drains running engine processes, fast-forwards `main` → `release` so the engine worktree at `$MNEMON_HOME` follows, smokes the updated worktree (engine health only), and only when the smoke is green tags the released commit `release/YYYY-MM-DD[-n]` (annotated; `-2`, `-3`… on the same day) and pushes `origin/release` and the tag together (`--atomic`); `mnemon version` reports the nearest release tag; `mnemon release --rollback <ref>` — a sha or a release tag — is the way back.

Never commit to `release` directly; never force-push `main`. `release` moves only through `mnemon release` (a fast-forward of `main`) and `mnemon release --rollback <ref>`, which prints — never runs — the `git push --force-with-lease origin release` that publishes a rollback. Release tags are written by `mnemon release` only (a failed smoke leaves none) and never deleted. Conventional commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`) — `git log --oneline` is the audit trail.

### Dev vs installed

- **Dev tree** (`~/Code/mnemon`, any path): edit, test, branch.
- **Installed engine** (`$MNEMON_HOME`, default `~/.local/share/mnemon/engine`): worktree or clone of `release`; `~/.local/bin/mnemon` symlinks to its `bin/mnemon`.

**Sessions, skills and jobs run `$MNEMON_HOME`, never the dev tree.** `mnemon version` shows which checkout answered. To exercise dev-tree code by hand, call `<dev-tree>/bin/mnemon --config <test-instance.yaml> …` explicitly against a test instance. Full recipes: [`docs/install.md`](docs/install.md).

### Public repo hygiene

No private facts in any tracked file: no home-directory paths that contain a user name, no hostnames, no people, no references to private notes. Write `~/Code/mnemon`, `$MNEMON_HOME`, `~/.local/share/mnemon/engine`, "the reader", "the maintainer".

## File Ownership

| Directory | Tracked | Notes |
|-----------|---------|-------|
| `bin/` | Yes | Core scripts; `bin/mnemon` is the dispatcher |
| `plugins/mnemon/` | Yes | Claude Code plugin (skills) |
| `.claude-plugin/` | Yes | Marketplace manifest (`mnemon`) |
| `templates/core/` | Yes | Official templates |
| `templates/community/` | Yes | User contributions |
| `vault-template/` | Yes | Scaffold for `mnemon init` / `setup.sh` |
| `docs/` | Yes | User-facing documentation (`install.md`, `README.md`, `specs/`) |
| `tests/` | Yes | Test scripts (`test_*.sh`) |
| `mnemon.yaml` | **No** | Legacy single config (gitignored); instances live in `~/.config/mnemon/` |
