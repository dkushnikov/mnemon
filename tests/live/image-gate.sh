#!/usr/bin/env bash
# tests/live/image-gate.sh — the manual live gate of the image origin (capture layer §21.13, the last row).
#
#   MNEMON_FIXTURES_DIR=/path/outside/the/repo tests/live/image-gate.sh [--effort <level>] [--model <model>]
#                                                                     [--max-usd <usd>] [--only <item>]
#
# Real `claude` and real tesseract; never inside a suite. It refuses to start when MNEMON_TEST_HOOKS is set
# (exit 2); without `claude`, `tesseract` or `python3` on PATH it prints `not run: <what is missing>` and exits
# 0. Per image it runs the worker bin/capture-image.py — at the configured effort level (`--effort`, default
# `high`, the default of `image_reader_effort`), with `--model` (default `haiku`) and the budget cap `--max-usd`
# (default 0.05), under the default `image_timeout` (120 s) — with an attempt dir of its own, and reads the
# report. The snapshot lands outside the repository: <MNEMON_FIXTURES_DIR>/<date>/image-<time>-<effort>/, one
# directory per item (attempt/ with the report, reading.txt and witness.txt; the wrapper record; the lifecycle
# record; item.json), the generated inputs under inputs/, and summary.md beside them.
#
# The items (--only takes one name; `repeat` brings `post` with it) and their thresholds:
#   post          img/post-en.png               agree; every word of the known text, with multiplicity
#   repeat        img/post-en.png, read again   the post's thresholds; whether the two readings are equal (recorded)
#   page          img/paper-2col.png            agree; every word; the left column's last word before the right's first
#   quote         img/quote-short.jpg           agree
#   russian       img/cyrillic.png (rus+eng)    agree; `not run: language model rus missing` when the probe misses it
#   ai            img/post-ai.png               agree; `AI` right 25 times of 25
#   dark-invert   img/post-en-dark-invert.png   agree
#   dark-theme    img/post-en-dark-theme.png    agree
#   block         img/post-en-block.png         agree; every word of both texts (the post and the page)
#   column-4200   inputs/column-4200.png        agree; every word of the post, twice
#   column-8000   inputs/column-8000.png        agree; every word of the post, four times
#   column-8001   inputs/column-8001.png        too-large at the envelope (reason edge); no model call
#   rotated       inputs/quote-exif6.jpg        unsupported/rotated at the envelope (orientation 6); no model call
#   witness       img/post-en.png               the worker's witness alone (tests/live/image/witness_repeat.py):
#                                               five runs, one text
# The words, the order and the `AI` count are the gate's own thresholds (tests/live/image/score.py).
#
# The real client's lifecycle, on every worker run (contract K9): the gate opens a scratch file on descriptor
# 9 and names it in MNEMON_VAULT_LOCK_FD (and its inode in MNEMON_VAULT_LOCK_INODE) for the worker's call. The
# worker's `--claude` is tests/live/image/claude-wrap.py: it records its pid, its process group, the names of
# the MNEMON_VAULT_LOCK_* variables it sees and its open descriptors, then execs the real `claude`. Beside the
# call, tests/live/image/lifecycle.py samples the process table for the descendants of the worker; once the
# worker has ended it asserts that no member of the worker's process group and no descendant of the reader's
# `claude` is left, and that the reader's process saw neither the descriptor nor a lock variable. A control
# run of the wrapper with the descriptor open first proves that the record would show it.
#
# Prints numbers and verdicts only — never a text of a reading: the texts stay in the snapshot. The closing
# line counts passed / failed / not run. Exit 1 when any threshold fails, 2 on a usage error or a refusal.
# Bash 3.2.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
HERE="$ROOT/tests/live/image"
WRAP="$HERE/claude-wrap.py"
ITEMS="post repeat page quote russian ai dark-invert dark-theme block column-4200 column-8000 column-8001 rotated witness"
EFFORT="high"
MODEL="haiku"
MAX_USD="0.05"
ONLY=""
TIMEOUT="120"

usage() {
  echo "usage: tests/live/image-gate.sh [--effort <level>] [--model <model>] [--max-usd <usd>] [--only <item>]" >&2
  echo "items: $ITEMS" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --effort|--model|--max-usd|--only)
      [ $# -ge 2 ] || usage
      case "$1" in
        --effort) EFFORT="$2" ;;
        --model) MODEL="$2" ;;
        --max-usd) MAX_USD="$2" ;;
        --only) ONLY="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage ;;
    *) echo "image-gate: unknown argument: $1" >&2; usage ;;
  esac
