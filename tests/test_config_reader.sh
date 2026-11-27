#!/usr/bin/env bash
# test_config_reader.sh — the configuration reader of bin/mnemon-config.sh reads an instance file in one pass, in
# the shell, and reads it exactly as the reader before it did. The reference below is that earlier reader, frozen:
# one awk and two sed (or awk) processes per key. On every toolchain the suite runs under (macOS BSD awk/sed with
# bash 3.2, the current bash, GNU awk/sed on Linux) the one-pass scan (_mnemon_config_scan + _mnemon_config_value),
# the one-key reader (_parse_yaml_value) and the reference must agree byte for byte on every key of every case file:
# quoting, `:` and `#` inside values, comments, empty values, surrounding whitespace, CRLF, a BOM, a key given twice
# or in another case, unknown and nested keys, tilde forms, control characters, Unicode spaces, a missing final
# newline — in the suite's locale and in the C locale. Then the fetch_timeout split against its awk formula, and
# the property itself: a full load starts no awk and no sed. Hermetic: temp files only, no vault, no network.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Config Reader Tests ==="
print_toolchain; assert_toolchain_legacy

TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-reader-XXXX)" && pwd -P)"
export HOME="$TEST_TMPDIR/home" XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_CONFIG_SOURCE MNEMON_SELECTED_BY
mkdir -p "$HOME" "$TEST_TMPDIR/cases"

# --- the reference: the reader before the one-pass loader, frozen -------------------------------------
ref_unquote_dq() {
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
ref_expand_tilde() {
  case "$1" in
    "~")   printf '%s' "$HOME" ;;
    "~/"*) printf '%s%s' "$HOME" "${1#\~}" ;;
    *)     printf '%s' "$1" ;;
  esac
}
ref_parse() {   # <key> <file>
  local key="$1" file="$2"
  local val
  val=$(awk -v k="$key" 'BEGIN { prefix = k ": " } substr($0, 1, length(prefix)) == prefix { print substr($0, length(prefix) + 1); exit }' "$file" 2>/dev/null)
  val=$(printf '%s' "$val" | sed 's/^[[:space:]]*//')
  case "$val" in
    \"*) val="$(ref_unquote_dq "${val#\"}")" ;;
    \'*) val="${val#\'}"; val="${val%%\'*}" ;;
    \#*) val="" ;;
    *)   val=$(printf '%s' "$val" | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//') ;;
  esac
  val="$(ref_expand_tilde "$val")"
  printf '%s\n' "$val"
}

# --- the case files (printf: exact bytes, nothing that git or an editor could normalise) ---------------
C="$TEST_TMPDIR/cases"
printf '%s\n' 'vault_path: "/v/dq"' 'reader_context_path: ""' 'search_provider: "unclosed value # still in' \
  'qmd_collection: "trailing backslash \' 'default_model: "esc \\" then text' 'default_language: "a\tb\nc\\d\"e"' \
  'whisper_model: "x" # comment after' 'auto_detect_origin: "  spaced  "' "archive_dir: \"it's\"" \
  'state_dir: "~/dq-tilde"' 'charter_path: "\"lead"' 'writer_host: "' 'instance: "inst \"one\""' > "$C/dq.yaml"
printf '%s\n' "vault_path: '/v/sq'" "reader_context_path: ''" "search_provider: 'a#b' # c" "qmd_collection: 'unclosed # x" \
  "default_model: 'it''s'" "default_language: 'back\\slash'" "whisper_model: '  sp  '" "archive_dir: '" \
  "state_dir: '~/sq-tilde'" "charter_path: 'x'tail" "writer_host: 'a\"b'" > "$C/sq.yaml"
printf 'vault_path:    "/v/lead"\nsearch_provider: \t'"'"'tabbed'"'"'\ndefault_model:  \t  sonnet  \t \nwriter_host:      \n' > "$C/lead-ws.yaml"
printf '%s\n' 'vault_path: "/v/lit"' 'instance: "~"' 'writer_host: "~/Guide"' 'charter_path: "a \"quoted\" \ title"' > "$C/literal.yaml"
printf '%s\n' 'vault_path: /v/a:b' 'writer_host: http://x:80/y?z=1#frag' 'default_model: sonnet#x' 'default_language: a #b' \
  "whisper_model: a	#b" 'auto_detect_origin: a  # b # c' 'search_provider: #only' 'qmd_collection:    # only comment' \
  'archive_dir: a#' 'state_dir: a #' 'charter_path: a: b: c' 'reader_context_path: ::' 'instance: i#1' > "$C/colon-hash.yaml"
