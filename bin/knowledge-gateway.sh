#!/usr/bin/env bash
# knowledge-gateway.sh — the capture engine behind `mnemon add` (docs/specs/2026-10-15-capture-contracts.md).
#
# source-add acquires the content itself (raw staged next to its destination, hashed, recorded,
# published), writes source.md and publishes the revision folder by one directory rename, runs the
# fenced extractor (claude -p --restricted --tools "" … with the prompt on stdin, structured output
# only) and publishes the validated extract.md. Receipts in the per-vault ledger
# (MNEMON_VAULT_STATE_DIR, bin/lib-capture.sh) make every step resumable; RESULT: lines and the exit
# code come from the files, never from model text. Every write happens under the vault lock
# (bin/vault-lock.py): the gateway re-execs itself through it when it was not started with the
# inherited descriptor (`mnemon add` passes it); --dry-run writes nothing and takes no lock.
set -euo pipefail

# Load config helper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MNEMON_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/mnemon-config.sh"

# Engine maintenance lock (mnemon release): exit 75 while the engine is being updated.
mnemon_engine_lock_guard

ORIG_ARGS=("$@")

usage() {
  cat <<'USAGE'
Usage: knowledge-gateway.sh <action> [options]

Actions:
  source-add    Capture a source and generate its extract
  status        Show pipeline status

Options:
  --origin <type>       url|text|youtube|audio|pdf|file|book|idea|ref:vault|batch
  --url <url>           Source URL (auto-detected: .pdf → pdf, youtube → youtube)
  --file <path>         Local file path (audio/pdf/file; auto-detected by extension)
  --file-text <path>    Text content from a file (batch internal; the bytes are the capture-text)
  --title <title>       Source title (optional, acquired from the page/PDF/media if not given)
  --author <author>     Source author (optional)
  --source-type <type>  article|video|podcast|book|paper|idea|conversation
  --context <ctx>       Capture context; must be declared in the instance charter (## Contexts);
                        default: personal when declared, else the first declared context
  --session <name>      Session title (optional)
  --intent <text>       Why this was captured (optional)
  --ref-path <path>     Vault note path (for origin=ref:vault)
  --capture-id <id>     Idempotent capture operation id from a feeder (--ref-id is an alias): a known
                        id replays or resumes from its receipt; an unknown id starts a fresh capture
  --resume <rev|id>     Finish an incomplete revision or capture (exit 2 when complete or missing)
  --recapture           New revision of an already captured resource (--force is an alias)
  --supersedes <rev>    The revision the new one supersedes (implies --recapture; must exist)
  --keep-run            Keep the attempt directory after a successful extraction
  --import-source <s>   Import source label (apple-notes, safari, telegram-saved, a producer id…)
  --import-ref <id>     The envelope id when the capture came through an inbox
  --import-batch <id>   Batch ID for grouping dump items
  --producer-fidelity <v>  The producer's fidelity claim (exact|embed|model-transcribed|url-only)
  --provenance-file <p> Path of the producer's provenance record (recorded in the receipt only)
  --manifest <path>     JSON manifest file (for origin=batch)
  --config <path>       Instance config file (outranks MNEMON_CONFIG/MNEMON_INSTANCE)
  --instance <name>     Mnemon instance to use (exports MNEMON_INSTANCE, drops an inherited
                        MNEMON_CONFIG; see bin/mnemon-config.sh). Not together with --config.
  --model <model>       Override default model
  --dry-run             Print what would happen, the extractor argv and the prompt; write nothing
  --whisper-model <m>   Whisper model (default from config)
  --no-whisper          Disable whisper fallback for YouTube
  --render              Force the rendered stage (headless Chrome); the raw is the rendered DOM HTML
  --no-render           Never render (implies --no-external: no rendering, local or remote)
  --no-external         Never fall back to an external fetcher (Firecrawl, Jina), whatever the instance allows
  --site <plan|auto>    Site capture of the --url landing from a plan (`mnemon site map`) or an
                        auto-generated one: the bundle of pages is the raw, the composite the text
  --depth auto|off|force  Depth-aware capture of thin landing pages (default auto; off for --site)
  --assume-cost <usd>   With --resume: reconcile every extractor call whose cost is unknown at this
                        cost (the budget rule of a chunked extraction)

Exit codes: 0 extracted / skipped-duplicate / replay · 1 failed or integrity-error · 2 usage or
contract (unknown context, ref:mcp, writer_host, capture-id reuse with a different request, an
invalid URL, a refused site plan) · 3 extractor or capture-layer environment missing before
publication · 4 source published, extract not · 75 lock held
USAGE
  exit 1
}

# --- Parse arguments ---

ACTION="${1:-}"
[[ -z "$ACTION" ]] && usage
shift

ORIGIN=""
URL=""
FILE_PATH=""
FILE_TEXT=""
TITLE=""
AUTHOR=""
SOURCE_TYPE=""
CONTEXT=""
CONTEXT_GIVEN=false
SESSION=""
INTENT=""
REF_PATH=""
REF_SOURCE=""
CAPTURE_ID=""
CAPTURE_ID_GIVEN=false
RESUME=""
RECAPTURE=false
SUPERSEDES=""
KEEP_RUN=false
MANIFEST=""
IMPORT_SOURCE=""
IMPORT_REF=""
IMPORT_BATCH=""
PRODUCER_FIDELITY=""
PROVENANCE_FILE=""
CONFIG_PATH=""
INSTANCE_FLAG=""
MODEL_FLAG=""
DRY_RUN=false
WHISPER_MODEL_FLAG=""
NO_WHISPER=false
RENDER=false
NO_RENDER=false
NO_EXTERNAL=false
SITE_MODE=""
SITE_PLAN_FILE=""
ASSUME_COST=""
DEPTH="auto"
BOUND_GIVEN=false

_need() { [[ $# -ge 2 ]] || { echo "ERROR: $1 requires a value" >&2; exit 2; }; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --origin)        _need "$@"; ORIGIN="$2"; BOUND_GIVEN=true; shift 2 ;;
    --url)           _need "$@"; URL="$2"; BOUND_GIVEN=true; shift 2 ;;
    --file)          _need "$@"; FILE_PATH="$2"; BOUND_GIVEN=true; shift 2 ;;
    --file-text)     _need "$@"; FILE_TEXT="$2"; BOUND_GIVEN=true; shift 2 ;;
    --title)         _need "$@"; TITLE="$2"; shift 2 ;;
    --author)        _need "$@"; AUTHOR="$2"; shift 2 ;;
    --source-type)   _need "$@"; SOURCE_TYPE="$2"; BOUND_GIVEN=true; shift 2 ;;
    --context)       _need "$@"; CONTEXT="$2"; CONTEXT_GIVEN=true; BOUND_GIVEN=true; shift 2 ;;
    --session)       _need "$@"; SESSION="$2"; shift 2 ;;
    --intent)        _need "$@"; INTENT="$2"; shift 2 ;;
    --ref-path)      _need "$@"; REF_PATH="$2"; BOUND_GIVEN=true; shift 2 ;;
    --ref-source)    _need "$@"; REF_SOURCE="$2"; shift 2 ;;
    --ref-id|--capture-id) _need "$@"; CAPTURE_ID="$2"; CAPTURE_ID_GIVEN=true; shift 2 ;;
    --resume)        _need "$@"; RESUME="$2"; shift 2 ;;
    --recapture|--force) RECAPTURE=true; BOUND_GIVEN=true; shift ;;
    --supersedes)    _need "$@"; SUPERSEDES="$2"; RECAPTURE=true; BOUND_GIVEN=true; shift 2 ;;
    --keep-run)      KEEP_RUN=true; shift ;;
    --manifest)      _need "$@"; MANIFEST="$2"; shift 2 ;;
    --import-source) _need "$@"; IMPORT_SOURCE="$2"; shift 2 ;;
    --import-ref)    _need "$@"; IMPORT_REF="$2"; shift 2 ;;
    --import-batch)  _need "$@"; IMPORT_BATCH="$2"; shift 2 ;;
    --producer-fidelity) _need "$@"; PRODUCER_FIDELITY="$2"; shift 2 ;;
    --provenance-file)   _need "$@"; PROVENANCE_FILE="$2"; shift 2 ;;
    --config)        _need "$@"; CONFIG_PATH="$2"; shift 2 ;;
    --instance)      _need "$@"; INSTANCE_FLAG="$2"; shift 2 ;;
    --model)         _need "$@"; MODEL_FLAG="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=true; shift ;;
    --whisper-model) _need "$@"; WHISPER_MODEL_FLAG="$2"; shift 2 ;;
    --no-whisper)    NO_WHISPER=true; shift ;;
    --render)        RENDER=true; BOUND_GIVEN=true; shift ;;
    --no-render)     NO_RENDER=true; BOUND_GIVEN=true; shift ;;
    --no-external)   NO_EXTERNAL=true; BOUND_GIVEN=true; shift ;;
    --site)          _need "$@"; if [[ "$2" == "auto" ]]; then SITE_MODE=auto; else SITE_MODE=file; SITE_PLAN_FILE="$2"; fi; BOUND_GIVEN=true; shift 2 ;;
    --assume-cost)   _need "$@"; ASSUME_COST="$2"; shift 2 ;;
    --depth)         _need "$@"; DEPTH="$2"; BOUND_GIVEN=true; shift 2 ;;
    -h|--help)       usage ;;
    *)               echo "Unknown option: $1" >&2; usage ;;
  esac
