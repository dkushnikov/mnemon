#!/usr/bin/env bash
# mnemon-config.sh — Config loader for Mnemon
# Source this file, then call load_config [path].
#
# Resolution order (first hit wins; an explicit selection always outranks an
# inherited one):
#   1. --config <path>                 (gateway/dispatcher flag; the path argument)
#   2. --instance <name>               (dispatcher/gateway flag; the flag exports
#                                       MNEMON_INSTANCE and drops an inherited MNEMON_CONFIG)
#   3. $MNEMON_CONFIG                  a file path
#   4. $MNEMON_INSTANCE                $MNEMON_CONFIG_DIR/instances/<name>.yaml
#   5. ./mnemon.yaml                   current directory
#   6. $MNEMON_CONFIG_DIR/default      text file, first line = default instance
#   7. legacy (warns): $MNEMON_CONFIG_DIR/mnemon.yaml, then $MNEMON_ROOT/mnemon.yaml
#   8. error listing every location tried
#
# Conflicts fail closed (exit 2): --config together with --instance on one
# command line; $MNEMON_CONFIG and $MNEMON_INSTANCE both set unless they are
# consistent — (a) they name the same file, (b) the file at MNEMON_CONFIG
# declares `instance: $MNEMON_INSTANCE`, or (c) MNEMON_INSTANCE=adhoc with
# MNEMON_CONFIG set. MNEMON_INSTANCE=adhoc without MNEMON_CONFIG, and an
# instance name without a file, are errors (exit 2), never a silent fallback.
#
# On success load_config exports MNEMON_CONFIG=<resolved file> and
# MNEMON_INSTANCE=<resolved name> together, so a child process that resolves
# again from the environment lands on the same file: the dispatcher decides
# once, children never re-decide.
#
# MNEMON_CONFIG_DIR defaults to ${XDG_CONFIG_HOME:-$HOME/.config}/mnemon.
#
# Exports: MNEMON_ROOT, MNEMON_CONFIG, MNEMON_CONFIG_FILE, MNEMON_CONFIG_SOURCE
#   (which selector won: --config, --instance, MNEMON_CONFIG, MNEMON_INSTANCE,
#   ./mnemon.yaml, default, legacy), MNEMON_INSTANCE, MNEMON_STATE_DIR,
#   VAULT_PATH, READER_CONTEXT_PATH, CHARTER_PATH, SEARCH_PROVIDER, QMD_COLLECTION,
#   DEFAULT_MODEL, DEFAULT_LANGUAGE, WHISPER_MODEL, AUTO_DETECT_ORIGIN, ARCHIVE_DIR,
#   WRITER_HOST. MNEMON_SELECTED_BY (set by the dispatcher/gateway when a flag
#   chose the instance) only labels MNEMON_CONFIG_SOURCE.
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

# Parse a single flat YAML key: `key: value`. A value in double or single
# quotes is taken verbatim up to the closing quote (a `#` inside stays, text
# after the quote is ignored); an unquoted value loses a trailing ` # comment`
# and surrounding whitespace. A leading tilde is resolved against $HOME.
_parse_yaml_value() {
  local key="$1" file="$2"
  local val
  # Use awk with exact string match (not regex) to avoid BRE metacharacter issues
  val=$(awk -v k="$key" 'BEGIN { prefix = k ": " } substr($0, 1, length(prefix)) == prefix { print substr($0, length(prefix) + 1); exit }' "$file" 2>/dev/null)
  val=$(printf '%s' "$val" | sed 's/^[[:space:]]*//')
  case "$val" in
    \"*) val="${val#\"}"; val="${val%%\"*}" ;;
    \'*) val="${val#\'}"; val="${val%%\'*}" ;;
    \#*) val="" ;;
    *)   val=$(printf '%s' "$val" | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//') ;;
  esac
  val="$(_mnemon_expand_tilde "$val")"
  printf '%s\n' "$val"
}

# Expand a leading `~` or `~/` to $HOME; `~user/...` is left alone.
_mnemon_expand_tilde() {
  case "$1" in
    "~")   printf '%s' "$HOME" ;;
    "~/"*) printf '%s%s' "$HOME" "${1#\~}" ;;
    *)     printf '%s' "$1" ;;
  esac
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

# Physical absolute form of a file path (symlinked directories resolved; the
# file itself need not exist). Used to compare two selectors.
_mnemon_phys_file() {
  local f="$1" d
  d="$(cd "$(dirname "$f")" 2>/dev/null && pwd -P)" || { printf '%s' "$f"; return 0; }
  printf '%s/%s' "$d" "$(basename "$f")"
}

# `instance:` key of a yaml file; an unrendered {{instance}} placeholder counts as unset.
_mnemon_declared_instance() {
  local v
  v="$(_parse_yaml_value instance "$1")"
  case "$v" in *"{{"*) v="" ;; esac
  printf '%s' "$v"
}

# MNEMON_CONFIG=$1 and MNEMON_INSTANCE=$2 are consistent when they name the
# same file (a), the file declares `instance: $2` (b), or $2 is adhoc (c).
_mnemon_env_consistent() {
  local cfg="$1" inst="$2" cfg_dir="$3"
  if [[ "$inst" == "$MNEMON_ADHOC_INSTANCE" ]]; then return 0; fi
  if [[ "$(_mnemon_phys_file "$cfg")" == "$(_mnemon_phys_file "$cfg_dir/instances/$inst.yaml")" ]]; then return 0; fi
  if [[ -f "$cfg" && "$(_mnemon_declared_instance "$cfg")" == "$inst" ]]; then return 0; fi
  return 1
}

