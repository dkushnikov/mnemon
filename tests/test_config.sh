#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Config Loader Tests ==="

# Isolation: every resolution below happens under a temp XDG tree and a temp
# cwd; the user's real ~/.config/mnemon, state dir and vault are never touched.
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-config-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg"
export XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR
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
assert_eq "$MNEMON_STATE_DIR" "$XDG_STATE_HOME/mnemon/adhoc" "state_dir defaults to XDG_STATE_HOME/mnemon/<instance>"
assert_eq "$CHARTER_PATH" "$TEST_TMPDIR/vault5/_meta/charter.md" "charter_path defaults to vault/_meta/charter.md"
assert_eq "$WRITER_HOST" "" "writer_host is empty when unset"

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

# --- Resolution order ---
# resolve: run load_config in the calling subshell from the temp cwd and print
# "<file>|<instance>|<stderr>" on success, or the error text with rc 1.
# Always call it inside ( ... ) with the environment the scenario needs.
resolve() {
  cd "$TEST_TMPDIR/cwd" || return 1
  local err="$TEST_TMPDIR/resolve.err"
  if load_config "$@" >/dev/null 2>"$err"; then
    printf '%s|%s|%s' "$MNEMON_CONFIG_FILE" "$MNEMON_INSTANCE" "$(tr '\n' ' ' <"$err")"
  else
    cat "$err"
    return 1
  fi
}
mk_cfg() { printf 'vault_path: %s\n' "$2" > "$1"; }

echo "--- resolution order ---"
mk_cfg "$CFG_DIR/instances/alpha.yaml" "$TEST_TMPDIR/vault-alpha"
mk_cfg "$CFG_DIR/instances/beta.yaml"  "$TEST_TMPDIR/vault-beta"
mk_cfg "$TEST_TMPDIR/explicit.yaml"    "$TEST_TMPDIR/vault-explicit"
mk_cfg "$TEST_TMPDIR/envcfg.yaml"      "$TEST_TMPDIR/vault-env"

# 1. explicit path beats MNEMON_CONFIG and MNEMON_INSTANCE
out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/envcfg.yaml" MNEMON_INSTANCE=alpha; resolve "$TEST_TMPDIR/explicit.yaml") ) || true
assert_contains "$out" "$TEST_TMPDIR/explicit.yaml|adhoc|" "explicit path wins over MNEMON_CONFIG and MNEMON_INSTANCE"

# 2. MNEMON_CONFIG beats MNEMON_INSTANCE
out=$( (export MNEMON_CONFIG="$TEST_TMPDIR/envcfg.yaml" MNEMON_INSTANCE=alpha; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/envcfg.yaml|adhoc|" "MNEMON_CONFIG wins over MNEMON_INSTANCE"

# 3. MNEMON_INSTANCE selects instances/<name>.yaml
out=$( (export MNEMON_INSTANCE=alpha; resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/alpha.yaml|alpha|" "MNEMON_INSTANCE selects instances/<name>.yaml"

# 3b. missing instance → error naming the path and suggesting mnemon init
rc=0; out=$( (export MNEMON_INSTANCE=nosuch; resolve) ) || rc=$?
assert_eq "$rc" "1" "missing instance fails"
assert_contains "$out" "$CFG_DIR/instances/nosuch.yaml" "missing instance error names the expected path"
assert_contains "$out" "mnemon init nosuch --vault" "missing instance error suggests mnemon init"

# 4. ./mnemon.yaml in the cwd beats the default file
mk_cfg "$TEST_TMPDIR/cwd/mnemon.yaml" "$TEST_TMPDIR/vault-cwd"
printf 'beta\n' > "$CFG_DIR/default"
out=$( (resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/cwd/mnemon.yaml|adhoc|" "./mnemon.yaml wins over the default file"
rm -f "$TEST_TMPDIR/cwd/mnemon.yaml"

# 5. default file → instances/<name>.yaml
out=$( (resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/beta.yaml|beta|" "default file selects its instance"

# 5b. default file with comments/blank lines still names the instance
printf '# the default instance\n\n  beta  \n' > "$CFG_DIR/default"
out=$( (resolve) ) || true
assert_contains "$out" "$CFG_DIR/instances/beta.yaml|beta|" "default file tolerates comments and whitespace"

# 5c. default naming a missing instance → error
printf 'ghost\n' > "$CFG_DIR/default"
rc=0; out=$( (resolve) ) || rc=$?
assert_eq "$rc" "1" "default → missing instance fails"
assert_contains "$out" "$CFG_DIR/instances/ghost.yaml" "default → missing instance error names the path"
rm -f "$CFG_DIR/default"

# 6a. legacy $MNEMON_CONFIG_DIR/mnemon.yaml, with a one-line warning
mk_cfg "$CFG_DIR/mnemon.yaml" "$TEST_TMPDIR/vault-legacy1"
out=$( (resolve) ) || true
assert_contains "$out" "$CFG_DIR/mnemon.yaml|adhoc|" "legacy config-dir mnemon.yaml resolves"
assert_contains "$out" "mnemon: legacy config $CFG_DIR/mnemon.yaml; create an instance with: mnemon init <name> --vault <vault>" "legacy warning text"
rm -f "$CFG_DIR/mnemon.yaml"

# 6b. legacy $MNEMON_ROOT/mnemon.yaml (MNEMON_ROOT pointed at a temp dir)
mkdir -p "$TEST_TMPDIR/fakeroot"
mk_cfg "$TEST_TMPDIR/fakeroot/mnemon.yaml" "$TEST_TMPDIR/vault-legacy2"
out=$( (MNEMON_ROOT="$TEST_TMPDIR/fakeroot"; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/fakeroot/mnemon.yaml|adhoc|" "legacy MNEMON_ROOT/mnemon.yaml resolves"
assert_contains "$out" "legacy config $TEST_TMPDIR/fakeroot/mnemon.yaml" "legacy root warning names the file"

# 6c. adhoc is reserved: MNEMON_INSTANCE=adhoc never selects an instance file
out=$( (export MNEMON_INSTANCE=adhoc; MNEMON_ROOT="$TEST_TMPDIR/fakeroot"; resolve) ) || true
assert_contains "$out" "$TEST_TMPDIR/fakeroot/mnemon.yaml|adhoc|" "MNEMON_INSTANCE=adhoc falls through (reserved name)"

# 7. nothing found → error listing every location tried
rc=0; out=$( (MNEMON_ROOT="$TEST_TMPDIR/emptyroot"; resolve) ) || rc=$?
assert_eq "$rc" "1" "no config anywhere → error"
assert_contains "$out" "no Mnemon config found" "error headline"
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

rm -rf "$TEST_TMPDIR"
summary
