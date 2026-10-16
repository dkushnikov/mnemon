#!/usr/bin/env bash
# test_hygiene.sh — public-repo gates: no private references outside LICENSE,
# valid plugin manifests, skills on the single engine path form.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

echo "=== Hygiene Tests ==="
print_toolchain; assert_toolchain_legacy
assert_contains "$(print_toolchain)" "toolchain: bash $BASH_VERSION" "print_toolchain names the bash that runs the suite (\$BASH_VERSION), not the one PATH resolves (G30)"

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

# --- 4. python sources compile cleanly: a SyntaxWarning (e.g. an invalid escape
#        sequence in a docstring) is an error here, as `python3 -W error` would make it
echo "--- python compile (-W error) ---"
py_ok=0 py_bad=0
for f in "$MNEMON_ROOT"/bin/*.py "$MNEMON_ROOT"/bin/importers/*.py "$MNEMON_ROOT"/tests/*.py; do
  [[ -f "$f" ]] || continue
  if err=$(python3 -W error -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])' "$f" 2>&1); then
    py_ok=$((py_ok + 1))
  else
    py_bad=$((py_bad + 1))
    fail "compiles under -W error: ${f#$MNEMON_ROOT/}" "$(printf '%s' "$err" | tail -1)"
  fi
done
[[ $py_bad -eq 0 ]] && pass "all $py_ok python sources compile under -W error"

# --- 5. python 3.9 compatibility: `X | None` in an annotation is evaluated at
#        import time on 3.9 unless the module postpones annotations
echo "--- python 3.9 annotations ---"
py39=$(python3 - "$MNEMON_ROOT" <<'PY'
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = sorted(list((root / "bin").glob("*.py")) + list((root / "bin" / "importers").glob("*.py")))
bad = []
for f in files:
    tree = ast.parse(f.read_text(encoding="utf-8"), str(f))
    postponed = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                    and any(a.name == "annotations" for a in n.names) for n in tree.body)
    if postponed:
        continue
    annotations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations += [a.annotation for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs if a.annotation]
            if node.args.vararg and node.args.vararg.annotation: annotations.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation: annotations.append(node.args.kwarg.annotation)
            if node.returns: annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
    for ann in annotations:
        if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(ann)):
            bad.append(f"{f.relative_to(root)}:{ann.lineno}")
            break
print("\n".join(bad))
PY
)
assert_eq "$py39" "" "PEP 604 unions in annotations only in modules with 'from __future__ import annotations'"
[[ -n "$py39" ]] && printf '%s\n' "$py39"

# --- 6. the contract fixtures (tests/fixtures/contracts/) equal generate.py's output —
#        the JSON files are generated, never hand-edited (capture contracts §5.3, §9.16)
echo "--- contract fixtures ---"
CONTRACTS="$MNEMON_ROOT/tests/fixtures/contracts"
GEN_TMP="$(mktemp -d -t mnemon-fixtures-XXXX)"
cp "$CONTRACTS/generate.py" "$GEN_TMP/"
if python3 "$GEN_TMP/generate.py" >/dev/null 2>&1; then
  for f in envelopes.json expected-argv.json gateway-outcomes.json README.md; do
    if cmp -s "$GEN_TMP/$f" "$CONTRACTS/$f"; then
      pass "tests/fixtures/contracts/$f equals generate.py's output"
    else
      fail "tests/fixtures/contracts/$f equals generate.py's output" "differs — edit generate.py and run: python3 tests/fixtures/contracts/generate.py"
    fi
  done
else
  fail "tests/fixtures/contracts/generate.py runs" "python3 generate.py failed in $GEN_TMP"
fi
rm -rf "$GEN_TMP"

# --- 7. the report fixtures (tests/fixtures/capture/reports/) equal generate.py's output — the frozen
#        seam contracts of the capture layer are generated, never hand-edited (capture layer §12 A12; G27)
echo "--- report fixtures ---"
REPORTS="$MNEMON_ROOT/tests/fixtures/capture/reports"
GEN_TMP="$(mktemp -d -t mnemon-reports-XXXX)"
cp "$REPORTS/generate.py" "$GEN_TMP/"
if python3 "$GEN_TMP/generate.py" >/dev/null 2>&1; then
  n_same=0
  for f in $(python3 - "$REPORTS/generate.py" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
block = src[src.index("FILES = {"):]
print("\n".join(re.findall(r'^\s*"([^"]+\.json)":', block, re.MULTILINE)))
PY
  ); do
    if cmp -s "$GEN_TMP/$f" "$REPORTS/$f"; then
      n_same=$((n_same + 1))
    else
      fail "tests/fixtures/capture/reports/$f equals generate.py's output" "differs — edit generate.py and run: python3 tests/fixtures/capture/reports/generate.py"
    fi
  done
  [[ $n_same -ge 14 ]] && pass "every report fixture ($n_same files) equals generate.py's output" || fail "every report fixture equals generate.py's output" "only $n_same compared"
  stray="$(cd "$REPORTS" && find . -name '*.json' | sed 's#^\./##' | sort | comm -23 - <(cd "$GEN_TMP" && find . -name '*.json' | sed 's#^\./##' | sort))"
  assert_eq "$stray" "" "no report fixture outside generate.py's output"
else
  fail "tests/fixtures/capture/reports/generate.py runs" "python3 generate.py failed in $GEN_TMP"
fi
rm -rf "$GEN_TMP"

summary
