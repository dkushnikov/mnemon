# Research interface — collections, the reader, index, search, fixtures (2026-10-27)

Status: **v1.1** (phase 3b: the wiki layer, the doctor's `research` document, the search block — on v1, phase 3a's exit criterion) · Scope: the executable contract between the engine's shared files and the branches that build on them — the bookmark tier (phase 3-capture), the wiki layer (phase 3b, this version) and the research operations (phase 3c). It freezes what a `Collections/*.md` document is (schema `collection/1`), how its `## Items` lines are read, what a `Wiki/*.md` page is (schema `wiki/1`: its prints, its evidential body, its locators, the states the doctor derives and the one veto predicate), what the readers return, how `filed` is derived, what `reindex` writes, what `mnemon search` returns (`mnemon-search/2`, with its wiki block), what the doctor's two documents are (`mnemon-doctor-data/1` unchanged, `mnemon-doctor-research/1`), which identifiers are reserved for the next phases, and which fixtures every branch must keep passing. Builds on [`2026-10-16-vault-store.md`](2026-10-16-vault-store.md) v1.4 (ownership §3.2, state axes §4) and the capture contracts' frontmatter subset ([`2026-10-15-capture-contracts.md`](2026-10-15-capture-contracts.md) §4.4). The decision document behind the four layers — why a wiki, the evidence contract, the doors, the pilot — is the maintainer's; this page carries only what code must agree on.

## 1. Versions and identifiers

