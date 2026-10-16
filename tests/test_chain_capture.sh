#!/usr/bin/env bash
# test_chain_capture.sh — the real capture chain against a local server (docs/specs/2026-10-15-capture-layer.md
# §10 "Integrator", §7.2, §7.4, §7.9, §7.10, §7.13–§7.15, §12 A12): the real dispatcher (bin/mnemon),
# bin/vault-lock.py, bin/knowledge-gateway.sh with bin/lib-capture.sh, the real capture layer —
# capture-fetch.py, capture-text.py, capture-site.py, canonical-audit.py and bin/capture/* — then
# knowledge-reindex.py, `mnemon doctor`, `mnemon audit --canonical` and `mnemon status`. Pages come from
# python's http.server (tests/capture_layer/stub_server.py) on a free port under the §3.2 test hooks
# (MNEMON_TEST_HOOKS=1, loopback allowed, the arXiv prefix rewritten to the server). Only the edges are
# stubbed: `claude` (tests/fixtures/capture/stub-claude.sh), render-url.sh (returns a rendered HTML fixture, or
# fails like a renderer that produced nothing), kb-depth-fold.py (answers "not applied" and records its argv) and
# the external fetcher (MNEMON_EXTERNAL_FETCH_CMD). No `defuddle` on PATH; the real `pandoc` when it is
# installed, else the DOM text — the assertions hold either way. Every real output is diffed against the
# frozen key sets of tests/fixtures/capture/reports/. bash 3.2, python 3.9.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MNEMON="$MNEMON_ROOT/bin/mnemon"
FIX="$MNEMON_ROOT/tests/fixtures/capture"
REPORTS="$FIX/reports"

echo "=== Real Capture Chain Tests ==="
echo "toolchain: $(bash --version | head -1) · $(python3 --version 2>&1)"
# the legacy run (PATH=/usr/bin:/bin:$PATH, §7.16 / A12): asserted, not assumed
if [[ "$(command -v bash)" == /bin/bash ]]; then
  [[ "$(bash --version | head -1)" == *"version 3.2."* ]] && pass "legacy toolchain: bash 3.2.x" || fail "legacy toolchain: bash 3.2.x" "$(bash --version | head -1)"
  [[ "$(python3 --version 2>&1)" == "Python 3.9."* ]] && pass "legacy toolchain: python 3.9.x" || fail "legacy toolchain: python 3.9.x" "$(python3 --version 2>&1)"
fi

