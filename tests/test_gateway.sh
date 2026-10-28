#!/usr/bin/env bash
# test_gateway.sh — the gateway's CLI surface: dry-run plans, origin and source-type detection,
# batch, status, the D11 refusal, and the shared contract fixtures (tests/fixtures/contracts/):
# every argv of expected-argv.json is accepted and the RESULT: shapes of gateway-outcomes.json are
# what the gateway emits. Deeper contract behaviour lives in test_capture.sh / test_fence.sh /
# test_lock.sh. Stubs from tests/fixtures/capture/setup.sh; no network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Gateway Tests ==="

# --- Test 1: Dry-run produces the plan, the extractor argv and the prompt ---
run_gw --url "https://example.com/test" --dry-run
assert_eq "$rc" "0" "dry-run exits 0"
assert_contains "$out" "DRY RUN" "dry-run flag works"
assert_contains "$out" "Origin: url" "origin in the plan and prompt"
assert_contains "$out" "URL: https://example.com/test" "URL in prompt"
assert_contains "$out" "READER CONTEXT" "reader context section present"
assert_contains "$out" "EXTRACTION TEMPLATE" "extraction template section present"
assert_contains "$out" "Test reader context" "reader context content embedded"
assert_contains "$out" "=== CONTENT ===" "content section present (inline at run time)"
assert_contains "$out" "=== OUTPUT ===" "output section present"
# --- Test 1b: Rating rubric is NOT duplicated in the gateway prompt (owned by reader-context) ---
assert_not_contains "$out" "5 = average, 7 = good, 9+ = exceptional" "gateway no longer hardcodes rating scale"
assert_contains "$out" "value-scale defined in the READER CONTEXT" "gateway points rating to reader-context"
# --- Test 1c: Framing instruction references staleness ---
assert_contains "$out" "framing context may be stale" "gateway instructs staleness check"
# --- Test 1d: the prompt no longer asks the model to write files, hash or print RESULT lines ---
assert_not_contains "$out" "RESULT:path=" "prompt has no RESULT instructions"
assert_not_contains "$out" "shasum" "prompt has no hashing instruction"
assert_not_contains "$out" "WebFetch" "prompt has no fetch instruction"
assert_contains "$out" "=== EXTRACTOR ARGV ===" "dry-run prints the extractor argv"
assert_contains "$out" "--restricted" "...with the fence"

# --- Test 1e: the depth mode is in the plan (GH #12) ---
assert_contains "$out" "Depth: auto (decided after acquisition" "the dry-run shows the default depth mode next to Fetch:"
run_gw --url "https://example.com/test" --depth force --dry-run
assert_contains "$out" "Depth: force" "--depth force is shown"
run_gw --url "https://example.com/test" --depth off --dry-run
assert_contains "$out" "Depth: off" "--depth off is shown"
run_gw --url "https://arxiv.org/abs/2401.12345" --depth force --dry-run
assert_contains "$out" "Depth: off (" "an arXiv capture says depth is off whatever the flag"

# --- Test 2: Auto-detection of YouTube URLs ---
run_gw --url "https://youtube.com/watch?v=abc123" --dry-run
assert_contains "$out" "Origin: youtube" "YouTube URL auto-detected"
run_gw --url "https://youtu.be/abc123" --dry-run
assert_contains "$out" "Origin: youtube" "youtu.be URL auto-detected"

# --- Test 3: Regular URL defaults to origin=url ---
run_gw --url "https://example.com/article" --dry-run
assert_contains "$out" "Origin: url" "regular URL → origin=url"

# --- Test 4: Source type auto-detection ---
run_gw --url "https://example.com/post" --dry-run
assert_contains "$out" "Source type: article" "URL → source_type=article"

# --- Test 4b: Article template no longer carries its own rating scale ---
assert_not_contains "$out" "obvious or wrong. 4-5 = average" "template rating guide removed (article)"
# --- Test 4c: templates carry a Fields section, not frontmatter the model writes ---
assert_contains "$out" "## Fields" "template Fields section embedded"
assert_not_contains "$out" "## Frontmatter" "template Frontmatter section gone"

# --- Test 5: Missing origin fails ---
run_gw --dry-run
[[ "$rc" -ne 0 ]] && pass "missing origin returns error" || fail "missing origin" "should have failed"

# --- Test 6: Status action ---
mkdir -p "$VAULT/Sources/2026-04-04_abc12345"
cat > "$VAULT/Sources/2026-04-04_abc12345/extract.md" << 'EXTRACT'
---
status: extracted
title: "Test Article"
---
EXTRACT
output=$("$GW" status --config "$CONFIG" 2>&1)
assert_contains "$output" "Total sources: 1" "status counts sources"
assert_contains "$output" "Test Article" "status shows titles"
rm -rf "$VAULT/Sources/2026-04-04_abc12345"

