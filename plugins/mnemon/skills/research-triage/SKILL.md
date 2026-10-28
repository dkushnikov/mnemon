---
name: research-triage
description: Map an agent-drafted Synthesis/ note of the Mnemon knowledge library into its destinations for the owner's verdict — items for a dossier, a reason to build a Wiki/ page, the agent's position for the page's reading, a seed of the owner's thesis, or a drop (read-only). Use when the user says "triage the agent drafts", "what should happen to the old Atlas drafts", "reclassify the draft on X", "clear the ratification queue", or when the doctor reports unratified drafts over the radius.
---

# /research-triage — the fragment map of an agent-drafted note

An agent-drafted note in `Synthesis/` (`drafted_by` set, not `stable`) counts against the owner's ratification radius. `research triage --propose` maps each one — the oldest first — into destinations: every `[[Sources/…]]` link as an `## Items` line for a dossier, every section as a reason to build a page (its prose is **not** copied into a page — the page is derived again from `source.md`), the first-person paragraphs as the agent's position for a page's `## Atlas reading`, and the rest to drop. The owner's thesis the engine cannot tell: that bucket is the owner's. The engine executes none of it. Protocol: `docs/research.md` §6.

## Usage

```
/research-triage
/research-triage --note "<name>"
```

## How to execute

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research triage --propose [--note "<name>" …]
```

Show the map as printed, one note at a time, and ask the owner for a verdict per note — three buckets:

| Verdict | What then |
|---|---|
| **ratify** | the owner sets `status: stable` by hand; nothing for the agent to do |
| **reclassify** | execute the map through the doors, in this order: the items appended to the dossier the owner names (`Collections/<dossier>.md`, `## Items`, append only, the role the owner gives); the page built with `/research-update` (`--kind` from the map); the position moved into that page's `## Atlas reading` by a `--reason` rebuild only if the owner wants it kept; a thesis the owner recognises as theirs → `/research-synthesize --status seed`; then, and only then, the old note removed — its name, date and `drafted_by` go into the page's `## Log` line (the `--reason`) and into `migrated_from` of the dossier |
| **drop** | remove the note (the vault's git keeps it) |

Never remove a note before its map is executed and confirmed; never set `stable`; never copy the draft's prose into a page.

## Display

```
<note> — <status> · drafted_by <agent> · created <date> · <age> d
  items (N): <the proposed lines>
  sections (N): <heading → kind>
  position (N paragraphs)
  verdict: ratify · reclassify · drop   ← the owner's
```

## If the command fails

- `no agent-drafted notes to triage` → nothing to do; the radius is about something else (`mnemon doctor --data`, `human/*`)
- `unknown command: research` → the installed engine predates phase 3c; update it
