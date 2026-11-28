#!/usr/bin/env bash
# test_image.sh — the image origin through the gateway (docs/specs/2026-10-15-capture-contracts.md §18 and §19,
# amendments A15 and A16; docs/specs/2026-10-15-capture-layer.md §21 and §22, amendments A20 and A21): identity
# and the raw, the steps and their crash hooks, the replay at every boundary, the outcome table against the
# RESULT lines, the history of readings, the frontmatter keys, the reader's preflight, the notes on stderr, the
# report of either schema (section 14). Everything runs against stubs:
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
# contract K5: the source-add skill branches on two phrases of the engine's message — «the image is kept» after
# the raw is published, «nothing was kept» before it — and says nothing about the image when the message has neither
says_kept() { assert_contains "$err" "the image is kept" "$1: the message says the image is kept"; assert_not_contains "$err" "nothing was kept" "$1: ...and never that nothing was kept"; }
says_none_kept() { assert_contains "$err" "nothing was kept" "$1: the message says nothing was kept"; assert_not_contains "$err" "the image is kept" "$1: ...and never that the image is kept"; }
says_neither() { assert_not_contains "$err" "the image is kept" "$1: the message says nothing about the image being kept"; assert_not_contains "$err" "nothing was kept" "$1: ...nor that nothing was kept"; }
# the NOTE lines of a published image revision, by kind, in their order on stderr (capture layer §22.4)
note_order() {
  local line order=""
  while IFS= read -r line; do
    case "$line" in
      "NOTE: the local recognizer does not confirm "*) order="$order unconfirmed" ;;
      "NOTE: unconfirmed numbers: "*) order="$order unconfirmed-list" ;;
      "NOTE: the local recognizer read "*) order="$order missing" ;;
      "NOTE: missing numbers: "*) order="$order missing-list" ;;
      "NOTE: the reading's report: "*) order="$order path" ;;
    esac
  done <<< "$err"
  printf '%s' "${order# }"
}
path_line() { printf '%s\n' "$err" | sed -n "s/^NOTE: the reading's report: //p"; }
# the path line names the owner's sidecar: <the receipt's report_path>, a file, of the image report's schema
path_names() {   # <owner capture id> <schema> <label>
  local want
  want="$(rget "$1" '.report_path')"
  assert_eq "$(path_line)" "$want" "$3: the path line names the report_path of the owner's receipt"
  assert_eq "$(jq -r '.text.schema' "$want" 2>/dev/null)" "$2" "$3: ...a file that exists, its text.schema $2"
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
assert_eq "$(capfm "$src" compare_rule)" '"mnemon-read-compare/2"' "compare_rule names rule /2 (contracts A16 §3.3)"
assert_eq "$(capfm "$src" witness_share)" "100.0" "witness_share: a bare decimal with one fractional digit"
assert_eq "$(capfm "$src" witness_missing_words)" "0" "witness_missing_words"
assert_eq "$(capfm "$src" witness_missing_numbers)" "0" "witness_missing_numbers: a capture without such numbers carries the key with 0"
assert_eq "$(capfm "$src" unconfirmed_words)" "0" "unconfirmed_words"
assert_eq "$(capfm "$src" unconfirmed_numbers)" "0" "unconfirmed_numbers"
[[ "$(capfm "$src" compared_words)" =~ ^[1-9][0-9]*$ ]] && pass "compared_words: a bare integer" || fail "compared_words" "$(capfm "$src" compared_words)"
keys="$(fmblock "$src" | sed -n '/^capture:/,$p' | sed -n 's/^  \([a-z_0-9]*\):.*/\1/p' | tr '\n' ' ')"
assert_eq "$keys" "method fidelity raw_store raw_kind raw_sha256 raw_bytes text_sha256 text_bytes converter completeness image_format image_pixels reader_adapter reader_claude_code witness_backend witness_version witness_adapter witness_languages witness_execution compare_rule witness_share witness_missing_words witness_missing_numbers compared_words unconfirmed_words unconfirmed_numbers engine " "the capture: block in the order of contracts A15 §3.3, witness_missing_numbers after witness_missing_words (A16)"
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
assert_eq "$(rget i-1 '.capture_report.text.schema')" "mnemon-capture-image/2" "the receipt holds the worker's report, of the schema /2"
assert_eq "$(rget i-1 '.read_history | length')" "1" "read_history: one entry per attempt that ran the worker"
assert_eq "$(rget i-1 '.read_history[0] | keys | join(" ")')" "attempt claude_code cost_usd failure_class languages outcome output_tokens reader_adapter reader_model_id share text_sha256 thinking_tokens verdict witness_execution" "...with the keys of contracts A15 §3.2"
assert_eq "$(rget i-1 '.read_history[0] | "\(.attempt) \(.outcome) \(.failure_class) \(.verdict) \(.text_sha256) \(.languages)"')" "1 agreed null agree $STUB_TEXT_SHA eng" "...attempt 1 agreed"
[[ "$(rget i-1 '.read_history[0].cost_usd')" != "null" ]] && pass "...with the cost of its call" || fail "read_history cost" "null"
assert_file_exists "$ARCHIVE/$rev.report.json" "the archive sidecar is published"
assert_eq "$(jq -r '.text.schema' "$ARCHIVE/$rev.report.json")" "mnemon-capture-image/2" "...with the image report in its text member"
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
# every count at zero: stderr holds the path line alone (capture layer §22.4 item 3), naming the published sidecar
assert_eq "$err" "NOTE: the reading's report: $ARCHIVE/$rev.report.json" "every count at zero: stderr holds only the path of the reading's report"
assert_eq "$(rget i-1 '.report_path') $(rget i-1 '.report_state')" "$ARCHIVE/$rev.report.json published" "...the path that the receipt records, published"
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
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE:')" "NOTE: the reading's report: $ARCHIVE/$rev.report.json" "...and, every count at zero, the path line its only NOTE (§22.4 item 3)"
path_names i-1 mnemon-capture-image/2 "the duplicate"
# through an alias: the path of the owner's receipt (§22.4 item 3)
run_gw --origin image --file "$shot" --capture-id i-alias
assert_eq "$rc $(result revision)" "0 $rev" "a request under another id resolves to the stored revision"
path_names i-1 mnemon-capture-image/2 "through an alias"
assert_eq "$(rget i-alias '.report_path // "none"')" "none" "...the alias's own receipt records no report path: the path is the owner's"
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
# the other direction (capture layer §22.4 item 2): the witness read 87, the reading holds 88 in its place
assert_contains "$err" "NOTE: the local recognizer read 1 number(s) that the text does not hold" "the missing numbers' count goes to stderr"
assert_contains "$err" "NOTE: missing numbers: 87" "...with the witness's number that the text does not hold"
assert_eq "$(note_order)" "unconfirmed unconfirmed-list missing missing-list path" "...the lines in the order of §22.4: the two pairs, then the path"
path_names i-note mnemon-capture-image/2 "i-note"
assert_eq "$(capfm "$(src_of i-note)" witness_missing_numbers)" "1" "...and capture.witness_missing_numbers to the frontmatter"
assert_eq "$(capfm "$(src_of i-note)" unconfirmed_words)" "1" "...and to the frontmatter"
assert_eq "$(capfm "$(src_of i-note)" unconfirmed_numbers)" "1" "...both counts"
assert_eq "$(capfm "$(src_of i-note)" witness_missing_words)" "1" "...and the witness's missing word"
[[ "$(capfm "$(src_of i-note)" witness_share)" =~ ^9[0-9]\.[0-9]$ ]] && pass "witness_share under 100 with one decimal" || fail "witness_share" "$(capfm "$(src_of i-note)" witness_share)"
assert_not_contains "$(fmblock "$(src_of i-note)")" "team" "the unconfirmed words stay out of the frontmatter"
stub_reset
run_gw --capture-id i-note
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "a replay prints the notes again, from the stored report"
assert_contains "$err" "NOTE: missing numbers: 87" "...the missing numbers too"
assert_eq "$(note_order)" "unconfirmed unconfirmed-list missing missing-list path" "...in the same order, the path last"
path_names i-note mnemon-capture-image/2 "the replay"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...with no new comparison"
# a resume that reports the revision: the extraction failed after the publication, --resume finishes it
noter="$(mkimg note-resume.png)"
MNEMON_TEST_READER_MODE=other-text STUB_MODE=exit1 run_gw --origin image --file "$noter" --capture-id i-noter
assert_eq "$rc $(result status)" "4 captured" "a published reading whose extraction failed: captured"
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "...the notes printed"
assert_eq "$(note_order)" "unconfirmed unconfirmed-list missing missing-list path" "...both pairs and the path for a captured revision"
assert_contains "$err" "NOTE: missing numbers: 87" "...the missing numbers listed"
path_names i-noter mnemon-capture-image/2 "the captured revision"
stub_reset
run_gw --resume i-noter
assert_eq "$rc $(result status)" "0 extracted" "--resume finishes the extraction"
assert_contains "$err" "NOTE: unconfirmed numbers: 88" "...and a resume prints the notes again"
assert_eq "$(note_order)" "unconfirmed unconfirmed-list missing missing-list path" "...both pairs and the path, from the stored report"
path_names i-noter mnemon-capture-image/2 "the resume"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...reading nothing"
run_gw --origin image --file "$note"
assert_eq "$(result status)" "skipped-duplicate" "a duplicate"
assert_contains "$err" "NOTE: the local recognizer does not confirm 1 word(s) and 1 number(s) of the text" "...prints them too"
assert_eq "$(note_order)" "unconfirmed unconfirmed-list missing missing-list path" "...both pairs and the path"
path_names i-note mnemon-capture-image/2 "the duplicate of i-note"
# without a readable report the first line says so
cp "$(receipt i-note)" "$TEST_TMPDIR/i-note.receipt"
jq '.capture_report.text = null' "$TEST_TMPDIR/i-note.receipt" > "$(receipt i-note)"
run_gw --origin image --file "$note"
assert_contains "$err" "the list is unavailable" "without a stored report the note says that the list is unavailable"
assert_not_contains "$err" "unconfirmed numbers:" "...and lists nothing"
# §22.4 «Without a readable report»: the count of missing numbers from the frontmatter, its list unavailable, no path
assert_contains "$err" "NOTE: the local recognizer read 1 number(s) that the text does not hold (the list is unavailable: this ledger holds no report of the reading)" "...the missing numbers' count from capture.witness_missing_numbers, the list unavailable"
assert_not_contains "$err" "missing numbers:" "...no list of missing numbers"
assert_not_contains "$err" "the reading's report:" "...and no path line without a readable report"
assert_eq "$(note_order)" "unconfirmed missing" "...one line of each pair"
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
says_neither "fence-unprobed before any raw"
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
# a replay of a kept image reads again: the reader's preflight runs on that path too (capture layer §21.4,
# §21.10, §21.12 item 4) — fence-unprobed, exit 3, the image still kept, no model call; the entry back → published
stub_reset
prk="$(mkimg pre-kept.png)"
MNEMON_TEST_READER_MODE=error run_gw --origin image --file "$prk" --capture-id i-prk
assert_eq "$rc $(result error_class) $(result retryable)" "1 convert true" "a reader error keeps the image: failed/convert, retryable"
prk_raw="$(rget i-prk '.raw_path')"
assert_eq "$(rget i-prk '.raw_published') $(sha_of "$prk_raw")" "true $(sha_of "$prk")" "...the raw published in the store"
write_probe_record     # the record without the entry image-reader
r0="$(reader_calls)"; t0="$(tess_calls)"
run_gw --capture-id i-prk
assert_eq "$rc $(result status) $(result error_class)" "3 failed fence-unprobed" "the replay of a kept image without the reader's probe entry: exit 3, fence-unprobed"
says_neither "i-prk (fence-unprobed on the replay of a kept image)"
assert_contains "$err" "mnemon fence-probe" "...the message names the command"
assert_eq "$(( $(reader_calls) - r0 )) $(( $(tess_calls) - t0 ))" "0 0" "...no reader call, no witness run"
assert_eq "$(rget i-prk '.raw_path') $(sha_of "$prk_raw")" "$prk_raw $(sha_of "$prk")" "...the image still kept, unchanged"
assert_eq "$(rget i-prk '.source_published') $(rget i-prk '.state')" "false failed" "...nothing published, the receipt failed and replayable"
[[ -d "$VAULT/Sources/$(rget i-prk '.revision')" ]] && fail "no source folder after fence-unprobed" "exists" || pass "...no source folder"
write_image_probe_record
rm -f "$prk"     # the retained raw is the input now
run_gw --capture-id i-prk
assert_eq "$rc $(result status)" "0 extracted" "the entry back: the same replay publishes from the kept image"
assert_eq "$(( $(reader_calls) - r0 ))" "1" "...with one reader call"

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
says_none_kept "i-big (over image_max_bytes, before the copy)"
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
says_none_kept "i-swap (the input changed before the copy)"
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
says_kept "i-env (no tesseract after the raw is published)"
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
assert_eq "$(rget i-envr '.capture_report.text.witness.absent | join(",")')" "lang:rus" "...the report names what is absent (witness.absent, capture layer §22.3)"
assert_eq "$(rget i-envr '.capture_report.text.witness | has("missing")')" "false" "...and has no key witness.missing"
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
says_kept "i-map2 (the deadline)"
assert_eq "$(rget i-map2 '.read_history[0] | "\(.outcome) \(.failure_class)"')" "timeout timeout" "...outcome timeout"
assert_eq "$(rget i-map2 '.stage')" "reader" "...the stage reached, from the worker's progress"
assert_eq "$(rget i-map2 '.capture_report.text.failure.reason')" "deadline" "...the supervisor's report in the receipt"
map3="$(mkimg map-fault.png)"
MNEMON_TEST_TESSERACT_MODE=hang MNEMON_TEST_IMAGE_WORKER_CRASH=1 run_gw --origin image --file "$map3" --capture-id i-map3
assert_eq "$rc" "1" "a worker that crashes with a live recognizer: exit 1"
assert_eq "$(result error_class) $(result retryable)" "convert true" "...convert, retryable: a fault that says nothing about the image"
says_kept "i-map3 (the supervisor's worker-fault report)"
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
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE:')" "NOTE: the reading's report: $(rget i-pub '.report_path')" "...a resume with every count at zero prints the path line alone (§22.4 item 3)"
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
    assert_eq "$(rget "$id" '.capture_report.text.schema')" "mnemon-capture-image/2" "$id: the report is in the receipt"
  fi
  assert_eq "$(result revision)$(result path)" "" "$id: no revision in RESULT"
  assert_eq "$(rget "$id" '.source_published')" "false" "$id: nothing published"
  assert_eq "$(stub_calls)" "0" "$id: the extractor was never called"
  says_kept "$id"
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
assert_eq "$(capfm "$onechar" witness_missing_numbers)" "0" "...and witness_missing_numbers: a revision without witness_share carries the key too (contracts A16 §3.3)"

# the gateway's own reading of the worker's exit, row by row (a fake worker through the seam)
cat > "$TEST_TMPDIR/fake-worker.sh" <<'FAKE'
#!/usr/bin/env bash
# a fake image worker: writes the report FAKE_REPORT names (a key of the fixture image.json, or none),
# a capture-text when FAKE_TEXT=yes (an empty one when FAKE_TEXT=empty), and exits FAKE_RC
out=""; report=""; prev=""
for a in "$@"; do
  case "$prev" in --out) out="$a" ;; --report) report="$a" ;; esac
  prev="$a"
