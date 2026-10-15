#!/usr/bin/env bash
# mnemon-config.sh — Config loader for Mnemon
# Source this file, then call load_config [path].
#
# Resolution order (first hit wins):
#   1. explicit path argument          (gateway --config / mnemon --config)
#   2. $MNEMON_CONFIG                  a file path
#   3. $MNEMON_INSTANCE                $MNEMON_CONFIG_DIR/instances/<name>.yaml
#   4. ./mnemon.yaml                   current directory
#   5. $MNEMON_CONFIG_DIR/default      text file, first line = default instance
#   6. legacy (warns): $MNEMON_CONFIG_DIR/mnemon.yaml, then $MNEMON_ROOT/mnemon.yaml
#   7. error listing every location tried
#
# MNEMON_CONFIG_DIR defaults to ${XDG_CONFIG_HOME:-$HOME/.config}/mnemon.
#
# Exports: MNEMON_ROOT, MNEMON_CONFIG_FILE, MNEMON_INSTANCE, MNEMON_STATE_DIR,
#   VAULT_PATH, READER_CONTEXT_PATH, CHARTER_PATH, SEARCH_PROVIDER, QMD_COLLECTION,
#   DEFAULT_MODEL, DEFAULT_LANGUAGE, WHISPER_MODEL, AUTO_DETECT_ORIGIN, ARCHIVE_DIR,
#   WRITER_HOST
#
# bash 3.2 compatible: no associative arrays, no ${var,,}, no mapfile.

# Auto-detect MNEMON_ROOT from this script's location
MNEMON_ROOT="${MNEMON_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Instance name reported for a config loaded by file path when the yaml has no
# `instance:` key. Reserved: `mnemon init adhoc` is refused and
# MNEMON_INSTANCE=adhoc never selects an instance file, so an exported adhoc
# can never break a child process that resolves config again.
MNEMON_ADHOC_INSTANCE="adhoc"

# Directory holding instances/, default and legacy mnemon.yaml.
mnemon_config_dir() {
  printf '%s' "${MNEMON_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/mnemon}"
}

