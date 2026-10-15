#!/usr/bin/env bash
# test_capture.sh — the persistence contract (docs/specs/2026-10-15-capture-contracts.md §9 items
# 1–3, 5, 8–10, 15): byte fidelity, source before LLM + crash recovery, publication, identity /
# binding / replay, the URL chain, raw immutability, PDF and files, the flat parsers. Everything
# runs against stubs (tests/fixtures/capture/setup.sh); no network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Capture Contract Tests ==="

# ---------------------------------------------------------------------------------------------
echo "--- 1. byte fidelity ---"
printf 'line one\r\nline two\n\n\001\002\003 bin\xc3\xa9ry-ish \t tail\n\n\n' > "$TEST_TMPDIR/fid.txt"
in_sha="$(shasum -a 256 "$TEST_TMPDIR/fid.txt" | cut -c1-64)"
run_gw_stdin "$TEST_TMPDIR/fid.txt" --origin text --capture-id fid-1 --title "Fidelity"
assert_eq "$rc" "0" "text capture exits 0"
rev="$(result revision)"
assert_eq "$(result status)" "extracted" "status extracted"
python3 - "$VAULT/Sources/$rev/source.md" "$TEST_TMPDIR/fid.txt" <<'PY' && pass "source.md body == stdin bytes (CRLF, trailing newlines, control bytes)" || fail "source.md body == stdin bytes" "differs"
import sys
d = open(sys.argv[1], "rb").read(); i = d.find(b"\n---\n", 3)
sys.exit(0 if d[i + 5:] == open(sys.argv[2], "rb").read() else 1)
PY
assert_eq "$(grep '^  text_sha256:' "$VAULT/Sources/$rev/source.md" | tr -d '" ' | cut -d: -f2)" "$in_sha" "capture.text_sha256 == recomputed"
assert_eq "$(result source_sha256)" "$in_sha" "RESULT:source_sha256 recomputed from the body"
assert_eq "$(rget fid-1 '.text_sha256')" "$in_sha" "receipt text_sha256"
assert_eq "$(rget fid-1 '.input_path')" "$VSTATE/runs/fid-1/attempt-1/capture.md" "stdin locator = attempt-1/capture.md"
assert_file_exists "$VSTATE/runs/fid-1/attempt-1/capture.md" "the locator survives the final receipt"
assert_eq "$(ls "$VSTATE/runs/fid-1/attempt-1" | tr '\n' ' ')" "capture.md " "the rest of the attempt dir is removed when the receipt is final"

# the same bytes through a batch manifest with inline content
python3 - "$TEST_TMPDIR/fid.txt" "$TEST_TMPDIR/manifest.json" <<'PY'
import json, sys
text = open(sys.argv[1], "rb").read().decode("utf-8")
json.dump([{"origin": "text", "content": text, "title": "Fidelity batch", "capture_id": "fid-batch"}], open(sys.argv[2], "w"), ensure_ascii=False)
PY
run_gw --manifest "$TEST_TMPDIR/manifest.json" --recapture
assert_eq "$rc" "0" "batch with inline content exits 0"
assert_contains "$out" "RESULT:batch_passed=1" "batch passed 1"
assert_eq "$(rget fid-batch '.text_sha256')" "$in_sha" "batch inline content → identical capture-text bytes (jq -j, never echoed)"
assert_contains "$(rget fid-batch '.input_path')" "/runs/batch-" "batch item input locator is the --file-text file under the ledger"
assert_eq "$(rget fid-batch '.revision')" "$rev-2" "--recapture in a batch → a -2 revision of the same resource"
assert_eq "$(fm "$VAULT/Sources/$rev-2/source.md" supersedes)" "\"$rev\"" "batch recapture carries supersedes"

# invalid UTF-8 → exit 1 invalid-utf8, nothing published, the dead letter keeps the content
printf 'ok \xff\xfe bad\n' > "$TEST_TMPDIR/bad.txt"
n0="$(nfolders)"
run_gw_stdin "$TEST_TMPDIR/bad.txt" --origin text --capture-id bad-utf8
assert_eq "$rc" "1" "invalid UTF-8 → exit 1"
assert_eq "$(result status)" "failed" "...status failed"
assert_eq "$(result error_class)" "invalid-utf8" "...error_class invalid-utf8"
assert_eq "$(result retryable)" "false" "...not retryable"
assert_eq "$(result revision)" "" "...no revision reported"
assert_eq "$(nfolders)" "$n0" "...nothing published"
assert_eq "$(rget bad-utf8 '.state')" "failed" "...receipt failed"
assert_file_exists "$VSTATE/runs/bad-utf8/attempt-1/capture.md" "...attempt dir kept with the content (dead letter)"

# ---------------------------------------------------------------------------------------------
echo "--- 2. source before LLM, directory publication, recovery ---"
stub_reset
run_gw --url "https://example.com/recovery" --capture-id rec-0
assert_eq "$rc" "0" "url capture with stub curl exits 0"
rev0="$(result revision)"
assert_contains "$(cat "$STUB_DIR/call-1.sources")" "Sources/$rev0/source.md" "the stub saw source.md published before it was called"
assert_eq "$(cat "$STUB_DIR/call-1.staging" | wc -l | tr -d ' ')" "0" "...and no staging directory"
assert_eq "$(cat "$STUB_DIR/call-1.cwd")" "$VSTATE/runs/rec-0/attempt-1" "the stub's cwd is the attempt dir"
assert_eq "$(staging_left)" "0" "no .staging-* left after a clean capture"
assert_eq "$(ls -d "$VSTATE/runs/rec-0" 2>/dev/null)" "" "url origin: the attempt dir is removed after the final receipt"

crash_run() {  # <boundary> <capture_id> <url>
  export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT="$1"
  run_gw --url "$3" --capture-id "$2"
  unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
}
n_before="$(nfolders)"

# after-raw-staged: staged file with its hash in the receipt; replay finishes the rename, no refetch
stub_reset
crash_run after-raw-staged rec-1 "https://example.com/rec1"
assert_eq "$(nfolders)" "$n_before" "crash after-raw-staged: no published revision"
assert_eq "$(rget rec-1 '.state')" "started" "...receipt started"
assert_eq "$(rget rec-1 '.raw_published')" "false" "...raw_published false"
staged="$(rget rec-1 '.raw_staged_path')"
assert_contains "$staged" "$ARCHIVE/.$(rget rec-1 '.revision').html.staging-" "...staged name is .<revision>.html.staging-<pid> in the destination dir"
assert_file_exists "$staged" "...the staged file exists"
assert_eq "$(shasum -a 256 "$staged" | cut -c1-64)" "$(rget rec-1 '.raw_sha256')" "...its hash is recorded"
c1="$(curl_calls)"
run_gw --capture-id rec-1
assert_eq "$rc" "0" "replay after-raw-staged → exit 0"
assert_eq "$(curl_calls)" "$c1" "...no refetch (curl not called again)"
assert_eq "$(result replayed)" "true" "...RESULT:replayed=true"
assert_file_exists "$ARCHIVE/$(rget rec-1 '.revision').html" "...raw renamed into place"
assert_eq "$(rget rec-1 '.raw_published')" "true" "...raw_published reconciled"
assert_eq "$(staging_left)" "0" "...no staging left"
assert_eq "$(rget rec-1 '.state')" "extracted" "...extracted"