done
if [[ "${FAKE_REPORT:-none}" != "none" ]]; then jq --arg k "$FAKE_REPORT" '.[$k]' "$FAKE_FIXTURE" > "$report"; fi
[[ "${FAKE_TEXT:-no}" == "yes" ]] && printf 'a reading\n' > "$out"
[[ "${FAKE_TEXT:-no}" == "empty" ]] && : > "$out"
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
# a model id outside the pattern (contracts A15 §3.3): the message says «nothing is published» and nothing about the image
jq '.done.method = "read:not a model id"' "$REPORTS/image.json" > "$TEST_TMPDIR/image-badid.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-badid.json" fake f-badid 0 done yes
assert_eq "$rc $(result error_class) $(result retryable)" "1 convert true" "a report whose model id fails the pattern: convert, retryable"
assert_contains "$err" "nothing is published" "...the message says nothing is published"
says_neither "f-badid (no usable model id)"
assert_eq "$(rget f-badid '.source_published')" "false" "...nothing published"
# the NOTE lists at most twenty unconfirmed numbers (capture layer §21.10)
jq '.done.comparison.unconfirmed_numbers = 25 | .done.comparison.unconfirmed.numbers = [range(101; 126) | tostring]' "$REPORTS/image.json" > "$TEST_TMPDIR/image-25.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-25.json" fake f-25 0 done yes
assert_contains "$err" "NOTE: unconfirmed numbers: $(jq -rn '[range(101; 121) | tostring] | join(" ")') … (the first twenty of 25)" "25 unconfirmed numbers: the NOTE lists the first twenty"
assert_not_contains "$(printf '%s\n' "$err" | grep '^NOTE: unconfirmed numbers:')" "121" "...and no more"
# a stored number keeps every character but control characters and whitespace: the comparison counts a number by
# \d, so Unicode decimal digits (Arabic-Indic, fullwidth) are numbers and are listed as they stand (§21.10)
python3 - "$REPORTS/image.json" "$TEST_TMPDIR/image-unicode.json" <<'PY'
import json, sys
rep = json.load(open(sys.argv[1], encoding="utf-8"))
nums = [chr(0x661) + chr(0x662) + chr(0x663), chr(0xFF14) + chr(0xFF15) + chr(0xFF16),
        "7" + chr(0x1F) + "8", "9" + chr(0x20) + "0" + chr(0x3000) + chr(0x0A)]
