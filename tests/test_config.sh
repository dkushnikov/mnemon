#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Config Loader Tests ==="
echo "toolchain: $(bash --version | head -1) · $(python3 --version 2>&1)"
# the legacy run (PATH=/usr/bin:/bin:$PATH): the system bash first on PATH means bash 3.2 / python 3.9 — asserted
if [[ "$(command -v bash)" == /bin/bash ]]; then
  [[ "$(bash --version | head -1)" == *"version 3.2."* ]] && pass "legacy toolchain: bash 3.2.x" || fail "legacy toolchain: bash 3.2.x" "$(bash --version | head -1)"
  [[ "$(python3 --version 2>&1)" == "Python 3.9."* ]] && pass "legacy toolchain: python 3.9.x" || fail "legacy toolchain: python 3.9.x" "$(python3 --version 2>&1)"
fi

# Isolation: every resolution below happens under a temp XDG tree and a temp
# cwd; the user's real ~/.config/mnemon, state dir and vault are never touched.
# Inherited capture-layer variables are cleared first (capture layer §7.16).
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-config-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg"
export XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT MNEMON_POLICY_ALLOW_LOOPBACK MNEMON_FETCH_URL_REWRITE MNEMON_EXTERNAL_FETCH_CMD
unset MNEMON_CAPTURE_FETCH_CMD MNEMON_CAPTURE_SITE_CMD MNEMON_DEPTH_FOLD_CMD
CFG_DIR="$XDG_CONFIG_HOME/mnemon"
mkdir -p "$CFG_DIR/instances" "$TEST_TMPDIR/cwd" "$TEST_TMPDIR/emptyroot"

# --- Test 1: Parse flat YAML values ---
TMPCONFIG=$(mktemp)
cat > "$TMPCONFIG" << 'EOF'
vault_path: ~/test-vault
reader_context_path: ~/test-vault/reader-context.md
search_provider: grep
qmd_collection: mnemon
default_model: sonnet
default_language: en
whisper_model: large-v3
auto_detect_origin: true
EOF

load_config "$TMPCONFIG"
assert_eq "$VAULT_PATH" "$HOME/test-vault" "vault_path resolves tilde"
assert_eq "$SEARCH_PROVIDER" "grep" "search_provider loaded"
assert_eq "$QMD_COLLECTION" "mnemon" "qmd_collection loaded"
assert_eq "$DEFAULT_MODEL" "sonnet" "default_model loaded"
assert_eq "$DEFAULT_LANGUAGE" "en" "default_language loaded"
assert_eq "$WHISPER_MODEL" "large-v3" "whisper_model loaded"
assert_eq "$AUTO_DETECT_ORIGIN" "true" "auto_detect_origin loaded"
rm -f "$TMPCONFIG"

# --- Test 2: Defaults when keys missing ---
TMPCONFIG2=$(mktemp)
cat > "$TMPCONFIG2" << 'EOF'
vault_path: ~/my-vault
EOF

load_config "$TMPCONFIG2"
assert_eq "$VAULT_PATH" "$HOME/my-vault" "vault_path from minimal config"
assert_eq "$READER_CONTEXT_PATH" "$HOME/my-vault/reader-context.md" "reader_context_path derives from vault_path"
assert_eq "$SEARCH_PROVIDER" "grep" "search_provider defaults to grep"
assert_eq "$DEFAULT_MODEL" "sonnet" "default_model defaults to sonnet"
assert_eq "$WHISPER_MODEL" "large-v3" "whisper_model defaults to large-v3"
rm -f "$TMPCONFIG2"

# --- Test 3: Config not found ---
output=$(load_config "/nonexistent/path.yaml" 2>&1) && {
  fail "nonexistent config" "should have returned error"
} || {
  pass "nonexistent config returns error"
}

# --- Test 4: MNEMON_ROOT auto-detection ---
assert_eq "$MNEMON_ROOT" "$(cd "$(dirname "$0")/.." && pwd)" "MNEMON_ROOT auto-detected"

