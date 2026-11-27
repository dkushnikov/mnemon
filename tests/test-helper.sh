#!/usr/bin/env bash
# Minimal test framework for Mnemon
set -euo pipefail

PASS=0
FAIL=0

# scrub_test_env: the test hooks, stub overrides, lock descriptors, vendor keys and proxies a suite
# could inherit from the shell that runs it are cleared before the suite starts (capture layer
# §7.16) — a stray MNEMON_POLICY_ALLOW_LOOPBACK or FIRECRAWL_API_KEY in the environment must never
# change what a suite proves. Every suite inherits this by sourcing the helper; a suite that needs a
# hook sets it itself, after this. TESSDATA_PREFIX is cleared too: the witness's probe prefers it to the
# stub tesseract's model directory (capture layer §21.3), so an inherited one would make a suite read
# the machine's language models; a test that needs it sets it itself.
scrub_test_env() {
  unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT MNEMON_TEST_RAW_LOSS_AT MNEMON_TEST_RAW_LOSS MNEMON_POLICY_ALLOW_LOOPBACK MNEMON_FETCH_URL_REWRITE \
        MNEMON_EXTERNAL_FETCH_CMD MNEMON_TEST_CONVERT_HANG MNEMON_TEST_WORKER_START_DELAY MNEMON_TEST_WORKER_START_FILE MNEMON_TEST_MAP_HANG MNEMON_TEST_SITE_PACE_S MNEMON_CAPTURE_FETCH_CMD MNEMON_CAPTURE_SITE_CMD MNEMON_CAPTURE_TEXT_CMD MNEMON_CAPTURE_CAPTIONS_CMD MNEMON_DEPTH_FOLD_CMD \
        MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE MNEMON_SMOKE_CMD MNEMON_SESSION MNEMON_TEST_LOADER_LOG MNEMON_RESOLVED \
        MNEMON_TEST_DOCTOR_STALL MNEMON_TEST_DOCTOR_STALL_INIT MNEMON_TEST_DOCTOR_PIDFILE MNEMON_TEST_DOCTOR_RAISE MNEMON_TEST_DOCTOR_TODAY \
        OCR_CMD MNEMON_TEST_TESSERACT_MODE MNEMON_TEST_READER_MODE MNEMON_TEST_IMAGE_WORKER_CRASH MNEMON_TEST_IMAGE_ENVELOPE_HANG \
        MNEMON_TEST_TESSERACT_CALLS MNEMON_TEST_TESSDATA_DIR MNEMON_TEST_READER_CALLS MNEMON_TEST_READER_FACTS \
        TESSDATA_PREFIX \
        MNEMON_TEST_IMAGE_SWAP_INPUT MNEMON_TEST_REPORT_FREEZE_FAIL \
        FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY
}
scrub_test_env
# MNEMON_CAPTURE_IMAGE_CMD is not in the unset list above: it is set here, which replaces an inherited value. The
# doctor runs `capture-image.py --probe` (capture layer §21.11); through this stub every suite's doctor and image
# dry run are hermetic and give one answer with or without a tesseract on the machine. The stub answers --probe
# alone and exits 2 on any other call: a suite that captures an image through the real worker unsets the
# variable for that section (tests/test_image.sh, the image sections of test_gateway.sh and test_raw_integrity.sh).
export MNEMON_CAPTURE_IMAGE_CMD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/fixtures/capture/image/stub-probe.sh"

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

# Portable forms of two BSD-only idioms the suites used (`sed -i ''`, `stat -f %Lp`), so the
# suites run under GNU utilities too (ubuntu CI).
sed_inplace() {   # <sed expression> <file>
  local expr="$1" f="$2"
  sed "$expr" "$f" > "$f.sed-tmp" && mv "$f.sed-tmp" "$f"
}
file_mode() {   # <path> → the permission bits as three octal digits (600, 700, …)
  python3 -c 'import os, sys; print("%03o" % (os.stat(sys.argv[1]).st_mode & 0o777))' "$1"
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
