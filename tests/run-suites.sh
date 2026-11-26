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
#   tests/run-suites.sh --jobs 6             run up to 6 suites at once (default 1: one after another)
#   tests/run-suites.sh --jobs auto          as many as the host has cores, at most 8
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
# on, or write into, the caller's cwd. Every suite also gets a private TMPDIR under the logs dir
# (`tmp.<toolchain>.<suite>`), removed after a passing suite and kept after a failing one: what one
# suite puts in the system temp directory is never seen by another, so a check like "no
# `mnemon-map-*` directory is left behind" holds while other suites run.
#
# --jobs N starts up to N suites at once — the runs of both toolchains form one queue, the longest
# suites (START_FIRST) first, the rest in table order — and still prints one table, the rows in the
# table's order: a row waits for the rows before it, so it may appear late, never out of order or
# twice. Without -k, the first failure starts no new
# suite; the suites already running finish and keep their rows, the rest are "not run". With N > 1
# the header names the jobs and a line before the summary gives the wall-clock time and the
# longest suite; with N = 1 the output is the sequential runner's, byte for byte.
#
# One runner per logs directory: two runners on one --logs directory would remove each other's
# working directories, TMPDIRs and row files, and one of them could end with exit 0 on a table it
# never finished. Before it starts a suite or removes anything, the runner takes the directory
# with a lock in it: `.run-suites.lock`, a directory made by one atomic mkdir, whose `pid` file
# names the owner. A runner refuses a directory whose owner is alive (exit 2, naming the directory
# and the owner's pid). A lock whose owner is gone is stale: the runner takes it over and says so
# in one line on stderr. The owner removes the lock when it exits; --list takes no lock.
#
# Exit codes: 0 every suite passed · 1 a suite failed or was not run · 2 usage error, no suites,
# the legacy toolchain is not available, or a live runner owns the logs directory · 130
# interrupted (the running suites are stopped).
# Runs under bash 3.2.

set -u

HERE="$(cd "$(dirname "$0")" && pwd -P)"
SUITES_DIR="$HERE"
MODE=current
KEEP_GOING=false
LIST=false
LOG_DIR=""
JOBS=1
JOBS_CAP=8
SELECTED=()
LEGACY_PATH="/usr/bin:/bin:$PATH"

usage() {
  cat <<'USAGE'
Usage: tests/run-suites.sh [--both | --legacy] [-k|--keep-going] [-j|--jobs <n>|auto] [--logs <dir>] [--suites-dir <dir>] [suite …]
       tests/run-suites.sh --list

  (default)        run every tests/test_*.sh under the current toolchain (bash and python3 on PATH)
  --both           then run every suite again under the legacy toolchain (PATH=/usr/bin:/bin first:
                   the system bash 3.2 and python 3.9); the host must have it (else exit 2)
  --legacy         the legacy toolchain only
  -k, --keep-going run on after a failing suite; without it the remaining suites are reported as
                   "not run" and the exit status is still 1
  -j, --jobs <n>   run up to <n> suites at once (default 1); the table keeps its order. Without -k a
                   failure starts no new suite: the running ones finish, the rest are "not run".
                   auto = the host's cores, at most 8
  --logs <dir>     keep each suite's output as <dir>/<toolchain>.<suite>.log (default: a temp dir,
                   named in the summary); a failing suite's TMPDIR stays as <dir>/tmp.<toolchain>.<suite>.
                   One runner at a time: a directory that a live runner owns is refused (exit 2)
  --suites-dir <d> take the test_*.sh suites from <d> instead of this script's directory
  --list           print the suites and exit
  suite …          run only these suites (names in the suites dir, or paths)

