#!/usr/bin/env bash
# test_triage.sh — the bookmark queue (phase 3-capture, capture layer §13 B4): `mnemon triage list |
# dismiss | restore | promote` through the dispatcher, the judgement's two carriers (extract.md
# status, triage.md), its scope (the latest revision), promotion through `mnemon add`, and what the
# readers (search, lib_vault, the brief) say about it. Stubs only: no network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"
source "$MNEMON_ROOT/bin/mnemon-config.sh"
MNEMON="$MNEMON_ROOT/bin/mnemon"

echo "=== Bookmark Triage Tests ==="

# run_m <args…>: the dispatcher with the test instance; sets out, err, rc
run_m() {
  local o="$TEST_TMPDIR/m.out" e="$TEST_TMPDIR/m.err"
  rc=0
  bash -c '"$@"; exit $?' _ "$MNEMON" --config "$CONFIG" "$@" </dev/null >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
col() { printf '%s\n' "$out" | awk -F'\t' -v r="$1" -v c="$2" '$1 == r { print $c }'; }

# ---------------------------------------------------------------------------------------------
echo "--- 0. a vault with bookmarks of every shape ---"
stub_reset
run_gw --url "https://example.com/a" --tier bookmark --title "Card A" --intent "from tabs" --capture-id bm-a
bma="$(result revision)"
run_gw --url "https://example.com/b" --tier bookmark --title "Card B" --capture-id bm-b
bmb="$(result revision)"
printf 'a text worth a second look\n' > "$TEST_TMPDIR/t.txt"
run_gw_stdin "$TEST_TMPDIR/t.txt" --origin text --tier bookmark --title "Text card" --capture-id bm-t
bmt="$(result revision)"
run_gw --url "https://example.com/full" --capture-id full-1
full="$(result revision)"
# a legacy placeholder (status: bookmark, no managed keys) and a legacy bookmark with an extract
mkdir -p "$VAULT/Sources/2026-09-01_feedf00d" "$VAULT/Sources/2026-09-02_cafebabe"
printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/legacy"\ntitle: "Legacy placeholder"\ncaptured: 2026-09-01\nstatus: bookmark\n---\n# Legacy placeholder\n\nhttps://example.com/legacy\n' > "$VAULT/Sources/2026-09-01_feedf00d/source.md"
printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/legacy-ex"\ntitle: "Legacy with extract"\ncaptured: 2026-09-02\ntier: bookmark\n---\n# Legacy with extract\n\nA light card with an extract of its own.\n' > "$VAULT/Sources/2026-09-02_cafebabe/source.md"
printf -- '---\ntype: extract\ntitle: "Legacy with extract"\nurl: "https://example.com/legacy-ex"\nsource_type: article\nstatus: extracted\nrating: 5\ndomains: [trust]\ntags: [card]\ncreated: 2026-09-02\n---\n## Summary\n\nA card about trust and quotas.\n' > "$VAULT/Sources/2026-09-02_cafebabe/extract.md"
assert_eq "$(nfolders)" "6" "six revisions"

# ---------------------------------------------------------------------------------------------
echo "--- 1. list ---"
run_m triage list
assert_eq "$rc" "0" "triage list exits 0"
assert_eq "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" "5" "five bookmarks in the queue (three cards, two legacy)"
assert_eq "$(col "$bma" 2) $(col "$bma" 4) $(col "$bma" 5)" "bookmarked Card A https://example.com/a" "a url card: status · title · url"
assert_eq "$(col "$bmt" 5)" "text" "a text card says text"
assert_eq "$(col 2026-09-01_feedf00d 2) $(col 2026-09-02_cafebabe 2)" "bookmarked bookmarked" "legacy placeholders are in the queue"
assert_not_contains "$out" "$full" "a full capture is not in the queue"
assert_eq "$(printf '%s\n' "$out" | cut -f1 | tr '\n' ' ')" "$(printf '%s\n' "$out" | cut -f1 | sort -r | tr '\n' ' ')" "sorted by capture date desc, then revision desc"
run_m triage list --json
assert_eq "$rc" "0" "--json exits 0"
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["schema"], d["count"], d["counts"]["bookmarked"], d["counts"]["dismissed"], d["counts"]["promoted"])')" "mnemon-triage/1 5 5 0 0" "--json: schema, count, counts"
run_m triage list --status dismissed
assert_eq "$rc $out" "0 no dismissed bookmarks" "an empty filter says so"
run_m triage list --status bogus
assert_eq "$rc" "2" "an unknown status is a usage error"

