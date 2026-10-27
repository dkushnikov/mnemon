# Research interface — collections, the reader, index, search, fixtures (2026-10-27)

Status: **v1** (phase 3a, the exit criterion of the research layers' first step) · Scope: the executable contract between the engine's shared files and the two branches that build on them — the bookmark tier (phase 3-capture) and the wiki layer with the doctor's `research` document (phase 3b). It freezes what a `Collections/*.md` document is (schema `collection/1`), how its `## Items` lines are read, what the reader returns, how `filed` is derived, what `reindex` writes, what `mnemon search` returns (`mnemon-search/2`), what stays unchanged in the doctor, which identifiers are reserved for the next phases, and which fixtures every branch must keep passing. Builds on [`2026-10-16-vault-store.md`](2026-10-16-vault-store.md) v1.4 (ownership §3.2, state axes §4) and the capture contracts' frontmatter subset ([`2026-10-15-capture-contracts.md`](2026-10-15-capture-contracts.md) §4.4). The decision document behind the four layers — why a wiki, the evidence contract, the doors, the pilot — is the maintainer's; this page carries only what code must agree on.

## 1. Versions and identifiers

| Identifier | Meaning | Owner |
|---|---|---|
| `research-interface/1` | this document; the `schema` of `lib_research.py --json` | phase 3a |
| `collection/1` | the schema of a `Collections/*.md` document (§2–§4) | phase 3a |
| `wiki/1` | the schema of a `Wiki/*.md` page (§9) — declared here, read in phase 3b | phase 3b |
| `mnemon-search/2` | the JSON of `mnemon search` (§7); v1 had no `schema` key and carried the result type in `tier` | phase 3a |
| `mnemon-doctor-data/1` | unchanged: the 19 checks of the vault store; its `counters` gain keys additively (§5) | phase 3a |
| `mnemon-doctor-research/1` | the doctor's `research` document, a sibling of the data document inside `doctor --data --json` (`"research": {…}`) — reserved (§8) | phase 3b |
| `mnemon-research-journal/1` | the append-only JSONL of the research operations in the instance's state directory — reserved (§10) | phase 3c |
| Guide **4** | `vault-template/` stamped `mnemon_guide: 4`: the four knowledge folders and their doors | phase 3a |

A change to any row is a change to this document, in the same PR as the code, with the fixtures of §11 regenerated and reviewed.

## 2. The YAML subset of a collection

The frontmatter of a collection is read by `lib_research.parse_flat_frontmatter`, a subset shared with the engine's other readers:

- `key: scalar` — a double-quoted scalar decodes `\"` and `\\` and nothing else; a single-quoted one is verbatim (`''` → `'`); a bare one loses a trailing ` # comment` and surrounding whitespace; `#…` is empty;
- `key: [a, "b, c", d]` — an inline list, commas outside quotes split it, each item a scalar; `[]` is the empty list;
- `key:` followed by `- item` lines (indented or at column zero) — a block list; `key:` with nothing under it is the empty scalar;
- comments and blank lines are skipped;
- a **duplicate key** is an error (the first value is kept, the document fails); a **nested mapping** (`key:` followed by `  sub: value`) is an error; an indented line outside a block list and a line that is not `key: value` are errors.

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
| `suppressed_pages` | no | list of wiki page file names (without `.md`) | `[]` |
| `recusal` | no | `on` · `off` | null (= the agent decides by tags, phase 3c) |
| `domains`, `tags`, `aliases` | no | lists of strings | `[]` |
| `migrated_from` | no | `Synthesis/<old name>`; written once by the migration, never changed | null |
| `source_of` | no | an external collection a scanner mirrors | null |

## 3. The `## Items` grammar

The body is split into `## ` sections outside fenced blocks (``` or ~~~); a `# ` title and `### ` sub-headings do not open sections. Exactly one `## Items` section is required: none → the document fails (`no `## Items` section`); two → it fails (`duplicate `## Items` heading (lines a, b)`) and the first is read. Other sections — `## Why`, `## Open questions`, `## Notes (owner)` and any other — are tolerated and returned by name in `sections`.

Inside `## Items`, line by line (fenced blocks skipped, blank lines ignored):

| Line | Reading |
|---|---|
| a list line (`- ` or `* `, any indentation) with **exactly one** wikilink whose target is `Sources/<rev>`, `Sources/<rev>/extract`, `Sources/<rev>/source`, optionally `.md`, `#fragment`, `\|display` (the `![[…]]` embed form counts as a link) | an **item**: `rev`, `file` (`extract` · `source` · null for the bare revision), `fragment`, `display`; the tail after the link, minus trailing punctuation and one dash (`—`, `–`, `-`), gives the **role** — the first `**…**` segment, normalized to lower case and single spaces — and the **note** (the rest, minus one more dash); without a leading `**…**` the role is null and the whole tail is the note; the role `excluded` sets `excluded: true` |
| an indented line (two spaces or more; a tab counts two) **without a link**, with or without a list marker, under an item | a continuation: appended to the item's note with a newline |
| a list line without a link at the top level, or before any item | **malformed**: `list line without a Sources/ link` |
| a list line with two or more wikilinks | **malformed**: `two or more links on one line` |
| one link whose target is empty, has a backslash, starts with `/`, contains a `.` or `..` segment, does not start with `Sources/`, names no revision, names a revision outside `^\d{4}-\d{2}-\d{2}_[0-9a-f]{8}(-\d+)?$`, or names a file other than `extract(.md)` / `source(.md)` | **malformed**, with the reason (`absolute path in link target` · `path traversal in link target` · `link outside Sources/ (another folder or another instance)` · `revision name malformed: …` · `unknown file in revision: …` · …) |
| a second item for a revision already listed | **malformed**: `duplicate revision <rev> (first at line n)` — the first stays an item |
| a non-list, non-indented line | **malformed**: `free text between items (no list marker)` |
| an indented line under a malformed line | **malformed**: `continuation under a malformed line` |

The raw target is classified **before** any resolver: nothing is normalized, nothing is looked up on disk. A malformed line fails the document; the other items still count and the line is named with its 1-based line number, its reason and its text (first 120 characters).

## 4. The reader record

`lib_research.read_collection_text(text, name, existing=None)` → one record; `scan_collections(vault, existing)` → the records of `Collections/*.md` sorted by name (dot-files skipped); `python3 bin/lib_research.py --vault <path> [--json]` prints them (`--json`: `{"schema": "research-interface/1", "collections": [...]}`; text: one `ok|fail|unreadable <name> — <status> · N items[ · K excluded][ · U unresolved][ · M malformed]` line, each error and malformed line under it, a summary line, exit 1 when any document is not `ok`); `--file <path>` reads one document without consulting a vault (`exists` and `unresolved` are null).

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

`state` is `fail` when `errors` or `malformed` is non-empty, `unreadable` when the file could not be read (then `errors` names the reason and every count is 0 — an unreadable document is never a clean one), `ok` otherwise. `exists` is resolved against the `existing` set the caller passes (`lib_vault` passes the names of `Sources/*/`); `unresolved` counts the items whose revision is absent. `links_outside_items` counts wikilinks in the other sections — they file nothing.

## 5. Derived relations and counters

`filed(rev)` (vault store §4, v1.4): a seed or draft note links `rev`, **or** `rev` is an item of any collection — any `status`, any role, `excluded` included (it was considered) — **or** (phase 3b) `rev` is named in a wiki page's `based_on`. `integrated` wins: a revision a `stable` note links is not counted as filed. A malformed line files nothing; an item whose revision does not exist files nothing.

`lib_vault.scan_vault()` carries `scan["collections"] = {"present": bool, "notes": [record…]}` and `counters()` gains, additively to `mnemon-doctor-data/1`:

| Key | Value |
|---|---|
| `collections` | documents in `Collections/` |
| `collections_fail` · `collections_unreadable` | documents with `state` fail · unreadable |
| `collections_status` | `{"open": n, "inbox": n, "closed": n, "unknown": n}` (`unknown` = no valid status) |
| `collection_items` · `collection_items_excluded` · `collection_items_malformed` · `collection_items_unresolved` | sums over the documents |
| `filed` | extended as above (the one existing key whose meaning changed) |

`lib_vault.py --status-counts` adds `collections=` and `collections_fail=` lines. The 19 checks, `summary`, `snapshot`, `diagnostics` and the brief line of `mnemon status --brief` are byte-for-byte what v1.3 produced on a vault without `Collections/` (the vault store suite proves it); `store/index` reads only the `- [[Sources/…` rows, so the new index section is never drift.

## 6. The index

`mnemon reindex` writes, in `index.md`: a `| Collections | N |` row in the Status table (always; 0 without the folder) and, only when `Collections/` exists, a `## Collections` section between `## Recent Sources` and `## Synthesis Notes`, one row per document sorted by name:

```
- [[Collections/<name>|<name>]] (<status>, <n> items[, <k> excluded][, <m> malformed])[ (fail: <first error>)] — <question>
- [[Collections/<name>|<name>]] (unreadable)
```

`<name>` and `<question>` pass through the index's `display_text` (a `|` is replaced). The counters line of `reindex` adds `Collections=N` when the folder exists. The rows are regenerated every time and never preserved as hand edits (only `- [[Sources/…` rows are).

## 7. Search — `mnemon-search/2`

`mnemon search --query <q> [--domain <d>] [--limit <n>] [--include-source] [--include-synthesis] [--include-collections] [--vault <path>] [--human]`

Targets: `Sources/*/extract.md` always; `Sources/*/source.md` with `--include-source`; `Synthesis/**/*.md` with `--include-synthesis`; `Collections/*.md` (dot-files skipped) with `--include-collections`. Matching, tokenization, the fallback and the ranking (rating desc, date desc, title) are v1's. `--include-wiki` is **reserved** for phase 3b (a usage error, exit 2, until then): wiki pages will form a separate block of the output with its own limit, ordered by `updated` desc then file name, excluding `suppressed` and `unbound` pages, with `## Atlas reading` cut from snippets, `orphan` pages only on a flag.

```json
{
 "schema": "mnemon-search/2",
 "query": "…", "tokens": ["…"], "provider": "grep", "configured_provider": "grep | qmd | null",
 "fallback_applied": false, "fallback_token": null, "count": 2,
 "results": [{
   "path": "Sources/<rev>/ | Synthesis/ | Collections/", "file": "extract.md | source.md | <note>.md",
   "result_type": "extract | source | synthesis | collection",
   "tier": "full | bookmark | null",
   "status": "<stored status> | null",
   "title": "…", "author": "…", "url": "…", "domains": [], "tags": [], "rating": "int | null", "created": "YYYY-MM-DD | ''",
   "snippet": "…"
 }]
}
```

- `result_type` is what the result is. `tier` is the **capture tier** of a Sources result (`extract` or `source`): `tier:` of the revision's `source.md`, else of its `extract.md` (a legacy placeholder), else `full`; it is `null` for every other result type. Until v2 the `tier` key carried the result type — a consumer that reads `tier` for the kind of result must read `result_type` now (the plugin's `source-search` skill does since 0.2.2).
- `status`: the stored `status:` of the file (`extracted`/`dismissed`/a legacy word for an extract; for a source result the paired extract's; `seed`/`draft`/`stable` for a synthesis note; `open`/`inbox`/`closed` for a collection), `null` when absent.
- A source result takes its metadata from the paired `extract.md` where that has any (v1); a collection result's `title` is its file stem, its `domains`/`tags`/`created` its frontmatter's.
- Deduplication: `extract` and `source` results are keyed by folder (extract wins); every other result type is keyed by folder + file — three collections in one folder are three results.
- `--domain` filters every result type by its `domains` (a collection's are its own; phase 3b wiki pages inherit the union of their collections').
- Exit codes: 0 · 2 for a usage error, a missing vault, a query with no usable token, a configuration error.
- `--human`: `<type>[ · tier: <tier>][ · status: <status>]` on the second line of each result.

## 8. The doctor

Phase 3a changes nothing in `mnemon doctor --data`: `mnemon-doctor-data/1`, its 19 checks, `--list`, the brief line and the exit codes are as in the vault store spec; only `counters` gains the keys of §5. Reserved for phase 3b: a document `research` with `"schema": "mnemon-doctor-research/1"`, embedded as `"research": {…}` next to `"data": {…}` in the `doctor --data --json` envelope and printed as `ok|warn|fail research/<check> — …` lines after the data lines; its checks are namespaced `research/<name>` (`--list research/<name>`); a `fail` there joins the aggregated exit 1 of `doctor --data`; `status --brief` keeps its policy (exit 0 after a run) and adds `research: N pages · K fail · M warn` to its line; one `--budget` covers both documents and a timeout marks the document it hit `not probed`. The checks it will carry: the collection schema and grammar (every document of §4 with `state` ≠ ok, as `fail` for schema and grammar, `warn` for unresolved items), the age of open collections (informational), and the wiki states of §9.

## 9. `wiki/1` — reserved for phase 3b

Declared here so the two branches agree on names; the reader, the validator and the states are phase 3b's. A `Wiki/<page>.md` carries, besides `type: wiki`, `kind` (`concept` · `comparison` · `contradiction` · `open-question`), `collections` (the supporting dossiers, `[[Collections/<name>]]`), `domains` (the union of theirs), `maintained_by`, `created`, `updated`, `visibility` and three **printed observations**: `based_on` — a list of `{rev, text_sha256, seal}` where `seal: capture` must equal the managed revision's `capture.text_sha256` and `seal: observed` is the hash observed at build time of a legacy revision (`observed` on a managed revision is `unbound`); `inputs` — `{page, kind, collections: {"Collections/<name>": "<sha256 of the file>"}, charter_sha256, protocol, template}` with `inputs.kind = kind` and `inputs.collections ⊆ collections`; `body_sha256` — sha256 of the **evidential body**: the text from the first heading after the frontmatter to the first of `## Atlas reading` / `## Log` (excluded), UTF-8, line endings normalized to LF, trailing spaces stripped. The body hash of `source.md` keeps the other rule — `lib_vault.body_bytes()`, exact bytes. Locators: `[[Sources/<rev>/source#<heading>]]` (the heading exists exactly once in the body of `source.md`) or a quoted fragment (`> "…" — [[Sources/<rev>/source]]`, a substring after whitespace normalization); the locator's revision ∈ `based_on`. Derived states, never stored: `stale` · `inputs-changed` · `uncited` · `orphan` · `edited-outside-update` · `weak-evidence` · `unbound` (reasons: a link outside the instance, a seal mismatch, `inputs.collections ⊄ collections`, `kind ≠ inputs.kind`, a missing target, a locator not found or not unique, `unlisted` — an evidential link to a revision outside `based_on`) · `suppressed` · `budget`. The **veto predicate**, one definition for the reader, the preflight, search and deletion: a page is suppressed ⇔ ∃ C ∈ `page.collections` with `C.status = open` and (`C.wiki = off` or the page's file name ∈ `C.suppressed_pages`); `inbox` and `closed` collections give no veto. Search (§7) excludes `suppressed` and `unbound` pages; `filed` (§5) counts `based_on`.

## 10. Configuration

No key of `mnemon config keys` changes in phase 3a. Reserved: `research_log_path` (phase 3c — a path for the research journal inside the vault, refused when it lies under `Wiki/`; absent → `<state>/mnemon/vaults/<key>/research-journal.jsonl`), `doctor_wiki_pages_per_collection` (default 5) and `doctor_wiki_words` (phase 3b — the `budget` state). The journal records (`mnemon-research-journal/1`, phase 3c) are append-only JSON lines with at least `ts`, `op` (`research-ask` · `research-update` · `research-propose`), `instance`, `page`, `inputs` (the package fingerprints), `outcome`.

## 11. Compatibility fixtures and ownership

`tests/fixtures/research/vault/` is the vault every branch reads the same way: five legacy revisions (one `tier: bookmark`, one superseded pair, one captured without an extract), a stable and a seed note, and seven collections — one valid `open` dossier with every accepted line form, a minimal `inbox` one, a `closed` one with the veto fields, and four that must fail (every rejected line form, a duplicate `## Items`, a broken frontmatter, a missing `## Items`). `tests/fixtures/research/expected/` holds the readings `generate.py` pins: `research.json` (the records of §4), `counters.json` (the keys of §5), `index.md` (the Status table and the section of §6), `search-default.json` · `search-all.json` · `search-domain.json` (§7). `tests/test_research.sh` runs `generate.py --check`, the unit tests of `tests/test_research.py`, the dispatcher on a copy of the fixture, the reader as a door, `init`'s scaffold and the Guide 4 canon. `generate.py --write` regenerates the goldens — only together with a change to this document.

| Shared file | Owner | Other branches |
|---|---|---|
| `bin/lib_research.py`, `tests/fixtures/research/`, `tests/test_research.*`, this document | phase 3a | phase 3b adds the `wiki/1` reader next to the collection one and extends the fixtures; phase 3-capture touches nothing here |
| `bin/lib_vault.py` (`scan_collections`, `counters`) | phase 3a | phase 3b adds `scan_wiki` and the `based_on` leg of `filed`; phase 3-capture adds `promoted` and the `tier` counters |
| `bin/knowledge-reindex.py` (`collection_rows`, the section) | phase 3a | phase 3b adds a `## Wiki` section after `## Collections` |
| `bin/mnemon-search.py` (`mnemon-search/2`) | phase 3a | phase 3-capture: bookmark results carry `tier: bookmark` (already read) and may add a `--tier` filter; phase 3b: `--include-wiki` and the wiki block — the schema stays 2 while the keys of §7 keep their meaning |
| `bin/vault-doctor.py`, the dispatcher's `_doctor_data` | phase 3a (unchanged) | phase 3b adds the `research` document (§8) |
| `vault-template/` (Guide 4) | phase 3a | phase 3b updates the `Wiki/` rows to the shipped operation; phase 3c adds the operations' doors |

Every PR against a shared file runs `tests/run-suites.sh` (on a Mac `--both`); the second branch to merge rebases and runs the union of the fixtures.

## 12. Non-goals (phase 3a)

No wiki reader, no printed observations verified, no `research` doctor document, no research operations, no journal, no scanner envelopes for collections, no dispatcher command for the reader (its door is `python3 $MNEMON_HOME/bin/lib_research.py`; the doctor's `research/collections` check is the phase 3b door), no authorship of `## Items` lines (replaced by the rule that agents and scanners only append), no qmd indexing of collections.

## 13. Status

v1, 2026-10-27 — the exit criterion of phase 3a: the reader and validator (`lib_research.py`), the extended `filed` and the collection counters, the index section, `mnemon-search/2`, Guide 4 and the `init` scaffold, the fixture vault and its goldens, the ownership table. Implementation on `feat/p3a-collections`.
