#!/usr/bin/env bash
# test_image.sh — the image origin through the gateway (docs/specs/2026-10-15-capture-contracts.md §18,
# amendment A15; docs/specs/2026-10-15-capture-layer.md §21, amendment A20): identity and the raw, the steps
# and their crash hooks, the replay at every boundary, the outcome table against the RESULT lines, the history
# of readings, the frontmatter keys, the reader's preflight, the notes on stderr. Everything runs against stubs:
# the stub tesseract through the test hook OCR_CMD, the stub reader behind the stub `claude`
# (tests/fixtures/capture/image/); no network, no model, no real recognizer.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Image Origin Tests ==="

IMGFIX="$FIX/image"
REPORTS="$FIX/reports"
TESSDATA="$TEST_TMPDIR/tessdata"
mkdir -p "$TESSDATA"
printf 'model eng\n' > "$TESSDATA/eng.traineddata"
export MNEMON_TEST_HOOKS=1 OCR_CMD="$IMGFIX/stub-tesseract.py" MNEMON_TEST_TESSDATA_DIR="$TESSDATA"
export MNEMON_TEST_TESSERACT_CALLS="$STUB_DIR/tesseract-calls" MNEMON_TEST_READER_CALLS="$STUB_DIR/reader-calls"
export MNEMON_TEST_READER_FACTS="$STUB_DIR/reader-facts.json"
# this suite captures through the real worker (its stubs are tesseract's and the reader's): the helper's probe
# stub in MNEMON_CAPTURE_IMAGE_CMD is set aside here and restored at the end
PROBE_STUB="${MNEMON_CAPTURE_IMAGE_CMD:-}"
unset MNEMON_CAPTURE_IMAGE_CMD

# the probe record of the stub's version, with and without the reader's entry (contracts A15 §4)
write_image_probe_record() {
  printf '{"9.9.9-stub (Claude Code)": {"pass": true, "date": "2026-11-27", "probes": {"no-tools": true, "image-reader": true}}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"
}
write_image_probe_record

