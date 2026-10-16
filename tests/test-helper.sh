#!/usr/bin/env bash
# Minimal test framework for Mnemon
set -euo pipefail

PASS=0
FAIL=0

# scrub_test_env: the test hooks, stub overrides, lock descriptors, vendor keys and proxies a suite
# could inherit from the shell that runs it are cleared before the suite starts (capture layer
# §7.16) — a stray MNEMON_POLICY_ALLOW_LOOPBACK or FIRECRAWL_API_KEY in the environment must never
# change what a suite proves. Every suite inherits this by sourcing the helper; a suite that needs a
# hook sets it itself, after this.
scrub_test_env() {
  unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT MNEMON_POLICY_ALLOW_LOOPBACK MNEMON_FETCH_URL_REWRITE \
        MNEMON_EXTERNAL_FETCH_CMD MNEMON_TEST_CONVERT_HANG MNEMON_CAPTURE_FETCH_CMD MNEMON_CAPTURE_SITE_CMD MNEMON_CAPTURE_TEXT_CMD MNEMON_DEPTH_FOLD_CMD \
        MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE MNEMON_SMOKE_CMD \
        FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY
}
scrub_test_env

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

# The toolchain proof of capture layer §7.16 / §12 A12: every suite prints the bash and python it runs
# under, and under the legacy PATH run (PATH=/usr/bin:/bin:…) asserts — never assumes — that the
# running bash is 3.2.x and the python3 it will call is 3.9.x.
print_toolchain() {
  # the bash that runs this suite ($BASH_VERSION), never the one PATH resolves — a forced bash 5.3 under the
  # legacy PATH used to print "GNU bash, version 3.2.57" and only the assertion told the truth (G30)
  echo "toolchain: bash $BASH_VERSION · $(python3 --version 2>&1) ($(command -v python3 2>/dev/null || echo python3))"
}

assert_toolchain_legacy() {
  case "$PATH" in
    /usr/bin:/bin|/usr/bin:/bin:*)
      [[ "$BASH_VERSION" == 3.2.* ]] && pass "legacy toolchain: bash 3.2.x" || fail "legacy toolchain: bash 3.2.x" "running bash $BASH_VERSION"
      [[ "$(python3 --version 2>&1)" == "Python 3.9."* ]] && pass "legacy toolchain: python 3.9.x" || fail "legacy toolchain: python 3.9.x" "$(python3 --version 2>&1)" ;;
  esac
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