Exit: 0 all passed · 1 a suite failed or was not run · 2 usage / no suites / legacy toolchain missing /
      logs directory in use.
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
    -j|--jobs)       [[ $# -ge 2 ]] || die2 "$1 needs a number or auto"; JOBS="$2"; shift ;;
    --jobs=*)        JOBS="${1#--jobs=}" ;;
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

# --jobs: a positive number, or auto = the online cores, capped
case "$JOBS" in
  auto)
    JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
    case "$JOBS" in ''|*[!0-9]*) JOBS=1 ;; esac
    JOBS=$((10#$JOBS))
    [[ $JOBS -ge 1 ]] || JOBS=1
    [[ $JOBS -le $JOBS_CAP ]] || JOBS=$JOBS_CAP ;;
  ''|*[!0-9]*) die2 "--jobs takes a positive number or auto, not '$JOBS'" ;;
  *) JOBS=$((10#$JOBS)); [[ $JOBS -ge 1 ]] || die2 "--jobs takes a positive number or auto, not 0" ;;
esac

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

# with more than one job, two suites of one name would run at once with one log, one working
# directory and one TMPDIR
if [[ $JOBS -gt 1 ]]; then
  seen=" "
  for f in "${SUITES[@]}"; do
    b="$(basename "$f")"
    case "$seen" in *" $b "*) die2 "--jobs: $b is named twice — the two runs would share a log, a working directory and a TMPDIR" ;; esac
    seen="$seen$b "
  done
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

tc_path_of() {   # <toolchain> → the PATH its suites run under
  case "$1" in
    current) printf '%s' "$PATH" ;;
    legacy)  printf '%s' "$LEGACY_PATH" ;;
  esac
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

# --- one runner per logs directory ---
# take_lock: own LOG_DIR through LOCK (mkdir is atomic; LOCK/pid names the owner) or refuse it
# (exit 2) while its owner lives. A stale lock is taken over through a second mkdir,
# LOCK/takeover.<the gone pid>: of the runners that found the same stale lock, one wins it; the
# others read the winner's pid on their next round. A lock with no pid file, or one that stays
# half taken over, is refused after a second: a runner is taking it, or one died while doing so.
# release_lock (the EXIT trap) removes the lock of the runner that owns it. bash runs no EXIT
# trap in a subshell or an asynchronous job, so a suite or a job never removes it.
LOCK="$LOG_DIR/.run-suites.lock"
LOCK_HELD=false
release_lock() {
  local owner
  $LOCK_HELD || return 0
  LOCK_HELD=false
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  [[ -z "$owner" || "$owner" == "$$" ]] && rm -rf "$LOCK"
  return 0
}
take_lock() {
  local owner i=0
  while :; do
    if mkdir "$LOCK" 2>/dev/null; then
      LOCK_HELD=true
      printf '%s\n' "$$" >"$LOCK/pid"
      return 0
    fi
    [[ -d "$LOCK" ]] || die2 "cannot create the lock $LOCK"
    owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
    case "$owner" in *[!0-9]*) owner="" ;; esac
    if [[ -n "$owner" ]]; then
      # (a pid file with this runner's own pid is the lock of a gone runner whose pid came back)
      if [[ "$owner" != "$$" ]] && kill -0 "$owner" 2>/dev/null; then
        die2 "the logs directory $LOG_DIR is in use by another runner (pid $owner, lock $LOCK) — wait for it to end, or pass another --logs"
      fi
      if mkdir "$LOCK/takeover.$owner" 2>/dev/null; then
        if [[ "$(cat "$LOCK/pid" 2>/dev/null || true)" == "$owner" ]]; then
          printf '%s\n' "$$" >"$LOCK/pid.$$" && mv -f "$LOCK/pid.$$" "$LOCK/pid" || die2 "cannot write $LOCK/pid"
          LOCK_HELD=true
          printf 'run-suites: took over the lock of %s from pid %s, which is gone\n' "$LOG_DIR" "$owner" >&2
          return 0
        fi
        rmdir "$LOCK/takeover.$owner" 2>/dev/null || true
      fi
    fi
    [[ $i -lt 20 ]] || die2 "the logs directory $LOG_DIR holds a half-taken lock ($LOCK): a runner is taking it, or one died while doing so — remove $LOCK once no runner uses the directory"
    i=$((i + 1))
    sleep 0.05
  done
}
trap release_lock EXIT
take_lock