# --- isolation: temp XDG config/state, nothing inherited, the test hooks on -------------------------------
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-chaincap-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_HOME MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE MNEMON_VAULT_STATE_DIR
unset MNEMON_TEST_CRASH_AT MNEMON_CAPTURE_FETCH_CMD MNEMON_CAPTURE_SITE_CMD MNEMON_CAPTURE_TEXT_CMD MNEMON_DEPTH_FOLD_CMD MEDIA_EXTRACT_CMD RENDER_URL_CMD
unset MNEMON_TEST_DEFUDDLE_OUTPUT MNEMON_EXTERNAL_FETCH_CMD MNEMON_FETCH_URL_REWRITE MNEMON_POLICY_ALLOW_LOOPBACK
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
unset STUB_MODE STUB_FIXTURE STUB_FIXTURE_BAD STUB_FIXTURE_PART STUB_FIXTURE_PART_BAD STUB_PART_BAD_CALLS STUB_FAIL_CALLS STUB_COST STUB_COST_NULL STUB_RENDER STUB_DIR
export MNEMON_TEST_HOOKS=1 MNEMON_POLICY_ALLOW_LOOPBACK=1
VAULT="$TEST_TMPDIR/vault"; ARCHIVE="$TEST_TMPDIR/archive"; STUBS="$TEST_TMPDIR/stubs"; STUB_DIR="$TEST_TMPDIR/stub-calls"
SITE="$TEST_TMPDIR/www"; PORTFILE="$TEST_TMPDIR/port"; REQLOG="$TEST_TMPDIR/requests.log"
mkdir -p "$STUBS" "$STUB_DIR" "$SITE/abs" "$SITE/html" "$SITE/blog" "$SITE/de-de" "$XDG_STATE_HOME/mnemon"
export STUB_DIR
SERVER_PID=""
cleanup() { [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null; cd /; rm -rf "$TEST_TMPDIR"; }
trap cleanup EXIT

# --- no defuddle on PATH: every directory holding one is replaced by a shadow directory of symlinks to its
#     other entries, so jq, pandoc and the python under test keep their places. MNEMON_TEST_NO_PANDOC=1 hides
#     pandoc the same way, to run the DOM-text path of every assertion on a machine that has pandoc -----------
HIDDEN="defuddle"; [[ "${MNEMON_TEST_NO_PANDOC:-}" == "1" ]] && HIDDEN="defuddle pandoc"
hide_tools() {
  local newpath="" d shadow f name hide
  local -a dirs
  IFS=: read -r -a dirs <<< "$PATH"
  for d in "${dirs[@]}"; do
    [[ -n "$d" ]] || continue
    hide=0
    for name in $HIDDEN; do [[ -x "$d/$name" ]] && hide=1; done
    if [[ $hide -eq 1 ]]; then
      shadow="$TEST_TMPDIR/path-shadow/$(printf '%s' "$d" | tr '/' '_')"
      if [[ ! -d "$shadow" ]]; then
        mkdir -p "$shadow"
        for f in "$d"/*; do
          case " $HIDDEN " in *" $(basename "$f") "*) continue ;; esac
          ln -s "$f" "$shadow/$(basename "$f")" 2>/dev/null || true
        done
      fi
      d="$shadow"
    fi
    newpath="${newpath:+$newpath:}$d"
  done
  PATH="$newpath"
}
hide_tools
export PATH
[[ -z "$(command -v defuddle 2>/dev/null || true)" ]] && pass "no defuddle on PATH" || fail "no defuddle on PATH" "$(command -v defuddle)"
HAVE_PANDOC=0; command -v pandoc >/dev/null 2>&1 && HAVE_PANDOC=1
[[ "${MNEMON_TEST_NO_PANDOC:-}" == "1" ]] && { [[ $HAVE_PANDOC -eq 0 ]] && pass "no pandoc on PATH (MNEMON_TEST_NO_PANDOC=1)" || fail "no pandoc on PATH (MNEMON_TEST_NO_PANDOC=1)" "$(command -v pandoc)"; }
if [[ $HAVE_PANDOC -eq 1 ]]; then echo "converters: $(pandoc --version | head -1), no defuddle"; else echo "converters: no pandoc, no defuddle — the DOM text"; fi

# --- stubs: claude (the fixture stub), the renderer, the depth fold, the external fetcher ---------------
cp "$FIX/stub-claude.sh" "$STUBS/claude"
cat > "$STUBS/render-url.sh" <<'STUB'
#!/usr/bin/env bash
# render-url.sh <url> --html --timeout <ms> --max-bytes <n>: STUB_RENDER=ok prints the rendered fixture,
# anything else fails like a renderer that produced nothing (exit 11); every call is recorded
printf '%s\n' "$*" >> "$STUB_DIR/render-calls"
[[ "${STUB_RENDER:-ok}" == "ok" ]] || exit 11
cat "$STUB_RENDERED"
STUB
cat > "$STUBS/kb-depth-fold.py" <<'STUB'
#!/usr/bin/env bash
# the depth fold (§4.2, §12 A8): records how the gateway calls it, folds nothing
printf '%s\n' "$*" >> "$STUB_DIR/depth-calls"
printf '{"applied": false, "reason": "stub: nothing to fold", "targets": [], "errors": []}\n'
STUB
cat > "$STUBS/external-fetch.sh" <<'STUB'
#!/usr/bin/env bash
# MNEMON_EXTERNAL_FETCH_CMD <fetcher> <url>: the vendor's HTML on stdout
printf '%s\n' "$*" >> "$STUB_DIR/external-calls"
cat "$STUB_VENDOR"
STUB
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"
export RENDER_URL_CMD="$STUBS/render-url.sh" MNEMON_DEPTH_FOLD_CMD="$STUBS/kb-depth-fold.py"
export STUB_FIXTURE="$FIX/extract-good.json" STUB_FIXTURE_PART="$FIX/extract-part-good.json" STUB_BODY_FROM_TEMPLATE=1 STUB_VAULT="$VAULT"
export STUB_RENDERED="$SITE/rendered.html" STUB_VENDOR="$SITE/vendor.html"
# the probe record for the stub's version (phase 1 D13), keyed by the exact `claude --version` line
printf '{"9.9.9-stub (Claude Code)": {"pass": true, "date": "2026-10-15", "probes": {"no-tools": true}}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"

# --- the pages the server serves (each page that must be `ok` carries more than thin_words = 60 words) ----
cat > "$SITE/article.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>The Example Article</title>
<meta property="article:published_time" content="2026-09-01T10:00:00Z">
</head>
<body>
<header><h1>The Example Article</h1></header>
<main>
<p>This article exists so that the capture chain can be exercised end to end against a local server. It carries enough prose for the classifier to call the page ok rather than thin, and its lines are long enough to be eligible for the completeness check.</p>
<h2>Why the chain matters</h2>
<p>The fetch stage saves the raw bytes before anything is converted, so a conversion can be redone later without touching the network. The report of that fetch travels into the receipt and then into the converter, which never fetches anything itself.</p>
<h2>What the converter keeps</h2>
<ul>
<li>Every eligible line of the reference must appear in the chosen candidate with its word boundaries intact.</li>
<li>Every heading of the reference must appear too, whatever the candidate converter produced.</li>
<li>Tables and code blocks are counted by the inventory and a lost one makes the capture partial.</li>
</ul>
<p>When no candidate passes, the DOM text wins by construction and the report says which converter served the page and what the others lost.</p>
</main>
<footer><p>Footer navigation and legal notices live here and are removed from the reference.</p></footer>
</body>
</html>
HTML
cat > "$SITE/rendered.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Rendered Page</title></head>
<body>
<main>
<h1>Rendered Page</h1>
<p>This is the document the stub renderer returns after scripts ran: a page with enough visible words to be classified as ok by the decision table, so that the chain keeps it as the rendered raw of the capture.</p>
<p>A second paragraph makes the page comfortably richer than any thin direct result and gives the completeness check more than one eligible line to find in the chosen candidate.</p>
</main>
</body>
</html>
HTML
cat > "$SITE/vendor.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Vendor Page</title></head>
<body>
<main>
<h1>Vendor Page</h1>
<p>This is the document an external fetcher relays after the local chain ended on a JavaScript shell: real text with enough visible words for the decision table to call it ok, so the chain publishes it with its egress recorded as external.</p>
<p>A second paragraph gives the completeness check a second eligible line and keeps the page well above the thin threshold of the instance.</p>
</main>
</body>
</html>
HTML
cat > "$SITE/botwall.html" <<'HTML'
<!DOCTYPE html>
<html><head><title>Just a moment...</title></head>
<body><div id="cf-chl">Checking your browser before accessing the site. Please wait a moment.</div></body></html>
HTML
cat > "$SITE/paywall.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>A Teaser Behind The Wall</title></head>
<body>
<main>
<h1>A Teaser Behind The Wall</h1>
<p>The first paragraphs of this piece are free to read and carry real sentences, long enough to count as a readable teaser rather than an empty shell under the decision table of the capture layer, which publishes such a page as blocked.</p>
<p>A second free paragraph adds more readable text so the visible word count stays comfortably above the thin threshold of the instance before the wall appears below this line of the page.</p>
<div class="paywall"><p>Subscribe to continue reading this story.</p></div>
</main>
</body>
</html>
HTML
cat > "$SITE/login.html" <<'HTML'
<!DOCTYPE html>
<html><head><title>Members</title></head>
<body><h1>Members only</h1><p>Sign in to continue to the members area.</p>
<form method="post"><input name="user"><input type="password" name="pw"><button>Sign in</button></form></body></html>
HTML
cat > "$SITE/spa.html" <<'HTML'
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>App Shell</title></head>
<body><div id="root"></div><script src="/static/app.js"></script><noscript>You need to enable JavaScript to run this app.</noscript></body></html>
HTML
# arXiv: the abstract page of the requested version (citation_* tags, the pinned pdf URL) and the LaTeXML full
# text (the synthetic damage fixture of the converter suite: a base64 figure, a download button, listing lines,
# \iow_now residue, a merged-cell table, a literal [TABLE] inside a code example)
for v in "" v1; do
cat > "$SITE/abs/1234.56789$v.html" <<'HTML'
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>[1234.56789v1] A Synthetic Paper on Example Methods</title>
<meta name="citation_title" content="A Synthetic Paper on Example Methods">
<meta name="citation_author" content="Author, First">
<meta name="citation_author" content="Writer, Second">
<meta name="citation_date" content="2026/02/03">
<meta name="citation_pdf_url" content="https://arxiv.org/pdf/1234.56789v1">
<meta name="citation_arxiv_id" content="1234.56789">
</head><body><h1>A Synthetic Paper on Example Methods</h1><p>Abstract: a synthetic abstract page.</p></body></html>
HTML
done
cp "$FIX/html/latexml.html" "$SITE/html/1234.56789v1.html"
# the site: a landing linking three pages, a sitemap that also names a legal and a localized page
cat > "$SITE/index.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Example Site</title></head>
<body>
<main>
<h1>Example Site</h1>
<p>Welcome to the example site that the site mapper discovers page by page: a landing page with a short description of the product, a documentation page and a blog, each with enough prose to be captured as a complete page of its own.</p>
<p>The mapper reads the links of this landing, the sitemap named by robots.txt and the llms.txt file when there is one, assigns a role to every page and writes a reviewable plan before anything is fetched into the bundle.</p>
<ul>
<li><a href="/product">Product</a></li>
<li><a href="/docs">Documentation</a></li>
<li><a href="/blog/post">Blog</a></li>
</ul>
</main>
</body>
</html>
HTML
cat > "$SITE/product.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Product</title></head>
<body>
<main>
<h1>Product</h1>
<p>The product page describes what the product does in a few sentences of ordinary prose, long enough for the decision table to call the page ok and for the completeness check to find several eligible lines in the chosen candidate.</p>
<p>A second paragraph explains who the product is for and how it is priced, and a third sentence keeps the visible word count above the thin threshold of the instance by a comfortable margin.</p>
</main>
</body>
</html>
HTML
cat > "$SITE/docs.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Documentation</title></head>
<body>
<main>
<h1>Documentation</h1>
<p>The documentation page is the first docs page the mapper sees: it looks for a HelpTOC.json next to it and reads the links of this page as a table of contents, which here names no further page.</p>
<p>Its prose is long enough to be captured as a complete page, with every eligible line of the reference present in the candidate the converter chooses for the bundle.</p>
</main>
</body>
</html>
HTML
cat > "$SITE/blog/post.html" <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>A Blog Post</title></head>
<body>
<main>
<h1>A Blog Post</h1>
<p>The blog post is discovered through the landing's links and through the sitemap, which the mapper deduplicates under the canonical form of the URL, so it appears once in the plan with the blog role.</p>
<p>Like the other pages of this site it carries enough prose to be classified ok and to be converted completely into its part of the composite.</p>
</main>
</body>
</html>
HTML
printf '<!DOCTYPE html><html><head><title>Privacy</title></head><body><p>Privacy policy text that the plan excludes as legal.</p></body></html>\n' > "$SITE/privacy.html"
printf '<!DOCTYPE html><html><head><title>Lokalisiert</title></head><body><p>A localized page the plan excludes.</p></body></html>\n' > "$SITE/de-de/x.html"

# --- the server: python's http.server on a free port, every request logged -------------------------------
python3 "$MNEMON_ROOT/tests/capture_layer/stub_server.py" --root "$SITE" --log "$REQLOG" --status /botwall=403 > "$PORTFILE" 2>/dev/null &
SERVER_PID=$!
i=0; while [[ ! -s "$PORTFILE" && $i -lt 200 ]]; do sleep 0.05; i=$((i + 1)); done
PORT="$(cat "$PORTFILE")"
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "the stub server did not start"; exit 1; }
BASE="http://127.0.0.1:$PORT"
printf 'User-agent: *\nDisallow: /private\nSitemap: %s/sitemap.xml\n' "$BASE" > "$SITE/robots.txt"
{
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
  for p in /product /docs /privacy /de-de/x /blog/post; do printf '<url><loc>%s%s</loc></url>\n' "$BASE" "$p"; done
  printf '</urlset>\n'
} > "$SITE/sitemap.xml"
echo "server: $BASE (pid $SERVER_PID)"

# --- the instances: t (archive_dir set, a 300 000-byte inline limit), t2 (external_fetch auto) -----------
"$MNEMON" init t --vault "$VAULT" --archive "$ARCHIVE" --non-interactive >/dev/null 2>&1 || { echo "mnemon init failed"; exit 1; }
printf 'extract_inline_max_bytes: 300000\n' >> "$XDG_CONFIG_HOME/mnemon/instances/t.yaml"
"$MNEMON" init t2 --vault "$VAULT" --archive "$ARCHIVE" --non-interactive >/dev/null 2>&1 || { echo "mnemon init t2 failed"; exit 1; }
printf 'external_fetch: auto\n' >> "$XDG_CONFIG_HOME/mnemon/instances/t2.yaml"

# --- helpers ---------------------------------------------------------------------------------------------
out=""; err=""; rc=0
run() {   # run <cmd…>: out, err, rc (an inner bash keeps "Killed: 9" notices of crash-injected runs in $err)
  local o="$TEST_TMPDIR/run.out" e="$TEST_TMPDIR/run.err"
  rc=0
  bash -c '"$@"; exit $?' _ "$@" </dev/null >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
run_stdin() {   # run_stdin <file> <cmd…>
  local f="$1" o="$TEST_TMPDIR/run.out" e="$TEST_TMPDIR/run.err"; shift
  rc=0
  bash -c '"$@"; exit $?' _ "$@" <"$f" >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
result() { printf '%s\n' "$out" | grep "^RESULT:$1=" | head -1 | cut -d= -f2-; }
VSTATE="$XDG_STATE_HOME/mnemon/vaults/$(printf '%s' "$VAULT" | shasum -a 256 | cut -c1-16)"
receipt() { printf '%s/captures/%s.json' "$VSTATE" "$1"; }
rget() { jq -r "$2" "$(receipt "$1")"; }
fm() { awk -v k="$2" 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f{p=k": "; if(substr($0,1,length(p))==p){print substr($0,length(p)+1);exit}}' "$1"; }
capfm() { awk -v k="$2" 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f&&$0=="capture:"{c=1;next} c&&substr($0,1,1)!=" "{exit} c{p="  "k": "; if(substr($0,1,length(p))==p){print substr($0,length(p)+1);exit}}' "$1"; }
nfolders() { (shopt -s nullglob; for d in "$VAULT"/Sources/*/; do basename "$d"; done) | wc -l | tr -d ' '; }
requests() { if [[ -f "$REQLOG" ]]; then wc -l < "$REQLOG" | tr -d ' '; else echo 0; fi; }
sha() { shasum -a 256 "$1" | cut -c1-64; }
# keyset <actual json> <fixture json> <label> <actual prefix> [<dotted path>…]: the key set of the actual object
# (under the prefix) equals the fixture's at every path — the integrator's diff of §12 A12 (values differ)
keyset() {
  python3 - "$@" <<'PY'
import json, sys
a, f, label, prefix = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3], sys.argv[4]
paths = sys.argv[5:] or [""]
def at(d, path):
    for p in [x for x in path.split(".") if x]:
        d = d[int(p)] if isinstance(d, list) else d[p]
    return d
