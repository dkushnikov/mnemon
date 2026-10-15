#!/usr/bin/env bash
# test_media.sh — bin/media-extract.py (spec docs/specs/2026-10-15-capture-layer.md §3.7, D11, D12,
# §7.11–12, §12 A9, A12): the track choice as a pure fixture-driven function, the flat mnemon-media/1
# record (validated against the frozen fixture's key set), the captions child, --timeout with
# process-group teardown (nested: the stub tool and its own child), SIGTERM from the parent, and the
# transcription-only run with honest nulls. No network and no real tool: yt-dlp, whisper, ffprobe
# are stubs on PATH, youtube_transcript_api a stub package on PYTHONPATH. bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MEDIA="$MNEMON_ROOT/bin/media-extract.py"
FIX="$MNEMON_ROOT/tests/fixtures/capture"

echo "=== media-extract.py Tests ==="

# --- toolchain proof (§12 A12): print both interpreters; under the legacy PATH assert 3.9.x / 3.2.x ---
echo "--- toolchain ---"
PY_VER="$(python3 --version 2>&1)"
echo "  python3: $PY_VER ($(command -v python3)) · bash: $BASH_VERSION"
if [[ "$(command -v python3)" == /usr/bin/python3 ]]; then
  assert_contains "$PY_VER" "Python 3.9." "legacy toolchain: python3 is 3.9.x"
  assert_contains "$BASH_VERSION" "3.2." "legacy toolchain: bash is 3.2.x"
else
  pass "toolchain: $PY_VER, bash $BASH_VERSION (the 3.9/3.2 proof runs under PATH=/usr/bin:/bin:\$PATH)"
fi

# --- isolation (§7.16): no inherited keys, proxies or test variables; a temp tree; stubs ---
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
unset MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE
unset STUB_YTA STUB_YTA_API STUB_YTDLP STUB_YTDLP_LANG STUB_WHISPER STUB_FFPROBE
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-media-XXXX)" && pwd -P)"
STUBS="$TEST_TMPDIR/stubs"
STUB_DIR="$TEST_TMPDIR/stub-calls"
PYMODS="$TEST_TMPDIR/pymods"
mkdir -p "$STUBS" "$STUB_DIR" "$PYMODS/youtube_transcript_api"
export STUB_DIR

cat > "$STUBS/yt-dlp" <<'STUB'
#!/usr/bin/env bash
# yt-dlp stub. `--dump-single-json` answers the metadata (STUB_YTDLP_LANG=null drops `language`);
# a download (-o <template>) writes audio.wav — STUB_YTDLP: ok (default) | slow (1 s) | hang (30 s
# with a `sleep` child of its own; both pids under $STUB_DIR for the teardown assertions).
# The MNEMON_VAULT_LOCK_* variables of its environment are recorded (must be none).
env | grep '^MNEMON_VAULT_LOCK_' > "$STUB_DIR/ytdlp-env" || true
if [[ "${1:-}" == "--dump-single-json" ]]; then
  lang='"pt"'; [[ "${STUB_YTDLP_LANG:-}" == null ]] && lang=null
  printf '{"id":"abc123xyz00","title":"Um vídeo de exemplo","channel":"Example Channel","duration":754,"upload_date":"20260901","language":%s,"description":"a description"}\n' "$lang"
  exit 0
fi
out=""; prev=""
for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
echo $$ > "$STUB_DIR/ytdlp.pid"
case "${STUB_YTDLP:-ok}" in
  hang) sleep 30 & echo $! > "$STUB_DIR/ytdlp-child.pid"; wait; exit 0 ;;
  slow) sleep 1 ;;
