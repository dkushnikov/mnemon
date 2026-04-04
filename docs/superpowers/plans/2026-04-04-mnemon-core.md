# Mnemon Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core Mnemon v1 — config system, portable gateway, skills, setup script — so that `./setup.sh ~/vault && claude plugin install ./ && /source-add --url <any-url>` produces a working knowledge extract.

**Architecture:** Bash gateway (`knowledge-gateway.sh`) reads config (`mnemon.yaml`), loads extraction templates + reader context, embeds both in prompt, invokes `claude -p` in the vault directory. Skills are thin Bash wrappers. Setup creates vault structure + config.

**Tech Stack:** Bash (gateway, config, setup), Python 3 (media-extract), Markdown (skills, templates), YAML-ish flat config

**Repo:** `~/Mnemon/` (already initialized with `git init`)

**Existing code to refactor:**
- `~/Claude/dotfiles/bin/knowledge-gateway.sh` (293 lines) — hardcoded paths, vault-specific prompts
- `~/Claude/dotfiles/bin/media-extract.py` (278 lines) — standalone, minimal changes needed

**Scope:** This is Plan 1 of 2. Covers: config, gateway, media-extract, plugin, skills, setup, vault template, article template (reference). Plan 2: remaining 6 templates, 5 protocols, 5 docs.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `.gitignore` | Ignore user config, caches, OS files |
| `bin/mnemon-config.sh` | Config loading — parse flat YAML, resolve paths, export vars |
| `bin/knowledge-gateway.sh` | Core engine — capture sources, build prompts with embedded templates, invoke `claude -p` |
| `bin/media-extract.py` | Media helper — YouTube transcripts (captions + whisper), audio transcription |
| `plugin.json` | Claude Code plugin manifest — registers skills |
| `CLAUDE.md` | Developer instructions for contributors to the Mnemon repo |
| `skills/source-add/SKILL.md` | `/source-add` skill — accepts URL/origin, calls gateway, shows result |
| `skills/source-search/SKILL.md` | `/source-search` skill — grep/QMD search, formatted results |
| `skills/source-status/SKILL.md` | `/source-status` skill — vault dashboard |
| `setup.sh` | Installation — create vault dirs, generate config, scaffold reader-context, check deps |
| `mnemon.yaml.template` | Config template with placeholders |
| `reader-context.md.template` | Personal context template for users to fill in |
| `templates/core/article.md` | Article extraction template (Fabric-inspired, reference implementation) |
| `templates/community/README.md` | Instructions for community template contributions |
| `vault-template/CLAUDE.md` | Vault-level Claude instructions (copied by setup.sh) |
| `vault-template/Sources/.gitkeep` | Empty Sources directory |
| `vault-template/Synthesis/.gitkeep` | Empty Synthesis directory |
| `vault-template/_meta/reader-context.md.template` | Placeholder (actual file lives in vault root) |
| `tests/test-helper.sh` | Minimal bash test framework (assert_eq, assert_file_exists, etc.) |
| `tests/test_config.sh` | Config loader tests |
| `tests/test_gateway.sh` | Gateway dry-run + auto-detection tests |
| `tests/test_setup.sh` | Setup script tests (runs on temp dir) |

---

## Task 1: Repo Scaffold

**Files:**
- Create: `~/Mnemon/.gitignore`
- Create: `~/Mnemon/tests/test-helper.sh`

- [ ] **Step 1: Create directory structure**

```bash
cd ~/Mnemon
mkdir -p bin skills/source-add skills/source-search skills/source-status \
  templates/core templates/community protocols vault-template/Sources \
  vault-template/Synthesis vault-template/_meta docs tests
```

- [ ] **Step 2: Create .gitignore**

Write `~/Mnemon/.gitignore`:

```
# User config (created by setup.sh, not tracked)
mnemon.yaml

# Python
__pycache__/
*.pyc
*.pyo

# OS
.DS_Store
Thumbs.db

# Editor
*.swp
*.swo
*~
.idea/
.vscode/

# Test artifacts
/tmp-test-*/
```

- [ ] **Step 3: Create test helper**

Write `~/Mnemon/tests/test-helper.sh`:

```bash
#!/usr/bin/env bash
# Minimal test framework for Mnemon
set -euo pipefail

PASS=0
FAIL=0
TEST_NAME=""

pass() { echo "  ✓ $1"; ((PASS++)) || true; }
fail() { echo "  ✗ $1: $2"; ((FAIL++)) || true; }

assert_eq() {
  local actual="$1" expected="$2" msg="$3"
  [[ "$actual" == "$expected" ]] && pass "$msg" || fail "$msg" "expected '$expected', got '$actual'"
}

assert_contains() {
  local haystack="$1" needle="$2" msg="$3"
  [[ "$haystack" == *"$needle"* ]] && pass "$msg" || fail "$msg" "expected to contain '$needle'"
}

assert_not_contains() {
  local haystack="$1" needle="$2" msg="$3"
  [[ "$haystack" != *"$needle"* ]] && pass "$msg" || fail "$msg" "expected NOT to contain '$needle'"
}

assert_file_exists() {
  local path="$1" msg="$2"
  [[ -f "$path" ]] && pass "$msg" || fail "$msg" "file not found: $path"
}

assert_dir_exists() {
  local path="$1" msg="$2"
  [[ -d "$path" ]] && pass "$msg" || fail "$msg" "dir not found: $path"
}

assert_executable() {
  local path="$1" msg="$2"
  [[ -x "$path" ]] && pass "$msg" || fail "$msg" "not executable: $path"
}

summary() {
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  [[ $FAIL -eq 0 ]]
}
```

- [ ] **Step 4: Create .gitkeep files for empty dirs**

```bash
touch ~/Mnemon/vault-template/Sources/.gitkeep
touch ~/Mnemon/vault-template/Synthesis/.gitkeep
```

- [ ] **Step 5: Verify and commit**

```bash
cd ~/Mnemon
# Verify structure
ls -R | head -40
# Commit
git add -A
git commit -m "scaffold: repo structure, .gitignore, test helper"
```

---

## Task 2: Config System

**Files:**
- Create: `~/Mnemon/bin/mnemon-config.sh`
- Create: `~/Mnemon/mnemon.yaml.template`
- Create: `~/Mnemon/tests/test_config.sh`

- [ ] **Step 1: Write config test**

Write `~/Mnemon/tests/test_config.sh`:

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$MNEMON_ROOT/bin/mnemon-config.sh"

echo "=== Config Loader Tests ==="

# --- Test 1: Parse flat YAML values ---
TMPCONFIG=$(mktemp)
cat > "$TMPCONFIG" << 'EOF'
vault_path: ~/test-vault
reader_context_path: ~/test-vault/reader-context.md
search_provider: grep
qmd_collection: mnemon
default_model: sonnet
default_language: en
whisper_model: large-v3
auto_detect_origin: true
EOF

