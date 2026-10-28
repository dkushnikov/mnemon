#!/usr/bin/env bash
# test_research.sh — the research interface (docs/specs/2026-10-27-research-interface.md, phase 3a):
# bin/lib_research.py (collection/1, the `## Items` grammar) through its unit tests, the compatibility
# fixture vault's goldens (tests/fixtures/research/expected/ — every branch that touches the shared
# files must keep them), the dispatcher's `search --include-collections`, `reindex` and `status` on the
# fixture, `init`'s scaffold (Collections/, Templates/Collection.md), the Guide 5 canon, the research
# operations' dispatcher route (phase 3c: `mnemon research …` with a stub claude and a probe record),
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
mkdir -p "$XDG_CONFIG_HOME" "$TEST_TMPDIR/cwd" "$TEST_TMPDIR/home" "$TEST_TMPDIR/stub"
cd "$TEST_TMPDIR/cwd"
# the dispatcher's doctor probes the fence (`claude --version` / `--help`) and reads $HOME: a stub claude
# first on PATH and a temp HOME keep the suite hermetic (AGENTS.md: no real claude, a temporary HOME)
printf '#!/bin/sh\ncase "$1" in --version) echo "0.0.0 (stub)";; --help) echo "--restricted";; *) exit 1;; esac\n' > "$TEST_TMPDIR/stub/claude"
chmod +x "$TEST_TMPDIR/stub/claude"
hermetic() { env HOME="$TEST_TMPDIR/home" PATH="$TEST_TMPDIR/stub:$PATH" MNEMON_TEST_HOOKS=1 MNEMON_TEST_DOCTOR_TODAY=2026-10-27 "$@"; }

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
for f in research.json counters.json index.md search-default.json search-all.json search-domain.json doctor-data.json; do
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
assert_eq "$(jq_ "$out" 'sorted(r["file"] for r in d["results"] if r["result_type"] == "collection")')" "['Annotated.md', 'Closed dossier.md', 'Inbox minimal.md', 'Trust without identity.md']" "four collections of one folder are four results (dedup by file; the two veto dossiers do not mention trust)"
assert_eq "$(jq_ "$out" 'd["wiki"]')" "None" "the wiki block is null without --include-wiki"
assert_eq "$(jq_ "$out" 'sorted(set(str(r["tier"]) for r in d["results"] if r["result_type"] == "collection"))')" "['None']" "a collection result has tier null"
assert_eq "$(jq_ "$out" '[r["tier"] for r in d["results"] if r["path"] == "Sources/2026-02-11_bbbbbbbb/"]')" "['bookmark']" "tier is the capture tier of the revision (bookmark from source.md)"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust
assert_eq "$(jq_ "$out" 'sorted(set(r["result_type"] for r in d["results"]))')" "['extract']" "collections are opt-in"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-collections --include-synthesis --include-source --human --limit 20
assert_contains "$out" "collection · status: open" "the human form names the result type and the collection's status"
assert_contains "$out" "synthesis · status: stable" "...and a synthesis note's"
assert_contains "$out" "extract · tier: bookmark" "...and the capture tier of a Sources result"
assert_contains "$out" "source · tier: bookmark" "...a bookmark revision without an extract is a source result"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-wiki
assert_eq "$rc" "0" "--include-wiki exits 0 (phase 3b)"
assert_eq "$(jq_ "$out" '[r["file"] for r in d["wiki"]["results"]]')" "['Edited outside.md', 'Trust — four answers.md', 'Inputs changed.md', 'Stale page.md']" "the wiki block: usable pages by updated desc — suppressed, unbound, schema-failed and orphan pages left out"
assert_eq "$(jq_ "$out" 'd["wiki"]["count"], d["wiki"]["limit"], sorted(set(r["result_type"] for r in d["wiki"]["results"])), set(str(r["tier"]) for r in d["wiki"]["results"])')" "(4, 5, ['wiki'], {'None'})" "...its own count and limit; result_type wiki, tier null"
assert_eq "$(jq_ "$out" '[r["states"] for r in d["wiki"]["results"] if r["file"] == "Stale page.md"]')" "[['stale']]" "...a page carries its derived states"
assert_eq "$(jq_ "$out" 'any("Mentions trust here too" in r["snippet"] for r in d["wiki"]["results"])')" "False" "...## Atlas reading is cut from the snippets"
assert_eq "$(jq_ "$out" 'sorted(set(r["result_type"] for r in d["results"]))')" "['extract']" "...the main block is unchanged by the flag"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-wiki --include-orphans --wiki-limit 1
assert_eq "$(jq_ "$out" '[r["file"] for r in d["wiki"]["results"]], d["wiki"]["count"]')" "(['Orphan page.md'], 1)" "--include-orphans adds orphan pages; --wiki-limit caps the block"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-wiki --domain mc/ai --human
assert_contains "$out" "wiki · contradiction · updated 2026-10-22 · domains: learning, mc/ai · ok" "the human form prints the wiki block with kind, updated, inherited domains and states"
assert_not_contains "$out" "Inputs changed" "--domain filters wiki pages by their inherited domains"

