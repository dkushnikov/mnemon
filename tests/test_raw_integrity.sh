#!/usr/bin/env bash
# test_raw_integrity.sh — the published raw as evidence (docs/specs/2026-10-15-capture-contracts.md
# §15, A12): the fresh copy that publishes a raw, the staged copy that a File Provider restores over
# the rename (renamed into place again, never swept as stale staging), the guard before the source
# publication (failed/evidence-mismatch, nothing published), the guard after it (integrity-error /
# raw-missing | raw-mismatch, the revision kept) on every path that reports a published revision —
# a fresh capture, an extraction failure, the replay of every receipt state, an alias, a duplicate,
# an incomplete revision, --resume — for the archive and the state store, a file raw and a site
# bundle. Everything runs against the stubs of tests/fixtures/capture/setup.sh; no network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Raw Integrity Tests ==="
inode() { ls -id "$1" | awk '{print $1}'; }
sha_of() { shasum -a 256 "$1" | cut -c1-64; }
loss_run() {   # <boundary> <mode> <args…>: one gateway run with a raw loss injected at the boundary
  local at="$1" mode="$2"; shift 2
  export MNEMON_TEST_HOOKS=1 MNEMON_TEST_RAW_LOSS_AT="$at" MNEMON_TEST_RAW_LOSS="$mode"
  run_gw "$@"
  unset MNEMON_TEST_HOOKS MNEMON_TEST_RAW_LOSS_AT MNEMON_TEST_RAW_LOSS
}
run_noarchive() {   # <args…>: the gateway with the config that has no archive_dir (raw_store: state)
  local o="$TEST_TMPDIR/run.out" e="$TEST_TMPDIR/run.err"
  rc=0
  bash -c '"$@"; exit $?' _ "$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" "$@" </dev/null >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
revert_raw() {   # <raw path>: what the File Provider did — the published name renamed back to a staged name
  mv "$1" "$(dirname "$1")/.$(basename "$1").staging-4242"
}

# ---------------------------------------------------------------------------------------------
echo "--- 1. publication: a fresh copy takes the published name ---"
stub_reset
STUB_FETCH_HOOK="ls -il '$ARCHIVE'/.*.staging-* > '$TEST_TMPDIR/staged-inode'" run_gw --url "https://example.com/ri-fresh" --capture-id ri-fresh
assert_eq "$rc $(result status)" "0 extracted" "a url capture exits 0"
raw="$(rget ri-fresh '.raw_path')"
assert_file_exists "$raw" "...the raw is published"
assert_eq "$(sha_of "$raw")" "$(rget ri-fresh '.raw_sha256')" "...with the recorded hash"
staged_ino="$(awk '{print $1; exit}' "$TEST_TMPDIR/staged-inode")"
[[ -n "$staged_ino" && "$staged_ino" != "$(inode "$raw")" ]] && pass "...the published name is a fresh copy, not the long-lived staged file (inode $staged_ino → $(inode "$raw"))" || fail "fresh copy" "staged inode '$staged_ino', published $(inode "$raw")"
perms() { awk '{ sub(/[@+.]$/, "", $2); print $2; exit }'; }
assert_eq "$(ls -il "$raw" | perms)" "$(perms < "$TEST_TMPDIR/staged-inode")" "...the copy keeps the staged file's mode"
assert_eq "$(staging_left)" "0" "...no staged copy left (the original and the copy's name are gone)"
assert_eq "$(rget ri-fresh '.raw_staged_path') $(rget ri-fresh '.raw_published')" "null true" "...the receipt as before: raw_published, no staged path"
# the copy cannot be made (the archive turns read-only after the download): the staged original is
# renamed itself, and when that fails too the capture fails as before (the staged copy and its hash kept)
stub_reset
STUB_FETCH_HOOK="chmod 555 '$ARCHIVE'" run_gw --url "https://example.com/ri-ro" --capture-id ri-ro
chmod 755 "$ARCHIVE"
assert_eq "$rc $(result error_class) $(rget ri-ro '.raw_published')" "1 fetch false" "no room for a copy and no rename → exit 1 fetch, raw_published false"
assert_file_exists "$(rget ri-ro '.raw_staged_path')" "...the staged copy is kept"
assert_eq "$(ls -a "$ARCHIVE" | grep -c -- '-r$' | tr -d ' ')" "0" "...and no half-made fresh copy is left"
run_gw --capture-id ri-ro
assert_eq "$rc $(result status)" "0 extracted" "...the replay finishes the rename (through a fresh copy too)"
assert_eq "$(staging_left)" "0" "...nothing staged left"

# ---------------------------------------------------------------------------------------------
echo "--- 2. the staged copy a File Provider restores is renamed into place, never swept ---"
stub_reset
run_gw --url "https://example.com/ri-a" --capture-id ri-a
rawa="$(rget ri-a '.raw_path')"; shaa="$(rget ri-a '.raw_sha256')"
revert_raw "$rawa"
[[ -e "$rawa" ]] && fail "the simulated revert" "the raw is still there" || pass "the published raw renamed back to .<name>.staging-4242 (the field incident)"
stub_reset
run_gw --url "https://example.com/ri-b" --capture-id ri-b
assert_eq "$rc $(result status)" "0 extracted" "the next capture runs"
assert_file_exists "$rawa" "...and its cleanup renamed the copy into place again (it used to delete it)"
assert_eq "$(sha_of "$rawa")" "$shaa" "...the bytes the receipt recorded"
assert_contains "$err" "NOTE: restored the published raw $rawa from .$(basename "$rawa").staging-4242" "...a NOTE names the restoration"
assert_eq "$(rget ri-a '.raw_restored | length') $(rget ri-a '.raw_restored[0].from')" "1 .$(basename "$rawa").staging-4242" "...raw_restored in the receipt that recorded the raw"
assert_eq "$(staging_left)" "0" "...no staged copy left"
# a staged copy at that name with other bytes is not the evidence: swept as before
revert_raw "$rawa"
printf 'other bytes\n' > "$ARCHIVE/.$(basename "$rawa").staging-4242"
stub_reset
run_gw --url "https://example.com/ri-c" --capture-id ri-c
[[ -e "$ARCHIVE/.$(basename "$rawa").staging-4242" || -e "$rawa" ]] && fail "a copy with other bytes" "kept or restored" || pass "a staged copy with other bytes is swept, never restored"
run_gw --capture-id ri-a
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "...so the extracted capture's replay is integrity-error/raw-missing"
assert_eq "$(result revision) $(result retryable)" "$(rget ri-a '.revision') false" "...naming its revision, not retryable"
assert_eq "$(result source_sha256)" "$(rget ri-a '.text_sha256')" "...source_sha256 = the intact source's body hash"
assert_eq "$(result extract)" "ok" "...extract as the files hold it"
assert_eq "$(rget ri-a '.state') $(rget ri-a '.error_class')" "integrity-error raw-missing" "...stored in the receipt"
assert_file_exists "$VAULT/Sources/$(rget ri-a '.revision')/source.md" "...the revision is kept"
# the copy carries the hash but cannot be renamed (the archive is read-only): kept, then restored
stub_reset
run_gw --url "https://example.com/ri-d" --capture-id ri-d
rawd="$(rget ri-d '.raw_path')"
revert_raw "$rawd"
chmod 555 "$ARCHIVE"
printf 'a text capture writes nothing into the archive\n' > "$TEST_TMPDIR/d2.txt"
run_gw --origin text --file-text "$TEST_TMPDIR/d2.txt" --capture-id ri-d2
chmod 755 "$ARCHIVE"
assert_file_exists "$ARCHIVE/.$(basename "$rawd").staging-4242" "a copy that carries the hash and cannot be renamed is kept, not deleted"
run_gw --capture-id ri-d
assert_eq "$rc $(result status)" "0 extracted" "...and the next run restores it (the replay answers from the files)"
assert_file_exists "$rawd" "...in place"

# ---------------------------------------------------------------------------------------------
echo "--- 3. loss after the conversion: the guard before the source publication ---"
for mode in delete tamper; do
  stub_reset
  n0="$(nfolders)"
  loss_run after-convert "$mode" --url "https://example.com/ri-conv-$mode" --capture-id "ri-conv-$mode"
  assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed evidence-mismatch false" "$mode after the conversion → failed/evidence-mismatch, permanent"
  assert_eq "$(result revision) $(nfolders)" " $n0" "...nothing published"
  assert_contains "$err" "before the source publication" "...the message says where"
  assert_eq "$(ls -d "$VAULT"/Sources/.*.staging-* 2>/dev/null | wc -l | tr -d ' ')" "0" "...no staged source directory left"
  assert_eq "$(stub_calls)" "0" "...no model call"
  assert_eq "$(rget "ri-conv-$mode" '.state') $(rget "ri-conv-$mode" '.error_class')" "failed evidence-mismatch" "...the receipt failed/evidence-mismatch"
  c1="$(fetch_calls)"
  run_gw --capture-id "ri-conv-$mode"
  assert_eq "$rc $(result error_class) $(fetch_calls)" "1 evidence-mismatch $c1" "...the replay: the same class again, never refetched"
done
assert_contains "$err" "no longer matches its record" "a changed raw says so"
stub_reset
loss_run after-convert revert --url "https://example.com/ri-conv-revert" --capture-id ri-conv-revert
assert_eq "$rc $(result status)" "0 extracted" "a raw renamed back after the conversion is restored and the capture goes on"
assert_file_exists "$(rget ri-conv-revert '.raw_path')" "...the raw in place"
assert_eq "$(rget ri-conv-revert '.raw_restored | length')" "1" "...raw_restored recorded"
stub_reset
run_noarchive --url "https://example.com/ri-conv-state" --capture-id ri-conv-state-0
assert_eq "$(rget ri-conv-state-0 '.raw_store')" "state" "(the state store config)"
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_RAW_LOSS_AT=after-convert MNEMON_TEST_RAW_LOSS=delete
run_noarchive --url "https://example.com/ri-conv-state2" --capture-id ri-conv-state
unset MNEMON_TEST_HOOKS MNEMON_TEST_RAW_LOSS_AT MNEMON_TEST_RAW_LOSS
assert_eq "$rc $(result status) $(result error_class)" "1 failed evidence-mismatch" "the state store: a raw gone after the conversion → failed/evidence-mismatch"

# ---------------------------------------------------------------------------------------------
echo "--- 4. loss after the source publication: integrity-error, the revision kept ---"
stub_reset
loss_run after-source-published delete --url "https://example.com/ri-sp" --capture-id ri-sp
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 integrity-error raw-missing false" "a raw gone after the source publication → integrity-error/raw-missing, exit 1"
revsp="$(rget ri-sp '.revision')"
assert_eq "$(result revision) $(result path)" "$revsp Sources/$revsp/" "...revision and path reported"
assert_file_exists "$VAULT/Sources/$revsp/source.md" "...the source stays"
assert_eq "$(result extract)" "ok" "...extract=ok (the extract was published before the check)"
assert_eq "$(rget ri-sp '.state') $(rget ri-sp '.error_class')" "integrity-error raw-missing" "...the receipt integrity-error/raw-missing, never extracted"
assert_eq "$(rget ri-sp '.extract_sha256')" "$(sha_of "$VAULT/Sources/$revsp/extract.md")" "...with the extract's hash"
assert_eq "$(rget ri-sp '.finalized_at')" "null" "...not finalized"
stub_reset
run_gw --capture-id ri-sp
assert_eq "$rc $(result status) $(result error_class) $(result replayed)" "1 integrity-error raw-missing true" "...the replay: integrity-error again"
assert_eq "$(stub_calls)" "0" "...without a model call"
# an extraction that fails on a revision without its raw: integrity-error, not a retryable capture
stub_reset
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_RAW_LOSS_AT=after-source-published MNEMON_TEST_RAW_LOSS=delete
STUB_MODE=exit1 run_gw --url "https://example.com/ri-sp-fail" --capture-id ri-sp-fail
unset MNEMON_TEST_HOOKS MNEMON_TEST_RAW_LOSS_AT MNEMON_TEST_RAW_LOSS
assert_eq "$rc $(result status) $(result error_class) $(result extract)" "1 integrity-error raw-missing missing" "an extraction failure on a revision without its raw → integrity-error (exit 1, not 4)"
# the File Provider case: renamed back during the extraction, restored before the receipt is final
stub_reset
loss_run after-source-published revert --url "https://example.com/ri-sp-revert" --capture-id ri-sp-revert
assert_eq "$rc $(result status)" "0 extracted" "a raw renamed back during the extraction is restored at the finalization"
assert_file_exists "$(rget ri-sp-revert '.raw_path')" "...in place"
assert_eq "$(rget ri-sp-revert '.raw_restored | length') $(staging_left)" "1 0" "...recorded, nothing staged left"

# ---------------------------------------------------------------------------------------------
echo "--- 5. loss before the finalization; the operator restores the raw ---"
stub_reset
loss_run after-extract-published stash --url "https://example.com/ri-fin" --capture-id ri-fin
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "a raw gone between the extract and the final receipt → integrity-error/raw-missing"
revf="$(rget ri-fin '.revision')"
assert_not_contains "$(cat "$VAULT/index.md" 2>/dev/null)" "Sources/$revf/extract|" "...the unfinalized capture is not indexed"
rawf="$(rget ri-fin '.raw_path')"
mv "$rawf.stash" "$rawf"
stub_reset
run_gw --capture-id ri-fin
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "...restored by the operator, the replay answers extracted"
assert_eq "$(rget ri-fin '.state') $(rget ri-fin '.error_class')" "extracted null" "...the receipt reconciled to extracted"
assert_eq "$(stub_calls)" "0" "...no model call (the extract was already published)"
[[ "$(rget ri-fin '.finalized_at')" != "null" ]] && pass "...and finalized: finalized_at set" || fail "finalized_at after the restored replay" "null"
assert_contains "$(cat "$VAULT/index.md")" "Sources/$revf/extract|" "...the reindex hook ran: the revision is in index.md"
assert_eq "$(result reindex)" "ok" "...RESULT:reindex=ok"
assert_eq "$(ls -d "$VSTATE/runs/ri-fin" 2>/dev/null)" "" "...the attempt dir removed after the final receipt"
# the raw-loss hooks are scrubbed like the crash hooks: an inherited MNEMON_TEST_RAW_LOSS_AT never
# reaches a suite (the recovery case after-source-published of test_capture.sh, under the inherited
# pair: leaked, the crash run would lose its raw at after-convert and publish nothing)
inherited="$(MNEMON_TEST_RAW_LOSS_AT=after-convert MNEMON_TEST_RAW_LOSS=delete bash -c '
  MNEMON_ROOT="$1"; source "$1/tests/test-helper.sh" >/dev/null; source "$1/tests/fixtures/capture/setup.sh" >/dev/null
  export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-source-published
  run_gw --url "https://example.com/ri-inherit" --capture-id ri-inherit
  unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
  run_gw --capture-id ri-inherit
  printf "%s %s %s|%s" "$rc" "$(result status)" "$(rget ri-inherit ".state")" "${MNEMON_TEST_RAW_LOSS_AT:-unset}"
  cleanup_capture_test' _ "$MNEMON_ROOT" 2>/dev/null)"
assert_eq "$inherited" "0 extracted extracted|unset" "an inherited MNEMON_TEST_RAW_LOSS_AT / MNEMON_TEST_RAW_LOSS is scrubbed: the crash recovery case recovers"
stub_reset
loss_run after-extract-published tamper --url "https://example.com/ri-fin-t" --capture-id ri-fin-t
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "a raw whose bytes changed → integrity-error/raw-mismatch"
rawt="$(rget ri-fin-t '.raw_path')"
python3 -c 'import sys; p=sys.argv[1]; d=open(p,"rb").read(); open(p,"wb").write(d[:-1])' "$rawt"
run_gw --capture-id ri-fin-t
assert_eq "$rc $(result status)" "0 extracted" "...the bytes restored, the replay goes on"

# ---------------------------------------------------------------------------------------------
echo "--- 6. a raw lost after the capture finished: replay, duplicate, alias, incomplete, --resume ---"
stub_reset
run_gw --url "https://example.com/ri-late" --capture-id ri-late
revl="$(rget ri-late '.revision')"; rawl="$(rget ri-late '.raw_path')"
cp "$rawl" "$TEST_TMPDIR/late.keep"
rm -f "$rawl"
run_gw --capture-id ri-late
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "replay of an extracted capture whose raw is gone → integrity-error (it used to answer extracted)"
run_gw --url "https://example.com/ri-late"
assert_eq "$rc $(result status) $(result error_class) $(result revision)" "1 integrity-error raw-missing $revl" "a duplicate request → integrity-error, never skipped-duplicate"
run_gw --url "https://example.com/ri-late" --capture-id ri-late-alias
assert_eq "$rc $(result status) $(result capture_id)" "1 integrity-error ri-late-alias" "...with a new capture id: the same, under the requested id"
[[ -f "$(receipt ri-late-alias)" ]] && fail "no alias receipt for a duplicate that fails its guard" "exists" || pass "...and no alias receipt"
cp "$TEST_TMPDIR/late.keep" "$rawl"
run_gw --url "https://example.com/ri-late"
assert_eq "$rc $(result status)" "0 skipped-duplicate" "...restored: skipped-duplicate again"
run_gw --capture-id ri-late
assert_eq "$rc $(result status) $(rget ri-late '.state')" "0 extracted extracted" "...and the owner's replay reconciles its receipt"
# the File Provider case after the capture: the next replay's cleanup restores it first
revert_raw "$rawl"
run_gw --capture-id ri-late
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "a raw renamed back after the capture: the replay restores it and answers extracted"
assert_file_exists "$rawl" "...in place"
# an incomplete revision (extract-failed) whose raw is gone: every door to it
stub_reset
STUB_MODE=exit1 run_gw --url "https://example.com/ri-inc" --capture-id ri-inc
assert_eq "$rc" "4" "(an extract-failed capture)"
revi="$(rget ri-inc '.revision')"; rawi="$(rget ri-inc '.raw_path')"
cp "$rawi" "$TEST_TMPDIR/inc.keep"
rm -f "$rawi"
stub_reset
run_gw --capture-id ri-inc
assert_eq "$rc $(result status) $(result error_class) $(result extract)" "1 integrity-error raw-missing missing" "replay of extract-failed without the raw → integrity-error"
assert_eq "$(stub_calls)" "0" "...before any model call"
run_gw --url "https://example.com/ri-inc"
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "the incomplete revision's resume (no id, owner receipt) → integrity-error"
run_gw --url "https://example.com/ri-inc" --capture-id ri-inc-alias
assert_eq "$rc $(result status) $(result capture_id)" "1 integrity-error ri-inc-alias" "...under an alias that delegates to the owner → integrity-error"
run_gw --resume "$revi"
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "--resume <revision> → integrity-error"
assert_eq "$(stub_calls)" "0" "...none of them called the model"
cp "$TEST_TMPDIR/inc.keep" "$rawi"
run_gw --resume "$revi"
assert_eq "$rc $(result status)" "0 extracted" "...restored: --resume finishes the extraction"
# --resume on a complete revision: «complete (nothing to finish)» is an answer about a published revision too, so the
# raw is checked first, by presence — by the revision's name and by the capture id; the owner's replay answers
stub_reset
run_gw --url "https://example.com/ri-done" --capture-id ri-done
revd2="$(rget ri-done '.revision')"; rawd2="$(rget ri-done '.raw_path')"
run_gw --resume "$revd2"
assert_eq "$rc" "2" "--resume <revision> on a complete revision with its raw: exit 2"
assert_contains "$err" "revision $revd2 is complete (nothing to finish)" "...«complete (nothing to finish)»"
run_gw --resume ri-done
assert_eq "$rc" "2" "--resume <capture_id> on a complete capture with its raw: exit 2"
assert_contains "$err" "capture ri-done is complete (nothing to finish)" "...«complete (nothing to finish)»"
cp "$rawd2" "$TEST_TMPDIR/done.keep"
rm -f "$rawd2"
run_gw --resume "$revd2"
assert_eq "$rc $(result status) $(result error_class) $(result capture_id)" "1 integrity-error raw-missing ri-done" "...its raw gone: --resume <revision> → integrity-error under the owner's id, never «complete»"
assert_eq "$(rget ri-done '.state')" "integrity-error" "...the owner's receipt records it, as its replay does"
cp "$TEST_TMPDIR/done.keep" "$rawd2"
run_gw --capture-id ri-done
assert_eq "$rc $(result status) $(rget ri-done '.state')" "0 extracted extracted" "...restored: the replay reconciles the receipt"
rm -f "$rawd2"
run_gw --resume ri-done
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "--resume <capture_id> on a complete capture whose raw is gone → integrity-error"
printf 'x' > "$rawd2"
run_gw --resume "$revd2"
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "...a raw of another size → raw-mismatch"
cp "$TEST_TMPDIR/done.keep" "$rawd2"
run_gw --capture-id ri-done
assert_eq "$rc $(result status)" "0 extracted" "...restored again"
assert_eq "$(stub_calls)" "1" "...and the model ran once, for the capture itself"
# a complete folder whose receipt is not in this ledger: the folder's own claim is the reference, nothing is written
mv "$(receipt ri-done)" "$TEST_TMPDIR/ri-done.receipt"
run_gw --resume "$revd2"
assert_eq "$rc" "2" "--resume <revision> without a receipt, the raw present: exit 2 (complete)"
rm -f "$rawd2"
before="$(ls "$VSTATE/captures" | wc -l | tr -d ' ')"
run_gw --resume "$revd2"
assert_eq "$rc $(result status) $(result error_class) $(result revision)" "1 integrity-error raw-missing $revd2" "...the raw gone: integrity-error from the folder's own claim"
assert_eq "$(ls "$VSTATE/captures" | wc -l | tr -d ' ')" "$before" "...and no receipt is written"
cp "$TEST_TMPDIR/done.keep" "$rawd2"
mv "$TEST_TMPDIR/ri-done.receipt" "$(receipt ri-done)"
# a crash between a publication and its receipt, then the raw lost: the reconciliation rows guard too
for boundary in after-source-published after-extract-published; do
  stub_reset
  export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT="$boundary"
  run_gw --url "https://example.com/ri-crash-$boundary" --capture-id "ri-crash-$boundary"
  unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
  st="$(rget "ri-crash-$boundary" '.state')"
  rm -f "$(rget "ri-crash-$boundary" '.raw_path')"
  stub_reset
  run_gw --capture-id "ri-crash-$boundary"
  assert_eq "$rc $(result status) $(result error_class) $(stub_calls)" "1 integrity-error raw-missing 0" "crash $boundary (receipt $st), raw gone → the replay's reconciliation is integrity-error, no model call"
done
# an archive_dir that moved: the receipt's absolute path resolves by name under the configured one
stub_reset
run_gw --url "https://example.com/ri-moved" --capture-id ri-moved
mkdir -p "$TEST_TMPDIR/archive2"
cp -p "$ARCHIVE"/* "$TEST_TMPDIR/archive2/" 2>/dev/null || true
sed "s|^archive_dir: .*|archive_dir: $TEST_TMPDIR/archive2|" "$CONFIG" > "$TEST_TMPDIR/moved.yaml"
mv "$(rget ri-moved '.raw_path')" "$TEST_TMPDIR/moved.keep"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/moved.yaml" --capture-id ri-moved </dev/null >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status)" "0 extracted" "a raw found by its name under a moved archive_dir is no integrity error"
mv "$TEST_TMPDIR/moved.keep" "$(rget ri-moved '.raw_path')"
# a duplicate checks presence and size only (an answer that writes nothing never reads the bytes
# of a file a synced folder may have evicted); the capture's own replay hashes them
rawm="$(rget ri-moved '.raw_path')"
cp "$rawm" "$TEST_TMPDIR/moved.keep"
python3 -c 'import sys; p=sys.argv[1]; d=bytearray(open(p,"rb").read()); d[0] ^= 1; open(p,"wb").write(bytes(d))' "$rawm"
run_gw --url "https://example.com/ri-moved"
assert_eq "$rc $(result status)" "0 skipped-duplicate" "a duplicate of a raw changed in place at its size: presence and size hold"
run_gw --capture-id ri-moved
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "...the capture's replay hashes it: integrity-error/raw-mismatch"
printf 'short\n' > "$rawm"
run_gw --url "https://example.com/ri-moved"
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "a duplicate of a raw with another size → integrity-error/raw-mismatch"
cp "$TEST_TMPDIR/moved.keep" "$rawm"
# the presence check never opens the raw (an evicted file of a synced folder would download): a raw
# that cannot be opened answers by its metadata — present at its size; at another size, caught
chmod 000 "$rawm"
run_gw --url "https://example.com/ri-moved"
assert_eq "$rc $(result status)" "0 skipped-duplicate" "a duplicate of a raw that cannot be opened (mode 000): present with its recorded size, from the metadata alone"
chmod 600 "$rawm"
python3 -c 'import sys; p=sys.argv[1]; d=open(p,"rb").read(); open(p,"wb").write(d[:-1])' "$rawm"
chmod 000 "$rawm"
run_gw --url "https://example.com/ri-moved"
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "...and one byte short, still caught without opening it"
chmod 600 "$rawm"
cp "$TEST_TMPDIR/moved.keep" "$rawm"

# ---------------------------------------------------------------------------------------------
echo "--- 7. the state store and an exact origin ---"
stub_reset
run_noarchive --url "https://example.com/ri-state" --capture-id ri-state
raws="$(rget ri-state '.raw_path')"
assert_eq "$rc $(rget ri-state '.raw_store')" "0 state" "a capture into the state store"
assert_contains "$raws" "$VSTATE/raw/" "...its raw under <vault state>/raw/"
revert_raw "$raws"
run_noarchive --capture-id ri-state
assert_eq "$rc $(result status)" "0 extracted" "a state raw renamed back is restored by the next run too"
assert_file_exists "$raws" "...in place"
rm -f "$raws"
run_noarchive --capture-id ri-state
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "a state raw that is gone → integrity-error/raw-missing"
run_noarchive --url "https://example.com/ri-state"
assert_eq "$rc $(result status)" "1 integrity-error" "...its duplicate too (the owner receipt names the state raw)"
printf 'exact text\n' > "$TEST_TMPDIR/exact.txt"
run_gw_stdin "$TEST_TMPDIR/exact.txt" --origin text --capture-id ri-exact
assert_eq "$rc $(result status) $(rget ri-exact '.raw_store')" "0 extracted none" "an exact origin has no raw"
run_gw_stdin "$TEST_TMPDIR/exact.txt" --origin text
assert_eq "$rc $(result status)" "0 skipped-duplicate" "...its duplicate is unaffected by the guard"
run_gw --capture-id ri-exact
assert_eq "$rc $(result status)" "0 extracted" "...and so is its replay"

# ---------------------------------------------------------------------------------------------
echo "--- 8. a site bundle ---"
PLAN="$FIX/reports/site-plan.json"
stub_reset
run_gw --url "https://example.com/" --site "$PLAN" --capture-id ri-site
assert_eq "$rc $(result status)" "0 extracted" "a site capture"
bundle="$(rget ri-site '.raw_path')"
assert_dir_exists "$bundle" "...its bundle published"
assert_eq "$(staging_left)" "0" "...through a fresh copy of the tree, nothing staged left"
revert_raw "$bundle"
run_gw --capture-id ri-site
assert_eq "$rc $(result status)" "0 extracted" "a bundle renamed back is restored (a directory)"
assert_dir_exists "$bundle" "...in place"
printf 'x' >> "$bundle/01.html"
run_gw --capture-id ri-site
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "a member changed → integrity-error/raw-mismatch"
assert_contains "$err" "01.html" "...naming the member"
stub_reset
loss_run after-source-published delete --url "https://example.com/" --site "$PLAN" --recapture --capture-id ri-site-2
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "a bundle gone after the source publication → integrity-error/raw-missing"
stub_reset
loss_run after-convert delete --url "https://example.com/" --site "$PLAN" --recapture --capture-id ri-site-3
assert_eq "$rc $(result status) $(result error_class)" "1 failed evidence-mismatch" "...gone after the assembly → failed/evidence-mismatch, nothing published"

# ---------------------------------------------------------------------------------------------
echo "--- 9. the data doctor names a raw renamed back and a raw integrity error ---"
vdoc() { rc=0; out="$(python3 "$MNEMON_ROOT/bin/vault-doctor.py" --vault "$VAULT" --archive "$ARCHIVE" --state-dir "$VSTATE" "$@" 2>/dev/null)" || rc=$?; }
stub_reset
run_gw --url "https://example.com/ri-doc" --capture-id ri-doc
revd="$(rget ri-doc '.revision')"; rawdoc="$(rget ri-doc '.raw_path')"
revert_raw "$rawdoc"
vdoc --list store/archive
assert_contains "$out" "$revd" "store/archive lists the revision whose raw is not at its name"
assert_contains "$out" "the staged copy .$(basename "$rawdoc").staging-4242 is — the file system renamed the published raw back" "...and says the raw was renamed back, not lost"
vdoc
assert_contains "$(printf '%s\n' "$out" | grep '^fail store/archive')" "1 of them renamed back to a staged name ($revd)" "...the check line counts it apart"
rm -f "$ARCHIVE/.$(basename "$rawdoc").staging-4242"
run_gw --capture-id ri-doc
assert_eq "$(result error_class)" "raw-missing" "(the replay records integrity-error/raw-missing)"
vdoc --list store/ledger
assert_contains "$out" "receipt ri-doc in integrity-error/raw-missing (the engine found the published raw missing)" "store/ledger names the raw class of an integrity error"

# ---------------------------------------------------------------------------------------------
echo "--- 10. an image raw is guarded as every raw is (contracts A15 §3.6) ---"
# images are captured through the real worker here: the helper's probe stub (MNEMON_CAPTURE_IMAGE_CMD) is set
# aside for this section and restored after it
PROBE_STUB="${MNEMON_CAPTURE_IMAGE_CMD:-}"
unset MNEMON_CAPTURE_IMAGE_CMD
IMGFIX="$FIX/image"
mkdir -p "$TEST_TMPDIR/ri-tessdata"; printf 'model eng\n' > "$TEST_TMPDIR/ri-tessdata/eng.traineddata"
export MNEMON_TEST_HOOKS=1 OCR_CMD="$IMGFIX/stub-tesseract.py" MNEMON_TEST_TESSDATA_DIR="$TEST_TMPDIR/ri-tessdata"
export MNEMON_TEST_TESSERACT_CALLS="$STUB_DIR/tesseract-calls" MNEMON_TEST_READER_CALLS="$STUB_DIR/reader-calls"
printf '{"9.9.9-stub (Claude Code)": {"pass": true, "date": "2026-11-27", "probes": {"no-tools": true, "image-reader": true}}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"
ri_img() { cat "$IMGFIX/base.png" > "$TEST_TMPDIR/$1.png"; printf 'ri %s' "$1" >> "$TEST_TMPDIR/$1.png"; printf '%s' "$TEST_TMPDIR/$1.png"; }
img_loss() {   # <boundary> <mode> <capture id>: one image capture with a raw loss injected at the boundary
  stub_reset
  MNEMON_TEST_RAW_LOSS_AT="$1" MNEMON_TEST_RAW_LOSS="$2" run_gw --origin image --file "$(ri_img "$3")" --capture-id "$3"
}
readings() { local n; n="$(grep -c '^read$' "$STUB_DIR/reader-calls" 2>/dev/null)" || true; echo "${n:-0}"; }
# before the publication — after the reading here: the raw is verified before the source names it
img_loss after-convert delete ri-img-conv
assert_eq "$rc $(result status) $(result error_class)" "1 failed evidence-mismatch" "an image raw gone after the reading → failed/evidence-mismatch, nothing published"
assert_eq "$(readings) $(rget ri-img-conv '.source_published')" "1 false" "...the reading was made, the source was not published"
img_loss after-convert tamper ri-img-conv-t
assert_eq "$rc $(result status) $(result error_class)" "1 failed evidence-mismatch" "an image raw changed after the reading → failed/evidence-mismatch"
img_loss after-convert revert ri-img-conv-r
assert_eq "$rc $(result status)" "0 extracted" "an image raw renamed back after the reading is restored and the capture finished"
assert_eq "$(rget ri-img-conv-r '.raw_restored | length') $(staging_left)" "1 0" "...raw_restored recorded, nothing staged left"
assert_file_exists "$(rget ri-img-conv-r '.raw_path')" "...the raw in place"
# after the publication: integrity-error, the revision kept
img_loss after-source-published delete ri-img-sp
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "an image raw gone after the source publication → integrity-error/raw-missing"
assert_file_exists "$VAULT/Sources/$(rget ri-img-sp '.revision')/source.md" "...the revision is kept"
img_loss after-source-published tamper ri-img-sp-t
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-mismatch" "an image raw changed after the source publication → integrity-error/raw-mismatch"
img_loss after-source-published revert ri-img-sp-r
assert_eq "$rc $(result status) $(rget ri-img-sp-r '.raw_restored | length')" "0 extracted 1" "an image raw renamed back during the extraction is restored at the finalization"
img_loss after-extract-published delete ri-img-fin
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "an image raw gone before the final receipt → integrity-error/raw-missing"
stub_reset
run_gw --capture-id ri-img-fin
assert_eq "$rc $(result status)" "1 integrity-error" "...the replay answers integrity-error again"
assert_eq "$(readings)" "0" "...and reads nothing: no reading after publication"
# a duplicate of the same bytes with the raw gone
stub_reset
dupimg="$(ri_img ri-img-dup)"
run_gw --origin image --file "$dupimg" --capture-id ri-img-dup
assert_eq "$rc $(result status)" "0 extracted" "(an image captured)"
rm -f "$(rget ri-img-dup '.raw_path')"
stub_reset
run_gw --origin image --file "$dupimg"
assert_eq "$rc $(result status) $(result error_class)" "1 integrity-error raw-missing" "a duplicate of an image whose raw is gone → integrity-error, never skipped-duplicate"
assert_eq "$(readings)" "0" "...and nothing is read again"
unset MNEMON_TEST_HOOKS OCR_CMD MNEMON_TEST_TESSDATA_DIR MNEMON_TEST_TESSERACT_CALLS MNEMON_TEST_READER_CALLS
write_probe_record
[[ -n "$PROBE_STUB" ]] && export MNEMON_CAPTURE_IMAGE_CMD="$PROBE_STUB"

cleanup_capture_test
summary
