#!/usr/bin/env bash
# test_tier.sh — the capture tiers (phase 3-capture, capture layer §13 B1–B3): the bookmark card,
# dedup and promotion, --canonical-url for a file or a text, the batch keys, replays and refusals.
# Everything runs against the stubs of tests/fixtures/capture/setup.sh: no network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Capture Tier Tests ==="

# ---------------------------------------------------------------------------------------------
echo "--- 1. the bookmark card of a URL ---"
stub_reset
run_gw --url "https://example.com/read-later?utm_source=tab" --tier bookmark --title "Read later" --intent "scanner: safari tabs" --capture-id bm-1
assert_eq "$rc" "0" "a bookmark exits 0"
assert_eq "$(result status) $(result extract) $(result tier)" "bookmarked none bookmark" "RESULT: bookmarked · extract none · tier bookmark"
rev="$(result revision)"
src="$VAULT/Sources/$rev/source.md"
assert_file_exists "$src" "source.md is published"
[[ -f "$VAULT/Sources/$rev/extract.md" ]] && fail "no extract.md" "extract.md exists" || pass "no extract.md for a bookmark"
assert_eq "$(fm "$src" tier)" "bookmark" "source.md carries tier: bookmark"
assert_eq "$(fm "$src" url)" "\"https://example.com/read-later?utm_source=tab\"" "the literal url is kept"
assert_eq "$(fm "$src" canonical_url)" "\"https://example.com/read-later\"" "canonical_url is the computed identity (tracking stripped)"
assert_eq "$(result canonical_url)" "https://example.com/read-later" "RESULT:canonical_url printed"
assert_eq "$(capfm "$src" method) $(capfm "$src" fidelity) $(capfm "$src" raw_store) $(capfm "$src" completeness)" "\"bookmark\" exact none unknown" "capture block: method bookmark · fidelity exact · raw none · completeness unknown"
assert_eq "$(fm "$src" title)" "\"Read later\"" "title from the request"
python3 - "$src" <<'PY' && pass "the body is the card: title, url, intent" || fail "the body is the card" "differs"
import sys
d = open(sys.argv[1], "rb").read(); i = d.find(b"\n---\n", 3)
want = b"# Read later\n\nhttps://example.com/read-later?utm_source=tab\n\nscanner: safari tabs\n"
sys.exit(0 if d[i + 5:] == want else 1)
PY
assert_eq "$(capfm "$src" text_sha256)" "\"$(body_sha "$src")\"" "capture.text_sha256 == the card's hash"
assert_eq "$(fetch_calls) $(stub_calls)" "0 0" "no fetch and no extractor call for a card"
assert_eq "$(rget bm-1 '.state') $(rget bm-1 '.request.tier') $(rget bm-1 '.request.recapture')" "bookmarked bookmark false" "receipt: state bookmarked, request.tier bookmark"
assert_eq "$(rget bm-1 '.finalized_at != null')" "true" "...finalized"
assert_contains "$(result reindex)" "ok" "the reindex hook ran"
assert_contains "$(cat "$VAULT/index.md")" "1 bookmark(s) without an extract" "index.md notes the bookmark (not as pending)"
assert_not_contains "$(cat "$VAULT/index.md")" "captured without extract.md" "...and not as a source captured without extract"
[[ -d "$VSTATE/runs/bm-1" ]] && fail "attempt dir cleaned" "runs/bm-1 remains" || pass "the attempt dir is cleaned after the final receipt"

# a full request without --tier keeps its request hash (tier is bound only when bookmark)
run_gw --url "https://example.com/full-1" --capture-id full-1
assert_eq "$rc $(result status)" "0 extracted" "a full capture still extracts"
assert_eq "$(rget full-1 '.request | has("tier")')" "false" "a full request carries no tier key (old receipts keep their hash)"
assert_eq "$(result tier)" "" "no RESULT:tier line for a full revision"