rep["done"]["comparison"]["unconfirmed_numbers"] = len(nums)
rep["done"]["comparison"]["unconfirmed"]["numbers"] = nums
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump(rep, fh, ensure_ascii=True)
PY
want="$(python3 -c 'print(" ".join([chr(0x661) + chr(0x662) + chr(0x663), chr(0xFF14) + chr(0xFF15) + chr(0xFF16), "78", "90"]))')"
FAKE_FIXTURE="$TEST_TMPDIR/image-unicode.json" fake f-uni 0 done yes
assert_eq "$rc" "0" "a report with Unicode digits among the unconfirmed numbers: published"
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE: unconfirmed numbers:')" "NOTE: unconfirmed numbers: $want" "...the NOTE lists the Arabic-Indic and the fullwidth number; a control character, spaces and a line end are dropped"
# the notes of the missing numbers (capture layer §22.4 item 2), from the report's missing_lists: zero → neither line,
# the pair of unconfirmed numbers then stands alone
jq '.done.comparison.missing_numbers = 0 | .done.comparison.missing_lists.numbers = [] | .done.comparison.witness_numbers = 7' "$REPORTS/image.json" > "$TEST_TMPDIR/image-nomiss.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-nomiss.json" fake f-nomiss 0 done yes
assert_eq "$rc" "0" "a report with unconfirmed numbers and no missing number: published"
assert_eq "$(note_order)" "unconfirmed unconfirmed-list path" "...missing_numbers 0: neither line of the pair, the pair of unconfirmed numbers alone, then the path"
assert_eq "$(capfm "$(src_of f-nomiss)" witness_missing_numbers)" "0" "...capture.witness_missing_numbers 0"
FAKE_FIXTURE="$REPORTS/image.json" fake f-miss4 0 done yes
assert_eq "$(note_order)" "unconfirmed unconfirmed-list missing missing-list path" "the fixture's done report: both pairs, then the path"
assert_contains "$err" "NOTE: the local recognizer read 4 number(s) that the text does not hold" "...the count of comparison.missing_numbers"
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE: missing numbers:')" "NOTE: missing numbers: 10.15 11.30 4 128" "...the list of comparison.missing_lists.numbers, as the witness text spells them"
assert_eq "$(capfm "$(src_of f-miss4)" witness_missing_numbers)" "4" "...and capture.witness_missing_numbers from comparison.missing_numbers"
assert_eq "$(capfm "$(src_of f-miss4)" witness_missing_words)" "8" "...capture.witness_missing_words from comparison.missing_words"
# at most twenty, then the tail
jq '.done.comparison.missing_numbers = 25 | .done.comparison.missing_lists.numbers = [range(201; 226) | tostring] | .done.comparison.witness_numbers = 32' "$REPORTS/image.json" > "$TEST_TMPDIR/image-miss25.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-miss25.json" fake f-miss25 0 done yes
assert_contains "$err" "NOTE: the local recognizer read 25 number(s) that the text does not hold" "25 missing numbers: the count line"
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE: missing numbers:')" "NOTE: missing numbers: $(jq -rn '[range(201; 221) | tostring] | join(" ")') … (the first twenty of 25)" "...the first twenty, then the tail"
assert_eq "$(capfm "$(src_of f-miss25)" witness_missing_numbers)" "25" "...the frontmatter key holds the whole count"
# the character rule of §21.10 on the new list: control characters and whitespace removed, a number left empty dropped
python3 - "$REPORTS/image.json" "$TEST_TMPDIR/image-miss-unicode.json" <<'PY2'
import json, sys
rep = json.load(open(sys.argv[1], encoding="utf-8"))
nums = [chr(0x661) + chr(0x662) + chr(0x663), chr(0xFF14) + chr(0xFF15) + chr(0xFF16),
        "7" + chr(0x1F) + "8", "9" + chr(0x20) + "0" + chr(0x3000) + chr(0x0A), chr(0x85) + chr(0x20) + chr(0x2028)]
