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
# Resolve once per command (B7): beside the values load_config exports
# MNEMON_RESOLVED, a digest (mnemon-resolution/1) over the loader itself (the
# sha256 of this file), the config file's bytes, HOME, XDG_STATE_HOME,
# XDG_CONFIG_HOME, MNEMON_CONFIG_DIR and every exported NAME=VALUE in
# MNEMON_EXPORT_NAMES order. A later load_config without a path argument —
# the gateway the dispatcher execs, the gateway re-executed under the vault
# lock, a Python tool through bin/lib_config.py — reuses the environment as it
# is when every name is set and the digest still matches; anything that moved
# (a selector flag, another instance, an edited config file, a changed or
# missing export, another engine's loader) fails the check and the file is
# loaded again. A path argument is an explicit selection: always loaded.
#
# MNEMON_CONFIG_DIR defaults to ${XDG_CONFIG_HOME:-$HOME/.config}/mnemon.
#
# Exports: MNEMON_ROOT, MNEMON_CONFIG, MNEMON_CONFIG_FILE, MNEMON_CONFIG_SOURCE
#   (which selector won: --config, --instance, MNEMON_CONFIG, MNEMON_INSTANCE,
#   ./mnemon.yaml, default, legacy), MNEMON_INSTANCE, MNEMON_STATE_DIR,
#   VAULT_PATH, READER_CONTEXT_PATH, CHARTER_PATH, SEARCH_PROVIDER, QMD_COLLECTION,
#   DEFAULT_MODEL, DEFAULT_LANGUAGE, WHISPER_MODEL, AUTO_DETECT_ORIGIN, ARCHIVE_DIR,
#   WRITER_HOST, EXTRACT_TIMEOUT, EXTRACT_INLINE_MAX_BYTES, EXTRACT_MAX_USD and
#   MNEMON_VAULT_STATE_DIR, plus the capture-layer keys of MNEMON_CONFIG_KEYS below
#   (docs/specs/2026-10-15-capture-layer.md §5, the image origin's §21.8) in upper case with their defaults
#   applied, and the fetch_timeout split FETCH_CONNECT_TIMEOUT / FETCH_READ_TIMEOUT.
#   MNEMON_SELECTED_BY (set by the dispatcher/gateway when a flag chose the
#   instance) only labels MNEMON_CONFIG_SOURCE.
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

