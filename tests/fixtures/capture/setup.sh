#!/usr/bin/env bash
# Shared scaffolding for tests/test_capture.sh, test_gateway.sh, test_archival.sh, test_site.sh,
# test_chunked.sh, test_fence.sh and test_lock.sh: a temp XDG tree, a vault, an instance config,
# the stubs of the capture contracts §9 and of the capture layer's seams (claude, capture-fetch.py,
# capture-site.py, render-url.sh, defuddle, pdftotext, pdfinfo, media-extract.py, kb-depth-fold.py)
# and a probe record for the stub's version. Source it after tests/test-helper.sh with MNEMON_ROOT
# set; it exports everything the gateway needs and defines helpers. bash 3.2.
#
# The seam stubs write complete reports of the frozen schemas in tests/fixtures/capture/reports/
# (every key of the fixture, values per mode), so the gateway consumes the real shapes:
#   capture-fetch.py  STUB_FETCH: html (default) · empty · pdf · audio · rendered · external ·
#                     blocked-botwall · blocked-login · blocked-paywall · rate-limit ·
#                     js-shell-noenv · timeout · fail;  --canonical-only [--stdin] strips tracking
#                     keys and fragments, routes youtube/arxiv/github kinds; counts real fetches in
#                     $STUB_DIR/fetch-count and canonical lookups in $STUB_DIR/canon-count; every
#                     argv line in $STUB_DIR/fetch-calls; STUB_FETCH_HOOK (alias STUB_CURL_HOOK)
#                     runs after the raw is written; STUB_FETCH_EXT names the sniffed audio ext;
#                     STUB_FETCH_PUBLISHED="<date>|<source>" sets metadata.published(_source);
#                     STUB_FETCH_HTML_META="<meta …>" injects tags into the html raw's <head>
#   capture-site.py   map · fetch-bundle · verify · assemble (STUB_SITE: ok (default) · page-fail ·
#                     landing-fail · timeout-after-landing · timeout; STUB_SITE_CONVERT_FAIL=<n>
#                     makes page n convert-failed); calls in $STUB_DIR/site-calls
#   kb-depth-fold.py  STUB_DEPTH: off (default) · fold · timeout · noyaml, the A8 shape (targets, no
#                     composite_body); argv in $STUB_DIR/depth-calls
#   media-extract.py  STUB_YT=whisper (the whisper fallback), STUB_MEDIA: ok · timeout · env;
#                     accepts --timeout and records it in $STUB_DIR/media-calls

FIX="$MNEMON_ROOT/tests/fixtures/capture"
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-cap-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT STUB_MODE STUB_FIXTURE_BAD STUB_CURL STUB_FETCH STUB_RENDER STUB_DEFUDDLE STUB_PDFTOTEXT
# capture layer §7.16: inherited keys, proxies and test hooks never reach a suite
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
unset MNEMON_POLICY_ALLOW_LOOPBACK MNEMON_FETCH_URL_REWRITE MNEMON_EXTERNAL_FETCH_CMD
unset MNEMON_CAPTURE_FETCH_CMD MNEMON_CAPTURE_SITE_CMD MNEMON_DEPTH_FOLD_CMD MEDIA_EXTRACT_CMD RENDER_URL_CMD
unset STUB_SITE STUB_SITE_CONVERT_FAIL STUB_DEPTH STUB_MEDIA STUB_YT STUB_FETCH_HOOK STUB_CURL_HOOK STUB_FETCH_EXT STUB_FETCH_PUBLISHED
unset STUB_FIXTURE_PART STUB_FIXTURE_PART_BAD STUB_PART_BAD_CALLS STUB_FAIL_CALLS STUB_COST STUB_COST_NULL
echo "toolchain: $(bash --version | head -1) · $(python3 --version 2>&1)"
# the legacy run (PATH=/usr/bin:/bin:$PATH, §7.16 / A12): when the system bash is first on PATH
# the suite must really be running bash 3.2 and python 3.9 — asserted, not assumed
if [[ "$(command -v bash)" == /bin/bash ]]; then
  [[ "$(bash --version | head -1)" == *"version 3.2."* ]] && pass "legacy toolchain: bash 3.2.x" || fail "legacy toolchain: bash 3.2.x" "$(bash --version | head -1)"
  [[ "$(python3 --version 2>&1)" == "Python 3.9."* ]] && pass "legacy toolchain: python 3.9.x" || fail "legacy toolchain: python 3.9.x" "$(python3 --version 2>&1)"
