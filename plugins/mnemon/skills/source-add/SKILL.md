---
name: source-add
description: Add a source to your Mnemon knowledge library — articles, YouTube videos, podcasts, PDFs, books, ideas. Captures content and generates a structured extract framed by your reader context. Use when the user provides a URL to save, says "add source", "capture this", "save this article/video/podcast", or wants to process any content into their knowledge library.
---

# /source-add — Capture a Source

Add a source to the knowledge library. The engine acquires the content itself, publishes it as `source.md` under `Sources/` in the vault of the selected instance, then runs the fenced extractor and publishes `extract.md`. This session is a caller: it never fetches, never writes into the vault, never extracts.

## Usage Examples

```
/source-add https://example.com/article
/source-add --url https://youtube.com/watch?v=abc123
/source-add --origin audio --url https://podcast.example.com/episode.mp3
/source-add --origin idea --title "My insight about X"
/source-add --origin book --title "Book Title" --author "Author Name"
/source-add --url https://spa-site.com/ --render     # JS-heavy SPA: pre-render via headless Chrome
/source-add --file ~/Downloads/paper.pdf             # local PDF: auto-detected, text via pdftotext
/source-add --url https://arxiv.org/pdf/2401.12345.pdf   # arXiv: resolved to the paper, full text of the pinned version
/source-add --instance work https://example.com/post # capture into another instance's vault
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--url <url>` | For URL sources | — | Source URL (YouTube / .pdf / arXiv auto-detected) |
| `--file <path>` | For local files | — | Local file path (audio, PDF or a document, auto-detected by extension) |
| `--origin <type>` | Auto-detected | From URL/file | `url`, `youtube`, `audio`, `pdf`, `file`, `text`, `book`, `idea` |
| `--title <title>` | For text/book/idea | Acquired | Source title |
| `--author <name>` | No | Acquired | Source author |
| `--source-type <type>` | No | From origin | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `--intent <text>` | No | none | Why the reader is capturing this |
| `--context <label>` | No | the charter's default | Capture context — one of the contexts the instance charter declares (`_meta/charter.md`, section `## Contexts`); an undeclared value is refused (exit 2). The default is `personal` when the charter declares it, else the first declared context; a vault without a charter accepts `personal` only. Never invent a context — pass one only when the user names it |
| `--depth <mode>` | No | `auto` | Depth-aware capture — fold in the richer same-entity artifact a URL fronts (e.g. the repository a post links to): `auto`, `off`, `force` |
| `--render` | No | off | Force the rendered stage (headless Chrome) for SPAs / client-side-rendered pages. Requires Google Chrome or Chromium |
| `--recapture` | No | off | A new revision of a resource that is already in the library (the new `source.md` names the old revision in `supersedes:`) |
| `--tier bookmark` | No | `full` | A bookmark card instead of a full capture: no fetch, no model, `source.md` with `tier: bookmark` (title, url, intent) and no `extract.md`; for a URL or a text, never a file. Use it when the user says "save for later", "bookmark", "just keep the link". A later full `add` of the same URL promotes it (the new revision supersedes the card); `mnemon triage list | promote | dismiss` is the queue |
| `--canonical-url <url>` | No | — | With `--file` / a text: identify the resource by this document's URL (its canonical form), not by the file's bytes — a Google Doc or Notion page exported to a file twice is one resource (the second export is `skipped-duplicate`; `--recapture` makes the next revision). Not with `--url` |
| `--resume <revision>` | No | — | Finish a capture whose source was published but whose extract was not (`RESULT:status=captured`, exit 4) |
| `--dry-run` | No | off | Show what would happen — the resolved request, the extractor argv and the prompt — without fetching, extracting or writing anything |
| `--instance <name>` | No | default instance | Which Mnemon instance (vault) receives the source — see "Instance selection" |

## Instance selection

`--instance <name>` is a dispatcher option: it goes **before** the command (`mnemon --instance <name> add …`; after the command it is refused). It outranks anything inherited from the environment. Without it the engine resolves the instance in this order: `--config <path>` → `--instance <name>` → `$MNEMON_CONFIG` → `$MNEMON_INSTANCE` → `./mnemon.yaml` in the working directory → the name stored in `~/.config/mnemon/default`. If `$MNEMON_CONFIG` and `$MNEMON_INSTANCE` are both set and disagree, the engine exits 2 with `conflicting selectors` instead of guessing — pass `--instance <name>` explicitly.

