#!/usr/bin/env bash
# Shared scaffolding for tests/test_capture.sh, test_fence.sh and test_lock.sh: a temp XDG tree,
# a vault, an instance config, the stubs of spec §9 (claude, curl, render-url.sh, defuddle,
# pdftotext, pdfinfo, media-extract.py) and a probe record for the stub's version.
# Source it after tests/test-helper.sh with MNEMON_ROOT set; it exports everything the gateway
# needs and defines helpers. bash 3.2.

FIX="$MNEMON_ROOT/tests/fixtures/capture"
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-cap-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT STUB_MODE STUB_FIXTURE_BAD STUB_CURL STUB_RENDER STUB_DEFUDDLE STUB_PDFTOTEXT
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
cat > "$STUBS/curl" <<'STUB'
#!/usr/bin/env bash
# STUB_CURL: html (default) | empty | pdf | fail · counts calls in $STUB_DIR/curl-count ·
# STUB_CURL_HOOK: a command run after the download (e.g. to remove the probe record mid-capture)
out=""; prev=""
for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
echo $(( $(cat "$STUB_DIR/curl-count" 2>/dev/null || echo 0) + 1 )) > "$STUB_DIR/curl-count"
case "${STUB_CURL:-html}" in
  html)  [[ -n "$out" ]] && printf '<html><head><title> Stub &amp; Page\n  Title </title></head><body><h1>Hello</h1><p>World <b>bold</b> from curl.</p></body></html>' > "$out" ;;
  empty) [[ -n "$out" ]] && printf '<html><head><title>Empty</title></head><body></body></html>' > "$out" ;;
  pdf)   [[ -n "$out" ]] && printf '%%PDF-1.7\n\000\000binary\n%%%%EOF\n' > "$out" ;;
  *)     exit 22 ;;
esac
[[ -n "${STUB_CURL_HOOK:-}" ]] && eval "$STUB_CURL_HOOK"
exit 0
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
cmd="$1"; shift
out=""; url=""; file=""; prev=""
for a in "$@"; do
  case "$prev" in --out-dir) out="$a" ;; --url) url="$a" ;; --file) file="$a" ;; esac
  prev="$a"
done
[[ -n "$out" ]] || { echo "stub media-extract: --out-dir required" >&2; exit 1; }
mkdir -p "$out"
printf 'transcript line one\ntranscript line two\n' > "$out/transcript.txt"
if [[ "$cmd" == "youtube" ]]; then
  printf '{"schema":"mnemon-media/1","method":"captions:en:manual","audio_path":null,"title":"Stub Video","duration":"0:10:00","upload_date":"20260101","video_id":"abc123xyz00"}\n' > "$out/media.json"
else
  if [[ -n "$file" ]]; then audio="$file"; else audio="$out/audio.wav"; printf 'RIFFstubaudio' > "$audio"; fi
  printf '{"schema":"mnemon-media/1","method":"whisper:tiny","audio_path":"%s","title":"Stub Audio","duration":"0:01:00","upload_date":null,"video_id":null}\n' "$audio" > "$out/media.json"
fi
STUB
cat > "$STUBS/kb-depth-fold.py" <<'STUB'
#!/usr/bin/env bash
# depth-fold stub: STUB_DEPTH=off (default) answers not applied; STUB_DEPTH=fold answers a composite
if [[ "${STUB_DEPTH:-off}" == "fold" ]]; then
  printf '{"applied": true, "reason": "folded", "composite_body": "# Landing\\n\\nthin landing text\\n\\n# Folded: https://example.com/deeper\\n\\nfolded body\\n", "manifest_yaml": "posted_url: \\"https://example.com/thin\\"\\nfinal_url: \\"https://example.com/thin\\"\\nfolded_sources:\\n  - url: https://example.com/deeper\\n    confidence: high\\nfetch_status: ok\\nsecret_key: never-merged\\n", "folded": ["https://example.com/deeper"], "errors": []}\n'
else
  printf '{"applied": false, "reason": "depth off (stub)"}\n'
fi
STUB
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"
export RENDER_URL_CMD="$STUBS/render-url.sh" MEDIA_EXTRACT_CMD="$STUBS/media-extract.py" MNEMON_DEPTH_FOLD_CMD="$STUBS/kb-depth-fold.py"
export STUB_DIR STUB_VAULT="$VAULT" STUB_FIXTURE="$FIX/extract-good.json"

# probe record for the stub's version (D13)
write_probe_record() {
  printf '{"9.9.9-stub (Claude Code)": {"pass": true, "date": "2026-10-15", "probes": {"no-tools": true}}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"
}
write_probe_record

# --- helpers ---
stub_reset() { rm -rf "$STUB_DIR"; mkdir -p "$STUB_DIR"; }
stub_calls() { cat "$STUB_DIR/count" 2>/dev/null || echo 0; }
curl_calls() { cat "$STUB_DIR/curl-count" 2>/dev/null || echo 0; }

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
cleanup_capture_test() { cd /; rm -rf "$TEST_TMPDIR"; }
