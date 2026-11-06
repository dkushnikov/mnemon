#!/usr/bin/env bash
# test_browser_lane.sh — bin/browser-lane.py, the browser lane's media form (capture layer A13; GH #12, #26):
# one yt-dlp metadata call and ONE caption download per video with --cookies-from-browser, in order, a bundle per
# video (mnemon-media/1 + the track; validated against the frozen fixture's key set), then `mnemon add … --media-bundle`
# through the dispatcher; walls are `blocked` and the lane moves on; --no-add, --dry-run, --json; nothing that looks
# like a cookie ever reaches a bundle or a line. No network and no real tool: yt-dlp and mnemon are stubs on PATH.
# bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
LANE="$MNEMON_ROOT/bin/browser-lane.py"
FIX="$MNEMON_ROOT/tests/fixtures/capture"

echo "=== browser-lane.py Tests ==="
print_toolchain; assert_toolchain_legacy

unset STUB_YTDLP STUB_ADD STUB_YTDLP_MANUAL MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-lane-XXXX)" && pwd -P)"
STUBS="$TEST_TMPDIR/stubs"
STUB_DIR="$TEST_TMPDIR/stub-calls"
OUT="$TEST_TMPDIR/out"
mkdir -p "$STUBS" "$STUB_DIR"
export STUB_DIR MEDIA_FIX="$FIX/media"

cat > "$STUBS/yt-dlp" <<'STUB'
#!/usr/bin/env bash
# yt-dlp stub for the browser lane. Every argv is appended to $STUB_DIR/ytdlp-calls (one line per call).
# `--version` → a version; `--dump-single-json` → the recorded info fixture (STUB_YTDLP_MANUAL=1 adds a manual
# `en` track; STUB_YTDLP: ok (default) · wall (the bot wall at the metadata call) · nosubs (no caption listing)
# · fail); a caption download (`--write-subs|--write-auto-subs`) writes <template>.<lang>.vtt from the fixture
# track (STUB_YTDLP=wall-subs: the wall here; two-files: a second file beside it). Any audio download flag
# (-x, -f, --audio-format) is a defect the lane must never produce: the stub refuses it.
printf '%s\n' "$*" >> "$STUB_DIR/ytdlp-calls"
for a in "$@"; do case "$a" in -x|-f|--audio-format|--extract-audio) echo "stub yt-dlp: audio download flag $a" >&2; exit 99 ;; esac; done
case " $* " in *" --version "*) echo "2026.10.26"; exit 0 ;; esac
# STUB_YTDLP=hang: the metadata call never answers (a child sleep of its own, both pids recorded for the teardown check)
if [[ "${STUB_YTDLP:-ok}" == hang && " $* " == *" --dump-single-json "* ]]; then echo $$ > "$STUB_DIR/ytdlp.pid"; sleep 30 & echo $! > "$STUB_DIR/ytdlp-child.pid"; wait; exit 0; fi
url="${@: -1}"
id="${url##*v=}"
if [[ " $* " == *" --dump-single-json "* ]]; then
  case "${STUB_YTDLP:-ok}" in
    wall) cat "$MEDIA_FIX/ytdlp-bot-wall.stderr.txt" >&2; exit 1 ;;
    fail) echo "ERROR: [youtube] $id: Video unavailable. This video is private" >&2; exit 1 ;;
  esac
  python3 - "$MEDIA_FIX/ytdlp-info-captions.json" "$id" "${STUB_YTDLP:-ok}" "${STUB_YTDLP_MANUAL:-}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
d["id"] = sys.argv[2]
d["webpage_url"] = "https://www.youtube.com/watch?v=" + sys.argv[2]
if sys.argv[3] == "nosubs":
    d["subtitles"], d["automatic_captions"] = {}, {}
if sys.argv[4]:
    d["subtitles"] = {"en": [{"ext": "vtt", "name": "English"}], "live_chat": [{"ext": "json", "name": "Live chat"}]}
print(json.dumps(d, ensure_ascii=False))
PY
  exit 0
fi
out=""; lang=""; prev=""
for a in "$@"; do
  case "$prev" in -o) out="$a" ;; --sub-langs) lang="$a" ;; esac
  prev="$a"
done
[[ -n "$out" && -n "$lang" ]] || { echo "stub yt-dlp: a caption download needs -o and --sub-langs" >&2; exit 2; }
case "${STUB_YTDLP:-ok}" in
  wall-subs) cat "$MEDIA_FIX/ytdlp-bot-wall.stderr.txt" >&2; exit 1 ;;