printf 'vault_path: /v/e\nsearch_provider:\nqmd_collection: \ndefault_model:  \ndefault_language: \t\nwhisper_model:   value   \narchive_dir:\tx\nwriter_host:x\n' > "$C/empty.yaml"
printf 'instance: crlf\r\nvault_path: /v/crlf\r\nsearch_provider: grep\r\nwriter_host: "q"\r\ncharter_path: '"'"'c'"'"'\r\ndefault_model: m # c\r\nfetch_timeout: 30\r\nqmd_collection: \r\narchive_dir: "unclosed\r\n' > "$C/crlf.yaml"
printf 'vault_path: /v/mixed\r\nsearch_provider: qmd\nwriter_host: h\r\ndefault_model: \r\n' > "$C/crlf-mixed.yaml"
printf 'vault_path: /v/cr\rsearch_provider: grep\r' > "$C/cr-only.yaml"
printf '\357\273\277vault_path: /v/bom\nsearch_provider: grep\n' > "$C/bom.yaml"
printf '\357\273\277instance: bomi\nvault_path: /v/bom2\n' > "$C/bom-instance.yaml"
printf 'vault_path: /v/first\nvault_path: /v/second\nsearch_provider:\nsearch_provider: qmd\nfetch_timeout: 10\nfetch_timeout: 70\n' > "$C/twice.yaml"
printf 'Vault_Path: /v/upper\nVAULT_PATH: /v/UP\nvault_path : /v/sp\nvault_path:/v/nosp\nvault_path:\t/v/tab\n vault_path: /v/indent\nvault_path:: /v/dcolon\nvault_path: /v/real\n' > "$C/case.yaml"
printf 'foo: bar\nvault_path_extra: /x\nxvault_path: /y\nvault_path: /v/u\nunknown key: z\n# vault_path: /commented\nvault_path reader_context_path: /bad\n*: star\n[v]ault_path: br\n' > "$C/unknown.yaml"
printf 'capture:\n  vault_path: /nested\n  instance: nested\nvault_path: /v/n\n---\ninstance: after-dashes\n' > "$C/nested.yaml"
printf 'vault_path: ~\nreader_context_path: ~/\nstate_dir: ~user/x\ncharter_path: ~x\narchive_dir: '"'"'~/sq'"'"'\nwriter_host: "~"\ndefault_model: ~ # c\nresearch_log_path: ~/log.jsonl\n' > "$C/tilde.yaml"
printf 'search_provider: qmd\nvault_path: /v/noeol' > "$C/noeol.yaml"
printf 'vault_path: "/v/noeol dq"' > "$C/noeol-dq.yaml"
printf 'vault_path: /v/c\001trl\nwriter_host: \013vt\013\ndefault_model: f\014f\nqmd_collection: \\\nwhisper_model: back\\nslash\n' > "$C/ctrl.yaml"
printf 'vault_path: /v/us\nwriter_host: a\302\240#c\ndefault_model: \343\200\200m\nsearch_provider: x\342\200\203\ndefault_language: \302\240\n' > "$C/unicode-space.yaml"
printf 'vault_path: "/v/\303\251\\\303\251\\\\\303\251\\"\303\251"\nwriter_host: "\344\270\255\\\346\226\207"\n' > "$C/dq-multibyte.yaml"
long="$(printf '%04000d' 0)"
printf 'vault_path: /v/%s\nwriter_host: %s # c\n' "$long" "${long//0/y }" > "$C/long.yaml"
# the bound of the shell reading: a raw value of 1024 characters is read in the shell, one of 1025 by the sed/awk
# form (_mnemon_scalar_long) — both must read as the reference does
esc=""; i=0
while [[ $i -lt 339 ]]; do esc="$esc"'a\\'; i=$((i + 1)); done
esc="$esc"'a\"'                                # 1020 characters: escapes, and an escaped quote
b1022="$(printf '%01022d' 0)"
printf 'vault_path: "%sqq"\nwriter_host: "%sqqq"\nsearch_provider: %s #\ndefault_model: %s  #\n' \
  "$esc" "$esc" "$b1022" "$b1022" > "$C/bound.yaml"
line="$(head -1 "$C/bound.yaml")"; v="${line#vault_path: }"
assert_eq "${#v}" "1024" "(setup) the bound case holds a raw value of exactly 1024 characters"
: > "$C/empty-file.yaml"
printf '# a\n# vault_path: /x\n\n' > "$C/comments-only.yaml"

