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
assert_eq "$(file_mode "$(receipt r-1)")" "600" "receipt mode 0600"
assert_eq "$(file_mode "$VSTATE")" "700" "vault state dir mode 0700"
assert_eq "$(file_mode "$VSTATE/captures")" "700" "captures dir mode 0700"
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "free" "the lock is free after the capture"

# writer_host mismatch → exit 2 before any write
sed 's/^instance: captest/instance: captest-writer/' "$CONFIG" | awk '1; END { print "writer_host: not-this-host" }' > "$TEST_TMPDIR/writer.yaml"
n0="$(nfolders)"; r0="$(ls "$VSTATE/captures" | wc -l | tr -d ' ')"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/writer.yaml" --url "https://example.com/w1" --capture-id w-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "2" "writer_host mismatch → exit 2"
assert_contains "$(cat "$TEST_TMPDIR/run.err")" "must not write vault" "...with the writer message"
assert_eq "$(nfolders)" "$n0" "...nothing published"
assert_eq "$(ls "$VSTATE/captures" | wc -l | tr -d ' ')" "$r0" "...no receipt written"

# a lock held by another process → exit 75 (the holder signals readiness by creating a file after
# its flock succeeded — exec'd under the lock, so the file proves the lock is held)
python3 "$LOCK" --vault "$VAULT" -- bash -c ': > "$0"; sleep 12' "$TEST_TMPDIR/held-1" &   # long enough for the four refusals below (dispatcher runs resolve the instance first)
holder=$!
wait_file "$TEST_TMPDIR/held-1"
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "held" "probe reports held while another process holds the lock"
run_gw --url "https://example.com/l1" --capture-id l-1
assert_eq "$rc" "75" "a held vault lock → exit 75"
assert_contains "$err" "another writer holds the vault lock" "...with the lock message"
[[ -f "$(receipt l-1)" ]] && fail "no receipt on 75" "exists" || pass "...no receipt written"
# GH #30: the refusal prints a RESULT block (status locked, error_class locked, retryable true, nothing else)
assert_eq "$(result status) $(result error_class) $(result retryable) $(result extract)" "locked locked true none" "...RESULT:status=locked · error_class=locked · retryable=true · extract=none"
assert_eq "[$(result revision)] [$(result path)] [$(result source_sha256)] $(result capture_id)" "[] [] [] l-1" "...revision, path and hash empty; the requested capture_id reported"
# ...and through the dispatcher — the GH #30 repro itself (reviewer A P1-1): vault-lock.py prints the block
rc=0; "$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" add --url "https://example.com/l1-dispatch" --capture-id d-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status) $(result error_class) $(result retryable) $(result capture_id)" "75 locked locked true d-1" "mnemon add while the lock is held: exit 75 with the RESULT block (capture_id d-1)"
assert_contains "$(cat "$TEST_TMPDIR/run.err")" "another writer holds the vault lock" "...and the lock message on stderr"
[[ -f "$(receipt d-1)" ]] && fail "no receipt on 75 (dispatcher)" "exists" || pass "...no receipt written"
rc=0; "$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" add --url "https://example.com/l1-dispatch2" --ref-id d-2 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result capture_id)" "75 d-2" "...the --ref-id alias is reported too"
rc=0; "$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" add --url "https://example.com/l1-dispatch2" --capture-id=d-2 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc [$(result capture_id)]" "75 []" "...a --capture-id=<id> is not the gateway's form: not read as an id (reviewer A N2)"
rc=0; "$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" add --url "https://example.com/l1-dispatch3" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc [$(result capture_id)] $(result status)" "75 [] locked" "...without a capture id the field is empty"
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
wait_file "$TEST_TMPDIR/orphan.pid"
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
python3 "$LOCK" --vault "$VAULT" -- bash -c ': > "$0"; sleep 3' "$TEST_TMPDIR/held-2" & holder=$!
wait_file "$TEST_TMPDIR/held-2"
rc=0; MNEMON_VAULT_LOCK_FD=3 MNEMON_VAULT_LOCK_INODE=1 "$GW" source-add --config "$CONFIG" --url "https://example.com/inode2" --capture-id inode-2 3</dev/null >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "75" "...and gets 75 when the real lock is held (it did not assume the descriptor)"
wait "$holder" 2>/dev/null || true
# the real inherited descriptor is honoured: no re-exec, no second acquisition
rc=0; python3 "$LOCK" --vault "$VAULT" -- "$GW" source-add --config "$CONFIG" --url "https://example.com/inherit" --capture-id inherit-1 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "0" "a gateway started through vault-lock.py captures under the inherited descriptor"
assert_eq "$(result status)" "extracted" "...extracted"