# ---------------------------------------------------------------------------------------------
echo "--- 2. dedup: a bookmark adds nothing to a resource the vault holds ---"
n0="$(nfolders)"
run_gw --url "https://example.com/read-later?utm_source=other" --tier bookmark --capture-id bm-2
assert_eq "$rc $(result status) $(result tier)" "0 skipped-duplicate bookmark" "a second bookmark of the same resource is a skipped-duplicate that names the tier"
assert_eq "$(result revision)" "$rev" "...of the bookmark revision"
assert_eq "$(nfolders)" "$n0" "...nothing written"
assert_contains "$err" "skipping the bookmark" "...with the hint"
run_gw --url "https://example.com/full-1" --tier bookmark --capture-id bm-3
assert_eq "$rc $(result status) $(result tier)" "0 skipped-duplicate " "a bookmark of a fully captured resource is a skipped-duplicate (no tier line: the head is full)"
# a bookmark never resumes an incomplete full revision
mkdir -p "$VAULT/Sources/2026-10-01_deadbeef"
printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/pending"\ntitle: "Pending"\ncaptured: 2026-10-01\n---\n# Pending\n\nhello\n' > "$VAULT/Sources/2026-10-01_deadbeef/source.md"
run_gw --url "https://example.com/pending" --tier bookmark --capture-id bm-4
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate 2026-10-01_deadbeef" "a bookmark of a captured-without-extract resource is a skipped-duplicate, never a resume"
assert_eq "$(stub_calls)" "1" "...no extractor call (the one call so far is full-1)"

# ---------------------------------------------------------------------------------------------
echo "--- 3. promotion: a full capture of a bookmarked resource supersedes the card ---"
stub_reset
run_gw --url "https://example.com/read-later" --capture-id promo-1
assert_eq "$rc $(result status)" "0 extracted" "a full add of a bookmarked URL extracts"
rev2="$(result revision)"
[[ "$rev2" != "$rev" ]] && pass "a new revision ($rev2)" || fail "a new revision" "same folder $rev"
assert_eq "$(fm "$VAULT/Sources/$rev2/source.md" supersedes)" "\"$rev\"" "the new revision supersedes the bookmark"
assert_eq "$(fm "$VAULT/Sources/$rev2/source.md" tier)" "" "...and is a full capture (no tier line)"
assert_contains "$err" "promoting it" "...with a NOTE"
assert_eq "$(rget promo-1 '.request.recapture') $(rget promo-1 '.request.supersedes')" "true $rev" "the receipt binds the implicit recapture/supersedes"
assert_eq "$(result tier)" "" "no tier line for the promoted (full) revision"
assert_eq "$(fetch_calls) $(stub_calls)" "1 1" "one fetch, one extractor call"
# the same full request replayed without --recapture is the same request (B2 binding)
stub_reset
run_gw --url "https://example.com/read-later" --capture-id promo-1
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "replaying the promotion with its id is a replay, not a binding mismatch"
assert_eq "$(stub_calls)" "0" "...nothing re-run"
# an explicit --supersedes of ANOTHER resource's card is not a promotion: its replay without the flag
# is a different request (reviewer A P2-10)
run_gw --url "https://example.com/card-n" --tier bookmark --capture-id bm-n
cardn="$(result revision)"
run_gw --url "https://example.com/n-other" --supersedes "$cardn" --capture-id sup-n
assert_eq "$rc $(result status)" "0 extracted" "an explicit --supersedes of another resource's card captures"
run_gw --url "https://example.com/n-other" --capture-id sup-n
assert_eq "$rc" "2" "...replayed without --supersedes it is a different request (exit 2), not an adopted promotion"
# the counters see the promotion
assert_eq "$(python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --status-counts | grep '^promoted=' | cut -d= -f2)" "2" "lib_vault counts promoted=2 (the promotion, and the card an explicit --supersedes gave a full descendant)"
# a bookmark of a promoted resource is a duplicate of the full head
run_gw --url "https://example.com/read-later" --tier bookmark --capture-id bm-5
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $rev2" "a bookmark after the promotion is a duplicate of the full head"

# a legacy placeholder (`status: bookmark` in source.md, no managed keys) promotes too
mkdir -p "$VAULT/Sources/2026-09-01_feedf00d"
printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://example.com/legacy-bm"\ntitle: "Legacy bookmark"\ncaptured: 2026-09-01\nstatus: bookmark\n---\n# Legacy bookmark\n\nhttps://example.com/legacy-bm\n' > "$VAULT/Sources/2026-09-01_feedf00d/source.md"
stub_reset
run_gw --url "https://example.com/legacy-bm" --capture-id promo-2
assert_eq "$rc $(result status)" "0 extracted" "a full add of a legacy placeholder captures in full"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" "\"2026-09-01_feedf00d\"" "...superseding the placeholder"