fi
VAULT="$TEST_TMPDIR/vault"
ARCHIVE="$TEST_TMPDIR/archive"
STUBS="$TEST_TMPDIR/stubs"
STUB_DIR="$TEST_TMPDIR/stub-calls"
CONFIG="$TEST_TMPDIR/instance.yaml"
GW="$MNEMON_ROOT/bin/knowledge-gateway.sh"
mkdir -p "$VAULT/Sources" "$VAULT/_meta" "$ARCHIVE" "$STUBS" "$STUB_DIR" "$XDG_STATE_HOME/mnemon"
printf 'Test reader context\nsynced: 2026-10-01\n' > "$VAULT/reader-context.md"
cat > "$CONFIG" <<EOF
instance: captest
vault_path: $VAULT
reader_context_path: $VAULT/reader-context.md
search_provider: grep
default_model: sonnet
whisper_model: tiny
archive_dir: $ARCHIVE
extract_timeout: 20
EOF
# a config without archive_dir (raw_store: state)
sed '/^archive_dir:/d; s/^instance: captest/instance: captest-noarchive/' "$CONFIG" > "$TEST_TMPDIR/noarchive.yaml"

# --- stubs ---
cp "$FIX/stub-claude.sh" "$STUBS/claude"
# the capture-fetch.py stub is a fixture file (tests/test_chain.sh uses it too)
cp "$FIX/stub-capture-fetch.py" "$STUBS/capture-fetch.py"
cat > "$STUBS/capture-site.py" <<'STUB'
#!/usr/bin/env python3
"""capture-site.py stub (capture layer §3.6): map · fetch-bundle · verify · assemble with real canonical-JSON manifests."""
import hashlib, json, os, re, sys
from urllib.parse import urlsplit

STUB_DIR = os.environ.get("STUB_DIR", "")
NOW = "2026-10-15T12:00:00Z"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def record(line):
    if STUB_DIR:
        with open(os.path.join(STUB_DIR, "site-calls"), "a") as fh:
            fh.write(line + "\n")


def canon_bytes(obj):
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def opt(argv, name, default=None):
    if name in argv:
        return argv[argv.index(name) + 1]
    return default


FLAGS = {"--json", "--no-render", "--no-external"}


def positional(argv):
    """the positional arguments: every token that is neither an option nor an option's value"""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            skip = a not in FLAGS
            continue
        out.append(a)
    return out


def page_html(title, text):
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{text}</p></body></html>\n".encode("utf-8")


def fetch_report(url, nbytes):
    return {"schema": "mnemon-fetch/1", "url": url, "fetch_url": url, "canonical_url": url, "canonical_version": 1, "kind": "page",
            "requested_version": None, "final_url": url, "method": "direct", "raw_kind": "response", "http_status": 200,
            "content_type": "text/html", "charset": "utf-8", "bytes": nbytes, "ext": None, "audio_sniff": None, "egress": "direct",
            "stages": [{"stage": "direct", "outcome": "ok", "kind": None, "status": 200, "bytes": nbytes, "elapsed_ms": 10, "note": None}],
            "acquisition": {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 4},
            "metadata": {"title": None, "authors": [], "published": None, "published_source": None, "arxiv_id": None, "arxiv_version": None,
                         "arxiv_requested_version": None, "declared_canonical_url": None},
            "hops": [{"url": url, "status": 200, "address": "203.0.113.10"}], "elapsed_ms": 10, "error_class": None, "error": None,
            "retryable": None, "stage": None,
            "policy": {"user_agent": UA, "max_bytes": 20000000, "max_redirects": 5, "connect_timeout": 15, "read_timeout": 45}}