# after-raw-published: final file exists, receipt behind → reconcile, no refetch
stub_reset
crash_run after-raw-published rec-2 "https://example.com/rec2"
assert_eq "$(rget rec-2 '.raw_published')" "true" "crash after-raw-published: receipt already true (the rename preceded it)"
c1="$(curl_calls)"
run_gw --capture-id rec-2
assert_eq "$rc" "0" "replay after-raw-published → exit 0"
assert_eq "$(curl_calls)" "$c1" "...no refetch"
assert_eq "$(rget rec-2 '.state')" "extracted" "...extracted"

# after-source-staged: a staged directory, no folder; replay removes it and continues from the raw
stub_reset
crash_run after-source-staged rec-3 "https://example.com/rec3"
assert_eq "$(ls -d "$VAULT"/Sources/.*.staging-* 2>/dev/null | wc -l | tr -d ' ')" "1" "crash after-source-staged: a staging dir is left"
assert_eq "$(nfolders)" "$((n_before + 2))" "...but no new published folder"
c1="$(curl_calls)"
run_gw --capture-id rec-3
assert_eq "$rc" "0" "replay after-source-staged → exit 0"
assert_eq "$(curl_calls)" "$c1" "...published raw reused, no refetch"
assert_eq "$(staging_left)" "0" "...stale staging dir removed"
assert_eq "$(rget rec-3 '.state')" "extracted" "...extracted"

# after-source-published: folder renamed, receipt behind → reconcile to source-published, extract
stub_reset
crash_run after-source-published rec-4 "https://example.com/rec4"
rev4="$(rget rec-4 '.revision')"
assert_file_exists "$VAULT/Sources/$rev4/source.md" "crash after-source-published: the folder exists"
assert_eq "$(rget rec-4 '.state')" "started" "...receipt still started"
stub_reset
run_gw --capture-id rec-4
assert_eq "$rc" "0" "replay after-source-published → exit 0"
assert_eq "$(stub_calls)" "1" "...one extraction call"
assert_eq "$(rget rec-4 '.state')" "extracted" "...extracted"
assert_eq "$(cat "$STUB_DIR/call-1.cwd")" "$VSTATE/runs/rec-4/attempt-2" "...under a new attempt"

# after-extract-published: extract.md renamed, receipt behind → reconcile, no model call
stub_reset
crash_run after-extract-published rec-5 "https://example.com/rec5"
rev5="$(rget rec-5 '.revision')"
assert_file_exists "$VAULT/Sources/$rev5/extract.md" "crash after-extract-published: extract.md exists"
assert_eq "$(rget rec-5 '.state')" "source-published" "...receipt behind (source-published)"
stub_reset
run_gw --capture-id rec-5
assert_eq "$rc" "0" "replay after-extract-published → exit 0"
assert_eq "$(stub_calls)" "0" "...no model call"
assert_eq "$(result status)" "extracted" "...status extracted"
assert_eq "$(result replayed)" "true" "...replayed"
assert_eq "$(rget rec-5 '.state')" "extracted" "...reconciled to extracted"
assert_eq "$(rget rec-5 '.extract_sha256')" "$(shasum -a 256 "$VAULT/Sources/$rev5/extract.md" | cut -c1-64)" "...extract_sha256 recorded"

# raw evidence is never refetched (§12 A3): a recorded raw_sha256 with neither staged nor final
# copy is evidence-missing; a published raw that changed or disappeared is evidence-mismatch
stub_reset
crash_run after-raw-staged ev-1 "https://example.com/ev1"
rm -f "$(rget ev-1 '.raw_staged_path')"
c1="$(curl_calls)"
run_gw --capture-id ev-1
assert_eq "$rc" "1" "a recorded raw with neither staged nor final copy → exit 1"
assert_eq "$(result error_class)" "evidence-missing" "...evidence-missing"
assert_eq "$(result retryable)" "false" "...permanent (operator)"
assert_eq "$(curl_calls)" "$c1" "...never refetched"
assert_eq "$(rget ev-1 '.state')" "failed" "...receipt failed"
run_gw --capture-id ev-1
assert_eq "$(result error_class)" "evidence-missing" "...and again on the next replay"
stub_reset
crash_run after-raw-published ev-2 "https://example.com/ev2"
printf 'tampered\n' > "$(rget ev-2 '.raw_path')"
c1="$(curl_calls)"
run_gw --capture-id ev-2
assert_eq "$rc" "1" "a published raw whose bytes changed → exit 1"
assert_eq "$(result error_class)" "evidence-mismatch" "...evidence-mismatch"
assert_eq "$(result retryable)" "false" "...permanent (operator)"
assert_eq "$(curl_calls)" "$c1" "...never refetched"
stub_reset
crash_run after-raw-published ev-3 "https://example.com/ev3"
rm -f "$(rget ev-3 '.raw_path')"
run_gw --capture-id ev-3
assert_eq "$(result error_class)" "evidence-mismatch" "a published raw that disappeared → evidence-mismatch"
assert_eq "$(nfolders)" "$((n_before + 5))" "...and none of the three evidence failures published a revision"

# a stub that exits 1 → source.md + no extract.md, extract-failed, dead letter, exit 4
stub_reset
STUB_MODE=exit1 run_gw --url "https://example.com/fail1" --capture-id fail-1
assert_eq "$rc" "4" "extractor exit 1 → exit 4"
revf="$(result revision)"
assert_file_exists "$VAULT/Sources/$revf/source.md" "...source.md published"
[[ -f "$VAULT/Sources/$revf/extract.md" ]] && fail "no extract.md" "exists" || pass "...no extract.md"
assert_eq "$(result status)" "captured" "...status captured"
assert_eq "$(result extract)" "missing" "...extract=missing"
assert_eq "$(result error_class)" "claude-exit" "...error_class claude-exit"
assert_eq "$(result retryable)" "true" "...retryable"
assert_eq "$(rget fail-1 '.state')" "extract-failed" "...receipt extract-failed"
assert_file_exists "$VSTATE/runs/fail-1/attempt-1/capture.md" "...attempt dir keeps capture.md"
assert_file_exists "$VSTATE/runs/fail-1/attempt-1/prompt.md" "...and prompt.md"
# --resume <revision> with a good stub
stub_reset
run_gw --resume "$revf"
assert_eq "$rc" "0" "--resume <revision> → exit 0"
assert_file_exists "$VAULT/Sources/$revf/extract.md" "...extract.md published"
assert_eq "$(rget fail-1 '.state')" "extracted" "...receipt extracted"
[[ "$(rget fail-1 '.finalized_at')" != "null" ]] && pass "...finalized_at set" || fail "finalized_at set" "null"
assert_eq "$(ls -d "$VSTATE/runs/fail-1" 2>/dev/null)" "" "...attempt dirs removed after the final receipt"
assert_eq "$(rget fail-1 '.attempts')" "2" "...attempts = 2"
run_gw --resume "$revf"
assert_eq "$rc" "2" "--resume of a complete capture → exit 2"
# attempt numbering continues from the receipt after the final cleanup removed the dirs
rm -f "$VAULT/Sources/$revf/extract.md"
stub_reset
run_gw --capture-id fail-1
assert_eq "$rc" "0" "a replay with extract.md gone re-extracts"
assert_eq "$(rget fail-1 '.attempts')" "3" "...attempts = 3 (the receipt's counter, never regressed by the cleanup)"
assert_eq "$(cat "$STUB_DIR/call-1.cwd")" "$VSTATE/runs/fail-1/attempt-3" "...in attempt-3"