# ---------------------------------------------------------------------------------------------
echo "--- 4. --recapture --tier bookmark ---"
run_gw --url "https://example.com/full-1" --tier bookmark --recapture --capture-id bm-6
assert_eq "$rc" "2" "a bookmark never supersedes a full capture (exit 2)"
assert_contains "$err" "never supersedes" "...with the reason"
run_gw --url "https://example.com/card-2" --tier bookmark --title "Card v1" --capture-id bm-7
assert_eq "$rc $(result status)" "0 bookmarked" "a card"
c1="$(result revision)"
run_gw --url "https://example.com/card-2" --tier bookmark --recapture --title "Card v2" --capture-id bm-8
assert_eq "$rc $(result status) $(result tier)" "0 bookmarked bookmark" "--recapture --tier bookmark refreshes a card"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" "\"$c1\"" "...the new card supersedes the old one"

# ---------------------------------------------------------------------------------------------
echo "--- 5. replay, resume and refusals ---"
stub_reset
run_gw --url "https://example.com/read-later?utm_source=tab" --tier bookmark --title "Read later" --intent "scanner: safari tabs" --capture-id bm-1
assert_eq "$rc $(result status) $(result replayed) $(result revision)" "0 bookmarked true $rev" "replaying a bookmarked receipt answers from the files"
assert_eq "$(fetch_calls) $(stub_calls)" "0 0" "...nothing acquired or extracted"
run_gw --url "https://example.com/read-later?utm_source=tab" --title "Read later" --intent "scanner: safari tabs" --capture-id bm-1
assert_eq "$rc" "2" "replaying a bookmark id as a full request is a different request (exit 2)"
assert_contains "$err" "capture-id reuse with a different request" "...binding mismatch"
run_gw --resume "$c1"
assert_eq "$rc" "2" "--resume of a bookmark revision is exit 2"
assert_contains "$err" "is a bookmark (nothing to finish)" "...nothing to finish, promote instead"
run_gw --resume bm-7
assert_eq "$rc" "2" "--resume of a bookmarked receipt is exit 2"
run_gw --tier bookmark --resume "$c1"
assert_eq "$rc" "2" "--tier bookmark with --resume is exit 2"
printf 'pdf bytes\n' > "$TEST_TMPDIR/a.pdf"
run_gw --file "$TEST_TMPDIR/a.pdf" --tier bookmark --capture-id bm-9
assert_eq "$rc" "2" "a bookmark of a file is refused (exit 2)"
assert_contains "$err" "takes a URL or a text" "...with the rule"
run_gw --url "https://example.com/x" --tier bogus
assert_eq "$rc" "2" "--tier bogus is a usage error"
run_gw --url "https://example.com/landing" --tier bookmark --site auto
assert_eq "$rc" "2" "--tier bookmark with --site is exit 2"
# a crash after the receipt, before publication, is recovered as a card on the next run
stub_reset
MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-source-staged run_gw --url "https://example.com/crash-card" --tier bookmark --capture-id bm-10 || true
assert_eq "$(rget bm-10 '.state')" "started" "crash before publication leaves the receipt started"
assert_eq "$(rget bm-10 '.raw_store') $(rget bm-10 '.raw_path') $(rget bm-10 '.raw_staged_path')" "none null null" "a card's receipt records no raw and no staged path (reviewer A P2-6)"
run_gw --url "https://example.com/crash-card" --tier bookmark --capture-id bm-10
assert_eq "$rc $(result status) $(result replayed)" "0 bookmarked true" "the replay finishes the card"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" tier)" "bookmark" "...as a bookmark"
assert_eq "$(fetch_calls) $(stub_calls)" "0 0" "...without fetch or extractor"
assert_eq "$(staging_left)" "0" "no staging left"
# a crash AFTER the publication (receipt started or source-published, the card on disk) is finished as
# bookmarked on replay — never extracted (reviewers A/B P1-2)
for hook in after-source-published after-bookmark-published; do
  stub_reset
  MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=$hook run_gw --url "https://example.com/crash-$hook" --tier bookmark --capture-id "bm-$hook" || true
  crev="$(rget "bm-$hook" '.revision')"
  assert_file_exists "$VAULT/Sources/$crev/source.md" "$hook: the card is published"
  [[ "$(rget "bm-$hook" '.state')" != "bookmarked" ]] && pass "$hook: the receipt is not final ($(rget "bm-$hook" '.state'))" || fail "$hook: the receipt is not final" "already bookmarked"
  run_gw --url "https://example.com/crash-$hook" --tier bookmark --capture-id "bm-$hook"
  assert_eq "$rc $(result status) $(result replayed) $(result revision)" "0 bookmarked true $crev" "$hook: the replay finishes the card as bookmarked"
  assert_eq "$(rget "bm-$hook" '.state') $(stub_calls)" "bookmarked 0" "$hook: the receipt is final, no extractor call"
  [[ -f "$VAULT/Sources/$crev/extract.md" ]] && fail "$hook: no extract.md beside the card" "extract.md written" || pass "$hook: no extract.md beside the card"
  run_gw --resume "bm-$hook"
  assert_eq "$rc" "2" "$hook: --resume of the card's receipt is exit 2"