def cmd_map(argv):
    url = positional(argv)[1]
    out = opt(argv, "--out")
    base = url if url.endswith("/") else url + "/"
    host = urlsplit(url).hostname
    max_pages = int(opt(argv, "--max-pages", "40"))
    plan = {"schema": "mnemon-site-plan/1", "landing": url, "created": NOW,
            "discovery": {"sources": [{"name": "landing-links", "yield": 4, "note": None},
                                      {"name": "sitemap", "yield": 2, "note": "/sitemap.xml via robots.txt Sitemap:"}],
                          "robots": {"fetched": True, "status": 200, "groups": ["*"], "rules_applied": ["Disallow: /private"]}},
            "hosts": [host, "docs." + host],
            "pages": [
                {"n": 1, "url": url, "canonical_url": url, "role": "landing", "include": True, "reason": "landing", "title": "Example", "discovered_by": "landing", "render": None},
                {"n": 2, "url": base + "product", "canonical_url": base + "product", "role": "product", "include": True, "reason": "product page", "title": "Product", "discovered_by": "landing-links", "render": None},
                {"n": 3, "url": f"https://docs.{host}/guide", "canonical_url": f"https://docs.{host}/guide", "role": "docs", "include": True, "reason": "docs", "title": "Guide", "discovered_by": "docs-toc", "render": None},
                {"n": 4, "url": base + "privacy", "canonical_url": base + "privacy", "role": "legal", "include": False, "reason": "excluded: legal", "title": "Privacy", "discovered_by": "sitemap", "render": None},
                {"n": 5, "url": base + "private/x", "canonical_url": base + "private/x", "role": "other", "include": False, "reason": "excluded: robots", "title": None, "discovered_by": "sitemap", "render": None},
            ],
            "caps": {"max_pages": max_pages}}
    data = json.dumps(plan, ensure_ascii=False, indent=1) + "\n"
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(data)
    if "--json" in argv or not out:
        sys.stdout.write(data)
    return 0


def cmd_fetch_bundle(argv):
    plan_path = positional(argv)[1]
    out = opt(argv, "--out")
    report_path = opt(argv, "--report")
    mode = os.environ.get("STUB_SITE", "ok")
    plan_bytes = open(plan_path, "rb").read()
    plan = json.loads(plan_bytes.decode("utf-8"))
    if mode == "timeout":
        if report_path:
            json.dump({"error_class": "timeout", "error": "landing exceeded site_timeout", "retryable": True, "stage": "landing"}, open(report_path, "w"))
        return 124
    if mode == "landing-fail":
        if report_path:
            json.dump({"error_class": "blocked", "error": "landing: bot wall", "retryable": False, "stage": "landing"}, open(report_path, "w"))
        return 1
    os.makedirs(out, exist_ok=True)
    included = [p for p in plan["pages"] if p.get("include")]
    members = []
    pages = []
    for i, p in enumerate(included):
        nn = "%02d" % p["n"]
        if mode == "page-fail" and i == len(included) - 1 and i > 0:
            fr = fetch_report(p["url"], 0)
            fr.update({"http_status": 500, "method": None, "raw_kind": None, "error_class": "server-error", "error": "HTTP 500", "retryable": True, "stage": "direct"})
            frb = canon_bytes(fr)
            open(os.path.join(out, nn + ".fetch.json"), "wb").write(frb)
            members.append({"path": nn + ".fetch.json", "bytes": len(frb), "sha256": sha(frb)})
            pages.append({"n": p["n"], "url": p["url"], "role": p["role"], "file": None, "fetch_report": nn + ".fetch.json", "sha256": None, "bytes": None,
                          "disposition": "fetch-failed", "error_class": "server-error", "error": "HTTP 500", "raw_kind": None, "http_status": 500})
            continue
        if mode == "timeout-after-landing" and i > 0:
            pages.append({"n": p["n"], "url": p["url"], "role": p["role"], "file": None, "fetch_report": None, "sha256": None, "bytes": None,
                          "disposition": "skipped-timeout", "error_class": "timeout", "error": "site_timeout reached", "raw_kind": None, "http_status": None})
            continue
        html = page_html(p.get("title") or p["role"], f"{p['role']} text of page {p['n']}")
        frb = canon_bytes(fetch_report(p["url"], len(html)))
        open(os.path.join(out, nn + ".html"), "wb").write(html)
        open(os.path.join(out, nn + ".fetch.json"), "wb").write(frb)
        members.append({"path": nn + ".html", "bytes": len(html), "sha256": sha(html)})
        members.append({"path": nn + ".fetch.json", "bytes": len(frb), "sha256": sha(frb)})
        pages.append({"n": p["n"], "url": p["url"], "role": p["role"], "file": nn + ".html", "fetch_report": nn + ".fetch.json", "sha256": sha(html),
                      "bytes": len(html), "disposition": "captured", "error_class": None, "error": None, "raw_kind": "response", "http_status": 200})
    open(os.path.join(out, "plan.json"), "wb").write(plan_bytes)
    members.append({"path": "plan.json", "bytes": len(plan_bytes), "sha256": sha(plan_bytes)})
    manifest = {"schema": "mnemon-site-bundle/1", "landing": plan["landing"], "created": NOW, "pages": pages, "members": members}
    mb = canon_bytes(manifest)
    open(os.path.join(out, "manifest.json"), "wb").write(mb)
    if report_path:
        json.dump({"error_class": None, "error": None, "retryable": None, "stage": None, "pages": len(pages)}, open(report_path, "w"))
    print(json.dumps({"raw_sha256": sha(mb), "raw_bytes": sum(m["bytes"] for m in members)}))
    return 0


