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
| `receipts/request-v1.json` | a phase-1 receipt (`mnemon-request/1`) | the gateway's replay and binding under the stored schema |
| `receipts/request-v2-page.json`, `receipts/request-v2-site-auto.json` | phase-2 receipts (`mnemon-request/2`, `calls`, `capture_report`, the sidecar fields, `site_plan_sha256`) | the gateway, `doctor captures`, the integrator's chain test |

Rules: a producer's suite validates its real output against the fixture's **key set** (same keys, same types,
same nullability — values differ); a consumer's suite reads the fixture as its input; the integrator diffs real
outputs of the merged branch against these files. The acquisition dispositions of a bundle live in the manifest
(immutable); the conversion dispositions live in the assembly report (§12 A5).

Regenerate with `python3 tests/fixtures/capture/reports/generate.py` after editing the generator — never edit
the JSON by hand (the hashes are computed).