esac
printf 'RIFFstubaudio' > "$(printf '%s' "$out" | sed 's/%(ext)s$/wav/')"
exit 0
STUB
cat > "$STUBS/whisper" <<'STUB'
#!/usr/bin/env bash
# whisper <audio> --model M --output_format txt --output_dir D · STUB_WHISPER: ok (default) | hang
audio="$1"; outdir=""; prev=""
for a in "$@"; do [[ "$prev" == "--output_dir" ]] && outdir="$a"; prev="$a"; done
echo $$ > "$STUB_DIR/whisper.pid"
if [[ "${STUB_WHISPER:-ok}" == hang ]]; then sleep 30 & echo $! > "$STUB_DIR/whisper-child.pid"; wait; fi
base="$(basename "$audio")"; base="${base%.*}"
printf 'whisper transcript line one\nwhisper transcript line two\n' > "$outdir/$base.txt"
STUB
cat > "$STUBS/ffprobe" <<'STUB'
#!/usr/bin/env bash
# ffprobe … <file> → the duration in seconds · STUB_FFPROBE: ok (default, 61.5) | fail | hang
echo $$ > "$STUB_DIR/ffprobe.pid"
case "${STUB_FFPROBE:-ok}" in
  fail) exit 1 ;;
  hang) sleep 30 & echo $! > "$STUB_DIR/ffprobe-child.pid"; wait ;;