# --- Test 5: New exports and their defaults (minimal config, loaded by path) ---
echo "--- new exports ---"
TMPCONFIG5="$TEST_TMPDIR/minimal.yaml"
printf 'vault_path: %s/vault5\n' "$TEST_TMPDIR" > "$TMPCONFIG5"
load_config "$TMPCONFIG5"
assert_eq "$MNEMON_CONFIG_FILE" "$TMPCONFIG5" "MNEMON_CONFIG_FILE is the resolved file"
assert_eq "$MNEMON_INSTANCE" "adhoc" "instance is adhoc for a path-given config without instance:"
adhoc_ns="adhoc-$(printf '%s' "$TMPCONFIG5" | shasum -a 256 | cut -c1-8)"
assert_eq "$MNEMON_STATE_DIR" "$XDG_STATE_HOME/mnemon/$adhoc_ns" "an ad-hoc config gets its own state namespace adhoc-<sha256(config path)[:8]> (spec §7)"
assert_eq "$CHARTER_PATH" "$TEST_TMPDIR/vault5/_meta/charter.md" "charter_path defaults to vault/_meta/charter.md"
assert_eq "$WRITER_HOST" "" "writer_host is empty when unset"
assert_eq "$EXTRACT_TIMEOUT" "900" "extract_timeout defaults to 900"
assert_eq "$EXTRACT_INLINE_MAX_BYTES" "400000" "extract_inline_max_bytes defaults to 400000"
assert_eq "$EXTRACT_MAX_USD" "" "extract_max_usd is empty when unset"
vault5_hash="$(printf '%s' "$TEST_TMPDIR/vault5" | shasum -a 256 | cut -c1-16)"
assert_eq "$MNEMON_VAULT_STATE_DIR" "$XDG_STATE_HOME/mnemon/vaults/$vault5_hash" "MNEMON_VAULT_STATE_DIR = XDG_STATE_HOME/mnemon/vaults/<sha256(vault path)[:16]> (D6)"
[[ -e "$XDG_STATE_HOME" ]] && fail "load_config creates nothing" "XDG_STATE_HOME exists" || pass "load_config computes the state dirs without creating them"

# --- Test 5b: the vault ledger is keyed by the physical vault path: two configs
#     (an instance and an ad-hoc file), a trailing slash and a symlinked parent all
#     land on one MNEMON_VAULT_STATE_DIR; instance state stays apart ---
echo "--- vault state dir ---"
mkdir -p "$TEST_TMPDIR/real/vault5b"
ln -s "$TEST_TMPDIR/real" "$TEST_TMPDIR/link"
printf 'instance: shared\nvault_path: %s/real/vault5b\nextract_timeout: 5\nextract_inline_max_bytes: 1234\nextract_max_usd: 0.5\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/shared-a.yaml"
printf 'vault_path: %s/link/vault5b/\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/shared-b.yaml"
load_config "$TEST_TMPDIR/shared-a.yaml"
vsd_a="$MNEMON_VAULT_STATE_DIR"; isd_a="$MNEMON_STATE_DIR"
assert_eq "$EXTRACT_TIMEOUT" "5" "extract_timeout key honoured"
assert_eq "$EXTRACT_INLINE_MAX_BYTES" "1234" "extract_inline_max_bytes key honoured"
assert_eq "$EXTRACT_MAX_USD" "0.5" "extract_max_usd key honoured"
load_config "$TEST_TMPDIR/shared-b.yaml"
vault5b_hash="$(printf '%s' "$TEST_TMPDIR/real/vault5b" | shasum -a 256 | cut -c1-16)"
assert_eq "$vsd_a" "$XDG_STATE_HOME/mnemon/vaults/$vault5b_hash" "the ledger dir hashes the physical path (instance config)"
assert_eq "$MNEMON_VAULT_STATE_DIR" "$vsd_a" "a symlinked path with a trailing slash resolves to the same ledger dir (ad-hoc config)"
[[ "$MNEMON_STATE_DIR" != "$isd_a" ]] && pass "instance-only state stays per config" || fail "instance-only state stays per config" "both configs got $isd_a"
assert_eq "$(mnemon_vault_state_dir "$TEST_TMPDIR/nope/missing-vault/")" "$XDG_STATE_HOME/mnemon/vaults/$(printf '%s' "$TEST_TMPDIR/nope/missing-vault" | shasum -a 256 | cut -c1-16)" "a vault that does not exist yet hashes its absolute path without the trailing slash"

