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
  ./setup.sh ~/Obsidian/Knowledge
  ./setup.sh ~/my-knowledge --non-interactive
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
CONFIG_PATH="$SCRIPT_DIR/mnemon.yaml"
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

if ! $SKIP_QMD && command -v qmd >/dev/null 2>&1; then
  echo ""
  echo "5. Configuring QMD search..."
  if qmd collection add mnemon "$VAULT_PATH/Sources" --mode hybrid 2>/dev/null; then
    echo "   ✓ QMD collection 'mnemon' configured"
    # Update config to use qmd
    if [[ -f "$CONFIG_PATH" ]]; then
      sed -i '' 's/^search_provider: grep/search_provider: qmd/' "$CONFIG_PATH" 2>/dev/null || true
    fi
  else
    echo "   ○ QMD collection may already exist"
  fi
else
  echo ""
  echo "5. QMD not available — search will use grep (keyword-only)."
  echo "   Install QMD for hybrid semantic + keyword search."
fi

# --- 6. Summary ---

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit your reader context (personalizes extractions):"
echo "     $VAULT_PATH/reader-context.md"
echo ""
echo "  2. Install the Claude Code plugin:"
echo "     claude plugin install $SCRIPT_DIR"
echo ""
echo "  3. Add your first source:"
echo "     /source-add https://example.com/interesting-article"
echo ""