esac
echo 61.5
STUB
chmod +x "$STUBS"/*
cat > "$PYMODS/youtube_transcript_api/__init__.py" <<'PY'
"""Stub of youtube_transcript_api for tests/test_media.sh. STUB_YTA selects the listing:
pt (default: an automatic en track and a manual pt track) · en-orig (one automatic en-orig track) ·
none (the listing raises) · hang (sleeps 30 s inside list(), its pid in $STUB_DIR/captions.pid) ·
missing (the package is not importable). STUB_YTA_API=v0 serves the 0.x class-method API whose
fetch() returns dicts; otherwise the 1.x instance API whose fetch() returns snippet objects."""
import os
import time

if os.environ.get("STUB_YTA") == "missing":
    raise ImportError("stub: youtube_transcript_api not installed")


class _Snippet:
    def __init__(self, text):
        self.text = text


class _Transcript:
    def __init__(self, code, generated, lines, dicts=False):
        self.language_code = code
        self.is_generated = generated
        self._lines = lines
        self._dicts = dicts

    def fetch(self):
        if self._dicts:
            return [{"text": t, "start": 0.0, "duration": 1.0} for t in self._lines]
        return [_Snippet(t) for t in self._lines]


def _listing(video_id, dicts=False):
    mode = os.environ.get("STUB_YTA", "pt")
    with open(os.path.join(os.environ.get("STUB_DIR", "."), "captions.pid"), "w") as fh:
        fh.write(str(os.getpid()))
    if mode == "hang":
        time.sleep(30)
    if mode == "none":
        raise RuntimeError("stub: no transcripts for " + video_id)
    if mode == "en-orig":
        return [_Transcript("en-orig", True, ["hello", "original world"], dicts)]
    return [_Transcript("en", True, ["hello", "world"], dicts),
            _Transcript("pt", False, ["olá", "mundo"], dicts)]


if os.environ.get("STUB_YTA_API") == "v0":
    class YouTubeTranscriptApi:
        @classmethod
        def list_transcripts(cls, video_id):
            return _listing(video_id, dicts=True)
else:
    class YouTubeTranscriptApi:
        def list(self, video_id):
            return _listing(video_id)
PY
export PATH="$STUBS:$PATH" PYTHONPATH="$PYMODS"
URL="https://www.youtube.com/watch?v=abc123xyz00"

# run_media <args…>: $out (stdout), $err (stderr), $rc
out=""; err=""; rc=0
run_media() { rc=0; out="$("$MEDIA" "$@" 2>"$TEST_TMPDIR/err")" || rc=$?; err="$(cat "$TEST_TMPDIR/err")"; }
# jget <file> <python expr over d>
jget() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=eval(sys.argv[2]); print(json.dumps(v, ensure_ascii=False) if isinstance(v,(dict,list,bool)) or v is None else v)' "$1" "$2"; }
# keyshape <output.json> <fixture.json>: same keys, same types, same nullability (reports/README.md)
keyshape() { python3 - "$1" "$2" <<'PY'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8")); b = json.load(open(sys.argv[2], encoding="utf-8"))
if set(a) != set(b):
    print("keys differ: only in output %s, only in fixture %s" % (sorted(set(a) - set(b)), sorted(set(b) - set(a)))); sys.exit(1)
bad = [k for k in a if (a[k] is None) != (b[k] is None) or (a[k] is not None and type(a[k]) is not type(b[k]))]
if bad:
    print("type/nullability differ: %s" % bad); sys.exit(1)
print("same keys, types and nullability")
PY
}
# wait_gone <pid> [seconds]: 0 once the process is gone, 1 when it is still alive after the limit
wait_gone() {
  local pid="$1" limit="${2:-3}" i=0
  while kill -0 "$pid" 2>/dev/null; do
    if [[ $i -ge $((limit * 20)) ]]; then return 1; fi
    sleep 0.05; i=$((i + 1))
  done
  return 0
}
stub_reset() { rm -rf "$STUB_DIR"; mkdir -p "$STUB_DIR"; }

# --- 1. the module: importable, CLI guarded, the pure functions on the fixture ---
echo "--- choose_track (D12, fixtures) ---"
assert_eq "$(grep -c '^if __name__ == "__main__":' "$MEDIA")" "1" "the CLI is guarded by __main__ (the module is importable)"
cases_out="$(python3 - "$MEDIA" "$FIX/tracks.json" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("media_extract", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fx = json.load(open(sys.argv[2], encoding="utf-8"))
assert fx["schema"] == "mnemon-track-fixtures/1"
for c in fx["cases"]:
    got = m.choose_track(c["tracks"], c["original_language"])
    print(("ok " if got == c["expect"] else "FAIL ") + c["name"] + ("" if got == c["expect"] else " → got %r" % (got,)))
# the chosen element is the listing object itself, so a caller can map it back
tracks = [{"lang": "en", "kind": "auto", "orig": False}, {"lang": "pt", "kind": "manual", "orig": False}]
print(("ok " if m.choose_track(tracks, "pt") is tracks[1] else "FAIL ") + "choose_track returns the listing element itself")
print(("ok " if m.choose_track(None, "pt") is None else "FAIL ") + "choose_track(None, …) is None")
PY
)"
printf '%s\n' "$cases_out" | while IFS= read -r line; do
  case "$line" in
    "ok "*)   echo "  ✓ ${line#ok }" ;;
    "FAIL "*) echo "  ✗ ${line#FAIL }" ;;
  esac
done
n_ok=$(printf '%s\n' "$cases_out" | grep -c '^ok ' || true)
n_bad=$(printf '%s\n' "$cases_out" | grep -c '^FAIL ' || true)
n_cases=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["cases"]))' "$FIX/tracks.json")
n_total=$((n_cases + 2))
assert_eq "$n_bad" "0" "every choose_track fixture case passes: $n_ok of $n_total checks"
[[ "$n_cases" -ge 10 ]] && pass "tracks.json holds $n_cases cases (the four of §7.12 and the remaining steps)" || fail "tracks.json holds enough cases" "$n_cases"

echo "--- list_tracks (the API adapter) ---"
adapter="$(python3 - "$MEDIA" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("media_extract", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class T:
    def __init__(self, code, gen): self.language_code = code; self.is_generated = gen
objs = [T("pt", False), T("en-orig", True), T("EN", True)]
print(m.list_tracks(objs))
print(m.list_tracks([{"language_code": "ru-orig", "is_generated": True}, {"lang": "de", "kind": "manual"}]))
print(m.list_tracks([]))
PY
)"
assert_contains "$adapter" "[{'lang': 'pt', 'kind': 'manual', 'orig': False}, {'lang': 'en', 'kind': 'auto', 'orig': True}, {'lang': 'EN', 'kind': 'auto', 'orig': False}]" "list_tracks adapts objects: -orig stripped and flagged, is_generated → auto, order kept"
assert_contains "$adapter" "[{'lang': 'ru', 'kind': 'auto', 'orig': True}, {'lang': 'de', 'kind': 'manual', 'orig': False}]" "list_tracks adapts dicts (language_code/is_generated or lang/kind)"
assert_contains "$adapter" "[]" "list_tracks of an empty listing is []"

# --- 2. the captions path end to end (stub library, stub yt-dlp): the flat media.json ---
echo "--- youtube: captions in the video's language (D12) ---"
stub_reset
D1="$TEST_TMPDIR/out-captions"
run_media youtube "$URL" --out-dir "$D1" --timeout 20
assert_eq "$rc" "0" "youtube captions run exits 0"
assert_eq "$out" "" "...stdout is empty in --out-dir mode"
assert_eq "$(cat "$D1/transcript.txt")" "$(printf 'olá\nmundo')" "transcript.txt holds the chosen (pt manual) track's text"
assert_file_exists "$D1/media.json" "media.json written"
assert_eq "$(jget "$D1/media.json" 'd["schema"]')" "mnemon-media/1" "media.json schema"
assert_eq "$(jget "$D1/media.json" 'd["method"]')" "captions:pt:manual" "method captions:pt:manual"
assert_eq "$(jget "$D1/media.json" 'd["language"]')" "pt" "top-level language = the transcript's language"
assert_eq "$(jget "$D1/media.json" 'd["original_language"]')" "pt" "top-level original_language from yt-dlp's language"
assert_eq "$(jget "$D1/media.json" 'd["track"]')" '{"lang": "pt", "kind": "manual", "orig": false}' "top-level track = the chosen track"
assert_eq "$(jget "$D1/media.json" 'd["channel"]')" "Example Channel" "top-level channel"
assert_eq "$(jget "$D1/media.json" 'd["title"]')" "Um vídeo de exemplo" "title unchanged"
assert_eq "$(jget "$D1/media.json" 'd["duration"]')" "0:12:34" "duration unchanged (H:MM:SS)"
assert_eq "$(jget "$D1/media.json" 'd["upload_date"]')" "20260901" "upload_date unchanged"
assert_eq "$(jget "$D1/media.json" 'd["video_id"]')" "abc123xyz00" "video_id unchanged"
assert_eq "$(jget "$D1/media.json" 'd["audio_path"]')" "null" "audio_path null on the captions path"
assert_eq "$(jget "$D1/media.json" '[k for k in d if isinstance(d[k], dict) and k != "track"]')" "[]" "no nested metadata object (D12: flat keys)"
shape="$(keyshape "$D1/media.json" "$FIX/reports/media.json" 2>&1)" && pass "media.json matches the frozen fixture's key set, types and nullability" || fail "media.json matches the frozen fixture reports/media.json" "$shape"
assert_eq "$(cat "$STUB_DIR/ytdlp-env" 2>/dev/null)" "" "yt-dlp sees no MNEMON_VAULT_LOCK_* variable (the descriptor is not inherited)"
[[ -e "$STUB_DIR/ytdlp.pid" ]] && fail "captions path downloads nothing" "yt-dlp download ran" || pass "captions path downloads nothing (yt-dlp only answered the metadata)"

echo "--- youtube: the fallback order and the two API generations ---"
stub_reset
D2="$TEST_TMPDIR/out-enorig"
run_media youtube "$URL" --out-dir "$D2" --timeout 20 --no-whisper
assert_eq "$rc" "0" "no track in the video's language: still captions (exit 0)"
STUB_YTA=en-orig run_media youtube "$URL" --out-dir "$D2" --timeout 20 --no-whisper
assert_eq "$rc" "0" "en-orig auto only, original pt → that track (exit 0)"
assert_eq "$(jget "$D2/media.json" 'd["method"]')" "captions:en:auto" "method names the language without the -orig suffix"
assert_eq "$(jget "$D2/media.json" 'd["track"]')" '{"lang": "en", "kind": "auto", "orig": true}' "track.orig is true for the -orig track"
assert_eq "$(jget "$D2/media.json" 'd["language"]')" "en" "language en (the transcript) while original_language stays pt"
assert_eq "$(jget "$D2/media.json" 'd["original_language"]')" "pt" "...original_language pt"
assert_eq "$(cat "$D2/transcript.txt")" "$(printf 'hello\noriginal world')" "...the -orig track's text"
D3="$TEST_TMPDIR/out-v0"
STUB_YTA_API=v0 run_media youtube "$URL" --out-dir "$D3" --timeout 20 --no-whisper
assert_eq "$rc" "0" "the 0.x class-method API (dict entries) works"
assert_eq "$(cat "$D3/transcript.txt")" "$(printf 'olá\nmundo')" "...same transcript from dict entries"
assert_eq "$(jget "$D3/media.json" 'd["method"]')" "captions:pt:manual" "...same choice"
D3b="$TEST_TMPDIR/out-nolang"
STUB_YTDLP_LANG=null run_media youtube "$URL" --out-dir "$D3b" --timeout 20 --no-whisper
assert_eq "$rc" "0" "a video without a declared language still captures"
assert_eq "$(jget "$D3b/media.json" 'd["original_language"]')" "null" "...original_language null (honest)"
assert_eq "$(jget "$D3b/media.json" 'd["method"]')" "captions:en:auto" "...the en/ru fallback picks the automatic en track over manual pt"

echo "--- youtube: the hidden _captions child ---"
run_media _captions abc123xyz00 --out "$TEST_TMPDIR/caps.txt" --original-language pt
assert_eq "$rc" "0" "_captions exits 0"
assert_eq "$out" '{"language": "pt", "track": {"lang": "pt", "kind": "manual", "orig": false}, "method": "captions:pt:manual"}' "_captions prints one JSON line (language, track, method)"
assert_eq "$(cat "$TEST_TMPDIR/caps.txt")" "$(printf 'olá\nmundo')" "_captions writes the transcript to --out"
STUB_YTA=none run_media _captions abc123xyz00 --out "$TEST_TMPDIR/caps2.txt"
assert_eq "$rc" "1" "_captions exits 1 when the listing fails"
assert_contains "$err" "cannot list captions for abc123xyz00" "...naming the video"
STUB_YTA=missing run_media _captions abc123xyz00 --out "$TEST_TMPDIR/caps3.txt"
assert_eq "$rc" "3" "_captions exits 3 (environment) without the library"
assert_contains "$err" "youtube_transcript_api is not importable" "...and says so"

# --- 3. whisper fallback: the download stays under --out-dir, honest nulls for what whisper cannot know ---
echo "--- youtube: whisper fallback ---"
stub_reset
D4="$TEST_TMPDIR/out-whisper"
STUB_YTA=none run_media youtube "$URL" --out-dir "$D4" --timeout 20 --whisper-model tiny
assert_eq "$rc" "0" "no captions → whisper fallback exits 0"
assert_contains "$err" "NOTE: captions unavailable" "...with a note naming the reason"
assert_eq "$(jget "$D4/media.json" 'd["method"]')" "whisper:tiny" "method whisper:<model>"
assert_eq "$(jget "$D4/media.json" 'd["audio_path"]')" "$D4/audio.wav" "audio_path = the downloaded audio under --out-dir"
assert_file_exists "$D4/audio.wav" "...which is kept (nothing under --out-dir deleted)"
assert_eq "$(jget "$D4/media.json" 'd["language"]')" "null" "language null (whisper's detection is not parsed)"
assert_eq "$(jget "$D4/media.json" 'd["track"]')" "null" "track null"
assert_eq "$(jget "$D4/media.json" 'd["original_language"]')" "pt" "original_language still from yt-dlp"
assert_eq "$(jget "$D4/media.json" 'd["channel"]')" "Example Channel" "channel still from yt-dlp"
assert_eq "$(jget "$D4/media.json" 'sorted(d)')" "$(jget "$FIX/reports/media.json" 'sorted(d)')" "the whisper record has exactly the fixture's keys"
assert_eq "$(cat "$D4/transcript.txt")" "$(printf 'whisper transcript line one\nwhisper transcript line two\n')" "transcript from whisper"
STUB_YTA=none run_media youtube "$URL" --out-dir "$TEST_TMPDIR/out-nowhisper" --no-whisper
assert_eq "$rc" "1" "no captions with --no-whisper → exit 1"
assert_contains "$err" "No captions and --no-whisper set" "...with the phase-1 message"
STUB_YTA=missing run_media youtube "$URL" --out-dir "$TEST_TMPDIR/out-missinglib" --timeout 20 --whisper-model tiny
assert_eq "$rc" "0" "a missing transcript library falls back to whisper"
assert_contains "$err" "not importable" "...naming the library in the note"

# --- 4. --timeout: one budget, process groups torn down (D11, §12 A9) ---
echo "--- --timeout: download stage (stub yt-dlp hangs with a child of its own) ---"
stub_reset
D5="$TEST_TMPDIR/out-timeout"
mkdir -p "$D5"; printf 'keep me\n' > "$D5/keep.txt"
t0=$(date +%s)
STUB_YTA=none STUB_YTDLP=hang run_media youtube "$URL" --out-dir "$D5" --timeout 2 --whisper-model tiny
t1=$(date +%s)
assert_eq "$rc" "124" "a hung download → exit 124"
echo "  (elapsed $((t1 - t0)) s for --timeout 2)"
[[ $((t1 - t0)) -le 4 ]] && pass "...within budget + grace (≤ 4 s, measured $((t1 - t0)) s)" || fail "...within budget + grace" "took $((t1 - t0)) s"
assert_contains "$err" "ERROR: timeout after" "...an error line on stderr"
assert_contains "$err" "stage download" "...naming the stage"
assert_file_exists "$D5/error.json" "error.json written under --out-dir"
assert_eq "$(jget "$D5/error.json" 'd["schema"]')" "mnemon-media-error/1" "error.json schema"
assert_eq "$(jget "$D5/error.json" 'd["error_class"]')" "timeout" "error.json error_class timeout"
assert_eq "$(jget "$D5/error.json" 'd["stage"]')" "download" "error.json stage download"
assert_eq "$(jget "$D5/error.json" '1.5 <= d["elapsed_s"] <= 4.5')" "true" "error.json elapsed_s ≈ the budget"
shape="$(keyshape "$D5/error.json" "$FIX/reports/media-error.json" 2>&1)" && pass "error.json matches the frozen fixture's key set" || fail "error.json matches reports/media-error.json" "$shape"
assert_file_exists "$D5/keep.txt" "nothing under --out-dir deleted"
assert_file_exists "$STUB_DIR/ytdlp.pid" "the stub ran"
ypid="$(cat "$STUB_DIR/ytdlp.pid")"; cpid="$(cat "$STUB_DIR/ytdlp-child.pid" 2>/dev/null || echo 0)"
wait_gone "$ypid" && pass "the stub yt-dlp is gone (kill -0 fails)" || fail "the stub yt-dlp is gone" "pid $ypid still alive"
[[ "$cpid" -gt 0 ]] && { wait_gone "$cpid" && pass "...and its own child (nested teardown, A9)" || fail "the stub's child is gone" "pid $cpid still alive"; }
kill -9 "$ypid" "$cpid" 2>/dev/null || true

echo "--- --timeout: captions stage (the library call hangs inside the child) ---"
stub_reset
D6="$TEST_TMPDIR/out-timeout-captions"
STUB_YTA=hang run_media youtube "$URL" --out-dir "$D6" --timeout 2
assert_eq "$rc" "124" "a hung caption fetch → exit 124"
assert_eq "$(jget "$D6/error.json" 'd["stage"]')" "captions" "error.json stage captions"
assert_file_exists "$STUB_DIR/captions.pid" "the captions child ran"
cpid="$(cat "$STUB_DIR/captions.pid")"
wait_gone "$cpid" && pass "the captions child is gone (a hung library call is killable)" || fail "the captions child is gone" "pid $cpid still alive"
kill -9 "$cpid" 2>/dev/null || true
[[ -e "$D6/transcript.txt" ]] && fail "no transcript after a timeout" "exists" || pass "no transcript after a timeout"

echo "--- --timeout: transcribe and probe stages (audio) ---"
stub_reset
AUDIO="$TEST_TMPDIR/talk.mp3"; printf 'ID3stubaudio' > "$AUDIO"
D7="$TEST_TMPDIR/out-timeout-whisper"
STUB_WHISPER=hang run_media audio --file "$AUDIO" --out-dir "$D7" --timeout 2 --whisper-model tiny
assert_eq "$rc" "124" "a hung whisper → exit 124"
assert_eq "$(jget "$D7/error.json" 'd["stage"]')" "transcribe" "error.json stage transcribe"
wpid="$(cat "$STUB_DIR/whisper.pid")"; wc_pid="$(cat "$STUB_DIR/whisper-child.pid" 2>/dev/null || echo 0)"
wait_gone "$wpid" && pass "the stub whisper is gone" || fail "the stub whisper is gone" "pid $wpid alive"
[[ "$wc_pid" -gt 0 ]] && { wait_gone "$wc_pid" && pass "...and its child" || fail "whisper's child is gone" "pid $wc_pid alive"; }
kill -9 "$wpid" "$wc_pid" 2>/dev/null || true
stub_reset
D8="$TEST_TMPDIR/out-timeout-probe"
STUB_FFPROBE=hang run_media audio --file "$AUDIO" --out-dir "$D8" --timeout 2 --whisper-model tiny
assert_eq "$rc" "124" "a hung ffprobe → exit 124"
assert_eq "$(jget "$D8/error.json" 'd["stage"]')" "probe" "error.json stage probe"
fpid="$(cat "$STUB_DIR/ffprobe.pid")"
wait_gone "$fpid" && pass "the stub ffprobe is gone" || fail "the stub ffprobe is gone" "pid $fpid alive"
kill -9 "$fpid" "$(cat "$STUB_DIR/ffprobe-child.pid" 2>/dev/null || echo 0)" 2>/dev/null || true

echo "--- no --timeout: no deadline (a slow tool is not a timeout) ---"
stub_reset
D9="$TEST_TMPDIR/out-slow"
STUB_YTA=none STUB_YTDLP=slow run_media youtube "$URL" --out-dir "$D9" --whisper-model tiny
assert_eq "$rc" "0" "a 1 s download without --timeout completes"
[[ -e "$D9/error.json" ]] && fail "no error.json without a timeout" "exists" || pass "no error.json without a timeout"
run_media youtube "$URL" --out-dir "$TEST_TMPDIR/out-badtimeout" --timeout 0
assert_eq "$rc" "2" "--timeout 0 is a usage error"
run_media youtube "$URL" --out-dir "$TEST_TMPDIR/out-badtimeout" --timeout abc
assert_eq "$rc" "2" "--timeout abc is a usage error"

echo "--- SIGTERM from the parent tears the children down (A9) ---"
stub_reset
D10="$TEST_TMPDIR/out-sigterm"
STUB_YTA=none STUB_YTDLP=hang "$MEDIA" youtube "$URL" --out-dir "$D10" --whisper-model tiny 2>"$TEST_TMPDIR/sigterm.err" &
MPID=$!
wait_file "$STUB_DIR/ytdlp-child.pid" 10
kill -TERM "$MPID"
src=0; wait "$MPID" 2>/dev/null || src=$?
assert_eq "$src" "143" "the helper exits 143 after SIGTERM"
assert_contains "$(cat "$TEST_TMPDIR/sigterm.err")" "interrupted by signal 15" "...and says so"
ypid="$(cat "$STUB_DIR/ytdlp.pid")"; cpid="$(cat "$STUB_DIR/ytdlp-child.pid")"
wait_gone "$ypid" && pass "the stub yt-dlp is gone after the parent's SIGTERM" || fail "the stub yt-dlp is gone after SIGTERM" "pid $ypid alive"
wait_gone "$cpid" && pass "...and its child" || fail "the stub's child is gone after SIGTERM" "pid $cpid alive"
kill -9 "$ypid" "$cpid" 2>/dev/null || true
[[ -e "$D10/error.json" ]] && fail "no error.json on a signal (that is the timeout's file)" "exists" || pass "no error.json on a signal"

# --- 5. the transcription-only run (audio --file): honest nulls, the receipt is authoritative on replay ---
echo "--- audio --file: transcription only ---"
stub_reset
D11="$TEST_TMPDIR/out-audio"
run_media audio --file "$AUDIO" --out-dir "$D11" --timeout 20 --whisper-model tiny --title "A talk"
assert_eq "$rc" "0" "audio --file exits 0"
assert_eq "$(jget "$D11/media.json" 'd["method"]')" "whisper:tiny" "method whisper:tiny"
assert_eq "$(jget "$D11/media.json" 'd["audio_path"]')" "$AUDIO" "audio_path = the given (published) raw"
assert_eq "$(jget "$D11/media.json" 'd["title"]')" "A talk" "title from --title"
assert_eq "$(jget "$D11/media.json" 'd["duration"]')" "0:01:01" "duration from ffprobe"
for k in upload_date video_id language original_language track channel; do
  assert_eq "$(jget "$D11/media.json" "d[\"$k\"]")" "null" "$k null — a transcription-only run reports only what it can know"
done
assert_eq "$(jget "$D11/media.json" 'sorted(d)')" "$(jget "$FIX/reports/media.json" 'sorted(d)')" "the audio record has exactly the fixture's keys"
run_media audio --file "$AUDIO" --out-dir "$TEST_TMPDIR/out-audio2" --whisper-model tiny
assert_eq "$(jget "$TEST_TMPDIR/out-audio2/media.json" 'd["title"]')" "talk" "title defaults to the file's stem"
STUB_FFPROBE=fail run_media audio --file "$AUDIO" --out-dir "$TEST_TMPDIR/out-audio3"
assert_eq "$rc" "1" "a file ffprobe rejects → exit 1"
assert_contains "$err" "Not a valid audio file" "...with the message"
run_media audio --out-dir "$TEST_TMPDIR/out-audio4"
assert_eq "$rc" "2" "audio without --url/--file is a usage error (exit 2)"
PATH="$TEST_TMPDIR/empty-bin:/usr/bin:/bin" run_media audio --file "$AUDIO" --out-dir "$TEST_TMPDIR/out-audio5"
assert_eq "$rc" "3" "a missing whisper → exit 3 (environment)"

# --- 6. the legacy stdout contract ---
echo "--- legacy stdout contract ---"
stub_reset
run_media youtube "$URL"
assert_eq "$rc" "0" "youtube without --out-dir exits 0"
assert_contains "$out" "--- YouTube Source Metadata ---" "...the metadata header"
assert_contains "$out" "Language: pt" "...the transcript language"
assert_contains "$out" "Original language: pt" "...the video's language"
assert_contains "$out" "Transcript method: captions" "...the method"
assert_contains "$out" "$(printf -- '--- Transcript ---\nolá\nmundo')" "...the transcript"
run_media audio --file "$AUDIO" --whisper-model tiny
assert_eq "$rc" "0" "audio without --out-dir exits 0"
assert_contains "$out" "--- Audio Source Metadata ---" "...the audio header"
assert_contains "$out" "whisper transcript line one" "...the transcript"

cd /
rm -rf "$TEST_TMPDIR"
summary
