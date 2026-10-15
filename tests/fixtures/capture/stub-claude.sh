#!/usr/bin/env bash
# Stub `claude` for the capture / fence / lock suites (spec §9 "Stubs"). Never talks to a model.
#
# Every call is recorded under $STUB_DIR as call-<n>.argv (one argument per line), call-<n>.cwd,
# call-<n>.stdin (the prompt bytes), call-<n>.sources (the Sources/*/source.md files that existed
# at call time — the "source before LLM" assertion) and call-<n>.staging (staging dirs seen);
# $STUB_DIR/count holds the number of calls. `claude --version` and `claude --help` answer like
# the real binary (the help lists --restricted).
#
# Behaviour (environment):
#   STUB_MODE         ok (default) · exit1 · is-error · missing-field · invalid-json · sleep ·
#                     bad-then-ok · max-turns · no-output
#   STUB_FIXTURE      structured_output JSON file for `ok` (and the second answer of bad-then-ok)
#   STUB_FIXTURE_BAD  structured_output JSON file for the first answer of bad-then-ok
#   STUB_SLEEP        seconds to sleep in `sleep` mode (a child `sleep` is spawned too, so the
#                     timeout test can check that the whole process group is reaped)
#   STUB_MARKER       a string put on the child sleep's command line (pgrep -f target)
#   STUB_FIXTURE_PART structured_output for a PART call of a chunked extraction (capture layer
#                     §3.8): used when the prompt on stdin carries the part template's marker
#                     line `<!-- mnemon:part-template -->`
#   STUB_FIXTURE_PART_BAD + STUB_PART_BAD_CALLS="n,m"  call numbers that answer the bad part
#                     fixture instead (the gateway's one validation re-run)
#   STUB_FAIL_CALLS   "n,m": call numbers that exit 1 (a failing part)
#   STUB_COST         total_cost_usd of every answer (default 0.012); STUB_COST_NULL=1 omits it
set -uo pipefail

case "${1:-}" in
  --version) echo "9.9.9-stub (Claude Code)"; exit 0 ;;
  --help)
    cat <<'HELP'
Usage: claude [options] [command] [prompt]
Options:
  -p, --print                  Print response and exit
  --restricted                 Restrict file tools to the working directory and disable shell tools
  --tools <tools...>           Tool names the model may use
  --permission-prompts <mode>  acceptEdits|none|...
  --strict-mcp-config          Only use MCP servers from --mcp-config
  --output-format <format>     text|json|stream-json
  --json-schema <schema>       JSON schema for structured output
HELP
    exit 0 ;;
esac

: "${STUB_DIR:?STUB_DIR must be set}"
mkdir -p "$STUB_DIR"
n=$(( $(cat "$STUB_DIR/count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_DIR/count"
printf '%s\n' "$@" > "$STUB_DIR/call-$n.argv"
pwd > "$STUB_DIR/call-$n.cwd"
cat > "$STUB_DIR/call-$n.stdin"
# §12 A1: the vault lock descriptor is closed and its variables scrubbed before claude runs
if [[ -z "${MNEMON_VAULT_LOCK_FD:-}${MNEMON_VAULT_LOCK_INODE:-}" ]]; then echo unset
elif [[ -e "/dev/fd/${MNEMON_VAULT_LOCK_FD:-999}" ]]; then echo open; else echo closed; fi > "$STUB_DIR/call-$n.lockfd"
if [[ -n "${STUB_VAULT:-}" ]]; then
  (shopt -s nullglob; for f in "$STUB_VAULT"/Sources/*/source.md; do echo "$f"; done) > "$STUB_DIR/call-$n.sources"
  (shopt -s nullglob; for d in "$STUB_VAULT"/Sources/.*.staging-*; do echo "$d"; done) > "$STUB_DIR/call-$n.staging"
fi

mode="${STUB_MODE:-ok}"
fixture="${STUB_FIXTURE:-}"
if [[ "$mode" == "bad-then-ok" ]]; then
  if [[ "$n" -eq 1 ]]; then fixture="${STUB_FIXTURE_BAD:?}"; fi
  mode=ok
fi
# a part call of a chunked extraction: the prompt carries the part template's marker line
if grep -qF -- '<!-- mnemon:part-template -->' "$STUB_DIR/call-$n.stdin"; then
  fixture="${STUB_FIXTURE_PART:-}"
  echo part > "$STUB_DIR/call-$n.kind"
  case ",${STUB_PART_BAD_CALLS:-}," in *",$n,"*) fixture="${STUB_FIXTURE_PART_BAD:?}" ;; esac
else
  echo main > "$STUB_DIR/call-$n.kind"
fi
case ",${STUB_FAIL_CALLS:-}," in *",$n,"*) echo "stub claude: simulated failure of call $n" >&2; exit 1 ;; esac

emit() {  # <structured_output file or empty> <extra jq filter>
  local so="$1" extra="${2:-.}" cost
  cost="${STUB_COST:-0.012}"
  [[ "${STUB_COST_NULL:-}" == "1" ]] && cost=null
  if [[ -n "$so" ]]; then
    jq -c --slurpfile so "$so" --argjson cost "$cost" '{type:"result", subtype:"success", is_error:false, total_cost_usd:$cost, duration_ms:1500, num_turns:1, permission_denials:[], structured_output:$so[0]} | '"$extra"
  else
    jq -n -c --argjson cost "$cost" '{type:"result", subtype:"success", is_error:false, total_cost_usd:$cost, duration_ms:1500, num_turns:1, permission_denials:[]} | '"$extra"
  fi
}

case "$mode" in
  ok)            emit "${fixture:?STUB_FIXTURE must be set for ok}" <<< '{}' ;;
  exit1)         echo "stub claude: simulated failure" >&2; exit 1 ;;
  is-error)      emit "" '. + {is_error:true, subtype:"error_during_execution", result:"simulated transient failure"}' <<< '{}' ;;
  max-turns)     emit "" '. + {is_error:true, subtype:"error_max_turns", result:"max turns"}' <<< '{}' ;;
  missing-field) jq -c 'del(.author)' "${fixture:?}" > "$STUB_DIR/so-$n.json"; emit "$STUB_DIR/so-$n.json" <<< '{}' ;;
  no-output)     emit "" <<< '{}' ;;
  invalid-json)  echo "this is not json" ;;
  sleep)
    sleep "${STUB_SLEEP:-60}" "${STUB_MARKER:-}" >/dev/null 2>&1 &
    sleep "${STUB_SLEEP:-60}"
    emit "${fixture:?}" <<< '{}' ;;
  *) echo "stub claude: unknown STUB_MODE $mode" >&2; exit 3 ;;
esac