# --- the table ---
row() {   # <mark> <toolchain> <suite> <result> <passed> <failed> <time>
  printf '%s %-8s %-24s %-8s %7s %7s %6s\n' "$1" "$2" "$3" "$4" "$5" "$6" "$7"
}

# run_suite <toolchain> <suite>: run one suite and print its table row, followed by the lines that
# explain a failure; return 0 when it passed. The caller sends the output to a file and prints it
# when the row's turn comes. The suite's own output goes to its log.
run_suite() {
  local tc="$1" suite="$2" tc_path name log cwd tmp start rc secs leftovers results passed failed ran verdict n
  tc_path="$(tc_path_of "$tc")"
  name="$(basename "$suite")"
  log="$LOG_DIR/$tc.$name.log"
  # every suite runs in a private, empty working directory: a suite must not depend on the
  # caller's cwd, and anything it leaves there is a leak the caller would otherwise find in
  # their own directory (a stub that took `pdftotext -v` for an output path once left a file
  # named `-v` wherever the suites were started from)
  cwd="$LOG_DIR/cwd.$tc.$name"
  # ...and with a private TMPDIR: suites that run at the same time (--jobs, or another pass in
  # another checkout) never see each other's temp files
  tmp="$LOG_DIR/tmp.$tc.$name"
  rm -rf "$cwd" "$tmp"; mkdir -p "$cwd" "$tmp"
  start=$(date +%s)
  if (cd "$cwd" && env PATH="$tc_path" TMPDIR="$tmp" bash "$suite") >"$log" 2>&1; then rc=0; else rc=$?; fi
  secs=$(( $(date +%s) - start ))
  printf '%s\n' "$secs" >"$LOG_DIR/.secs.$tc.$name"
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
    rm -rf "$tmp"
    row '✓' "$tc" "$name" passed "$passed" "$failed" "${secs}s"
    return 0
  fi
  row '✗' "$tc" "$name" failed "$passed" "$failed" "${secs}s"
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
  return 1
}

# --- the queue: one job per toolchain × suite, in table order ---
J_TC=(); J_SUITE=(); J_NAME=(); J_STATE=(); J_PID=(); J_RC=()
for tc in $TOOLCHAINS; do
  for suite in "${SUITES[@]}"; do
    J_TC+=("$tc"); J_SUITE+=("$suite"); J_NAME+=("$(basename "$suite")")
    J_STATE+=(wait); J_PID+=(""); J_RC+=("")
  done