run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" reindex
assert_eq "$rc" "0" "mnemon reindex on the fixture exits 0"
assert_contains "$out" "Collections=10, Wiki=10" "...and reports the collections and the wiki pages"
assert_contains "$(cat "$VAULT/index.md")" "| Collections | 10 |" "index.md: the Status row"
assert_contains "$(cat "$VAULT/index.md")" "| Wiki | 10 |" "index.md: the Wiki Status row (phase 3b)"
assert_eq "$(grep -c '^- \[\[Collections/' "$VAULT/index.md")" "10" "index.md: one row per collection"
assert_eq "$(grep -c '^- \[\[Wiki/' "$VAULT/index.md")" "10" "index.md: one row per wiki page (the dot-file skipped)"
assert_contains "$(cat "$VAULT/index.md")" "- [[Wiki/Trust — four answers|Trust — four answers]] (contradiction, 3 sources, 2 collections, updated 2026-10-22)" "index.md: a wiki row carries the stored facts, not the states"
assert_contains "$(cat "$VAULT/index.md")" "- [[Wiki/Bad schema|Bad schema]] (?, 0 sources, 1 collections) (fail: missing required \`based_on\`)" "index.md: a page whose schema fails says so"
assert_eq "$(python3 -c 'import sys; t=open(sys.argv[1]).read().split("\n"); print(t.index("## Wiki") > t.index("## Collections") and t.index("## Wiki") < t.index("## Synthesis Notes"))' "$VAULT/index.md")" "True" "index.md: ## Wiki sits between ## Collections and ## Synthesis Notes"
assert_contains "$(cat "$VAULT/index.md")" "(open, 4 items, 1 excluded) — How can an agent be trusted when it cannot be identified?" "index.md: status, items, excluded, the question"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" status
assert_eq "$rc" "0" "mnemon status exits 0 on the fixture"
run hermetic "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --data --json
JSON="$TEST_TMPDIR/doc.json"; printf '%s' "$out" > "$JSON"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["data"]["schema"], len(d["data"]["checks"]), sorted(c["name"] for c in d["data"]["checks"])[0])' "$JSON")" "mnemon-doctor-data/1 19 data/arxiv-abstract" "doctor --data stays mnemon-doctor-data/1 with its 19 checks"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]["counters"]; print(d["collections"], d["collections_fail"], d["filed"], d["integrated"], d["wiki_pages"], d["wiki_fail"])' "$JSON")" "10 4 4 1 10 1" "...and its counters carry the collections and the wiki additively, filed extended (based_on ⊆ the filed set here)"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["research"]; print(d["schema"], len(d["checks"]), d["summary"]["coverage"], d["summary"]["fail"], d["summary"]["warn"], d["summary"]["probed"])' "$JSON")" "mnemon-doctor-research/1 10 {'pages': 10, 'collections': 10} 3 5 10" "the research document is embedded next to data: 10 checks, coverage, 3 fail · 5 warn (research/index is ok after the reindex above), probed 10/10"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print([c["status"] for c in d["checks"] if c["name"] == "research/wiki-binding"], d["exit"])' "$JSON")" "['fail'] 1" "...the research lines are in checks[] and a research fail joins the aggregated exit 1"
assert_eq "$rc" "1" "doctor --data --json exits 1 on the fixture (research fails; the data document has none)"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]; print(d["summary"]["fail"])' "$JSON")" "0" "...the data document itself has 0 fail: the exit came from research"
assert_eq "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["data"]; print([c["status"] for c in d["checks"] if c["name"] == "store/index"][0])' "$JSON")" "ok" "store/index reads only the Sources rows: the Collections section is no drift"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-collections --limit 20
assert_eq "$rc" "0" "search is read-only: nothing changed by reindex/doctor breaks it"
run hermetic "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --list research/wiki-binding
assert_eq "$rc" "0" "doctor --list research/<check> is the namespaced door"
assert_eq "$(printf '%s\n' "$out" | cut -f1 | sort | tr '\n' ' ')" "Wiki/Suppressed by name Wiki/Unbound binding Wiki/Unbound unlisted Wiki/Vetoed page " "...one item per unbound or suppressed page"
run hermetic "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --list wiki-evidence
assert_contains "$out" "Collections/Trust without identity · 2026-03-12_cccccccc-2	uncited: no verified locator on any of the 7 pages" "...the short name reaches a research check too; an uncited item names its dossier and revision"
run hermetic "$MNEMON" --config "$TEST_TMPDIR/research.yaml" status --brief
assert_eq "$rc" "0" "status --brief exits 0 on the fixture (its policy: exit 0 after a run)"
assert_contains "$out" "· probed 17/19 · research: 10 pages · 3 fail · 5 warn" "...and carries the research part after the data one: pages, the checks' fails and warns (research/index ok after the reindex above)"
# one budget, two documents: a deadline that hits only the research document leaves the data one probed
run hermetic env MNEMON_TEST_DOCTOR_STALL_RESEARCH=5 "$MNEMON" --config "$TEST_TMPDIR/research.yaml" status --brief --budget 2
assert_eq "$rc" "0" "status --brief under a budget the research document cannot meet exits 0"
assert_contains "$out" "· probed 17/19 · research: not probed (research: budget 2 s exceeded)" "...the data document arrived before the deadline and is kept; the research one is not probed"
assert_not_contains "$out" "data: not probed" "...the data document is probed"
run hermetic env MNEMON_TEST_DOCTOR_STALL_RESEARCH=5 python3 "$MNEMON_ROOT/bin/vault-doctor.py" --vault "$VAULT" --budget 2 --research --json
assert_eq "$(jq_ "$out" 'd["summary"]["not_probed"], d["summary"]["probed"], [x["code"] for x in d["diagnostics"]]')" "(10, 0, ['budget'])" "...its ten checks are not probed with a budget diagnostic"
run hermetic env MNEMON_TEST_DOCTOR_RAISE=research/wiki-orphan "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --data
assert_contains "$out" "warn research/wiki-orphan — not probed: error: RuntimeError: test hook" "a research check that raises is not probed, the others are kept"
assert_contains "$out" "fail doctor/error — research/wiki-orphan: RuntimeError: test hook" "...and a fail-level error diagnostic names it"

