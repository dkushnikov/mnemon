#!/usr/bin/env bash
# test_vault_doctor.sh — bin/lib_vault.py, bin/vault-doctor.py and the dispatcher's `doctor --data`,
# `doctor --list`, `status --brief` (docs/specs/2026-10-16-vault-store.md S1–S5, S7 — the design's
# test matrix): the three folder classes and the gateway's body hash rule, every one of the 19
# checks on a fixture vault with deliberate violations and on a clean one, the radius grammar,
# declared and parked queues, the frozen mnemon-doctor-data/1 document, --list, the one-line brief
# with its budget, the lock probe, the observational proof (nothing written anywhere), the
# dispatcher's exit codes, the one Extracted of `status`, the reindex Commands block.
# Temp vaults, a fixed --today, no network. bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DOCTOR="$MNEMON_ROOT/bin/vault-doctor.py"
LIBV="$MNEMON_ROOT/bin/lib_vault.py"
MNEMON="$MNEMON_ROOT/bin/mnemon"
REINDEX="$MNEMON_ROOT/bin/knowledge-reindex.py"

echo "=== vault-doctor Tests ==="
print_toolchain; assert_toolchain_legacy

unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_VAULT_PATH MNEMON_HOME
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-vdoc-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
mkdir -p "$XDG_CONFIG_HOME"
TODAY=2026-10-16
THIS_HOST="$(hostname -s 2>/dev/null || hostname)"; THIS_HOST="${THIS_HOST%%.*}"

out=""; err=""; rc=0
run() { rc=0; out="$("$@" 2>"$TEST_TMPDIR/err")" || rc=$?; err="$(cat "$TEST_TMPDIR/err")"; }
# doc [args…]: the doctor on $VAULT with the fixed clock and the engine guide pinned at 2
doc() { run python3 "$DOCTOR" --vault "$VAULT" --today "$TODAY" --guide-version 2 "$@"; }
line_of() { printf '%s\n' "$out" | grep -E "^(ok|warn|fail) $1 — " || true; }
status_of() { line_of "$1" | cut -d' ' -f1; }
count_status() { printf '%s\n' "$out" | grep -c "^$1 " || true; }
jf() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print(eval(sys.argv[2]))' "$1" "$2"; }
jc() { jf "$1" "[c for c in d['checks'] if c['name'] == '$2'][0]$3"; }

# --- fixture builders (a global VAULT) ---
words() { python3 -c 'import sys; print(" ".join("w%d" % i for i in range(int(sys.argv[1]))))' "$1"; }
sha_body() { python3 - "$1" <<'PY'
import hashlib, sys
with open(sys.argv[1], "rb") as fh:
    data = fh.read()
i = data.find(b"\n---\n", 3)
print(hashlib.sha256(data[i + 5:]).hexdigest() if data.startswith(b"---\n") and i >= 0 else "none")
PY
}
fresh_vault() { VAULT="$TEST_TMPDIR/$1"; mkdir -p "$VAULT/Sources" "$VAULT/Synthesis" "$VAULT/_inputs" "$VAULT/_meta"; }
# mk_legacy <folder> <words> [frontmatter line…] — not one managed key (STYPE overrides the type)
mk_legacy() {
  local f="$1" n="$2"; shift 2
  mkdir -p "$VAULT/Sources/$f"
  { printf -- '---\ntype: source\nsource_type: %s\ntitle: "%s"\n' "${STYPE:-article}" "$f"
    for l in "$@"; do printf '%s\n' "$l"; done
    printf -- '---\n%s\n' "$(words "$n")"; } > "$VAULT/Sources/$f/source.md"
}
# mk_managed <folder> <words> [frontmatter line…] — the four managed keys, capture.text_sha256 computed
# from the body by the gateway's rule. Overrides: RSHA, CTX, ORIGIN, RAW_STORE, COMPLETENESS (`-` omits
# the line), STYPE; an argument starting with two spaces lands inside the capture: block.
mk_managed() {
  local f="$1" n="$2"; shift 2
  local p="$VAULT/Sources/$f/source.md" extra="" cap="" line sha
  mkdir -p "$VAULT/Sources/$f"
  for line in "$@"; do
    case "$line" in "  "*) cap="$cap$line"$'\n' ;; *) extra="$extra$line"$'\n' ;; esac
  done
  { printf -- '---\ntype: source\nsource_type: %s\norigin: %s\ntitle: "%s"\ncapture_id: "cap-%s"\nresource_kind: url\nresource_sha256: "%s"\nrevision: "%s"\ncontext: "%s"\n' \
      "${STYPE:-article}" "${ORIGIN:-url}" "$f" "$f" "${RSHA:-$(printf '%s' "$f" | shasum -a 256 | cut -c1-64)}" "$f" "${CTX:-personal}"
    printf '%s' "$extra"
    printf 'capture:\n  method: "direct+defuddle"\n  raw_store: %s\n  text_sha256: "PLACEHOLDER"\n' "${RAW_STORE:-none}"
    if [[ "${COMPLETENESS:-complete}" != "-" ]]; then printf '  completeness: %s\n' "${COMPLETENESS:-complete}"; fi
    printf '%s' "$cap"
    printf -- '---\n%s\n' "$(words "$n")"; } > "$p"
  sha="$(sha_body "$p")"
  python3 - "$p" "$sha" <<'PY'
import sys
p, sha = sys.argv[1:3]
with open(p, encoding="utf-8") as fh:
    s = fh.read()
with open(p, "w", encoding="utf-8") as fh:
    fh.write(s.replace('text_sha256: "PLACEHOLDER"', 'text_sha256: "%s"' % sha, 1))
