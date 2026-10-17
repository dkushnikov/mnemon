# Source Schema — source.md

The source file holds the captured text exactly as the engine produced it. It is **immutable** — never modified after publication: its body is the capture-text byte for byte, `capture.text_sha256` is its hash, and the engine reports an edited or missing body as `integrity-error`. The normative definition is the capture contracts, [`docs/specs/2026-10-15-capture-contracts.md`](../docs/specs/2026-10-15-capture-contracts.md) §3.3 (the file) and §3.1 (identity), with the capture layer's additive keys in [`docs/specs/2026-10-15-capture-layer.md`](../docs/specs/2026-10-15-capture-layer.md) §4; this page is the short form.

The frontmatter is written by the gateway before any model runs; the model never writes a line of it.

## Frontmatter

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `source` |
| `source_type` | enum | Yes | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation`, `website` (a site capture), `document` (a vault reference) |
| `content_format` | enum | Yes | `text`, `transcript`, `pdf`, `markdown` (a converted document), `reference` |
| `origin` | enum | Yes | `url`, `youtube`, `audio`, `pdf`, `file`, `text`, `book`, `idea`, `ref:vault` |
| `url` | string | URL origins | The URL as given |
| `canonical_url` | string | URL origins | The computed identity of the resource — canonicalization v1: tracking parameters and plain fragments dropped, everything else kept (`capture.canonical_version`) |
| `title` | string | No | `--title`, else the acquired page / PDF / media title |
| `author` | string | No | `--author`, else the acquired author |
| `captured` | date | Yes | ISO date of capture (YYYY-MM-DD) |
| `captured_by` | string | Yes | `agent` — the gateway; legacy hand-made files may say `manual` |
| `capture_id` | string | Yes | The capture operation's id (a feeder's `--capture-id`, else generated); the receipt in the vault's ledger is keyed by it |
| `resource_kind` | enum | Yes | `url`, `text`, `file`, `ref` |
| `resource_sha256` | string | Yes | **The identity**: `sha256("<kind>:" + key)` — a URL's canonical form, a text's hash, a file's hash, a reference's path |
| `resource_id` | string | Yes | The first 8 hex characters of `sha256(key)` (the key alone, without the kind prefix) — the folder label `hash8`, never the identity; two resources may share it |
| `revision` | string | Yes | The folder name, `YYYY-MM-DD_<hash8>[-n]` |
| `supersedes` | string or list | Re-captures | The revision(s) this one replaces — one as a scalar, several (one re-capture resolving a group of duplicates) as a YAML block list; `mnemon reindex` marks them `⤳ superseded` |
| `archive` | string | When the raw is archived | The raw's file name in the instance's `archive_dir` (`<revision>.<ext>`) |
| `origin_path` / `ref_path` | string | `--file` / `ref:vault` | The local file or the vault note the capture came from |
| `context` | string | Yes | The capture context — one of the contexts the instance charter declares (`## Contexts`); the gateway refuses an undeclared one |
| `import_source`, `import_ref`, `import_batch` | string | Inbox / batch | The producer label, the envelope id, the batch id |
| `pages` | integer | PDFs | From `pdfinfo` when available |
| `final_url`, `declared_canonical_url`, `published`, `arxiv_version`, `media.*`, `transcript_language`, `folded_sources`, `site_pages`, … | — | When known | The acquisition's metadata lines (the capture layer §4.1, §4.3) |
| `capture` | block | Yes (managed captures) | How the text was produced — see below |

### The `capture` block

| Key | Values |
|---|---|
| `method` | `direct+defuddle`, `direct+pandoc`, `render+dom-text`, `site+mixed`, `pdftotext`, `pandoc:<format>`, `captions:<lang>:<manual\|auto>`, `whisper:<model>`, `stdin`, `ref-vault`, `depth-composite`, … |
| `fidelity` | `exact` (the given text), `converted` (a page, a PDF, a document), `transcribed` (media) |
| `producer_fidelity` | A producer's own claim when the capture came through the inbox (`exact`, `embed`, `model-transcribed`, `url-only`) |
| `raw_store`, `raw_kind`, `raw_sha256`, `raw_bytes` | Where the original is kept (`archive`, `state`, `none`) and what it is (`response`, `rendered`, `composite`, `transcript`, `file`, `site`) |
| `acquisition` | What the fetch got: `ok`, `thin`, `js-shell`, `bot-wall`, `forbidden`, `login`, `paywall`, … |
| `completeness` | What the conversion kept, checked against the saved page: `complete`, `partial` (with `missing_lines`, `glued_lines`, `headings_missing`, `repairs`), `blocked` (a paywall teaser), `unknown` (nothing to check against: PDFs, media, plain text). A diagnostic, not a gate |
| `egress` | `external` when a vendor fetcher got the page (only with `external_fetch: auto`) |
| `canonical_version` | `1` |
| `text_sha256`, `text_bytes` | The body's hash and size — the integrity anchor |

### Origin → defaults

| Origin | Default `source_type` | `content_format` | Text comes from |
|--------|-------------------|----------------|---|
| `url` | article (`paper` for arXiv, `website` for `--site`) | text | the fetch chain → `defuddle` / `pandoc` / the DOM text |
| `youtube` | video | transcript | captions by the video's language, `whisper` last |
| `audio` | podcast | transcript | `whisper` |
| `pdf` | paper | pdf | `pdftotext -layout` (no OCR: a scan without a text layer fails as `no-text`) |
| `file` | article | markdown or text | `pandoc` for the formats it reads, the bytes for plain text |
| `text` | article | text | stdin, verbatim |
| `book` | book | text | stdin, verbatim |
| `idea` | idea | text | stdin, verbatim |
| `ref:vault` | document | reference | the referenced note |

`--source-type` overrides the default.

## Body

The capture-text, byte for byte: the converted page, the PDF's text, the transcript (with its metadata header), or the given text. A site capture's body is a composite — `# <landing title> — site capture`, then `## Part n — <url> (<role>)` per converted page. The body is never edited; a durable note belongs in `index_note:` of `extract.md`.

## Legacy files

Captures made before the contracts (2026-10) carry none of `capture_id`, `revision`, `resource_sha256`, `capture.text_sha256`. They are **legacy**: matched by URL for deduplication, never rewritten, counted apart by `mnemon doctor --data`. A file with some of the four keys is **damaged** and a doctor `fail`.

## Immutability and re-capture

Once `source.md` is published it is never modified. The same resource again (same canonical URL, same text, same file) answers `skipped-duplicate` and writes nothing; a newer version of the resource is `mnemon add --recapture …`: a **new** folder (`-2`, `-3`…) whose `supersedes:` names the old revision(s), which stay where they are.