done
# a crash after the publication, then a replay through an alias (another id for the same card)
stub_reset
MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-source-published run_gw --url "https://example.com/crash-alias" --tier bookmark --capture-id bm-alias-owner || true
run_gw --url "https://example.com/crash-alias" --tier bookmark --capture-id bm-alias-2
assert_eq "$rc $(result status) $(stub_calls)" "0 bookmarked 0" "a second id for an unfinished card finishes it as bookmarked through the alias"
assert_contains "$err" "finishing it as bookmarked" "...with the NOTE"
assert_eq "$(rget bm-alias-owner '.state') $(rget bm-alias-2 '.resolved_to_revision')" "bookmarked $(rget bm-alias-owner '.revision')" "the owner's receipt is final; the alias resolves to the card"
[[ -f "$VAULT/Sources/$(rget bm-alias-owner '.revision')/extract.md" ]] && fail "no extract through the alias" "written" || pass "no extract through the alias"
run_gw --url "https://example.com/crash-alias" --tier bookmark --capture-id bm-alias-2
assert_eq "$rc $(result status) $(result replayed) $(result tier)" "0 skipped-duplicate true bookmark" "the alias (no attempt of its own) replays as a duplicate of the finished card"
run_gw --url "https://example.com/crash-alias" --tier bookmark --capture-id bm-alias-3
assert_eq "$rc $(result status) $(result tier) $(rget bm-alias-3 '.state')" "0 skipped-duplicate bookmark bookmarked" "a third id is a duplicate of the finished card, its alias receipt bookmarked"
# an unfinished card found without any id is finished under its owner's receipt
stub_reset
MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-source-published run_gw --url "https://example.com/crash-noid" --tier bookmark --capture-id bm-noid || true
run_gw --url "https://example.com/crash-noid" --tier bookmark
assert_eq "$rc $(result status) $(result capture_id) $(rget bm-noid '.state')" "0 bookmarked bm-noid bookmarked" "without an id the owner's receipt finishes the card"
# an explicit --supersedes of a full capture with --tier bookmark is refused (reviewer A P1-3)
run_gw --url "https://example.com/other-y" --tier bookmark --supersedes "$rev2" --capture-id bm-sup
assert_eq "$rc" "2" "--tier bookmark --supersedes <full revision> is exit 2"
assert_contains "$err" "a bookmark never supersedes it" "...with the rule"
[[ -f "$(receipt bm-sup)" ]] && fail "nothing written on the refusal" "receipt exists" || pass "nothing written on the refusal"
# a replay of a --file capture after a pre-acquisition failure keeps its input (reviewers A/B P1-1)
printf '# A file\n\nfile text for the replay\n' > "$TEST_TMPDIR/replay.md"
stub_reset
rm -f "$XDG_STATE_HOME/mnemon/fence-probes.json"
run_gw --file "$TEST_TMPDIR/replay.md" --capture-id file-replay
assert_eq "$rc $(result error_class)" "3 fence-unprobed" "a --file capture fails at the preflight without a probe record"
write_probe_record
run_gw --file "$TEST_TMPDIR/replay.md" --capture-id file-replay
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "...and its replay captures the file (the locator is restored from the receipt)"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" origin_path)" "\"$TEST_TMPDIR/replay.md\"" "...from the recorded file"
assert_eq "$(fetch_calls)" "0" "...without any fetch"
printf 'pdf bytes\n' > "$TEST_TMPDIR/replay.pdf"
rm -f "$XDG_STATE_HOME/mnemon/fence-probes.json"
run_gw --file "$TEST_TMPDIR/replay.pdf" --canonical-url "https://docs.example.com/p.pdf" --capture-id pdf-replay
assert_eq "$rc $(result error_class)" "3 fence-unprobed" "a --file pdf with --canonical-url fails at the preflight"
write_probe_record
stub_reset
run_gw --file "$TEST_TMPDIR/replay.pdf" --canonical-url "https://docs.example.com/p.pdf" --capture-id pdf-replay
assert_eq "$(result replayed) $(fetch_calls)" "true 0" "...its replay reads the file, never fetches the canonical URL"