done

case "$DEPTH" in auto|off|force) ;; *) echo "ERROR: --depth takes auto|off|force" >&2; exit 2 ;; esac
if $RENDER && $NO_RENDER; then echo "ERROR: --render and --no-render are exclusive" >&2; exit 2; fi
if [[ -n "$ASSUME_COST" ]]; then
  [[ "$ASSUME_COST" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "ERROR: --assume-cost takes a non-negative number of dollars" >&2; exit 2; }
  [[ -n "$RESUME" ]] || { echo "ERROR: --assume-cost only goes with --resume <revision|capture_id>" >&2; exit 2; }
fi
# a site capture is an acquisition mode of the landing resource (D9): no depth fold
[[ -n "$SITE_MODE" ]] && DEPTH=off

# Selectors: --config (explicit path) outranks --instance outranks the inherited
# MNEMON_CONFIG / MNEMON_INSTANCE pair; both flags on one command line is an error.
if [[ -n "$CONFIG_PATH" && -n "$INSTANCE_FLAG" ]]; then
  echo "ERROR: conflicting selectors: --config and --instance on one command line (use one)" >&2
  exit 2
fi
if [[ -n "$INSTANCE_FLAG" ]]; then
  export MNEMON_INSTANCE="$INSTANCE_FLAG" MNEMON_SELECTED_BY="--instance"
  unset MNEMON_CONFIG MNEMON_CONFIG_SOURCE
fi
# Load config (uses CONFIG_PATH if set, otherwise resolves from the environment;
# a loader refusal — conflict, missing instance — ends the script with its status)
load_config "$CONFIG_PATH"

# Flags override config values
MODEL="${MODEL_FLAG:-$DEFAULT_MODEL}"
[[ -n "$WHISPER_MODEL_FLAG" ]] && WHISPER_MODEL="$WHISPER_MODEL_FLAG"

# --- Libraries: L1 archive helpers (RENDER_URL_CMD, backfill) and the capture contract ---
source "$SCRIPT_DIR/lib-archive.sh"
source "$SCRIPT_DIR/lib-capture.sh"

# --- Auto-detect origin ---
# A URL's kind comes from the capture layer's canonicalization (`capture-fetch.py --canonical-only`,
# no network; §12 A12): youtube (watch, youtu.be, shorts, embed, live, music, m.) → the media path;
# a .pdf URL → pdf; everything else (page, arxiv, github) → url. An explicit --origin wins.

if [[ -z "$ORIGIN" && "$AUTO_DETECT_ORIGIN" == "true" ]]; then
  if [[ -n "$MANIFEST" ]]; then
    ORIGIN="batch"
  elif [[ -n "$REF_PATH" ]]; then
    ORIGIN="ref:vault"
  elif [[ -n "$REF_SOURCE" ]]; then
    ORIGIN="ref:mcp"
  elif [[ -n "$FILE_TEXT" ]]; then
    ORIGIN="text"
  elif [[ -n "$URL" ]]; then
    cap_canonical_lookup "$URL"
    if [[ "$URL_KIND" == "youtube" ]]; then
      ORIGIN="youtube"
    elif [[ "$URL" =~ \.pdf(\?|#|$) ]]; then
      ORIGIN="pdf"
    else
      ORIGIN="url"
    fi
  elif [[ -n "$FILE_PATH" ]]; then
    if [[ "$FILE_PATH" =~ \.pdf$ ]]; then
      ORIGIN="pdf"
    elif command -v ffprobe >/dev/null 2>&1 && \
       ffprobe -v quiet -show_entries format=format_name -of csv=p=0 "$FILE_PATH" 2>/dev/null | \
       grep -qiE 'mp3|wav|flac|ogg|aac|m4a|opus'; then
      ORIGIN="audio"
    else
      ORIGIN="file"
    fi
  fi
fi
[[ -n "$FILE_TEXT" && -z "$ORIGIN" ]] && ORIGIN="text"

# --- Auto-detect source_type from origin ---

if [[ -z "$SOURCE_TYPE" ]]; then
  case "${ORIGIN:-}" in
    url)        SOURCE_TYPE="article"
                [[ -n "$URL" ]] && { cap_canonical_lookup "$URL"; [[ "$URL_KIND" == "arxiv" ]] && SOURCE_TYPE="paper"; }
                [[ -n "$SITE_MODE" ]] && SOURCE_TYPE="website" ;;
    youtube)    SOURCE_TYPE="video" ;;
    audio)      SOURCE_TYPE="podcast" ;;
    pdf)        SOURCE_TYPE="paper" ;;
    book)       SOURCE_TYPE="book" ;;
    idea)       SOURCE_TYPE="idea" ;;
    text)       SOURCE_TYPE="article" ;;
    ref:vault)  SOURCE_TYPE="document" ;;
    ref:mcp)    SOURCE_TYPE="document" ;;
    batch)      SOURCE_TYPE="" ;;  # determined per-item
    *)          SOURCE_TYPE="article" ;;
  esac
fi

# ---------------------------------------------------------------------------------------------
# Contract checks that need no lock (exit 2 before any write)
# ---------------------------------------------------------------------------------------------
cap_contract_checks() {
  if [[ "$ORIGIN" == "ref:mcp" ]]; then
    cap_die2 "ref:mcp is not supported in phase 1 (D11): there is no acquisition step for MCP content. Capture it through an inbox producer (mnemon ingest) or paste it as --origin text."
  fi
  # the routing contract of §12 A12: an explicit --origin youtube needs a YouTube URL
  if [[ "$ORIGIN" == "youtube" && -n "$URL" ]]; then
    cap_canonical_lookup "$URL"
    [[ "$URL_KIND" == "youtube" ]] || cap_die2 "--origin youtube needs a YouTube URL (watch, youtu.be, shorts, embed, live); $URL is a $URL_KIND"
  fi
  if [[ -n "$SITE_MODE" ]]; then
    [[ -n "$URL" && "$ORIGIN" == "url" ]] || cap_die2 "--site needs a --url landing (a site capture is an acquisition mode of the landing page)"
    if [[ "$SITE_MODE" == "file" ]]; then
      [[ -f "$SITE_PLAN_FILE" ]] || cap_input_missing_exit "site plan not found: $SITE_PLAN_FILE"
      cap_canonical_lookup "$URL"
      # a plan whose landing is not --url, that exceeds the cap or includes a robots-disallowed page is refused (§3.6)
      cap_py site-plan-check "$SITE_PLAN_FILE" "$CANONICAL_URL" "${SITE_MAX_PAGES:-40}" "$CAP_FETCH_CMD" >/dev/null || exit 2
    fi
  fi
  if [[ -n "${WRITER_HOST:-}" && "$WRITER_HOST" != "$(cap_host)" ]]; then
    cap_die2 "this host ($(cap_host)) must not write vault $VAULT_PATH (writer_host $WRITER_HOST)"
  fi
  if $CONTEXT_GIVEN; then
    cap_validate_context "$CONTEXT"
  else
    CONTEXT="$(cap_default_context)"
  fi
  if $CAPTURE_ID_GIVEN; then cap_validate_capture_id "$CAPTURE_ID"; fi
  if [[ -n "$SUPERSEDES" && ! -d "$VAULT_PATH/Sources/$SUPERSEDES" ]]; then
    cap_die2 "--supersedes names a revision that does not exist: Sources/$SUPERSEDES/"
  fi
  if [[ -n "$RESUME" && $CAPTURE_ID_GIVEN == true ]]; then
    cap_die2 "--resume and --capture-id are exclusive (a replay is --capture-id alone)"
  fi
  if [[ "$ORIGIN" == "ref:vault" && ! -f "$REF_PATH" ]]; then
    cap_input_missing_exit "ref:vault file not found: $REF_PATH"
  fi
  if [[ -n "$FILE_PATH" && "$ORIGIN" != "batch" && ! -f "$FILE_PATH" ]]; then
    cap_input_missing_exit "file not found: $FILE_PATH"
  fi
}