def verify_dir(d, expected):
    mp = os.path.join(d, "manifest.json")
    if not os.path.isfile(mp):
        return "evidence-missing manifest.json"
    mb = open(mp, "rb").read()
    if sha(mb) != expected:
        return "evidence-mismatch manifest"
    manifest = json.loads(mb.decode("utf-8"))
    for m in manifest["members"]:
        p = os.path.join(d, m["path"])
        if ".." in m["path"].split("/") or m["path"].startswith("/"):
            return "evidence-mismatch " + m["path"]
        if os.path.islink(p):
            return "evidence-mismatch " + m["path"]
        if not os.path.isfile(p):
            return "evidence-missing " + m["path"]
        b = open(p, "rb").read()
        if len(b) != m["bytes"] or sha(b) != m["sha256"]:
            return "evidence-mismatch " + m["path"]
    return None


def cmd_verify(argv):
    d = positional(argv)[1]
    err = verify_dir(d, opt(argv, "--sha256"))
    if err:
        print(err, file=sys.stderr)
        return 1
    return 0


def cmd_assemble(argv):
    d = positional(argv)[1]
    out = opt(argv, "--out")
    report_path = opt(argv, "--report")
    if os.environ.get("STUB_SITE") == "assemble-timeout":
        # the landing's conversion worker killed by convert_timeout (G25): timeout, stage convert, not retryable
        if report_path:
            json.dump({"schema": "mnemon-site/1", "pages": [], "included": 0, "captured": 0, "converted": 0, "completeness": "partial",
                       "words": 0, "bytes": 0, "error_class": "timeout", "error": "the landing page: conversion exceeded the assembly budget",
                       "retryable": False, "stage": "convert"}, open(report_path, "w"))
        print("capture-site: timeout: the landing page: conversion exceeded the assembly budget", file=sys.stderr)
        return 124
    manifest = json.loads(open(os.path.join(d, "manifest.json"), "rb").read().decode("utf-8"))
    plan = json.loads(open(os.path.join(d, "plan.json"), "rb").read().decode("utf-8"))
    titles = {p["n"]: p.get("title") for p in plan["pages"]}
    cfail = os.environ.get("STUB_SITE_CONVERT_FAIL", "")
    cfail = {int(x) for x in cfail.split(",") if x.strip()}
    landing_title = titles.get(1) or "Site"
    parts = [f"# {landing_title} — site capture\n"]
    pages = []
    converters = ["pandoc", "dom-text", "pandoc", "pandoc"]
    for idx, p in enumerate(manifest["pages"]):
        if p["disposition"] != "captured":
            pages.append({"n": p["n"], "url": p["url"], "role": p["role"], "disposition": p["disposition"], "converter": None, "completeness": None,
                          "missing_count": None, "glued_count": None, "words": 0, "bytes": 0, "error_class": p["error_class"], "error": p["error"]})
            continue
        if p["n"] in cfail:
            pages.append({"n": p["n"], "url": p["url"], "role": p["role"], "disposition": "convert-failed", "converter": None, "completeness": None,
                          "missing_count": None, "glued_count": None, "words": 0, "bytes": 0, "error_class": "convert", "error": "empty conversion output"})
            continue
        html = open(os.path.join(d, p["file"]), "rb").read().decode("utf-8")
        m = re.search(r"<p>(.*?)</p>", html, re.S)
        text = m.group(1) if m else ""
        body = f"# {titles.get(p['n']) or p['role']}\n\n{text}\n"
        conv = converters[idx % len(converters)]
        pages.append({"n": p["n"], "url": p["url"], "role": p["role"], "disposition": "converted", "converter": conv, "completeness": "complete",
                      "missing_count": 0, "glued_count": 0, "words": len(body.split()), "bytes": len(body.encode("utf-8")), "error_class": None, "error": None})
        if p["n"] == 1:
            parts.append(f"\n## Part 1 — {p['url']}\n\n{body}")
        else:
            parts.append(f"\n## Part {len(parts)} — {p['url']} ({p['role']})\n\n{body}")
    composite = "".join(parts)
    included = len(manifest["pages"])
    captured = sum(1 for p in manifest["pages"] if p["disposition"] == "captured")
    converted = sum(1 for p in pages if p["disposition"] == "converted")
    if converted == included:
        completeness = "complete"
    else:
        completeness = "partial"
    report = {"schema": "mnemon-site/1", "pages": pages, "included": included, "captured": captured, "converted": converted,
              "completeness": completeness, "words": len(composite.split()), "bytes": len(composite.encode("utf-8"))}
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    landing_ok = any(p["n"] == 1 and p["disposition"] == "converted" for p in pages)
    if not landing_ok:
        print("assemble: the landing page did not convert", file=sys.stderr)
        return 1
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(composite)
    os.rename(tmp, out)
    return 0


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else ""
    record(" ".join(argv))
    if cmd == "map":
        return cmd_map(argv)
    if cmd == "fetch-bundle":
        return cmd_fetch_bundle(argv)
    if cmd == "verify":
        return cmd_verify(argv)
    if cmd == "assemble":
        return cmd_assemble(argv)
    print("stub capture-site: usage", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
STUB
cat > "$STUBS/render-url.sh" <<'STUB'
#!/usr/bin/env bash
[[ "${STUB_RENDER:-fail}" == "ok" ]] && { printf 'rendered text content\n\nsecond paragraph\n'; exit 0; }
exit 11
STUB
cat > "$STUBS/defuddle" <<'STUB'
#!/usr/bin/env bash
# STUB_DEFUDDLE: ok (default) | empty | fail · `defuddle --version` answers
[[ "${1:-}" == "--version" ]] && { echo "0.0-stub"; exit 0; }
case "${STUB_DEFUDDLE:-ok}" in
  ok)    printf '## Hello\n\nWorld **bold** from defuddle.\n' ;;
  empty) printf '  \n' ;;
  *)     exit 1 ;;