done
case "$EFFORT" in
  low|medium|high|xhigh|max) ;;
  *) echo "image-gate: --effort takes low, medium, high, xhigh or max" >&2; exit 2 ;;
esac
case "$MODEL" in
  ''|-*) echo "image-gate: --model takes a model name" >&2; exit 2 ;;
esac
case "$MAX_USD" in
  ''|.|*[!0-9.]*|*.*.*) echo "image-gate: --max-usd takes a positive number" >&2; exit 2 ;;
esac
case "$MAX_USD" in
  *[1-9]*) ;;
  *) echo "image-gate: --max-usd takes a positive number" >&2; exit 2 ;;
esac
if [ -n "$ONLY" ]; then
  case " $ITEMS " in
    *" $ONLY "*) ;;
    *) echo "image-gate: --only: unknown item: $ONLY" >&2; usage ;;
  esac
fi

if [ -n "${MNEMON_TEST_HOOKS+x}" ]; then
  echo "image-gate: refused: MNEMON_TEST_HOOKS is set — the live gate runs the real claude and tesseract, never inside a suite" >&2
  exit 2
fi

REAL_CLAUDE="$(command -v claude 2>/dev/null || true)"
case "$REAL_CLAUDE" in
  /*) ;;
  *) REAL_CLAUDE="" ;;
esac
missing=""
[ -n "$REAL_CLAUDE" ] || missing="claude"
command -v tesseract >/dev/null 2>&1 || missing="${missing:+$missing, }tesseract"
command -v python3 >/dev/null 2>&1 || missing="${missing:+$missing, }python3"
if [ -n "$missing" ]; then
  echo "not run: $missing"
  exit 0
fi

if [ -z "${MNEMON_FIXTURES_DIR:-}" ]; then
  echo "image-gate: set MNEMON_FIXTURES_DIR to a snapshot directory outside the repository" >&2
  exit 2
fi
mkdir -p "$MNEMON_FIXTURES_DIR"
FIX="$(cd "$MNEMON_FIXTURES_DIR" && pwd -P)"
case "$FIX/" in
  "$ROOT/"*) echo "image-gate: refused: MNEMON_FIXTURES_DIR lies inside the repository, and the snapshot holds the readings" >&2; exit 2 ;;
esac

# the hooks of the suites stay off: with MNEMON_TEST_HOOKS unset the worker ignores them, and none reaches it
unset OCR_CMD CLAUDE_CMD MNEMON_CAPTURE_IMAGE_CMD MNEMON_TEST_TESSERACT_MODE MNEMON_TEST_READER_MODE \
  MNEMON_TEST_IMAGE_WORKER_CRASH MNEMON_TEST_IMAGE_ENVELOPE_HANG MNEMON_TEST_WORKER_START_DELAY \
  MNEMON_TEST_WORKER_START_FILE MNEMON_TEST_TESSERACT_CALLS MNEMON_TEST_READER_CALLS MNEMON_TEST_TESSDATA_DIR \
  MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
RUN="$FIX/${STAMP%%T*}/image-${STAMP#*T}-$EFFORT"
[ ! -e "$RUN" ] || RUN="$RUN-$$"
mkdir -p "$RUN"
TP="unset"
[ -z "${TESSDATA_PREFIX:-}" ] || TP="set"
echo "image gate: effort $EFFORT, model $MODEL, budget cap \$$MAX_USD, timeout ${TIMEOUT} s, items: ${ONLY:-all}; TESSDATA_PREFIX $TP"

selected() {  # <item>: is the item part of this run
  [ -z "$ONLY" ] && return 0
  [ "$ONLY" = "$1" ] && return 0
  [ "$ONLY" = "repeat" ] && [ "$1" = "post" ] && return 0
  return 1
}

file_id() {  # <path> → <st_dev>:<st_ino>
  python3 -c 'import os, sys; s = os.stat(sys.argv[1]); print("%d:%d" % (s.st_dev, s.st_ino))' "$1"
}

mark() {  # <seq> <item> <"not run"|fail> <reason>
  mkdir -p "$RUN/$2"
  python3 "$HERE/score.py" mark --seq "$1" --name "$2" --status "$3" --reason "$4" --out "$RUN/$2/item.json" || true
}

CUR_SPID=""
CUR_WPID=""
on_abort() {
  if [ -n "$CUR_SPID" ]; then kill -TERM "$CUR_SPID" 2>/dev/null || true; fi
  if [ -n "$CUR_WPID" ]; then kill -TERM "$CUR_WPID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  echo "image-gate: interrupted; the snapshot so far: $RUN" >&2
  exit 130
}
trap on_abort INT TERM

WORKER_RC=0
# the launcher of the supervisor: it waits (at most 10 s) for the watcher's go file, then execs the command —
# the same pid, the descriptor and the variables kept — so that the watcher samples before the worker starts
LAUNCH='i=0; while [ ! -e "$1" ] && [ "$i" -lt 1000 ]; do sleep 0.01; i=$((i + 1)); done; shift; exec "$@"'
run_worker() {  # <item> <image> <languages>: one worker call, watched; sets WORKER_RC
  local name="$1" image="$2" langs="$3" dir id
  dir="$RUN/$name"
  mkdir -p "$dir/attempt"
  : > "$dir/lock-scratch"
  id="$(file_id "$dir/lock-scratch")"
  exec 9>>"$dir/lock-scratch"
  IMAGE_GATE_RECORD="$dir/wrapper.json" IMAGE_GATE_CLAUDE="$REAL_CLAUDE" \
  MNEMON_VAULT_LOCK_FD=9 MNEMON_VAULT_LOCK_INODE="${id#*:}" \
    /bin/sh -c "$LAUNCH" image-gate-launch "$dir/go" \
      python3 "$ROOT/bin/capture-image.py" "$image" --input-name "$(basename "$image")" --languages "$langs" \
      --out "$dir/attempt/capture.md" --report "$dir/attempt/report.json" \
      --reader-model "$MODEL" --reader-effort "$EFFORT" --reader-max-usd "$MAX_USD" \
      --claude "$WRAP" --timeout "$TIMEOUT" </dev/null >"$dir/worker.stdout" 2>"$dir/worker.stderr" &
  CUR_SPID=$!
  exec 9>&-
  python3 "$HERE/lifecycle.py" watch --supervisor "$CUR_SPID" --record "$dir/wrapper.json" --scratch "$id" \
    --go "$dir/go" --max-seconds "$((TIMEOUT + 120))" --out "$dir/lifecycle.json" \
    >"$dir/lifecycle.txt" 2>"$dir/lifecycle.err" &
  CUR_WPID=$!
  WORKER_RC=0
  wait "$CUR_SPID" || WORKER_RC=$?
  wait "$CUR_WPID" || true
  CUR_SPID=""
  CUR_WPID=""
}

judge() {  # <seq> <item> <checks> [score.py judge options…]: prints the item's line
  local seq="$1" name="$2" checks="$3" rc=0
  shift 3
  python3 "$HERE/score.py" judge --seq "$seq" --name "$name" --dir "$RUN/$name" --rc "$WORKER_RC" \
    --checks "$checks" --out "$RUN/$name/item.json" "$@" || rc=$?
  if [ "$rc" -gt 1 ] || [ ! -f "$RUN/$name/item.json" ]; then
    mark "$seq" "$name" fail "the scoring helper failed (exit $rc)"
  fi
}

item() {  # <seq> <item> <image> <languages> <checks> [score.py judge options…]
  local seq="$1" name="$2" image="$3" langs="$4" checks="$5"
  shift 5
  run_worker "$name" "$image" "$langs"
  judge "$seq" "$name" "$checks" "$@"
}

# --- the instrument: the wrapper, started with the descriptor open, must list it ---------------------------
if [ "$ONLY" != "witness" ]; then
  dir="$RUN/lifecycle-instrument"
  mkdir -p "$dir"
  : > "$dir/lock-scratch"
  id="$(file_id "$dir/lock-scratch")"
  truebin="/usr/bin/true"
  [ -x "$truebin" ] || truebin="/bin/true"
  rc=0
  exec 9>>"$dir/lock-scratch"
  IMAGE_GATE_RECORD="$dir/wrapper.json" IMAGE_GATE_CLAUDE="$truebin" \
  MNEMON_VAULT_LOCK_FD=9 MNEMON_VAULT_LOCK_INODE="${id#*:}" \
    "$WRAP" -p </dev/null >/dev/null 2>"$dir/wrapper.stderr" || rc=$?
  exec 9>&-
  python3 "$HERE/lifecycle.py" control --record "$dir/wrapper.json" --scratch "$id" --rc "$rc" --seq 0 \
    --out "$dir/item.json" || true
  [ -f "$dir/item.json" ] || mark 0 lifecycle-instrument fail "the control run could not be judged"
fi

# --- the generated inputs: the columns and the rotated JPEG --------------------------------------------------
INPUTS="$RUN/inputs"
GEN_RC=0
if selected column-4200 || selected column-8000 || selected column-8001 || selected rotated; then
  mkdir -p "$INPUTS"
  python3 "$HERE/make_inputs.py" --out "$INPUTS" >"$INPUTS/manifest.jsonl" 2>"$INPUTS/make_inputs.err" || GEN_RC=$?
fi

T="$HERE/truth"
I="$HERE/img"
if selected post; then item 1 post "$I/post-en.png" eng agree,words,lifecycle --truth "$T/post-en.txt"; fi
if selected repeat; then
  item 2 repeat "$I/post-en.png" eng agree,words,lifecycle --truth "$T/post-en.txt" --same-as "$RUN/post/attempt/reading.txt"
fi
if selected page; then
  item 3 page "$I/paper-2col.png" eng agree,words,order,lifecycle --truth "$T/paper-2col.txt" --left 6-8 --right 9-11
fi
if selected quote; then item 4 quote "$I/quote-short.jpg" eng agree,lifecycle --truth "$T/quote-short.txt"; fi
if selected russian; then
  python3 "$ROOT/bin/capture-image.py" --probe --languages rus+eng >"$RUN/probe-rus+eng.json" 2>"$RUN/probe-rus+eng.err" || true
  if [ "$(python3 "$HERE/score.py" probe-missing --probe "$RUN/probe-rus+eng.json" --token lang:rus)" = "missing" ]; then
    mark 5 russian "not run" "language model rus missing"
  else
    item 5 russian "$I/cyrillic.png" rus+eng agree,lifecycle --truth "$T/cyrillic.txt"
  fi
fi
if selected ai; then item 6 ai "$I/post-ai.png" eng agree,ai,lifecycle --truth "$T/post-ai.txt"; fi
if selected dark-invert; then item 7 dark-invert "$I/post-en-dark-invert.png" eng agree,lifecycle --truth "$T/post-en.txt"; fi
if selected dark-theme; then item 8 dark-theme "$I/post-en-dark-theme.png" eng agree,lifecycle --truth "$T/post-en.txt"; fi
if selected block; then
  item 9 block "$I/post-en-block.png" eng agree,words,lifecycle --truth "$T/post-en.txt" --truth "$T/paper-2col.txt"
fi
gen_item() {  # <seq> <item> <file> <checks> [options…]
  local seq="$1" name="$2" file="$3" checks="$4"
  shift 4
  if [ "$GEN_RC" -ne 0 ] || [ ! -f "$INPUTS/$file" ]; then
    mark "$seq" "$name" fail "the generator did not write $file (exit $GEN_RC)"
  else
    item "$seq" "$name" "$INPUTS/$file" eng "$checks" "$@"
  fi
}
if selected column-4200; then gen_item 10 column-4200 column-4200.png agree,words,lifecycle --truth "$T/post-en.txt" --repeat 2; fi
if selected column-8000; then gen_item 11 column-8000 column-8000.png agree,words,lifecycle --truth "$T/post-en.txt" --repeat 4; fi
if selected column-8001; then gen_item 12 column-8001 column-8001.png too-large,no-call,lifecycle; fi
if selected rotated; then gen_item 13 rotated quote-exif6.jpg rotated,no-call,lifecycle; fi
if selected witness; then
  mkdir -p "$RUN/witness"
  python3 "$HERE/witness_repeat.py" --root "$ROOT" --image "$I/post-en.png" --languages eng --runs 5 --seq 14 \
    --out-dir "$RUN/witness" || true
  [ -f "$RUN/witness/item.json" ] || mark 14 witness fail "the witness helper failed"
fi

rc=0
python3 "$HERE/score.py" summary --run "$RUN" --effort "$EFFORT" --model "$MODEL" --max-usd "$MAX_USD" \
  --tessdata-prefix "$TP" || rc=$?
exit "$rc"