bad = []
for path in paths:
    try:
        ka, kf = set(at(at(a, prefix), path)), set(at(f, path))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        bad.append("%s: unreadable (%r)" % (path or ".", exc)); continue
    if ka != kf:
        bad.append("%s: extra %s, missing %s" % (path or ".", sorted(ka - kf), sorted(kf - ka)))
print(("PASS " if not bad else "FAIL ") + label + ("" if not bad else " — " + "; ".join(bad)))
PY
}
check_keys() { local line; line="$(keyset "$@")"; case "$line" in PASS\ *) pass "${line#PASS }" ;; *) fail "${line#FAIL }" "" ;; esac; }

# ==================================================================================================
echo "--- (a) a plain article: direct fetch, convert, publish; identity by the canonical URL; recapture; reindex ---"
run "$MNEMON" --instance t add --url "$BASE/article?utm_source=x" --capture-id a1
assert_eq "$rc $(result status)" "0 extracted" "mnemon add --url …/article?utm_source=x exits 0, extracted"
R1="$(result revision)"; SRC1="$VAULT/Sources/$R1/source.md"
assert_eq "$(fm "$SRC1" url)" "\"$BASE/article?utm_source=x\"" "url: keeps the literal URL"
assert_eq "$(fm "$SRC1" canonical_url) $(result canonical_url)" "\"$BASE/article\" $BASE/article" "canonical_url: without utm_source (v1); RESULT:canonical_url agrees"
assert_eq "$(rget a1 '.request.schema + " " + .request.resource_key + " " + (.canonical_version|tostring) + " " + .request.render + " " + .request.external')" "mnemon-request/2 $BASE/article 1 auto off" "the receipt binds mnemon-request/2 with the canonical URL as resource_key, render auto, external off"
assert_eq "$(capfm "$SRC1" fetch) $(capfm "$SRC1" completeness) $(capfm "$SRC1" missing_lines) $(capfm "$SRC1" glued_lines) $(capfm "$SRC1" headings_missing)" "direct complete 0 0 0" "capture: fetch direct, completeness complete, nothing missing or glued"
conv="$(capfm "$SRC1" converter)"
[[ "$conv" == pandoc || "$conv" == dom-text ]] && pass "converter: $conv (defuddle is not on PATH)" || fail "converter" "'$conv'"
[[ $HAVE_PANDOC -eq 1 ]] && assert_eq "$conv" "pandoc" "...pandoc, the first candidate that passed"
[[ "$(capfm "$SRC1" reference_lines)" -gt 0 ]] && pass "reference_lines > 0 ($(capfm "$SRC1" reference_lines), eligible $(capfm "$SRC1" eligible_lines))" || fail "reference_lines > 0" "$(capfm "$SRC1" reference_lines)"
assert_eq "$(capfm "$SRC1" acquisition) $(capfm "$SRC1" canonical_version) $(capfm "$SRC1" http_status) $(capfm "$SRC1" raw_kind) $(capfm "$SRC1" repairs)" 'ok 1 200 response "none"' "...acquisition ok, canonical_version 1, http_status 200, raw_kind response, repairs none"
assert_eq "$(fm "$SRC1" published)" '"2026-09-01"' "published: from article:published_time (source_type article)"
assert_eq "$(fm "$SRC1" source_type) $(fm "$SRC1" title)" 'article "The Example Article"' "source_type article, the title from the page"
assert_eq "$(fm "$SRC1" final_url)" "\"$BASE/article\"" "final_url from the fetch report: the canonical form is what the chain fetched"
assert_eq "$(grep -c 'GET /article' "$REQLOG")" "1" "...the server saw one request, for /article without the tracking parameter"
body="$(cat "$SRC1")"
assert_contains "$body" "Every heading of the reference must appear too" "the capture-text carries the article's list items"
assert_not_contains "$body" "Footer navigation and legal notices" "...and not the footer"
assert_eq "$(rget a1 '.capture_report.fetch.schema + " " + .capture_report.text.schema + " " + (.capture_report.site|tostring) + " " + .report_state')" "mnemon-fetch/1 mnemon-capture-text/2 null published" "the receipt holds both reports (fetch, text); the sidecar is published"
assert_eq "$(rget a1 '.capture_report.fetch.method + " " + .capture_report.fetch.raw_kind + " " + .capture_report.fetch.acquisition.outcome + " " + .capture_report.text.converter + " " + .capture_report.text.acquisition')" "direct response ok $conv ok" "...the fetch report (direct, response, ok) and the conversion report (acquisition copied from the fetch report)"
assert_file_exists "$ARCHIVE/$R1.report.json" "<archive>/<revision>.report.json exists"
assert_eq "$(jq -r '.schema + " " + .fetch.schema + " " + .text.schema + " " + (.site|tostring) + " " + (.media|tostring)' "$ARCHIVE/$R1.report.json")" "mnemon-capture-report/1 mnemon-fetch/1 mnemon-capture-text/2 null null" "...the frozen mnemon-capture-report/1 with both reports"
assert_eq "$(sha "$ARCHIVE/$R1.report.json")" "$(rget a1 '.report_sha256')" "...its bytes hash to the receipt's report_sha256"
assert_file_exists "$ARCHIVE/$R1.html" "the raw HTML is archived"
assert_eq "$(sha "$ARCHIVE/$R1.html")" "$(rget a1 '.raw_sha256')" "...with the receipt's raw_sha256"
assert_eq "$(result report)" "ok" "RESULT:report=ok"
depth="$(cat "$STUB_DIR/depth-calls" 2>/dev/null || true)"
assert_contains "$depth" "--landing-file " "the depth fold ran after the landing fetch with --landing-file (§4.2, A8)"
assert_contains "$depth" "--context " "...and the frozen acquisition context"
assert_contains "$depth" "--budget-s " "...under the remaining budget"
[[ -e "$STUB_DIR/render-calls" ]] && fail "no render for an ok page" "render-url.sh was called" || pass "no render for an ok page"
# the real outputs against the frozen key sets (§12 A12)
check_keys "$ARCHIVE/$R1.report.json" "$REPORTS/fetch-ok.json" "the fetch report has the key set of reports/fetch-ok.json" fetch "" stages.0 acquisition metadata hops.0 policy
check_keys "$ARCHIVE/$R1.report.json" "$REPORTS/text.json" "the conversion report has the key set of reports/text.json" text "" candidates.0 inventory inventory.headings repairs repairs.html repairs.markdown tools
check_keys "$(receipt a1)" "$REPORTS/receipts/request-v2-page.json" "the receipt has the key set of receipts/request-v2-page.json" "" "" request metadata capture_report calls.0
python3 "$MNEMON_ROOT/bin/capture-fetch.py" --canonical-only "https://Example.com/post?utm_source=x&s=alpha#intro" > "$TEST_TMPDIR/canon.json"
check_keys "$TEST_TMPDIR/canon.json" "$REPORTS/canonical.json" "capture-fetch.py --canonical-only has the key set of reports/canonical.json" ""
assert_eq "$(jq -r '.canonical_url + " " + .kind' "$TEST_TMPDIR/canon.json")" "https://example.com/post?s=alpha page" "...and canonicalizes the fixture's own example as the fixture says"
# the same page with another tracking parameter: a duplicate, nothing fetched
n_req="$(requests)"
run "$MNEMON" --instance t add --url "$BASE/article?utm_source=y" --capture-id a2
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $R1" "the same page with ?utm_source=y → skipped-duplicate naming $R1 (identity by the canonical URL)"
assert_contains "$err" "already captured at Sources/$R1/" "...the hint names the folder"
assert_eq "$(rget a2 '.state + " " + .resolved_to_revision + " " + .resolved_to_capture_id')" "extracted $R1 a1" "...an alias receipt resolved to a1's revision"
assert_eq "$(requests)" "$n_req" "...without a request to the server"
# --recapture: a new revision superseding the first; the index row of the first carries the marker
run "$MNEMON" --instance t add --url "$BASE/article?utm_source=y" --recapture --capture-id a3
assert_eq "$rc $(result status)" "0 extracted" "--recapture → a new revision"
R3="$(result revision)"
[[ -n "$R3" && "$R3" != "$R1" ]] && pass "...$R3 ≠ $R1" || fail "a new revision" "'$R3'"
assert_eq "$(fm "$VAULT/Sources/$R3/source.md" supersedes)" "\"$R1\"" "...whose source.md records supersedes: $R1"
assert_eq "$(fm "$VAULT/Sources/$R3/source.md" canonical_url) $(fm "$VAULT/Sources/$R3/source.md" url)" "\"$BASE/article\" \"$BASE/article?utm_source=y\"" "...with the same canonical URL and its own literal URL"
run "$MNEMON" --instance t reindex
assert_eq "$rc" "0" "mnemon reindex exits 0"
assert_contains "$(cat "$VAULT/index.md")" "$R1" "index.md lists the old revision"
row_of() { grep "^- \[\[Sources/$1/extract|" "$VAULT/index.md" | head -1 || true; }   # a revision's own row (the newer `-2` revision's row would match a bare grep for the first)
assert_contains "$(row_of "$R1")" " ⤳ superseded by [[Sources/$R3/extract|" "...and its row carries the ⤳ marker naming $R3 (derived from supersedes:)"
assert_not_contains "$(row_of "$R3")" "⤳ superseded by" "...while the new revision's row carries none"
assert_contains "$(cat "$VAULT/index.md")" "| Superseded | 1 |" "...the Status table counts one superseded revision"

