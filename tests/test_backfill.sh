#!/usr/bin/env bash
# A1 step 2 — backfill-archives.sh: target selection + frontmatter patching +
# archival reuse, in a sandbox vault with stubbed curl/render (no network, no
# real vault touched).
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BF="$MNEMON_ROOT/bin/backfill-archives.sh"

echo "=== Backfill Tests (A1 step 2) ==="

TEST_TMPDIR=$(mktemp -d)
VAULT="$TEST_TMPDIR/vault"
ARCHIVE="$TEST_TMPDIR/archive"
STUBS="$TEST_TMPDIR/stubs"
CONFIG="$TEST_TMPDIR/mnemon.yaml"
mkdir -p "$VAULT/Sources" "$ARCHIVE" "$STUBS"

cat > "$CONFIG" << EOF
vault_path: $VAULT
search_provider: grep
archive_dir: $ARCHIVE
EOF

mk_source() {  # dir origin captured archive url
  local d="$VAULT/Sources/$1"; mkdir -p "$d"
  {
    echo "---"
    echo "type: source"
    echo "origin: $2"
    echo "captured: $3"
    [[ -n "$4" ]] && echo "archive: \"$4\""
    echo "url: \"$5\""
    echo "captured_by: agent"
    echo "---"
    echo ""
    echo "# body"
  } > "$d/source.md"
}

mk_source "target"  url       2026-05-01 ""         "https://target.test/x"
mk_source "haspath" url       2026-05-01 "old.html" "https://has.test/x"
mk_source "pre"     url       2026-04-01 ""         "https://pre.test/x"
mk_source "ref"     ref:vault 2026-05-01 ""         ""

cat > "$STUBS/curl" << 'STUB'
#!/usr/bin/env bash
[[ "${STUB_CURL:-fail}" == "ok" ]] && { echo "<html>c</html>"; exit 0; }
exit 1
STUB
cat > "$STUBS/render-url.sh" << 'STUB'
#!/usr/bin/env bash
[[ "${STUB_RENDER:-fail}" == "ok" ]] && { echo "rendered"; exit 0; }
exit 1
STUB
chmod +x "$STUBS/curl" "$STUBS/render-url.sh"
export PATH="$STUBS:$PATH"
export RENDER_URL_CMD="$STUBS/render-url.sh"

has_archive() { grep -q '^archive:' "$VAULT/Sources/$1/source.md" && echo yes || echo no; }
archive_val() { grep '^archive:' "$VAULT/Sources/$1/source.md" | head -1; }
nfiles()      { find "$ARCHIVE" -maxdepth 1 -type f -name "${1:-*}" 2>/dev/null | wc -l | tr -d ' '; }

# --- Scenario 1: curl fails, render ok -> target archived via render (.txt) ---
STUB_CURL=fail STUB_RENDER=ok bash "$BF" --apply --config "$CONFIG" >/dev/null 2>&1 || true
assert_eq "$(has_archive target)" "yes" "target got archive: field"
assert_not_contains "$(archive_val target)" "unarchivable" "target archived, not unarchivable"
assert_eq "$(has_archive pre)"  "no"  "pre-cutoff source skipped"
assert_eq "$(has_archive ref)"  "no"  "non-url (ref) source skipped"
assert_contains "$(archive_val haspath)" "old.html" "already-archived source untouched"
assert_eq "$(nfiles '*.txt')" "1" "one render (.txt) archive written"

# --- Scenario 2: both fail -> target marked unarchivable, no file written ---
mk_source "target" url 2026-05-01 "" "https://target.test/x"
find "$ARCHIVE" -maxdepth 1 -type f -delete 2>/dev/null || true
STUB_CURL=fail STUB_RENDER=fail bash "$BF" --apply --config "$CONFIG" >/dev/null 2>&1 || true
assert_contains "$(archive_val target)" "unarchivable" "both-fail target marked unarchivable"
assert_eq "$(nfiles)" "0" "no archive file when unarchivable"

rm -rf "$TEST_TMPDIR"
summary
