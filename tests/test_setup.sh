#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Setup Tests ==="

TEST_TMPDIR=$(mktemp -d -t mnemon-test-XXXX)
VAULT="$TEST_TMPDIR/test-vault"

# --- Test 1: Setup creates vault structure ---
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd

assert_dir_exists "$VAULT/Sources" "Sources/ created"
assert_dir_exists "$VAULT/Synthesis" "Synthesis/ created"
assert_dir_exists "$VAULT/_meta" "_meta/ created"
assert_file_exists "$VAULT/CLAUDE.md" "CLAUDE.md copied"
assert_file_exists "$VAULT/_meta/Protocol.md" "Protocol.md copied"
assert_file_exists "$VAULT/reader-context.md" "reader-context.md created"

# --- Test 2: Config generated ---
assert_file_exists "$MNEMON_ROOT/mnemon.yaml" "mnemon.yaml created"

# Check config contains correct vault path
config_vault=$(grep '^vault_path:' "$MNEMON_ROOT/mnemon.yaml" | head -1)
assert_contains "$config_vault" "$VAULT" "config has correct vault_path"

# --- Test 3: Idempotent — running again doesn't overwrite ---
echo "# User customized this" >> "$VAULT/CLAUDE.md"
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd
last_line=$(tail -1 "$VAULT/CLAUDE.md")
assert_eq "$last_line" "# User customized this" "existing CLAUDE.md not overwritten"

# --- Test 4: Setup is executable ---
assert_executable "$MNEMON_ROOT/setup.sh" "setup.sh is executable"

# Cleanup
rm -rf "$TEST_TMPDIR"
# Also clean up generated config
rm -f "$MNEMON_ROOT/mnemon.yaml"

summary
