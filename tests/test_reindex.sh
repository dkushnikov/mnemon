#!/usr/bin/env bash
# test_reindex.sh — bin/knowledge-reindex.py (spec docs/specs/2026-10-15-capture-layer.md §3.9, D14,
# §7.14, §12 A1, A2): every row regenerated from the files (a changed rating changes the row);
# index_note appended; supersedes markers derived from source.md in both forms, fan-out, a successor
# without extract.md, dangling targets counted, cycles reported and not marked; the preservation
# transaction — lossless, deduplicated by (folder, sha256(row)) across the day's file, written and
# fsynced BEFORE index.md is replaced, an unwritable report → exit 1 and the index untouched,
# --no-hand-edit-report; the pending note; malformed headers; the dispatcher's flag passthrough.
# A temp vault, no network. bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
REINDEX="$MNEMON_ROOT/bin/knowledge-reindex.py"
MNEMON="$MNEMON_ROOT/bin/mnemon"

echo "=== knowledge-reindex.py Tests ==="

# --- toolchain proof (§12 A12) ---
echo "--- toolchain ---"
PY_VER="$(python3 --version 2>&1)"
echo "  python3: $PY_VER ($(command -v python3)) · bash: $BASH_VERSION"
# the legacy run is the PATH prefix /usr/bin:/bin (test-helper.sh), never "python3 is /usr/bin/python3" —
# on ubuntu that is the current toolchain
assert_toolchain_legacy

unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_VAULT_PATH MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-reindex-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
VAULT="$TEST_TMPDIR/vault"
mkdir -p "$VAULT/Sources" "$VAULT/Synthesis" "$VAULT/_meta"
TODAY="$(date +%Y-%m-%d)"
TODAY_UTC="$(date -u +%Y-%m-%d)"   # the hand-edit heading carries a UTC stamp; the file name the local date
REPORT="$VAULT/_inputs/index-hand-edits-$TODAY.md"

# mk_source <folder> <title> [frontmatter line…] · mk_extract <folder> <title> <rating> [frontmatter line…]
mk_source() {
  local f="$1" t="$2"; shift 2
  mkdir -p "$VAULT/Sources/$f"
  { printf -- '---\ntype: source\nsource_type: article\ntitle: "%s"\nrevision: "%s"\n' "$t" "$f"
    for l in "$@"; do printf '%s\n' "$l"; done
    printf -- '---\n# %s\n\nbody text\n' "$t"; } > "$VAULT/Sources/$f/source.md"
}
mk_extract() {
  local f="$1" t="$2" r="$3"; shift 3
  mkdir -p "$VAULT/Sources/$f"
  { printf -- '---\ntype: extract\nsource_type: article\nstatus: extracted\ntitle: "%s"\nrating: %s\ndomains: [x]\n' "$t" "$r"
    for l in "$@"; do printf '%s\n' "$l"; done
    printf -- '---\n### Summary\n\ntext\n'; } > "$VAULT/Sources/$f/extract.md"
}
out=""; err=""; rc=0
run_ri() { rc=0; out="$(python3 "$REINDEX" "$@" 2>"$TEST_TMPDIR/err")" || rc=$?; err="$(cat "$TEST_TMPDIR/err")"; }
row_of() { grep -F -- "- [[Sources/$1/" "$VAULT/index.md" || true; }
report_entries() { grep -c -- "^<!-- mnemon:hand-edit folder=${1:-}" "$REPORT" 2>/dev/null || true; }
# set_row <folder> <new row text>: hand-edit that folder's row in index.md
set_row() { python3 - "$VAULT/index.md" "$1" "$2" <<'PY'
import re, sys
p, folder, new = sys.argv[1:4]
lines = open(p, encoding="utf-8").read().split("\n")
lines = [new if ln.startswith("- [[Sources/%s/" % folder) else ln for ln in lines]
open(p, "w", encoding="utf-8").write("\n".join(lines))
PY
}

