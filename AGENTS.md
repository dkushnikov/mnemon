# Mnemon — notes for coding agents

This is a pointer, not a second guide. The full developer guide is [`CLAUDE.md`](CLAUDE.md); read it first, then the document this page routes you to.

## Invariants (never trade these away)

- **`source.md` is immutable.** Its body is the capture-text byte for byte (`capture.text_sha256`); the engine reports an edited or missing one as `integrity-error`. A re-capture is a new revision folder, never an edit.
- **The engine acquires and publishes; the model only extracts.** The gateway fetches the content, publishes `source.md`, then runs the extractor — Claude Code in a fence (`claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config --no-session-persistence --json-schema …`, prompt on stdin, no tools) — and publishes its validated structured output as `extract.md`. `RESULT:` lines and exit codes come from the files, never from the model's words.
- **A session is a caller, not the extractor.** A Claude Code session, a skill, a script or an agent calls `mnemon add …` and reads its `RESULT:` lines; it never fetches on the engine's behalf, never writes into `Sources/`, never writes `extract.md`.
- **One writer per vault, one engine path.** Writes happen under the vault lock; everything user-facing goes through the installed engine's dispatcher `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"` — never a dev checkout, never a script bundled with the plugin.
- **Public repository.** No private facts in any tracked file: no home paths with a user name, no hostnames, no people, no references to private notes (`tests/test_hygiene.sh` enforces it).

## Where things are decided

| Task | Read |
|---|---|
| How a capture works, flags, completeness, timeouts, site and arXiv captures, chunked extraction | [`docs/capture.md`](docs/capture.md) |
| Feeding sources from a bot, a scanner or a script (envelopes, `mnemon ingest`) | [`docs/inbox.md`](docs/inbox.md) |
| Persistence (receipts, identity, `RESULT:`, exit codes) and the extractor fence | [`docs/specs/2026-10-15-capture-contracts.md`](docs/specs/2026-10-15-capture-contracts.md) |
| The capture layer (fetch chain, converters, the network policy) | [`docs/specs/2026-10-15-capture-layer.md`](docs/specs/2026-10-15-capture-layer.md) |
| What a vault is as a data store — entities, ownership, states, the data doctor | [`docs/specs/2026-10-16-vault-store.md`](docs/specs/2026-10-16-vault-store.md) |
| The research layers — `Collections/` (schema `collection/1`, the `## Items` grammar, the reader records), `Wiki/` (schema `wiki/1`: prints, evidential body, locators, derived states, the veto predicate), the extended `filed`, the index sections, the search JSON `mnemon-search/2` with its wiki block, the doctor's `research` document `mnemon-doctor-research/1`, the compatibility fixtures every branch must pass | [`docs/specs/2026-10-27-research-interface.md`](docs/specs/2026-10-27-research-interface.md) |
| Installing, dev tree vs installed engine, instances, releasing | [`docs/install.md`](docs/install.md) |
| Architecture, conventions, the branch model, the test runner, hygiene | [`CLAUDE.md`](CLAUDE.md) |
| What the Claude Code skills promise | [`plugins/mnemon/skills/`](plugins/mnemon/skills/) — thin doors into the engine, no logic |

## Working in this repository

- Branch from `main`, run `tests/run-suites.sh` (every suite, one table, exit 1 on any failure; on a Mac `--both` adds the legacy toolchain — bash 3.2 and python 3.9 must keep working, CI's macOS job checks it otherwise), open a PR. `release` moves only through `mnemon release`.
- Tests are hermetic: no network, no real `claude`, a temporary `HOME`/`XDG_*`; a test that would touch the real plugin registry, a real vault or `~/.config/mnemon` is a defect.
- Specs carry their decisions and amendments; a change to a contract lands in the spec and the code in the same PR.
