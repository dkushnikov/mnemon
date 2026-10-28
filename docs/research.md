# Research operations — `research/1`

Status: **v1** (phase 3c) · Scope: the public protocol of the operations that build, use and tidy the research layers of a vault — `mnemon research update` (with `--propose`), `apply`, `delete`, `ask`, `synthesize` and `triage --propose` — their inputs, the doors they may write through, the preflight, the no-op rule, the errors, the journal and the postconditions. What a `Collections/*.md` dossier and a `Wiki/*.md` page *are*, how they are read and which states the doctor derives is the research interface ([`specs/2026-10-27-research-interface.md`](specs/2026-10-27-research-interface.md), `collection/1` · `wiki/1`); this page says what an operation may do to them. The Claude Code skills of the plugin are **adapters** of this protocol: they call the engine's dispatcher and format its answers; the engine owns the preflight, the fence, the validation, the publication and the journal.

**The one rule behind every operation:** a page of `Wiki/` is derived from a recorded package of inputs by a model that runs with no tools, and is published only after the engine has validated the result against the vault and re-checked that nothing it read has moved. Nothing in this protocol lets a session, a skill or a model write a page by hand.

## 1. Identifiers

| Identifier | Meaning |
|---|---|
| `research/1` | this protocol; printed as `inputs.protocol` on every page the engine publishes (interface §9) |
| `wiki/<kind>@1` | the page template of a kind (`concept` · `comparison` · `contradiction` · `open-question`): the sections of §3.6, printed as `inputs.template` |
| `research-package/1` | the build package — the prompt the fenced model receives (§3.4) |
| `research-answer/1` | the structured output the fenced model returns (§3.5) |
| `mnemon-research-result/1` | the JSON of `research update` · `apply` · `delete` · `synthesize` (`--json`): `{schema, op, outcome, class, page, detail, …}` (§8) |
| `mnemon-research-propose/1` | the JSON of `research update --propose` (§3.10) |
| `mnemon-research-ask/1` | the JSON of `research ask` (§4) |
| `mnemon-research-triage/1` | the JSON of `research triage --propose` (§6) |
| `mnemon-research-journal/1` | one JSON line per operation in the research journal (§7) |
| Guide **5** | `vault-template/` stamped `mnemon_guide: 5`: the `Wiki/` door is now `mnemon research update`, the other operations are listed under Doors |

## 2. The operations and their doors

| Operation | Reads | Writes (under the vault lock) | Runs a model | Journal |
|---|---|---|---|---|
| `research update` | the supporting dossiers, the charter, the `source.md` of every item, the target page | **one file**: `Wiki/<page>.md` (temporary file → rename) | yes, in the fence (§3.4) | `research-update` |
| `research update --propose` | the same, read-only | nothing | no | `research-propose` |
| `research apply <id>` | one dossier | one `## Items` line of that dossier (the approved rebind, I7) | no | `research-apply` |
| `research delete <page>` | the page, the dossiers, the vault's notes | removes `Wiki/<page>.md`; rewrites incoming links in `Collections/`, `Synthesis/` and the consumable sections of other pages (§3.11) | no | `research-delete` |
| `research ask` | the usable pages (interface §9) and their dossiers | nothing (the journal only) | no | `research-ask` |
| `research synthesize` | the charter's radius, `Synthesis/` | one new `Synthesis/<name>.md` (`seed` or `draft`, `drafted_by`) | no | `research-synthesize` |
| `research triage --propose` | the agent-drafted notes of `Synthesis/` | nothing | no | `research-triage` |

Every writer is refused on a host that is not the instance's `writer_host` (vault store I7) and runs under the vault lock (`bin/vault-lock.py`, like `add`, `reindex` and `triage dismiss`; a held lock is exit 75). The read-only operations take no lock: their only write is one appended journal line, and when the journal lies inside the vault (`research_log_path`) they too are refused on a host that is not the `writer_host` (§7). No operation writes `Sources/`, `index.md`, the archive or the ledger; none writes `status: stable` anywhere; none edits a page in place.

## 3. `research update`

```
mnemon [--instance <name>] research update --page <name> --kind <kind> --collection <dossier> [--collection <dossier> …]
        [--reason "<why>"] [--agent <name>] [--recusal on|off] [--dry-run] [--json]
mnemon research update --propose [--collection <dossier>] [--page <name>] [--json]
```

`--page` is the page key: the file name without `.md` — no `/`, `\`, `|`, `#`, `[[` / `]]`, no leading `.`, no `.md` suffix, at most 120 characters. `--kind` is one of the four. `--collection` names a dossier by its file stem (repeatable; the first one named is the page's primary dossier, the one its `## Log` line cites); together they are the page's `collections`. `--agent` is `maintained_by` (default `$MNEMON_AGENT`, else `agent`): a plain token (`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`, written unquoted as the Guide shows; anything else is `usage`). `--reason` is the reason recorded in `## Log` and the one thing that makes an unchanged package rebuild (§3.3).

### 3.1 Preflight — nothing is read for the model before every check passes

In this order; the first failure stops the operation with the class named (§8), nothing written, one journal line.

