#!/usr/bin/env bash
# test_site.sh — site capture through the gateway (docs/specs/2026-10-15-capture-layer.md §3.6,
# §4.3, D9, §12 A4/A5, §7.10 gateway half): the plan as the input locator, the bundle directory as
# the raw (staged, verified, recorded, renamed), dispositions in folded_sources, the aggregate
# completeness, the crash boundaries, the evidence rows, reconversion by verify + assemble, the
# plan refusals and `mnemon site map`. Everything runs against the capture-site.py stub of
# tests/fixtures/capture/setup.sh; no network, no model.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"

echo "=== Site Capture Tests ==="
PLAN="$FIX/reports/site-plan.json"
LANDING="https://example.com/"
manifest_sha() { shasum -a 256 "$1/manifest.json" | cut -c1-64; }
members_bytes() { jq '[.members[].bytes] | add' "$1/manifest.json"; }

# ---------------------------------------------------------------------------------------------
echo "--- 1. the plan refusals (exit 2, nothing written) ---"
run_gw --url "https://other.example.org/" --site "$PLAN" --capture-id sr-1
assert_eq "$rc" "2" "a plan whose landing differs from --url → exit 2"
assert_contains "$err" "is not the --url landing" "...naming both"
jq '.pages[4].include = true' "$PLAN" > "$TEST_TMPDIR/robots-plan.json"
run_gw --url "$LANDING" --site "$TEST_TMPDIR/robots-plan.json" --capture-id sr-2
assert_eq "$rc" "2" "a hand-edited plan including a robots-disallowed page → exit 2"
assert_contains "$err" "disallowed by robots.txt" "...naming the rule"
jq '.caps.max_pages = 2' "$PLAN" > "$TEST_TMPDIR/cap-plan.json"
run_gw --url "$LANDING" --site "$TEST_TMPDIR/cap-plan.json" --capture-id sr-3
assert_eq "$rc" "2" "a plan whose included pages exceed its cap → exit 2"
sed 's/^instance: captest/instance: captest-cap/' "$CONFIG" | awk '1; END { print "site_max_pages: 2" }' > "$TEST_TMPDIR/cap.yaml"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/cap.yaml" --url "$LANDING" --site "$PLAN" --capture-id sr-4 >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "2" "...and the instance's site_max_pages caps a plan too"
run_gw --url "$LANDING" --site "$TEST_TMPDIR/missing-plan.json" --capture-id sr-5
assert_eq "$rc $(result error_class)" "1 input-missing" "a missing plan file → exit 1 input-missing"
[[ -f "$(receipt sr-1)" || -f "$(receipt sr-2)" || -f "$(receipt sr-3)" ]] && fail "no receipt for a refused plan" "exists" || pass "no receipt for a refused plan"
assert_eq "$(site_calls)" "0" "...capture-site.py was never called"