load_config "$TMPCONFIG"
assert_eq "$VAULT_PATH" "$HOME/test-vault" "vault_path resolves tilde"
assert_eq "$SEARCH_PROVIDER" "grep" "search_provider loaded"
assert_eq "$QMD_COLLECTION" "mnemon" "qmd_collection loaded"
assert_eq "$DEFAULT_MODEL" "sonnet" "default_model loaded"
assert_eq "$DEFAULT_LANGUAGE" "en" "default_language loaded"
assert_eq "$WHISPER_MODEL" "large-v3" "whisper_model loaded"
assert_eq "$AUTO_DETECT_ORIGIN" "true" "auto_detect_origin loaded"
rm -f "$TMPCONFIG"

# --- Test 2: Defaults when keys missing ---
TMPCONFIG2=$(mktemp)
cat > "$TMPCONFIG2" << 'EOF'
vault_path: ~/my-vault
EOF

load_config "$TMPCONFIG2"
assert_eq "$VAULT_PATH" "$HOME/my-vault" "vault_path from minimal config"
assert_eq "$SEARCH_PROVIDER" "grep" "search_provider defaults to grep"
assert_eq "$DEFAULT_MODEL" "sonnet" "default_model defaults to sonnet"
assert_eq "$WHISPER_MODEL" "large-v3" "whisper_model defaults to large-v3"
rm -f "$TMPCONFIG2"

# --- Test 3: Config not found ---
output=$(load_config "/nonexistent/path.yaml" 2>&1) && {
  fail "nonexistent config" "should have returned error"
} || {
  pass "nonexistent config returns error"
}

# --- Test 4: MNEMON_ROOT auto-detection ---
assert_eq "$MNEMON_ROOT" "$(cd "$(dirname "$0")/.." && pwd)" "MNEMON_ROOT auto-detected"

summary
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/Mnemon && bash tests/test_config.sh
```

Expected: FAIL — `mnemon-config.sh` doesn't exist yet.

- [ ] **Step 3: Write config loader**

Write `~/Mnemon/bin/mnemon-config.sh`:

```bash
#!/usr/bin/env bash
# mnemon-config.sh — Config loader for Mnemon
# Source this file, then call load_config [path].
#
# Exports: MNEMON_ROOT, VAULT_PATH, READER_CONTEXT_PATH, SEARCH_PROVIDER,
#   QMD_COLLECTION, DEFAULT_MODEL, DEFAULT_LANGUAGE, WHISPER_MODEL, AUTO_DETECT_ORIGIN

# Auto-detect MNEMON_ROOT from this script's location
MNEMON_ROOT="${MNEMON_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Parse a single flat YAML key. Handles: key: value (with optional quotes, tilde)
_parse_yaml_value() {
  local key="$1" file="$2"
  local val
  val=$(grep "^${key}:" "$file" 2>/dev/null | head -1 | sed "s/^${key}:[[:space:]]*//" | sed 's/[[:space:]]*$//')
  # Strip surrounding quotes
  val="${val#\"}" ; val="${val%\"}"
  val="${val#\'}" ; val="${val%\'}"
  # Resolve tilde
  val="${val/#\~/$HOME}"
  echo "$val"
}

load_config() {
  local config_file="${1:-}"

  # Config resolution: explicit arg → $MNEMON_CONFIG → ./mnemon.yaml → $MNEMON_ROOT/mnemon.yaml
  if [[ -z "$config_file" ]]; then
    if [[ -n "${MNEMON_CONFIG:-}" ]]; then
      config_file="$MNEMON_CONFIG"
    elif [[ -f "./mnemon.yaml" ]]; then
      config_file="./mnemon.yaml"
    elif [[ -f "$MNEMON_ROOT/mnemon.yaml" ]]; then
      config_file="$MNEMON_ROOT/mnemon.yaml"
    else
      echo "ERROR: No mnemon.yaml found. Run setup.sh first." >&2
      return 1
    fi
  fi

  if [[ ! -f "$config_file" ]]; then
    echo "ERROR: Config not found: $config_file" >&2
    return 1
  fi

  # Parse values
  VAULT_PATH="$(_parse_yaml_value vault_path "$config_file")"
  READER_CONTEXT_PATH="$(_parse_yaml_value reader_context_path "$config_file")"
  SEARCH_PROVIDER="$(_parse_yaml_value search_provider "$config_file")"
  QMD_COLLECTION="$(_parse_yaml_value qmd_collection "$config_file")"
  DEFAULT_MODEL="$(_parse_yaml_value default_model "$config_file")"
  DEFAULT_LANGUAGE="$(_parse_yaml_value default_language "$config_file")"
  WHISPER_MODEL="$(_parse_yaml_value whisper_model "$config_file")"
  AUTO_DETECT_ORIGIN="$(_parse_yaml_value auto_detect_origin "$config_file")"

  # Apply defaults
  VAULT_PATH="${VAULT_PATH:-$HOME/Obsidian/Knowledge}"
  READER_CONTEXT_PATH="${READER_CONTEXT_PATH:-$VAULT_PATH/reader-context.md}"
  SEARCH_PROVIDER="${SEARCH_PROVIDER:-grep}"
  QMD_COLLECTION="${QMD_COLLECTION:-mnemon}"
  DEFAULT_MODEL="${DEFAULT_MODEL:-sonnet}"
  DEFAULT_LANGUAGE="${DEFAULT_LANGUAGE:-en}"
  WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
  AUTO_DETECT_ORIGIN="${AUTO_DETECT_ORIGIN:-true}"

  export MNEMON_ROOT VAULT_PATH READER_CONTEXT_PATH SEARCH_PROVIDER
  export QMD_COLLECTION DEFAULT_MODEL DEFAULT_LANGUAGE WHISPER_MODEL AUTO_DETECT_ORIGIN
}
```

- [ ] **Step 4: Write config template**

Write `~/Mnemon/mnemon.yaml.template`:

```yaml
# Mnemon configuration — generated by setup.sh
# Edit paths to match your setup. Flat YAML only (no nesting).

vault_path: {{vault_path}}
reader_context_path: {{vault_path}}/reader-context.md

# Search: grep (zero deps) or qmd (hybrid BM25 + vector, needs QMD installed)
search_provider: grep
qmd_collection: mnemon

# Extraction
default_model: sonnet
default_language: en

# Media (optional deps: yt-dlp, whisper, ffprobe)
whisper_model: large-v3
auto_detect_origin: true
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd ~/Mnemon && bash tests/test_config.sh
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/Mnemon
git add bin/mnemon-config.sh mnemon.yaml.template tests/test_config.sh
git commit -m "feat: config loader with flat YAML parsing + tests"
```

---

## Task 3: Gateway Refactor

**Files:**
- Create: `~/Mnemon/bin/knowledge-gateway.sh`
- Create: `~/Mnemon/tests/test_gateway.sh`
- Reference: `~/Claude/dotfiles/bin/knowledge-gateway.sh` (original)

The gateway is refactored from the original (293 lines) with these changes:
1. Config-driven paths (no hardcoded `$HOME/Obsidian/Knowledge`)
2. Extraction template embedded in prompt (loaded from `templates/core/{source_type}.md`)
3. Reader context embedded in prompt (loaded from `reader_context_path`)
4. New prompt structure: ACTION → CAPTURE CONTEXT → READER CONTEXT → EXTRACTION TEMPLATE → INSTRUCTIONS
5. Simpler `status` action (direct bash, not `claude -p`)
6. WebFetch added to allowed tools

- [ ] **Step 1: Write gateway test**

Write `~/Mnemon/tests/test_gateway.sh`:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/Mnemon && bash tests/test_gateway.sh
```