esac
target="$(printf '%s' "$out" | sed "s/%(ext)s$/$lang.vtt/")"
cp "$MEDIA_FIX/captions-sample.en-orig.vtt" "$target"
[[ "${STUB_YTDLP:-ok}" == "two-files" ]] && cp "$MEDIA_FIX/captions-sample.en-orig.vtt" "$(printf '%s' "$out" | sed "s/%(ext)s$/extra.vtt/")"
exit 0
STUB
cat > "$STUBS/mnemon" <<'STUB'
#!/usr/bin/env bash
# mnemon stub (the dispatcher the lane runs for `add`): records argv, answers per STUB_ADD — extracted (default,
# exit 0) · dup (skipped-duplicate, exit 0) · captured (exit 4, the extract pending) · locked (exit 75) · fail (exit 1)
printf '%s\n' "$*" >> "$STUB_DIR/mnemon-calls"
bundle=""; prev=""
for a in "$@"; do case "$prev" in --media-bundle) bundle="$a" ;; esac; prev="$a"; done
[[ -f "$bundle/media.json" ]] || { echo "stub mnemon: no media.json in $bundle" >&2; exit 2; }
echo "RESULT:capture_id=c-stub"
case "${STUB_ADD:-extracted}" in
  dup)      echo "RESULT:revision=2026-11-01_abcd1234"; echo "RESULT:status=skipped-duplicate"; exit 0 ;;
  captured) echo "RESULT:revision=2026-11-05_abcd1234"; echo "RESULT:status=captured"; echo "RESULT:error_class=fence-unprobed"; exit 4 ;;
  locked)   echo "RESULT:status=locked"; echo "RESULT:error_class=locked"; exit 75 ;;
  fail)     echo "RESULT:status=failed"; echo "RESULT:error_class=convert"; echo "ERROR: conversion produced no text" >&2; exit 1 ;;
esac
echo "RESULT:revision=2026-11-05_abcd1234"; echo "RESULT:path=Sources/2026-11-05_abcd1234/"; echo "RESULT:status=extracted"; echo "RESULT:completeness=complete"
exit 0
STUB
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"
URL1="https://www.youtube.com/watch?v=abc123xyz00"
URL2="https://youtu.be/def456uvw00?t=12"