PY
}
# mk_extract <folder> <rating|-> [frontmatter line…] — STATUS (unset → extracted; empty → `status: `),
# STYPE; NOSECTIONS=1 writes a body without the five sections
mk_extract() {
  local f="$1" r="$2"; shift 2
  mkdir -p "$VAULT/Sources/$f"
  { printf -- '---\ntype: extract\nsource_type: %s\nstatus: %s\ntitle: "%s"\n' "${STYPE:-article}" "${STATUS-extracted}" "$f"
    if [[ "$r" != "-" ]]; then printf 'rating: %s\n' "$r"; fi
    printf 'domains: [x]\n'
    for l in "$@"; do printf '%s\n' "$l"; done
    printf -- '---\n'
    if [[ "${NOSECTIONS:-0}" == 1 ]]; then printf 'a body without the sections\n'; else printf '### Summary\n\ntext\n\n### Key Ideas\n\n- one\n'; fi
  } > "$VAULT/Sources/$f/extract.md"
}
# mk_pair <folder> <words> <rating> [source line…]: a managed source.md and a consistent extract.md
mk_pair() {
  local f="$1" n="$2" r="$3"; shift 3
  mk_managed "$f" "$n" "$@"
  mk_extract "$f" "$r" "capture_id: \"cap-$f\"" "revision: \"$f\""
}
# mk_synth <name> <status|-> <drafted_by|-> <created|-> [body]
mk_synth() {
  local n="$1" st="$2" by="$3" cr="$4" body="${5:-}"
  { printf -- '---\n'
    if [[ "$st" != "-" ]]; then printf 'status: %s\n' "$st"; fi
    if [[ "$by" != "-" ]]; then printf 'drafted_by: %s\n' "$by"; fi
    if [[ "$cr" != "-" ]]; then printf 'created: %s\n' "$cr"; fi
    printf -- '---\n# %s\n\n%s\n' "$n" "$body"; } > "$VAULT/Synthesis/$n.md"
}
# mk_queue <name> [frontmatter line…]: an _inputs file with three `- ` items
mk_queue() {
  local n="$1"; shift
  { if [[ $# -gt 0 ]]; then printf -- '---\n'; for l in "$@"; do printf '%s\n' "$l"; done; printf -- '---\n'; fi
    printf '# %s\n\n- one\n- two\n- three\n' "$n"; } > "$VAULT/_inputs/$n.md"
}
# mk_receipt <dir> <id> <state> <revision> <text_sha256|->
mk_receipt() {
  local sha='null'
  [[ "$5" != "-" ]] && sha="\"$5\""
  printf '{"schema": "mnemon-capture-receipt/1", "capture_id": "%s", "state": "%s", "revision": "%s", "text_sha256": %s}\n' "$2" "$3" "$4" "$sha" > "$1/captures/$2.json"
}
# snap <dir…>: every path with mtime, inode and size — the observational proof
snap() { python3 - "$@" <<'PY'
import os, sys
rows = []
for root in sys.argv[1:]:
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for n in sorted(dirs + files):
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            rows.append("%s %d %d %d" % (os.path.relpath(p, root), st.st_mtime_ns, st.st_ino, st.st_size))
print("\n".join(rows))
PY
}

# =============================================================================================
# The dirty vault — every violation of the matrix at once
# =============================================================================================
fresh_vault dirty
ARCHIVE="$TEST_TMPDIR/archive"; LEDGER="$TEST_TMPDIR/ledger"
mkdir -p "$ARCHIVE/2026-10-01_m1000001.site" "$LEDGER/captures" "$VAULT/_inputs/pending-writes"
M1=2026-10-01_m1000001; M2=2026-10-02_m2000002; M3=2026-10-03_m3000003; D1=2026-10-04_d1000004
M4=2026-10-05_m4000005; M5=2026-10-06_m5000006; M6=2026-10-07_m6000007; M7=2026-10-08_m7000008
M8=2026-10-09_m8000009; M9=2026-10-10_m9000010; M10=2026-10-11_m1000011; M11=2026-10-12_m1100012
M12=2026-10-13_m1200013; M13=2026-10-14_m1300014; M14=2026-10-15_m1400015; M15=2026-10-16_m1500016
M16=2026-10-17_m1600017; T7=2026-10-18_t7000018
L1=2026-09-01_l1000001; L2=2026-09-02_l2000002; L3=2026-09-03_l3000003; L4=2026-09-04_l4000004
L5=2026-09-05_l5000005; L6=2026-09-06_l6000006; L7=2026-09-07_l7000007; L8=2026-09-08_l8000008
L9=2026-09-09_l9000009; L10=2026-09-10_l1000010; L11=2026-09-11_l1100011; L12=2026-09-12_l1200012
L13=2026-09-13_l1300013; C1=2026-09-14_c1000014; C2=2026-09-15_c2000015; X1=2026-09-16_x1000016
L14=2026-09-17_l1400017; L15=2026-09-18_l1500018; L16=2026-09-19_l1600019; L17=2026-09-20_l1700020
L18=2026-09-21_l1800021; T1=2026-09-22_t1000022; T2=2026-09-23_t2000023; T3=2026-09-24_t3000024
T4=2026-09-25_t4000025; T5=2026-09-26_t5000026; T6=2026-09-27_t6000027; T8=2026-09-28_t8000028
A2=2026-09-29_a2000029; A3=2026-09-30_a3000030; A4=2026-10-19_a4000031; S1=2026-10-20_s1000032
E1=2026-10-21_e1000033; U1=2026-10-22_u1000034

# I1 — a managed body edited after capture, a foreign `_width`, a partial key set (damaged), legacy `_width`,
# a legacy header without its closing ---, a legacy empty body
RAW_STORE=archive; mk_pair "$M1" 350 8 'url: "https://example.com/m1?utm_source=x"' 'canonical_url: "https://example.com/m1"' "archive: \"$M1.html\""; unset RAW_STORE
mk_pair "$M2" 350 6 'canonical_url: "https://example.com/m2"'
M2_SHA_AT_CAPTURE="$(sha_body "$VAULT/Sources/$M2/source.md")"
printf 'an edit after capture\n' >> "$VAULT/Sources/$M2/source.md"
mk_pair "$M3" 350 5 'canonical_url: "https://example.com/m3"' '_width: 100'
mkdir -p "$VAULT/Sources/$D1"; printf -- '---\ntype: source\ntitle: "%s"\ncapture_id: "cap-d1"\nrevision: "%s"\n---\n# damaged\n\n%s\n' "$D1" "$D1" "$(words 350)" > "$VAULT/Sources/$D1/source.md"
mk_legacy "$L1" 350 '_width: 100'; STATUS=bogus; mk_extract "$L1" 4; unset STATUS
mk_legacy "$L2" 350 'url: "https://example.com/l2"'; mk_extract "$L2" 5 'capture_context:' '  vault: personal'
mkdir -p "$VAULT/Sources/$L3"; printf -- '---\ntype: source\ntitle: "open"\n# no closing delimiter\n' > "$VAULT/Sources/$L3/source.md"
mk_legacy "$L4" 0; STATUS=""; NOSECTIONS=1; mk_extract "$L4" 2; unset STATUS NOSECTIONS
# I2 — two current managed revisions with one resource_sha256 (and different canonical_url: inconsistent),
# a legacy and a managed revision of one canonical URL, a non-url legacy (unkeyed)
RSHA="$(printf 'shared' | shasum -a 256 | cut -c1-64)"
mk_pair "$M4" 350 5 'canonical_url: "https://example.com/m4"'; mk_pair "$M5" 350 5 'canonical_url: "https://example.com/m5"'; unset RSHA
mk_pair "$M6" 350 5 'canonical_url: "https://dup.example.com/x"'
mk_legacy "$L5" 350 'url: "https://dup.example.com/x?utm_source=a"'; mk_extract "$L5" 5
mk_legacy "$L6" 350; mk_extract "$L6" 5
# I4 — a superseded pair of one resource (and a stored `superseded` word), a cross-resource edge, fan-out,
# a cycle, a dangling target
mk_legacy "$L7" 350 'url: "https://example.com/l7"'; STATUS=superseded; mk_extract "$L7" 5; unset STATUS
mk_legacy "$L8" 350 'url: "https://example.com/l7"' "supersedes: \"$L7\""; mk_extract "$L8" 5
mk_legacy "$L9" 350 'url: "https://example.com/l9"'; mk_extract "$L9" 5
mk_legacy "$L10" 350 'url: "https://example.com/l10"' "supersedes: \"Sources/$L9/extract\""; mk_extract "$L10" 5
mk_legacy "$L11" 350; mk_extract "$L11" 5
mk_legacy "$L12" 350 "supersedes: \"$L11\""; mk_extract "$L12" 5
mk_legacy "$L13" 350 "supersedes: \"$L11\""; mk_extract "$L13" 5
mk_legacy "$C1" 350 "supersedes: \"$C2\""; mk_extract "$C1" 5
mk_legacy "$C2" 350 "supersedes: \"$C1\""; mk_extract "$C2" 5
mk_legacy "$X1" 350 'supersedes: "2026-01-01_dead0000"'; mk_extract "$X1" 5
# I5 — raw resolving (plain, legacy suffix form), unarchivable, unresolved, managed without raw, owners of
# <rev>.report.json and <rev>.site/, unowned entries
RAW_STORE=archive; mk_pair "$M7" 350 5 "archive: \"$M7.html\""; mk_pair "$M8" 350 5; mk_pair "$M9" 350 5 "archive: \"$M9.pdf\""; unset RAW_STORE
mk_legacy "$L14" 350 "archive: \"$L14.html (in archive_dir: /old/archive)\""; mk_extract "$L14" 5
mk_legacy "$L15" 350 'archive: unarchivable'; mk_extract "$L15" 5
mk_legacy "$L16" 350 "archive: \"$L16.html\""; mk_extract "$L16" 5
for f in "$M1.html" "$M7.html" "$L14.html" "$M1.report.json" "orphan.txt" "2026-01-01_gone0000.report.json"; do printf 'raw\n' > "$ARCHIVE/$f"; done
# I6 — a managed url revision without completeness, one blocked, one with an unknown word, a text one
COMPLETENESS=-; mk_pair "$M10" 350 5 'canonical_url: "https://example.com/m10"'; unset COMPLETENESS
COMPLETENESS=blocked; mk_pair "$M11" 350 5 'canonical_url: "https://example.com/m11"'; unset COMPLETENESS
COMPLETENESS=bogus; mk_pair "$M12" 350 5 'canonical_url: "https://example.com/m12"'; unset COMPLETENESS
ORIGIN=text; COMPLETENESS=-; mk_pair "$M13" 350 5; unset ORIGIN COMPLETENESS
# I9 — a managed context the charter does not declare
CTX=research; mk_pair "$M14" 350 5 'canonical_url: "https://example.com/m14"'; unset CTX
# I10 — a managed pair disagreeing on revision, one whose url differs, an extract without a source
mk_managed "$M15" 350 'canonical_url: "https://example.com/m15"'; mk_extract "$M15" 5 "capture_id: \"cap-$M15\"" 'revision: "other"'
mk_managed "$M16" 350 'url: "https://a.example.com/m16"' 'canonical_url: "https://a.example.com/m16"'
mk_extract "$M16" 5 "capture_id: \"cap-$M16\"" "revision: \"$M16\"" 'url: "https://b.example.com/m16"'
mkdir -p "$VAULT/Sources/$E1"; mk_extract "$E1" 5
# Q1/Q2/Q3 — thin bodies by feature; a short type with partial completeness; bookmark tier; arXiv
mk_legacy "$T1" 200 'url: "https://example.com/t1"'; mk_extract "$T1" 9
mk_legacy "$T2" 20 'archive: unarchivable'; mk_extract "$T2" 1
mk_legacy "$T3" 150 'url: "https://arxiv.org/abs/2401.00001"'; mk_extract "$T3" 7
STYPE=video; mk_legacy "$T4" 100; mk_extract "$T4" 6; unset STYPE
STYPE=website; mk_legacy "$T5" 120; mk_extract "$T5" 3; unset STYPE
STYPE=video; mk_legacy "$T6" 100 'url: "https://arxiv.org/abs/2401.00006"'; mk_extract "$T6" 2; unset STYPE
STYPE=idea; COMPLETENESS=partial; mk_pair "$T7" 30 5 'canonical_url: "https://example.com/t7"'; unset STYPE COMPLETENESS
mk_legacy "$T8" 100 'tier: bookmark'; mk_extract "$T8" 4
mk_legacy "$A2" 100 'url: "https://arxiv.org/abs/2401.00002"'; mk_extract "$A2" 5
mk_legacy "$A3" 700 'url: "https://arxiv.org/abs/2401.00002"' "supersedes: \"$A2\""; mk_extract "$A3" 5
mk_legacy "$A4" 100 'url: "https://arxiv.org/abs/2401.00004"' 'status: bookmark' 'tier: bookmark'
mk_legacy "$S1" 10 'fetch_status: failed-timeout'; mk_extract "$S1" 5
# Q4 — a non-numeric and a missing rating
mk_legacy "$L17" 350; mk_extract "$L17" high
mk_legacy "$L18" 350; mk_extract "$L18" -
# a file that cannot be read (owner permissions)
mk_legacy "$U1" 350; chmod 000 "$VAULT/Sources/$U1/source.md"
# fillers: the budget test needs a walk that takes longer than a millisecond
for i in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40; do
  mk_legacy "2026-08-01_f00000$i" 350; mk_extract "2026-08-01_f00000$i" 8
done
# I3 — index.md with a hand-edited row (L2), a correct row (M1), a row of a vanished folder; a hand-edits report
cat > "$VAULT/index.md" <<EOF
---
type: index
---

## Recent Sources

- [[Sources/$L2/extract|$L2]] (article, x, rating 5) — a hand remark
- [[Sources/$M1/extract|$M1]] (article, x, rating 8)
- [[Sources/2026-01-01_gone0000/extract|Gone]] (article, x)
EOF
printf '# rows\n\n<!-- mnemon:hand-edit folder=%s sha256=%064d -->\nrow one\n\n<!-- mnemon:hand-edit folder=%s sha256=%064d -->\nrow two\n' "$L2" 1 "$L2" 2 > "$VAULT/_inputs/index-hand-edits-2026-10-10.md"
# I7 — the ledger: a consistent receipt, one whose hash predates the edit, one naming a vanished revision,
# an unfinished one, an unreadable one
mk_receipt "$LEDGER" r-m1 extracted "$M1" "$(sha_body "$VAULT/Sources/$M1/source.md")"
mk_receipt "$LEDGER" r-m2 extracted "$M2" "$M2_SHA_AT_CAPTURE"
mk_receipt "$LEDGER" r-gone source-published 2026-01-01_gone0000 -
mk_receipt "$LEDGER" r-started started "$M3" -
printf 'not json\n' > "$LEDGER/captures/r-bad.json"
: > "$LEDGER/lock"
# I8/H1/H2 — Synthesis: agent drafts of several ages (one by mtime), unknown authorship, a human draft, a
# stable note integrating M1, seeds, an unknown status word
mk_synth "Draft Old" draft atlas 2026-06-01
mk_synth "Draft New" draft atlas 2026-10-10
mk_synth "Empty Atlas" - atlas 2026-07-01
mk_synth "Unknown One" - - 2026-05-01
mk_synth "Human Draft" draft - 2026-05-01
mk_synth "No Created" draft atlas -
mk_synth "Stable One" stable atlas 2026-05-01 "see [[Sources/$M1/extract|M1]], ![[Sources/$L2/source]] and [[Other Note]]"
mk_synth "Seed A" seed - 2026-06-14
mk_synth "Seed B" seed atlas 2026-10-01
mk_synth "Seed Weird" weird atlas 2026-10-01
# H3 — declared queues (over and within the radius), parked forever, parked until a future date, parking
# that expired, two untyped files, pending-writes
mk_queue recapture-queue 'type: queue' 'since: 2026-09-01'
mk_queue fresh-queue 'type: queue' 'since: 2026-10-10'
mk_queue parked-forever 'type: queue' 'since: 2026-01-01' 'parked: 2026-09-01' 'parked_why: later'
mk_queue parked-until 'type: queue' 'since: 2026-01-01' 'parked: 2026-09-01' 'parked_until: 2026-12-01'
mk_queue parked-expired 'type: queue' 'since: 2026-01-01' 'parked: 2026-09-01' 'parked_until: 2026-10-01'
mk_queue tabs; mk_queue bookmarks 'type: note'
printf '{}\n' > "$VAULT/_inputs/pending-writes/one.json"
# charter (contexts personal + work, a radius with one unparsable line), reader-context 15 d old with an
# invalid threshold, Protocol stamped behind, CLAUDE.md unstamped
printf '# Charter\n\n## Contexts\n\n### personal\n### Work — the day job\n\n```\n### fenced — ignored\n```\n\n## Radius\nseeds: 10 open · 30 days\ndrafts: 5 unratified, 45 days\nqueues: 30 days\nseeds: lots\n\n## Value scale\n\ntext\n' > "$VAULT/_meta/charter.md"
printf -- '---\ntype: reader-context\nsynced: 2026-10-01\nstaleness_threshold_days: abc\n---\n# me\n' > "$VAULT/_meta/reader-context.md"
printf '<!-- mnemon_guide: 1 -->\n# Protocol\n' > "$VAULT/_meta/Protocol.md"
printf '# Vault\n' > "$VAULT/CLAUDE.md"
DIRTY="$VAULT"

echo "--- the dirty vault: text lines, one per check, severity by cause ---"
doc --archive "$ARCHIVE" --state-dir "$LEDGER"
assert_eq "$rc" "0" "vault-doctor.py exits 0 (the dispatcher derives the exit from fail lines)"
assert_eq "$(printf '%s\n' "$out" | grep -c '^fail \|^warn \|^ok ' || true)" "$(printf '%s\n' "$out" | grep -c . || true)" "every line is ok|warn|fail <name> — <detail>"
assert_eq "$(printf '%s\n' "$out" | grep -c ' — ' || true)" "20" "19 checks and the radius/parse diagnostic"
assert_eq "$(status_of store/source-integrity)" "fail" "I1 fail: an edited managed body, a foreign `_width`, a partial key set"
assert_contains "$(line_of store/source-integrity)" "2 do not ($M2, $M3)" "I1 names the managed bodies that do not match (edited; `_width`)"
assert_contains "$(line_of store/source-integrity)" "1 damaged ($D1)" "I1: a partial managed key set is damaged (fail), not legacy"
assert_contains "$(line_of store/source-integrity)" "1 header not closed ($L3)" "I1 legacy: a header without its closing --- is reported"
assert_contains "$(line_of store/source-integrity)" "1 empty body ($L4)" "I1 legacy: an empty body is reported"
assert_contains "$(line_of store/source-integrity)" "1 with \`_\` keys ($L1)" "I1 legacy: a foreign key warns (counted apart from managed)"
assert_eq "$(status_of store/identity)" "fail" "I2 fail: two current revisions of one resource"
assert_contains "$(line_of store/identity)" "2 groups share a resource" "I2: one group by resource_sha256 (managed), one by canonical URL (legacy ↔ managed, utm stripped)"
assert_contains "$(line_of store/identity)" "$L5 + $M6" "I2: the legacy/managed pair of one canonical URL is named"
assert_contains "$(line_of store/identity)" "inconsistent 1" "I2: a managed resource_sha256 group with differing canonical_url is inconsistent"
assert_contains "$(line_of store/identity)" "unkeyed " "I2: non-url legacy revisions are counted as unkeyed, not checked"
assert_eq "$(status_of store/index)" "warn" "I3 warn: index.md drifted"
assert_contains "$(line_of store/index)" "1 rows differ" "I3: the hand-edited row differs from the regenerated one"
assert_contains "$(line_of store/index)" "1 extra" "I3: the row of a vanished folder is extra"
assert_contains "$(line_of store/index)" "hand-edits report 2026-10-10: 2 rows" "I3: the newest hand-edits report and its row count"
assert_eq "$(status_of store/supersedes)" "fail" "I4 fail: a cycle and a dangling target"
assert_contains "$(line_of store/supersedes)" "8 edges, 1 dangling, 1 cycles — current count incomplete" "I4: edges, dangling, cycles; current marked incomplete"
assert_contains "$(line_of store/supersedes)" "1 cross-resource edge ($L10 → $L9)" "I4: an edge between different resources is counted and named"
assert_contains "$(line_of store/supersedes)" "fan-out 1 ($L11)" "I4: fan-out counted"
assert_contains "$(line_of store/supersedes)" "1 extract.md carry a stored \`superseded\` (legacy word)" "I4: a stored superseded word is a legacy word"
assert_eq "$(status_of store/archive)" "fail" "I5 fail: managed revisions with raw_store archive and no raw"
assert_contains "$(line_of store/archive)" "3 references resolve (1 legacy suffix form, 2 unarchivable)" "I5: plain and legacy suffix forms resolve, unarchivable is skipped"
assert_contains "$(line_of store/archive)" "2 managed without raw ($M8, $M9)" "I5: no archive: line, or one that does not resolve, on a managed archive-store revision"
assert_contains "$(line_of store/archive)" "1 unresolved ($L16)" "I5: a legacy reference that does not resolve warns"
assert_contains "$(line_of store/archive)" "2 archive files unowned: 2026-01-01_gone0000.report.json, orphan.txt" "I5: a sidecar of an existing revision and its .site/ are owned; the rest is unowned"
assert_eq "$(status_of store/completeness)" "fail" "I6 fail: a managed url revision without completeness"
assert_contains "$(line_of store/completeness)" "blocked 1" "I6: the distribution shows blocked"
assert_contains "$(line_of store/completeness)" "2 missing or invalid ($M10, $M12)" "I6: missing and an unknown word are schema failures"
assert_contains "$(line_of store/completeness)" "legacy url revisions without a record" "I6: legacy url revisions are 'without a record', never unknown"
assert_eq "$(status_of store/ledger)" "fail" "I7 fail: a receipt contradicts the files"
assert_contains "$(line_of store/ledger)" "2 contradict the files (r-gone, r-m2)" "I7: a receipt whose text_sha256 differs from the body, one naming a missing revision"
assert_contains "$(line_of store/ledger)" "without a receipt" "I7: managed revisions without a receipt warn (state lost, replay restores)"
assert_contains "$(line_of store/ledger)" "1 incomplete; 1 unreadable" "I7: a started receipt is a count, an unreadable one too"
assert_eq "$(status_of store/status-vocabulary)" "warn" "I8 warn"
assert_contains "$(line_of store/status-vocabulary)" "legacy words 1 (superseded 1)" "I8: the legacy list is counted apart"
assert_contains "$(line_of store/status-vocabulary)" "empty 1" "I8: an empty status is counted"
assert_contains "$(line_of store/status-vocabulary)" "unknown 1 (bogus 1)" "I8: an unknown word is named (`processed` is on the tolerated legacy list, S2)"
assert_contains "$(line_of store/status-vocabulary)" "synthesis: seed 2 · draft 4 · stable 1 · empty 2 · unknown 1 (weird 1)" "I8: the synthesis vocabulary"
assert_eq "$(status_of store/context)" "warn" "I9 warn: a managed context outside the charter"
assert_contains "$(line_of store/context)" "1 outside: $M14" "I9: the managed revision is named"
assert_contains "$(line_of store/context)" "charter (personal, work)" "I9: the charter's contexts (first token, lowercased, fences skipped)"
assert_contains "$(line_of store/context)" "legacy vault names not compared (personal 1)" "I9: legacy capture_context.vault is counted, not compared"
assert_eq "$(status_of store/extract-consistency)" "fail" "I10 fail"
assert_contains "$(line_of store/extract-consistency)" "1 do not ($E1)" "I10: an extract without a source"
assert_contains "$(line_of store/extract-consistency)" "1 disagree ($M15)" "I10: a managed pair disagreeing on revision"
assert_contains "$(line_of store/extract-consistency)" "1 url differ ($M16)" "I10: a differing url warns"
assert_contains "$(line_of store/extract-consistency)" "1 without sections ($L4)" "I10: an extract without any of the five sections warns"
assert_eq "$(status_of store/guide-version)" "warn" "I11 warn"
assert_contains "$(line_of store/guide-version)" "vault CLAUDE.md unstamped, _meta/Protocol.md 1 (behind) (engine guide 2)" "I11: unstamped and behind, against the engine's guide version"
assert_eq "$(status_of data/thin)" "warn" "Q1 warn"
assert_contains "$(line_of data/thin)" "8 current bodies < 300 words (landing/stub 3 · article-short 1 · arxiv-abstract 1 · media 1 · unknown 1 · website/repo/document 1)" "Q1: the suspected classes (unarchivable/stub → arxiv → media → site types → article-short; conflicting → unknown)"
assert_contains "$(line_of data/thin)" "2 rated ≥ 7 — top: $T1 ★9 200w, $T3 ★7 150w" "Q1: rated ≥ 7 first, by rating then words"
assert_contains "$(line_of data/thin)" "completeness partial/blocked 2" "Q1: explicit partial/blocked are listed regardless of length (the idea is excluded from the length test only)"
assert_eq "$(status_of data/arxiv-abstract)" "warn" "Q2 warn"
assert_contains "$(line_of data/arxiv-abstract)" "2 current arXiv revisions below 600 words (1 superseded): $T3, $T6" "Q2: current suspects; the superseded one is counted apart; the bookmark is not counted"
assert_eq "$(status_of data/stubs)" "warn" "Q3 warn"
assert_contains "$(line_of data/stubs)" "5 placeholders: fetch_status failed 1 · bookmark 1 · < 40 words 4" "Q3: OR of the source.md features, unique revisions (the short-type idea counts here: Q3 has no type exclusion)"
assert_eq "$(status_of data/rating-scale)" "warn" "Q4 warn above the threshold"
assert_contains "$(line_of data/reader-context)" "synced 15 d ago, threshold 14 d" "Q5: age from synced, strictly over the threshold"
assert_contains "$(line_of data/reader-context)" "(staleness_threshold_days \`abc\` invalid, default 14 used)" "Q5: an invalid threshold falls back to 14 with a note"
assert_eq "$(status_of data/reader-context)" "warn" "Q5 warn"
assert_eq "$(status_of human/ratification)" "warn" "H1 warn: the oldest draft is over the radius"
assert_contains "$(line_of human/ratification)" "4 unratified agent drafts, oldest 137 d (1 by mtime) (radius 5 / 45 d); unknown authorship 1 (Synthesis/Unknown One)" "H1: drafted_by ∧ status ∈ {draft, empty}; age by created, mtime as a marked fallback; unknown authorship apart"
assert_eq "$(status_of human/seeds)" "warn" "H2 warn"
assert_contains "$(line_of human/seeds)" "2 seeds, oldest 124 d (radius 10 / 30 d)" "H2: seeds of any author, the radius from the charter"
assert_eq "$(status_of human/queues)" "warn" "H3 warn"
assert_contains "$(line_of human/queues)" "5 declared queues, oldest 288 d; 2 over radius (parked-expired.md, recapture-queue.md); parked 2; untyped 2 (bookmarks.md, tabs.md); pending-writes legacy 1 (radius 30 d)" "H3: age from since (not mtime), parked excluded until parked_until passes, untyped in the detail, pending-writes legacy"
assert_contains "$(line_of radius/parse)" 'warn radius/parse — cannot parse "seeds: lots" (defaults used)' "an unparsable radius line is a warning, never a silent default"
TEXT_OK="$(count_status ok)"; TEXT_WARN="$(count_status warn)"; TEXT_FAIL="$(count_status fail)"
TEXT_OUT="$out"

echo "--- the frozen JSON: schema, 19 stable names, summary = text, probed, items complete ---"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --json
assert_eq "$rc" "0" "--json exits 0"
JSON="$TEST_TMPDIR/dirty.json"; printf '%s' "$out" > "$JSON"
assert_eq "$(jf "$JSON" 'd["schema"]')" "mnemon-doctor-data/1" "schema mnemon-doctor-data/1"
assert_eq "$(jf "$JSON" 'd["instance"]')" "dirty" "instance = the vault's name without a config"
assert_eq "$(jf "$JSON" 'd["vault"]')" "$DIRTY" "vault path"
for name in store/source-integrity store/identity store/index store/supersedes store/archive store/completeness store/ledger store/status-vocabulary store/context store/extract-consistency store/guide-version data/thin data/arxiv-abstract data/stubs data/rating-scale data/reader-context human/ratification human/seeds human/queues; do
  assert_eq "$(jf "$JSON" "len([c for c in d['checks'] if c['name'] == '$name'])")" "1" "stable name present once: $name"
done
assert_eq "$(jf "$JSON" 'd["summary"]["ok"], d["summary"]["warn"], d["summary"]["fail"]')" "($TEXT_OK, $TEXT_WARN, $TEXT_FAIL)" "summary counts equal the text lines"
assert_eq "$(jf "$JSON" 'd["summary"]["probed"], d["summary"]["not_probed"]')" "(20, 0)" "every check probed (archive and ledger given)"
assert_eq "$(jf "$JSON" 'sorted(d["checks"][0].keys())')" "['counts', 'detail', 'fix', 'items', 'name', 'probed', 'reason', 'status', 'threshold']" "the check shape"
assert_eq "$(jf "$JSON" 'sorted(d["summary"].keys())')" "['fail', 'not_probed', 'ok', 'probed', 'warn']" "the summary shape"
assert_eq "$(jf "$JSON" 'sorted(k for k in d["snapshot"] if k in ("lock_held", "unreadable", "budget_s", "complete"))')" "['budget_s', 'complete', 'lock_held', 'unreadable']" "the snapshot fields"
assert_eq "$(jf "$JSON" 'd["snapshot"]["complete"], d["snapshot"]["lock_held"], d["snapshot"]["budget_s"]')" "(True, False, None)" "a full run: complete, lock free, no budget"
if [[ "$(id -u)" == 0 ]]; then
  echo "  ○ skipped: unreadable-file counting (running as root, permissions do not bite)"
else
  assert_eq "$(jf "$JSON" 'd["snapshot"]["unreadable"]')" "1" "a source.md that cannot be read is counted, never an exception"
  assert_eq "$(jf "$JSON" 'd["counters"]["unreadable_folders"]')" "1" "...and its folder is neither managed nor legacy"
fi
assert_eq "$(jc "$JSON" data/thin '["counts"]["thin"]')" "8" "counts.thin"
assert_eq "$(jc "$JSON" data/thin '["threshold"]')" "{'thin_words': 300, 'stub_words': 40, 'rating_hi': 7}" "Q1 threshold recorded"
assert_eq "$(jc "$JSON" data/thin '["fix"]')" "mnemon add --recapture" "Q1 fix"
assert_eq "$(jc "$JSON" data/thin '["items"][0]["id"], d and [c for c in d["checks"] if c["name"] == "data/thin"][0]["items"][0]["class"]')" "('$T1', 'article-short')" "items are complete and ranked (★9 first)"
assert_eq "$(jc "$JSON" data/thin '["items"][1]["features"]')" "['arxiv', 'completeness:none']" "features per item (arXiv host; a legacy url has no completeness record)"
assert_eq "$(jc "$JSON" data/thin "[\"items\"][-1][\"id\"], [c for c in d['checks'] if c['name'] == 'data/thin'][0]['items'][-1]['class']")" "('$M11', 'completeness')" "the partial/blocked list follows the thin list"
assert_eq "$(jf "$JSON" "[i for i in [c for c in d['checks'] if c['name'] == 'data/stubs'][0]['items'] if i['id'] == '$S1'][0]['features']")" "['fetch_status failed', '< 40 words']" "Q3: one revision carries both features (independent of extract.status)"
assert_eq "$(jc "$JSON" store/identity '["fix"]')" "mnemon audit --canonical" "I2 fix"
assert_eq "$(jc "$JSON" store/index '["fix"]')" "mnemon reindex" "I3 fix"
assert_eq "$(jc "$JSON" store/ledger '["fix"]')" "mnemon add --resume" "I7 fix"
assert_eq "$(jc "$JSON" store/source-integrity '["fix"]')" "operator" "I1 fix"
assert_eq "$(jc "$JSON" human/ratification '["items"][0]["reason"]')" "unratified draft 137 d" "H1 items carry the age"
assert_eq "$(jc "$JSON" human/ratification '["threshold"]')" "{'count': 5, 'days': 45, 'defaults': False}" "H1 threshold from the charter"
assert_eq "$(jc "$JSON" human/queues '["counts"]["parked"]')" "2" "H3 parked count"
rated="$(jc "$JSON" data/rating-scale '["counts"]["rated"]')"; hi="$(jc "$JSON" data/rating-scale '["counts"]["hi"]')"; cur="$(jc "$JSON" data/rating-scale '["counts"]["current"]')"
assert_contains "$(printf '%s\n' "$TEXT_OUT" | grep '^warn data/rating-scale')" "($hi/$rated, coverage $rated/$cur); threshold 20 %" "Q4: the denominator is the rated current revisions, coverage shown"
assert_eq "$(jf "$JSON" 'd["counters"]["folders"], d["counters"]["managed"], d["counters"]["damaged"], d["counters"]["legacy"]')" "(92, 17, 1, 72)" "counters: folders, managed, damaged, legacy (the unreadable one in none of the classes)"
assert_eq "$(jf "$JSON" 'd["counters"]["superseded"], d["counters"]["current"], d["counters"]["current_incomplete"]')" "(6, 85, True)" "counters: superseded (cycle members included), current, current incomplete"
assert_eq "$(jf "$JSON" 'd["counters"]["extracted"], d["counters"]["captured"]')" "(88, 4)" "counters: extracted = folders with extract.md; captured = source.md without one"
assert_eq "$(jf "$JSON" 'd["counters"]["integrated"], d["counters"]["links_unresolved"]')" "(2, 1)" "the S2 link resolver: [[Sources/<rev>/extract…]] and ![[…/source]] resolve, [[Other Note]] does not"
assert_eq "$(jf "$JSON" 'd["counters"]["seeds"], d["counters"]["drafts_unratified"], d["counters"]["unknown_authorship"]')" "(2, 4, 1)" "counters: seeds, unratified drafts, unknown authorship"

echo "--- --list <check>: full name or the part after /, every item, tab-separated ---"
doc --list thin
assert_eq "$rc" "0" "--list thin exits 0"
assert_eq "$(printf '%s\n' "$out" | head -1)" "$T1	article-short	9	200" "the first thin item: <id> <class> <rating> <words>"
assert_eq "$(printf '%s\n' "$out" | grep -c .)" "10" "every item (8 thin + 2 completeness), one per line"
LIST_THIN="$out"
doc --list data/thin
assert_eq "$out" "$LIST_THIN" "--list data/thin = --list thin"
doc --list source-integrity
assert_contains "$out" "$L3	header not closed within 256 KB	-	-" "an item without rating/words prints - and its reason"
doc --list nosuch
assert_eq "$rc" "2" "--list of an unknown check exits 2"
assert_contains "$err" "no check named 'nosuch'" "...and says so"

echo "--- the one-line brief: numbers equal --json; a budget that cannot be met is said, not hidden ---"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --brief
assert_eq "$rc" "0" "--brief exits 0"
assert_eq "$(printf '%s\n' "$out" | grep -c .)" "1" "--brief is one physical line"
assert_eq "$out" "Dirty · 92 sources (17 managed · 85 current · 6 superseded) · 88 extracted · 10 synthesis (2 seeds / 124 d · 4 drafts / 137 d) · data: $TEXT_FAIL fail · $TEXT_WARN warn · probed 20/20" "the brief's numbers are the JSON's counters and summary"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --brief --budget 0.001
assert_eq "$rc" "0" "--budget 0.001 exits 0"
assert_contains "$out" "Dirty · data: not probed (budget 0.001 s exceeded at " "a budget the walk cannot meet: not probed, with the folder count — never 0 fail"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --json --budget 0.001
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["snapshot"]["complete"], d["summary"]["probed"], d["summary"]["not_probed"], d["checks"][0]["probed"], d["checks"][0]["status"])')" "False 1 19 False ok" "--json under the budget: snapshot incomplete, every check not probed with status ok (the radius diagnostic does not depend on the walk)"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --brief --json
assert_contains "$out" '"schema": "mnemon-doctor-data/1"' "--brief --json carries the schema"
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("checks" in d, d["counters"]["folders"], d["summary"]["fail"])')" "False 92 $TEXT_FAIL" "--brief --json: counters and summary, no checks"

echo "--- the vault lock: probed, never taken; held → the snapshot is marked ---"
READY="$TEST_TMPDIR/holder.ready"
python3 - "$LEDGER/lock" "$READY" <<'PY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(60)
PY
HOLDER=$!
wait_file "$READY" 10
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --brief
assert_contains "$out" " (snapshot may be partial)" "lock held → the brief says the snapshot may be partial"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --json
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["snapshot"]["lock_held"], d["snapshot"]["lock"])')" "True held" "lock held → snapshot.lock_held"
kill "$HOLDER" 2>/dev/null || true; wait "$HOLDER" 2>/dev/null || true
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --brief
assert_not_contains "$out" "(snapshot may be partial)" "lock released → no mark (the probe never kept it)"

