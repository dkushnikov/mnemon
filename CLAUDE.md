# Mnemon — Developer Guide

Open-source AI-powered personal knowledge extraction system.
Repo: `dkushnikov/mnemon`. License: MIT. Public repository — see "Public repo hygiene" below.

## Architecture

```
User → /source-add (plugins/mnemon skill) → mnemon (bin/mnemon, dispatcher) → knowledge-gateway.sh → claude -p → Sources/
                                                       ↓                              ↓                     ├── source.md (immutable)
                                             instance config                    reader-context.md           └── extract.md (AI-generated)
                                  ~/.config/mnemon/instances/<name>.yaml        templates/core/*.md
```

- **Dispatcher** (`bin/mnemon`): the single entry point — `mnemon [--instance <name>] [--config <path>] <command>`. Resolves the instance, then runs `add` (= gateway `source-add`, same flags, stdin for text), `status`, `search` (JSON on stdout), `reindex`, `triage`, `audit`, `init`, `doctor`, `config`, `release`, `version`; `ingest|scan|monitor` are stubs until phase 4.
- **Gateway** (`bin/knowledge-gateway.sh`): core engine behind `mnemon add`. Loads the instance config, reads template + reader context, builds the prompt, invokes `claude -p` in the vault directory.
- **Instances** (`~/.config/mnemon/instances/<name>.yaml` + `~/.config/mnemon/default`): flat YAML per vault (vault path, archive dir, search provider, model), written by `mnemon init`. Selection order — explicit beats inherited: `--config` → `--instance` → `$MNEMON_CONFIG` → `$MNEMON_INSTANCE` → `./mnemon.yaml` → `default`. `--config` with `--instance` on one command line, or `$MNEMON_CONFIG` and `$MNEMON_INSTANCE` naming different configs, exit 2 (`conflicting selectors`); the dispatcher resolves once and exports the pair `MNEMON_CONFIG` + `MNEMON_INSTANCE` to the command, so children never re-decide. Legacy `~/.config/mnemon/mnemon.yaml` keeps working with a warning.
- **Templates** (`templates/core/`): extraction prompt templates per source type. Fabric-inspired format.
- **Vault template** (`vault-template/`): scaffold copied into a new vault.
- **Plugin** (`plugins/mnemon/`, manifest `.claude-plugin/marketplace.json`, marketplace `mnemon`): the Claude Code integration layer — skills `/source-add`, `/source-search`, `/source-status`. Skills carry no logic: each runs `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" <command>` and formats the result. Formerly the separate `mnemon-plugin` repository, now archived.

Mnemon is the tool; the plugin is a thin door into it.

## Key Conventions

- **source.md is immutable.** Never modify after creation.
- **extract.md is mutable.** Re-extraction overwrites it.
- **Folder naming:** `Sources/YYYY-MM-DD_{hash8}/` where hash8 = SHA-256 first 8 chars of canonical URL.
- **Templates produce structured output:** YAML frontmatter + Summary + Executive Summary + Key Ideas + Connections + Raw Quotes.
- **Reader context frames extraction:** Every extract is personalized through `reader-context.md`.
- **One path form for the engine.** Skills and docs call `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"` — never a dev checkout, never a script bundled with the plugin.

## Development

```bash
# Run all tests
for t in tests/test_*.sh; do bash "$t" || break; done

# Dry-run a capture through the dispatcher against an explicit instance file
./bin/mnemon --config /path/to/instance.yaml add --url https://example.com --dry-run

# Legacy installer on a test vault (deprecated, kept working; --skip-plugin keeps your plugin registry untouched)
./setup.sh /tmp/test-vault --non-interactive --skip-qmd --skip-plugin
```

### Branch model

`main` is the integration branch; the installed engine follows `release`.

1. Branch from `main`: `feat/p<N>-<slug>` (phase N of the program), or `fix/…`, `docs/…`.
2. Open a PR, review, merge into `main`.
3. `mnemon release` (from the dev tree) takes the engine maintenance lock — other engine commands exit 75 meanwhile — drains running engine processes, fast-forwards `main` → `release` so the engine worktree at `$MNEMON_HOME` follows, smokes the updated worktree (engine health only) and pushes `origin/release` only when the smoke is green; `mnemon release --rollback <ref>` is the way back.

Never commit to `release` directly; never force-push either branch. Conventional commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`) — `git log --oneline` is the audit trail.

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
