---
name: research-synthesize
description: Draft a Synthesis/ note of the owner's own thought in the Mnemon knowledge library — a seed or a draft with drafted_by, written by the engine only while the ratification radius holds; never stable. Use when the user explicitly asks "draft my position on X", "write a seed for my thesis", "sketch what I think about X as a synthesis note", "put my thought into Synthesis". Not for cross-source summaries (those are Wiki/ pages: /research-update) and not for filing sources (a dossier).
---

# /research-synthesize — a draft of the owner's thought, inside the radius

`Synthesis/` is the owner's thinking. An agent writes there only on the owner's explicit request, only `seed` or `draft`, always with `drafted_by` — and only while the charter's radius holds: unratified drafts and open seeds over the radius mean the owner is behind on ratifying, and a new draft would be debt, not knowledge. The engine checks the radius with the data doctor's own checks and refuses when it is crossed, naming the oldest notes to ratify or prune. `stable` stays the owner's act, by hand. Protocol: `docs/research.md` §5.

## Usage

```
/research-synthesize --name "<note>" --status seed|draft
```

## How to execute

1. The request must be the owner's explicit ask for **their** position (a seed: a first thought in one to three paragraphs; a draft: a longer argument). "Save this knowledge somewhere" is not it — that is a dossier (`Collections/`) or a page (`/research-update`).
2. Write the note's body as the owner dictated or asked for — their thesis, their words where given, links to the revisions it builds on as `[[Sources/<rev>/extract]]`, optionally the page it answers as `--from-page`. The body is yours to compose on the owner's instruction; the engine writes the frontmatter and checks nothing else.
3. Feed the body on stdin:

```bash
printf '%s\n' "<body>" | "${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research synthesize --name "<note>" --status seed|draft [--from-page "<page>"] [--agent "$MNEMON_AGENT"]
```

   For a long body write it to a temporary file and `cat` it into the pipe.

4. Read the `RESULT:` line:

| `RESULT:status=` | Exit | What happened |
|---|---|---|
| `drafted` | 0 | `Synthesis/<note>.md` written with `status`, `drafted_by`, `created` |
| `refused` · `class=radius` | 2 | the radius is crossed; the message names the oldest drafts and seeds — tell the owner: ratify (set `stable` by hand) or prune those first, then ask again; **do not** write the note yourself |
| `refused` · `class=exists` | 2 | a note of that name exists — pick another name or ask the owner |
| `refused` · `class=usage` | 2 | `--status stable`, an invalid name, an empty body |

## Display

```
Synthesis/<note>.md — <seed | draft> (drafted_by <agent>)
<or: refused — the radius is crossed; the oldest: <list>; ratify or prune first>
```

Remind the owner that a `stable` status is theirs to set, by hand, when the note is ratified.
