#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<'EOF'
Usage: setup.sh <vault-path> [options]

Create a Mnemon knowledge vault and configure the system.

Arguments:
  vault-path          Where to create/use the knowledge vault

Options:
  --non-interactive   Skip interactive prompts, use defaults
  --skip-qmd          Don't configure QMD search collection
  -h, --help          Show this help

Example:
  ./setup.sh ~/my-knowledge
  ./setup.sh ~/Obsidian/Knowledge --non-interactive
EOF
  exit 0
}

# --- Parse arguments ---

VAULT_PATH=""
NON_INTERACTIVE=false
SKIP_QMD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --skip-qmd)        SKIP_QMD=true; shift ;;
    -h|--help)         usage ;;
    -*)                echo "Unknown option: $1" >&2; usage ;;
    *)                 VAULT_PATH="$1"; shift ;;
  esac
done

if [[ -z "$VAULT_PATH" ]]; then
  echo "ERROR: vault path required." >&2
  echo ""
  usage
fi

# Resolve tilde and make absolute
VAULT_PATH="${VAULT_PATH/#\~/$HOME}"
resolved_parent="$(cd "$(dirname "$VAULT_PATH")" 2>/dev/null && pwd)" || true
if [[ -n "$resolved_parent" ]]; then
  VAULT_PATH="$resolved_parent/$(basename "$VAULT_PATH")"
fi

echo "=== Mnemon Setup ==="
echo "Vault: $VAULT_PATH"
echo "Mnemon: $SCRIPT_DIR"
echo ""

# --- 1. Create vault structure ---

echo "1. Creating vault structure..."
mkdir -p "$VAULT_PATH/Sources" "$VAULT_PATH/Synthesis" "$VAULT_PATH/_meta"
echo "   ✓ Sources/, Synthesis/, _meta/"

# --- 2. Copy vault template files (skip if exist) ---

echo "2. Copying vault template..."
copy_if_missing() {
  local src="$1" dst="$2" label="$3"
  if [[ ! -f "$dst" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    echo "   ✓ Created $label"
  else
    echo "   ○ Skipped $label (exists)"
  fi
}

copy_if_missing "$SCRIPT_DIR/vault-template/CLAUDE.md" "$VAULT_PATH/CLAUDE.md" "CLAUDE.md"
copy_if_missing "$SCRIPT_DIR/vault-template/_meta/Protocol.md" "$VAULT_PATH/_meta/Protocol.md" "_meta/Protocol.md"
copy_if_missing "$SCRIPT_DIR/reader-context.md.template" "$VAULT_PATH/reader-context.md" "reader-context.md"

# --- 3. Generate mnemon.yaml ---

echo "3. Generating config..."
# Honor $MNEMON_CONFIG so tests (and other automation) can isolate config
# from the user's real ~/Mnemon/mnemon.yaml. Defaults to $SCRIPT_DIR/mnemon.yaml
# for normal interactive use. mnemon-config.sh already honors the same env var
# on the loader side — this keeps both ends symmetric.
CONFIG_PATH="${MNEMON_CONFIG:-$SCRIPT_DIR/mnemon.yaml}"
mkdir -p "$(dirname "$CONFIG_PATH")"
if [[ ! -f "$CONFIG_PATH" ]]; then
  escaped_path="${VAULT_PATH//&/\\&}"
  sed "s|{{vault_path}}|$escaped_path|g" "$SCRIPT_DIR/mnemon.yaml.template" > "$CONFIG_PATH"
  echo "   ✓ Created mnemon.yaml"
else
  echo "   ○ Skipped mnemon.yaml (exists)"
fi

# --- 4. Check dependencies ---

echo ""
echo "4. Checking dependencies..."
check_dep() {
  local cmd="$1" label="$2" required="$3" install_hint="$4"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "   ✓ $label"
  elif [[ "$required" == "true" ]]; then
    echo "   ✗ $label (REQUIRED) — $install_hint"
  else
    echo "   ○ $label (optional) — $install_hint"
  fi
}

check_dep "claude" "Claude Code" "true" "https://docs.anthropic.com/en/docs/claude-code"
check_dep "yt-dlp" "yt-dlp (YouTube transcripts)" "false" "brew install yt-dlp"
check_dep "whisper" "Whisper (audio transcription)" "false" "pip install openai-whisper"
check_dep "ffprobe" "FFprobe (audio detection)" "false" "brew install ffmpeg"
check_dep "qmd" "QMD (semantic search)" "false" "See QMD documentation"

# --- 5. QMD setup (optional) ---

# Read the collection name straight from the generated config so users can
# override it by editing mnemon.yaml before re-running setup.sh.
QMD_COLLECTION_NAME="obsidian-knowledge"
if [[ -f "$CONFIG_PATH" ]]; then
  parsed=$(awk -F': *' '/^qmd_collection:/ { gsub(/["'\'']/, "", $2); print $2; exit }' "$CONFIG_PATH")
  [[ -n "$parsed" ]] && QMD_COLLECTION_NAME="$parsed"
fi

