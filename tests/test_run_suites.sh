#!/usr/bin/env bash
# test_run_suites.sh — tests/run-suites.sh on fake suites: the table (passed / failed / not run),
# fail-fast vs --keep-going, the exit status, --both and the legacy toolchain gate, selection,
# --list, the logs — and the defect it replaces: the documented `for … || break` loop returns 0
# after a failing suite (GH #15).
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
RUNNER="$MNEMON_ROOT/tests/run-suites.sh"

echo "=== Suite Runner Tests ==="
print_toolchain; assert_toolchain_legacy

TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-runsuites-XXXX)" && pwd -P)"
run() { rc=0; out=$("$@" 2>&1) || rc=$?; }
# the table row of <suite> under <toolchain>, without the mark
row_of() { printf '%s\n' "$out" | grep -E "^. +$1 +$2 " || true; }

# mk_suite <dir> <name> <passed> <failed>: a fake suite in the helper's output format — N ✓ lines,
# M ✗ lines, the Results line, exit 1 on any failure (what `summary` does)
mk_suite() {
  cat > "$1/$2" <<EOF
#!/usr/bin/env bash
echo "=== Fake $2 ==="
i=0; while [ \$i -lt $3 ]; do i=\$((i + 1)); echo "  ✓ ok \$i"; done
i=0; while [ \$i -lt $4 ]; do i=\$((i + 1)); echo "  ✗ bad \$i: expected x, got y"; done
echo ""
echo "Results: $3 passed, $4 failed"
[ $4 -eq 0 ]
EOF
}
ALL="$TEST_TMPDIR/allpass"; MID="$TEST_TMPDIR/mid"; EMPTY="$TEST_TMPDIR/empty"
mkdir -p "$ALL" "$MID" "$EMPTY"
mk_suite "$ALL" test_a.sh 3 0; mk_suite "$ALL" test_b.sh 2 0; mk_suite "$ALL" test_c.sh 1 0
mk_suite "$MID" test_a.sh 3 0; mk_suite "$MID" test_b.sh 2 1; mk_suite "$MID" test_c.sh 1 0
# a suite that dies before its Results line (a syntax error, a missing tool, set -e)
printf '#!/usr/bin/env bash\necho "=== Fake crash ==="\nexit 127\n' > "$MID/test_d.sh"
# suites in python unittest's format (tests/test_capture_layer.sh): `Ran N tests` then OK / FAILED (…)
UT="$TEST_TMPDIR/unittest"; mkdir -p "$UT"
printf '#!/usr/bin/env bash\necho "=== Fake unittest ==="\necho "..."\necho "----"\necho "Ran 191 tests in 1.5s"\necho ""\necho "OK"\necho "PASS test_a.sh"\n' > "$UT/test_a.sh"
printf '#!/usr/bin/env bash\necho "=== Fake unittest ==="\necho "FAIL: test_x (mod.T)"\necho "ERROR: test_y (mod.T)"\necho "----"\necho "Ran 12 tests in 0.2s"\necho ""\necho "FAILED (failures=1, errors=2)"\necho "FAIL test_b.sh"\nexit 1\n' > "$UT/test_b.sh"

echo "--- the defect the runner replaces (GH #15) ---"
run bash -c 'for t in "$1"/test_*.sh; do bash "$t" >/dev/null 2>&1 || break; done' _ "$MID"
assert_eq "$rc" "0" "the documented loop returns 0 after a failing suite — the status of break"

echo "--- all suites pass ---"
run "$RUNNER" --suites-dir "$ALL" --logs "$TEST_TMPDIR/logs-all"
assert_eq "$rc" "0" "exit 0 when every suite passed"
assert_contains "$out" "run-suites: 3 suite(s) × current" "the header counts the suites and names the toolchain"
assert_contains "$out" "toolchain current: bash $BASH_VERSION" "the toolchain line names the bash the suites run under"
assert_contains "$(row_of current test_a.sh)" "passed         3       0" "a passing suite's row: passed, the counts of its Results line"
assert_contains "$(row_of current test_c.sh)" "passed         1       0" "every suite has a row"
assert_contains "$out" "run-suites: 3 suite run(s) · 3 passed · 0 failed · 0 not run · logs: $TEST_TMPDIR/logs-all" "the summary line: totals and the logs directory"
assert_file_exists "$TEST_TMPDIR/logs-all/current.test_b.sh.log" "each suite's output is kept as <toolchain>.<suite>.log"
assert_contains "$(cat "$TEST_TMPDIR/logs-all/current.test_b.sh.log")" "Results: 2 passed, 0 failed" "...with the suite's full output"
assert_not_contains "$out" "✗" "no failure lines when everything passed"