esac
STUB
cat > "$STUBS/pdftotext" <<'STUB'
#!/usr/bin/env bash
# pdftotext -layout <pdf> <out>; STUB_PDFTOTEXT: ok (default) | empty
out="${@: -1}"
case "${STUB_PDFTOTEXT:-ok}" in
  ok) printf 'PDF text page one\n\fPDF text page two\n' > "$out" ;;
  *)  printf '\n\f\n' > "$out" ;;
esac
STUB
cat > "$STUBS/pdfinfo" <<'STUB'
#!/usr/bin/env bash
printf 'Title:          Stub  Paper\tTitle\nAuthor:         Nobody\nPages:          12\n'
STUB
cat > "$STUBS/media-extract.py" <<'STUB'
#!/usr/bin/env bash
# media-extract.py stub: youtube <url> --out-dir D … | audio --out-dir D (--url U | --file F) …
# [--timeout S]. STUB_YT=whisper makes the youtube run report a downloaded audio (the whisper
# fallback); STUB_MEDIA: ok (default) · timeout (error.json + exit 124) · env (exit 3). Every call is
# appended to $STUB_DIR/media-calls (the command, its --url/--file and the --timeout it received).
cmd="$1"; shift
out=""; url=""; file=""; timeout=""; prev=""
for a in "$@"; do
  case "$prev" in --out-dir) out="$a" ;; --url) url="$a" ;; --file) file="$a" ;; --timeout) timeout="$a" ;; esac
  prev="$a"