# A missing --file / --ref-path at invocation: no lock, no receipt — but every exit 1 carries
# RESULT lines (§3.6), so a feeder records failed/input-missing (permanent), never malformed-result.
cap_input_missing_exit() {
  echo "ERROR: $1" >&2
  REVISION=""
  cap_result failed none input-missing false false
  exit 1
}

# ---------------------------------------------------------------------------------------------
# Dry run (§3.4): no lock, no receipt, no attempt dir — the plan, the extractor argv, the prompt
# ---------------------------------------------------------------------------------------------
cap_dry_run() {
  local kind key label raw_ext="" raw_note="none (raw == capture-text)" tmp content="" template reader schema="$CAP_REQUEST_SCHEMA" bytes
  kind="$(cap_resource_kind "$ORIGIN")"
  case "$kind" in
    url) key="$URL" ;;
    ref) key="$REF_PATH" ;;
    file) key="sha256 of $FILE_PATH" ;;
    text) if [[ -n "$FILE_TEXT" ]]; then key="sha256 of $FILE_TEXT"; else key="sha256 of the stdin bytes"; fi ;;
  esac
  if [[ "$kind" == "url" && -n "$URL" ]]; then
    cap_canonical_lookup "$URL"
    key="$CANONICAL_URL (canonical v${CANONICAL_VERSION:-1} of $URL)"
  fi
  case "$ORIGIN" in
    url)     if [[ -n "$SITE_MODE" ]]; then raw_ext="site/"; else raw_ext=html; fi ;;
    pdf)     raw_ext=pdf ;;
    youtube) raw_ext=txt ;;
    audio)   raw_ext="<audio ext>" ;;
    file|book) raw_ext="$(cap_ext_of "$FILE_PATH" bin)" ;;
  esac
  if [[ -n "$raw_ext" ]]; then
    if [[ -n "${ARCHIVE_DIR:-}" ]]; then raw_note="archive — $ARCHIVE_DIR/$(cap_today)_<resource_id>.$raw_ext"
    else raw_note="state — $MNEMON_VAULT_STATE_DIR/raw/$(cap_today)_<resource_id>.$raw_ext"; fi
  fi
  echo "=== DRY RUN ==="
  echo "Vault: $VAULT_PATH"
  echo "Model: $MODEL"
  echo "Template: templates/core/${SOURCE_TYPE}.md"
  echo "Origin: $ORIGIN"
  echo "Source type: $SOURCE_TYPE"
  echo "Context: $CONTEXT"
  if $CAPTURE_ID_GIVEN; then echo "Capture id: $CAPTURE_ID"; else echo "Capture id: (generated at run time)"; fi
  echo "Resource: $kind — $key"
  if [[ "$kind" == "url" && -n "$URL" ]]; then
    echo "Canonical URL: $CANONICAL_URL"
    echo "URL kind: $URL_KIND${REQUESTED_VERSION:+ (requested version $REQUESTED_VERSION)}"
  fi
  echo "Request schema: $schema"
  echo "Revision: $(cap_today)_<resource_id> (allocated under the vault lock at run time)"
  [[ -n "$FILE_PATH" ]] && echo "Origin path: $FILE_PATH"
  echo "Raw store: $raw_note"
  $RECAPTURE && echo "Recapture: yes (supersedes ${SUPERSEDES:-the latest revision})"
  if [[ "$SITE_MODE" == "file" ]]; then
    cap_py site-plan-summary "$SITE_PLAN_FILE"
  elif [[ "$SITE_MODE" == "auto" ]]; then
    echo "Site plan: generated at run time (capture-site.py map, at most ${SITE_MAX_PAGES:-40} pages); kept as attempt-1/plan.json"
  fi
  if [[ "$kind" == "url" ]]; then
    echo "Fetch: render $( $RENDER && echo force || { $NO_RENDER && echo off || echo auto; } ), external $( { [[ "${EXTERNAL_FETCH:-off}" == auto ]] && ! $NO_EXTERNAL && ! $NO_RENDER; } && echo auto || echo off ), acquire timeout ${ACQUIRE_TIMEOUT:-300}s"
  fi
  echo "Vault state: $MNEMON_VAULT_STATE_DIR"
  echo "=== CHUNK PLAN ==="
  content=""
  [[ "$ORIGIN" == "ref:vault" && -f "$REF_PATH" ]] && content="$REF_PATH"
  [[ -n "$FILE_TEXT" && -f "$FILE_TEXT" ]] && content="$FILE_TEXT"
  if [[ -n "$content" ]]; then
    bytes="$(cap_bytes "$content")"
    if [[ "$bytes" -gt "${EXTRACT_INLINE_MAX_BYTES:-400000}" ]]; then
      CAP_DRY_SPLIT="$(mktemp -d -t mnemon-dry-split.XXXXXX)"
      if python3 "$SCRIPT_DIR/extract-publish.py" split --in "$content" --max-bytes "${EXTRACT_INLINE_MAX_BYTES:-400000}" --max-parts "${EXTRACT_MAX_PARTS:-8}" --out-dir "$CAP_DRY_SPLIT" > "$CAP_DRY_SPLIT/split.txt" 2>&1; then
        echo "chunked: $bytes bytes > extract_inline_max_bytes=${EXTRACT_INLINE_MAX_BYTES:-400000} → $(jq 'length' "$CAP_DRY_SPLIT/parts.json") part(s) + 1 merge call"
        jq -r '.[] | "  part \(.n): \(.bytes) bytes — \(.first_line)"' "$CAP_DRY_SPLIT/parts.json"
      else
        echo "unsupported-size: $(head -1 "$CAP_DRY_SPLIT/split.txt")"
      fi
      rm -rf "$CAP_DRY_SPLIT"
    else
      echo "single call: $bytes bytes ≤ extract_inline_max_bytes=${EXTRACT_INLINE_MAX_BYTES:-400000}"
    fi
  elif [[ "$kind" == "text" ]]; then
    echo "decided at run time from the stdin bytes (inline limit ${EXTRACT_INLINE_MAX_BYTES:-400000} bytes, at most ${EXTRACT_MAX_PARTS:-8} parts)"
  else
    echo "chunking decided after acquisition (inline limit ${EXTRACT_INLINE_MAX_BYTES:-400000} bytes, at most ${EXTRACT_MAX_PARTS:-8} parts)"
  fi
  echo "=== EXTRACTOR ARGV ==="
  local argv='claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config --no-session-persistence --output-format json --json-schema <extract/1> --max-turns 4 --model '"$MODEL"
  [[ -n "${EXTRACT_MAX_USD:-}" ]] && argv="$argv --max-budget-usd <extract_max_usd − spent, per call>"
  echo "$argv"
  echo "(working directory: <vault state>/runs/<capture_id>/attempt-1; prompt on stdin; timeout ${EXTRACT_TIMEOUT:-900}s; inline limit ${EXTRACT_INLINE_MAX_BYTES:-400000} bytes)"
  echo "=== PROMPT ==="
  CAP_DRY_TMP="$(mktemp -d -t mnemon-dry.XXXXXX)"
  trap 'rm -rf "${CAP_DRY_TMP:-}"' EXIT
  tmp="$CAP_DRY_TMP"
  ATTEMPT_DIR="$tmp"
  CONTENT_FORMAT="text"
  case "$ORIGIN" in youtube|audio) CONTENT_FORMAT=transcript ;; pdf) CONTENT_FORMAT=pdf ;; ref:vault) CONTENT_FORMAT=reference ;; esac
  template="$(cap_template_file "$SOURCE_TYPE")"
  reader="$(cap_reader_file)"
  cap_write_prompt "$content" "$template" "$reader" "$tmp/prompt.md"
  cat "$tmp/prompt.md"
  return 0
}

