#!/usr/bin/env bash
# test_fence.sh — the isolation contract from the gateway's side (docs/specs/2026-10-15-capture-
# contracts.md §9 items 4, 6, 7): validation + serialization of the extractor's output, the fence
# argv and the probe gate, the charter contexts. Stub claude, no model, no network.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Extractor Fence Tests ==="

# ---------------------------------------------------------------------------------------------
echo "--- 4. validation and serialization ---"
stub_reset
STUB_MODE=bad-then-ok STUB_FIXTURE_BAD="$FIX/extract-missing-heading.json" run_gw --url "https://example.com/v1" --capture-id v-1
assert_eq "$rc" "0" "a first answer without ### Key Ideas → one re-run → the second publishes"
assert_eq "$(stub_calls)" "2" "...the stub was called twice"
assert_contains "$(cat "$STUB_DIR/call-2.stdin")" "=== VALIDATION ===" "...the re-run prompt carries the VALIDATION section"
assert_contains "$(cat "$STUB_DIR/call-2.stdin")" "the previous answer was rejected:" "...with the gateway-authored preamble"
assert_contains "$(cat "$STUB_DIR/call-2.stdin")" "body: must contain the heading ### Key Ideas" "...and the <field>: <rule> diagnostic"
assert_not_contains "$(cat "$STUB_DIR/call-2.stdin")" "missing the Key Ideas heading" "...never the model's own text reflected back"
assert_not_contains "$(cat "$STUB_DIR/call-1.stdin")" "=== VALIDATION ===" "...the first prompt had none"
assert_eq "$(rget v-1 '.attempts')" "1" "...one attempt (the re-run is inside it)"
assert_file_exists "$VAULT/Sources/$(result revision)/extract.md" "...extract.md published"

stub_reset
STUB_FIXTURE="$FIX/extract-missing-heading.json" run_gw --url "https://example.com/v2" --capture-id v-2
assert_eq "$rc" "4" "two bad answers → exit 4"
assert_eq "$(stub_calls)" "2" "...exactly two calls"
assert_eq "$(result status)" "captured" "...status captured"
assert_eq "$(result extract)" "invalid" "...extract=invalid"
assert_eq "$(result error_class)" "invalid-output" "...invalid-output"
assert_eq "$(result retryable)" "false" "...not retryable"
assert_eq "$(rget v-2 '.state')" "extract-failed" "...receipt extract-failed"
[[ -f "$VAULT/Sources/$(result revision)/extract.md" ]] && fail "no extract.md after invalid-output" "exists" || pass "...no extract.md"
[[ -f "$VAULT/Sources/$(result revision)/.extract.md.tmp" ]] && fail "no tmp left" "exists" || pass "...no .extract.md.tmp left"

stub_reset
STUB_MODE=bad-then-ok STUB_FIXTURE_BAD="$FIX/extract-bad-title.json" run_gw --url "https://example.com/v3" --capture-id v-3
assert_eq "$rc" "0" "a title with a newline + rating 10 → rejected, the clean second answer publishes"
assert_contains "$(cat "$STUB_DIR/call-2.stdin")" "title: must be a single line" "...diagnostic names the title rule"
python3 "$MNEMON_ROOT/bin/knowledge-reindex.py" "$VAULT" >/dev/null
assert_contains "$(grep -F "Sources/$(result revision)/extract|" "$VAULT/index.md")" "rating 6)" "...the reindexer reads rating from the real field"
assert_eq "$(printf '%s' "$(result title)" | wc -l | tr -d ' ')" "0" "RESULT:title never contains a newline"

stub_reset
STUB_FIXTURE="$FIX/extract-bad-people.json" run_gw --url "https://example.com/v4" --capture-id v-4
assert_eq "$rc" "0" "people elements that cannot be written unquoted are dropped, not fatal"
exv="$VAULT/Sources/$(result revision)/extract.md"
assert_contains "$(cat "$exv")" "people: [Grace Hopper]" "...*Alice, null, {} and 12 dropped"
assert_contains "$(cat "$exv")" "tags: [testing]" "...tags lowercased, deduplicated, invalid dropped"
assert_contains "$(cat "$exv")" "domains: [learning]" "...domains deduplicated after lowercasing, ~ dropped"
assert_contains "$(cat "$exv")" "language: en" "...language lowercased"
assert_contains "$(rget v-4 '.notes | join(";")')" "people: dropped 4" "...the drops are noted in the receipt"

stub_reset
STUB_FIXTURE="$FIX/extract-bad-domains.json" run_gw --url "https://example.com/v5" --capture-id v-5
assert_eq "$rc" "4" "all domains invalid → exit 4"
assert_eq "$(result error_class)" "invalid-output" "...invalid-output"
assert_contains "$(cat "$STUB_DIR/call-2.stdin")" "domains: at least 1 valid item" "...diagnostic after the bounds re-check"