c = rep["done"]["comparison"]
c["missing_numbers"] = len(nums)
c["witness_numbers"] = c["reading_numbers"] - c["unconfirmed_numbers"] + len(nums)
c["missing_lists"]["numbers"] = nums
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump(rep, fh, ensure_ascii=True)
PY2
want="$(python3 -c 'print(" ".join([chr(0x661) + chr(0x662) + chr(0x663), chr(0xFF14) + chr(0xFF15) + chr(0xFF16), "78", "90"]))')"
FAKE_FIXTURE="$TEST_TMPDIR/image-miss-unicode.json" fake f-miss-uni 0 done yes
assert_eq "$rc" "0" "a report with Unicode digits, a control character, whitespace and an empty entry among the missing numbers: published"
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE: missing numbers:')" "NOTE: missing numbers: $want" "...the list keeps the Arabic-Indic and the fullwidth number, drops a control character and whitespace, and drops a number left empty"
assert_contains "$err" "NOTE: the local recognizer read 5 number(s) that the text does not hold" "...the count line names the report's count"
assert_eq "$(printf '%s\n' "$err" | grep -c '^NOTE: missing numbers:')" "1" "...one line"
fake f-notext 0 done no
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-notext '.stage')" "1 convert true worker" "exit 0 with a right report and no capture.md: the worker's fault — convert, retryable, stage worker"
assert_eq "$(rget f-notext '.source_published') $(rget f-notext '.text_sha256')" "false null" "...nothing published, no text hash taken"
assert_eq "$(rget f-notext '.read_history[0].outcome')" "worker-fault" "...outcome worker-fault"
# contract K1, the «or empty» arm: an empty capture.md is checked before it is hashed, like a missing one
fake f-emptytext 0 done empty
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-emptytext '.stage')" "1 convert true worker" "exit 0 with a right report and an empty capture.md: the worker's fault — convert, retryable, stage worker"
assert_eq "$(rget f-emptytext '.source_published') $(rget f-emptytext '.text_sha256')" "false null" "...nothing published, no text hash taken"
assert_eq "$(rget f-emptytext '.read_history[0].outcome')" "worker-fault" "...outcome worker-fault"
[[ -d "$VAULT/Sources/$(rget f-emptytext '.revision')" ]] && fail "f-emptytext: no source folder" "exists" || pass "...no source folder"
for pair in "0 compare yes" "0 none yes" "1 done yes" "1 none no" "1 probe no" "1 timeout no" "3 done yes" "3 compare no" "3 none no" "143 none no" "2 none no" "9 done yes"; do
  set -- $pair
  fake "f-$1-$2" "$1" "$2" "$3"
  assert_eq "$rc $(result error_class) $(result retryable) $(rget "f-$1-$2" '.stage')" "1 convert true worker" "worker exit $1 with report '$2': the gateway's own worker fault (convert, retryable, stage worker)"
  says_kept "f-$1-$2 (the gateway's own worker fault)"
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
# the three probe reasons of contract K2, read by the dry run as by the doctor (capture layer §21.10: exit 0, the
# exit 3 the run would take printed with its reason)
for mode in langs-fail langs-no-dir; do
  MNEMON_TEST_TESSERACT_MODE="$mode" run_gw --origin image --file "$dry" --dry-run
  assert_eq "$rc" "0" "the dry run with the stub's $mode (no language model directory) exits 0"
  assert_contains "$out" "Witness: NOT ready — the run would exit 3 after the image is kept (environment: model-directory; missing: model-directory)" "...and prints the exit 3 the run would take: model-directory"