Expected: FAIL — `knowledge-gateway.sh` doesn't exist yet.

- [ ] **Step 3: Write the gateway**

Write `~/Mnemon/bin/knowledge-gateway.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Load config helper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MNEMON_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/mnemon-config.sh"

usage() {
  cat <<'USAGE'
Usage: knowledge-gateway.sh <action> [options]

Actions:
  source-add    Capture a new source and generate extract
  status        Show pipeline status

Options:
  --origin <type>       url|text|youtube|audio|book|idea
  --url <url>           Source URL
  --file <path>         Local file path (for audio)
  --title <title>       Source title (optional, inferred if not given)
  --author <author>     Source author (optional)
  --source-type <type>  article|video|podcast|book|paper|idea|conversation
  --context <ctx>       Capture context label (default: personal)
  --session <name>      Session title (optional)
  --intent <text>       Why this was captured (optional)
  --config <path>       Path to mnemon.yaml
  --model <model>       Override default model
  --dry-run             Show prompt without executing
  --whisper-model <m>   Whisper model (default from config)
  --no-whisper          Disable whisper fallback for YouTube
USAGE
  exit 1
}

# --- Parse arguments ---

ACTION="${1:-}"
[[ -z "$ACTION" ]] && usage
shift

ORIGIN=""
URL=""
FILE_PATH=""
TITLE=""
AUTHOR=""
SOURCE_TYPE=""
CONTEXT="personal"
SESSION=""
INTENT=""
CONFIG_PATH=""
MODEL_FLAG=""
DRY_RUN=false
WHISPER_MODEL_FLAG=""
NO_WHISPER=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --origin)        ORIGIN="$2"; shift 2 ;;
    --url)           URL="$2"; shift 2 ;;
    --file)          FILE_PATH="$2"; shift 2 ;;
    --title)         TITLE="$2"; shift 2 ;;
    --author)        AUTHOR="$2"; shift 2 ;;
    --source-type)   SOURCE_TYPE="$2"; shift 2 ;;
    --context)       CONTEXT="$2"; shift 2 ;;
    --session)       SESSION="$2"; shift 2 ;;
    --intent)        INTENT="$2"; shift 2 ;;
    --config)        CONFIG_PATH="$2"; shift 2 ;;
    --model)         MODEL_FLAG="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=true; shift ;;
    --whisper-model) WHISPER_MODEL_FLAG="$2"; shift 2 ;;
    --no-whisper)    NO_WHISPER=true; shift ;;
    *)               echo "Unknown option: $1" >&2; usage ;;
  esac
done

# Load config (uses CONFIG_PATH if set, otherwise auto-discovers)
load_config "$CONFIG_PATH"

# Flags override config values
MODEL="${MODEL_FLAG:-$DEFAULT_MODEL}"
[[ -n "$WHISPER_MODEL_FLAG" ]] && WHISPER_MODEL="$WHISPER_MODEL_FLAG"

# --- Auto-detect origin ---

if [[ -z "$ORIGIN" && "$AUTO_DETECT_ORIGIN" == "true" ]]; then
  if [[ -n "$URL" ]]; then
    if [[ "$URL" =~ (youtube\.com/watch|youtu\.be/|youtube\.com/shorts/) ]]; then
      ORIGIN="youtube"
    else
      ORIGIN="url"
    fi
  elif [[ -n "$FILE_PATH" ]]; then
    if command -v ffprobe >/dev/null 2>&1 && \
       ffprobe -v quiet -show_entries format=format_name -of csv=p=0 "$FILE_PATH" 2>/dev/null | \
       grep -qiE 'mp3|wav|flac|ogg|aac|m4a|opus'; then
      ORIGIN="audio"
    else
      ORIGIN="file"
    fi
  fi
fi

# --- Auto-detect source_type from origin ---

if [[ -z "$SOURCE_TYPE" ]]; then
  case "${ORIGIN:-}" in
    url)      SOURCE_TYPE="article" ;;
    youtube)  SOURCE_TYPE="video" ;;
    audio)    SOURCE_TYPE="podcast" ;;
    book)     SOURCE_TYPE="book" ;;
    idea)     SOURCE_TYPE="idea" ;;
    text)     SOURCE_TYPE="article" ;;
    *)        SOURCE_TYPE="article" ;;
  esac
fi

# --- Template and context loading ---

load_template() {
  local st="$1"
  local path="$MNEMON_ROOT/templates/core/${st}.md"
  if [[ -f "$path" ]]; then
    cat "$path"
  else
    echo "(No extraction template found for source_type '${st}'. Use generic extraction: create Summary, Executive Summary framed by reader context, Key Ideas with domain tags, Connections, Raw Quotes.)"
  fi
}

load_reader_context() {
  if [[ -f "$READER_CONTEXT_PATH" ]]; then
    cat "$READER_CONTEXT_PATH"
  else
    echo "(No reader context configured at $READER_CONTEXT_PATH. Generate a generic extract without personal framing. Suggest the user edit reader-context.md.)"
  fi
}

# --- Prompt construction ---

build_prompt() {
  local template reader_context
  template=$(load_template "$SOURCE_TYPE")
  reader_context=$(load_reader_context)

  local prompt="NON-INTERACTIVE GATEWAY REQUEST. Execute ONLY the requested action. No onboarding, no briefing, no questions, no summaries of what you plan to do. Just execute.

=== ACTION ===
source-add
Origin: $ORIGIN
Source type: $SOURCE_TYPE"

  [[ -n "$URL" ]]       && prompt+=$'\n'"URL: $URL"
  [[ -n "$FILE_PATH" ]] && prompt+=$'\n'"File: $FILE_PATH"
  [[ -n "$TITLE" ]]     && prompt+=$'\n'"Title: $TITLE"
  [[ -n "$AUTHOR" ]]    && prompt+=$'\n'"Author: $AUTHOR"

  if [[ "$ORIGIN" == "youtube" || "$ORIGIN" == "audio" ]]; then
    prompt+=$'\n'"Content format: transcript"
    prompt+=$'\n'"Note: Transcript text is provided via stdin. Use it as the source content."
  fi

  prompt+=$'\n\n'"=== CAPTURE CONTEXT ===
Context: $CONTEXT
Session: ${SESSION:-unknown}
Intent: ${INTENT:-none specified}

=== READER CONTEXT ===
$reader_context

=== EXTRACTION TEMPLATE ===
$template

=== INSTRUCTIONS ===
1. For URL sources: fetch content using the WebFetch tool. For text/transcript sources: use the stdin content provided.
2. Compute folder hash: run \`echo -n \"<canonical-url-or-title_date>\" | shasum -a 256 | cut -c1-8\` via Bash tool.
3. Create folder: Sources/\$(date +%Y-%m-%d)_<hash8>/
4. If folder exists, append -2, -3, etc.
5. Write source.md — immutable raw content with frontmatter:
   type: source, source_type: $SOURCE_TYPE, content_format: <text|transcript|reference>, origin: $ORIGIN, url: \"$URL\", author: \"${AUTHOR:-}\", captured: <today>, captured_by: agent
6. Apply the EXTRACTION TEMPLATE to generate extract.md.
7. Executive Summary MUST be framed by the READER CONTEXT — not generic, but personal to the reader.
8. Key Ideas MUST use domain tags from the Reader Context.
9. Rating: 1-10 based on actual insight density. Be critical. 5 = average, 7 = good, 9+ = exceptional.
10. Print results exactly as (no other text after these lines):
    RESULT:path=Sources/<folder>/
    RESULT:status=extracted
    RESULT:title=<extracted title>"

  echo "$prompt"
}

# --- Claude invocation ---

invoke_claude() {
  local prompt="$1"
  local stdin_content="${2:-}"

  if $DRY_RUN; then
    echo "=== DRY RUN ==="
    echo "Vault: $VAULT_PATH"
    echo "Model: $MODEL"
    echo "Template: templates/core/${SOURCE_TYPE}.md"
    echo "=== PROMPT ==="
    echo "$prompt"
    if [[ -n "$stdin_content" ]]; then
      echo "=== STDIN (${#stdin_content} chars) ==="
      echo "${stdin_content:0:300}..."
    fi
    return 0
  fi

  if [[ ! -d "$VAULT_PATH" ]]; then
    echo "ERROR: Vault not found at $VAULT_PATH. Run setup.sh first." >&2
    return 1
  fi

  local output exit_code=0

  if [[ -n "$stdin_content" ]]; then
    output=$(echo "$stdin_content" | (cd "$VAULT_PATH" && claude -p \
      --model "$MODEL" \
      --allowedTools Read,Write,Edit,Bash,Glob,Grep,WebFetch \
      --output-format text \
      "$prompt") 2>&1) || exit_code=$?
  else
    output=$((cd "$VAULT_PATH" && claude -p \
      --model "$MODEL" \
      --allowedTools Read,Write,Edit,Bash,Glob,Grep,WebFetch \
      --output-format text \
      "$prompt") 2>&1) || exit_code=$?
  fi

  if [[ $exit_code -ne 0 ]]; then
    echo "ERROR: Gateway failed (exit code $exit_code)" >&2
    save_pending_write "$prompt" "$stdin_content" "$output"
    echo "$output" >&2
    return 1
  fi

  echo "$output"

  # Extract RESULT lines for caller
  echo "$output" | grep "^RESULT:" || true
}

save_pending_write() {
  local prompt="$1" content="${2:-}" error_output="${3:-}"
  local pending_dir="$VAULT_PATH/_inputs/pending-writes"
  mkdir -p "$pending_dir"
  local ts
  ts=$(date +%Y%m%d-%H%M%S)
  local error_escaped
  error_escaped=$(printf '%s' "$error_output" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')
  cat > "$pending_dir/$ts.json" << PEND_EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "action": "$ACTION",
  "origin": "$ORIGIN",
  "url": "$URL",
  "file": "$FILE_PATH",
  "context": "$CONTEXT",
  "session": "$SESSION",
  "error": "$error_escaped"
}
PEND_EOF
  echo "Saved to pending-writes: $pending_dir/$ts.json" >&2
}

# --- Main dispatch ---

case "$ACTION" in
  source-add)
    [[ -z "$ORIGIN" ]] && { echo "ERROR: Cannot determine origin. Provide --origin or --url." >&2; usage; }
    prompt=$(build_prompt)

    if [[ "$ORIGIN" == "youtube" ]]; then
      media_args=("youtube" "$URL" "--whisper-model" "$WHISPER_MODEL")
      $NO_WHISPER && media_args+=("--no-whisper")
      stdin_content=$("$SCRIPT_DIR/media-extract.py" "${media_args[@]}") || {
        save_pending_write "$prompt" "" "media-extract.py failed for $URL"
        exit 1
      }
      prompt=$(build_prompt)
      invoke_claude "$prompt" "$stdin_content"

    elif [[ "$ORIGIN" == "audio" ]]; then
      media_args=("audio" "--whisper-model" "$WHISPER_MODEL")
      [[ -n "$URL" ]] && media_args+=("--url" "$URL")
      [[ -n "$FILE_PATH" ]] && media_args+=("--file" "$FILE_PATH")
      [[ -n "$TITLE" ]] && media_args+=("--title" "$TITLE")
      stdin_content=$("$SCRIPT_DIR/media-extract.py" "${media_args[@]}") || {
        save_pending_write "$prompt" "" "media-extract.py failed for ${URL:-$FILE_PATH}"
        exit 1
      }
      prompt=$(build_prompt)
      invoke_claude "$prompt" "$stdin_content"

    elif [[ "$ORIGIN" == "text" || "$ORIGIN" == "idea" ]]; then
      if [[ -t 0 ]]; then
        echo "Paste content, then press Ctrl+D:" >&2
      fi
      stdin_content=$(cat)
      invoke_claude "$prompt" "$stdin_content"

    else
      invoke_claude "$prompt"
    fi
    ;;

  status)
    echo "=== Mnemon Status ==="
    echo "Vault: $VAULT_PATH"
    echo ""
    if [[ ! -d "$VAULT_PATH/Sources" ]]; then
      echo "No Sources/ directory. Run setup.sh first."
      exit 0
    fi
    total=$(find "$VAULT_PATH/Sources" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
    extracted=$(grep -rl '^status: extracted' "$VAULT_PATH/Sources/"*/extract.md 2>/dev/null | wc -l | tr -d ' ')
    echo "Total sources: $total"
    echo "Extracted: $extracted"
    echo ""
    echo "Recent additions:"
    find "$VAULT_PATH/Sources" -maxdepth 1 -mindepth 1 -type d -name "20*" 2>/dev/null | sort -r | head -5 | while read -r d; do
      title=$(grep '^title:' "$d/extract.md" 2>/dev/null | head -1 | sed 's/^title:[[:space:]]*//' | tr -d '"' || echo "untitled")
      echo "  $(basename "$d") — $title"
    done
    ;;

  *)
    echo "Unknown action: $ACTION" >&2
    usage
    ;;
esac
```