echo "--- not probed, honestly: archive_dir unset / inaccessible; no ledger on a reader host / on the writer host ---"
doc --state-dir "$LEDGER"
assert_eq "$(line_of store/archive)" "ok store/archive — not probed: archive_dir not configured" "I5 without archive_dir: ok, not probed"
doc --state-dir "$LEDGER" --archive "$TEST_TMPDIR/no-such-archive"
assert_eq "$(line_of store/archive)" "warn store/archive — not probed: archive_dir not accessible: $TEST_TMPDIR/no-such-archive" "I5 with an archive_dir that is not there: warn, not probed"
doc --archive "$ARCHIVE"
assert_contains "$(line_of store/ledger)" "ok store/ledger — not probed: no ledger on this host" "I7 without a ledger (a reader host, a fresh copy): ok, not probed"
doc --archive "$ARCHIVE" --writer-host "$THIS_HOST"
assert_contains "$(line_of store/ledger)" "warn store/ledger — not probed: no ledger on this host" "I7 without a ledger on the writer host (managed revisions exist): warn, not probed"
doc --archive "$ARCHIVE" --json
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); c=[x for x in d["checks"] if x["name"]=="store/ledger"][0]; print(c["probed"], c["status"], c["reason"][:22]); print(d["summary"]["probed"], d["summary"]["not_probed"])')" "False ok no ledger on this host
19 1" "probed: false with the reason; the summary counts it as not probed"