done
printf 'model rus\n' > "$TESSDATA/rus.traineddata"
chmod 0 "$TESSDATA/rus.traineddata"
if [[ -r "$TESSDATA/rus.traineddata" ]]; then
  echo "  (model-unreadable not checked: a file of mode 000 is readable to this user)"
else
  run_gw --origin image --file "$dry" --ocr-languages rus+eng --dry-run
  assert_eq "$rc" "0" "the dry run with an unreadable language model exits 0"
  assert_contains "$out" "Witness: NOT ready — the run would exit 3 after the image is kept (environment: model-unreadable; missing: lang:rus)" "...and prints the exit 3 the run would take: model-unreadable"
fi
chmod 644 "$TESSDATA/rus.traineddata"; rm -f "$TESSDATA/rus.traineddata"
# an executable whose interpreter does not exist: it resolves, and it cannot be started
printf '#!/nonexistent/interpreter\n' > "$TEST_TMPDIR/unstartable-tesseract"
chmod 755 "$TEST_TMPDIR/unstartable-tesseract"
OCR_CMD="$TEST_TMPDIR/unstartable-tesseract" run_gw --origin image --file "$dry" --dry-run
assert_eq "$rc" "0" "the dry run with a tesseract that cannot be started exits 0"
assert_contains "$out" "Witness: NOT ready — the run would exit 3 after the image is kept (environment: tesseract-start; missing: tesseract)" "...and prints the exit 3 the run would take: tesseract-start"
assert_eq "$(reader_calls) $(tess_runs)" "0 0" "...no dry run read the image or called the model"
write_probe_record
run_gw --origin image --file "$dry" --dry-run
assert_eq "$rc" "0" "the dry run without the reader's probe entry exits 0"
assert_contains "$out" "Reader: NOT ready — the run would exit 3 before the image is kept (fence-unprobed)" "...and prints the exit 3 the run would take"
assert_contains "$out" "mnemon fence-probe" "...with the command"
assert_eq "$(reader_calls)" "0" "...no model call"
write_image_probe_record

