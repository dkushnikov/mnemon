#!/usr/bin/env bash
# test_chunked.sh — chunked extraction (docs/specs/2026-10-15-capture-layer.md §3.8, D13, §12 A3,
# §7.13): an oversized capture-text is split at heading boundaries outside fences, every part goes
# through the fenced extractor with the part template and extract-part/1, the merge receives the
# parts' outputs, extract.md records extraction: chunked / parts: N; one budget for the capture's
# lifetime with durable reservations in the receipt. Stub claude from tests/fixtures/capture/.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Chunked Extraction Tests ==="

# the instance: inline limit 300 000 bytes
sed 's/^instance: captest/instance: captest-chunk/; $a\
extract_inline_max_bytes: 300000' "$CONFIG" > "$TEST_TMPDIR/chunk.yaml"
CONFIG="$TEST_TMPDIR/chunk.yaml"

# a ~1 MB fixture: 12 `## ` sections of ~85 KB, a fenced block spanning a heading-looking line
python3 - "$TEST_TMPDIR/big.md" <<'PY'
import random, sys
random.seed(7)
words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
out = []
for s in range(1, 13):
    out.append("## Section %d\n\n" % s)
    body, size = [], 0
    while size < 85000:
        para = " ".join(random.choice(words) for _ in range(120)) + ".\n\n"
        body.append(para); size += len(para)
    if s == 5:
        body.insert(3, "```\ncode line\n## not a heading: inside the fence\nmore code\n```\n\n")
    out.append("".join(body))
open(sys.argv[1], "w").write("".join(out))
PY
big_bytes="$(wc -c < "$TEST_TMPDIR/big.md" | tr -d ' ')"
[[ "$big_bytes" -gt 1000000 ]] && pass "fixture: $big_bytes bytes of 12 sections with a fence spanning a heading-looking line" || fail "fixture size" "$big_bytes"
part_calls() { grep -l '^part$' "$STUB_DIR"/call-*.kind 2>/dev/null | wc -l | tr -d ' '; }
main_calls() { grep -l '^main$' "$STUB_DIR"/call-*.kind 2>/dev/null | wc -l | tr -d ' '; }

# ---------------------------------------------------------------------------------------------
echo "--- 1. split, parts, merge, extract.md ---"
stub_reset
run_gw_stdin "$TEST_TMPDIR/big.md" --origin text --capture-id ch-1 --title "Big"
assert_eq "$rc $(result status)" "0 extracted" "a 1 MB text capture is extracted in parts"
rev1="$(result revision)"
parts_json="$(rget ch-1 '.calls | map(select(.kind == "part")) | length')"
n_parts="$parts_json"
[[ "$n_parts" -ge 3 && "$n_parts" -le 8 ]] && pass "the receipt records $n_parts part calls (≤ extract_max_parts 8)" || fail "part count" "$n_parts"
assert_eq "$(part_calls) $(main_calls)" "$n_parts 1" "the stub saw $n_parts part prompts and one merge prompt"
assert_eq "$(rget ch-1 '.calls | map(select(.kind == "merge")) | length')" "1" "...one merge call in the receipt"
# the split: byte-preserving, never inside the fence, each part ≤ the limit — from the attempt dir kept by --keep-run
stub_reset
run_gw_stdin "$TEST_TMPDIR/big.md" --origin text --capture-id ch-keep --title "Big" --recapture --keep-run
attempt="$VSTATE/runs/ch-keep/attempt-1"
assert_file_exists "$attempt/parts/parts.json" "--keep-run keeps parts.json"
count="$(jq 'length' "$attempt/parts/parts.json")"
assert_eq "$count" "$n_parts" "...parts.json counts the same parts"
python3 - "$attempt/parts" "$TEST_TMPDIR/big.md" <<'PYC' && pass "the parts concatenate byte-equal to the input" || fail "byte-preserving split" "differs"
import json, os, sys
d, src = sys.argv[1], sys.argv[2]
parts = json.load(open(os.path.join(d, "parts.json")))
data = b"".join(open(os.path.join(d, "part-%d.md" % p["n"]), "rb").read() for p in parts)
assert data == open(src, "rb").read()
assert all(p["bytes"] == os.path.getsize(os.path.join(d, "part-%d.md" % p["n"])) for p in parts)
PYC
over=0; cut_in_fence=0
for f in "$attempt"/parts/part-*.md; do
  [[ "$(wc -c < "$f" | tr -d ' ')" -le 300000 ]] || over=$((over + 1))
  [[ $(( $(grep -c '^```' "$f") % 2 )) -eq 0 ]] || cut_in_fence=$((cut_in_fence + 1))
