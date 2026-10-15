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
#   WRITER_HOST, EXTRACT_TIMEOUT, EXTRACT_INLINE_MAX_BYTES, EXTRACT_MAX_USD and
#   MNEMON_VAULT_STATE_DIR. MNEMON_SELECTED_BY (set by the dispatcher/gateway when
#   a flag chose the instance) only labels MNEMON_CONFIG_SOURCE.
#
# Two state directories (docs/specs/2026-10-15-capture-contracts.md D6, §7):
#   MNEMON_STATE_DIR        instance-only state — `state_dir:` or
#                           $XDG_STATE_HOME/mnemon/<instance>; a config loaded by
#                           path without an instance name gets its own namespace
#                           $XDG_STATE_HOME/mnemon/adhoc-<sha256(config path)[:8]>
#   MNEMON_VAULT_STATE_DIR  the capture ledger of the physical vault — receipts,
#                           attempt dirs, the state raw store, ingest state and the
#                           vault lock: $XDG_STATE_HOME/mnemon/vaults/<sha256(vault
#                           path resolved with pwd -P, no trailing slash)[:16]>.
#                           Shared by every config that points at the vault. The
#                           loader only computes it (doctor is observational); the
#                           writers create it with mode 0700.
#
# bash 3.2 compatible: no associative arrays, no ${var,,}, no mapfile.

# Auto-detect MNEMON_ROOT from this script's location
MNEMON_ROOT="${MNEMON_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Instance name reported for a config loaded by file path when the yaml has no
# `instance:` key. Reserved: `mnemon init adhoc` is refused and
# MNEMON_INSTANCE=adhoc never selects an instance file, so an exported adhoc
# can never break a child process that resolves config again.
MNEMON_ADHOC_INSTANCE="adhoc"

# --- Engine maintenance lock (A3) -------------------------------------------
# `mnemon release` holds ${XDG_STATE_HOME:-$HOME/.local/state}/mnemon/engine-release.lock
# (lines: pid=, timestamp= (epoch), date=, target=) while it moves the installed
# engine. Every entry point calls mnemon_engine_lock_guard first: a lock younger
# than MNEMON_ENGINE_LOCK_MAX_AGE makes it exit 75 (EX_TEMPFAIL); an older one is
# stale — reported on stderr and ignored. The release's own smoke children carry
# MNEMON_ENGINE_LOCK_OWNER=<pid of the lock> and pass.
MNEMON_ENGINE_LOCK_MAX_AGE=1800

mnemon_engine_lock_file() {
  printf '%s/mnemon/engine-release.lock' "$(mnemon_state_home)"
}

# Value of a `key=` line in the lock file (empty when absent).
_mnemon_lock_field() {
  awk -F= -v k="$1" '$1 == k { print substr($0, length(k) + 2); exit }' "$2" 2>/dev/null
}

# Age of the lock in seconds; return 1 when there is no lock.
mnemon_engine_lock_age() {
  local f ts now
  f="$(mnemon_engine_lock_file)"
  [[ -f "$f" ]] || return 1
  ts="$(_mnemon_lock_field timestamp "$f")"
  now="$(date +%s)"
  if [[ "$ts" =~ ^[0-9]+$ ]]; then
    printf '%s' $((now - ts))
  elif [[ -n "$(find "$f" -mmin +$((MNEMON_ENGINE_LOCK_MAX_AGE / 60)) 2>/dev/null)" ]]; then
    printf '%s' $((MNEMON_ENGINE_LOCK_MAX_AGE + 1))
  else
    printf '0'
  fi
}

# 0 = no lock · 1 = fresh lock (update in progress) · 2 = stale lock
mnemon_engine_lock_state() {
  local age
  age="$(mnemon_engine_lock_age)" || return 0
  if [[ "$age" -ge "$MNEMON_ENGINE_LOCK_MAX_AGE" ]]; then return 2; fi
  return 1
}