# --- Test 5d: the capture-layer keys (capture layer §5) — exported in upper case with their
#     defaults, the fetch_timeout split, numeric validation (exit 2), and one table in three
#     places: bin/mnemon-config.sh (MNEMON_CONFIG_KEYS), bin/lib_config.py (CONFIG_KEYS) and
#     the dispatcher (`mnemon config keys` / `config get`) ---
echo "--- capture-layer keys (§5) ---"
load_config "$TMPCONFIG5"
assert_eq "$EXTERNAL_FETCH" "off" "external_fetch defaults to off"
assert_eq "$EXTERNAL_FETCHERS" "firecrawl,jina" "external_fetchers defaults to firecrawl,jina"
assert_contains "$FETCH_USER_AGENT" "Mozilla/5.0" "fetch_user_agent defaults to a desktop browser string"
assert_eq "$FETCH_TIMEOUT $FETCH_CONNECT_TIMEOUT $FETCH_READ_TIMEOUT" "60 15 45" "fetch_timeout 60 splits into connect 15 / read 45"
assert_eq "$FETCH_MAX_BYTES $RENDER_TIMEOUT $RENDER_MAX_BYTES" "20000000 90 20000000" "fetch_max_bytes / render_timeout / render_max_bytes defaults"
assert_eq "$ACQUIRE_TIMEOUT $TRANSCRIBE_TIMEOUT $SITE_MAX_PAGES $SITE_TIMEOUT $THIN_WORDS $EXTRACT_MAX_PARTS" "300 3600 40 900 60 8" "acquire/transcribe/site/thin/parts defaults"
printf 'vault_path: %s/vault5\nexternal_fetch: auto\nexternal_fetchers: jina\nfetch_timeout: 20\nsite_max_pages: 7\nthin_words: 10\nextract_max_parts: 3\nfetch_user_agent: "agent one"\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/keys.yaml"
load_config "$TEST_TMPDIR/keys.yaml"
assert_eq "$EXTERNAL_FETCH $EXTERNAL_FETCHERS $SITE_MAX_PAGES $THIN_WORDS $EXTRACT_MAX_PARTS" "auto jina 7 10 3" "configured values honoured"
assert_eq "$FETCH_TIMEOUT $FETCH_CONNECT_TIMEOUT $FETCH_READ_TIMEOUT" "20 5 15" "fetch_timeout 20 splits into connect 5 (T/4 < 15) / read 15"
assert_eq "$FETCH_USER_AGENT" "agent one" "a quoted fetch_user_agent with a space is kept"
for bad in "fetch_timeout: abc" "site_max_pages: 1.5" "extract_max_parts: -2" "fetch_max_bytes: inf" "external_fetch: maybe" "render_timeout: nan" "extract_inline_max_bytes: 12.0" "extract_max_usd: -0.5"; do
  printf 'vault_path: %s/vault5\n%s\n' "$TEST_TMPDIR" "$bad" > "$TEST_TMPDIR/bad.yaml"
  rc=0; msg="$( (load_config "$TEST_TMPDIR/bad.yaml") 2>&1 )" || rc=$?
  assert_eq "$rc" "2" "'$bad' is refused at config load (exit 2)"
  assert_contains "$msg" "${bad%%:*}" "...naming the key"
done
printf 'vault_path: %s/vault5\nfetch_timeout: 0.5\nextract_max_usd: 1.25\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/frac.yaml"
load_config "$TEST_TMPDIR/frac.yaml"
assert_eq "$FETCH_TIMEOUT $EXTRACT_MAX_USD" "0.5 1.25" "fractional values are accepted where a number (not an integer) is expected"
# three-way agreement: the bash table, the python table, the dispatcher
table_sh="$(mnemon_config_keys)"
table_py="$(python3 - "$MNEMON_ROOT/bin" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import lib_config
for key, var, typ, default in lib_config.CONFIG_KEYS:
    print(key, typ, default or "-")