# This file, for the loader signature of the resolution digest (absolute, without a subprocess).
_MNEMON_CONFIG_SH="${BASH_SOURCE[0]}"
case "$_MNEMON_CONFIG_SH" in /*) ;; *) _MNEMON_CONFIG_SH="$PWD/$_MNEMON_CONFIG_SH" ;; esac

# The resolved configuration: every name load_config exports (MNEMON_ROOT aside — each engine
# derives its own), in one order. bin/lib_config.py EXPORTS is the same list in the same order;
# tests/test_config.sh asserts it. The resolution digest is computed over these names.
MNEMON_EXPORT_NAMES="MNEMON_CONFIG MNEMON_CONFIG_FILE MNEMON_CONFIG_SOURCE MNEMON_INSTANCE MNEMON_STATE_DIR
VAULT_PATH READER_CONTEXT_PATH CHARTER_PATH SEARCH_PROVIDER QMD_COLLECTION DEFAULT_MODEL DEFAULT_LANGUAGE
WHISPER_MODEL AUTO_DETECT_ORIGIN ARCHIVE_DIR WRITER_HOST MNEMON_VAULT_STATE_DIR
EXTRACT_TIMEOUT EXTRACT_INLINE_MAX_BYTES EXTRACT_MAX_USD EXTERNAL_FETCH EXTERNAL_FETCHERS FETCH_USER_AGENT
FETCH_TIMEOUT FETCH_CONNECT_TIMEOUT FETCH_READ_TIMEOUT FETCH_MAX_BYTES RENDER_TIMEOUT RENDER_MAX_BYTES
ACQUIRE_TIMEOUT CONVERT_TIMEOUT TRANSCRIBE_TIMEOUT SITE_MAX_PAGES SITE_TIMEOUT THIN_WORDS EXTRACT_MAX_PARTS
OCR_LANGUAGES IMAGE_READER_MODEL IMAGE_READER_EFFORT IMAGE_READER_MAX_USD IMAGE_TIMEOUT IMAGE_MAX_BYTES
IMAGE_MAX_PIXELS IMAGE_MAX_EDGE IMAGE_MIN_WITNESS_SHARE IMAGE_MISS_ALLOWANCE
DOCTOR_THIN_WORDS DOCTOR_STUB_WORDS DOCTOR_ARXIV_WORDS DOCTOR_RATING_HI_SHARE
DOCTOR_WIKI_PAGES_PER_COLLECTION DOCTOR_WIKI_WORDS
RESEARCH_LOG_PATH RESEARCH_PACKAGE_MAX_BYTES RESEARCH_TIMEOUT RESEARCH_MAX_USD RESEARCH_RECUSAL_TAGS"
# The environment inputs that shape the resolved values without being exported by the loader.
MNEMON_RESOLUTION_INPUTS="HOME XDG_STATE_HOME XDG_CONFIG_HOME MNEMON_CONFIG_DIR"

# --- Scalar keys with defaults (capture contracts §7, capture layer §5, §21.8) -
# One table, `key|VAR|type|default` per line: the loader parses, validates and
# exports every row; bin/lib_config.py carries the same table (CONFIG_KEYS) and
# bin/mnemon prints it (`mnemon config keys`) — tests/test_config.sh asserts the
# three agree. Types: str · enum:a,b · int (a non-negative integer; a fraction is
# refused) · num (a non-negative decimal) · langs (one to eight tesseract language
# codes joined by `+`, none twice; capture layer §21.8) · level (an effort level:
# low, medium, high, xhigh, max). A non-numeric, negative, non-finite or
# fractional-where-integer value is refused at config load (exit 2), and so is a
# value outside its type or a key's range (`image_min_witness_share`: 0 to 100);
# an empty value takes the default (`extract_max_usd` has none: unset means no cap).
MNEMON_CONFIG_KEYS='extract_timeout|EXTRACT_TIMEOUT|num|900
extract_inline_max_bytes|EXTRACT_INLINE_MAX_BYTES|int|400000
extract_max_usd|EXTRACT_MAX_USD|num|
external_fetch|EXTERNAL_FETCH|enum:off,auto|off
external_fetchers|EXTERNAL_FETCHERS|str|firecrawl,jina
fetch_user_agent|FETCH_USER_AGENT|str|Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36
fetch_timeout|FETCH_TIMEOUT|num|60
fetch_max_bytes|FETCH_MAX_BYTES|int|20000000
render_timeout|RENDER_TIMEOUT|num|90
render_max_bytes|RENDER_MAX_BYTES|int|20000000
acquire_timeout|ACQUIRE_TIMEOUT|num|300
convert_timeout|CONVERT_TIMEOUT|num|120
transcribe_timeout|TRANSCRIBE_TIMEOUT|num|3600
site_max_pages|SITE_MAX_PAGES|int|40
site_timeout|SITE_TIMEOUT|num|900
thin_words|THIN_WORDS|int|60
extract_max_parts|EXTRACT_MAX_PARTS|int|8
ocr_languages|OCR_LANGUAGES|langs|eng
image_reader_model|IMAGE_READER_MODEL|str|haiku
image_reader_effort|IMAGE_READER_EFFORT|level|high
image_reader_max_usd|IMAGE_READER_MAX_USD|num|0.05
image_timeout|IMAGE_TIMEOUT|num|120
image_max_bytes|IMAGE_MAX_BYTES|int|7000000
image_max_pixels|IMAGE_MAX_PIXELS|int|50000000
image_max_edge|IMAGE_MAX_EDGE|int|8000
image_min_witness_share|IMAGE_MIN_WITNESS_SHARE|num|90
image_miss_allowance|IMAGE_MISS_ALLOWANCE|int|20
doctor_thin_words|DOCTOR_THIN_WORDS|int|300
doctor_stub_words|DOCTOR_STUB_WORDS|int|40
doctor_arxiv_words|DOCTOR_ARXIV_WORDS|int|600
doctor_rating_hi_share|DOCTOR_RATING_HI_SHARE|int|20
doctor_wiki_pages_per_collection|DOCTOR_WIKI_PAGES_PER_COLLECTION|int|5
doctor_wiki_words|DOCTOR_WIKI_WORDS|int|3000
research_log_path|RESEARCH_LOG_PATH|str|
research_package_max_bytes|RESEARCH_PACKAGE_MAX_BYTES|int|400000
research_timeout|RESEARCH_TIMEOUT|num|900
research_max_usd|RESEARCH_MAX_USD|num|
research_recusal_tags|RESEARCH_RECUSAL_TAGS|str|agent-identity,agent-subjecthood,atlas-architecture,atlas-identity,harness-vs-weights'

# The upper-case variable of a table key (empty when the key is not in the table).
mnemon_config_var_of() {
  local key var rest
  while IFS='|' read -r key var rest; do
    [[ "$key" == "$1" ]] && { printf '%s' "$var"; return 0; }
  done <<< "$MNEMON_CONFIG_KEYS"
  return 1
}

# `mnemon config keys`: one `key default` line per table row (an empty default prints as `-`).
mnemon_config_keys() {
  local key var type def
  while IFS='|' read -r key var type def; do
    [[ -n "$key" ]] || continue
    printf '%s %s %s\n' "$key" "$type" "${def:--}"
  done <<< "$MNEMON_CONFIG_KEYS"
}

# The type `langs` (capture layer §21.8): ^[a-z]{3}(_[a-z0-9]+)*(\+[a-z]{3}(_[a-z0-9]+)*){0,7}$ —
# one to eight tesseract language codes joined by `+`. The letters are spelled out: a range such
# as [a-z] follows the locale's collation in some C libraries, a list never does.
_MNEMON_LOWER='abcdefghijklmnopqrstuvwxyz'
_MNEMON_LANG_CODE_RE="[$_MNEMON_LOWER]{3}(_[${_MNEMON_LOWER}0123456789]+)*"
_MNEMON_LANGS_RE="^${_MNEMON_LANG_CODE_RE}([+]${_MNEMON_LANG_CODE_RE}){0,7}\$"

# Validate one value against a type of the table: 0 when it fits, else one `ERROR: <key> …` line on
# stderr and 1. <where> closes the message in parentheses: the config file, or the flag that carried
# the value. After the type, a key's own range: `image_min_witness_share` lies in 0–100. Pure bash —
# a full load starts no process (tests/test_config_reader.sh). The gateway checks a value of its own
# with it too (`--ocr-languages`, capture layer §21.8), directly or through mnemon_config_check below.
_mnemon_check_config_value() {   # <key> <value> <type> <where>
  local key="$1" val="$2" type="$3" file="$4"
  case "$type" in
    int) [[ "$val" =~ ^[0-9]+$ ]] || {
           echo "ERROR: $key must be a non-negative integer, got '$val' ($file)" >&2; return 1; } ;;
    num) [[ "$val" =~ ^[0-9]+(\.[0-9]+)?$ ]] || {
           echo "ERROR: $key must be a non-negative number, got '$val' ($file)" >&2; return 1; } ;;
    enum:*)
         local allowed="${type#enum:}" one
         local IFS=,
         for one in $allowed; do [[ "$val" == "$one" ]] && return 0; done
         echo "ERROR: $key must be one of ${allowed//,/|}, got '$val' ($file)" >&2; return 1 ;;
    langs) _mnemon_check_langs "$key" "$val" "$file" || return 1 ;;
    level)
         case "$val" in
           low|medium|high|xhigh|max) ;;
           *) echo "ERROR: $key must be one of low|medium|high|xhigh|max, got '$val' ($file)" >&2; return 1 ;;
         esac ;;
    *)   ;;
  esac
  case "$key" in
    image_min_witness_share)
         _mnemon_num_at_most_100 "$val" || {
           echo "ERROR: $key must lie in 0 to 100 (a percentage), got '$val' ($file)" >&2; return 1; } ;;
  esac
  return 0
}

# A `langs` value: the pattern of the type, then no code twice (the order is the caller's and is
# kept; nothing is sorted). 0 when it fits, else one ERROR line on stderr and 1.
_mnemon_check_langs() {   # <key> <value> <where>
  local key="$1" val="$2" where="$3" rest code seen="+"
  if [[ ! "$val" =~ $_MNEMON_LANGS_RE ]]; then
    echo "ERROR: $key must be one to eight tesseract language codes joined by + (eng, rus+eng, chi_sim), got '$val' ($where)" >&2
    return 1
  fi
  rest="$val+"
  while [[ -n "$rest" ]]; do
    code="${rest%%+*}"; rest="${rest#*+}"
    case "$seen" in
      *"+$code+"*) echo "ERROR: $key names the language code $code twice, got '$val' ($where)" >&2; return 1 ;;
    esac
    seen="$seen$code+"
  done
  return 0
}

# 0 when a value that passed the `num` check (digits, an optional fraction) is at most 100.
_mnemon_num_at_most_100() {   # <value>
  local ip="${1%%.*}" fp=""
  case "$1" in *.*) fp="${1#*.}" ;; esac
  ip="${ip#"${ip%%[!0]*}"}"            # leading zeros dropped; all zeros → empty
  [[ -n "$ip" ]] || return 0
  [[ ${#ip} -le 3 ]] || return 1
  (( 10#$ip < 100 )) && return 0
  (( 10#$ip == 100 )) || return 1
  [[ "$fp" =~ ^0*$ ]]
}

# Validate one value of a table key by the type the table gives it: the answer of
# _mnemon_check_config_value (0 fits; 1 with one ERROR line on stderr); an unknown key is 1 too.
# For a value that does not come from the config file — the gateway's flag (capture layer §21.8):
#   mnemon_config_check ocr_languages "$value" --ocr-languages || exit 2
mnemon_config_check() {   # <key> <value> <where>
  local key var type def
  while IFS='|' read -r key var type def; do
    if [[ -n "$key" && "$key" == "$1" ]]; then
      _mnemon_check_config_value "$1" "$2" "$type" "$3"
      return $?
    fi
  done <<< "$MNEMON_CONFIG_KEYS"
  echo "ERROR: unknown config key '$1' ($3)" >&2
  return 1
}

# Validate and assign every table key of the scanned config file (defaults applied); call it
# after _mnemon_config_scan <file>, which read the values. Returns 2 on the first refused value —
# load_config fails closed on it.
_mnemon_load_table_keys() {   # <file> (named in the error message)
  local file="$1" key var type def val
  while IFS='|' read -r key var type def; do
    [[ -n "$key" ]] || continue
    _mnemon_config_value "$key"; val="$_MNEMON_SCALAR"
    if [[ -z "$val" ]]; then val="$def"; fi
    if [[ -n "$val" ]]; then
      _mnemon_check_config_value "$key" "$val" "$type" "$file" || return 2
    fi
    printf -v "$var" '%s' "$val"
  done <<< "$MNEMON_CONFIG_KEYS"
  _mnemon_fetch_split
  return 0
}

# The fetch_timeout split (§3.2): connect = min(15, T/4), read = T - connect, both as awk's
# printf "%g" prints them. When both parts are integers below 10^6 — an integer T of up to seven
# digits that is 60 or more, or a multiple of 4 below 60 — %g prints them as plain integers in
# every locale, and the split is computed here without a process. Every other T (a fraction, a
# quarter, a long integer) goes through the awk that defines the split: its %g writes the
# locale's decimal separator and, from 10^6 on, an exponent.
_mnemon_fetch_split() {
  local t="$FETCH_TIMEOUT" n=""
  case "$t" in
    ""|*[!0123456789]*) ;;
    *) if [[ ${#t} -le 7 ]]; then n=$((10#$t)); fi ;;
  esac
  if [[ -n "$n" ]]; then
    if [[ $n -ge 60 && $((n - 15)) -lt 1000000 ]]; then
      FETCH_CONNECT_TIMEOUT=15; FETCH_READ_TIMEOUT=$((n - 15))
      return 0
    elif [[ $n -lt 60 && $((n % 4)) -eq 0 ]]; then
      FETCH_CONNECT_TIMEOUT=$((n / 4)); FETCH_READ_TIMEOUT=$((n - n / 4))
      return 0
    fi
  fi
  FETCH_CONNECT_TIMEOUT="$(awk -v t="$FETCH_TIMEOUT" 'BEGIN { c = t / 4; if (c > 15) c = 15; printf "%g", c }')"
  FETCH_READ_TIMEOUT="$(awk -v t="$FETCH_TIMEOUT" -v c="$FETCH_CONNECT_TIMEOUT" 'BEGIN { printf "%g", t - c }')"
  return 0
}

# --- Engine maintenance lock (A3) -------------------------------------------
# `mnemon release` holds ${XDG_STATE_HOME:-$HOME/.local/state}/mnemon/engine-release.lock
# (lines: pid=, timestamp= (epoch), date=, target=) while it moves the installed
# engine. Every entry point calls mnemon_engine_lock_guard first: a lock younger
# than MNEMON_ENGINE_LOCK_MAX_AGE makes it exit 75 (EX_TEMPFAIL); an older one is
# stale — reported on stderr and ignored. The release's own smoke children carry
# MNEMON_ENGINE_LOCK_OWNER=<pid of the lock> and pass.
MNEMON_ENGINE_LOCK_MAX_AGE=1800

# The engine's version string, one describe for `mnemon version` and the extractor.engine stamp of
# extract.md (lib-capture.sh cap_engine): the nearest tag among the release tags (release/YYYY-MM-DD[-n],
# written by `mnemon release`) and the older v* tags — on a released engine that is its release tag —
# else the commit; `-dirty` on an edited checkout. Other tags (a scratch tag, one pulled from a fork)
# never name a version. Empty output outside a repo.
mnemon_engine_describe() {   # <checkout root>
  git -C "$1" describe --tags --always --dirty --match 'release/*' --match 'v*' 2>/dev/null
}

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

# --- The configuration reader: one pass over the file, in this shell ---------
# A key of a flat YAML file is the first line that starts with `key: ` (exact,
# case-sensitive; a later line with the same key is ignored, an indented one
# never matches); its value is the rest of that line, read by _mnemon_scalar
# and then tilde-expanded. load_config reads every key it needs in one pass
# (_mnemon_config_scan); _parse_yaml_value reads one key. Neither starts a
# process for a value of up to 1024 characters: the reading is bash's own, the
# same in bash 3.2 and later (a longer value: _mnemon_scalar_long).
# tests/test_config_reader.sh holds the earlier reader (one awk and two sed per
# key) as its reference and checks both forms against it, key by key.

# The keys load_config reads besides the table (MNEMON_CONFIG_KEYS).
MNEMON_BASE_KEYS="vault_path reader_context_path search_provider qmd_collection default_model default_language
whisper_model auto_detect_origin archive_dir state_dir charter_path writer_host instance"

# The value of one raw scalar (the text after `key: `) in _MNEMON_SCALAR. Leading
# whitespace is dropped. A double-quoted value is taken up to its first unescaped
# `"` (a `#` inside stays, text after the quote is ignored) with \" → " and \\ → \
# — the form `mnemon init` writes; any other backslash sequence is kept as it is.
# A single-quoted value is taken verbatim up to its closing quote. A value that
# starts with `#` is empty. An unquoted value loses a trailing ` # comment` (a `#`
# after whitespace) and trailing whitespace. No tilde expansion here. A raw value
# longer than 1024 characters goes through _mnemon_scalar_long instead.
_mnemon_scalar() {   # <raw>
  local v="$1" ws
  if [[ ${#v} -gt 1024 ]]; then _mnemon_scalar_long "$v"; return 0; fi
  ws=${v%%[![:space:]]*}; v=${v#"$ws"}
  case "$v" in
    \"*) _mnemon_dq_body "${v#\"}"; return 0 ;;
    \'*) v=${v#\'}; v=${v%%\'*} ;;
    \#*) v="" ;;
    *)   v=${v%%[[:space:]]#*}; ws=${v##*[![:space:]]}; v=${v%"$ws"} ;;
  esac
  _MNEMON_SCALAR="$v"
}

# The same reading for a raw value longer than 1024 characters, with the sed and
# awk of the reader before the one-pass loader: they stay linear in the length of
# a line, while bash's pattern operators in a multibyte locale grow with its
# square. A config line that long is no real configuration; the bound keeps one
# from costing seconds. A failing tool leaves the value empty, as it did then.
_mnemon_scalar_long() {   # <raw>
  local val
  val=$(printf '%s' "$1" | sed 's/^[[:space:]]*//') || true
  case "$val" in
    \"*) val="$(_mnemon_unquote_dq "${val#\"}")" || true ;;
    \'*) val="${val#\'}"; val="${val%%\'*}" ;;
    \#*) val="" ;;
    *)   val=$(printf '%s' "$val" | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//') || true ;;
  esac
  _MNEMON_SCALAR="$val"
}