# failed/convert with a published raw → the retry converts the retained raw, curl not called
stub_reset
STUB_CURL=empty STUB_DEFUDDLE=fail run_gw --url "https://example.com/conv" --capture-id conv-1
assert_eq "$rc" "1" "both converters empty → exit 1"
assert_eq "$(result error_class)" "convert" "...error_class convert"
assert_eq "$(result retryable)" "true" "...retryable"
assert_eq "$(result revision)" "" "...nothing published"
assert_file_exists "$ARCHIVE/$(rget conv-1 '.revision').html" "...raw retained"
c1="$(curl_calls)"
STUB_DEFUDDLE=ok run_gw --capture-id conv-1
assert_eq "$rc" "0" "retry converts the retained raw"
assert_eq "$(curl_calls)" "$c1" "...without calling curl"
assert_contains "$(cat "$VAULT/Sources/$(rget conv-1 '.revision')/source.md")" "from defuddle" "...body from the conversion of the retained raw"

# a staged raw that cannot be renamed into place (the archive dir turns read-only after the
# download) is a loud fetch failure: the receipt never claims raw_published, the staged copy and
# its hash are kept, and the replay finishes the rename without a refetch
stub_reset
STUB_CURL_HOOK="chmod 555 '$ARCHIVE'" run_gw --url "https://example.com/ro-archive" --capture-id ro-1
chmod 755 "$ARCHIVE"
assert_eq "$rc" "1" "a raw rename that fails → exit 1 (never a silent traceback)"
assert_eq "$(result error_class)" "fetch" "...error_class fetch"
assert_eq "$(result retryable)" "true" "...retryable"
assert_eq "$(rget ro-1 '.raw_published')" "false" "...the receipt does not claim raw_published"
ro_staged="$(rget ro-1 '.raw_staged_path')"
assert_file_exists "$ro_staged" "...the staged copy is retained"
assert_eq "$(rget ro-1 '.raw_sha256')" "$(shasum -a 256 "$ro_staged" | cut -c1-64)" "...with its hash recorded"
[[ -e "$ARCHIVE/$(rget ro-1 '.revision').html" ]] && fail "no final raw after the failed rename" "exists" || pass "...no final raw yet"
c1="$(curl_calls)"
run_gw --capture-id ro-1
assert_eq "$rc" "0" "the replay finishes the rename and extracts"
assert_eq "$(curl_calls)" "$c1" "...without a refetch"
assert_file_exists "$ARCHIVE/$(rget ro-1 '.revision').html" "...raw renamed into place"
assert_eq "$(rget ro-1 '.raw_published')" "true" "...raw_published reconciled"
assert_eq "$(staging_left)" "0" "...no staging left"
# a revision directory that cannot be published (Sources/ turns read-only after the download):
# exit 1 convert with the raw published, and the replay re-converts the retained raw
stub_reset
n_ro="$(nfolders)"
STUB_CURL_HOOK="chmod 555 '$VAULT/Sources'" run_gw --url "https://example.com/ro-sources" --capture-id ro-2
chmod 755 "$VAULT/Sources"
assert_eq "$rc" "1" "a source publication that fails → exit 1"
assert_eq "$(result error_class)" "convert" "...error_class convert"
assert_eq "$(result revision)" "" "...nothing published"
assert_eq "$(nfolders)" "$n_ro" "...no folder"
assert_eq "$(rget ro-2 '.raw_published')" "true" "...the raw is published"
assert_eq "$(rget ro-2 '.source_published')" "false" "...the source is not"
c1="$(curl_calls)"
run_gw --capture-id ro-2
assert_eq "$rc" "0" "the replay publishes the source from the retained raw"
assert_eq "$(curl_calls)" "$c1" "...without a refetch"
assert_eq "$(rget ro-2 '.state')" "extracted" "...extracted"
# raw_kind is recorded before the raw is published: a --render capture that crashed right after
# its raw publication is reconverted as rendered text, never pushed through the HTML converter
stub_reset
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-raw-published
STUB_RENDER=ok run_gw --url "https://example.com/rendered-crash" --render --capture-id rk-1
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
assert_eq "$(rget rk-1 '.raw_kind')" "rendered" "crash after-raw-published on a --render capture: the receipt already records raw_kind rendered"
STUB_RENDER=ok run_gw --capture-id rk-1
assert_eq "$rc" "0" "...the replay exits 0"
assert_contains "$(cat "$VAULT/Sources/$(rget rk-1 '.revision')/source.md")" '  method: "render-text"' "...reconverted as rendered text (method render-text)"
assert_contains "$(cat "$VAULT/Sources/$(rget rk-1 '.revision')/source.md")" 'rendered text content' "...with the rendered text as the body"

# ---------------------------------------------------------------------------------------------
echo "--- 3. publication ---"
ex="$VAULT/Sources/$rev0/extract.md"
assert_contains "$(cat "$ex")" 'title: "Plain T"' "extract.md: model title"
assert_contains "$(cat "$ex")" 'tags: [testing, capture, contracts]' "extract.md: flow list tags"
assert_contains "$(cat "$ex")" 'people: [Ada Lovelace]' "extract.md: flow list people"
assert_contains "$(cat "$ex")" 'domains: [learning]' "extract.md: domains"
assert_contains "$(cat "$ex")" 'rating: 6' "extract.md: rating"
assert_contains "$(cat "$ex")" 'status: extracted' "extract.md: status extracted"
assert_contains "$(cat "$ex")" "capture_id: \"rec-0\"" "extract.md: capture_id"
assert_contains "$(cat "$ex")" "revision: \"$rev0\"" "extract.md: revision"
assert_contains "$(cat "$ex")" 'url: "https://example.com/recovery"' "extract.md: url"
assert_contains "$(cat "$ex")" '  schema: extract/1' "extract.md: extractor schema"
assert_contains "$(cat "$ex")" '  claude_code: "9.9.9-stub (Claude Code)"' "extract.md: claude_code"
assert_contains "$(cat "$ex")" '  vault: captest' "extract.md: capture_context.vault = instance"
assert_contains "$(cat "$ex")" '### Key Ideas' "extract.md: body"
assert_eq "$(rget rec-0 '.extract_sha256')" "$(shasum -a 256 "$ex" | cut -c1-64)" "receipt extract_sha256 == sha256(extract.md)"
assert_contains "$(cat "$VAULT/index.md")" "[[Sources/$rev0/extract|Plain T]]" "index.md rebuilt before the gateway exits"
assert_eq "$(rget rec-0 '.argv[2]')" "--restricted" "receipt argv shows the fence"
# §12 A8: RESULT:reindex reports the hook's outcome; a failed rebuild never fails the capture
stub_reset
run_gw --url "https://example.com/reindex-ok" --capture-id ri-0
assert_eq "$(result reindex)" "ok" "RESULT:reindex=ok after an extraction"
assert_eq "$(printf '%s\n' "$out" | grep -c '^RESULT:reindex=')" "1" "...once"
chmod 555 "$VAULT"
run_gw --url "https://example.com/reindex-fail" --capture-id ri-1
chmod 755 "$VAULT"
assert_eq "$rc" "0" "a reindex failure (index.md unwritable) leaves the exit code at 0"
assert_eq "$(result status)" "extracted" "...status extracted"
assert_eq "$(result reindex)" "failed" "...RESULT:reindex=failed"
assert_contains "$err" "WARNING: index.md was not rebuilt" "...with a warning on stderr"
assert_contains "$err" "mnemon reindex" "...naming the command that rebuilds it"
assert_eq "$(rget ri-1 '.state')" "extracted" "...the receipt is extracted"
run_gw --capture-id ri-1
assert_eq "$(printf '%s\n' "$out" | grep -c '^RESULT:reindex=')" "0" "a replay from the files prints no reindex line"
run_gw --url "https://example.com/reindex-ok" --capture-id ri-dup
assert_eq "$(printf '%s\n' "$out" | grep -c '^RESULT:reindex=')" "0" "a duplicate prints no reindex line"
# the qmd refresh runs after a successful extraction (every success path exits inside cap_main,
# so a post-hook after it never ran), in the background, without the vault lock descriptor
sed 's/^instance: captest/instance: captest-qmd/; s/^search_provider: grep/search_provider: qmd/' "$CONFIG" > "$TEST_TMPDIR/qmd.yaml"
printf '#!/usr/bin/env bash\necho "$1 fd=${MNEMON_VAULT_LOCK_FD:-unset}" >> "$STUB_DIR/qmd-calls"\n' > "$STUBS/qmd"
chmod +x "$STUBS/qmd"
stub_reset
rc=0; "$GW" source-add --config "$TEST_TMPDIR/qmd.yaml" --url "https://example.com/qmd" --capture-id qmd-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "0" "a capture with search_provider qmd exits 0"
assert_eq "$(result status)" "extracted" "...extracted"
i=0; while [[ $i -lt 50 ]]; do [[ -f "$STUB_DIR/qmd-calls" && "$(wc -l < "$STUB_DIR/qmd-calls" | tr -d ' ')" == "2" ]] && break; sleep 0.1; i=$((i + 1)); done
assert_eq "$(cat "$STUB_DIR/qmd-calls" 2>/dev/null | tr '\n' ' ')" "update fd=unset embed fd=unset " "...qmd update and qmd embed ran after the extraction, without the lock descriptor"
rm -f "$STUBS/qmd"