# Base for per-instance state directories.
mnemon_state_home() {
  printf '%s' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

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

# First non-empty, non-comment line of a `default` file (the default instance).
# Returns 1 when the file is missing or names nothing.
_mnemon_default_instance() {
  local f="$1" name
  [[ -f "$f" ]] || return 1
  name=$(awk 'NF && $1 !~ /^#/ { sub(/^[ \t]+/, ""); sub(/[ \t]+$/, ""); print; exit }' "$f" 2>/dev/null)
  [[ -n "$name" ]] || return 1
  printf '%s' "$name"
}

# Absolute form of a file path (directory part resolved; the file need not exist).
_mnemon_abs_file() {
  local f="$1" d
  case "$f" in
    /*) printf '%s' "$f" ;;
    *)
      d="$(cd "$(dirname "$f")" 2>/dev/null && pwd)" || { printf '%s' "$f"; return 0; }
      printf '%s/%s' "$d" "$(basename "$f")" ;;
  esac
}

_mnemon_legacy_warning() {
  echo "mnemon: legacy config $1; create an instance with: mnemon init <name> --vault <vault>" >&2
}

load_config() {
  local config_file="${1:-}"
  local cfg_dir resolved_instance="" default_name=""
  cfg_dir="$(mnemon_config_dir)"

  if [[ -z "$config_file" ]]; then
    if [[ -n "${MNEMON_CONFIG:-}" ]]; then
      config_file="$MNEMON_CONFIG"
    elif [[ -n "${MNEMON_INSTANCE:-}" && "$MNEMON_INSTANCE" != "$MNEMON_ADHOC_INSTANCE" ]]; then
      config_file="$cfg_dir/instances/$MNEMON_INSTANCE.yaml"
      resolved_instance="$MNEMON_INSTANCE"
      if [[ ! -f "$config_file" ]]; then
        echo "ERROR: instance '$MNEMON_INSTANCE' has no config at $config_file" >&2
        echo "  create it with: mnemon init $MNEMON_INSTANCE --vault <path>" >&2
        return 1
      fi
    elif [[ -f "./mnemon.yaml" ]]; then
      config_file="./mnemon.yaml"
    elif default_name="$(_mnemon_default_instance "$cfg_dir/default")"; then
      config_file="$cfg_dir/instances/$default_name.yaml"
      resolved_instance="$default_name"
      if [[ ! -f "$config_file" ]]; then
        echo "ERROR: default instance '$default_name' (from $cfg_dir/default) has no config at $config_file" >&2
        echo "  create it with: mnemon init $default_name --vault <path>  (or inspect: mnemon config list)" >&2
        return 1
      fi
    elif [[ -f "$cfg_dir/mnemon.yaml" ]]; then
      config_file="$cfg_dir/mnemon.yaml"
      _mnemon_legacy_warning "$config_file"
    elif [[ -f "$MNEMON_ROOT/mnemon.yaml" ]]; then
      config_file="$MNEMON_ROOT/mnemon.yaml"
      _mnemon_legacy_warning "$config_file"
    else
      {
        echo "ERROR: no Mnemon config found. Tried, in order:"
        echo "  1. --config <path>             (not given)"
        echo "  2. \$MNEMON_CONFIG               (unset)"
        echo "  3. \$MNEMON_INSTANCE             (${MNEMON_INSTANCE:-unset}) -> $cfg_dir/instances/<name>.yaml"
        echo "  4. ./mnemon.yaml                ($(pwd)/mnemon.yaml)"
        echo "  5. $cfg_dir/default            (no default instance)"
        echo "  6. $cfg_dir/mnemon.yaml        (legacy)"
        echo "  7. $MNEMON_ROOT/mnemon.yaml    (legacy)"
        echo "Create an instance with: mnemon init <name> --vault <vault>"
      } >&2
      return 1
    fi
  fi

  if [[ ! -f "$config_file" ]]; then
    echo "ERROR: Config not found: $config_file" >&2
    return 1
  fi

  MNEMON_CONFIG_FILE="$(_mnemon_abs_file "$config_file")"

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
  MNEMON_STATE_DIR="$(_parse_yaml_value state_dir "$config_file")"
  CHARTER_PATH="$(_parse_yaml_value charter_path "$config_file")"
  WRITER_HOST="$(_parse_yaml_value writer_host "$config_file")"

  # Instance name: the name that selected the file wins; otherwise the yaml's
  # `instance:` key; otherwise adhoc. An unrendered `{{instance}}` placeholder
  # (config generated from the template by hand) counts as unset.
  local instance_key
  instance_key="$(_parse_yaml_value instance "$config_file")"
  case "$instance_key" in *"{{"*) instance_key="" ;; esac
  if [[ -n "$resolved_instance" ]]; then
    MNEMON_INSTANCE="$resolved_instance"
  elif [[ -n "$instance_key" ]]; then
    MNEMON_INSTANCE="$instance_key"
  else
    MNEMON_INSTANCE="$MNEMON_ADHOC_INSTANCE"
  fi

  # Apply defaults
  VAULT_PATH="${VAULT_PATH:-}"
  if [[ -z "$VAULT_PATH" ]]; then
    echo "ERROR: vault_path not set in $MNEMON_CONFIG_FILE. Run: mnemon init <name> --vault <path>" >&2
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
  MNEMON_STATE_DIR="${MNEMON_STATE_DIR:-$(mnemon_state_home)/mnemon/$MNEMON_INSTANCE}"
  CHARTER_PATH="${CHARTER_PATH:-$VAULT_PATH/_meta/charter.md}"
  WRITER_HOST="${WRITER_HOST:-}"

  export MNEMON_ROOT MNEMON_CONFIG_FILE MNEMON_INSTANCE MNEMON_STATE_DIR
  export VAULT_PATH READER_CONTEXT_PATH CHARTER_PATH SEARCH_PROVIDER
  export QMD_COLLECTION DEFAULT_MODEL DEFAULT_LANGUAGE WHISPER_MODEL AUTO_DETECT_ORIGIN ARCHIVE_DIR
  export WRITER_HOST
}
