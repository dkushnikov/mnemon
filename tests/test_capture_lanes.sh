#!/usr/bin/env bash
# test_capture_lanes.sh — the capture lanes' contract points of capture contracts §16–§17: the session of a capture
# (A13, §16: explicit --session → $MNEMON_SESSION → empty; a replay and --resume keep the receipt's) and a plain-text
# --file through the exact-text route (A14, §17). Everything runs against the stubs of tests/fixtures/capture/setup.sh;
# no network, no model. bash 3.2 / python 3.9.
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
echo "--- A13 the session: explicit → MNEMON_SESSION → empty ---"
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

echo "--- A13 a replay keeps the receipt's session ---"
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

echo "--- A13 a batch passes the session to its items ---"
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

# ---------------------------------------------------------------------------------------------
echo "--- A14 a plain-text --file is captured as its bytes (the --file-text route) ---"
stub_reset
# Markdown-looking text that pandoc would rewrite (emphasis, a list marker, two trailing spaces, CRLF)
printf '* item one\r\n**bold**  \r\nplain line\r\n' > "$TEST_TMPDIR/notes.txt"
sha_notes="$(shasum -a 256 "$TEST_TMPDIR/notes.txt" | cut -c1-64)"
run_gw --file "$TEST_TMPDIR/notes.txt" --capture-id pt-1 --title "Notes"
assert_eq "$rc $(result status)" "0 extracted" "--file notes.txt: extracted"
rev_pt="$(result revision)"; src_pt="$VAULT/Sources/$rev_pt/source.md"
assert_eq "$(body_sha "$src_pt")" "$sha_notes" "...source.md's body is the file's bytes (CRLF, markup and trailing spaces untouched)"
assert_eq "$(result source_sha256)" "$sha_notes" "...RESULT:source_sha256 equals the input's hash"
assert_eq "$(rget pt-1 '.request.origin') $(rget pt-1 '.request.resource_kind') $(rget pt-1 '.request.input_sha256')" "text text $sha_notes" "...the request: origin text, a text-kind resource keyed by the bytes"
assert_eq "$(rget pt-1 '.input_path')" "$TEST_TMPDIR/notes.txt" "...the file is the input locator"
assert_eq "$(capfm "$src_pt" method) $(capfm "$src_pt" fidelity) $(capfm "$src_pt" raw_store)" '"file-text" exact none' "...capture.method file-text, fidelity exact, no raw (the text is the raw)"
assert_eq "$(fm "$src_pt" origin)" "text" "...origin: text in source.md"
# the raw guard of capture contracts A12 runs on every path that reports a published revision; a plain-text --file
# has no raw to guard (raw_store: none), so the capture above, its replay and its duplicate all pass it
assert_eq "$(rget pt-1 '.raw_store') $(rget pt-1 '.raw_path') $(rget pt-1 '.state')" "none null extracted" "...the receipt records no raw (raw_store none), and the guard let it end extracted"
n_arch="$(ls -A "$ARCHIVE" | wc -l | tr -d ' ')"
run_gw --file "$TEST_TMPDIR/notes.txt" --capture-id pt-1
assert_eq "$rc $(result status) $(result revision) $(result error_class)" "0 extracted $rev_pt " "...its replay (--capture-id pt-1) passes the raw guard without a raw"
run_gw --file "$TEST_TMPDIR/notes.txt"
assert_eq "$rc $(result status) $(result revision) $(result error_class)" "0 skipped-duplicate $rev_pt " "...and so does its duplicate (the same bytes, no capture id)"
assert_eq "$(ls -A "$ARCHIVE" | wc -l | tr -d ' ') $(rget pt-1 '.state')" "$n_arch extracted" "...the archive gains nothing, and the receipt stays extracted"

printf 'UPPER CASE EXTENSION\n' > "$TEST_TMPDIR/SHOUT.TXT"
run_gw --file "$TEST_TMPDIR/SHOUT.TXT" --capture-id pt-upper
assert_eq "$rc $(rget pt-upper '.request.origin') $(capfm "$VAULT/Sources/$(result revision)/source.md" method)" '0 text "file-text"' "an uppercase .TXT takes the same route"
printf 'a .text file\n' > "$TEST_TMPDIR/other.text"
run_gw --file "$TEST_TMPDIR/other.text" --capture-id pt-text
assert_eq "$rc $(rget pt-text '.request.origin')" "0 text" "...and so does .text"