# ---------------------------------------------------------------------------------------------
echo "--- 5. identity, binding, replay ---"
stub_reset
run_gw --capture-id fid-1
assert_eq "$rc" "0" "same --capture-id after success → exit 0"
assert_eq "$(result replayed)" "true" "...replayed=true"
assert_eq "$(result revision)" "$rev" "...same revision"
assert_eq "$(stub_calls)" "0" "...no model call"
assert_eq "$(result title)" "Plain T" "...title from the files"
run_gw_stdin "$TEST_TMPDIR/fid.txt" --origin text --capture-id fid-1 --title "Another title"
assert_eq "$rc" "0" "a different unbound field (--title) → replay"
assert_eq "$(result replayed)" "true" "...replayed"
run_gw --capture-id fid-1 --url "https://example.com/other" --origin url
assert_eq "$rc" "2" "a different bound field → exit 2"
assert_contains "$err" "capture-id reuse with a different request" "...with the contract message"

# --file origin: retained inputs win over the locator (§12 A4) — with the raw published, a moved or
# changed original is irrelevant; the locator is consulted only when nothing is retained
printf 'a markdown file\n' > "$TEST_TMPDIR/note.md"
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-source-staged
run_gw --file "$TEST_TMPDIR/note.md" --capture-id file-1
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
assert_eq "$(rget file-1 '.input_path')" "$TEST_TMPDIR/note.md" "--file origin: input locator is the path as given"
assert_eq "$(rget file-1 '.raw_published')" "true" "...the raw is published (the crash came after the source was staged)"
printf 'a changed file\n' > "$TEST_TMPDIR/note.md"
run_gw --capture-id file-1
assert_eq "$rc" "0" "a changed original after the raw was published: the replay finishes from the retained raw"
assert_eq "$(rget file-1 '.state')" "extracted" "...extracted"
assert_contains "$(cat "$VAULT/Sources/$(rget file-1 '.revision')/source.md")" "a markdown file" "...with the retained raw's bytes as the body, not the changed file's"
rm -f "$XDG_STATE_HOME/mnemon/fence-probes.json"
printf 'a second file\n' > "$TEST_TMPDIR/note2.md"
run_gw --file "$TEST_TMPDIR/note2.md" --capture-id file-2
assert_eq "$rc" "3" "fixture: a --file capture refused before acquisition (no probe record) retains nothing"
write_probe_record
assert_eq "$(rget file-2 '.raw_sha256')" "null" "...no raw recorded"
printf 'changed before anything was retained\n' > "$TEST_TMPDIR/note2.md"
run_gw --capture-id file-2
assert_eq "$rc" "1" "...the replay consults the locator: a changed input → exit 1"
assert_eq "$(result error_class)" "input-missing" "...input-missing"
assert_eq "$(rget file-2 '.state')" "failed" "...receipt failed"
# failed/environment (exit 3 before publication): the replay resumes from the retained checkpoint —
# a text origin's capture-text in attempt-1 is published without re-reading stdin (§12 A3)
rm -f "$XDG_STATE_HOME/mnemon/fence-probes.json"
printf 'retained text\n' > "$TEST_TMPDIR/env.txt"
run_gw_stdin "$TEST_TMPDIR/env.txt" --origin text --capture-id env-text
assert_eq "$rc" "3" "a text capture with no probe record → exit 3 before publication"
assert_eq "$(rget env-text '.error_class')" "fence-unprobed" "...receipt failed/fence-unprobed"
assert_file_exists "$VSTATE/runs/env-text/attempt-1/capture.md" "...the capture-text is retained in attempt-1"
write_probe_record
run_gw --capture-id env-text
assert_eq "$rc" "0" "...the replay publishes from the retained capture-text (stdin is /dev/null here)"
assert_contains "$(cat "$VAULT/Sources/$(rget env-text '.revision')/source.md")" "retained text" "...with the retained bytes"
assert_eq "$(rget env-text '.attempts')" "2" "...attempts counted"

# unknown --capture-id for a captured resource → skipped-duplicate + alias receipt
src_before="$(shasum -a 256 "$VAULT/Sources/$rev0/source.md")"
stub_reset
run_gw --url "https://example.com/recovery" --capture-id alias-1
assert_eq "$rc" "0" "unknown id for a captured resource → exit 0"
assert_eq "$(result status)" "skipped-duplicate" "...skipped-duplicate"
assert_eq "$(result capture_id)" "alias-1" "...RESULT:capture_id = the requested id"
assert_eq "$(result path)" "Sources/$rev0/" "...path = the existing folder"
assert_eq "$(result extract)" "ok" "...extract=ok"
assert_eq "$(rget alias-1 '.resolved_to_revision')" "$rev0" "...alias receipt resolved_to_revision"
assert_eq "$(rget alias-1 '.resolved_to_capture_id')" "rec-0" "...resolved_to_capture_id"
assert_eq "$(shasum -a 256 "$VAULT/Sources/$rev0/source.md")" "$src_before" "...source.md unchanged"
assert_eq "$(stub_calls)" "0" "...no model call"
run_gw --capture-id alias-1
assert_eq "$rc" "0" "replay of the alias → exit 0"
assert_eq "$(result status)" "skipped-duplicate" "...reports from the files of its target"
assert_eq "$(result replayed)" "true" "...replayed"
assert_eq "$(result revision)" "$rev0" "...target revision"