# The body of a double-quoted scalar ($1 = the text after the opening quote) in
# _MNEMON_SCALAR: the reading of _mnemon_unquote_dq, chunk by chunk — the text up
# to the next `\`, cut at a `"` inside it (a `"` ends the value); `\\` and `\"`
# decode to their second character; any other backslash pair stays as it is; a
# lone trailing backslash stays. Byte by byte (LC_ALL=C for this function only):
# the bytes that matter are ASCII, and in a multibyte locale each operation would
# convert the whole remainder again — quadratic in a value full of escapes.
_mnemon_dq_body() {   # <text after the opening quote>
  local LC_ALL=C s="$1" out="" a
  while [[ -n "$s" ]]; do
    a=${s%%\\*}
    case "$a" in *\"*) out="$out${a%%\"*}"; break ;; esac
    out="$out$a"; s=${s:${#a}}
    case "$s" in
      "")            break ;;
      \\\\*|\\\"*)   out="$out${s:1:1}"; s=${s:2} ;;
      \\?*)          out="$out${s:0:2}"; s=${s:2} ;;
      *)             out="$out$s"; break ;;
    esac
  done
  _MNEMON_SCALAR="$out"
}

# A leading `~` or `~/` of _MNEMON_SCALAR resolved against $HOME (the reading of
# _mnemon_expand_tilde; `~user/...` is left alone). With HOME unset the subshell of
# _mnemon_expand_tilde answers, as it did for every value before the one-pass reader:
# under `set -u` it reports the unbound HOME and the value is empty, the load goes on.
_mnemon_scalar_tilde() {
  case "$_MNEMON_SCALAR" in
    "~"|"~/"*)
      if [[ -n "${HOME+set}" ]]; then
        _MNEMON_SCALAR="$HOME${_MNEMON_SCALAR#\~}"
      else
        _MNEMON_SCALAR="$(_mnemon_expand_tilde "$_MNEMON_SCALAR")" || true
      fi ;;
  esac
  return 0
}