# --- 4. the reader as a door ---
echo "--- lib_research.py as a door ---"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --vault "$VAULT"
assert_eq "$rc" "1" "exit 1: four documents fail"
assert_contains "$out" "collections: 10 · fail 4 · unreadable 0" "the summary line"
assert_contains "$out" "wiki: 10 pages · fail 5 · warn 4 · not probed 0" "the wiki summary line: schema fail, suppressed and unbound are fails"
assert_contains "$out" "fail Vetoed page — concept" "a page's line opens with its severity, not its schema state"
assert_contains "$out" "! suppressed: vetoed by open collection \`Vetoed\` (wiki: off)" "a veto is named with its reason"
assert_contains "$out" "! unbound: line 28: unlisted — [[Sources/2026-04-13_dddddddd/source#Delta capture]] names a revision outside based_on" "an unlisted evidential link is named with its line"
assert_contains "$out" "line 12: two or more links on one line" "every malformed line is named with its reason"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$VAULT/Collections/Trust without identity.md"
assert_eq "$rc" "0" "--file on a valid document exits 0"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$VAULT/Collections/Trust without identity.md" --json
assert_eq "$(jq_ "$out" 'd["collections"][0]["counts"]["items"], d["collections"][0]["counts"]["unresolved"]')" "(4, None)" "--file resolves nothing (unresolved null): the vault is not consulted"
chmod 000 "$VAULT/Collections/Closed dossier.md"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --vault "$VAULT" --json
assert_eq "$(jq_ "$out" '[c["state"] for c in d["collections"] if c["name"] == "Closed dossier"]')" "['unreadable']" "an unreadable document has state unreadable, never raised, never ok"
chmod 644 "$VAULT/Collections/Closed dossier.md"