echo "--- the doctor writes nothing: vault, ledger and archive are byte-for-byte the same before and after ---"
before="$(snap "$DIRTY" "$LEDGER" "$ARCHIVE")"
doc --archive "$ARCHIVE" --state-dir "$LEDGER" >/dev/null
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --json >/dev/null
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --list thin >/dev/null
doc --archive "$ARCHIVE" --state-dir "$LEDGER" --brief >/dev/null
run python3 "$LIBV" --vault "$DIRTY" --state-dir "$LEDGER" --archive "$ARCHIVE" --status-counts
after="$(snap "$DIRTY" "$LEDGER" "$ARCHIVE")"
assert_eq "$after" "$before" "every path's mtime, inode and size unchanged; no new file anywhere (doctor, --json, --list, --brief, lib_vault)"

echo "--- the clean vault: 19 ok lines, probed 19/19, exit 0 ---"
fresh_vault clean
CARCH="$TEST_TMPDIR/clean-archive"; CLEDGER="$TEST_TMPDIR/clean-ledger"; mkdir -p "$CARCH" "$CLEDGER/captures"
K1=2026-10-01_k1000001; K2=2026-10-02_k2000002; K3=2026-10-03_k3000003
RAW_STORE=archive
mk_pair "$K1" 350 5 'canonical_url: "https://example.com/k1"' "archive: \"$K1.html\""
mk_pair "$K2" 350 6 'canonical_url: "https://example.com/k2"' "archive: \"$K2.html\""
mk_pair "$K3" 350 6 'canonical_url: "https://example.com/k3"' "archive: \"$K3.html\""
unset RAW_STORE
for k in "$K1" "$K2" "$K3"; do
  printf 'raw\n' > "$CARCH/$k.html"; printf '{}\n' > "$CARCH/$k.report.json"
  mk_receipt "$CLEDGER" "r-$k" extracted "$k" "$(sha_body "$VAULT/Sources/$k/source.md")"
