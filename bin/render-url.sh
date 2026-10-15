#!/usr/bin/env bash
# render-url.sh — render a JS-heavy page (SPA) via Chrome headless and emit clean text.
#
# Usage:
#   render-url.sh <url>                    # prints rendered text to stdout
#   render-url.sh <url> --html             # prints rendered HTML (post-JS) to stdout
#   render-url.sh <url> --timeout 15000    # virtual-time-budget in ms (default 10000)
#   render-url.sh <url> --max-bytes N       # output cap (render_max_bytes, default 20000000; exit 11 above it)
#
# Exit codes:
#   0  success, content on stdout
#   10 Chrome/Chromium not found — install hint on stderr
#   11 render produced empty / suspiciously-thin output, or output above --max-bytes
#   12 unsupported flag or missing URL
#
# Design:
# - Uses Chrome --headless --dump-dom (simplest CLI interface; no Node deps).
# - Extracts body text via lightweight Python (re-based) stripper — NOT defuddle.
#   Defuddle aggressively identifies a single "main" region and loses sections
#   on landing pages with multiple hero-equivalent blocks.
# - Does NOT handle interactions, waits, auth. Use Playwright MCP for those.
# - Boundary (capture layer D3): the policy checks the URL before Chrome launches; Chrome runs with a
#   throw-away profile and a mock keychain, no extensions, no sync, no first-run — but its own connection,
#   redirects, subresources and script requests are not policed, and --max-bytes caps only the DOM output.

set -uo pipefail

URL=""
MODE="text"   # text | html
TIMEOUT="10000"
MAX_BYTES="20000000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --html)    MODE="html"; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --max-bytes) MAX_BYTES="$2"; shift 2 ;;
    --help|-h) sed -n '2,24p' "$0"; exit 0 ;;
    --*)       echo "ERROR: unknown flag: $1" >&2; exit 12 ;;
    *)         URL="$1"; shift ;;
  esac
done

[[ -z "$URL" ]] && { echo "ERROR: URL required" >&2; exit 12; }

# Locate Chrome / Chromium
CHROME=""
for candidate in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "$(command -v google-chrome 2>/dev/null)" \
  "$(command -v chromium 2>/dev/null)"; do
  if [[ -x "$candidate" ]]; then CHROME="$candidate"; break; fi
done

if [[ -z "$CHROME" ]]; then
  cat >&2 <<'EOF'
ERROR: Chrome/Chromium not found.
Install Google Chrome (https://www.google.com/chrome/) or Chromium,
or use Playwright MCP for interaction-requiring renders.
EOF
  exit 10
fi

TMP=$(mktemp -t render-url.XXXXXX)
# A throw-away profile keeps headless Chrome off the user's running profile, and --use-mock-keychain (the flag
# Playwright/Puppeteer set by default) keeps it off the login keychain: from a sandboxed shell the keychain is
# unreachable (errSecNoDefaultKeychain, -25307) and macOS pops a "Keychain Not Found" dialog at the user whose
# "Reset To Defaults" button would replace their login keychain (seen 2026-10-15 during test runs).
PROFILE=$(mktemp -d -t render-url-profile.XXXXXX)
trap 'rm -rf "$TMP" "$PROFILE"' EXIT

"$CHROME" --headless --disable-gpu \
  --use-mock-keychain --user-data-dir="$PROFILE" \
  --disable-extensions --disable-sync --no-first-run \
  --virtual-time-budget="$TIMEOUT" \
  --dump-dom "$URL" 2>/dev/null > "$TMP"

SIZE=$(wc -c < "$TMP" | tr -d ' ')
if [[ "$SIZE" -lt 1000 ]]; then
  echo "ERROR: rendered output suspiciously small ($SIZE bytes)" >&2
  exit 11
fi
if [[ "$MAX_BYTES" =~ ^[0-9]+$ && "$SIZE" -gt "$MAX_BYTES" ]]; then
  echo "ERROR: rendered output $SIZE bytes exceeds --max-bytes $MAX_BYTES" >&2
  exit 11
fi

if [[ "$MODE" == "html" ]]; then
  cat "$TMP"
  exit 0
fi

# Text mode: strip tags, preserve block structure.
python3 - "$TMP" <<'PY'
import re, sys
with open(sys.argv[1]) as f:
    html = f.read()
html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL)
m = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL)
body = m.group(1) if m else html
# Block-level tags → newlines (preserve structure)
body = re.sub(
    r'</?(h[1-6]|p|li|div|section|article|br|hr|tr|table|ul|ol|nav|header|footer|main|aside)[^>]*>',
    '\n', body, flags=re.IGNORECASE)
# Strip remaining tags
text = re.sub(r'<[^>]+>', ' ', body)
# Entity decode (minimal)
text = re.sub(r'&nbsp;', ' ', text)
text = re.sub(r'&amp;', '&', text)
text = re.sub(r'&lt;', '<', text)
text = re.sub(r'&gt;', '>', text)
text = re.sub(r'&quot;', '"', text)
# Collapse whitespace while preserving paragraph breaks
text = re.sub(r'[ \t]+', ' ', text)
text = re.sub(r'\n\s*\n+', '\n\n', text)
lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 2]
print('\n\n'.join(lines))
PY
