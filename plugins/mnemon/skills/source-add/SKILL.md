---
name: source-add
description: Add a source to your Mnemon knowledge library — articles, YouTube videos, podcasts, PDFs, books, ideas, screenshots. Captures content and generates a structured extract framed by your reader context. Use when the user provides a URL to save, hands over a screenshot or an image file, says "add source", "capture this", "save this article/video/podcast", "save this screenshot", or wants to process any content into their knowledge library.
---

# /source-add — Capture a Source

Add a source to the knowledge library. The engine acquires the content itself, publishes it as `source.md` under `Sources/` in the vault of the selected instance, then runs the fenced extractor and publishes `extract.md`. This session is a caller: it never fetches, never writes into the vault, never extracts.

## Usage Examples

```
/source-add https://example.com/article
/source-add --url https://youtube.com/watch?v=abc123
/source-add --origin audio --url https://podcast.example.com/episode.mp3
/source-add --origin idea --title "My insight about X"
/source-add --origin book --file ~/Books/title.epub       # a book is a document file; a page about a book: --url <url> --source-type book
/source-add --url https://spa-site.com/ --render     # JS-heavy SPA: pre-render via headless Chrome
/source-add --file ~/Downloads/paper.pdf             # local PDF: auto-detected, text via pdftotext
/source-add --url https://arxiv.org/pdf/2401.12345.pdf   # arXiv: resolved to the paper, full text of the pinned version
/source-add --origin image --file ~/Desktop/shot.png --ocr-languages rus+eng   # a screenshot: explicit only — see "Image or screenshot"
/source-add --instance work https://example.com/post # capture into another instance's vault
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--url <url>` | For URL sources | — | Source URL (YouTube / .pdf / arXiv auto-detected) |
| `--file <path>` | For local files | — | Local file path (audio, PDF or a document, auto-detected by extension; an image only with `--origin image`) |
| `--origin <type>` | Auto-detected | From URL/file | `url`, `youtube`, `audio`, `pdf`, `file`, `text`, `book` (a document file: needs `--file`; a page about a book is `--url … --source-type book`, notes are `--origin text --source-type book`), `image` (an upright PNG or JPEG screenshot: needs `--file`, never auto-detected — see "Image or screenshot"), `idea` |
| `--ocr-languages <seq>` | For an image | the instance's `ocr_languages` | With `--origin image` only: the languages of the text in the image, for the local recognizer that witnesses the reading — tesseract codes joined by `+`, the main language first (`eng`, `rus+eng`) |
| `--title <title>` | For text/book/idea | Acquired | Source title |
| `--author <name>` | No | Acquired | Source author |
| `--source-type <type>` | No | From origin | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `--intent <text>` | No | none | Why the reader is capturing this |
| `--session <title>` | Yes, from a session | `$MNEMON_SESSION`, else empty | The session the capture is made in — a short title of this conversation. The engine records it in the receipt and in `extract.md` (`capture_context.session`); a replay or `--resume` keeps the session recorded the first time, whatever the later call passes. Pass it on every `add` and `browser-lane` command; a caller whose environment sets `MNEMON_SESSION` may leave it out |
| `--context <label>` | No | the charter's default | Capture context — one of the contexts the instance charter declares (`_meta/charter.md`, section `## Contexts`); an undeclared value is refused (exit 2). The default is `personal` when the charter declares it, else the first declared context; a vault without a charter accepts `personal` only. Never invent a context — pass one only when the user names it |
| `--depth <mode>` | No | `auto` | Depth-aware capture — fold in the richer same-entity artifact a URL fronts (e.g. the repository a post links to): `auto`, `off`, `force` |
| `--render` | No | off | Force the rendered stage (headless Chrome) for SPAs / client-side-rendered pages. Requires Google Chrome or Chromium |
| `--recapture` | No | off | A new revision of a resource that is already in the library (the new `source.md` names the old revision in `supersedes:`) |
| `--tier bookmark` | No | `full` | A bookmark card instead of a full capture: no fetch, no model, `source.md` with `tier: bookmark` (title, url, intent) and no `extract.md`; for a URL or a text, never a file. Use it when the user says "save for later", "bookmark", "just keep the link". A later full `add` of the same URL promotes it (the new revision supersedes the card); `mnemon triage list | promote | dismiss` is the queue |
| `--canonical-url <url>` | No | — | With `--file` / a text: identify the resource by this document's URL (its canonical form), not by the file's bytes — a Google Doc or Notion page exported to a file twice is one resource (the second export is `skipped-duplicate`; `--recapture` makes the next revision). Not with `--url` |
| `--resume <revision>` | No | — | Finish a capture whose source was published but whose extract was not (`RESULT:status=captured`, exit 4) |
| `--dry-run` | No | off | Show what would happen — the resolved request, the extractor argv and the prompt — without fetching, extracting or writing anything; for an image also the witness's probe and whether the fence probe record holds the image entry — not whether a language model loads or `claude` is logged in (exit 0 even when the run would exit 3) |
| `--instance <name>` | No | default instance | Which Mnemon instance (vault) receives the source — see "Instance selection" |