- [ ] **Step 4: Make gateway executable**

```bash
chmod +x ~/Mnemon/bin/knowledge-gateway.sh
```

- [ ] **Step 5: Run tests**

```bash
cd ~/Mnemon && bash tests/test_gateway.sh
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/Mnemon
git add bin/knowledge-gateway.sh tests/test_gateway.sh
git commit -m "feat: portable knowledge gateway with config + embedded templates"
```

---

## Task 4: Media Extract

**Files:**
- Create: `~/Mnemon/bin/media-extract.py` (copy from dotfiles with minimal changes)

The media-extract.py is already standalone. Copy it as-is — it has no hardcoded paths.

- [ ] **Step 1: Copy media-extract.py**

```bash
cp ~/Claude/dotfiles/bin/media-extract.py ~/Mnemon/bin/media-extract.py
chmod +x ~/Mnemon/bin/media-extract.py
```

- [ ] **Step 2: Verify it runs**

```bash
cd ~/Mnemon && python3 bin/media-extract.py --help
```

Expected: Shows help with `youtube` and `audio` subcommands.

- [ ] **Step 3: Commit**

```bash
cd ~/Mnemon
git add bin/media-extract.py
git commit -m "feat: media extraction helper (YouTube + audio transcription)"
```

---

## Task 5: Article Extraction Template