A=2026-10-01_aaaa0001; B=2026-10-02_bbbb0002; C=2026-10-03_cccc0003; D=2026-10-04_dddd0004
E=2026-10-05_eeee0005; F=2026-10-06_ffff0006; G=2026-10-07_gggg0007; H=2026-10-08_hhhh0008
I=2026-10-09_iiii0009; J=2026-10-10_jjjj0010; K=2026-10-11_kkkk0011; L=2026-10-12_llll0012
mk_source "$A" "Alpha";                                   mk_extract "$A" "Alpha" 7
mk_source "$B" "Beta"  "supersedes: \"$A\"";              mk_extract "$B" "Beta" 5
mk_source "$C" "Gamma" "supersedes: \"Sources/$B/extract\""; mk_extract "$C" "Gamma" 6
mk_source "$D" "Delta";                                   mk_extract "$D" "Delta" 3
mk_source "$E" "Epsilon" "supersedes: \"$D\"";            mk_extract "$E" "Epsilon" 4
mk_source "$F" "Zeta" "supersedes: \"$D\"";               mk_extract "$F" "Zeta" 4
mk_source "$G" "Gamma v2" "supersedes: \"$C\""            # a successor without extract.md (pending)
mk_source "$H" "Eta" "supersedes: \"2026-09-01_dead0000\""; mk_extract "$H" "Eta" 2   # dangling
mk_source "$I" "Iota" "supersedes: \"$J\"";               mk_extract "$I" "Iota" 1
mk_source "$J" "Kappa" "supersedes: \"$I\"";              mk_extract "$J" "Kappa" 1   # a cycle with I
mk_source "$K" "Bookmark" "status: bookmark"              # a legacy placeholder
mkdir -p "$VAULT/Sources/$L"; printf -- '---\ntype: source\ntitle: "Open"\nsupersedes: "%s"\n' "$A" > "$VAULT/Sources/$L/source.md"   # no closing ---
printf '# A synthesis\n\nnotes\n' > "$VAULT/Synthesis/My Note.md"
# a hand-edited index: a remark on D's row and a row of a folder that no longer exists
cat > "$VAULT/index.md" <<EOF
---
type: index
created: 2026-03-29
updated: 2026-10-01
---

## Recent Sources

- [[Sources/$D/extract|Delta]] (article, x, rating 3) — my hand remark
- [[Sources/2026-01-01_gone0000/extract|Gone]] (article, x)
EOF

echo "--- regeneration, index_note-free rows, derived supersedes (both forms), fan-out, successor without extract.md ---"
run_ri "$VAULT"
assert_eq "$rc" "0" "reindex exits 0"
assert_eq "$(row_of "$A")" "- [[Sources/$A/extract|Alpha]] (article, x, rating 7) ⤳ superseded by [[Sources/$B/extract|Beta]]" "supersedes: \"<revision>\" marks the old row with the new title"
assert_eq "$(row_of "$B")" "- [[Sources/$B/extract|Beta]] (article, x, rating 5) ⤳ superseded by [[Sources/$C/extract|Gamma]]" "supersedes: \"Sources/<revision>/extract\" (prefix and suffix stripped) marks too"
assert_eq "$(row_of "$C")" "- [[Sources/$C/extract|Gamma]] (article, x, rating 6) ⤳ superseded by [[Sources/$G/source|Gamma v2]]" "a successor without extract.md links its source"
assert_eq "$(row_of "$D")" "- [[Sources/$D/extract|Delta]] (article, x, rating 3) ⤳ superseded by [[Sources/$E/extract|Epsilon]] ⤳ superseded by [[Sources/$F/extract|Zeta]]" "fan-out lists every successor (and the hand remark is gone from the index)"
assert_eq "$(row_of "$H")" "- [[Sources/$H/extract|Eta]] (article, x, rating 2)" "a dangling supersedes marks nothing"
assert_contains "$out" "Dangling-supersedes=1" "...and is counted"
assert_eq "$(row_of "$I")" "- [[Sources/$I/extract|Iota]] (article, x, rating 1)" "a cycle member is not marked (I)"
assert_eq "$(row_of "$J")" "- [[Sources/$J/extract|Kappa]] (article, x, rating 1)" "a cycle member is not marked (J)"
assert_contains "$err" "supersedes cycle: $I → $J → $I" "the cycle is reported on stderr"
assert_contains "$err" "malformed-frontmatter $L" "a header without a closing --- is reported as malformed-frontmatter (A2)"
assert_eq "$(row_of "$A" | grep -c "$L" || true)" "0" "...and its supersedes: is not read"
assert_contains "$out" "Superseded=4" "the counters line reports N superseded"
assert_contains "$(cat "$VAULT/index.md")" "| Superseded | 4 |" "the Status table shows the superseded count"
assert_contains "$out" "Sources=12, Extracted=9, Synthesis=1" "the other counters"
assert_contains "$(cat "$VAULT/index.md")" "_2 source(s) captured without extract.md (finish each with \`mnemon add --resume <revision>\`: $G, $L)._" "the pending note names mnemon add --resume and the revisions"
assert_contains "$(cat "$VAULT/index.md")" "_1 legacy placeholder(s) without extract.md (\`status: bookmark\` or \`fetch_status: failed\` in source.md — not resumable; capture again): $K._" "legacy placeholders are counted apart"
assert_contains "$(cat "$VAULT/index.md")" "- [[Synthesis/My Note|A synthesis]]" "Synthesis notes listed"
assert_eq "$(row_of 2026-01-01_gone0000)" "" "a row of a vanished folder is dropped from the index"
[[ -e "$VAULT/.index.md.tmp" ]] && fail "no temp file left" "exists" || pass "index.md written atomically (no .index.md.tmp left)"