# ---------------------------------------------------------------------------------------------
echo "--- 2. dismiss without an extract → triage.md ---"
run_m triage dismiss "$bma" --why "stale: the tab was closed"
assert_eq "$rc" "0" "dismiss exits 0"
assert_contains "$out" "dismissed Sources/$bma/ — triage.md" "...names the carrier"
assert_file_exists "$VAULT/Sources/$bma/triage.md" "triage.md written"
assert_eq "$(fm "$VAULT/Sources/$bma/triage.md" status) $(fm "$VAULT/Sources/$bma/triage.md" revision) $(fm "$VAULT/Sources/$bma/triage.md" why)" "dismissed \"$bma\" \"stale: the tab was closed\"" "triage.md: status · revision · why"
assert_eq "$(fm "$VAULT/Sources/$bma/triage.md" type)" "triage" "...type triage"
[[ -f "$VAULT/Sources/$bma/extract.md" ]] && fail "no extract.md invented" "extract.md exists" || pass "no extract.md invented for the judgement"
src_before="$(shasum -a 256 "$VAULT/Sources/$bma/source.md" | cut -c1-64)"
run_m triage list
assert_not_contains "$out" "$bma" "a dismissed card leaves the default queue"
run_m triage list --status dismissed
assert_eq "$(col "$bma" 2) $(col "$bma" 6)" "dismissed $(date +%Y-%m-%d) — stale: the tab was closed" "...and is listed under dismissed with the date and the why"
run_m triage dismiss "$bma"
assert_eq "$rc $out" "0 Sources/$bma/ is already dismissed" "dismissing twice is idempotent"
assert_eq "$(shasum -a 256 "$VAULT/Sources/$bma/source.md" | cut -c1-64)" "$src_before" "source.md is never touched"
# the gateway tells a feeder the judgement: a bookmark of a dismissed resource is a duplicate that
# says dismissed (scanners never resurrect it); a full add promotes and the line is gone
run_gw --url "https://example.com/a?utm_source=again" --tier bookmark --capture-id bm-a-again
assert_eq "$rc $(result status) $(result tier) $(result judgement)" "0 skipped-duplicate bookmark dismissed" "a bookmark of a dismissed resource: skipped-duplicate · tier bookmark · judgement dismissed"
assert_eq "$(nfolders)" "6" "...nothing written"
run_gw --url "https://example.com/b" --tier bookmark --capture-id bm-b-again
assert_eq "$rc $(result status) $(result judgement)" "0 skipped-duplicate " "a duplicate of a card in the queue carries no judgement line"
# the readers
run_m search --query "card" --include-source --tier bookmark
assert_eq "$rc" "0" "search --tier bookmark"
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); r=[x for x in d["results"] if x["path"]=="Sources/'"$bma"'/"][0]; print(r["result_type"], r["tier"], r["status"])')" "source bookmark dismissed" "search: the dismissed card is a source result with status dismissed"
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sorted(set(x["tier"] for x in d["results"])))')" "['bookmark']" "--tier bookmark keeps bookmarks only"
run_m search --query "example" --include-source --tier full
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sorted(set(x["tier"] for x in d["results"])), any(x["path"]=="Sources/'"$bma"'/" for x in d["results"]), d["count"])')" "['full'] False 1" "--tier full keeps the one full capture and drops the bookmarks"
run_m search --query "example" --include-source
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["count"])')" "5" "without --tier every revision with a URL matches (the text card has none)"
assert_eq "$(python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --status-counts | grep -E '^(bookmarks|bookmarks_dismissed|promoted)=' | tr '\n' ' ')" "bookmarks=5 bookmarks_dismissed=1 promoted=0 " "lib_vault: bookmarks 5 · dismissed 1 · promoted 0"
run_m status --brief
assert_eq "$rc" "0" "status --brief exits 0"
assert_contains "$out" " · 5 bookmarks (1 dismissed)" "the brief shows the queue"
run_m status --brief --json
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d["counters"]; print(c["tier"]["bookmark"], c["tier"]["full"], c["triage"]["bookmarked"], c["triage"]["dismissed"], c["promoted"])')" "5 1 4 1 0" "--json: tier · triage · promoted counters"