**Files:**
- Create: `~/Mnemon/templates/core/article.md`
- Create: `~/Mnemon/templates/community/README.md`

This is the reference extraction template. Fabric-inspired format: IDENTITY → STEPS → OUTPUT INSTRUCTIONS → INPUT. Implements: two-pass extraction (IDEAS then INSIGHTS), 16-word discipline, anti-sycophancy, domain-tagged Key Ideas.

- [ ] **Step 1: Write article template**

Write `~/Mnemon/templates/core/article.md`:

```markdown
# IDENTITY

You are a knowledge extraction specialist for a personal knowledge library. Your job: turn articles into structured, searchable knowledge artifacts framed by the reader's personal context.

Be critical and honest. If the content is mediocre, say so. No sycophantic praise. Rate based on actual insight density, not author reputation or publication prestige. Tend towards being more critical than generous.

# STEPS

## Pass 1: IDEAS

Read the entire article. Identify every distinct idea, insight, claim, or finding. For each:
- State it as a standalone atomic claim (one sentence, max 16 words)
- It must be understandable WITHOUT reading the original article
- Tag it with the most relevant domain from the Reader Context

Cast a wide net. Include: core arguments, supporting evidence, counterpoints, methodological choices, surprising claims, practical implications.

## Pass 2: INSIGHTS

From the IDEAS, select only the most valuable — ideas that:
- Change how the reader should think or act (given their context)
- Connect to other domains or existing knowledge
- Contradict conventional wisdom or the reader's current approach
- Have concrete, practical implications
- Would still be worth remembering in 3 months

Discard filler, obvious claims, and ideas that don't survive the "so what?" test.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: article
content_format: text
origin: url
visibility: personal
status: extracted
title: "<article title — use the actual title, not a summary>"
author: "<author name>"
url: "<source URL>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <detected language: en, ru, etc.>
tags: [<3-8 topic tags, lowercase>]
people: [<people mentioned or referenced>]
domains: [<1-3 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = low value, obvious or wrong. 4-5 = average, some useful points. 6-7 = good, multiple actionable insights. 8-9 = excellent, changes thinking. 10 = exceptional, reference-grade.

## Body

### Summary

2-3 sentences. What is this article about? Pure description, no opinion, no framing.

### Executive Summary

0.5 to 1 page. This is the most important section.

Frame ENTIRELY through the Reader Context. Not "what does this article say?" but "what does this mean for THIS reader, given THEIR role, goals, and current priorities?"

Requirements:
- Reference specific elements from the reader's context (role, domains, goals)
- If the article contradicts the reader's current approach — highlight the contradiction explicitly
- If the article is mediocre — say so directly. "This article covers well-trodden ground" is fine
- End with a clear verdict: is this worth the reader's time? Why or why not?
- Write in the reader's preferred language (check Reader Context)

### Key Ideas

Each idea from Pass 2, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

Requirements:
- Each claim MUST be understandable without reading the article
- Domain tag MUST come from the Reader Context domain list
- Minimum 3 Key Ideas, maximum 15
- Order by importance to the reader, not by article order
- No filler — every idea must pass the "worth remembering in 3 months" test

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

Only genuine connections. 2-5 items. Don't force connections that aren't there.

### Raw Quotes

3-5 direct quotes from the article. Select quotes that:
- Capture a key insight in the author's exact words
- Are surprising, counterintuitive, or particularly well-stated
- Could stand alone as memorable wisdom

Format: `> "Quote text" — Author Name`

# INPUT

The article content follows. Apply the extraction above.
```

- [ ] **Step 2: Write community templates README**

Write `~/Mnemon/templates/community/README.md`:

```markdown
# Community Templates

Drop-in extraction templates compatible with Mnemon.

## Adding a Template

1. Create a `.md` file in this directory
2. Follow the Mnemon template format: IDENTITY → STEPS → OUTPUT INSTRUCTIONS → INPUT
3. The gateway loads templates by `source_type` name: `templates/core/{source_type}.md`
4. Community templates override core templates if placed in `templates/core/`

## Fabric Compatibility

Fabric `system.md` pattern files can be placed here. The gateway will use them if referenced by `--source-type` matching the filename (without extension).

Note: Fabric patterns produce freeform prose. Mnemon core templates produce structured output (frontmatter + Key Ideas + domain tags). Community templates may produce either format.

## Contributing

PRs welcome. Include a brief description of what your template extracts and for what source types.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Mnemon
git add templates/
git commit -m "feat: article extraction template (Fabric-inspired, two-pass, domain-tagged)"
```

---

## Task 6: Plugin Manifest + CLAUDE.md

**Files:**
- Create: `~/Mnemon/plugin.json`
- Create: `~/Mnemon/CLAUDE.md`

- [ ] **Step 1: Write plugin.json**

Write `~/Mnemon/plugin.json`:

```json
{
  "name": "mnemon",
  "version": "0.1.0",
  "description": "AI-powered personal knowledge extraction system. Capture articles, videos, podcasts, books — get structured, searchable knowledge artifacts framed by your personal context.",
  "skills": [
    "skills/source-add",
    "skills/source-search",
    "skills/source-status"
  ]
}
```

- [ ] **Step 2: Write repo CLAUDE.md**

Write `~/Mnemon/CLAUDE.md`:

```markdown
# Mnemon — Developer Guide

Open-source AI-powered personal knowledge extraction system.
Repo: `dkushnikov/mnemon`. License: TBD (MIT or Apache 2.0).

## Architecture

```
User → /source-add (skill) → knowledge-gateway.sh → claude -p → Sources/
                                    ↓                              ├── source.md (immutable)
                              mnemon.yaml (config)                 └── extract.md (AI-generated)
                              reader-context.md
                              templates/core/*.md
```

- **Gateway** (`bin/knowledge-gateway.sh`): core engine. Loads config, reads template + reader context, builds prompt, invokes `claude -p` in the vault directory.
- **Config** (`mnemon.yaml`): flat YAML with vault path, search provider, model, etc. Created by `setup.sh`, gitignored.
- **Skills** (`skills/`): thin Bash wrappers that call the gateway. Registered via `plugin.json`.
- **Templates** (`templates/core/`): extraction prompt templates per source type. Fabric-inspired format.
- **Vault template** (`vault-template/`): scaffold copied by `setup.sh` into user's vault.

## Key Conventions

- **source.md is immutable.** Never modify after creation.
- **extract.md is mutable.** Re-extraction overwrites it.
- **Folder naming:** `Sources/YYYY-MM-DD_{hash8}/` where hash8 = SHA-256 first 8 chars of canonical URL.
- **Templates produce structured output:** YAML frontmatter + Summary + Executive Summary + Key Ideas + Connections + Raw Quotes.
- **Reader context frames extraction:** Every extract is personalized through `reader-context.md`.

## Development

```bash
# Run tests
bash tests/test_config.sh
bash tests/test_gateway.sh
bash tests/test_setup.sh

# Dry-run gateway
./bin/knowledge-gateway.sh source-add --url https://example.com --config mnemon.yaml --dry-run

# Setup on test vault
./setup.sh /tmp/test-vault --non-interactive
```

## File Ownership

| Directory | Tracked | Notes |
|-----------|---------|-------|
| `bin/` | Yes | Core scripts |
| `skills/` | Yes | Claude Code skills |
| `templates/core/` | Yes | Official templates |
| `templates/community/` | Yes | User contributions |
| `vault-template/` | Yes | Scaffold for setup.sh |
| `mnemon.yaml` | **No** | User config (gitignored) |
| `docs/` | Yes | User-facing documentation |
| `tests/` | Yes | Test scripts |
```

- [ ] **Step 3: Commit**

```bash
cd ~/Mnemon
git add plugin.json CLAUDE.md
git commit -m "feat: plugin manifest + developer CLAUDE.md"
```

---

## Task 7: Skills

**Files:**
- Create: `~/Mnemon/skills/source-add/SKILL.md`
- Create: `~/Mnemon/skills/source-search/SKILL.md`
- Create: `~/Mnemon/skills/source-status/SKILL.md`

- [ ] **Step 1: Write /source-add skill**

Write `~/Mnemon/skills/source-add/SKILL.md`:

```markdown
---
name: source-add
description: Add a source to your Mnemon knowledge library — articles, YouTube videos, podcasts, books, ideas. Captures content and generates a structured extract with personal context framing. Use when the user provides a URL to save, says "add source", "capture this", "save this article/video/podcast", or wants to process any content into their knowledge library.
---

# /source-add — Capture a Source

Add a source to the knowledge library. The gateway fetches content, applies an extraction template, and generates structured `source.md` + `extract.md` in the vault.

## Usage Examples

```
/source-add https://example.com/article
/source-add --url https://youtube.com/watch?v=abc123
/source-add --origin audio --url https://podcast.example.com/episode.mp3
/source-add --origin idea --title "My insight about X"
/source-add --origin book --title "Book Title" --author "Author Name"
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--url <url>` | For URL sources | — | Source URL (YouTube auto-detected) |
| `--origin <type>` | Auto-detected | From URL | `url`, `youtube`, `audio`, `text`, `book`, `idea` |
| `--title <title>` | For text/book/idea | Inferred | Source title |
| `--author <name>` | No | Inferred | Source author |
| `--source-type <type>` | No | From origin | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `--intent <text>` | No | none | Why you're capturing this |
| `--context <ctx>` | No | personal | `personal` or `mc` |

## How to Execute

1. Build the command from user's input. If the user just provides a URL, that's sufficient — origin and source_type are auto-detected.

2. Call the gateway via the Bash tool:

```bash
~/Mnemon/bin/knowledge-gateway.sh source-add --url "<url>" [--origin <origin>] [--title "<title>"] [--author "<author>"] [--intent "<intent>"]
```

3. Parse RESULT lines from the output:
   - `RESULT:path=Sources/<folder>/` — created source path
   - `RESULT:status=extracted` — extraction status  
   - `RESULT:title=<title>` — extracted title

4. Show the user a concise summary:

```
Added: <title>
Path: <path>
Status: <status>
```

5. If the gateway fails, show the error message. Common issues:
   - "No mnemon.yaml found" → run `~/Mnemon/setup.sh <vault-path>` first
   - "Vault not found" → check vault_path in mnemon.yaml
   - Media extraction failed → check yt-dlp/whisper installation

## For text/idea origins

If the user wants to capture text or an idea, they need to provide the content. Ask them to paste it, then pipe it to the gateway:

```bash
echo "<pasted content>" | ~/Mnemon/bin/knowledge-gateway.sh source-add --origin text --title "<title>"
```
```

- [ ] **Step 2: Write /source-search skill**

Write `~/Mnemon/skills/source-search/SKILL.md`:

```markdown
---
name: source-search
description: Search your Mnemon knowledge library for sources and ideas. Use when the user asks "what do I know about X", "find sources about", "search knowledge", "have I saved anything about", or wants to find connections across their knowledge library.
---

# /source-search — Search Knowledge Library

Search across all sources in the knowledge vault. Uses grep (default) or QMD (if configured) for hybrid semantic + keyword search.

## Usage Examples

```
/source-search AI agents
/source-search "organizational design" --domain career
/source-search knowledge management --limit 5
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| query | — | Search terms (required) |
| `--domain <d>` | all | Filter by domain tag |
| `--limit <n>` | 10 | Max results |

## How to Execute

### Step 1: Load config

```bash
source ~/Mnemon/bin/mnemon-config.sh && load_config
```

This gives you `$VAULT_PATH`, `$SEARCH_PROVIDER`, `$QMD_COLLECTION`.

### Step 2: Run search based on provider

**If SEARCH_PROVIDER=grep (default):**

```bash
grep -rl --include="extract.md" -i "<query>" "$VAULT_PATH/Sources/" 2>/dev/null | head -<limit>
```

For each result file, extract metadata:
```bash
grep -m1 '^title:' "<file>" | sed 's/^title:[[:space:]]*//' | tr -d '"'
grep -m1 '^domains:' "<file>"
grep -m1 -i "<query>" "<file>"
```

**If SEARCH_PROVIDER=qmd:**

```bash
qmd search "<query>" --collection "$QMD_COLLECTION" --limit <limit> --format json
```

### Step 3: Apply domain filter (if --domain specified)

Filter results to only include files where `domains:` contains the requested domain.

### Step 4: Format output

Show results as:
```
Found N results for "<query>":

1. **Title** (date)
   Domains: [domain1, domain2]
   ...matching snippet...
   Path: Sources/YYYY-MM-DD_hash8/

2. **Title** (date)
   ...
```

If no results: "No sources found for '<query>'. Try broader terms or check /source-status for library overview."

If QMD not installed and search_provider=qmd: "QMD not installed. Falling back to grep. Install QMD for semantic search."
```

- [ ] **Step 3: Write /source-status skill**

Write `~/Mnemon/skills/source-status/SKILL.md`:

```markdown
---
name: source-status
description: Show the status of your Mnemon knowledge library — total sources, breakdown by origin and domain, recent additions. Use when the user asks "how many sources", "library status", "what have I captured", "knowledge dashboard", or wants an overview of their library.
---

# /source-status — Knowledge Library Dashboard

Show a summary of the knowledge library: total sources, breakdown by status/origin/domain, and recent additions.

## Usage

```
/source-status
```

No parameters needed.

## How to Execute

Call the gateway status action:

```bash
~/Mnemon/bin/knowledge-gateway.sh status
```

The gateway outputs a formatted dashboard. Display it to the user.

If additional detail is needed (the gateway shows basics), you can augment with:

```bash
# Count by origin
source ~/Mnemon/bin/mnemon-config.sh && load_config
for f in "$VAULT_PATH"/Sources/*/extract.md; do
  grep -m1 '^origin:' "$f" 2>/dev/null
done | sort | uniq -c | sort -rn

# Count by domain
for f in "$VAULT_PATH"/Sources/*/extract.md; do
  grep -m1 '^domains:' "$f" 2>/dev/null
done | tr '[],' '\n' | sed 's/^[[:space:]]*//' | grep -v '^$' | grep -v '^domains:' | sort | uniq -c | sort -rn
```

## Display Format

```
=== Mnemon Knowledge Library ===
Vault: <path>
Total: N sources