# --- Test 7: ref:vault dry-run ---
REF_FILE="$TEST_TMPDIR/test-note.md"
printf '# Test Note\nSome vault content here\n' > "$REF_FILE"
run_gw --ref-path "$REF_FILE" --dry-run
assert_contains "$out" "DRY RUN" "ref:vault dry-run works"
assert_contains "$out" "Origin: ref:vault" "ref:vault origin in prompt"
assert_contains "$out" "Ref path:" "ref_path in prompt"
assert_contains "$out" "Some vault content here" "ref:vault content is inlined in the CONTENT section"

# --- Test 8: ref:vault auto-detect from --ref-path ---
assert_contains "$out" "Origin: ref:vault" "auto-detect ref:vault from --ref-path"

# --- Test 9: ref:vault defaults source_type to document ---
assert_contains "$out" "Source type: document" "ref:vault → source_type=document"

# --- Test 10: ref:vault / --file with a missing file: exit 1 with a RESULT block (never a bare exit) ---
run_gw --ref-path "/nonexistent/file.md"
assert_eq "$rc" "1" "ref:vault missing file → exit 1"
assert_eq "$(result status) $(result error_class) $(result retryable)" "failed input-missing false" "...with RESULT lines a feeder reads as failed/input-missing (permanent)"
run_gw --file "/nonexistent/paper.pdf" --capture-id gone-1
assert_eq "$rc" "1" "a missing --file → exit 1"
assert_eq "$(result status) $(result error_class) $(result capture_id)" "failed input-missing gone-1" "...RESULT failed/input-missing under the requested id"
[[ -f "$(receipt gone-1)" ]] && fail "no receipt for a missing --file" "exists" || pass "...no receipt (refused before the lock)"

# --- Test 11–13: ref:mcp is refused in phase 1 (D11) ---
run_gw --ref-source notion --ref-id "page-123" --dry-run
assert_eq "$rc" "2" "ref:mcp → exit 2 (D11)"
assert_contains "$err" "inbox" "ref:mcp refusal points to inbox producers"
run_gw --ref-source granola --dry-run
assert_eq "$rc" "2" "auto-detect ref:mcp from --ref-source → exit 2"
run_gw --origin ref:mcp --ref-source notion
assert_eq "$rc" "2" "explicit --origin ref:mcp → exit 2"

# --- Test 14: batch dry-run ---
MANIFEST_FILE="$TEST_TMPDIR/test-manifest.json"
cat > "$MANIFEST_FILE" << 'MANIFEST'
[
  {"origin": "url", "url": "https://example.com/article1", "title": "Article One"},
  {"origin": "url", "url": "https://example.com/article2", "title": "Article Two"}
]
MANIFEST
run_gw --manifest "$MANIFEST_FILE" --dry-run
assert_contains "$err" "Batch Import" "batch shows header"
assert_contains "$err" "2 items" "batch counts items"
assert_contains "$err" "Article One" "batch shows item 1"
assert_contains "$err" "Article Two" "batch shows item 2"
assert_contains "$err" "Batch Complete" "batch shows summary"
assert_contains "$out" "RESULT:batch_total=2" "batch summary lines"

# --- Test 15: batch auto-detect from --manifest ---
assert_contains "$err" "Batch Import" "auto-detect batch from --manifest"

# --- Test 16: batch with invalid JSON fails ---
BAD_MANIFEST="$TEST_TMPDIR/bad-manifest.json"
echo "not valid json" > "$BAD_MANIFEST"
run_gw --manifest "$BAD_MANIFEST" --dry-run
[[ "$rc" -ne 0 ]] && pass "batch invalid JSON returns error" || fail "batch invalid JSON" "should have failed"

# --- Test 17: batch with missing manifest fails ---
run_gw --manifest "/nonexistent/manifest.json" --dry-run
[[ "$rc" -ne 0 ]] && pass "batch missing manifest returns error" || fail "batch missing manifest" "should have failed"

# --- Test 18: import-source and import-batch in prompt ---
run_gw --url "https://example.com/test" --import-source "safari" --import-batch "2026-04-05-safari" --dry-run
assert_contains "$out" "Import source: safari" "import-source in prompt"
assert_contains "$out" "Import batch: 2026-04-05-safari" "import-batch in prompt"

