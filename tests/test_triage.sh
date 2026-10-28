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
run_m triage dismiss 2026-09-02_cafebabe --why "duplicate of a stable note"
assert_eq "$rc" "0" "dismiss of a bookmark with an extract exits 0"
assert_contains "$out" "extract.md (status: dismissed)" "...the carrier is extract.md"
assert_eq "$(fm "$ex" status) $(fm "$ex" dismissed) $(fm "$ex" dismissed_why)" "dismissed $(date +%Y-%m-%d) \"duplicate of a stable note\"" "status: dismissed · dismissed: date · dismissed_why"
assert_eq "$(grep -v '^status:\|^dismissed:\|^dismissed_why:' "$ex")" "$before" "every other byte of extract.md is kept"
[[ -f "$VAULT/Sources/2026-09-02_cafebabe/triage.md" ]] && fail "no triage.md beside an extract" "exists" || pass "no triage.md beside an extract"
run_m search --query "card" --include-source --tier bookmark
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); r=[x for x in d["results"] if x["path"]=="Sources/2026-09-02_cafebabe/"][0]; print(r["result_type"], r["status"])')" "extract dismissed" "search: the extract result says dismissed"
run_m triage restore 2026-09-02_cafebabe
assert_eq "$(fm "$ex" status) [$(fm "$ex" dismissed)]" "extracted []" "restore: status extracted, the dismissed lines gone"
assert_eq "$(grep -v '^status:' "$ex")" "$before" "...and the rest byte-equal"

# ---------------------------------------------------------------------------------------------
echo "--- 5. refusals and the scope of the judgement ---"
run_m triage dismiss "$full"
assert_eq "$rc" "2" "dismissing a full capture is refused (exit 2)"
assert_contains "$err" "is not a bookmark" "...with the reason"
run_m triage dismiss 2026-01-01_00000000
assert_eq "$rc" "2" "an unknown revision is exit 2"
run_m triage restore "$full"
assert_eq "$rc" "2" "restore of a full capture is exit 2"
run_m triage dismiss
assert_eq "$rc" "2" "dismiss without a revision is a usage error"
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
assert_contains "$err" "superseded by $newb" "...naming the head"
# a text card promotes through its own body
stub_reset
run_m triage promote "$bmt"
assert_eq "$rc" "0" "promote of a text card exits 0"
newt="$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)"
assert_eq "$(fm "$VAULT/Sources/$newt/source.md" supersedes) $(fm "$VAULT/Sources/$newt/source.md" origin)" "\"$bmt\" text" "the text card is superseded by a full text capture"
# a dismissed card promoted: the judgement lapses with the superseded carrier
run_m triage dismiss "$bma" --why "not now"
stub_reset
run_m triage promote "$bma" -- --intent "second thoughts"
assert_eq "$rc" "0" "promote of a dismissed card exits 0 (flags after -- reach mnemon add)"
newa="$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)"
assert_eq "$(fm "$VAULT/Sources/$newa/source.md" supersedes)" "\"$bma\"" "...superseding the dismissed card"
run_m triage list --status dismissed
assert_eq "$out" "no dismissed bookmarks" "the judgement lapsed with the card"
assert_eq "$(python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --status-counts | grep -E '^(bookmarks|promoted)=' | tr '\n' ' ')" "bookmarks=2 promoted=3 " "lib_vault: 2 in the queue, 3 promoted"
# the legacy placeholder promotes too
stub_reset
run_m triage promote 2026-09-01_feedf00d
assert_eq "$rc" "0" "a legacy placeholder promotes"
assert_eq "$(fm "$VAULT/Sources/$(printf '%s\n' "$out" | grep '^RESULT:revision=' | cut -d= -f2)/source.md" supersedes)" "\"2026-09-01_feedf00d\"" "...superseded by the full capture"

# ---------------------------------------------------------------------------------------------
echo "--- 7. the URL-list triage is untouched ---"
run_m triage --help
assert_eq "$rc" "0" "triage --help still answers (triage-urls.py)"
assert_contains "$out" "--vault" "...its own usage"

cleanup_capture_test
summary