1. **Writer host and lock** — the dispatcher's rule (§2).
2. **Usage** — the page key, the kind, at least one `--collection`, `--recusal` a valid word (`usage`, exit 2).
3. **Journal path** — `research_log_path` (§7) resolves to a `.jsonl` file physically inside the vault (the nearest existing ancestor is resolved through symlinks) and outside `Sources/`, `Wiki/`, `Collections/`, `Synthesis/`, `_meta/`, `Templates/`, `_inputs/`, `index.md` and `CLAUDE.md` — the journal can alias no source, page, dossier, note or canon file (`config`, exit 2).
4. **Supporting dossiers** — every `--collection` exists in `Collections/` and reads as `state: ok` (a dossier whose schema or `## Items` grammar fails is not a package — the doctor's `research/collections` names the line); **at least one is `status: open`** (a page none of whose dossiers is open would be born `orphan`); an open dossier with **unresolved items** (an item whose target does not exist) is refused — the package would silently lose it; an existing page's supporting dossiers may **not be dropped** by an update — every dossier the page lists must be named again (removing support is the owner's act: the veto, then `delete`) (`collection`, exit 2). Dossiers that are not `open` (`inbox`, `closed`) may be listed: they govern nothing, veto nothing and contribute no items; they are recorded in `collections`, not in `inputs.collections`.
5. **Veto** — `lib_research.is_suppressed(page, collections, …)` is false over the **union** of the dossiers named and the dossiers the existing page lists (`veto`, exit 2). The page name and the dossiers are what the owner's `wiki: off` and `suppressed_pages` are compared against; the veto is evaluated again before publication (§3.7). The veto is scoped as the interface scopes it — to the supporting dossiers of the page, not to the file name globally: a deleted name can be built again under a different, unvetoing dossier.
6. **Budget** — for a **new** page: the open dossiers named already have fewer than `doctor_wiki_pages_per_collection` pages each (pages are counted by the `collections` they list, readable pages only); an existing page is always allowed to rebuild (`budget`, exit 2).
7. **The target** — if `Wiki/<page>.md` exists, **the bytes the snapshot captured** (§3.2 — read once; the same bytes the recheck compares and the rename replaces) are read with `read_wiki_text` and validated with `validate_wiki` on the vault's research context: a page whose schema fails or whose evidential body's sha256 ≠ its printed `body_sha256` (`edited-outside-update`) is **never overwritten** — the operation stops, both hashes are printed, the prints are not recomputed (`target`, exit 2). The owner returns the edit to git or moves the text to the dossier's `## Notes (owner)` or to `Synthesis/`, then runs the update again. Any other state (`stale`, `inputs-changed`, `unbound`, `weak-evidence`, `orphan`) is a reason to rebuild, not a stop. Validating the captured bytes, not a copy read earlier, is what closes the window between "validated" and "hashed": a hand edit saved between the two would otherwise be hashed as the target, pass the recheck and be overwritten.
8. **The fence** — `claude` is on `PATH` (or `$CLAUDE_CMD`), `claude --version` answers, `--help` lists `--restricted`, and the probe record `${XDG_STATE_HOME:-~/.local/state}/mnemon/fence-probes.json` carries `pass: true` for exactly that version (capture contracts §4.2, D13); any miss → `fence-unprobed`, exit 3, with the command to run (`mnemon fence-probe`). `--dry-run` reports the fence's status instead of refusing.
9. **Recusal** — declared when the owner wrote `recusal: on` in any open supporting dossier, or when none wrote `recusal: off` and a tag of any open supporting dossier is in `research_recusal_tags` (§9; the default list names the agent's own design, identity and behaviour). `--recusal on|off` overrides the tags, never an owner's explicit `recusal:`. Recusal omits `## Atlas reading` from the page (§3.6) and is written into the `## Log` line.

### 3.2 The snapshot

The engine reads each dependency **once** and derives its validation, its fingerprint and its contribution to the package from those bytes (nothing is read a second time for the prompt):

- every supporting dossier: the sha256 of its file bytes (the reader record's `sha256`, interface §5), its `status`, `wiki`, `suppressed_pages`, `recusal`;
- the charter (`charter_path`, default `_meta/charter.md`): its sha256, or `absent`; an unreadable charter stops the operation (`snapshot`, exit 2 — a dependency that cannot be read never yields a clean result);
- every **item** of every open supporting dossier that is not `excluded` (an item excluded in one dossier and listed in another is in the package — the exclusion is per dossier): its revision — **once** per revision, whatever the number of dossiers listing it (`based_on` is a set; the dossiers' roles and notes stay with the revision in the package) —, the exact body bytes of `source.md` (`lib_vault.body_bytes`) hashed, the whole file's sha256 (for the recheck: the frontmatter the package shows is covered too), and the **seal** — `capture` when the scanner's class is `managed` (then `capture.text_sha256` must equal the body's hash), `observed` when it is `legacy`; a `damaged` revision (some managed keys), a managed revision whose body ≠ `capture.text_sha256` (integrity error), a `source.md` without a frontmatter body (`body_bytes` finds none) or one that cannot be read, a revision on a supersedes cycle — each stops the operation (`snapshot`, exit 2); a bookmark-tier revision with a body is an item like any other; a superseded revision is an item like any other (the page will be `stale`; `--propose` is its door, §3.10);
- the whole vault's dependencies the validator needs: **no** `source.md` anywhere may be unreadable and `Sources/`, `Collections/`, `Wiki/` must be listable — otherwise the supersedes graph is incomplete and no page can be validated clean (`snapshot`, exit 2; the validator would say *not probed*);
- the target: the sha256 of `Wiki/<page>.md`'s bytes, or `absent`;
- the versions: `research/1` and `wiki/<kind>@1`.

The **package fingerprint** is the sha256 of the canonical JSON (`sort_keys`, separators `,`/`:`, UTF-8, no ASCII escaping) of `{page, kind, supporting: [<every --collection, in order>], collections: {"Collections/<name>": sha256 …} (open dossiers only), charter_sha256 | null, based_on: [{rev, text_sha256, seal} … sorted by rev], protocol, template}` — what the page prints as `collections`, `inputs` and `based_on`.

### 3.3 No-op, decided before any model runs (contract I2)

When the target exists and its prints reproduce the **build identity** — same `kind`, the same ordered `collections`, same `inputs.collections` (names and hashes), same `charter_sha256`, same `based_on` set with the same hashes **and seals**, same `protocol` and `template` —, the page is not in a repair-required state (no fail state, nothing *not probed*: an `unbound` page with unchanged inputs is rebuilt, never a no-op), its `## Atlas reading` agrees with the recusal decision (a page carrying one while recusal is now declared is rebuilt without it), and no `--reason` was given, the operation ends as **no-op**: nothing is written, `updated` does not move, the prints and `## Log` stay byte for byte, exit 0, outcome `no-op` in the journal. A `--reason` rebuilds an unchanged package (the owner asked); a new page, or a page whose package differs, builds. `stale` alone is **not** a reason: a superseded revision leaves the package unchanged (the pinned `source.md` is immutable) — the rebind proposal of §3.10 is the door, and after the owner's rebind the package differs and the next update builds.

### 3.4 The package and the fence (contract I5)

The model receives **exactly** the package below as one prompt on stdin, assembled by the engine (`research-package/1`) **from the bytes the snapshot captured** (the charter, the dossiers, every `source.md` — read once in §3.2; the retry of §3.5 reuses them, nothing is re-read for a prompt), and nothing else: nothing of the existing page (not even its headings — a heading is content), not any other wiki page, not an `extract.md`, not the reader context, not the conversation — the self-referential loop is excluded by construction, not by instruction; the template fixes the sections.

```
NON-INTERACTIVE RESEARCH BUILD. You are a function: read the sections below and return only the structured fields of the output schema. No questions, no commentary, no tool use.
=== PAGE ===            page key · kind · supporting dossiers · recusal: on|off · protocol · template
=== CHARTER ===         the charter's text, or "(no charter)"
=== DOSSIER: <name> === the dossier file (frontmatter and body) without its `## Notes (owner)` section — one per open supporting dossier
=== TEMPLATE ===        the sections and rules of wiki/<kind>@1 (§3.6)
=== SOURCE: <rev> ===   Role / Note from each dossier line naming it, then title · author · url · captured · source_type · origin · tier · completeness from source.md's frontmatter, a note when the revision is superseded, then the body of source.md verbatim — one per revision, sorted
=== OUTPUT ===          the rules of §3.5
```

**Limit.** The package's size is measured before the model runs: above `research_package_max_bytes` (default 400 000) the operation stops as `oversize` (exit 2) naming the total and the largest sources — split the dossier, exclude items, or raise the key. v1 does **not** chunk a research package (a chunked build would have to keep every revision and every locator across parts; deferred — §12).

**The fence.** The engine runs `bin/extract-run.py` — the extractor's runner, unchanged: `claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config --no-session-persistence --output-format json --json-schema <research-answer/1> --max-turns 4 --model <default_model>`, the prompt on stdin, in a per-attempt directory under the vault's state directory (`research/<date>-<page hash>/attempt-N/`), `research_timeout` seconds and `--max-budget-usd research_max_usd` when set — both **per attempt** (an update makes at most two, §3.5: the whole operation may spend twice either); the vault lock descriptor and variables are scrubbed from the child (§12 A1 of the capture contracts). The attempt directory keeps `prompt.md`, `argv.txt`, `result.json`, `stderr.txt`. The model has no tools: an instruction inside a source, a dossier or the charter cannot run anything; it can still persuade the model's text (capture contracts §4.9), which is what §3.5's validation and the fixtures of §11 are for.

### 3.5 The answer and its validation

`research-answer/1`:

```json
{
  "type": "object",
  "properties": {
    "state_of_evidence": {"type": "string", "minLength": 1, "maxLength": 60000},
    "positions":         {"type": "string", "maxLength": 30000},
    "contradictions":    {"type": "string", "maxLength": 30000},
    "open_questions":    {"type": "string", "maxLength": 20000},
    "atlas_reading":     {"type": "string", "maxLength": 30000}
  },
  "required": ["state_of_evidence", "open_questions", "atlas_reading"]
}
```

The engine assembles the page from the fields (§3.6) and validates the **assembled page** with the interface's reader and validator (`read_wiki_text` + `validate_wiki` on the vault's research context — the same code the doctor runs), plus the rules of this protocol. The build is rejected when:

- a field is missing, not a string, above its `maxLength`, or holds a NUL byte or a carriage return (the reader treats a bare CR as a line break, so a CR could smuggle a heading or an open fence past the line checks; the engine checks the schema's types and bounds itself, before assembly — the structured-output contract is not trusted);
- a field holds a `#` or `##` heading line outside a fence (it would move the body boundary or add a second consumable section — the interface's reader accepts two `## Log` sections; the engine does not), or leaves a fence open at its end (fences are tracked by the reader's rule — a run of three or more backticks or tildes, closed by a run of the same character at least as long; an open fence would swallow `## Atlas reading` and `## Log` into the evidential body); the fields may use `###` and deeper only;
- the reader reports a schema error on the assembled page, or the assembled page's `##` sections are not exactly the ones the engine wrote (the kind's sections, then `## Atlas reading` when given, then `## Log`, once each, the cut at the first consumable heading — defence in depth behind the field checks);
- the validator leaves anything **not probed** (a dependency it could not read — the snapshot refuses these before the model runs, so this is the second line of defence);
- the validator derives `unbound` — a locator not found or not unique, an empty quote, a link outside the instance, a link to a target that does not exist, or **`unlisted`** (a Sources link to a revision outside the package, contract I6);
- a field holds a link that is not a Sources locator or navigation link of the interface's §3 forms: no `[[Collections/…]]`, `[[Wiki/…]]`, `[[Synthesis/…]]`, no external `[[…]]` — a page cites evidence, nothing else; `atlas_reading` may link Sources and the page's **own** dossiers (`[[Collections/<a supporting dossier>]]`), nothing else;
- `positions` is empty for a `comparison`, `contradictions` is empty for a `contradiction`;
- the evidential body exceeds `doctor_wiki_words` (`budget`);
- `atlas_reading` is non-empty under recusal (it is dropped, not an error; the Log line says so).

One rejection re-runs the model **once** with a `=== VALIDATION ===` section that names the rule (never the model's own text reflected back); a second rejection stops the operation (`invalid-output`, exit 4) — the target is untouched. `weak-evidence` (a `partial`/`blocked` revision) and `stale` are warnings printed with the result, never a rejection; an item of the package that none of **this page's** verified locators cites is reported as `not cited by this page: N` and is not a rejection — the owner decides whether the item belongs (`**excluded**`) or the page is incomplete (`--reason`). This is a build-local count; the doctor's `uncited` (`research/wiki-evidence`) is the dossier-wide one, across every usable page listing the dossier, and may differ.

### 3.6 The page the engine publishes (`wiki/<kind>@1`)

```
---
type: wiki
kind: <kind>
collections: ["[[Collections/<a>]]", "[[Collections/<b>]]"]        # every --collection, in order
domains: [<the union of the open dossiers' domains, sorted>]
based_on:                                                          # one entry per item of the package, sorted by revision
  - rev: "[[Sources/<rev>/extract]]"                               # /extract when the revision has one, else the bare revision
    text_sha256: "<sha256 of source.md's body bytes>"
    seal: capture | observed
inputs:
  page: "<page>"
  kind: <kind>
  collections: {"Collections/<a>": "<sha256>"}                     # the open dossiers only
  charter_sha256: "<sha256>"                                       # omitted without a charter
  protocol: "research/1"
  template: "wiki/<kind>@1"
maintained_by: <agent>
created: <date of the first publication, kept>
updated: <today>
body_sha256: "<sha256 of the evidential body as published>"
visibility: <kept from the existing page; personal for a new one>
---

# <page>

## State of evidence
<state_of_evidence>

## Positions            ← comparison only
<positions>

## Contradictions       ← contradiction only
<contradictions>

## Open questions
<open_questions>

## Atlas reading        ← omitted under recusal, and when atlas_reading is empty
<today> — <atlas_reading>

## Log
<the existing page's Log lines, kept>
- <today> — built | rebuilt from [[Collections/<primary>]] on <n> revisions (<reason | package changed | new page>)[; recusal declared (<why>) | ; no reading given]; research/1
```

The evidential body is the text from `# <page>` to `## Atlas reading` / `## Log` (interface §9); `body_sha256` is computed on the assembled text by the same function the doctor uses. A `concept` and an `open-question` page carry no `## Positions` / `## Contradictions`; a `comparison` carries `## Positions`; a `contradiction` carries `## Contradictions`. `## Atlas reading` and `## Log` are consumable sections — outside the hash and outside the `research ask` package; search cuts `## Atlas reading` from its snippets (interface §7) and keeps `## Log` (a date and a reason are not an opinion).

### 3.7 Recheck before publication (contract I2)

With the page assembled and validated, the engine re-reads from disk everything of §3.2 — the dossiers' bytes, their veto fields, the charter, every item's `source.md` body, the target page's bytes — and compares each with the snapshot. **Any difference stops the operation** (`lost-update`, exit 4): nothing is written, the result names what moved. The atomic rename of §3.8 protects a reader from a half-written page; only this recheck protects the owner from a lost edit — and only in the one-writer mode the Guide asks for (the residual window between the recheck and the rename is accepted; an edit lost there comes back from the vault's git, not from the engine).

### 3.8 Publication

One file, written once: `Wiki/` is created when the vault has none yet (a fresh vault's first page), then a temporary file in `Wiki/` (`.research-<page>.<pid>.tmp`), fsync, `rename` over `Wiki/<page>.md`, the directory fsynced. The prints (`based_on`, `inputs`, `body_sha256`), `updated`, the body, `## Atlas reading` and the new `## Log` line are all in that one write — no second edit, no append after the rename. An interrupted write leaves the old page intact and no temporary file (the file is removed on any failure before the rename). `created` is kept from the existing page; `## Log` lines of the existing page are kept in order and the new line appended.

### 3.9 The journal and the result

After the rename the engine appends one line to the research journal (§7); a crash between the two leaves a published page without a journal line — the page is the record (`## Log`), the journal is an observation, and nothing is replayed. Stdout carries the human lines and a `RESULT:` line (`RESULT:op=research-update status=published|rebuilt|no-op|refused|stopped page=<page> class=<class or ->`); `--json` prints `mnemon-research-result/1` instead. `--dry-run` runs the preflight (reporting the fence instead of refusing), takes the snapshot, decides the no-op, prints the package's sections with their sizes, the fingerprint and the argv — and runs no model and writes nothing: no journal line, not even for a refusal it meets on the way.

### 3.10 `--propose` — proposals, never edits

`research update --propose [--collection <dossier>] [--page <name>] [--json]` is read-only: it validates every page of the selection (all pages; the pages listing a dossier; one page) on the vault's research context and prints what an operation would do, one proposal per cause, each with an id that binds **what was approved**: a rebind's id is `sha256(rebind|<dossier>|<dossier file sha256>|<line>|<the line's exact text>|<from>|<to>)[:12]` — any change to the dossier (the line, its role, a continuation, another line) changes the id; the other kinds hash their page and cause:

| State of the page | Proposal |
|---|---|
| `stale` — a `based_on` revision is superseded | **rebind**: for every open supporting dossier with an item on that revision, the `## Items` line rewritten to the revision's current head (`lib_vault.current_heads`), role and note kept — one proposal per head when there are several; a head that is already an item of the dossier, or a superseded revision with no reachable head, is a **report** (`kind: report`), not a proposal — a rebind must not duplicate a line |
| `inputs-changed` | **rebuild**: the `research update` command that rebuilds the page (the package changed, so it is no no-op; an `unbound` page with unchanged inputs is rebuilt too — §3.3 excludes it from no-op) |
| `edited-outside-update` | **resolve**: the hand edit must go back to git or to the dossier's notes / `Synthesis/` before any update (no command — the owner's call) |
| `suppressed` | **delete**: `research delete <page>` |
| `orphan` with no open supporting dossier | **delete**: `research delete <page>` |
| `uncited` items of its dossiers | **cite**: listed per dossier — the next update may cite them, or the owner marks them `**excluded**` |
| `unbound` (other reasons) | **rebuild** with the reasons — the page is no ground until it is rebuilt |

The JSON (`mnemon-research-propose/1`): `{schema, vault, generated_at, pages: [{page, kind, collections, states, proposals: [{id, kind: rebind|rebuild|resolve|delete|cite|report, collection, line, text, from, to, new_text, command, reason}]}]}`; a refusal under `--propose` carries `op: research-propose`. One journal line (`research-propose`) records the proposals' ids. Nothing is written to the vault.

### 3.11 `apply` and `delete` — the two edits an agent may make on the owner's word

`research apply <id> --by <who> [--json]` executes **one rebind proposal** (I7: an explicitly approved edit of one `## Items` line, applied once, without a duplicate): the proposals are recomputed from disk and the id must be among them — since the id binds the dossier's digest and the line's text, an id survives only while nothing in that dossier moved; a missing id is `proposal` (exit 2: applied already, or the dossier, the line or the revisions changed — propose again); the dossier's digest and the line are compared once more right before the write. The link target of that line is rewritten to the head revision — the file part is kept when the head has that file (`/extract` → `/extract`, `/source` → `/source`), else `/extract` when the head has one, else the bare revision; a `#fragment` is dropped (it anchored into the old file); the display text, the role and the note are untouched — and the dossier is written atomically; a head that is already an item is a refusal (`duplicate`, exit 2). `--by` names who approved — the adapter asks the owner before calling; the engine records the name in the journal, it cannot verify it. The rebind does not rebuild the page: the package now differs (`inputs-changed`), and the next `research update` does.

`research delete <page> [--json]` removes a page in exactly the two cases of the decision document, each **proven**: the page is `suppressed` (an open, readable supporting dossier vetoes it), or **no** supporting dossier is `open` with every supporting dossier readable and `Collections/` listable (an unreadable dossier or an unlistable folder gives no orphan verdict — the interface withholds it — and the deletion is refused). A page whose schema fails (no frontmatter, a broken `collections:`) has unknown support, not none: refused — the owner fixes or removes it by hand. Any other page is refused (`delete`, exit 2) — the owner deletes by naming the page in `suppressed_pages` and running `delete`; the veto then outlives the file under that dossier (§3.1 step 5 scopes it). Before removing the file the engine rewrites the incoming links to the page — every `[[Wiki/<page>]]`, `[[Wiki/<page>.md]]`, `[[Wiki/<page>#…]]`, `[[Wiki/<page>|…]]` and the `![[…]]` embed form, fences included, exact name only — to `[[Collections/<primary dossier>]]` (the page's first supporting dossier) in the **bodies** of `Collections/*.md` and `Synthesis/*.md` — never in a frontmatter: a `suppressed_pages` entry written as `[[Wiki/<page>]]` is the owner's veto and outlives the file — and in the **consumable** sections (`## Atlas reading`, `## Log`) of other pages, one file at a time by **compare-and-swap**: each file is re-read right before its write and skipped (reported) when it changed meanwhile, another page's body boundary derived from those same bytes; a `![[Wiki/<page>]]` embed becomes a plain `[[Collections/<primary>]]` link; a UTF-8 BOM before a frontmatter is respected; a link inside another page's evidential body is reported, not rewritten (that body is the other page's print — its door is its own rebuild); a page with no supporting dossier at all has no target: its incoming links are reported and left. The page itself is removed **last**, after its own bytes are compared with the ones validated (`lost-update`, exit 4, when they moved): a failure midway leaves the page in place with some links already rewritten, and the operation is run again (a rewritten link stays rewritten). The rewritten, skipped and reported files are in the result and the journal.

## 4. `research ask` — the usable evidence, and the record of its use

```
mnemon research ask --task-id <id> --question "<text>" [--collection <dossier> …] [--page <name> …] [--domain <d>] [--limit <n>] [--include-orphans] [--json]
mnemon research ask --task-id <id> --record --page <name> --outcome used|not-used|no-page [--note "<text>"] [--json]
```

**Serve.** The engine validates every page on the vault's research context and serves the **usable** ones (interface §9: severity `ok` or `warn`; never `unbound`, `suppressed`, schema-failed or not-probed pages; `orphan` pages only with `--include-orphans`). The selection is deterministic: a page is served when it is usable **and** (no `--page` given, or its name is one of them) **and** (no `--collection` given, or it lists one of them) **and** (no `--domain` given, or the domain is in the union of its stored `domains` and its supporting dossiers' — the interface's rule); `--limit` is at least 1 (default 10), `usage` below; the pages are ordered by `updated` desc, then name. `--question` is **recorded, not used for retrieval** in v1 (the adapter chooses among the served pages; the pilot's cluster is small); an unlistable `Wiki/` or `Collections/` is refused (`collection`, exit 2), never an empty success. A page named with `--page` that is not served is listed under `withheld` with the reason (`no such page`, `not usable: <states>`, `orphan`), so an empty answer is explained. For each page: `name`, `kind`, `collections` (with their `question` and `status`), `states`, `updated`, `body_sha256` (the hash of the body **as served** — for a usable `edited-outside-update` page it differs from `printed_body_sha256`, the print), `based_on`, the verified `locators`, and `body` — the **evidential body only** (`## Atlas reading` and `## Log` are never served: an opinion is not evidence, contract O-W4). The answer the adapter gives rests on this body and on the primary locators it carries; the fresh-session fixture of §11 asserts that a session with the engine's output and no other instruction answers with `[[Sources/<rev>/source#…]]` locators and nothing from `## Atlas reading`. The text form prints one block per page; `--json` is `mnemon-research-ask/1`: `{schema, vault, task_id, question, count, pages: […]}`. One journal line (`research-ask`, outcome `served`) records the task id, the question and every page served with its `body_sha256` — the pilot's unit of return.

**Record.** After answering, the adapter records what happened: `--record` writes one journal line (`research-ask`, outcome `recorded`) with the task id, `use_outcome` — `used` (the page carried the answer), `not-used` (served, not needed), `no-page` (no page covered the question; `--page` is then optional) —, the page and its **current** `body_sha256` (the version the record refers to; a serve of the same task lists the versions served), and an optional note. Nothing in the vault changes. A `task_id` is the caller's: one id per task, reused across the calls of that task (the pilot counts distinct ids with a `used` record).

## 5. `research synthesize` — a draft of the owner's thought, inside the radius

```
mnemon research synthesize --name "<note>" --status seed|draft [--agent <name>] [--from-page <name> …] [--json] < body.md
```

The decision rule of W5: an agent writes into `Synthesis/` only on the owner's explicit request, only `seed` or `draft`, always with `drafted_by`, and **only while the radius holds** (S5 outranks the request). The engine:

1. reads the charter's `## Radius` (an unreadable charter, a radius line the doctor cannot parse, or a `Synthesis/` that cannot be read in full → refused, `radius`: the radius is unknown, not satisfied; a missing section uses the doctor's defaults, as the doctor does) and evaluates the two human checks of the data doctor on the current snapshot — `human/ratification` (unratified agent drafts: count and oldest age) and `human/seeds` (open seeds) — with the doctor's own functions, then the **prospective** count: the candidate note counts, so a vault exactly at the count limit admits no more (`current + 1 > limit` refuses; a limit of 0 admits nothing); **either over the radius → refused** (`radius`, exit 2) naming the three oldest unratified drafts and seeds (name, status, created) as the way back: ratify or prune, then ask again;
2. refuses `--status stable` as a usage error and refuses a name that exists (`exists`, exit 2);
3. writes `Synthesis/<name>.md` atomically: `type: synthesis`, `status: <seed|draft>`, `drafted_by: <agent>`, `created: <today>`, `from_pages: [[[Wiki/<name>]] …]` when given, then the body read from stdin (the adapter's text — the owner's thought as dictated or asked for, never the engine's); a body without a `# ` title gets `# <name>`;
4. appends one journal line (`research-synthesize`).

The note links the revisions it builds on as the owner's notes do (`[[Sources/<rev>/extract]]`); the engine checks nothing about the body's content beyond the frontmatter it writes. `stable` stays the owner's act, by hand.

## 6. `research triage --propose` — the fragment map of an agent-drafted note

```
mnemon research triage --propose [--note <name> …] [--json]
```

Read-only (W5): for every note of `Synthesis/` with `drafted_by` and a status other than `stable` — the three oldest first (O7) — the engine prints a **map of fragments to destinations**, for the owner's verdict:

| Fragment of the note | Proposed destination |
|---|---|
| a `[[Sources/<rev>…]]` link, with the text of its line | an `## Items` line of a dossier: `- [[Sources/<rev>/extract]] — **<role: the owner's>** — <the line's text>` (appended by the agent through the dossier door once the owner names the dossier) |
| a section (`##`) and its paragraphs that state what sources say | a **reason** to build a page with `research update` — a suggested `kind` by the section's heading (`vs`/`versus`/`против` → `comparison`; `contradiction`/`противоречие` → `contradiction`; a heading ending in `?` → `open-question`; else `concept`); the draft's prose is **not** copied into a page — the page is derived again from `source.md` |
| a paragraph in the first person (`I`, `my`, `я`, `мы`, `наша`) | the agent's position: `## Atlas reading` of that page, whole, dated, naming the note it came from |
| a thesis the owner recognises as their own | a `seed` of `Synthesis/` (`research synthesize --status seed`) — the owner's verdict only; the engine cannot tell |
| everything else | drop |

Three verdict buckets per note — **ratify** (the owner makes it `stable` by hand) · **reclassify** (execute the map) · **drop** (delete the note). **v1 is a report**: the engine proposes and executes none of it. What the doors can execute: an item line is appended to the dossier the owner names (the proposed line renders the note's text **without** its wikilink, so it is one link, one line — the owner gives the role); a page is built with `research update` (the `--reason` names the note: that is the provenance the page's `## Log` carries); a thesis the owner recognises as theirs becomes a `seed` through `research synthesize`; the old note is removed only after the map is executed, and the dossier's `migrated_from` is the owner's line. What no door executes in v1: moving the position paragraph **verbatim** into `## Atlas reading` — `research update` takes no such payload (the model writes the reading afresh from the package); the paragraph is reported for the owner, and its loss is part of the verdict (decision O-W1). A payload for it is deferred (§12). The JSON (`mnemon-research-triage/1`): `{schema, vault, generated_at, notes: [{name, status, drafted_by, created, age_days, items: [{rev, line, text, proposed}], sections: [{heading, kind, paragraphs}], position: [paragraph …], buckets: ["ratify", "reclassify", "drop"]}]}`. One journal line (`research-triage`).

## 7. The journal — `mnemon-research-journal/1`

An append-only file of JSON lines, one per operation, outside the pages: `research_log_path` (§9) when set — a path inside the vault (relative to the vault root, or absolute under it; `~` expanded), **refused** when it lies under `Wiki/` (the validator, search and the budget must never see the journal as a page) or outside the vault — else `<vault state dir>/research-journal.jsonl` (the ledger's directory: `${XDG_STATE_HOME:-~/.local/state}/mnemon/vaults/<key>/`). The file is created on first use, written with one `write` per line, never rewritten, never read by any operation (the pilot reads it).

```json
{"schema": "mnemon-research-journal/1", "ts": "2026-10-28T10:15:00Z", "op": "research-update", "instance": "knowledge", "host": "…", "agent": "atlas",
 "page": "Trust — four answers", "kind": "contradiction", "collections": ["Trust without identity"],
 "inputs": {"fingerprint": "<sha256>", "collections": {"Collections/Trust without identity": "<sha256>"}, "charter_sha256": "<sha256>|null", "based_on": 3, "protocol": "research/1", "template": "wiki/contradiction@1"},
 "outcome": "published | rebuilt | no-op | refused | stopped | served | recorded | applied | deleted | drafted | proposed",
 "class": null, "detail": "built from Trust without identity on 3 revisions", "body_sha256": "<sha256>|null",
 "task_id": null, "question": null, "pages": [], "proposals": [], "attempts": 1, "cost_usd": 0.04, "duration_ms": 18200}
```

`op` ∈ `research-update` · `research-propose` · `research-apply` · `research-delete` · `research-ask` · `research-synthesize` · `research-triage`; `outcome` as above; `class` the error class of §8 for `refused` / `stopped`, else null. Per operation, beyond the common keys (`schema`, `ts`, `instance`, `host`, `agent`, `op`, `page`, `outcome`, `class`, `detail`):

| `op` | Keys |
|---|---|
| `research-update` | `kind`, `collections`, `inputs` (`fingerprint`, `collections`, `charter_sha256`, `based_on` count, `protocol`, `template`), `body_sha256` (published / no-op), `attempts`, `cost_usd`, `duration_ms`, `warnings` |
| `research-propose` | `collections` (the filter), `proposals` (the ids) |
| `research-apply` | `collections` (the one dossier), `proposals` (the one id), `approved_by` |
| `research-delete` | `collections`, `rewritten`, `skipped`, `reported` (each `{file, links[, note]}`) |
| `research-ask` (serve) | `task_id`, `question`, `pages` (`{name, body_sha256}` served) |
| `research-ask` (record) | `task_id`, `use_outcome` (`used` · `not-used` · `no-page`), `page` (null for `no-page` without one), `body_sha256` (the page's current one, null when absent) |
| `research-synthesize` | `status`, `from_pages` |
| `research-triage` | `notes` (the names mapped) |

A `refused` line carries `class` and `detail` only (and `page` when known); `usage` and `config` refusals write no line (the destination may be the problem). A journal write that fails **after** a publication or a write is reported on stderr and does not undo the write (the page or the dossier is the record). Keys may be added; none is renamed within schema 1.

## 8. Errors, outcomes, exit codes

| Exit | Outcome | Classes | Meaning |
|---|---|---|---|
| 0 | `published` · `rebuilt` · `no-op` · `dry-run` · `served` · `recorded` · `applied` · `deleted` · `drafted` · `proposed` | — | done; `no-op` and `dry-run` wrote nothing |
| 2 | `refused` | `usage` · `config` · `collection` · `veto` · `budget` · `target` · `snapshot` · `oversize` · `proposal` (an id that no longer exists: applied, or the dossier, the line or the revisions changed) · `duplicate` · `delete` · `radius` · `exists` · `writer-host` | stopped before any model ran and before any write; the message names the fix. Precedence for `update`: `usage` → `config` → `collection` → `veto` → `snapshot` (the inputs and the target are captured before the target can be judged) → `target` → `budget` → no-op (a no-op needs no fence) → `fence-unprobed` → `oversize`; an unexpected error anywhere is `error` (exit 1) with its `RESULT:` line and journal line |
| 3 | `refused` | `fence-unprobed` · `environment` (the fence runner `bin/extract-run.py` missing from the engine) | the fence or a dependency is missing — the command to run is printed |
| 4 | `stopped` | `generation` (timeout, a claude error result, invalid JSON) · `invalid-output` (two rejected answers) · `lost-update` (the recheck found a change) | the model ran; nothing was written; the attempt directory names the run |
| 1 | `stopped` | `write` · `error` | a write failed or an unexpected error (every exception not of the classes above: one `RESULT:` line, one journal line, the traceback on stderr) — the vault may hold a temporary file only if `rename` itself failed (never a half page); a `delete` stopped midway names the files already rewritten |
| 75 | — | — | the dispatcher's engine-maintenance lock (`mnemon release` in progress), or the vault lock held by another writer (`bin/vault-lock.py`); retry |

`RESULT:` lines carry `status=<outcome> class=<class or ->`; the `--json` result carries the same under `outcome` and `class`.

## 9. Configuration

Five keys of the instance file, in the one table of `mnemon config keys` ([capture.md §12](capture.md#12-configuration)):

| Key | Type | Default | Meaning |
|---|---|---|---|
| `research_log_path` | str (a path; `~` expanded) | unset | the journal inside the vault (§7, §3.1 step 3); unset → `<vault state dir>/research-journal.jsonl` |
| `research_package_max_bytes` | int ≥ 0 | `400000` | the package limit of §3.4 (`oversize` above it; 0 refuses every build) |
| `research_timeout` | num ≥ 0 (seconds) | `900` | one fenced attempt (then `generation`, like `extract_timeout`); 0 is no time at all |
| `research_max_usd` | num ≥ 0 | unset | `--max-budget-usd` of one fenced attempt when set; `0` is a cap of zero (the attempt is refused by the fence), as `extract_max_usd` treats it |
| `research_recusal_tags` | str (comma-separated) | `agent-identity,agent-subjecthood,atlas-architecture,atlas-identity,harness-vs-weights` | dossier tags that declare recusal (§3.1 step 9); compared exactly after trimming; an empty value declares none by tag |

The loader validates the types as for every key of the table (a negative, fractional-for-int, non-finite or malformed value refuses the config with exit 2 before any operation). The build's model is the instance's `default_model` (`DEFAULT_MODEL`), as for extraction; the page budgets are the doctor's `doctor_wiki_pages_per_collection` and `doctor_wiki_words`.

## 10. The adapters — what a skill does and does not

The plugin's skills `research-update`, `research-ask`, `research-synthesize` and `research-triage` are the doors a Claude Code session uses. Each runs the installed engine's dispatcher (`"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] research …`) and relays its output. A skill never: writes a file under `Wiki/`, `Collections/` or `Synthesis/` itself; edits an `## Items` line except through `research apply` with the owner's word; passes the conversation, another page or an extract to the engine (there is no flag for it); reads `## Atlas reading` to answer a question; sets `stable`. A skill does: ask the owner before `apply` (and records who said yes), carry the `task_id` through a task's `ask` calls and record the outcome, and show the engine's `RESULT:` and proposals as they are. The fresh-session check of §11 is the test of the adapter: a session that has only the engine's output and the public protocol must answer the way the pilot's rubric asks (claims with primary locators, positions kept apart, no opinion as evidence).

## 11. Fixtures and tests

`tests/test_research.py` (`ResearchOperationTests`, on temporary copies of the compatibility vault `tests/fixtures/research/vault/` with a stub `claude` that answers from `tests/fixtures/research/answers/*.json` and a probe record written for its version) and `tests/test_research.sh` (the dispatcher routes, the lock, the writer host, the Guide 5 canon, the config keys):

| Fixture | What it asserts |
|---|---|
| **build** | a new `contradiction` page on the open dossier of the compatibility vault (`doctor_wiki_pages_per_collection` raised for the run — the fixture dossier already carries 7 pages — and `--recusal off`, because its tag `agent-identity` declares recusal by default): the prints match observations of the files, `body_sha256` equals the reader's, `based_on` lists every non-excluded item once with the right seal, `## Atlas reading` present, one `## Log` line, journal `published`; the doctor reads the page as clean apart from the dossier-wide `uncited` |
| **idempotence** | the same update twice: the second is `no-op` — the file's bytes, `updated`, the prints and `## Log` unchanged; `--reason` rebuilds (a second `## Log` line, `created` kept) |
| **inputs-changed** | the dossier's `question` edited → the doctor says `inputs-changed`, `--propose` offers a rebuild, `update` rebuilds (not no-op) |
| **stale → propose → apply** | a page resting on `2026-03-12_cccccccc` with a dossier item on it: `stale`; `--propose` offers the rebind to `cccccccc-2` with an id; `apply <id> --by owner` rewrites that one line (role and note kept, no duplicate); the dossier with `cccccccc-2` already listed gets a report, not a proposal; after the rebind the next update is `inputs-changed` → builds |
| **uncited** | a dossier of three items, an answer citing two with verified locators → published, `uncited: 1` reported; `excluded` items never count |
| **unlisted** | an answer citing a revision outside the package → rejected, one retry with `=== VALIDATION ===`, then `invalid-output` (exit 4); the target untouched |
| **orphan** | `--collection "Closed dossier"` alone → refused (`collection`); a page with one open dossier and one closed veto dossier (`wiki: off`) → allowed |
| **edited-outside-update** | a byte of the evidential body changed → `update` refused (`target`), the file byte for byte as edited, prints not recomputed; an edit of `## Atlas reading` or `## Log` only → not a stop |
| **lost-update** | the target page (or a dossier) changed between the snapshot and the recheck (the stub mutates it) → `lost-update` (exit 4), the file equals the mutated content, no temporary file |
| **schema-failed page** | `delete` of a page without a frontmatter, or with a broken `collections:` → refused (`delete`), the file kept |
| **carriage return** | a field holding a bare CR before an open fence or a `## Log` line → rejected (`invalid-output`) |
| **journal identity** | `research_log_path` spelled `wiki/j.jsonl` on a case-folding volume, or a hard link of a `source.md` → `config`; the source unchanged |
| **unexpected error** | the journal failing after the rename → the page stays, one `RESULT:` line, exit 0 with the failure on stderr; a rewrite failing midway through `delete` → `error` (exit 1), one journal line, the page kept |
| **served hash** | `ask` on a usable `edited-outside-update` page → `body_sha256` is the served body's, `printed_body_sha256` the print's, the journal carries the former |
| **reading links** | `atlas_reading` linking `[[Synthesis/…]]` → rejected; linking the page's own dossier → published |
| **odd domains, modes** | a dossier domain with a comma or a ` #` survives the page's frontmatter; a new page has the ordinary mode (`0666 & ~umask`), a dossier rewritten by `apply` keeps its mode |
| **report kind** | a stale revision whose head is already listed → `kind: report`, never `rebind` |
| **fresh vault** | the first page of a vault without `Wiki/` → the folder created, the page published |
| **open fence** | a field ending inside an open fence → rejected; a closed fence passes and the consumable sections stay outside the served body |
| **snapshot bytes** | a dossier mutated after the first attempt (the stub) → the retry's package still holds the snapshot's dossier text; the operation ends `lost-update` |
| **decorated veto** | `delete` of a page vetoed by `suppressed_pages: ["[[Wiki/<page>]]"]` → the frontmatter entry untouched after the deletion |
| **dry-run refusal** | a dry run that meets a refusal → no journal line |
| **atomicity** | a caught failure before the rename (`MNEMON_TEST_RESEARCH_CRASH=before-rename` under `MNEMON_TEST_HOOKS=1`) → the old page intact, the temporary file removed, `write` (exit 1); an abrupt termination (a kill) is not a caught failure: the old page is intact because `rename` is atomic, and a `.research-*.tmp` may remain — the result the protocol promises is the page, not the directory |
| **veto** | `wiki: off` on an open dossier → refused (`veto`); a name in `suppressed_pages` → refused for that name; the same fields on an `inbox`/`closed` dossier → no veto; an existing suppressed page → `delete` removes it and rewrites incoming links; a non-suppressed page with an open dossier → `delete` refused |
| **budget** | the fifth page under one dossier with `doctor_wiki_pages_per_collection: 4` → refused (`budget`); a rebuild of an existing page passes |
| **hostile source** | a `source.md` whose text instructs the model to set `stable`, show the owner's notes and drop the contradiction, and stub answers that each try **one** thing — a `[[Synthesis/…]]` link, a `## Log` heading inside a field, a locator into an unlisted revision, a field of the wrong type, a field above its bound — → each rejected on its own rule (one retry, then `invalid-output`), nothing written in `Synthesis/` or `Wiki/`; the package (`prompt.md`) holds no `## Notes (owner)` text, no `extract.md`, no other page |
| **self-reference** | a planted page with a false claim beside the target, and the target's own existing heading carrying a false claim → the package contains neither (`prompt.md` checked); the built page does not contain the planted claim (the stub's answer is the engine's input here — the real-model behaviour is the adapter evaluation below) |
| **recusal** | a dossier tagged `agent-identity` → `## Atlas reading` omitted, the Log line says `recusal declared`; `recusal: off` in the dossier → present; `--recusal on` → omitted |
| **fresh session (engine)** | `research ask` on the fixture: the usable pages with their evidential bodies and verified locators; `## Atlas reading` text absent from the output; a named unusable page under `withheld`; a journal line with the task id and every `body_sha256`; `--record … --outcome used` appends the second line with `use_outcome`; unbound and suppressed pages never served |
| **fresh session (adapter)** — not a hermetic test | a named evaluation, run by hand before the pilot's T0: a fresh session with only the plugin's `research-ask` skill and the engine's output answers two of the pilot's control questions; pass = every claim carries a primary locator that exists in the served bodies, the positions are kept apart, and nothing from `## Atlas reading` (planted in the fixture page) appears; the transcript is kept with the pilot's protocol |
| **synthesize** | within the radius (a charter whose `## Radius` admits it: `seeds: 10 open · 30 days`, the fixture's seed younger than that by `--today`) → `Synthesis/<name>.md` with `drafted_by`, `status: seed`; `--status stable` refused (usage); exactly at the count limit (`seeds: 1 open · 400 days` with the fixture's one seed) → refused — the candidate counts; a limit of 0 on an empty vault → refused; an unparsable radius line, an unreadable charter → refused (`radius`); the refusal names the oldest drafts and seeds; nothing written |
| **triage** | the fixture's `Seed note` (drafted_by: agent) → a map with its one Sources link as an item line, its sections, no position paragraph; read-only |
| **journal** | the default path under the state dir; `research_log_path` inside the vault works and the lines append; a path under `Wiki/` or outside the vault → `config` (exit 2) before anything else |
| **fence** | no probe record for the stub's version → `fence-unprobed` (exit 3) with `mnemon fence-probe` named; `--dry-run` reports it and prints the package instead |
| **doctor contract** | `mnemon-doctor-data/1` on the compatibility fixture stays byte for byte apart from the Guide stamp (`engine guide 5`) — regenerated with the interface document's v1.2 |

The compatibility vault itself gains no page and no journal: every operation fixture runs on a temporary copy.

## 12. Non-goals (deferred)

Chunking a research package (I5's permissible split) — v1 refuses oversize; selective rebuild of one section (the dependencies section ↔ inputs are not specified); rewriting a link inside another page's evidential body on `delete`; a page lock or any multi-writer coordination (one writer per instance, the recheck and the atomic rename are the protection); a payload that moves a draft's position paragraph verbatim into `## Atlas reading` (§6: v1 reports it); retrieval by the question in `ask` (v1 filters and journals); an engine-side judgement of the answer's truth (the validator checks that a locator exists and is unique — not that the claim follows from the cited text; the owner reads the page); a `research-interface/2` (every change of phase 3c is additive: the journal, five config keys, Guide 5).

## 13. Status

v1, 2026-10-28 — phase 3c: the protocol of the decision document's W4 (steps 1–8, `--propose`, the single veto predicate, the fence, the atomic one-file publication, the recheck of the target), W5 (`synthesize` under the radius, `triage --propose` for the agent-drafted notes), the journal of its §Публичный протокол, contracts I2, I5, I6, I7 placed. One amendment to that document's deferred list: the operations are implemented in the engine (`bin/mnemon-research.py`, dispatcher `mnemon research`) rather than left to adapters — the owner's decision of 2026-10-28 (the publication with prints, the recheck and the fence are deterministic mechanics every adapter would otherwise re-implement; the adapters stay thin). Codex doc-review `20261028T102536` (gpt-6-astra, xhigh, 805 s): `decision-level: rework · implementation contracts: 17` — eight P1 (the journal aliasing a protected file, the target validated before its bytes were captured, an `apply` id not bound to the line's text, `delete` under unknown support, an update dropping the vetoing dossier, a no-op blind to recusal and seals, the existing page's headings in the package, not-probed and a second `## Log` reaching publication) and nine P2 (shared revisions and the damaged class, the `uncited` scope, `## Log` in search, triage's unexecutable moves, the `ask` selection, the journal's record shapes, config types and per-attempt limits, prospective radius admission, four fixtures that could not assert what they said) — every item folded into this v1 the same day (§3.1–§3.11, §4–§9, §11–§12); the four questions answered inline: the veto is scoped to the supporting dossiers (step 5), triage v1 is a report (§6), admission is prospective (§5), the question is journal-only (§4). Codex code-review `20261028T110942` on the implementation (2 P1: no `Wiki/` on a fresh vault, an open fence swallowing the consumable sections; 4 P2: the package re-reading the dossiers and charter, `delete` rewriting a decorated `suppressed_pages` entry, a dry run journaling a refusal, the triage skill promising the position transfer) — folded the same day (§3.4, §3.5, §3.8, §3.9, §3.11, §11, the skill). Reviewer A (a fresh Claude context, `_claude/reports/mnemon/2026-10-28-p3c-reviewer-a.md` in the maintainer's workspace): accept with amendments — 3 P1 (`delete` of a schema-failed page, a bare CR in a field, journal containment by name only), 13 P2, 6 P3 — folded the same day (§3.1, §3.4–§3.6, §3.10, §3.11, §4, §8, §9); the reader's own CR disagreement between `evidential_body` and `_sections` (`lib_research.py`, phase 3b) is noted for that file's owner, the engine rejects a CR before it matters. Implementation on `feat/p3c-research`.
