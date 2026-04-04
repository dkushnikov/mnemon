# Extract Schema — extract.md

The extract file contains AI-generated structured knowledge from a source. It is **mutable** — can be overwritten by re-extraction with updated reader context.

## Frontmatter

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `extract` |
| `source_type` | enum | Yes | Matches source.md |
| `content_format` | enum | Yes | Matches source.md |
| `origin` | enum | Yes | Matches source.md |
| `visibility` | enum | Yes | `personal`, `public`, `company`, `private` |
| `status` | enum | Yes | `captured`, `extracted`, `integrated` |
| `title` | string | Yes | Source title |
| `author` | string | No | Source author |
| `url` | string | No | Source URL |
| `created` | date | Yes | Source capture date |
| `extracted` | date | Yes | Extraction date |
| `language` | string | Yes | Content language code (en, ru, etc.) |
| `tags` | list | Yes | 3-8 topic tags, lowercase |
| `people` | list | No | People mentioned or referenced |
| `domains` | list | Yes | 1-3 domains from reader context |
| `rating` | integer | Yes | 1-10 quality/insight density score |
| `capture_context.vault` | string | Yes | Calling vault context |
| `capture_context.session` | string | No | Session name |
| `capture_context.intent` | string | No | Why captured |
| `capture_context.import_source` | string | Yes | `gateway`, `manual`, `batch` |
| `capture_context.import_batch` | string | No | Batch ID if batch import |

## Body Sections (required)

### Summary
2-3 sentences. Factual description. No opinion, no framing.

### Executive Summary
0.5-1 page. Framed by reader-context.md. Personal to the reader. Critical and honest. Written in the reader's preferred language.

### Key Ideas
Atomic claims, domain-tagged:
```
- **Bold Title** #domain/area — one-sentence claim (max 16 words)
```

### Connections
Wiki-style links: `- [[topic]] — why this connects`

### Raw Quotes
3-5 direct quotes from the source.

## Source-Type-Specific Sections

- **book**: adds `Central Thesis` section between Summary and Executive Summary
- **paper**: adds `Methodology Note` section between Summary and Executive Summary
- **conversation**: adds `Decisions` and `Action Items` sections before Executive Summary

## Rating Guide

| Score | Meaning |
|-------|---------|
| 1-3 | Low value — obvious, wrong, or heavily padded |
| 4-5 | Average — some useful points, nothing surprising |
| 6-7 | Good — multiple actionable insights |
| 8-9 | Excellent — changes thinking on the topic |
| 10 | Exceptional — reference-grade, revisit regularly |
