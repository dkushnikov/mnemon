#!/usr/bin/env bash
# test_depth.sh — depth-aware capture: lib_url (pure) + kb-probe-deeper over the capture policy (hermetic:
# a local stub server under the §3.2 test hooks — no network, no DNS) + the gateway's alias dedup (D4).
# The probe's deeper tests (wall rejection, budgets, the fold, teardown) live in
# tests/capture_layer/test_depth_helpers.py; this suite keeps the bash-level checks and the gateway part.
set -uo pipefail
BIN="$(cd "$(dirname "$0")/../bin" && pwd)"
TESTS="$(cd "$(dirname "$0")" && pwd)"
fail=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAIL: $1" >&2; fail=1; }

echo "=== Depth Tests ==="
echo "python: $(python3 --version 2>&1) ($(command -v python3))"
echo "bash:   ${BASH_VERSION}"
case "$PATH" in
  /usr/bin:/bin:*)
    pyv="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    [[ "$pyv" == "3.9" ]] || { echo "FAIL: legacy PATH run expects python 3.9.x, got $pyv"; exit 1; }
    [[ "$BASH_VERSION" == 3.2.* ]] || { echo "FAIL: legacy PATH run expects bash 3.2.x, got $BASH_VERSION"; exit 1; }
    echo "toolchain: legacy (python 3.9, bash 3.2) asserted" ;;
esac

# §7.16: inherited keys, proxies and test variables cleared before anything runs
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy RENDER_URL_CMD
for v in $(env | sed -n 's/^\(MNEMON_[A-Z0-9_]*\)=.*/\1/p'); do unset "$v"; done

echo "== lib_url (pure; a comparison form, never an identity) =="
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
assert "never an identity" in L.__doc__, "the docstring states the comparison-only role"
PY

# --- a stub site for the probe: llms.txt and a docs page, served locally under the policy hooks ---
SITE="$(mktemp -d -t depthsite.XXXXXX)"
mkdir -p "$SITE/docs"
printf '# Example\n\n- one\n- two\n- three\n' > "$SITE/llms.txt"
python3 -c "print('<html><body><h1>Docs</h1><p>' + 'docs word '*80 + '</p></body></html>')" > "$SITE/docs/index.html"
PORTFILE="$SITE/port"
python3 "$TESTS/capture_layer/stub_server.py" --root "$SITE" > "$PORTFILE" 2>/dev/null &
SRV=$!
i=0; while [[ ! -s "$PORTFILE" && $i -lt 100 ]]; do sleep 0.05; i=$((i + 1)); done
PORT="$(cat "$PORTFILE")"
cleanup() { kill "$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null; rm -rf "$SITE"; }
trap cleanup EXIT
export MNEMON_TEST_HOOKS=1 MNEMON_POLICY_ALLOW_LOOPBACK=1
export MNEMON_FETCH_URL_REWRITE="https://example-spa.test=http://127.0.0.1:$PORT;https://docs.example-spa.test=http://127.0.0.1:$PORT/docs;https://rich.test=http://127.0.0.1:$PORT/rich;https://docs.rich.test=http://127.0.0.1:$PORT/rich/docs"
export RENDER_URL_CMD="$SITE/render-fail.sh"
printf '#!/usr/bin/env bash\nexit 11\n' > "$RENDER_URL_CMD"; chmod +x "$RENDER_URL_CMD"

echo "== kb-probe-deeper: thin SPA detection (supplied landing, targets through the policy, no network) =="
fx="$(mktemp -t depthfx.XXXXXX.html)"
cat > "$fx" <<'HTML'
<!doctype html><html><head><title>X</title><link rel="canonical" href="https://example-spa.test/home"></head>
<body><div id="root"></div><script src="/static/app.js"></script></body></html>
HTML
out="$(python3 "$BIN/kb-probe-deeper.py" "https://example-spa.test/" --landing-file "$fx" --context '{"final_url":"https://example-spa.test/","render":"off"}' 2>/dev/null)"
rm -f "$fx"
echo "$out" | python3 -c "
import sys,json; d=json.load(sys.stdin)
assert d['thin'] is True, d
assert d['spa'] is True, d
assert d['rendered'] is False, d
assert isinstance(d['candidates'], list)
assert 'posted_url' in d and 'errors' in d
assert d['declared_canonical_url'] == 'https://example-spa.test/home', d
assert 'canonical_url' not in d, 'canonical_url is reserved for the computed identity'
types = sorted(set(c['type'] for c in d['candidates']))
assert types == ['docs', 'llms.txt'], types
assert all(c['same_entity_confidence'] == 'high' for c in d['candidates']), d['candidates']
assert d['errors'] == [], d['errors']
print('  ok: thin+spa detected, json shape valid, llms.txt and docs found through the policy (candidates=%d)'%len(d['candidates']))
" || bad "probe thin/spa fixture"

echo "== kb-probe-deeper: rich landing → not thin (supplied landing) =="
fx2="$(mktemp -t depthfx2.XXXXXX.html)"
python3 -c "print('<html><body>' + 'word '*600 + '</body></html>')" > "$fx2"
out2="$(python3 "$BIN/kb-probe-deeper.py" "https://rich.test/" --landing-file "$fx2" --context '{"render":"off"}' 2>/dev/null)"
rm -f "$fx2"
echo "$out2" | python3 -c "
import sys,json; d=json.load(sys.stdin)
assert d['thin'] is False, d
assert d['candidates']==[], d
print('  ok: rich landing not thin, no candidates')
" || bad "probe rich fixture"

echo "== kb-probe-deeper: the policy hooks are ignored without MNEMON_TEST_HOOKS=1 (a warning names them) =="
err="$( MNEMON_TEST_HOOKS= python3 "$BIN/kb-probe-deeper.py" "https://example-spa.test/" --landing-file /dev/null --budget-s 2 --context '{"render":"off"}' 2>&1 >/dev/null )"
[[ "$err" == *"MNEMON_FETCH_URL_REWRITE"* && "$err" == *"MNEMON_TEST_HOOKS"* ]] && ok "hooks warning names the ignored variables" || bad "hooks warning: $err"
unset MNEMON_TEST_HOOKS MNEMON_POLICY_ALLOW_LOOPBACK MNEMON_FETCH_URL_REWRITE RENDER_URL_CMD

echo "== D6a: the triage spike is gone (GH #29) — no triage-urls.py in bin/ =="
[[ -e "$BIN/triage-urls.py" ]] && bad "bin/triage-urls.py still present (retired 2026-10-28)" || ok "bin/triage-urls.py retired"

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