## Instance selection

`--instance <name>` is a dispatcher option: it goes **before** the command (`mnemon --instance <name> add …`; after the command it is refused). It outranks anything inherited from the environment. Without it the engine resolves the instance in this order: `--config <path>` → `--instance <name>` → `$MNEMON_CONFIG` → `$MNEMON_INSTANCE` → `./mnemon.yaml` in the working directory → the name stored in `~/.config/mnemon/default`. If `$MNEMON_CONFIG` and `$MNEMON_INSTANCE` are both set and disagree, the engine exits 2 with `conflicting selectors` instead of guessing — pass `--instance <name>` explicitly.

A vault that should always capture into its own instance sets the variable in its `.claude/settings.json`:

```json
{ "env": { "MNEMON_INSTANCE": "<name>" } }
```

Claude Code sessions started in that vault then never need `--instance`. Add it only when the user names another library explicitly.

**Scripts, jobs and callers other than Claude Code** (a cron job, a producer, a shell alias, another agent runtime): the `.claude/settings.json` binding is read by Claude Code sessions only — nothing else sees it. Such a caller passes `--instance <name>` or `--config <path>` before the command on every invocation (`mnemon --instance work add --url …`), or exports `MNEMON_INSTANCE` itself; without a selector the engine falls back to `~/.config/mnemon/default`, which may be another library.

## YouTube behind a bot wall — the browser lane

When a YouTube capture answers `RESULT:status=failed`, `error_class=blocked`, `RESULT:acquisition=bot-wall` (`retryable=false`), the engine's host is one YouTube distrusts; trying again meets the same wall. The door is the browser lane — a producer that runs on the user's machine with the user's own browser cookies, outside the engine's fetch chain, and hands the engine a bundle (the caption track) to publish:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] browser-lane youtube "<url>" ["<url>"…] --session "<session title>" [--browser safari] [--intent "<intent>"] [--context <label>] [--recapture]
```

It takes the videos one at a time (one caption track each, a pause between them), prints one line per video (`captured`, `skipped-duplicate`, `blocked`, `failed`, …) and the gateway's `RESULT:` lines, then a summary; the exit code is 0 only when every video was captured or was already in the library. Safari is the default browser and needs nothing further; Chrome asks the user for its keychain item on screen — say so before using `--browser chrome`. Never pass a cookie file or a cookie value: `--browser` takes a browser's name only. The result is a normal captured video (`source_type: video`, `completeness` checked against the caption track, chapters as headings), reported as in step 3. A video that was captured earlier through a text workaround (`--file-text` with `--canonical-url`) answers `skipped-duplicate`; add `--recapture` to replace it with the captioned revision.

## PDF capture

Both local PDFs (`--file paper.pdf`) and remote PDFs (`--url https://.../x.pdf`) auto-detect as `origin=pdf` (source_type=paper). Remote PDFs are fetched under the engine's network policy; the original URL stays canonical in frontmatter.

The text comes from `pdftotext -layout` (Poppler) and is published as `source.md` **byte for byte**; the extractor reads that text and nothing else. There is no OCR and no visual reading of pages: a scanned PDF without a text layer fails as `no-text` (`RESULT:status=failed`, nothing published), figures are absent, and tables survive only as the text layout `pdftotext` produces. `pdftotext` must be installed (`brew install poppler`); without it the capture is refused before anything is published (`error_class=environment`, exit 3). The page count lands in frontmatter (`pages: N`) when `pdfinfo` (same package) is available. Completeness of a PDF capture is `unknown` — there is no page to check the text against.

