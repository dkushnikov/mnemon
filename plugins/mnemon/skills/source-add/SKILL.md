---
name: source-add
description: Add a source to your Mnemon knowledge library — articles, YouTube videos, podcasts, PDFs, books, ideas. Captures content and generates a structured extract framed by your reader context. Use when the user provides a URL to save, says "add source", "capture this", "save this article/video/podcast", or wants to process any content into their knowledge library.
---

# /source-add — Capture a Source

Add a source to the knowledge library. The engine fetches the content, applies the extraction template for its source type, and writes `source.md` + `extract.md` under `Sources/` in the vault of the selected instance.

## Usage Examples

```
/source-add https://example.com/article
/source-add --url https://youtube.com/watch?v=abc123
/source-add --origin audio --url https://podcast.example.com/episode.mp3
/source-add --origin idea --title "My insight about X"
/source-add --origin book --title "Book Title" --author "Author Name"
/source-add --url https://spa-site.com/ --render     # JS-heavy SPA: pre-render via headless Chrome
/source-add --file ~/Downloads/paper.pdf             # local PDF: auto-detected, Read tool extracts
/source-add --url https://arxiv.org/pdf/2401.12345.pdf   # remote PDF: auto-detected, downloaded first
/source-add --instance work https://example.com/post # capture into another instance's vault
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--url <url>` | For URL sources | — | Source URL (YouTube / .pdf auto-detected) |
| `--file <path>` | For local files | — | Local file path (audio or PDF, auto-detected by extension) |
| `--origin <type>` | Auto-detected | From URL/file | `url`, `youtube`, `audio`, `pdf`, `text`, `book`, `idea` |
| `--title <title>` | For text/book/idea | Inferred | Source title |
| `--author <name>` | No | Inferred | Source author |
| `--source-type <type>` | No | From origin | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `--intent <text>` | No | none | Why the reader is capturing this |
| `--context <label>` | No | `personal` | Capture-context label recorded in the source frontmatter |
| `--depth <mode>` | No | `auto` | Depth-aware capture — fold in the richer same-entity artifact a URL fronts (e.g. the repository a post links to): `auto`, `off`, `force` |
| `--render` | No | off | Pre-render the URL via Chrome headless (SPAs / client-side-rendered pages). Requires Google Chrome or Chromium |
| `--dry-run` | No | off | Show the assembled prompt without extracting or writing anything |
| `--instance <name>` | No | default instance | Which Mnemon instance (vault) receives the source — see "Instance selection" |

## Instance selection

`--instance <name>` is a dispatcher option: it goes **before** the command (`mnemon --instance <name> add …`). Without it the engine resolves the instance in this order: `--config <path>` → `$MNEMON_CONFIG` → `--instance` / `$MNEMON_INSTANCE` → `./mnemon.yaml` in the working directory → the name stored in `~/.config/mnemon/default`.

A vault that should always capture into its own instance sets the variable in its `.claude/settings.json`:

```json
{ "env": { "MNEMON_INSTANCE": "<name>" } }
```

Sessions started in that vault then never need `--instance`. Add it only when the user names another library explicitly.

## PDF capture

Both local PDFs (`--file paper.pdf`) and remote PDFs (`--url https://.../x.pdf`) auto-detect as `origin=pdf` (source_type=paper). Remote PDFs are downloaded to a temporary location first; the original URL stays canonical in frontmatter.

Extraction uses Claude Code's native `Read` tool — no pypdf/pdftotext preprocessing. This handles text, layout, tables, and image/OCR content that CLI tools would miss. No `--render` / `--whisper` needed.

Page count lands in frontmatter (`pages: N`). For PDFs with heavy image/figure content, the extract describes the visual layout where relevant.

## When to use `--render`

A plain HTTP fetch returns an empty `<div id="root">` for client-side-rendered apps (React/Vue/Svelte SPAs). Symptoms that call for a retry with `--render`:

- Extract is suspiciously short (hero/tagline only, no body content)
- Source is a landing page, docs site, or dashboard built as a SPA
- The page looks fine in a browser but the extract is mostly meta tags

Pass `--render` and Mnemon pre-renders via Chrome headless (~2-5s latency), keeping the URL canonical in frontmatter. Falls back with a clear error if Chrome/Chromium isn't installed.

Do NOT use `--render` reflexively — the default fast path is fine for 90% of web content. Use it when you see SPA symptoms.

## How to Execute

1. Build the command from the user's input. A bare URL is enough — origin and source type are auto-detected.

2. Run the dispatcher via the Bash tool. This is the only path form to use — never a dev checkout, never a script bundled with this plugin:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] add --url "<url>" [--origin <origin>] [--title "<title>"] [--author "<author>"] [--intent "<intent>"] [--depth <mode>] [--render]
```

3. Parse the `RESULT:` lines from the output:
   - `RESULT:path=Sources/<folder>/` — created source path, relative to the instance's vault
   - `RESULT:status=extracted` — extraction status (`skipped-duplicate` when the canonical URL already exists in the library)
   - `RESULT:title=<title>` — extracted title

4. Show the user a concise summary:

```
Added: <title>
Path: <path>
Status: <status>
```

5. If the command fails, show the error message. Common issues:
   - `No such file or directory` on the dispatcher path → the engine is not installed at `$MNEMON_HOME` (default `~/.local/share/mnemon/engine`); follow `docs/install.md` in the Mnemon repository
   - no instance / config not found → `mnemon init <name> --vault <path>` creates one (add `--default` to make it the default), or set `MNEMON_INSTANCE`
   - "Vault not found" → run `mnemon doctor`, then check `vault_path` in `~/.config/mnemon/instances/<name>.yaml`
   - media extraction failed → `mnemon doctor` reports the missing optional dependency (`yt-dlp`, `ffmpeg`, `whisper`)

## For text/idea origins

If the user wants to capture text or an idea, they need to provide the content. Ask them to paste it, then feed it to the dispatcher on stdin:

```bash
printf '%s\n' "<pasted content>" | "${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" add --origin text --title "<title>"
```

For long texts write the content to a temporary file and `cat` it into the pipe. Never paraphrase or shorten it — the engine stores the text verbatim as `source.md`; only `extract.md` is generated.
