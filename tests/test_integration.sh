#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Integration Tests ==="

TEST_TMPDIR=$(mktemp -d -t mnemon-integration-XXXX)
VAULT="$TEST_TMPDIR/test-vault"

# Isolate the test's config from the user's real ~/Mnemon/mnemon.yaml.
# setup.sh and mnemon-config.sh both honor $MNEMON_CONFIG, so exporting it
# here routes all config reads and writes into $TEST_TMPDIR.
export MNEMON_CONFIG="$TEST_TMPDIR/mnemon.yaml"

# --- Test 1: Full setup flow ---
echo "--- Setup ---"
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd
assert_dir_exists "$VAULT/Sources" "vault created"
assert_file_exists "$MNEMON_CONFIG" "config generated"

# --- Test 2: Config loads correctly ---
echo "--- Config ---"
source "$MNEMON_ROOT/bin/mnemon-config.sh"
load_config "$MNEMON_CONFIG"
assert_eq "$VAULT_PATH" "$VAULT" "config vault_path matches"
assert_file_exists "$READER_CONTEXT_PATH" "reader context exists"
assert_eq "$QMD_COLLECTION" "obsidian-knowledge" "default qmd_collection name"

# --- Test 3: Gateway dry-run works end-to-end ---
echo "--- Gateway dry-run ---"
output=$("$MNEMON_ROOT/bin/knowledge-gateway.sh" source-add \
  --url "https://example.com/test-article" \
  --intent "integration test" \
  --config "$MNEMON_CONFIG" \
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
  --config "$MNEMON_CONFIG" 2>&1)
assert_contains "$output" "Total sources: 1" "status counts sources"
assert_contains "$output" "Integration Test Article" "status shows title"

# --- Test 5: Gateway handles YouTube URL detection in full flow ---
echo "--- YouTube detection ---"
output=$("$MNEMON_ROOT/bin/knowledge-gateway.sh" source-add \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --config "$MNEMON_CONFIG" \
  --dry-run 2>&1)
assert_contains "$output" "Origin: youtube" "YouTube detected in full flow"
assert_contains "$output" "Source type: video" "video source type"

# --- Test 5b: Dry-run does NOT fire auto-reindex hook ---
# The hook in source-add should short-circuit when --dry-run is set.
# If it fires, it would call `qmd update && qmd embed` in background,
# touching the user's real qmd index. Regression test for that.
echo "--- Auto-reindex guard ---"
# Set search_provider to qmd in the test config so the hook's provider
# check would pass if the dry-run guard weren't working.
sed -i '' 's/^search_provider: grep/search_provider: qmd/' "$MNEMON_CONFIG"
# We can't easily observe "no background qmd invocation" directly, so
# we assert the gateway exits cleanly and the output still contains the
# dry-run marker — the hook runs AFTER the main if/else block, so any
# syntax error or shell trap in the hook would corrupt exit status.
output=$("$MNEMON_ROOT/bin/knowledge-gateway.sh" source-add \
  --url "https://example.com/reindex-guard-test" \
  --config "$MNEMON_CONFIG" \
  --dry-run 2>&1)
assert_contains "$output" "DRY RUN" "dry-run still works with qmd provider set"
# Flip back for subsequent tests
sed -i '' 's/^search_provider: qmd/search_provider: grep/' "$MNEMON_CONFIG"

# Skills and plugin manifest moved to the dkushnikov/mnemon-plugin repo;
# tests for those assertions live there now (see commit removing plugin.json
# and skills/ from Mnemon tool repo).

# --- Test 8: Template exists ---
echo "--- Templates ---"
assert_file_exists "$MNEMON_ROOT/templates/core/article.md" "article template exists"
template_content=$(cat "$MNEMON_ROOT/templates/core/article.md")
assert_contains "$template_content" "IDENTITY" "template has IDENTITY section"
assert_contains "$template_content" "STEPS" "template has STEPS section"
assert_contains "$template_content" "OUTPUT INSTRUCTIONS" "template has OUTPUT INSTRUCTIONS"
assert_contains "$template_content" "16 words" "template enforces 16-word discipline"

# Cleanup — $MNEMON_CONFIG lives inside $TEST_TMPDIR, so removing the tmpdir
# is enough. Do NOT touch $MNEMON_ROOT/mnemon.yaml — that's the user's real
# config, which earlier versions of this test destroyed on every run.
rm -rf "$TEST_TMPDIR"

summary