| Identifier | Meaning | Owner |
|---|---|---|
| `research-interface/1` | this document; the `schema` of `lib_research.py --json` (`{"schema", "collections": [...], "wiki": [...]}` since 3b) | phase 3a |
| `collection/1` | the schema of a `Collections/*.md` document (§2–§4) | phase 3a |
| `wiki/1` | the schema of a `Wiki/*.md` page (§9): the prints, the evidential body, the locators, the derived states | phase 3b |
| `mnemon-search/2` | the JSON of `mnemon search` (§7); v1 had no `schema` key and carried the result type in `tier`; phase 3b adds the `wiki` block and the result type `wiki` — the schema stays 2 (additive) | phase 3a · 3b |
| `mnemon-doctor-data/1` | unchanged: the 19 checks of the vault store; its `counters` gain keys additively (§5: `collections*` in 3a, `wiki*` in 3b) | phase 3a |
| `mnemon-doctor-research/1` | the doctor's `research` document (§8): ten checks `research/<name>`, a sibling of the data document inside `doctor --data --json` (`"research": {…}`) | phase 3b |
| `mnemon-research-journal/1` | the append-only JSONL of the research operations in the instance's state directory — reserved (§10) | phase 3c |
| Guide **4** | `vault-template/` stamped `mnemon_guide: 4`: the four knowledge folders and their doors; phase 3b rewrote the `Wiki/` rows to the shipped states and added `Templates/Wiki.md` without a bump (the folders and the door table did not change — phase 3c bumps when the operations' doors land) | phase 3a · 3b |

A change to any row is a change to this document, in the same PR as the code, with the fixtures of §11 regenerated and reviewed.

## 2. The YAML subset of a collection

The frontmatter of a collection is read by `lib_research.parse_flat_frontmatter`, a subset shared with the engine's other readers:

- `key: scalar` — a double-quoted scalar decodes `\"` and `\\` and nothing else; a single-quoted one is verbatim (`''` → `'`); a bare one loses a trailing ` # comment` and surrounding whitespace; `#…` is empty;
- `key: [a, "b, c", d]` — an inline list, commas outside quotes split it, each item a scalar; `[]` is the empty list; a ` # comment` after the closing bracket is dropped;
- `key:` followed by `- item` lines (indented or at column zero) — a block list; comment lines inside it are skipped; `key:` with nothing under it is the empty scalar;
- comments and blank lines are skipped;
- a **duplicate key** is an error (the first value is kept, the document fails); a **nested mapping** (`key:` followed by `  sub: value`) and a **nested list** (an item indented deeper than the first, or a bracket inside an inline list) are errors; an indented line outside a block list and a line that is not `key: value` are errors; a quoted scalar may be followed by a ` # comment`.

Keys of `collection/1`: scalars `type`, `status`, `question`, `curated_by`, `created`, `updated`, `wiki`, `recusal`, `migrated_from`, `source_of`; lists `domains`, `tags`, `aliases`, `suppressed_pages`. Unknown keys are kept as they are and returned under `extra` (a migrated note carries its old `visibility`, `sources`, …). A list key given a scalar is an error; a scalar key given a list is an error.

| Key | Required | Values | Default |
|---|---|---|---|
| `type` | yes | `collection` | — |
| `status` | yes | `open` · `inbox` · `closed` | — |
| `question` | yes | non-empty text (proposed under `inbox`) | — |
| `curated_by` | yes | non-empty: the owner, an agent name, `scanner:<name>` (the vocabulary is the instance's) | — |
| `created` | yes | `YYYY-MM-DD` | — |
| `updated` | no | `YYYY-MM-DD` | null |
| `wiki` | no | `on` · `off` (case-insensitive, normalized) | `on` |
| `suppressed_pages` | no | list of wiki page file names: no `/`, no `[[`/`]]`, no `.md` suffix, not empty (anything else is an error — the veto must match a file name) | `[]` |
| `recusal` | no | `on` · `off` | null (= the agent decides by tags, phase 3c) |
| `domains`, `tags`, `aliases` | no | lists of strings | `[]` |
| `migrated_from` | no | `Synthesis/<old name>`; written once by the migration, never changed | null |
| `source_of` | no | an external collection a scanner mirrors | null |

## 3. The `## Items` grammar

The body is split into `## ` sections outside fenced blocks (a run of three or more backticks or tildes, closed by a run of the same character at least as long, with nothing after it); a `# ` title and `### ` sub-headings do not open sections. A heading is an ATX heading with up to three leading spaces, one space after the `##`, and closing `#`s only after whitespace; the section name is compared exactly (`Items`, not `items`). A UTF-8 BOM before the frontmatter is ignored; CRLF line endings are read. Exactly one `## Items` section is required: none → the document fails (`no `## Items` section`); two → it fails (`duplicate `## Items` heading (lines a, b)`) and the first is read. Other sections — `## Why`, `## Open questions`, `## Notes (owner)` and any other — are tolerated and returned by name in `sections`.

Inside `## Items`, line by line (fenced blocks skipped, blank lines ignored):

| Line | Reading |
|---|---|
| a list line (`- ` or `* `, any indentation) with **exactly one** wikilink whose target is `Sources/<rev>`, `Sources/<rev>/extract`, `Sources/<rev>/source`, optionally `.md`, `#fragment`, `\|display` (the `![[…]]` embed form counts as a link) | an **item**: `rev`, `file` (`extract` · `source` · null for the bare revision), `fragment`, `display`; the tail after the link, minus trailing punctuation and one dash (`—`, `–`, `-`), gives the **role** — the first `**…**` segment, normalized to lower case and single spaces, surrounding `*`/`_` and a trailing `:.,;` dropped (`***excluded***:` is `excluded`) — and the **note** (the rest, minus one more dash); without a leading `**…**` the role is null and the whole tail is the note; the role `excluded` sets `excluded: true` |
| an indented line (two spaces or more; a tab counts two) **without a link**, with or without a list marker, under an item | a continuation: appended to the item's note with a newline; a nested line **with** a Sources link is an item of its own, with any other link it is malformed |
| a list line without a link at the top level, or before any item | **malformed**: `list line without a Sources/ link` |
| a list line with two or more wikilinks | **malformed**: `two or more links on one line` |
| one link whose target is empty, has a backslash, starts with `/`, contains a `.` or `..` segment, does not start with `Sources/`, names no revision, names a revision outside `^\d{4}-\d{2}-\d{2}_[0-9a-f]{8}(-\d+)?$`, or names a file other than `extract(.md)` / `source(.md)` | **malformed**, with the reason (`absolute path in link target` · `path traversal in link target` · `link outside Sources/ (another folder or another instance)` · `revision name malformed: …` · `unknown file in revision: …` · …) |
| a second item for a revision already listed | **malformed**: `duplicate revision <rev> (first at line n)` — the first stays an item |
| a non-list line that is not an indented continuation | **malformed**: `free text between items (no list marker)` |
| an indented line (with or without a marker) under a malformed line | **malformed**: `continuation under a malformed line` |

The raw target is classified **before** any resolver: nothing is normalized, nothing is looked up on disk. A malformed line fails the document; the other items still count and the line is named with its 1-based line number, its reason and its text (first 120 characters).

## 4. The reader record

`lib_research.read_collection_text(text, name, existing=None)` → one record; `scan_collections(vault, existing)` → the records of `Collections/*.md` sorted by name (dot-files skipped); `python3 bin/lib_research.py --vault <path> [--json]` prints them (`--json`: `{"schema": "research-interface/1", "collections": [...]}`; text: one `ok|fail|unreadable <name> — <status> · N items[ · K excluded][ · U unresolved][ · M malformed]` line, each error and malformed line under it, a summary line, exit 1 when any document is not `ok`); `--file <path>` reads one document without consulting a vault (`exists` and `unresolved` are null). A `Collections/` folder that exists but cannot be listed is never an empty one: `list_collections` raises, the CLI exits 1 saying so, `lib_vault` counts it (`snapshot.complete = false`, `snapshot.collections_unlisted`), reindex keeps one row saying the section was not regenerated (§6).

```json
{
 "schema": "collection/1",
 "name": "<file stem>", "file": "Collections/<stem>.md",
 "state": "ok | fail | unreadable",
 "errors": ["<schema-level reasons, in order>"],
 "status": "open", "question": "…", "curated_by": "…", "created": "YYYY-MM-DD", "updated": "YYYY-MM-DD | null",
 "domains": [], "tags": [], "aliases": [], "wiki": "on | off", "suppressed_pages": [], "recusal": "on | off | null",
 "migrated_from": "… | null", "source_of": "… | null",
 "extra": {"<unknown key>": "<scalar or list>"},
 "sections": ["Why", "Items", "Open questions", "Notes (owner)"],
 "items": [{"line": 24, "rev": "2026-01-10_aaaaaaaa", "file": "extract | source | null", "fragment": "… | null",
            "display": "… | null", "role": "… | null", "excluded": false, "note": "…", "exists": "true | false | null"}],
 "malformed": [{"line": 12, "reason": "…", "text": "…"}],
 "counts": {"items": 4, "excluded": 1, "malformed": 0, "unresolved": "0 | null", "links_outside_items": 1}
}
```

`state` is `fail` when `errors` or `malformed` is non-empty, `unreadable` when the file could not be read (then `errors` names the reason, the counts are 0 and `unresolved` null — an unreadable document is never a clean one), `ok` otherwise. `exists` is resolved against the `existing` the caller passes: a set of revision names, or — what `lib_vault` and `existing_revisions()` pass — a mapping revision → the files it holds, in which case the item's **target** must exist: the folder for the bare form, `extract.md` or `source.md` otherwise (an item to a captured-only revision's `extract` is unresolved); `unresolved` counts the items whose target is absent. `links_outside_items` counts wikilinks in the other sections — they file nothing. With `--file`, `file` is the document's base name.

## 5. Derived relations and counters

`filed(rev)` (vault store §4, v1.4): a seed or draft note links `rev`, **or** `rev` is an item of any collection — any `status`, any role, `excluded` included (it was considered) — **or** `rev` is named in a wiki page's `based_on` (phase 3b: any readable page, whatever its state — the page rested on it). `integrated` wins: a revision a `stable` note links is not counted as filed. A malformed line files nothing; an item or a `based_on` entry whose revision does not exist files nothing.

`lib_vault.scan_vault()` carries `scan["collections"] = {"present": bool, "notes": [record…]}` (each record with a `sha256` of the file's bytes — the fingerprint a page's `inputs` prints) and, since phase 3b, `scan["wiki"] = {"present": bool, "notes": [wiki record…]}` and `scan["charter"]["sha256"]`; `lib_vault.research_context(scan, budget_words)` is what `validate_wiki` reads (§9). `counters()` gains, additively to `mnemon-doctor-data/1`:

| Key | Value |
|---|---|
| `collections` | documents in `Collections/` |
| `collections_fail` · `collections_unreadable` | documents with `state` fail · unreadable |
| `collections_status` | `{"open": n, "inbox": n, "closed": n, "unknown": n}` (`unknown` = no valid status) |
| `collection_items` · `collection_items_excluded` · `collection_items_malformed` · `collection_items_unresolved` | sums over the documents |
| `filed` | extended as above (the one existing key whose meaning changed) |
| `wiki_pages` · `wiki_fail` · `wiki_unreadable` · `wiki_based_on` | pages in `Wiki/`, those whose schema fails, those unreadable, the `based_on` entries summed (phase 3b) |

`lib_vault.py --status-counts` adds `collections=`, `collections_fail=`, `wiki=` and `wiki_fail=` lines. The 19 checks, `summary`, `snapshot`, `diagnostics` of `mnemon-doctor-data/1` are what v1.3 produced (checked by hand against `main` on the fixture vault with and without `Collections/`, 2026-10-27: identical apart from the temporary state path; `counters` gained exactly the keys above) and are frozen as the golden `expected/doctor-data.json` (§11) — the branches must leave it byte-for-byte (phase 3b did: the fixture pages rest only on revisions the dossiers already file, and the golden strips the `collection*` and `wiki*` counter keys); `store/index` reads only the `- [[Sources/…` rows, so the new index sections are never drift (they are `research/index`'s). A `Wiki/` that exists but cannot be listed is counted like an unlistable `Collections/` (`snapshot.unreadable`, `snapshot.complete = false`) — the data snapshot does not name it (`wiki_unlisted` is the research document's).

## 6. The index

`mnemon reindex` writes, in `index.md`: a `| Collections | N |` row and (phase 3b) a `| Wiki | N |` row in the Status table (always; 0 without the folder) and, only when the folder exists, a `## Collections` section between `## Recent Sources` and `## Synthesis Notes` — then, only when `Wiki/` exists, a `## Wiki` section between `## Collections` (or `## Recent Sources`) and `## Synthesis Notes` — one row per document sorted by name:

```
- [[Collections/<name>|<name>]] (<status>, <n> items[, <k> excluded][, <m> malformed])[ (fail: <first error>)] — <question>
- [[Collections/<name>|<name>]] (unreadable)
- _Collections/ could not be listed (<error>); the rows are not regenerated_
```

```
- [[Wiki/<name>|<name>]] (<kind>, <n> sources, <k> collections[, updated <date>])[ (fail: <first error>)]
- [[Wiki/<name>|<name>]] (unreadable)
- _Wiki/ could not be listed (<error>); the rows are not regenerated_
```

`<name>` and `<question>` pass through the index's `display_text` (a `|` is replaced); a document without a valid status (or a page without a valid kind) shows `?` in its place; an unlistable folder gives the Status row `?`. A wiki row carries the stored facts only — the derived states are the doctor's (§8), never the index's. The counters line of `reindex` adds `Collections=N` and `Wiki=N` when the folders exist. The rows are regenerated every time and never preserved as hand edits (only `- [[Sources/…` rows are).

## 7. Search — `mnemon-search/2`

`mnemon search --query <q> [--domain <d>] [--limit <n>] [--include-source] [--include-synthesis] [--include-collections] [--include-wiki [--wiki-limit <n>] [--include-orphans]] [--vault <path>] [--human]`

Targets: `Sources/*/extract.md` always; `Sources/*/source.md` with `--include-source`; `Synthesis/**/*.md` with `--include-synthesis`; `Collections/*.md` (dot-files skipped) with `--include-collections`. Matching, tokenization, the fallback and the ranking (rating desc, date desc, title) are v1's. `--include-wiki` (phase 3b) adds the **wiki block** — `Wiki/*.md` (dot-files skipped) as a separate part of the output with its own limit (`--wiki-limit`, default 5): the pages are read and validated by the research reader on the vault's snapshot (§9) and only the **usable** ones are searched — `suppressed`, `unbound`, schema-failed and not-probed pages never appear, `orphan` pages only with `--include-orphans`; a page matches when its text without the `## Atlas reading` section holds every token (or, when the main search fell back, the fallback token); the block is ordered by `updated` desc, then file name — never by rating; `--domain` filters it by the page's domains, the union of its stored `domains` and its supporting collections'; snippets are taken from the text without `## Atlas reading`. The main block is unchanged by the flag.

```json
{
 "schema": "mnemon-search/2",
 "query": "…", "tokens": ["…"], "provider": "grep", "configured_provider": "grep | qmd | null",
 "fallback_applied": false, "fallback_token": null, "count": 2,
 "wiki": "null | {\"count\": 1, \"limit\": 5, \"results\": [{…the keys below…, \"kind\": \"concept | comparison | contradiction | open-question\", \"updated\": \"YYYY-MM-DD | null\", \"states\": [\"stale\", …]}]}",
 "results": [{
   "path": "Sources/<rev>/ | Synthesis/ | Collections/", "file": "extract.md | source.md | <note>.md",
   "result_type": "extract | source | synthesis | collection | wiki",
   "tier": "full | bookmark | null",
   "status": "<stored status> | null",
   "title": "…", "author": "…", "url": "…", "domains": [], "tags": [], "rating": "int | null", "created": "YYYY-MM-DD | ''",
   "snippet": "…"
 }]
}
```

- `result_type` is what the result is. `tier` is the **capture tier** of a Sources result (`extract` or `source`): `tier:` of the revision's `source.md`, else of its `extract.md` (a legacy placeholder), else `full`; it is `null` for every other result type. Until v2 the `tier` key carried the result type — a consumer that reads `tier` for the kind of result must read `result_type` now (the plugin's `source-search` skill does since 0.2.2).
- `status`: the stored `status:` of the file (`extracted`/`dismissed`/a legacy word for an extract; for a source result the paired extract's — `null` without an extract, never `source.md`'s own; `seed`/`draft`/`stable` for a synthesis note; `open`/`inbox`/`closed` for a collection), `null` when absent.
- A **bookmark revision without an extract** (phase 3-capture's lightweight capture, `tier: bookmark` in `source.md`, no `extract.md`) is not found by the default scan (extracts only); with `--include-source` it is a `source` result with `tier: bookmark`, `status: null`, its `title`/`url`/`created` from `source.md`, `rating: null`, `domains`/`tags` empty unless `source.md` carries them. Phase 3-capture may add a `--tier` filter; the absence case above is what phase 3a freezes (fixture `2026-05-14_ffffffff`). A `tier:` value outside `full`/`bookmark` passes through lower-cased.
- The wiki block (phase 3b): `wiki` is `null` without `--include-wiki`, else `{count, limit, results}`; a wiki result has `path: "Wiki/"`, `file: "<name>.md"`, `result_type: wiki`, `tier: null`, `status: null`, `title` = the file stem, `author`/`url` empty, `rating: null`, `tags: []`, `created` from the page, plus `kind`, `updated` and `states` — the derived states the page carries (`[]` = clean; `stale`, `inputs-changed`, `edited-outside-update`, `orphan`, `weak-evidence`, `budget` are the only ones a shown page can carry). Pages the doctor marks `not probed` are excluded like `unbound` ones. A `Wiki/` that exists but cannot be listed is a usage error (exit 2) with the flag, untouched without it.
- A source result takes its metadata from the paired `extract.md` where that has any (v1); a collection result's `title` is its file stem and its `status`/`domains`/`tags`/`created` come from the research reader (§4) — the same subset, the same duplicate-key and `...` rules as reindex and the doctor.
- Deduplication: `extract` and `source` results are keyed by folder (extract wins); every other result type is keyed by folder + file — three collections in one folder are three results.
- `--domain` filters every result type by its `domains` (a collection's are its own; a wiki page's are the union of its stored list and its supporting collections').
- Exit codes: 0 · 2 for a usage error, a missing vault, a query with no usable token, a configuration error, a negative `--wiki-limit`, or — with `--include-collections` / `--include-wiki` — a `Collections/` / `Wiki/` folder that exists but cannot be listed (an unlistable folder is never an empty answer; §4).
- `--human`: `<type>[ · tier: <tier>][ · status: <status>]` on the second line of each result; the wiki block follows the results as `Wiki (<n> page(s), limit <m>):` with `wiki · <kind> · updated <date> · domains: … · <states or ok>` per page.

## 8. The doctor

Phase 3a changed nothing in the output of `mnemon doctor --data`: `mnemon-doctor-data/1`, its 19 checks, `--list`, the brief line and the exit codes are as in the vault store spec; only `counters` gains the keys of §5. Phase 3b adds the **research document** next to it, on the same read-only snapshot, in the same run.

**`mnemon-doctor-research/1`.** `vault-doctor.py --research --json` prints it; `--envelope` prints `{"data": …, "research": …}` (the dispatcher's input); `--tsv` and the text form print the data rows, then the research rows; `--list research/<name>` (or the part after `/`, the data checks searched first) lists one check's items. Its keys: `schema`, `instance`, `vault`, `generated_at`, `snapshot` (`today`, `budget_s`, `complete`, `unreadable`, `collections_unlisted`, `wiki_unlisted`, `pages_probed`, `pages_not_probed`), `counters` (the `collection*` and `wiki*` keys of §5), `pages` (one row per page: `name`, `kind`, `severity` — `fail` · `warn` · `ok` · `null` when not probed —, `states`, `usable`, `collections`, `updated`), `checks` (the ten below, in this order, each `{name, status, probed, reason, detail, counts, threshold, fix, items}` like the data checks), `diagnostics` (`error` — fail — when `Wiki/` or `Collections/` exists but cannot be listed or a check raised; `budget` — warn — when the deadline hit this document), `summary` (the data summary's keys plus `coverage: {pages, collections}` and `pages: {fail, warn, ok, not_probed}`).

| Check | What it reports | Severity | Door |
|---|---|---|---|
| `research/collections` | every collection with `state` ≠ ok — schema, grammar (the malformed lines named), unreadable — and the documents with unresolved items | fail · warn (unresolved) | operator |
| `research/collection-age` | the age of every open dossier (by `updated`, else `created`) — informational, never raises | ok | operator |
| `research/wiki-schema` | pages whose schema fails (§9: missing prints, a bad `kind`, a malformed `based_on` entry, `inputs.page` ≠ the file name, no evidential body…); pages **not probed** (unreadable; a dependency that could not be read) | fail · warn (not probed) | research-update |
| `research/wiki-binding` | `unbound` pages, each reason named; `suppressed` pages (the veto predicate of §9), the vetoing dossier named; counts the locators and those verified | fail | research-update |
| `research/wiki-stale` | pages resting on a superseded revision; **not probed** (warn) when the supersedes graph is incomplete (dangling targets or cycles) and any page has a `based_on` | warn | research-update --propose |
| `research/wiki-inputs` | `edited-outside-update` (the evidential body's sha256 ≠ `body_sha256`) and `inputs-changed` (a dossier's, the charter's, a revision text's fingerprint, the protocol or template version moved) | warn | research-update |
| `research/wiki-evidence` | `uncited` — for every dossier that at least one page lists in `collections`: the items without the role `excluded` whose revision no **verified** locator of those pages cites (unresolved items are `research/collections`' finding, not uncited); `weak-evidence` pages; `info` items (completeness `unknown` on a PDF or media) | warn | research-update |
| `research/wiki-orphan` | pages none of whose `collections` exists with `status: open` (each named with its status or `does not exist`) | warn | operator |
| `research/wiki-budget` | dossiers with more than `doctor_wiki_pages_per_collection` pages listing them; pages whose evidential body exceeds `doctor_wiki_words` words (§10) | warn | operator |
| `research/index` | the `## Collections` and `## Wiki` sections of `index.md` against the regenerated rows (§6), the `| Collections |` / `| Wiki |` Status rows against the counts (absent rows are drift only when the folder exists); `index.md` missing with rows to generate | warn | mnemon reindex |

**Embedding and exits.** `mnemon doctor --data` prints the research lines (`ok|warn|fail research/<name> — …`, then its diagnostics as `doctor/<code>`) after the data lines; `--json` embeds both documents — `{"checks": […], "data": {…}, "research": {…}, "exit": N}`; a `fail` in either joins the aggregated exit 1 (a research-only fail is exit 1 while `data.summary.fail` is 0). `status --brief` keeps its policy (exit 0 after a run) and its line gains, after `probed p/19`, `research: N pages · K fail · M warn` — N the pages in `Wiki/`, K and M the research **checks'** fails and warns — or `research: not probed (…)`; `--brief --json` carries the research document without its `checks` and `pages` as `research`. One `--budget` covers both: the worker prints the data document as soon as it is built, then the research one, one JSON per line; the parent keeps what arrived before the deadline and marks the document the deadline hit `not probed` (all its checks, a `budget` diagnostic — `research: budget … exceeded` for the research one). `--wiki-pages-per-collection` and `--wiki-words` override the config keys for one run (the goldens pin `--wiki-words 80`).

## 9. `wiki/1` — the page, its prints, the derived states

**The YAML subset of a page** is nested — `lib_research.parse_frontmatter_tree`, distinct from the flat subset of §2 (the collection reader is untouched): block mappings and sequences by indentation, a sequence item that opens a mapping (`- rev: …` with deeper `text_sha256:` / `seal:` lines), inline lists `[…]` and inline mappings `{"k": "v", …}` (commas outside quotes; quoted keys), the scalars of §2, comments and blank lines skipped; a duplicate key is an error (the first value kept), a line that is neither `key: value` nor `- item` is an error.

| Key | Required | Values |
|---|---|---|
| `type` | yes | `wiki` |
| `kind` | yes | `concept` · `comparison` · `contradiction` · `open-question` |
| `collections` | yes (may be empty) | list of `[[Collections/<name>]]` (brackets, `.md`, `\|display` optional): the **supporting** dossiers — their open status and veto govern the page; a duplicate, an entry outside `Collections/` or outside the instance is an error |
| `domains` | no | list of strings (the union of the supporting dossiers'; search adds theirs) |
| `based_on` | yes | list of `{rev, text_sha256, seal}`: `rev` a Sources link of §3's forms (brackets optional), `text_sha256` a sha256 hex digest, `seal` `capture` · `observed`; unknown keys, a duplicate revision, a malformed entry are errors |
| `inputs` | yes | mapping: `page` (= this file's name without `.md`, else an error), `kind` (one of the four), `collections` (`{"Collections/<name>": "<sha256 of the file>"}`, may be `{}`), `charter_sha256` (optional, a digest), `protocol`, `template` — missing `page`/`kind`/`protocol`/`template`, an unknown key or a bad digest are errors |
| `body_sha256` | yes | a sha256 hex digest |
| `maintained_by`, `visibility`, `created`, `updated` | no | scalars; the dates ISO |

**The evidential body** — `lib_research.evidential_body`: the lines from the first heading (any level, outside fences) after the frontmatter to the first `## Atlas reading` / `## Log` heading (level 2, exact name, outside fences; excluded), line endings normalized to LF, trailing spaces and tabs stripped from every line, trailing blank lines dropped, one final LF; `body_sha256` is the sha256 of these UTF-8 bytes; a page without a heading before the cut has no evidential body (an error). `## Atlas reading` and `## Log` are consumable: outside the hash, their links counted apart (`counts.links_consumable`), never a state. The body hash of `source.md` keeps the other rule — `lib_vault.body_bytes()`, exact bytes (contract I4).

**Links and locators of the body** (`wiki_links`): every wikilink outside fences is classified — `sources` (the forms of §3; the raw target read before any resolver), `local` (`Collections/…`, `Wiki/…`, `Synthesis/…` — `.md` optional), `outside` (`..`, an absolute path, a backslash, or a malformed Sources target), `unresolved` (anything else). A locator is a `sources` link to `source`: the **heading form** `[[Sources/<rev>/source#<heading>]]` — the heading text (spaces normalized, `#` stripped) exists **exactly once** among the headings of `source.md`'s body (any level, outside fences); the **quote form** `> "<fragment>" — [[Sources/<rev>/source]]` on one blockquote line (`"…"`, `«…»` or `“…”`; one dash of the three; trailing punctuation tolerated) — the fragment is a substring of the body after whitespace normalization; an empty fragment is never found; a fragment found more than once still locates (it names the text, not a place). A bare `[[Sources/<rev>/source]]` and any `extract` link are navigation, not locators.

**The reader record** — `lib_research.read_wiki_text(text, name)` → one record, nothing resolved; `scan_wiki(vault)` the records of `Wiki/*.md` sorted (dot-files skipped; an unlistable folder raises, counted by `lib_vault` like §4's); `read_wiki` / `unreadable_wiki_record` as for collections:

```json
{
 "schema": "wiki/1", "name": "<file stem>", "file": "Wiki/<stem>.md",
 "state": "ok | fail | unreadable", "errors": ["<schema reasons, in order>"],
 "kind": "concept | … | null", "collections": ["<name>"], "domains": [], "maintained_by": "… | null", "created": "… | null", "updated": "… | null",
 "visibility": "… | null", "body_sha256": "<printed> | null",
 "based_on": [{"rev": "<rev>", "file": "extract | source | null", "text_sha256": "<hex> | null", "seal": "capture | observed | null"}],
 "inputs": {"page": "…", "kind": "…", "collections": {"<name>": "<hex> | null"}, "charter_sha256": "… | null", "protocol": "…", "template": "…"} ,
 "extra": {"<unknown key>": "…"}, "sections": ["State of evidence", "Atlas reading", "Log"],
 "body": {"sha256": "<computed> | null", "words": 0, "start": 0, "end": 0, "cut": "Atlas reading | Log | null"},
 "links": [{"kind": "sources | local | outside | unresolved", "line": 0, "target": "…", "...": "the parts"}],
 "locators": [{"line": 0, "rev": "<rev>", "form": "heading | quote", "heading": "…", "quote": "…", "verified": "true | false | null", "reason": "… | null"}],
 "counts": {"based_on": 0, "collections": 0, "links": 0, "locators": 0, "links_consumable": 0},
 "states": [], "reasons": {}, "not_probed": [], "severity": null, "usable": null, "cited": []
}
```

`state` is `fail` when `errors` is non-empty, `unreadable` when the file could not be read, `ok` otherwise. The last six keys are the validator's.

**The research context** — `lib_vault.research_context(scan, budget_words)`: `revisions` (rev → `has_source`, `has_extract`, `klass`, `capture_sha256`, `body_sha256`, `completeness`, `superseded`, `source_type`, `unreadable`), `graph_complete` (no dangling targets, cycles or self edges), `collections` (name → the record of §4 with its `sha256`), `charter_sha256`, `source_text(rev)` (the body of `source.md`, read once, `None` when unreadable), `local_exists(path)`, `budget_words`.

**The validator** — `lib_research.validate_wiki(rec, ctx)` fills `states` (in priority order), `reasons` (state → the named reasons), `not_probed`, `severity`, `usable`, `cited` (the revisions its verified locators cite). An unreadable page: `not_probed: ["unreadable"]`, severity `null`, unusable. A schema-failed page: `states: ["schema"]`, severity `fail`, unusable, nothing else derived. Otherwise the states, **never stored**:

| State | Rule (each reason named in `reasons`) | Severity |
|---|---|---|
| `unbound` | `kind` ≠ `inputs.kind` · a key of `inputs.collections` ∉ `collections` · a `based_on` revision without `source.md` · `seal: capture` on a revision that is not managed or whose `capture.text_sha256` ≠ the printed hash, or whose body ≠ `capture.text_sha256` (an integrity error) · `seal: observed` on a managed revision · `seal: observed` whose printed hash ≠ the body's (`lib_vault.body_bytes`) · a body link outside the instance · a body link to a target that does not exist · **`unlisted`** — a body link to a revision outside `based_on` · a heading locator not found or not unique · a quote locator empty or not found | **fail** — the page is no proven ground: out of search, out of research-ask (contract I6) |
| `suppressed` | the veto predicate (below) is true | **fail** |
| `edited-outside-update` | the evidential body's sha256 ≠ `body_sha256` | warn |
| `stale` | a `based_on` revision is superseded (the vault store's graph); with an incomplete graph a superseded candidate is **not probed**, never current | warn |
| `inputs-changed` | a dossier of `inputs.collections` is gone or its file sha256 ≠ the printed one · the charter's sha256 ≠ `charter_sha256` (none on both sides is equal) · `protocol` ≠ `research/1` · `template` ≠ `wiki/<kind>@1` · a revision's text ≠ the printed hash | warn |
| `orphan` | no entry of `collections` exists with `status: open` — each named: `is <status>` · `does not exist`; no entry at all: `no collections listed` | warn |
| `weak-evidence` | a `based_on` revision with `completeness` `partial` or `blocked`, or an integrity error; `unknown` on a `pdf` · `video` · `podcast` · `audio` source is an `info` reason, not a state | warn |
| `budget` | the evidential body exceeds `ctx.budget_words` words (the dossier-level budget is `research/wiki-budget`'s) | warn |
| `unresolved` (reasons only) | a `local` link whose file does not exist; a link that is none of the kinds | warn |

**Not probed**: a `based_on` revision or a locator's `source.md` that could not be read, a dossier of `inputs.collections` that could not be read, a superseded candidate under an incomplete graph — each named; a page with any of them and no fail state has severity `null` and is unusable (never a clean one). `severity` is `fail` with `unbound` or `suppressed`, else `warn` with any state or unresolved link, else `ok`; `usable` ⇔ severity ∈ {ok, warn}. Priority of display: `unbound` · `suppressed` · `edited-outside-update` · `stale` · `inputs-changed` · `orphan` · `weak-evidence` · `budget`.

**The veto predicate** — `lib_research.is_suppressed(page_name, page_collections, collections)`, one definition for the validator, search and the deletion of phase 3c: a page is suppressed ⇔ ∃ C ∈ `page.collections` with `C.status = open` and (`C.wiki = off` or the page's file name ∈ `C.suppressed_pages`); `inbox` and `closed` collections give no veto, nor does one that does not exist; one veto suffices, an open dossier without a veto does not lift another's. Pages of a closed dossier are `orphan`, not suppressed.

**Doors.** `python3 bin/lib_research.py --vault <path>` validates the pages after the collections (the states through `lib_vault.research_context`), one `ok|fail|unreadable <name> — <kind> · N based_on · K collections · L locators[ · <states>][ · not probed]` line each with the reasons under it, a `wiki: N pages · fail F · warn W · not probed P` summary, exit 1 when any page fails its schema, is `fail`, or is not probed; `--file <page>` reads a page by its `type: wiki` without a vault (no states); `--json` adds `"wiki": [record…]` (without the working copy of the body lines). `Templates/Wiki.md` is the page scaffold `mnemon init` copies (a scaffold, not a valid page).

## 10. Configuration

Phase 3b adds two keys to `mnemon config keys` (the table of `bin/lib_config.py`, `bin/mnemon-config.sh` and `mnemon config keys`, exported as `DOCTOR_WIKI_PAGES_PER_COLLECTION` / `DOCTOR_WIKI_WORDS`): `doctor_wiki_pages_per_collection` (int, default **5** — `research/wiki-budget` warns above it) and `doctor_wiki_words` (int, default **3000** — the `budget` state of a page above it). Reserved: `research_log_path` (phase 3c — a path for the research journal inside the vault, refused when it lies under `Wiki/`; absent → `<state>/mnemon/vaults/<key>/research-journal.jsonl`). The journal records (`mnemon-research-journal/1`, phase 3c) are append-only JSON lines with at least `ts`, `op` (`research-ask` · `research-update` · `research-propose`), `instance`, `page`, `inputs` (the package fingerprints), `outcome`.

## 11. Compatibility fixtures and ownership

`tests/fixtures/research/vault/` is the vault every branch reads the same way: six legacy revisions (a `tier: bookmark` with an extract, a bookmark without any extract, a superseded pair, one captured without an extract), a stable and a seed note, ten collections — one valid `open` dossier with every accepted line form, an annotated one (`...`, comments, a decorated role), a minimal `inbox` one, a `closed` one with the veto fields and an unresolved target, two open ones that veto (`wiki: off`; a name in `suppressed_pages`), and four that must fail (every rejected line form, a duplicate `## Items`, a broken frontmatter, a missing `## Items`) — and, since phase 3b, ten wiki pages, one per derived state of §9 (a clean contradiction page with a verified heading and a verified quote locator; `stale`; `inputs-changed`; `edited-outside-update`; `orphan`; `suppressed` by `wiki: off` and by name; `unbound` by unlisted links, `kind ≠ inputs.kind` and `inputs.collections ⊄ collections`; `unbound` by a wrong observed seal, a missing heading, a quote not in the source, a link outside the instance, with an unresolved link; a schema failure) plus a dot-file the readers skip. The pages' prints are observations of the fixture files: `tests/fixtures/research/prints.py --check` verifies them (the deliberately wrong ones listed in it), `prints.py` prints every fingerprint when a fixture changes on purpose; their `based_on` names only revisions the dossiers already file, so `doctor-data.json` stays byte for byte. The managed-revision cases (`seal: capture` right and wrong, `observed` on a managed revision, an integrity error, `completeness` partial/blocked/unknown) and the not-probed cases (an unreadable `source.md`, an incomplete graph) are unit tests on temporary vaults (`tests/test_research.py`, `WikiValidatorTests`). `tests/fixtures/research/expected/` holds the readings `generate.py` pins: `research.json` (the records of §4 and §9, with the states), `counters.json` (the keys of §5), `index.md` (the Status table and the sections of §6), `search-default.json` · `search-all.json` · `search-domain.json` · `search-wiki.json` · `search-wiki-orphans.json` (§7), `doctor-data.json` (the data document of §8 with `generated_at`, the paths and the research counter keys normalized away — byte for byte since v1), `doctor-research.json` (the research document of §8 on the same run, `--wiki-words 80`). `tests/test_research.sh` runs `generate.py --check`, the unit tests of `tests/test_research.py`, the dispatcher on a copy of the fixture (search with the wiki block, reindex with the `## Wiki` section, `doctor --data --json` with both documents and the aggregated exit), the reader as a door, `init`'s scaffold (both templates) and the Guide 4 canon. `generate.py --write` regenerates the goldens — only together with a change to this document.

| Shared file | Owner | Other branches |
|---|---|---|
| `bin/lib_research.py`, `tests/fixtures/research/`, `tests/test_research.*`, this document | phase 3a | phase 3b added the `wiki/1` reader and validator next to the collection one (§9) and the fixture pages; phase 3-capture touches nothing here; phase 3c adds the operations' fixtures (stale → propose, no-op, lost update, hostile source, fresh session) |
| `bin/lib_vault.py` (`scan_collections`, `counters`) | phase 3a | phase 3b added `scan_wiki`, `research_context`, the dossier and charter fingerprints, the `based_on` leg of `filed` and the `wiki*` counters; phase 3-capture adds `promoted` and the `tier` counters |
| `bin/knowledge-reindex.py` (`collection_rows`, the section) | phase 3a | phase 3b added `wiki_rows`, the `## Wiki` section after `## Collections` and the `| Wiki |` Status row |
| `bin/mnemon-search.py` (`mnemon-search/2`) | phase 3a | phase 3-capture: bookmark results carry `tier: bookmark` (already read) and may add a `--tier` filter; phase 3b added `--include-wiki`, `--wiki-limit`, `--include-orphans` and the `wiki` block — the schema stays 2, the keys of §7 keep their meaning, `wiki` is `null` without the flag |
| `bin/vault-doctor.py`, the dispatcher's `_doctor_data` | phase 3a (output unchanged; one test hook added — `MNEMON_TEST_DOCTOR_TODAY`, honoured only under `MNEMON_TEST_HOOKS=1`, pins the clock for suites that go through the dispatcher) | phase 3b added the `research` document (§8): the ten checks, `--research`, `--envelope`, the two-line worker under one budget, the research part of `--brief`, `--list research/*`; the dispatcher embeds `research` and routes its lines through `_check` |
| `vault-template/` (Guide 4) | phase 3a | phase 3b rewrote the `Wiki/` rows to the shipped states and added `Templates/Wiki.md` (no stamp bump); phase 3c adds the operations' doors and bumps the Guide |

Every PR against a shared file runs `tests/run-suites.sh` (on a Mac `--both`); the second branch to merge rebases and runs the union of the fixtures.

## 12. Non-goals

Phase 3a: no wiki reader, no printed observations verified, no `research` doctor document. Phase 3b: no research operations (`research-update` · `research-ask` · `research-synthesize` · `research-triage` are phase 3c's — no page is generated, published or deleted by the engine; the fence, the package limit and the no-op rule of contracts I2 and I5 are theirs), no journal, no scanner envelopes for collections, no dispatcher command for the reader (its door is `python3 $MNEMON_HOME/bin/lib_research.py`), no authorship of `## Items` lines (replaced by the rule that agents and scanners only append), no qmd indexing of collections or pages, no `research-interface/2` (every change of phase 3b was additive).

## 12a. The implementation contracts of the decision document (I1–I6)

| ID | Where this document fixes it |
|---|---|
| I1 — the recorded build package | `inputs` (the page key, `kind`, the dossier fingerprints, the charter's, the protocol and template versions) and `based_on` (a text fingerprint per revision) are the stored package (§9); the rebuild test — the prints kept outside `Wiki/`, the folder deleted on a copy, the pages rebuilt — is phase 3c's fixture |
| I2 — the publication order | phase 3c (§12): the snapshot validated is the one captured, the no-op decided before generation, the prints and `## Log` published as one file |
| I3 — the exact subsets | §2 (flat, collections) and §9 (nested, pages); repeated revisions in `## Items` and in `based_on` fail; an empty quote is never found, a non-unique one still locates; targets are classified raw, never canonicalized; `on`/`off` normalized; `kind = inputs.kind` enforced as `unbound` |
| I4 — the byte rules | the evidential body normalized (§9), `source.md` exact bytes (`lib_vault.body_bytes`); `seal: observed` on a managed revision is `unbound` (a unit test pins it) |
| I5 — the package limit | phase 3c (§12): the research package's size, timeout and cost, the oversize error, the chunking that keeps every revision and locator |
| I6 — `unlisted` | a reason inside `unbound` (§9): one count, excluded from search (§7) and from research-ask; `not probed` pages are excluded the same way |

## 13. Status

v1, 2026-10-27 — the exit criterion of phase 3a: the reader and validator (`lib_research.py`), the extended `filed` and the collection counters, the index section, `mnemon-search/2`, Guide 4 and the `init` scaffold, the fixture vault and its goldens, the ownership table. Implementation on `feat/p3a-collections`.

v1.1, 2026-10-28 — phase 3b: `wiki/1` executable (§9: the nested subset, the prints, the evidential body, the locators, the validator's states, the veto predicate), the `based_on` leg of `filed` and the `wiki*` counters (§5), the `## Wiki` section and Status row (§6), the wiki block of `mnemon search` (§7), the doctor's `research` document with its ten checks, the envelope, the brief part and the one budget (§8), the two budget keys (§10), the fixture pages, `prints.py` and the new goldens (§11), the contracts I1–I6 (§12a). Implementation on `feat/p3b-wiki`.
