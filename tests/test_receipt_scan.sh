#!/usr/bin/env bash
# test_receipt_scan.sh — the reads of bin/lib-capture.sh that run on every capture, done with fewer
# jq launches and the same answers: the ledger scan cap_receipts_jq (the revision names that
# non-final receipts reserve, the staging map of cap_cleanup_staging) prints byte for byte what one
# `jq -r <filter> <receipt>` per receipt printed — a healthy ledger, a receipt that does not parse
# in the middle, an empty receipt, a receipt that is valid JSON but not an object, no receipts at
# all, a byte-order mark, an unreadable receipt, small batches, a missing separator — from one jq
# per scan on a healthy ledger; the canonical answer of capture-fetch.py --canonical-only is read
# by one jq, with an empty and a spaced value kept exactly; and the jq launches of one capture do
# not grow with the number of receipts in the ledger. Stubs of tests/fixtures/capture/setup.sh; no
# network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Receipt Scan Tests ==="
LIB="$MNEMON_ROOT/bin/lib-capture.sh"
REALJQ="$(command -v jq)"
SHIM="$TEST_TMPDIR/jq-shim"; COUNT="$TEST_TMPDIR/jq-count"
mkdir -p "$SHIM"
printf '#!/bin/sh\necho x >> "%s"\nexec "%s" "$@"\n' "$COUNT" "$REALJQ" > "$SHIM/jq"; chmod +x "$SHIM/jq"
launches() { grep -c '' "$COUNT" 2>/dev/null || true; }

