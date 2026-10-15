#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Setup Tests ==="

TEST_TMPDIR=$(mktemp -d -t mnemon-test-XXXX)
VAULT="$TEST_TMPDIR/test-vault"
# Isolate the generated config to the sandbox. setup.sh and mnemon-config.sh
# both honor $MNEMON_CONFIG; without this the test clobbers (and then rm's) the
# user's real mnemon.yaml in the repo root — gitignored, so git can't restore it.
export MNEMON_CONFIG="$TEST_TMPDIR/mnemon.yaml"

# --- Test 1: Setup creates vault structure ---
# --skip-plugin keeps the test from registering this checkout as a marketplace
# in the developer's real Claude Code plugin registry.
setup_out=$(bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd --skip-plugin 2>&1)
echo "$setup_out"
assert_contains "$setup_out" "superseded by: mnemon init" "deprecation banner printed"

assert_dir_exists "$VAULT/Sources" "Sources/ created"
assert_dir_exists "$VAULT/Synthesis" "Synthesis/ created"
assert_dir_exists "$VAULT/_meta" "_meta/ created"
assert_file_exists "$VAULT/CLAUDE.md" "CLAUDE.md copied"
assert_file_exists "$VAULT/_meta/Protocol.md" "Protocol.md copied"
assert_file_exists "$VAULT/reader-context.md" "reader-context.md created"

# --- Test 2: Config generated ---
assert_file_exists "$MNEMON_CONFIG" "mnemon.yaml created"

# Check config contains correct vault path
config_vault=$(grep '^vault_path:' "$MNEMON_CONFIG" | head -1)
assert_contains "$config_vault" "$VAULT" "config has correct vault_path"

# --- Test 2b: a vault path with a space, a ' #' and a quote round-trips (F3) ---
VAULT_ODD="$TEST_TMPDIR/we ird #7 \"q\""
env MNEMON_CONFIG="$TEST_TMPDIR/mnemon-2b.yaml" bash "$MNEMON_ROOT/setup.sh" "$VAULT_ODD" --non-interactive --skip-qmd --skip-plugin >/dev/null 2>&1
assert_dir_exists "$VAULT_ODD/Sources" "setup creates a vault whose path has a space, ' #' and a quote"
rt=$(bash -c 'source "$1/bin/mnemon-config.sh" && load_config "$2" && printf "%s" "$VAULT_PATH"' _ "$MNEMON_ROOT" "$TEST_TMPDIR/mnemon-2b.yaml")
assert_eq "$rt" "$VAULT_ODD" "the generated config quotes the path, so the loader reads it back unchanged"

# --- Test 3: Idempotent — running again doesn't overwrite ---
echo "# User customized this" >> "$VAULT/CLAUDE.md"
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd --skip-plugin
last_line=$(tail -1 "$VAULT/CLAUDE.md")
assert_eq "$last_line" "# User customized this" "existing CLAUDE.md not overwritten"

# --- Test 4: Setup is executable ---
assert_executable "$MNEMON_ROOT/setup.sh" "setup.sh is executable"

# --- Test 5: the plugin step gives the skills an engine path ---
# The skills call "${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon";
# a legacy clone must make that path resolve. This is the ONE test that runs the
# plugin step, so it lifts the test-helper guard (MNEMON_SKIP_PLUGIN) and the
# environment guards (CI, CLAUDE_PLUGIN_ROOT) explicitly — inside a sandbox: a
# temp HOME, no inherited MNEMON_HOME, and a PATH without `claude`, so nothing
# can touch the developer's real plugin registry or installed engine.
FAKE_HOME="$TEST_TMPDIR/home"
mkdir -p "$FAKE_HOME"
UNGUARD="env -u MNEMON_SKIP_PLUGIN -u CI -u CLAUDE_PLUGIN_ROOT -u MNEMON_HOME"
if env PATH=/usr/bin:/bin sh -c 'command -v claude' >/dev/null 2>&1; then
  echo "  ○ skipped engine-path test: claude is on /usr/bin:/bin"
