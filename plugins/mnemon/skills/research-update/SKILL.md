---
name: research-update
description: Build or rebuild a Wiki/ page of the Mnemon knowledge library from a research dossier (Collections/), through the engine's fenced build — or see what the pages need (--propose: a stale page's rebind, a rebuild, a delete) and apply one approved rebind. Use when the user says "build the wiki page", "update the page on X", "what does the evidence say about X — make a page", "rebuild the page", "what do the wiki pages need", "the source was re-captured, rebind the dossier", "delete the vetoed page", or asks about a collection's derived page.
---

# /research-update — a Wiki/ page from its dossiers

A page of `Wiki/` is **derived**: the engine builds it from a recorded package (the supporting dossiers, the charter, the `source.md` of every item) in the same fence as the extractor, validates the result against the vault, and publishes it as one file with its prints. This session is a caller — it never writes under `Wiki/`, never edits a dossier line except through `apply` on the owner's word, and never passes the conversation, another page or an extract to the engine (there is no flag for it). Protocol: `docs/research.md` in the engine repository.

## Usage

```
/research-update --page "<page>" --kind concept|comparison|contradiction|open-question --collection "<dossier>" [--collection "<dossier>" …] [--reason "<why>"]
/research-update --propose [--collection "<dossier>"] [--page "<page>"]
/research-update apply <proposal-id>
/research-update delete "<page>"
/research-update --page "<page>" --kind <kind> --collection "<dossier>" --dry-run
```

`--page` is the file name without `.md`; `--collection` the dossier's file stem (the first one named is the page's primary dossier). An unchanged package is a **no-op** (nothing written); `--reason` rebuilds it anyway and is written into the page's `## Log`.

## How to execute

Build or rebuild — the dispatcher, the only path form:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research update --page "<page>" --kind <kind> --collection "<dossier>" [--collection …] [--reason "<why>"] [--agent "$MNEMON_AGENT"]
```

Pass `--agent` only when `MNEMON_AGENT` is set in the environment (the engine writes it as `maintained_by`). Read the `RESULT:` line and the exit code; never report a page from exit 0 alone:

| `RESULT:status=` | Exit | What happened | What to tell the user |
|---|---|---|---|
| `published` · `rebuilt` | 0 | the page is in `Wiki/<page>.md` with its prints and a `## Log` line; `warn:` lines may precede it (`uncited: N` items no locator cites, `stale`, `weak-evidence`) | the page and the warnings as printed; an `uncited` item is the owner's call (mark it `**excluded**` or rebuild with `--reason`) |
| `no-op` | 0 | the package is unchanged and no `--reason` was given: nothing written | say so; offer `--reason` if the user wants a rebuild |
| `refused` | 2 | before any model ran: `class=` `veto` (an open dossier forbids the page), `collection` (not open, unresolved items, malformed), `budget`, `target` (the page was **edited by hand** — never overwritten), `snapshot`, `oversize`, `config`, `usage` | the engine's message verbatim — each names the fix; for `target`: the owner returns the edit to git or moves the text to the dossier's `## Notes (owner)` / `Synthesis/` |
| `refused` | 3 | `fence-unprobed`: the fence has no probe record for this Claude Code version | run `mnemon fence-probe` once, then retry |
| `stopped` | 4 | the model ran, nothing was written: `generation`, `invalid-output` (two rejected answers — the message names the rule), `lost-update` (something moved during the build) | report the class and the attempt directory; `lost-update` is safe to retry |

**Proposals** (read-only):

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research update --propose [--collection "<dossier>"] [--page "<page>"]
```

Show the proposals as printed, with their ids. A `rebind` (a `## Items` line pinned to a superseded revision) is **the owner's edit**: ask the owner whether to apply it, and only on a yes run

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research apply <id> --by "<who said yes>"
```

A `rebuild` proposal is the `research update` command printed with it; a `resolve` one is the owner's (a hand edit to return to git); a `delete` one is

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research delete "<page>"
```

which the engine accepts only for a page that is `suppressed` (vetoed by an open dossier) or has no open supporting dossier.

## Display

The engine's lines as printed, then one block:

```
Page: Wiki/<page>.md — <published | rebuilt | no-op | refused (<class>) | stopped (<class>)>
Warnings: <the warn: lines, or none>
Next: <only when the status calls for one: --reason, fence-probe, the owner's decision on a target refusal, the apply command to confirm>
```

## If the command fails

- `No such file or directory` on the dispatcher path → the engine is not installed at `$MNEMON_HOME`; follow `docs/install.md`
- `unknown command: research` → the installed engine predates phase 3c; update the engine (`release` branch)
- exit 75 → the vault or the engine is locked; retry in a moment
- `this host … must not write vault` → the instance declares another `writer_host`; the build runs there