# ---------------------------------------------------------------------------------------------
echo "--- 14. the witness's numbers that the text lacks; the report of either schema (capture layer §22, contracts A16) ---"
# through the real worker: the witness's mode `numbers` is the text of `ok` and a line `10:15 - 11:30`
stub_reset
run_gw --origin image --file "$(mkimg nums-plain.png)" --capture-id i-nums-plain
assert_eq "$rc $(result status)" "0 extracted" "(a capture whose witness reads no number that the text lacks)"
plain_out="$out"
stub_reset
MNEMON_TEST_TESSERACT_MODE=numbers run_gw --origin image --file "$(mkimg nums.png)" --capture-id i-nums
assert_eq "$rc $(result status)" "0 extracted" "a witness text with numbers that the reading lacks: published, never refused"
assert_eq "$(capfm "$(src_of i-nums)" witness_missing_numbers)" "4" "capture.witness_missing_numbers holds the count (contracts A16 §3.3)"
assert_eq "$(capfm "$(src_of i-nums)" compare_rule)" '"mnemon-read-compare/2"' "...with compare_rule mnemon-read-compare/2"
assert_eq "$(capfm "$(src_of i-nums)" witness_missing_words)" "0" "...and no missing word"
assert_eq "$(note_order)" "missing missing-list path" "the pair of missing numbers without the pair of unconfirmed ones, then the path"
assert_contains "$err" "NOTE: the local recognizer read 4 number(s) that the text does not hold" "...the count"
assert_eq "$(printf '%s\n' "$err" | grep '^NOTE: missing numbers:')" "NOTE: missing numbers: 10 15 11 30" "...the list, in the witness's order"
path_names i-nums mnemon-capture-image/2 "i-nums"
assert_eq "$(rget i-nums '.capture_report.text.comparison.missing_lists.numbers | join(" ")')" "10 15 11 30" "the receipt's report holds the list"
assert_eq "$(jq -r '.text.comparison.missing_lists.numbers | join(" ")' "$(rget i-nums '.report_path')")" "10 15 11 30" "...and so does the archive sidecar"
assert_not_contains "$(fmblock "$(src_of i-nums)")" "11 30" "...the list never reaches the frontmatter"
# the comparison's lists add no RESULT line and put nothing into one (§22.4 «Not changed»)
result_block() { printf '%s\n' "$1" | grep '^RESULT:' | sed -E 's/^(RESULT:(capture_id|revision|path)=).*/\1<id>/; s/[0-9a-f]{64}/<sha>/g'; }
assert_eq "$(result_block "$out")" "$(result_block "$plain_out")" "the RESULT block of a capture with missing numbers equals the block without them, line for line, but for the ids and hashes"
assert_eq "$(result_block "$out" | wc -l | tr -d ' ')" "$(result_block "$plain_out" | wc -l | tr -d ' ')" "...as many RESULT lines"
# the path line is absent when the freeze failed, without archive_dir, and while the sidecar's publication is pending
stub_reset
MNEMON_TEST_TESSERACT_MODE=numbers MNEMON_TEST_REPORT_FREEZE_FAIL=1 run_gw --origin image --file "$(mkimg nums-nofreeze.png)" --capture-id i-nofreeze
assert_eq "$rc $(rget i-nofreeze '.report_state')" "0 null" "a capture whose report freeze fails is published, with no frozen report"
assert_eq "$(note_order)" "missing missing-list" "...its notes from the receipt's report, and no path line"
stub_reset
MNEMON_TEST_TESSERACT_MODE=numbers run_cfg "$TEST_TMPDIR/noarchive.yaml" --origin image --file "$(mkimg nums-noarchive.png)" --capture-id i-noarchive
assert_eq "$rc $(rget i-noarchive '.report_state')" "0 null" "an instance without archive_dir: published, no sidecar"
assert_eq "$(note_order)" "missing missing-list" "...the notes, and no path line"
cat > "$TEST_TMPDIR/tess-ro.sh" <<RO
#!/usr/bin/env bash
# the stub tesseract that makes the archive read-only while the worker runs: the raw is published, the sidecar is not
chmod 555 "$ARCHIVE"
exec "$IMGFIX/stub-tesseract.py" "\$@"
RO
chmod +x "$TEST_TMPDIR/tess-ro.sh"
stub_reset
OCR_CMD="$TEST_TMPDIR/tess-ro.sh" run_gw --origin image --file "$(mkimg pending.png)" --capture-id i-pending
chmod 755 "$ARCHIVE"
assert_eq "$rc $(result status) $(result report)" "0 extracted failed" "the sidecar's publication fails after the source exists: published, RESULT:report=failed"
assert_eq "$(rget i-pending '.report_state')" "pending" "...report_state pending"
assert_eq "$(note_order)" "" "...no path line while the publication is pending (every count at zero)"
run_gw --capture-id i-pending
assert_eq "$rc $(result report) $(rget i-pending '.report_state')" "0 ok published" "the replay repairs the sidecar"
assert_eq "$(note_order)" "path" "...and then prints the path line"
path_names i-pending mnemon-capture-image/2 "after the repair"
# the sidecar on disk while the receipt still says pending (the window between the sidecar's rename and the receipt
# update): the receipt decides — no path line on a duplicate, which repairs nothing of the owner's
cp "$(receipt i-pending)" "$TEST_TMPDIR/i-pending.receipt"
jq '.report_state = "pending"' "$TEST_TMPDIR/i-pending.receipt" > "$(receipt i-pending)"
run_gw --origin image --file "$TEST_TMPDIR/pending.png"
assert_eq "$rc $(result status) $(rget i-pending '.report_state')" "0 skipped-duplicate pending" "a duplicate while the owner's receipt says pending"
assert_file_exists "$(rget i-pending '.report_path')" "...the sidecar is on disk"
assert_eq "$(note_order)" "" "...and no path line: report_state is not published"
cp "$TEST_TMPDIR/i-pending.receipt" "$(receipt i-pending)"