done
assert_eq "$over $cut_in_fence" "0 0" "...every part ≤ 300 000 bytes, no part cut inside the fence"
assert_eq "$(jq -r '.[0].first_line' "$attempt/parts/parts.json")" "## Section 1" "...parts start at headings"
# the part prompts: the part template, its marker, the extract-part/1 schema, the part-mode OUTPUT paragraph
p1="$(grep -l '^part$' "$STUB_DIR"/call-*.kind | head -1 | sed 's/\.kind$//')"
assert_contains "$(cat "$p1.stdin")" "<!-- mnemon:part-template -->" "a part prompt carries the part template (its marker line)"
assert_contains "$(cat "$p1.stdin")" "Return the structured fields of \`extract-part/1\` for this part only" "...and the part-mode OUTPUT paragraph"
assert_contains "$(cat "$p1.stdin")" "## Section 1" "...with the part as CONTENT"
assert_not_contains "$(cat "$p1.stdin")" "## Section 12" "...and only the part"
assert_contains "$(tr '\n' ' ' < "$p1.argv")" '"required":["title","summary","key_ideas","quotes"]' "...the --json-schema is extract-part/1"
assert_contains "$(tr '\n' ' ' < "$p1.argv")" '"additionalProperties":false' "...with no additional properties"
assert_eq "$(cat "$p1.cwd")" "$attempt/parts/part-1" "...run in its own directory parts/part-1/"
assert_file_exists "$attempt/parts/part-1/part.json" "...its validated output kept as part.json"
assert_file_exists "$attempt/parts/part-1/argv.json" "...with argv.json"
# the merge prompt: the parts' outputs instead of the content, the main template, extract/1
m1="$(grep -l '^main$' "$STUB_DIR"/call-*.kind | head -1 | sed 's/\.kind$//')"
assert_contains "$(cat "$m1.stdin")" "=== PART EXTRACTS ($n_parts) ===" "the merge prompt carries the part extracts"
assert_contains "$(cat "$m1.stdin")" "## Part 1 — Part title" "...rendered as ## Part n — <title>"
assert_contains "$(cat "$m1.stdin")" "- Capture contracts make fidelity checkable instead of asserted #learning" "...the claims as - claim #domain"
assert_contains "$(cat "$m1.stdin")" "> Source before LLM." "...the quotes as > …"
assert_not_contains "$(cat "$m1.stdin")" "=== CONTENT ===" "...no CONTENT section"
assert_not_contains "$(cat "$m1.stdin")" "## Section 7" "...none of the source text"
assert_contains "$(cat "$m1.stdin")" "### Key Ideas" "...the main template"
assert_contains "$(tr '\n' ' ' < "$m1.argv")" '"body":' "...the --json-schema is extract/1"
assert_eq "$(cat "$m1.cwd")" "$attempt/merge" "...run in merge/"
ex="$VAULT/Sources/$(rget ch-keep '.revision')/extract.md"
assert_eq "$(fm "$ex" extraction) $(fm "$ex" parts)" "chunked $n_parts" "extract.md records extraction: chunked, parts: N"
assert_eq "$(fm "$VAULT/Sources/$rev1/extract.md" extraction)" "chunked" "...for the first capture too"
# the budget ledger: every call reserved then reconciled, cost_usd = the sum
assert_eq "$(rget ch-keep '.calls | map(select(.state == "done")) | length') $(rget ch-keep '.calls | map(select(.state == "reserved")) | length')" "$((n_parts + 1)) 0" "every call is reconciled (none left reserved)"
assert_eq "$(rget ch-keep '.cost_usd')" "$(awk -v n="$((n_parts + 1))" 'BEGIN { printf "%g", n * 0.012 }')" "cost_usd = the sum of every call's cost"
assert_eq "$(rget ch-keep '.cost_unknown')" "false" "...no unknown cost"
assert_eq "$(rget ch-keep '.calls[0].id') $(rget ch-keep '.calls[0].n') $(rget ch-keep '.calls[0].attempt') $(rget ch-keep '.calls[0].reserved_usd')" "part-1-r1-a1 1 1 null" "a call record: id (part, round, attempt), n, attempt, no reservation without a budget"
assert_eq "$(rget ch-keep '.calls[-1].id') $(rget ch-keep '.calls[-1].kind')" "merge-r1-a1 merge" "...the merge call's id names its round and attempt"
assert_eq "$(grep -c -- '--max-budget-usd' "$p1.argv")" "0" "...and no --max-budget-usd without extract_max_usd"
# a small text stays a single call
printf 'small text\n' > "$TEST_TMPDIR/small.txt"
stub_reset
run_gw_stdin "$TEST_TMPDIR/small.txt" --origin text --capture-id ch-small
assert_eq "$(fm "$VAULT/Sources/$(result revision)/extract.md" extraction) $(part_calls)" "single 0" "a text below the limit: extraction single, no part call"
assert_eq "$(rget ch-small '.calls | length') $(rget ch-small '.calls[0].kind') $(rget ch-small '.calls[0].state') $(rget ch-small '.calls[0].id')" "1 single done single-r1-a1" "...recorded as one reconciled single call (A3)"
assert_eq "$(grep -c '^parts:' "$VAULT/Sources/$(result revision)/extract.md")" "0" "...and no parts: line"