# Entry-point guard (bin/mnemon, bin/knowledge-gateway.sh).
mnemon_engine_lock_guard() {
  local f state=0 owner
  f="$(mnemon_engine_lock_file)"
  mnemon_engine_lock_state || state=$?
  case "$state" in
    0) return 0 ;;
    2) echo "mnemon: ignoring stale engine lock $f (older than $((MNEMON_ENGINE_LOCK_MAX_AGE / 60)) min; remove it if no release is running)" >&2
       return 0 ;;
  esac
  owner="$(_mnemon_lock_field pid "$f")"
  if [[ -n "${MNEMON_ENGINE_LOCK_OWNER:-}" && "$MNEMON_ENGINE_LOCK_OWNER" == "$owner" ]]; then return 0; fi
  echo "mnemon: engine is being updated, retry later (lock $f, pid ${owner:-?}, target $(_mnemon_lock_field target "$f"))" >&2
  exit 75
}

# Directory holding instances/, default and legacy mnemon.yaml.
mnemon_config_dir() {
  printf '%s' "${MNEMON_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/mnemon}"
}

# Base for per-instance state directories.
mnemon_state_home() {
  printf '%s' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

# Body of a double-quoted scalar ($1 = the text after the opening quote): the
# content up to the first unescaped `"`, with \" → " and \\ → \ (the two escapes
# `mnemon init` writes); any other backslash sequence is kept as it is.
_mnemon_unquote_dq() {
  printf '%s' "$1" | awk 'BEGIN { q = "\"" }
    { s = $0; out = ""; i = 1; n = length(s)
      while (i <= n) {
        c = substr(s, i, 1)
        if (c == "\\" && i < n) {
          d = substr(s, i + 1, 1)
          if (d == "\\" || d == q) { out = out d } else { out = out c d }
          i += 2; continue
        }
        if (c == q) break
        out = out c; i++
      }
      printf "%s", out }'
}

# Parse a single flat YAML key: `key: value`. A double-quoted value is taken up
# to the closing quote (a `#` inside stays, text after the quote is ignored) with
# \" and \\ decoded — the form `mnemon init` writes; a single-quoted value is
# taken verbatim up to the closing quote; an unquoted value loses a trailing
# ` # comment` and surrounding whitespace. A leading tilde is resolved against $HOME.
_parse_yaml_value() {
  local key="$1" file="$2"
  local val
  # Use awk with exact string match (not regex) to avoid BRE metacharacter issues
  val=$(awk -v k="$key" 'BEGIN { prefix = k ": " } substr($0, 1, length(prefix)) == prefix { print substr($0, length(prefix) + 1); exit }' "$file" 2>/dev/null)
  val=$(printf '%s' "$val" | sed 's/^[[:space:]]*//')
  case "$val" in
    \"*) val="$(_mnemon_unquote_dq "${val#\"}")" ;;
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

# Literal frontmatter scalar of an artifact (source.md / extract.md), spec §4.4:
# `_mnemon_yaml_literal <key> <file>` prints the value of the first top-level
# `key: value` line inside the frontmatter block (between the opening `---` and the
# closing `---`; nested keys are indented and never match; the body is never read).
# A double-quoted value decodes exactly `\"` → `"` and `\\` → `\` — the two escapes
# the gateway writes — and nothing else; a single-quoted value is taken verbatim up
# to its closing quote; an unquoted value loses a trailing ` # comment` and
# surrounding whitespace. No tilde expansion: a title `~` or `~/Guide` is literal.
# (`_parse_yaml_value` is the configuration reader and keeps expanding `~` for
# paths; it must never be used on artifact fields.)
_mnemon_yaml_literal() {
  local key="$1" file="$2"
  local val
  val=$(awk -v k="$key" 'BEGIN { prefix = k ": "; bare = k ":" }
    NR == 1 { if ($0 == "---") { fm = 1; next } else { exit } }
    fm && ($0 == "---" || $0 == "...") { exit }
    fm && substr($0, 1, length(prefix)) == prefix { print substr($0, length(prefix) + 1); exit }
    fm && $0 == bare { print ""; exit }' "$file" 2>/dev/null)
  val=$(printf '%s' "$val" | sed 's/^[[:space:]]*//')
  case "$val" in
    \"*) val="$(_mnemon_unquote_dq "${val#\"}")" ;;
    \'*) val="${val#\'}"; val="${val%%\'*}" ;;
    \#*) val="" ;;
    *)   val=$(printf '%s' "$val" | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//') ;;
  esac
  printf '%s\n' "$val"
}

# Capture ledger directory of a physical vault (D6): the vault path resolved with
# `pwd -P` when it exists (else as given, absolute), trailing slashes stripped,
# hashed. Printed, never created.
mnemon_vault_state_dir() {
  local vault="$1" phys
  phys="$(cd "$vault" 2>/dev/null && pwd -P)" || phys="$(_mnemon_abs_file "$vault")"
  while [[ "${#phys}" -gt 1 && "$phys" == */ ]]; do phys="${phys%/}"; done
  printf '%s/mnemon/vaults/%s' "$(mnemon_state_home)" "$(printf '%s' "$phys" | shasum -a 256 | cut -c1-16)"
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
  EXTRACT_TIMEOUT="$(_parse_yaml_value extract_timeout "$config_file")"
  EXTRACT_INLINE_MAX_BYTES="$(_parse_yaml_value extract_inline_max_bytes "$config_file")"
  EXTRACT_MAX_USD="$(_parse_yaml_value extract_max_usd "$config_file")"

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
  # Instance-only state: a by-path config without an instance name gets a
  # namespace of its own (spec §7) so two ad-hoc configs never share it; the
  # capture ledger is per vault (MNEMON_VAULT_STATE_DIR) and shared on purpose.
  if [[ -z "$MNEMON_STATE_DIR" ]]; then
    if [[ "$MNEMON_INSTANCE" == "$MNEMON_ADHOC_INSTANCE" ]]; then
      MNEMON_STATE_DIR="$(mnemon_state_home)/mnemon/adhoc-$(printf '%s' "$MNEMON_CONFIG_FILE" | shasum -a 256 | cut -c1-8)"
    else
      MNEMON_STATE_DIR="$(mnemon_state_home)/mnemon/$MNEMON_INSTANCE"
    fi
  fi
  MNEMON_VAULT_STATE_DIR="$(mnemon_vault_state_dir "$VAULT_PATH")"
  CHARTER_PATH="${CHARTER_PATH:-$VAULT_PATH/_meta/charter.md}"
  WRITER_HOST="${WRITER_HOST:-}"
  EXTRACT_TIMEOUT="${EXTRACT_TIMEOUT:-900}"
  EXTRACT_INLINE_MAX_BYTES="${EXTRACT_INLINE_MAX_BYTES:-400000}"
  EXTRACT_MAX_USD="${EXTRACT_MAX_USD:-}"

  # The pair a child re-resolves from — consistent by construction.
  MNEMON_CONFIG="$MNEMON_CONFIG_FILE"

  export MNEMON_ROOT MNEMON_CONFIG MNEMON_CONFIG_FILE MNEMON_CONFIG_SOURCE MNEMON_INSTANCE MNEMON_STATE_DIR
  export MNEMON_VAULT_STATE_DIR
  export VAULT_PATH READER_CONTEXT_PATH CHARTER_PATH SEARCH_PROVIDER
  export QMD_COLLECTION DEFAULT_MODEL DEFAULT_LANGUAGE WHISPER_MODEL AUTO_DETECT_ORIGIN ARCHIVE_DIR
  export WRITER_HOST EXTRACT_TIMEOUT EXTRACT_INLINE_MAX_BYTES EXTRACT_MAX_USD
}
