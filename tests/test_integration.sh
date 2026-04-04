#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Integration Tests ==="

TEST_TMPDIR=$(mktemp -d -t mnemon-integration-XXXX)
VAULT="$TEST_TMPDIR/test-vault"

# --- Test 1: Full setup flow ---
echo "--- Setup ---"
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd
assert_dir_exists "$VAULT/Sources" "vault created"
assert_file_exists "$MNEMON_ROOT/mnemon.yaml" "config generated"

# --- Test 2: Config loads correctly ---
echo "--- Config ---"
source "$MNEMON_ROOT/bin/mnemon-config.sh"
load_config "$MNEMON_ROOT/mnemon.yaml"
assert_eq "$VAULT_PATH" "$VAULT" "config vault_path matches"
assert_file_exists "$READER_CONTEXT_PATH" "reader context exists"

# --- Test 3: Gateway dry-run works end-to-end ---
echo "--- Gateway dry-run ---"
output=$("$MNEMON_ROOT/bin/knowledge-gateway.sh" source-add \
  --url "https://example.com/test-article" \
  --intent "integration test" \
  --config "$MNEMON_ROOT/mnemon.yaml" \
  --dry-run 2>&1)
assert_contains "$output" "DRY RUN" "gateway dry-run works"
assert_contains "$output" "https://example.com/test-article" "URL in prompt"
assert_contains "$output" "EXTRACTION TEMPLATE" "template embedded"
assert_contains "$output" "READER CONTEXT" "reader context embedded"
assert_contains "$output" "integration test" "intent passed through"

# --- Test 4: Gateway status works ---
echo "--- Status ---"
mkdir -p "$VAULT/Sources/2026-04-04_abc12345"
cat > "$VAULT/Sources/2026-04-04_abc12345/extract.md" << 'EOF'
---
status: extracted
title: "Integration Test Article"
domains: [learning]
origin: url
---

## Summary
Test article for integration testing.
EOF

output=$("$MNEMON_ROOT/bin/knowledge-gateway.sh" status \
  --config "$MNEMON_ROOT/mnemon.yaml" 2>&1)
assert_contains "$output" "Total sources: 1" "status counts sources"
assert_contains "$output" "Integration Test Article" "status shows title"

# --- Test 5: Gateway handles YouTube URL detection in full flow ---
echo "--- YouTube detection ---"
output=$("$MNEMON_ROOT/bin/knowledge-gateway.sh" source-add \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --config "$MNEMON_ROOT/mnemon.yaml" \
  --dry-run 2>&1)
assert_contains "$output" "Origin: youtube" "YouTube detected in full flow"
assert_contains "$output" "Source type: video" "video source type"

# --- Test 6: Skills exist ---
echo "--- Skills ---"
assert_file_exists "$MNEMON_ROOT/skills/source-add/SKILL.md" "source-add skill exists"
assert_file_exists "$MNEMON_ROOT/skills/source-search/SKILL.md" "source-search skill exists"
assert_file_exists "$MNEMON_ROOT/skills/source-status/SKILL.md" "source-status skill exists"

# --- Test 7: Plugin manifest valid ---
echo "--- Plugin ---"
assert_file_exists "$MNEMON_ROOT/plugin.json" "plugin.json exists"
# Check it mentions all three skills
plugin_content=$(cat "$MNEMON_ROOT/plugin.json")
assert_contains "$plugin_content" "source-add" "plugin has source-add"
assert_contains "$plugin_content" "source-search" "plugin has source-search"
assert_contains "$plugin_content" "source-status" "plugin has source-status"

# --- Test 8: Template exists ---
echo "--- Templates ---"
assert_file_exists "$MNEMON_ROOT/templates/core/article.md" "article template exists"
template_content=$(cat "$MNEMON_ROOT/templates/core/article.md")
assert_contains "$template_content" "IDENTITY" "template has IDENTITY section"
assert_contains "$template_content" "STEPS" "template has STEPS section"
assert_contains "$template_content" "OUTPUT INSTRUCTIONS" "template has OUTPUT INSTRUCTIONS"
assert_contains "$template_content" "16 words" "template enforces 16-word discipline"

# Cleanup
rm -rf "$TEST_TMPDIR"
rm -f "$MNEMON_ROOT/mnemon.yaml"

summary