# ---------------------------------------------------------------------------------------------
echo "--- 3. restore ---"
run_m triage restore "$bma"
assert_eq "$rc" "0" "restore exits 0"
[[ -f "$VAULT/Sources/$bma/triage.md" ]] && fail "triage.md removed" "still there" || pass "triage.md removed"
run_m triage list
assert_contains "$out" "$bma" "...the card is back in the queue"
run_m triage restore "$bma"
assert_eq "$rc $out" "0 Sources/$bma/ is not dismissed" "restoring twice is idempotent"

# ---------------------------------------------------------------------------------------------
echo "--- 4. dismiss with an extract → extract.md status ---"
ex="$VAULT/Sources/2026-09-02_cafebabe/extract.md"
before="$(grep -v '^status:' "$ex")"
ex_sha="$(shasum -a 256 "$ex" | cut -c1-64)"
run_m triage dismiss 2026-09-02_cafebabe --why "duplicate of a stable note"
assert_eq "$rc" "0" "dismiss of a bookmark with an extract exits 0"
assert_contains "$out" "extract.md (status: dismissed)" "...the carrier is extract.md"
assert_eq "$(fm "$ex" status) $(fm "$ex" dismissed) $(fm "$ex" dismissed_why)" "dismissed $(date +%Y-%m-%d) \"duplicate of a stable note\"" "status: dismissed · dismissed: date · dismissed_why"
assert_eq "$(grep -v '^status:\|^dismissed:\|^dismissed_why:\|^dismissed_from:' "$ex")" "$before" "every other line of extract.md is kept"
[[ -f "$VAULT/Sources/2026-09-02_cafebabe/triage.md" ]] && fail "no triage.md beside an extract" "exists" || pass "no triage.md beside an extract"
run_m search --query "card" --include-source --tier bookmark
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); r=[x for x in d["results"] if x["path"]=="Sources/2026-09-02_cafebabe/"][0]; print(r["result_type"], r["status"])')" "extract dismissed" "search: the extract result says dismissed"
assert_eq "$(fm "$ex" dismissed_from)" "\"extracted\"" "dismissed_from records the prior word"
run_gw --url "https://example.com/legacy-ex" --tier bookmark --capture-id bm-ex-again
assert_eq "$rc $(result status) $(result judgement)" "0 skipped-duplicate dismissed" "a dismissed extract's judgement is told to a feeder too (from extract.md)"
run_m triage restore 2026-09-02_cafebabe
assert_eq "$(fm "$ex" status) [$(fm "$ex" dismissed)]" "extracted []" "restore: status extracted, the dismissed lines gone"
assert_eq "$(shasum -a 256 "$ex" | cut -c1-64)" "$ex_sha" "...the file is byte-equal to the original (reviewer A P2-8)"
# a legacy status word comes back as itself; a file without a status line gets none back
mkdir -p "$VAULT/Sources/2026-09-03_0ddba11e" "$VAULT/Sources/2026-09-04_0ddba11f"
printf -- '---\ntype: source\norigin: url\nurl: "https://example.com/legacy-processed"\ntitle: "Processed"\ncaptured: 2026-09-03\ntier: bookmark\n---\n# Processed\n' > "$VAULT/Sources/2026-09-03_0ddba11e/source.md"
printf -- '---\ntype: extract\ntitle: "Processed"\nstatus: processed\nrating: 3\n---\n## Summary\n\nold words.\n' > "$VAULT/Sources/2026-09-03_0ddba11e/extract.md"
printf -- '---\ntype: source\norigin: url\nurl: "https://example.com/legacy-nostatus"\ntitle: "No status"\ncaptured: 2026-09-04\ntier: bookmark\n---\n# No status\n' > "$VAULT/Sources/2026-09-04_0ddba11f/source.md"
printf -- '---\ntype: extract\ntitle: "No status"\nrating: 2\n---\n## Summary\n\nno status line.\n' > "$VAULT/Sources/2026-09-04_0ddba11f/extract.md"
sha_p="$(shasum -a 256 "$VAULT/Sources/2026-09-03_0ddba11e/extract.md" | cut -c1-64)"
sha_n="$(shasum -a 256 "$VAULT/Sources/2026-09-04_0ddba11f/extract.md" | cut -c1-64)"
run_m triage dismiss 2026-09-03_0ddba11e
assert_eq "$(fm "$VAULT/Sources/2026-09-03_0ddba11e/extract.md" status) $(fm "$VAULT/Sources/2026-09-03_0ddba11e/extract.md" dismissed_from)" "dismissed \"processed\"" "a legacy word is recorded in dismissed_from"
run_m triage dismiss 2026-09-04_0ddba11f
assert_eq "$(fm "$VAULT/Sources/2026-09-04_0ddba11f/extract.md" status) $(fm "$VAULT/Sources/2026-09-04_0ddba11f/extract.md" dismissed_from)" "dismissed \"\"" "a file without a status line gets one appended and dismissed_from empty"
run_m triage restore 2026-09-03_0ddba11e
assert_eq "$rc $(shasum -a 256 "$VAULT/Sources/2026-09-03_0ddba11e/extract.md" | cut -c1-64)" "0 $sha_p" "restore brings `processed` back, byte for byte"
run_m triage restore 2026-09-04_0ddba11f
assert_eq "$rc $(shasum -a 256 "$VAULT/Sources/2026-09-04_0ddba11f/extract.md" | cut -c1-64)" "0 $sha_n" "restore drops the appended status line, byte for byte"
# a CRLF extract.md is refused, nothing written
mkdir -p "$VAULT/Sources/2026-09-05_c1f0c1f0"
printf -- '---\ntype: source\norigin: url\nurl: "https://example.com/crlf"\ntitle: "CRLF"\ncaptured: 2026-09-05\ntier: bookmark\n---\n# CRLF\n' > "$VAULT/Sources/2026-09-05_c1f0c1f0/source.md"
printf -- '---\r\ntype: extract\r\nstatus: extracted\r\n---\r\nbody\r\n' > "$VAULT/Sources/2026-09-05_c1f0c1f0/extract.md"
sha_c="$(shasum -a 256 "$VAULT/Sources/2026-09-05_c1f0c1f0/extract.md" | cut -c1-64)"
run_m triage dismiss 2026-09-05_c1f0c1f0
assert_eq "$rc $(shasum -a 256 "$VAULT/Sources/2026-09-05_c1f0c1f0/extract.md" | cut -c1-64)" "1 $sha_c" "a CRLF extract.md is refused (exit 1) and untouched"
assert_contains "$err" "CRLF" "...saying why"