PY
)"
assert_eq "$table_py" "$table_sh" "bin/lib_config.py CONFIG_KEYS == bin/mnemon-config.sh MNEMON_CONFIG_KEYS (key, type, default)"
assert_eq "$("$MNEMON_ROOT/bin/mnemon" config keys)" "$table_sh" "mnemon config keys prints the same table"
printf 'instance: keys5\nvault_path: %s/vault5\n' "$TEST_TMPDIR" > "$CFG_DIR/instances/keys5.yaml"
load_config "$TMPCONFIG5"
agree=0; disagree=""
while read -r key typ def; do
  [[ -n "$key" ]] || continue
  var="$(mnemon_config_var_of "$key")"
  want="$def"; [[ "$want" == "-" ]] && want=""
  got="$("$MNEMON_ROOT/bin/mnemon" --instance keys5 config get "$key")"
  if [[ "${!var}" == "$want" && "$got" == "$want" ]]; then agree=$((agree + 1)); else disagree="$disagree $key(load='${!var}' get='$got' want='$want')"; fi
done <<< "$table_sh"
assert_eq "$disagree" "" "every table key: load_config's export and 'mnemon config get' both answer the table's default ($agree keys)"
assert_contains "$("$MNEMON_ROOT/bin/mnemon" --instance keys5 config dump)" "SITE_MAX_PAGES=40" "config dump lists the capture-layer exports"
python3 - "$MNEMON_ROOT/bin" "$CFG_DIR/instances/keys5.yaml" <<'PY' && pass "lib_config.load_config exposes every table variable" || fail "lib_config.load_config exposes every table variable" "missing or wrong"
import sys
sys.path.insert(0, sys.argv[1])
import lib_config
cfg = lib_config.load_config(sys.argv[2])
for key, var, typ, default in lib_config.CONFIG_KEYS:
    assert cfg.get(var) == default, (var, cfg.get(var), default)
assert cfg["FETCH_CONNECT_TIMEOUT"] == "15" and cfg["FETCH_READ_TIMEOUT"] == "45"
PY

# --- Test 5c: _mnemon_yaml_literal — the artifact decoder (spec §4.4): exactly
#     \" and \\ decoded, no tilde expansion, frontmatter only; _parse_yaml_value
#     (the config reader) keeps expanding ~ ---
echo "--- yaml literal decoder ---"
ART="$TEST_TMPDIR/artifact.md"
cat > "$ART" << 'EOF'
---
type: source
title: "~"
author: "~/Guide"
session: "a \"quoted\" \ title"
intent: "back\\slash and \"q\""
revision: "2026-10-15_c2e16f9b"
rating: 7   # comment
plain: ~/literal
single: 'it''s'
capture:
  title: "nested, never read"
  method: "curl+defuddle"
empty:
---

title: "body line, never read"
method: "body line, never read"
EOF
assert_eq "$(_mnemon_yaml_literal title "$ART")" "~" 'literal: a title "~" stays a tilde'
assert_eq "$(_mnemon_yaml_literal author "$ART")" "~/Guide" 'literal: "~/Guide" is not expanded'
assert_eq "$(_mnemon_yaml_literal session "$ART")" 'a "quoted" \ title' 'literal: \" and a lone backslash decode'
assert_eq "$(_mnemon_yaml_literal intent "$ART")" 'back\slash and "q"' 'literal: \\ → \ and \" → "'
assert_eq "$(_mnemon_yaml_literal revision "$ART")" "2026-10-15_c2e16f9b" "literal: plain quoted scalar"
assert_eq "$(_mnemon_yaml_literal rating "$ART")" "7" "literal: unquoted value loses a trailing comment"
assert_eq "$(_mnemon_yaml_literal plain "$ART")" "~/literal" "literal: an unquoted leading tilde is not expanded"
assert_eq "$(_mnemon_yaml_literal single "$ART")" "it" "literal: single-quoted value ends at the first closing quote (not a gateway form)"
assert_eq "$(_mnemon_yaml_literal method "$ART")" "" "literal: a nested key is not a top-level key; the body is never read"
assert_eq "$(_mnemon_yaml_literal empty "$ART")" "" "literal: a key without a value is empty"
printf 'nofm: "x"\n' > "$TEST_TMPDIR/nofm.md"
assert_eq "$(_mnemon_yaml_literal nofm "$TEST_TMPDIR/nofm.md")" "" "literal: a file without a frontmatter block yields nothing"
printf 'vault_path: ~/cfg-vault\n' > "$TEST_TMPDIR/tilde-cfg.yaml"
assert_eq "$(_parse_yaml_value vault_path "$TEST_TMPDIR/tilde-cfg.yaml")" "$HOME/cfg-vault" "_parse_yaml_value (config) still expands ~"