# mkimg <name> [base]: an image of its own bytes (the identity is the bytes' hash) — a base fixture with a few
# bytes after its end marker, which the envelope counts and accepts
mkimg() {
  cat "${2:-$IMGFIX/base.png}" > "$TEST_TMPDIR/$1"
  printf 'unique %s' "$1" >> "$TEST_TMPDIR/$1"
  printf '%s' "$TEST_TMPDIR/$1"
}
reader_calls() { local n; n="$(grep -c '^read$' "$STUB_DIR/reader-calls" 2>/dev/null)" || true; echo "${n:-0}"; }
tess_runs() { local n; n="$(grep -c '^run$' "$STUB_DIR/tesseract-calls" 2>/dev/null)" || true; echo "${n:-0}"; }
tess_calls() { local n; n="$(grep -c . "$STUB_DIR/tesseract-calls" 2>/dev/null)" || true; echo "${n:-0}"; }
sha_of() { shasum -a 256 "$1" | cut -c1-64; }
fmblock() { awk 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f{print}' "$1"; }
src_of() { printf '%s/Sources/%s/source.md' "$VAULT" "$(rget "$1" '.revision')"; }
# run_cfg <config> <args…>: the gateway with another instance file of the same vault
run_cfg() {
  local cfg="$1" o="$TEST_TMPDIR/run.out" e="$TEST_TMPDIR/run.err"; shift
  rc=0
  bash -c '"$@"; exit $?' _ "$GW" source-add --config "$cfg" "$@" </dev/null >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
cfg_with() {   # <name> <yaml line>… → prints the path of a config with those keys
  local name="$1" f="$TEST_TMPDIR/$1.yaml"; shift
  sed "s/^instance: captest/instance: captest-$name/" "$CONFIG" > "$f"
  printf '%s\n' "$@" >> "$f"
  printf '%s' "$f"
}
STUB_TEXT_SHA="$(python3 - "$MNEMON_ROOT" "$IMGFIX/stub-text.txt" <<'PY'
import hashlib, sys
sys.path.insert(0, sys.argv[1] + "/bin")
from capture import image_reader
print(hashlib.sha256(image_reader.normalize(open(sys.argv[2], encoding="utf-8").read()).encode("utf-8")).hexdigest())
PY
)"

# ---------------------------------------------------------------------------------------------
echo "--- 1. an image is captured: the raw, the reading, the keys ---"
stub_reset
shot="$(mkimg shot-1.png)"
run_gw --origin image --file "$shot" --capture-id i-1 --title "A screenshot" --author "Example Author" --keep-run
assert_eq "$rc" "0" "an image capture exits 0"
assert_eq "$(result status)" "extracted" "...status extracted"
assert_eq "$(result completeness)" "unknown" "...RESULT:completeness=unknown (the witness is a second reader, not a reference)"
rev="$(result revision)"
src="$VAULT/Sources/$rev/source.md"
assert_file_exists "$src" "...source.md published"
assert_eq "$(tess_runs)" "1" "...one run of the witness"
assert_eq "$(reader_calls)" "1" "...one call of the reader"
assert_eq "$(stub_calls)" "1" "...one call of the extractor"
assert_eq "$(fm "$src" origin)" "image" "origin: image"
assert_eq "$(fm "$src" source_type)" "article" "source_type defaults to article"
assert_eq "$(fm "$src" content_format)" "text" "content_format: text"
assert_eq "$(fm "$src" origin_path)" "\"$shot\"" "origin_path: the --file path as given"
assert_eq "$(fm "$src" resource_kind)" "file" "resource_kind: file"
assert_eq "$(fm "$src" title)" '"A screenshot"' "--title reaches source.md"
assert_eq "$(fm "$src" author)" '"Example Author"' "--author reaches source.md"
assert_eq "$(capfm "$src" method)" '"read:claude-haiku-5-5"' "capture.method names the model id the call reported"
assert_eq "$(capfm "$src" fidelity)" "recognized" "capture.fidelity: recognized"
assert_eq "$(capfm "$src" raw_store)" "archive" "raw_store archive"
assert_eq "$(capfm "$src" raw_kind)" "image" "raw_kind image"
assert_eq "$(capfm "$src" raw_sha256)" "\"$(sha_of "$shot")\"" "raw_sha256 equals the input's hash"
assert_eq "$(capfm "$src" converter)" "image-reader" "converter image-reader"
assert_eq "$(capfm "$src" completeness)" "unknown" "completeness unknown"
assert_eq "$(capfm "$src" image_format)" "png" "image_format from the signature"
assert_eq "$(capfm "$src" image_pixels)" '"16x16"' "image_pixels quoted"
assert_eq "$(capfm "$src" reader_adapter)" '"image-reader/1"' "reader_adapter"
assert_eq "$(capfm "$src" reader_claude_code)" '"2.1.328"' "reader_claude_code quoted (a version never reads back as a number)"
assert_eq "$(capfm "$src" witness_backend)" "tesseract" "witness_backend"
assert_eq "$(capfm "$src" witness_version)" '"5.5.4"' "witness_version quoted"
assert_eq "$(capfm "$src" witness_adapter)" '"ocr-tesseract/1"' "witness_adapter"
assert_eq "$(capfm "$src" witness_languages)" '"eng"' "witness_languages"
[[ "$(capfm "$src" witness_execution)" =~ ^\"[0-9a-f]{64}\"$ ]] && pass "witness_execution: the witness's key" || fail "witness_execution" "$(capfm "$src" witness_execution)"
assert_eq "$(capfm "$src" compare_rule)" '"mnemon-read-compare/1"' "compare_rule"
assert_eq "$(capfm "$src" witness_share)" "100.0" "witness_share: a bare decimal with one fractional digit"
assert_eq "$(capfm "$src" witness_missing_words)" "0" "witness_missing_words"
assert_eq "$(capfm "$src" unconfirmed_words)" "0" "unconfirmed_words"
assert_eq "$(capfm "$src" unconfirmed_numbers)" "0" "unconfirmed_numbers"
[[ "$(capfm "$src" compared_words)" =~ ^[1-9][0-9]*$ ]] && pass "compared_words: a bare integer" || fail "compared_words" "$(capfm "$src" compared_words)"
keys="$(fmblock "$src" | sed -n '/^capture:/,$p' | sed -n 's/^  \([a-z_0-9]*\):.*/\1/p' | tr '\n' ' ')"
assert_eq "$keys" "method fidelity raw_store raw_kind raw_sha256 raw_bytes text_sha256 text_bytes converter completeness image_format image_pixels reader_adapter reader_claude_code witness_backend witness_version witness_adapter witness_languages witness_execution compare_rule witness_share witness_missing_words compared_words unconfirmed_words unconfirmed_numbers engine " "the capture: block in the order of contracts A15 §3.3"
for key in url canonical_url final_url posted_url declared_canonical_url folded_sources published; do
  assert_eq "$(fm "$src" "$key")" "" "an image's source.md carries no $key"
done
assert_not_contains "$(fmblock "$src")" "reading machine" "no word of the image outside the body"
assert_eq "$(body_sha "$src")" "$STUB_TEXT_SHA" "the body is the normalized reading, byte for byte"
assert_eq "$(capfm "$src" text_sha256)" "\"$STUB_TEXT_SHA\"" "capture.text_sha256"
assert_file_exists "$ARCHIVE/$rev.png" "the raw is the archive's <revision>.png"
cmp -s "$shot" "$ARCHIVE/$rev.png" && pass "...the input, byte for byte" || fail "the raw equals the input" "differs"
assert_eq "$(rget i-1 '.raw_kind')" "image" "receipt raw_kind image"
assert_eq "$(rget i-1 '.raw_sha256')" "$(sha_of "$shot")" "receipt raw_sha256 = input_sha256"
assert_eq "$(rget i-1 '.request.input_sha256')" "$(sha_of "$shot")" "...the hash bound at S1"
assert_eq "$(rget i-1 '.request.ocr_languages')" "eng" "the request binds ocr_languages"
assert_eq "$(rget i-1 '.request.origin')" "image" "...and the origin"
assert_eq "$(rget i-1 '.capture_report.text.schema')" "mnemon-capture-image/1" "the receipt holds the worker's report"
assert_eq "$(rget i-1 '.read_history | length')" "1" "read_history: one entry per attempt that ran the worker"
assert_eq "$(rget i-1 '.read_history[0] | keys | join(" ")')" "attempt claude_code cost_usd failure_class languages outcome output_tokens reader_adapter reader_model_id share text_sha256 thinking_tokens verdict witness_execution" "...with the keys of contracts A15 §3.2"
assert_eq "$(rget i-1 '.read_history[0] | "\(.attempt) \(.outcome) \(.failure_class) \(.verdict) \(.text_sha256) \(.languages)"')" "1 agreed null agree $STUB_TEXT_SHA eng" "...attempt 1 agreed"
[[ "$(rget i-1 '.read_history[0].cost_usd')" != "null" ]] && pass "...with the cost of its call" || fail "read_history cost" "null"
assert_file_exists "$ARCHIVE/$rev.report.json" "the archive sidecar is published"
assert_eq "$(jq -r '.text.schema' "$ARCHIVE/$rev.report.json")" "mnemon-capture-image/1" "...with the image report in its text member"
assert_eq "$(jq -r '.schema' "$ARCHIVE/$rev.report.json")" "mnemon-capture-report/1" "...the sidecar's shape unchanged"
assert_contains "$(cat "$STUB_DIR/call-1.stdin")" "Origin: image" "the extractor's prompt says the content was read from an image"
assert_contains "$(cat "$STUB_DIR/call-1.stdin")" "File: shot-1.png" "...and carries the image's basename"
assert_contains "$(cat "$STUB_DIR/call-1.stdin")" "Field notes on a small reading machine" "...and the reading as its content"
assert_eq "$(jq -r '.vault_lock_vars | length' "$STUB_DIR/reader-facts.json")" "0" "the reader's call sees no vault-lock variable"
assert_eq "$(jq -r '.effort_env_matches_argv' "$STUB_DIR/reader-facts.json")" "true" "...and the pinned effort level in its environment"
assert_eq "$(jq -r '.image_sha256' "$STUB_DIR/reader-facts.json")" "$(sha_of "$shot")" "the reader got the raw's bytes, unaltered"
assert_file_exists "$VSTATE/runs/i-1/attempt-1/witness.txt" "--keep-run keeps witness.txt"
assert_file_exists "$VSTATE/runs/i-1/attempt-1/reading.txt" "...and reading.txt"
# the reader's pair and the extractor's share the attempt dir: the gateway renames the reader's after the worker
# (the extractor's runner writes its own argv.txt / argv.json there after the reading, capture contracts §4.2)
assert_contains "$(cat "$VSTATE/runs/i-1/attempt-1/reader-argv.txt" 2>/dev/null)" "--input-format stream-json --output-format stream-json --verbose" "...the reader's argv as reader-argv.txt"
assert_eq "$(jq -r 'index("--input-format") != null and index("stream-json") != null' "$VSTATE/runs/i-1/attempt-1/reader-argv.json" 2>/dev/null)" "true" "...and reader-argv.json"
assert_contains "$(cat "$VSTATE/runs/i-1/attempt-1/argv.txt" 2>/dev/null)" "--output-format json" "...the extractor's argv keeps argv.txt"
assert_not_contains "$(cat "$VSTATE/runs/i-1/attempt-1/argv.txt" 2>/dev/null)" "--input-format" "...and it is the extractor's, not the reader's"
assert_eq "$(jq -r 'index("--output-format") as $i | if $i == null then "" else .[$i + 1] end' "$VSTATE/runs/i-1/attempt-1/argv.json" 2>/dev/null)" "json" "...and argv.json"
assert_eq "$err" "" "nothing on stderr when the witness confirms every word"
assert_eq "$(staging_left)" "0" "no staging left"

# JPEG bytes under a .png name: read by content
stub_reset
jp="$(mkimg named.png "$IMGFIX/jpeg-named.png")"
run_gw --origin image --file "$jp" --capture-id i-jpg
assert_eq "$rc" "0" "JPEG bytes named .png are captured"
assert_file_exists "$ARCHIVE/$(result revision).jpg" "...the raw's extension comes from the signature: <revision>.jpg"
assert_eq "$(capfm "$VAULT/Sources/$(result revision)/source.md" image_format)" "jpeg" "...image_format jpeg"

# ---------------------------------------------------------------------------------------------
echo "--- 2. identity: duplicates, the file route, a later URL ---"
stub_reset
run_gw --origin image --file "$shot"
assert_eq "$rc" "0" "the same bytes again exit 0"
assert_eq "$(result status)" "skipped-duplicate" "...skipped-duplicate"
assert_eq "$(result revision)" "$rev" "...naming the stored revision"
assert_eq "$(tess_calls) $(reader_calls) $(stub_calls)" "0 0 0" "...no witness, no reader, no extractor"
# the same bytes that once failed as a file are the same resource
both="$(mkimg both.png)"
stub_reset
run_gw --origin file --file "$both" --capture-id i-file
assert_eq "$rc" "1" "a PNG on the file route fails"
assert_eq "$(result error_class)" "unsupported" "...unsupported"
assert_contains "$err" "--origin image" "...and the refusal names the image door"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...nothing is recognized on the file route"
run_gw --file "$both"
assert_eq "$(result error_class)" "unsupported" "a --file without --origin is never auto-detected as an image"
run_gw --origin image --file "$both" --capture-id i-both
assert_eq "$rc" "0" "the same bytes as --origin image are captured"
assert_eq "$(rget i-both '.resource_sha256')" "$(rget i-file '.resource_sha256')" "...one resource whatever the origin"
dout="$("$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" doctor --list captures 2>&1 </dev/null || true)"
assert_contains "$dout" "$(printf 'i-file\tfailed/resolved\t')" "...and the doctor lists the failed file receipt failed/resolved once the image revision is published"
# a later URL capture is its own resource
run_gw --url "https://example.com/after-image" --capture-id i-url
assert_eq "$(result status)" "extracted" "a later --url capture is not deduplicated against an image"
# the ordered sequence is bound: another sequence on a replay is another request
printf 'model rus\n' > "$TESSDATA/rus.traineddata"
ord="$(mkimg order.png)"
run_gw --origin image --file "$ord" --ocr-languages rus+eng --capture-id i-ord
assert_eq "$rc" "0" "--ocr-languages rus+eng"
assert_eq "$(rget i-ord '.request.ocr_languages')" "rus+eng" "...bound as given, never sorted"
assert_eq "$(capfm "$(src_of i-ord)" witness_languages)" '"rus+eng"' "...and the witness ran under it"
run_gw --origin image --file "$ord" --ocr-languages eng+rus --capture-id i-ord
assert_eq "$rc" "2" "a replay that names another sequence is a binding mismatch (exit 2)"
run_gw --origin image --file "$ord" --capture-id i-ord
assert_eq "$rc" "2" "...and so is one that leaves the flag out when the instance says another sequence"
run_gw --capture-id i-ord
assert_eq "$rc" "0" "a replay by the id alone uses the bound sequence"
rm -f "$TESSDATA/rus.traineddata"

# ---------------------------------------------------------------------------------------------
echo "--- 3. the notes: what the witness does not confirm ---"
stub_reset
note="$(mkimg note.png)"
MNEMON_TEST_READER_MODE=other-text run_gw --origin image --file "$note" --capture-id i-note
assert_eq "$rc" "0" "a reading with small differences is published"
assert_contains "$err" "NOTE: the local recognizer does not confirm 1 word(s) and 1 number(s) of the text" "the counts go to stderr"
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "...with the unconfirmed numbers"
assert_eq "$(capfm "$(src_of i-note)" unconfirmed_words)" "1" "...and to the frontmatter"
assert_eq "$(capfm "$(src_of i-note)" unconfirmed_numbers)" "1" "...both counts"
assert_eq "$(capfm "$(src_of i-note)" witness_missing_words)" "1" "...and the witness's missing word"
[[ "$(capfm "$(src_of i-note)" witness_share)" =~ ^9[0-9]\.[0-9]$ ]] && pass "witness_share under 100 with one decimal" || fail "witness_share" "$(capfm "$(src_of i-note)" witness_share)"
assert_not_contains "$(fmblock "$(src_of i-note)")" "team" "the unconfirmed words stay out of the frontmatter"
stub_reset
run_gw --capture-id i-note
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "a replay prints the notes again, from the stored report"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...with no new comparison"
# a resume that reports the revision: the extraction failed after the publication, --resume finishes it
noter="$(mkimg note-resume.png)"
MNEMON_TEST_READER_MODE=other-text STUB_MODE=exit1 run_gw --origin image --file "$noter" --capture-id i-noter
assert_eq "$rc $(result status)" "4 captured" "a published reading whose extraction failed: captured"
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "...the notes printed"
stub_reset
run_gw --resume i-noter
assert_eq "$rc $(result status)" "0 extracted" "--resume finishes the extraction"
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "...and a resume prints the notes again"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...reading nothing"
run_gw --origin image --file "$note"
assert_eq "$(result status)" "skipped-duplicate" "a duplicate"
assert_contains "$err" "NOTE: the local recognizer does not confirm 1 word(s) and 1 number(s) of the text" "...prints them too"
# without a readable report the first line says so
cp "$(receipt i-note)" "$TEST_TMPDIR/i-note.receipt"
jq '.capture_report.text = null' "$TEST_TMPDIR/i-note.receipt" > "$(receipt i-note)"
run_gw --origin image --file "$note"
assert_contains "$err" "the list is unavailable" "without a stored report the note says that the list is unavailable"
assert_not_contains "$err" "unconfirmed numbers:" "...and lists nothing"
cp "$TEST_TMPDIR/i-note.receipt" "$(receipt i-note)"

# ---------------------------------------------------------------------------------------------
echo "--- 4. the reader's preflight ---"
write_probe_record     # the record without the entry image-reader
stub_reset
pre="$(mkimg pre.png)"
n0="$(ls "$ARCHIVE" | wc -l | tr -d ' ')"
run_gw --origin image --file "$pre" --capture-id i-pre
assert_eq "$rc" "3" "a probe record without image-reader: an image capture exits 3"
assert_eq "$(result error_class)" "fence-unprobed" "...fence-unprobed"
assert_eq "$(result retryable)" "true" "...retryable"
assert_contains "$err" "mnemon fence-probe" "...the message names the command"
assert_eq "$(ls "$ARCHIVE" | wc -l | tr -d ' ')" "$n0" "...before any raw"
assert_eq "$(rget i-pre '.raw_path') $(rget i-pre '.raw_sha256')" "null null" "...the receipt names no raw"
assert_eq "$(rget i-pre '.read_history // [] | length')" "0" "...and no reading"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...neither reader ran"
printf 'plain text under the same record\n' > "$TEST_TMPDIR/pre.txt"
run_gw_stdin "$TEST_TMPDIR/pre.txt" --origin text --capture-id i-pre-text
assert_eq "$rc" "0" "a text capture under the same record extracts as before"
write_image_probe_record
run_gw --capture-id i-pre
assert_eq "$rc" "0" "the record written → the replay publishes"
assert_eq "$(result status)" "extracted" "...extracted"
assert_eq "$(rget i-pre '.raw_sha256')" "$(sha_of "$pre")" "...the input was read and verified"
# the extractor's own preflight (no record for this version at all): exit 3 before the raw, then the replay
rm -f "$XDG_STATE_HOME/mnemon/fence-probes.json"
stub_reset
prex="$(mkimg pre-extractor.png)"
n0="$(ls "$ARCHIVE" | wc -l | tr -d ' ')"
run_gw --origin image --file "$prex" --capture-id i-prex
assert_eq "$rc $(result error_class)" "3 fence-unprobed" "no fence probe record: the extractor's preflight exits 3"
assert_eq "$(ls "$ARCHIVE" | wc -l | tr -d ' ') $(rget i-prex '.raw_path')" "$n0 null" "...before any raw, with no raw intent"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...neither reader ran"
write_image_probe_record
run_gw --capture-id i-prex
assert_eq "$rc $(result status)" "0 extracted" "the record written → the replay publishes"
assert_eq "$(rget i-prex '.raw_sha256')" "$(sha_of "$prex")" "...the input read and verified, the destination prepared again"

# ---------------------------------------------------------------------------------------------
echo "--- 5. the size cap and the changed input: no raw ---"
small="$(cfg_with small 'image_max_bytes: 100')"
stub_reset
big="$(mkimg big.png)"
n0="$(ls "$ARCHIVE" | wc -l | tr -d ' ')"
run_cfg "$small" --origin image --file "$big" --capture-id i-big
assert_eq "$rc" "1" "an input over image_max_bytes exits 1"
assert_eq "$(result error_class) $(result retryable) $(result acquisition)" "too-large false too-large" "...too-large, not retryable, RESULT:acquisition=too-large"
assert_contains "$(rget i-big '.error')" "image_max_bytes=100" "...the receipt's error names the cap"
assert_contains "$(rget i-big '.error')" "$(wc -c < "$big" | tr -d ' ') bytes" "...and the size"
assert_eq "$(ls "$ARCHIVE" | wc -l | tr -d ' ')" "$n0" "...no raw"
assert_eq "$(rget i-big '.capture_report.text') $(rget i-big '.read_history // [] | length')" "null 0" "...no report, no reading"
assert_eq "$(tess_calls)" "0" "...the worker never ran"
run_gw --capture-id i-big
assert_eq "$rc" "0" "the cap raised → the replay publishes from the verified locator"
# the input replaced between the request and the copy
stub_reset
swap="$(mkimg swap.png)"
other="$(mkimg swap-other.png)"
n0="$(ls -a "$ARCHIVE" | wc -l | tr -d ' ')"
MNEMON_TEST_IMAGE_SWAP_INPUT="$other" run_gw --origin image --file "$swap" --capture-id i-swap
assert_eq "$rc" "1" "an input that changes before the copy exits 1"
assert_eq "$(result error_class) $(result retryable)" "input-missing false" "...input-missing"
assert_eq "$(ls -a "$ARCHIVE" | wc -l | tr -d ' ')" "$n0" "...no raw and no staged copy left"
assert_eq "$(rget i-swap '.raw_path') $(rget i-swap '.raw_staged_path') $(rget i-swap '.raw_sha256')" "null null null" "...the raw intent cleared"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...nothing was read"
run_gw --capture-id i-swap
assert_eq "$rc" "1" "a replay answers input-missing again"
assert_eq "$(result error_class)" "input-missing" "...the same class"
assert_eq "$(ls -a "$ARCHIVE" | wc -l | tr -d ' ')" "$n0" "...the input is not copied"

# ---------------------------------------------------------------------------------------------
echo "--- 6. the environment after the raw: the retry from the retained image ---"
stub_reset
envi="$(mkimg env.png)"
OCR_CMD="$TEST_TMPDIR/no-tesseract-here" run_gw --origin image --file "$envi" --capture-id i-env
assert_eq "$rc" "3" "no tesseract: exit 3"
assert_eq "$(result status) $(result error_class) $(result retryable)" "failed environment true" "...failed/environment, retryable"
assert_contains "$err" "mnemon add --capture-id i-env" "...the message names the replay"
assert_eq "$(rget i-env '.raw_published')" "true" "...after the raw is published"
assert_file_exists "$(rget i-env '.raw_path')" "...the image is kept"
assert_eq "$(rget i-env '.read_history[0] | "\(.outcome) \(.failure_class) \(.text_sha256)"')" "environment environment null" "...read_history: environment"
assert_eq "$(rget i-env '.capture_report.text.stage')" "probe" "...the report's stage is probe"
assert_eq "$(reader_calls)" "0" "...no model call without the witness"
rm -f "$envi"
run_gw --capture-id i-env
assert_eq "$rc" "0" "the witness installed → the replay by --capture-id publishes"
assert_eq "$(result status)" "extracted" "...extracted, the input never read (it is gone)"
assert_eq "$(rget i-env '.read_history | map(.outcome) | join(" ")')" "environment agreed" "...the history keeps both attempts"
# the same through --resume <capture id>; a missing language model is the environment too
stub_reset
envr="$(mkimg env-resume.png)"
run_gw --origin image --file "$envr" --ocr-languages rus+eng --capture-id i-envr
assert_eq "$rc" "3" "a missing language model: exit 3, no fallback to another sequence"
assert_eq "$(rget i-envr '.capture_report.text.witness.missing | join(",")')" "lang:rus" "...the report names what is missing"
assert_eq "$(tess_runs) $(reader_calls)" "0 0" "...no run under eng alone, no model call"
reserved="$(rget i-envr '.revision')"
rm -f "$envr"
run_gw --resume "$reserved"
assert_eq "$rc" "2" "--resume <reserved revision> without a folder → exit 2"
printf 'model rus\n' > "$TESSDATA/rus.traineddata"
run_gw --resume i-envr
assert_eq "$rc" "0" "--resume <capture id> publishes from the retained image"
rm -f "$TESSDATA/rus.traineddata"
# a language that does not load, exit 0 included
stub_reset
part="$(mkimg partial.png)"
printf 'model rus\n' > "$TESSDATA/rus.traineddata"
MNEMON_TEST_TESSERACT_MODE=partial-lang run_gw --origin image --file "$part" --ocr-languages rus+eng --capture-id i-part
assert_eq "$rc" "3" "a bound language that did not load: exit 3 although tesseract exited 0"
assert_eq "$(rget i-part '.capture_report.text.failure.reason') $(rget i-part '.capture_report.text.stage')" "model-load witness" "...model-load at stage witness"
assert_eq "$(reader_calls)" "0" "...nothing published under a narrower sequence, no model call"
rm -f "$TESSDATA/rus.traineddata"
# the reader's login
stub_reset
auth="$(mkimg auth.png)"
MNEMON_TEST_READER_MODE=auth run_gw --origin image --file "$auth" --capture-id i-auth
assert_eq "$rc" "3" "a reader that is not logged in: exit 3"
assert_eq "$(result error_class) $(result retryable)" "environment true" "...environment, retryable"

# ---------------------------------------------------------------------------------------------
echo "--- 7. the history of readings across attempts ---"
stub_reset
hist="$(mkimg hist.png)"
MNEMON_TEST_CRASH_AT=after-read run_gw --origin image --file "$hist" --capture-id i-hist
assert_eq "$rc" "137" "a crash after the reading, before publication"
assert_eq "$(rget i-hist '.read_history | length') $(rget i-hist '.text_sha256')" "1 $STUB_TEXT_SHA" "...the reading and its hash reached the receipt in one update"
[[ -d "$VAULT/Sources/$(rget i-hist '.revision')" ]] && fail "nothing published after the crash" "exists" || pass "...nothing published"
OCR_CMD="$TEST_TMPDIR/no-tesseract-here" run_gw --capture-id i-hist
assert_eq "$rc" "3" "the replay without the witness: environment"
MNEMON_TEST_READER_MODE=other-text run_gw --capture-id i-hist
assert_eq "$rc" "0" "both stubs back, the reader reads differently: published"
assert_eq "$(rget i-hist '.read_history | map(.outcome) | join(" ")')" "agreed environment agreed" "read_history: three entries, agreed · environment · agreed"
h1="$(rget i-hist '.read_history[0].text_sha256')"; h3="$(rget i-hist '.read_history[2].text_sha256')"
assert_eq "$h1" "$STUB_TEXT_SHA" "...the first reading's hash"
[[ "$h3" != "$h1" && "$h3" =~ ^[0-9a-f]{64}$ ]] && pass "...and another hash for the third" || fail "two different text_sha256" "$h1 $h3"
assert_eq "$(rget i-hist '.read_history[1].text_sha256')" "null" "...null where no reading came"
assert_eq "$(rget i-hist '.capture_report.text.previous_text')" "$h1" "previous_text names the last reading the history holds"
assert_contains "$err" "NOTE: this attempt's reading differs from the previous attempt's" "...and the gateway says so"
assert_eq "$(body_sha "$(src_of i-hist)")" "$h3" "the source carries exactly one reading: the published attempt's"
assert_eq "$(rget i-hist '.text_sha256')" "$h3" "...and the receipt's text hash is its hash"
# the other outcomes of the mapping: mismatch, timeout, worker-fault
stub_reset
map1="$(mkimg map-mismatch.png)"
MNEMON_TEST_TESSERACT_MODE=double run_gw --origin image --file "$map1" --capture-id i-map1
assert_eq "$(rget i-map1 '.read_history[0] | "\(.outcome) \(.failure_class) \(.verdict)"')" "mismatch reading-mismatch loss" "a refusal of the comparison → outcome mismatch"
[[ "$(rget i-map1 '.read_history[0].text_sha256')" =~ ^[0-9a-f]{64}$ ]] && pass "...with the hash of the refused reading" || fail "refused reading hash" "$(rget i-map1 '.read_history[0].text_sha256')"
MNEMON_TEST_TESSERACT_MODE=double MNEMON_TEST_READER_MODE=other-text run_gw --capture-id i-map1
assert_eq "$rc $(result error_class)" "1 reading-mismatch" "a replay refused again, with another reading"
assert_eq "$(rget i-map1 '.capture_report.text.previous_text')" "$(rget i-map1 '.read_history[0].text_sha256')" "...its report names the previous reading's hash"
assert_contains "$err" "NOTE: this attempt's reading differs from the previous attempt's" "...and the gateway says so for a refused reading too"
quick="$(cfg_with quick 'image_timeout: 2')"
map2="$(mkimg map-timeout.png)"
MNEMON_TEST_READER_MODE=hang run_cfg "$quick" --origin image --file "$map2" --capture-id i-map2
assert_eq "$rc" "1" "the deadline: exit 1"
assert_eq "$(result error_class) $(result retryable)" "timeout false" "...timeout, never retried automatically"
assert_eq "$(rget i-map2 '.read_history[0] | "\(.outcome) \(.failure_class)"')" "timeout timeout" "...outcome timeout"
assert_eq "$(rget i-map2 '.stage')" "reader" "...the stage reached, from the worker's progress"
assert_eq "$(rget i-map2 '.capture_report.text.failure.reason')" "deadline" "...the supervisor's report in the receipt"
map3="$(mkimg map-fault.png)"
MNEMON_TEST_TESSERACT_MODE=hang MNEMON_TEST_IMAGE_WORKER_CRASH=1 run_gw --origin image --file "$map3" --capture-id i-map3
assert_eq "$rc" "1" "a worker that crashes with a live recognizer: exit 1"
assert_eq "$(result error_class) $(result retryable)" "convert true" "...convert, retryable: a fault that says nothing about the image"
assert_eq "$(rget i-map3 '.read_history[0] | "\(.outcome) \(.failure_class)"') $(rget i-map3 '.stage')" "worker-fault convert worker" "...outcome worker-fault, stage worker"

# ---------------------------------------------------------------------------------------------
echo "--- 8. the cost of every reading stays on record ---"
stub_reset
cost="$(mkimg cost.png)"
MNEMON_TEST_TESSERACT_MODE=double run_gw --origin image --file "$cost" --capture-id i-cost
assert_eq "$(result error_class)" "reading-mismatch" "attempt 1: a mismatch"
c1="$(rget i-cost '.read_history[0].cost_usd')"
[[ "$c1" != "null" && -n "$c1" ]] && pass "...with the cost of its call" || fail "mismatch cost" "$c1"
MNEMON_TEST_READER_MODE=no-result run_gw --capture-id i-cost
assert_eq "$(rget i-cost '.read_history[1] | "\(.outcome) \(.cost_usd) \(.output_tokens) \(.thinking_tokens)"')" "failed null null null" "attempt 2 without a result event: its cost is null — unknown, not zero"
run_gw --capture-id i-cost
assert_eq "$rc" "0" "attempt 3 agrees and is published"
[[ ! -d "$VSTATE/runs/i-cost" ]] && pass "...the attempt dirs are cleaned" || fail "attempt dirs cleaned" "still there"
assert_eq "$(rget i-cost '.read_history[0].cost_usd')" "$c1" "...and the first attempt's cost is still in the history"
[[ "$(rget i-cost '.read_history[0].output_tokens')" =~ ^[0-9]+$ ]] && pass "...with its token counts" || fail "token counts kept" "$(rget i-cost '.read_history[0].output_tokens')"
assert_eq "$(rget i-cost '.read_history | map(.attempt) | join(" ")')" "1 2 3" "...one entry per attempt"

# ---------------------------------------------------------------------------------------------
echo "--- 9. a source is never published beside the report of another reading ---"
stub_reset
fz="$(mkimg freeze.png)"
MNEMON_TEST_CRASH_AT=after-report-frozen run_gw --origin image --file "$fz" --capture-id i-fz
assert_eq "$rc" "137" "attempt A freezes its report and dies before the source is renamed"
frozen_a="$(rget i-fz '.report_sha256')"
[[ "$frozen_a" =~ ^[0-9a-f]{64}$ ]] && pass "...the frozen identity is in the receipt" || fail "A froze its report" "$frozen_a"
MNEMON_TEST_READER_MODE=other-text MNEMON_TEST_REPORT_FREEZE_FAIL=1 run_gw --capture-id i-fz
assert_eq "$rc" "0" "attempt B reads another text and its freeze fails: B is published"
assert_eq "$(rget i-fz '.report_sha256') $(rget i-fz '.report_path') $(rget i-fz '.report_state')" "null null null" "...with no frozen report identity, never with A's"
[[ -e "$ARCHIVE/$(rget i-fz '.revision').report.json" ]] && fail "no sidecar of another reading" "exists" || pass "...and no sidecar of A's reading"
assert_eq "$(body_sha "$(src_of i-fz)")" "$(rget i-fz '.read_history[1].text_sha256')" "...the source holds B's reading"
run_gw --capture-id i-fz
assert_eq "$rc" "0" "a later replay finds no conflict"
assert_eq "$(result status)" "extracted" "...extracted"

# ---------------------------------------------------------------------------------------------
echo "--- 10. a crash at every boundary ---"
for hook in after-raw-staged after-raw-renamed after-raw-published after-read after-source-staged; do
  stub_reset
  f="$(mkimg "crash-$hook.png")"
  MNEMON_TEST_CRASH_AT="$hook" run_gw --origin image --file "$f" --capture-id "i-$hook"
  assert_eq "$rc" "137" "$hook: the run dies"
  [[ -f "$VAULT/Sources/$(rget "i-$hook" '.revision')/source.md" ]] && fail "$hook: nothing published" "source.md exists" || pass "$hook: nothing published"
  before="$(reader_calls)"
  rm -f "$f"     # a recorded raw is never acquired again: the input is not needed
  run_gw --capture-id "i-$hook"
  assert_eq "$rc" "0" "$hook: the replay publishes"
  assert_eq "$(result status)" "extracted" "$hook: extracted"
  assert_eq "$(rget "i-$hook" '.raw_sha256')" "$(capfm "$(src_of "i-$hook")" raw_sha256 | tr -d '"')" "$hook: the source names the recorded raw"
  assert_eq "$(( $(reader_calls) - before ))" "1" "$hook: the replay before publication is a whole new attempt — one call of the reader"
  assert_eq "$(staging_left)" "0" "$hook: no staging left"
done
for hook in after-source-published after-extract-published; do
  stub_reset
  f="$(mkimg "crash-$hook.png")"
  MNEMON_TEST_CRASH_AT="$hook" run_gw --origin image --file "$f" --capture-id "i-$hook"
  assert_eq "$rc" "137" "$hook: the run dies"
  assert_file_exists "$(src_of "i-$hook")" "$hook: the source is published"
  t0="$(tess_calls)"; r0="$(reader_calls)"
  run_gw --capture-id "i-$hook"
  assert_eq "$rc" "0" "$hook: the replay finishes"
  assert_eq "$(result status)" "extracted" "$hook: extracted"
  assert_eq "$(( $(tess_calls) - t0 )) $(( $(reader_calls) - r0 ))" "0 0" "$hook: after publication no witness and no reader runs"
done
# ---------------------------------------------------------------------------------------------
echo "--- 11. after publication nothing reads again ---"
stub_reset
pub="$(mkimg published.png)"
STUB_MODE=exit1 run_gw --origin image --file "$pub" --capture-id i-pub
assert_eq "$rc" "4" "the reading agreed, the extraction failed: exit 4"
assert_eq "$(result status)" "captured" "...captured"
assert_file_exists "$(src_of i-pub)" "...the source is published"
t0="$(tess_calls)"; r0="$(reader_calls)"
OCR_CMD="$TEST_TMPDIR/no-tesseract-here" MNEMON_TEST_READER_MODE=error run_gw --resume i-pub
assert_eq "$rc" "0" "--resume of an extract-failed capture finishes without the stubs"
assert_eq "$(( $(tess_calls) - t0 )) $(( $(reader_calls) - r0 ))" "0 0" "...no witness, no reader"
OCR_CMD="$TEST_TMPDIR/no-tesseract-here" run_gw --capture-id i-pub
assert_eq "$rc" "0" "a replay of an extracted capture"
assert_eq "$(( $(tess_calls) - t0 )) $(( $(reader_calls) - r0 ))" "0 0" "...reads nothing either"
write_probe_record
run_gw --capture-id i-pub
assert_eq "$rc" "0" "...and needs no reader's probe entry"
write_image_probe_record

# ---------------------------------------------------------------------------------------------
echo "--- 12. the outcome table, row by row ---"
# row <id> <want rc> <class> <retryable> <acquisition|-> <stage|-> <raw yes|no> <args…>   (stub modes come from the environment)
row() {
  local id="$1" wrc="$2" cls="$3" retry="$4" acq="$5" stage="$6" raw="$7"; shift 7
  stub_reset
  "$@"
  assert_eq "$rc" "$wrc" "$id: exit $wrc"
  assert_eq "$(result status) $(result error_class) $(result retryable)" "failed $cls $retry" "$id: failed/$cls, retryable $retry"
  if [[ "$acq" == "-" ]]; then assert_eq "$(result acquisition)" "" "$id: no RESULT:acquisition"
  else assert_eq "$(result acquisition)" "$acq" "$id: RESULT:acquisition=$acq"; fi
  [[ "$stage" == "-" ]] || assert_eq "$(rget "$id" '.stage')" "$stage" "$id: receipt stage $stage"
  if [[ "$raw" == "yes" ]]; then
    assert_eq "$(rget "$id" '.raw_published')" "true" "$id: the raw is retained"
    assert_eq "$(rget "$id" '.capture_report.text.schema')" "mnemon-capture-image/1" "$id: the report is in the receipt"
  fi
  assert_eq "$(result revision)$(result path)" "" "$id: no revision in RESULT"
  assert_eq "$(rget "$id" '.source_published')" "false" "$id: nothing published"
  assert_eq "$(stub_calls)" "0" "$id: the extractor was never called"
}
cp "$IMGFIX/text-named.png" "$TEST_TMPDIR/t-wrong.png"
row t-wrong 1 unsupported false wrong-format envelope yes run_gw --origin image --file "$TEST_TMPDIR/t-wrong.png" --capture-id t-wrong
assert_eq "$(tess_calls)" "0" "t-wrong: text bytes named .png never reach the recognizer"
cp "$IMGFIX/truncated.png" "$TEST_TMPDIR/t-trunc.png"
row t-trunc 1 unsupported false truncated envelope yes run_gw --origin image --file "$TEST_TMPDIR/t-trunc.png" --capture-id t-trunc
cp "$IMGFIX/ihdr-twice.png" "$TEST_TMPDIR/t-corrupt.png"
row t-corrupt 1 unsupported false corrupt envelope yes run_gw --origin image --file "$TEST_TMPDIR/t-corrupt.png" --capture-id t-corrupt
cp "$IMGFIX/exif6.jpg" "$TEST_TMPDIR/t-rot.jpg"
row t-rot 1 unsupported false rotated envelope yes run_gw --origin image --file "$TEST_TMPDIR/t-rot.jpg" --capture-id t-rot
assert_file_exists "$ARCHIVE/$(rget t-rot '.revision').jpg" "t-rot: the raw is kept under its signature's extension"
cp "$IMGFIX/apng.png" "$TEST_TMPDIR/t-apng.png"
row t-apng 1 unsupported false wrong-format envelope yes run_gw --origin image --file "$TEST_TMPDIR/t-apng.png" --capture-id t-apng
pixels="$(cfg_with pixels 'image_max_pixels: 100')"
row t-pixels 1 too-large false too-large envelope yes run_cfg "$pixels" --origin image --file "$(mkimg t-pixels.png)" --capture-id t-pixels
edge="$(cfg_with edge 'image_max_edge: 10')"
row t-edge 1 too-large false too-large envelope yes run_cfg "$edge" --origin image --file "$(mkimg t-edge.png)" --capture-id t-edge
assert_eq "$(rget t-edge '.capture_report.text.validation.reason')" "edge" "t-edge: the envelope's reason is edge"
# the witness
MNEMON_TEST_TESSERACT_MODE=decode row t-decode 1 unsupported false corrupt witness yes run_gw --origin image --file "$(mkimg t-decode.png "$IMGFIX/idat-broken.png")" --capture-id t-decode
assert_eq "$(rget t-decode '.capture_report.text.validation.outcome') $(rget t-decode '.capture_report.text.failure.acquisition')" "ok corrupt" "t-decode: the effective classification apart from the envelope's outcome"
assert_eq "$(reader_calls)" "0" "t-decode: no model call for an image the witness cannot decode"
MNEMON_TEST_TESSERACT_MODE=too-large row t-limit 1 too-large false too-large witness yes run_gw --origin image --file "$(mkimg t-limit.png)" --capture-id t-limit
MNEMON_TEST_TESSERACT_MODE=fail row t-wfail 1 convert false - witness yes run_gw --origin image --file "$(mkimg t-wfail.png)" --capture-id t-wfail
MNEMON_TEST_TESSERACT_MODE=non-utf8 row t-enc 1 convert false - output yes run_gw --origin image --file "$(mkimg t-enc.png)" --capture-id t-enc
# the reader
MNEMON_TEST_READER_MODE=tool-in-init row t-fence 1 convert false - reader yes run_gw --origin image --file "$(mkimg t-fence.png)" --capture-id t-fence
assert_eq "$(rget t-fence '.capture_report.text.failure.reason')" "reader-fence" "t-fence: a breach of the fence at the call"
MNEMON_TEST_READER_MODE=budget row t-budget 1 convert false - reader yes run_gw --origin image --file "$(mkimg t-budget.png)" --capture-id t-budget
MNEMON_TEST_READER_MODE=max-turns row t-turns 1 convert false - reader yes run_gw --origin image --file "$(mkimg t-turns.png)" --capture-id t-turns
MNEMON_TEST_READER_MODE=error row t-rerr 1 convert true - reader yes run_gw --origin image --file "$(mkimg t-rerr.png)" --capture-id t-rerr
MNEMON_TEST_READER_MODE=no-structured row t-rout 1 convert true - reader yes run_gw --origin image --file "$(mkimg t-rout.png)" --capture-id t-rout
# the text
MNEMON_TEST_READER_MODE=whitespace row t-zero 1 no-text false - text yes run_gw --origin image --file "$(mkimg t-zero.png)" --capture-id t-zero
MNEMON_TEST_READER_MODE=empty row t-empty 1 no-text false - text yes run_gw --origin image --file "$(mkimg t-empty.png)" --capture-id t-empty
# the comparison
MNEMON_TEST_TESSERACT_MODE=double row t-loss 1 reading-mismatch false words-missing compare yes run_gw --origin image --file "$(mkimg t-loss.png)" --capture-id t-loss --keep-run
assert_file_exists "$VSTATE/runs/t-loss/attempt-1/witness.txt" "t-loss: a refused reading is evidence too — witness.txt stays in the attempt dir"
assert_file_exists "$VSTATE/runs/t-loss/attempt-1/reading.txt" "t-loss: ...and reading.txt"
assert_contains "$(cat "$VSTATE/runs/t-loss/attempt-1/reader-argv.txt" 2>/dev/null)" "--input-format stream-json --output-format stream-json --verbose" "t-loss: ...and the reader's argv, renamed on every outcome"
assert_eq "$(jq -r '.[0:2] | .[1]' "$VSTATE/runs/t-loss/attempt-1/reader-argv.json" 2>/dev/null)" "-p" "t-loss: ...and reader-argv.json"
[[ -e "$VSTATE/runs/t-loss/attempt-1/argv.txt" ]] && fail "t-loss: no argv.txt" "exists" || pass "t-loss: ...no argv.txt: the extractor never ran"
assert_contains "$err" "witness languages eng" "t-loss: the engine's sentence names the witness's languages"
assert_contains "$err" "the floor is 90 % and 20 words" "t-loss: ...and the floor"
for word in "reading machine" "brillig" "Field notes" "slithy"; do
  assert_not_contains "$err" "$word" "t-loss: no word of either text on stderr ($word)"
  assert_not_contains "$out" "$word" "t-loss: ...or in RESULT ($word)"
done
assert_not_contains "$(rget t-loss '.error')" "brillig" "t-loss: ...or in the receipt's error"
MNEMON_TEST_TESSERACT_MODE=junk row t-noconf 1 reading-mismatch false no-confirmation compare yes run_gw --origin image --file "$(mkimg t-noconf.png)" --capture-id t-noconf
# a replay of a refused reading is a new reading; the floor's keys are limits and may be moved for it
loose="$(cfg_with loose 'image_min_witness_share: 40' 'image_miss_allowance: 5')"
MNEMON_TEST_TESSERACT_MODE=double run_cfg "$loose" --capture-id t-loss
assert_eq "$rc" "0" "the floor moved → the same pair is published on the replay"
# min_share is a float in the report (layer §21.7: "min_share": 90.0); jq 1.7 prints 40.0 as written, 1.6 as 40
assert_eq "$(rget t-loss '.capture_report.text.comparison.floor | "\(.min_share == 40 and (.min_share | type) == "number") \(.allowance)"')" "true 5" "...and the report records the floor it ran under"
assert_eq "$(rget t-loss '.read_history | map(.outcome) | join(" ")')" "mismatch agreed" "...the history keeps the refusal"
# a short valid reading is published like a long one
MNEMON_TEST_TESSERACT_MODE=half run_gw --origin image --file "$(mkimg t-half.png)" --capture-id t-half
assert_eq "$rc" "0" "a reading that holds more than the witness saw is published"
[[ "$(capfm "$(src_of t-half)" unconfirmed_words)" -gt 0 ]] && pass "...with its unconfirmed words counted" || fail "unconfirmed counted" "0"
# one visible character is a text (contracts A15 §3.6): neither reader has a word, the reading is not empty → agree
stub_reset
MNEMON_TEST_TESSERACT_MODE=empty MNEMON_TEST_READER_MODE=one-char run_gw --origin image --file "$(mkimg t-onechar.png)" --capture-id t-onechar
assert_eq "$rc $(result status)" "0 extracted" "one visible character, a witness with no word: published and extracted"
onechar="$(src_of t-onechar)"
assert_eq "$(body_sha "$onechar")" "$(printf 'x\n' | shasum -a 256 | cut -c1-64)" "...the body is the one character"
assert_eq "$(rget t-onechar '.capture_report.text.comparison | "\(.verdict) \(.share)"')" "agree null" "...verdict agree, share null"
assert_eq "$(fmblock "$onechar" | grep -c '^  witness_share:')" "0" "...no witness_share key: absent when the witness has no word (contracts A15 §3.3)"
assert_eq "$(capfm "$onechar" witness_missing_words) $(capfm "$onechar" compared_words) $(capfm "$onechar" unconfirmed_words) $(capfm "$onechar" unconfirmed_numbers)" "0 0 0 0" "...the four counts are there"

# the gateway's own reading of the worker's exit, row by row (a fake worker through the seam)
cat > "$TEST_TMPDIR/fake-worker.sh" <<'FAKE'
#!/usr/bin/env bash
# a fake image worker: writes the report FAKE_REPORT names (a key of the fixture image.json, or none),
# a capture-text when FAKE_TEXT=yes, and exits FAKE_RC
out=""; report=""; prev=""
for a in "$@"; do
  case "$prev" in --out) out="$a" ;; --report) report="$a" ;; esac
  prev="$a"
done
if [[ "${FAKE_REPORT:-none}" != "none" ]]; then jq --arg k "$FAKE_REPORT" '.[$k]' "$FAKE_FIXTURE" > "$report"; fi
[[ "${FAKE_TEXT:-no}" == "yes" ]] && printf 'a reading\n' > "$out"
exit "${FAKE_RC:-0}"
FAKE
chmod +x "$TEST_TMPDIR/fake-worker.sh"
export FAKE_FIXTURE="$REPORTS/image.json"
fake() {   # <id> <rc> <report key|none> <text yes|no>
  stub_reset
  MNEMON_CAPTURE_IMAGE_CMD="$TEST_TMPDIR/fake-worker.sh" FAKE_RC="$2" FAKE_REPORT="$3" FAKE_TEXT="$4" \
    run_gw --origin image --file "$(mkimg "$1.png")" --capture-id "$1"
}
fake f-ok 0 done yes
assert_eq "$rc" "0" "exit 0, a report of an agreed reading and a capture-text: published"
# a version such as 5.10 never reads back as a number (contracts A15 §3.3: cap_q)
jq '.done.witness.version = "5.10" | .done.reader.claude_code = "2.10"' "$REPORTS/image.json" > "$TEST_TMPDIR/image-510.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-510.json" fake f-510 0 done yes
assert_eq "$rc" "0" "a report with witness version 5.10: published"
assert_eq "$(capfm "$(src_of f-510)" witness_version) $(capfm "$(src_of f-510)" reader_claude_code)" '"5.10" "2.10"' "...witness_version and reader_claude_code quoted: they read back as strings"
# the NOTE lists at most twenty unconfirmed numbers (capture layer §21.10)
jq '.done.comparison.unconfirmed_numbers = 25 | .done.comparison.unconfirmed.numbers = [range(101; 126) | tostring]' "$REPORTS/image.json" > "$TEST_TMPDIR/image-25.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-25.json" fake f-25 0 done yes
assert_contains "$err" "NOTE: unconfirmed numbers: $(jq -rn '[range(101; 121) | tostring] | join(" ")') … (the first twenty of 25)" "25 unconfirmed numbers: the NOTE lists the first twenty"
assert_not_contains "$err" "121" "...and no more"
fake f-notext 0 done no
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-notext '.stage')" "1 convert true worker" "exit 0 with a right report and no capture.md: the worker's fault — convert, retryable, stage worker"
assert_eq "$(rget f-notext '.source_published') $(rget f-notext '.text_sha256')" "false null" "...nothing published, no text hash taken"
assert_eq "$(rget f-notext '.read_history[0].outcome')" "worker-fault" "...outcome worker-fault"
for pair in "0 compare yes" "0 none yes" "1 done yes" "1 none no" "1 probe no" "1 timeout no" "3 done yes" "3 compare no" "3 none no" "143 none no" "2 none no" "9 done yes"; do
  set -- $pair
  fake "f-$1-$2" "$1" "$2" "$3"
  assert_eq "$rc $(result error_class) $(result retryable) $(rget "f-$1-$2" '.stage')" "1 convert true worker" "worker exit $1 with report '$2': the gateway's own worker fault (convert, retryable, stage worker)"
  assert_eq "$(rget "f-$1-$2" '.read_history[0] | "\(.outcome) \(.failure_class)"')" "worker-fault convert" "...outcome worker-fault"
  assert_eq "$(rget "f-$1-$2" '.source_published')" "false" "...nothing published"
