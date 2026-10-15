#!/usr/bin/env bash
# test_hygiene.sh — public-repo gates: no private references outside LICENSE,
# valid plugin manifests, skills on the single engine path form.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

echo "=== Hygiene Tests ==="

# --- 1. no private references (home paths with a user name, the legacy
#        tilde-Mnemon checkout path, people in Latin or Cyrillic). The patterns
#        are assembled from pieces so this file never matches the gate itself.
#        A linked worktree's `.git` is a file holding a local path — excluded as
#        git metadata, not repo content.
echo "--- private references ---"
P_HOME="Users/dkush""nikov"
P_LEGACY="~/Mne""mon"
P_NAME_LAT="\\bDi""ma\\b"
P_NAME_CYR="Ди""м"
hits=$(grep -rnE "$P_HOME|$P_LEGACY|$P_NAME_LAT|$P_NAME_CYR" "$MNEMON_ROOT" --exclude-dir=.git --exclude=.git --exclude=LICENSE 2>/dev/null || true)
assert_eq "$hits" "" "no private references outside LICENSE"
[[ -n "$hits" ]] && printf '%s\n' "$hits"
hits=$(grep -rn '"email"' "$MNEMON_ROOT/plugins/mnemon/plugin.json" "$MNEMON_ROOT/.claude-plugin/marketplace.json" || true)
assert_eq "$hits" "" "plugin manifests carry no e-mail address"

# --- 2. plugin manifests ---
echo "--- plugin manifests ---"
MP="$MNEMON_ROOT/.claude-plugin/marketplace.json"
PJ="$MNEMON_ROOT/plugins/mnemon/plugin.json"
python3 -m json.tool "$MP" >/dev/null 2>&1 && pass "marketplace.json is valid JSON" || fail "marketplace.json is valid JSON" "python3 -m json.tool failed"
python3 -m json.tool "$PJ" >/dev/null 2>&1 && pass "plugin.json is valid JSON" || fail "plugin.json is valid JSON" "python3 -m json.tool failed"
jq_() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"; }
assert_eq "$(jq_ "$PJ" 'd["version"]')" "0.2.0" "plugin.json version is 0.2.0"
assert_eq "$(jq_ "$PJ" 'd["name"]')" "mnemon" "plugin.json name is mnemon"
assert_eq "$(jq_ "$MP" 'd["name"]')" "mnemon" "marketplace name is mnemon"
assert_eq "$(jq_ "$MP" 'len(d["plugins"])')" "1" "marketplace lists one plugin"
assert_eq "$(jq_ "$MP" 'd["plugins"][0]["name"]')" "mnemon" "marketplace plugin is mnemon"
assert_eq "$(jq_ "$MP" 'd["plugins"][0]["source"]')" "./plugins/mnemon" "marketplace source is ./plugins/mnemon"
assert_dir_exists "$MNEMON_ROOT/plugins/mnemon/skills" "plugin ships its skills"
[[ -d "$MNEMON_ROOT/plugins/mnemon/skills/source-search/scripts" ]] && fail "no scripts bundled with source-search" "scripts/ exists" || pass "no scripts bundled with source-search (search lives in the engine)"

# --- 3. skills call the engine through one path form only ---
echo "--- skill path form ---"
ONE_FORM='"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"'
for skill in "$MNEMON_ROOT"/plugins/mnemon/skills/*/SKILL.md; do
  name="$(basename "$(dirname "$skill")")"
  n=$(grep -cF "$ONE_FORM" "$skill" || true)
  [[ "$n" -ge 1 ]] && pass "$name uses the one path form (${n} occurrence(s))" || fail "$name uses the one path form" "0 occurrences"
  assert_eq "$(grep -c 'CLAUDE_PLUGIN_ROOT' "$skill" || true)" "0" "$name never runs a plugin-bundled script"
  stray=$(grep -F 'bin/mnemon' "$skill" | grep -vF "$ONE_FORM" || true)
  assert_eq "$stray" "" "$name never names bin/mnemon outside the one path form"
  assert_eq "$(sed -n '2p' "$skill")" "name: $name" "$name frontmatter name matches its directory"
done

summary