# ---------------------------------------------------------------------------------------------
# Replay / recovery for a known capture_id (§3.4 recovery table, binding rules of §3.1)
# ---------------------------------------------------------------------------------------------
cap_restore_from_receipt() {   # globals from the receipt's request + metadata (one jq, @sh-quoted)
  local r assignments
  r="$(cap_receipt_path "$CAPTURE_ID")"
  assignments="$(jq -r '
    def s(v): ((if v == null then "" else v end) | tostring | @sh);
    "ORIGIN=" + s(.request.origin), "URL=" + s(.request.url), "REF_PATH=" + s(.request.ref_path),
    "CONTEXT=" + s(.request.context), "SOURCE_TYPE=" + s(.request.source_type // "article"),
    "RECAPTURE=" + s(.request.recapture), "SUPERSEDES=" + s(.request.supersedes), "RENDER=" + s(.request.render),
    "DEPTH=" + s(.request.depth), "RESOURCE_KIND=" + s(.request.resource_kind), "RESOURCE_KEY=" + s(.request.resource_key),
    "INPUT_SHA256=" + s(.request.input_sha256), "RESOURCE_SHA256=" + s(.resource_sha256), "RESOURCE_ID=" + s(.resource_id),
    "REQUEST_JSON=" + s(.request | tojson), "REQUEST_SHA256=" + s(.request_sha256), "INPUT_PATH=" + s(.input_path),
    "TITLE=" + s(.metadata.title), "AUTHOR=" + s(.metadata.author), "INTENT=" + s(.metadata.intent),
    "SESSION=" + s(.metadata.session), "IMPORT_SOURCE=" + s(.metadata.import_source), "IMPORT_REF=" + s(.metadata.import_ref),
    "IMPORT_BATCH=" + s(.metadata.import_batch), "PRODUCER_FIDELITY=" + s(.metadata.producer_fidelity),
    "PROVENANCE_FILE=" + s(.metadata.provenance_file), "REVISION=" + s(.revision), "RAW_STORE=" + s(.raw_store // "none"),
    "RAW_PATH=" + s(.raw_path), "RAW_STAGED=" + s(.raw_staged_path), "RAW_KIND=" + s(.raw_kind // "none"),
    "RAW_SHA256=" + s(.raw_sha256), "RAW_BYTES=" + s(.raw_bytes), "RAW_PUBLISHED=" + s(.raw_published),
    "TEXT_SHA256=" + s(.text_sha256), "TEXT_BYTES=" + s(.text_bytes), "RECEIPT_STATE=" + s(.state),
    "RECEIPT_CLASS=" + s(.error_class), "RECEIPT_ATTEMPTS=" + s(.attempts // 0),
    "RESOLVED_REV=" + s(.resolved_to_revision), "RESOLVED_CID=" + s(.resolved_to_capture_id),
    "LEGACY_MATCH=" + s(.legacy_match // false),
    "REQUEST_SCHEMA=" + s(.request.schema // "mnemon-request/1"), "CANONICAL_URL=" + s(.canonical_url), "CANONICAL_VERSION=" + s(.canonical_version),
    "REQ_RENDER=" + s(.request.render), "REQ_EXTERNAL=" + s(.request.external), "REQ_SITE=" + s(.request.site),
    "REQUESTED_VERSION=" + s(.request.requested_version), "SITE_PLAN_SHA256=" + s(.site_plan_sha256), "REPORT_STATE=" + s(.report_state)' "$r")"
  eval "$assignments"
  FILE_PATH=""
  if [[ "$RESOURCE_KIND" == "file" ]]; then FILE_PATH="$INPUT_PATH"; fi
  if [[ "$RESOURCE_KIND" == "text" && -n "$INPUT_PATH" && "$INPUT_PATH" != "$RUNS_DIR/$CAPTURE_ID/attempt-1/capture.md" ]]; then FILE_TEXT="$INPUT_PATH"; fi
  # identity and the request schema come from the receipt, never recomputed (D5); the bound
  # flags of a /2 request are restored as flags so a re-acquisition runs the same chain
  CAP_BIND_SCHEMA="$REQUEST_SCHEMA"
  NO_RENDER=false; NO_EXTERNAL=false; SITE_MODE=""; SITE_PLAN_FILE=""; URL_KIND=page
  if [[ "$REQUEST_SCHEMA" == "$CAP_REQUEST_SCHEMA_V1" ]]; then
    case "$REQ_RENDER" in true) RENDER=true ;; *) RENDER=false ;; esac
    CANONICAL_URL=""; CANONICAL_VERSION=""
  else
    case "$REQ_RENDER" in force) RENDER=true ;; off) RENDER=false; NO_RENDER=true ;; *) RENDER=false ;; esac
    [[ "$REQ_EXTERNAL" == "off" ]] && NO_EXTERNAL=true
    case "$REQ_SITE" in "") ;; auto) SITE_MODE=auto ;; *) SITE_MODE=file; SITE_PLAN_FILE="$INPUT_PATH" ;; esac
    if [[ "$RESOURCE_KIND" == "url" && -n "$URL" ]]; then
      # the kind routes the acquisition; the identity stays the receipt's whatever the lookup says
      local keep_c="$CANONICAL_URL" keep_v="$CANONICAL_VERSION" keep_rv="$REQUESTED_VERSION"
      cap_canonical_lookup "$URL" 2>/dev/null || true
      CANONICAL_URL="$keep_c"; CANONICAL_VERSION="$keep_v"; REQUESTED_VERSION="$keep_rv"
    fi
  fi
  [[ "$ORIGIN" == "youtube" ]] && URL_KIND=youtube
  KEEP_LOCATOR=false
  [[ "$RESOURCE_KIND" == "text" && -z "$FILE_TEXT" ]] && KEEP_LOCATOR=true
  [[ "$SITE_MODE" == "auto" ]] && KEEP_LOCATOR=true
  return 0
}

# The binding check: other bound arguments on the command line must describe the same request,
# compared under the receipt's request schema (§12 A4): a /1 receipt binds the literal URL and
# knows no render/external/site fields — those flags mismatch only when they are not their
# defaults; a /2 receipt binds the canonical identity and the new fields.
cap_binding_check() {
  $BOUND_GIVEN || return 0
  local want have_json have_sha tmp schema
  want="$(cap_receipt_get "$CAPTURE_ID" '.request_sha256')"
  schema="$(cap_receipt_get "$CAPTURE_ID" '.request.schema // "mnemon-request/1"')"
  CAP_BIND_SCHEMA="$schema"
  if [[ "$schema" == "$CAP_REQUEST_SCHEMA_V1" ]]; then
    if $NO_RENDER || $NO_EXTERNAL || [[ -n "$SITE_MODE" ]]; then
      cap_die2 "capture-id reuse with a different request: '$CAPTURE_ID' is a phase-1 request (mnemon-request/1) that binds no --no-render / --no-external / --site"
    fi
  fi
  # Compute the current request without touching the ledger.
  if [[ -z "$ORIGIN" ]]; then cap_die2 "capture-id reuse with a different request: no origin"; fi
  if $CONTEXT_GIVEN; then cap_validate_context "$CONTEXT"; else CONTEXT="$(cap_default_context)"; fi
  if [[ "$(cap_resource_kind "$ORIGIN")" == "text" ]]; then
    tmp="$(mktemp -t mnemon-bind.XXXXXX)"
    if [[ -n "$FILE_TEXT" ]]; then cat "$FILE_TEXT" > "$tmp" 2>/dev/null || true; else cat > "$tmp"; fi
    TEXT_INPUT="$tmp"
  fi
  cap_resource_identity
  # --recapture resolves `supersedes` to the latest revision at capture time; the receipt wins.
  if $RECAPTURE && [[ -z "$SUPERSEDES" ]]; then SUPERSEDES="$(cap_receipt_get "$CAPTURE_ID" '.request.supersedes // ""')"; fi
  cap_canonical_request
  [[ -n "${tmp:-}" ]] && rm -f "$tmp"
  CAP_BIND_SCHEMA=""
  if [[ "$REQUEST_SHA256" != "$want" ]]; then
    cap_die2 "capture-id reuse with a different request: '$CAPTURE_ID' is bound to $(cap_receipt_get "$CAPTURE_ID" '.request | "\(.origin) \(.resource_kind) \(.resource_key)"') (context $(cap_receipt_get "$CAPTURE_ID" '.request.context'), schema $schema)"
  fi
  return 0
}

