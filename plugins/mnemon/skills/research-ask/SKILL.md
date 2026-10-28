---
name: research-ask
description: Answer a research question from the Mnemon knowledge library's wiki pages — the engine serves the usable pages' evidential bodies with their locators into source.md, the answer rests on them, and the use is recorded with a task id. Use when the user asks "what does the evidence say about X", "what do my sources say about X", "according to the wiki, …", "answer from the research pages", "what positions are there on X", or any substantive question a built Wiki/ page may cover.
---

# /research-ask — answer from the evidence, record the use

The engine validates every page of `Wiki/` and serves the **usable** ones (never an unbound, suppressed or not-probed page) — their evidential bodies and verified locators only. `## Atlas reading` is never served: an opinion is not evidence, and the answer must not rest on it. Every call is journaled with a task id; the use is recorded afterwards. Protocol: `docs/research.md` §4 in the engine repository.

## Usage

```
/research-ask "<question>"
/research-ask "<question>" --collection "<dossier>"
/research-ask "<question>" --page "<page>"
```

## How to execute

1. **One task id per task**, reused across the task's calls: a short slug the user gave, else `<date>-<four words of the question>` (e.g. `2026-10-28-trust-without-identity`). State it in the answer.
2. Serve the pages:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research ask --task-id "<id>" --question "<question>" [--collection "<dossier>"] [--page "<page>"] [--domain <d>] [--limit <n>]
```

   The output is one block per page: name, kind, dossiers with their questions, states, `body_sha256`, then the evidential body. `--json` gives the same with the verified `locators` listed.

3. **Answer from the served bodies**: claims with their primary locators (`[[Sources/<rev>/source#<heading>]]` or the quote form) as they stand in the pages; keep distinct positions apart with the conditions under which each holds; say what the pages leave open. Never cite a page's `## Atlas reading` (it is not served), never read the pages' files for it, never invent a consensus. When no page covers the question, say so — `/source-search` finds sources, `/research-update` builds a page.

4. **Record the use** — one call per page that carried the answer, or one `no-page` when none did:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research ask --task-id "<id>" --record --page "<page>" --outcome used|not-used|no-page [--note "<one line>"]
```

   (`--page` is required by the engine even for `no-page`: name the page closest to the question, or the primary dossier's page.)

## Display

```
Task: <id>
Pages: <name (kind, states) — served>, …   or: no usable page covers this
<the answer: claims with locators; positions apart; what is open>
Recorded: <page> — used | not-used | no-page
```

A page whose states include `stale` or `weak-evidence` is served with a warning: say so next to the claims that rest on it.

## If the command fails

- `refused` with `class=config` → `research_log_path` of the instance is invalid (under `Wiki/` or outside the vault); the owner fixes the config
- `refused` with `class=collection` → `Collections/` could not be listed: no page is known to be usable; report it
- `unknown command: research` → the installed engine predates phase 3c; update it
- exit 75 → the engine is being updated (`mnemon release`); retry in a moment