# Every key the loader reads, space-delimited with a space at each end (built once).
_mnemon_scan_keys() {
  [[ -z "${_MNEMON_SCAN_KEYS:-}" ]] || return 0
  local key rest keys=" " IFS=$' \t\n'
  for key in $MNEMON_BASE_KEYS; do keys="$keys$key "; done
  while IFS='|' read -r key rest; do
    if [[ -n "$key" ]]; then keys="$keys$key "; fi
  done <<< "$MNEMON_CONFIG_KEYS"
  _MNEMON_SCAN_KEYS="$keys"
}

# One pass over the config file: for every key the loader reads, the raw text
# after `key: ` on the first line that starts with it, in _mnemon_raw_<key>; a key
# no line names stays unset. An unreadable file reads as an empty one.
_mnemon_config_scan() {   # <file>
  local line="" key seen=" " IFS=$' \t\n'
  _mnemon_scan_keys
  for key in $_MNEMON_SCAN_KEYS; do unset "_mnemon_raw_$key"; done
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in *": "*) ;; *) continue ;; esac
    key=${line%%: *}
    case "$key" in ""|*" "*) continue ;; esac
    case "$_MNEMON_SCAN_KEYS" in *" $key "*) ;; *) continue ;; esac
    case "$seen" in *" $key "*) continue ;; esac
    seen="$seen$key "
    printf -v "_mnemon_raw_$key" '%s' "${line#*: }"
  done 2>/dev/null < "$1" || true
  return 0
}

