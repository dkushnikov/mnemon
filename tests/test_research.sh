#!/usr/bin/env bash
# test_research.sh — the research interface (docs/specs/2026-10-27-research-interface.md, phase 3a):
# bin/lib_research.py (collection/1, the `## Items` grammar) through its unit tests, the compatibility
# fixture vault's goldens (tests/fixtures/research/expected/ — every branch that touches the shared
# files must keep them), the dispatcher's `search --include-collections`, `reindex` and `status` on the
# fixture, `init`'s scaffold (Collections/, Templates/Collection.md), the Guide 4 canon
# (vault-template names the four folders, stamped 4), and the doctor's envelope staying
# mnemon-doctor-data/1 with its 19 checks. Temp vaults, no network. bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MNEMON="$MNEMON_ROOT/bin/mnemon"
FIXTURE="$MNEMON_ROOT/tests/fixtures/research/vault"

echo "=== Research interface Tests ==="
print_toolchain; assert_toolchain_legacy

unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_VAULT_PATH MNEMON_HOME
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-research-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
mkdir -p "$XDG_CONFIG_HOME" "$TEST_TMPDIR/cwd"
cd "$TEST_TMPDIR/cwd"

out=""; err=""; rc=0
run() { rc=0; out="$("$@" 2>"$TEST_TMPDIR/err")" || rc=$?; err="$(cat "$TEST_TMPDIR/err")"; }
jq_() { printf '%s' "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$2"; }

# --- 1. unit tests ---
echo "--- python unit tests ---"
if python3 "$MNEMON_ROOT/tests/test_research.py" >"$TEST_TMPDIR/py.log" 2>&1; then
  pass "python unit tests: $(grep -Eo 'Ran [0-9]+ tests' "$TEST_TMPDIR/py.log" | head -1)"
else
  fail "python unit tests" "see output below"
  cat "$TEST_TMPDIR/py.log"
fi

# --- 2. the compatibility goldens ---
echo "--- compatibility fixtures (tests/fixtures/research/expected) ---"
run python3 "$MNEMON_ROOT/tests/fixtures/research/generate.py" --check
assert_eq "$rc" "0" "every expected reading of the fixture vault is reproduced (generate.py --check)"
[[ "$rc" -eq 0 ]] || printf '%s\n' "$out"
for f in research.json counters.json index.md search-default.json search-all.json search-domain.json; do
  assert_file_exists "$MNEMON_ROOT/tests/fixtures/research/expected/$f" "golden $f is tracked"
done
run python3 "$MNEMON_ROOT/tests/fixtures/research/generate.py"
assert_eq "$rc" "2" "generate.py without --check/--write is a usage error"

# --- 3. the dispatcher on the fixture: search, reindex, status, doctor ---
echo "--- the dispatcher on a copy of the fixture ---"
VAULT="$TEST_TMPDIR/vault"; cp -R "$FIXTURE" "$VAULT"
printf 'instance: research\nvault_path: %s\nsearch_provider: grep\n' "$VAULT" > "$TEST_TMPDIR/research.yaml"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-collections --limit 20
assert_eq "$rc" "0" "mnemon search --include-collections exits 0"
assert_eq "$(jq_ "$out" 'd["schema"]')" "mnemon-search/2" "the JSON is mnemon-search/2"
assert_eq "$(jq_ "$out" 'sorted(set(r["result_type"] for r in d["results"]))')" "['collection', 'extract']" "collections and extracts, no synthesis without its flag"
assert_eq "$(jq_ "$out" 'sorted(r["file"] for r in d["results"] if r["result_type"] == "collection")')" "['Closed dossier.md', 'Inbox minimal.md', 'Trust without identity.md']" "three collections of one folder are three results (dedup by file)"
assert_eq "$(jq_ "$out" 'sorted(set(str(r["tier"]) for r in d["results"] if r["result_type"] == "collection"))')" "['None']" "a collection result has tier null"
assert_eq "$(jq_ "$out" '[r["tier"] for r in d["results"] if r["path"] == "Sources/2026-02-11_bbbbbbbb/"]')" "['bookmark']" "tier is the capture tier of the revision (bookmark from source.md)"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust
assert_eq "$(jq_ "$out" 'sorted(set(r["result_type"] for r in d["results"]))')" "['extract']" "collections are opt-in"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-collections --human
assert_contains "$out" "collection · status: open" "the human form names the result type and the collection's status"
assert_contains "$out" "extract · tier: bookmark" "...and the capture tier of a Sources result"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-wiki
assert_eq "$rc" "2" "--include-wiki is reserved for phase 3b (a usage error today)"