# ==================================================================================================
echo "--- (b) a 403 challenge page: the renderer serves the raw (rendered) ---"
run "$MNEMON" --instance t add --url "$BASE/botwall" --capture-id b1
assert_eq "$rc $(result status)" "0 extracted" "a 403 bot wall is rendered and published"
RB="$(result revision)"; SRCB="$VAULT/Sources/$RB/source.md"
assert_eq "$(capfm "$SRCB" fetch) $(capfm "$SRCB" raw_kind) $(capfm "$SRCB" http_status) $(capfm "$SRCB" acquisition)" "render rendered null ok" "capture: fetch render, raw_kind rendered, http_status null (never invented), acquisition ok"
assert_eq "$(rget b1 '[.capture_report.fetch.stages[] | .stage + ":" + .outcome] | join(" ")')" "direct:bot-wall render:ok" "the fetch report records both stages: direct bot-wall, render ok"
assert_eq "$(rget b1 '.capture_report.fetch.method + " " + .capture_report.fetch.raw_kind + " " + (.capture_report.fetch.http_status|tostring) + " " + (.capture_report.fetch.final_url|tostring)')" "render rendered null null" "...method render, raw_kind rendered, http_status and final_url null"
assert_eq "$(head -c 15 "$ARCHIVE/$RB.html")" "<!DOCTYPE html>" "the raw is the rendered HTML"
assert_eq "$(fm "$SRCB" title)" '"Rendered Page"' "...the title of the rendered document"
assert_eq "$(grep -c '^final_url:' "$SRCB")" "0" "...no final_url (the render stage knows none)"
assert_contains "$(cat "$STUB_DIR/render-calls")" "$BASE/botwall --html --timeout " "render-url.sh was called with --html and a virtual-time budget"
assert_contains "$(cat "$STUB_DIR/render-calls")" " --max-bytes " "...and --max-bytes"
assert_eq "$(rget b1 '.capture_report.text.candidates[0].converter + " " + .capture_report.text.candidates[0].note')" "defuddle skipped: rendered raw" "the converter skips defuddle for a rendered raw"
assert_eq "$(capfm "$SRCB" completeness) $(capfm "$SRCB" missing_lines)" "complete 0" "...and the rendered page converts completely"

# ==================================================================================================
echo "--- (c) a paywall teaser: published as blocked ---"
run "$MNEMON" --instance t add --url "$BASE/paywall" --capture-id c1
assert_eq "$rc $(result status) $(result completeness) $(result acquisition)" "0 extracted blocked paywall" "a paywall teaser with real text is published: RESULT completeness blocked, acquisition paywall"
RC="$(result revision)"; SRCC="$VAULT/Sources/$RC/source.md"
assert_eq "$(capfm "$SRCC" completeness) $(capfm "$SRCC" blocked_kind) $(capfm "$SRCC" acquisition) $(capfm "$SRCC" fetch)" "blocked paywall paywall direct" "capture: completeness blocked, blocked_kind paywall, acquisition paywall, fetch direct"
assert_eq "$(rget c1 '.capture_report.fetch.stages | length') $(rget c1 '.capture_report.fetch.acquisition.kind') $(rget c1 '.capture_report.text.completeness')" "1 paywall blocked" "...one stage (no escalation past a paywall); the converter reports blocked too"
assert_contains "$(cat "$SRCC")" "free to read" "the teaser text before the wall is the capture-text"
assert_eq "$(grep -c '^  egress:' "$SRCC")" "0" "...no egress line (direct)"