# --- 5. init scaffold and the Guide 5 canon ---
echo "--- init scaffold, Guide 5 ---"
run "$MNEMON" init research2 --vault "$TEST_TMPDIR/v2" --non-interactive
assert_eq "$rc" "0" "mnemon init exits 0"
assert_dir_exists "$TEST_TMPDIR/v2/Collections" "init creates Collections/"
assert_file_exists "$TEST_TMPDIR/v2/Templates/Collection.md" "init copies Templates/Collection.md"
assert_contains "$out" "✓ Sources/ Collections/ Synthesis/ _meta/" "...and says so"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$TEST_TMPDIR/v2/Templates/Collection.md"
assert_eq "$rc" "1" "the template is a scaffold, not a valid document (placeholder revision)"
assert_contains "$out" "revision name malformed" "...rejected for its placeholder, nothing else"
assert_file_exists "$TEST_TMPDIR/v2/Templates/Wiki.md" "init copies Templates/Wiki.md (phase 3b)"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --file "$TEST_TMPDIR/v2/Templates/Wiki.md"
assert_eq "$rc" "1" "the wiki template is a scaffold read as a wiki page (type: wiki), not a valid one"
assert_contains "$out" "fail Wiki — concept" "...read by the wiki reader"
assert_contains "$out" "based_on[0]: rev: revision name malformed" "...rejected for its placeholders"
for f in CLAUDE.md _meta/Protocol.md; do
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'Collections/' "vault-template/$f names Collections/"
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'Wiki/' "vault-template/$f names Wiki/"
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'mnemon_guide: 5' "vault-template/$f is Guide 5"
  assert_contains "$(cat "$MNEMON_ROOT/vault-template/$f")" 'mnemon research update' "vault-template/$f names the Wiki/ door (phase 3c)"
done
assert_eq "$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import lib_vault; print(lib_vault.engine_guide_version())' "$MNEMON_ROOT/bin")" "5" "the engine's guide version is 5"
# a vault on Guide 3 is reported behind by the doctor
printf -- '---\nmnemon_guide: 3\n---\n# Vault\n' > "$VAULT/CLAUDE.md"
run hermetic "$MNEMON" --config "$TEST_TMPDIR/research.yaml" doctor --data
assert_contains "$out" "warn store/guide-version — vault CLAUDE.md 3 (behind)" "a Guide 3 copy is reported behind engine 5"
assert_not_contains "$out" "claude 2." "the fence probe saw the stub claude, not the real one"

# --- 6. a Collections/ that exists but cannot be listed is never an empty one ---
echo "--- an unlistable Collections/ ---"
chmod 000 "$VAULT/Collections"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --vault "$VAULT"
assert_eq "$rc" "1" "the reader exits 1 when Collections/ cannot be listed"
assert_contains "$err" "could not be listed" "...and says so on stderr"
run python3 "$MNEMON_ROOT/bin/lib_vault.py" --vault "$VAULT" --json
assert_eq "$(jq_ "$out" 'd["snapshot"]["complete"], d["counters"]["collections"], "collections_unlisted" in d["snapshot"]')" "(False, 0, True)" "lib_vault: the snapshot is incomplete, the folder is unlisted, not empty"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" reindex
assert_eq "$rc" "0" "reindex still writes the index"
assert_contains "$err" "Collections/ could not be listed" "...and says the section was not regenerated"
assert_contains "$(cat "$VAULT/index.md")" "_Collections/ could not be listed" "...with one row saying so instead of an empty section"
assert_contains "$(cat "$VAULT/index.md")" "| Collections | ? |" "...and the Status row says ?"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust --include-collections
assert_eq "$rc" "2" "search --include-collections refuses an unlistable Collections/ (exit 2)"
assert_contains "$err" "Collections/ could not be listed" "...and says why"
run "$MNEMON" --config "$TEST_TMPDIR/research.yaml" search --query trust
assert_eq "$rc" "0" "...while a search without the flag never touches the folder"
chmod 755 "$VAULT/Collections"