# incomplete latest revision + the same resource (no id) → resumed, never a new folder
stub_reset
STUB_MODE=exit1 run_gw --url "https://example.com/incomplete" --capture-id inc-1
assert_eq "$rc" "4" "an extraction failure leaves an incomplete revision"
revi="$(result revision)"
n1="$(nfolders)"
stub_reset
run_gw --url "https://example.com/incomplete"
assert_eq "$rc" "0" "same resource without an id → resumed"
assert_eq "$(nfolders)" "$n1" "...no new folder"
assert_eq "$(result revision)" "$revi" "...the incomplete revision"
assert_eq "$(result capture_id)" "inc-1" "...under the receipt that published it"
assert_eq "$(rget inc-1 '.state')" "extracted" "...which is now extracted"
# an alias that resumes: unknown id + incomplete revision
stub_reset
STUB_MODE=exit1 run_gw --url "https://example.com/incomplete2" --capture-id inc-2
revi2="$(result revision)"
stub_reset
run_gw --url "https://example.com/incomplete2" --capture-id inc-2-alias
assert_eq "$rc" "0" "alias of an incomplete revision resumes it"
assert_eq "$(result capture_id)" "inc-2-alias" "...under the alias"
assert_eq "$(result revision)" "$revi2" "...same folder"
assert_eq "$(rget inc-2-alias '.attempts')" "1" "...with the alias's own attempt accounting"
assert_eq "$(rget inc-2-alias '.resolved_to_capture_id')" "inc-2" "...resolved to the original operation"
assert_eq "$(rget inc-2-alias '.state')" "extracted" "...the alias records the outcome"
# §12 A4: the alias delegated the recovery to the owner receipt, which is the one updated
assert_eq "$(rget inc-2 '.state')" "extracted" "...the owner receipt is updated through the delegation (A4)"
assert_eq "$(rget inc-2 '.attempts')" "2" "...under the owner's attempt accounting (attempt 2)"
assert_eq "$(_mnemon_yaml_literal capture_id "$VAULT/Sources/$revi2/extract.md")" "inc-2" "...extract.md carries the owner's capture_id"
assert_eq "$(ls -d "$VSTATE/runs/inc-2-alias" 2>/dev/null)" "" "...the alias has no attempt dirs of its own"
stub_reset
run_gw --capture-id inc-2
assert_eq "$rc" "0" "a replay of the owner → exit 0"
assert_eq "$(result status)" "extracted" "...extracted from the files"
assert_eq "$(stub_calls)" "0" "...without a second paid extraction"
run_gw --capture-id inc-2-alias
assert_eq "$(result status)" "extracted" "a replay of the alias reports extracted from the files"
assert_eq "$(result capture_id)" "inc-2-alias" "...under the alias id"
assert_eq "$(stub_calls)" "0" "...no model call either"
# an alias that fails its delegated extraction: both receipts record it, the owner keeps the state
stub_reset
STUB_MODE=exit1 run_gw --url "https://example.com/incomplete3" --capture-id inc-3
revi3="$(result revision)"
stub_reset
STUB_MODE=exit1 run_gw --url "https://example.com/incomplete3" --capture-id inc-3-alias
assert_eq "$rc" "4" "an alias whose delegated extraction fails → exit 4"
assert_eq "$(result capture_id)" "inc-3-alias" "...reported under the alias"
assert_eq "$(rget inc-3-alias '.state') $(rget inc-3-alias '.attempts') $(rget inc-3-alias '.error_class')" "extract-failed 1 claude-exit" "...the alias: extract-failed, one attempt, the class"
assert_eq "$(rget inc-3 '.state') $(rget inc-3 '.attempts')" "extract-failed 2" "...the owner: extract-failed, attempt 2"
stub_reset
run_gw --resume "$revi3"
assert_eq "$rc" "0" "--resume <revision> finishes it under the owner"
assert_eq "$(result capture_id)" "inc-3" "...reporting the owner id"
assert_eq "$(rget inc-3 '.state')" "extracted" "...owner extracted"

# --recapture → -2 with supersedes; --supersedes across resources; replayed id never a second revision
run_gw --url "https://example.com/recovery" --recapture --capture-id rec-0b
assert_eq "$rc" "0" "--recapture exits 0"
assert_eq "$(result revision)" "$rev0-2" "...new -2 revision"
assert_eq "$(fm "$VAULT/Sources/$rev0-2/source.md" supersedes)" "\"$rev0\"" "...supersedes the latest revision"
nf="$(nfolders)"
run_gw --url "https://example.com/recovery" --recapture --capture-id rec-0b
assert_eq "$(nfolders)" "$nf" "--recapture with a replayed id → no second revision"
assert_eq "$(result replayed)" "true" "...the receipt wins (replayed)"
assert_eq "$(result revision)" "$rev0-2" "...same revision"
run_gw --url "https://example.com/cross" --supersedes "$rev" --capture-id cross-1
assert_eq "$rc" "0" "--supersedes naming a revision of another resource is accepted"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" "\"$rev\"" "...and recorded"
run_gw --url "https://example.com/cross2" --supersedes "2020-01-01_deadbeef" --capture-id cross-2
assert_eq "$rc" "2" "--supersedes of a missing revision → exit 2"

# hash8 collision: two resources sharing the folder label
read -r colA colB <<< "$(python3 - <<'PY'
import hashlib
seen = {}
n = 0
while True:
    u = f"https://example.com/c/{n}"
    h = hashlib.sha256(u.encode()).hexdigest()[:8]
    if h in seen:
        print(seen[h], u); break
    seen[h] = u; n += 1
PY
)"
run_gw --url "$colA" --capture-id col-a
revA="$(result revision)"
run_gw --url "$colB" --capture-id col-b
revB="$(result revision)"
assert_eq "$revB" "$revA-2" "hash8 collision → _<label> and _<label>-2"
run_gw --url "$colA"
assert_eq "$(result revision)" "$revA" "...each resource matched by its own resource_sha256 (A)"
run_gw --url "$colB"
assert_eq "$(result revision)" "$revB" "...(B)"

# numeric suffix order: -10 is the latest, after -2
rid10="$(printf '%s' "https://example.com/ten" | shasum -a 256 | cut -c1-8)"
rsha10="$(printf '%s' "url:https://example.com/ten" | shasum -a 256 | cut -c1-64)"
for s in "" -2 -9 -10; do
  d="$VAULT/Sources/2026-10-01_$rid10$s"; mkdir -p "$d"
  printf -- '---\ntype: source\norigin: url\nurl: "https://example.com/ten"\ncapture_id: "ten%s"\nresource_sha256: "%s"\nrevision: "2026-10-01_%s%s"\n---\nbody\n' "$s" "$rsha10" "$rid10" "$s" > "$d/source.md"
  printf -- '---\ntype: extract\ntitle: "Ten%s"\nstatus: extracted\n---\n### Summary\n' "$s" > "$d/extract.md"
done
run_gw --url "https://example.com/ten"
assert_eq "$(result revision)" "2026-10-01_$rid10-10" "numeric suffix order: -10 is the latest revision"