printf '\xef\xbb\xbfBOM first line\r\nsecond\r\n' > "$TEST_TMPDIR/bom.txt"
run_gw --file "$TEST_TMPDIR/bom.txt" --capture-id pt-bom
assert_eq "$rc $(body_sha "$VAULT/Sources/$(result revision)/source.md")" "0 $(shasum -a 256 "$TEST_TMPDIR/bom.txt" | cut -c1-64)" "a BOM and CRLF stay in the body as given (exact: no normalization)"

printf 'ok \xff\xfe bad\n' > "$TEST_TMPDIR/bad.txt"
n0="$(nfolders)"
run_gw --file "$TEST_TMPDIR/bad.txt" --capture-id pt-bad
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed invalid-utf8 false" "invalid UTF-8 in a .txt: failed/invalid-utf8, not retryable"
assert_eq "$(nfolders)" "$n0" "...nothing published"

mkdir -p "$TEST_TMPDIR/plain.dir"
printf 'an extensionless text file\n' > "$TEST_TMPDIR/plain.dir/README"
stub_file_mime text/plain
run_gw --file "$TEST_TMPDIR/plain.dir/README" --capture-id pt-noext
assert_eq "$rc $(rget pt-noext '.request.origin') $(capfm "$VAULT/Sources/$(result revision)/source.md" method)" '0 text "file-text"' "an extensionless file typed text/plain takes the text route"
printf 'opaque bytes\n' > "$TEST_TMPDIR/plain.dir/blob"
stub_file_mime application/octet-stream
run_gw --file "$TEST_TMPDIR/plain.dir/blob" --capture-id pt-blob
assert_eq "$rc $(result error_class) $(rget pt-blob '.request.origin')" "1 unsupported file" "...an extensionless file of another type stays on the file route (unsupported .bin)"
stub_file_off

printf '# a heading\n\n*emphasis*\n' > "$TEST_TMPDIR/note.md"
run_gw --file "$TEST_TMPDIR/note.md" --capture-id pt-md
assert_eq "$(rget pt-md '.request.origin')" "file" "a .md file stays on the file route (Markdown is a converter's input)"

echo "--- A14 an explicit --origin file keeps the file route ---"
printf 'explicit *file* origin\n' > "$TEST_TMPDIR/explicit.txt"
run_gw --origin file --file "$TEST_TMPDIR/explicit.txt" --capture-id pt-explicit
assert_eq "$(rget pt-explicit '.request.origin') $(rget pt-explicit '.request.resource_kind') $(rget pt-explicit '.raw_store')" "file file archive" "--origin file --file x.txt: origin file, a file-kind resource, the raw retained"
if command -v pandoc >/dev/null 2>&1; then
  assert_eq "$rc $(capfm "$VAULT/Sources/$(result revision)/source.md" method)" '0 "pandoc:markdown"' "...converted by pandoc as before"
else
  assert_eq "$rc $(result error_class)" "1 unsupported" "...the file route without pandoc: unsupported, as before"
fi

echo "--- A14 --canonical-url ---"
printf 'exported document text\n' > "$TEST_TMPDIR/export1.txt"
printf 'exported document text, edited\n' > "$TEST_TMPDIR/export2.txt"
run_gw --file "$TEST_TMPDIR/export1.txt" --canonical-url "https://docs.example.com/d/abc/edit?utm_source=x" --capture-id pt-canon
src_c="$VAULT/Sources/$(result revision)/source.md"
assert_eq "$rc $(rget pt-canon '.request.origin') $(rget pt-canon '.request.resource_kind')" "0 text url" "--file x.txt --canonical-url: origin text, a url-kind resource"
assert_eq "$(fm "$src_c" canonical_url) $(capfm "$src_c" method)" '"https://docs.example.com/d/abc/edit" "file-text"' "...identified by the canonical form, captured as its bytes"
assert_eq "$(body_sha "$src_c")" "$(shasum -a 256 "$TEST_TMPDIR/export1.txt" | cut -c1-64)" "...the body is the file's bytes"
run_gw --file "$TEST_TMPDIR/export2.txt" --canonical-url "https://docs.example.com/d/abc/edit"
assert_eq "$rc $(result status)" "0 skipped-duplicate" "...a second export of the same document is a duplicate"

