<!-- mnemon_guide: 5 -->
# Mnemon Vault Protocol

The store mechanics are defined once, in the engine: `$MNEMON_HOME/docs/specs/2026-10-16-vault-store.md` (entities, ownership, state axes, invariants, the data doctor) and, for the research layers, `$MNEMON_HOME/docs/specs/2026-10-27-research-interface.md` (collections, the `## Items` grammar, the wiki contract). This file is the short form a session reads; where the two differ, the spec wins. `mnemon doctor --data` executes the invariants named below.

## Folders

Four folders of knowledge, one question each:

- `Sources/` — **what was captured.** `YYYY-MM-DD_{hash8}[-n]/`, one folder per **revision** of a resource: the date of capture, the first 8 hex characters of the resource key's sha256 as a label (never the identity — the identity is `resource_sha256` in `source.md`), `-2`, `-3`… for further revisions of the same resource. A folder is never renamed or moved. A re-capture is a **new** folder naming its predecessor in `supersedes:`; the old folder stays.
- `Collections/` — **which revisions bear on a question.** One note per dossier: a `question`, the revisions it gathers as `## Items` lines with a role and a note, the owner's veto over derived pages. Filing, not thinking.
- `Wiki/` — **what the evidence says about a topic now.** Derived pages an agent builds from a recorded input package; each carries printed observations (`based_on` with a text fingerprint and a seal per revision, `inputs` — the build package, `body_sha256` — the evidential body) the doctor verifies, and locators into `source.md`. Never edited by hand.
- `Synthesis/` — **the owner's position.** `status: seed → draft → stable`; a `seed` is the owner's first thought, a `stable` note is ratified.

Three classes of `Sources/` folders: **managed** (`source.md` carries `capture_id`, `revision`, `resource_sha256`, `capture.text_sha256` — published by the engine under the capture contracts), **legacy** (none of them — older captures; matched by URL for deduplication, never rewritten), **damaged** (some of them — a defect the doctor reports as `fail`).

## Who writes what

| File / folder | Written by | May be changed by |
|---|---|---|
| `source.md` | the engine, before any model runs | nobody — the body is the captured text byte for byte (`capture.text_sha256`); an edit is an `integrity-error` |
| `extract.md` | the engine (validated model output) | the engine (re-extraction) and you — **only** these frontmatter fields: `status`, `index_note`, `title`, `tags`, `domains`, `people`, `visibility`, `rating` |
| the archive (originals) | the engine | nobody |
| `index.md` | the engine (`mnemon reindex`) | nobody by hand — a durable note goes to `index_note:` in `extract.md` |
| `Collections/` | the question, `## Notes (owner)` and the veto (`wiki`, `suppressed_pages`) — you (an agent when you ask it to open one; a scanner or an agent files an `inbox` dossier with a *proposed* question); `## Items` lines — an agent, a scanner, you | per field: an agent or a scanner only **appends** items on its own; you edit or remove any line, the question, the notes, the veto; `open` and `closed` are yours (`inbox` is theirs) |
| `Wiki/` | an agent only, through `mnemon research update` (`$MNEMON_HOME/docs/research.md`: built in the extractor's fence from the dossiers, the charter and the items' `source.md`; validated; one atomic write with the prints) | the agent, the same operation — **never by hand** (`edited-outside-update`, and the next update stops); your note goes to the collection's `## Notes (owner)` or to `Synthesis/`; you delete a page by naming it in `suppressed_pages` of an open supporting dossier, or `wiki: off` — it becomes `suppressed`, leaves search, and `mnemon research delete <page>` removes it |
| `Synthesis/` | you (the thinking); an agent only `seed`/`draft` notes marked `drafted_by: <agent>`, on explicit request | you ratify (`status: stable`) |
| `_inputs/` | sessions and producers (incoming material) | sessions (draining) |
| `_meta/` | you (`charter.md`); an agent on request (`reader-context.md`) | per the freshness protocol |

## A collection

```
---
type: collection
status: open            # open | inbox | closed — pages are derived only under open
question: "…"           # the research question; proposed in inbox, confirmed when you open it
curated_by: owner       # who filed it: owner, an agent name, scanner:<name>
created: YYYY-MM-DD
wiki: on                # off = no derived page under this dossier (veto)
suppressed_pages: []    # page file names you removed and forbid rebuilding
---
## Items
- [[Sources/<rev>/extract]] — **<role>** — <note>
- [[Sources/<rev>/extract]] — **excluded** — considered, not cited
```

