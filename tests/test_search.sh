#!/usr/bin/env bash
# test_search.sh — bin/mnemon-search.py: python unit/regression tests plus an
# end-to-end pass through `mnemon search` against a temp vault.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MNEMON="$MNEMON_ROOT/bin/mnemon"

echo "=== Search Tests ==="

TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-search-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg"
export XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR
mkdir -p "$TEST_TMPDIR/cwd"
cd "$TEST_TMPDIR/cwd"

jget() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

# --- 1. python unit + regression tests ---
echo "--- python unit tests ---"
if python3 "$MNEMON_ROOT/tests/test_search.py" >"$TEST_TMPDIR/py.log" 2>&1; then
  pass "python unit tests: $(grep -Eo 'Ran [0-9]+ tests' "$TEST_TMPDIR/py.log" | head -1)"
else
  fail "python unit tests" "see output below"
  cat "$TEST_TMPDIR/py.log"
fi

# --- 2. end-to-end through the dispatcher ---
echo "--- mnemon search (end-to-end) ---"
VAULT="$TEST_TMPDIR/vault"
"$MNEMON" init searchtest --vault "$VAULT" --non-interactive >/dev/null

# mk_extract <dir> <title> <rating> <created> <domains> <body>
mk_extract() {
  local d="$VAULT/Sources/$1"
  mkdir -p "$d"
  cat > "$d/extract.md" <<EOF
---
type: extract
title: "$2"
rating: $3
created: $4
domains: [$5]
tags:
  - alpha-tag
  - beta-tag
capture_context:
  vault: test
  import_source: gateway
---

$6
EOF
}
mk_extract 2026-04-04_aaa11111 "Karpathy on LLM wikis" 9 2026-04-04 "learning" "Karpathy describes an LLM-maintained wiki workflow."
mk_extract 2026-03-01_bbb22222 "Knowledge systems review" 7 2026-03-01 "learning, career" "Review of knowledge management systems; mentions Karpathy once."
mk_extract 2026-02-01_ccc33333 "Gardening notes" 5 2026-02-01 "home" "Tomatoes need staking in June."

out=$("$MNEMON" --instance searchtest search --query karpathy)
assert_eq "$(printf '%s' "$out" | jget 'd["count"]')" "2" "two extracts match karpathy"
assert_eq "$(printf '%s' "$out" | jget 'd["results"][0]["title"]')" "Karpathy on LLM wikis" "ranked by rating desc"
assert_eq "$(printf '%s' "$out" | jget 'd["results"][0]["created"]')" "2026-04-04" "created kept as a YYYY-MM-DD string"
assert_eq "$(printf '%s' "$out" | jget 'd["results"][0]["tags"]')" "['alpha-tag', 'beta-tag']" "block-list tags parsed"
assert_eq "$(printf '%s' "$out" | jget 'd["results"][0]["path"]')" "Sources/2026-04-04_aaa11111/" "path is vault-relative"
assert_eq "$(printf '%s' "$out" | jget 'd["provider"]')" "grep" "provider reported from the instance config"
assert_eq "$(printf '%s' "$out" | jget 'd["fallback_applied"]')" "False" "no fallback on a direct hit"

out=$("$MNEMON" --instance searchtest search --query karpathy --domain career)
assert_eq "$(printf '%s' "$out" | jget 'd["count"]')" "1" "--domain filters"
assert_eq "$(printf '%s' "$out" | jget 'd["results"][0]["title"]')" "Knowledge systems review" "--domain keeps the right extract"

out=$(MNEMON_INSTANCE=searchtest "$MNEMON" search --query gardening)
assert_eq "$(printf '%s' "$out" | jget 'd["count"]')" "1" "MNEMON_INSTANCE selects the instance"

out=$("$MNEMON" --instance searchtest search --query "karpathy zzzznotaword")
assert_eq "$(printf '%s' "$out" | jget 'd["fallback_applied"]')" "True" "fallback applied when no file has all tokens"
assert_eq "$(printf '%s' "$out" | jget 'd["fallback_token"]')" "karpathy" "fallback picks the existing token"

out=$("$MNEMON" --instance searchtest search --query karpathy --limit 1)
assert_eq "$(printf '%s' "$out" | jget 'd["count"]')" "1" "--limit respected"

out=$("$MNEMON" --instance searchtest search --query karpathy --human)
assert_contains "$out" "Found 2 result(s) for 'karpathy'" "--human output"
assert_contains "$out" "1. Karpathy on LLM wikis" "--human lists titles"

rc=0; out=$(MNEMON_INSTANCE=nosuch "$MNEMON" search --query karpathy --vault "$VAULT" 2>&1) || rc=$?
assert_eq "$rc" "0" "--vault override needs no config"
assert_eq "$(printf '%s' "$out" | jget 'd["count"]')" "2" "--vault override searches the given vault"

rc=0; out=$("$MNEMON" --instance nosuch search --query karpathy 2>&1) || rc=$?
assert_eq "$rc" "2" "missing instance → exit 2"
assert_contains "$out" "instances/nosuch.yaml" "missing instance error names the file"

rc=0; out=$("$MNEMON" --instance searchtest search --query "a" 2>&1) || rc=$?
assert_eq "$rc" "2" "query without usable tokens → exit 2"

out=$(MNEMON_INSTANCE=searchtest python3 "$MNEMON_ROOT/bin/mnemon-search.py" --query karpathy --limit 1)
assert_eq "$(printf '%s' "$out" | jget 'd["count"]')" "1" "direct script invocation (plugin-compatible) works"

cd /
rm -rf "$TEST_TMPDIR"
summary
