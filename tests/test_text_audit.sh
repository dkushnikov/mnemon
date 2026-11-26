#!/usr/bin/env bash
# test_text_audit.sh — `mnemon audit text-mismatch` (bin/text-mismatch-audit.py; capture layer §18, A17): the
# python suite tests/test_text_audit.py (synthetic vaults, one fixture per result class, the flags, the JSON,
# the dispatcher, the conversion deadline, the no-write property) under the toolchain this suite runs in. The
# pure rule (capture.check.source_presence) is tested by tests/capture_layer/test_source_presence.py.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

echo "=== Text Mismatch Audit Tests ==="
print_toolchain; assert_toolchain_legacy

TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-tma-XXXX)" && pwd -P)"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR

if python3 -B "$MNEMON_ROOT/tests/test_text_audit.py" >"$TEST_TMPDIR/py.log" 2>&1; then
  pass "python suite: $(grep -Eo 'Ran [0-9]+ tests' "$TEST_TMPDIR/py.log" | head -1)"
else
  fail "python suite" "see output below"
  cat "$TEST_TMPDIR/py.log"
fi

# the help names the wording rule: candidates, and the causes other than the body
out="$("$MNEMON_ROOT/bin/mnemon" audit text-mismatch --help 2>&1 || true)"
assert_contains "$out" "a candidate, not a finding" "help: a mismatch is a candidate"
assert_contains "$out" "converter drift" "help: converter drift produces a mismatch too"
out="$("$MNEMON_ROOT/bin/mnemon" help 2>&1 || true)"
assert_contains "$out" "audit text-mismatch" "mnemon help lists audit text-mismatch"

rm -rf "$TEST_TMPDIR"
summary