# --- Test 6: New keys honoured (instance, state_dir, charter_path, writer_host) ---
cat > "$TEST_TMPDIR/full.yaml" << 'EOF'
instance: fullinst
vault_path: ~/vault6
state_dir: ~/state6
charter_path: ~/charter6.md   # inline comment
writer_host: "some-writer"
EOF
load_config "$TEST_TMPDIR/full.yaml"
assert_eq "$MNEMON_INSTANCE" "fullinst" "instance: key names the instance"
assert_eq "$MNEMON_STATE_DIR" "$HOME/state6" "state_dir key (tilde resolved)"
assert_eq "$CHARTER_PATH" "$HOME/charter6.md" "charter_path key (comment stripped)"
assert_eq "$WRITER_HOST" "some-writer" "writer_host key (quotes stripped)"

# --- Test 6b: quoted values keep '#'; unquoted values lose a trailing ` # comment` (A2) ---
cat > "$TEST_TMPDIR/hash.yaml" << 'EOF'
vault_path: "/tmp/vault#mc"
charter_path: '/tmp/charter#1.md'   # comment after the closing quote
writer_host: host-a   # trailing comment
default_model: sonnet#x
default_language:   # only a comment
EOF
load_config "$TEST_TMPDIR/hash.yaml"
assert_eq "$VAULT_PATH" "/tmp/vault#mc" "double-quoted value keeps #"
assert_eq "$CHARTER_PATH" "/tmp/charter#1.md" "single-quoted value keeps #; text after the quote ignored"
assert_eq "$WRITER_HOST" "host-a" "unquoted value loses the trailing comment"
assert_eq "$DEFAULT_MODEL" "sonnet#x" "a # without preceding whitespace is part of an unquoted value"
assert_eq "$DEFAULT_LANGUAGE" "en" "a value that is only a comment counts as unset (default applies)"

# --- Test 6d: double-quoted values decode \" and \\ — the form `mnemon init` writes (F3) ---
cat > "$TEST_TMPDIR/esc.yaml" << 'EOF'
vault_path: "/tmp/we ird #1 \"q\" back\\slash"
charter_path: "C:\temp\x"   # other backslash sequences stay as they are
writer_host: "quoted \"host\""
EOF
load_config "$TEST_TMPDIR/esc.yaml"
assert_eq "$VAULT_PATH" '/tmp/we ird #1 "q" back\slash' 'double-quoted value: \" → ", \\ → \, space and # kept'
assert_eq "$CHARTER_PATH" 'C:\temp\x' "double-quoted value: unknown backslash sequences are kept verbatim"
assert_eq "$WRITER_HOST" 'quoted "host"' "double-quoted value: an escaped quote does not end the value"

# --- Test 6c: only a bare ~ or ~/ expands; ~user/... stays literal ---
cat > "$TEST_TMPDIR/tilde.yaml" << 'EOF'
vault_path: ~/tilde-vault
charter_path: ~someone/charter.md
state_dir: ~
EOF
load_config "$TEST_TMPDIR/tilde.yaml"
assert_eq "$VAULT_PATH" "$HOME/tilde-vault" "~/path expands to HOME/path"
assert_eq "$CHARTER_PATH" "~someone/charter.md" "~user/path is not rewritten"
assert_eq "$MNEMON_STATE_DIR" "$HOME" "a bare ~ expands to HOME"

# --- Test 7: Unrendered {{instance}} placeholder counts as unset ---
printf 'instance: {{instance}}\nvault_path: %s/vault7\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/tpl.yaml"
load_config "$TEST_TMPDIR/tpl.yaml"
assert_eq "$MNEMON_INSTANCE" "adhoc" "unrendered {{instance}} placeholder → adhoc"