done
mk_synth "Stable" stable - 2026-09-01 "[[Sources/$K1/extract|K1]]"
mk_synth "Seed" seed atlas 2026-10-10
mk_queue inbox 'type: queue' 'since: 2026-10-10'
printf '# Charter\n\n## Contexts\n\n### personal\n\n## Radius\nseeds: 10 open · 30 days\ndrafts: 5 unratified · 45 days\nqueues: 30 days\n' > "$VAULT/_meta/charter.md"
printf -- '---\nsynced: 2026-10-10\nstaleness_threshold_days: 14\n---\n# me\n' > "$VAULT/_meta/reader-context.md"
printf -- '---\nmnemon_guide: 2\n---\n# Vault\n' > "$VAULT/CLAUDE.md"
printf '<!-- mnemon_guide: 2 -->\n# Protocol\n' > "$VAULT/_meta/Protocol.md"
run python3 "$REINDEX" "$VAULT" --no-hand-edit-report
assert_eq "$rc" "0" "the clean vault's index.md is generated by reindex"
CLEAN="$VAULT"
doc --archive "$CARCH" --state-dir "$CLEDGER"
assert_eq "$rc" "0" "exit 0"
assert_eq "$(count_status ok)" "19" "19 ok lines"
assert_eq "$(count_status warn) $(count_status fail)" "0 0" "no warn, no fail"
assert_eq "$(line_of store/source-integrity)" "ok store/source-integrity — 3 managed bodies match text_sha256; 0 damaged; 0 legacy headers closed, bodies present, no \`_\` keys" "I1 ok line"
assert_eq "$(line_of store/index)" "ok store/index — 0 rows differ, 0 missing, 0 extra; no hand-edits report" "I3 ok: the regenerated rows equal reindex's"
assert_eq "$(line_of store/ledger)" "ok store/ledger — 3 managed revisions, 3 receipts consistent; 0 incomplete" "I7 ok line"
assert_eq "$(line_of store/guide-version)" "ok store/guide-version — vault CLAUDE.md and _meta/Protocol.md stamped 2 (engine guide 2)" "I11 ok: both copies at the engine's version"
assert_eq "$(line_of store/extract-consistency)" "ok store/extract-consistency — 3 extracts have a source; 3 managed pairs agree on capture_id/revision; 0 without sections" "I10 ok line"
assert_eq "$(line_of data/rating-scale)" "ok data/rating-scale — 0 % of rated current revisions ≥ 7 (0/3, coverage 3/3); threshold 20 %" "Q4 ok line"
assert_eq "$(line_of human/queues)" "ok human/queues — 1 declared queues, oldest 6 d; untyped 0; pending-writes legacy 0 (radius 30 d)" "H3 ok line"
doc --archive "$CARCH" --state-dir "$CLEDGER" --brief --instance clean
assert_eq "$out" "Clean · 3 sources (3 managed · 3 current · 0 superseded) · 3 extracted · 2 synthesis (1 seeds / 6 d · 0 drafts) · data: 0 fail · 0 warn · probed 19/19" "the brief of a clean vault"