printf 'body\n' > "$TEST_TMPDIR/t.txt"
run_gw_stdin "$TEST_TMPDIR/t.txt" --origin text --capture-id v-6 --title "$(printf 'multi\nline\ttitle')"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" 'title: "multi line title"' "a --title with control characters is normalized, never rejected, and written single-line"
assert_eq "$(result title)" "Plain T" "RESULT:title prefers extract.md's title"
printf 'another body\n' > "$TEST_TMPDIR/t2.txt"
STUB_MODE=exit1 run_gw_stdin "$TEST_TMPDIR/t2.txt" --origin text --capture-id v-7 --title "$(printf 'only\nsource')"
assert_eq "$(result title)" "only source" "...and falls back to source.md's normalized title when there is no extract"

# ---------------------------------------------------------------------------------------------
echo "--- 6. fence argv and gate ---"
run_gw --url "https://example.com/dry" --dry-run
assert_eq "$rc" "0" "dry-run exits 0"
for needle in '--restricted' '--tools ""' '--permission-prompts none' '--strict-mcp-config' '--json-schema'; do
  assert_contains "$out" "$needle" "dry-run argv contains $needle"
done
for needle in '--allowedTools' '--settings' '--add-dir' 'no tool restrictions' '--mcp-config' '--permission-mode'; do
  assert_not_contains "$out" "$needle" "dry-run never prints $needle"
done
assert_contains "$out" "=== PROMPT ===" "dry-run prints the prompt"
assert_eq "$(ls -d "$VSTATE"/runs/* 2>/dev/null | grep -c dry || true)" "0" "dry-run writes no attempt dir"
assert_eq "$(ls "$VSTATE"/captures/ 2>/dev/null | grep -c dry || true)" "0" "dry-run writes no receipt"

stub_reset
run_gw --url "https://example.com/argv" --capture-id a-1 --keep-run
assert_eq "$rc" "0" "capture with --keep-run exits 0"
argv_txt="$VSTATE/runs/a-1/attempt-1/argv.txt"
assert_file_exists "$argv_txt" "--keep-run keeps argv.txt"
for needle in '--restricted' '--tools ""' '--permission-prompts none' '--strict-mcp-config' '--json-schema'; do
  assert_contains "$(cat "$argv_txt")" "$needle" "argv.txt contains $needle"
done
for needle in '--allowedTools' '--settings' '--add-dir' '--mcp-config'; do
  assert_not_contains "$(cat "$argv_txt")" "$needle" "argv.txt never contains $needle"
done
assert_eq "$(wc -l < "$STUB_DIR/call-1.argv" | tr -d ' ')" "15" "the stub received exactly the 15 fence arguments — no positional prompt"
assert_eq "$(tail -1 "$STUB_DIR/call-1.argv")" "sonnet" "...the last argument is the model"
assert_eq "$(head -1 "$STUB_DIR/call-1.stdin")" "NON-INTERACTIVE EXTRACTION REQUEST. You are a function: read the sections below and return only the structured fields of the output schema. No questions, no commentary, no tool use." "the prompt arrives on stdin"
assert_contains "$(cat "$STUB_DIR/call-1.stdin")" "=== CONTENT ===" "...with the content inline"
assert_contains "$(cat "$STUB_DIR/call-1.stdin")" "from defuddle" "...the capture-text itself"
assert_eq "$(cat "$STUB_DIR/call-1.cwd")" "$VSTATE/runs/a-1/attempt-1" "the stub's cwd is the attempt dir"
assert_file_exists "$VSTATE/runs/a-1/attempt-1/prompt.md" "...which holds prompt.md"
assert_file_exists "$VSTATE/runs/a-1/attempt-1/template.md" "...template.md"
assert_file_exists "$VSTATE/runs/a-1/attempt-1/reader-context.md" "...reader-context.md"
assert_file_exists "$VSTATE/runs/a-1/attempt-1/result.json" "...result.json"

# the probe gate (D13)
probes="$XDG_STATE_HOME/mnemon/fence-probes.json"
rm -f "$probes"
n0="$(nfolders)"
run_gw --url "https://example.com/gate1" --capture-id g-1
assert_eq "$rc" "3" "no probe record for the running version → exit 3 before publication"
assert_eq "$(nfolders)" "$n0" "...nothing published"
assert_eq "$(result status)" "failed" "...status failed"
assert_eq "$(result error_class)" "fence-unprobed" "...fence-unprobed"
assert_eq "$(result retryable)" "true" "...retryable"
assert_contains "$err" "mnemon fence-probe" "...the message names the command to run"
assert_eq "$(rget g-1 '.state')" "failed" "...receipt failed"
write_probe_record
run_gw --capture-id g-1
assert_eq "$rc" "0" "...the replay after the probe passes"
printf '{"1.0.0-other (Claude Code)": {"pass": true}}\n' > "$probes"
run_gw --url "https://example.com/gate2" --capture-id g-2
assert_eq "$rc" "3" "a probe record for another version does not count"
write_probe_record
STUB_CURL_HOOK="rm -f $probes" run_gw --url "https://example.com/gate3" --capture-id g-3
assert_eq "$rc" "4" "the record disappearing between publication and extraction → exit 4"
assert_eq "$(result error_class)" "fence-unprobed" "...fence-unprobed"
assert_eq "$(result status)" "captured" "...the capture is retained"
assert_file_exists "$VAULT/Sources/$(result revision)/source.md" "...source.md published"
write_probe_record