# a revision reserved by a non-final receipt is not allocated again (also by a second config)
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-source-staged
run_gw --url "https://example.com/reserve" --capture-id res-1
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
resv="$(rget res-1 '.revision')"
run_gw --url "https://example.com/reserve" --recapture --capture-id res-2
assert_eq "$(result revision)" "$resv-2" "a name reserved by a started receipt is skipped by the next allocation"
sed 's/^instance: captest/instance: captest-b/' "$CONFIG" > "$TEST_TMPDIR/second.yaml"
"$GW" source-add --config "$TEST_TMPDIR/second.yaml" --url "https://example.com/reserve" --recapture --capture-id res-3 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || true
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$(result revision)" "$resv-3" "...also by a second config pointing at the same vault (shared ledger)"
assert_file_exists "$(receipt res-3)" "...whose receipt lands in the same ledger"

# integrity: missing source.md / edited body on every path
mv "$VAULT/Sources/$rev5/source.md" "$TEST_TMPDIR/rev5-source.md"
run_gw --capture-id rec-5
assert_eq "$rc" "1" "missing source.md → exit 1"
assert_eq "$(result status)" "integrity-error" "...integrity-error"
assert_eq "$(result error_class)" "evidence-missing" "...evidence-missing"
assert_eq "$(result revision)" "$rev5" "...revision reported"
assert_eq "$(result retryable)" "false" "...retryable=false"
assert_eq "$(result source_sha256)" "" "...source_sha256 empty (the source could not be read, A3)"
assert_eq "$(rget rec-5 '.state') $(rget rec-5 '.error_class')" "integrity-error evidence-missing" "...the receipt is stored as integrity-error (A3)"
mv "$TEST_TMPDIR/rev5-source.md" "$VAULT/Sources/$rev5/source.md"
run_gw --capture-id rec-5
assert_eq "$rc" "0" "the file restored: the integrity-error receipt re-verifies and the replay goes on"
assert_eq "$(result status)" "extracted" "...extracted from the files"
assert_eq "$(rget rec-5 '.state')" "extracted" "...the receipt leaves integrity-error"
printf 'edited\n' >> "$VAULT/Sources/$rev5/source.md"
run_gw --capture-id rec-5
assert_eq "$rc" "1" "edited body on an extracted replay → exit 1"
assert_eq "$(result error_class)" "evidence-mismatch" "...evidence-mismatch"
assert_eq "$(result source_sha256)" "$(body_sha "$VAULT/Sources/$rev5/source.md")" "...source_sha256 = the recomputed hash of the edited body (A3)"
STUB_MODE=exit1 run_gw --url "https://example.com/edit2" --capture-id edit-2
reve="$(result revision)"
printf 'edited\n' >> "$VAULT/Sources/$reve/source.md"
run_gw --capture-id edit-2
assert_eq "$rc" "1" "edited body on a resume path → exit 1"
assert_eq "$(result error_class)" "evidence-mismatch" "...evidence-mismatch"
assert_eq "$(rget edit-2 '.state')" "integrity-error" "...the receipt is stored as integrity-error"
run_gw --url "https://example.com/edit2" --capture-id edit-2-alias
assert_eq "$rc" "1" "edited body behind an alias resume → exit 1"
assert_eq "$(result error_class)" "evidence-mismatch" "...evidence-mismatch"
assert_eq "$(rget edit-2-alias '.state')" "integrity-error" "...the alias records it too"
python3 - "$VAULT/Sources/$reve/source.md" <<'PY'
import sys
p = sys.argv[1]; d = open(p, "rb").read()
assert d.endswith(b"edited\n"); open(p, "wb").write(d[:-len(b"edited\n")])
PY
stub_reset
run_gw --capture-id edit-2
assert_eq "$rc" "0" "the body restored: the resume finishes the extraction"
assert_eq "$(rget edit-2 '.state')" "extracted" "...owner extracted"
assert_eq "$(stub_calls)" "1" "...with one extraction"

# ---------------------------------------------------------------------------------------------
echo "--- 8. url chain ---"
src0="$VAULT/Sources/$rev0/source.md"
assert_contains "$(cat "$src0")" "from defuddle" "stub curl html → defuddle output is the body"
assert_contains "$(cat "$src0")" '  method: "curl+defuddle"' "...capture.method curl+defuddle"
assert_contains "$(cat "$src0")" 'title: "Stub & Page Title"' "an acquired <title> with a newline is normalized, not rejected"
assert_contains "$(cat "$src0")" '  fidelity: converted' "...fidelity converted"
assert_contains "$(cat "$src0")" '  raw_kind: response' "...raw_kind response"
assert_contains "$(cat "$src0")" "archive: \"$rev0.html\"" "...archive: name = revision"
assert_eq "$(grep '^  raw_sha256:' "$src0" | tr -d '" ' | cut -d: -f2)" "$(shasum -a 256 "$ARCHIVE/$rev0.html" | cut -c1-64)" "raw <revision>.html with matching raw_sha256 in archive_dir"
STUB_DEFUDDLE=fail run_gw --url "https://example.com/pandoc" --capture-id pan-1
assert_eq "$rc" "0" "defuddle failing → pandoc"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  method: "curl+pandoc"' "...method curl+pandoc"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" "World **bold** from curl" "...pandoc body"
STUB_CURL=pdf run_gw --url "https://example.com/report" --capture-id pdf-url
assert_eq "$rc" "0" "a URL answering application/pdf without .pdf → the pdf row"
assert_file_exists "$ARCHIVE/$(result revision).pdf" "...raw stored as .pdf"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  method: "pdftotext"' "...converted with pdftotext"
# depth-aware composite: the composite body is the raw (.txt, composite) and the capture-text;
# the manifest's allowlisted keys are merged, everything else is not
STUB_DEPTH=fold run_gw --url "https://example.com/thin" --capture-id depth-1
assert_eq "$rc" "0" "depth composite capture exits 0"
srcd="$VAULT/Sources/$(result revision)/source.md"
assert_contains "$(cat "$srcd")" '  raw_kind: composite' "...raw_kind composite"
assert_contains "$(cat "$srcd")" '  method: "depth-composite"' "...method depth-composite"
assert_contains "$(cat "$srcd")" 'posted_url: "https://example.com/thin"' "...allowlisted manifest key merged"
assert_contains "$(cat "$srcd")" '  - url: https://example.com/deeper' "...folded_sources block merged"
assert_contains "$(cat "$srcd")" 'fetch_status: ok' "...fetch_status merged"
assert_not_contains "$(cat "$srcd")" 'secret_key' "...a key outside the allowlist is dropped"
assert_contains "$(cat "$srcd")" 'folded body' "...the composite body is the capture-text"
assert_file_exists "$ARCHIVE/$(result revision).txt" "...the composite is retained as <revision>.txt"


# archive_dir unset → raw in <vault state>/raw/, raw_store: state
"$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" --url "https://example.com/state-raw" --capture-id state-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || true
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$(result status)" "extracted" "archive_dir unset: capture still works"
assert_file_exists "$VSTATE/raw/$(result revision).html" "...raw kept in <vault state>/raw/"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  raw_store: state' "...raw_store: state"
assert_not_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" 'archive:' "...no archive: field"

# ---------------------------------------------------------------------------------------------
echo "--- 9. raw immutability and reuse ---"
assert_file_exists "$ARCHIVE/$rev0.html" "two revisions on one day: <revision>.html"
assert_file_exists "$ARCHIVE/$rev0-2.html" "...and <revision>-2.html"
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-raw-staged
run_gw --url "https://example.com/foreign" --capture-id foreign-1
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
rm -f "$(rget foreign-1 '.raw_staged_path')"
printf 'someone else wrote this\n' > "$(rget foreign-1 '.raw_path')"
run_gw --capture-id foreign-1
assert_eq "$rc" "1" "a file at the reserved name that no receipt accounts for → exit 1"
assert_eq "$(result error_class)" "archive-exists" "...archive-exists"
assert_eq "$(result retryable)" "false" "...operator, not retried"
run_gw --capture-id foreign-1
assert_eq "$(result error_class)" "archive-exists" "...and again on the next replay"

