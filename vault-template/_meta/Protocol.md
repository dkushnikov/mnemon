<!-- mnemon_guide: 3 -->
# Mnemon Vault Protocol

The store mechanics are defined once, in the engine: `$MNEMON_HOME/docs/specs/2026-10-16-vault-store.md` (entities, ownership, state axes, invariants, the data doctor). This file is the short form a session reads; where the two differ, the spec wins. `mnemon doctor --data` executes the invariants named below.

## Folders

`Sources/YYYY-MM-DD_{hash8}[-n]/` — one folder per **revision** of a resource: the date of capture, the first 8 hex characters of the resource key's sha256 as a label (never the identity — the identity is `resource_sha256` in `source.md`), `-2`, `-3`… for further revisions of the same resource. A folder is never renamed or moved. A re-capture is a **new** folder naming its predecessor in `supersedes:`; the old folder stays.

Three classes of folders: **managed** (`source.md` carries `capture_id`, `revision`, `resource_sha256`, `capture.text_sha256` — published by the engine under the capture contracts), **legacy** (none of them — older captures; matched by URL for deduplication, never rewritten), **damaged** (some of them — a defect the doctor reports as `fail`).

## Who writes what

| File / folder | Written by | May be changed by |
|---|---|---|
| `source.md` | the engine, before any model runs | nobody — the body is the captured text byte for byte (`capture.text_sha256`); an edit is an `integrity-error` |
| `extract.md` | the engine (validated model output) | the engine (re-extraction) and you — **only** these frontmatter fields: `status`, `index_note`, `title`, `tags`, `domains`, `people`, `visibility`, `rating` |
| the archive (originals) | the engine | nobody |
| `index.md` | the engine (`mnemon reindex`) | nobody by hand — a durable note goes to `index_note:` in `extract.md` |
| `Synthesis/` | you (the thinking); an agent only `seed`/`draft` notes marked `drafted_by: <agent>` | you ratify (`status: stable`) |
| `_inputs/` | sessions and producers (incoming material) | sessions (draining) |
| `_meta/` | you (`charter.md`); an agent on request (`reader-context.md`) | per the freshness protocol |

## States

Nothing that can be derived from the files is stored:

- **processing** — `captured` (only `source.md`) or `extracted` (both files); failures live in the engine's ledger, not in the vault;
- **revision** — `current` or `superseded`, from the `supersedes:` graph (`mnemon reindex` marks superseded rows with `⤳`);
- **relations** — `integrated` (linked from a `stable` synthesis note) or `filed` (linked from a seed, a draft or a collection), from the links themselves.

Stored in `extract.md` `status:` are only `extracted` (the engine's default) and `dismissed` (your judgement, phase 3). `tier: bookmark` (phase 3) is a capture fact the engine writes. Older words (`fetch-failed`, `superseded`, `integrated`, `processed`) are legacy and are counted, not rewritten.

Synthesis notes: `status: seed → draft → stable`; a note with `drafted_by: <agent>` and a status other than `stable` is unratified.

## Invariants the doctor checks

`mnemon doctor --data` prints one line per check (`ok|warn|fail <group>/<name> — <detail>`) and never repairs anything; `mnemon status --brief` is the one-line summary for session briefings.

- **store/** — `source-integrity` (managed body hash, required keys, no foreign `_` keys) · `identity` (no two current revisions of one resource) · `index` (rows equal the regenerated index) · `supersedes` (acyclic, no dangling targets) · `archive` (every reference resolves, no unowned files) · `completeness` (every managed url capture records it) · `ledger` (receipts agree with the files) · `status-vocabulary` · `context` (declared in the charter) · `extract-consistency` · `guide-version` (this file's stamp vs the engine).
- **data/** — `thin` (bodies under `doctor_thin_words`, with features and a suspected class) · `arxiv-abstract` · `stubs` · `rating-scale` · `reader-context` (freshness).
- **human/** — `ratification` (unratified agent drafts vs the charter's radius) · `seeds` · `queues` (declared `_inputs` queues vs the radius).

Thresholds: `doctor_*` keys in the instance configuration; the human radius in the charter's `## Radius` section (see the spec §6.3). Queues are declared with `type: queue` and `since: <date>` in their frontmatter and may be parked with `parked: <date>`.

## Doors (what to run)

- capture: `mnemon add --url <url>` / `--file <path>` / text on stdin; a newer version of a resource: `mnemon add --recapture …`
- after an interactive session touched `Sources/` or `Synthesis/`: `mnemon reindex`
- duplicates under canonicalization: `mnemon audit --canonical` (report only)
- the archive: `mnemon audit`
- health: `mnemon doctor`, the data: `mnemon doctor --data`, the one line: `mnemon status --brief`

## Domains

`domains:` is an open, shared vocabulary; a source may carry several. Default set: `learning`, `health`, `relationships`, `home`, `finance`, `career`, `culture`, `influence`, `inner-work`. Add your own in `reader-context.md` / the charter and keep one meaning per word.