One `## Items` line = one revision, exactly one `[[Sources/<rev>…]]` link; the role is the first `**…**` after the dash, the rest is the note; a nested line without a link continues the note. Two links on a line, a link outside `Sources/`, `..` in a target, free text between items or a second `## Items` heading fail the dossier (the reindex row, `python3 $MNEMON_HOME/bin/lib_research.py --vault <path>` and the doctor's `research/collections` line say so). A nested line with a Sources link is an item of its own; a nested line with any other link is malformed — a cross-reference belongs in `## Why` or `## Open questions`. An item pins its revision: a re-captured source stays pinned to the old revision until you (or an agent on your word) rebind the line.

## States

Nothing that can be derived from the files is stored:

- **processing** — `captured` (only `source.md`) or `extracted` (both files); failures live in the engine's ledger, not in the vault;
- **revision** — `current` or `superseded`, from the `supersedes:` graph (`mnemon reindex` marks superseded rows with `⤳`);
- **relations** — `integrated` (linked from a `stable` synthesis note) or `filed` (linked from a seed or a draft, an item of any collection — `excluded` included, it was considered — or the `based_on` of a wiki page), from the links themselves.

Stored in `extract.md` `status:` are only `extracted` (the engine's default) and `dismissed` (your judgement; for a bookmark card without an extract it lives in `triage.md` beside `source.md`). `tier: bookmark` is a capture fact the engine writes — a bookmark is a card, not a read: `mnemon triage list` is its queue, `mnemon triage promote <revision>` (or a full `mnemon add` of the same URL) captures it in full, `mnemon triage dismiss <revision>` records your judgement about the resource on its latest revision. Older words (`fetch-failed`, `superseded`, `integrated`, `processed`) are legacy and are counted, not rewritten. A wiki page stores its **printed observations** — what it rested on when it was built — and the doctor derives its states from them, never storing one: `unbound` and `suppressed` are fails (the page is no proven ground: out of search, out of any answer), `edited-outside-update` · `stale` · `inputs-changed` · `orphan` · `weak-evidence` · `budget` are warnings, `uncited` is a dossier item no verified locator of its pages cites; a page whose dependencies could not be read is `not probed`, never clean. The veto is one predicate: an **open** supporting dossier with `wiki: off`, or naming the page in `suppressed_pages` — `inbox` and `closed` dossiers veto nothing (their pages are `orphan`).

Synthesis notes: `status: seed → draft → stable`; a note with `drafted_by: <agent>` and a status other than `stable` is unratified.

## Invariants the doctor checks

`mnemon doctor --data` prints one line per check (`ok|warn|fail <group>/<name> — <detail>`) and never repairs anything; `mnemon status --brief` is the one-line summary for session briefings.

- **store/** — `source-integrity` (managed body hash, required keys, no foreign `_` keys) · `identity` (no two current revisions of one resource) · `index` (rows equal the regenerated index) · `supersedes` (acyclic, no dangling targets) · `archive` (every reference resolves, no unowned files) · `completeness` (every managed url capture records it) · `ledger` (receipts agree with the files) · `status-vocabulary` · `context` (declared in the charter) · `extract-consistency` · `guide-version` (this file's stamp vs the engine).
- **data/** — `thin` (bodies under `doctor_thin_words`, with features and a suspected class) · `arxiv-abstract` · `stubs` · `rating-scale` · `reader-context` (freshness).
- **human/** — `ratification` (unratified agent drafts vs the charter's radius) · `seeds` · `queues` (declared `_inputs` queues vs the radius).
- **research/** — its own document next to the data checks (`mnemon-doctor-research/1`): `collections` (schema and grammar, unresolved items) · `collection-age` (open dossiers, informational) · `wiki-schema` · `wiki-binding` (`unbound`, `suppressed`, the locators) · `wiki-stale` · `wiki-inputs` (`edited-outside-update`, `inputs-changed`) · `wiki-evidence` (`uncited`, `weak-evidence`) · `wiki-orphan` · `wiki-budget` (`doctor_wiki_pages_per_collection`, `doctor_wiki_words`) · `index` (the `## Collections` and `## Wiki` sections). A research `fail` joins `doctor --data`'s exit 1; `status --brief` adds `research: N pages · K fail · M warn`.

Thresholds: `doctor_*` keys in the instance configuration; the human radius in the charter's `## Radius` section (see the spec §6.3). Queues are declared with `type: queue` and `since: <date>` in their frontmatter and may be parked with `parked: <date>`.

## Doors (what to run)

- capture: `mnemon add --url <url>` / `--file <path>` / text on stdin; a newer version of a resource: `mnemon add --recapture …`
- after an interactive session touched `Sources/`, `Collections/`, `Wiki/` or `Synthesis/`: `mnemon reindex`
- search: `mnemon search --query <q>` (Sources); `--include-collections`, `--include-synthesis` widen it; `--include-wiki` adds a separate block of usable pages (`--include-orphans`, `--wiki-limit`)
- validate the dossiers and the pages: `python3 $MNEMON_HOME/bin/lib_research.py --vault <path>` (exit 1 names every malformed line and every failing page); the doctor's `research/` lines: `mnemon doctor --data`, `--list research/<check>`
- build or rebuild a page: `mnemon research update --page <name> --kind concept|comparison|contradiction|open-question --collection <dossier>… [--reason <why>]` (an unchanged package is a no-op; a hand-edited page stops it); what the pages need: `mnemon research update --propose` (a stale page → a rebind proposal; `mnemon research apply <id> --by <who>` applies one on your word); a vetoed or orphan page: `mnemon research delete <page>`
- answer from the evidence: `mnemon research ask --task-id <id> --question <q>` serves the usable pages' evidential bodies (never `## Atlas reading`); `--record --page <name> --outcome used|not-used|no-page` records the use
- a draft of your thought, on your request and inside the radius: `mnemon research synthesize --name <note> --status seed|draft < body.md`; the agent-drafted notes mapped for your verdict: `mnemon research triage --propose`
- the journal of these operations: `research_log_path` in the instance config (inside the vault, never under `Wiki/`), else the vault's state directory
- duplicates under canonicalization: `mnemon audit --canonical` (report only)
- the archive: `mnemon audit`
- health: `mnemon doctor`, the data: `mnemon doctor --data`, the one line: `mnemon status --brief`

## Domains

`domains:` is an open, shared vocabulary; a source may carry several. Default set: `learning`, `health`, `relationships`, `home`, `finance`, `career`, `culture`, `influence`, `inner-work`. Add your own in `reader-context.md` / the charter and keep one meaning per word.