# ---------------------------------------------------------------------------------------------
echo "--- 5. the judgement on a full revision (tier full, GH #30 / O6); refusals and scope ---"
fex="$VAULT/Sources/$full/extract.md"
fex_sha="$(shasum -a 256 "$fex" | cut -c1-64)"
run_m triage dismiss "$full" --why "read it, not for the library"
assert_eq "$rc" "0" "dismiss of a full capture exits 0 (the judgement is about the resource, whatever its tier)"
assert_contains "$out" "dismissed Sources/$full/ — extract.md (status: dismissed)" "...the carrier is its extract.md"
assert_eq "$(fm "$fex" status) $(fm "$fex" dismissed_from) $(fm "$fex" dismissed_why)" "dismissed \"extracted\" \"read it, not for the library\"" "status: dismissed · dismissed_from: extracted · dismissed_why"
[[ -f "$VAULT/Sources/$full/triage.md" ]] && fail "no triage.md beside a full extract" "exists" || pass "no triage.md beside a full extract"
run_m triage list
assert_not_contains "$out" "$full" "a dismissed full revision is not in the default (bookmarked) queue"
run_m triage list --status dismissed
assert_eq "$(col "$full" 2)" "dismissed" "...but list --status dismissed shows it"
run_m triage list --status all --json
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); r=[x for x in d["items"] if x["revision"]==sys.argv[1]][0]; print(r["status"], r["tier"], r["has_extract"])' "$full")" "dismissed full True" "--json: status dismissed · tier full"
run_gw --url "https://example.com/full" --capture-id full-again
assert_eq "$rc $(result status) $(result judgement)" "0 skipped-duplicate dismissed" "a new add of the dismissed resource: skipped-duplicate · RESULT:judgement=dismissed"
assert_eq "$(python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --status-counts | grep -E '^(status_dismissed|bookmarks_dismissed)=' | tr '\n' ' ')" "status_dismissed=1 bookmarks_dismissed=0 " "lib_vault: counted as an extract status, not as a dismissed bookmark"
run_m triage restore "$full"
assert_eq "$rc $(fm "$fex" status)" "0 extracted" "restore brings extracted back"
assert_eq "$(shasum -a 256 "$fex" | cut -c1-64)" "$fex_sha" "...byte for byte"
run_m triage list --status dismissed
assert_eq "$out" "no dismissed bookmarks" "...and the dismissed list is empty again"
# a full revision without extract.md is an unfinished capture, not a carrier: refused with the resume door
mkdir -p "$VAULT/Sources/2026-09-08_unf1n15h"
printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/unfinished"\ntitle: "Unfinished"\ncaptured: 2026-09-08\n---\n# Unfinished\n\nbody without an extract.\n' > "$VAULT/Sources/2026-09-08_unf1n15h/source.md"
run_m triage dismiss 2026-09-08_unf1n15h
assert_eq "$rc" "2" "dismiss of a full revision without extract.md is refused (exit 2)"
assert_contains "$err" "mnemon add --resume 2026-09-08_unf1n15h" "...naming the resume door"
[[ -f "$VAULT/Sources/2026-09-08_unf1n15h/triage.md" ]] && fail "no triage.md on a full revision" "exists" || pass "...no triage.md written"
rm -rf "$VAULT/Sources/2026-09-08_unf1n15h"
run_m triage promote "$full"
assert_eq "$rc" "2" "promote of a full capture is refused (exit 2)"
assert_contains "$err" "is not a bookmark" "...with the reason (promote is for cards)"
run_m triage dismiss 2026-01-01_00000000
assert_eq "$rc" "2" "an unknown revision is exit 2"
run_m triage dismiss
assert_eq "$rc" "2" "dismiss without a revision is a usage error"
# the single-writer rule: a declared writer_host that is not this host refuses the write (Codex P2-2)
sed "s/^instance: captest$/instance: captest-reader/" "$CONFIG" > "$TEST_TMPDIR/reader.yaml"
printf 'writer_host: some-other-host\n' >> "$TEST_TMPDIR/reader.yaml"
rc=0; out="$(bash -c '"$@"' _ "$MNEMON" --config "$TEST_TMPDIR/reader.yaml" triage dismiss "$bmb" 2>&1)" || rc=$?
assert_eq "$rc" "2" "dismiss on a host that is not the writer_host is exit 2"
assert_contains "$out" "writer_host some-other-host" "...naming the writer"
[[ -f "$VAULT/Sources/$bmb/triage.md" ]] && fail "nothing written by a non-writer host" "triage.md exists" || pass "nothing written by a non-writer host"
rc=0; out="$(bash -c '"$@"' _ "$MNEMON" --config "$TEST_TMPDIR/reader.yaml" triage restore "$bma" 2>&1)" || rc=$?
assert_eq "$rc" "2" "restore on a host that is not the writer_host is exit 2 too"
# the doctor's data checks on this vault: Q1 and Q3 leave the legacy placeholder (status: bookmark, 8 words) alone
run_m doctor --data --json
assert_eq "$rc" "0" "doctor --data --json on the vault exits 0"
assert_eq "$(printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]   # the combined envelope: the data document sits under "data"
checks = {c["name"]: c for c in d["checks"]}
thin = [i["id"] for i in checks["data/thin"]["items"]]
stubs = [i["id"] for i in checks["data/stubs"]["items"]]
print("2026-09-01_feedf00d" in thin, "2026-09-01_feedf00d" in stubs, checks["data/stubs"]["counts"]["bookmarks_excluded"] >= 1, d["counters"]["tier"]["other"])
')" "False False True 0" "Q1 and Q3 exclude the legacy status: bookmark placeholder (effective tier); tier.other is 0"
# a foreign tier word is counted apart, never as full or bookmark
mkdir -p "$VAULT/Sources/2026-09-07_0ther0th"
printf -- '---\ntype: source\norigin: url\nurl: "https://example.com/other-tier"\ntitle: "Other tier"\ncaptured: 2026-09-07\ntier: snapshot\n---\n# Other tier\n' > "$VAULT/Sources/2026-09-07_0ther0th/source.md"
assert_eq "$(python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --json | python3 -c 'import json,sys; t=json.load(sys.stdin)["counters"]["tier"]; print(t["other"], t["full"] + t["bookmark"] + t["other"])')" "1 $(nfolders)" "tier.other counts a foreign tier word; full + bookmark + other = the folders with a source"
run_m triage list
assert_not_contains "$out" "2026-09-07_0ther0th" "...and it is not in the bookmark queue"
rm -rf "$VAULT/Sources/2026-09-07_0ther0th"
# the lock: a writer holding the vault lock makes dismiss exit 75
python3 "$MNEMON_ROOT/bin/vault-lock.py" --vault "$VAULT" -- bash -c 'sleep 3' &
holder=$!
sleep 0.5
run_m triage dismiss "$bmb"
assert_eq "$rc" "75" "dismiss under a held vault lock exits 75"
wait "$holder" 2>/dev/null || true
[[ -f "$VAULT/Sources/$bmb/triage.md" ]] && fail "nothing written under the held lock" "triage.md exists" || pass "nothing written under the held lock"

