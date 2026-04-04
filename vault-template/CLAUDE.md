# Mnemon Knowledge Vault

Personal knowledge extraction vault. Sources are captured and extracted into structured knowledge artifacts.

## Structure

- `Sources/` — One folder per source: `YYYY-MM-DD_{hash8}/` with `source.md` + `extract.md`
- `Synthesis/` — Human-written notes connecting ideas across sources (never auto-generated)
- `reader-context.md` — Your personal context (role, domains, goals) that frames every extraction

## Principles

1. **source.md is immutable.** Once created, never modified. Re-capture = new folder.
2. **AI extracts, human synthesizes.** Claude creates source.md + extract.md. Only the user creates Synthesis notes.
3. **Key Ideas are atomic.** Each one stands alone, domain-tagged, max 16 words.
4. **Extraction is personal.** Every extract is framed by reader-context.md — not generic.

## Gateway Mode

When invoked non-interactively via `knowledge-gateway.sh` (`claude -p`):
- Skip ALL session onboarding, briefing, and questions
- Execute ONLY the requested action from the prompt
- Follow the EXTRACTION TEMPLATE embedded in the prompt exactly
- Print RESULT lines at the end

## Schemas

### source.md frontmatter
```yaml
type: source
source_type: <article|video|podcast|book|paper|idea|conversation>
content_format: <text|transcript|reference>
origin: <url|text|youtube|audio|book|idea>
url: "<source url>"
author: "<author>"
captured: <YYYY-MM-DD>
captured_by: agent
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
capture_context:
  vault: <context label>
  session: "<session>"
  intent: "<intent>"
  import_source: gateway
  import_batch: ""
```

### extract.md body sections
1. **Summary** — 2-3 sentences, factual
2. **Executive Summary** — framed by reader-context.md, personal
3. **Key Ideas** — `- **Title** #domain — claim (max 16 words)`
4. **Connections** — wiki-style links to related topics
5. **Raw Quotes** — 3-5 best quotes from source

## Don't

- Don't modify source.md after creation
- Don't create Synthesis notes without user request
- Don't create files outside Sources/ and Synthesis/