# ==================================================================================================
echo "--- (d) a login wall: failed, blocked, not retryable, no folder ---"
n0="$(nfolders)"
run "$MNEMON" --instance t add --url "$BASE/login" --capture-id d1
assert_eq "$rc $(result status) $(result error_class) $(result retryable)" "1 failed blocked false" "a login wall fails the capture: exit 1, blocked, not retryable"
assert_eq "$(nfolders)" "$n0" "...no folder"
assert_eq "$(rget d1 '.state + " " + .error_class + " " + (.retryable|tostring) + " " + .capture_report.fetch.acquisition.kind + " " + .stage')" "failed blocked false login direct" "...the receipt: failed/blocked, the fetch report's classification login, stage direct"
assert_eq "$(rget d1 '.capture_report.fetch.stages | length')" "1" "...never escalated"
assert_eq "$(ls "$ARCHIVE" | grep -c "$(rget d1 '.revision')" || true)" "0" "...nothing archived"

# ==================================================================================================
echo "--- (d2) a hostile page: the classification is linear, acquire_timeout holds, the vault lock is free (G1, D11) ---"
# 400 000 bytes of <script> elements none of which is closed: the regex classification of the first
# release rescanned the body from every one of them (quadratic) and held the vault lock for minutes
python3 - "$SITE/hostile.html" <<'PY'
import sys
open(sys.argv[1], "wb").write((b"<script>" + b"a" * 8) * 25000)
PY
"$MNEMON" init t3 --vault "$VAULT" --archive "$ARCHIVE" --non-interactive >/dev/null 2>&1 || { echo "mnemon init t3 failed"; exit 1; }
printf 'acquire_timeout: 5\n' >> "$XDG_CONFIG_HOME/mnemon/instances/t3.yaml"
t0=$(date +%s)
STUB_RENDER=fail "$MNEMON" --instance t3 add --url "$BASE/hostile" --capture-id d2 >"$TEST_TMPDIR/d2.out" 2>"$TEST_TMPDIR/d2.err" &
D2_PID=$!
i=0; while kill -0 "$D2_PID" 2>/dev/null && [[ $i -lt 200 ]]; do sleep 0.05; i=$((i + 1)); done
if kill -0 "$D2_PID" 2>/dev/null; then
  fail "the hostile capture finishes within 10 s" "still running after $(( $(date +%s) - t0 )) s"; kill "$D2_PID" 2>/dev/null || true
else
  pass "the hostile capture finishes within 10 s ($(( $(date +%s) - t0 )) s)"
fi
rc=0; wait "$D2_PID" || rc=$?
out="$(cat "$TEST_TMPDIR/d2.out")"
python3 "$MNEMON_ROOT/bin/vault-lock.py" --vault "$VAULT" -- true && pass "a second writer takes the vault lock afterwards" || fail "a second writer takes the vault lock afterwards" "vault-lock.py exit $?"
assert_eq "$rc $(result error_class)" "3 environment" "...the page is a JavaScript shell the renderer could not render: exit 3 environment"
assert_eq "$(rget d2 '.capture_report.fetch.acquisition.kind + " " + (.capture_report.fetch.acquisition.visible_words|tostring)')" "js-shell 0" "...classified js-shell with zero visible words (the unclosed <script> swallows the rest)"
assert_eq "$(nfolders)" "$n0" "...no folder"

# ==================================================================================================
echo "--- (e) a JavaScript shell: environment without a renderer; the external fetcher when allowed ---"
STUB_RENDER=fail run "$MNEMON" --instance t add --url "$BASE/spa" --capture-id e1
assert_eq "$rc $(result error_class) $(result retryable)" "3 environment true" "a JavaScript shell with a failing renderer and external_fetch off → exit 3 environment (retryable)"
assert_eq "$(rget e1 '[.capture_report.fetch.stages[] | .stage + ":" + .outcome] | join(" ")')" "direct:js-shell render:failed" "...the stages: direct js-shell, render failed; no external stage"
assert_eq "$(rget e1 '.state + " " + .error_class + " " + .capture_report.fetch.acquisition.kind')" "failed environment js-shell" "...the receipt: failed/environment, classification js-shell"
assert_eq "$(nfolders)" "$n0" "...no folder"
STUB_RENDER=fail FIRECRAWL_API_KEY=stub-key MNEMON_EXTERNAL_FETCH_CMD="$STUBS/external-fetch.sh" run "$MNEMON" --instance t2 add --url "$BASE/spa" --capture-id e2
assert_eq "$rc $(result status)" "0 extracted" "with external_fetch auto and a key, the vendor's HTML is published"
RE="$(result revision)"; SRCE="$VAULT/Sources/$RE/source.md"
assert_eq "$(capfm "$SRCE" egress) $(capfm "$SRCE" fetch) $(fm "$SRCE" egress) $(capfm "$SRCE" raw_kind)" "external firecrawl external response" "capture: egress external, fetch firecrawl, raw_kind response; egress: external in the metadata"
assert_eq "$(rget e2 '[.capture_report.fetch.stages[] | .stage + ":" + .outcome] | join(" ")')" "direct:js-shell render:failed external:firecrawl:ok" "...the stages end at external:firecrawl ok"
assert_eq "$(rget e2 '.capture_report.fetch.egress + " " + .capture_report.fetch.method + " " + .request.external')" "external firecrawl auto" "...the fetch report says egress external, method firecrawl; the request bound external auto"
assert_eq "$(cat "$STUB_DIR/external-calls")" "firecrawl $BASE/spa" "the external stub was called once: firecrawl <url>"
assert_contains "$(capfm "$SRCE" method)" '"firecrawl+' "method firecrawl+<converter>"
assert_eq "$(fm "$SRCE" title)" '"Vendor Page"' "the vendor's document is the raw"
assert_eq "$(rget e2 '.capture_report.text.candidates[0].note')" "skipped: external raw" "the converter skips defuddle for an external raw"

