#!/usr/bin/env bash
# A1 — url-archival fallback chain (curl -> render -> unarchivable marker).
# Stubs curl/render/claude so we exercise the real non-dry-run archival path
# without network, Chrome, or a real extraction.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW="$MNEMON_ROOT/bin/knowledge-gateway.sh"

echo "=== Archival Fallback Tests (A1) ==="

TEST_TMPDIR=$(mktemp -d)
VAULT="$TEST_TMPDIR/vault"
ARCHIVE="$TEST_TMPDIR/archive"
STUBS="$TEST_TMPDIR/stubs"
CONFIG="$TEST_TMPDIR/mnemon.yaml"
mkdir -p "$VAULT/Sources" "$ARCHIVE" "$STUBS"
echo "Test reader context" > "$VAULT/reader-context.md"

mkdir -p "$MNEMON_ROOT/templates/core"
[[ -f "$MNEMON_ROOT/templates/core/article.md" ]] || echo "# TEST TEMPLATE" > "$MNEMON_ROOT/templates/core/article.md"

cat > "$CONFIG" << EOF
vault_path: $VAULT
reader_context_path: $VAULT/reader-context.md
search_provider: grep
default_model: sonnet
archive_dir: $ARCHIVE
EOF

# stub: claude — capture the prompt (last arg), emit RESULT lines, no real work
cat > "$STUBS/claude" << 'STUB'
#!/usr/bin/env bash
printf '%s' "${@: -1}" > "$CLAUDE_CAPTURE"
echo "RESULT:path=Sources/test/"
echo "RESULT:status=extracted"
STUB

# stub: curl — STUB_CURL=ok prints content (success), else exit 1 (fetch failed)
cat > "$STUBS/curl" << 'STUB'
#!/usr/bin/env bash
[[ "${STUB_CURL:-fail}" == "ok" ]] && { echo "<html>curl content</html>"; exit 0; }
exit 1
STUB

# stub: render-url.sh — STUB_RENDER=ok prints content (success), else exit 1
cat > "$STUBS/render-url.sh" << 'STUB'
#!/usr/bin/env bash
[[ "${STUB_RENDER:-fail}" == "ok" ]] && { echo "rendered text content"; exit 0; }
exit 1
STUB

chmod +x "$STUBS/claude" "$STUBS/curl" "$STUBS/render-url.sh"

export PATH="$STUBS:$PATH"
export RENDER_URL_CMD="$STUBS/render-url.sh"
export CLAUDE_CAPTURE="$TEST_TMPDIR/claude_prompt.txt"

reset()  { find "$ARCHIVE" -maxdepth 1 -type f -delete 2>/dev/null || true; : > "$CLAUDE_CAPTURE"; }
nfiles() { find "$ARCHIVE" -maxdepth 1 -type f -name "${1:-*}" 2>/dev/null | wc -l | tr -d ' '; }
run_gw() { export STUB_CURL="$1" STUB_RENDER="$2"; "$GW" source-add --url "$3" --config "$CONFIG" >/dev/null 2>&1 || true; }

# T1: curl succeeds -> archived as .html (regression guard for existing behavior)
reset
run_gw ok fail "https://example.com/t1"
assert_eq "$(nfiles '*.html')" "1" "curl success archives .html"
assert_contains "$(cat "$CLAUDE_CAPTURE")" "Archive:" "curl-success prompt carries Archive line"

# T2: curl fails, render succeeds -> archived via render as .txt (NEW fallback)
reset
run_gw fail ok "https://example.com/t2"
assert_eq "$(nfiles '*.txt')" "1" "curl fail + render ok archives via render (.txt)"
assert_not_contains "$(cat "$CLAUDE_CAPTURE")" "unarchivable" "render-fallback success is not marked unarchivable"

# T3: both fail -> no file written + archive: unarchivable marker (NEW)
reset
run_gw fail fail "https://example.com/t3"
assert_eq "$(nfiles)" "0" "both fail -> no archive file written"
assert_contains "$(cat "$CLAUDE_CAPTURE")" "Archive: unarchivable" "both fail -> unarchivable marker in prompt"

# T4: archival disabled (no archive_dir) -> no Archive line at all.
# Guards against spurious "unarchivable" when the user never enabled archival.
cat > "$TEST_TMPDIR/noarchive.yaml" << EOF
vault_path: $VAULT
reader_context_path: $VAULT/reader-context.md
search_provider: grep
default_model: sonnet
EOF
reset
export STUB_CURL=fail STUB_RENDER=fail
"$GW" source-add --url "https://example.com/t4" --config "$TEST_TMPDIR/noarchive.yaml" >/dev/null 2>&1 || true
assert_not_contains "$(cat "$CLAUDE_CAPTURE")" "Archive:" "archival disabled -> no Archive line (no spurious unarchivable)"

rm -rf "$TEST_TMPDIR"
summary
