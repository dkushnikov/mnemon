#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Config Loader Tests ==="

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

summary
