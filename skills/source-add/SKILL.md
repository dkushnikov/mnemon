---
name: source-add
description: Add a source to your Mnemon knowledge library — articles, YouTube videos, podcasts, books, ideas. Captures content and generates a structured extract with personal context framing. Use when the user provides a URL to save, says "add source", "capture this", "save this article/video/podcast", or wants to process any content into their knowledge library.
---

# /source-add — Capture a Source

Add a source to the knowledge library. The gateway fetches content, applies an extraction template, and generates structured `source.md` + `extract.md` in the vault.

## Usage Examples

```
/source-add https://example.com/article
/source-add --url https://youtube.com/watch?v=abc123
/source-add --origin audio --url https://podcast.example.com/episode.mp3
/source-add --origin idea --title "My insight about X"
/source-add --origin book --title "Book Title" --author "Author Name"
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--url <url>` | For URL sources | — | Source URL (YouTube auto-detected) |
| `--origin <type>` | Auto-detected | From URL | `url`, `youtube`, `audio`, `text`, `book`, `idea` |
| `--title <title>` | For text/book/idea | Inferred | Source title |
| `--author <name>` | No | Inferred | Source author |
| `--source-type <type>` | No | From origin | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `--intent <text>` | No | none | Why you're capturing this |
| `--context <ctx>` | No | personal | `personal` or `mc` |

## How to Execute

1. Build the command from user's input. If the user just provides a URL, that's sufficient — origin and source_type are auto-detected.

2. Call the gateway via the Bash tool:

```bash
~/Mnemon/bin/knowledge-gateway.sh source-add --url "<url>" [--origin <origin>] [--title "<title>"] [--author "<author>"] [--intent "<intent>"]
```

3. Parse RESULT lines from the output:
   - `RESULT:path=Sources/<folder>/` — created source path
   - `RESULT:status=extracted` — extraction status  
   - `RESULT:title=<title>` — extracted title

4. Show the user a concise summary:

```
Added: <title>
Path: <path>
Status: <status>
```

5. If the gateway fails, show the error message. Common issues:
   - "No mnemon.yaml found" → run `~/Mnemon/setup.sh <vault-path>` first
   - "Vault not found" → check vault_path in mnemon.yaml
   - Media extraction failed → check yt-dlp/whisper installation

## For text/idea origins

If the user wants to capture text or an idea, they need to provide the content. Ask them to paste it, then pipe it to the gateway:

```bash
echo "<pasted content>" | ~/Mnemon/bin/knowledge-gateway.sh source-add --origin text --title "<title>"
```
