#!/usr/bin/env bash
# test_capture_lanes.sh — the capture lanes' contract points of capture contracts §16: the session of a capture
# (A14: explicit --session → $MNEMON_SESSION → empty; a replay and --resume keep the receipt's). Everything runs
# against the stubs of tests/fixtures/capture/setup.sh; no network, no model. bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Capture Lanes Tests ==="

# ccfm <extract.md> <key>: a `  key: value` line of the capture_context: block
ccfm() { awk -v k="$2" 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f&&$0=="capture_context:"{c=1;next} c&&substr($0,1,1)!=" "{exit} c{p="  "k": "; if(substr($0,1,length(p))==p){print substr($0,length(p)+1);exit}}' "$1"; }
ext_of() { printf '%s/Sources/%s/extract.md' "$VAULT" "$1"; }
text_file() { printf '%s\n' "$2" > "$TEST_TMPDIR/$1.txt"; printf '%s' "$TEST_TMPDIR/$1.txt"; }

# ---------------------------------------------------------------------------------------------
echo "--- A14 the session: explicit → MNEMON_SESSION → empty ---"
stub_reset
run_gw_stdin "$(text_file s1 'session text one')" --origin text --title "S1" --capture-id s-1 --session "Flag session"
assert_eq "$rc $(result status)" "0 extracted" "an explicit --session: extracted"
assert_eq "$(rget s-1 '.metadata.session')" "Flag session" "...the receipt records the flag's session"
assert_eq "$(ccfm "$(ext_of "$(result revision)")" session)" '"Flag session"' "...and extract.md's capture_context.session"
assert_contains "$(cat "$STUB_DIR/call-1.stdin")" "Session: Flag session" "...and the prompt's capture context"

run_gw_stdin "$(text_file s2 'session text two')" --origin text --title "S2" --capture-id s-2
assert_eq "$(rget s-2 '.metadata.session')" "null" "no flag, no MNEMON_SESSION: the receipt's session is null (empty)"
assert_eq "$(ccfm "$(ext_of "$(result revision)")" session)" '""' "...and extract.md's session is empty"

MNEMON_SESSION="Env session" run_gw_stdin "$(text_file s3 'session text three')" --origin text --title "S3" --capture-id s-3
assert_eq "$(rget s-3 '.metadata.session')" "Env session" "no flag: the caller's MNEMON_SESSION is the session"
assert_eq "$(ccfm "$(ext_of "$(result revision)")" session)" '"Env session"' "...in extract.md too"

MNEMON_SESSION="Env session" run_gw_stdin "$(text_file s4 'session text four')" --origin text --title "S4" --capture-id s-4 --session "Flag wins"
assert_eq "$(rget s-4 '.metadata.session')" "Flag wins" "--session outranks MNEMON_SESSION"

MNEMON_SESSION="Env session" run_gw_stdin "$(text_file s5 'session text five')" --origin text --title "S5" --capture-id s-5 --session ""
assert_eq "$(rget s-5 '.metadata.session')" "null" "an explicit empty --session outranks MNEMON_SESSION too (empty)"

echo "--- A14 a replay keeps the receipt's session ---"
stub_reset
STUB_MODE=exit1 MNEMON_SESSION="First session" run_gw_stdin "$(text_file r1 'replay text one')" --origin text --title "R1" --capture-id r-1
assert_eq "$rc $(result status)" "4 captured" "(setup) the extractor fails: the source is published, exit 4"
rev_r1="$(result revision)"
assert_eq "$(rget r-1 '.metadata.session')" "First session" "...the receipt records the first caller's session"
MNEMON_SESSION="Retry session" run_gw --capture-id r-1 --session "Retry flag"
assert_eq "$rc $(result status)" "0 extracted" "a replay by --capture-id with another session finishes the capture"
assert_eq "$(ccfm "$(ext_of "$rev_r1")" session)" '"First session"' "...extract.md carries the receipt's session, not the retrying caller's flag or environment"
assert_eq "$(rget r-1 '.metadata.session')" "First session" "...and the receipt is unchanged"

stub_reset
STUB_MODE=exit1 run_gw_stdin "$(text_file r2 'replay text two')" --origin text --title "R2" --capture-id r-2 --session "Original"
rev_r2="$(result revision)"
MNEMON_SESSION="Resumer" run_gw --resume "$rev_r2"
assert_eq "$rc $(result status)" "0 extracted" "--resume <revision> finishes the capture"
assert_eq "$(ccfm "$(ext_of "$rev_r2")" session)" '"Original"' "...under the session its receipt recorded"

stub_reset
STUB_MODE=exit1 run_gw_stdin "$(text_file r3 'replay text three')" --origin text --title "R3" --capture-id r-3
rev_r3="$(result revision)"
MNEMON_SESSION="Resumer" run_gw --resume r-3 --session "Resumer flag"
assert_eq "$rc $(result status)" "0 extracted" "--resume <capture_id> of a capture recorded without a session"
assert_eq "$(ccfm "$(ext_of "$rev_r3")" session)" '""' "...stays without one: an empty recorded session is the receipt's value too"

echo "--- A14 a batch passes the session to its items ---"
stub_reset
python3 - "$TEST_TMPDIR/batch-s.json" <<'PY'
import json, sys
json.dump([{"origin": "text", "content": "batch session item\n", "title": "B1", "capture_id": "bs-1"}], open(sys.argv[1], "w"))
PY
MNEMON_SESSION="Env batch" run_gw --manifest "$TEST_TMPDIR/batch-s.json" --session ""
assert_eq "$rc" "0" "a batch with an explicit empty --session"
assert_eq "$(rget bs-1 '.metadata.session')" "null" "...its item is explicit-empty too (the environment does not leak past the flag)"
stub_reset
python3 - "$TEST_TMPDIR/batch-s2.json" <<'PY'
import json, sys
json.dump([{"origin": "text", "content": "batch session item two\n", "title": "B2", "capture_id": "bs-2"}], open(sys.argv[1], "w"))
PY
MNEMON_SESSION="Env batch" run_gw --manifest "$TEST_TMPDIR/batch-s2.json"
assert_eq "$(rget bs-2 '.metadata.session')" "Env batch" "a batch without the flag: its items take MNEMON_SESSION"

cleanup_capture_test
summary