# --- Test 18b: --context must be declared in the charter (D5) ---
run_gw --url "https://example.com/test" --context mc --dry-run
assert_eq "$rc" "2" "an undeclared context → exit 2 (no charter: only personal)"
printf '# Charter\n\n## Contexts\n\n### personal\n\n### mc\n' > "$VAULT/_meta/charter.md"
run_gw --url "https://example.com/test" --context mc --dry-run
assert_eq "$rc" "0" "a declared context is accepted"
assert_contains "$out" "Context: mc" "gateway carries context label in CAPTURE CONTEXT block"
rm -f "$VAULT/_meta/charter.md"

# --- Test 19: --render flag recognized; the rendered text is the raw ---
run_gw --url "https://example.com/spa" --render --dry-run
assert_eq "$rc" "0" "--render dry-run exits 0"
assert_not_contains "$out" "Unknown option" "--render flag recognized"
assert_contains "$out" "Raw store: archive — $ARCHIVE/$(date +%Y-%m-%d)_<resource_id>.html" "--render: the raw is the rendered DOM HTML (.html, capture layer D4)"
assert_contains "$out" "Fetch: render force" "...the dry-run shows the forced render"

# --- Test 20: PDF origin auto-detected from --file extension ---
touch "$TEST_TMPDIR/paper.pdf"
run_gw --file "$TEST_TMPDIR/paper.pdf" --dry-run
assert_contains "$out" "Origin: pdf" "pdf origin auto-detected from .pdf file"
assert_contains "$out" "Source type: paper" "pdf → paper source_type"
assert_contains "$out" "Content format: pdf" "content format marked pdf in prompt"
assert_not_contains "$out" "Read tool" "prompt no longer instructs tool usage"
assert_contains "$out" "paper.pdf" "file name present in the plan"

# --- Test 21: PDF origin auto-detected from URL ending in .pdf ---
run_gw --url "https://arxiv.org/pdf/2401.12345.pdf" --dry-run
assert_contains "$out" "Origin: pdf" "pdf origin auto-detected from .pdf URL"
assert_contains "$out" "URL: https://arxiv.org/pdf/2401.12345.pdf" "URL preserved in prompt"

# --- Test 22: PDF URL with query string still detected ---
run_gw --url "https://example.com/download.pdf?v=2" --dry-run
assert_contains "$out" "Origin: pdf" "pdf origin detected with URL query string"

# --- Test 23: explicit --origin pdf with --file works ---
run_gw --origin pdf --file "$TEST_TMPDIR/paper.pdf" --dry-run
assert_contains "$out" "Origin: pdf" "explicit pdf origin works"
assert_contains "$out" "Source type: paper" "pdf → paper source_type (explicit)"

# --- Test 24–27: PDF dry-run shows the raw store and the origin path ---
run_gw --file "$TEST_TMPDIR/paper.pdf" --dry-run
assert_contains "$out" "Raw store: archive — $ARCHIVE/" "pdf dry-run shows the archive raw store"
assert_contains "$out" "_<resource_id>.pdf" "...with the revision-named .pdf"
assert_contains "$out" "Origin path: $TEST_TMPDIR/paper.pdf" "pdf from file shows origin_path"
run_gw --url "https://arxiv.org/pdf/2401.12345.pdf" --dry-run
assert_not_contains "$out" "Origin path:" "pdf from URL has no origin_path"
assert_contains "$out" "Raw store: archive" "pdf from URL still has a raw store"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" --file "$TEST_TMPDIR/paper.pdf" --dry-run >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_contains "$(cat "$TEST_TMPDIR/run.out")" "Raw store: state — $VSTATE/raw/" "without archive_dir the raw store is the vault state"

# --- Test 28–30: media dry-runs show the raw store ---
run_gw --url "https://youtube.com/watch?v=test123" --dry-run
assert_contains "$out" "_<resource_id>.txt" "youtube dry-run shows the transcript raw (.txt)"
touch "$TEST_TMPDIR/recording.mp3"
run_gw --file "$TEST_TMPDIR/recording.mp3" --origin audio --dry-run
assert_contains "$out" "Raw store: archive" "audio dry-run shows the raw store"
assert_contains "$out" "Origin path:" "audio file shows origin_path"

# --- Test 31: new flags are accepted and shown ---
run_gw --url "https://example.com/flags" --capture-id "feed:1~2" --import-ref "feed:1~2" --producer-fidelity exact --provenance-file "$TEST_TMPDIR/prov.json" --keep-run --dry-run
assert_eq "$rc" "0" "--capture-id/--import-ref/--producer-fidelity/--provenance-file/--keep-run accepted"
assert_contains "$out" "Capture id: feed:1~2" "dry-run shows the capture id"
run_gw --url "https://example.com/flags" --ref-id "legacy-ref" --dry-run
assert_contains "$out" "Capture id: legacy-ref" "--ref-id is an alias of --capture-id"
run_gw --url "https://example.com/flags" --capture-id "bad id!" --dry-run
assert_eq "$rc" "2" "an invalid capture-id → exit 2"
run_gw --url "https://example.com/flags" --depth sideways --dry-run
assert_eq "$rc" "2" "--depth takes auto|off|force"
run_gw --url "https://example.com/flags" --resume x --capture-id y --dry-run
assert_eq "$rc" "2" "--resume and --capture-id are exclusive"
touch "$TEST_TMPDIR/book.epub"
run_gw --origin book --file "$TEST_TMPDIR/book.epub" --dry-run
assert_contains "$out" "Resource: file — sha256 of $TEST_TMPDIR/book.epub" "--origin book --file is a file resource (identity from its bytes, never an empty url key)"
assert_contains "$out" "_<resource_id>.epub" "...with the book's extension as the raw name"