done
[[ -n "$out" ]] || { echo "stub media-extract: --out-dir required" >&2; exit 1; }
[[ -n "${STUB_DIR:-}" ]] && printf '%s url=%s file=%s timeout=%s\n' "$cmd" "$url" "$file" "$timeout" >> "$STUB_DIR/media-calls"
mkdir -p "$out"
case "${STUB_MEDIA:-ok}" in
  timeout) printf '{"schema":"mnemon-media-error/1","error_class":"timeout","stage":"download","elapsed_s":%s}\n' "${timeout:-0}" > "$out/error.json"; exit 124 ;;
  env)     echo "stub media-extract: yt-dlp missing" >&2; exit 3 ;;
esac
printf 'transcript line one\ntranscript line two\n' > "$out/transcript.txt"
if [[ "$cmd" == "youtube" ]]; then
  if [[ "${STUB_YT:-captions}" == "whisper" ]]; then
    printf 'RIFFstubytaudio' > "$out/audio.wav"
    printf '{"schema":"mnemon-media/1","method":"whisper:tiny","audio_path":"%s","title":"Stub Video","duration":"0:10:00","upload_date":"20260101","video_id":"abc123xyz00","language":"en","original_language":"pt","track":null,"channel":"Stub Channel"}\n' "$out/audio.wav" > "$out/media.json"
  else
    printf '{"schema":"mnemon-media/1","method":"captions:en:manual","audio_path":null,"title":"Stub Video","duration":"0:10:00","upload_date":"20260101","video_id":"abc123xyz00","language":"en","original_language":"pt","track":{"lang":"en","kind":"manual","orig":false},"channel":"Stub Channel"}\n' > "$out/media.json"
  fi
else
  if [[ -n "$file" ]]; then audio="$file"; else audio="$out/audio.wav"; printf 'RIFFstubaudio' > "$audio"; fi
  printf '{"schema":"mnemon-media/1","method":"whisper:tiny","audio_path":"%s","title":"Stub Audio","duration":"0:01:00","upload_date":null,"video_id":null,"language":null,"original_language":null,"track":null,"channel":null}\n' "$audio" > "$out/media.json"
fi
STUB
cat > "$STUBS/kb-depth-fold.py" <<'STUB'
#!/usr/bin/env bash
# depth-fold stub (capture layer §4.2, §12 A8): the gateway calls it after the landing fetch with
# --landing-file <staged raw> --budget-s <remaining> --context <json>; the answer lists the folded
# targets only (no composite_body — the gateway assembles converted landing + targets once).
# STUB_DEPTH=off (default) answers not applied; fold answers two targets; timeout answers a timeout;
# noyaml is the real helper's answer on a python without PyYAML (the message on stderr, exit 3, no JSON).
[[ -n "${STUB_DIR:-}" ]] && printf '%s\n' "$*" >> "$STUB_DIR/depth-calls"
case "${STUB_DEPTH:-off}" in
  noyaml)
    echo "kb-depth-fold: PyYAML is required for the depth manifest (pip install pyyaml); no fold applied" >&2
    exit 3 ;;
  fold)
    printf '{"applied": true, "reason": "folded", "targets": [{"url": "https://example.com/deeper", "role": "docs", "header": "# Folded: https://example.com/deeper", "text": "folded body\\n", "status": 200, "sha256": "0000000000000000000000000000000000000000000000000000000000000001"}], "manifest_yaml": "posted_url: \\"https://example.com/thin\\"\\nfinal_url: \\"https://example.com/thin\\"\\ncanonical_url: \\"https://example.com/thin-declared\\"\\nfolded_sources:\\n  - url: https://example.com/deeper\\n    confidence: high\\nfetch_status: ok\\nsecret_key: never-merged\\n", "folded": ["https://example.com/deeper"], "errors": []}\n' ;;
  timeout)
    printf '{"applied": false, "reason": "timeout: budget exhausted before any target was fetched", "targets": [], "errors": ["timeout"]}\n' ;;
  *)
    printf '{"applied": false, "reason": "depth off (stub)"}\n' ;;
esac
STUB
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"
export RENDER_URL_CMD="$STUBS/render-url.sh" MEDIA_EXTRACT_CMD="$STUBS/media-extract.py" MNEMON_DEPTH_FOLD_CMD="$STUBS/kb-depth-fold.py"
export MNEMON_CAPTURE_FETCH_CMD="$STUBS/capture-fetch.py" MNEMON_CAPTURE_SITE_CMD="$STUBS/capture-site.py"
export STUB_DIR STUB_VAULT="$VAULT" STUB_FIXTURE="$FIX/extract-good.json" STUB_FIXTURE_PART="$FIX/extract-part-good.json"