# Install the qmd Bun-launcher workaround if we detect the conditions that
# trigger it (bun installed, $BUN_INSTALL set, real qmd lives in /opt/homebrew).
# Root cause: qmd 2.0.1's bin/qmd uses `[ -n "$BUN_INSTALL" ]` to pick bun,
# even when qmd was installed via npm. Bun's bundled sqlite can't load
# sqlite-vec → any vector query crashes. Fixed on qmd main but not in 2.0.1.
# See tobi/qmd#363 and the Known Issues section of Mnemon's README.
install_qmd_bun_workaround() {
  local real_qmd="/opt/homebrew/bin/qmd"
  local wrapper="$HOME/.local/bin/qmd"
  [[ -z "${BUN_INSTALL:-}" ]] && return 0
  [[ ! -x "$real_qmd" ]] && return 0
  # If ~/.local/bin already shadows /opt/homebrew/bin, skip (wrapper may
  # already exist from a previous run).
  if [[ -f "$wrapper" ]]; then
    echo "   ✓ qmd Bun-launcher workaround already in place ($wrapper)"
    return 0
  fi
  # Check PATH ordering: ~/.local/bin must come before /opt/homebrew/bin.
  local path_order
  path_order=$(echo "$PATH" | tr ':' '\n' | awk -v h="$HOME/.local/bin" -v b="/opt/homebrew/bin" '
    $0 == h { local = NR }
    $0 == b { brew = NR }
    END { if (local && brew && local < brew) print "ok"; else print "bad" }')
  if [[ "$path_order" != "ok" ]]; then
    cat <<MSG >&2
   ⚠  qmd Bun-launcher workaround needed but not installable automatically:
      \$HOME/.local/bin must be earlier than /opt/homebrew/bin in your \$PATH.
      Add this to your shell profile:
        export PATH="\$HOME/.local/bin:\$PATH"
      Then re-run this script. See README "Known issues" for details.
MSG
    return 1
  fi
  mkdir -p "$HOME/.local/bin"
  cat > "$wrapper" <<WRAPPER
#!/bin/sh
# Auto-generated by Mnemon setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Works around tobi/qmd#363 (qmd 2.0.1 bin/qmd picks bun when \$BUN_INSTALL
# is set, even on npm installs, causing sqlite-vec crashes under bun).
# Remove this wrapper once qmd v2.0.2+ is installed.
BUN_INSTALL= exec /opt/homebrew/bin/qmd "\$@"
WRAPPER
  chmod +x "$wrapper"
  echo "   ✓ Installed qmd Bun-launcher workaround at $wrapper"
  return 0
}

if ! $SKIP_QMD && command -v qmd >/dev/null 2>&1; then
  echo ""
  echo "5. Configuring QMD search..."

  # Install the Bun-launcher workaround BEFORE using qmd, so the smoke test
  # below doesn't crash on machines where the bug applies.
  install_qmd_bun_workaround || true

  # Add the collection pointing at the full vault (not just Sources/), so
  # Synthesis/ notes and other top-level content are searchable.
  if qmd collection add "$QMD_COLLECTION_NAME" "$VAULT_PATH" --mode hybrid 2>/dev/null; then
    echo "   ✓ QMD collection '$QMD_COLLECTION_NAME' configured"
  else
    echo "   ○ QMD collection '$QMD_COLLECTION_NAME' may already exist"
  fi

  # Populate the index and generate embeddings. This doubles as the smoke
  # test for the Bun-launcher bug — `qmd embed` is where vector-table code
  # actually runs.
  echo "   • Indexing vault..."
  if qmd update >/dev/null 2>&1; then
    echo "   ✓ Index updated"
  else
    echo "   ⚠  qmd update failed — check 'qmd update' output manually"
  fi

  echo "   • Generating embeddings (may take a moment)..."
  embed_output=$(qmd embed 2>&1)
  if echo "$embed_output" | grep -qE "vec0|sqlite-vec|Bun has crashed"; then
    cat <<MSG >&2
   ✗ QMD vector indexing crashed with the known Bun-launcher bug
     (tobi/qmd#363). The automatic workaround above did not help — your
     PATH may be misconfigured or qmd is installed in an unusual location.
     See Mnemon README "Known issues" for the manual fix.
MSG
  else
    echo "   ✓ Embeddings generated"
    # Flip the provider to qmd only if embeddings actually worked.
    if [[ -f "$CONFIG_PATH" ]]; then
      sed -i '' 's/^search_provider: grep/search_provider: qmd/' "$CONFIG_PATH" 2>/dev/null || true
    fi
  fi
else
  echo ""
  echo "5. QMD not available — search will use grep (keyword-only)."
  echo "   Install QMD for hybrid semantic + keyword search."
fi

# --- 6. Claude Code plugin ---

echo ""
echo "6. Installing Claude Code plugin..."
if command -v claude >/dev/null 2>&1; then
  if claude plugin marketplace add https://github.com/dkushnikov/mnemon-plugin 2>/dev/null && \
     claude plugin install mnemon@mnemon-plugin 2>/dev/null; then
    echo "   ✓ Plugin installed (/source-add, /source-search, /source-status)"
  else
    echo "   ○ Plugin may already be installed, or install failed"
    echo "     Manual install: claude plugin marketplace add https://github.com/dkushnikov/mnemon-plugin"
    echo "                     claude plugin install mnemon@mnemon-plugin"
  fi
else
  echo "   ○ Claude Code not found — install plugin later:"
  echo "     claude plugin marketplace add https://github.com/dkushnikov/mnemon-plugin"
  echo "     claude plugin install mnemon@mnemon-plugin"
fi

# --- 7. Summary ---

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit your reader context (personalizes extractions):"
echo "     $VAULT_PATH/reader-context.md"
echo ""
echo "  2. Add your first source:"
echo "     /source-add https://example.com/interesting-article"
echo ""