# ---------------------------------------------------------------------------------------------
echo "--- 6. a text bookmark ---"
printf 'a note pasted from a chat\nsecond line\n' > "$TEST_TMPDIR/note.txt"
run_gw_stdin "$TEST_TMPDIR/note.txt" --origin text --tier bookmark --title "Chat note" --capture-id bm-text
assert_eq "$rc $(result status) $(result tier)" "0 bookmarked bookmark" "a text bookmark"
tsrc="$VAULT/Sources/$(result revision)/source.md"
assert_eq "$(fm "$tsrc" tier) $(fm "$tsrc" content_format) $(capfm "$tsrc" method)" "bookmark text \"bookmark\"" "tier bookmark · the text format · method bookmark"
python3 - "$tsrc" "$TEST_TMPDIR/note.txt" <<'PY' && pass "the body is the text itself" || fail "the body is the text itself" "differs"
import sys
d = open(sys.argv[1], "rb").read(); i = d.find(b"\n---\n", 3)
sys.exit(0 if d[i + 5:] == open(sys.argv[2], "rb").read() else 1)
PY
[[ -f "$VAULT/Sources/$(result revision)/extract.md" ]] && fail "no extract for a text bookmark" "exists" || pass "no extract.md for a text bookmark"
# the same text in full promotes the card
stub_reset
run_gw_stdin "$TEST_TMPDIR/note.txt" --origin text --capture-id promo-text
assert_eq "$rc $(result status)" "0 extracted" "the same text in full extracts"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" "\"$(fm "$tsrc" revision | tr -d '"')\"" "...superseding the text card"

# ---------------------------------------------------------------------------------------------
echo "--- 7. --dry-run ---"
run_gw --url "https://example.com/dry" --tier bookmark --dry-run
assert_eq "$rc" "0" "dry-run of a bookmark exits 0"
assert_contains "$out" "Tier: bookmark" "...names the tier"
assert_contains "$out" "=== BOOKMARK CARD ===" "...and the card plan"
assert_not_contains "$out" "=== EXTRACTOR ARGV ===" "...no extractor section"
assert_contains "$out" "Raw store: none (a card has no raw)" "...and no raw store"
run_gw --url "https://example.com/dry" --dry-run
assert_contains "$out" "Tier: full" "a full dry-run says tier full"
# the card's intent is normalized: control characters become spaces
run_gw --url "https://example.com/intent-nl" --tier bookmark --intent "$(printf 'line one\nline two')" --capture-id bm-nl
assert_eq "$(tail -1 "$VAULT/Sources/$(result revision)/source.md")" "line one line two" "a newline in the intent becomes a space in the card"

