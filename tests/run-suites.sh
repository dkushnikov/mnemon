#!/usr/bin/env bash
# run-suites.sh — run every tests/test_*.sh suite under one or both toolchains and tell the
# truth about the outcome: one table with a row per suite and toolchain (passed / failed /
# not run), one summary line, exit 0 only when every suite passed.
#
#   tests/run-suites.sh                      the current toolchain: the bash and python3 on PATH
#   tests/run-suites.sh --both               the current toolchain, then the legacy one
#   tests/run-suites.sh --legacy             the legacy toolchain only
#   tests/run-suites.sh -k                   keep going after a failing suite (default: stop at the
#                                            first failure and report the remaining suites as not run)
#   tests/run-suites.sh test_config.sh …     a subset of suites
#   tests/run-suites.sh --list               the suites it would run
#
# The legacy toolchain is the system bash 3.2 and python 3.9 of macOS, selected by putting
# /usr/bin:/bin first on PATH — exactly the PATH under which tests/test-helper.sh asserts both
# versions. A host without that toolchain refuses --both and --legacy (exit 2) instead of failing
# every suite on the assertion.
#
# Why this file exists: the documented loop `for t in tests/test_*.sh; do bash "$t" || break; done`
# returns the status of `break` — 0 — after a failing suite (GH #15). Each suite is honest on its
# own (`summary` exits 1 on a failure); this runner keeps that honesty across the set.
#
# Every suite runs in a private, empty working directory under the logs dir; a suite that leaves
# a file there is reported as failed (the file is kept for inspection) — suites must not depend
# on, or write into, the caller's cwd.
#
# Exit codes: 0 every suite passed · 1 a suite failed or was not run · 2 usage error, no suites,
# or the legacy toolchain is not available. Runs under bash 3.2.

set -u

HERE="$(cd "$(dirname "$0")" && pwd -P)"
SUITES_DIR="$HERE"
MODE=current
KEEP_GOING=false
LIST=false
LOG_DIR=""
SELECTED=()
LEGACY_PATH="/usr/bin:/bin:$PATH"

usage() {
  cat <<'USAGE'
Usage: tests/run-suites.sh [--both | --legacy] [-k|--keep-going] [--logs <dir>] [--suites-dir <dir>] [suite …]
       tests/run-suites.sh --list

  (default)        run every tests/test_*.sh under the current toolchain (bash and python3 on PATH)
  --both           then run every suite again under the legacy toolchain (PATH=/usr/bin:/bin first:
                   the system bash 3.2 and python 3.9); the host must have it (else exit 2)
  --legacy         the legacy toolchain only
  -k, --keep-going run on after a failing suite; without it the remaining suites are reported as
                   "not run" and the exit status is still 1
  --logs <dir>     keep each suite's output as <dir>/<toolchain>.<suite>.log (default: a temp dir,
                   named in the summary)
  --suites-dir <d> take the test_*.sh suites from <d> instead of this script's directory
  --list           print the suites and exit
  suite …          run only these suites (names in the suites dir, or paths)

Exit: 0 all passed · 1 a suite failed or was not run · 2 usage / no suites / legacy toolchain missing.
USAGE
}

die2() { printf 'run-suites: %s\n' "$*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --both)          MODE=both ;;
    --legacy)        MODE=legacy ;;
    --current)       MODE=current ;;
    -k|--keep-going) KEEP_GOING=true ;;
    --list)          LIST=true ;;
    --logs)          [[ $# -ge 2 ]] || die2 "--logs needs a directory"; LOG_DIR="$2"; shift ;;
    --logs=*)        LOG_DIR="${1#--logs=}" ;;
    --suites-dir)    [[ $# -ge 2 ]] || die2 "--suites-dir needs a directory"; SUITES_DIR="$2"; shift ;;
    --suites-dir=*)  SUITES_DIR="${1#--suites-dir=}" ;;
    -h|--help)       usage; exit 0 ;;
    -*)              die2 "unknown option $1 (see --help)" ;;
    *)               SELECTED+=("$1") ;;
  esac
  shift
done

[[ -d "$SUITES_DIR" ]] || die2 "no such suites directory: $SUITES_DIR"
SUITES_DIR="$(cd "$SUITES_DIR" && pwd -P)"