A vault that should always capture into its own instance sets the variable in its `.claude/settings.json`:

```json
{ "env": { "MNEMON_INSTANCE": "<name>" } }
```

Claude Code sessions started in that vault then never need `--instance`. Add it only when the user names another library explicitly.

**Scripts, jobs and callers other than Claude Code** (a cron job, a producer, a shell alias, another agent runtime): the `.claude/settings.json` binding is read by Claude Code sessions only — nothing else sees it. Such a caller passes `--instance <name>` or `--config <path>` before the command on every invocation (`mnemon --instance work add --url …`), or exports `MNEMON_INSTANCE` itself; without a selector the engine falls back to `~/.config/mnemon/default`, which may be another library.

## PDF capture

Both local PDFs (`--file paper.pdf`) and remote PDFs (`--url https://.../x.pdf`) auto-detect as `origin=pdf` (source_type=paper). Remote PDFs are fetched under the engine's network policy; the original URL stays canonical in frontmatter.

The text comes from `pdftotext -layout` (Poppler) and is published as `source.md` **byte for byte**; the extractor reads that text and nothing else. There is no OCR and no visual reading of pages: a scanned PDF without a text layer fails as `no-text` (`RESULT:status=failed`, nothing published), figures are absent, and tables survive only as the text layout `pdftotext` produces. `pdftotext` must be installed (`brew install poppler`); without it the capture is refused before anything is published (`error_class=environment`, exit 3). The page count lands in frontmatter (`pages: N`) when `pdfinfo` (same package) is available. Completeness of a PDF capture is `unknown` — there is no page to check the text against.

## When to use `--render`

A plain HTTP fetch returns an empty `<div id="root">` for client-side-rendered apps (React/Vue/Svelte SPAs). The engine's chain already tries the rendered stage on its own when the direct fetch comes back as a JavaScript shell (`RESULT:acquisition=js-shell`); pass `--render` when you see SPA symptoms the chain did not catch:

- Extract is suspiciously short (hero/tagline only, no body content)
- Source is a landing page, docs site, or dashboard built as a SPA
- The page looks fine in a browser but the extract is mostly meta tags

Pass `--render` and the engine renders via Chrome headless (~2-5s latency), keeping the URL canonical in frontmatter. It fails with a clear error (`environment`) if Chrome/Chromium isn't installed.

Do NOT use `--render` reflexively — the default fast path is fine for 90% of web content. Use it when you see SPA symptoms.

## How to Execute

1. Build the command from the user's input. A bare URL is enough — origin and source type are auto-detected.