out=""; err=""; rc=0
run_lane() { rc=0; out="$(python3 "$LANE" "$@" 2>"$TEST_TMPDIR/err")" || rc=$?; err="$(cat "$TEST_TMPDIR/err")"; }
stub_reset() { rm -rf "$STUB_DIR" "$OUT"; mkdir -p "$STUB_DIR" "$OUT"; }
calls() { cat "$STUB_DIR/$1" 2>/dev/null || true; }
ncalls() { calls "$1" | grep -c . || true; }
bundle_of() { ls -d "$OUT/$1"/*/ 2>/dev/null | head -1 | sed 's:/$::'; }
jget() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=eval(sys.argv[2]); print(json.dumps(v, ensure_ascii=False) if isinstance(v,(dict,list,bool)) or v is None else v)' "$1" "$2"; }
keyshape() { python3 - "$1" "$2" <<'PY'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8")); b = json.load(open(sys.argv[2], encoding="utf-8"))
if set(a) != set(b):
    print("keys differ: only in output %s, only in fixture %s" % (sorted(set(a) - set(b)), sorted(set(b) - set(a)))); sys.exit(1)
bad = [k for k in a if (a[k] is None) != (b[k] is None) or (a[k] is not None and type(a[k]) is not type(b[k]))]
if bad:
    print("type/nullability differ: %s" % bad); sys.exit(1)
if set(a["producer"]) != set(b["producer"]) or set(a["track"]) != set(b["track"]):
    print("nested key sets differ"); sys.exit(1)
print("same keys, types and nullability")
PY
}

# --- 1. dry-run: the plan, nothing runs ---
echo "--- dry-run ---"
stub_reset
run_lane youtube "$URL1" "https://example.com/not-a-video" --dry-run --out "$OUT" --mnemon "$STUBS/mnemon" --intent "probe"
assert_eq "$rc" "1" "dry-run with a non-YouTube URL exits 1 (invalid)"
assert_contains "$out" "plan abc123xyz00 — yt-dlp --no-update --no-playlist --no-progress --cookies-from-browser safari --skip-download --dump-single-json '$URL1'" "...the metadata call is planned with the browser's cookies (the URL quoted for the shell: a ? is a glob)"
assert_contains "$out" "--skip-download --write-auto-subs|--write-subs --sub-langs <chosen at run time by the video's language> --sub-format vtt/srt/best -o '$OUT/abc123xyz00/<UTC timestamp>/abc123xyz00.%(ext)s' '$URL1'" "...one caption download, one language, nothing else downloaded"
assert_contains "$out" "$STUBS/mnemon add --url '$URL1' --media-bundle '$OUT/abc123xyz00/<UTC timestamp>' --import-source browser-lane --producer-fidelity <exact|model-transcribed by the track> --intent probe" "...and the gateway call with the bundle"
assert_contains "$out" "invalid https://example.com/not-a-video — not a YouTube video URL" "...a non-YouTube URL is invalid, by the engine's own resolver"
assert_contains "$out" "browser-lane: 2 video(s) · invalid 1 · planned 1" "...the summary line"
assert_eq "$(ncalls ytdlp-calls) $(ncalls mnemon-calls)" "0 0" "...nothing ran"
[[ -e "$OUT/abc123xyz00" ]] && fail "dry-run writes nothing" "a bundle dir exists" || pass "dry-run writes nothing"
run_lane youtube "$URL1" --dry-run --out "$OUT"
assert_eq "$rc" "0" "dry-run of YouTube URLs only exits 0"

# --- 2. one video, end to end against the stubs ---
echo "--- one video ---"
stub_reset
run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --context personal --intent "a talk" --pause 0
assert_eq "$rc" "0" "one video exits 0"
B="$(bundle_of abc123xyz00)"
[[ -n "$B" && -d "$B" ]] && pass "a bundle directory <out>/<video_id>/<stamp>/ exists" || fail "bundle directory" "none under $OUT/abc123xyz00"
assert_file_exists "$B/media.json" "...with media.json"
assert_file_exists "$B/abc123xyz00.en-orig.vtt" "...and the one caption file named <id>.<code>.vtt"
assert_eq "$(ls "$B" | wc -l | tr -d ' ')" "2" "...and nothing else (no cookies file, no audio)"
assert_eq "$(keyshape "$B/media.json" "$FIX/reports/media-bundle.json")" "same keys, types and nullability" "media.json has the frozen key set of reports/media-bundle.json"
assert_eq "$(jget "$B/media.json" 'd["method"]') $(jget "$B/media.json" 'd["video_id"]') $(jget "$B/media.json" 'd["language"]') $(jget "$B/media.json" 'd["original_language"]')" "captions:en:auto abc123xyz00 en en-US" "...method, video_id, the track's language and the video's"
assert_eq "$(jget "$B/media.json" 'd["track"]')" '{"lang": "en", "kind": "auto", "orig": true}' "...the en-orig automatic track chosen for an en-US video (choose_track: the video's language, -orig first)"
assert_eq "$(jget "$B/media.json" 'd["captions_path"]') $(jget "$B/media.json" 'd["captions_code"]')" "abc123xyz00.en-orig.vtt en-orig" "...captions_path names the file inside the bundle, captions_code yt-dlp's code"
assert_eq "$(jget "$B/media.json" 'len(d["chapters"])') $(jget "$B/media.json" 'd["chapters"][1]')" '3 {"start": 52.0, "end": 94.0, "title": "Why personal agents took off"}' "...chapters carried as {start, end, title}"
assert_eq "$(jget "$B/media.json" 'd["producer"]["lane"]') $(jget "$B/media.json" 'd["producer"]["browser"]') $(jget "$B/media.json" 'd["producer"]["tool_version"]')" "browser safari 2026.10.26" "...producer: the lane, the browser's name, yt-dlp's version"
assert_eq "$(jget "$B/media.json" 'd["duration"]') $(jget "$B/media.json" 'd["upload_date"]') $(jget "$B/media.json" 'd["channel"]')" "0:01:40 20260901 Example Channel" "...duration, upload_date, channel from the info"
assert_eq "$(ncalls ytdlp-calls)" "3" "three yt-dlp calls: metadata, the one caption download, the version probe"
assert_contains "$(calls ytdlp-calls | sed -n '1p')" "--cookies-from-browser safari --skip-download --dump-single-json $URL1" "...call 1: metadata with the browser's cookies"
assert_contains "$(calls ytdlp-calls | sed -n '2p')" "--cookies-from-browser safari --skip-download --write-auto-subs --sub-langs en-orig --sub-format vtt/srt/best -o $B/abc123xyz00.%(ext)s $URL1" "...call 2: ONE automatic track, en-orig, nothing downloaded but captions"
assert_eq "$(calls ytdlp-calls | grep -c -- '--cookies-from-browser safari')" "2" "...both acquisition calls carry the cookies flag (the version probe needs none)"
assert_eq "$(ncalls mnemon-calls)" "1" "one mnemon add"
assert_eq "$(calls mnemon-calls)" "add --url $URL1 --media-bundle $B --import-source browser-lane --producer-fidelity model-transcribed --context personal --intent a talk" "...with the bundle, the producer id, the fidelity of an automatic track, context and intent"
assert_contains "$out" "RESULT:status=extracted" "the gateway's RESULT lines are forwarded"
assert_contains "$out" "captured abc123xyz00 — Sources/2026-11-05_abcd1234/ · en-orig (auto) · 3 chapter(s) · completeness complete" "...and the video's line names the revision, the track and the completeness"
assert_contains "$out" "browser-lane: 1 video(s) · captured 1" "...the summary"
assert_eq "$(grep -ril 'cookie' "$B" | wc -l | tr -d ' ')" "0" "nothing in the bundle mentions a cookie"
assert_not_contains "$out$err" "Cookie" "nothing printed mentions a cookie"

# --- 3. two videos, in order; a manual track; the dedup answer ---
echo "--- two videos, a manual track, a duplicate ---"
stub_reset
STUB_ADD=dup STUB_YTDLP_MANUAL=1 run_lane youtube "$URL1" "$URL2" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "0" "two videos, both already in the vault: exit 0"
assert_eq "$(ncalls ytdlp-calls) $(ncalls mnemon-calls)" "6 2" "...3 yt-dlp calls per video, one add per video"
assert_eq "$(calls mnemon-calls | sed -n '1p' | cut -d' ' -f3) $(calls mnemon-calls | sed -n '2p' | cut -d' ' -f3)" "$URL1 https://www.youtube.com/watch?v=def456uvw00" "...in the given order, the youtu.be form canonicalized to the watch URL"
assert_contains "$(calls ytdlp-calls | sed -n '2p')" "--write-subs --sub-langs en --sub-format" "...a manual en track wins over the automatic en-orig (choose_track) and is fetched with --write-subs"
assert_contains "$(calls mnemon-calls | sed -n '1p')" "--producer-fidelity exact" "...a manual track's fidelity claim is exact"
B2="$(bundle_of def456uvw00)"
assert_file_exists "$B2/def456uvw00.en.vtt" "...the second bundle holds its own track"
assert_contains "$out" "skipped-duplicate abc123xyz00 — abc123xyz00 is already in the vault (2026-11-01_abcd1234); --recapture makes a new revision" "...a duplicate is reported with the door"
assert_eq "$(printf '%s\n' "$out" | grep -c '^skipped-duplicate ')" "2" "...for both"

# --- 4. walls: the lane reports and moves on ---
echo "--- walls ---"
stub_reset
STUB_YTDLP=wall run_lane youtube "$URL1" "$URL2" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "1" "a wall at the metadata call: exit 1"
assert_contains "$out" "blocked abc123xyz00 — bot-wall: ERROR: [youtube] abc123xyz00: Sign in to confirm you’re not a bot" "...the video's line names the wall and yt-dlp's own line"
assert_eq "$(printf '%s\n' "$out" | grep -c '^blocked ')" "2" "...the next video is still tried (and walled too)"
assert_eq "$(ncalls mnemon-calls)" "0" "...no gateway call"
[[ -e "$OUT/abc123xyz00" ]] && fail "no bundle before the metadata" "a bundle dir exists" || pass "no bundle directory for a video walled at the metadata call"
stub_reset
STUB_YTDLP=wall-subs run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "1" "a wall at the caption download: exit 1"
assert_contains "$out" "blocked abc123xyz00 — bot-wall:" "...reported as blocked"
B="$(bundle_of abc123xyz00)"
[[ -n "$B" && ! -f "$B/media.json" ]] && pass "...the bundle directory exists without media.json (an incomplete bundle, never offered to the gateway)" || fail "incomplete bundle" "media.json present or no dir"
assert_eq "$(ncalls mnemon-calls)" "0" "...no gateway call"
stub_reset
STUB_YTDLP=nosubs run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "1" "no caption track: exit 1"
assert_contains "$out" "failed abc123xyz00 — no caption track (0 listed); the engine's whisper path is the alternative" "...named, with the alternative"
stub_reset
STUB_YTDLP=fail run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_contains "$out" "failed abc123xyz00 — yt-dlp metadata failed: ERROR: [youtube] abc123xyz00: Video unavailable" "another yt-dlp refusal is failed with its cause (not a wall)"
stub_reset
STUB_YTDLP=two-files run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "1" "two caption files in the bundle: exit 1"
assert_contains "$out" "expected one caption file in the bundle, found 2" "...the lane refuses to guess which one is the track"

# --- 5. --no-add, the gateway's answers, --json ---
echo "--- --no-add, gateway answers, --json ---"
stub_reset
run_lane youtube "$URL1" --out "$OUT" --no-add --mnemon "$STUBS/mnemon" --pause 0 --recapture
assert_eq "$rc" "0" "--no-add exits 0"
B="$(bundle_of abc123xyz00)"
assert_contains "$out" "bundled abc123xyz00 — $B · en-orig (auto) · 3 chapter(s) · $STUBS/mnemon add --url '$URL1' --media-bundle $B --import-source browser-lane --producer-fidelity model-transcribed --recapture" "...the bundle and the exact add argv to run later (--recapture carried)"
assert_eq "$(ncalls mnemon-calls)" "0" "...no gateway call"
stub_reset
STUB_ADD=captured run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "0" "gateway exit 4 (source published, extract pending): the lane exits 0"
assert_contains "$out" "captured-pending-extract abc123xyz00 — Sources/2026-11-05_abcd1234/ published; finish with: $STUBS/mnemon add --resume 2026-11-05_abcd1234" "...with the --resume door through the dispatcher that ran the lane"
stub_reset
STUB_ADD=locked run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "1" "gateway exit 75: exit 1"
B="$(bundle_of abc123xyz00)"
assert_contains "$out" "locked abc123xyz00 — the vault is busy; the bundle stays — retry: $STUBS/mnemon add --url '$URL1' --media-bundle $B" "...the bundle stays and the retry argv is printed"
assert_file_exists "$B/media.json" "...the bundle is intact"
stub_reset
STUB_ADD=fail run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "1" "gateway exit 1: exit 1"
assert_contains "$out" "add-failed abc123xyz00 — mnemon add exit 1 (convert: ERROR: conversion produced no text); the bundle stays — retry:" "...add-failed with the gateway's class and last stderr line"
assert_contains "$err" "ERROR: conversion produced no text" "...the gateway's stderr is forwarded"
stub_reset
run_lane youtube "$URL1" "https://example.com/x" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0 --json
assert_eq "$rc" "1" "--json: the exit code is unchanged"
printf '%s\n' "$out" > "$TEST_TMPDIR/lane.json"
assert_eq "$(jget "$TEST_TMPDIR/lane.json" 'd["schema"]') $(jget "$TEST_TMPDIR/lane.json" 'd["counts"]') $(jget "$TEST_TMPDIR/lane.json" 'd["exit"]')" 'mnemon-browser-lane/1 {"captured": 1, "invalid": 1} 1' "...one mnemon-browser-lane/1 document with counts and the exit"
assert_eq "$(jget "$TEST_TMPDIR/lane.json" 'd["videos"][0]["outcome"]') $(jget "$TEST_TMPDIR/lane.json" 'd["videos"][0]["track"]["code"]') $(jget "$TEST_TMPDIR/lane.json" 'd["videos"][0]["add"]["exit"]') $(jget "$TEST_TMPDIR/lane.json" 'd["videos"][0]["add"]["result"]["status"]')" "captured en-orig 0 extracted" "...per video: outcome, track, the gateway's exit and RESULT map"
assert_eq "$(jget "$TEST_TMPDIR/lane.json" 'd["videos"][1]["outcome"]') $(jget "$TEST_TMPDIR/lane.json" 'd["videos"][1]["video_id"]')" "invalid null" "...an invalid URL has no video id"

# --- 5b. Codex review (2026-11-05): a timeout is one video's failure; the selector and the quoting of printed commands ---
echo "--- timeout per video, selector, quoting ---"
stub_reset
STUB_YTDLP=hang run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0 --timeout 1
assert_eq "$rc" "1" "a yt-dlp call past --timeout: exit 1"
assert_contains "$out" "failed abc123xyz00 — timeout after 1." "...the video's line says timeout, stage and budget"
assert_contains "$out" "in stage download (budget 1s); the yt-dlp process group was terminated" "...with the stage"
assert_contains "$out" "browser-lane: 1 video(s) · failed 1" "...and the summary is still printed"
for f in ytdlp.pid ytdlp-child.pid; do
  pid="$(cat "$STUB_DIR/$f" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then fail "stub process $f terminated" "pid $pid alive"; kill -9 "$pid" 2>/dev/null; else pass "stub process $f terminated with its group"; fi
done
stub_reset
STUB_YTDLP=hang run_lane youtube "$URL1" "$URL2" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0 --timeout 1 --json
assert_eq "$rc" "1" "two hanging videos under --json: exit 1, no crash"
printf '%s\n' "$out" > "$TEST_TMPDIR/lane-timeout.json"
assert_eq "$(jget "$TEST_TMPDIR/lane-timeout.json" 'd["counts"]')" '{"failed": 2}' "...the second video was still tried; the document carries both"
stub_reset
MNEMON_INSTANCE=work run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0 --intent "a talk"
assert_eq "$rc" "0" "with the dispatcher's resolved instance in the environment: exit 0"
assert_eq "$(calls mnemon-calls | cut -d' ' -f1-3)" "--instance work add" "...the executed add names the instance before the command"
stub_reset
MNEMON_INSTANCE=work run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0 --intent "a talk" --no-add
B="$(bundle_of abc123xyz00)"
assert_contains "$out" "$STUBS/mnemon --instance work add --url '$URL1' --media-bundle $B --import-source browser-lane --producer-fidelity model-transcribed --intent 'a talk'" "...a printed command names the instance too, and quotes the intent for the shell"
stub_reset
MNEMON_CONFIG=/some/dir/work.yaml MNEMON_INSTANCE=adhoc run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0 --no-add
assert_contains "$out" "$STUBS/mnemon --config /some/dir/work.yaml add --url" "...a config loaded by path is repeated as --config"
stub_reset
MNEMON_INSTANCE=work STUB_ADD=captured run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_contains "$out" "finish with: $STUBS/mnemon --instance work add --resume 2026-11-05_abcd1234" "...and the --resume hint carries the instance and the dispatcher's path"
stub_reset
run_lane youtube "$URL1" --out "$OUT" --dry-run --mnemon "$STUBS/mnemon" --intent "two words"
assert_contains "$out" "--intent 'two words'" "the dry-run plan quotes a two-word intent"
assert_contains "$out" "--sub-langs <chosen at run time by the video's language> --sub-format vtt/srt/best" "...and leaves the plan's placeholders readable"

# --- 6. usage: the browser name, the tools ---
echo "--- usage ---"
stub_reset
run_lane youtube "$URL1" --out "$OUT" --browser "/tmp/cookies.txt" --dry-run
assert_eq "$rc" "2" "--browser takes a browser name, never a path (a cookie file is not the lane's business)"
run_lane youtube "$URL1" --out "$OUT" --browser "chrome:Profile 1" --dry-run --mnemon "$STUBS/mnemon"
assert_eq "$rc" "0" "yt-dlp's BROWSER:PROFILE form passes"
assert_contains "$out" "--cookies-from-browser 'chrome:Profile 1' --skip-download --dump-single-json" "...and reaches the plan, quoted for the shell"
run_lane youtube "$URL1" --out "$OUT" --pause -1 --dry-run
assert_eq "$rc" "2" "a negative --pause is a usage error"
run_lane youtube "$URL1" --out "$OUT" --mnemon "$TEST_TMPDIR/no-such-mnemon" --pause 0
assert_eq "$rc" "2" "a missing dispatcher is a usage error before anything runs"
assert_eq "$(ncalls ytdlp-calls)" "0" "...and yt-dlp did not run"
PATH="/usr/bin:/bin" run_lane youtube "$URL1" --out "$OUT" --mnemon "$STUBS/mnemon" --pause 0
assert_eq "$rc" "3" "no yt-dlp on PATH: exit 3 (environment)"
assert_contains "$err" "yt-dlp not found. Install: brew install yt-dlp" "...with the install hint"

cd /
rm -rf "$TEST_TMPDIR"
summary