# ==================================================================================================
echo "--- (f) arXiv: the version resolved from the abs page, the LaTeXML full text sanitized ---"
MNEMON_FETCH_URL_REWRITE="https://arxiv.org=$BASE" run "$MNEMON" --instance t add --url "https://arxiv.org/abs/1234.56789v1" --capture-id f1
assert_eq "$rc $(result status)" "0 extracted" "mnemon add --url https://arxiv.org/abs/1234.56789v1 (rewritten to the local server) exits 0"
RF="$(result revision)"; SRCF="$VAULT/Sources/$RF/source.md"
assert_eq "$(fm "$SRCF" canonical_url) $(fm "$SRCF" source_type)" '"https://arxiv.org/abs/1234.56789" paper' "canonical_url abs/<id> (no version), source_type paper"
assert_eq "$(fm "$SRCF" url)" '"https://arxiv.org/abs/1234.56789v1"' "url: the literal URL"
assert_contains "$(fm "$SRCF" author)" "Author, First" "author from the citation_author tags"
assert_eq "$(fm "$SRCF" arxiv_id) $(fm "$SRCF" arxiv_version) $(fm "$SRCF" arxiv_requested_version)" '"1234.56789" 1 1' "arxiv_id, arxiv_version 1 (resolved from citation_pdf_url), arxiv_requested_version 1"
assert_eq "$(fm "$SRCF" published)" '"2026-02-03"' "published: from citation_date"
assert_eq "$(fm "$SRCF" title)" '"A Synthetic Paper on Example Methods"' "the title of the document"
assert_eq "$(rget f1 '.request.requested_version|tostring') $(rget f1 '.request.resource_key')" "1 https://arxiv.org/abs/1234.56789" "the request binds requested_version 1 and the canonical resource key"
assert_eq "$(rget f1 '.capture_report.fetch.fetch_url + " " + .capture_report.fetch.kind + " " + (.capture_report.fetch.metadata.arxiv_version|tostring) + " " + .capture_report.fetch.metadata.published_source')" "https://arxiv.org/html/1234.56789v1 arxiv 1 citation_date" "the fetch report: the html/<id>v1 fetch URL, kind arxiv, arxiv_version 1, published from citation_date"
assert_eq "$(rget f1 '[.capture_report.fetch.stages[] | .stage + ":" + .outcome] | join(" ")')" "metadata:ok direct:ok" "...stages: metadata (the abs page), direct (the HTML)"
assert_eq "$(grep -c 'GET /abs/1234.56789v1' "$REQLOG") $(grep -c 'GET /html/1234.56789v1' "$REQLOG")" "1 1" "the server saw the abs page and the v1 HTML once each (the policy's rewrite, the identity untouched)"
bodyf="$(cat "$SRCF")"
assert_eq "$(grep -c 'data:' "$SRCF" || true)" "0" "no data: URI in source.md (the base64 figure and the listing button are gone)"
assert_not_contains "$bodyf" '\iow_now' "...no \\iow_now residue"
assert_not_contains "$bodyf" "⬇" "...no download button"
assert_not_contains "$bodyf" "newcommand" "...no macro residue before the title"
assert_contains "$bodyf" "return x + 1" "the listing lines are kept"
assert_contains "$bodyf" "first line of a boxed listing" "...the boxed listing's lines too (the \\iow_now residue split into lines)"
assert_contains "$bodyf" "Task A · Acc" "the merged-cell table is rewritten with stacked headers joined by ' · '"
assert_eq "$(grep -c '^\[TABLE\]$' "$SRCF" || true)" "1" "the literal [TABLE] inside the code example is untouched"
if [[ $HAVE_PANDOC -eq 1 ]]; then
  assert_eq "$(capfm "$SRCF" converter) $(capfm "$SRCF" completeness)" "pandoc complete" "with pandoc: converter pandoc, completeness complete"
  [[ "$(grep -c '^| Model ' "$SRCF" || true)" -ge 1 ]] && pass "...a pipe table" || fail "a pipe table" "no '| Model' row"
  [[ "$(grep -c '^```' "$SRCF" || true)" -ge 2 ]] && pass "...a fenced listing" || fail "a fenced listing" "no fence"
  assert_contains "$bodyf" "~~~~text" "...the \\iow_now residue inside a ~~~~text fence (a markdown-level repair of the candidate)"
else
  assert_eq "$(capfm "$SRCF" converter) $(capfm "$SRCF" completeness)" "dom-text partial" "without pandoc: the DOM text, partial (the table is not a markdown table)"
fi
assert_contains "$(capfm "$SRCF" repairs)" "latexml_listings=1" "repairs: the listing run"
assert_contains "$(capfm "$SRCF" repairs)" "tables_rewritten=1" "...the merged-cell table"
assert_contains "$(capfm "$SRCF" repairs)" "data_links=1" "...the download button"
MNEMON_FETCH_URL_REWRITE="https://arxiv.org=$BASE" run "$MNEMON" --instance t add --url "https://arxiv.org/abs/1234.56789v2" --capture-id f2
assert_eq "$rc $(result status) $(result revision)" "0 skipped-duplicate $RF" "another version of the captured paper without --recapture → skipped-duplicate"
assert_contains "$err" "arXiv version 1" "...naming the captured version"

# ==================================================================================================
echo "--- (g) a site: map → plan, the bundle raw, folded_sources, a crash and a resume without refetching ---"
PLAN="$TEST_TMPDIR/plan.json"
run "$MNEMON" --instance t site map "$BASE/" --out "$PLAN"
assert_eq "$rc" "0" "mnemon site map <landing> --out plan.json exits 0"
assert_eq "$(jq -r '.schema + " " + .landing' "$PLAN")" "mnemon-site-plan/1 $BASE/" "a mnemon-site-plan/1 for the landing"
assert_eq "$(jq -r '[.pages[] | select(.include) | .url] | join(" ")' "$PLAN")" "$BASE/ $BASE/product $BASE/docs $BASE/blog/post" "included: the landing, /product, /docs, /blog/post (discovery order)"
assert_eq "$(jq -r '[.pages[] | select(.include) | .role] | join(" ")' "$PLAN")" "landing product docs blog" "...with the roles landing, product, docs, blog"
assert_eq "$(jq -r '[.pages[] | select(.url | endswith("/privacy")) | .role + " " + (.include|tostring) + " " + .reason] | join("")' "$PLAN")" "legal false excluded: legal" "/privacy is excluded: legal"
assert_eq "$(jq -r '[.pages[] | select(.url | endswith("/de-de/x")) | .role + " " + (.include|tostring) + " " + .reason] | join("")' "$PLAN")" "localized false excluded: localized" "/de-de/x is excluded: localized"
assert_eq "$(jq -r '(.discovery.robots.fetched|tostring) + " " + (.discovery.robots.status|tostring) + " " + ([.discovery.sources[] | .name] | join(" "))' "$PLAN")" "true 200 landing-links sitemap llms.txt docs-toc github-readme" "robots.txt fetched (200); the discovery sources recorded"
assert_eq "$(jq -r '[.discovery.sources[] | select(.name == "landing-links" or .name == "sitemap") | (.yield|tostring)] | join(" ")' "$PLAN")" "3 2" "...the landing linked three pages, the sitemap yielded the two more (/privacy, /de-de/x)"
assert_eq "$(jq -r '.pages[0].title + " " + ([.hosts[]] | join(","))' "$PLAN")" "Example Site 127.0.0.1" "the landing's title and the one eligible host"
check_keys "$PLAN" "$REPORTS/site-plan.json" "the plan has the key set of reports/site-plan.json" "" "" discovery discovery.robots discovery.sources.0 pages.0 caps
run "$MNEMON" --instance t add --url "$BASE/" --site "$PLAN" --capture-id g1
assert_eq "$rc $(result status)" "0 extracted" "mnemon add --url <landing> --site plan.json exits 0"
RG="$(result revision)"; SRCG="$VAULT/Sources/$RG/source.md"; BUNDLE="$ARCHIVE/$RG.site"
assert_dir_exists "$BUNDLE" "the raw is the bundle directory <revision>.site/"
assert_eq "$(ls "$BUNDLE" | tr '\n' ' ')" "01.fetch.json 01.html 02.fetch.json 02.html 03.fetch.json 03.html 04.fetch.json 04.html manifest.json plan.json " "...NN.html + NN.fetch.json per page, plan.json, manifest.json"
assert_eq "$(jq -r '[.members[] | .path] | sort | join(" ")' "$BUNDLE/manifest.json")" "01.fetch.json 01.html 02.fetch.json 02.html 03.fetch.json 03.html 04.fetch.json 04.html plan.json" "the manifest's members cover every file but the manifest"
python3 - "$BUNDLE" <<'PY' && pass "...every member's size and sha256 match the file" || fail "member hashes" "a member differs"
import hashlib, json, os, sys
d = sys.argv[1]
m = json.load(open(os.path.join(d, "manifest.json")))
for e in m["members"]:
    b = open(os.path.join(d, e["path"]), "rb").read()
    assert len(b) == e["bytes"] and hashlib.sha256(b).hexdigest() == e["sha256"], e["path"]