2. Run the dispatcher via the Bash tool. This is the only path form to use — never a dev checkout, never a script bundled with this plugin:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] add --url "<url>" [--origin <origin>] [--title "<title>"] [--author "<author>"] [--intent "<intent>"] [--depth <mode>] [--render] [--recapture]
```

3. Read the `RESULT:` lines and the exit code. The engine computes both from the files it published — **never report a full capture from exit 0 alone**: exit 0 also means `skipped-duplicate`, and `RESULT:status` is the fact.

| `RESULT:status=` | Exit | What happened | What to tell the user |
|---|---|---|---|
| `extracted` | 0 | `source.md` and `extract.md` are published under `RESULT:path=` | Added — title, path, and the completeness (below) |
| `captured` | 4 | `source.md` is published, `extract.md` is not: the extractor failed, its output was invalid, or it refused (`RESULT:error_class=`, e.g. `fence-unprobed`, `unsupported-size`, `budget-exhausted`) | Captured, not yet extracted. Finish it with `mnemon [--instance <name>] add --resume <RESULT:revision>`; when the class is `fence-unprobed`, `mnemon fence-probe` first |
| `bookmarked` | 0 | `--tier bookmark`: `source.md` with the card is published, no `extract.md`; `RESULT:tier=bookmark` | Bookmarked — title and path; a full read later is `mnemon add --url <url>` (it promotes the card) or `mnemon triage promote <revision>` |
| `skipped-duplicate` | 0 | The resource is already in the library (same canonical URL, text or file hash); `RESULT:path=` is the existing revision (`RESULT:tier=bookmark` when that revision is a bookmark card; `RESULT:judgement=dismissed` when the reader dismissed it in the triage) | Already in the library at that path — nothing new was written. A newer version of the page is a `--recapture`; a bookmarked resource is promoted by a plain full `add`; a dismissed one — say so, and promote only if the user wants it read after all |
| `failed` | 1 | Nothing was published: acquisition or conversion failed — `RESULT:error_class=` `blocked` (every wall: a bot wall, a login wall, a paywall without text, a 403, a rate limit — `RESULT:acquisition=` names which), `fetch`, `timeout`, `convert`, `no-text` (a PDF without a text layer), `unsupported` (a file type no converter reads), `input-missing`, `invalid-utf`…; `RESULT:retryable=` says whether a later try may succeed | Not captured, with the class; suggest `--render` when the acquisition says `js-shell`, a retry later for a retryable class, nothing for a wall or a scanned PDF |
| `failed` | 3 | Nothing was published because the environment lacks a tool: `RESULT:error_class=environment` (Poppler for a PDF, Chrome for `--render`, a media tool) or `fence-unprobed` (no `claude`, too old for `--restricted`, or no fence record for its version) | Read the class and the engine's message: install the named tool (`mnemon doctor` lists them), or for `fence-unprobed` run `mnemon fence-probe` (once per Claude Code version); then retry |
| `integrity-error` | 1 | A previously published `source.md` is missing or was edited (`capture.text_sha256` no longer matches) | An operator problem with the existing revision, not a capture failure — report it, do not retry |
| — | 2 | Usage or contract: an undeclared `--context`, conflicting selectors, an invalid URL, `--resume` of a complete capture, `--supersedes` of a missing revision | The engine's message, verbatim |
| `locked` | 75 | The vault or the engine is locked — another writer holds the vault lock (a capture, an ingest pass, `reindex`, `triage dismiss|restore`, a `research` writer) or a `mnemon release` is updating the engine; the vault case prints `RESULT:status=locked`, `error_class=locked`, `retryable=true` (the engine case prints no RESULT lines) — nothing was written | Retry in a moment |

The recovery follows `RESULT:error_class` and the engine's message, never the exit code alone: exit 3 is "a tool is missing", and which tool is in the message.

`RESULT:completeness=` (URL captures) says how much of the fetched page the conversion kept — `complete`, `partial` (something was lost), `blocked` (a paywall teaser: only the text before the wall), `unknown` (nothing to check against: PDFs, media, plain text). It is a diagnostic, not a gate: a `partial` or `blocked` source **is** published — say so, the reader must know what they are reading. `RESULT:acquisition=` (`ok`, `thin`, `js-shell`, `bot-wall`, `paywall`, …) says what the fetch got. Other lines: `RESULT:revision=` (the folder name, the argument of `--resume`), `RESULT:title=`, `RESULT:canonical_url=`.

4. Show the user a concise summary — one block, the facts from the `RESULT:` lines:

```
Added: <RESULT:title>
Path: <RESULT:path>
Status: <RESULT:status> · completeness <RESULT:completeness>   (when printed)
Next: <only when the status calls for one — the --resume command, fence-probe, --recapture>
```

5. If the command fails before any `RESULT:` line, show the error message. Common issues:
   - `No such file or directory` on the dispatcher path → the engine is not installed at `$MNEMON_HOME` (default `~/.local/share/mnemon/engine`); follow `docs/install.md` in the Mnemon repository
   - no instance / config not found → `mnemon init <name> --vault <path>` creates one (add `--default` to make it the default), or set `MNEMON_INSTANCE`
   - "Vault not found" → run `mnemon doctor`, then check `vault_path` in `~/.config/mnemon/instances/<name>.yaml`
   - `environment` on a PDF → `brew install poppler`; on media → `mnemon doctor` reports the missing optional dependency (`yt-dlp`, `ffmpeg`, `whisper`); on `--render` → Google Chrome or Chromium

## For text/idea origins

If the user wants to capture text or an idea, they need to provide the content. Ask them to paste it, then feed it to the dispatcher on stdin:

```bash
printf '%s\n' "<pasted content>" | "${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" add --origin text --title "<title>"
```

For long texts write the content to a temporary file and `cat` it into the pipe. Never paraphrase or shorten it — the engine stores the text verbatim as `source.md`; only `extract.md` is generated.