# a stored /1 report stays readable (J4): a revision published before A21, its receipt and its sidecar of the /1
# shape, its frontmatter without witness_missing_numbers — made here from a published revision, the way the
# earlier engine left it
export FAKE_FIXTURE="$REPORTS/image.json"
STUB_MODE=exit1 fake i-v1 0 done yes
assert_eq "$rc $(result status)" "4 captured" "(a published image revision whose extraction failed)"
v1rev="$(rget i-v1 '.revision')"
python3 - "$(receipt i-v1)" "$REPORTS/image-v1.json" "$(src_of i-v1)" <<'PY2'
import hashlib, json, os, sys
receipt_path, fixture, source = sys.argv[1:4]
receipt = json.load(open(receipt_path, encoding="utf-8"))
v1 = json.load(open(fixture, encoding="utf-8"))["done"]
receipt["capture_report"]["text"] = v1
cr = receipt["capture_report"]
report = {"schema": "mnemon-capture-report/1", "fetch": cr.get("fetch"), "text": cr.get("text"),
          "site": cr.get("site"), "media": receipt.get("media_meta")}
data = (json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
receipt["report_sha256"] = hashlib.sha256(data).hexdigest()
targets = [receipt["report_path"]]
if receipt.get("attempt_dir") and os.path.isfile(os.path.join(receipt["attempt_dir"], "report.json")):
    targets.append(os.path.join(receipt["attempt_dir"], "report.json"))
for target in targets:
    with open(target, "wb") as fh:
        fh.write(data)
with open(receipt_path, "w", encoding="utf-8") as fh:
    json.dump(receipt, fh, indent=1, ensure_ascii=False)
text = open(source, encoding="utf-8").read()
head, sep, body = text.partition("\n---\n")
head = "\n".join(l for l in head.split("\n") if not l.startswith("  witness_missing_numbers:"))
head = head.replace('compare_rule: "mnemon-read-compare/2"', 'compare_rule: "mnemon-read-compare/1"')
open(source, "w", encoding="utf-8").write(head + sep + body)
PY2
assert_eq "$(rget i-v1 '.capture_report.text.schema') $(capfm "$(src_of i-v1)" compare_rule)" 'mnemon-capture-image/1 "mnemon-read-compare/1"' "(the receipt holds the /1 report, the frontmatter names rule /1)"
v1sha="$(rget i-v1 '.report_sha256')"
v1_notes() {   # <label>
  assert_eq "$rc" "0" "$1: exit 0"
  assert_eq "$(note_order)" "unconfirmed unconfirmed-list path" "$1: the two lines of §21.10 and the path line, no line about missing numbers"
  assert_contains "$err" "NOTE: the local recognizer does not confirm 1 word(s) and 2 number(s) of the text" "$1: ...the counts of the /1 report"
  assert_contains "$err" "NOTE: unconfirmed numbers: 12 3.4" "$1: ...its unconfirmed numbers"
  path_names i-v1 mnemon-capture-image/1 "$1"
  assert_eq "$(fmblock "$(src_of i-v1)" | grep -c '^  witness_missing_numbers:')" "0" "$1: ...the frontmatter without witness_missing_numbers"
}
stub_reset
run_gw --resume i-v1
assert_eq "$(result status)" "extracted" "a resume of the /1 revision finishes the extraction"
v1_notes "the resume of a /1 revision"
run_gw --capture-id i-v1
v1_notes "the replay of a /1 revision"
run_gw --origin image --file "$TEST_TMPDIR/i-v1.png"
assert_eq "$(result status)" "skipped-duplicate" "a duplicate of the /1 revision"
v1_notes "the duplicate of a /1 revision"
assert_eq "$(tess_calls) $(reader_calls)" "0 0" "...nothing read again"
rm -f "$ARCHIVE/$v1rev.report.json"
run_gw --origin image --file "$TEST_TMPDIR/i-v1.png"
assert_eq "$(rget i-v1 '.report_state') $(note_order)" "published unconfirmed unconfirmed-list" "the receipt says published and the file is gone: a duplicate prints no path line"
run_gw --capture-id i-v1
assert_eq "$rc $(result report)" "0 ok" "a /1 sidecar removed: the replay publishes it again from the receipt"
assert_eq "$(shasum -a 256 "$ARCHIVE/$v1rev.report.json" | cut -c1-64) $(rget i-v1 '.report_sha256')" "$v1sha $v1sha" "...with its recorded hash"
assert_eq "$(jq -r '.text.schema' "$ARCHIVE/$v1rev.report.json")" "mnemon-capture-image/1" "...still a /1 report"
v1_notes "the replay that published the /1 sidecar again"
# an unpublished capture whose receipt holds a /1 report of an earlier attempt: the replay is a new attempt, /2
stub_reset
MNEMON_TEST_READER_MODE=error run_gw --origin image --file "$(mkimg v1-unpub.png)" --capture-id i-v1u
assert_eq "$rc $(result error_class)" "1 convert" "(a capture that published nothing)"
cp "$(receipt i-v1u)" "$TEST_TMPDIR/i-v1u.receipt"
jq --slurpfile v "$REPORTS/image-v1.json" '.capture_report.text = $v[0].done' "$TEST_TMPDIR/i-v1u.receipt" > "$(receipt i-v1u)"
assert_eq "$(rget i-v1u '.capture_report.text.schema')" "mnemon-capture-image/1" "(the receipt holds the /1 report of an earlier attempt)"
run_gw --capture-id i-v1u
assert_eq "$rc $(result status)" "0 extracted" "the replay publishes"
assert_eq "$(rget i-v1u '.capture_report.text.schema')" "mnemon-capture-image/2" "...and writes a /2 report in place of the /1 one"
assert_eq "$(capfm "$(src_of i-v1u)" witness_missing_numbers) $(capfm "$(src_of i-v1u)" compare_rule)" '0 "mnemon-read-compare/2"' "...its revision carries the key and rule /2"
# a worker's report of the schema /1 on a fresh attempt is no readable report (§22.3 «A fresh report is /2»)
FAKE_FIXTURE="$REPORTS/image-v1.json" fake f-v1 0 done yes
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-v1 '.stage')" "1 convert true worker" "a fresh /1 report with exit 0: the gateway's own worker fault (convert, retryable, stage worker)"
assert_eq "$(rget f-v1 '.read_history[0].outcome') $(rget f-v1 '.capture_report.text')" "worker-fault null" "...outcome worker-fault, no report in the receipt"
assert_eq "$(rget f-v1 '.source_published')" "false" "...nothing published"
FAKE_FIXTURE="$REPORTS/image-v1.json" fake f-v1t 124 done no
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-v1t '.stage')" "1 timeout false null" "a fresh /1 report with exit 124: the timeout, the stage unknown"
# exit 1 and exit 3 with a /1 report that names a failure: still no readable report, so the gateway's own
# worker fault and never the failure that the report names; the same two reports under /2 are that failure
jq '{envelope: (.envelope | .schema = "mnemon-capture-image/1"), probe: (.probe | .schema = "mnemon-capture-image/1")}' "$REPORTS/image.json" > "$TEST_TMPDIR/image-v1-failures.json"
FAKE_FIXTURE="$TEST_TMPDIR/image-v1-failures.json" fake f-v1e1 1 envelope no
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-v1e1 '.stage')" "1 convert true worker" "a fresh /1 report with exit 1 and a failure: the worker fault, not the failure it names"
assert_eq "$(rget f-v1e1 '.read_history[0].outcome') $(rget f-v1e1 '.capture_report.text')" "worker-fault null" "...outcome worker-fault, no report in the receipt"
FAKE_FIXTURE="$TEST_TMPDIR/image-v1-failures.json" fake f-v1e3 3 probe no
assert_eq "$rc $(result error_class) $(result retryable) $(rget f-v1e3 '.stage')" "1 convert true worker" "a fresh /1 report with exit 3 and an environment failure: the worker fault, exit 1"
FAKE_FIXTURE="$REPORTS/image.json" fake f-v2e1 1 envelope no
assert_eq "$rc $(result error_class) $(result retryable)" "1 unsupported false" "...the same report under /2 with exit 1: the failure it names"
FAKE_FIXTURE="$REPORTS/image.json" fake f-v2e3 3 probe no
assert_eq "$rc $(result error_class) $(result retryable)" "3 environment true" "...and under /2 with exit 3: the environment failure"
unset FAKE_FIXTURE
[[ -n "$PROBE_STUB" ]] && export MNEMON_CAPTURE_IMAGE_CMD="$PROBE_STUB"

cleanup_capture_test
summary