# the two filters of the scans, as bin/lib-capture.sh runs them
RES='select(.state != "extracted" and .state != "bookmarked" and (.revision // "") != "" and (.resolved_to_revision // "") == "") | .revision'
MAP='
      (select(.state != "extracted" and .state != "bookmarked") | "O\t" + ((.raw_staged_path // "") | rtrimstr("/"))),
      (select((.raw_path | type) == "string" and (.raw_sha256 | type) == "string")
        | "R\t" + (.raw_path | rtrimstr("/")) + "\t" + .raw_sha256 + "\t" + (.capture_id // ""))'

# old_scan <dir> <filter> > file: the reference — one jq per receipt, as the scans ran before
old_scan() {
  ( shopt -s nullglob; for f in "$1"/*.json; do "$REALJQ" -r "$2" "$f" 2>/dev/null || true; done )
}
# new_scan <dir> <filter> [shell code run after sourcing the library] > file (launches in $COUNT)
new_scan() {
  : > "$COUNT"
  ( source "$LIB"; CAPTURES_DIR="$1"; eval "${3:-}"; PATH="$SHIM:$PATH"; cap_receipts_jq "$2" )
}
# same_scan <label> <dir> [setup]: both filters, byte for byte; the launches of the last one in $n
same_scan() {
  local label="$1" d="$2" setup="${3:-}" k f
  for k in RES MAP; do
    f="${!k}"
    old_scan "$d" "$f" > "$TEST_TMPDIR/scan.old"
    new_scan "$d" "$f" "$setup" > "$TEST_TMPDIR/scan.new"
    n="$(launches)"
    if cmp -s "$TEST_TMPDIR/scan.old" "$TEST_TMPDIR/scan.new"; then pass "$label: the $k scan prints the bytes of one jq per receipt ($n jq)"
    else fail "$label: the $k scan" "$(diff "$TEST_TMPDIR/scan.old" "$TEST_TMPDIR/scan.new" | head -5 | tr '\n' ' ')"; fi
  done
}
receipt_json() {   # <capture_id> <state> <revision|null> [raw_staged_path] [raw_path raw_sha256] [resolved_to_revision]
  local rev='null' st='null' rp='null' rs='null' rr='null'
  [[ "$3" != null ]] && rev="\"$3\""
  [[ -n "${4:-}" ]] && st="\"$4\""
  [[ -n "${5:-}" ]] && rp="\"$5\"" && rs="\"$6\""
  [[ -n "${7:-}" ]] && rr="\"$7\""
  printf '{\n  "schema": "mnemon-capture-receipt/1",\n  "capture_id": "%s",\n  "state": "%s",\n  "revision": %s,\n  "raw_staged_path": %s,\n  "raw_path": %s,\n  "raw_sha256": %s,\n  "resolved_to_revision": %s\n}\n' \
    "$1" "$2" "$rev" "$st" "$rp" "$rs" "$rr"
}
healthy_ledger() {   # <dir>: twelve receipts, every kind of state the scans tell apart
  mkdir -p "$1"
  receipt_json h01 started 2026-11-01_00000001 "/a/.2026-11-01_00000001.html.staging-7/" > "$1/h01.json"
  receipt_json h02 fetched 2026-11-01_00000002 "" /a/2026-11-01_00000002.html "$(printf 'a%.0s' {1..64})" > "$1/h02.json"
  receipt_json h03 source-published 2026-11-01_00000003 > "$1/h03.json"
  receipt_json h04 failed 2026-11-01_00000004 "" /a/2026-11-01_00000004.pdf "$(printf 'b%.0s' {1..64})" > "$1/h04.json"
  receipt_json h05 extract-failed 2026-11-01_00000005 > "$1/h05.json"
  receipt_json h06 integrity-error 2026-11-01_00000006 > "$1/h06.json"
  receipt_json h07 extracted 2026-11-01_00000007 "" /a/2026-11-01_00000007.html "$(printf 'c%.0s' {1..64})" > "$1/h07.json"
  receipt_json h08 bookmarked 2026-11-01_00000008 > "$1/h08.json"
  receipt_json h09 started 2026-11-01_00000009 "" "" "" 2026-11-01_00000001 > "$1/h09.json"
  receipt_json h10 started null > "$1/h10.json"
  receipt_json h11 started "" > "$1/h11.json"
  receipt_json h12 started 2026-11-01_0000000c "/s/.2026-11-01_0000000c.site.staging-3" > "$1/h12.json"
}

# ---------------------------------------------------------------------------------------------
echo "--- 1. the ledger scan: the bytes of one jq per receipt, one jq for the scan ---"
L="$TEST_TMPDIR/ledgers"
healthy_ledger "$L/healthy"
same_scan "a healthy ledger of 12 receipts" "$L/healthy"
assert_eq "$n" "1" "...one jq for the whole ledger"
assert_eq "$( (source "$LIB"; CAPTURES_DIR="$L/healthy"; cap_reserved_revisions) | tr '\n' ' ')" \
  "2026-11-01_00000001 2026-11-01_00000002 2026-11-01_00000003 2026-11-01_00000004 2026-11-01_00000005 2026-11-01_00000006 2026-11-01_0000000c " \
  "...cap_reserved_revisions: every non-final, non-alias receipt with a revision, in ledger order"

# a receipt that does not parse, in the middle of the list: skipped, the others still count
healthy_ledger "$L/malformed"
printf '{"state":"started","revision":"2026-11-01_trunc001"' > "$L/malformed/h06b.json"
same_scan "a receipt that does not parse, in the middle" "$L/malformed"
[[ "$n" -lt 13 ]] && pass "...fewer jq than receipts ($n for 13): the batch is halved around it" || fail "malformed: launches" "$n jq for 13 receipts"
r="$( (source "$LIB"; CAPTURES_DIR="$L/malformed"; cap_reserved_revisions) )"
assert_not_contains "$r" "trunc001" "...it reserves nothing"
assert_contains "$r" "2026-11-01_0000000c" "...and the receipts after it still count"

# an empty receipt; whitespace only
healthy_ledger "$L/empty"
: > "$L/empty/h06b.json"; printf ' \n\t\n' > "$L/empty/h06c.json"
same_scan "an empty receipt and a blank one, in the middle" "$L/empty"
assert_eq "$n" "1" "...still one jq"

# valid JSON that is not an object (the filter's error is per value, as with one jq per receipt)
healthy_ledger "$L/notobject"
printf '[1, {"state":"started","revision":"2026-11-01_inarray1"}]\n' > "$L/notobject/h03b.json"
printf '"2026-11-01_string01"\n' > "$L/notobject/h06b.json"
printf '42\n' > "$L/notobject/h06c.json"
printf 'null\n' > "$L/notobject/h06d.json"
printf 'true\n' > "$L/notobject/h06e.json"
same_scan "valid JSON that is not an object (array, string, number, null, true)" "$L/notobject"
assert_eq "$n" "1" "...still one jq"
assert_contains "$(cat "$TEST_TMPDIR/scan.new")" "$(printf 'O\t')" "...a null receipt prints its bare O line, as before"

# no receipts at all
mkdir -p "$L/zero"; printf 'x' > "$L/zero/notes.txt"
same_scan "no receipts" "$L/zero"
assert_eq "$n" "0" "...no jq at all"
assert_eq "$(wc -c < "$TEST_TMPDIR/scan.new" | tr -d ' ')" "0" "...and no output"

# what jq strips only at the start of its input: a byte-order mark (the receipt still counts)
healthy_ledger "$L/bom"
printf '\357\273\277{"state":"started","revision":"2026-11-01_bom00001","raw_staged_path":"/a/.b.staging-1"}\n' > "$L/bom/h06b.json"
same_scan "a receipt behind a byte-order mark" "$L/bom"
assert_contains "$( (source "$LIB"; CAPTURES_DIR="$L/bom"; cap_reserved_revisions) )" "2026-11-01_bom00001" "...its revision stays reserved"

# a token or a structure that would run across files; several values in one receipt; no final newline
healthy_ledger "$L/across"
printf '{"state":"started",' > "$L/across/h06b.json"; printf '"revision":"2026-11-01_joined01"}\n' > "$L/across/h06c.json"
printf '{"state":"started","revision":"2026-11-01_tail0001"} 7' > "$L/across/h08b.json"
printf '{"state":"started","revision":"2026-11-01_multi001"}{"state":"failed","revision":"2026-11-01_multi002"}' > "$L/across/h10b.json"
same_scan "half a receipt in one file, the rest in the next; a trailing number; two values, no newline" "$L/across"
assert_not_contains "$( (source "$LIB"; CAPTURES_DIR="$L/across"; cap_reserved_revisions) )" "joined01" "...two halves never join into a receipt"

# an output string with a NUL (escaped in the receipt): the bytes of before, NUL included
healthy_ledger "$L/nul"
printf '{"state":"started","revision":"2026-11-01_nul\\u00000001"}\n' > "$L/nul/h06b.json"
same_scan "a revision with an escaped NUL" "$L/nul"

# a path that is no readable receipt: skipped, as the jq of that receipt printed nothing
healthy_ledger "$L/unreadable"
mkdir "$L/unreadable/h06b.json"
ln -s "$L/nowhere.json" "$L/unreadable/h06c.json"
if [[ "$(id -u)" != 0 ]]; then
  printf '{"state":"started","revision":"2026-11-01_locked01"}\n' > "$L/unreadable/h06d.json"; chmod 000 "$L/unreadable/h06d.json"
fi
same_scan "a directory, a dangling link and an unreadable receipt in the list" "$L/unreadable"
assert_eq "$n" "1" "...still one jq"
chmod 600 "$L/unreadable/h06d.json" 2>/dev/null || true

# batches: many receipts, a small argument budget — the same bytes, one jq per batch
mkdir -p "$L/many"
for i in $(seq 1 40); do receipt_json "m$i" "$( [[ $((i % 3)) -eq 0 ]] && echo extracted || echo started)" "2026-11-02_$(printf '%08d' "$i")" "" "/a/m$i" "$(printf '%064d' "$i")" > "$L/many/m$(printf '%03d' "$i").json"; done
same_scan "40 receipts" "$L/many"
assert_eq "$n" "1" "...one jq for 40 receipts"
same_scan "40 receipts in batches of a 2 KB argument budget" "$L/many" 'CAP_SCAN_BATCH_BYTES=2048'
[[ "$n" -gt 1 && "$n" -lt 40 ]] && pass "...one jq per batch ($n)" || fail "batches: launches" "$n jq"
printf '{"state":"started","revision":"2026-11-02_trunc002"' > "$L/many/m020b.json"
same_scan "41 receipts, one that does not parse" "$L/many"
[[ "$n" -lt 20 ]] && pass "...$n jq, not one per receipt" || fail "41 receipts, one bad: launches" "$n jq"

# no separator file (an incomplete engine tree): one jq per receipt, as before
same_scan "the separator file missing" "$L/healthy" 'CAP_SCAN_SEP="$TEST_TMPDIR/no-such.sep"'
assert_eq "$n" "12" "...one jq per receipt"

# ---------------------------------------------------------------------------------------------
echo "--- 2. the canonical answer: one jq, the values of the six reads ---"
canon_old() {   # <json>: the six reads of before (validation + five fields)
  local json="$1"
  printf '%s' "$json" | "$REALJQ" -e '.schema == "mnemon-canonical/1"' >/dev/null 2>&1 || { echo INVALID; return; }
  printf '%s|' "$(printf '%s' "$json" | "$REALJQ" -r '.canonical_url')" "$(printf '%s' "$json" | "$REALJQ" -r '.canonical_version // 1')" \
    "$(printf '%s' "$json" | "$REALJQ" -r '.fetch_url // .canonical_url')" "$(printf '%s' "$json" | "$REALJQ" -r '.kind // "page"')" \
    "$(printf '%s' "$json" | "$REALJQ" -r '.requested_version // ""')"
}
canon_new() {   # <json>: cap_canonical_lookup against a stub that answers <json> (launches in $COUNT)
  printf '%s\n' "$1" > "$TEST_TMPDIR/canon.json"
  printf '#!/bin/sh\ncat "%s"\n' "$TEST_TMPDIR/canon.json" > "$TEST_TMPDIR/canon-stub"; chmod +x "$TEST_TMPDIR/canon-stub"
  : > "$COUNT"
  ( source "$LIB"; CAP_FETCH_CMD="$TEST_TMPDIR/canon-stub"; cap_die2() { echo INVALID; exit 0; }; PATH="$SHIM:$PATH"
    cap_canonical_lookup "https://example.com/c"
    printf '%s|' "$CANONICAL_URL" "$CANONICAL_VERSION" "$FETCH_URL" "$URL_KIND" "$REQUESTED_VERSION" )
}
j='{"schema":"mnemon-canonical/1","canonical_url":"  https://example.com/a b  ","canonical_version":1,"fetch_url":"","kind":"page","requested_version":""}'
got="$(canon_new "$j")"; n="$(launches)"
assert_eq "$got" "  https://example.com/a b  |1||page||" "an empty fetch_url, an empty requested_version and a URL with spaces keep their places"
assert_eq "$got" "$(canon_old "$j")" "...the values of the six reads"
assert_eq "$n" "1" "...from one jq"
j='{"schema":"mnemon-canonical/1","canonical_url":"https://e.com/'"'"'q'"'"'\"d\"\\b$x`y`\t;|&","canonical_version":2,"kind":"arxiv","requested_version":"v3"}'
got="$(canon_new "$j")"; n="$(launches)"
assert_eq "$got" "$(canon_old "$j")" "quotes, a backslash, a tab, \$ and backticks: the values of the six reads ($n jq)"
j='{"schema":"mnemon-canonical/1","canonical_url":"https://e.com/a\nb","canonical_version":1}'
got="$(canon_new "$j")"; n="$(launches)"
assert_eq "$got" "$(canon_old "$j")" "a newline inside a value: the six reads of before run, with their values"
assert_eq "$n" "7" "...(one jq, then the six)"
got="$(canon_new '{"schema":"mnemon-canonical/2","canonical_url":"u"}')"
assert_eq "$got" "INVALID" "another schema is still an invalid url"

# ---------------------------------------------------------------------------------------------
echo "--- 3. a capture: its jq launches do not grow with the ledger ---"
bulk() {   # <n>: receipts bulk-0001… cloned from a real one (every tenth non-final)
  python3 - "$(receipt rs-warm)" "$VSTATE/captures" "$1" <<'PY'
import json, os, sys
src, d, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
base = json.load(open(src))
for i in range(1, n + 1):
    r = dict(base, capture_id="bulk-%04d" % i, revision="2026-01-%02d_%08x" % (1 + i % 28, i),
             state="failed" if i % 10 == 0 else "extracted")
    p = os.path.join(d, r["capture_id"] + ".json")
    with open(p, "w") as fh:
        fh.write(json.dumps(r, indent=2) + "\n")
    os.chmod(p, 0o600)
PY
}
counted_gw() { : > "$COUNT"; PATH="$SHIM:$PATH" run_gw "$@"; n="$(launches)"; }
nreceipts() { ls "$VSTATE/captures" | grep -c '\.json$' | tr -d ' '; }
stub_reset
run_gw --url "https://example.com/rs-warm" --capture-id rs-warm
bulk 9
k="$(nreceipts)"
counted_gw --url "https://example.com/rs-10" --capture-id rs-10
n10="$n"; assert_eq "$rc $(result status)" "0 extracted" "a capture with $k receipts in the ledger ($n jq)"
bulk 60
k="$(nreceipts)"
counted_gw --url "https://example.com/rs-61" --capture-id rs-61
assert_eq "$rc $(result status)" "0 extracted" "a capture with $k receipts in the ledger"
assert_eq "$n" "$n10" "...the same number of jq launches as with 10 ($n10)"
# a stale staged raw makes the cleanup scan run as well
printf 'stale' > "$ARCHIVE/.2026-01-01_deadbeef.html.staging-999"
k="$(nreceipts)"
counted_gw --url "https://example.com/rs-stale" --capture-id rs-stale
assert_eq "$rc $(result status)" "0 extracted" "a capture with a stale staged raw and $k receipts"
assert_eq "$n" "$((n10 + 1))" "...one jq more than without it: the cleanup scan is one jq"
assert_eq "$(ls -a "$ARCHIVE" | grep -c 'deadbeef' | tr -d ' ')" "0" "...and the stale staged raw is swept"
# a receipt that does not parse, last in the ledger: the cleanup still sweeps and the capture goes on
printf '{"state":"started"' > "$VSTATE/captures/zz-bad.json"
printf 'stale' > "$ARCHIVE/.2026-01-01_deadbee2.html.staging-999"
run_gw --url "https://example.com/rs-bad" --capture-id rs-bad
assert_eq "$rc $(result status)" "0 extracted" "a receipt that does not parse, last in the ledger: the capture goes on"
assert_eq "$(ls -a "$ARCHIVE" | grep -c 'deadbee2' | tr -d ' ')" "0" "...and the stale staged raw is swept"
rm -f "$VSTATE/captures/zz-bad.json"

cleanup_capture_test
summary