# --- 7. the research operations through the dispatcher (phase 3c, docs/research.md) ---
echo "--- mnemon research (the dispatcher route) ---"
RV="$TEST_TMPDIR/rvault"; cp -R "$FIXTURE" "$RV"
printf 'instance: research3\nvault_path: %s\nsearch_provider: grep\ndoctor_wiki_pages_per_collection: 20\nresearch_log_path: _claude/research-log.jsonl\n' "$RV" > "$TEST_TMPDIR/research3.yaml"
# the stub claude answers from a canned file and records a probe pass for its version
cat > "$TEST_TMPDIR/stub/claude" <<'STUB'
#!/bin/sh
case "$1" in --version) echo "0.0.0 (stub)"; exit 0;; --help) echo "--restricted"; exit 0;; esac
cat > /dev/null
printf '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.01,"duration_ms":5,"num_turns":1,"structured_output":'
cat "${STUB_ANSWER:?}"
printf '}\n'
STUB
chmod +x "$TEST_TMPDIR/stub/claude"
mkdir -p "$XDG_STATE_HOME/mnemon"
printf '{"0.0.0 (stub)": {"pass": true}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"
research() { env HOME="$TEST_TMPDIR/home" PATH="$TEST_TMPDIR/stub:$PATH" STUB_ANSWER="$MNEMON_ROOT/tests/fixtures/research/answers/contradiction-good.json" MNEMON_AGENT=atlas "$MNEMON" --config "$TEST_TMPDIR/research3.yaml" research "$@"; }
run research update --page "Trust — routed" --kind contradiction --collection "Trust without identity" --recusal off
assert_eq "$rc" "0" "mnemon research update through the dispatcher exits 0"
assert_contains "$out" "RESULT:op=research-update status=published page=Trust — routed class=-" "...and publishes the page"
assert_file_exists "$RV/Wiki/Trust — routed.md" "the page is in Wiki/"
assert_file_exists "$RV/_claude/research-log.jsonl" "the journal lands at research_log_path inside the vault"
run research update --page "Trust — routed" --kind contradiction --collection "Trust without identity" --recusal off
assert_contains "$out" "status=no-op" "the second update is a no-op (the package is unchanged)"
run research update --propose --page "Trust — routed"
assert_eq "$rc" "0" "research update --propose is read-only and exits 0"
assert_contains "$out" "RESULT:op=research-propose status=proposed" "...with its RESULT line"
run research ask --task-id t-route --question "how?" --page "Trust — routed"
assert_eq "$rc" "0" "research ask exits 0"
assert_contains "$out" "## State of evidence" "...and serves the evidential body"
assert_not_contains "$out" "complementary, not rival" "...without the page's ## Atlas reading"
run research triage --propose
assert_eq "$rc" "0" "research triage --propose exits 0"
assert_contains "$out" "Seed note — seed · drafted_by agent" "...and maps the fixture's agent-drafted seed"
run research bogus
assert_eq "$rc" "2" "an unknown research verb is a usage error"
run python3 "$MNEMON_ROOT/bin/lib_research.py" --vault "$RV"
assert_contains "$out" "ok Trust — routed — contradiction · 3 based_on · 1 collections · 2 locators" "the reader validates the built page as clean"
run hermetic "$MNEMON" --config "$TEST_TMPDIR/research3.yaml" doctor --data --json
assert_eq "$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print([p["severity"] for p in d["research"]["pages"] if p["name"] == "Trust — routed"])' "$out")" "['ok']" "the doctor reads the built page as ok"
# the single-writer rule: a declared writer_host that is not this host refuses the writers before the lock
printf 'instance: research4\nvault_path: %s\nwriter_host: another-host\n' "$RV" > "$TEST_TMPDIR/research4.yaml"
run env HOME="$TEST_TMPDIR/home" PATH="$TEST_TMPDIR/stub:$PATH" "$MNEMON" --config "$TEST_TMPDIR/research4.yaml" research update --page "X" --kind concept --collection "Trust without identity"
assert_eq "$rc" "2" "a writer_host that is not this host refuses research update"
assert_contains "$err" "must not write vault" "...and says so"
run env HOME="$TEST_TMPDIR/home" PATH="$TEST_TMPDIR/stub:$PATH" "$MNEMON" --config "$TEST_TMPDIR/research3.yaml" config keys
assert_contains "$out" "research_log_path str -" "mnemon config keys lists the research keys (phase 3c)"
assert_contains "$out" "research_package_max_bytes int 400000" "...with their defaults"

rm -rf "$TEST_TMPDIR"
summary