# inline limit
sed 's/^instance: captest/instance: captest-small/; $a\
extract_inline_max_bytes: 100' "$CONFIG" > "$TEST_TMPDIR/small.yaml"
head -c 300 /dev/zero | tr '\0' 'x' > "$TEST_TMPDIR/big.txt"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/small.yaml" --origin text --capture-id big-1 < "$TEST_TMPDIR/big.txt" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "4" "content above extract_inline_max_bytes → exit 4"
assert_eq "$(result error_class)" "unsupported-size" "...unsupported-size"
assert_eq "$(result retryable)" "false" "...not retryable"
assert_eq "$(result status)" "captured" "...the capture is retained"
assert_file_exists "$VAULT/Sources/$(result revision)/source.md" "...source.md published"

# ref:mcp (D11)
run_gw --ref-source notion --ref-id page-123
assert_eq "$rc" "2" "ref:mcp → exit 2"
assert_contains "$err" "inbox" "...pointing to inbox producers"
run_gw --origin ref:mcp --ref-source notion --dry-run
assert_eq "$rc" "2" "...also with --dry-run"

# ---------------------------------------------------------------------------------------------
echo "--- 7. charter contexts ---"
charter="$VAULT/_meta/charter.md"
cat > "$charter" <<'EOF'
# Charter

## Purpose

Not contexts.

## Contexts

### personal
Everything private.

### work (the day job)

```
### fake
```

### work
duplicate, the first wins

## Other

### nope
EOF
run_gw --url "https://example.com/ctx" --context work --dry-run
assert_eq "$rc" "0" "--context work (declared) → ok"
assert_contains "$out" "Context: work" "...carried into the plan and prompt"
assert_contains "$err" "declares context 'work' twice" "...a duplicate heading is noted on stderr"
run_gw --url "https://example.com/ctx" --context mc --dry-run
assert_eq "$rc" "2" "--context mc (undeclared) → exit 2"
assert_contains "$err" "context 'mc' is not declared in $charter (declared: personal, work)" "...message lists the declared contexts"
run_gw --url "https://example.com/ctx" --context fake --dry-run
assert_eq "$rc" "2" "a ### heading inside a fenced block is ignored"
run_gw --url "https://example.com/ctx" --context nope --dry-run
assert_eq "$rc" "2" "a ### heading outside ## Contexts is ignored"
run_gw --url "https://example.com/ctx" --dry-run
assert_contains "$out" "Context: personal" "omitted --context → personal when declared"

rm -f "$charter"
run_gw --url "https://example.com/ctx" --context personal --dry-run
assert_eq "$rc" "0" "no charter: personal ok"
run_gw --url "https://example.com/ctx" --context work --dry-run
assert_eq "$rc" "2" "no charter: work exit 2"
assert_contains "$err" "(declared: personal)" "...declared set is {personal}"
printf '# Charter\n\n## Contexts\n\n(nothing yet)\n' > "$charter"
run_gw --url "https://example.com/ctx" --context work --dry-run
assert_eq "$rc" "2" "empty section: work exit 2"
run_gw --url "https://example.com/ctx" --context personal --dry-run
assert_eq "$rc" "0" "empty section: personal ok"
printf '# Charter\n\n## Contexts\n\n### work\n\n### research\n' > "$charter"
run_gw --url "https://example.com/ctx" --dry-run
assert_contains "$out" "Context: work" "a charter without personal → the first declared is the default"
run_gw --url "https://example.com/ctx" --context personal --dry-run
assert_eq "$rc" "2" "...and personal is then undeclared"
printf 'ctx body\n' > "$TEST_TMPDIR/ctx.txt"
run_gw_stdin "$TEST_TMPDIR/ctx.txt" --origin text --capture-id ctx-1 --context research
assert_eq "$rc" "0" "a real capture with a declared context"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" 'context: "research"' "...context written to source.md"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/extract.md")" 'context: "research"' "...and to extract.md"
assert_eq "$(rget ctx-1 '.request.context')" "research" "...and bound in the receipt"
rm -f "$charter"

cleanup_capture_test
summary