echo "--- an empty vault, no Synthesis/, no _inputs/: ok lines, exit 0, no exception ---"
VAULT="$TEST_TMPDIR/empty"; mkdir -p "$VAULT/Sources"
doc
assert_eq "$rc" "0" "exit 0"
assert_eq "$(printf '%s\n' "$out" | grep -c ' — ' || true)" "19" "19 lines"
assert_eq "$(printf '%s\n' "$out" | grep -c 'error:' || true)" "0" "no exception surfaced"
assert_eq "$(line_of store/source-integrity)" "ok store/source-integrity — 0 managed bodies match text_sha256; 0 damaged; 0 legacy headers closed, bodies present, no \`_\` keys" "I1 on nothing"
assert_eq "$(line_of store/index)" "ok store/index — index.md missing; 0 rows to generate; no hand-edits report" "I3: no index and nothing to index is fine"
assert_eq "$(line_of human/ratification)" "ok human/ratification — 0 unratified agent drafts (radius 5 / 45 d) (defaults); unknown authorship 0" "H1 without a charter: engine defaults, marked"
assert_eq "$(line_of human/seeds)" "ok human/seeds — 0 seeds (radius 10 / 30 d) (defaults)" "H2 without a charter"
assert_eq "$(line_of human/queues)" "ok human/queues — 0 declared queues; untyped 0; pending-writes legacy 0 (radius 30 d) (defaults)" "H3 without _inputs/"
assert_contains "$(line_of data/reader-context)" "warn data/reader-context — no freshness data (missing " "Q5: no file = no freshness data (absence is not freshness)"
assert_contains "$(line_of store/context)" "ok store/context — 0 managed contexts ∈ charter (personal); no charter (default personal)" "I9 without a charter: personal"
doc --brief
assert_eq "$out" "Empty · 0 sources (0 managed · 0 current · 0 superseded) · 0 extracted · 0 synthesis (0 seeds · 0 drafts) · data: 0 fail · 2 warn · probed 17/19" "the brief of an empty vault (archive and ledger not probed)"