# --- Test 32: render-url.sh keeps headless Chrome off the login keychain and the
# user's profile. From a sandboxed shell the keychain is unreachable
# (errSecNoDefaultKeychain, -25307) and macOS shows a "Keychain Not Found" dialog
# whose "Reset To Defaults" would replace the user's login keychain; a throw-away
# --user-data-dir keeps the run out of the running Chrome's profile. Static check:
# the suite never launches Chrome.
RENDER="$MNEMON_ROOT/bin/render-url.sh"
chrome_call=$(sed -n '/^"\$CHROME" --headless/,/--dump-dom/p' "$RENDER")
assert_contains "$chrome_call" "--use-mock-keychain" "render-url.sh launches Chrome with --use-mock-keychain"
assert_contains "$chrome_call" '--user-data-dir="$PROFILE"' "render-url.sh launches Chrome with a throw-away --user-data-dir"
assert_contains "$(grep -F 'mktemp -d' "$RENDER")" 'PROFILE=$(mktemp -d -t render-url-profile.' "the profile dir is a fresh mktemp -d"
assert_contains "$(grep -F 'trap ' "$RENDER")" 'rm -rf "$TMP" "$PROFILE"' "the EXIT trap removes the profile dir with the dump"

# --- Test 33: the dead letter moved: _inputs/pending-writes is never written ---
STUB_FETCH=fail run_gw --url "https://example.com/unreachable" --capture-id dead-1
assert_eq "$rc" "1" "fetch failure → exit 1"
assert_eq "$(result error_class)" "fetch" "...error_class fetch"
[[ -e "$VAULT/_inputs/pending-writes" ]] && fail "no pending-writes" "exists" || pass "_inputs/pending-writes is no longer written (D6)"
assert_file_exists "$(receipt dead-1)" "...the receipt is the record instead"

# ---------------------------------------------------------------------------------------------
# --- Contract fixtures (tests/fixtures/contracts/): argv acceptance and RESULT shapes ---
# ---------------------------------------------------------------------------------------------
echo "--- contract fixtures: argv acceptance ---"
CF="$MNEMON_ROOT/tests/fixtures/contracts"
FXD="$TEST_TMPDIR/fixtures"
mkdir -p "$FXD/payloads"
# materialize per case: argv (placeholders substituted) one per line, stdin bytes, payload files
python3 - "$CF" "$FXD" "$VAULT" <<'PY'
import json, os, sys
cf, out, vault = sys.argv[1], sys.argv[2], sys.argv[3]
argv = json.load(open(os.path.join(cf, "expected-argv.json")))["cases"]
envs = {c["name"]: c for c in json.load(open(os.path.join(cf, "envelopes.json")))}
for name, case in argv.items():
    env = envs[name]
    eid = env["envelope"]["envelope_id"]
    pdir = os.path.join(out, "payloads", eid)
    os.makedirs(pdir, exist_ok=True)
    for rel, hexdata in env["payload_files"].items():
        p = os.path.join(pdir, "files", os.path.basename(rel))   # the consumer's layout: files/<basename> (A8)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(bytes.fromhex(hexdata))
    prov = os.path.join(pdir, "provenance.json")
    json.dump({"schema": "mnemon-provenance/1", "envelope_id": eid, "producer": env["envelope"]["producer"],
               "instance": env["envelope"]["instance"], "created_at": env["envelope"]["created_at"],
               "fidelity": env["envelope"]["fidelity"], "request_url": env["envelope"]["request"].get("url"),
               "request": env["envelope"]["request"], "provenance": env["envelope"]["provenance"]}, open(prov, "w"))
    lines = []
    for a in case["argv"]:
        a = a.replace("<envelope_id>", eid).replace("<provenance>", prov)
        if a.startswith("<snapshot:"):
            a = os.path.join(pdir, "files", os.path.basename(a[len("<snapshot:"):-1]))
        if a.startswith("<revision:"):
            a = "2026-10-12_aaaaaaaa"   # a revision the test creates in the vault
        lines.append(a)
    open(os.path.join(out, name + ".argv"), "w").write("\n".join(lines) + "\n")
    text = env["envelope"]["payload"].get("text")
    if text is not None:
        open(os.path.join(out, name + ".stdin"), "wb").write(text.encode("utf-8"))
    open(os.path.join(out, name + ".id"), "w").write(eid)