echo "--- the preservation transaction (A1) ---"
assert_file_exists "$REPORT" "the hand-edit report exists under _inputs/ (created)"
assert_contains "$(cat "$REPORT")" "- [[Sources/$D/extract|Delta]] (article, x, rating 3) — my hand remark" "the exact hand-edited row is in the report"
assert_contains "$(cat "$REPORT")" "- [[Sources/2026-01-01_gone0000/extract|Gone]] (article, x)" "...and the vanished folder's row"
assert_contains "$(cat "$REPORT")" "## $TODAY_UTC" "...under a dated heading (UTC stamp)"
assert_eq "$(report_entries)" "2" "two entries"
assert_contains "$out" "Preserved=2" "the counters line reports the preserved rows"
assert_contains "$err" "2 replaced row(s) preserved in _inputs/index-hand-edits-$TODAY.md" "a one-line notice on stderr"
sum1="$(cksum "$REPORT")"
run_ri "$VAULT"
assert_eq "$rc" "0" "a second run exits 0"
assert_not_contains "$out" "Preserved=" "...preserves nothing when nothing differs"
assert_eq "$(cksum "$REPORT")" "$sum1" "...and leaves the report untouched"

echo "--- index_note from extract.md (decoded, display-escaped), appended before the marker ---"
mk_extract "$A" "Alpha" 7 'index_note: "see also \"Beta\" | notes]] here"'
run_ri "$VAULT"
assert_eq "$(row_of "$A")" "- [[Sources/$A/extract|Alpha]] (article, x, rating 7) — see also \"Beta\" / notes] ] here ⤳ superseded by [[Sources/$B/extract|Beta]]" "index_note appended as ' — <note>' (\\\" decoded, | and ]] display-escaped), then the marker"
assert_eq "$(report_entries "$A")" "1" "the previous (generated) row of A was preserved — every replaced row is (A1)"
assert_contains "$(cat "$REPORT")" "- [[Sources/$A/extract|Alpha]] (article, x, rating 7) ⤳ superseded by [[Sources/$B/extract|Beta]]" "...verbatim"

echo "--- a changed rating changes the row ---"
mk_extract "$D" "Delta" 9
run_ri "$VAULT"
assert_eq "$(row_of "$D")" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) ⤳ superseded by [[Sources/$E/extract|Epsilon]] ⤳ superseded by [[Sources/$F/extract|Zeta]]" "rating 3 → 9 regenerates the row"
assert_eq "$(report_entries "$D")" "2" "...the rating-3 row preserved (second entry for D)"

echo "--- two edits of one row in one day → two entries; the same text again → no third (dedup by folder + sha256) ---"
set_row "$D" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) — hand edit one"
run_ri "$VAULT"
assert_eq "$(report_entries "$D")" "3" "edit one preserved"
set_row "$D" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) — hand edit two"
run_ri "$VAULT"
assert_eq "$(report_entries "$D")" "4" "edit two preserved as a distinct entry (same day, same file)"
set_row "$D" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) — hand edit one"
run_ri "$VAULT"
assert_eq "$(report_entries "$D")" "4" "the same row text a second time is not duplicated"
assert_not_contains "$out" "Preserved=" "...and nothing counted as preserved"
assert_eq "$(grep -c -- "— hand edit one" "$REPORT")" "1" "...one copy of that text in the file"

echo "--- the report is written and fsynced BEFORE index.md is replaced (crash hook) ---"
set_row "$D" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) — hand edit three"
MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-hand-edit-report run_ri "$VAULT"
assert_eq "$rc" "99" "the crash hook fires after the report"
assert_contains "$(row_of "$D")" "hand edit three" "index.md untouched at the crash point"
assert_contains "$(cat "$REPORT")" "— hand edit three" "...while the report already holds the row"
run_ri "$VAULT"
assert_eq "$rc" "0" "the next run completes"
assert_not_contains "$(row_of "$D")" "hand edit three" "...regenerating the row"
assert_eq "$(grep -c -- "— hand edit three" "$REPORT")" "1" "...without a second copy of the preserved row"

