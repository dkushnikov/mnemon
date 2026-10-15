#!/usr/bin/env bash
# test_lock.sh — receipts, the vault lock and the writer (docs/specs/2026-10-15-capture-contracts.md
# §9 items 11–12) with real processes: atomic receipts, writer_host, exit 75, one ledger and one
# lock for two configs on a vault, two concurrent adds of one resource, parent death while a child
# holds the descriptor, an inherited descriptor with the wrong inode, the extractor timeout.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"
LOCK="$MNEMON_ROOT/bin/vault-lock.py"

echo "=== Receipts, Lock and Writer Tests ==="

# ---------------------------------------------------------------------------------------------
echo "--- 11. receipts, lock, writer ---"
run_gw --url "https://example.com/r1" --capture-id r-1
assert_eq "$rc" "0" "a capture under the self-acquired lock exits 0"
assert_eq "$(ls "$VSTATE/captures" | grep -c '\.tmp$' || true)" "0" "no receipt .tmp left behind"
assert_eq "$(stat -f %Lp "$(receipt r-1)")" "600" "receipt mode 0600"
assert_eq "$(stat -f %Lp "$VSTATE")" "700" "vault state dir mode 0700"
assert_eq "$(stat -f %Lp "$VSTATE/captures")" "700" "captures dir mode 0700"
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "free" "the lock is free after the capture"

# writer_host mismatch → exit 2 before any write
sed 's/^instance: captest/instance: captest-writer/; $a\
writer_host: not-this-host' "$CONFIG" > "$TEST_TMPDIR/writer.yaml"
n0="$(nfolders)"; r0="$(ls "$VSTATE/captures" | wc -l | tr -d ' ')"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/writer.yaml" --url "https://example.com/w1" --capture-id w-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "2" "writer_host mismatch → exit 2"
assert_contains "$(cat "$TEST_TMPDIR/run.err")" "must not write vault" "...with the writer message"
assert_eq "$(nfolders)" "$n0" "...nothing published"
assert_eq "$(ls "$VSTATE/captures" | wc -l | tr -d ' ')" "$r0" "...no receipt written"

# a lock held by another process → exit 75
python3 "$LOCK" --vault "$VAULT" -- sleep 4 &
holder=$!
sleep 0.5
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "held" "probe reports held while another process holds the lock"
run_gw --url "https://example.com/l1" --capture-id l-1
assert_eq "$rc" "75" "a held vault lock → exit 75"
assert_contains "$err" "another writer holds the vault lock" "...with the lock message"
[[ -f "$(receipt l-1)" ]] && fail "no receipt on 75" "exists" || pass "...no receipt written"
# two configs on one vault share the lock…
rc=0; "$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" --url "https://example.com/l2" --capture-id l-2 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "75" "a second config pointing at the same vault sees the same lock (75)"
wait "$holder" 2>/dev/null || true
# …and the ledger
rc=0; "$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" --url "https://example.com/l2" --capture-id l-2 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "0" "...and captures once the lock is free"
assert_file_exists "$(receipt l-2)" "...its receipt lands in the shared ledger"
assert_eq "$(jq -r '.instance' "$(receipt l-2)")" "captest-noarchive" "...recording its own instance"
run_gw --url "https://example.com/l2" --capture-id l-2-from-a
assert_eq "$(result status)" "skipped-duplicate" "...and the first config sees the capture as a duplicate (one ledger, one vault)"

# two concurrent adds of the same resource: one captures, one exits 75, one folder
n1="$(nfolders)"
export STUB_MODE=sleep STUB_SLEEP=2
"$GW" source-add --config "$CONFIG" --url "https://example.com/race" --capture-id race-a >"$TEST_TMPDIR/race-a.out" 2>"$TEST_TMPDIR/race-a.err" & pa=$!
"$GW" source-add --config "$CONFIG" --url "https://example.com/race" --capture-id race-b >"$TEST_TMPDIR/race-b.out" 2>"$TEST_TMPDIR/race-b.err" & pb=$!
ra=0; wait "$pa" || ra=$?
rb=0; wait "$pb" || rb=$?
unset STUB_MODE STUB_SLEEP
codes="$(printf '%s\n%s\n' "$ra" "$rb" | sort | tr '\n' ' ')"
assert_eq "$codes" "0 75 " "two concurrent adds: one exits 0, the other 75"
assert_eq "$(nfolders)" "$((n1 + 1))" "...one folder"