# Resolve the input for a re-acquisition (started / failed receipts): the locator must still hold
# the recorded bytes.
cap_restore_input() {
  case "$RESOURCE_KIND" in
    text)
      [[ -n "$INPUT_PATH" && -f "$INPUT_PATH" ]] || cap_fail_exit input-missing "the input locator ${INPUT_PATH:-?} is gone; the capture cannot be replayed"
      [[ "$(cap_sha256 "$INPUT_PATH")" == "$INPUT_SHA256" ]] || cap_fail_exit input-missing "the input at $INPUT_PATH no longer matches the recorded hash"
      TEXT_INPUT="$INPUT_PATH" ;;
    file)
      [[ -n "$INPUT_PATH" && -f "$INPUT_PATH" ]] || cap_fail_exit input-missing "the input file ${INPUT_PATH:-?} is gone; the capture cannot be replayed"
      [[ "$(cap_sha256 "$INPUT_PATH")" == "$INPUT_SHA256" ]] || cap_fail_exit input-missing "the input file $INPUT_PATH no longer matches the recorded hash"
      FILE_PATH="$INPUT_PATH" ;;
    ref)
      [[ -f "$REF_PATH" ]] || cap_fail_exit input-missing "the referenced note $REF_PATH is gone" ;;
  esac
  return 0
}

# Reconcile extract.md that was published before the receipt caught up.
cap_reconcile_extracted() {   # <keep_locator>
  EXTRACT_SHA256="$(cap_sha256 "$VAULT_PATH/Sources/$REVISION/extract.md")"
  cap_finish_extracted true "$1"
}

cap_extract_belongs() {   # extract.md of REVISION carries this capture_id and revision
  local f="$VAULT_PATH/Sources/$REVISION/extract.md"
  [[ -f "$f" ]] || return 1
  [[ "$(_mnemon_yaml_literal capture_id "$f")" == "$CAPTURE_ID" && "$(_mnemon_yaml_literal revision "$f")" == "$REVISION" ]]
}

cap_replay() {
  REPLAYED=true
  cap_binding_check
  cap_restore_from_receipt
  cap_receipt_update "$CAPTURE_ID" '.replayed=true'

  # --- alias receipts: the fixed target revision; publication recovery is delegated (§12 A4) ---
  if [[ -n "$RESOLVED_REV" ]]; then cap_alias_replay; fi

  case "$RECEIPT_STATE" in
    extracted|integrity-error)
      # (an integrity-error receipt is re-verified: when the operator restored the file the
      # capture goes on from the files as they are, §12 A3)
      cap_verify_source "$REVISION" "$CAPTURE_ID" "$TEXT_SHA256" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
      cap_sidecar_recover
      if [[ ! -f "$VAULT_PATH/Sources/$REVISION/extract.md" ]]; then
        cap_run_extract_step true "$KEEP_LOCATOR"
      fi
      if [[ "$RECEIPT_STATE" == "integrity-error" ]]; then
        cap_receipt_update "$CAPTURE_ID" '.state="extracted" | .error_class=null | .error=null | .retryable=null'
      fi
      # hash equal or not: the RESULT comes from the files as they are
      cap_result extracted ok "" "" true
      exit 0 ;;
    source-published|extract-failed)
      cap_verify_source "$REVISION" "$CAPTURE_ID" "$TEXT_SHA256" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
      cap_sidecar_recover
      if cap_extract_belongs; then cap_reconcile_extracted "$KEEP_LOCATOR"; fi
      cap_run_extract_step true "$KEEP_LOCATOR" ;;
    started|failed)
      if [[ -f "$VAULT_PATH/Sources/$REVISION/source.md" ]]; then
        # crash after the directory rename, before the receipt: a source.md at the reserved name
        # is verified (§12 A3 — verification applies once a source.md with this id exists)
        cap_verify_source "$REVISION" "$CAPTURE_ID" "$TEXT_SHA256" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
        cap_receipt_update "$CAPTURE_ID" '.state="source-published" | .source_published=true | .error_class=null | .error=null | .retryable=null'
        cap_sidecar_recover
        if cap_extract_belongs; then cap_reconcile_extracted "$KEEP_LOCATOR"; fi
        cap_run_extract_step true "$KEEP_LOCATOR"
      fi
      case "$RECEIPT_CLASS" in
        archive-exists|input-missing|evidence-missing|evidence-mismatch)
          # operator classes: exit 1 again with the same class (§3.4; raw evidence rows of §12 A3)
          REVISION=""
          echo "ERROR: $(cap_receipt_get "$CAPTURE_ID" '.error // ""')" >&2
          cap_result failed none "$RECEIPT_CLASS" false true
          exit 1 ;;
      esac
      cap_recover_acquire ;;
    *) cap_die2 "receipt $CAPTURE_ID has an unknown state '$RECEIPT_STATE'" ;;
  esac
}

# An alias names a fixed target revision (§3.1). When the owner receipt (the operation that
# published the revision) is in the ledger, the publication recovery is delegated to it: the rows
# run with CAPTURE_ID = the owner and update the owner's receipt, extract.md carries the owner's
# id, the alias (ALIAS_ID) records only its own attempt accounting and reports the requested id
# (§12 A4). Without an owner receipt the alias runs the rows itself. Verification compares
# source.md with the OWNER id. Expects the alias's receipt restored into the globals.
cap_alias_replay() {
  local alias="$CAPTURE_ID" owner="$RESOLVED_CID" target="$RESOLVED_REV"
  local alias_state="$RECEIPT_STATE" alias_attempts="$RECEIPT_ATTEMPTS" keep=false
  ALIAS_ID=""
  REVISION="$target"
  if [[ -n "$owner" ]] && cap_receipt_exists "$owner" && [[ "$(cap_receipt_get "$owner" '.resolved_to_revision // ""')" == "" ]]; then
    ALIAS_ID="$alias"
    CAPTURE_ID="$owner"
    cap_restore_from_receipt        # the owner's request, locator, hashes and state
    REVISION="$target"
    keep="$KEEP_LOCATOR"
    cap_receipt_update "$CAPTURE_ID" '.replayed=true'
  fi
  if [[ "$LEGACY_MATCH" == true ]]; then
    # a legacy / alias match carries no owner id to compare with: the folder's own hash decides
    cap_verify_folder_source "$REVISION" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
  else
    cap_verify_source "$REVISION" "$owner" "$TEXT_SHA256" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
  fi
  if [[ -n "$ALIAS_ID" && ( "$RECEIPT_STATE" == "started" || "$RECEIPT_STATE" == "failed" ) ]]; then
    # the owner's receipt lagged behind its published folder (crash after the rename)
    cap_receipt_update "$CAPTURE_ID" '.state="source-published" | .source_published=true | .error_class=null | .error=null | .retryable=null'
  fi
  if [[ "$alias_state" == "extracted" && "$alias_attempts" -eq 0 ]]; then
    # a duplicate alias: the RESULT from the files of its target, nothing written
    cap_result skipped-duplicate "$(cap_extract_state_of "$REVISION")" "" "" true
    exit 0
  fi
  if [[ -f "$VAULT_PATH/Sources/$REVISION/extract.md" ]]; then
    if [[ "$alias_state" == "extracted" ]]; then cap_result extracted ok "" "" true; exit 0; fi
    if cap_extract_belongs || [[ "$LEGACY_MATCH" == true ]]; then cap_reconcile_extracted "$keep"; fi
    cap_result extracted ok "" "" true     # an extract that belongs to another operation: the files decide
    exit 0
  fi
  cap_run_extract_step true "$keep"
}