echo "--- I4: a cross-resource edge alone warns; current is complete ---"
fresh_vault i4
mk_legacy 2026-09-01_aaaa0001 350 'url: "https://example.com/a"'; mk_extract 2026-09-01_aaaa0001 5
mk_legacy 2026-09-02_bbbb0002 350 'url: "https://example.com/b"' 'supersedes: "2026-09-01_aaaa0001"'; mk_extract 2026-09-02_bbbb0002 5
doc
assert_eq "$(line_of store/supersedes)" "warn store/supersedes — 1 edges, 0 dangling, 0 cycles; 1 cross-resource edge (2026-09-02_bbbb0002 → 2026-09-01_aaaa0001)" "I4 warn only; no incomplete mark"
assert_eq "$(line_of store/identity)" "ok store/identity — 1 current revisions, 0 share a resource (resource_sha256 · canonical URL v1); unkeyed 0 (non-url legacy)" "I2: the superseded revision is not current"

echo "--- Q4: the denominator and the coverage; the threshold is strict ---"
fresh_vault q4
mk_legacy 2026-09-01_q0000001 350; mk_extract 2026-09-01_q0000001 8
mk_legacy 2026-09-02_q0000002 350; mk_extract 2026-09-02_q0000002 5
mk_legacy 2026-09-03_q0000003 350; mk_extract 2026-09-03_q0000003 high
mk_legacy 2026-09-04_q0000004 350; mk_extract 2026-09-04_q0000004 -
doc
assert_eq "$(line_of data/rating-scale)" "warn data/rating-scale — 50 % of rated current revisions ≥ 7 (1/2, coverage 2/4); threshold 20 %" "Q4: a non-numeric and a missing rating are outside the denominator"
doc --rating-hi-share 50
assert_eq "$(status_of data/rating-scale)" "ok" "Q4: equal to the threshold is not over it"
doc --list rating-scale
assert_eq "$out" "2026-09-01_q0000001	rated ≥ 7	8	350" "--list rating-scale: the ≥ 7 revisions"

echo "--- Q5: no synced, a future date, a custom threshold ---"
fresh_vault q5
printf -- '---\ntype: reader-context\n---\n# me\n' > "$VAULT/_meta/reader-context.md"
doc
assert_contains "$(line_of data/reader-context)" "warn data/reader-context — no freshness data (\`synced:\` missing or invalid in " "Q5: no synced"
printf -- '---\nsynced: 2027-01-01\n---\n' > "$VAULT/_meta/reader-context.md"
doc
assert_contains "$(line_of data/reader-context)" "warn data/reader-context — no freshness data (\`synced: 2027-01-01\` is in the future" "Q5: a future date"
printf -- '---\nsynced: 2026-09-01\nstaleness_threshold_days: 60\n---\n' > "$VAULT/_meta/reader-context.md"
doc
assert_contains "$(line_of data/reader-context)" "ok data/reader-context — synced 45 d ago, threshold 60 d" "Q5: the file's own threshold"
doc --reader-context "$TEST_TMPDIR/elsewhere.md"
assert_contains "$(line_of data/reader-context)" "no freshness data (missing $TEST_TMPDIR/elsewhere.md)" "Q5: --reader-context names the file"

echo "--- the radius: partial section, a line without the count, --charter ---"
fresh_vault radius
mk_synth "Seed" seed - 2026-09-01
printf '# C\n\n## Radius\nseeds: 20 days\n' > "$TEST_TMPDIR/charter-partial.md"
doc --charter "$TEST_TMPDIR/charter-partial.md"
assert_eq "$(line_of human/seeds)" "warn human/seeds — 1 seeds, oldest 45 d (radius 10 / 20 d)" "seeds: the count is optional (engine default), the days are the charter's"
assert_eq "$(line_of human/ratification)" "ok human/ratification — 0 unratified agent drafts (radius 5 / 45 d) (defaults); unknown authorship 0" "drafts absent from the section: defaults, marked"
assert_eq "$(printf '%s\n' "$out" | grep -c 'radius/parse' || true)" "0" "nothing to report when every line parses"
printf '# C\n\n## Radius\nseeds: 10 open; 30 days\ndrafts: five unratified · 45 days\n' > "$TEST_TMPDIR/charter-bad.md"
doc --charter "$TEST_TMPDIR/charter-bad.md"
assert_eq "$(line_of radius/parse)" 'warn radius/parse — cannot parse "drafts: five unratified · 45 days" (defaults used)' "a line off the grammar is named"
assert_contains "$(line_of human/ratification)" "(radius 5 / 45 d) (defaults)" "...and that key takes the defaults, marked"
assert_contains "$(line_of human/seeds)" "(radius 10 / 30 d)" "...while the parsable line holds (; as the separator)"

echo "--- H3: a declared queue without since; parked_until in the past ends the parking ---"
fresh_vault h3
mk_queue nosince 'type: queue'
mk_queue expired 'type: queue' 'since: 2026-01-01' 'parked: 2026-06-01' 'parked_until: 2026-06-30'
mk_queue parked 'type: queue' 'since: 2026-01-01' 'parked: 2026-06-01'
doc --list queues
assert_contains "$out" "_inputs/nosince.md	declared queue without a valid \`since:\`	-	-" "a declared queue needs since"
assert_contains "$out" "_inputs/expired.md	3 items · 288 d	-	-" "expired parking: the age runs from since again"
assert_contains "$out" "_inputs/parked.md	parked (indefinitely)	-	-" "parked without parked_until: indefinitely"
doc
assert_contains "$(line_of human/queues)" "3 declared queues, oldest 288 d; 2 over radius (expired.md, nosince.md); parked 1; untyped 0" "H3 counts"