# probe record for the stub's version (D13)
write_probe_record() {
  printf '{"9.9.9-stub (Claude Code)": {"pass": true, "date": "2026-10-15", "probes": {"no-tools": true}}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"
}
write_probe_record

# --- helpers ---
stub_reset() { rm -rf "$STUB_DIR"; mkdir -p "$STUB_DIR"; }
stub_calls() { cat "$STUB_DIR/count" 2>/dev/null || echo 0; }
fetch_calls() { cat "$STUB_DIR/fetch-count" 2>/dev/null || echo 0; }
curl_calls() { fetch_calls; }   # the phase-1 name: real acquisitions through the fetch seam
canon_calls() { cat "$STUB_DIR/canon-count" 2>/dev/null || echo 0; }
site_calls() { local n; n="$(grep -c "^${1:-}" "$STUB_DIR/site-calls" 2>/dev/null)" || true; echo "${n:-0}"; }
depth_calls() { cat "$STUB_DIR/depth-calls" 2>/dev/null || true; }
# a stub `file` on PATH answering one mime type for everything (the §3.7 sniff); stub_file_off removes it
stub_file_mime() { printf '#!/usr/bin/env bash\necho "%s"\n' "$1" > "$STUBS/file"; chmod +x "$STUBS/file"; }
stub_file_off() { rm -f "$STUBS/file"; }

# run_gw <args…>: runs the gateway with the test instance; sets out (stdout), err (stderr), rc
out=""; err=""; rc=0
# (an inner bash keeps "Killed: 9" job notices of crash-injected runs in $err, off the console)
run_gw() {
  local o="$TEST_TMPDIR/run.out" e="$TEST_TMPDIR/run.err"
  rc=0
  bash -c '"$@"; exit $?' _ "$GW" source-add --config "$CONFIG" "$@" </dev/null >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
# run_gw_stdin <file> <args…>
run_gw_stdin() {
  local f="$1" o="$TEST_TMPDIR/run.out" e="$TEST_TMPDIR/run.err"; shift
  rc=0
  bash -c '"$@"; exit $?' _ "$GW" source-add --config "$CONFIG" "$@" <"$f" >"$o" 2>"$e" || rc=$?
  out="$(cat "$o")"; err="$(cat "$e")"
}
result() { printf '%s\n' "$out" | grep "^RESULT:$1=" | head -1 | cut -d= -f2-; }
VSTATE="$XDG_STATE_HOME/mnemon/vaults/$(printf '%s' "$VAULT" | shasum -a 256 | cut -c1-16)"
receipt() { printf '%s/captures/%s.json' "$VSTATE" "$1"; }
rget() { jq -r "$2" "$(receipt "$1")"; }
folders() { (shopt -s nullglob; for d in "$VAULT"/Sources/*/; do basename "$d"; done); }
nfolders() { folders | wc -l | tr -d ' '; }
staging_left() { (shopt -s nullglob; for d in "$VAULT"/Sources/.*.staging-* "$ARCHIVE"/.*.staging-* "$VSTATE"/raw/.*.staging-*; do echo "$d"; done) | wc -l | tr -d ' '; }
body_sha() { python3 - "$1" <<'PY'
import hashlib, sys
d = open(sys.argv[1], "rb").read()
i = d.find(b"\n---\n", 3)
print(hashlib.sha256(d[i + 5:]).hexdigest())
PY
}
fm() { awk -v k="$2" 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f{p=k": "; if(substr($0,1,length(p))==p){print substr($0,length(p)+1);exit}}' "$1"; }
# capfm <source.md> <key>: a `  key: value` line of the capture: block
capfm() { awk -v k="$2" 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f&&$0=="capture:"{c=1;next} c&&substr($0,1,1)!=" "{exit} c{p="  "k": "; if(substr($0,1,length(p))==p){print substr($0,length(p)+1);exit}}' "$1"; }
cleanup_capture_test() { cd /; rm -rf "$TEST_TMPDIR"; }
