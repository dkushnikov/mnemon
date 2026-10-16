# The capture layer — what a URL capture does and how to read it

Phase 1 of the capture contracts made the gateway honest about *what* it published (three artifacts, three hashes, a receipt per capture — [`specs/2026-10-15-capture-contracts.md`](specs/2026-10-15-capture-contracts.md)). The capture layer, phase 2 ([`specs/2026-10-15-capture-layer.md`](specs/2026-10-15-capture-layer.md)), is about *whether what it published is the page*: one network policy for every request the engine makes, a fetch chain that knows when a page needs rendering, three converters checked against the saved page, a sanitizer, site capture from a reviewable plan, resolvers for arXiv and YouTube, a canonical identity for URLs, deadlines with named owners, chunked extraction under one budget, and an `index.md` whose rows are regenerated from the files behind a preservation step. This page is the operator's guide to it; the spec is the contract.

The phase-1 facts hold unchanged: the gateway acquires and publishes, the model only extracts; `source.md` is immutable and its body is the capture-text byte for byte; `RESULT:` lines and exit codes come from the files; every write happens under the vault lock.

## 1. The fetch chain and what `egress` means

A URL capture (`mnemon add --url …`) acquires the page in up to three stages, in this order, and records every stage it tried in the receipt (`capture_report.fetch.stages`):

1. **direct** — the engine's own HTTP GET under the network policy: `http`/`https` only, no user-info, every address of every hop checked (private, loopback, link-local, reserved and every other address that is not globally reachable — a tailnet's or a carrier NAT's shared space included — are refused as `ssrf-blocked` / `ssrf-redirect`) and the connection pinned to the checked address, at most 5 redirects (each re-checked), a byte cap (`fetch_max_bytes`, 20 MB, decoded bytes, decompression bounded per chunk), connect/read timeouts (`fetch_timeout`) under the absolute acquisition deadline (the body is read one receive at a time, and a watchdog armed at the deadline shuts the socket down, so a server trickling bytes — in the body, or one header byte at a time before any body — cannot outlast it), one User-Agent (`fetch_user_agent`), **no cookies, no credentials, no proxies**. The response is classified (§2) and the chain stops, escalates or fails from that.
2. **rendered** — headless Chrome (`render-url.sh --html`) when the direct result is a JavaScript shell, a bot wall, a 403, or thin (fewer than `thin_words` visible words), or on `--render`. The raw of a rendered page is its post-JavaScript DOM HTML (`raw_kind: rendered`); it has no HTTP status and no final URL, and both are recorded as `null` rather than invented. Chrome runs with a throw-away profile and a mock keychain, a virtual-time budget, `render_timeout` and an output cap (`render_max_bytes`). **The policy does not police the browser**: its own connection, redirects, sub-resources and script-initiated requests are the browser's. The URL is checked before Chrome launches; that is a pre-validation of the name, not first-hop enforcement. `--no-render` skips this stage (and, because it means "no rendering", the external stage too). Without Chrome, a page that needed rendering fails as `environment` (exit 3) unless an external fetcher is allowed.
3. **external** — Firecrawl, then Jina Reader (`external_fetchers`), **only** when the instance says `external_fetch: auto` (the default is `off`), only for a JavaScript shell, a bot wall or a 403 after direct and rendered both failed, only for a URL that passes the policy, with keys from the environment (`FIRECRAWL_API_KEY`, `JINA_API_KEY`). A fetcher without a key is skipped with a note. A vendor's answer is classified like any other: a relayed wall is not a raw. `--no-external` skips the stage for one capture.

