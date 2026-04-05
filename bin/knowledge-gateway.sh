#!/usr/bin/env bash
set -euo pipefail

# Load config helper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MNEMON_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/mnemon-config.sh"

usage() {
  cat <<'USAGE'
Usage: knowledge-gateway.sh <action> [options]

Actions:
  source-add    Capture a new source and generate extract
  status        Show pipeline status

Options:
  --origin <type>       url|text|youtube|audio|book|idea
  --url <url>           Source URL
  --file <path>         Local file path (for audio)
  --title <title>       Source title (optional, inferred if not given)
  --author <author>     Source author (optional)
  --source-type <type>  article|video|podcast|book|paper|idea|conversation
  --context <ctx>       Capture context label (default: personal)
  --session <name>      Session title (optional)
  --intent <text>       Why this was captured (optional)
  --ref-path <path>     Vault note path (for origin=ref:vault)
  --ref-source <name>   MCP server name (for origin=ref:mcp)
  --ref-id <id>         ID in source system (for origin=ref:mcp)
  --manifest <path>     JSON manifest file (for origin=batch)
  --import-source <s>   Import source label (apple-notes, safari, telegram-saved, etc.)
  --import-batch <id>   Batch ID for grouping dump items
  --config <path>       Path to mnemon.yaml
  --model <model>       Override default model
  --dry-run             Show prompt without executing
  --whisper-model <m>   Whisper model (default from config)
  --no-whisper          Disable whisper fallback for YouTube
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
TITLE=""
AUTHOR=""
SOURCE_TYPE=""
CONTEXT="personal"
SESSION=""
INTENT=""
REF_PATH=""
REF_SOURCE=""
REF_ID=""
MANIFEST=""
IMPORT_SOURCE=""
IMPORT_BATCH=""
CONFIG_PATH=""
MODEL_FLAG=""
DRY_RUN=false
WHISPER_MODEL_FLAG=""
NO_WHISPER=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --origin)        [[ $# -lt 2 ]] && { echo "ERROR: --origin requires a value" >&2; exit 1; }; ORIGIN="$2"; shift 2 ;;
    --url)           [[ $# -lt 2 ]] && { echo "ERROR: --url requires a value" >&2; exit 1; }; URL="$2"; shift 2 ;;
    --file)          [[ $# -lt 2 ]] && { echo "ERROR: --file requires a value" >&2; exit 1; }; FILE_PATH="$2"; shift 2 ;;
    --title)         [[ $# -lt 2 ]] && { echo "ERROR: --title requires a value" >&2; exit 1; }; TITLE="$2"; shift 2 ;;
    --author)        [[ $# -lt 2 ]] && { echo "ERROR: --author requires a value" >&2; exit 1; }; AUTHOR="$2"; shift 2 ;;
    --source-type)   [[ $# -lt 2 ]] && { echo "ERROR: --source-type requires a value" >&2; exit 1; }; SOURCE_TYPE="$2"; shift 2 ;;
    --context)       [[ $# -lt 2 ]] && { echo "ERROR: --context requires a value" >&2; exit 1; }; CONTEXT="$2"; shift 2 ;;
    --session)       [[ $# -lt 2 ]] && { echo "ERROR: --session requires a value" >&2; exit 1; }; SESSION="$2"; shift 2 ;;
    --intent)        [[ $# -lt 2 ]] && { echo "ERROR: --intent requires a value" >&2; exit 1; }; INTENT="$2"; shift 2 ;;
    --ref-path)      [[ $# -lt 2 ]] && { echo "ERROR: --ref-path requires a value" >&2; exit 1; }; REF_PATH="$2"; shift 2 ;;
    --ref-source)    [[ $# -lt 2 ]] && { echo "ERROR: --ref-source requires a value" >&2; exit 1; }; REF_SOURCE="$2"; shift 2 ;;
    --ref-id)        [[ $# -lt 2 ]] && { echo "ERROR: --ref-id requires a value" >&2; exit 1; }; REF_ID="$2"; shift 2 ;;
    --manifest)      [[ $# -lt 2 ]] && { echo "ERROR: --manifest requires a value" >&2; exit 1; }; MANIFEST="$2"; shift 2 ;;
    --import-source) [[ $# -lt 2 ]] && { echo "ERROR: --import-source requires a value" >&2; exit 1; }; IMPORT_SOURCE="$2"; shift 2 ;;
    --import-batch)  [[ $# -lt 2 ]] && { echo "ERROR: --import-batch requires a value" >&2; exit 1; }; IMPORT_BATCH="$2"; shift 2 ;;
    --config)        [[ $# -lt 2 ]] && { echo "ERROR: --config requires a value" >&2; exit 1; }; CONFIG_PATH="$2"; shift 2 ;;
    --model)         [[ $# -lt 2 ]] && { echo "ERROR: --model requires a value" >&2; exit 1; }; MODEL_FLAG="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=true; shift ;;
    --whisper-model) [[ $# -lt 2 ]] && { echo "ERROR: --whisper-model requires a value" >&2; exit 1; }; WHISPER_MODEL_FLAG="$2"; shift 2 ;;
    --no-whisper)    NO_WHISPER=true; shift ;;
    *)               echo "Unknown option: $1" >&2; usage ;;
  esac
done

# Load config (uses CONFIG_PATH if set, otherwise auto-discovers)
load_config "$CONFIG_PATH"

# Flags override config values
MODEL="${MODEL_FLAG:-$DEFAULT_MODEL}"
[[ -n "$WHISPER_MODEL_FLAG" ]] && WHISPER_MODEL="$WHISPER_MODEL_FLAG"

# --- Auto-detect origin ---

if [[ -z "$ORIGIN" && "$AUTO_DETECT_ORIGIN" == "true" ]]; then
  if [[ -n "$MANIFEST" ]]; then
    ORIGIN="batch"
  elif [[ -n "$REF_PATH" ]]; then
    ORIGIN="ref:vault"
  elif [[ -n "$REF_SOURCE" && -n "$REF_ID" ]]; then
    ORIGIN="ref:mcp"
  elif [[ -n "$URL" ]]; then
    if [[ "$URL" =~ (youtube\.com/watch|youtu\.be/|youtube\.com/shorts/) ]]; then
      ORIGIN="youtube"
    else
      ORIGIN="url"
    fi
  elif [[ -n "$FILE_PATH" ]]; then
    if command -v ffprobe >/dev/null 2>&1 && \
       ffprobe -v quiet -show_entries format=format_name -of csv=p=0 "$FILE_PATH" 2>/dev/null | \
       grep -qiE 'mp3|wav|flac|ogg|aac|m4a|opus'; then
      ORIGIN="audio"
    else
      ORIGIN="file"
    fi
  fi
fi

# --- Auto-detect source_type from origin ---

if [[ -z "$SOURCE_TYPE" ]]; then
  case "${ORIGIN:-}" in
    url)        SOURCE_TYPE="article" ;;
    youtube)    SOURCE_TYPE="video" ;;
    audio)      SOURCE_TYPE="podcast" ;;
    book)       SOURCE_TYPE="book" ;;
    idea)       SOURCE_TYPE="idea" ;;
    text)       SOURCE_TYPE="article" ;;
    ref:vault)  SOURCE_TYPE="document" ;;
    ref:mcp)    SOURCE_TYPE="document" ;;
    batch)      SOURCE_TYPE="" ;;  # determined per-item
    *)          SOURCE_TYPE="article" ;;
  esac
fi

# --- Template and context loading ---

load_template() {
  local st="$1"
  local path="$MNEMON_ROOT/templates/core/${st}.md"
  if [[ -f "$path" ]]; then
    cat "$path"
  else
    echo "(No extraction template found for source_type '${st}'. Use generic extraction: create Summary, Executive Summary framed by reader context, Key Ideas with domain tags, Connections, Raw Quotes.)"
  fi
}

load_reader_context() {
  if [[ -f "$READER_CONTEXT_PATH" ]]; then
    cat "$READER_CONTEXT_PATH"
  else
    echo "(No reader context configured at $READER_CONTEXT_PATH. Generate a generic extract without personal framing. Suggest the user edit reader-context.md.)"
  fi
}

# --- Prompt construction ---

_sanitize_input() {
  # Strip newlines and === delimiters from user-supplied values to prevent prompt injection
  printf '%s' "$1" | tr '\n' ' ' | sed 's/===//g'
}

build_prompt() {
  local template reader_context
  template=$(load_template "$SOURCE_TYPE")
  reader_context=$(load_reader_context)

  # Sanitize user-supplied fields
  local safe_title safe_author safe_intent safe_session
  safe_title="$(_sanitize_input "${TITLE:-}")"
  safe_author="$(_sanitize_input "${AUTHOR:-}")"
  safe_intent="$(_sanitize_input "${INTENT:-none specified}")"
  safe_session="$(_sanitize_input "${SESSION:-unknown}")"

  local safe_ref_path safe_ref_source safe_ref_id safe_import_source safe_import_batch
  safe_ref_path="$(_sanitize_input "${REF_PATH:-}")"
  safe_ref_source="$(_sanitize_input "${REF_SOURCE:-}")"
  safe_ref_id="$(_sanitize_input "${REF_ID:-}")"
  safe_import_source="$(_sanitize_input "${IMPORT_SOURCE:-}")"
  safe_import_batch="$(_sanitize_input "${IMPORT_BATCH:-}")"

  local prompt="NON-INTERACTIVE GATEWAY REQUEST. Execute ONLY the requested action. No onboarding, no briefing, no questions, no summaries of what you plan to do. Just execute.

=== ACTION ===
source-add
Origin: $ORIGIN
Source type: $SOURCE_TYPE"

  [[ -n "$URL" ]]             && prompt+=$'\n'"URL: $URL"
  [[ -n "$FILE_PATH" ]]       && prompt+=$'\n'"File: $FILE_PATH"
  [[ -n "$safe_title" ]]      && prompt+=$'\n'"Title: $safe_title"
  [[ -n "$safe_author" ]]     && prompt+=$'\n'"Author: $safe_author"
  [[ -n "$safe_ref_path" ]]   && prompt+=$'\n'"Ref path: $safe_ref_path"
  [[ -n "$safe_ref_source" ]] && prompt+=$'\n'"Ref source: $safe_ref_source"
  [[ -n "$safe_ref_id" ]]     && prompt+=$'\n'"Ref ID: $safe_ref_id"

  if [[ "$ORIGIN" == "youtube" || "$ORIGIN" == "audio" ]]; then
    prompt+=$'\n'"Content format: transcript"
    prompt+=$'\n'"Note: Transcript text is provided via stdin. Use it as the source content."
  elif [[ "$ORIGIN" == "ref:vault" ]]; then
    prompt+=$'\n'"Note: Source content is provided via stdin. Create source.md with ref_path: $safe_ref_path in frontmatter. The source REFERENCES the original note, not copies it."
  elif [[ "$ORIGIN" == "ref:mcp" ]]; then
    prompt+=$'\n'"Note: Use the MCP tool for '$safe_ref_source' to fetch content by ID '$safe_ref_id'. Create source.md with ref_source and ref_id fields in frontmatter."
  fi

  prompt+=$'\n\n'"=== CAPTURE CONTEXT ===
Context: $CONTEXT
Session: $safe_session
Intent: $safe_intent"
  [[ -n "$safe_import_source" ]] && prompt+=$'\n'"Import source: $safe_import_source"
  [[ -n "$safe_import_batch" ]]  && prompt+=$'\n'"Import batch: $safe_import_batch"

  prompt+=$'\n\n'"=== READER CONTEXT ===
$reader_context

=== EXTRACTION TEMPLATE ===
$template

=== INSTRUCTIONS ===
1. For URL sources: fetch content using the WebFetch tool. For text/transcript sources: use the stdin content provided.
2. Compute folder hash: run \`echo -n \"<canonical-url-or-title_date>\" | shasum -a 256 | cut -c1-8\` via Bash tool.
3. Create folder: Sources/\$(date +%Y-%m-%d)_<hash8>/
4. If folder exists, append -2, -3, etc.
5. Write source.md — immutable raw content with frontmatter:
   type: source, source_type: $SOURCE_TYPE, content_format: <text|transcript|reference>, origin: $ORIGIN, url: \"$URL\", author: \"${AUTHOR:-}\", captured: <today>, captured_by: agent
6. Apply the EXTRACTION TEMPLATE above to generate extract.md.
7. Executive Summary MUST be framed by the READER CONTEXT — not generic, but personal to the reader.
8. Key Ideas MUST use domain tags from the Reader Context.
9. Rating: 1-10 based on actual insight density. Be critical. 5 = average, 7 = good, 9+ = exceptional.
10. Print results exactly as (no other text after these lines):
    RESULT:path=Sources/<folder>/
    RESULT:status=extracted
    RESULT:title=<extracted title>"

  echo "$prompt"
}

# --- Claude invocation ---

invoke_claude() {
  local prompt="$1"
  local stdin_content="${2:-}"
  local skip_allowed_tools="${3:-false}"

  if $DRY_RUN; then
    echo "=== DRY RUN ==="
    echo "Vault: $VAULT_PATH"
    echo "Model: $MODEL"
    echo "Template: templates/core/${SOURCE_TYPE}.md"
    [[ "$skip_allowed_tools" == "true" ]] && echo "Mode: MCP (no tool restrictions)"
    echo "=== PROMPT ==="
    echo "$prompt"
    if [[ -n "$stdin_content" ]]; then
      echo "=== STDIN (${#stdin_content} chars) ==="
      echo "${stdin_content:0:300}..."
    fi
    return 0
  fi

  if [[ ! -d "$VAULT_PATH" ]]; then
    echo "ERROR: Vault not found at $VAULT_PATH. Run setup.sh first." >&2
    return 1
  fi

  local output exit_code=0
  local -a claude_args=(--model "$MODEL" --output-format text)
  if [[ "$skip_allowed_tools" != "true" ]]; then
    claude_args+=(--allowedTools=Read,Write,Edit,Bash,Glob,Grep,WebFetch)
  fi

  if [[ -n "$stdin_content" ]]; then
    output=$(echo "$stdin_content" | (cd "$VAULT_PATH" && claude -p \
      "${claude_args[@]}" \
      "$prompt") 2>&1) || exit_code=$?
  else
    output=$((cd "$VAULT_PATH" && claude -p \
      "${claude_args[@]}" \
      "$prompt") 2>&1) || exit_code=$?
  fi

  if [[ $exit_code -ne 0 ]]; then
    echo "ERROR: Gateway failed (exit code $exit_code)" >&2
    save_pending_write "$prompt" "$stdin_content" "$output"
    echo "$output" >&2
    return 1
  fi

  # Show output without RESULT lines (narrative for human)
  echo "$output" | grep -v "^RESULT:" || true

  # Extract RESULT lines for caller (machine-parseable)
  echo "$output" | grep "^RESULT:" || true
}

_json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' '
}

save_pending_write() {
  local prompt="$1" content="${2:-}" error_output="${3:-}"
  local pending_dir="$VAULT_PATH/_inputs/pending-writes"
  mkdir -p "$pending_dir"
  local ts
  ts=$(date +%Y%m%d-%H%M%S)
  cat > "$pending_dir/$ts.json" << PEND_EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "action": "$(_json_escape "$ACTION")",
  "origin": "$(_json_escape "$ORIGIN")",
  "url": "$(_json_escape "$URL")",
  "file": "$(_json_escape "$FILE_PATH")",
  "context": "$(_json_escape "$CONTEXT")",
  "session": "$(_json_escape "$SESSION")",
  "error": "$(_json_escape "$error_output")"
}
PEND_EOF
  echo "Saved to pending-writes: $pending_dir/$ts.json" >&2
}

# --- Batch processing ---

run_batch() {
  command -v jq >/dev/null 2>&1 || { echo "ERROR: jq required for batch mode. Install: brew install jq" >&2; exit 1; }

  if [[ ! -f "$MANIFEST" ]]; then
    echo "ERROR: Manifest file not found: $MANIFEST" >&2
    exit 1
  fi

  if ! jq empty "$MANIFEST" 2>/dev/null; then
    echo "ERROR: Invalid JSON in manifest: $MANIFEST" >&2
    exit 1
  fi

  local total passed=0 failed=0 errors=""
  total=$(jq 'length' "$MANIFEST")

  echo "=== Batch Import ===" >&2
  echo "Manifest: $MANIFEST ($total items)" >&2
  [[ -n "$IMPORT_SOURCE" ]] && echo "Import source: $IMPORT_SOURCE" >&2
  [[ -n "$IMPORT_BATCH" ]]  && echo "Import batch: $IMPORT_BATCH" >&2
  echo "" >&2

  for i in $(seq 0 $((total - 1))); do
    local item_origin item_url item_title item_content item_source_type
    local item_ref_path item_ref_source item_ref_id item_file
    item_origin=$(jq -r ".[$i].origin // empty" "$MANIFEST")
    item_url=$(jq -r ".[$i].url // empty" "$MANIFEST")
    item_title=$(jq -r ".[$i].title // empty" "$MANIFEST")
    item_content=$(jq -r ".[$i].content // empty" "$MANIFEST")
    item_source_type=$(jq -r ".[$i].source_type // empty" "$MANIFEST")
    item_ref_path=$(jq -r ".[$i].ref_path // empty" "$MANIFEST")
    item_ref_source=$(jq -r ".[$i].ref_source // empty" "$MANIFEST")
    item_ref_id=$(jq -r ".[$i].ref_id // empty" "$MANIFEST")
    item_file=$(jq -r ".[$i].file // empty" "$MANIFEST")

    local label="${item_title:-${item_url:-item $((i+1))}}"
    echo "[$((i+1))/$total] $label" >&2

    # Build per-item gateway args
    local -a item_args=(source-add)
    [[ -n "$item_origin" ]]      && item_args+=(--origin "$item_origin")
    [[ -n "$item_url" ]]         && item_args+=(--url "$item_url")
    [[ -n "$item_file" ]]        && item_args+=(--file "$item_file")
    [[ -n "$item_title" ]]       && item_args+=(--title "$item_title")
    [[ -n "$item_source_type" ]] && item_args+=(--source-type "$item_source_type")
    [[ -n "$item_ref_path" ]]    && item_args+=(--ref-path "$item_ref_path")
    [[ -n "$item_ref_source" ]]  && item_args+=(--ref-source "$item_ref_source")
    [[ -n "$item_ref_id" ]]      && item_args+=(--ref-id "$item_ref_id")

    # Inherit parent batch params
    item_args+=(--context "$CONTEXT")
    [[ -n "$SESSION" ]]        && item_args+=(--session "$SESSION")
    [[ -n "$INTENT" ]]         && item_args+=(--intent "$INTENT")
    [[ -n "$IMPORT_SOURCE" ]]  && item_args+=(--import-source "$IMPORT_SOURCE")
    [[ -n "$IMPORT_BATCH" ]]   && item_args+=(--import-batch "$IMPORT_BATCH")
    [[ -n "$CONFIG_PATH" ]]    && item_args+=(--config "$CONFIG_PATH")
    [[ -n "$MODEL_FLAG" ]]     && item_args+=(--model "$MODEL_FLAG")
    $DRY_RUN && item_args+=(--dry-run)

    # For text origin with inline content, pipe it
    if [[ "$item_origin" == "text" && -n "$item_content" ]]; then
      if echo "$item_content" | "$0" "${item_args[@]}" 2>&1; then
        ((passed++)) || true
      else
        ((failed++)) || true
        errors+="  [$((i+1))] $label: gateway failed"$'\n'
      fi
    else
      if "$0" "${item_args[@]}" 2>&1; then
        ((passed++)) || true
      else
        ((failed++)) || true
        errors+="  [$((i+1))] $label: gateway failed"$'\n'
      fi
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

  # Machine-parseable summary
  echo "RESULT:batch_total=$total"
  echo "RESULT:batch_passed=$passed"
  echo "RESULT:batch_failed=$failed"

  [[ $failed -gt 0 ]] && return 1
  return 0
}

# --- Main dispatch ---

case "$ACTION" in
  source-add)
    [[ -z "$ORIGIN" ]] && { echo "ERROR: Cannot determine origin. Provide --origin or --url." >&2; usage; }

    # Batch mode dispatches directly — no prompt needed
    if [[ "$ORIGIN" == "batch" ]]; then
      run_batch

    else
      prompt=$(build_prompt)

      if [[ "$ORIGIN" == "youtube" ]]; then
        # In dry-run mode, skip transcript fetch — just show the prompt
        if $DRY_RUN; then
          invoke_claude "$prompt"
        else
          media_args=("youtube" "$URL" "--whisper-model" "$WHISPER_MODEL")
          $NO_WHISPER && media_args+=("--no-whisper")
          stdin_content=$("$SCRIPT_DIR/media-extract.py" "${media_args[@]}") || {
            save_pending_write "$prompt" "" "media-extract.py failed for $URL"
            exit 1
          }
          invoke_claude "$prompt" "$stdin_content"
        fi

      elif [[ "$ORIGIN" == "audio" ]]; then
        # In dry-run mode, skip transcript fetch — just show the prompt
        if $DRY_RUN; then
          invoke_claude "$prompt"
        else
          media_args=("audio" "--whisper-model" "$WHISPER_MODEL")
          [[ -n "$URL" ]] && media_args+=("--url" "$URL")
          [[ -n "$FILE_PATH" ]] && media_args+=("--file" "$FILE_PATH")
          [[ -n "$TITLE" ]] && media_args+=("--title" "$TITLE")
          stdin_content=$("$SCRIPT_DIR/media-extract.py" "${media_args[@]}") || {
            save_pending_write "$prompt" "" "media-extract.py failed for ${URL:-$FILE_PATH}"
            exit 1
          }
          invoke_claude "$prompt" "$stdin_content"
        fi

      elif [[ "$ORIGIN" == "ref:vault" ]]; then
        if [[ ! -f "$REF_PATH" ]]; then
          echo "ERROR: ref:vault file not found: $REF_PATH" >&2
          exit 1
        fi
        stdin_content=$(cat "$REF_PATH")
        invoke_claude "$prompt" "$stdin_content"

      elif [[ "$ORIGIN" == "ref:mcp" ]]; then
        # No --allowedTools restriction — Claude needs MCP tools
        invoke_claude "$prompt" "" "true"

      elif [[ "$ORIGIN" == "text" || "$ORIGIN" == "idea" ]]; then
        if [[ -t 0 ]]; then
          echo "Paste content, then press Ctrl+D:" >&2
        fi
        stdin_content=$(cat)
        invoke_claude "$prompt" "$stdin_content"

      else
        invoke_claude "$prompt"
      fi
    fi
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
    extracted=$(grep -l '^status: extracted' "$VAULT_PATH/Sources/"*/extract.md 2>/dev/null | wc -l | tr -d ' ') || extracted=0
    echo "Total sources: $total"
    echo "Extracted: $extracted"
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