# ---------------------------------------------------------------------------------------------
echo "--- 2. a validation retry of one part, a failing part, --resume ---"
stub_reset
STUB_FIXTURE_PART_BAD="$FIX/extract-part-bad.json" STUB_PART_BAD_CALLS=2 STUB_COST_BY_CALL="3=0.05" run_gw_stdin "$TEST_TMPDIR/big.md" --origin text --capture-id ch-retry --recapture --keep-run
assert_eq "$rc $(result status)" "0 extracted" "a part answered badly once is re-run with the validation diagnostic and the capture succeeds"
assert_eq "$(part_calls)" "$((n_parts + 1))" "...one more part call than parts"
assert_eq "$(rget ch-retry '.calls | map(select(.kind == "part" and .n == 2)) | length')" "2" "...both calls of part 2 recorded"
assert_eq "$(rget ch-retry '[.calls[] | select(.kind == "part" and .n == 2) | .id] | join(" ")')" "part-2-r1-a1 part-2-r2-a1" "...under distinct ids naming the round (A3: exactly once each)"
assert_eq "$(rget ch-retry '[.calls[] | select(.kind == "part" and .n == 2) | .cost_usd | tostring] | join(" ")')" "0.012 0.05" "...each keeping its own cost (the retry cost 0.05, the first answer 0.012)"
assert_eq "$(rget ch-retry '.cost_usd')" "$(awk -v n="$((n_parts + 1))" 'BEGIN { printf "%g", n * 0.012 + 0.05 }')" "...and the sum counted in the budget"
assert_file_exists "$VSTATE/runs/ch-retry/attempt-1/parts/part-2/prompt-retry.md" "...the re-run's prompt"
assert_contains "$(cat "$VSTATE/runs/ch-retry/attempt-1/parts/part-2/prompt-retry.md")" "=== VALIDATION ===" "...carries the VALIDATION diagnostic"
assert_contains "$(cat "$VSTATE/runs/ch-retry/attempt-1/parts/part-2/prompt-retry.md")" "summary: required" "...gateway-authored, naming the rule"
# a crash between the retry's answer and its debit: the round-2 call reconciles from result-retry.json
jq '(.calls[] | select(.id == "part-2-r2-a1")) |= (.state = "reserved" | .cost_usd = null | .result = null)' "$(receipt ch-retry)" > "$TEST_TMPDIR/r.json" && cp "$TEST_TMPDIR/r.json" "$(receipt ch-retry)"
rm -f "$VAULT/Sources/$(rget ch-retry '.revision')/extract.md"
stub_reset
run_gw --capture-id ch-retry
assert_eq "$rc $(result status)" "0 extracted" "a replay with the retry call left reserved finishes"
assert_eq "$(rget ch-retry '[.calls[] | select(.id == "part-2-r2-a1") | .state, (.cost_usd | tostring), .result] | join(" ")')" "done 0.05 reconciled" "...the round-2 call reconciled from its own result-retry.json (A3)"
stub_reset
STUB_FAIL_CALLS=3 run_gw_stdin "$TEST_TMPDIR/big.md" --origin text --capture-id ch-fail --recapture
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "4 captured claude-exit true" "a failing part → extract-failed with its class (exit 4), the capture retained"
revf="$(result revision)"
assert_eq "$(rget ch-fail '.calls | length') $(rget ch-fail '.calls[2].state') $(rget ch-fail '.calls[2].result')" "3 failed claude-exit" "...three calls recorded, the third failed"
assert_eq "$(rget ch-fail '.cost_usd')" "$(awk 'BEGIN { printf "%g", 2 * 0.012 }')" "...the two finished parts' costs debited (a failed call without a cost debits nothing)"
stub_reset
run_gw --resume "$revf"
assert_eq "$rc $(result status)" "0 extracted" "--resume re-runs the extraction"
assert_eq "$(part_calls)" "$n_parts" "...every part again (no part cache)"
assert_eq "$(rget ch-fail '.calls | length')" "$((3 + n_parts + 1))" "...the budget continues: the earlier calls stay in the ledger"
assert_eq "$(rget ch-fail '.calls[-1].attempt')" "2" "...under attempt 2"