# --- Test 8: Relative path becomes absolute in MNEMON_CONFIG_FILE ---
printf 'vault_path: %s/vault8\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/cwd/rel.yaml"
out=$( (cd "$TEST_TMPDIR/cwd" && load_config ./rel.yaml >/dev/null 2>&1 && printf '%s' "$MNEMON_CONFIG_FILE") ) || true
assert_eq "$out" "$TEST_TMPDIR/cwd/rel.yaml" "relative config path is reported absolute"
rm -f "$TEST_TMPDIR/cwd/rel.yaml"

# --- Resolution order (A1: explicit beats inherited; conflicts fail closed) ---
# The direct load_config calls above exported the pair MNEMON_CONFIG +
# MNEMON_INSTANCE into this shell; the scenarios below start from a clean slate.
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_SOURCE MNEMON_SELECTED_BY
# resolve: run load_config in the calling subshell from the temp cwd and print
# "<file>|<instance>|<source>|<stderr>" on success, or the error text with the
# loader's return code. Always call it inside ( ... ) with the scenario's env.
resolve() {
  cd "$TEST_TMPDIR/cwd" || return 1
  local err="$TEST_TMPDIR/resolve.err" rc=0
  if load_config "$@" >/dev/null 2>"$err"; then
    printf '%s|%s|%s|%s' "$MNEMON_CONFIG_FILE" "$MNEMON_INSTANCE" "$MNEMON_CONFIG_SOURCE" "$(tr '\n' ' ' <"$err")"
  else
    rc=$?
    cat "$err"
    return $rc
  fi
}
mk_cfg() { printf 'vault_path: %s\n' "$2" > "$1"; }

echo "--- resolution order ---"
mk_cfg "$CFG_DIR/instances/alpha.yaml" "$TEST_TMPDIR/vault-alpha"
mk_cfg "$CFG_DIR/instances/beta.yaml"  "$TEST_TMPDIR/vault-beta"
mk_cfg "$TEST_TMPDIR/explicit.yaml"    "$TEST_TMPDIR/vault-explicit"
mk_cfg "$TEST_TMPDIR/envcfg.yaml"      "$TEST_TMPDIR/vault-env"
printf 'instance: alpha\nvault_path: %s\n' "$TEST_TMPDIR/vault-alpha-copy" > "$TEST_TMPDIR/copy-of-alpha.yaml"

# 1. explicit path beats MNEMON_CONFIG and MNEMON_INSTANCE (even a conflicting pair)
out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/envcfg.yaml" MNEMON_INSTANCE=alpha; resolve "$TEST_TMPDIR/explicit.yaml") ) || true
assert_contains "$out" "$TEST_TMPDIR/explicit.yaml|adhoc|--config|" "explicit path wins over MNEMON_CONFIG and MNEMON_INSTANCE"

# 2. MNEMON_CONFIG alone
out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/envcfg.yaml"; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/envcfg.yaml|adhoc|MNEMON_CONFIG|" "MNEMON_CONFIG selects the file (source MNEMON_CONFIG)"

# 2b. both set and inconsistent → exit 2, nothing selected
rc=0; out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/envcfg.yaml" MNEMON_INSTANCE=alpha; resolve) ) || rc=$?
assert_eq "$rc" "2" "MNEMON_CONFIG + MNEMON_INSTANCE naming different configs → exit 2"
assert_contains "$out" "conflicting selectors in the environment (MNEMON_CONFIG=$TEST_TMPDIR/envcfg.yaml, MNEMON_INSTANCE=alpha)" "conflict message names both selectors"

