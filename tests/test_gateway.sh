#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW="$MNEMON_ROOT/bin/knowledge-gateway.sh"

echo "=== Gateway Tests ==="

# Create a temp config for testing
TMPDIR=$(mktemp -d)
VAULT="$TMPDIR/vault"
mkdir -p "$VAULT/Sources"

cat > "$TMPDIR/mnemon.yaml" << EOF
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
output=$($GW source-add --url "https://example.com/test" --config "$TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "DRY RUN" "dry-run flag works"
assert_contains "$output" "source-add" "action in prompt"
assert_contains "$output" "https://example.com/test" "URL in prompt"
assert_contains "$output" "READER CONTEXT" "reader context section present"
assert_contains "$output" "EXTRACTION TEMPLATE" "extraction template section present"
assert_contains "$output" "Test reader context" "reader context content embedded"

# --- Test 2: Auto-detection of YouTube URLs ---
output=$($GW source-add --url "https://youtube.com/watch?v=abc123" --config "$TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: youtube" "YouTube URL auto-detected"

output=$($GW source-add --url "https://youtu.be/abc123" --config "$TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: youtube" "youtu.be URL auto-detected"

# --- Test 3: Regular URL defaults to origin=url ---
output=$($GW source-add --url "https://example.com/article" --config "$TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Origin: url" "regular URL → origin=url"

# --- Test 4: Source type auto-detection ---
output=$($GW source-add --url "https://example.com/post" --config "$TMPDIR/mnemon.yaml" --dry-run 2>&1)
assert_contains "$output" "Source type: article" "URL → source_type=article"

# --- Test 5: Missing origin fails ---
output=$($GW source-add --config "$TMPDIR/mnemon.yaml" --dry-run 2>&1) && {
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

output=$($GW status --config "$TMPDIR/mnemon.yaml" 2>&1)
assert_contains "$output" "Total sources: 1" "status counts sources"
assert_contains "$output" "Test Article" "status shows titles"

# Cleanup
rm -rf "$TMPDIR"

summary