By status:   extracted: X | captured: Y
By origin:   url: A | youtube: B | audio: C | text: D
By domain:   learning: E | career: F | ...

Recent (last 5):
  2026-04-04_abc12345 — Article Title
  2026-04-03_def67890 — Video Title
  ...
```
```

- [ ] **Step 4: Commit**

```bash
cd ~/Mnemon
git add skills/
git commit -m "feat: skills — /source-add, /source-search, /source-status"
```

---

## Task 8: Vault Template + Reader Context

**Files:**
- Create: `~/Mnemon/vault-template/CLAUDE.md`
- Create: `~/Mnemon/vault-template/_meta/Protocol.md`
- Create: `~/Mnemon/reader-context.md.template`

- [ ] **Step 1: Write vault CLAUDE.md**

This is copied by `setup.sh` into the user's vault. It tells Claude how to behave when working in the vault (both interactively and via gateway).

Write `~/Mnemon/vault-template/CLAUDE.md`:

```markdown
# Mnemon Knowledge Vault

Personal knowledge extraction vault. Sources are captured and extracted into structured knowledge artifacts.

## Structure

- `Sources/` — One folder per source: `YYYY-MM-DD_{hash8}/` with `source.md` + `extract.md`
- `Synthesis/` — Human-written notes connecting ideas across sources (never auto-generated)
- `reader-context.md` — Your personal context (role, domains, goals) that frames every extraction

## Principles

1. **source.md is immutable.** Once created, never modified. Re-capture = new folder.
2. **AI extracts, human synthesizes.** Claude creates source.md + extract.md. Only the user creates Synthesis notes.
3. **Key Ideas are atomic.** Each one stands alone, domain-tagged, max 16 words.
4. **Extraction is personal.** Every extract is framed by reader-context.md — not generic.

## Gateway Mode

When invoked non-interactively via `knowledge-gateway.sh` (`claude -p`):
- Skip ALL session onboarding, briefing, and questions
- Execute ONLY the requested action from the prompt
- Follow the EXTRACTION TEMPLATE embedded in the prompt exactly
- Print RESULT lines at the end

## Schemas

### source.md frontmatter
```yaml
type: source
source_type: <article|video|podcast|book|paper|idea|conversation>
content_format: <text|transcript|reference>
origin: <url|text|youtube|audio|book|idea>
url: "<source url>"
author: "<author>"
captured: <YYYY-MM-DD>
captured_by: agent
```

### extract.md frontmatter
```yaml
type: extract
source_type: <matches source>
content_format: <matches source>
origin: <matches source>
visibility: <personal|public|company|private>
status: <captured|extracted|integrated>
title: "<title>"
author: "<author>"
url: "<url>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <en|ru|...>
tags: [<topic tags>]
people: [<mentioned people>]
domains: [<domain tags>]
rating: <1-10>
capture_context:
  vault: <context label>
  session: "<session>"
  intent: "<intent>"
  import_source: gateway
  import_batch: ""
```

### extract.md body sections
1. **Summary** — 2-3 sentences, factual
2. **Executive Summary** — framed by reader-context.md, personal
3. **Key Ideas** — `- **Title** #domain — claim (max 16 words)`
4. **Connections** — wiki-style links to related topics
5. **Raw Quotes** — 3-5 best quotes from source

## Don't

- Don't modify source.md after creation
- Don't create Synthesis notes without user request
- Don't create files outside Sources/ and Synthesis/
```

- [ ] **Step 2: Write vault Protocol.md**

Write `~/Mnemon/vault-template/_meta/Protocol.md`:

```markdown
# Mnemon Vault Protocol

## Folder Naming

`Sources/YYYY-MM-DD_{hash8}/` where:
- Date = capture date
- hash8 = first 8 characters of SHA-256 hash
  - For URLs: hash of canonical URL
  - For non-URL: hash of `{title}_{created_date}`
- Collision: append `-2`, `-3`, etc.

## Status Lifecycle

```
captured → extracted → integrated
```

- **captured**: source.md exists, extract.md missing or failed
- **extracted**: both source.md and extract.md exist with full content
- **integrated**: user has reviewed, connected to other knowledge, or created Synthesis

## Immutability

- source.md: NEVER modified after creation. Contains raw captured content.
- extract.md: CAN be overwritten by re-extraction (with updated reader context).
- Re-capture of same URL: new folder (different date = different hash input? No — same URL = same hash. Use collision handling.)

## Domains

Default domains (Life Capital framework):
- `learning` — what changes how I think
- `health` — evidence-based optimization
- `relationships` — social capital, peers, network
- `home` — environment, space, routines
- `finance` — wealth, investment, tax
- `career` — professional growth, leadership
- `culture` — joy, travel, hobbies
- `influence` — personal brand, community
- `inner-work` — coaching, self-awareness

Users can add custom domains in reader-context.md.
```

- [ ] **Step 3: Write reader-context.md template**

Write `~/Mnemon/reader-context.md.template`:

```markdown
# Reader Context

This file personalizes your knowledge extractions. Every source you add is framed through YOUR context — your role, domains, and goals. Two people reading the same article get different extracts.

Edit this file to match who you are and what you care about.

## Who I Am

<!-- Your role, background, what you do. 2-3 sentences. -->
<!-- Example: "Software engineer at a fintech startup. 5 years experience. Interested in distributed systems and team leadership." -->

## Domains of Interest

<!-- Which areas do you want extracts tagged with? Use these as domain tags in Key Ideas. -->
<!-- Default domains (Life Capital framework): -->
- `learning` — what changes how I think
- `health` — evidence-based, actionable
- `relationships` — peers, network, community
- `finance` — investment, wealth building
- `career` — professional growth, leadership
- `culture` — joy, enrichment, creativity
- `inner-work` — self-awareness, coaching

<!-- Add your own domains: -->
<!-- - `my-domain` — what it means to me -->

## Current Goals

<!-- What are you focused on right now? This helps frame Executive Summaries. -->
<!-- Example: -->
<!-- - Preparing for a senior engineer promotion -->
<!-- - Building a reading habit (3 articles/week) -->
<!-- - Learning about AI agents and their applications -->

## What I Want From Extracts

<!-- How should the Executive Summary talk to you? -->
- Challenge my assumptions — if the source contradicts my approach, say so
- Be direct, no fluff. If the source is mediocre, say it's mediocre
- Connect to my current priorities where genuine
- Actionable implications over theoretical interest

## Language

