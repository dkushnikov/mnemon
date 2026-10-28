# Vault store — entities, ownership, states, invariants (2026-10-16)

Status: **accepted** (v1.4 — v1.3 after two external document reviews, the first live run and two code reviews; v1.4 adds the research layers: the `Collections/` and `Wiki/` rows of §3.2, the printed observations and the extended `filed` of §4, see [`2026-10-27-research-interface.md`](2026-10-27-research-interface.md)) · Scope: how a Mnemon vault is modelled as a data store, and the observational data doctor (`mnemon doctor --data`, `mnemon status --brief`) that executes the model's invariants. Builds on [`2026-10-15-capture-contracts.md`](2026-10-15-capture-contracts.md) (persistence and isolation, v4.1) and [`2026-10-15-capture-layer.md`](2026-10-15-capture-layer.md) (capture layer, v2.1). Guide version: **4** (the `mnemon_guide` stamp carried by `vault-template/`; 3 = the Guide aligned with §3.2 — agent `seed`/`draft` with `drafted_by` on request, doors per D1, the radius — 2026-10-17; 4 = the four knowledge folders `Sources/` · `Collections/` · `Wiki/` · `Synthesis/` with their doors — 2026-10-27).

## 1. Problem

A vault accumulates state in five places — revision folders, the archive, the per-vault ledger, `index.md`, and the human-written notes around them — and until now the only instrument looked at the engine (dependencies, lock, fence, receipts), never at the data. On a real vault of 640 folders the first census found: 98.7 % of the folders predate the capture contracts (no receipt, no capture block), one word (`status`) carrying three meanings (processing, revision, human judgement), a hand-maintained "integrated" flag set once while 230 sources were actually linked from synthesis notes, 70 index rows silently diverging from the files, a reader context already past its own staleness threshold, and a ratification backlog three times older than anyone had intended. None of that was wrong by any rule, because the rules were prose in five documents that no tool read.

This document makes the rules checkable: it names the entities and who may write them, separates the state axes, lists the invariants, and freezes the contract of a doctor that observes them and never repairs anything.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | **The engine is the only writer** of `Sources/`, `index.md`, the archive and the ledger. Humans and agents write the vault only through declared doors: a curation subset of `extract.md` frontmatter, `Synthesis/`, `_inputs/`, `_meta/`, Bases, the vault's own notes. |
| D2 | **A revision belongs to exactly one resource.** The resource key is the one of the capture contracts §3.1; a legacy revision is keyed by the canonical form of its `url:`; a non-url legacy revision has no key. Legacy `source.md` is never rewritten; a legacy revision becomes managed only through a new revision (`mnemon add --recapture`, `supersedes:`). |
| D3 | **Derivable state is derived, never stored.** Processing (`captured`/`extracted`), revision (`current`/`superseded`) and relations (`integrated`/`filed`) come from the files. Stored are only a human judgement without another artifact (`dismissed`, phase 3) and a capture fact (`tier`, phase 3). `status: extracted` written by the engine is a service value of the `extract/1` schema, not a state. |
| D4 | **One counter.** "Extracted" means folders with `extract.md`; `mnemon status`, `mnemon reindex`, the doctor and `status --brief` read one module (`bin/lib_vault.py`). |
| D5 | **19 named checks, severity by cause.** A managed-schema or graph violation is `fail`; legacy nonconformity, a stale derivative and a crossed threshold are `warn`; a check that cannot run here is reported as *not probed* with its reason, never as a clean pass. |
| D6 | **The doctor only observes.** It writes nothing — not the vault, not the state dir, not the archive, no cache — and prints the door that would fix each finding. It is safe to run from a session-start hook under a time budget. |
| D7 | **Thresholds have two homes and no overlap.** The human ratification radius lives only in the vault charter (`## Radius`, grammar below, parse errors diagnosed); data-quality thresholds live only in the instance configuration (`doctor_*` keys). Engine defaults apply when absent. |
| D8 | **Canon once.** The store mechanics are described here and in `vault-template/` (CLAUDE.md = the Vault Guide, `_meta/Protocol.md`), both stamped with the guide version; a vault's copies only point at this document and add vault-specific policy; the doctor measures a copy that fell behind (I11). |