done
fake f-124 124 none no
assert_eq "$rc $(result error_class) $(result retryable)" "1 timeout false" "exit 124 without a report is the timeout, never convert"
assert_eq "$(rget f-124 '.stage') $(rget f-124 '.read_history[0].outcome')" "null timeout" "...stage null without a report, outcome timeout"
fake f-124r 124 timeout no
assert_eq "$(result error_class) $(rget f-124r '.stage')" "timeout reader" "exit 124 with the supervisor's report: the stage from the report"
fake f-wf 1 worker no
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-wf '.read_history[0].outcome')" "1 convert true worker-fault" "the supervisor's worker-fault report: convert, retryable, outcome worker-fault"
fake f-env 3 probe no
assert_eq "$rc $(result error_class)" "3 environment" "exit 3 with an environment failure: exit 3"
unset FAKE_FIXTURE

# ---------------------------------------------------------------------------------------------
echo "--- 13. the dry run ---"
stub_reset
dry="$(mkimg dry.png)"
n0="$(ls "$VSTATE/captures" | wc -l | tr -d ' ')"
run_gw --origin image --file "$dry" --ocr-languages rus+eng --dry-run
assert_eq "$rc" "0" "the dry run exits 0"
assert_contains "$out" "Origin: image" "...prints the origin"
assert_contains "$out" "witness languages rus+eng" "...the bound sequence"
assert_contains "$out" "_<resource_id>.png" "...the raw's extension by the signature"
assert_contains "$out" "eng: present" "...each language of the sequence: present"
assert_contains "$out" "rus: missing" "...or missing"
assert_contains "$out" "the run would exit 3" "...and the exit 3 the run would take"
assert_contains "$out" "model-missing" "...with the reason"
assert_contains "$out" "fence probe for images: pass" "...and the reader's preflight"
assert_eq "$(reader_calls) $(tess_runs) $(stub_calls)" "0 0 0" "...no reading, no model call"
assert_eq "$(ls "$VSTATE/captures" | wc -l | tr -d ' ')" "$n0" "...no receipt"
OCR_CMD="$TEST_TMPDIR/no-tesseract-here" run_gw --origin image --file "$dry" --dry-run
assert_eq "$rc" "0" "the dry run with no witness exits 0"
assert_contains "$out" "Witness: NOT ready" "...and says that the run would exit 3"
assert_contains "$out" "no-tesseract" "...with the reason"
write_probe_record
run_gw --origin image --file "$dry" --dry-run
assert_eq "$rc" "0" "the dry run without the reader's probe entry exits 0"
assert_contains "$out" "Reader: NOT ready — the run would exit 3 before the image is kept (fence-unprobed)" "...and prints the exit 3 the run would take"
assert_contains "$out" "mnemon fence-probe" "...with the command"
assert_eq "$(reader_calls)" "0" "...no model call"
write_image_probe_record
[[ -n "$PROBE_STUB" ]] && export MNEMON_CAPTURE_IMAGE_CMD="$PROBE_STUB"

cleanup_capture_test
summary