# ---------------------------------------------------------------------------------------------
echo "--- 3. unsupported sizes ---"
head -c 400000 /dev/zero | tr '\0' 'x' > "$TEST_TMPDIR/span.txt"
stub_reset
run_gw_stdin "$TEST_TMPDIR/span.txt" --origin text --capture-id ch-span
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "4 captured unsupported-size false" "a single unsplittable span above the limit → unsupported-size (the capture retained)"
assert_eq "$(part_calls) $(main_calls)" "0 0" "...no call made"
sed 's/^instance: captest-chunk/instance: captest-2parts/; $a\
extract_max_parts: 2' "$CONFIG" > "$TEST_TMPDIR/two.yaml"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/two.yaml" --origin text --capture-id ch-2parts --recapture < "$TEST_TMPDIR/big.md" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result error_class)" "4 unsupported-size" "extract_max_parts: 2 for a text needing more → unsupported-size"
sed 's/^instance: captest-chunk/instance: captest-tiny/; s/^extract_inline_max_bytes: 300000/extract_inline_max_bytes: 300/' "$CONFIG" > "$TEST_TMPDIR/tiny.yaml"
python3 - "$TEST_TMPDIR/many.md" <<'PY'
import sys
open(sys.argv[1], "w").write("".join("## H%d\n\n%s\n\n" % (i, "word " * 40) for i in range(1, 6)))
PY
stub_reset
rc=0; "$GW" source-add --config "$TEST_TMPDIR/tiny.yaml" --origin text --capture-id ch-merge-big < "$TEST_TMPDIR/many.md" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result error_class)" "4 unsupported-size" "merge inputs above the inline limit → unsupported-size"
assert_contains "$err$(cat "$TEST_TMPDIR/run.err")" "rendered part extracts" "...after the parts ran, at the merge"
[[ "$(part_calls)" -gt 0 ]] && pass "...the parts had run ($(part_calls) calls)" || fail "parts ran" "0"