# compare_reader <label>: every key of every case file — the scan, _parse_yaml_value and the reference
NL=$'\n'
compare_reader() {
  local label="$1" f name keys k line want got scanned bad n
  for f in "$C"/*.yaml; do
    name="$(basename "$f" .yaml)"
    # the key part of every line with `: ` (spaces, `*` and brackets included), then the keys every file is
    # asked for (mostly absent); one per line, never word-split or globbed
    keys="${NL}vault_path${NL}instance${NL}fetch_timeout${NL}foo${NL}Vault_Path${NL}"
    while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        *": "*) k="${line%%: *}"
                case "$keys" in *"$NL$k$NL"*) ;; *) keys="$keys$k$NL" ;; esac ;;
      esac
    done < "$f"
    _mnemon_config_scan "$f"
    bad=""; n=0
    while IFS= read -r k; do
      [[ -n "$k" ]] || continue
      n=$((n + 1))
      want="$(ref_parse "$k" "$f")"
      got="$(_parse_yaml_value "$k" "$f")"
      [[ "$got" == "$want" ]] || bad="$bad [$k] _parse_yaml_value='$got' reference='$want'"
      case "$k" in *[!a-z_]*) continue ;; esac   # the scan holds the loader's keys only, all [a-z_]
      case "$_MNEMON_SCAN_KEYS" in
        *" $k "*) _mnemon_config_value "$k"; scanned="$_MNEMON_SCALAR"
                  [[ "$scanned" == "$want" ]] || bad="$bad [$k] scan='$scanned' reference='$want'" ;;
      esac
    done <<< "$keys"
    assert_eq "$bad" "" "$label $name: $n keys read as the reference reads them"
  done
}
echo "--- the reader against the reference (the suite's locale: ${LC_ALL:-${LC_CTYPE:-${LANG:-unset}}}) ---"
compare_reader "locale"
echo "--- the reader against the reference (LC_ALL=C) ---"
had_lc="${LC_ALL+x}"; old_lc="${LC_ALL-}"
export LC_ALL=C
compare_reader "C"
if [[ -n "$had_lc" ]]; then export LC_ALL="$old_lc"; else unset LC_ALL; fi

# --- the fetch_timeout split: the shell's integers against the awk formula (the fallback is that awk) -----
echo "--- fetch_timeout split ---"
split_bad=""
for t in 0 1 2 3 4 5 8 14 15 16 56 57 59 60 61 060 0000060 007 100 999999 1000014 1000015 1234567 9999999 10000000 \
         99999999999999999999 0.5 0.25 60.0 12.345678 0.0000001 45.1 59.999999; do
  want_c="$(awk -v t="$t" 'BEGIN { c = t / 4; if (c > 15) c = 15; printf "%g", c }')"
  want_r="$(awk -v t="$t" -v c="$want_c" 'BEGIN { printf "%g", t - c }')"
  FETCH_TIMEOUT="$t"; _mnemon_fetch_split
  [[ "$FETCH_CONNECT_TIMEOUT $FETCH_READ_TIMEOUT" == "$want_c $want_r" ]] || split_bad="$split_bad $t:'$FETCH_CONNECT_TIMEOUT $FETCH_READ_TIMEOUT'≠'$want_c $want_r'"
done
assert_eq "$split_bad" "" "fetch_timeout split: connect / read equal awk's %g for integers, quarters, fractions, long and seven-digit values"

# --- the property: one pass, no per-key process -----------------------------------------------------------
echo "--- process starts of a full load ---"
SHIMS="$TEST_TMPDIR/shims"; mkdir -p "$SHIMS"
for tool in awk sed grep cut tr head python3; do
  real="$(command -v "$tool" || true)"
  [[ -n "$real" ]] || continue
  printf '#!/bin/sh\nprintf "%%s\\n" %s >> "%s/starts.log"\nexec "%s" "$@"\n' "$tool" "$TEST_TMPDIR" "$real" > "$SHIMS/$tool"
  chmod +x "$SHIMS/$tool"
done
printf 'instance: one\nvault_path: %s/vault\nsearch_provider: grep\nwriter_host: "h"\ncharter_path: '"'"'~/c'"'"' # c\nfetch_timeout: 60\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/one.yaml"
: > "$TEST_TMPDIR/starts.log"
out="$(PATH="$SHIMS:$PATH" "$BASH" -c 'source "$1/bin/mnemon-config.sh" && load_config "$2" && printf "%s|%s|%s" "$MNEMON_INSTANCE" "$WRITER_HOST" "$FETCH_READ_TIMEOUT"' _ "$MNEMON_ROOT" "$TEST_TMPDIR/one.yaml")"
assert_eq "$out" "one|h|45" "(setup) the instance loads through the shims"
assert_eq "$(tr '\n' ' ' < "$TEST_TMPDIR/starts.log")" "" "a full load starts no awk, sed, grep, cut, tr, head or python3 (one pass in the shell)"
: > "$TEST_TMPDIR/starts.log"
out="$(PATH="$SHIMS:$PATH" "$BASH" -c 'source "$1/bin/mnemon-config.sh" && _parse_yaml_value vault_path "$2"' _ "$MNEMON_ROOT" "$TEST_TMPDIR/one.yaml")"
assert_eq "$out $(tr '\n' ' ' < "$TEST_TMPDIR/starts.log")" "$TEST_TMPDIR/vault " "_parse_yaml_value starts no process either"
: > "$TEST_TMPDIR/starts.log"
PATH="$SHIMS:$PATH" "$BASH" -c 'source "$1/bin/mnemon-config.sh" && _parse_yaml_value vault_path "$2" >/dev/null' _ "$MNEMON_ROOT" "$C/bound.yaml"
assert_eq "$(tr '\n' ' ' < "$TEST_TMPDIR/starts.log")" "" "a raw value of 1024 characters is read in the shell"
: > "$TEST_TMPDIR/starts.log"
PATH="$SHIMS:$PATH" "$BASH" -c 'source "$1/bin/mnemon-config.sh" && _parse_yaml_value writer_host "$2" >/dev/null' _ "$MNEMON_ROOT" "$C/bound.yaml"
assert_contains "$(tr '\n' ' ' < "$TEST_TMPDIR/starts.log")" "sed" "a raw value of 1025 characters goes through the sed/awk form (linear in its length)"

rm -rf "$TEST_TMPDIR"
summary