echo "--- an unwritable report → exit 1 and index.md untouched ---"
set_row "$D" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) — hand edit four"
before="$(cksum "$VAULT/index.md")"
chmod 400 "$REPORT"; chmod 500 "$VAULT/_inputs"
run_ri "$VAULT"
chmod 755 "$VAULT/_inputs"; chmod 644 "$REPORT"
assert_eq "$rc" "1" "exit 1 when the report cannot be written"
assert_contains "$err" "hand-edit report not written" "...naming the reason"
assert_contains "$err" "index.md left untouched" "...and the consequence"
assert_eq "$(cksum "$VAULT/index.md")" "$before" "index.md byte-identical"
assert_eq "$(grep -c -- "— hand edit four" "$REPORT")" "0" "the report has no partial entry"

echo "--- --no-hand-edit-report skips the preservation (explicit) ---"
run_ri "$VAULT" --no-hand-edit-report
assert_eq "$rc" "0" "exit 0 with --no-hand-edit-report"
assert_not_contains "$(row_of "$D")" "hand edit four" "the row is regenerated"
assert_eq "$(grep -c -- "— hand edit four" "$REPORT")" "0" "...and the hand edit is not preserved"
assert_contains "$err" "1 replaced row(s) not preserved (--no-hand-edit-report)" "...with a notice"

echo "--- a superseded folder without a row is counted, not marked ---"
M=2026-10-13_mmmm0013; N=2026-10-14_nnnn0014
mk_source "$M" "Mu"
mk_source "$N" "Nu" "supersedes: \"$M\""; mk_extract "$N" "Nu" 5
run_ri "$VAULT"
assert_contains "$out" "Superseded=5" "a superseded folder without extract.md counts"
assert_eq "$(row_of "$M")" "" "...and has no row"
assert_eq "$(row_of "$N")" "- [[Sources/$N/extract|Nu]] (article, x, rating 5)" "...its successor is unmarked"

echo "--- a block list: one re-capture supersedes several revisions (O9) ---"
O=2026-10-15_oooo0015; P=2026-10-16_pppp0016; Q=2026-10-17_qqqq0017
mk_source "$O" "Omicron";                                 mk_extract "$O" "Omicron" 6
mk_source "$P" "Pi";                                      mk_extract "$P" "Pi" 6
mk_source "$Q" "Rho" "supersedes:" "  - \"$O\"" "  - \"Sources/$P/extract\"" "  - \"2026-09-02_dead0001\""; mk_extract "$Q" "Rho" 8
run_ri "$VAULT"
assert_eq "$rc" "0" "reindex exits 0 with a block list"
assert_eq "$(row_of "$O")" "- [[Sources/$O/extract|Omicron]] (article, x, rating 6) ⤳ superseded by [[Sources/$Q/extract|Rho]]" "the first listed predecessor carries the marker to the one successor"
assert_eq "$(row_of "$P")" "- [[Sources/$P/extract|Pi]] (article, x, rating 6) ⤳ superseded by [[Sources/$Q/extract|Rho]]" "...and the second (the Sources/<rev>/extract form inside the list)"
assert_eq "$(row_of "$Q")" "- [[Sources/$Q/extract|Rho]] (article, x, rating 8)" "the successor's own row is unmarked"
assert_contains "$out" "Superseded=7" "both predecessors count (5 + 2)"
assert_contains "$out" "Dangling-supersedes=2" "a vanished folder inside the list is dangling and marks nothing (1 + 1)"

echo "--- the dispatcher passes --no-hand-edit-report through (under the vault lock) ---"
CFG="$TEST_TMPDIR/instance.yaml"
printf 'instance: ri\nvault_path: %s\nreader_context_path: %s/reader-context.md\nsearch_provider: grep\ndefault_model: sonnet\n' "$VAULT" "$VAULT" > "$CFG"
printf 'reader\n' > "$VAULT/reader-context.md"
set_row "$D" "- [[Sources/$D/extract|Delta]] (article, x, rating 9) — hand edit five"
rc=0; out="$("$MNEMON" --config "$CFG" reindex --no-hand-edit-report 2>"$TEST_TMPDIR/err")" || rc=$?; err="$(cat "$TEST_TMPDIR/err")"
assert_eq "$rc" "0" "mnemon reindex --no-hand-edit-report exits 0"
assert_contains "$out" "reindex: vault — Sources=17" "...and rebuilt the index"
assert_eq "$(grep -c -- "— hand edit five" "$REPORT")" "0" "...without preserving (the flag reached the script)"
rc=0; out="$("$MNEMON" --config "$CFG" reindex 2>"$TEST_TMPDIR/err")" || rc=$?
assert_eq "$rc" "0" "mnemon reindex exits 0"

cd /
rm -rf "$TEST_TMPDIR"
summary