# ---------------------------------------------------------------------------------------------
echo "--- 10. pdf and files ---"
printf '%%PDF-1.7 stub\n' > "$TEST_TMPDIR/paper.pdf"
# (--source-type article: the good fixture carries the article template's Body headings)
run_gw --file "$TEST_TMPDIR/paper.pdf" --capture-id pdf-1 --source-type article
assert_eq "$rc" "0" "pdf --file exits 0"
revp="$(result revision)"
assert_contains "$(cat "$VAULT/Sources/$revp/source.md")" "PDF text page one" "body = pdftotext output"
assert_contains "$(cat "$VAULT/Sources/$revp/source.md")" 'title: "Stub Paper Title"' "title from pdfinfo (normalized)"
assert_contains "$(cat "$VAULT/Sources/$revp/source.md")" 'pages: 12' "pages from pdfinfo"
assert_contains "$(cat "$VAULT/Sources/$revp/source.md")" "origin_path: \"$TEST_TMPDIR/paper.pdf\"" "origin_path kept"
n2="$(nfolders)"
PATH="$TEST_TMPDIR/nopdf:/usr/bin:/bin" bash -c 'mkdir -p "$1"; for t in claude curl defuddle pdfinfo render-url.sh media-extract.py; do ln -sf "$2/$t" "$1/$t"; done' _ "$TEST_TMPDIR/nopdf" "$STUBS"
rc=0
PATH="$TEST_TMPDIR/nopdf:/usr/bin:/bin" "$GW" source-add --config "$CONFIG" --file "$TEST_TMPDIR/paper.pdf" --recapture --capture-id pdf-nopoppler --source-type article >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "3" "pdftotext absent → exit 3"
assert_eq "$(nfolders)" "$n2" "...nothing published"
assert_eq "$(rget pdf-nopoppler '.state')" "failed" "...receipt failed"
assert_eq "$(rget pdf-nopoppler '.retryable')" "true" "...retryable"
STUB_PDFTOTEXT=empty run_gw --file "$TEST_TMPDIR/paper.pdf" --recapture --capture-id pdf-empty --source-type article
assert_eq "$rc" "1" "empty pdf text → exit 1"
assert_eq "$(result error_class)" "no-text" "...no-text"
assert_file_exists "$ARCHIVE/$(rget pdf-empty '.revision').pdf" "...raw retained"
# a resumed capture keeps the published source's content format in extract.md
stub_reset
STUB_MODE=exit1 run_gw --file "$TEST_TMPDIR/paper.pdf" --recapture --capture-id pdf-resume --source-type article
assert_eq "$rc" "4" "fixture: a pdf capture whose extraction failed"
run_gw --resume "$(rget pdf-resume '.revision')"
assert_eq "$rc" "0" "--resume of the pdf capture exits 0"
assert_contains "$(cat "$VAULT/Sources/$(rget pdf-resume '.revision')/extract.md")" 'content_format: pdf' "...extract.md carries content_format pdf (read from the published source, not defaulted to text)"
# a local book is a file resource: identity from its bytes, never one empty url key for every book
printf 'book one\n' > "$TEST_TMPDIR/b1.epub"
printf 'book two\n' > "$TEST_TMPDIR/b2.epub"
run_gw --origin book --file "$TEST_TMPDIR/b1.epub" --capture-id book-1
assert_eq "$(rget book-1 '.request.resource_kind')" "file" "--origin book --file: resource_kind file"
assert_eq "$(rget book-1 '.request.input_sha256')" "$(shasum -a 256 "$TEST_TMPDIR/b1.epub" | cut -c1-64)" "...input_sha256 = the book's bytes"
run_gw --origin book --file "$TEST_TMPDIR/b2.epub" --capture-id book-2
[[ "$(rget book-1 '.resource_sha256')" != "$(rget book-2 '.resource_sha256')" ]] && pass "...two books are two resources" || fail "two books are two resources" "same resource_sha256"
assert_eq "$(result status)" "failed" "...the second book is not a duplicate of the first (exit 1 unsupported from the stub-less pandoc, never skipped-duplicate)"
printf 'binary\000stuff' > "$TEST_TMPDIR/weird.xyz"
run_gw --file "$TEST_TMPDIR/weird.xyz" --capture-id file-xyz
assert_eq "$rc" "1" "unsupported file type → exit 1"
assert_eq "$(result error_class)" "unsupported" "...unsupported"
assert_file_exists "$ARCHIVE/$(rget file-xyz '.revision').xyz" "...raw retained"
# the extension comes from the basename: a dot in a directory name is not one (an extensionless
# file is .bin and unsupported; the same bytes as .md convert)
mkdir -p "$TEST_TMPDIR/sub.dir"
printf 'notes without an extension\n' > "$TEST_TMPDIR/sub.dir/notes"
run_gw --file "$TEST_TMPDIR/sub.dir/notes" --capture-id ext-1
assert_eq "$rc" "1" "an extensionless --file under a dotted directory → exit 1 unsupported (not a path error)"
assert_eq "$(result error_class)" "unsupported" "...unsupported"
assert_file_exists "$ARCHIVE/$(rget ext-1 '.revision').bin" "...raw retained as <revision>.bin"
assert_eq "$(rget ext-1 '.raw_published')" "true" "...and the receipt's raw_published is honest"
assert_eq "$(staging_left)" "0" "...no staging left"
cp "$TEST_TMPDIR/sub.dir/notes" "$TEST_TMPDIR/sub.dir/notes.md"
run_gw --file "$TEST_TMPDIR/sub.dir/notes.md" --capture-id ext-2
assert_eq "$rc" "0" "the same bytes as .md under the dotted directory → extracted"
assert_file_exists "$ARCHIVE/$(result revision).md" "...raw retained as <revision>.md"
# audio by URL (§12 A7): the gateway downloads the file (one curl call), publishes it as the raw,
# then media-extract.py transcribes the PUBLISHED raw
stub_reset
c1="$(curl_calls)"
STUB_CURL=audio run_gw --origin audio --url "https://example.com/ep.mp3?dl=1" --capture-id audio-1 --keep-run
assert_eq "$rc" "0" "audio by URL exits 0"
reva="$(result revision)"
assert_eq "$(curl_calls)" "$((c1 + 1))" "...the gateway downloaded the audio itself (one curl call)"
assert_file_exists "$ARCHIVE/$reva.mp3" "...published as <revision>.mp3 (the extension from the URL path)"
assert_eq "$(cat "$ARCHIVE/$reva.mp3")" "RIFFstubaudio" "...with the downloaded bytes"
assert_eq "$(cat "$STUB_DIR/media-calls")" "audio url= file=$ARCHIVE/$reva.mp3" "...and media-extract.py transcribed the published raw (audio --file <raw>), never a URL"
assert_contains "$(cat "$VAULT/Sources/$reva/source.md")" '  method: "whisper:tiny"' "...method from media.json"
assert_contains "$(cat "$VAULT/Sources/$reva/source.md")" '  raw_kind: file' "...raw_kind file"
assert_contains "$(cat "$VAULT/Sources/$reva/source.md")" 'transcript line one' "...the transcript is the body"
# a crash after the download: the retry transcribes the recorded raw without refetching
stub_reset
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-raw-published
STUB_CURL=audio run_gw --origin audio --url "https://example.com/ep2.mp3" --capture-id audio-crash
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
assert_eq "$(rget audio-crash '.raw_published') $(rget audio-crash '.raw_kind')" "true file" "crash after the audio download: the raw is recorded and published"
[[ -f "$STUB_DIR/media-calls" ]] && fail "no transcription before the crash" "ran" || pass "...and nothing was transcribed yet"
c1="$(curl_calls)"
run_gw --capture-id audio-crash
assert_eq "$rc" "0" "the retry exits 0"
assert_eq "$(curl_calls)" "$c1" "...without refetching (curl not called)"
assert_eq "$(cat "$STUB_DIR/media-calls")" "audio url= file=$ARCHIVE/$(rget audio-crash '.revision').mp3" "...transcribing the recorded raw"
assert_contains "$(cat "$VAULT/Sources/$(rget audio-crash '.revision')/source.md")" '  method: "whisper:tiny"' "...method whisper"
# audio by --file: a copy of the given file is published first, the published copy is transcribed
stub_reset
printf 'RIFFlocalaudio' > "$TEST_TMPDIR/talk.M4A"
run_gw --origin audio --file "$TEST_TMPDIR/talk.M4A" --capture-id audio-file
assert_eq "$rc" "0" "audio by --file exits 0"
revf2="$(result revision)"
assert_file_exists "$ARCHIVE/$revf2.m4a" "...published as <revision>.m4a (lowercased extension)"
assert_eq "$(cat "$STUB_DIR/media-calls")" "audio url= file=$ARCHIVE/$revf2.m4a" "...media-extract.py transcribed the published copy, not the original"
# youtube: captions → the transcript is the raw; the whisper fallback publishes the downloaded audio as the raw
stub_reset
run_gw --url "https://youtube.com/watch?v=abc123xyz00" --capture-id yt-1
assert_eq "$rc" "0" "youtube exits 0"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  method: "captions:en:manual"' "youtube records the method from media.json"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" '  raw_kind: transcript' "...captions: the transcript is the raw"
assert_file_exists "$ARCHIVE/$(result revision).txt" "...published as <revision>.txt"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" 'video_title: "Stub Video"' "...allowlisted metadata merged"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" 'transcript line one' "...transcript is the body"
stub_reset
STUB_YT=whisper run_gw --url "https://youtube.com/watch?v=whisper00000" --capture-id yt-w
assert_eq "$rc" "0" "youtube with the whisper fallback exits 0"
revw="$(result revision)"
assert_contains "$(cat "$VAULT/Sources/$revw/source.md")" '  raw_kind: file' "...the downloaded audio is the raw (raw_kind file, A7)"
assert_file_exists "$ARCHIVE/$revw.wav" "...published as <revision>.wav"
assert_eq "$(cat "$ARCHIVE/$revw.wav")" "RIFFstubytaudio" "...with the audio bytes media.json's audio_path named"
assert_contains "$(cat "$VAULT/Sources/$revw/source.md")" '  method: "whisper:tiny"' "...method whisper"
# a whisper youtube capture replayed from its retained audio re-transcribes it, never refetches
stub_reset
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-raw-published
STUB_YT=whisper run_gw --url "https://youtube.com/watch?v=whisper00001" --capture-id yt-w2
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
stub_reset
run_gw --capture-id yt-w2
assert_eq "$rc" "0" "a youtube whisper capture crashed after its raw publication replays"
assert_eq "$(cat "$STUB_DIR/media-calls")" "audio url= file=$ARCHIVE/$(rget yt-w2 '.revision').wav" "...by transcribing the retained audio (no youtube run)"

