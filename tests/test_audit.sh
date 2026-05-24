#!/usr/bin/env bash
# A1 step 5 — archive-audit.py: reports the 3 consistency gaps from a config,
# in a sandbox vault with known gaps.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUDIT="$MNEMON_ROOT/bin/archive-audit.py"

echo "=== Archive Audit Tests (A1 step 5) ==="

TEST_TMPDIR=$(mktemp -d)
VAULT="$TEST_TMPDIR/vault"
ARCHIVE="$TEST_TMPDIR/archive"
CONFIG="$TEST_TMPDIR/mnemon.yaml"
mkdir -p "$VAULT/Sources" "$ARCHIVE"

cat > "$CONFIG" << EOF
vault_path: $VAULT
archive_dir: $ARCHIVE
EOF

# archive files: one referenced (good.html), one orphan (orphan.txt)
echo x > "$ARCHIVE/good.html"
echo y > "$ARCHIVE/orphan.txt"

mk() { local d="$VAULT/Sources/$1"; mkdir -p "$d"; shift; printf '%s\n' "$@" > "$d/source.md"; }
mk clean    '---' 'origin: url' 'captured: 2026-05-01' 'archive: "good.html"'  '---'   # ok
mk missarch '---' 'origin: url' 'captured: 2026-05-01'                          '---'   # GAP A
mk dangling '---' 'origin: url' 'captured: 2026-05-01' 'archive: "ghost.html"' '---'   # GAP B
mk unarch   '---' 'origin: url' 'captured: 2026-05-01' 'archive: "unarchivable"' '---'  # not a gap
mk refsrc   '---' 'origin: ref:vault' 'captured: 2026-05-01'                    '---'   # not GAP A (ref)

out=$(python3 "$AUDIT" --config "$CONFIG" 2>&1) || true

assert_contains "$out" "GAP A — post-feature non-ref sources missing archive: 1" "GAP A = the missing-archive url source (ref excluded)"
assert_contains "$out" "GAP B — archive: -> missing file: 1"                      "GAP B = the dangling pointer (unarchivable excluded)"
assert_contains "$out" "GAP C — orphan archives (no referencing source): 1"       "GAP C = the orphan archive file"

rm -rf "$TEST_TMPDIR"
summary