echo "--- the dispatcher: doctor --data, --json envelope, --list, exit codes; status --brief; the one Extracted ---"
printf 'instance: dirty\nvault_path: %s\narchive_dir: %s\nreader_context_path: %s/_meta/reader-context.md\nsearch_provider: grep\n' "$DIRTY" "$ARCHIVE" "$DIRTY" > "$TEST_TMPDIR/dirty.yaml"
printf 'instance: clean\nvault_path: %s\narchive_dir: %s\nreader_context_path: %s/_meta/reader-context.md\nsearch_provider: grep\n' "$CLEAN" "$CARCH" "$CLEAN" > "$TEST_TMPDIR/clean.yaml"
printf 'instance: gone\nvault_path: %s/no-such-vault\nsearch_provider: grep\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/gone.yaml"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" doctor --data
assert_eq "$rc" "1" "doctor --data exits 1 on a data fail"
assert_contains "$out" "fail store/source-integrity — " "the data group follows the engine checks, in the dispatcher's line shape"
assert_contains "$out" "ok config — $TEST_TMPDIR/dirty.yaml" "...after the engine checks"
assert_eq "$(printf '%s\n' "$out" | grep -c '^[a-z]* \(store\|data\|human\|radius\)/' || true)" "20" "20 data lines"
assert_eq "$(printf '%s\n' "$out" | grep -c '^fail doctor/data' || true)" "0" "no tool error"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" doctor
assert_eq "$(printf '%s\n' "$out" | grep -c '^[a-z]* \(store\|data\|human\)/' || true)" "0" "without --data the doctor is unchanged"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" doctor --data --json
assert_eq "$rc" "1" "doctor --data --json exits 1 on a data fail"
ENV="$TEST_TMPDIR/envelope.json"; printf '%s' "$out" > "$ENV"
assert_eq "$(jf "$ENV" 'd["exit"]')" "1" "the envelope's exit"
assert_eq "$(jf "$ENV" 'd["data"]["schema"]')" "mnemon-doctor-data/1" "the tool's document is embedded as data"
assert_eq "$(jf "$ENV" 'len([c for c in d["checks"] if c["name"] == "store/source-integrity" and c["status"] == "fail"])')" "1" "...and its checks are in checks[] too (uniform)"
assert_eq "$(jf "$ENV" 'd["data"]["summary"]["probed"], d["data"]["summary"]["not_probed"]')" "(19, 1)" "through the config: archive probed, the ledger (XDG state of this test) not"
assert_not_contains "$out" "ok config —" "--json emits no text lines"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" doctor --list thin
assert_eq "$rc" "0" "doctor --list thin exits 0"
assert_eq "$out" "$LIST_THIN" "doctor --list thin prints only that list, equal to the tool's"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" doctor --data --list data/thin
assert_eq "$out" "$LIST_THIN" "--data --list data/thin: the same list"
run "$MNEMON" --config "$TEST_TMPDIR/clean.yaml" doctor --data
fails="$(printf '%s\n' "$out" | grep -c '^fail ' || true)"; depfails="$(printf '%s\n' "$out" | grep -c '^fail \(deps\|fence\|writer\)' || true)"
assert_eq "$fails" "$depfails" "the clean vault adds no fail line (only this host's deps/fence may)"
if [[ "$fails" -eq 0 ]]; then assert_eq "$rc" "0" "doctor --data exits 0 without a fail"; else assert_eq "$rc" "1" "doctor --data exits 1 because of this host's deps"; fi
assert_eq "$(printf '%s\n' "$out" | grep -c '^ok \(store\|data\|human\)/' || true)" "19" "19 data lines ok through the dispatcher (the ledger one is ok, not probed: this test's XDG state holds no captures)"
assert_contains "$out" "ok store/ledger — not probed: no ledger on this host" "...the ledger line says so"
run "$MNEMON" --config "$TEST_TMPDIR/gone.yaml" doctor --data
assert_eq "$rc" "1" "a vault that is not there: exit 1"
assert_contains "$out" "fail doctor/data — error: vault-doctor: vault not found: $TEST_TMPDIR/no-such-vault" "an error in the tool is one fail line, the engine checks are kept"
assert_contains "$out" " deps — " "...the engine checks are still there"
run "$MNEMON" --config "$TEST_TMPDIR/clean.yaml" status --brief
assert_eq "$rc" "0" "status --brief exits 0"
assert_eq "$(printf '%s\n' "$out" | grep -c .)" "1" "status --brief is one physical line"
assert_contains "$out" "Clean · 3 sources (3 managed · 3 current · 0 superseded) · 3 extracted · 2 synthesis (1 seeds / " "...with the instance's counters"
assert_contains "$out" " · data: 0 fail · 0 warn · probed 18/19" "...and the data summary (the ledger not probed here)"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" status --brief --budget 0.001
assert_contains "$out" "Dirty · data: not probed (budget 0.001 s exceeded at " "status --brief --budget: honest"
run "$MNEMON" --config "$TEST_TMPDIR/clean.yaml" status --brief --json
assert_eq "$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["instance"], d["counters"]["folders"], d["summary"]["fail"], "checks" in d)')" "clean 3 0 False" "status --brief --json: counters + summary"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" status
assert_eq "$rc" "0" "status exits 0"
assert_contains "$out" "Extracted: 88" "status takes Extracted from lib_vault: folders with extract.md (88), not the word count"
assert_contains "$out" "By status: extracted 85 · dismissed 0 · legacy words 1 · empty 1 · unknown 1" "status shows the stored words as a breakdown"
assert_contains "$out" "Superseded: " "status keeps its other counters"
run python3 "$LIBV" --vault "$DIRTY" --status-counts
assert_contains "$out" "extracted=88" "lib_vault --status-counts: the same number"
before="$(snap "$DIRTY" "$ARCHIVE" "$CLEAN" "$CARCH")"
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" doctor --data --json
run "$MNEMON" --config "$TEST_TMPDIR/clean.yaml" status --brief
run "$MNEMON" --config "$TEST_TMPDIR/dirty.yaml" status
after="$(snap "$DIRTY" "$ARCHIVE" "$CLEAN" "$CARCH")"
assert_eq "$after" "$before" "the dispatcher paths write nothing into the vaults or the archives either"

echo "--- the usage text ---"
run "$MNEMON" help
assert_contains "$out" "doctor [--instance <name>] [--json] [--data [--list <check>]]" "help lists doctor --data / --list"
assert_contains "$out" "status [--brief [--json] [--budget <s>]]" "help lists status --brief"
run "$MNEMON" --config "$TEST_TMPDIR/clean.yaml" doctor --list
assert_eq "$rc" "2" "doctor --list without a name is a usage error"

echo "--- reindex: the Commands block names what exists ---"
VAULT="$TEST_TMPDIR/ri"; mkdir -p "$VAULT/Sources" "$VAULT/Synthesis"
mk_legacy 2026-09-01_r0000001 50; mk_extract 2026-09-01_r0000001 5
run python3 "$REINDEX" "$VAULT" --no-hand-edit-report
assert_eq "$rc" "0" "reindex exits 0"
idx="$(cat "$VAULT/index.md")"
assert_contains "$idx" '- `/source-add <URL or title>` — capture a new source' "Commands: /source-add"
assert_contains "$idx" '- `/source-search <query>`' "Commands: /source-search"
assert_contains "$idx" '- `/source-status`' "Commands: /source-status"
assert_contains "$idx" '- `mnemon reindex`' "Commands: mnemon reindex"
assert_not_contains "$idx" "/source-process" "Commands: the removed /source-process is gone"
assert_not_contains "$idx" "/knowledge-status" "Commands: the removed /knowledge-status is gone"

echo "--- the engine's own guide stamp ---"
assert_eq "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import lib_vault; print(lib_vault.engine_guide_version())' "$MNEMON_ROOT/bin")" "2" "vault-template/CLAUDE.md is stamped mnemon_guide: 2"
assert_eq "$(head -1 "$MNEMON_ROOT/vault-template/_meta/Protocol.md")" "<!-- mnemon_guide: 2 -->" "vault-template/_meta/Protocol.md starts with the stamp comment"

chmod 644 "$DIRTY/Sources/$U1/source.md" 2>/dev/null || true
cd /
if [[ -n "${KEEP_TMP:-}" ]]; then echo "  (kept: $TEST_TMPDIR)"; else rm -rf "$TEST_TMPDIR"; fi
summary