# The value of a scanned key in _MNEMON_SCALAR (empty when no line names it), `~` expanded.
_mnemon_config_value() {   # <key>
  local raw="_mnemon_raw_$1"
  _mnemon_scalar "${!raw-}"
  _mnemon_scalar_tilde
}

# Parse a single flat YAML key: `key: value` — the reading above, for one key
# (`mnemon config list`, the doctor). Prints the value and a newline.
_parse_yaml_value() {
  local key="$1" file="$2" line="" raw=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in "$key: "*) raw=${line#"$key: "}; break ;; esac
  done 2>/dev/null < "$file" || true
  _mnemon_scalar "$raw"
  _mnemon_scalar_tilde
  printf '%s\n' "$_MNEMON_SCALAR"
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
  local vault="$1" phys sum
  phys="$(cd "$vault" 2>/dev/null && pwd -P)" || phys="$(_mnemon_abs_file "$vault")"
  while [[ "${#phys}" -gt 1 && "$phys" == */ ]]; do phys="${phys%/}"; done
  sum="$(printf '%s' "$phys" | shasum -a 256)" || true
  printf '%s/mnemon/vaults/%s' "$(mnemon_state_home)" "${sum:0:16}"
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

# The resolution digest of the current values (mnemon-resolution/1): NUL-terminated fields — the
# schema, the loader's sha256, the config file's sha256, the inputs, every export — hashed. The
# Python mirror is bin/lib_config.py resolution_digest(). Prints nothing when the file is unreadable.
_mnemon_resolution_digest() {
  local v cfgsha sum
  if [[ -z "${_MNEMON_LOADER_SIG:-}" ]]; then
    sum="$(shasum -a 256 < "$_MNEMON_CONFIG_SH")" || true
    _MNEMON_LOADER_SIG="${sum:0:64}"
  fi
  cfgsha="$(shasum -a 256 < "${MNEMON_CONFIG_FILE:-/nonexistent}" 2>/dev/null)" || true
  cfgsha="${cfgsha:0:64}"
  [[ -n "$cfgsha" ]] || return 1
  sum="$({
    printf 'mnemon-resolution/1\0loader=%s\0config_sha256=%s\0' "$_MNEMON_LOADER_SIG" "$cfgsha"
    for v in $MNEMON_RESOLUTION_INPUTS $MNEMON_EXPORT_NAMES; do printf '%s=%s\0' "$v" "${!v-}"; done
  } | shasum -a 256)" || true
  printf '%s\n' "${sum:0:64}"
}