print("\n".join(sorted(argv)))
PY
# the revision a <revision:NAME> placeholder resolves to
mkdir -p "$VAULT/Sources/2026-10-12_aaaaaaaa"
printf -- '---\ntype: source\norigin: text\ncapture_id: "fixture-old"\nresource_sha256: "0000"\nrevision: "2026-10-12_aaaaaaaa"\n---\nold\n' > "$VAULT/Sources/2026-10-12_aaaaaaaa/source.md"
printf -- '---\ntype: extract\ntitle: "Old"\nstatus: extracted\n---\n### Summary\n' > "$VAULT/Sources/2026-10-12_aaaaaaaa/extract.md"

load_argv() {   # <case> → ARGV array
  ARGV=()
  while IFS= read -r line; do ARGV+=("$line"); done < "$FXD/$1.argv"
}
for name in $(jq -r '.cases | keys[]' "$CF/expected-argv.json"); do
  load_argv "$name"
  stdin_file=/dev/null
  [[ -f "$FXD/$name.stdin" ]] && stdin_file="$FXD/$name.stdin"
  rc=0; "$GW" source-add --config "$CONFIG" "${ARGV[@]}" --dry-run <"$stdin_file" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
  out="$(cat "$TEST_TMPDIR/run.out")"; err="$(cat "$TEST_TMPDIR/run.err")"
  if [[ "$name" == "context-undeclared" ]]; then
    assert_eq "$rc" "2" "fixture argv '$name' is parsed and refused by the context contract (exit 2)"
    assert_contains "$err" "context 'mc' is not declared" "...with the D5 message"
  else
    assert_eq "$rc" "0" "fixture argv '$name' is accepted (dry-run exit 0)"
    assert_not_contains "$err" "Unknown option" "...no unknown option for '$name'"
    assert_contains "$out" "Capture id: $(cat "$FXD/$name.id")" "...capture id bound for '$name'"
  fi
done

