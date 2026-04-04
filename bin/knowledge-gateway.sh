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
  if [[ -n "$URL" ]]; then
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
    url)      SOURCE_TYPE="article" ;;
    youtube)  SOURCE_TYPE="video" ;;
    audio)    SOURCE_TYPE="podcast" ;;
    book)     SOURCE_TYPE="book" ;;
    idea)     SOURCE_TYPE="idea" ;;
    text)     SOURCE_TYPE="article" ;;
    *)        SOURCE_TYPE="article" ;;
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

build_prompt() {
  local template reader_context
  template=$(load_template "$SOURCE_TYPE")
  reader_context=$(load_reader_context)

  local prompt="NON-INTERACTIVE GATEWAY REQUEST. Execute ONLY the requested action. No onboarding, no briefing, no questions, no summaries of what you plan to do. Just execute.

=== ACTION ===
source-add
Origin: $ORIGIN
Source type: $SOURCE_TYPE"

  [[ -n "$URL" ]]       && prompt+=$'\n'"URL: $URL"
  [[ -n "$FILE_PATH" ]] && prompt+=$'\n'"File: $FILE_PATH"
  [[ -n "$TITLE" ]]     && prompt+=$'\n'"Title: $TITLE"
  [[ -n "$AUTHOR" ]]    && prompt+=$'\n'"Author: $AUTHOR"

  if [[ "$ORIGIN" == "youtube" || "$ORIGIN" == "audio" ]]; then
    prompt+=$'\n'"Content format: transcript"
    prompt+=$'\n'"Note: Transcript text is provided via stdin. Use it as the source content."
  fi

  prompt+=$'\n\n'"=== CAPTURE CONTEXT ===
Context: $CONTEXT
Session: ${SESSION:-unknown}
Intent: ${INTENT:-none specified}

=== READER CONTEXT ===
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

  if $DRY_RUN; then
    echo "=== DRY RUN ==="
    echo "Vault: $VAULT_PATH"
    echo "Model: $MODEL"
    echo "Template: templates/core/${SOURCE_TYPE}.md"
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

  if [[ -n "$stdin_content" ]]; then
    output=$(echo "$stdin_content" | (cd "$VAULT_PATH" && claude -p \
      --model "$MODEL" \
      --allowedTools Read,Write,Edit,Bash,Glob,Grep,WebFetch \
      --output-format text \
      "$prompt") 2>&1) || exit_code=$?
  else
    output=$((cd "$VAULT_PATH" && claude -p \
      --model "$MODEL" \
      --allowedTools Read,Write,Edit,Bash,Glob,Grep,WebFetch \
      --output-format text \
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

# --- Main dispatch ---

case "$ACTION" in
  source-add)
    [[ -z "$ORIGIN" ]] && { echo "ERROR: Cannot determine origin. Provide --origin or --url." >&2; usage; }
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

    elif [[ "$ORIGIN" == "text" || "$ORIGIN" == "idea" ]]; then
      if [[ -t 0 ]]; then
        echo "Paste content, then press Ctrl+D:" >&2
      fi
      stdin_content=$(cat)
      invoke_claude "$prompt" "$stdin_content"

    else
      invoke_claude "$prompt"
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