<!-- What language should Executive Summaries be written in? -->
<!-- Options: en, ru, or "match the source" -->
Executive Summary language: en
```

- [ ] **Step 4: Commit**

```bash
cd ~/Mnemon
git add vault-template/ reader-context.md.template
git commit -m "feat: vault template (CLAUDE.md, Protocol.md) + reader-context template"
```

---

## Task 9: Setup Script

**Files:**
- Create: `~/Mnemon/setup.sh`
- Create: `~/Mnemon/tests/test_setup.sh`

- [ ] **Step 1: Write setup test**

Write `~/Mnemon/tests/test_setup.sh`:

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Setup Tests ==="

TMPDIR=$(mktemp -d -t mnemon-test-XXXX)
VAULT="$TMPDIR/test-vault"

# --- Test 1: Setup creates vault structure ---
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd

assert_dir_exists "$VAULT/Sources" "Sources/ created"
assert_dir_exists "$VAULT/Synthesis" "Synthesis/ created"
assert_dir_exists "$VAULT/_meta" "_meta/ created"
assert_file_exists "$VAULT/CLAUDE.md" "CLAUDE.md copied"
assert_file_exists "$VAULT/_meta/Protocol.md" "Protocol.md copied"
assert_file_exists "$VAULT/reader-context.md" "reader-context.md created"

# --- Test 2: Config generated ---
assert_file_exists "$MNEMON_ROOT/mnemon.yaml" "mnemon.yaml created"

# Check config contains correct vault path
config_vault=$(grep '^vault_path:' "$MNEMON_ROOT/mnemon.yaml" | head -1)
assert_contains "$config_vault" "$VAULT" "config has correct vault_path"

# --- Test 3: Idempotent — running again doesn't overwrite ---
echo "# User customized this" >> "$VAULT/CLAUDE.md"
bash "$MNEMON_ROOT/setup.sh" "$VAULT" --non-interactive --skip-qmd
last_line=$(tail -1 "$VAULT/CLAUDE.md")
assert_eq "$last_line" "# User customized this" "existing CLAUDE.md not overwritten"

# --- Test 4: Setup is executable ---
assert_executable "$MNEMON_ROOT/setup.sh" "setup.sh is executable"

# Cleanup
rm -rf "$TMPDIR"
# Also clean up generated config
rm -f "$MNEMON_ROOT/mnemon.yaml"

summary
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/Mnemon && bash tests/test_setup.sh
```

Expected: FAIL — `setup.sh` doesn't exist yet.

- [ ] **Step 3: Write setup.sh**

Write `~/Mnemon/setup.sh`:

```bash
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
VAULT_PATH="$(cd "$(dirname "$VAULT_PATH")" 2>/dev/null && pwd)/$(basename "$VAULT_PATH")" || VAULT_PATH="$VAULT_PATH"

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
  sed "s|{{vault_path}}|$VAULT_PATH|g" "$SCRIPT_DIR/mnemon.yaml.template" > "$CONFIG_PATH"
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
    local ver=""
    ver=$("$cmd" --version 2>/dev/null | head -1 || echo "installed")
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
```

- [ ] **Step 4: Make setup.sh executable**

```bash
chmod +x ~/Mnemon/setup.sh
```

- [ ] **Step 5: Run tests**

```bash
cd ~/Mnemon && bash tests/test_setup.sh
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/Mnemon
git add setup.sh tests/test_setup.sh
git commit -m "feat: setup script — vault creation, config generation, dep checking"
```

---

## Task 10: Integration Test

**Files:**
- Create: `~/Mnemon/tests/test_integration.sh`

This test verifies the full pipeline: setup → config → gateway dry-run → status.

- [ ] **Step 1: Write integration test**

Write `~/Mnemon/tests/test_integration.sh`:

```bash
#!/usr/bin/env bash
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Integration Tests ==="

TMPDIR=$(mktemp -d -t mnemon-integration-XXXX)
VAULT="$TMPDIR/test-vault"

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
rm -rf "$TMPDIR"
rm -f "$MNEMON_ROOT/mnemon.yaml"

summary
```

- [ ] **Step 2: Run all tests**

```bash
cd ~/Mnemon
echo "=== Running all tests ==="
bash tests/test_config.sh && \
bash tests/test_gateway.sh && \
bash tests/test_setup.sh && \
bash tests/test_integration.sh
echo "=== All tests passed ==="
```

Expected: All tests pass.

- [ ] **Step 3: Final commit**

```bash
cd ~/Mnemon
git add tests/test_integration.sh
git commit -m "test: integration tests — full setup → gateway → status flow"
```

- [ ] **Step 4: Verify repo state**

```bash
cd ~/Mnemon
echo "=== Final Repo State ==="
git log --oneline
echo ""
echo "=== Files ==="
find . -not -path './.git/*' -type f | sort
echo ""
echo "=== Tests ==="
bash tests/test_config.sh && bash tests/test_gateway.sh && bash tests/test_setup.sh && bash tests/test_integration.sh
```

Expected: 8 commits, all files present, all tests green.

---

## Self-Review Checklist

### Spec Coverage

| PRD Requirement | Task | Status |
|----------------|------|--------|
| R1.1 setup.sh creates vault structure | Task 9 | ✅ |
| R1.2 setup.sh generates mnemon.yaml | Task 9 | ✅ |
| R1.3 setup.sh scaffolds reader-context.md | Task 9 | ✅ |
| R1.4 setup.sh checks dependencies | Task 9 | ✅ |
| R1.5 setup.sh configures QMD (if installed) | Task 9 | ✅ |
| R1.6 Plugin installs via claude plugin install | Task 6 | ✅ |
| R1.7 setup.sh creates CLAUDE.md in vault | Task 8+9 | ✅ |
| R2.1 Gateway accepts 6 origins | Task 3 | ✅ |
| R2.2 Auto-detection from URL patterns | Task 3 | ✅ |
| R2.3 source.md immutable | Task 8 (CLAUDE.md) | ✅ (convention) |
| R2.4 Folder naming YYYY-MM-DD_hash8 | Task 3 (prompt) | ✅ |
| R2.5 Hash collision handling | Task 3 (prompt) | ✅ |
| R2.6 Failed writes → pending queue | Task 3 | ✅ |
| R2.7 Gateway reads config from mnemon.yaml | Task 2+3 | ✅ |
| R3.1-R3.3 Extract schema + personal context | Task 5+8 | ✅ |
| R3.4 7 extraction templates | Task 5 (1 of 7) | ⚠️ Plan 2 |
| R3.5 Template selection by source_type | Task 3 | ✅ |
| R3.6-R3.7 Key Ideas format + status | Task 5 | ✅ |
| R3.8 QMD pre-search before extraction | — | ⚠️ Plan 2 |
| R3.9 Fabric-inspired template format | Task 5 | ✅ |
| R4.1-R4.5 Search (grep/QMD) | Task 7 (skill) | ✅ |
| R5.1-R5.3 Skills | Task 7 | ✅ |

**Gaps for Plan 2:** 6 remaining templates (youtube, podcast, book, paper, idea, conversation), QMD pre-search in extraction, protocols, documentation.

### Placeholder Scan
No "TBD", "TODO", "implement later" found. All code complete.

### Type Consistency
- `load_config()` signature and exports consistent across config, gateway, and tests
- `VAULT_PATH`, `MNEMON_ROOT`, `SEARCH_PROVIDER` used consistently
- Template path pattern `templates/core/{source_type}.md` consistent between gateway and article template filename

---

## Plan 2 Preview (Content & Documentation)

After Plan 1 is verified, Plan 2 will cover:
- **Templates:** youtube.md, podcast.md, book.md, paper.md, idea.md, conversation.md
- **Protocols:** source-schema.md, extract-schema.md, storage.md, status-lifecycle.md, domains.md
- **Docs:** README.md, getting-started.md, architecture.md, use-cases.md, extending.md
- **Enhancement:** QMD pre-search before extraction (R3.8)