echo "--- contract fixtures: RESULT shapes ---"
# compare_shape <scenario> : the gateway's RESULT lines (in $out) against gateway-outcomes.json.
# Keys and order must match; capture_id/status/extract/error_class/retryable exactly; revision and
# path empty iff the fixture's are; source_sha256 exactly when the fixture has one, empty iff empty;
# title is the stub's guess, not compared.
compare_shape() {
  local scenario="$1" eid="$2" expected actual ok=true k ev av
  expected="$(jq -r --arg s "$1" --arg id "$2" '.scenarios[$s].stdout[] | gsub("<envelope_id>"; $id)' "$CF/gateway-outcomes.json")"
  # the phase-2 keys (completeness, canonical_url, acquisition, report) are additive (§4.3): the
  # phase-1 fixture shapes are compared without them
  actual="$(printf '%s\n' "$out" | grep '^RESULT:' | grep -vE '^RESULT:(replayed|completeness|canonical_url|acquisition|report)=')"
  assert_eq "$(printf '%s\n' "$actual" | cut -d= -f1 | tr '\n' ' ')" "$(printf '%s\n' "$expected" | cut -d= -f1 | tr '\n' ' ')" "$scenario: RESULT keys and order"
  for k in capture_id status extract error_class retryable; do
    ev="$(printf '%s\n' "$expected" | grep "^RESULT:$k=" | cut -d= -f2-)"; av="$(printf '%s\n' "$actual" | grep "^RESULT:$k=" | cut -d= -f2-)"
    assert_eq "$av" "$ev" "$scenario: $k"
  done
  for k in revision path source_sha256; do
    ev="$(printf '%s\n' "$expected" | grep "^RESULT:$k=" | cut -d= -f2-)"; av="$(printf '%s\n' "$actual" | grep "^RESULT:$k=" | cut -d= -f2-)"
    if [[ -z "$ev" ]]; then assert_eq "$av" "" "$scenario: $k empty"
    elif [[ "$k" == "source_sha256" ]]; then assert_eq "$av" "$ev" "$scenario: source_sha256 (the hash of the fixture's stdin bytes)"
    else [[ -n "$av" ]] && pass "$scenario: $k set" || fail "$scenario: $k set" "empty"; fi
  done
}
run_case() {   # <case> <extra args…>: runs the fixture argv for real
  local name="$1"; shift
  load_argv "$name"
  local stdin_file=/dev/null
  [[ -f "$FXD/$name.stdin" ]] && stdin_file="$FXD/$name.stdin"
  rc=0; bash -c '"$@"; exit $?' _ "$GW" source-add --config "$CONFIG" "${ARGV[@]}" "$@" <"$stdin_file" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
  out="$(cat "$TEST_TMPDIR/run.out")"; err="$(cat "$TEST_TMPDIR/run.err")"
}
stub_reset
run_case text-plain
assert_eq "$rc" "0" "extracted: exit 0"
compare_shape extracted "$(cat "$FXD/text-plain.id")"
run_case text-with-url
assert_eq "$rc" "0" "duplicate: exit 0"
compare_shape duplicate "$(cat "$FXD/text-with-url.id")"
# exit4-*: the fixture's source_sha256 is the text payload's hash, so the scenarios run on the
# text-plain argv (new revision via --recapture, a fresh id; a later --capture-id wins)
tp="$(cat "$FXD/text-plain.id")"
STUB_MODE=is-error run_case text-plain --recapture --capture-id "$tp~t"
assert_eq "$rc" "4" "exit4-transient: exit 4"
compare_shape exit4-transient "$tp~t"
STUB_FIXTURE="$FIX/extract-missing-heading.json" run_case text-plain --recapture --capture-id "$tp~p"
assert_eq "$rc" "4" "exit4-permanent: exit 4"
compare_shape exit4-permanent "$tp~p"
STUB_MODE=is-error run_case url-auto
assert_eq "$rc" "4" "exit4-transient on a url capture: exit 4"
assert_eq "$(result error_class)" "transient" "...error_class transient"
# exit1-*: fetch (curl + render fail), unsupported (a file no converter reads), archive-exists, integrity
load_argv url-auto
STUB_FETCH=fail run_gw --url "https://example.com/fetch-fails" --capture-id "$(cat "$FXD/url-auto.id")~f"
assert_eq "$rc" "1" "exit1-fetch: exit 1"
compare_shape exit1-fetch "$(cat "$FXD/url-auto.id")~f"
printf 'bytes' > "$TEST_TMPDIR/blob.xyz"
run_gw --file "$TEST_TMPDIR/blob.xyz" --capture-id "$(cat "$FXD/file-pdf.id")~u"
assert_eq "$rc" "1" "exit1-unsupported: exit 1"
compare_shape exit1-unsupported "$(cat "$FXD/file-pdf.id")~u"
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-raw-staged
run_gw --url "https://example.com/taken" --capture-id "$(cat "$FXD/url-auto.id")~a"
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
rm -f "$(rget "$(cat "$FXD/url-auto.id")~a" '.raw_staged_path')"
printf 'foreign\n' > "$(rget "$(cat "$FXD/url-auto.id")~a" '.raw_path')"
run_gw --capture-id "$(cat "$FXD/url-auto.id")~a"
assert_eq "$rc" "1" "exit1-archive-exists: exit 1"
compare_shape exit1-archive-exists "$(cat "$FXD/url-auto.id")~a"
# the fixture's integrity scenario is the missing source (evidence unverified: empty source_sha256, A6)
rev_tp="$(rget "$(cat "$FXD/text-plain.id")" '.revision')"
mv "$VAULT/Sources/$rev_tp/source.md" "$TEST_TMPDIR/source.md.away"
run_gw --capture-id "$(cat "$FXD/text-plain.id")"
assert_eq "$rc" "1" "exit1-integrity: exit 1 (source.md missing)"
compare_shape exit1-integrity "$(cat "$FXD/text-plain.id")"
mv "$TEST_TMPDIR/source.md.away" "$VAULT/Sources/$rev_tp/source.md"
# A6: frontmatter readers stop at the closing ---; a body that mimics frontmatter lines is never read
mkdir -p "$VAULT/Sources/2026-10-01_legacy01"
printf -- '---\ntype: source\norigin: url\nurl: "https://example.com/legacy-a6"\ntitle: "Legacy"\n---\nbody text\ncapture:\n  text_sha256: "0000000000000000000000000000000000000000000000000000000000000000"\nurl: "https://example.com/only-in-a-body"\n' > "$VAULT/Sources/2026-10-01_legacy01/source.md"
printf -- '---\ntype: extract\ntitle: "Legacy"\nstatus: extracted\n---\n### Summary\n' > "$VAULT/Sources/2026-10-01_legacy01/extract.md"
run_gw --url "https://example.com/legacy-a6" --capture-id a6-alias
assert_eq "$(result status)" "skipped-duplicate" "a legacy folder matched by its frontmatter url is a duplicate"
assert_eq "$(result source_sha256)" "$(body_sha "$VAULT/Sources/2026-10-01_legacy01/source.md")" "...reporting the existing folder's recomputed source hash (A6)"
assert_eq "$(rget a6-alias '.text_sha256')" "null" "...the alias receipt takes no text hash from a capture: block that sits in the body (A6)"
assert_eq "$(rget a6-alias '.legacy_match')" "true" "...and records legacy_match"
run_gw --capture-id a6-alias
assert_eq "$rc" "0" "a replay of the legacy alias exits 0 (verified by existence: the legacy folder has no revision/capture_id fields)"
assert_eq "$(result status)" "skipped-duplicate" "...reporting from the files of its target"
assert_eq "$(result replayed)" "true" "...as a replay"
run_gw --url "https://example.com/only-in-a-body" --capture-id a6-fresh
assert_eq "$(result status)" "extracted" "a url that appears only in a legacy folder's body is no match: captured fresh (A6)"
rm -rf "$VAULT/Sources/2026-10-01_legacy01"
run_gw --capture-id a6-alias
assert_eq "$rc" "1" "a legacy target that disappeared is evidence-missing on the alias replay"
assert_eq "$(result error_class)" "evidence-missing" "...evidence-missing"
# exit2-contract
run_case context-undeclared
assert_eq "$rc" "2" "exit2-contract: exit 2"
assert_eq "$(printf '%s\n' "$out" | grep -c '^RESULT:' || true)" "0" "exit2-contract: no RESULT lines"
assert_contains "$err" "context 'mc' is not declared" "exit2-contract: stderr carries the contract message"
# exit3 / exit75 for completeness of the matrix
rm -f "$XDG_STATE_HOME/mnemon/fence-probes.json"
run_gw --url "https://example.com/env" --capture-id env-1
assert_eq "$rc" "3" "exit3-environment: exit 3 (no probe record)"
write_probe_record
python3 "$MNEMON_ROOT/bin/vault-lock.py" --vault "$VAULT" -- bash -c ': > "$0"; sleep 2' "$TEST_TMPDIR/held-1" & holder=$!
wait_file "$TEST_TMPDIR/held-1"
run_gw --url "https://example.com/locked" --capture-id lock-1
assert_eq "$rc" "75" "exit75-locked: exit 75"
wait "$holder" 2>/dev/null || true
# the recapture fixture: --recapture --supersedes <revision:text-plain> creates a new revision
run_case "text-plain~2"
assert_eq "$rc" "0" "extracted-recapture: exit 0"
compare_shape extracted-recapture "$(cat "$FXD/text-plain~2.id")"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" '"2026-10-12_aaaaaaaa"' "extracted-recapture: supersedes recorded"