load_config() {
  local config_file="${1:-}"
  local cfg_dir resolved_instance="" default_name="" source=""
  local env_config="${MNEMON_CONFIG:-}" env_instance="${MNEMON_INSTANCE:-}"
  cfg_dir="$(mnemon_config_dir)"

  if [[ -n "$config_file" ]]; then
    # 1. explicit path argument (gateway --config, lib_config.load_config(path))
    source="--config"
  elif [[ -n "$env_config" && -n "$env_instance" ]]; then
    # 3+4. both inherited: consistent pair → the file, with the name; else refuse
    if ! _mnemon_env_consistent "$env_config" "$env_instance" "$cfg_dir"; then
      {
        echo "ERROR: conflicting selectors in the environment (MNEMON_CONFIG=$env_config, MNEMON_INSTANCE=$env_instance)"
        echo "  they select different configs; unset one, or choose explicitly: mnemon --instance <name> … | mnemon --config <path> …"
      } >&2
      return 2
    fi
    config_file="$env_config"
    if [[ "$env_instance" != "$MNEMON_ADHOC_INSTANCE" ]]; then resolved_instance="$env_instance"; fi
    source="${MNEMON_CONFIG_SOURCE:-${MNEMON_SELECTED_BY:-MNEMON_CONFIG}}"
  elif [[ -n "$env_config" ]]; then
    # 3. $MNEMON_CONFIG
    config_file="$env_config"
    source="${MNEMON_SELECTED_BY:-MNEMON_CONFIG}"
  elif [[ -n "$env_instance" ]]; then
    # 4. $MNEMON_INSTANCE (adhoc names no file: only valid next to MNEMON_CONFIG)
    if [[ "$env_instance" == "$MNEMON_ADHOC_INSTANCE" ]]; then
      echo "ERROR: MNEMON_INSTANCE=$MNEMON_ADHOC_INSTANCE names no instance file; it is only valid together with MNEMON_CONFIG=<path> (or pass --config <path>)" >&2
      return 2
    fi
    config_file="$cfg_dir/instances/$env_instance.yaml"
    resolved_instance="$env_instance"
    source="${MNEMON_SELECTED_BY:-MNEMON_INSTANCE}"
    if [[ ! -f "$config_file" ]]; then
      echo "ERROR: instance '$env_instance' has no config at $config_file" >&2
      echo "  create it with: mnemon init $env_instance --vault <path>  (or inspect: mnemon config list)" >&2
      return 2
    fi
  elif [[ -f "./mnemon.yaml" ]]; then
    # 5. ./mnemon.yaml
    config_file="./mnemon.yaml"
    source="./mnemon.yaml"
  elif default_name="$(_mnemon_default_instance "$cfg_dir/default")"; then
    # 6. default instance
    config_file="$cfg_dir/instances/$default_name.yaml"
    resolved_instance="$default_name"
    source="default"
    if [[ ! -f "$config_file" ]]; then
      echo "ERROR: default instance '$default_name' (from $cfg_dir/default) has no config at $config_file" >&2
      echo "  create it with: mnemon init $default_name --vault <path>  (or inspect: mnemon config list)" >&2
      return 2
    fi
  elif [[ -f "$cfg_dir/mnemon.yaml" ]]; then
    # 7. legacy
    config_file="$cfg_dir/mnemon.yaml"
    source="legacy"
    _mnemon_legacy_warning "$config_file"
  elif [[ -f "$MNEMON_ROOT/mnemon.yaml" ]]; then
    config_file="$MNEMON_ROOT/mnemon.yaml"
    source="legacy"
    _mnemon_legacy_warning "$config_file"
  else
    {
      echo "ERROR: no Mnemon config found. Tried, in order:"
      echo "  1. --config <path>             (not given)"
      echo "  2. --instance <name>           (not given)"
      echo "  3. \$MNEMON_CONFIG               (unset)"
      echo "  4. \$MNEMON_INSTANCE             (unset) -> $cfg_dir/instances/<name>.yaml"
      echo "  5. ./mnemon.yaml                ($(pwd)/mnemon.yaml)"
      echo "  6. $cfg_dir/default            (no default instance)"
      echo "  7. $cfg_dir/mnemon.yaml        (legacy)"
      echo "  8. $MNEMON_ROOT/mnemon.yaml    (legacy)"
      echo "Create an instance with: mnemon init <name> --vault <vault>"
    } >&2
    return 1
  fi

  if [[ ! -f "$config_file" ]]; then
    echo "ERROR: Config not found: $config_file" >&2
    return 1
  fi

  MNEMON_CONFIG_FILE="$(_mnemon_abs_file "$config_file")"
  MNEMON_CONFIG_SOURCE="$source"

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
  # `instance:` key; otherwise adhoc.
  local instance_key
  instance_key="$(_mnemon_declared_instance "$config_file")"
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

  # The pair a child re-resolves from — consistent by construction.
  MNEMON_CONFIG="$MNEMON_CONFIG_FILE"

  export MNEMON_ROOT MNEMON_CONFIG MNEMON_CONFIG_FILE MNEMON_CONFIG_SOURCE MNEMON_INSTANCE MNEMON_STATE_DIR
  export VAULT_PATH READER_CONTEXT_PATH CHARTER_PATH SEARCH_PROVIDER
  export QMD_COLLECTION DEFAULT_MODEL DEFAULT_LANGUAGE WHISPER_MODEL AUTO_DETECT_ORIGIN ARCHIVE_DIR
  export WRITER_HOST
}