# --- the suites: every test_*.sh in the suites dir, or the ones named ---
SUITES=()
if [[ ${#SELECTED[@]} -gt 0 ]]; then
  for s in "${SELECTED[@]}"; do
    case "$s" in /*) f="$s" ;; */*) f="$PWD/$s" ;; *) f="$SUITES_DIR/$s" ;; esac
    [[ -f "$f" ]] || die2 "no such suite: $s"
    SUITES+=("$f")
  done
else
  for f in "$SUITES_DIR"/test_*.sh; do
    [[ -f "$f" ]] && SUITES+=("$f")
  done
fi
[[ ${#SUITES[@]} -gt 0 ]] || die2 "no test_*.sh suites in $SUITES_DIR"

if $LIST; then
  for f in "${SUITES[@]}"; do basename "$f"; done
  exit 0
fi

# --- toolchains ---
# tc_versions <PATH>: the bash and python3 that PATH resolves, with their versions — the suites
# will run under exactly this PATH, so this is what `bash "$suite"` and its `python3` calls get.
tc_versions() {
  local path="$1" b bp p pp
  bp="$(env PATH="$path" sh -c 'command -v bash' 2>/dev/null || echo bash)"
  b="$(env PATH="$path" bash -c 'printf %s "$BASH_VERSION"' 2>/dev/null || echo '?')"
  pp="$(env PATH="$path" sh -c 'command -v python3' 2>/dev/null || echo python3)"
  p="$(python_version "$path")"
  [[ -n "$p" ]] || p="python3 missing"
  printf 'bash %s · %s (%s · %s)' "$b" "$p" "$bp" "$pp"
}

# python_version <PATH>: the `Python X.Y.Z` line of the python3 that PATH resolves — the line that
# starts with "Python", not the first line (a warning printed before it must not pass for the version)
python_version() {
  env PATH="$1" python3 --version 2>&1 | grep -m1 -E '^Python [0-9]' || true
}

legacy_available() {
  local b p
  b="$(env PATH="$LEGACY_PATH" bash -c 'printf %s "$BASH_VERSION"' 2>/dev/null || true)"
  p="$(python_version "$LEGACY_PATH")"
  [[ "$b" == 3.2.* && "$p" == "Python 3.9."* ]]
}

TOOLCHAINS=""
case "$MODE" in
  current) TOOLCHAINS="current" ;;
  legacy)  TOOLCHAINS="legacy" ;;
  both)    TOOLCHAINS="current legacy" ;;
esac
case "$MODE" in
  legacy|both)
    if ! legacy_available; then
      die2 "the legacy toolchain (bash 3.2 and python 3.9 first on PATH=/usr/bin:/bin) is not available on this host — found $(tc_versions "$LEGACY_PATH"); run without --both/--legacy"
    fi ;;
esac

# --- logs ---
if [[ -z "$LOG_DIR" ]]; then
  LOG_DIR="$(mktemp -d -t run-suites-XXXX)" || die2 "cannot create a temp dir for the logs"
else
  mkdir -p "$LOG_DIR" || die2 "cannot create the logs directory $LOG_DIR"
fi
LOG_DIR="$(cd "$LOG_DIR" && pwd -P)"

# --- the table ---
row() {   # <mark> <toolchain> <suite> <result> <passed> <failed> <time>
  printf '%s %-8s %-24s %-8s %7s %7s %6s\n' "$1" "$2" "$3" "$4" "$5" "$6" "$7"
}