# ---------------------------------------------------------------------------------------------
# Phase 2 — the capture layer's CLI surface (docs/specs/2026-10-15-capture-layer.md §4.1, §4.3, §5)
# ---------------------------------------------------------------------------------------------
echo "--- phase 2: dry-run shows the canonical identity, the kind and the request schema ---"
run_gw --url "https://Example.com/Post?utm_source=x&s=alpha#intro" --dry-run
assert_eq "$rc" "0" "a url dry-run exits 0"
assert_contains "$out" "Canonical URL: https://example.com/Post?s=alpha" "...prints the canonical URL (host lowercased, utm dropped, fragment dropped, s kept)"
assert_contains "$out" "URL kind: page" "...and the kind"
assert_contains "$out" "Request schema: mnemon-request/2" "...and the request schema"
assert_contains "$out" "Fetch: render auto, external off, acquire timeout 300s" "...and the chain settings"
assert_contains "$out" "chunking decided after acquisition" "...the chunk plan is deferred for a URL"
run_gw --url "https://arxiv.org/abs/2401.12345v2" --dry-run
assert_contains "$out" "URL kind: arxiv (requested version 2)" "an arXiv URL: kind arxiv with the requested version"
assert_contains "$out" "Source type: paper" "...source_type defaults to paper"
assert_contains "$out" "Canonical URL: https://arxiv.org/abs/2401.12345" "...canonical abs/<id>"
run_gw --url "https://www.youtube.com/live/abc123xyz00" --dry-run
assert_contains "$out" "Origin: youtube" "a /live/ form routes to the media path by the canonical kind"
run_gw --url "https://music.youtube.com/watch?v=abc123xyz00" --dry-run
assert_contains "$out" "Origin: youtube" "...so does music.youtube.com"
run_gw --url "https://www.youtube.com/watch?v=abc123xyz00" --origin url --dry-run
assert_eq "$rc" "0" "an explicit --origin url on a YouTube URL is a page capture (explicit wins)"
assert_contains "$out" "Origin: url" "...origin url"
run_gw --url "https://example.com/not-a-video" --origin youtube --dry-run
assert_eq "$rc" "2" "an explicit --origin youtube on a non-YouTube URL → exit 2"
run_gw --url "https://example.com/x" --no-render --no-external --dry-run
assert_eq "$rc" "0" "--no-render / --no-external are accepted"
assert_contains "$out" "Fetch: render off, external off" "...and shown"
run_gw --url "https://example.com/x" --render --no-render --dry-run
assert_eq "$rc" "2" "--render with --no-render → exit 2"
run_gw --url "https://example.com/x" --assume-cost 0.5 --dry-run
assert_eq "$rc" "2" "--assume-cost without --resume → exit 2"
run_gw --url "https://example.com/x" --site auto --dry-run
assert_eq "$rc" "0" "--site auto dry-run exits 0"
assert_contains "$out" "Site plan: generated at run time" "...prints the deferral"
assert_contains "$out" "Source type: website" "...source_type website"
assert_contains "$out" "_<resource_id>.site/" "...the raw is a bundle directory"
f0="$(fetch_calls)"
run_gw --url "https://example.com/" --site "$FIX/reports/site-plan.json" --dry-run
assert_eq "$rc" "0" "--site <plan> dry-run exits 0 and fetches nothing"
assert_contains "$out" "Hosts: example.com, docs.example.com" "...lists the plan's hosts"
assert_contains "$out" "2. https://example.com/product (product)" "...and its included pages"
assert_not_contains "$out" "privacy" "...not the excluded ones"
assert_eq "$(fetch_calls)" "$f0" "...nothing fetched"
run_gw --origin text --file-text "$VAULT/reader-context.md" --dry-run
assert_contains "$out" "single call:" "a small --file-text shows a single-call chunk plan"
run_gw --site "$FIX/reports/site-plan.json" --origin text --dry-run </dev/null
assert_eq "$rc" "2" "--site without a --url landing → exit 2"

