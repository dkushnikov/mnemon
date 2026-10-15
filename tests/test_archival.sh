#!/usr/bin/env bash
# A1 — url raw-store chain (curl → render → fetch failure) under the capture contract
# (docs/specs/2026-10-15-capture-contracts.md §6, D8): the raw is staged next to its destination,
# named after the revision, hashed into source.md; a page served as PDF takes the pdf row; the
# rendered text is the retained artifact when curl fails; both failing is an acquisition failure
# (exit 1, nothing published) — there is no "unarchivable" marker any more. Legacy folders are
# matched by url: (legacy_match). Stubs from tests/fixtures/capture/setup.sh.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Archival Chain Tests (A1) ==="

nfiles() { find "$ARCHIVE" -maxdepth 1 -type f -name "${1:-*}" 2>/dev/null | wc -l | tr -d ' '; }

# T1: curl succeeds → <revision>.html in archive_dir, hash recorded in source.md
run_gw --url "https://example.com/t1" --capture-id t1
assert_eq "$rc" "0" "curl success → exit 0"
assert_eq "$(nfiles '*.html')" "1" "curl success archives <revision>.html"
assert_file_exists "$ARCHIVE/$(result revision).html" "...named after the revision"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" "archive: \"$(result revision).html\"" "...source.md archive: field"
assert_eq "$(grep '^  raw_sha256:' "$VAULT/Sources/$(result revision)/source.md" | tr -d '" ' | cut -d: -f2)" "$(shasum -a 256 "$ARCHIVE/$(result revision).html" | cut -c1-64)" "...raw_sha256 matches the archived bytes"
# the archive sidecar (capture layer D15, A6): the frozen report next to the raw, its hash in the receipt
assert_file_exists "$ARCHIVE/$(result revision).report.json" "...the sidecar <revision>.report.json sits next to the raw"
assert_eq "$(shasum -a 256 "$ARCHIVE/$(result revision).report.json" | cut -c1-64)" "$(rget t1 '.report_sha256')" "...with the hash the receipt froze (report_sha256)"
assert_eq "$(rget t1 '.report_state') $(result report)" "published ok" "...report_state published, RESULT:report=ok"
assert_eq "$(jq -r '.schema + " " + .fetch.schema + " " + .text.schema + " " + (.site|tostring) + " " + (.media|tostring)' "$ARCHIVE/$(result revision).report.json")" "mnemon-capture-report/1 mnemon-fetch/1 mnemon-capture-text/2 null null" "...holding the fetch and conversion reports"
audit_out="$(python3 "$MNEMON_ROOT/bin/archive-audit.py" --config "$CONFIG" 2>&1)"
assert_contains "$audit_out" "GAP C — orphan archives (no referencing source): 0" "archive-audit.py tolerates the sidecar (no orphan)"

# T1b: url origin served as PDF (binary with NUL bytes, no .pdf in the URL) → .pdf, byte-exact
STUB_CURL=pdf run_gw --url "https://example.com/q4-report" --capture-id t1b
assert_eq "$rc" "0" "binary (pdf) download → exit 0"
assert_eq "$(nfiles '*.pdf')" "1" "binary download archived as .pdf (ext from content)"
printf '%%PDF-1.7\n\000\000binary\n%%%%EOF\n' > "$TEST_TMPDIR/expected.pdf"
assert_eq "$(shasum -a 256 "$ARCHIVE/$(result revision).pdf" | cut -c1-64)" "$(shasum -a 256 "$TEST_TMPDIR/expected.pdf" | cut -c1-64)" "pdf bytes preserved exactly (no null stripping)"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  method: "pdftotext"' "...converted by the pdf row"

# T2: the direct stage answers a JavaScript shell, the render stage succeeds → the raw is the
# rendered DOM HTML (<revision>.html, raw_kind rendered; capture layer D4), converted like a page
STUB_FETCH=rendered run_gw --url "https://example.com/t2" --capture-id t2
assert_eq "$rc" "0" "js-shell + render ok → exit 0"
assert_eq "$(nfiles '*.html')" "2" "the render stage retains the rendered DOM as <revision>.html"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  raw_kind: rendered' "...raw_kind rendered"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  method: "render+' "...method render+<converter>"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  http_status: null' "...no HTTP status is invented for a rendered page"
assert_contains "$(cat "$ARCHIVE/$(result revision).html")" "rendered text content" "...the rendered DOM is the archived raw"
assert_eq "$(nfiles '*.txt')" "0" "...and no rendered .txt is written any more"

# T3: the chain fails → acquisition failure, nothing published, no file written
n_before="$(nfiles)"
STUB_FETCH=fail run_gw --url "https://example.com/t3" --capture-id t3
assert_eq "$rc" "1" "both fail → exit 1"
assert_eq "$(result status)" "failed" "...status failed"
assert_eq "$(result error_class)" "fetch" "...error_class fetch (no unarchivable marker)"
assert_eq "$(result retryable)" "true" "...retryable"
assert_eq "$(nfiles)" "$n_before" "both fail → no archive file written"
assert_eq "$(staging_left)" "0" "...and no staged file left"
[[ -d "$VAULT/Sources/$(rget t3 '.revision')" ]] && fail "nothing published" "folder exists" || pass "...nothing published"

# T4: archival disabled (no archive_dir) → the raw lives in the vault state, no archive: field
rc=0; "$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" --url "https://example.com/t4" --capture-id t4 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "0" "no archive_dir → exit 0"
assert_not_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" "archive:" "archival disabled → no archive: field"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" "  raw_store: state" "...raw_store: state"
assert_file_exists "$VSTATE/raw/$(result revision).html" "...raw retained under <vault state>/raw/"

# T6: dedup — a legacy folder (no resource_sha256) is matched by url:, never modified; --force recaptures
mkdir -p "$VAULT/Sources/2026-01-01_existing"
printf '%s\n' '---' 'type: source' 'origin: url' 'url: "https://dup.test/x"' '---' '' '# body' > "$VAULT/Sources/2026-01-01_existing/source.md"
printf '%s\n' '---' 'type: extract' 'status: extracted' 'title: "Old"' '---' > "$VAULT/Sources/2026-01-01_existing/extract.md"
legacy_sha="$(shasum -a 256 "$VAULT/Sources/2026-01-01_existing/source.md")"
stub_reset
run_gw --url "https://dup.test/x"
assert_eq "$rc" "0" "legacy duplicate → exit 0"
assert_eq "$(result status)" "skipped-duplicate" "duplicate URL is skipped (legacy url: match)"
assert_eq "$(result path)" "Sources/2026-01-01_existing/" "...pointing at the legacy folder"
assert_eq "$(stub_calls)" "0" "skipped duplicate does not invoke claude"
assert_eq "$(shasum -a 256 "$VAULT/Sources/2026-01-01_existing/source.md")" "$legacy_sha" "the legacy folder is never modified"
run_gw --url "https://dup.test/x" --force --capture-id dup-force
assert_eq "$rc" "0" "--force re-captures the duplicate"
assert_eq "$(result status)" "extracted" "...as a new revision"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" '"2026-01-01_existing"' "...superseding the legacy folder"
assert_eq "$(rget dup-force '.legacy_match')" "false" "...the new receipt is not a legacy match"
run_gw --url "https://fresh.test/y" --capture-id fresh-y
assert_eq "$(result status)" "extracted" "non-duplicate URL proceeds normally"

cleanup_capture_test
summary