# 0 when the inherited resolution can be reused as it is: MNEMON_RESOLVED set, every exported name
# set (an empty value counts), the config file readable and the digest equal.
_mnemon_resolution_reusable() {
  local v d
  [[ -n "${MNEMON_RESOLVED:-}" ]] || return 1
  for v in $MNEMON_EXPORT_NAMES; do [[ -n "${!v+x}" ]] || return 1; done
  [[ -f "${MNEMON_CONFIG_FILE:-}" ]] || return 1
  d="$(_mnemon_resolution_digest)" || return 1
  [[ "$d" == "$MNEMON_RESOLVED" ]]
}

# Test hook (MNEMON_TEST_HOOKS=1 + MNEMON_TEST_LOADER_LOG=<file>): one line per load_config call,
# `full <pid>` or `reuse <pid>` — how tests count the loads of one command.
_mnemon_loader_trace() {
  if [[ "${MNEMON_TEST_HOOKS:-}" == 1 && -n "${MNEMON_TEST_LOADER_LOG:-}" ]]; then
    printf '%s %s sh\n' "$1" "$$" >> "$MNEMON_TEST_LOADER_LOG" 2>/dev/null || true
  fi
  return 0
}

load_config() {
  local config_file="${1:-}"
  local cfg_dir resolved_instance="" default_name="" source=""
  # resolve once per command (B7): the inherited resolution, when nothing it depends on moved
  if [[ -z "$config_file" ]] && _mnemon_resolution_reusable; then
    _mnemon_loader_trace reuse
    export MNEMON_ROOT MNEMON_RESOLVED
    # shellcheck disable=SC2086
    export $MNEMON_EXPORT_NAMES
    return 0
  fi
  _mnemon_loader_trace full
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

  # Parse values: one pass over the file reads every key (_mnemon_config_scan)
  _mnemon_config_scan "$config_file"
  _mnemon_config_value vault_path;          VAULT_PATH="$_MNEMON_SCALAR"
  _mnemon_config_value reader_context_path; READER_CONTEXT_PATH="$_MNEMON_SCALAR"
  _mnemon_config_value search_provider;     SEARCH_PROVIDER="$_MNEMON_SCALAR"
  _mnemon_config_value qmd_collection;      QMD_COLLECTION="$_MNEMON_SCALAR"
  _mnemon_config_value default_model;       DEFAULT_MODEL="$_MNEMON_SCALAR"
  _mnemon_config_value default_language;    DEFAULT_LANGUAGE="$_MNEMON_SCALAR"
  _mnemon_config_value whisper_model;       WHISPER_MODEL="$_MNEMON_SCALAR"
  _mnemon_config_value auto_detect_origin;  AUTO_DETECT_ORIGIN="$_MNEMON_SCALAR"
  _mnemon_config_value archive_dir;         ARCHIVE_DIR="$_MNEMON_SCALAR"
  _mnemon_config_value state_dir;           MNEMON_STATE_DIR="$_MNEMON_SCALAR"
  _mnemon_config_value charter_path;        CHARTER_PATH="$_MNEMON_SCALAR"
  _mnemon_config_value writer_host;         WRITER_HOST="$_MNEMON_SCALAR"
  # the scalar keys of MNEMON_CONFIG_KEYS: parsed, validated (exit 2 on a refused value), defaulted
  _mnemon_load_table_keys "$config_file" || return 2

  # Instance name: the name that selected the file wins; otherwise the yaml's
  # `instance:` key (an unrendered {{instance}} placeholder counts as unset, as in
  # _mnemon_declared_instance); otherwise adhoc.
  local instance_key
  _mnemon_config_value instance; instance_key="$_MNEMON_SCALAR"
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
  # Instance-only state: a by-path config without an instance name gets a
  # namespace of its own (spec §7) so two ad-hoc configs never share it; the
  # capture ledger is per vault (MNEMON_VAULT_STATE_DIR) and shared on purpose.
  if [[ -z "$MNEMON_STATE_DIR" ]]; then
    if [[ "$MNEMON_INSTANCE" == "$MNEMON_ADHOC_INSTANCE" ]]; then
      local adhoc_sum
      adhoc_sum="$(printf '%s' "$MNEMON_CONFIG_FILE" | shasum -a 256)"
      MNEMON_STATE_DIR="$(mnemon_state_home)/mnemon/adhoc-${adhoc_sum:0:8}"
    else
      MNEMON_STATE_DIR="$(mnemon_state_home)/mnemon/$MNEMON_INSTANCE"
    fi
  fi
  MNEMON_VAULT_STATE_DIR="$(mnemon_vault_state_dir "$VAULT_PATH")"
  CHARTER_PATH="${CHARTER_PATH:-$VAULT_PATH/_meta/charter.md}"
  WRITER_HOST="${WRITER_HOST:-}"

  # The pair a child re-resolves from — consistent by construction.
  MNEMON_CONFIG="$MNEMON_CONFIG_FILE"

  export MNEMON_ROOT
  # shellcheck disable=SC2086
  export $MNEMON_EXPORT_NAMES
  # the stamp a child reuses the resolution by (B7); without a digest (an unreadable file) none
  MNEMON_RESOLVED="$(_mnemon_resolution_digest || true)"
  if [[ -n "$MNEMON_RESOLVED" ]]; then export MNEMON_RESOLVED; else unset MNEMON_RESOLVED; fi
  return 0
}
