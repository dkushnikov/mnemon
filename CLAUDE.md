# Mnemon — Developer Guide

Open-source AI-powered personal knowledge extraction system.
Repo: `dkushnikov/mnemon`. License: MIT.

## Architecture

```
User → /source-add (skill) → knowledge-gateway.sh → claude -p → Sources/
                                    ↓                              ├── source.md (immutable)
                              mnemon.yaml (config)                 └── extract.md (AI-generated)
                              reader-context.md
                              templates/core/*.md
```

- **Gateway** (`bin/knowledge-gateway.sh`): core engine. Loads config, reads template + reader context, builds prompt, invokes `claude -p` in the vault directory.
- **Config** (`mnemon.yaml`): flat YAML with vault path, search provider, model, etc. Created by `setup.sh`, gitignored.
- **Templates** (`templates/core/`): extraction prompt templates per source type. Fabric-inspired format.
- **Vault template** (`vault-template/`): scaffold copied by `setup.sh` into user's vault.

The Claude Code plugin (skills `/source-add`, `/source-search`, `/source-status` plus commands) lives in a separate repo: [`dkushnikov/mnemon-plugin`](https://github.com/dkushnikov/mnemon-plugin). Mnemon is the tool; the plugin is the Claude Code integration layer on top of it.

## Key Conventions

- **source.md is immutable.** Never modify after creation.
- **extract.md is mutable.** Re-extraction overwrites it.
- **Folder naming:** `Sources/YYYY-MM-DD_{hash8}/` where hash8 = SHA-256 first 8 chars of canonical URL.
- **Templates produce structured output:** YAML frontmatter + Summary + Executive Summary + Key Ideas + Connections + Raw Quotes.
- **Reader context frames extraction:** Every extract is personalized through `reader-context.md`.

## Development

```bash
# Run tests
bash tests/test_config.sh
bash tests/test_gateway.sh
bash tests/test_setup.sh

# Dry-run gateway
./bin/knowledge-gateway.sh source-add --url https://example.com --config mnemon.yaml --dry-run

# Setup on test vault
./setup.sh /tmp/test-vault --non-interactive
```

## File Ownership

| Directory | Tracked | Notes |
|-----------|---------|-------|
| `bin/` | Yes | Core scripts |
| `templates/core/` | Yes | Official templates |
| `templates/community/` | Yes | User contributions |
| `vault-template/` | Yes | Scaffold for setup.sh |
| `mnemon.yaml` | **No** | User config (gitignored) |
| `docs/` | Yes | User-facing documentation |
| `tests/` | Yes | Test scripts |