# parent death: the child keeps the descriptor, a third writer waits for the child
export STUB_MODE=sleep STUB_SLEEP=3
python3 "$LOCK" --vault "$VAULT" -- bash -c '"$0" source-add --config "$1" --url https://example.com/orphan --capture-id orphan-1 >"$2/orphan.out" 2>"$2/orphan.err" & echo $! > "$2/orphan.pid"; wait' "$GW" "$CONFIG" "$TEST_TMPDIR" &
parent=$!
sleep 1
kill -9 "$parent" 2>/dev/null; wait "$parent" 2>/dev/null || true
unset STUB_MODE STUB_SLEEP
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "held" "parent killed: the child gateway still holds the lock"
run_gw --url "https://example.com/third" --capture-id third-1
assert_eq "$rc" "75" "...a third writer gets 75 while the child runs"
child="$(cat "$TEST_TMPDIR/orphan.pid")"
while kill -0 "$child" 2>/dev/null; do sleep 0.2; done
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "free" "...the lock is released when the child exits"
assert_contains "$(cat "$TEST_TMPDIR/orphan.out")" "RESULT:status=extracted" "...the orphaned child finished its capture"
run_gw --url "https://example.com/third" --capture-id third-1
assert_eq "$rc" "0" "...and the third writer then succeeds"

# an inherited descriptor whose inode does not match → acquire on its own
rc=0; MNEMON_VAULT_LOCK_FD=3 MNEMON_VAULT_LOCK_INODE=1 "$GW" source-add --config "$CONFIG" --url "https://example.com/inode" --capture-id inode-1 3</dev/null >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "0" "a descriptor with the wrong inode is not trusted: the gateway acquires on its own and succeeds"
python3 "$LOCK" --vault "$VAULT" -- sleep 3 & holder=$!
sleep 0.5
rc=0; MNEMON_VAULT_LOCK_FD=3 MNEMON_VAULT_LOCK_INODE=1 "$GW" source-add --config "$CONFIG" --url "https://example.com/inode2" --capture-id inode-2 3</dev/null >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "75" "...and gets 75 when the real lock is held (it did not assume the descriptor)"
wait "$holder" 2>/dev/null || true
# the real inherited descriptor is honoured: no re-exec, no second acquisition
rc=0; python3 "$LOCK" --vault "$VAULT" -- "$GW" source-add --config "$CONFIG" --url "https://example.com/inherit" --capture-id inherit-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "0" "a gateway started through vault-lock.py captures under the inherited descriptor"
assert_eq "$(result status)" "extracted" "...extracted"

# ---------------------------------------------------------------------------------------------
echo "--- 12. timeout ---"
sed 's/^instance: captest/instance: captest-timeout/; s/^extract_timeout: .*/extract_timeout: 2/' "$CONFIG" > "$TEST_TMPDIR/timeout.yaml"
marker="mnemon-timeout-marker-$$"
rc=0; STUB_MODE=sleep STUB_SLEEP=30 STUB_MARKER="$marker" "$GW" source-add --config "$TEST_TMPDIR/timeout.yaml" --url "https://example.com/slow" --capture-id slow-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "4" "extract_timeout=2 with a sleeping stub → exit 4"
assert_eq "$(result error_class)" "timeout" "...error_class timeout"
assert_eq "$(result retryable)" "true" "...retryable"
assert_eq "$(rget slow-1 '.state')" "extract-failed" "...receipt extract-failed"
sleep 0.5
assert_eq "$(pgrep -f "$marker" | wc -l | tr -d ' ')" "0" "...the stub's child process is gone (whole group reaped)"
assert_contains "$(cat "$VSTATE/runs/slow-1/attempt-1/stderr.txt")" "timeout after 2s" "...stderr.txt records the timeout"

cleanup_capture_test
summary