# ---------------------------------------------------------------------------------------------
echo "--- A1. the lock proof is this vault's lock file, re-asserted; no child releases it ---"
# the extractor never sees the descriptor: closed and scrubbed before claude runs
stub_reset
run_gw --url "https://example.com/a1-claude" --capture-id a1-claude
assert_eq "$rc" "0" "a capture for the claude-environment check exits 0"
assert_eq "$(cat "$STUB_DIR/call-1.lockfd")" "unset" "claude runs without MNEMON_VAULT_LOCK_FD/INODE and without the descriptor"
# a descriptor for another vault's lock is no proof: the gateway acquires this vault's lock itself
mkdir -p "$TEST_TMPDIR/other-vault/Sources"
rc=0; python3 "$LOCK" --vault "$TEST_TMPDIR/other-vault" -- "$GW" source-add --config "$CONFIG" --url "https://example.com/a1-other" --capture-id a1-other </dev/null >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc" "0" "started under another vault's lock descriptor: the gateway acquires its own and captures"
assert_eq "$(result status)" "extracted" "...extracted"
python3 "$LOCK" --vault "$VAULT" -- bash -c ': > "$0"; sleep 3' "$TEST_TMPDIR/held-3" & holder=$!
wait_file "$TEST_TMPDIR/held-3"
rc=0; python3 "$LOCK" --vault "$TEST_TMPDIR/other-vault" -- "$GW" source-add --config "$CONFIG" --url "https://example.com/a1-other2" --capture-id a1-other2 </dev/null >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "75" "...and gets 75 while this vault's lock is held elsewhere (the other vault's descriptor matched the inode hint, not the proof)"
[[ -f "$(receipt a1-other2)" ]] && fail "...no receipt on 75" "exists" || pass "...no receipt on 75"
# a descriptor open on this vault's lock file that holds nothing, with the lock held elsewhere:
# the stat comparison passes, the re-asserted flock is refused → 75, never "under the lock" by the hint
rc=0; python3 - "$VSTATE/lock" "$GW" "$CONFIG" <<'PY' >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
import os, subprocess, sys
fd = os.open(sys.argv[1], os.O_RDWR)
os.set_inheritable(fd, True)
env = dict(os.environ, MNEMON_VAULT_LOCK_FD=str(fd), MNEMON_VAULT_LOCK_INODE=str(os.fstat(fd).st_ino))
sys.exit(subprocess.run([sys.argv[2], "source-add", "--config", sys.argv[3], "--url", "https://example.com/a1-unlocked",
                         "--capture-id", "a1-unlocked"], env=env, pass_fds=(fd,), stdin=subprocess.DEVNULL).returncode)
PY
assert_eq "$rc" "75" "a descriptor on this lock file that holds nothing, lock held elsewhere: the re-asserted flock is refused → 75"
assert_contains "$(cat "$TEST_TMPDIR/run.err")" "another writer holds the vault lock" "...with the lock message"
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$(result status) $(result error_class) $(result retryable)" "locked locked true" "...and the RESULT block says locked (the inherited-descriptor path too)"
[[ -f "$(receipt a1-unlocked)" ]] && fail "...no receipt on 75" "exists" || pass "...no receipt on 75"
wait "$holder" 2>/dev/null || true
# an abnormal child exit releases nothing: the parent's descriptor still holds the lock
export MNEMON_TEST_HOOKS=1
python3 "$LOCK" --vault "$VAULT" -- bash -c ': > "$2/crash.ready"; MNEMON_TEST_CRASH_AT=after-source-published "$0" source-add --config "$1" --url https://example.com/a1-crash --capture-id a1-crash </dev/null >"$2/crash.out" 2>"$2/crash.err"; sleep 3' "$GW" "$CONFIG" "$TEST_TMPDIR" &
parent=$!
wait_file "$TEST_TMPDIR/crash.ready"
unset MNEMON_TEST_HOOKS
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "held" "a child gateway killed mid-capture: the parent's descriptor still holds the lock"
run_gw --url "https://example.com/a1-third" --capture-id a1-third
assert_eq "$rc" "75" "...a third writer gets 75 (no child released the lock)"
wait "$parent" 2>/dev/null || true
assert_eq "$(python3 "$LOCK" --vault "$VAULT" --probe)" "free" "...and the lock is free once the parent exits"
assert_eq "$(rget a1-crash '.state')" "started" "...the crashed capture's receipt is still started (the crash point precedes the receipt update)"
assert_file_exists "$VAULT/Sources/$(rget a1-crash '.revision')/source.md" "...behind its published source.md"

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