# ---------------------------------------------------------------------------------------------
echo "--- 4. the budget (A3): --max-budget-usd per call, budget-exhausted, unknown costs ---"
sed 's/^instance: captest-chunk/instance: captest-budget/; $a\
extract_max_usd: 0.03' "$CONFIG" > "$TEST_TMPDIR/budget.yaml"
stub_reset
rc=0; "$GW" source-add --config "$TEST_TMPDIR/budget.yaml" --origin text --capture-id ch-budget --recapture < "$TEST_TMPDIR/big.md" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "4 captured budget-exhausted false" "extract_max_usd below the parts' sum → budget-exhausted after the parts that fit"
assert_eq "$(part_calls)" "3" "...three parts fit in 0.03 (0.012 each)"
assert_eq "$(rget ch-budget '.calls | length') $(rget ch-budget '.cost_usd')" "3 0.036" "...their costs recorded (3 calls, spent 0.036)"
assert_eq "$(rget ch-budget '.calls[0].reserved_usd') $(rget ch-budget '.calls[1].reserved_usd') $(rget ch-budget '.calls[2].reserved_usd')" "0.03 0.018 0.006" "...each call reserved the remaining budget"
c1="$(grep -l '^part$' "$STUB_DIR"/call-*.kind | head -1 | sed 's/\.kind$//')"
assert_contains "$(tr '\n' ' ' < "$c1.argv")" "--max-budget-usd 0.03" "...and received --max-budget-usd = the remaining budget, not the full one"
c3="$(grep -l '^part$' "$STUB_DIR"/call-*.kind | sed -n '3p' | sed 's/\.kind$//')"
assert_contains "$(tr '\n' ' ' < "$c3.argv")" "--max-budget-usd 0.006" "...decreasing call by call"
assert_contains "$(cat "$TEST_TMPDIR/run.err")" "raise the budget" "...the message says what to do"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/budget.yaml" --capture-id ch-budget >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result error_class)" "4 budget-exhausted" "a replay without a raised budget stops at once"
sed 's/^extract_max_usd: 0.03/extract_max_usd: 1/' "$TEST_TMPDIR/budget.yaml" > "$TEST_TMPDIR/budget2.yaml"
stub_reset
rc=0; "$GW" source-add --config "$TEST_TMPDIR/budget2.yaml" --capture-id ch-budget >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status)" "0 extracted" "a raised budget finishes it"
assert_eq "$(rget ch-budget '.cost_usd')" "$(awk -v n="$((3 + n_parts + 1))" 'BEGIN { printf "%g", n * 0.012 }')" "...the lifetime spend includes the earlier parts"
# an unknown cost keeps its reservation: budget-unreconciled, --assume-cost reconciles
stub_reset
rc=0; STUB_COST_NULL=1 "$GW" source-add --config "$TEST_TMPDIR/budget2.yaml" --origin text --capture-id ch-unknown --recapture < "$TEST_TMPDIR/big.md" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "4 captured budget-unreconciled false" "a call with an unknown cost under a finite budget keeps its reservation: the next call stops with budget-unreconciled"
assert_eq "$(part_calls)" "1" "...after the first part"
assert_eq "$(rget ch-unknown '.cost_unknown') $(rget ch-unknown '.calls[0].cost_usd') $(rget ch-unknown '.calls[0].reserved_usd')" "true null 1" "...cost_unknown, the call's cost null, its reservation the whole budget"
assert_contains "$(cat "$TEST_TMPDIR/run.err")" "--assume-cost" "...the message names the operator's tool"
revu="$(result revision)"
stub_reset
rc=0; "$GW" source-add --config "$TEST_TMPDIR/budget2.yaml" --resume "$revu" --assume-cost 0.02 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status)" "0 extracted" "--resume --assume-cost reconciles the unknown call and finishes"
assert_eq "$(rget ch-unknown '.calls[0].cost_usd') $(rget ch-unknown '.calls[0].assumed') $(rget ch-unknown '.cost_unknown')" "0.02 true false" "...the assumed cost recorded as assumed: true"
# without a finite budget an unknown cost is recorded and nothing stops
stub_reset
STUB_COST_NULL=1 run_gw_stdin "$TEST_TMPDIR/small.txt" --origin text --capture-id ch-nocap --recapture
assert_eq "$rc $(result status) $(rget ch-nocap '.cost_unknown') $(rget ch-nocap '.cost_usd')" "0 extracted true null" "without extract_max_usd an unknown cost is recorded (cost_unknown) and nothing stops"
# a crash between a finished call and its debit: the reservation reconciles from result.json on resume
stub_reset
printf 'crash text\n' > "$TEST_TMPDIR/crash.txt"
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-extract-published
run_gw_stdin "$TEST_TMPDIR/crash.txt" --origin text --capture-id ch-crash
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
assert_eq "$(rget ch-crash '.calls[0].state')" "done" "fixture: a call reconciled before the crash"
jq '.calls[0].state = "reserved" | .calls[0].cost_usd = null' "$(receipt ch-crash)" > "$TEST_TMPDIR/r.json" && cp "$TEST_TMPDIR/r.json" "$(receipt ch-crash)"
rm -f "$VAULT/Sources/$(rget ch-crash '.revision')/extract.md"
stub_reset
run_gw --capture-id ch-crash
assert_eq "$rc $(result status)" "0 extracted" "a replay with a call left reserved finishes"
assert_eq "$(rget ch-crash '.calls[0].state') $(rget ch-crash '.calls[0].cost_usd') $(rget ch-crash '.calls[0].result')" "done 0.012 reconciled" "...the reserved call reconciled from its own result.json first (A3)"

# ---------------------------------------------------------------------------------------------
echo "--- 5. --dry-run prints the part plan for a text origin ---"
run_gw --origin text --file-text "$TEST_TMPDIR/big.md" --dry-run
assert_eq "$rc" "0" "dry-run exits 0"
assert_contains "$out" "=== CHUNK PLAN ===" "...prints the chunk plan"
assert_contains "$out" "chunked: $big_bytes bytes > extract_inline_max_bytes=300000 → $n_parts part(s) + 1 merge call" "...with the part count and the merge"
assert_contains "$out" "part 1: " "...and every part's size"
run_gw --url "https://example.com/big" --dry-run
assert_contains "$out" "chunking decided after acquisition" "a URL defers the chunk plan"
touch "$TEST_TMPDIR/doc.pdf"
run_gw --file "$TEST_TMPDIR/doc.pdf" --dry-run
assert_contains "$out" "chunking decided after acquisition" "...so does a --file pdf (A12)"

cleanup_capture_test
summary