# ---------------------------------------------------------------------------------------------
echo "--- 8. --canonical-url: a file or a text identified by its document ---"
stub_reset
printf '# Exported doc\n\nfirst export of the document\n' > "$TEST_TMPDIR/doc-v1.md"
printf '# Exported doc\n\nsecond export, a paragraph more\n' > "$TEST_TMPDIR/doc-v2.md"
run_gw --file "$TEST_TMPDIR/doc-v1.md" --canonical-url "https://docs.example.com/document/d/ABC/edit?usp=sharing&utm_source=slack" --title "Exported doc" --capture-id cu-1
assert_eq "$rc $(result status)" "0 extracted" "a file with --canonical-url is captured"
crev="$(result revision)"
csrc="$VAULT/Sources/$crev/source.md"
assert_eq "$(fm "$csrc" origin) $(fm "$csrc" resource_kind)" "file url" "origin file, resource_kind url"
assert_eq "$(fm "$csrc" url)" "\"https://docs.example.com/document/d/ABC/edit?usp=sharing&utm_source=slack\"" "url: is the literal given URL"
assert_eq "$(fm "$csrc" canonical_url)" "\"https://docs.example.com/document/d/ABC/edit?usp=sharing\"" "canonical_url: the computed v1 form"
assert_eq "$(fm "$csrc" origin_path)" "\"$TEST_TMPDIR/doc-v1.md\"" "origin_path: the file"
assert_eq "$(capfm "$csrc" canonical_version)" "1" "capture.canonical_version 1"
assert_eq "$(result canonical_url)" "https://docs.example.com/document/d/ABC/edit?usp=sharing" "RESULT:canonical_url printed"
assert_eq "$(rget cu-1 '.request.resource_kind') $(rget cu-1 '.request.resource_key') $(rget cu-1 '.request.origin') $(rget cu-1 '.request.url')" "url https://docs.example.com/document/d/ABC/edit?usp=sharing file https://docs.example.com/document/d/ABC/edit?usp=sharing&utm_source=slack" "the request: resource url/canonical key, origin file, the literal url"
assert_eq "$(rget cu-1 '.request.input_sha256')" "$(shasum -a 256 "$TEST_TMPDIR/doc-v1.md" | cut -c1-64)" "...input_sha256 binds the file's bytes"
assert_eq "$(rget cu-1 '.request.render') $(rget cu-1 '.request.external') $(rget cu-1 '.request.site') $(rget cu-1 '.request.requested_version')" "auto off null null" "...the url-only fields bind their defaults for a file input (reviewer A P2-11)"
EXTERNAL_FETCH=auto run_gw --file "$TEST_TMPDIR/doc-v1.md" --canonical-url "https://docs.example.com/document/d/ABC/edit?usp=sharing&utm_source=slack" --title "Exported doc" --capture-id cu-1 --render
assert_eq "$rc $(result replayed)" "0 true" "...so an instance with external_fetch auto and a --render flag replay the same request"
assert_eq "$(fetch_calls)" "0" "no fetch: the file is the input"
# another export of the same document: dedup by document, not by file (the v1 rules keep `usp=`;
# only the tracking keys go — the same canonical form needs the same query)
run_gw --file "$TEST_TMPDIR/doc-v2.md" --canonical-url "https://docs.example.com/document/d/ABC/edit?usp=sharing&fbclid=zzz" --capture-id cu-2
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $crev" "another export of the same document is a skipped-duplicate"
run_gw --file "$TEST_TMPDIR/doc-v2.md" --canonical-url "https://docs.example.com/document/d/ABC/edit?usp=sharing" --recapture --capture-id cu-3
assert_eq "$rc $(result status)" "0 extracted" "--recapture makes the next revision of the document"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" "\"$crev\"" "...superseding the first export"
# a replay of cu-1 with other bytes is a different request
run_gw --file "$TEST_TMPDIR/doc-v2.md" --canonical-url "https://docs.example.com/document/d/ABC/edit?usp=sharing&utm_source=slack" --title "Exported doc" --capture-id cu-1
assert_eq "$rc" "2" "replaying cu-1 with other bytes is a binding mismatch (exit 2)"
run_gw --file "$TEST_TMPDIR/doc-v1.md" --canonical-url "https://docs.example.com/document/d/ABC/edit?usp=sharing&utm_source=slack" --title "Exported doc" --capture-id cu-1
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "replaying cu-1 with the same bytes replays"
# a page fetched earlier through --url and the same document given as a file are one resource
stub_reset
run_gw --url "https://example.com/post?utm_source=a" --capture-id cu-url
assert_eq "$rc $(result status)" "0 extracted" "a URL capture"
urev="$(result revision)"
run_gw --file "$TEST_TMPDIR/doc-v1.md" --canonical-url "https://example.com/post?utm_source=b" --capture-id cu-4
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $urev" "a file declared as that page is a duplicate of the URL capture"
# a text on stdin with --canonical-url
run_gw_stdin "$TEST_TMPDIR/note.txt" --origin text --canonical-url "https://notes.example.com/n/42" --capture-id cu-5
assert_eq "$rc $(result status)" "0 extracted" "a text with --canonical-url"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" resource_kind) $(fm "$VAULT/Sources/$(result revision)/source.md" origin)" "url text" "...resource url, origin text"
# refusals
run_gw --url "https://example.com/x" --canonical-url "https://example.com/y"
assert_eq "$rc" "2" "--canonical-url with --url is exit 2"
assert_contains "$err" "identity of a --url capture is computed" "...with the rule"
nf="$(nfolders)"
run_gw --file "$TEST_TMPDIR/doc-v1.md" --canonical-url "not a url"
assert_eq "$rc" "2" "an invalid --canonical-url is exit 2"
assert_eq "$(nfolders)" "$nf" "...nothing written"
run_gw --file "$TEST_TMPDIR/doc-v1.md" --canonical-url "https://example.com/d" --dry-run
assert_eq "$rc" "0" "dry-run with --canonical-url"
assert_contains "$out" "declared by --canonical-url" "...names the declared identity"
# a bookmark with --canonical-url: a text card identified by its document
run_gw_stdin "$TEST_TMPDIR/note.txt" --origin text --tier bookmark --canonical-url "https://notes.example.com/n/43" --capture-id cu-6
assert_eq "$rc $(result status) $(result tier)" "0 bookmarked bookmark" "a text bookmark with --canonical-url"
assert_eq "$(fm "$VAULT/Sources/$(result revision)/source.md" canonical_url)" "\"https://notes.example.com/n/43\"" "...keyed by the document"

