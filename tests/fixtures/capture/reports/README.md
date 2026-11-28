# Report fixtures (spec `docs/specs/2026-10-15-capture-layer.md` §3.1, §3.3, §3.5, §3.6, §3.7, §4.1, §12 A4, §12 A12)

The frozen seam contracts between the capture layer and the gateway — one example per schema, including the
failure and null shapes:

| File | Schema | Producer → consumer |
|---|---|---|
| `canonical.json` | `mnemon-canonical/1` | `capture-fetch.py --canonical-only` → `cap_resource_identity`, the lookup |
| `fetch-ok.json`, `fetch-failed.json`, `fetch-timeout.json` | `mnemon-fetch/1` | `capture-fetch.py` → `cap_acquire_*`, the receipt (`capture_report.fetch`), `capture-text.py --fetch-report` |
| `text.json` | `mnemon-capture-text/2` | `capture-text.py` → `cap_convert_html`, the `capture:` block, the receipt |
| `site-plan.json` | `mnemon-site-plan/1` | `capture-site.py map` → `mnemon add --site`, `fetch-bundle` |
| `site-manifest.json` | `mnemon-site-bundle/1` | `fetch-bundle` → `verify`, `cap_raw_commit` (canonical bytes: sorted keys, no spaces, UTF-8, one trailing newline; `raw_sha256` hashes exactly these bytes) |
| `site-bundle-stdout.json` | the one stdout line of `fetch-bundle` | `fetch-bundle` → `cap_acquire_site` |
| `site-report.json` | `mnemon-site/1` | `assemble` → `folded_sources`, `site_pages`, `capture.completeness` |
| `media.json`, `media-error.json`, `media-error-blocked.json` | `mnemon-media/1`, `mnemon-media-error/1` (the timeout shape; the wall shape of GH #26 — `error_class: blocked`, `kind`, the tool's line in `error`, `retryable`) | `media-extract.py` → `cap_media_meta`, the receipt (`media_meta`); `error.json` → `cap_media_run` (class, kind, stage, message) |
| `media-bundle.json` | `mnemon-media/1` as the browser lane writes it (A13): the flat record plus `chapters`, `captions_path`, `captions_code`, `producer` | `browser-lane.py` → `cap_media_bundle_check`, `cap_media_meta` (the receipt's `media_meta` keeps `method` and `chapters`), `capture-captions.py --media` |
| `image.json` | `mnemon-capture-image/2`: one report per stage (`envelope`, `probe`, `witness`, `output`, `reader`, `text`, `compare`, `timeout`, `worker`, `done`; capture layer §21.7, the key set of §22.3: `comparison.missing_words`, `witness_numbers`, `missing_numbers`, `missing_lists`, `witness.absent`) | `capture-image.py` → `cap_convert_image`, the receipt (`capture_report.text`, `read_history`), `cap_image_fields`, `cap_image_notes` |
| `image-v1.json` | `mnemon-capture-image/1`: the `done` report of the earlier shape (`comparison.missing`, no number counts, no `missing_lists`, `witness.missing`; capture layer §22.3 «A stored `/1` report») | the replay tests of a receipt written before A21 (`cap_image_notes` reads either schema); a fake worker that writes it on a fresh attempt (`cap_convert_image` takes only `/2`) |
| `ocr-probe.json` | `mnemon-ocr-probe/1`: one document per case (all present, no tesseract, a version older than 4, a missing language model, a probe timeout; capture layer §21.6 «The probe») | `capture-image.py --probe` → `cap_dry_run_image`, the doctor's `ocr` line |
| `receipts/request-v1.json` | a phase-1 receipt (`mnemon-request/1`) | the gateway's replay and binding under the stored schema |
| `receipts/request-v2-page.json`, `receipts/request-v2-site-auto.json` | phase-2 receipts (`mnemon-request/2`, `calls`, `capture_report`, the sidecar fields, `site_plan_sha256`) | the gateway, `doctor captures`, the integrator's chain test |

Rules: a producer's suite validates its real output against the fixture's **key set** (same keys, same types,
same nullability — values differ); a consumer's suite reads the fixture as its input; the integrator diffs real
outputs of the merged branch against these files. The acquisition dispositions of a bundle live in the manifest
(immutable); the conversion dispositions live in the assembly report (§12 A5).

Regenerate with `python3 tests/fixtures/capture/reports/generate.py` after editing the generator — never edit
the JSON by hand (the hashes are computed).