done
NJ=${#J_TC[@]}
N_TOTAL=$NJ

# the start order. With one job it is the table's order. With more, the suites that run longest
# start first (both toolchains' runs of each), the rest follow in table order: a pool that started
# test_capture.sh late would end on it alone. Only the start order changes, never the table's; a
# name that is not among the suites is skipped. Measured on a 6-job pass: test_capture.sh ≈ 680 s,
# test_mnemon_cli.sh ≈ 410 s, test_capture_layer.sh ≈ 300 s, every other suite under 200 s.
START_FIRST="test_capture.sh test_mnemon_cli.sh test_capture_layer.sh"
J_ORDER=(); J_PICKED=()
if [[ $JOBS -gt 1 ]]; then
  for n in $START_FIRST; do
    k=0
    while [[ $k -lt $NJ ]]; do
      if [[ "${J_NAME[$k]}" == "$n" ]]; then J_ORDER+=("$k"); J_PICKED[$k]=1; fi
      k=$((k + 1))
    done
  done
fi
k=0
while [[ $k -lt $NJ ]]; do
  [[ -n "${J_PICKED[$k]:-}" ]] || J_ORDER+=("$k")
  k=$((k + 1))
done

job_file() { printf '%s/.%s.%s.%s' "$LOG_DIR" "$1" "${J_TC[$2]}" "${J_NAME[$2]}"; }   # <kind> <job>

# start_job <k>: with one job, run the suite in the foreground (the sequential runner, stdin and
# all); with more, in the background with stdin from /dev/null. The job writes its row to .row.*,
# then its status to .done.*: the loop reaps it once .done.* exists. A background job starts with
# job control on (set -m, for the fork only) so that it gets its own process group, which an
# interrupt can stop as a whole, and keeps the default SIGINT and SIGQUIT — without job control
# bash starts an asynchronous command with both ignored, and a suite that started with SIGINT
# ignored cannot trap it: it would run in another environment than under the sequential runner.
start_job() {
  local k="$1" out done_f
  out="$(job_file row "$k")"; done_f="$(job_file done "$k")"
  rm -f "$out" "$done_f"
  if [[ $JOBS -eq 1 ]]; then
    if run_suite "${J_TC[$k]}" "${J_SUITE[$k]}" >"$out"; then J_RC[$k]=0; else J_RC[$k]=1; fi
    J_STATE[$k]=done
  else
    set -m
    { if run_suite "${J_TC[$k]}" "${J_SUITE[$k]}" >"$out"; then r=0; else r=1; fi
      printf '%s\n' "$r" >"$done_f"; exit "$r"; } </dev/null &
    J_PID[$k]=$!
    set +m
    J_STATE[$k]=run
    RUNNING=$((RUNNING + 1))
  fi
}

# reap_jobs: every running job whose .done.* exists — or whose process is gone without one, which
# is a failure the runner reports itself — becomes done
reap_jobs() {
  local k done_f
  k=0
  while [[ $k -lt $NJ ]]; do
    if [[ "${J_STATE[$k]}" == run ]]; then
      done_f="$(job_file done "$k")"
      if [[ -s "$done_f" ]] || ! kill -0 "${J_PID[$k]}" 2>/dev/null; then
        wait "${J_PID[$k]}" 2>/dev/null
        if [[ -s "$done_f" ]]; then
          J_RC[$k]="$(cat "$done_f")"
        else
          J_RC[$k]=1
          { row '✗' "${J_TC[$k]}" "${J_NAME[$k]}" failed '—' '—' '—'
            printf '    the job running this suite ended without a result — see %s\n' "$LOG_DIR/${J_TC[$k]}.${J_NAME[$k]}.log"
          } >"$(job_file row "$k")"
        fi
        rm -f "$done_f"
        J_STATE[$k]=done
        RUNNING=$((RUNNING - 1))
        PROGRESS=true
        [[ "${J_RC[$k]}" == 0 ]] || $KEEP_GOING || STOP=true
      fi
    fi
    k=$((k + 1))
  done
}

# kill_jobs <signal>: send it to the process group of every running job; print how many there were
kill_jobs() {
  local k=0 n=0
  while [[ $k -lt $NJ ]]; do
    if [[ "${J_STATE[$k]}" == run ]]; then kill "-$1" -- "-${J_PID[$k]}" 2>/dev/null; n=$((n + 1)); fi
    k=$((k + 1))
  done
  printf '%s' "$n"
}
# interrupted: stop every running suite — its whole process group, TERM, and KILL for what is left
# after 5 s — say so, exit 130
on_interrupt() {
  local n i=0 k alive
  trap - INT TERM
  n="$(kill_jobs TERM)"
  while [[ $i -lt 50 ]]; do
    alive=false; k=0
    while [[ $k -lt $NJ ]]; do
      if [[ "${J_STATE[$k]}" == run ]] && kill -0 -- "-${J_PID[$k]}" 2>/dev/null; then alive=true; fi
      k=$((k + 1))
    done
    $alive || break
    sleep 0.1; i=$((i + 1))
  done
  $alive && kill_jobs KILL >/dev/null
  wait 2>/dev/null
  printf '\nrun-suites: interrupted — %d running suite(s) stopped · logs: %s\n' "$n" "$LOG_DIR" >&2
  exit 130
}
if [[ $JOBS -gt 1 ]]; then
  trap on_interrupt INT TERM
  printf 'run-suites: %d suite(s) × %s · %d jobs\n' "${#SUITES[@]}" "$(printf '%s' "$TOOLCHAINS" | tr ' ' '+')" "$JOBS"
else
  printf 'run-suites: %d suite(s) × %s\n' "${#SUITES[@]}" "$(printf '%s' "$TOOLCHAINS" | tr ' ' '+')"
fi
row ' ' toolchain suite result passed failed time

n_pass=0; n_fail=0; n_skip=0
STOP=false
RUNNING=0
NEXT_START=0      # the next position in J_ORDER to start
NEXT_PRINT=0      # the next row to print
HEADER_TC=""      # the toolchain whose line is printed
LONGEST=0; LONGEST_NAME=""
WALL_START=$(date +%s)

while [[ $NEXT_PRINT -lt $NJ ]]; do
  PROGRESS=false
  [[ $RUNNING -gt 0 ]] && reap_jobs
  # print every row whose turn has come: a finished job, or a job that will never start
  while [[ $NEXT_PRINT -lt $NJ ]]; do
    k=$NEXT_PRINT
    if [[ "${J_TC[$k]}" != "$HEADER_TC" ]]; then
      HEADER_TC="${J_TC[$k]}"
      printf 'toolchain %s: %s\n' "$HEADER_TC" "$(tc_versions "$(tc_path_of "$HEADER_TC")")"
    fi
    if [[ "${J_STATE[$k]}" == done ]]; then
      cat "$(job_file row "$k")"; rm -f "$(job_file row "$k")"
      if [[ "${J_RC[$k]}" == 0 ]]; then n_pass=$((n_pass + 1)); else n_fail=$((n_fail + 1)); fi
      secs="$(cat "$(job_file secs "$k")" 2>/dev/null || echo 0)"; rm -f "$(job_file secs "$k")"
      if [[ "$secs" -gt $LONGEST || -z "$LONGEST_NAME" ]]; then LONGEST=$secs; LONGEST_NAME="${J_TC[$k]} ${J_NAME[$k]}"; fi
    elif [[ "${J_STATE[$k]}" == wait ]] && $STOP; then
      row '–' "${J_TC[$k]}" "${J_NAME[$k]}" 'not run' '—' '—' '—'
      J_STATE[$k]=skip
      n_skip=$((n_skip + 1))
    else
      break
    fi
    NEXT_PRINT=$((NEXT_PRINT + 1))
    PROGRESS=true
  done
  # start jobs while there is room
  while ! $STOP && [[ $NEXT_START -lt $NJ && $RUNNING -lt $JOBS ]]; do
    start_job "${J_ORDER[$NEXT_START]}"
    NEXT_START=$((NEXT_START + 1))
    PROGRESS=true
    [[ $JOBS -eq 1 ]] && break
  done
  if [[ $JOBS -eq 1 ]]; then
    # the sequential runner: the job just run is printed next; a failure stops it without -k
    [[ $NEXT_START -gt 0 && "${J_RC[${J_ORDER[$((NEXT_START - 1))]}]}" == 1 ]] && ! $KEEP_GOING && STOP=true
  fi
  $PROGRESS || sleep 0.2
done

if [[ $JOBS -gt 1 ]]; then
  printf 'run-suites: wall clock %ds with %d jobs · longest suite %s (%ds)\n' \
    "$(( $(date +%s) - WALL_START ))" "$JOBS" "$LONGEST_NAME" "$LONGEST"
fi
printf 'run-suites: %d suite run(s) · %d passed · %d failed · %d not run · logs: %s\n' \
  "$N_TOTAL" "$n_pass" "$n_fail" "$n_skip" "$LOG_DIR"
[[ $n_fail -eq 0 && $n_skip -eq 0 ]]