## Image or screenshot

Use this section when the user hands over an image — a PNG or JPEG file, or a screenshot that exists as a local file — or says "save this screenshot". The engine reads the image's text itself. This session looks at the image, captures the documents it points to, decides whether the image itself is captured, and reports. Every command here runs through the one path form of How to Execute, step 2.

**1. Look first, and say where the image goes.** Look at the image before any command (the Read tool opens a file). You need it for the meta-reading (step 2) and to choose the witness's languages (step 5). An image with no local file cannot be captured itself: ask for the file when step 4 needs it; the documents it points to can still be captured (step 3). Tell the user where the image goes: this session's model sees the pixels now. When the image itself is captured, the engine sends it once more, to the model that the user's `claude` uses, to read its text, and checks that reading on this machine with tesseract. An image that only points to a document is not captured, and the engine sends nothing of it.

**2. The meta-reading — what the image shows beyond its running text.** Name in free form what is plainly there: the address of a document (a full URL, an arXiv identifier, a DOI), an author, a title, the source (a channel, a site, a publication), a date. There is no schema and no fixed list. Name only what you can read in the image, and infer nothing.

- **Pointer or mention — the picture's subject decides.** The image *points* to a document when the document itself is what the picture shows: its own page, or a card or preview that presents it, with its address. A reference inside the running text of a post is a *mention*, with or without a title: a title with a DOI in a sentence, a URL in a line, an entry of a reference list. Name a mention in the report; do not capture it. In doubt it is a mention: a report costs the user one sentence, and a wrong capture stays in the library.
  - A screenshot of a paper's first page with its arXiv stamp is a pointer.
  - A post whose link preview shows an article's title and address is a pointer, and the post's own text makes the image a source too.
  - A post that says "see <title>, doi:…" in its text holds a mention.
  - A bare address in a sentence is a mention.
- **Several documents shown:** capture each. **A URL and an identifier that name one document:** one capture — the engine's identity would answer a second one `skipped-duplicate`.
- **Read the address character for character.** Never complete or derive one: a cut or elided address is not an address. Build an `https` address yourself in two cases only: an arXiv identifier, with the version the image shows when it shows one (`https://arxiv.org/abs/2401.12345v2`), and a DOI (`https://doi.org/` and the DOI, percent-encoded).

**3. Capture each document the image points to — at once, without a question.** It is an ordinary URL capture. It does not wait for the language choice: the languages are chosen only before the image itself is captured. It takes no `--title` and no `--author` from the image: the engine acquires the document's own.

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] add --url "<address>" --session "<session title>" [--intent "<intent>"]
```

**4. Capture the image as a source in three cases.** It holds text of its own beyond the pointer — a post, a comment, a quote. Or the document it shows has no usable address (cut, elided, none): the picture is then the only copy at hand. Or the document capture failed: then capture the image and report both outcomes (the table below). An image that only points, and whose document was captured, is not captured; the report says that the screenshot was not archived. The user's word wins: "the screenshot too" captures it.

The two captures by `RESULT:status` — `failed` is the only failure of the document capture:

| `RESULT:status` | The document's capture | The image's capture |
|---|---|---|
| `extracted` | captured | captured: report the comparison (step 6) |
| `captured` (exit 4) | captured — not a failure: the source is published and the extract is not; `--resume <RESULT:revision>` finishes it | the same; the comparison as for any published revision |
| `skipped-duplicate` | in the library already — not a failure: name the stored revision, whatever version the image shows | the same bytes are in the library: name the stored revision and its comparison |
| `failed` | **the failure**: no source of the document was published — capture the image (third case) and report both outcomes | not captured: the failure rows (step 6) |
| `integrity-error`, `locked`, no `RESULT` block | no verdict: report it, and capture nothing in its place (the image is still captured in the first two cases) | no verdict: report it as the general table says |

**5. The image capture.** Choose the witness's languages: the languages of the text you read in the image, the main language first, in tesseract's codes — `eng`, `rus+eng`, `ukr+eng`, `deu`. When you cannot tell, ask the user. The sequence is for the witness: a wrong one makes the engine refuse a correct reading.

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] add --origin image --file "<path>" --ocr-languages <sequence> --session "<session title>" [--title "<title>"] [--author "<author>"] [--intent "<intent>"]
```