# ---------------------------------------------------------------------------------------------
echo "--- 9. batch: tier and canonical_url per item ---"
stub_reset
python3 - "$TEST_TMPDIR/manifest.json" "$TEST_TMPDIR/doc-v2.md" <<'PY'
import json, sys
json.dump([
  {"origin": "url", "url": "https://example.com/batch-bm", "title": "Batch bookmark", "tier": "bookmark", "capture_id": "bt-1"},
  {"origin": "file", "file": sys.argv[2], "canonical_url": "https://docs.example.com/document/d/XYZ/edit", "capture_id": "bt-2"},
  {"origin": "url", "url": "https://example.com/batch-full", "capture_id": "bt-3"},
], open(sys.argv[1], "w"))
PY
run_gw --manifest "$TEST_TMPDIR/manifest.json"
assert_eq "$rc" "0" "the batch passes"
assert_contains "$out" "RESULT:batch_passed=3" "3 items passed"
assert_eq "$(rget bt-1 '.state') $(rget bt-2 '.state') $(rget bt-3 '.state')" "bookmarked extracted extracted" "item tiers: bookmark · full (canonical) · full"
assert_eq "$(fm "$VAULT/Sources/$(rget bt-2 '.revision')/source.md" canonical_url)" "\"https://docs.example.com/document/d/XYZ/edit\"" "the item's canonical_url is the identity"
assert_eq "$(stub_calls)" "2" "two extractor calls (the bookmark made none)"
# the batch-level --tier applies to every item without its own
python3 - "$TEST_TMPDIR/manifest2.json" <<'PY'
import json, sys
json.dump([
  {"origin": "url", "url": "https://example.com/batch-bm-2", "capture_id": "bt-4"},
  {"origin": "url", "url": "https://example.com/batch-bm-3", "tier": "full", "capture_id": "bt-5"},
], open(sys.argv[1], "w"))
PY
stub_reset
run_gw --manifest "$TEST_TMPDIR/manifest2.json" --tier bookmark
assert_eq "$rc $(rget bt-4 '.state') $(rget bt-5 '.state')" "0 bookmarked extracted" "--tier bookmark on the batch; an item's own tier outranks it"
run_gw --manifest "$TEST_TMPDIR/manifest2.json" --canonical-url "https://example.com/one"
assert_eq "$rc" "2" "--canonical-url on a batch is exit 2 (one document per item)"

cleanup_capture_test
summary