## 3. Entities and ownership

### 3.1 Folder classes

| Class | Definition | Checks |
|---|---|---|
| **managed** | `source.md` carries all four: `capture_id`, `revision`, `resource_sha256`, `capture.text_sha256` | I1 in full, I6, I7, I9, I10 |
| **damaged managed** | at least one of the four, not all | **fail** (I1) — a partial schema is damage, never legacy |
| **legacy** | none of the four | I1 structurally (header closed within 256 KB, body non-empty); everything else counted |

A revision that lost all four keys is indistinguishable from legacy without external evidence; the evidence is the receipt (I7) and the raw (I5).

### 3.2 Who writes what

| Entity | Where | Created by | Changed by | Deleted by |
|---|---|---|---|---|
| **Resource** (identity) | `resource_sha256`, `canonical_url` in `source.md`; legacy: `url:` | engine | nobody | — |
| **Revision** `Sources/<YYYY-MM-DD>_<hash8>[-n]/` | folder | engine (`add`, `ingest`) | — | nobody; a superseded revision stays |
| `source.md` | in the revision | engine, before any model runs | **nobody** — body = capture-text (`capture.text_sha256`), frontmatter engine-owned | — |
| `extract.md` | in the revision | engine (validated model output) | engine (re-extraction) **and a human or agent in the curation subset only:** `status`, `index_note`, `title`, `tags`, `domains`, `people`, `visibility`, `rating`, and the triage's `dismissed`, `dismissed_why`, `dismissed_from` (v1.5) | — |
| `triage.md` (v1.5) | in a revision **without** `extract.md` (a bookmark card) | `mnemon triage dismiss` (the engine, under the vault lock) | the same door | `mnemon triage restore`; a promotion supersedes the whole revision and the judgement lapses with it |
| **Raw** (originals) | `archive_dir/<rev>.<ext>`, `<rev>.report.json`, `<rev>.site/` | engine | nobody | `mnemon audit` on explicit request only |
| **Receipt** | `<state>/mnemon/vaults/<key>/captures/` | engine | engine | engine (machine-local; rebuilt by the replay rules of the capture contracts §3.5) |
| `index.md` | vault root | engine (`reindex`) | **never by hand** — a durable note goes to `index_note:` | — |
| `Collections/` (phase 3a; rights per field) | vault | the question, `## Notes (owner)` and the veto fields (`wiki`, `suppressed_pages`) — the owner (an agent when asked to create one; in `inbox` a *proposed* question); `## Items` lines — agent · scanner · owner; `status: inbox` — a scanner or an agent on its own initiative, `open` — the owner or an agent on request, `closed` — the owner | per field: an agent or a scanner **only appends** `## Items` lines on its own (an edit of one line the owner approved explicitly — a rebind to a newer revision, a role set to `excluded` — the agent applies without adding a conflicting duplicate); the owner edits or removes any line, the question, the notes, the veto | owner |
| `Wiki/` (phase 3b) | vault | **an agent only**, through the `research-update` operation (generation in the extractor fence from a recorded input package; printed observations `based_on` · `inputs` · `body_sha256`) | the agent, the same operation; **never by hand** — an owner's note goes to a collection's `## Notes (owner)` or to `Synthesis/`; `## Atlas reading` and `## Log` are consumable sections outside `body_sha256` | the owner (adding the page's file name to `suppressed_pages` of a supporting collection); an agent only when the page is `suppressed` or no supporting collection is `open`, after rewriting the incoming links |
| `Synthesis/` | vault | owner (thinking); an agent only `seed`/`draft` with `drafted_by: <agent>` | owner ratifies (`stable`) | owner |
| `_inputs/` | vault | sessions, producers | sessions (draining) | sessions |
| `_meta/` | vault | owner (`charter.md`); agent on request (`reader-context.md`) | per the freshness protocol | — |
| vault notes, Bases, Templates, CLAUDE.md | vault | agent / owner | same | same |

A re-capture's `source.md` names the revision it supersedes — or several revisions of the same resource at once, as a block list, when one `mnemon add --recapture --supersedes <rev>,<rev>…` resolves a group of duplicates (O9, 2026-10-17); the §4 graph and I4 read both forms.

Not checked (stated, not hidden): a human edit of the body or of a non-curation field of `extract.md` — the receipt's `extract_sha256` is ignored after a curation edit by contract, so the doctor has no anchor; whether re-extraction preserves curation fields is undefined today and belongs to phase 3 with triage.

## 4. State axes

| Axis | Values | Source of truth |
|---|---|---|
| processing | `captured` (source.md only) · `extracted` (both files) · `failed` / `extract-failed` / `integrity-error` (ledger only) | file presence; the receipt |
| revision | `current` · `superseded` | the `supersedes:` graph of every `source.md` (both forms: `<rev>` or `Sources/<rev>/extract`; one value or a block list — a revision may supersede several revisions of one resource, each named one is superseded) |
| relations | `integrated` · `filed` · (`promoted`, phase 3) | wikilinks from `Synthesis/`, the `## Items` of `Collections/` and the `based_on` of `Wiki/` resolved to a revision id |
| judgement | `dismissed` (phase 3-capture) | stored in `extract.md` `status:` (+ `dismissed:`, `dismissed_why:`); for a revision without `extract.md` — a bookmark card — in `Sources/<rev>/triage.md` (`type: triage`, `status: dismissed`, `dismissed`, `why`). About the **resource**, carried by its latest revision (O6): a promotion supersedes the carrier and the judgement lapses with it. The door is `mnemon triage dismiss | restore` |
| tier | `full` (absent = full) · `bookmark` (phase 3-capture) | stored by the engine at capture (`tier: bookmark` in `source.md`); the *effective* tier also reads the legacy placeholder `status: bookmark` as bookmark |

**Stored `status:` values in `extract.md`:** `extracted` (service value) · `dismissed` (judgement, phase 3). **Allowed legacy words** (counted apart, never rewritten by the engine): `fetch-failed`, `superseded`, `integrated`, `processed`, `bookmark`, empty. Anything else is *unknown* and reported.

**Derived values and their resolver:**

| Derived | Rule |
|---|---|
| `superseded(rev)` | `rev` is named in the `supersedes:` of any `source.md` |
| `current(rev)` | not superseded |
| `integrated(rev)` | ≥ 1 note in `Synthesis/` with `status: stable` links `rev`; a link to a superseded revision counts for that revision and is **not transferred** to its successor |
| `filed(rev)` | a seed/draft note links `rev`, **or** `rev` is an item of any collection's `## Items` (any `status`, any role — an `excluded` item was *considered*, so it is filed), **or** `rev` is named in a wiki page's `based_on` (phase 3b); `integrated` wins — a revision a `stable` note links is not counted as filed. A malformed `## Items` line files nothing. Extended in v1.4 (phase 3a). |
| `promoted(rev)` (phase 3-capture) | `rev` is of effective tier bookmark and has a `supersedes` descendant (transitively) of effective tier full — a full `mnemon add` of a bookmarked resource makes one (the promotion, capture layer §13 B2); counted as `promoted`, with `tier: {full, bookmark}` and `triage: {bookmarked, dismissed}` (current bookmarks without / with the judgement), additively in the counters |
| link resolver | `[[Sources/<rev>/extract…]]`, `[[Sources/<rev>/source…]]`, `[[Sources/<rev>…]]` and the same inside `![[…]]` → `<rev>`; basename-only links (`[[extract]]`) and other forms are **unresolved** and counted |

**Printed observations (v1.4) — the third stored class.** Besides a judgement and a capture fact, a vault stores an *observation made at publication* that no file can yield later: a wiki page's `based_on` (the revisions it rests on, each with a text fingerprint `text_sha256` and a `seal` — `capture` when it equals the managed revision's `capture.text_sha256`, `observed` for a legacy revision), `inputs` (the build package: the page key, `kind`, the hashes of the collections and the charter it used, the protocol and template versions) and `body_sha256` (the evidential body). A print does not break D3: its *current* value is recomputed from the files; what is stored is the past observation — exactly like `capture.text_sha256` of `source.md`. Everything else about a research document is **derived and never stored**: the states `stale` · `inputs-changed` · `uncited` · `orphan` · `edited-outside-update` · `weak-evidence` · `unbound` (with the reason `unlisted` among others) · `suppressed` · `budget`, and the owner's veto predicate, are computed by the doctor's `research` document next to `mnemon-doctor-data/1` (phase 3b). A collection carries no prints: its stored `status` and the derived `filed` of its items are all there is. The schemas, the `## Items` grammar, the reader records and the search and index contracts are frozen in [`2026-10-27-research-interface.md`](2026-10-27-research-interface.md).