# started / failed without a published source: finish or redo the acquisition with the same
# reserved names, then publish and extract. The retained inputs win over the locator (§12 A4):
# the original --file is consulted only when nothing is retained, and recorded raw evidence is
# never refetched (§12 A3).
cap_recover_acquire() {
  local staged_match=false
  cap_new_attempt "$CAPTURE_ID"
  cap_receipt_update "$CAPTURE_ID" '.state="started" | .error_class=null | .error=null | .retryable=null'
  if ! cap_preflight_extractor; then cap_fail_exit "$PREFLIGHT_CLASS" "$PREFLIGHT_MSG"; fi
  if [[ "$RAW_STORE" != "none" && -n "$RAW_PATH" ]]; then
    RAW_DIR="$(dirname "$RAW_PATH")"
    if [[ "$RAW_PUBLISHED" == "true" ]]; then
      # a published raw must still be there, unchanged — it is the evidence, never refetched; a
      # published bundle is verified member by member (§12 A5: evidence-mismatch for any change)
      if [[ -n "$RAW_SHA256" && -e "$RAW_PATH" ]] && cap_raw_matches "$RAW_PATH" "$RAW_SHA256"; then
        cap_reconvert || cap_fail_exit "$FAIL_CLASS" "$FAIL_MSG"
      elif [[ -e "$RAW_PATH" ]]; then
        cap_fail_exit evidence-mismatch "the published raw $RAW_PATH no longer matches its recorded hash${CAP_VERIFY_NOTE:+ ($CAP_VERIFY_NOTE)} (recorded raw evidence is never refetched; operator)"
      else
        cap_fail_exit evidence-mismatch "the published raw $RAW_PATH is missing (recorded raw evidence is never refetched; operator)"
      fi
    elif [[ -n "$RAW_SHA256" ]]; then
      # recorded, not yet published: finish from the staged or final copy carrying that hash
      # (staged and final trees of a bundle are verified before the collision is reconciled, A5)
      if [[ -n "$RAW_STAGED" && -e "$RAW_STAGED" ]]; then
        if cap_raw_matches "$RAW_STAGED" "$RAW_SHA256"; then
          if [[ -e "$RAW_PATH" ]] && ! cap_raw_matches "$RAW_PATH" "$RAW_SHA256"; then
            cap_fail_exit archive-exists "a different file sits at $RAW_PATH (no receipt accounts for it)"
          fi
          if [[ -e "$RAW_PATH" ]]; then rm -rf "$RAW_STAGED"
          elif ! cap_raw_rename_into_place "$RAW_STAGED" "$RAW_PATH"; then
            cap_fail_exit fetch "could not publish the staged raw into $RAW_PATH (the staged copy and its hash are retained)"
          fi
          staged_match=true
        else
          cap_fail_exit archive-exists "the staged raw $RAW_STAGED does not match the recorded hash${CAP_VERIFY_NOTE:+ ($CAP_VERIFY_NOTE)} (no receipt accounts for it)"
        fi
      elif [[ -e "$RAW_PATH" ]]; then
        if cap_raw_matches "$RAW_PATH" "$RAW_SHA256"; then staged_match=true
        else cap_fail_exit archive-exists "a different file sits at $RAW_PATH (no receipt accounts for it)"; fi
      else
        cap_fail_exit evidence-missing "the raw recorded for $RAW_PATH (sha256 $RAW_SHA256) exists neither staged nor published (recorded raw evidence is never refetched; operator)"
      fi
      cap_crash_at after-raw-renamed
      cap_receipt_update "$CAPTURE_ID" '.raw_published=true | .raw_staged_path=null'
      cap_reconvert || cap_fail_exit "$FAIL_CLASS" "$FAIL_MSG"
    else
      # nothing recorded: a stale staged file or directory is discarded and the input is acquired
      # again — this is the one place the locator is consulted (a --site auto plan frozen in
      # attempt-1/plan.json is reused by cap_acquire_site)
      [[ -n "$RAW_STAGED" && -e "$RAW_STAGED" ]] && rm -rf "$RAW_STAGED"
      cap_restore_input
      cap_acquire_fresh
    fi
  else
    cap_restore_input
    cap_acquire_fresh
  fi
  cap_publish_source || cap_fail_exit "$FAIL_CLASS" "$FAIL_MSG"
  if cap_extract; then cap_finish_extracted true "$KEEP_LOCATOR"; else cap_finish_extract_failed true; fi
}

cap_acquire_fresh() {
  RAW_SHA256=""; RAW_BYTES=""
  DEPTH_MANIFEST_FILE=""
  # (the depth preflight of §4.2 runs inside cap_acquire_url, after the landing fetch, on the staged raw)
  cap_acquire || cap_fail_exit "$FAIL_CLASS" "$FAIL_MSG"
}

# A raw (file or bundle directory) carries the recorded hash: a file by its bytes, a bundle by
# capture-site.py verify (the manifest hash and every member — CAP_VERIFY_NOTE names a miss).
cap_raw_matches() {   # <path> <sha256>
  CAP_VERIFY_NOTE=""
  if [[ -d "$1" ]]; then cap_bundle_verify "$1" "$2"; else [[ -f "$1" && "$(cap_sha256 "$1")" == "$2" ]]; fi
}

cap_raw_rename_into_place() {   # <staged> <final>
  if [[ -d "$1" ]]; then cap_py publish-dir "$1" "$2"; else cap_py fsync-rename "$1" "$2"; fi
}

# ---------------------------------------------------------------------------------------------
# A fresh operation (no receipt for the capture_id)
# ---------------------------------------------------------------------------------------------
cap_fresh() {
  local incoming="" latest folder legacy folder_cid
  REPLAYED=false
  KEEP_LOCATOR=false
  TEXT_INPUT=""
  # 1. input bytes (a file, never $(…))
  case "$(cap_resource_kind "$ORIGIN")" in
    text)
      if [[ -n "$FILE_TEXT" ]]; then
        [[ -f "$FILE_TEXT" ]] || { echo "ERROR: --file-text not found: $FILE_TEXT" >&2; exit 1; }
        TEXT_INPUT="$FILE_TEXT"
      else
        [[ -t 0 ]] && echo "Paste content, then press Ctrl+D:" >&2
        incoming="$RUNS_DIR/.incoming-$$"
        cat > "$incoming"
        chmod 600 "$incoming"
        TEXT_INPUT="$incoming"
        KEEP_LOCATOR=true
      fi ;;
  esac
  cap_resource_identity
  cap_canonical_request

  # 2. dedup / resume against the resource's revisions (never a new folder unless --recapture):
  # the candidate/head rule of §4.1 (a cycle exits 1 here, under the id this run reports)
  $CAPTURE_ID_GIVEN || CAPTURE_ID="$(cap_generate_capture_id "$RESOURCE_ID")"
  cap_lookup_head
  latest=""
  if [[ -n "$CAP_HEAD" ]]; then
    if [[ "$CAP_HEAD_MANAGED" == "true" ]]; then latest="$CAP_HEAD false"; else latest="$CAP_HEAD true"; fi
  fi
  if [[ -n "$latest" ]] && ! $RECAPTURE; then
    folder="${latest%% *}"; legacy="${latest##* }"
    folder_cid="$(_mnemon_yaml_literal capture_id "$VAULT_PATH/Sources/$folder/source.md")"
    REVISION="$folder"
    if [[ "$legacy" == "true" || -f "$VAULT_PATH/Sources/$folder/extract.md" ]]; then
      [[ -n "$incoming" ]] && rm -f "$incoming"
      # every success path verifies the published source first (§3.4 step 5, §9.5): a duplicate
      # has no receipt of its own, so the folder's own capture.text_sha256 is the reference —
      # an edited body is integrity-error/evidence-mismatch (exit 1), never skipped-duplicate;
      # a legacy source without the field is verified by existence only
      cap_verify_folder_source "$folder" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
      if [[ -n "$SITE_MODE" ]]; then
        echo "WARNING: the landing is already captured at Sources/$folder/ — skipping; a site capture of a captured landing needs --recapture (the site revision supersedes it)" >&2
      elif [[ "${URL_KIND:-}" == "arxiv" && -n "${REQUESTED_VERSION:-}" ]]; then
        echo "WARNING: already captured at Sources/$folder/ (arXiv version $(_mnemon_yaml_literal arxiv_version "$VAULT_PATH/Sources/$folder/source.md")) — skipping; another version is a --recapture" >&2
      else
        echo "WARNING: already captured at Sources/$folder/ — skipping (use --recapture for a new revision)" >&2
      fi
      if $CAPTURE_ID_GIVEN; then
        TEXT_SHA256="$(cap_source_text_sha "$folder" 2>/dev/null || true)"
        ATTEMPT_DIR=""; RAW_STORE=none; RAW_PATH=""; RAW_STAGED=""; INPUT_PATH=""
        CAP_INIT_ATTEMPTS=0 cap_receipt_create extracted "$folder" "$folder_cid" "$legacy"
        cap_receipt_update "$CAPTURE_ID" '.finalized_at=$now'
      fi
      cap_result skipped-duplicate "$(cap_extract_state_of "$folder")" "" "" false
      exit 0
    fi
    # incomplete latest revision: resume it under a new attempt — as the owner when its receipt
    # is in the ledger and no id was given; otherwise under an alias receipt that delegates the
    # recovery to the owner receipt (§12 A4) or, without one, runs it itself
    [[ -n "$incoming" ]] && rm -f "$incoming"
    echo "NOTE: Sources/$folder/ has source.md but no extract.md — resuming the extraction" >&2
    if [[ -n "$folder_cid" ]] && cap_receipt_exists "$folder_cid" && ! $CAPTURE_ID_GIVEN; then
      CAPTURE_ID="$folder_cid"
      cap_restore_from_receipt
      REPLAYED=true
      cap_verify_source "$REVISION" "$CAPTURE_ID" "$TEXT_SHA256" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
      if cap_extract_belongs; then cap_reconcile_extracted "$KEEP_LOCATOR"; fi
      cap_run_extract_step true "$KEEP_LOCATOR"
    fi
    TEXT_SHA256="$(cap_source_text_sha "$folder" 2>/dev/null || true)"
    ATTEMPT_DIR=""; RAW_STORE=none; RAW_PATH=""; RAW_STAGED=""; INPUT_PATH=""
    CAP_INIT_ATTEMPTS=0 cap_receipt_create source-published "$folder" "$folder_cid" false
    cap_receipt_update "$CAPTURE_ID" '.source_published=true'
    REPLAYED=true
    RESOLVED_REV="$folder"; RESOLVED_CID="$folder_cid"; RECEIPT_STATE=source-published; RECEIPT_ATTEMPTS=0; LEGACY_MATCH=false
    cap_alias_replay
  fi
  if $RECAPTURE && [[ -z "$SUPERSEDES" && -n "$latest" ]]; then
    SUPERSEDES="${latest%% *}"
    cap_canonical_request
  fi

  # 3. a new revision: reserve the name, bind the id, record the intent
  REVISION="$(cap_allocate_revision "$RESOURCE_ID")"
  cap_new_attempt "$CAPTURE_ID"
  INPUT_PATH=""
  case "$RESOURCE_KIND" in
    text)
      if [[ -n "$incoming" ]]; then mv "$incoming" "$ATTEMPT_DIR/capture.md"; TEXT_INPUT="$ATTEMPT_DIR/capture.md"; INPUT_PATH="$TEXT_INPUT"
      else INPUT_PATH="$FILE_TEXT"; fi ;;
    file) INPUT_PATH="$FILE_PATH" ;;
    url)  [[ "$SITE_MODE" == "file" ]] && INPUT_PATH="$SITE_PLAN_FILE" ;;   # the plan is the input locator (D9)
  esac
  [[ "$SITE_MODE" == "auto" ]] && KEEP_LOCATOR=true   # attempt-1/plan.json, kept with the receipt
  RAW_STORE=none; RAW_PATH=""; RAW_STAGED=""; RAW_KIND=none
  case "$ORIGIN" in
    url)     if [[ -n "$SITE_MODE" ]]; then cap_raw_prepare site; RAW_KIND=site; else cap_raw_prepare html; fi ;;
    pdf)     cap_raw_prepare pdf ;;
    youtube) cap_raw_prepare txt ;;
    audio)   if [[ -n "$FILE_PATH" ]]; then cap_raw_prepare "$(cap_ext_of "$FILE_PATH" audio)"; else cap_raw_prepare "$(cap_url_ext "$URL" bin)"; fi ;;
    file|book) cap_raw_prepare "$(cap_ext_of "$FILE_PATH" bin)" ;;
  esac
  TEXT_SHA256=""
  CAP_INIT_ATTEMPTS=1 cap_receipt_create started
  if ! cap_preflight_extractor; then cap_fail_exit "$PREFLIGHT_CLASS" "$PREFLIGHT_MSG"; fi
  cap_acquire_fresh
  cap_publish_source || cap_fail_exit "$FAIL_CLASS" "$FAIL_MSG"
  if cap_extract; then cap_finish_extracted false "$KEEP_LOCATOR"; else cap_finish_extract_failed false; fi
}

