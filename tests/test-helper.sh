#!/usr/bin/env bash
# Minimal test framework for Mnemon
set -euo pipefail

PASS=0
FAIL=0

# No test may register the checkout under test as a Claude Code plugin
# marketplace: setup.sh skips its plugin step while this is set (tests also pass
# --skip-plugin; this is the belt to that braces). The one test that exercises
# the plugin step does so in a sandbox and unsets it explicitly.
export MNEMON_SKIP_PLUGIN=1

pass() { echo "  ✓ $1"; ((PASS++)) || true; }
fail() { echo "  ✗ $1: $2"; ((FAIL++)) || true; }

assert_eq() {
  local actual="$1" expected="$2" msg="$3"
  [[ "$actual" == "$expected" ]] && pass "$msg" || fail "$msg" "expected '$expected', got '$actual'"
}

assert_contains() {
  local haystack="$1" needle="$2" msg="$3"
  [[ "$haystack" == *"$needle"* ]] && pass "$msg" || fail "$msg" "expected to contain '$needle'"
}

assert_not_contains() {
  local haystack="$1" needle="$2" msg="$3"
  [[ "$haystack" != *"$needle"* ]] && pass "$msg" || fail "$msg" "expected NOT to contain '$needle'"
}

assert_file_exists() {
  local path="$1" msg="$2"
  [[ -f "$path" ]] && pass "$msg" || fail "$msg" "file not found: $path"
}

assert_dir_exists() {
  local path="$1" msg="$2"
  [[ -d "$path" ]] && pass "$msg" || fail "$msg" "dir not found: $path"
}

assert_executable() {
  local path="$1" msg="$2"
  [[ -x "$path" ]] && pass "$msg" || fail "$msg" "not executable: $path"
}

# wait_file <path> [seconds=10]: poll until the file exists — the readiness signal of a
# background lock holder that creates it after its flock succeeded. Never a fixed sleep window
# (a loaded machine makes those lie), never a probe that could take the lock from the holder for
# an instant. Prints a note on timeout; the assertions that follow report the consequence.
wait_file() {
  local path="$1" limit="${2:-10}" i=0
  while [[ ! -e "$path" ]]; do
    if [[ $i -ge $((limit * 20)) ]]; then echo "  ! wait_file: $path did not appear within ${limit}s"; return 0; fi
    sleep 0.05; i=$((i + 1))
  done
  return 0
}

summary() {
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  [[ $FAIL -eq 0 ]]
}
