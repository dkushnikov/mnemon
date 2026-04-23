#!/usr/bin/env bash
# mnemon-config.sh — Config loader for Mnemon
# Source this file, then call load_config [path].
#
# Exports: MNEMON_ROOT, VAULT_PATH, READER_CONTEXT_PATH, SEARCH_PROVIDER,
#   QMD_COLLECTION, DEFAULT_MODEL, DEFAULT_LANGUAGE, WHISPER_MODEL, AUTO_DETECT_ORIGIN

# Auto-detect MNEMON_ROOT from this script's location
MNEMON_ROOT="${MNEMON_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Parse a single flat YAML key. Handles: key: value (with optional quotes, tilde)
_parse_yaml_value() {
  local key="$1" file="$2"
  local val
  # Use awk with exact string match (not regex) to avoid BRE metacharacter issues
  val=$(awk -v k="$key" 'BEGIN { prefix = k ": " } substr($0, 1, length(prefix)) == prefix { print substr($0, length(prefix) + 1); exit }' "$file" 2>/dev/null)
  # Strip inline comments (but not inside quotes)
  val=$(echo "$val" | sed 's/[[:space:]]*#.*$//')
  # Strip trailing whitespace
  val=$(echo "$val" | sed 's/[[:space:]]*$//')
  # Strip surrounding quotes
  val="${val#\"}" ; val="${val%\"}"
  val="${val#\'}" ; val="${val%\'}"
  # Resolve tilde
  val="${val/#\~/$HOME}"
  echo "$val"
}

load_config() {
  local config_file="${1:-}"

  # Config resolution: explicit arg → $MNEMON_CONFIG → ./mnemon.yaml → $MNEMON_ROOT/mnemon.yaml
  if [[ -z "$config_file" ]]; then
    if [[ -n "${MNEMON_CONFIG:-}" ]]; then
      config_file="$MNEMON_CONFIG"
    elif [[ -f "./mnemon.yaml" ]]; then
      config_file="./mnemon.yaml"
    elif [[ -f "$MNEMON_ROOT/mnemon.yaml" ]]; then
      config_file="$MNEMON_ROOT/mnemon.yaml"
    else
      echo "ERROR: No mnemon.yaml found. Run setup.sh first." >&2
      return 1
    fi
  fi

  if [[ ! -f "$config_file" ]]; then
    echo "ERROR: Config not found: $config_file" >&2
    return 1
  fi

  # Parse values
  VAULT_PATH="$(_parse_yaml_value vault_path "$config_file")"
  READER_CONTEXT_PATH="$(_parse_yaml_value reader_context_path "$config_file")"
  SEARCH_PROVIDER="$(_parse_yaml_value search_provider "$config_file")"
  QMD_COLLECTION="$(_parse_yaml_value qmd_collection "$config_file")"
  DEFAULT_MODEL="$(_parse_yaml_value default_model "$config_file")"
  DEFAULT_LANGUAGE="$(_parse_yaml_value default_language "$config_file")"
  WHISPER_MODEL="$(_parse_yaml_value whisper_model "$config_file")"
  AUTO_DETECT_ORIGIN="$(_parse_yaml_value auto_detect_origin "$config_file")"
  ARCHIVE_DIR="$(_parse_yaml_value archive_dir "$config_file")"

  # Apply defaults
  VAULT_PATH="${VAULT_PATH:-}"
  if [[ -z "$VAULT_PATH" ]]; then
    echo "ERROR: vault_path not set in config. Run setup.sh first." >&2
    return 1
  fi
  READER_CONTEXT_PATH="${READER_CONTEXT_PATH:-$VAULT_PATH/reader-context.md}"
  SEARCH_PROVIDER="${SEARCH_PROVIDER:-grep}"
  QMD_COLLECTION="${QMD_COLLECTION:-mnemon}"
  DEFAULT_MODEL="${DEFAULT_MODEL:-sonnet}"
  DEFAULT_LANGUAGE="${DEFAULT_LANGUAGE:-en}"
  WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
  AUTO_DETECT_ORIGIN="${AUTO_DETECT_ORIGIN:-true}"
  ARCHIVE_DIR="${ARCHIVE_DIR:-}"

  export MNEMON_ROOT VAULT_PATH READER_CONTEXT_PATH SEARCH_PROVIDER
  export QMD_COLLECTION DEFAULT_MODEL DEFAULT_LANGUAGE WHISPER_MODEL AUTO_DETECT_ORIGIN ARCHIVE_DIR
}