echo "--- phase 2: status counts superseded and chunked (§3.9) ---"
output=$("$GW" status --config "$CONFIG" 2>&1)
s0="$(printf '%s\n' "$output" | sed -n 's/^Superseded: //p')"; c0="$(printf '%s\n' "$output" | sed -n 's/^Chunked: //p')"
[[ "$s0" =~ ^[0-9]+$ && "$c0" =~ ^[0-9]+$ ]] && pass "status prints Superseded: and Chunked: counts" || fail "status counts" "'$s0' '$c0'"
mkdir -p "$VAULT/Sources/2026-04-04_aaaa0001" "$VAULT/Sources/2026-04-05_aaaa0001" "$VAULT/Sources/2026-04-06_bbbb0002" "$VAULT/Sources/2026-04-07_cccc0003"
printf -- '---\ntype: source\norigin: text\n---\nold\n' > "$VAULT/Sources/2026-04-04_aaaa0001/source.md"
printf -- '---\ntype: source\norigin: text\nsupersedes: "2026-04-04_aaaa0001"\n---\nnew\n' > "$VAULT/Sources/2026-04-05_aaaa0001/source.md"
printf -- '---\ntype: extract\nstatus: extracted\ntitle: "New"\nextraction: chunked\nparts: 3\n---\n' > "$VAULT/Sources/2026-04-05_aaaa0001/extract.md"
printf -- '---\ntype: source\norigin: text\nsupersedes: "Sources/2026-04-04_aaaa0001/extract"\n---\nnewer\n' > "$VAULT/Sources/2026-04-06_bbbb0002/source.md"
printf -- '---\ntype: extract\nstatus: extracted\ntitle: "Newer"\nextraction: single\n---\n' > "$VAULT/Sources/2026-04-06_bbbb0002/extract.md"
# a block list naming two more folders (O9): both count
printf -- '---\ntype: source\norigin: text\nsupersedes:\n  - "2026-04-05_aaaa0001"\n  - "Sources/2026-04-06_bbbb0002/extract"\n---\nnewest\n' > "$VAULT/Sources/2026-04-07_cccc0003/source.md"
output=$("$GW" status --config "$CONFIG" 2>&1)
assert_contains "$output" "Superseded: $((s0 + 3))" "status counts the folders named in any supersedes: (both scalar forms name one folder → +1; a block list names two more → +3)"
assert_contains "$output" "Chunked: $((c0 + 1))" "...and the extract.md files with extraction: chunked (+1)"
rm -rf "$VAULT/Sources/2026-04-04_aaaa0001" "$VAULT/Sources/2026-04-05_aaaa0001" "$VAULT/Sources/2026-04-06_bbbb0002" "$VAULT/Sources/2026-04-07_cccc0003"

cleanup_capture_test
summary