echo "--- a failure in the middle: fail fast (default) ---"
run "$RUNNER" --suites-dir "$MID" --logs "$TEST_TMPDIR/logs-mid"
assert_eq "$rc" "1" "exit 1 when a suite failed"
assert_contains "$(row_of current test_a.sh)" "passed         3       0" "the suite before the failure: passed"
assert_contains "$(row_of current test_b.sh)" "failed         2       1" "the failing suite: failed, with its counts"
assert_contains "$out" "✗ current  test_b.sh" "the failing row is marked ✗"
assert_contains "$out" "exit 1 — the failed assertions of $TEST_TMPDIR/logs-mid/current.test_b.sh.log:" "the failure names the exit status and the log"
assert_contains "$out" "|   ✗ bad 1: expected x, got y" "...and quotes the failed assertions"
assert_contains "$(row_of current test_c.sh)" "not run" "the suites after the failure are reported as not run"
assert_contains "$(row_of current test_d.sh)" "not run" "...every one of them"
assert_contains "$out" "run-suites: 4 suite run(s) · 1 passed · 1 failed · 2 not run" "the summary counts passed, failed and not run"
[[ -e "$TEST_TMPDIR/logs-mid/current.test_c.sh.log" ]] && fail "a not-run suite was not started" "log exists" || pass "a not-run suite was not started (no log)"

echo "--- a failure in the middle: --keep-going ---"
run "$RUNNER" --suites-dir "$MID" -k --logs "$TEST_TMPDIR/logs-k"
assert_eq "$rc" "1" "exit 1 with --keep-going too"
assert_contains "$(row_of current test_c.sh)" "passed         1       0" "the suites after the failure run"
assert_contains "$(row_of current test_d.sh)" "failed       —     —" "a suite that died before its Results line: failed, no counts"
assert_contains "$out" 'exit 127 and no "Results:" line — the suite did not finish' "...the exit status is reported and the log's tail shown"
assert_contains "$out" "| === Fake crash ===" "...the tail of its log"
assert_contains "$out" "run-suites: 4 suite run(s) · 2 passed · 2 failed · 0 not run" "--keep-going: nothing is not run"
run "$RUNNER" --suites-dir "$MID" --keep-going
assert_eq "$rc" "1" "--keep-going is the long form of -k"

echo "--- a suite that writes into the caller's working directory ---"
LEAK="$TEST_TMPDIR/leak"; mkdir -p "$LEAK"
mk_suite "$LEAK" test_a.sh 2 0
# passes on its own terms, but leaves `-v` and a scratch file in whatever directory it was started from
printf '#!/usr/bin/env bash\necho "=== Fake leak ==="\nprintf x > ./-v\nprintf y > scratch.tmp\necho "  ✓ fine"\necho ""\necho "Results: 1 passed, 0 failed"\n' > "$LEAK/test_b.sh"
CALLER="$TEST_TMPDIR/caller"; mkdir -p "$CALLER"
run bash -c 'cd "$1" && "$2" --suites-dir "$3" --logs "$4" -k' _ "$CALLER" "$RUNNER" "$LEAK" "$TEST_TMPDIR/logs-leak"
assert_eq "$rc" "1" "a suite that leaves files in its working directory fails the run"
assert_contains "$(row_of current test_a.sh)" "passed         2       0" "...the clean suite passes"
assert_contains "$(row_of current test_b.sh)" "failed         1       0" "...the leaking suite is failed although its own Results line is clean"
assert_contains "$out" "the suite left file(s) in its working directory (kept in $TEST_TMPDIR/logs-leak/cwd.current.test_b.sh): -v scratch.tmp" "...naming the files and where they were kept"
assert_eq "$(/bin/ls -A "$CALLER" | wc -l | tr -d ' ')" "0" "the caller's working directory stays untouched — every suite runs in a private one"
assert_file_exists "$TEST_TMPDIR/logs-leak/cwd.current.test_b.sh/-v" "the leaked file is kept for inspection"
[[ -d "$TEST_TMPDIR/logs-leak/cwd.current.test_a.sh" ]] && fail "a clean suite's working directory is removed" "still there" || pass "a clean suite's working directory is removed"
assert_contains "$(cat "$TEST_TMPDIR/logs-leak/current.test_b.sh.log")" "run-suites: test_b.sh left file(s) in its working directory: -v scratch.tmp" "...and the log records the leak"

echo "--- suites in python unittest's format ---"
run "$RUNNER" --suites-dir "$UT" -k
assert_eq "$rc" "1" "exit 1 when a unittest-format suite failed"
assert_contains "$(row_of current test_a.sh)" "passed       191       0" "a passing unittest suite: passed = Ran N tests, 0 failed"
assert_contains "$(row_of current test_b.sh)" "failed         9       3" "a failing one: failed = failures + errors, passed = the rest"
assert_contains "$out" "| FAIL: test_x (mod.T)" "...its FAIL lines are quoted"
assert_contains "$out" "| ERROR: test_y (mod.T)" "...and its ERROR lines"

