#!/usr/bin/env bash
# test_depth.sh — depth-aware capture: lib_url (pure) + kb-probe-deeper (offline smoke).
# Network-dependent E2E (openviking → finds volcengine/OpenViking) is validated
# manually; CI tests here are hermetic (no network).
set -uo pipefail
BIN="$(cd "$(dirname "$0")/../bin" && pwd)"
fail=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAIL: $1" >&2; fail=1; }

echo "== lib_url (pure) =="
python3 - "$BIN" <<'PY' && ok "lib_url normalize/domain/github_repo" || bad "lib_url"
import sys; sys.path.insert(0, sys.argv[1])
import lib_url as L
assert L.normalize_url("https://www.Openviking.ai/?utm_source=x&si=y") == "https://openviking.ai", L.normalize_url("https://www.Openviking.ai/?utm_source=x&si=y")
assert L.normalize_url("openviking.ai/path/") == "https://openviking.ai/path"
assert L.registrable_domain("https://docs.openviking.ai/x") == "openviking.ai"
assert L.registrable_domain("https://github.com") == "github.com"
assert L.github_repo("https://github.com/volcengine/OpenViking/tree/main") == "volcengine/OpenViking"
assert L.github_repo("https://github.com/topics/ai") is None
assert L.same_site("https://docs.openviking.ai", "https://openviking.ai/") is True
assert L.same_site("https://github.com/x", "https://openviking.ai") is False
PY

echo "== kb-probe-deeper: thin SPA detection (offline fixture, no external links) =="
fx="$(mktemp -t depthfx.XXXXXX.html)"
cat > "$fx" <<'HTML'
<!doctype html><html><head><title>X</title></head>
<body><div id="root"></div><script src="/static/app.js"></script></body></html>
HTML
out="$(python3 "$BIN/kb-probe-deeper.py" "https://example-spa.test/" --landing-file "$fx" 2>/dev/null)"
rm -f "$fx"
echo "$out" | python3 -c "
import sys,json; d=json.load(sys.stdin)
assert d['thin'] is True, d
assert d['spa'] is True, d
assert isinstance(d['candidates'], list)
assert 'posted_url' in d and 'errors' in d
print('  ok: thin+spa detected, json shape valid (candidates=%d)'%len(d['candidates']))
" || bad "probe thin/spa fixture"

echo "== kb-probe-deeper: rich landing → not thin (offline fixture) =="
fx2="$(mktemp -t depthfx2.XXXXXX.html)"
python3 -c "print('<html><body>' + 'word '*600 + '</body></html>')" > "$fx2"
out2="$(python3 "$BIN/kb-probe-deeper.py" "https://rich.test/" --landing-file "$fx2" 2>/dev/null)"
rm -f "$fx2"
echo "$out2" | python3 -c "
import sys,json; d=json.load(sys.stdin)
assert d['thin'] is False, d
assert d['candidates']==[], d
print('  ok: rich landing not thin, no candidates')
" || bad "probe rich fixture"

[[ $fail -eq 0 ]] && echo "PASS test_depth.sh" || { echo "FAIL test_depth.sh"; exit 1; }