else
  out=$($UNGUARD HOME="$FAKE_HOME" PATH=/usr/bin:/bin MNEMON_CONFIG="$TEST_TMPDIR/mnemon-5.yaml" bash "$MNEMON_ROOT/setup.sh" "$TEST_TMPDIR/vault-5" --non-interactive --skip-qmd 2>&1)
  assert_contains "$out" "Engine for the skills: $FAKE_HOME/.local/share/mnemon/engine → $MNEMON_ROOT" "setup points the default engine path at this checkout"
  [[ -L "$FAKE_HOME/.local/share/mnemon/engine" ]] && pass "default engine path is a symlink" || fail "default engine path is a symlink" "not a symlink"
  assert_eq "$(readlink "$FAKE_HOME/.local/share/mnemon/engine")" "$MNEMON_ROOT" "symlink targets this checkout"
  v=$(env -u MNEMON_HOME HOME="$FAKE_HOME" MNEMON_CONFIG="$TEST_TMPDIR/mnemon-5.yaml" "$FAKE_HOME/.local/share/mnemon/engine/bin/mnemon" version 2>&1) && pass "the skills' path form runs the dispatcher" || fail "the skills' path form runs the dispatcher" "$v"
  assert_contains "$out" "claude plugin marketplace add $MNEMON_ROOT" "without claude, setup prints the manual plugin install"
  # second run: the path exists → kept, not replaced
  out=$($UNGUARD HOME="$FAKE_HOME" PATH=/usr/bin:/bin MNEMON_CONFIG="$TEST_TMPDIR/mnemon-5.yaml" bash "$MNEMON_ROOT/setup.sh" "$TEST_TMPDIR/vault-5" --non-interactive --skip-qmd 2>&1)
  assert_contains "$out" "Engine for the skills: $FAKE_HOME/.local/share/mnemon/engine" "an existing engine path is reported, not replaced"
  # MNEMON_HOME set → honoured, no symlink
  FAKE_HOME2="$TEST_TMPDIR/home2"; mkdir -p "$FAKE_HOME2"
  out=$(env -u MNEMON_SKIP_PLUGIN -u CI -u CLAUDE_PLUGIN_ROOT HOME="$FAKE_HOME2" PATH=/usr/bin:/bin MNEMON_HOME="$MNEMON_ROOT" MNEMON_CONFIG="$TEST_TMPDIR/mnemon-5.yaml" bash "$MNEMON_ROOT/setup.sh" "$TEST_TMPDIR/vault-5" --non-interactive --skip-qmd 2>&1)
  assert_contains "$out" "Engine for the skills: \$MNEMON_HOME = $MNEMON_ROOT" "MNEMON_HOME is honoured"
  [[ -e "$FAKE_HOME2/.local/share/mnemon/engine" ]] && fail "no symlink when MNEMON_HOME is set" "created" || pass "no symlink when MNEMON_HOME is set"
  # --skip-plugin → nothing is linked
  FAKE_HOME3="$TEST_TMPDIR/home3"; mkdir -p "$FAKE_HOME3"
  $UNGUARD HOME="$FAKE_HOME3" PATH=/usr/bin:/bin MNEMON_CONFIG="$TEST_TMPDIR/mnemon-5.yaml" bash "$MNEMON_ROOT/setup.sh" "$TEST_TMPDIR/vault-5" --non-interactive --skip-qmd --skip-plugin >/dev/null 2>&1
  [[ -e "$FAKE_HOME3/.local/share/mnemon/engine" ]] && fail "--skip-plugin links no engine path" "created" || pass "--skip-plugin links no engine path"

  # --- Test 6: the plugin step never runs under MNEMON_SKIP_PLUGIN / CI / CLAUDE_PLUGIN_ROOT ---
  # A stub `claude` on PATH records every invocation; the guards must leave the
  # log empty and link no engine path. The control run (no guard) proves the stub
  # is what setup.sh would call — still sandboxed: the stub, not the real claude.
  STUB="$TEST_TMPDIR/stubbin"; mkdir -p "$STUB"
  CLAUDE_LOG="$TEST_TMPDIR/claude.log"
  printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\nexit 0\n' "$CLAUDE_LOG" > "$STUB/claude"
  chmod +x "$STUB/claude"
  guard_run() {  # guard_run <home> <env assignments...>
    local h="$1"; shift
    mkdir -p "$h"
    env -u MNEMON_SKIP_PLUGIN -u CI -u CLAUDE_PLUGIN_ROOT -u MNEMON_HOME "$@" HOME="$h" PATH="$STUB:/usr/bin:/bin" MNEMON_CONFIG="$TEST_TMPDIR/mnemon-6.yaml" \
      bash "$MNEMON_ROOT/setup.sh" "$TEST_TMPDIR/vault-6" --non-interactive --skip-qmd 2>&1
  }
  out=$(guard_run "$TEST_TMPDIR/home6a" MNEMON_SKIP_PLUGIN=1)
  assert_contains "$out" "Skipped the plugin step (MNEMON_SKIP_PLUGIN=1 in the environment)" "MNEMON_SKIP_PLUGIN=1 skips the plugin step with a notice"
  assert_contains "$out" "the plugin registry was not touched" "...and says the registry is untouched"
  [[ -e "$TEST_TMPDIR/home6a/.local/share/mnemon/engine" ]] && fail "MNEMON_SKIP_PLUGIN links no engine path" "created" || pass "MNEMON_SKIP_PLUGIN links no engine path"
  out=$(guard_run "$TEST_TMPDIR/home6b" CI=true)
  assert_contains "$out" "Skipped the plugin step (CI is set)" "CI skips the plugin step"
  out=$(guard_run "$TEST_TMPDIR/home6c" CLAUDE_PLUGIN_ROOT="$TEST_TMPDIR/some-plugin")
  assert_contains "$out" "Skipped the plugin step (CLAUDE_PLUGIN_ROOT is set" "CLAUDE_PLUGIN_ROOT skips the plugin step"
  [[ -e "$CLAUDE_LOG" ]] && fail "guards: claude was never invoked" "$(cat "$CLAUDE_LOG")" || pass "guards: claude was never invoked"
  out=$(guard_run "$TEST_TMPDIR/home6d" MNEMON_SKIP_PLUGIN=0)
  assert_contains "$out" "Plugin installed" "control: MNEMON_SKIP_PLUGIN=0 does not guard — the (stub) claude is reached"
  assert_eq "$(cat "$CLAUDE_LOG" 2>/dev/null)" "plugin marketplace add $MNEMON_ROOT
plugin install mnemon@mnemon" "control: setup.sh calls marketplace add + plugin install exactly once each"
fi

# Cleanup — config lives inside $TEST_TMPDIR (via $MNEMON_CONFIG), so this covers it
rm -rf "$TEST_TMPDIR"

summary