echo "--- selection, --list, usage ---"
run "$RUNNER" --suites-dir "$MID" test_c.sh
assert_eq "$rc" "0" "a named suite runs alone"
assert_contains "$out" "run-suites: 1 suite(s) × current" "...and only it"
run "$RUNNER" --suites-dir "$MID" "$MID/test_a.sh" test_c.sh
assert_eq "$rc" "0" "suites may be named by path or by name"
assert_contains "$out" "2 suite run(s) · 2 passed" "...both run"
run "$RUNNER" --suites-dir "$MID" --list
assert_eq "$rc" "0" "--list exits 0"
assert_eq "$out" "$(printf 'test_a.sh\ntest_b.sh\ntest_c.sh\ntest_d.sh')" "--list prints the suites in order, nothing else"
run "$RUNNER" --list
assert_contains "$out" "test_run_suites.sh" "without --suites-dir the runner lists the repository's own suites"
assert_contains "$out" "test_hygiene.sh" "...all of them"
run "$RUNNER" --suites-dir "$MID" test_zzz.sh
assert_eq "$rc" "2" "an unknown suite is a usage error"
assert_contains "$out" "no such suite: test_zzz.sh" "...named"
run "$RUNNER" --suites-dir "$EMPTY"
assert_eq "$rc" "2" "a directory without suites is a usage error"
run "$RUNNER" --suites-dir "$TEST_TMPDIR/nowhere"
assert_eq "$rc" "2" "a missing suites directory is a usage error"
run "$RUNNER" --bogus
assert_eq "$rc" "2" "an unknown option is a usage error"
assert_contains "$out" "unknown option --bogus" "...named"
run "$RUNNER" --help
assert_eq "$rc" "0" "--help exits 0"
assert_contains "$out" "--both" "--help documents --both"

echo "--- --both and the legacy toolchain ---"
LEGACY_PATH="/usr/bin:/bin:$PATH"
legacy_bash="$(env PATH="$LEGACY_PATH" bash -c 'printf %s "$BASH_VERSION"')"
legacy_py="$(env PATH="$LEGACY_PATH" python3 --version 2>&1)"
if [[ "$legacy_bash" == 3.2.* && "$legacy_py" == "Python 3.9."* ]]; then
  echo "  (legacy toolchain present: bash $legacy_bash, $legacy_py)"
  run "$RUNNER" --suites-dir "$ALL" --both --logs "$TEST_TMPDIR/logs-both"
  assert_eq "$rc" "0" "--both exits 0 when every suite passed under both toolchains"
  assert_contains "$out" "run-suites: 3 suite(s) × current+legacy" "--both names both toolchains"
  assert_contains "$out" "toolchain legacy: bash 3.2." "the legacy toolchain line reports bash 3.2"
  assert_contains "$out" "Python 3.9." "...and python 3.9"
  assert_contains "$(row_of legacy test_a.sh)" "passed         3       0" "the legacy run has its own rows"
  assert_contains "$out" "run-suites: 6 suite run(s) · 6 passed · 0 failed · 0 not run" "the summary counts both runs"
  assert_file_exists "$TEST_TMPDIR/logs-both/legacy.test_a.sh.log" "legacy logs are kept apart from the current ones"
  run "$RUNNER" --suites-dir "$MID" --both
  assert_eq "$rc" "1" "--both exits 1 on a failure"
  assert_contains "$(row_of legacy test_a.sh)" "not run" "fail-fast marks the whole legacy run as not run"
  assert_contains "$out" "run-suites: 8 suite run(s) · 1 passed · 1 failed · 6 not run" "...and counts it"
  run "$RUNNER" --suites-dir "$ALL" --legacy
  assert_eq "$rc" "0" "--legacy runs the legacy toolchain alone"
  assert_contains "$out" "run-suites: 3 suite(s) × legacy" "...and says so"
  assert_not_contains "$out" "toolchain current:" "...without the current one"
else
  echo "  (no legacy toolchain on this host: bash $legacy_bash, $legacy_py)"
  run "$RUNNER" --suites-dir "$ALL" --both
  assert_eq "$rc" "2" "--both refuses a host without the legacy toolchain"
  assert_contains "$out" "legacy toolchain" "...and says what is missing"
  assert_not_contains "$out" "test_a.sh" "...before running anything"
  run "$RUNNER" --suites-dir "$ALL" --legacy
  assert_eq "$rc" "2" "--legacy refuses it too"
fi

echo "--- one real suite through the runner ---"
run "$RUNNER" --logs "$TEST_TMPDIR/logs-real" test_hygiene.sh
assert_eq "$rc" "0" "the repository's hygiene suite passes through the runner"
assert_contains "$(row_of current test_hygiene.sh)" "passed" "...with a passed row"
assert_not_contains "$(row_of current test_hygiene.sh)" "—" "...and real counts"

cd /
rm -rf "$TEST_TMPDIR"
summary
