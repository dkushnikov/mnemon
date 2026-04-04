# Source Schema — source.md

The source file captures raw content exactly as received. It is **immutable** — never modified after creation.

## Frontmatter

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `source` |
| `source_type` | enum | Yes | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `content_format` | enum | Yes | `text`, `transcript`, `reference` |
| `origin` | enum | Yes | `url`, `text`, `youtube`, `audio`, `book`, `idea` |
| `url` | string | If URL-based | Canonical source URL |
| `author` | string | No | Source author or creator |
| `captured` | date | Yes | ISO date of capture (YYYY-MM-DD) |
| `captured_by` | string | Yes | `agent` (gateway) or `manual` |

### Origin → Source Type Mapping

| Origin | Default source_type | content_format |
|--------|-------------------|----------------|
| `url` | article | text |
| `youtube` | video | transcript |
| `audio` | podcast | transcript |
| `text` | article | text |
| `book` | book | text |
| `idea` | idea | text |

## Body

Raw captured content. For URL sources: fetched article text. For transcripts: full transcript with metadata header. For ideas: user's raw text.

## Immutability Rule

Once source.md is written, it is NEVER modified. If you need to re-capture:
- Same URL → same hash → collision handling (append -2)
- Updated content → new capture is a new source