# --resume <revision|capture_id>: the operator's explicit tool
cap_resolve_resume() {
  local target="$1" cid
  if cap_receipt_exists "$target"; then
    cap_receipt_case_check "$target"
    CAPTURE_ID="$target"; CAPTURE_ID_GIVEN=true
    if [[ "$(cap_receipt_get "$target" '.state')" == "extracted" && -f "$VAULT_PATH/Sources/$(cap_receipt_get "$target" '.resolved_to_revision // .revision')/extract.md" ]]; then
      cap_die2 "--resume: capture $target is complete (nothing to finish)"
    fi
    return 0
  fi
  if [[ -d "$VAULT_PATH/Sources/$target" ]]; then
    [[ -f "$VAULT_PATH/Sources/$target/extract.md" ]] && cap_die2 "--resume: revision $target is complete (nothing to finish)"
    [[ -f "$VAULT_PATH/Sources/$target/source.md" ]] || cap_die2 "--resume: Sources/$target/ has no source.md"
    cid="$(_mnemon_yaml_literal capture_id "$VAULT_PATH/Sources/$target/source.md")"
    if [[ -n "$cid" ]] && cap_receipt_exists "$cid"; then
      CAPTURE_ID="$cid"; CAPTURE_ID_GIVEN=true
      return 0
    fi
    # no receipt in this ledger: an alias under a fresh id resumes the folder
    cap_restore_folder_request "$target"
    REVISION="$target"
    $CAPTURE_ID_GIVEN || CAPTURE_ID="$(cap_generate_capture_id "$RESOURCE_ID")"
    TEXT_SHA256="$(cap_source_text_sha "$target" 2>/dev/null || true)"
    ATTEMPT_DIR=""; RAW_STORE=none; RAW_PATH=""; RAW_STAGED=""; INPUT_PATH=""
    CAP_INIT_ATTEMPTS=0 cap_receipt_create source-published "$target" "$cid" false
    cap_receipt_update "$CAPTURE_ID" '.source_published=true'
    REPLAYED=true
    cap_verify_source "$REVISION" "$cid" "$TEXT_SHA256" || cap_integrity_exit "$VERIFY_CLASS" "$VERIFY_MSG"
    cap_run_extract_step true false
  fi
  cap_die2 "--resume: no receipt and no revision named '$target'"
}

# Request globals from a published folder's frontmatter (for --resume without a receipt).
cap_restore_folder_request() {
  local f="$VAULT_PATH/Sources/$1/source.md"
  ORIGIN="$(_mnemon_yaml_literal origin "$f")"; [[ -n "$ORIGIN" ]] || ORIGIN=text
  URL="$(_mnemon_yaml_literal url "$f")"
  REF_PATH="$(_mnemon_yaml_literal ref_path "$f")"
  CONTEXT="$(_mnemon_yaml_literal context "$f")"; [[ -n "$CONTEXT" ]] || CONTEXT="$(cap_default_context)"
  SOURCE_TYPE="$(_mnemon_yaml_literal source_type "$f")"; [[ -n "$SOURCE_TYPE" ]] || SOURCE_TYPE=article
  CONTENT_FORMAT="$(_mnemon_yaml_literal content_format "$f")"
  RESOURCE_KIND="$(_mnemon_yaml_literal resource_kind "$f")"; [[ -n "$RESOURCE_KIND" ]] || RESOURCE_KIND="$(cap_resource_kind "$ORIGIN")"
  RESOURCE_SHA256="$(_mnemon_yaml_literal resource_sha256 "$f")"
  RESOURCE_ID="$(_mnemon_yaml_literal resource_id "$f")"; [[ -n "$RESOURCE_ID" ]] || RESOURCE_ID="$(printf '%s' "$1" | sed 's/^[0-9-]*_//; s/-[0-9]*$//')"
  RESOURCE_KEY="(from folder $1)"
  INPUT_SHA256=""
  TITLE="$(_mnemon_yaml_literal title "$f")"
  AUTHOR="$(_mnemon_yaml_literal author "$f")"
  SUPERSEDES="$(_mnemon_yaml_literal supersedes "$f")"
  cap_canonical_request
}