- This explicit command is the only image capture in version 1. An image is never auto-detected: `--file shot.png` without `--origin image` fails `unsupported`, and its message names `--origin image`.
- **Whose name.** Pass `--title` and `--author` when the user names them or the image shows them plainly. They are the image's own: the author of the post, not the authors of a document it points to. Where the image leaves it unclear whose name it shows, pass none. What the user names wins over what the image shows.
- `--intent` as the user said it. The path given is context, not evidence: the engine keeps the bytes.
- `--dry-run` shows the witness's probe — tesseract found, its version, each language model present as a file — and whether the fence probe record holds the image entry. It cannot show that a language model loads or that `claude` is logged in: only a run shows those. It exits 0 also when the run would exit 3.
- **The engine takes no part in the meta-reading.** It reads no address and fetches nothing for an image capture. An image's `source.md` carries no URL key. The document and the screenshot are separate resources, and neither supersedes the other.

**6. Report.**

*The documents.* Show the address as you read it and the title in the library side by side. The title in the library is `RESULT:title=`: the title the library shows for the revision (the extract's when there is one). It is not independent evidence of what the address names. When the image shows the document's title and the two differ, the report's first line says so. The difference is a note, not a verdict: it can mean a misread address, and it can mean that the library titles the document in other words. A match proves nothing either. Name each mention, and say that it was not captured.

*The image.* `RESULT:completeness=unknown` is normal for an image. The text was read from the image by a model (`capture.fidelity: recognized`) and checked against a local recognizer; the image is kept as the raw. Name the comparison on every image capture, in exactly one of three forms. Forms 1 and 2 apply only when `RESULT:status` is `extracted`, `captured` or `skipped-duplicate`. For them, read the `capture:` block of the `source.md` under `RESULT:path` with the Read tool (`RESULT:path` is relative to the vault root; `mnemon [--instance <name>] config get vault_path` prints the root).

1. **Published, with a share** — the block carries `capture.witness_share`. Say the share with the missing count beside it, `capture.witness_missing_words`: «tesseract's words found in the text: 98.7 % — 4 of tesseract's words not found». Then say the two counts `capture.unconfirmed_words` and `capture.unconfirmed_numbers`.
   - When `capture.unconfirmed_words` is more than a quarter of `capture.compared_words`, say that first, in those terms: «201 of 370 words have no confirmation». Say what such a count can mean, without deciding which: a block that the local recognizer did not read, or text that the model added.
   - When a count is above zero, say in one sentence what it means: the local recognizer did not confirm these; they can be text it cannot see, a word completed at the screen edge, or a misreading — check them against the image where they matter. List the unconfirmed numbers from the engine's `NOTE: unconfirmed numbers:` line.
2. **Published, without a share** — the block has no `capture.witness_share`: neither reader found a word of three letters, so nothing was compared by words.
3. **Not published** — every other `RESULT:status`: `failed`, `integrity-error`, `locked`, or no `RESULT` block. The comparison is unavailable: name the outcome that ended the capture. Do not read a `source.md` for this form, even when `RESULT:path` is set. An absent number never reads as agreement.

**A retry and a replay are two things.**

- A *retry* is `mnemon add --capture-id <RESULT:capture_id>` after an image capture that published nothing. When the engine kept the image, a retry is a whole new attempt: a new run of tesseract, a new call of the model, a new comparison. Its notes come from its new report. When the engine has not kept the image, a retry first reads the original file again from its path. A `locked` capture is the exception: the engine wrote nothing and no receipt, so nothing is recoverable by its id. Run the same command again, with the original file.
- A *replay* of a published revision — `--capture-id` or `--resume` of a capture whose source is published — and a `skipped-duplicate` read nothing again: no tesseract, no model. Their notes come from the stored report of the one reading. Say so when you report a replay or a duplicate.

The engine's notes, on stderr, for a published image revision:

- `NOTE: the local recognizer does not confirm <n> word(s) and <m> number(s) of the text` — printed only when one of the two counts is above zero.
- `NOTE: unconfirmed numbers: <up to twenty>` — printed only when the number count is above zero.
- Without these lines, both counts are zero.
- The first line can end `(the list is unavailable: this ledger holds no report of the reading)`: then say that the list is unavailable.
- `NOTE: this attempt's reading differs from the previous attempt's`: say that this retry read the image again and its text differs from the earlier reading.

The numbers are for the user, never a gate of this skill. The engine's own refusals (`reading-mismatch`, `no-text`) are the only ones: never refuse, retry or recapture because of a count.

*A failed image capture.* The engine's message decides what you tell the user about the image. Never say more about the image than the message says.

- **The message says `the image is kept`.** Say: not captured, nothing was published, and the engine keeps the image as capture `<RESULT:capture_id>`. A retry works from the kept image, without the original file.
- **The message says `nothing was kept`.** Say: not captured, and nothing was kept. A retry reads the original file again, so the file must still be at its path.
- **The message says neither.** Do not say whether the image was kept; follow the row of the table below. For example: `evidence-mismatch` (the kept image is missing or changed); a message that says `nothing is published` and nothing about the image (the model's call reported no usable model id: the `convert` row with `RESULT:retryable=true`); `fence-unprobed`; and `input-missing` at the first check (`image not found: …`, or the input `changed since the request was recorded`).

| `RESULT:error_class` · `RESULT:acquisition` | What to do and say |
|---|---|
| `unsupported` · `wrong-format` | HEIC, WebP, an animated image, a PDF: ask for a PNG or JPEG export. Never convert the image yourself |
| `unsupported` · `rotated` | a photo with an orientation tag: ask for an upright screenshot |
| `unsupported` · `truncated` or `corrupt` | a damaged file: ask for the original |
| `too-large` | over the bytes, the long edge or the pixels: ask for the capture in parts, each its own image. The engine never shrinks an image |
| `no-text` | the model read nothing: say so |
| `reading-mismatch` · `words-missing` | the reading lacks a notable part of what tesseract saw: say the share and the count from the engine's message. First check the language sequence — another sequence is a new capture (the command of step 5 again, with the original file); else retry the id for a new reading. The image is kept |
| `reading-mismatch` · `no-confirmation` | tesseract confirmed no word of the reading: say so. The image is kept |
| `convert` with `RESULT:retryable=true` | the model call failed: retry the id |
| `convert` with `RESULT:retryable=false`, `timeout` | the engine's message, verbatim; a retry helps only once the operator changed what it names |
| `evidence-mismatch` | the kept image is missing or changed since the engine kept it: an operator problem. Report it; a retry answers the same |
| `input-missing` | the file changed or vanished after the request: ask for the file again |
| `fence-unprobed` (exit 3) | the fence probe record of this Claude Code version lacks the image entry. It can come at the start of a capture, before the image is kept, and also on a retry of an image the engine kept (the reader's check runs again). Run `mnemon fence-probe`, then retry the id: `mnemon add --capture-id <RESULT:capture_id>`. The original file must still be at its path only when no earlier message of this capture said `the image is kept` |
| `environment` (exit 3) | comes after the image is kept: install or repair what the message names — `brew install tesseract`, a language model of tesseract, the login of `claude` — then retry the id: `mnemon add --capture-id <RESULT:capture_id>`. The original file is not needed any more |

The summary block, with the lines that apply:

```
<first, when it applies: the title the image shows differs from the title in the library>
Document: <address as read> → <RESULT:status> · in the library as "<RESULT:title>" · <RESULT:path>
Mentioned, not captured: <each mention>
Image: <RESULT:status> · <RESULT:path> · completeness unknown (read by a model, checked by tesseract)
Comparison: <one of the three forms>
Screenshot: not archived (it only points to the document)
Next: <only when the outcome calls for one>
```

**7. Not here.** Never pass this session's own reading of the image to the engine as the image's text: `--origin text` with a transcription by this session would be paraphrase, the failure the capture contracts exist to prevent. What this session reads reaches the engine only as the metadata of step 2 (`--title`, `--author`) and as the address of another capture. Never convert, crop, rotate or shrink the image. Never treat a number of the comparison as a gate. The command passes `--session "<session title>"` as every `add` of this skill does (plugin 0.4.3), and a retry or a replay keeps the session the receipt recorded.

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
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] add --url "<url>" --session "<session title>" [--origin <origin>] [--title "<title>"] [--author "<author>"] [--intent "<intent>"] [--depth <mode>] [--render] [--recapture]
```

   `--session` names this conversation (see Parameters); the lane's command above passes it the same way.

3. Read the `RESULT:` lines and the exit code. The engine computes both from the files it published — **never report a full capture from exit 0 alone**: exit 0 also means `skipped-duplicate`, and `RESULT:status` is the fact.

| `RESULT:status=` | Exit | What happened | What to tell the user |
|---|---|---|---|
| `extracted` | 0 | `source.md` and `extract.md` are published under `RESULT:path=` | Added — title, path, and the completeness (below) |
| `captured` | 4 | `source.md` is published, `extract.md` is not: the extractor failed, its output was invalid, or it refused (`RESULT:error_class=`, e.g. `fence-unprobed`, `unsupported-size`, `budget-exhausted`) | Captured, not yet extracted. Finish it with `mnemon [--instance <name>] add --resume <RESULT:revision>`; when the class is `fence-unprobed`, `mnemon fence-probe` first |
| `bookmarked` | 0 | `--tier bookmark`: `source.md` with the card is published, no `extract.md`; `RESULT:tier=bookmark` | Bookmarked — title and path; a full read later is `mnemon add --url <url>` (it promotes the card) or `mnemon triage promote <revision>` |
| `skipped-duplicate` | 0 | The resource is already in the library (same canonical URL, text or file hash); `RESULT:path=` is the existing revision (`RESULT:tier=bookmark` when that revision is a bookmark card; `RESULT:judgement=dismissed` when the reader dismissed it in the triage) | Already in the library at that path — nothing new was written. A newer version of the page is a `--recapture`; a bookmarked resource is promoted by a plain full `add`; a dismissed one — say so, and promote only if the user wants it read after all |
| `failed` | 1 | Nothing was published: acquisition or conversion failed — `RESULT:error_class=` `blocked` (every wall: a bot wall, a login wall, a paywall without text, a 403, a rate limit — `RESULT:acquisition=` names which), `fetch`, `timeout`, `convert`, `no-text` (a PDF without a text layer), `unsupported` (a file type no converter reads), `input-missing`, `invalid-utf`, `reading-mismatch` (an image)…; `RESULT:retryable=` says whether a later try may succeed | Not captured, with the class; suggest `--render` when the acquisition says `js-shell`, trying again later for a retryable class, nothing for a wall or a scanned PDF; an image: "Image or screenshot", step 6 |
| `failed` | 3 | Nothing was published because the environment lacks a tool: `RESULT:error_class=environment` (Poppler for a PDF, Chrome for `--render`, a media tool, tesseract or a language model for an image) or `fence-unprobed` (no `claude`, too old for `--restricted`, or no fence record for its version — for an image, no record of the image probe) | Read the class and the engine's message: install the named tool (`mnemon doctor` lists them), or for `fence-unprobed` run `mnemon fence-probe` (once per Claude Code version); then run the command again — for an image, retry the id (step 6 of "Image or screenshot") |
| `integrity-error` | 1 | A previously published `source.md` is missing or was edited (`capture.text_sha256` no longer matches) | An operator problem with the existing revision, not a capture failure — report it, do not run the command again |
| — | 2 | Usage or contract: an undeclared `--context`, conflicting selectors, an invalid URL, `--resume` of a complete capture, `--supersedes` of a missing revision | The engine's message, verbatim |
| `locked` | 75 | The vault or the engine is locked — another writer holds the vault lock (a capture, an ingest pass, `reindex`, `triage dismiss|restore`, a `research` writer) or a `mnemon release` is updating the engine; the vault case prints `RESULT:status=locked`, `error_class=locked`, `retryable=true` (the engine case prints no RESULT lines) — nothing was written | Run the same command again in a moment |

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
   - `environment` on a PDF → `brew install poppler`; on media → `mnemon doctor` reports the missing optional dependency (`yt-dlp`, `ffmpeg`, `whisper`); on `--render` → Google Chrome or Chromium; on an image → `brew install tesseract` (other languages: `brew install tesseract-lang`)

## For text/idea origins

If the user wants to capture text or an idea, they need to provide the content. Ask them to paste it, then feed it to the dispatcher on stdin:

```bash
printf '%s\n' "<pasted content>" | "${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" add --origin text --title "<title>" --session "<session title>"
```

For long texts write the content to a temporary file and `cat` it into the pipe. Never paraphrase or shorten it — the engine stores the text verbatim as `source.md`; only `extract.md` is generated.