N_TOTAL=$(( ${#SUITES[@]} * $(set -- $TOOLCHAINS; echo $#) ))
printf 'run-suites: %d suite(s) × %s\n' "${#SUITES[@]}" "$(printf '%s' "$TOOLCHAINS" | tr ' ' '+')"
row ' ' toolchain suite result passed failed time

n_pass=0; n_fail=0; n_skip=0
STOP=false

for tc in $TOOLCHAINS; do
  case "$tc" in
    current) tc_path="$PATH" ;;
    legacy)  tc_path="$LEGACY_PATH" ;;
  esac
  printf 'toolchain %s: %s\n' "$tc" "$(tc_versions "$tc_path")"
  for suite in "${SUITES[@]}"; do
    name="$(basename "$suite")"
    if $STOP; then
      row '–' "$tc" "$name" 'not run' '—' '—' '—'
      n_skip=$((n_skip + 1))
      continue
    fi
    log="$LOG_DIR/$tc.$name.log"
    # every suite runs in a private, empty working directory: a suite must not depend on the
    # caller's cwd, and anything it leaves there is a leak the caller would otherwise find in
    # their own directory (a stub that took `pdftotext -v` for an output path once left a file
    # named `-v` wherever the suites were started from)
    cwd="$LOG_DIR/cwd.$tc.$name"
    rm -rf "$cwd"; mkdir -p "$cwd"
    start=$(date +%s)
    if (cd "$cwd" && env PATH="$tc_path" bash "$suite") >"$log" 2>&1; then rc=0; else rc=$?; fi
    secs=$(( $(date +%s) - start ))
    leftovers="$(cd "$cwd" && find . -mindepth 1 -maxdepth 1 | sed 's#^\./##' | sort | tr '\n' ' ' | sed 's/ $//')"
    if [[ -n "$leftovers" ]]; then
      printf 'run-suites: %s left file(s) in its working directory: %s\n' "$name" "$leftovers" >>"$log"
      [[ $rc -eq 0 ]] && rc=98
    else
      rmdir "$cwd" 2>/dev/null || true
    fi
    # the counts: the helper's last `Results: N passed, M failed` line, else python unittest's
    # `Ran N tests` with `OK` / `FAILED (failures=a, errors=b)`; a suite with neither shows —.
    # The verdict is the exit status either way.
    results="$(grep -E '^Results: [0-9]+ passed, [0-9]+ failed' "$log" | tail -1)"
    if [[ -n "$results" ]]; then
      passed="${results#Results: }"; passed="${passed%% passed*}"
      failed="${results##* passed, }"; failed="${failed%% failed*}"
    elif ran="$(grep -E '^Ran [0-9]+ tests? in ' "$log" | tail -1)" && [[ -n "$ran" ]]; then
      ran="${ran#Ran }"; ran="${ran%% *}"
      verdict="$(grep -E '^(OK|FAILED)' "$log" | tail -1)"
      failed=0
      for n in $(printf '%s' "$verdict" | grep -oE '(failures|errors)=[0-9]+' | sed 's/.*=//'); do failed=$((failed + n)); done
      passed=$((ran - failed))
      results="$ran"
    else
      passed='—'; failed='—'
    fi
    # a suite whose own verdict line counts failures but which exited 0 is failed too — the exit
    # status is the verdict, the counts must agree with it
    if [[ $rc -eq 0 && "$failed" != '—' && "$failed" -gt 0 ]]; then
      printf 'run-suites: %s exited 0 but its verdict line counts %s failure(s)\n' "$name" "$failed" >>"$log"
      rc=97
    fi
    if [[ $rc -eq 0 ]]; then
      row '✓' "$tc" "$name" passed "$passed" "$failed" "${secs}s"
      n_pass=$((n_pass + 1))
    else
      row '✗' "$tc" "$name" failed "$passed" "$failed" "${secs}s"
      n_fail=$((n_fail + 1))
      if [[ -n "$leftovers" ]]; then
        printf '    the suite left file(s) in its working directory (kept in %s): %s\n' "$cwd" "$leftovers"
      fi
      if [[ $rc -eq 98 ]]; then
        : # the suite itself passed; the leak is the failure, said above
      elif [[ $rc -eq 97 ]]; then
        printf '    the suite exited 0 but its verdict line counts %s failure(s) — see %s\n' "$failed" "$log"
      elif [[ -z "$results" ]]; then
        printf '    exit %s and no "Results:" line — the suite did not finish or reports in its own format; last lines of %s:\n' "$rc" "$log"
        tail -n 15 "$log" | sed 's/^/    | /'
      elif grep -qF '✗' "$log"; then
        printf '    exit %s — the failed assertions of %s:\n' "$rc" "$log"
        grep -F '✗' "$log" | head -n 20 | sed 's/^/    | /'
      else
        printf '    exit %s — the failures of %s:\n' "$rc" "$log"
        grep -E '^(FAIL|ERROR)[: ]' "$log" | head -n 20 | sed 's/^/    | /'
      fi
      $KEEP_GOING || STOP=true
    fi
  done
done

printf 'run-suites: %d suite run(s) · %d passed · %d failed · %d not run · logs: %s\n' \
  "$N_TOTAL" "$n_pass" "$n_fail" "$n_skip" "$LOG_DIR"
[[ $n_fail -eq 0 && $n_skip -eq 0 ]]