**`egress`** is where the request for the page physically went: `direct` (the engine or its browser, from this machine) or `external` (a vendor fetched the page on the engine's behalf). It is written into `source.md` only when it is `external` (`capture.egress: external`), and `mnemon doctor`'s `egress` line shows the instance's setting and whether each fetcher's key variable is set — never the key.

**Nothing in the chain is a way past a login, a paywall or a rate limit.** A login wall fails the capture (permanent, `blocked`/`login`); a paywall teaser with real text is published as `completeness: blocked` with the text before the wall; a 429 or a `Retry-After` fails as retryable and is retried later by the inbox's own backoff, never by the chain. One exception is a challenge page that *answers* with a 429 — the Vercel Security Checkpoint does, and names itself in the body: a 429 that carries a bot-wall marker is classified `bot-wall`, not `rate-limit`, and escalates once to the renderer (headless Chrome passes the checkpoint), then to an external fetcher when the instance allows it. When every stage ends on a wall the capture fails with `error_class: blocked`.

The same policy serves the site mapper and bundle fetcher, the depth helpers (`--depth`), the arXiv metadata request and URL PDF / audio downloads (`capture-fetch.py --expect pdf|audio`; `curl` is gone). The media tools (`yt-dlp`, the transcript library, `whisper`) are the second named boundary: the video URL's host is checked before they run; their own requests are theirs, under `transcribe_timeout`.

## 2. Completeness outcomes and how to read the `capture:` block

Two checks, both **diagnostics** — neither refuses a capture on its own:

**Acquisition** names what the fetched raw is, from one ordered decision table over the status, the headers and the visible words: `ok`, `thin`, `js-shell`, `bot-wall`, `forbidden`, `login`, `paywall`, `rate-limit`, `not-found`, `server-error`, `unavailable`. The chain decides from it (§1). The outcome is recorded as `capture.acquisition`, so "complete" never claims the fetch got the intended page — only that the conversion kept what the fetch got. The classification is one linear pass over at most the first 1 MB of the body (`CLASSIFY_MAX_BYTES`): the visible words, the markers and the JavaScript-shell signals all come from that prefix, and an unclosed `<script>`, `<style>` or `<title>` costs no more than a closed one (it swallows the rest, as in a browser) — a hostile page cannot hold the acquisition, and with it the vault lock, in CPU time that no deadline can interrupt. The conversion check is linear too: every regex over candidate markdown uses bounded, nesting-free character classes, so a page of 100 000 `[` or `(` converts in seconds, and whatever is not linear runs in a worker the supervisor kills at `convert_timeout` (§8).

**Conversion** compares the published text with a *reference* projected from the same raw bytes: the DOM text of the page's content subtree (`<article class="ltx_document">` for arXiv, else `<body>` without scripts, styles, navigation, footers, forms, cookie banners and reader comments). Three candidates are tried in order — `defuddle` (skipped for rendered, site and external raws), `pandoc`, the DOM text itself — each sanitized first (§3). A candidate wins when it contains every *eligible* reference line (6 or more words, or 20 or more characters of a script written without spaces, not a chrome line — the 2026-09-22 recipe's phrases of cookie banners, copyright lines and Substack's own footer and promos, in lines of at most 12 words — not the title) **with its word boundaries** — a line whose characters are all present but whose whitespace-separated words were glued together ("Deploythecompanybrain") counts as missing, while a word the page itself splits across inline markup (a drop cap, a link inside a word) is accepted in either form — and every h1–h3 heading. Otherwise the DOM text is published (`converter: dom-text`) with the page's own word boundaries, which satisfies the reference by construction while the inventory says what was lost. The DOM text is the fallback of last resort, never a failure: should it fail the check itself (a check defect), it is still published, as `partial` with the counts and `missing_examples` of its own check and the candidate note `dom-text failed its own check`; a conversion fails (`convert`, "no candidate produced text") only when the page holds no text at all, and the report then records every candidate that was tried. Reader-comment sections are cut only where a real widget is identified: a container id or class such as `comments`, `comment-list` or `disqus_thread`, or a heading like `12 Comments`, `Comments (3)`, `Leave a comment`; a section named *Discussion*, *Responses* or plain *Comments* is body text and stays.

Two things the diagnostics record without acting on them: `bytes_per_word` is flagged as `bloat` above 40 bytes, and because it counts whitespace-separated words a CJK page is always flagged — recorded only, never acted on. A page that came back `thin`, whose render was unavailable or failed, and that holds no text at all is kept as the acquisition says (A7) and then fails at conversion as `convert` (no text to publish), not as an acquisition failure.

`capture.completeness` is defined **relative to the saved snapshot**:

| Outcome | Meaning |
|---|---|
| `complete` | every eligible reference line is present with its word boundaries, every heading is present, no table or code block of the page is missing from the text |
| `partial` | something was lost — `missing_lines`, `glued_lines`, `headings_missing` and the inventory say what |
| `blocked` | a paywall teaser was published: the real text before the wall, nothing behind it (`blocked_kind: paywall`) |
| `unknown` | nothing to check against: plain text, PDFs, media transcripts, legacy rendered text, a page with no eligible line, and composites captured before phase 2 |

The `capture:` block of `source.md` (additive to phase 1):

```yaml
capture:
  method: "direct+pandoc"        # <fetch>+<converter>: direct | render | firecrawl | jina | site + defuddle | pandoc | dom-text | text | mixed
  fetch: direct                  # the stage that produced the raw
  converter: pandoc
  acquisition: ok                # the classification of the fetched raw (§2)
  completeness: complete         # complete | partial | blocked | unknown
  reference_lines: 212           # lines of the reference, eligible lines, and what the chosen candidate lacked
  eligible_lines: 96
  missing_lines: 0
  glued_lines: 0
  headings_missing: 0
  repairs: "svg_icons=4,next_images=2"   # the sanitizer's counted repairs, or none
  canonical_version: 1
  http_status: 200               # null for a rendered page
  egress: external               # only when a vendor fetched the page
  blocked_kind: paywall          # only when completeness is blocked
  fidelity: converted            # phase 1: exact | converted | transcribed
  raw_kind: response             # response | rendered | composite | transcript | file | site
  raw_sha256: "…"
  text_sha256: "…"
```

`RESULT:completeness=`, `RESULT:acquisition=` and `RESULT:canonical_url=` are printed by `mnemon add`; the full fetch and conversion reports live in the receipt (`capture_report.fetch`, `capture_report.text`), and when `archive_dir` is set the final report is frozen next to the raw as `<revision>.report.json`. `missing_examples` in the conversion report shows up to five of the lines that were lost.

How to read a capture, in practice: a `partial` source with `missing_lines: 1` and `headings_missing: 0` is usually a caption or a footnote; a `partial` with `converter: dom-text` and `tables` lost in the inventory is a page whose tables did not survive conversion — the raw is retained, so a better converter later can redo it without a fetch (`mnemon add --resume` after a reconversion-capable release). `thin` under `acquisition` means the page had fewer than `thin_words` visible words even after rendering was tried: a landing page, a stub, or a site that serves its content elsewhere — consider `--site` or `--depth auto`.

## 3. Sanitation

The sanitizer runs **before** the check, counts every repair, and never guesses a position. At the HTML level: every `<math>` element becomes the text of its TeX source — `$\theta$`, display math as `$$…$$` (LaTeXML writes both the glyphs and the source, so the DOM text used to read `θ\theta` while pandoc wrote `$\theta$`, every formula line counted as missing and a paper fell back to the DOM text without its tables); an SVG with text becomes a paragraph of its labels and an icon SVG is removed; LaTeXML listing lines become fenced code; `[⬇](data:…)` download buttons go (a `data:` link that carries real text is unwrapped to its text, never dropped); merged-cell tables are rewritten into simple tables (cells duplicated across their span, stacked header rows joined with " · ") so pandoc never emits `[TABLE]` for them — spans are clamped to 1 000 and a table whose expansion would exceed 20 000 cells is left as it is and counted `tables_unrepaired`, with the conversion deadline (`convert_timeout`) checked between tables. At the markdown level, fence-aware (nothing inside a code fence is touched): inline `data:` images become `*[inline figure omitted at capture: alt]*`, remaining `data:` links are dropped, Next.js image URLs become `*[img: alt]*`, LaTeXML residue (`\iow_now:Ne`, macro residue before the title) is removed, blank-line runs collapse. Both levels are idempotent. `bytes_per_word` is recorded and flagged above 40, never acted on.

`published:` is the gateway's decision from the converter's dated candidates: for `source_type: paper` the arXiv `citation_date`; for `article` an `article:published_time` or the `datePublished` of an Article / BlogPosting / NewsArticle / ScholarlyArticle JSON-LD node — never a WebSite or Organization node, never for a site capture, and omitted when more than one distinct date is found (the candidates are kept in the receipt).

## 4. Canonical identity

The literal URL stays in `url:`. Every new URL capture also writes `canonical_url:` — the computed identity, version 1 (`capture.canonical_version: 1`) — and the folder's `resource_sha256` is derived from it. The v1 rules are conservative: whitespace trimmed; scheme and host lowercased; the default port dropped; percent-encoding normalized in the path; tracking parameters removed from the query (`utm_*`, `fbclid`, `gclid`, `msclkid`, `mc_cid`, `_hsenc`, `ref_src`, `hide_intro_popup` and the rest of the list in the spec) while every other parameter is kept in order; the fragment dropped unless it is an application route (`#/page-a`, `#!…`); arXiv and YouTube resolved to one form per paper or video. **Not** done, on purpose: `www.` stripping, `http` → `https`, trailing-slash collapsing, lowercasing paths, following redirects (the redirect target is recorded as `final_url`, an alias for lookups, never the identity).

Consequences: two `add` calls with `?utm_source=a` and `?utm_source=b` are one resource (the second answers `skipped-duplicate` and reports the canonical URL); `?s=alpha` and `?s=beta` are two pages. Lookups find every folder — new or phase-1 — whose URLs canonicalize to the same string and choose the head deterministically (the folder not superseded by another, latest capture date first); a phase-1 folder found this way resumes or dedups exactly as before, and its receipt replays under its own schema (`mnemon-request/1`), never re-derived. Two consequences of that binding (A4): a `/1` receipt binds the literal `url`, so replaying it with another literal `utm` variant of the same page exits 2 (binding mismatch) — a new capture of that variant answers `skipped-duplicate` instead; and a `/1` request knows none of the new flags, so a `/1` replay given a non-default new flag such as `--no-external` exits 2 as well. A `/2` receipt binds the literal `url` just the same: replaying it (`--capture-id`) with another `utm` variant of the same page is a different request — exit 2, `capture-id reuse with a different request` — while a new capture of that variant, without the id, answers `skipped-duplicate` under the canonical identity. The page's own `<link rel=canonical>`, which the depth probe used to write into `canonical_url`, now lives in `declared_canonical_url`.

`mnemon audit --canonical` groups the vault's folders by the v1 form of their URLs and prints the groups of two or more with their revisions, `supersedes` links and extract status. It writes nothing and merges nothing — a duplicate from before canonicalization is yours to resolve (`mnemon add --recapture` on the one you keep, or leave both). It reads each `source.md` header through its closing `---` or `...` within 256 KB, the one rule the lookup and the reindexer share.

**Depth folding** (`--depth auto`, the default for a thin landing) runs after the landing was acquired through the chain, on the staged raw, under the remaining acquisition budget — the probe has no cap of its own — and never on a landing the chain classified as a paywall teaser (nothing deeper is fetched behind a wall). A folded capture is published as a composite (`raw_kind: composite`): the converted landing followed by the folded targets, retained as `<revision>.txt`. **A depth composite keeps the composite text only** — the landing's HTML is not retained beside it, so a composite cannot be reconverted from HTML the way a plain page can (`mnemon add --resume` reuses the retained text as it is); its completeness covers the landing part only and says so. Retaining the landing HTML next to the composite is a phase-3 follow-up. The fold's manifest is YAML, so the helper needs PyYAML; on a python without it the fold is skipped — the landing is captured alone, one stderr line says `depth fold skipped: PyYAML is missing`, and the receipt records `depth: skipped (no PyYAML)`.

## 5. Site capture with a reviewable plan

A site is many pages; the capture contract wants one resource per entity. Site capture makes the **landing** the resource and the plan its input:

```bash
mnemon site map https://example.com/ --out plan.json          # discover pages, write the plan (nothing captured)
# review or edit plan.json: flip include flags, add a host, drop a role
mnemon add --url https://example.com/ --site plan.json         # capture the included pages into one bundle
mnemon add --url https://example.com/ --site auto              # map and capture in one run (the plan is kept with the receipt)
```

`site map` discovers same-site pages from the landing's links (after rendering when the landing is a JavaScript shell), `/sitemap.xml` and the sitemaps named in `/robots.txt` — a sitemap index is followed two levels deep (`sitemap_index.xml` → `help/sitemap.xml` → `help/<product>/sitemap.xml`), at most 25 sitemaps and 20 000 URLs per map, the sub-sitemaps under the landing's section first — `/llms.txt`, documentation tables of contents and a linked GitHub README (its text only). Same-site means the landing's host (with or without `www.`), its `docs.` / `help.` / `developer(s).` / `blog.` / `api.` / `learn.` / `support.` subdomains, or a host the plan lists explicitly. Every page gets a **role** (`landing`, `product`, `docs`, `llms_txt`, `blog`, `pricing`, `careers`, `job`, `github_readme`, `legal`, `localized`, `howto`, `other`), an include/exclude decision and a reason: legal, localized, how-to and other pages are excluded by default (`en` and `en-*` paths or hosts are never `localized` — an English docs tree under `/en/` is the site's content — and neither is the landing's own locale prefix, read from its final URL as well). When the landing is not the site root, `product` is scoped to the landing's first path segment: a same-site page outside it (`/store/` for a landing `/air/`, `/products/…` for a docs landing under `/help/`) is `other` unless it is a docs, blog, pricing, careers or job page. `--include` / `--exclude` regexes are searched in the URL's **path plus `?query`** (never the scheme or host), so an anchored `^/blog/` or `^/help/air/` does what it says: `--include` **restricts** the plan to the landing and the pages matching some include regex (a matching page the rules excluded by role is re-admitted), `--exclude` then removes, and the cap `site_max_pages` (40) applies last, keeping the pages under the landing's section (its URL's directory — `/help/air/` for a docs page, `/air/` for a product landing; docs-TOC pages count as in it) first, then by role priority, then in discovery order. `robots.txt` is honoured as RFC 9309 defines it — the rules of **each origin** a page belongs to (`docs.example.com` has its own `robots.txt`, fetched the first time a page or a redirect hop lands there), matched against the path *and* the query, product token `mnemon` else `*`, longest match — a plan cannot include a disallowed page; an unreachable `robots.txt` disallows everything on that origin, a 4xx allows everything; the mapper keeps at least one second between requests to one host and stages its own work in a private temporary directory. `--no-render` and `--no-external` given to `mnemon add --site auto` govern the mapping of the plan as well as the bundle, and the mapper receives the instance's external-fetch settings exactly as the bundle fetcher does — a landing that needs a vendor fetch is mapped through the same chain it will be captured with.

`mnemon add --site` revalidates the plan (its landing must equal `--url`, the included pages must fit the cap, no robots-disallowed page) and fetches every included page through the chain of §1 — the landing first (a landing that fails is fatal), every other failure recorded and skipped — into a **bundle** `<revision>.site/` whose manifest names every member with its size and hash (`raw_sha256` is the manifest's hash). Each page carries a **disposition** that stays visible in the manifest, the report and `folded_sources:` of `source.md`: `captured`, `fetch-failed`, `skipped-timeout` (the `site_timeout` ran out) for acquisition; `converted`, `convert-failed` for conversion. The body is a composite — `# <landing title> — site capture`, then `## Part n — <url> (<role>)` per converted page in plan order — and:

```yaml
site_pages: "7/8"              # converted / included
folded_sources:
  - url: https://example.com/product
    role: product
    disposition: converted
    converter: pandoc
    completeness: complete
    missing_lines: 0
  - url: https://docs.example.com/guide
    role: docs
    disposition: fetch-failed
capture:
  raw_kind: site
  completeness: partial          # complete when every included page converted and every judged page is complete
  method: "site+mixed"           # or site+<converter> when one converter served every part
```

The aggregate `completeness` is `complete` when every included page converted and every converted page that has a reference is `complete`; parts without a reference — the README, `llms.txt`, a page with no eligible line — keep `completeness: unknown` in `folded_sources` and are neutral in the aggregate, so a site that includes a README can still be `complete`. `blocked` when the landing is a paywall teaser, `partial` otherwise.

A landing with an existing revision needs `--recapture` for a site capture (the new revision `supersedes` the plain one); `--recapture` on a partial site capture fills the omitted pages; `--resume` only finishes the extraction from the retained bundle. A bundle is verified like a file (every member's hash against the manifest) before every reconversion; a modified or missing member after publication is `evidence-mismatch`. `--dry-run --site plan.json` lists the pages and fetches nothing. The small report `capture-site.py fetch-bundle --report` writes (`mnemon-site-fetch/1`: the dispositions and the error fields) is not a frozen seam contract — the gateway reads only its error fields when the bundle fails; the manifest and the one stdout line are the contract.

## 6. arXiv

`arxiv.org/abs/<id>`, `/pdf/<id>`, `/html/<id>`, with or without `vN`, are one paper (`canonical_url: https://arxiv.org/abs/<id>`), `source_type` defaults to `paper`, and the content is the **full text**, never the abstract page: the fetch resolves the version once from the abstract page of the requested version (or the latest) — `citation_*` metadata (authors, `published:`), the version number — and fetches that version's HTML (`html/<id>vN`), else its PDF through the `pdf` row. `arxiv_version` (fetched) and `arxiv_requested_version` are written to `source.md`. An explicit `v1` in the URL archives v1, not v3; a later version is a `--recapture` with `supersedes`; asking for another version of a captured paper without `--recapture` answers `skipped-duplicate` naming the captured version. Conversion keeps `<article class="ltx_document">` and applies the LaTeXML sanitation (listings, download buttons, merged tables, residue).

## 7. YouTube

Captions are chosen **by the video's language**, read from `yt-dlp`: a manual track in that language · an automatic track in it (YouTube's `-orig` track first) · a manual `en` or `ru` track · an automatic `en` or `ru` track · the first manual · the first automatic · `whisper`. A Portuguese video with Portuguese captions is no longer captured from machine English. `source.md` records `transcript_language` (the track's language) and `original_language` (the video's), `capture.method` is `captions:<lang>:<manual|auto>` or `whisper:<model>`, and `media.json` under the attempt directory carries `language`, `original_language`, `track`, `channel` beside the phase-1 keys. The receipt's `media_meta` is recorded at acquisition and is authoritative on a replay: a resumed youtube-whisper capture that re-transcribes the retained audio takes `video_title`, `upload_date`, `duration` and `video_id` from the receipt, not from a filename. `embed/`, `live/`, `shorts/`, `youtu.be` and `music.youtube.com` forms all resolve to the same video; an explicit `--origin url` on a YouTube URL captures it as a page instead.

URL audio (`--origin audio --url …`) is downloaded through the policy and named by a content sniff (`.mp3`, `.m4a`, `.wav`, `.ogg`, `.flac`, `.aac`, `.mp4`, `.webm`), the URL's extension as the fallback.

## 8. Timeouts

Every deadline has one owner, children run in their own process group, budgets are passed down — so a hung download no longer holds the vault lock forever:

| Budget | Owner | What it bounds |
|---|---|---|
| `acquire_timeout` (300 s) | `capture-fetch.py` | the whole acquisition of one URL: direct, rendered and external stages together |
| `fetch_timeout` (60 s) | the policy | one direct request (connect = min(15, T/4), read = the rest); each page of a site |
| `render_timeout` (90 s) | the render stage | headless Chrome for one page |
| `site_timeout` (900 s) | `capture-site.py` | mapping; the bundle loop (pages left over are `skipped-timeout`, the bundle is published partial when the landing was captured) |
| `transcribe_timeout` (3600 s) | `media-extract.py` | yt-dlp, ffprobe, whisper and the caption fetch (its own subprocess, so a hung library call is killable) |
| `convert_timeout` (120 s) | `capture-text.py` | the whole conversion of one retained raw, in a worker process the supervisor kills past it (defuddle and pandoc under the same deadline inside it); a site bundle's assembly as a whole, each page with what is left |
| `extract_timeout` (900 s) | `extract-run.py` | one extractor call (phase 1) |

On expiry the owner kills its process groups (SIGTERM, a 2 s grace, SIGKILL), reaps them, writes `error_class: timeout` with the `stage` into its report and exits 124; the gateway records `FAIL_CLASS=timeout` with the stage. Two classes of timeout: an **acquisition** owner's (`capture-fetch.py`, `capture-site.py map` / `fetch-bundle`, `media-extract.py`) is retryable — a later run may get the page, and a timeout after the raw was published is resumed by reconverting the retained raw, never by refetching; a **conversion** timeout — a `capture-text.py` exit 124 is the class `timeout` with `stage: convert`, and so is an assembly whose landing the budget killed — is **not** retryable: a page that cannot be converted in the budget will not convert on a retry either, the raw stays published, and `mnemon add --resume` after raising `convert_timeout` is the way back. The gateway itself holds no outer timeout. `capture-text.py` is a thin supervisor: the whole conversion runs in a worker process (its own process group) that the supervisor kills at `convert_timeout`, so an in-process scan that never checks the deadline cannot hold the vault lock; inside the worker, when `defuddle` or `pandoc` outlives the remaining budget the tool is killed with its group and that **candidate is skipped** with a note (`pandoc timed out after N ms`), the next candidate is tried and the DOM text needs no tool. `capture-site.py assemble` converts every page of a bundle through the same worker with the remaining budget, and `map` / `fetch-bundle` run as workers under the CLI's own watchdog (`site_timeout` plus a 10 s teardown grace). Any other conversion failure is `convert` to the gateway, which honours the report's `retryable`: the converter walks the page's tree without recursion and caps its depth at 2 000 levels (deeper elements are flattened into the capped one), so 20 000 nested `<div>` or 6 000 never-closed table cells still convert; a document nested past what the interpreter can parse at all is `nesting` — permanent, never retried.

## 9. Chunked extraction

Capture-text above `extract_inline_max_bytes` (400,000 bytes) is no longer refused: it is split at heading boundaries outside code fences (else at blank lines) into at most `extract_max_parts` (8) parts of at most the inline limit, each part is extracted through the fenced extractor with the part template (`extract-part/1`: a title, a summary, standalone claims with a domain each, verbatim quotes — no rating, no body), and one merge run receives the parts' outputs instead of the content and returns the usual `extract/1`. `extract.md` records `extraction: chunked` and `parts: N`; the receipt records every call under an id that names the part, the round and the attempt (`part-3-r2-a1` is the validation re-run of part 3; `single-r1-a1`, `merge-r1-a1`), so a re-run is its own call, reconciled exactly once. **One budget for the capture's lifetime**: with `extract_max_usd` set, every call — parts, merge, validation retries, resumes — is debited from it, each call gets the remaining budget as its own cap, and a call that would start with nothing left stops the capture as `budget-exhausted` (retryable only with a raised budget); a call whose cost came back unknown keeps its reservation until `mnemon add --resume <id> --assume-cost <usd>` reconciles it. A phase-1 receipt that already spent `cost_usd` has that spend seeded as one reconciled `single` call before the ledger starts, so the budget keeps counting it. More parts than allowed, a single unsplittable span above the limit, or merge inputs above it → `unsupported-size`, as before. `--resume` re-runs every part (there is no part cache) and keeps debiting the same budget.

## 10. Index regeneration and `index_note`

`mnemon reindex` (also the post-extract hook of every `mnemon add`) **regenerates every row** of `index.md` from `Sources/*/extract.md` and `source.md` — a changed rating changes the row — instead of freezing a row forever once written. Two things that used to be hand-edits are now derived:

- **`index_note`** — a one-line `index_note: "…"` in a folder's `extract.md` (extract.md is mutable; source.md is not) is appended to its row as ` — <note>`. Put a durable annotation there, not in `index.md`.
- **`supersedes`** — a re-capture's `source.md` names its predecessor (`supersedes: "<revision>"`, or `Sources/<revision>/extract`), and the reindexer marks the old row ` ⤳ superseded by [[Sources/<new>/extract|<title>]]` from that — one marker per successor, a successor without `extract.md` linked through its `source`, a cycle reported on stderr and not marked, a target that no longer exists counted as `Dangling-supersedes` and marking nothing. Nothing is written into any `source.md` or `extract.md`.

**The hand-edit report.** Before a regeneration replaces an existing row with different text, the exact old row is appended under a dated heading to `<vault>/_inputs/index-hand-edits-<YYYY-MM-DD>.md` — every distinct old row, once (deduplicated by folder and the row's hash across the day's file, so a second edit on the same day is kept too), the file and its directory fsynced — and only then is `index.md` replaced (atomically). If that file cannot be written the index is left untouched and the run exits 1 with the reason. `mnemon reindex --no-hand-edit-report` skips the step explicitly. The first run after this change preserves every hand-written marker and annotation the old index carried; move what you want to keep into `index_note:`.

The counters line reports `Superseded=N` (and `Pending`, `Legacy`, `Dangling-supersedes`, `Preserved`, `Malformed-frontmatter` when non-zero); the pending note in `index.md` names `mnemon add --resume <revision>` for sources captured without an extract and counts legacy placeholders (`status: bookmark`, `fetch_status: failed` in `source.md`, not resumable) apart. `mnemon status` reports `superseded` and `chunked` from the same scans.

## 11. Doctor and audit

`mnemon doctor` gains three observational lines: `capture-deps` (defuddle ≥ 0.6, pandoc ≥ 3, Chrome by the renderer's own candidate list — "rendering unavailable" without it — and Poppler, with versions; a missing tool warns, never fails), `egress` (the instance's `external_fetch`, and per fetcher whether its key variable is set — never the value), `canonical` (`canonicalization v1`). `deps-python` no longer names `requests` (every request goes through the policy on the standard library; `pyyaml` stays for the depth manifest). `captures` also lists folders whose `source.md` header is not closed within 256 KB (`malformed-frontmatter`): the lookup cannot read them, and it says so rather than truncating silently. `mnemon audit --canonical` is §4.

## 12. Configuration

Instance keys (all optional; precedence: command-line flag > instance config > default; a non-numeric, negative or non-finite value where a number is expected — or a fraction where an integer is — refuses the config with exit 2):

| Key | Default | Meaning |
|---|---|---|
| `external_fetch` | `off` | `off` \| `auto` — whether Firecrawl / Jina may be tried after the local chain fails on a bot wall, a 403 or a JavaScript shell |
| `external_fetchers` | `firecrawl,jina` | the order; keys from `FIRECRAWL_API_KEY`, `JINA_API_KEY` in the environment |
| `fetch_user_agent` | a current desktop browser string | the one User-Agent of every engine request |
| `fetch_timeout` | `60` | one direct request; each site page |
| `fetch_max_bytes` | `20000000` | the body cap of a direct request (decoded bytes; above it the capture is `too-large`, never a truncated raw) |
| `render_timeout` | `90` | headless Chrome, one page |
| `render_max_bytes` | `20000000` | the cap on the rendered DOM output |
| `acquire_timeout` | `300` | the whole acquisition of one URL |
| `convert_timeout` | `120` | the conversion of one retained raw (a worker process killed past it: `timeout`, not retryable — the raw stays for `--resume` after raising the budget); a site bundle's assembly as a whole |
| `transcribe_timeout` | `3600` | the media helper's run |
| `site_max_pages` | `40` | pages a plan may include |
| `site_timeout` | `900` | mapping; the bundle loop |
| `thin_words` | `60` | below it a page is `thin` and rendered once |
| `extract_max_parts` | `8` | parts of a chunked extraction |
| `extract_inline_max_bytes` | `400000` | phase 1: above it the text is extracted in parts |
| `extract_max_usd` | unset | phase 1: the budget of one capture's extraction (parts, merge, retries, resumes) |
| `extract_timeout` | `900` | phase 1: one extractor call |

`mnemon.yaml.template` documents them; `mnemon config get <key>` and `mnemon config dump` show the resolved values.

## 13. What survives

With `archive_dir` set, what survives the loss of this machine's local state is in the archive: the raw (a file, or a bundle with its plan and per-page fetch reports), the frozen final report `<revision>.report.json`, and, in the vault, `source.md` with its frontmatter summary. The receipt — the ledger under `~/.local/state/mnemon/vaults/<id>/` — is local and re-derivable only for the facts those carry; a new machine captures afresh and finds the old folders through the canonical lookup.

## 14. Tests and the live gate

The hermetic suites (`tests/test_*.sh`, bash 3.2 / python 3.9 and the current toolchain) replay synthetic fixtures with deliberate damage (`tests/fixtures/capture/`) against a local server; the frozen seam contracts live in `tests/fixtures/capture/reports/`. The live gate — five sites and three arXiv papers pinned in `tests/live/corpus.txt`, fetched by `tests/live/capture-gate.sh` into a snapshot outside the repository — is a manual run with network, and `tests/live/replay.sh` re-converts a snapshot deterministically.
