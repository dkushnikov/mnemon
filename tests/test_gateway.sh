#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW="$MNEMON_ROOT/bin/knowledge-gateway.sh"

echo "=== Gateway Tests ==="

# Create a temp config for testing
TEST_TMPDIR=$(mktemp -d)
VAULT="$TEST_TMPDIR/vault"
mkdir -p "$VAULT/Sources"

cat > "$TEST_TMPDIR/mnemon.yaml" << EOF
vault_path: $VAULT
reader_context_path: $VAULT/reader-context.md
search_provider: grep
default_model: sonnet
whisper_model: large-v3
auto_detect_origin: true
EOF

echo "Test reader context" > "$VAULT/reader-context.md"

# Create a minimal article template for testing
mkdir -p "$MNEMON_ROOT/templates/core"
if [[ ! -f "$MNEMON_ROOT/templates/core/article.md" ]]; then
  echo "# TEST TEMPLATE: Extract article" > "$MNEMON_ROOT/templates/core/article.md"
fi

# --- Test 1: Dry-run produces expected output ---
output=$($GW source-add --url "https://example.com/test" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "DRY RUN" "dry-run flag works"
assert_contains "$output" "source-add" "action in prompt"
assert_contains "$output" "https://example.com/test" "URL in prompt"
assert_contains "$output" "READER CONTEXT" "reader context section present"
assert_contains "$output" "EXTRACTION TEMPLATE" "extraction template section present"
assert_contains "$output" "Test reader context" "reader context content embedded"

# --- Test 2: Auto-detection of YouTube URLs ---
output=$($GW source-add --url "https://youtube.com/watch?v=abc123" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: youtube" "YouTube URL auto-detected"

output=$($GW source-add --url "https://youtu.be/abc123" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: youtube" "youtu.be URL auto-detected"

# --- Test 3: Regular URL defaults to origin=url ---
output=$($GW source-add --url "https://example.com/article" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: url" "regular URL → origin=url"

# --- Test 4: Source type auto-detection ---
output=$($GW source-add --url "https://example.com/post" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Source type: article" "URL → source_type=article"

# --- Test 5: Missing origin fails ---
output=$($GW source-add --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1) && {
  fail "missing origin" "should have failed"
} || {
  pass "missing origin returns error"
}

# --- Test 6: Status action ---
# Create a fake source
mkdir -p "$VAULT/Sources/2026-04-04_abc12345"
cat > "$VAULT/Sources/2026-04-04_abc12345/extract.md" << 'EXTRACT'
---
status: extracted
title: "Test Article"
---
EXTRACT

output=$($GW status --config "$TEST_TMPDIR/mnemon.yaml" 2>&1)
assert_contains "$output" "Total sources: 1" "status counts sources"
assert_contains "$output" "Test Article" "status shows titles"

# --- Test 7: ref:vault dry-run ---
REF_FILE="$TEST_TMPDIR/test-note.md"
echo "# Test Note\nSome vault content here" > "$REF_FILE"
output=$($GW source-add --ref-path "$REF_FILE" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "DRY RUN" "ref:vault dry-run works"
assert_contains "$output" "Origin: ref:vault" "ref:vault origin in prompt"
assert_contains "$output" "Ref path:" "ref_path in prompt"
assert_contains "$output" "STDIN" "ref:vault passes file content as stdin"

# --- Test 8: ref:vault auto-detect from --ref-path ---
output=$($GW source-add --ref-path "$REF_FILE" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: ref:vault" "auto-detect ref:vault from --ref-path"

# --- Test 9: ref:vault defaults source_type to document ---
output=$($GW source-add --ref-path "$REF_FILE" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Source type: document" "ref:vault → source_type=document"

# --- Test 10: ref:vault with missing file fails ---
output=$($GW source-add --ref-path "/nonexistent/file.md" --config "$TEST_TMPDIR/mnemon.yaml" 2>&1) && {
  fail "ref:vault missing file" "should have failed"
} || {
  pass "ref:vault missing file returns error"
}

# --- Test 11: ref:mcp dry-run ---
output=$($GW source-add --ref-source notion --ref-id "page-123" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "DRY RUN" "ref:mcp dry-run works"
assert_contains "$output" "Origin: ref:mcp" "ref:mcp origin in prompt"
assert_contains "$output" "Ref source: notion" "ref_source in prompt"
assert_contains "$output" "Ref ID: page-123" "ref_id in prompt"
assert_contains "$output" "MCP" "ref:mcp mode indicated"

# --- Test 12: ref:mcp auto-detect ---
output=$($GW source-add --ref-source granola --ref-id "meeting-456" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: ref:mcp" "auto-detect ref:mcp from --ref-source + --ref-id"

# --- Test 13: ref:mcp defaults source_type to document ---
output=$($GW source-add --ref-source notion --ref-id "page-123" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Source type: document" "ref:mcp → source_type=document"

# --- Test 14: batch dry-run ---
MANIFEST_FILE="$TEST_TMPDIR/test-manifest.json"
cat > "$MANIFEST_FILE" << 'MANIFEST'
[
  {"origin": "url", "url": "https://example.com/article1", "title": "Article One"},
  {"origin": "url", "url": "https://example.com/article2", "title": "Article Two"}
]
MANIFEST
output=$($GW source-add --manifest "$MANIFEST_FILE" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Batch Import" "batch shows header"
assert_contains "$output" "2 items" "batch counts items"
assert_contains "$output" "Article One" "batch shows item 1"
assert_contains "$output" "Article Two" "batch shows item 2"
assert_contains "$output" "Batch Complete" "batch shows summary"

# --- Test 15: batch auto-detect from --manifest ---
output=$($GW source-add --manifest "$MANIFEST_FILE" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Batch Import" "auto-detect batch from --manifest"

# --- Test 16: batch with invalid JSON fails ---
BAD_MANIFEST="$TEST_TMPDIR/bad-manifest.json"
echo "not valid json" > "$BAD_MANIFEST"
output=$($GW source-add --manifest "$BAD_MANIFEST" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1) && {
  fail "batch invalid JSON" "should have failed"
} || {
  pass "batch invalid JSON returns error"
}

# --- Test 17: batch with missing manifest fails ---
output=$($GW source-add --manifest "/nonexistent/manifest.json" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1) && {
  fail "batch missing manifest" "should have failed"
} || {
  pass "batch missing manifest returns error"
}

# --- Test 18: import-source and import-batch in prompt ---
output=$($GW source-add --url "https://example.com/test" --import-source "safari" --import-batch "2026-04-05-safari" --config "$TEST_TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Import source: safari" "import-source in prompt"
assert_contains "$output" "Import batch: 2026-04-05-safari" "import-batch in prompt"

# Cleanup
rm -rf "$TEST_TMPDIR"

summary