PY
MSHA="$(sha "$BUNDLE/manifest.json")"
assert_eq "$(rget g1 '.raw_sha256') $(capfm "$SRCG" raw_sha256)" "$MSHA \"$MSHA\"" "raw_sha256 = the manifest's hash (receipt and frontmatter)"
assert_eq "$(rget g1 '.raw_bytes')" "$(jq '[.members[].bytes] | add' "$BUNDLE/manifest.json")" "raw_bytes = the members' sum"
assert_eq "$(capfm "$SRCG" raw_kind) $(capfm "$SRCG" fetch) $(capfm "$SRCG" completeness) $(fm "$SRCG" site_pages) $(fm "$SRCG" source_type) $(fm "$SRCG" archive)" "site site complete \"4/4\" website \"$RG.site\"" "raw_kind site, fetch site, completeness complete, site_pages 4/4, source_type website, archive: the bundle"
cmp -s "$BUNDLE/plan.json" "$PLAN" && pass "the bundle's plan.json is a byte copy of the plan" || fail "plan.json copy" "differs"
assert_eq "$(jq -r '[.pages[] | .disposition] | join(" ")' "$BUNDLE/manifest.json")" "captured captured captured captured" "every page captured (acquisition dispositions in the manifest)"
assert_eq "$(jq -r '[.pages[] | .raw_kind + ":" + (.http_status|tostring)] | join(" ")' "$BUNDLE/manifest.json")" "response:200 response:200 response:200 response:200" "...each a direct response 200"
assert_eq "$(rget g1 '[.capture_report.site.pages[] | .disposition] | join(" ")')" "converted converted converted converted" "...and converted (conversion dispositions in the site report)"
fs="$(awk '/^folded_sources:/{f=1;next} f&&/^[^ ]/{exit} f' "$SRCG")"
assert_contains "$fs" "  - url: \"$BASE/product\"" "folded_sources: /product"
assert_contains "$fs" "    role: product" "...with its role"
assert_contains "$fs" "    disposition: converted" "...its disposition"
assert_contains "$fs" "    completeness: complete" "...its completeness"
assert_contains "$fs" "    missing_lines: 0" "...its missing lines"
assert_contains "$fs" "  - url: \"$BASE/docs\"" "...the docs page"
assert_contains "$fs" "  - url: \"$BASE/blog/post\"" "...the blog post"
assert_contains "$fs" "    role: blog" "...with the blog role"
assert_eq "$(printf '%s\n' "$fs" | grep -c '^  - url:' || true)" "3" "...three entries (every included page after the landing)"
bodyg="$(cat "$SRCG")"
assert_contains "$bodyg" "# Example Site — site capture" "the composite starts with the landing's title"
assert_contains "$bodyg" "## Part 1 — $BASE/" "...Part 1 is the landing"
assert_contains "$bodyg" "## Part 2 — $BASE/product (product)" "...Part 2 the product page"
assert_contains "$bodyg" "## Part 3 — $BASE/docs (docs)" "...Part 3 the docs page"
assert_contains "$bodyg" "## Part 4 — $BASE/blog/post (blog)" "...Part 4 the blog post — plan order"
assert_contains "$bodyg" "The blog post is discovered through the landing's links" "...with every page's text"
assert_eq "$(rget g1 '.request.site') $(rget g1 '.site_plan_sha256') $(rget g1 '.input_path') $(rget g1 '.request.depth')" "$(sha "$PLAN") $(sha "$PLAN") $PLAN off" "the plan is the input: bound as its sha256, frozen as site_plan_sha256, the locator; depth off"
assert_eq "$(rget g1 '.capture_report.site.schema + " " + (.capture_report.site.converted|tostring) + "/" + (.capture_report.site.included|tostring) + " " + .capture_report.site.completeness + " " + (.capture_report.fetch|tostring)')" "mnemon-site/1 4/4 complete null" "the site report in the receipt (no page-level fetch report)"
assert_file_exists "$ARCHIVE/$RG.report.json" "the sidecar holds the site report"
assert_eq "$(jq -r '.site.schema + " " + (.fetch|tostring)' "$ARCHIVE/$RG.report.json")" "mnemon-site/1 null" "...site, no fetch report"
check_keys "$BUNDLE/manifest.json" "$REPORTS/site-manifest.json" "the manifest has the key set of reports/site-manifest.json" "" "" pages.0 members.0
check_keys "$(receipt g1)" "$REPORTS/site-report.json" "the site report has the key set of reports/site-report.json" capture_report.site "" pages.0
check_keys "$(receipt g1)" "$REPORTS/receipts/request-v2-site-auto.json" "the site receipt has the key set of receipts/request-v2-site-auto.json" "" "" request capture_report
check_keys "$BUNDLE/01.fetch.json" "$REPORTS/fetch-ok.json" "a page's fetch report in the bundle has the key set of reports/fetch-ok.json" "" "" stages.0 acquisition metadata hops.0 policy
# the one stdout line of fetch-bundle (A12), from the CLI itself on the same plan into a scratch directory
python3 "$MNEMON_ROOT/bin/capture-site.py" fetch-bundle "$PLAN" --out "$TEST_TMPDIR/bundle2" --report "$TEST_TMPDIR/bundle2.json" > "$TEST_TMPDIR/bundle-stdout.json" 2>/dev/null || fail "capture-site.py fetch-bundle on the plan" "exit $?"
assert_eq "$(wc -l < "$TEST_TMPDIR/bundle-stdout.json" | tr -d ' ')" "1" "capture-site.py fetch-bundle prints exactly one line on stdout"
check_keys "$TEST_TMPDIR/bundle-stdout.json" "$REPORTS/site-bundle-stdout.json" "...with the key set of reports/site-bundle-stdout.json" ""
assert_eq "$(jq -r '.raw_sha256 + " " + (.raw_bytes|tostring)' "$TEST_TMPDIR/bundle-stdout.json")" "$(sha "$TEST_TMPDIR/bundle2/manifest.json") $(jq '[.members[].bytes] | add' "$TEST_TMPDIR/bundle2/manifest.json")" "...raw_sha256 = that bundle's manifest hash, raw_bytes = its members' sum"
assert_eq "$(jq -r '.schema + " " + (.raw_sha256|tostring) + " " + (.error_class|tostring)' "$TEST_TMPDIR/bundle2.json")" "mnemon-site-fetch/1 $(sha "$TEST_TMPDIR/bundle2/manifest.json") null" "...and --report (mnemon-site-fetch/1, not a frozen schema) carries the same hash and no error"
# a crash after the bundle was published: the resume assembles the published bundle and fetches nothing
n_req1="$(requests)"
MNEMON_TEST_CRASH_AT=after-raw-published run "$MNEMON" --instance t add --url "$BASE/" --site "$PLAN" --recapture --capture-id g2
[[ $rc -ne 0 ]] && pass "a crash injected at after-raw-published kills the capture (exit $rc)" || fail "the crash" "exit 0"
assert_eq "$(rget g2 '.state + " " + (.raw_published|tostring) + " " + (.source_published|tostring) + " " + .request.supersedes')" "started true false $RG" "...the receipt: started, the bundle published, no source yet, supersedes $RG"
RG2="$(rget g2 '.revision')"
assert_dir_exists "$ARCHIVE/$RG2.site" "...the bundle sits at its final name"
[[ -d "$VAULT/Sources/$RG2" ]] && fail "no source folder yet" "exists" || pass "...no source folder yet"
n_req2="$(requests)"
[[ "$n_req2" -gt "$n_req1" ]] && pass "...the server saw the bundle's requests ($((n_req2 - n_req1)))" || fail "requests during the crash run" "none"
run "$MNEMON" --instance t add --resume g2
assert_eq "$rc $(result status) $(result revision)" "0 extracted $RG2" "mnemon add --resume <capture id> finishes the capture from the published bundle"
assert_eq "$(requests)" "$n_req2" "...with no new request to the server: assemble only, never a refetch"
assert_eq "$(rget g2 '.raw_sha256') $(rget g2 '.capture_report.site.schema') $(rget g2 '.attempts')" "$(sha "$ARCHIVE/$RG2.site/manifest.json") mnemon-site/1 2" "...the bundle unchanged, the site report recorded, a second attempt"
assert_eq "$(fm "$VAULT/Sources/$RG2/source.md" supersedes) $(fm "$VAULT/Sources/$RG2/source.md" site_pages)" "\"$RG\" \"4/4\"" "...the new revision supersedes $RG, 4/4 pages"
assert_eq "$(ls "$ARCHIVE" | grep -c 'staging' || true)" "0" "no staging left in the archive"