**Synthesis notes:** `status: seed → draft → stable`. *Unratified* = `drafted_by` non-empty **and** `status ∈ {draft, empty}` (seeds are measured apart; `stable` is ratified). A note with neither `drafted_by` nor `status` has unknown authorship: reported, excluded from the radius, never promoted by rule.

## 5. Invariants — the checks

Stable names; `group/name`. Severity by cause (§6.1).

| # | Name | Invariant and what is counted | Severity | Fix door |
|---|---|---|---|---|
| I1 | `store/source-integrity` | managed: body hash (`cap_py body-sha256`: the bytes after the first `\n---\n` of a file starting with `---\n`) = `capture.text_sha256`; the four keys present; no top-level key starting with `_`. Damaged managed → fail. Legacy: header closed, body non-empty; `_` keys → warn | fail (managed) · warn (legacy) | operator (restore the immutable file) |
| I2 | `store/identity` | **current** revisions grouped by `resource_sha256` (managed, decisive) and by the current canonical form (v2 since 2026-10-28; v1 before) of `url:`/`canonical_url:` (every folder with a URL); a group of ≥ 2 current = duplicate — **fail when a managed revision is among them** (the engine's dedup was bypassed), **warn when all are legacy** (pre-contract captures, `legacy-only N`), **warn as `pre-rule N`** when the members were distinct identities under the canonicalization they were captured with (`capture.canonical_version`, v1 when absent) and coincide only under the current rules (the rule moved; `mnemon audit --canonical` is the door); one `resource_sha256` with different `canonical_url` → `inconsistent`; non-url legacy → `unkeyed N` | fail (duplicate with managed) · warn (legacy-only, inconsistent) | `mnemon audit --canonical` → `--recapture --supersedes` |
| I3 | `store/index` | rows of `index.md` = in-memory regeneration by the reindex module (exact text, suffixes included): `differ / missing / extra`; the **current** size of the newest `_inputs/index-hand-edits-*.md` (growth is not derivable from one snapshot) | warn | `mnemon reindex` |
| I4 | `store/supersedes` | acyclic; every target exists; **cross-resource edges** (different keys) counted apart (allowed through an explicit `--supersedes`, always visible — a `source.md` naming several revisions is one edge per name, each judged on its own); fan-out counted; stored `superseded` words = legacy. On a cycle or a dangling edge the `current` counter is marked incomplete | fail (cycle, dangling) · warn (cross-resource, legacy words) | operator |
| I5 | `store/archive` | `archive:` resolves — forms `<name>`, `<name> (in archive_dir: …)` (suffix dropped), `unarchivable` (legacy marker, skipped); managed with `capture.raw_store: archive` and no raw → fail; owners of archive entries: `<rev>.<ext>` by `archive:`, `<rev>.report.json` and `<rev>.site/` of an existing revision folder; everything else unowned | fail (managed without raw) · warn (unresolved reference, unowned entries) · `archive_dir` unset → ok, not probed · set but unreachable → warn, not probed | `mnemon audit` |
| I6 | `store/completeness` | managed url revisions: field missing or value ∉ `complete · partial · blocked · unknown` → fail; distribution; `blocked` → warn; legacy url revisions = "without a record N" (never merged with `unknown`) | fail · warn · ok | `mnemon add --recapture` |
| I7 | `store/ledger` | receipts vs published files: a receipt in `extracted`/`source-published` contradicting the files (`text_sha256`, missing folder) → fail (the engine's `integrity-error`); a managed revision without a receipt while a ledger exists → warn (state loss, rebuilt by replay); no ledger: with no managed revision at all → ok, not probed (`no ledger yet — nothing captured`); otherwise the only evidence of the writer host is the instance's `writer_host` — set and equal to this host → warn, not probed (`state lost?`); set and different, or unset → ok, not probed; `started`/`failed`/`extract-failed` receipts → `incomplete N` | fail · warn · not probed | `mnemon add --resume` / operator |
| I8 | `store/status-vocabulary` | `extract.status` ∈ {extracted, dismissed} · allowed legacy list · empty (legacy) · unknown words; `synthesis.status` ∈ {seed, draft, stable} · empty · unknown. An empty `status` on a **managed** `extract.md` is a schema violation counted here and failed in I10 | warn | a vault-side normalization (owner's call) |
| I9 | `store/context` | managed `context:` ∈ the charter's `## Contexts` (a charter changed after capture → warn); legacy `capture_context.vault` is another namespace — counted, not compared | warn | charter |
| I10 | `store/extract-consistency` | `extract.md` without `source.md` → fail; managed pair disagreeing on `revision` → fail; a differing `capture_id` is a **recovery alias** when the ledger holds an alias receipt resolving to the source's id or revision (ok, counted), a warn without a ledger to confirm it, a fail when the ledger has no such receipt; a managed `extract.md` without `status` (schema `extract/1`) → fail; `url` differing → warn; none of the five sections (`Summary`, `Executive Summary`, `Key Ideas`, `Connections`, `Raw Quotes`; `##` or `###`) → warn | fail · warn | operator / re-extraction |
| I11 | `store/guide-version` | `mnemon_guide:` stamp of the vault's `CLAUDE.md` and `_meta/Protocol.md` vs the installed engine's `vault-template/` version: behind, ahead (a copy newer than the engine), unstamped or a Guide file missing → warn | warn | update the Guide copies (vault operation) |
| Q1 | `data/thin` | current, effective tier full, `source_type` (lower-cased) ∉ {idea, message, conversation, voice-note, x}, body words (whitespace tokens after the frontmatter) < `doctor_thin_words`; per item the **features** (`unarchivable`, stub < `doctor_stub_words`, arXiv host, media type, website/repo/document type, `completeness` partial/blocked/none) and a **suspected class** (landing/stub · arxiv-abstract · media · website/repo/document · article-short · unknown); sorted rating ↓, words ↑. The short-type exclusion applies to the length test only — `partial`/`blocked` revisions are always listed | warn | `mnemon add --recapture` from `--list thin` |
| Q2 | `data/arxiv-abstract` | **suspicion**: current, host `arxiv.org`, not bookmark, words < `doctor_arxiv_words` | warn | `mnemon add --recapture` |
| Q3 | `data/stubs` | OR of `source.md` features: `fetch_status` starting with `failed`, words < `doctor_stub_words`; unique revisions, features listed; independent of any word in `extract.md`. Revisions of effective tier bookmark (`tier: bookmark`, or the legacy `status: bookmark`) are **excluded** and counted (`bookmarks_excluded`): a card is short by design and has its own door | warn | recapture; bookmarks → `mnemon triage` |
| Q4 | `data/rating-scale` | **user threshold**, not an invariant: share of current revisions with a numeric rating ≥ 7 vs `doctor_rating_hi_share` (%); coverage shown; the cause is not attributed | warn | the charter's value scale / a rating review |
| Q5 | `data/reader-context` | `synced:` older than the file's `staleness_threshold_days` (default 14; invalid → default, noted), strict `>`; missing file/field or a future date → warn "no freshness data" | warn | the reader-context freshness protocol |
| H1 | `human/ratification` | unratified notes (§4): count and age by `created:` (missing/invalid → mtime, noted) vs radius `drafts`; `unknown N` apart | warn | ratification (owner) |
| H2 | `human/seeds` | `status: seed` (any author): count and oldest age vs radius `seeds` | warn | ratification · collections (phase 3) |
| H3 | `human/queues` | **declared** queues: `_inputs/*.md` with frontmatter `type: queue` and `since: <date>`; items = `^- ` lines; age = today − `since`; `parked:` per §6.3; undeclared `.md` files (except `index-hand-edits-*.md`) → `untyped N` = **warn** (coverage the vault must restore: declare or remove; `probed: true`); `pending-writes/` → `legacy N` | warn (age over radius · undeclared files) · ok (no queues, nothing undeclared) | drain · park · declare |

## 6. The doctor

### 6.1 Severity by cause

| Cause | Severity |
|---|---|
| managed schema violated (I1 hash/keys/`_` keys, I5 raw, I6 field, I10 mismatch, I7 receipt contradicting files) | **fail** |
| graph violated (I2 duplicate current revisions including a managed one, I4 cycle or dangling) | **fail** |
| legacy nonconformity (`_` keys in legacy, legacy status words, suffix-form archive references, cross-resource edges, legacy-only duplicate groups) | warn |
| a derivative is stale (I3 drift, I11 guide version) | warn |
| a threshold is crossed (Q1–Q5, H1–H3) | warn |
| not probed, but the configuration says the resource should exist (archive dir set but missing; a writer host without a ledger) | warn, `probed: false` |
| not probed because not configured or not this host (no archive dir; not the writer) | ok, `probed: false` |
| information (distributions, coverage) | ok |
| **execution diagnostics** — a tool problem, not a check: `radius-parse` (a charter line did not parse), `error` (an exception inside a check), `budget` (the brief's deadline) | warn / fail, outside the 19 checks, in `diagnostics`; printed as `warn doctor/radius-parse — …`, `fail doctor/error — …` |

### 6.2 Interfaces

- `mnemon doctor --data [--json] [--list <check>]` — the engine checks as today, then the data group; text lines `ok|warn|fail <group>/<name> — <detail>` for the 19 checks plus `doctor/<code>` lines for diagnostics; `--json` is the existing envelope `{"checks":[…],"exit":N}` extended: `checks` = the engine checks under their old names + the 19 data checks (`group/name`, `status`, `detail`) + diagnostics as `doctor/<code>`; `"data"` = the whole `mnemon-doctor-data/1` document; `exit` = 1 on any `fail` anywhere (engine, data or `doctor/error`), else 0. `--list` accepts the full name or the part after `/` and prints one item per line (`<id>\t<class or reason>\t<rating>\t<words>`). An exception inside a check becomes a `doctor/error` diagnostic (fail) and `snapshot.complete: false`; the other checks are not lost.
- `mnemon status --brief [--json] [--budget <seconds>]` — exactly one physical line with the shared counters and the data summary, for session-start hooks: `<instance> · N sources (m managed · c current · s superseded) · e extracted · k synthesis (seeds / oldest d · drafts / oldest d) · data: f fail · w warn · probed p/19`. The budget (default 10 s) is a **deadline around the whole invocation**: the scan, the archive and ledger listings, the index regeneration and the checks run in a child worker process; the parent waits at most the budget and kills the worker when it expires (a blocking filesystem call is cut too), then prints `data: not probed (budget …)` and records a `budget` diagnostic. A held vault lock makes the line say `(snapshot may be partial)`. Never `0 fail` without coverage.
- `mnemon status` — the dashboard; `Extracted` from the shared module, plus `By status: extracted · dismissed · legacy words · empty · unknown`.
- Direct tool: `bin/vault-doctor.py --vault <path> [--archive <dir>] [--charter <file>] [--reader-context <file>] [--state-dir <dir>] [--instance <name>] [--writer-host <host>] [--guide-version <n>] [--today YYYY-MM-DD] [--json | --list <check> | --brief [--budget s]]`.

JSON document `mnemon-doctor-data/1` (frozen):

```json
{"schema": "mnemon-doctor-data/1", "instance": "<name>", "vault": "<path>", "generated_at": "<UTC ISO>",
 "snapshot": {"lock_held": false, "unreadable": 0, "budget_s": null, "complete": true},
 "counters": {"folders": 0, "managed": 0, "damaged": 0, "legacy": 0, "current": 0, "superseded": 0,
              "extracted": 0, "captured": 0, "synthesis": 0, "seeds": 0, "drafts_unratified": 0},
 "checks": [{"name": "store/source-integrity", "status": "ok", "probed": true, "reason": null,
             "detail": "…", "counts": {}, "threshold": {}, "fix": "operator",
             "items": [{"id": "<revision | path>", "rating": null, "words": null, "reason": "…"}]}],
 "diagnostics": [{"code": "radius-parse | error | budget | unreadable", "level": "warn | fail", "message": "…"}],
 "summary": {"checks": 19, "ok": 0, "warn": 0, "fail": 0, "probed": 19, "not_probed": 0,
             "diagnostics_warn": 0, "diagnostics_fail": 0}}
```

`checks` always holds exactly the 19 named checks (not-probed ones included); an item always carries `id`, `rating`, `words`; most checks add `reason`, `data/thin` adds `features` and `class`, and a check may add its own fields (`members`, `age_days`, `items`); a not-probed check keeps the status its cause assigns (`ok` or `warn`) with `probed: false` — read `probed` before `status` (under a budget kill all 19 are `ok` and not probed; the brief line and `summary.probed` say so); `diagnostics` is a separate array for tool problems (`unreadable`: files that could not be read, the snapshot is then incomplete); `summary.ok/warn/fail` count checks only and `diagnostics_warn/fail` the diagnostics, so the text output equals `checks` plus `diagnostics` line for line. `items` are complete in `--json` and truncated to a few in the text detail.

### 6.3 Thresholds, the radius and parked queues

Configuration keys (instance file; engine defaults): `doctor_thin_words` 300 · `doctor_stub_words` 40 · `doctor_arxiv_words` 600 · `doctor_rating_hi_share` 20.

Charter section (`_meta/charter.md`, charter schema 2), lines until the next heading, code fences ignored:

```
## Radius
seeds: 10 open · 30 days
drafts: 5 unratified · 45 days
queues: 30 days
```

One form per key, anything else with that key is a `radius-parse` diagnostic (defaults for that key): `^seeds:\s*(\d+)\s+open\s*[·,;]\s*(\d+)\s+days?\s*$` (both numbers required) · `^drafts:\s*(\d+)\s+unratified\s*[·,;]\s*(\d+)\s+days?\s*$` (both required) · `^queues:\s*(\d+)\s+days?\s*$` (age only — there is no limit on the number of queues). Integers; days; ages compare with a strict `>`; a missing section or line → engine defaults (10/30, 5/45, 30) and `(defaults)` in the detail; a line that does not parse → `warn doctor/radius-parse — cannot parse "<line>" (defaults used)`, never silent.

A queue is a file `_inputs/*.md` whose frontmatter declares `type: queue` and `since: <YYYY-MM-DD>` (the arrival date of the oldest open item; a draining session moves it to the next oldest). `parked: <date>` (+ optional `parked_until: <date>`, `parked_why: <text>`) parks the **whole file**, new items included, until `parked_until` (indefinitely without it); parking does not change `since`; parked queues are counted apart and stay visible.

### 6.4 Snapshot safety

Read-only; the vault lock is probed non-blocking on a private descriptor — taken and released at once, never created (a writer acquiring in that instant sees `EWOULDBLOCK`, as with `vault-lock.py --probe`); a lock file that cannot be opened is a warn diagnostic with `lock_held: null`; a file that vanishes or cannot be read is counted as `unreadable`, never raised; the only files the tool writes are python's bytecode caches under the engine checkout (`__pycache__`, gitignored); the brief runs under an end-to-end deadline in a killable worker process (verified with a deliberately stalled worker through the test hook `MNEMON_TEST_DOCTOR_STALL`); the full `doctor --data` has none. No cache in this version (a cache needs a writing owner; revisit near 2 000 folders).

## 7. Legacy policy

Match, never migrate: legacy revisions take part in lookups and dedup (the capture contracts' `legacy_match`), their `source.md` is never rewritten, and nothing synthesizes a `capture` block, a receipt or a completeness value for them. Counters show legacy apart (`legacy`, `without a record`, `unkeyed`). The curation subset of a legacy `extract.md` may be normalized by the vault's owner as an ordinary vault operation with a before/after report; the machine facts the doctor relies on (`fetch_status`, body length) live in `source.md` and survive it.

## 8. Canon

| What | Where (single place) | Maintainer |
|---|---|---|
| entities, axes, invariants, doctor contract | this document (`$MNEMON_HOME/docs/specs/2026-10-16-vault-store.md`) | engine |
| the Vault Guide for sessions (rules, doors, stamp `mnemon_guide`) | `vault-template/CLAUDE.md`, `vault-template/_meta/Protocol.md` — copied by `mnemon init` | engine |
| the vault's human intent: purpose, contexts, value scale, radius | `<vault>/_meta/charter.md` | the owner |
| vault-specific policy (domains, credibility checks, filing) | the vault's own `_meta/Protocol.md` and `CLAUDE.md`, pointing here | the vault |

`protocols/status-lifecycle.md` and `protocols/storage.md` are pointers to this document from this version on. The doctor cannot establish *who* changed a file; authorship discipline stays with the Guide and the vault's version control.

## 9. Acceptance

Automated in `tests/test_vault_doctor.sh` under bash 3.2 / python 3.9 and the current toolchain: every fail path (edited managed body, `_` key on managed, partial managed keys, duplicate current revisions by both keys, cycle, dangling, receipt contradicting files, extract without source, managed pair mismatch), every warn path (index drift and the hand-edits report, suffix-form and `unarchivable` references, unowned archive entries, stored `superseded`, legacy and unknown status words, undeclared context, missing sections, guide stamp absent/behind, thin features and classes with a short type excluded from length but listed for `partial`, suspected arXiv abstract with superseded and bookmark excluded, stubs with two features, rating coverage, reader-context without data, radius from the charter / defaults / an unparsable line, unknown authorship outside the radius, declared vs undeclared vs parked queues with `since` older than mtime), the empty-vault / no-`Synthesis/` / no-`_inputs/` / no-archive cases (ok lines, exit 0), the observational proof (mtime and inode snapshot of the vault, state dir and archive unchanged; no new files), the frozen JSON (schema, exactly 19 names in `checks`, `summary` equal to the check lines, diagnostics apart, coverage counts, the combined `doctor --data --json` envelope), `--list`, the one-line brief whose numbers equal the JSON, the budget path including a stalled worker killed at the deadline, the `writer_host` cases of I7, a managed extract without `status`, a charter line in another key's form, the dispatcher's exit codes, `--today`, and the regenerated `index.md` Commands block without retired commands. Manual, once per release: the brief and the full report on a real vault, compared with the previous release's numbers.

## 10. Non-goals (deferred)

Collections — phase 3a (the research interface, v1.4); the wiki layer and the doctor's `research` document — phase 3b; `tier: bookmark`, `mnemon triage`, `dismissed` and its scope — shipped in phase 3-capture (v1.5; capture layer §13). Preserving curation fields across re-extraction — phase 3. Materializing derived values into `extract.md` for Bases — phase 3. Migrating legacy frontmatter — never (match, don't migrate). A frontmatter hash for `source.md` — a separate contract change if editor mutations recur. A doctor cache — when a vault nears 2 000 folders. Automatic draining or recapture from the doctor's lists — the doctor observes; `mnemon add --recapture --from <list>` is a phase-3 candidate.

## 11. Status

v1.5, 2026-10-28 — phase 3-capture folded in: the judgement row of §4 names its two carriers (`extract.md` status, `triage.md` for a revision without an extract) and its scope (the resource, on the latest revision — O6), the tier row the effective tier, `promoted(rev)` its rule and the additive counters (`tier`, `promoted`, `triage`); Q3 excludes bookmark-tier revisions; `store/ledger` accepts the `bookmarked` receipt state; the brief shows the bookmark queue. The executable contract is [`2026-10-15-capture-layer.md`](2026-10-15-capture-layer.md) §13.

v1.4, 2026-10-27 — the research layers folded in (phase 3a): the `Collections/` row of §3.2 given its per-field rights and a `Wiki/` row added; §4 gains the printed observations and the extended `filed`; Guide 4. The decision document behind it (four layers, the evidence contract, the doors, the pilot) is the maintainer's; the executable contract is [`2026-10-27-research-interface.md`](2026-10-27-research-interface.md).

v1.3, 2026-10-17 — after two external document reviews (rework → accept with amendments), the first live run (legacy-only duplicate groups warn) and two code reviews (the alias rule of I10, the lock and cache wording of §6.4); every blocking issue, contradiction and gap folded in: the three folder classes, the four axes with a link resolver, severity by cause with explicit coverage and a separate diagnostics channel, the frozen JSON and the combined envelope, one radius form per key with parse diagnostics, declared queues, `writer_host` as the only writer evidence, an end-to-end brief deadline). Implementation on `feat/p2.5-data-doctor`.