run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" reindex
assert_eq "$rc" "0" "mnemon reindex on the fixture exits 0"
assert_contains "$out" "Collections=7" "...and reports the collections"
assert_contains "$(cat "$VAULT/index.md")" "| Collections | 7 |" "index.md: the Status row"
assert_eq "$(grep -c '^- \[\[Collections/' "$VAULT/index.md")" "7" "index.md: one row per collection"
assert_contains "$(cat "$VAULT/index.md")" "(open, 4 items, 1 excluded) — How can an agent be trusted when it cannot be identified?" "index.md: status, items, excluded, the question"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" status
assert_eq "$rc" "0" "mnemon status exits 0 on the fixture"
run env MNEMON_TEST_HOOKS=1 MNEMON_TEST_DOCTOR_TODAY=2026-10-27 "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --data --json
JSON="$TEST_TMPDIR/doc.json"; printf '%s' "$out" > "$JSON"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["data"]["schema"], len(d["data"]["checks"]), sorted(c["name"] for c in d["data"]["checks"])[0])' "$JSON")" "mnemon-doctor-data/1 19 data/arxiv-abstract" "doctor --data stays mnemon-doctor-data/1 with its 19 checks (the research document is phase 3b)"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]["counters"]; print(d["collections"], d["collections_fail"], d["filed"], d["integrated"])' "$JSON")" "7 4 4 1" "...and its counters carry the collections additively, filed extended"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]; print([c["status"] for c in d["checks"] if c["name"] == "store/index"][0])' "$JSON")" "ok" "store/index reads only the Sources rows: the Collections section is no drift"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-collections --limit 20
assert_eq "$rc" "0" "search is read-only: nothing changed by reindex/doctor breaks it"

# --- 4. the reader as a door ---
echo "--- lib_research.py as a door ---"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --vault "$VAULT"
assert_eq "$rc" "1" "exit 1: four documents fail"
assert_contains "$out" "collections: 7 · fail 4 · unreadable 0" "the summary line"
assert_contains "$out" "line 12: two or more links on one line" "every malformed line is named with its reason"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$VAULT/Collections/Trust without identity.md"
assert_eq "$rc" "0" "--file on a valid document exits 0"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$VAULT/Collections/Trust without identity.md" --json
assert_eq "$(jq_ "$out" 'd["collections"][0]["counts"]["items"], d["collections"][0]["counts"]["unresolved"]')" "(4, None)" "--file resolves nothing (unresolved null): the vault is not consulted"
chmod 000 "$VAULT/Collections/Closed dossier.md"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --vault "$VAULT" --json
assert_eq "$(jq_ "$out" '[c["state"] for c in d["collections"] if c["name"] == "Closed dossier"]')" "['unreadable']" "an unreadable document has state unreadable, never raised, never ok"
chmod 644 "$VAULT/Collections/Closed dossier.md"

# --- 5. init scaffold and the Guide 4 canon ---
echo "--- init scaffold, Guide 4 ---"
run "$MNEMON" init research2 --vault "$TEST_TMPDIR/v2" --non-interactive
assert_eq "$rc" "0" "mnemon init exits 0"
assert_dir_exists "$TEST_TMPDIR/v2/Collections" "init creates Collections/"
assert_file_exists "$TEST_TMPDIR/v2/Templates/Collection.md" "init copies Templates/Collection.md"
assert_contains "$out" "✓ Sources/ Collections/ Synthesis/ _meta/" "...and says so"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$TEST_TMPDIR/v2/Templates/Collection.md"
assert_eq "$rc" "1" "the template is a scaffold, not a valid document (placeholder revision)"
assert_contains "$out" "revision name malformed" "...rejected for its placeholder, nothing else"
for f in CLAUDE.md _meta/Protocol.md; do
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'Collections/' "vault-template/$f names Collections/"
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'Wiki/' "vault-template/$f names Wiki/"
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'mnemon_guide: 4' "vault-template/$f is Guide 4"
done
assert_eq "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import lib_vault; print(lib_vault.engine_guide_version())' "$MNEMON_ROOT/bin")" "4" "the engine's guide version is 4"
# a vault on Guide 3 is reported behind by the doctor
printf -- '---\nmnemon_guide: 3\n---\n# Vault\n' > "$VAULT/CLAUDE.md"
run env MNEMON_TEST_HOOKS=1 MNEMON_TEST_DOCTOR_TODAY=2026-10-27 "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --data
assert_contains "$out" "warn store/guide-version — vault CLAUDE.md 3 (behind)" "a Guide 3 copy is reported behind engine 4"

rm -rf "$TEST_TMPDIR"
summary