# ---------------------------------------------------------------------------------------------
echo "--- 15. flat parsers on the new format ---"
assert_eq "$(_mnemon_yaml_literal capture_id "$src0")" "rec-0" "_mnemon_yaml_literal reads a top-level key of source.md"
assert_eq "$(_mnemon_yaml_literal method "$src0")" "" "...the nested capture: block does not leak a top-level key"
assert_eq "$(_mnemon_yaml_literal rating "$ex")" "6" "...reads rating from extract.md"
sout="$(python3 "$MNEMON_ROOT/bin/mnemon-search.py" --vault "$VAULT" --query "recovery" --include-source)"
assert_eq "$(printf '%s' "$sout" | jq -r '.results[0].title')" "Plain T" "mnemon-search reads the new extract.md (title)"
assert_eq "$(printf '%s' "$sout" | jq -r '.results[0].rating')" "6" "...rating from the real field"
assert_eq "$(printf '%s' "$sout" | jq -r '.results[0].domains[0]')" "learning" "...unquoted flow list parsed"
# the three titles round-trip through the publisher, _mnemon_yaml_literal, the reindexer and search
i=0
for t in '~' '~/Guide' 'a "quoted" \ title' 'close ]] me | pipe'; do
  i=$((i + 1))
  jq --arg t "$t" '.title=$t' "$FIX/extract-good.json" > "$TEST_TMPDIR/fx-$i.json"
  STUB_FIXTURE="$TEST_TMPDIR/fx-$i.json" run_gw --url "https://example.com/title/$i" --capture-id title-$i --title "$t"
  revt="$(result revision)"
  assert_eq "$(_mnemon_yaml_literal title "$VAULT/Sources/$revt/extract.md")" "$t" "title round-trips through extract-publish + _mnemon_yaml_literal: $t"
  assert_eq "$(_mnemon_yaml_literal title "$VAULT/Sources/$revt/source.md")" "$t" "...and through source.md"
  assert_eq "$(result title)" "$t" "...RESULT:title"
  st="$(python3 "$MNEMON_ROOT/bin/mnemon-search.py" --vault "$VAULT" --query defuddle --include-source --limit 1000 | jq -r --arg p "Sources/$revt/" '.results[] | select(.path == $p) | .title')"
  assert_eq "$st" "$t" "...mnemon-search decodes it"
done
python3 "$MNEMON_ROOT/bin/knowledge-reindex.py" "$VAULT" >/dev/null
line="$(grep -F "Sources/$revt/extract|" "$VAULT/index.md")"
assert_contains "$line" "[[Sources/$revt/extract|close ] ] me / pipe]]" "a title with ]] and | renders a safe index line"
python3 - "$line" <<'PY' && pass "...the line stays one valid wikilink entry (no ]] and no | inside the display text)" || fail "valid index line" "$line"
import re, sys
m = re.match(r"^- \[\[Sources/([\w-]+)/extract\|(.*)\]\] \(", sys.argv[1])
sys.exit(0 if m and "]]" not in m.group(2) and "|" not in m.group(2) else 1)
PY
assert_contains "$(grep -F "Sources/$(rget title-1 '.revision')/extract|" "$VAULT/index.md")" "|~]]" "a title ~ is kept literally in the index"
rl="$(python3 - "$MNEMON_ROOT/bin/knowledge-reindex.py" "$VAULT/Sources/$(rget title-3 '.revision')/extract.md" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("kr", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.parse_field(open(sys.argv[2], encoding="utf-8").read(), "title"))
PY
)"
assert_eq "$rl" 'a "quoted" \ title' "knowledge-reindex parse_field decodes exactly \\\" and \\\\"

cleanup_capture_test
summary