# ---------------------------------------------------------------------------------------------
echo "--- 2. a site capture from a plan file: the bundle is the raw, the plan the locator ---"
stub_reset
run_gw --url "$LANDING" --site "$PLAN" --capture-id site-1
assert_eq "$rc $(result status)" "0 extracted" "mnemon add --url <landing> --site plan.json exits 0"
rev1="$(result revision)"
src1="$VAULT/Sources/$rev1/source.md"
bundle="$ARCHIVE/$rev1.site"
assert_dir_exists "$bundle" "the raw is the bundle directory <revision>.site/"
assert_eq "$(ls "$bundle" | tr '\n' ' ')" "01.fetch.json 01.html 02.fetch.json 02.html 03.fetch.json 03.html manifest.json plan.json " "...with NN.html, NN.fetch.json, plan.json and manifest.json"
assert_eq "$(jq -r '[.members[].path] | sort | join(" ")' "$bundle/manifest.json")" "01.fetch.json 01.html 02.fetch.json 02.html 03.fetch.json 03.html plan.json" "...members covering every file but the manifest"
assert_eq "$(rget site-1 '.raw_sha256')" "$(manifest_sha "$bundle")" "raw_sha256 = the manifest's hash"
assert_eq "$(rget site-1 '.raw_bytes')" "$(members_bytes "$bundle")" "raw_bytes = the members' sum"
assert_eq "$(capfm "$src1" raw_sha256)" "\"$(manifest_sha "$bundle")\"" "...in the frontmatter too"
cmp -s "$bundle/plan.json" "$PLAN" && pass "the bundle's plan.json is a byte copy of the input plan" || fail "plan.json copy" "differs"
assert_eq "$(rget site-1 '.input_path') $(rget site-1 '.request.input_sha256') $(rget site-1 '.request.site') $(rget site-1 '.site_plan_sha256')" "$PLAN $(shasum -a 256 "$PLAN" | cut -c1-64) $(shasum -a 256 "$PLAN" | cut -c1-64) $(shasum -a 256 "$PLAN" | cut -c1-64)" "the plan is the input locator: input_path, input_sha256 and the bound site field are its path and hash (A4)"
assert_eq "$(rget site-1 '.request.depth') $(rget site-1 '.request.source_type') $(rget site-1 '.raw_kind')" "off website site" "...depth off, source_type website, raw_kind site"
assert_eq "$(fm "$src1" archive) $(capfm "$src1" raw_kind) $(capfm "$src1" raw_store)" "\"$rev1.site\" site archive" "archive: names the bundle; raw_kind site"
assert_eq "$(capfm "$src1" method) $(capfm "$src1" completeness) $(capfm "$src1" fetch)" '"site+mixed" complete site' "method site+mixed (two converters), completeness = the aggregate, fetch site"
assert_eq "$(fm "$src1" site_pages)" '"3/3"' "site_pages = converted/included"
assert_eq "$(fm "$src1" title)" '"Example"' "the landing's title from the plan"
assert_eq "$(result completeness) $(result canonical_url)" "complete $LANDING" "RESULT:completeness and canonical_url"
fs="$(awk '/^folded_sources:/{f=1;next} f&&/^[^ ]/{exit} f' "$src1")"
assert_contains "$fs" "  - url: \"https://example.com/product\"" "folded_sources: one entry per included page after the landing"
assert_contains "$fs" "    role: product" "...with its role"
assert_contains "$fs" "    disposition: converted" "...its disposition"
assert_contains "$fs" "    converter: dom-text" "...its converter"
assert_contains "$fs" "    completeness: complete" "...its completeness"
assert_contains "$fs" "    missing_lines: 0" "...and its missing lines"
assert_contains "$fs" "  - url: \"https://docs.example.com/guide\"" "...the docs page too"
assert_not_contains "$fs" "https://example.com/\"" "...never the landing itself"
body="$(cat "$src1")"
assert_contains "$body" "# Example — site capture" "the composite starts with the landing title"
assert_contains "$body" "## Part 1 — https://example.com/" "...Part 1 is the landing"
assert_contains "$body" "## Part 2 — https://example.com/product (product)" "...then the pages in plan order with their roles"
assert_contains "$body" "## Part 3 — https://docs.example.com/guide (docs)" "...all of them"
assert_eq "$(rget site-1 '.capture_report.site.schema') $(rget site-1 '.capture_report.fetch') $(rget site-1 '.capture_report.text')" "mnemon-site/1 null null" "the site report is in the receipt (no page-level fetch/text report)"
assert_eq "$(jq -r '.site.schema + " " + (.fetch|tostring)' "$ARCHIVE/$rev1.report.json")" "mnemon-site/1 null" "the sidecar holds the site report"
call="$(grep '^fetch-bundle' "$STUB_DIR/site-calls")"
assert_contains "$call" "fetch-bundle $PLAN --out $ARCHIVE/.$rev1.site.staging-" "fetch-bundle ran on the plan into the staged directory .<revision>.site.staging-<pid>/"
assert_contains "$call" "--site-timeout 900 --fetch-timeout 60 --render-timeout 90 --acquire-timeout 300" "...with every deadline of D11"
assert_contains "$call" "--policy " "...the policy"
assert_contains "$call" "--external " "...and the external settings"
assert_eq "$(site_calls assemble)" "1" "assemble ran once, on the published bundle"
assert_contains "$(grep '^assemble' "$STUB_DIR/site-calls")" "assemble $bundle --out" "...($bundle)"
assert_contains "$(grep '^assemble' "$STUB_DIR/site-calls")" " --timeout 120" "...under --timeout convert_timeout (G25)"
assert_eq "$(staging_left)" "0" "no staging left"
assert_eq "$(fetch_calls)" "0" "capture-fetch.py was not called: the bundle fetcher owns the page loop"
assert_eq "$(ls "$VSTATE/runs/site-1/attempt-1" 2>/dev/null | wc -l | tr -d ' ')" "0" "the attempt dir is removed after the final receipt (the locator is the plan file)"
# the same landing again: a duplicate, with the site hint; --recapture supersedes
run_gw --url "$LANDING?utm_source=x" --site "$PLAN" --capture-id site-dup
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $rev1" "a site capture of a captured landing → skipped-duplicate"
assert_contains "$err" "needs --recapture" "...with the --recapture hint"
run_gw --url "$LANDING" --capture-id site-plain-dup
assert_eq "$rc $(result status)" "0 skipped-duplicate" "...and a plain capture of it is the same resource"
run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-rc
assert_eq "$rc $(result status) $(fm "$VAULT/Sources/$(result revision)/source.md" supersedes)" "0 extracted \"$rev1\"" "--recapture → a new site revision superseding the first"
assert_dir_exists "$ARCHIVE/$(result revision).site" "...with its own bundle"
# the original plan changed or deleted after publication: the bundle wins on a replay
cp "$PLAN" "$TEST_TMPDIR/plan-copy.json"
run_gw --url "$LANDING" --site "$TEST_TMPDIR/plan-copy.json" --recapture --capture-id site-copy
revcopy="$(result revision)"
printf '{}' > "$TEST_TMPDIR/plan-copy.json"
run_gw --capture-id site-copy
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "a changed plan after publication: the ID-only replay answers from the files"
rm -f "$TEST_TMPDIR/plan-copy.json"
run_gw --capture-id site-copy
assert_eq "$rc $(result status)" "0 extracted" "...a deleted plan too (the bundle's plan.json wins)"

