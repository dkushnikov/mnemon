# Mnemon Knowledge Vault

Personal knowledge extraction vault. Sources are captured and extracted into structured knowledge artifacts.

## Structure

- `Sources/` — One folder per captured revision: `YYYY-MM-DD_{hash8}/` (`-2`, `-3`… for further revisions of the same resource) with `source.md` + `extract.md`
- `Synthesis/` — Human-written notes connecting ideas across sources (never auto-generated)
- `reader-context.md` — Your personal context (role, domains, goals) that frames every extraction

## Principles

1. **source.md is immutable.** Once published, never modified — its body is the captured text byte for byte, and its `capture.text_sha256` lets the engine detect an edit (`integrity-error`). Re-capture = new folder (`mnemon add --recapture`).
2. **AI extracts, human synthesizes.** The engine publishes source.md (what was captured) and extract.md (validated model output). Only the user creates Synthesis notes.
3. **Key Ideas are atomic.** Each one stands alone, domain-tagged, max 16 words.
4. **Extraction is personal.** Every extract is framed by reader-context.md — not generic.

## How captures land

`mnemon add` (the gateway) fetches the content itself — `curl` and `defuddle` / `pandoc` for pages, `pdftotext` for PDFs, `yt-dlp` / `whisper` for media, or the text it is given — and publishes `Sources/<revision>/source.md` with that text. It then runs a fenced extractor (`claude -p --restricted --tools "" --no-session-persistence --json-schema …`, prompt on stdin, no tools) that returns structured fields, validates them and publishes `extract.md`. The model never writes into the vault, and a session started in this vault is never the extractor: `RESULT:` lines and exit codes are computed from the files by the gateway. Automated feeders drop envelopes into an inbox directory for `mnemon ingest <dir>`.

## Schemas

### source.md frontmatter
```yaml
type: source
source_type: <article|video|podcast|book|paper|idea|conversation>
content_format: <text|transcript|pdf|reference|markdown>
origin: <url|text|youtube|audio|pdf|file|book|idea|ref:vault>
url: "<source url>"               # when known
title: "<title>"                  # --title or the acquired page / PDF title
author: "<author>"
captured: <YYYY-MM-DD>
captured_by: agent
capture_id: "<capture operation id>"
resource_kind: <url|text|file|ref>
resource_sha256: "<identity of the captured resource>"
resource_id: "<hash8 — the folder label>"
revision: "<YYYY-MM-DD_hash8[-n]>"
context: "<capture context, declared in the charter>"
capture:                          # how the text was produced; never written by the model
  method: "<curl+defuddle|curl+pandoc|pdftotext|stdin|…>"
  fidelity: <exact|converted|transcribed>
  raw_store: <archive|state|none>
  text_sha256: "<sha256 of the body>"
  text_bytes: <n>
```

### extract.md frontmatter
```yaml
type: extract
source_type: <matches source>
content_format: <matches source>
origin: <matches source>
visibility: <personal|public|company|private>
status: <captured|extracted|integrated>
title: "<title>"
author: "<author>"
url: "<url>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <en|ru|...>
tags: [<topic tags>]
people: [<mentioned people>]
domains: [<domain tags>]
rating: <1-10>
capture_id: "<matches source>"
revision: "<matches source>"
capture_context:
  vault: <instance name>
  session: "<session>"
  intent: "<intent>"
  import_source: gateway
  import_batch: ""
extractor:                        # written by the gateway: schema, model, Claude Code version, template and input hashes
  schema: extract/1
  model: <model>
  claude_code: "<version>"
  template: <source type>
```

### extract.md body sections
1. **Summary** — 2-3 sentences, factual
2. **Executive Summary** — framed by reader-context.md, personal
3. **Key Ideas** — `- **Title** #domain — claim (max 16 words)`
4. **Connections** — wiki-style links to related topics
5. **Raw Quotes** — 3-5 best quotes from source

## Don't

- Don't modify source.md after creation (the engine detects an edited body as `integrity-error`)
- Don't write Sources/ folders by hand — `mnemon add` publishes them with their receipts
- Don't create Synthesis notes without user request
- Don't create files outside Sources/ and Synthesis/