# ==================================================================================================
echo "--- (g2) the live gate runner with a pre-placed plan (G13): no mapping, the plan used as it is ---"
GATE_OUT="$TEST_TMPDIR/gate"; GATE_DATE="$(date -u +%Y-%m-%d)"
gslug="$(printf '%s' "$BASE/" | sed -e 's#^https\{0,1\}://##' -e 's#[^A-Za-z0-9._-]#_#g' | cut -c1-80)"
mkdir -p "$GATE_OUT/$GATE_DATE/$gslug"
jq '.pages |= map(if .role == "landing" then . else (.include = false | .reason = "excluded: hand-edited") end)' "$PLAN" > "$GATE_OUT/$GATE_DATE/$gslug/plan.json"
printf '%s | site | 10 | data:\n' "$BASE/" > "$TEST_TMPDIR/gate-corpus.txt"
sitemap_before="$(grep -c 'GET /sitemap.xml' "$REQLOG" || true)"
rc=0; MNEMON_FIXTURES_DIR="$GATE_OUT" bash "$MNEMON_ROOT/tests/live/capture-gate.sh" "$TEST_TMPDIR/gate-corpus.txt" >"$TEST_TMPDIR/gate.out" 2>"$TEST_TMPDIR/gate.err" || rc=$?
assert_eq "$rc" "0" "capture-gate.sh over a one-item corpus with a pre-placed plan exits 0 ($(tail -n 1 "$TEST_TMPDIR/gate.err" 2>/dev/null | cut -c1-120))"
assert_contains "$(cat "$TEST_TMPDIR/gate.out")" "using the pre-placed plan $GATE_OUT/$GATE_DATE/$gslug/plan.json" "...the runner says it used the pre-placed plan"
assert_contains "$(cat "$TEST_TMPDIR/gate.out")" "[ok] $BASE/" "...and the item is ok"
assert_eq "$(jq -r '[.pages[] | select(.include)] | length' "$GATE_OUT/$GATE_DATE/$gslug/bundle/plan.json" 2>/dev/null)" "1" "...the bundle was fetched from that plan (the landing alone)"
assert_eq "$(grep -c 'GET /sitemap.xml' "$REQLOG" || true)" "$sitemap_before" "...and no mapping request was made"
assert_contains "$(cat "$GATE_OUT/$GATE_DATE/report.md")" "| $gslug | site |" "...the summary row is written"

# ==================================================================================================
echo "--- (h) a 1 MB text: chunked extraction under extract_inline_max_bytes ---"
python3 - "$TEST_TMPDIR/big.md" <<'PY'
import random, sys
random.seed(7)
words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
out = []
for s in range(1, 13):
    out.append("## Section %d\n\n" % s)
    body, size = [], 0
    while size < 85000:
        para = " ".join(random.choice(words) for _ in range(120)) + ".\n\n"
        body.append(para); size += len(para)
    if s == 5:
        body.insert(3, "```\ncode line\n## not a heading: inside the fence\nmore code\n```\n\n")
    out.append("".join(body))
open(sys.argv[1], "w").write("".join(out))
PY
big_bytes="$(wc -c < "$TEST_TMPDIR/big.md" | tr -d ' ')"
[[ "$big_bytes" -gt 1000000 ]] && pass "fixture: $big_bytes bytes of 12 sections" || fail "fixture size" "$big_bytes"
rm -rf "$STUB_DIR"; mkdir -p "$STUB_DIR"
run_stdin "$TEST_TMPDIR/big.md" "$MNEMON" --instance t add --origin text --title "Big" --capture-id h1
assert_eq "$rc $(result status)" "0 extracted" "a 1 MB text through mnemon add --origin text is extracted"
RH="$(result revision)"; EX="$VAULT/Sources/$RH/extract.md"
n_parts="$(rget h1 '.calls | map(select(.kind == "part")) | length')"
[[ "$n_parts" -ge 3 ]] && pass "the receipt records $n_parts part calls (≥ 3)" || fail "part count ≥ 3" "$n_parts"
assert_eq "$(fm "$EX" extraction) $(fm "$EX" parts)" "chunked $n_parts" "extract.md: extraction chunked, parts $n_parts"
assert_eq "$(grep -l '^part$' "$STUB_DIR"/call-*.kind 2>/dev/null | wc -l | tr -d ' ') $(grep -l '^main$' "$STUB_DIR"/call-*.kind 2>/dev/null | wc -l | tr -d ' ')" "$n_parts 1" "the stub claude saw $n_parts part prompts and one merge prompt"
assert_eq "$(rget h1 '.calls | map(select(.kind == "merge")) | length') $(rget h1 '.calls | map(select(.state == "done")) | length') $(rget h1 '.calls | map(select(.state == "reserved")) | length')" "1 $((n_parts + 1)) 0" "...one merge call; every call reconciled, none left reserved"
assert_eq "$(rget h1 '.cost_usd') $(rget h1 '.cost_unknown')" "$(awk -v n="$((n_parts + 1))" 'BEGIN { printf "%g", n * 0.012 }') false" "cost_usd = the sum of every call's cost"
m1="$(grep -l '^main$' "$STUB_DIR"/call-*.kind | head -1 | sed 's/\.kind$//')"
assert_contains "$(cat "$m1.stdin")" "=== PART EXTRACTS ($n_parts) ===" "the merge prompt carries the part extracts"
assert_eq "$(fm "$VAULT/Sources/$RH/source.md" origin) $(capfm "$VAULT/Sources/$RH/source.md" completeness)" "text unknown" "the text source: origin text, completeness unknown (no reference)"

# ==================================================================================================
echo "--- (i) doctor and audit --canonical ---"
run "$MNEMON" --instance t doctor
assert_contains "$out" " capture-deps — " "doctor: the capture-deps line"
assert_contains "$out" "defuddle missing" "...defuddle reported missing (it is off PATH here)"
if [[ $HAVE_PANDOC -eq 1 ]]; then assert_contains "$out" "pandoc $(pandoc --version | head -1 | awk '{print $2}')" "...pandoc with its version"; else assert_contains "$out" "pandoc missing" "...pandoc reported missing"; fi
assert_contains "$out" "ok egress — external_fetch off (never past the local chain)" "doctor: the egress line (off for instance t)"
assert_contains "$out" "ok canonical — canonicalization v1 (capture-fetch.py --canonical-only)" "doctor: canonicalization v1 answered by capture-fetch.py"
run "$MNEMON" --instance t2 doctor
assert_contains "$out" "egress — external_fetch auto" "doctor: instance t2 reports external_fetch auto"
assert_contains "$out" "FIRECRAWL_API_KEY not set" "...and that no key is set in this environment (never the key)"
run "$MNEMON" --instance t audit --canonical
assert_eq "$rc" "0" "mnemon audit --canonical exits 0"
assert_contains "$out" "$BASE/article" "...groups the two utm variants' folders under their canonical URL"
assert_contains "$out" "  $R1  url=$BASE/article?utm_source=x  supersedes=-  extract=ok" "...the first revision"
assert_contains "$out" "  $R3  url=$BASE/article?utm_source=y  supersedes=$R1  extract=ok" "...and the recapture with its supersedes link"
assert_contains "$out" "  $RG2  url=$BASE/  supersedes=$RG  extract=ok" "...the site landing's revisions form the second group"
assert_contains "$out" "2 group(s) coincide under canonicalization v1; nothing was merged" "...two groups, nothing merged"
run "$MNEMON" --instance t audit --canonical --json
assert_eq "$(printf '%s' "$out" | jq -r '.schema + " " + (.canonical_version|tostring) + " " + (.groups|length|tostring)')" "mnemon-canonical-audit/1 1 2" "...--json: mnemon-canonical-audit/1, v1, two groups"

# ==================================================================================================
echo "--- (j) status: superseded and chunked counts ---"
run "$MNEMON" --instance t status
assert_eq "$rc" "0" "mnemon status exits 0"
assert_contains "$out" "Superseded: 2" "status: 2 superseded revisions ($R1 and $RG)"
assert_contains "$out" "Chunked: 1" "status: 1 chunked extraction"

summary