# ---------------------------------------------------------------------------------------------
echo "--- 3. dispositions: a failing page, a timeout, a conversion failure ---"
stub_reset
STUB_SITE=page-fail run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-pf
assert_eq "$rc $(result status)" "0 extracted" "a page that 500s does not fail the capture"
srcpf="$VAULT/Sources/$(result revision)/source.md"
assert_eq "$(jq -r '.pages[2].disposition + " " + .pages[2].error_class' "$ARCHIVE/$(result revision).site/manifest.json")" "fetch-failed server-error" "...fetch-failed in the manifest (acquisition dispositions, A5)"
assert_eq "$(capfm "$srcpf" completeness) $(fm "$srcpf" site_pages) $(result completeness)" "partial \"2/3\" partial" "...aggregate partial, site_pages 2/3"
fs="$(awk '/^folded_sources:/{f=1;next} f&&/^[^ ]/{exit} f' "$srcpf")"
assert_contains "$fs" "    disposition: fetch-failed" "...the page's disposition stays visible in folded_sources"
assert_contains "$fs" "    converter: null" "...with no converter"
assert_not_contains "$(cat "$srcpf")" "## Part 3" "...and the page is absent from the composite"
STUB_SITE=timeout-after-landing run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-to
assert_eq "$rc $(result status)" "0 extracted" "site_timeout after the landing → published"
assert_eq "$(capfm "$VAULT/Sources/$(result revision)/source.md" completeness) $(fm "$VAULT/Sources/$(result revision)/source.md" site_pages)" "partial \"1/3\"" "...partial with site_pages 1/3"
assert_contains "$(cat "$VAULT/Sources/$(result revision)/source.md")" "    disposition: skipped-timeout" "...the pages skipped-timeout"
STUB_SITE_CONVERT_FAIL=3 run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-cf
assert_eq "$rc $(result status)" "0 extracted" "a page whose conversion fails does not fail the capture"
srccf="$VAULT/Sources/$(result revision)/source.md"
assert_contains "$(cat "$srccf")" "    disposition: convert-failed" "...convert-failed in folded_sources (conversion dispositions live in the report, A5)"
assert_eq "$(jq -r '.pages[2].disposition' "$ARCHIVE/$(result revision).site/manifest.json")" "captured" "...while the manifest still says captured"
assert_eq "$(capfm "$srccf" completeness) $(fm "$srccf" site_pages)" "partial \"2/3\"" "...aggregate partial, 2/3"
assert_not_contains "$(cat "$srccf")" "## Part 3" "...and the page is omitted from the composite"
n0="$(nfolders)"
STUB_SITE=landing-fail run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-lf
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed blocked false" "a landing that fails → exit 1 with its class (no folder)"
assert_eq "$(nfolders)" "$n0" "...nothing published"
assert_eq "$(staging_left)" "0" "...no staged bundle left"
STUB_SITE=timeout run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-t
assert_eq "$rc $(result error_class) $(result retryable) $(rget site-t '.stage')" "1 timeout true landing" "a site-wide timeout before the landing → failed/timeout, retryable, stage landing"
# the landing's conversion killed by convert_timeout during the assembly (G25): timeout, stage convert, not
# retryable; the bundle is retained for --resume after raising the budget
stub_reset
STUB_SITE=assemble-timeout run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-at
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed timeout false" "assemble exit 124 → failed / timeout / retryable false (the report's flag; G25)"
assert_eq "$(rget site-at '.stage') $(rget site-at '.raw_published') $(rget site-at '.capture_report.site.error_class')" "convert true timeout" "...stage convert, the bundle retained, the site report recorded"
assert_dir_exists "$ARCHIVE/$(rget site-at '.revision').site" "...the bundle directory is published"
assert_contains "$err" "convert_timeout" "...the message names convert_timeout"
# the landing's own conversion failing fails the publication; the bundle is retained; --resume reconverts
stub_reset
STUB_SITE_CONVERT_FAIL=1 run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id site-lc
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed convert true" "a landing whose conversion fails → failed/convert (A5)"
assert_eq "$(rget site-lc '.raw_published') $(rget site-lc '.capture_report.site.pages[0].disposition')" "true convert-failed" "...the bundle is retained and the site report recorded"
assert_dir_exists "$ARCHIVE/$(rget site-lc '.revision').site" "...the bundle directory is published"
stub_reset
run_gw --capture-id site-lc
assert_eq "$rc $(result status)" "0 extracted" "the replay reconverts the retained bundle"
assert_eq "$(site_calls fetch-bundle) $(site_calls assemble)" "0 1" "...by verify + assemble only — fetch-bundle never ran again"
[[ "$(site_calls verify)" -ge 1 ]] && pass "...the published bundle was verified before the assembly" || fail "verify ran" "0"

# ---------------------------------------------------------------------------------------------
echo "--- 4. crash boundaries of a directory raw ---"
crash_site() {   # <boundary> <capture_id>
  export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT="$1"
  run_gw --url "$LANDING" --site "$PLAN" --recapture --capture-id "$2"
  unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
}
stub_reset
crash_site after-raw-staged cs-1
revs1="$(rget cs-1 '.revision')"
staged="$(rget cs-1 '.raw_staged_path')"
assert_dir_exists "$staged" "crash after-raw-staged: the staged bundle directory exists"
assert_eq "$(rget cs-1 '.raw_published') $(rget cs-1 '.raw_sha256')" "false $(manifest_sha "$staged")" "...its manifest hash recorded, raw_published false"
stub_reset
run_gw --capture-id cs-1
assert_eq "$rc $(result status)" "0 extracted" "the replay finishes the rename"
assert_dir_exists "$ARCHIVE/$revs1.site" "...the bundle is published"
[[ -d "$staged" ]] && fail "staged gone" "still there" || pass "...the staged directory is gone"
assert_eq "$(site_calls fetch-bundle) $(site_calls verify)" "0 2" "...without refetching: the staged tree was verified before the rename, the published one before assembly"
stub_reset
crash_site after-raw-renamed cs-2
revs2="$(rget cs-2 '.revision')"
assert_eq "$(rget cs-2 '.raw_published')" "false" "crash after-raw-renamed: the receipt is behind the rename (A5 hook)"
assert_dir_exists "$ARCHIVE/$revs2.site" "...the bundle sits at its final name"
stub_reset
run_gw --capture-id cs-2
assert_eq "$rc $(result status) $(rget cs-2 '.raw_published')" "0 extracted true" "...the replay reconciles raw_published from the final tree (verified)"
assert_eq "$(site_calls fetch-bundle)" "0" "...never refetched"
stub_reset
crash_site after-raw-published cs-3
stub_reset
run_gw --capture-id cs-3
assert_eq "$rc $(result status) $(site_calls fetch-bundle)" "0 extracted 0" "crash after-raw-published: the replay assembles the published bundle"
stub_reset
crash_site after-source-staged cs-4
assert_eq "$(ls -d "$VAULT"/Sources/.*.staging-* 2>/dev/null | wc -l | tr -d ' ')" "1" "crash after-source-staged: a staging dir is left"
run_gw --capture-id cs-4
assert_eq "$rc $(result status) $(staging_left)" "0 extracted 0" "...the replay removes it and publishes from the bundle"
stub_reset
crash_site after-plan-frozen cs-5
assert_eq "$(rget cs-5 '.raw_published') $(rget cs-5 '.site_plan_sha256')" "false $(shasum -a 256 "$PLAN" | cut -c1-64)" "crash after the plan freeze of a plan-file capture: the plan's hash recorded, nothing fetched yet"
assert_eq "$(site_calls fetch-bundle)" "0" "...fetch-bundle had not started"
stub_reset
run_gw --capture-id cs-5
assert_eq "$rc $(result status) $(site_calls fetch-bundle)" "0 extracted 1" "...the replay acquires the bundle"
# orphan staging directories are removed by the next invocation
mkdir -p "$ARCHIVE/.2026-01-01_deadbeef.site.staging-99999"
run_gw --url "https://example.com/plain-after-orphan" --capture-id orphan-sweep
[[ -d "$ARCHIVE/.2026-01-01_deadbeef.site.staging-99999" ]] && fail "orphan staging dir removed" "still there" || pass "an orphan .site.staging-* directory (no receipt owns it) is removed by the next invocation"

# ---------------------------------------------------------------------------------------------
echo "--- 5. evidence: a published bundle must verify member by member ---"
stub_reset
crash_site after-source-staged ev-1
rev_ev="$(rget ev-1 '.revision')"
printf 'modified\n' >> "$ARCHIVE/$rev_ev.site/02.html"
stub_reset
run_gw --capture-id ev-1
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed evidence-mismatch false" "a modified member of a published bundle → evidence-mismatch (permanent)"
assert_contains "$err" "evidence-mismatch 02.html" "...naming the member"
assert_eq "$(site_calls fetch-bundle)" "0" "...never refetched"
crash_site after-source-staged ev-2
rev_ev2="$(rget ev-2 '.revision')"
rm -f "$ARCHIVE/$rev_ev2.site/01.fetch.json"
run_gw --capture-id ev-2
assert_eq "$rc $(result error_class)" "1 evidence-mismatch" "a missing member of a PUBLISHED bundle → evidence-mismatch (A5: evidence-missing is for a recorded, never-published raw)"
assert_contains "$err" "evidence-missing 01.fetch.json" "...the verifier's note names it"
crash_site after-source-staged ev-3
rev_ev3="$(rget ev-3 '.revision')"
jq '.landing = "https://evil.example/"' "$ARCHIVE/$rev_ev3.site/plan.json" > "$TEST_TMPDIR/p.json" && mv "$TEST_TMPDIR/p.json" "$ARCHIVE/$rev_ev3.site/plan.json"
run_gw --capture-id ev-3
assert_eq "$rc $(result error_class)" "1 evidence-mismatch" "a changed plan.json inside the bundle → evidence-mismatch"
crash_site after-raw-staged ev-4
rm -rf "$(rget ev-4 '.raw_staged_path')"
run_gw --capture-id ev-4
assert_eq "$rc $(result error_class)" "1 evidence-missing" "a recorded bundle with neither staged nor final tree → evidence-missing"
crash_site after-raw-staged ev-5
rm -f "$(rget ev-5 '.raw_staged_path')/02.html"
run_gw --capture-id ev-5
assert_eq "$rc $(result error_class)" "1 archive-exists" "a staged tree that no longer matches its recorded hash → archive-exists (operator)"
assert_contains "$err" "evidence-missing 02.html" "...with the verifier's note"
# a published bundle that was replayed intact after extraction: integrity of the source still rules
run_gw --capture-id site-1
assert_eq "$rc $(result status) $(result replayed)" "0 extracted true" "an intact site capture replays from its files"

# ---------------------------------------------------------------------------------------------
echo "--- 6. --site auto: the generated plan is the locator, frozen before fetch-bundle (A4) ---"
stub_reset
run_gw --url "https://auto.example.com/" --site auto --capture-id auto-1
assert_eq "$rc $(result status)" "0 extracted" "mnemon add --url <landing> --site auto exits 0"
reva="$(result revision)"
plan_auto="$VSTATE/runs/auto-1/attempt-1/plan.json"
assert_file_exists "$plan_auto" "the generated plan is kept as attempt-1/plan.json (the locator, like a stdin capture's capture.md)"
assert_eq "$(ls "$VSTATE/runs/auto-1/attempt-1" | tr '\n' ' ')" "plan.json " "...and nothing else of the attempt dir survives the final receipt"
assert_eq "$(rget auto-1 '.input_path') $(rget auto-1 '.request.site') $(rget auto-1 '.request.input_sha256') $(rget auto-1 '.site_plan_sha256')" "$plan_auto auto null $(shasum -a 256 "$plan_auto" | cut -c1-64)" "...input_path = the plan, site bound as \"auto\", input_sha256 null, site_plan_sha256 recorded (A4)"
assert_eq "$(site_calls map) $(site_calls fetch-bundle)" "1 1" "map ran once, then fetch-bundle"
assert_contains "$(grep '^map' "$STUB_DIR/site-calls")" "map https://auto.example.com/ --max-pages 40 --site-timeout 900" "...with the instance's cap and deadline"
# the mapper gets the same --external settings as the bundle fetcher (G12)
map_ext="$(grep '^map' "$STUB_DIR/site-calls" | head -1 | sed -n 's/.*--external \({[^}]*}\).*/\1/p')"
bundle_ext="$(grep '^fetch-bundle' "$STUB_DIR/site-calls" | head -1 | sed -n 's/.*--external \({[^}]*}\).*/\1/p')"
[[ -n "$map_ext" ]] && pass "...and --external ($map_ext)" || fail "map received --external" "$(grep '^map' "$STUB_DIR/site-calls" | head -1)"
assert_eq "$map_ext" "$bundle_ext" "...the same --external JSON fetch-bundle receives"
cmp -s "$plan_auto" "$ARCHIVE/$reva.site/plan.json" && pass "the bundle's plan.json is the generated plan" || fail "bundle plan == generated plan" "differs"
python3 - "$(receipt auto-1)" "$FIX/reports/receipts/request-v2-site-auto.json" <<'PY' && pass "the receipt's key set equals receipts/request-v2-site-auto.json (incl. site_plan_sha256)" || fail "receipt key set == fixture" "differs"
import json, sys
got, fx = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert set(got) == set(fx), sorted(set(got) ^ set(fx))
assert set(got["request"]) == set(fx["request"]), sorted(set(got["request"]) ^ set(fx["request"]))
assert set(got["capture_report"]["site"]) == set(fx["capture_report"]["site"]), sorted(set(got["capture_report"]["site"]) ^ set(fx["capture_report"]["site"]))
assert set(got["capture_report"]["site"]["pages"][0]) == set(fx["capture_report"]["site"]["pages"][0])
PY
# a crash after the plan freeze and before the bundle: the replay reuses the frozen plan
stub_reset
export MNEMON_TEST_HOOKS=1 MNEMON_TEST_CRASH_AT=after-plan-frozen
run_gw --url "https://auto2.example.com/" --site auto --capture-id auto-2
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT
assert_eq "$(rget auto-2 '.site_plan_sha256' | cut -c1-8) $(rget auto-2 '.raw_published')" "$(shasum -a 256 "$VSTATE/runs/auto-2/attempt-1/plan.json" | cut -c1-8) false" "crash after the plan freeze: the plan's hash is in the receipt, nothing fetched"
assert_eq "$(site_calls map) $(site_calls fetch-bundle)" "1 0" "...map ran, fetch-bundle not yet"
run_gw --capture-id auto-2
assert_eq "$rc $(result status)" "0 extracted" "the replay captures the bundle"
assert_eq "$(site_calls map) $(site_calls fetch-bundle)" "1 1" "...from the frozen plan (map not run again)"
# a failure before the freeze (map itself failing) leaves nothing to reuse
stub_reset
STUB_SITE=landing-fail run_gw --url "https://auto3.example.com/" --site auto --capture-id auto-3
assert_eq "$rc $(result error_class)" "1 blocked" "the landing failing inside fetch-bundle fails the capture"
assert_file_exists "$VSTATE/runs/auto-3/attempt-1/plan.json" "...the frozen plan stays with the failed receipt (the dead letter)"
run_gw --capture-id auto-3
assert_eq "$rc $(result status) $(site_calls map)" "0 extracted 1" "...the replay reuses it"
# --dry-run --site auto prints the deferral; --resume finishes extraction only
STUB_MODE=exit1 run_gw --url "https://auto4.example.com/" --site auto --capture-id auto-4
assert_eq "$rc $(result status)" "4 captured" "fixture: a site capture whose extraction failed"
stub_reset
run_gw --resume "$(rget auto-4 '.revision')"
assert_eq "$rc $(result status) $(site_calls fetch-bundle) $(site_calls assemble)" "0 extracted 0 0" "--resume finishes the extraction from the published source: no acquisition, no assembly"
# the effective rendering switches reach the mapping as well as the bundle (A12; decision 12)
stub_reset
run_gw --url "https://auto5.example.com/" --site auto --no-render --capture-id auto-5
assert_eq "$rc $(result status)" "0 extracted" "mnemon add --site auto --no-render exits 0"
assert_contains "$(grep '^map' "$STUB_DIR/site-calls" | tail -1)" " --no-render" "...map received --no-render (no Chrome for a JavaScript-shell landing either)"
assert_contains "$(grep '^map' "$STUB_DIR/site-calls" | tail -1)" " --no-external" "...and --no-external (no rendering means no remote rendering)"
assert_contains "$(grep '^fetch-bundle' "$STUB_DIR/site-calls" | tail -1)" " --no-render" "...as fetch-bundle did"
assert_eq "$(rget auto-5 '.request.render') $(rget auto-5 '.request.external')" "off off" "...bound in the request"
stub_reset
run_gw --url "https://auto6.example.com/" --site auto --no-external --capture-id auto-6
assert_contains "$(grep '^map' "$STUB_DIR/site-calls" | tail -1)" " --no-external" "--no-external alone reaches map"
assert_not_contains "$(grep '^map' "$STUB_DIR/site-calls" | tail -1)" " --no-render" "...without --no-render"

# ---------------------------------------------------------------------------------------------
echo "--- 7. without archive_dir, and mnemon site map ---"
rc=0; "$GW" source-add --config "$TEST_TMPDIR/noarchive.yaml" --url "https://state.example.com/" --site auto --capture-id site-state >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
out="$(cat "$TEST_TMPDIR/run.out")"
assert_eq "$rc $(result status)" "0 extracted" "a site capture without archive_dir exits 0"
assert_dir_exists "$VSTATE/raw/$(result revision).site" "...the bundle lives under <vault state>/raw/"
assert_eq "$(capfm "$VAULT/Sources/$(result revision)/source.md" raw_store)" "state" "...raw_store state"
assert_eq "$(rget site-state '.report_state')" "null" "...and no sidecar without an archive"
rc=0; "$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" site map "https://map.example.com/" --out "$TEST_TMPDIR/mapped.json" >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "0" "mnemon site map <url> --out plan.json exits 0"
assert_eq "$(jq -r '.schema + " " + .landing' "$TEST_TMPDIR/mapped.json")" "mnemon-site-plan/1 https://map.example.com/" "...writing a mnemon-site-plan/1 for the landing"
assert_contains "$(grep '^map' "$STUB_DIR/site-calls" | tail -1)" "--max-pages 40 --site-timeout 900 --policy " "...through capture-site.py map with the instance's cap, deadline and policy"
rc=0; "$MNEMON_ROOT/bin/mnemon" --config "$CONFIG" site >"$TEST_TMPDIR/run.out" 2>"$TEST_TMPDIR/run.err" || rc=$?
assert_eq "$rc" "2" "mnemon site without map → exit 2"
run_gw --url "https://map.example.com/" --site "$TEST_TMPDIR/mapped.json" --capture-id mapped-1
assert_eq "$rc $(result status)" "0 extracted" "...and the mapped plan captures"

cleanup_capture_test
summary