# 2c. both set and consistent (a): the same file
out=$( (export MNEMON_CONFIG="$CFG_DIR/instances/alpha.yaml" MNEMON_INSTANCE=alpha; resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/alpha.yaml|alpha|MNEMON_CONFIG|" "consistent pair (same file) resolves, keeps the instance name"

# 2d. both set and consistent (b): the file declares instance: <name>
out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/copy-of-alpha.yaml" MNEMON_INSTANCE=alpha; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/copy-of-alpha.yaml|alpha|" "consistent pair (file declares the instance) resolves"

# 2e. both set and consistent (c): adhoc with a path
out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/envcfg.yaml" MNEMON_INSTANCE=adhoc; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/envcfg.yaml|adhoc|" "MNEMON_INSTANCE=adhoc with MNEMON_CONFIG resolves the file"

# 2f. inherited MNEMON_CONFIG_SOURCE survives in a consistent child
out=$( (export MNEMON_CONFIG="$CFG_DIR/instances/alpha.yaml" MNEMON_INSTANCE=alpha MNEMON_CONFIG_SOURCE=--instance; resolve) ) || true
assert_contains "$out" "|alpha|--instance|" "a child keeps the parent's selector label"

# 3. MNEMON_INSTANCE selects instances/<name>.yaml
out=$( (export MNEMON_INSTANCE=alpha; resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/alpha.yaml|alpha|MNEMON_INSTANCE|" "MNEMON_INSTANCE selects instances/<name>.yaml"

# 3b. missing instance → exit 2 naming the path and suggesting mnemon init
rc=0; out=$( (export MNEMON_INSTANCE=nosuch; resolve) ) || rc=$?
assert_eq "$rc" "2" "missing instance exits 2"
assert_contains "$out" "$CFG_DIR/instances/nosuch.yaml" "missing instance error names the expected path"
assert_contains "$out" "mnemon init nosuch --vault" "missing instance error suggests mnemon init"

# 3c. adhoc is reserved: MNEMON_INSTANCE=adhoc without MNEMON_CONFIG → exit 2, no fallback
printf 'beta\n' > "$CFG_DIR/default"
rc=0; out=$( (export MNEMON_INSTANCE=adhoc; resolve) ) || rc=$?
assert_eq "$rc" "2" "MNEMON_INSTANCE=adhoc alone exits 2 (never falls through to the default)"
assert_contains "$out" "MNEMON_INSTANCE=adhoc names no instance file" "adhoc-alone error explains itself"
rm -f "$CFG_DIR/default"

# 3d. a flag label set by the dispatcher/gateway is reported as the source
out=$( (export MNEMON_INSTANCE=alpha MNEMON_SELECTED_BY=--instance; resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/alpha.yaml|alpha|--instance|" "MNEMON_SELECTED_BY labels the source"

# 4. ./mnemon.yaml in the cwd beats the default file
mk_cfg "$TEST_TMPDIR/cwd/mnemon.yaml" "$TEST_TMPDIR/vault-cwd"
printf 'beta\n' > "$CFG_DIR/default"
out=$( (resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/cwd/mnemon.yaml|adhoc|./mnemon.yaml|" "./mnemon.yaml wins over the default file"
rm -f "$TEST_TMPDIR/cwd/mnemon.yaml"

# 5. default file → instances/<name>.yaml
out=$( (resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/beta.yaml|beta|default|" "default file selects its instance"

# 5b. default file with comments/blank lines still names the instance
printf '# the default instance\n\n  beta  \n' > "$CFG_DIR/default"
out=$( (resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/beta.yaml|beta|" "default file tolerates comments and whitespace"

# 5c. default naming a missing instance → exit 2
printf 'ghost\n' > "$CFG_DIR/default"
rc=0; out=$( (resolve) ) || rc=$?
assert_eq "$rc" "2" "default → missing instance exits 2"
assert_contains "$out" "$CFG_DIR/instances/ghost.yaml" "default → missing instance error names the path"
rm -f "$CFG_DIR/default"

# 6a. legacy $MNEMON_CONFIG_DIR/mnemon.yaml, with a one-line warning
mk_cfg "$CFG_DIR/mnemon.yaml" "$TEST_TMPDIR/vault-legacy1"
out=$( (resolve) ) || true
assert_contains "$out" "$CFG_DIR/mnemon.yaml|adhoc|legacy|" "legacy config-dir mnemon.yaml resolves"
assert_contains "$out" "mnemon: legacy config $CFG_DIR/mnemon.yaml; create an instance with: mnemon init <name> --vault <vault>" "legacy warning text"
rm -f "$CFG_DIR/mnemon.yaml"

# 6b. legacy $MNEMON_ROOT/mnemon.yaml (MNEMON_ROOT pointed at a temp dir)
mkdir -p "$TEST_TMPDIR/fakeroot"
mk_cfg "$TEST_TMPDIR/fakeroot/mnemon.yaml" "$TEST_TMPDIR/vault-legacy2"
out=$( (MNEMON_ROOT="$TEST_TMPDIR/fakeroot"; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/fakeroot/mnemon.yaml|adhoc|legacy|" "legacy MNEMON_ROOT/mnemon.yaml resolves"
assert_contains "$out" "legacy config $TEST_TMPDIR/fakeroot/mnemon.yaml" "legacy root warning names the file"

# 7. nothing found → error listing every location tried
rc=0; out=$( (MNEMON_ROOT="$TEST_TMPDIR/emptyroot"; resolve) ) || rc=$?
assert_eq "$rc" "1" "no config anywhere → error"
assert_contains "$out" "no Mnemon config found" "error headline"
assert_contains "$out" "--instance <name>" "error lists --instance"
assert_contains "$out" "MNEMON_CONFIG" "error lists MNEMON_CONFIG"
assert_contains "$out" "$CFG_DIR/instances/<name>.yaml" "error lists the instances dir"
assert_contains "$out" "$TEST_TMPDIR/cwd/mnemon.yaml" "error lists ./mnemon.yaml (absolute)"
assert_contains "$out" "$CFG_DIR/default" "error lists the default file"
assert_contains "$out" "$CFG_DIR/mnemon.yaml" "error lists the legacy config-dir file"
assert_contains "$out" "$TEST_TMPDIR/emptyroot/mnemon.yaml" "error lists the legacy root file"
assert_contains "$out" "mnemon init <name> --vault <vault>" "error suggests mnemon init"

# 8. instance resolution sets the per-instance state dir default
out=$( (export MNEMON_INSTANCE=alpha; cd "$TEST_TMPDIR/cwd" && load_config >/dev/null 2>&1 && printf '%s' "$MNEMON_STATE_DIR") ) || true
assert_eq "$out" "$XDG_STATE_HOME/mnemon/alpha" "state dir default uses the resolved instance name"

# 9. a successful load exports the consistent pair; a child resolving from the
#    environment lands on the same file and name (the dispatcher decides once)
echo "--- exported pair ---"
child='source "$MNEMON_ROOT/bin/mnemon-config.sh" && load_config >/dev/null && printf "%s|%s|%s" "$MNEMON_CONFIG_FILE" "$MNEMON_INSTANCE" "$MNEMON_CONFIG"'
out=$( (export MNEMON_INSTANCE=alpha; cd "$TEST_TMPDIR/cwd" && load_config >/dev/null 2>&1 && printf '%s|%s\n' "$MNEMON_CONFIG" "$MNEMON_INSTANCE" && bash -c "$child") ) || true
assert_contains "$out" "$CFG_DIR/instances/alpha.yaml|alpha" "load_config exports MNEMON_CONFIG=<file> together with MNEMON_INSTANCE"
assert_contains "$out" "$CFG_DIR/instances/alpha.yaml|alpha|$CFG_DIR/instances/alpha.yaml" "a child re-resolving from the environment lands on the same file"
out=$( (cd "$TEST_TMPDIR/cwd" && load_config "$TEST_TMPDIR/copy-of-alpha.yaml" >/dev/null 2>&1 && bash -c "$child") ) || true
assert_eq "$out" "$TEST_TMPDIR/copy-of-alpha.yaml|alpha|$TEST_TMPDIR/copy-of-alpha.yaml" "by-path config declaring instance: alpha → child sees a consistent pair (not a conflict)"
out=$( (cd "$TEST_TMPDIR/cwd" && load_config "$TEST_TMPDIR/envcfg.yaml" >/dev/null 2>&1 && bash -c "$child") ) || true
assert_eq "$out" "$TEST_TMPDIR/envcfg.yaml|adhoc|$TEST_TMPDIR/envcfg.yaml" "by-path config without instance: → adhoc + MNEMON_CONFIG, consistent for a child"

rm -rf "$TEST_TMPDIR"
summary
