# The inputs of the live image gate

`tests/live/image-gate.sh` is the manual gate of the image origin (capture layer §21.13, the last row): real `claude`, real tesseract, run by the maintainer before the implementing change merges and after every tesseract major upgrade. This directory holds its synthetic inputs and its helpers. The rendered images are committed, so the gate runs without a renderer.

**Every text here is invented** for the measurements of §21.9: the authors, the handles, the institute, the paper, its arXiv identifier, the channel and the friend called Al. No real person, account, document or machine is named.

## Run the gate

```
MNEMON_FIXTURES_DIR=/path/outside/the/repo tests/live/image-gate.sh [--effort high] [--model haiku] [--max-usd 0.05] [--only <item>]
```

The snapshot — the reports, the readings, the witness texts, the records of the lifecycle check, `summary.md` — lands under `MNEMON_FIXTURES_DIR`, outside the repository. The gate prints numbers and verdicts only. Its header lists the items and their thresholds.

The tools of the gate have their own tests, with no real tool: `python3 -m unittest discover -s tests/live/image -p 'test_*.py'`.

## Files

| File | What it is | Where it came from |
|---|---|---|
| `html/post-en.html`, `truth/post-en.txt` | an English post (196 words) | the page generator of the OCR benchmark (2026-11-26), unchanged |
| `html/paper-2col.html`, `truth/paper-2col.txt` | the first page of a two-column paper with a rotated margin stamp | the same generator; the truth keeps the stamp last. Lines 6–8 of the truth are the left column, lines 9–11 the right one |
| `html/quote-short.html`, `truth/quote-short.txt` | a quote card | the same generator |
| `html/cyrillic.html`, `truth/cyrillic.txt` | a Russian channel post | the same generator |
| `html/post-ai.html`, `truth/post-ai.txt` | a post in the system font with 25 stand-alone `AI` and three real `Al` | the `AI` probe of 2026-11-27 (`post-ai2.html` there) |
| `img/post-en.png` | 1170 × 2100 | Chrome, from `html/post-en.html` |
| `img/paper-2col.png` | 1224 × 1584 | Chrome, from `html/paper-2col.html` |
| `img/quote-short.jpg` | 1200 × 660, JPEG quality 85 | Chrome, from `html/quote-short.html`, then `sips` |
| `img/cyrillic.png` | 1170 × 1800 | Chrome, from `html/cyrillic.html` |
| `img/post-ai.png` | 1320 × 2868 | Chrome, from `html/post-ai.html` |
| `img/post-en-dark-invert.png` | the post inverted, 1170 × 2100 | derived for this directory (2026-11-27), below |
| `img/post-en-dark-theme.png` | the post as light grey on dark blue, 1170 × 2100 | derived for this directory (2026-11-27), below |
| `img/post-en-block.png` | the post with the page under it as a dark framed block, 1170 × 3590 | derived for this directory (2026-11-27), below |
| `img/post-en-gray.png` | an 8-bit grayscale copy of the post, 1170 × 2100 | derived for this directory (2026-11-27), below; the source of the tall columns |
| `make_inputs.py` | the generator of the inputs that are not committed | written for the gate, stdlib only |
| `score.py` | the gate's thresholds: the words, the columns' order, the `AI` count; its item verdicts and the summary | written for the gate, stdlib only |
| `claude-wrap.py` | the worker's `--claude` during the gate: records its process and its descriptors, then execs the real `claude` | written for the gate, stdlib only |
| `lifecycle.py` | the check of the real client's lifecycle (contract K9) | written for the gate, stdlib only |
| `witness_repeat.py` | the worker's witness alone, five runs of one image | written for the gate, stdlib only |
| `test_image_gate.py` | the tests of the five tools above and of the gate's refusals | written for the gate, stdlib only |

## Render the HTML again

The Chrome command of the benchmark (`render.sh` there), with a throw-away profile:

```
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
shot() {   # <name> <width> <height> <scale>
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --use-mock-keychain --no-first-run \
    --user-data-dir="$TMP/chrome-profile" --force-device-scale-factor="$4" --window-size="$2,$3" \
    --screenshot="$TMP/$1.png" "file://$PWD/tests/live/image/html/$1.html"
}
shot post-en 390 700 3
shot paper-2col 816 1056 1.5
shot quote-short 600 330 2
shot cyrillic 390 600 3
shot post-ai 440 956 3
sips -s format jpeg -s formatOptions 85 "$TMP/quote-short.png" --out "$TMP/quote-short.jpg"
```

On 2026-11-27, on the machine that made the committed images, these commands wrote five files byte-identical to them. Another Chrome version or another system font can render other pixels; the truth texts stay right as long as the page shows the same text.

## The derived images

They were made once, with Pillow 12.2, by the probe scripts of 2026-11-27 — scripts that this directory does not carry (the repository is stdlib only). Each step below is a function of the committed post, so it can be repeated:

- `post-en-dark-invert.png`: `ImageOps.invert` of `img/post-en.png` (RGB).
- `post-en-dark-theme.png`: `ImageOps.colorize` of the post's grayscale (`ImageOps.grayscale`), black → `#E7E9EA` (the ink), white → `#15202B` (the ground).
- `post-en-block.png`: a white page of 1170 × (2100 + h + 80); the post at the top; under it, 40 px from the left and 40 px below the post, `img/paper-2col.png` scaled to 1090 px wide (LANCZOS; h = 1410) and coloured like the dark theme. It is the first of the probe's three renderings (light grey on dark blue), the one the model read at `high` (§21.9 «A dark block inside a light page»); the gate scores its reading against both truths, the post's and the page's.
- `post-en-gray.png`: `ImageOps.grayscale` of the post, saved by Pillow with `optimize=True`.

## The inputs the generator writes

`make_inputs.py --out <dir>` writes them into the snapshot; the same interpreter writes the same bytes on every run (and python 3.9 and 3.14 wrote the same bytes on 2026-11-27):

- `column-4200.png`: the grayscale post stacked twice, 1170 × 4200. Its known text is the post's, twice.
- `column-8000.png`: four copies of rows 36–2035 of the grayscale post, 1170 × 8000. Rows 60–2012 hold all the ink of the post (the generator checks that every row outside the copy is white), so each copy holds the whole post: the known text is the post's, four times. The measured columns were stacked from whole copies (§21.9: 8 400 px, four copies); a column of exactly 8 000 px, the long-edge cap, needs the margins cut.
- `column-8001.png`: `column-8000.png` with one white row under it — one pixel over the cap, so the envelope check refuses it (`too-large`, reason `edge`) before any model call.
- `quote-exif6.jpg`: the quote card with its EXIF segment replaced by one whose IFD0 holds the orientation tag alone, value 6. The pixels are the upright card's: byte surgery cannot turn JPEG pixels, and the envelope check refuses by the tag (`unsupported`, `rotated`) before anything decodes the image. The measured rotated JPEG of §21.9 had turned pixels too.

## The gate's own thresholds

The engine's comparison (§21.5) decides `agree`; the gate adds three checks of its own (`score.py`):

- **Words, with multiplicity** (contract K3): each word of the known text stands in the reading at least as often as in the text. A word is what the benchmark's scorer counted: `\w+` over the NFC text, lowercased. On the post, the page, both columns and the block.
- **The columns of the page**: the last word of the left column comes before the first word of the right one. Each is found by an anchor of at least three words, unique in the truth; the first occurrence in the reading counts. The word check cannot see the order: a reading with the columns swapped holds the same words.
- **`AI` right 25 times of 25**: the letter runs of the truth and the reading are aligned, and each of the truth's stand-alone `AI` must fall in an equal block. A real `Al` written `AI` — the model's measured error — does not count against it; the summary records how often the reading writes each.
