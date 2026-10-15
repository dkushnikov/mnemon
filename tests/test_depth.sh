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

echo "== D6a: depth-probe gate no-ops when disabled / high-substance =="
MNEMON_TRIAGE_DEPTH_PROBE=0 python3 - "$BIN" <<'PY' && ok "D6a gate no-op" || bad "D6a gate"
import importlib.util, sys
spec = importlib.util.spec_from_file_location('t', sys.argv[1] + '/triage-urls.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.apply_depth_probe_boost('https://x.test/', 2, 'short') == (2, None)
assert m.apply_depth_probe_boost('https://x.test/', 4, None) == (4, None)
PY

echo "== D4: gateway dedup matches url aliases (canonical_url, final_url, posted_url, folded_sources) =="
# The real gateway against the capture stubs (tests/fixtures/capture/setup.sh): a folder whose
# frontmatter carries url A, canonical_url B, final_url C, posted_url D and a folded_sources
# entry E is the same resource for every one of them (capture contracts §3.1 legacy rule);
# the match is frontmatter-bounded — a URL that appears only in the body is not an alias.
(
  MNEMON_ROOT="$(cd "$BIN/.." && pwd -P)"
  source "$MNEMON_ROOT/tests/test-helper.sh"
  source "$MNEMON_ROOT/tests/fixtures/capture/setup.sh"
  d="$VAULT/Sources/2026-10-01_d4d4d4d4"; mkdir -p "$d"
  printf -- '---\ntype: source\nsource_type: article\norigin: url\nurl: "https://openviking.ai"\ncanonical_url: "https://www.openviking.ai/"\nfinal_url: https://openviking.ai/home\nposted_url: '"'"'https://x.example/post/1'"'"'\nfolded_sources:\n  - url: https://github.com/volcengine/OpenViking\n    confidence: high\n  - url: "https://docs.openviking.ai/intro"\ncaptured: 2026-10-01\n---\nlanding text\nurl: https://body.example/only\n' > "$d/source.md"
  printf -- '---\ntype: extract\ntitle: "OpenViking"\nstatus: extracted\n---\n### Summary\n' > "$d/extract.md"
  n0="$(nfolders)"
  for pair in url=https://openviking.ai canonical_url=https://www.openviking.ai/ final_url=https://openviking.ai/home posted_url=https://x.example/post/1 folded=https://github.com/volcengine/OpenViking folded-quoted=https://docs.openviking.ai/intro; do
    run_gw --url "${pair#*=}" --capture-id "d4-${pair%%=*}"
    assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate 2026-10-01_d4d4d4d4" "add --url ${pair#*=} (${pair%%=*}) → skipped-duplicate of the folder"
    assert_eq "$(rget "d4-${pair%%=*}" '.legacy_match')" "true" "...recorded as a legacy match"
  done
  assert_eq "$(nfolders)" "$n0" "no new folder for any alias"
  assert_eq "$(curl_calls)" "0" "...and nothing was fetched"
  run_gw --url "https://github.com/other/repo" --capture-id d4-other
  assert_eq "$rc $(result status)" "0 extracted" "an unrelated URL is captured, not a duplicate"
  run_gw --url "https://body.example/only" --capture-id d4-body
  assert_eq "$rc $(result status)" "0 extracted" "a URL that appears only in the body is not an alias (frontmatter-bounded)"
  # a new-format folder with a merged depth manifest carries the keys too: its folded target is a duplicate
  STUB_DEPTH=fold run_gw --url "https://example.com/thin" --capture-id d4-thin
  revt="$(result revision)"
  run_gw --url "https://example.com/deeper" --capture-id d4-deeper
  assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $revt" "a target folded into a new-format composite is a duplicate of it (folded_sources of the merged manifest)"
  cleanup_capture_test
  summary
) || fail=1

[[ $fail -eq 0 ]] && echo "PASS test_depth.sh" || { echo "FAIL test_depth.sh"; exit 1; }