# ---------------------------------------------------------------------------------------------
# Batch (--manifest): per item, one child gateway under the inherited lock
# ---------------------------------------------------------------------------------------------
run_batch() {
  command -v jq >/dev/null 2>&1 || { echo "ERROR: jq required for batch mode. Install: brew install jq" >&2; exit 1; }
  [[ -f "$MANIFEST" ]] || { echo "ERROR: Manifest file not found: $MANIFEST" >&2; exit 1; }
  jq empty "$MANIFEST" 2>/dev/null || { echo "ERROR: Invalid JSON in manifest: $MANIFEST" >&2; exit 1; }

  local total passed=0 failed=0 errors="" batch_dir="" out_file
  total=$(jq 'length' "$MANIFEST")
  if ! $DRY_RUN; then
    batch_dir="$RUNS_DIR/batch-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p "$batch_dir"; chmod 700 "$batch_dir"
  fi

  echo "=== Batch Import ===" >&2
  echo "Manifest: $MANIFEST ($total items)" >&2
  [[ -n "$IMPORT_SOURCE" ]] && echo "Import source: $IMPORT_SOURCE" >&2
  [[ -n "$IMPORT_BATCH" ]]  && echo "Import batch: $IMPORT_BATCH" >&2
  echo "" >&2

  local i
  for i in $(seq 0 $((total - 1))); do
    local item_origin item_url item_title item_source_type item_ref_path item_file item_capture_id has_content
    item_origin=$(jq -r ".[$i].origin // empty" "$MANIFEST")
    item_url=$(jq -r ".[$i].url // empty" "$MANIFEST")
    item_title=$(jq -r ".[$i].title // empty" "$MANIFEST")
    item_source_type=$(jq -r ".[$i].source_type // empty" "$MANIFEST")
    item_ref_path=$(jq -r ".[$i].ref_path // empty" "$MANIFEST")
    item_file=$(jq -r ".[$i].file // empty" "$MANIFEST")
    item_capture_id=$(jq -r ".[$i].capture_id // .[$i].ref_id // empty" "$MANIFEST")
    has_content=$(jq -r "if (.[$i].content // null) == null then \"no\" else \"yes\" end" "$MANIFEST")

    local label="${item_title:-${item_url:-item $((i+1))}}"
    echo "[$((i+1))/$total] $label" >&2

    local -a item_args
    item_args=(source-add)
    [[ -n "$item_origin" ]]      && item_args+=(--origin "$item_origin")
    [[ -n "$item_url" ]]         && item_args+=(--url "$item_url")
    [[ -n "$item_file" ]]        && item_args+=(--file "$item_file")
    [[ -n "$item_title" ]]       && item_args+=(--title "$item_title")
    [[ -n "$item_source_type" ]] && item_args+=(--source-type "$item_source_type")
    [[ -n "$item_ref_path" ]]    && item_args+=(--ref-path "$item_ref_path")
    [[ -n "$item_capture_id" ]]  && item_args+=(--capture-id "$item_capture_id")
    if [[ "$has_content" == "yes" ]]; then
      if $DRY_RUN; then
        item_args+=(--origin "${item_origin:-text}")
      else
        # inline content → a file written with jq -j (no added newline), never echoed
        jq -j ".[$i].content" "$MANIFEST" > "$batch_dir/item-$i.txt"
        item_args+=(--file-text "$batch_dir/item-$i.txt")
      fi
    fi

    $CONTEXT_GIVEN             && item_args+=(--context "$CONTEXT")
    [[ -n "$SESSION" ]]        && item_args+=(--session "$SESSION")
    [[ -n "$INTENT" ]]         && item_args+=(--intent "$INTENT")
    [[ -n "$IMPORT_SOURCE" ]]  && item_args+=(--import-source "$IMPORT_SOURCE")
    [[ -n "$IMPORT_BATCH" ]]   && item_args+=(--import-batch "$IMPORT_BATCH")
    [[ -n "$CONFIG_PATH" ]]    && item_args+=(--config "$CONFIG_PATH")
    [[ -n "$MODEL_FLAG" ]]     && item_args+=(--model "$MODEL_FLAG")
    $RECAPTURE                 && item_args+=(--recapture)
    $KEEP_RUN                  && item_args+=(--keep-run)
    $DRY_RUN                   && item_args+=(--dry-run)

    local rc=0 eclass=""
    if $DRY_RUN; then
      "$0" "${item_args[@]}" </dev/null || rc=$?
    else
      out_file="$batch_dir/item-$i.out"
      "$0" "${item_args[@]}" </dev/null > "$out_file" || rc=$?
      cat "$out_file"
      eclass="$(grep '^RESULT:error_class=' "$out_file" | head -1 | cut -d= -f2- || true)"
    fi
    if [[ $rc -eq 0 ]]; then
      ((passed++)) || true
    else
      ((failed++)) || true
      errors+="  [$((i+1))] $label: exit $rc${eclass:+ ($eclass)}"$'\n'
    fi
    echo "" >&2
  done

  echo "=== Batch Complete ===" >&2
  echo "Total: $total | Passed: $passed | Failed: $failed" >&2
  if [[ -n "$errors" ]]; then
    echo "" >&2
    echo "Failures:" >&2
    echo "$errors" >&2
  fi

  echo "RESULT:batch_total=$total"
  echo "RESULT:batch_passed=$passed"
  echo "RESULT:batch_failed=$failed"

  [[ $failed -gt 0 ]] && return 1
  return 0
}

# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------
cap_main() {
  # A replay (--capture-id / --resume alone) takes its origin from the receipt (§3.1 binding rules).
  if [[ -z "$ORIGIN" ]] && ! $CAPTURE_ID_GIVEN && [[ -z "$RESUME" ]]; then
    echo "ERROR: Cannot determine origin. Provide --origin or --url." >&2; usage
  fi
  cap_contract_checks

  if $DRY_RUN; then
    if [[ "$ORIGIN" == "batch" ]]; then run_batch; return $?; fi
    cap_dry_run
    return 0
  fi

  [[ -d "$VAULT_PATH" ]] || { echo "ERROR: Vault not found at $VAULT_PATH. Run mnemon init first." >&2; exit 1; }

  # Under the vault lock, or become so (exec never returns).
  if ! cap_under_lock; then
    cap_reexec_under_lock "$0" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
  fi
  cap_state_init
  CAP_VAULT_PHYS="$(cd "$VAULT_PATH" && pwd -P)"
  cap_cleanup_staging

  if [[ "$ORIGIN" == "batch" ]]; then
    run_batch
    return $?
  fi

  REF_ID_GIVEN=""
  $CAPTURE_ID_GIVEN && REF_ID_GIVEN=1

  if [[ -n "$RESUME" ]]; then
    cap_resolve_resume "$RESUME"   # exits unless it resolved a receipt id
    REF_ID_GIVEN=""
  fi

  if $CAPTURE_ID_GIVEN && cap_receipt_exists "$CAPTURE_ID"; then
    cap_receipt_case_check "$CAPTURE_ID"
    cap_replay
  fi
  if [[ -z "$ORIGIN" ]]; then
    echo "ERROR: no receipt for capture-id '$CAPTURE_ID' in this vault's ledger and no origin given — an unknown id starts a fresh capture, which needs --origin/--url/--file." >&2
    exit 2
  fi
  cap_fresh
}

case "$ACTION" in
  source-add)
    # Every success path exits inside cap_main (cap_success_exit runs the qmd refresh there, with
    # the lock descriptor closed first — D15, §12 A1/A8); batch items run their own.
    rc=0
    cap_main || rc=$?
    exit $rc
    ;;

  status)
    echo "=== Mnemon Status ==="
    echo "Vault: $VAULT_PATH"
    echo ""
    if [[ ! -d "$VAULT_PATH/Sources" ]]; then
      echo "No Sources/ directory. Run setup.sh first."
      exit 0
    fi
    total=$(find "$VAULT_PATH/Sources" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
    # The one Extracted (vault store S2): folders with extract.md — the processing axis, read by
    # bin/lib_vault.py as the doctor and `status --brief` read it; the stored `status:` words of
    # extract.md are a breakdown of that count, never a second counter (#9)
    vc="$(python3 "$SCRIPT_DIR/lib_vault.py" --vault "$VAULT_PATH" --status-counts 2>/dev/null || true)"
    _vc() { printf '%s\n' "$vc" | awk -F= -v k="$1" '$1 == k { print $2; exit }'; }
    extracted="$(_vc extracted)"; extracted="${extracted:-0}"
    # §3.9: superseded = folders named in any published source.md's supersedes: (both forms);
    # chunked = extract.md files with extraction: chunked
    counts="$(cap_py status-counts "$VAULT_PATH/Sources" 2>/dev/null || echo 'superseded=0 chunked=0')"
    echo "Total sources: $total"
    echo "Extracted: $extracted"
    echo "By status: extracted $(_vc status_extracted | sed 's/^$/0/') · dismissed $(_vc status_dismissed | sed 's/^$/0/') · legacy words $(_vc status_legacy_words | sed 's/^$/0/') · empty $(_vc status_empty | sed 's/^$/0/') · unknown $(_vc status_unknown | sed 's/^$/0/')"
    echo "Superseded: ${counts#superseded=}" | sed 's/ chunked=.*//'
    echo "Chunked: ${counts##*chunked=}"
    echo ""
    echo "Recent additions:"
    find "$VAULT_PATH/Sources" -maxdepth 1 -mindepth 1 -type d -name "20*" 2>/dev/null | sort -r | head -5 | while read -r d; do
      title=$(grep '^title:' "$d/extract.md" 2>/dev/null | head -1 | sed 's/^title:[[:space:]]*//' | tr -d '"' || echo "untitled")
      echo "  $(basename "$d") — $title"
    done
    ;;

  *)
    echo "Unknown action: $ACTION" >&2
    usage
    ;;
esac