echo "--- A14 the same bytes captured before as a file are one resource ---"
printf 'captured once as a file\n' > "$TEST_TMPDIR/once.txt"
run_gw --origin file --file "$TEST_TMPDIR/once.txt" --capture-id pt-once-file
rev_once="$(result revision)"
if [[ "$rc" == 0 ]]; then
  run_gw --file "$TEST_TMPDIR/once.txt" --capture-id pt-once-text
  assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $rev_once" "a plain-text --file whose bytes the vault holds as a file-kind revision: skipped-duplicate of that revision"
  run_gw --file "$TEST_TMPDIR/once.txt" --recapture --capture-id pt-once-re
  rev_re="$(result revision)"
  assert_eq "$rc $(result status) $(fm "$VAULT/Sources/$rev_re/source.md" supersedes)" "0 extracted \"$rev_once\"" "...--recapture makes the text-route revision, superseding the file-kind one"
  assert_eq "$(body_sha "$VAULT/Sources/$rev_re/source.md")" "$(shasum -a 256 "$TEST_TMPDIR/once.txt" | cut -c1-64)" "...with the bytes as its body"
  assert_eq "$(fm "$VAULT/Sources/$rev_once/source.md" resource_kind) $(fm "$VAULT/Sources/$rev_re/source.md" resource_kind)" "file text" "...the historical revision keeps its file-kind key, the new one is text-kind"
  # the data doctor reads the pair as one resource (the second reader, integrated review): no cross-resource edge
  # for the migration the engine made, while an explicit supersedes of another resource still is one
  supersedes_edges() {
    python3 "$MNEMON_ROOT/bin/vault-doctor.py" --vault "$VAULT" --state-dir "$TEST_TMPDIR/doctor-state" --json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
c = [x for x in d["checks"] if x["name"] == "store/supersedes"][0]
print(c["counts"]["cross_resource"], " ".join(sorted(i["id"] for i in c["items"] if i.get("reason") == "cross-resource edge")))'
  }
  mkdir -p "$TEST_TMPDIR/doctor-state"
  edges="$(supersedes_edges)"
  assert_not_contains "$edges" "$rev_re → $rev_once" "doctor --data: the file-to-text migration is not a cross-resource edge"
  assert_eq "${edges%% *}" "0" "...no cross-resource edge in the vault"
  printf 'another resource entirely\n' > "$TEST_TMPDIR/other.txt"
  run_gw --file "$TEST_TMPDIR/other.txt" --supersedes "$rev_pt" --capture-id pt-cross
  rev_cross="$(result revision)"
  edges="$(supersedes_edges)"
  assert_eq "$edges" "1 $rev_cross → $rev_pt" "...while a supersedes of another resource is still reported as one"
else
  echo "  (pandoc missing: the file-kind predecessor cannot be made; the alias case is not run)"
fi

echo "--- A14 a receipt recorded before the rule replays as it was recorded ---"
printf 'an older *file* capture\n' > "$TEST_TMPDIR/older.txt"
STUB_MODE=exit1 run_gw --origin file --file "$TEST_TMPDIR/older.txt" --capture-id pt-old
rev_old="$(result revision)"
if [[ "$rc" == 4 ]]; then
  body_old="$(body_sha "$VAULT/Sources/$rev_old/source.md")"
  run_gw --file "$TEST_TMPDIR/older.txt" --capture-id pt-old
  assert_eq "$rc $(result status) $(result revision)" "0 extracted $rev_old" "the feeder repeats --file x.txt with the id of an origin-file receipt: bound and finished, not exit 2"
  assert_eq "$(rget pt-old '.request.origin') $(body_sha "$VAULT/Sources/$rev_old/source.md")" "file $body_old" "...under the receipt's origin file, the published source untouched"
else
  echo "  (pandoc missing: no file-route source to replay)"
fi
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-raw-published
run_gw --origin file --file "$TEST_TMPDIR/older.txt" --recapture --capture-id pt-old-crash
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
assert_eq "$(rget pt-old-crash '.state') $(rget pt-old-crash '.raw_published')" "started true" "(setup) an origin-file capture crashed after its raw was published"
run_gw --file "$TEST_TMPDIR/older.txt" --recapture --capture-id pt-old-crash
src_oc="$VAULT/Sources/$(rget pt-old-crash '.revision')/source.md"
if command -v pandoc >/dev/null 2>&1; then
  assert_eq "$rc $(rget pt-old-crash '.request.origin') $(capfm "$src_oc" method)" '0 file "pandoc:markdown"' "...the replay reconverts the retained raw on the file route it recorded, never through the text route"
else
  assert_eq "$rc $(result error_class) $(rget pt-old-crash '.request.origin')" "1 unsupported file" "...the replay stays on the recorded file route (no pandoc: unsupported)"
fi

echo "--- A14 --file-text is a documented door ---"
help_out="$(bash "$GW" source-add --help 2>&1 || true)"
assert_contains "$help_out" "--file-text <path>    A text file captured as its bytes, exactly" "the gateway's --help documents --file-text as a door"
assert_contains "$help_out" "takes the --file-text" "...and the plain-text --file route"

cleanup_capture_test
summary