# ---------------------------------------------------------------------------------------------
echo "--- 6. promote ---"
run_m triage promote "$bmb" --dry-run
assert_eq "$rc" "0" "promote --dry-run exits 0"
assert_contains "$out" "add --url https://example.com/b --title Card B --context personal" "...prints the mnemon add command from the card"
stub_reset
run_m triage promote "$bmb"
assert_eq "$rc" "0" "promote exits 0"
assert_contains "$out" "RESULT:status=extracted" "...the resource is captured in full"
newb="$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)"
assert_eq "$(fm "$VAULT/Sources/$newb/source.md" supersedes)" "\"$bmb\"" "the new revision supersedes the card"
assert_contains "$err" "promoting it" "...through the gateway's promotion"
assert_eq "$(stub_calls)" "1" "one extractor call"
run_m triage list
assert_not_contains "$out" "$bmb" "a promoted card leaves the queue"
run_m triage list --status promoted
assert_eq "$(col "$bmb" 2)" "promoted" "...and is listed under promoted"
run_m triage dismiss "$bmb"
assert_eq "$rc" "2" "dismissing a superseded card is refused"
assert_contains "$err" "latest revision: $newb" "...naming the head"
# after two refreshes the head named is the current one, not the direct successor (reviewer A P2-12)
run_gw --url "https://example.com/refresh" --tier bookmark --title "R1" --capture-id bm-r1
r1="$(result revision)"
run_gw --url "https://example.com/refresh" --tier bookmark --recapture --title "R2" --capture-id bm-r2
run_gw --url "https://example.com/refresh" --tier bookmark --recapture --title "R3" --capture-id bm-r3
r3="$(result revision)"
run_m triage dismiss "$r1"
assert_eq "$rc" "2" "dismissing a twice-refreshed card is refused"
assert_contains "$err" "latest revision: $r3" "...naming the current revision after two refreshes"
# a legacy card whose tier is only in extract.md is a bookmark for every reader and promotes (reviewer A P2-5)
mkdir -p "$VAULT/Sources/2026-09-06_e1e1e1e1"
printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/extract-tier"\ntitle: "Extract-tier card"\ncaptured: 2026-09-06\n---\n# Extract-tier card\n\nhttps://example.com/extract-tier\n' > "$VAULT/Sources/2026-09-06_e1e1e1e1/source.md"
printf -- '---\ntype: extract\ntitle: "Extract-tier card"\nstatus: bookmark\ntier: bookmark\n---\n## Summary\n\na light card.\n' > "$VAULT/Sources/2026-09-06_e1e1e1e1/extract.md"
run_m triage list
assert_contains "$out" "2026-09-06_e1e1e1e1" "an extract-tier legacy card is in the queue"
stub_reset
run_m triage promote 2026-09-06_e1e1e1e1
assert_eq "$rc" "0" "...and promotes"
assert_contains "$out" "RESULT:status=extracted" "...in full"
assert_eq "$(fm "$VAULT/Sources/$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)/source.md" supersedes)" "\"2026-09-06_e1e1e1e1\"" "...superseding the legacy card"
# a text card promotes through its own body — on stdin, nothing left in $TMPDIR (reviewer A P2-4)
stub_reset
tmp_before="$(/bin/ls "${TMPDIR:-/tmp}" 2>/dev/null | grep -c '^mnemon-promote-' || true)"
run_m triage promote "$bmt" --dry-run
assert_contains "$out" "add --origin text --title Text card --context personal" "the dry-run shows the text card's command"
assert_contains "$out" "< <the card's text on stdin>" "...with the text on stdin, no --file-text"
run_m triage promote "$bmt"
assert_eq "$rc" "0" "promote of a text card exits 0"
newt="$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)"
assert_eq "$(fm "$VAULT/Sources/$newt/source.md" supersedes) $(fm "$VAULT/Sources/$newt/source.md" origin)" "\"$bmt\" text" "the text card is superseded by a full text capture"
assert_eq "$(/bin/ls "${TMPDIR:-/tmp}" 2>/dev/null | grep -c '^mnemon-promote-' || true)" "$tmp_before" "no temp file left in TMPDIR"
assert_contains "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["input_path"])' "$VSTATE/captures/$(fm "$VAULT/Sources/$newt/source.md" capture_id | tr -d '"').json")" "/attempt-1/capture.md" "the input locator is the gateway's own (runs/<id>/attempt-1/capture.md)"
# an edited text card is integrity-error, never promoted as an unrelated resource (Codex P2-1)
printf 'another text to bookmark\n' > "$TEST_TMPDIR/t2.txt"
run_gw_stdin "$TEST_TMPDIR/t2.txt" --origin text --tier bookmark --title "Edited card" --capture-id bm-t2
bmt2="$(result revision)"
printf 'edited by hand\n' >> "$VAULT/Sources/$bmt2/source.md"
n_before="$(nfolders)"
run_m triage promote "$bmt2"
assert_eq "$rc" "1" "promote of an edited text card exits 1"
assert_contains "$err" "integrity-error" "...integrity-error"
assert_eq "$(nfolders)" "$n_before" "...nothing captured"
# a duplicated flag in either form is not repeated
run_m triage promote "$bmb" --dry-run --title=Mine
assert_not_contains "$out" "--title Card B" "a --title=… given by the caller outranks the card's title"
# a dismissed card promoted: the judgement lapses with the superseded carrier
run_m triage dismiss "$bma" --why "not now"
stub_reset
run_m triage promote "$bma" -- --intent "second thoughts"
assert_eq "$rc" "0" "promote of a dismissed card exits 0 (flags after -- reach mnemon add)"
assert_not_contains "$out" "RESULT:judgement=" "...the promoted revision carries no judgement (it lapsed with the card)"
newa="$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)"
assert_eq "$(fm "$VAULT/Sources/$newa/source.md" supersedes)" "\"$bma\"" "...superseding the dismissed card"
run_m triage list --status dismissed
assert_eq "$out" "no dismissed bookmarks" "the judgement lapsed with the card"
assert_eq "$(python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --status-counts | grep -E '^(bookmarks|promoted)=' | tr '\n' ' ')" "bookmarks=7 promoted=4 " "lib_vault: 7 in the queue (the placeholder, four legacy cards, the refreshed card, the edited card), 4 promoted"
# the legacy placeholder promotes too
stub_reset
run_m triage promote 2026-09-01_feedf00d
assert_eq "$rc" "0" "a legacy placeholder promotes"
assert_eq "$(fm "$VAULT/Sources/$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)/source.md" supersedes)" "\"2026-09-01_feedf00d\"" "...superseded by the full capture"

# ---------------------------------------------------------------------------------------------
echo "--- 7. the URL-list triage spike is retired (GH #29): any other verb is a usage error ---"
printf 'https://example.com/list\n' > "$TEST_TMPDIR/urls.txt"
run_m triage "$TEST_TMPDIR/urls.txt"
assert_eq "$rc" "2" "triage <file> exits 2"
assert_contains "$err" "triage: expected list | dismiss <rev> | restore <rev> | promote <rev>" "...naming the four verbs"
assert_contains "$err" "retired" "...and saying the URL-list triage was retired"
run_m triage --help
assert_eq "$rc" "2" "triage --help is no longer the spike's usage (exit 2, the dispatcher's message)"

cleanup_capture_test
summary
