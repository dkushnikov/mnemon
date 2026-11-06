#!/usr/bin/env python3
"""browser-lane.py — the browser lane's media form: YouTube captions through the reader's own browser session.

    browser-lane.py youtube <url>… [--browser safari] [--out <dir>] [--no-add] [--context <ctx>] [--intent <text>]
                    [--recapture] [--pause <s>] [--timeout <s>] [--dry-run] [--json] [--mnemon <path>]

YouTube answers a host it distrusts with a bot wall, and the engine's media path does not try to pass it — no
cookies, no proxies (docs/capture.md §7). This producer does what the engine must not: it runs yt-dlp with
`--cookies-from-browser <browser>` — the reader's own logged-in browser, on the reader's own machine — ONE video
at a time, sequentially, and acquires exactly ONE caption track per video (several tracks in a row answer HTTP
429). It writes a bundle per video and hands it to the gateway (`mnemon add --url <video> --media-bundle <dir>`),
which publishes the track as the raw (`raw_kind: captions`) and converts it (capture.md §7, the browser lane).
The gateway never sees a cookie, and the bundle holds none: yt-dlp reads the browser's cookie store for its own
requests and writes nothing of it. Nothing here prints a cookie either — the browser's name is the only thing
that reaches a log.

The bundle, `<out>/<video_id>/<UTC timestamp>/`:
  media.json              mnemon-media/1, flat: method `captions:<lang>:<manual|auto>`, audio_path null, title,
                          duration, upload_date, video_id, language, original_language, track, channel — and,
                          additive (capture layer A13): `chapters` [{start, end, title}], `captions_path` (the
                          track's file name inside the bundle), `captions_code` (yt-dlp's code — `en-orig` is
                          the original automatic track), `producer` {lane, browser, tool, tool_version,
                          acquired_at}
  <video_id>.<code>.vtt   the track as yt-dlp wrote it (SRT when the video offers no VTT; the converter reads both)

Per video: (1) `yt-dlp --dump-single-json` — the metadata and the caption listing; a wall in its output is
`blocked` (media-extract.classify_wall) and the lane moves on to the next video — cookies YouTube does not accept
are not a thing to retry in a loop; (2) the track by the engine's own rule (media-extract.choose_track: the
video's language first, manual before automatic, the `-orig` track first); (3) one `yt-dlp --write-subs |
--write-auto-subs --sub-langs <code> --sub-format vtt/srt/best --skip-download`; (4) the bundle; (5) `mnemon add`
unless --no-add; (6) --pause seconds before the next video. Every tool runs under one supervisor per video
(media-extract.Supervisor): its own process group, --timeout for the video, SIGTERM → 2 s → SIGKILL.

Output: one line per video, `<outcome> <video_id> — <detail>` — outcomes `captured` · `skipped-duplicate` ·
`captured-pending-extract` (exit 4 of the gateway: the source is published, `mnemon add --resume` finishes it) ·
`bundled` (--no-add) · `blocked` · `failed` · `invalid` (not a YouTube URL) · `add-failed` (the gateway refused
or failed; the bundle stays, the argv is printed) · `locked` (exit 75; the bundle stays) — then one summary line.
--json prints one `mnemon-browser-lane/1` document instead. Exit 0 when every video was captured, bundled or a
duplicate; 1 when any was blocked, failed, invalid or refused; 2 usage. Stdlib only, python 3.9; external tools:
yt-dlp (`brew install yt-dlp`) and the browser whose cookie store it reads (Safari needs nothing; Chrome asks
for its keychain item on screen the first time — the lane neither suppresses nor answers that prompt).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

BIN_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN_DIR)

from capture import resolve as resolvemod  # noqa: E402

SCHEMA = "mnemon-browser-lane/1"
PRODUCER_ID = "browser-lane"
LANE = "browser"
EXIT_FAILED = 1
EXIT_USAGE = 2
DEFAULT_PAUSE = 2.0
DEFAULT_TIMEOUT = 300.0
# yt-dlp's BROWSER[+KEYRING][:PROFILE][::CONTAINER]; a name, never a path that could be a cookie file
_BROWSER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+_.:/ -]{0,120}$")
_CAPTION_EXTS = (".vtt", ".srt")


def _load_media_extract():
    """bin/media-extract.py as a module: its Supervisor, choose_track, classify_wall, format_duration are the
    engine's own rules — the lane never re-implements them."""
    spec = importlib.util.spec_from_file_location("media_extract", os.path.join(BIN_DIR, "media-extract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ME = _load_media_extract()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_out_dir() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "mnemon", "browser-lane")


def video_of(url: str):
    """(canonical watch URL, video id) for a YouTube URL in any of its forms, else (None, None)."""
    try:
        res = resolvemod.resolve(url)
    except Exception:
        return None, None
    if res.kind != "youtube":
        return None, None
    canonical = res.canonical.url
    vid = parse_qs(urlsplit(canonical).query).get("v", [None])[0]
    return canonical, vid


def tracks_from_info(info: dict):
    """The caption listing of a yt-dlp info document as the engine's track list — manual tracks from
    `subtitles`, automatic ones from `automatic_captions`, `-orig` marked — each with yt-dlp's `code` kept, so
    the chosen element names what to download. `live_chat` is not a caption track."""
    tracks = []
    for code in (info.get("subtitles") or {}):
        if not isinstance(code, str) or code == "live_chat":
            continue
        tracks.append({"lang": code, "kind": "manual", "orig": False, "code": code})
    for code in (info.get("automatic_captions") or {}):
        if not isinstance(code, str):
            continue
        orig = code.lower().endswith("-orig")
        tracks.append({"lang": code[:-5] if orig else code, "kind": "auto", "orig": orig, "code": code})
    return tracks


def _language_of(info: dict):
    lang = info.get("language")
    return lang.strip() if isinstance(lang, str) and lang.strip() else None


def chapters_of(info: dict):
    out = []
    for ch in (info.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        title = ch.get("title")
        try:
            start = float(ch.get("start_time"))
        except (TypeError, ValueError):
            continue
        end = ch.get("end_time")
        try:
            end = float(end) if end is not None else None
        except (TypeError, ValueError):
            end = None
        if isinstance(title, str) and title.strip():
            out.append({"start": start, "end": end, "title": " ".join(title.split())})
    out.sort(key=lambda c: c["start"])
    return out


def media_record(info: dict, vid: str, track: dict, captions_name: str, browser: str, tool_version) -> dict:
    """The bundle's `mnemon-media/1` record: media-extract's flat record, then the lane's additive keys."""
    channel = info.get("channel") or info.get("uploader")
    language = _language_of(info)
    rec = ME.media_record(
        "captions:%s:%s" % (track["lang"], track["kind"]), None, info.get("title") or "",
        ME.format_duration(info.get("duration") or 0),
        upload_date=str(info["upload_date"]) if info.get("upload_date") else None,
        video_id=vid, language=track["lang"], original_language=language,
        track={"lang": track["lang"], "kind": track["kind"], "orig": bool(track.get("orig"))},
        channel=str(channel) if channel else None)
    doc = {"schema": "mnemon-media/1"}
    doc.update(rec)
    doc["chapters"] = chapters_of(info)
    doc["captions_path"] = captions_name
    doc["captions_code"] = track["code"]
    doc["producer"] = {"lane": LANE, "browser": browser, "tool": "yt-dlp", "tool_version": tool_version,
                       "acquired_at": now_iso()}
    return doc


def fidelity_of(track: dict) -> str:
    """The producer's fidelity claim for the gateway (`--producer-fidelity`): a manual track is the publisher's
    text (`exact`); an automatic one is YouTube's model (`model-transcribed`)."""
    return "exact" if track.get("kind") == "manual" else "model-transcribed"


def ytdlp_base(browser: str):
    return ["yt-dlp", "--no-update", "--no-playlist", "--no-progress", "--cookies-from-browser", browser]


def add_argv(mnemon: str, canonical: str, bundle: str, track: dict, args) -> list:
    argv = [mnemon, "add", "--url", canonical, "--media-bundle", bundle,
            "--import-source", PRODUCER_ID, "--producer-fidelity", fidelity_of(track)]
    if args.context:
        argv += ["--context", args.context]
    if args.intent:
        argv += ["--intent", args.intent]
    if args.recapture:
        argv.append("--recapture")
    return argv


def tool_version(sup):
    rc, out, err = sup.run(["yt-dlp", "--version"], "probe")
    return out.decode("utf-8", "replace").strip().splitlines()[0] if rc == 0 and out.strip() else None


class Outcome:
    def __init__(self, url, vid=None, canonical=None):
        self.url = url
        self.video_id = vid
        self.canonical = canonical
        self.outcome = None
        self.detail = ""
        self.bundle = None
        self.track = None
        self.add_exit = None
        self.add_result = {}
        self.wall = None

    def done(self, outcome, detail=""):
        self.outcome = outcome
        self.detail = detail
        return self

    def as_json(self):
        return {"url": self.url, "video_id": self.video_id, "canonical_url": self.canonical, "outcome": self.outcome,
                "detail": self.detail, "bundle": self.bundle, "track": self.track, "wall": self.wall,
                "add": {"exit": self.add_exit, "result": self.add_result} if self.add_exit is not None else None}


def _result_map(stdout: str) -> dict:
    out = {}
    for ln in stdout.splitlines():
        if ln.startswith("RESULT:") and "=" in ln:
            k, v = ln[7:].split("=", 1)
            out[k] = v
    return out


def one_video(url: str, args, mnemon: str, emit) -> Outcome:
    o = Outcome(url)
    canonical, vid = video_of(url)
    if not vid:
        return o.done("invalid", "not a YouTube video URL (watch, youtu.be, shorts, embed, live, music)")
    o.video_id, o.canonical = vid, canonical
    if args.dry_run:
        bundle = os.path.join(args.out, vid, "<UTC timestamp>")
        o.bundle = bundle
        emit("plan %s — %s" % (vid, " ".join(ytdlp_base(args.browser) + ["--skip-download", "--dump-single-json", canonical])))
        emit("plan %s — %s" % (vid, " ".join(ytdlp_base(args.browser) + ["--skip-download", "--write-auto-subs|--write-subs", "--sub-langs", "<chosen at run time by the video's language>", "--sub-format", "vtt/srt/best", "-o", os.path.join(bundle, vid + ".%(ext)s"), canonical])))
        if not args.no_add:
            emit("plan %s — %s" % (vid, " ".join(add_argv(mnemon, canonical, bundle, {"kind": "auto"}, args)).replace("model-transcribed", "<exact|model-transcribed by the track>")))
        return o.done("planned", "nothing ran (--dry-run)")

    sup = ME.Supervisor(args.timeout)
    # 1. metadata and the caption listing — one call, the reader's cookies
    rc, out, err = sup.run(ytdlp_base(args.browser) + ["--skip-download", "--dump-single-json", canonical], "download")
    if rc != 0:
        kind = ME.classify_wall(err)
        if kind:
            o.wall = kind
            return o.done("blocked", "%s: %s" % (kind, ME._wall_line(err)))
        return o.done("failed", "yt-dlp metadata failed: %s" % (ME._tail(err) or "exit %d" % rc))
    try:
        info = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        return o.done("failed", "yt-dlp metadata is not JSON")
    if not isinstance(info, dict):
        return o.done("failed", "yt-dlp metadata is not an object")
    if info.get("id") and info.get("id") != vid:
        return o.done("failed", "yt-dlp answered for video %r, not %r" % (info.get("id"), vid))
    # 2. one track, by the engine's rule
    tracks = tracks_from_info(info)
    track = ME.choose_track(tracks, _language_of(info))
    if track is None:
        return o.done("failed", "no caption track (%d listed); the engine's whisper path is the alternative" % len(tracks))
    o.track = {"lang": track["lang"], "kind": track["kind"], "orig": bool(track.get("orig")), "code": track["code"]}
    # 3. the track — one call, one language, nothing downloaded but the captions
    bundle = os.path.join(args.out, vid, stamp())
    os.makedirs(bundle, mode=0o700, exist_ok=True)
    o.bundle = bundle
    before = set(os.listdir(bundle))
    flag = "--write-subs" if track["kind"] == "manual" else "--write-auto-subs"
    rc, out, err = sup.run(ytdlp_base(args.browser) + ["--skip-download", flag, "--sub-langs", track["code"],
                                                        "--sub-format", "vtt/srt/best",
                                                        "-o", os.path.join(bundle, vid + ".%(ext)s"), canonical], "captions")
    if rc != 0:
        kind = ME.classify_wall(err)
        if kind:
            o.wall = kind
            return o.done("blocked", "%s: %s" % (kind, ME._wall_line(err)))
        return o.done("failed", "yt-dlp caption download failed: %s" % (ME._tail(err) or "exit %d" % rc))
    new = sorted(f for f in os.listdir(bundle) if f not in before and f.lower().endswith(_CAPTION_EXTS))
    if len(new) != 1:
        return o.done("failed", "expected one caption file in the bundle, found %d (%s)" % (len(new), ", ".join(new) or "none"))
    captions_name = new[0]
    if os.path.getsize(os.path.join(bundle, captions_name)) == 0:
        return o.done("failed", "the caption file %s is empty" % captions_name)
    # 4. the record — written after the track, so a bundle with media.json is a complete bundle
    rec = media_record(info, vid, track, captions_name, args.browser, tool_version(sup))
    tmp = os.path.join(bundle, ".media.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, os.path.join(bundle, "media.json"))
    if args.no_add:
        return o.done("bundled", "%s · %s (%s) · %d chapter(s) · %s" % (bundle, track["code"], track["kind"], len(rec["chapters"]),
                                                                        " ".join(add_argv(mnemon, canonical, bundle, track, args))))
    # 5. the gateway: the bundle is its input, the video its resource
    argv = add_argv(mnemon, canonical, bundle, track, args)
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as exc:
        return o.done("add-failed", "cannot run %s: %s" % (argv[0], exc))
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    if stderr.strip():
        sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")
    for ln in stdout.splitlines():
        if ln.startswith("RESULT:"):
            emit(ln)
    o.add_exit = proc.returncode
    o.add_result = _result_map(stdout)
    status = o.add_result.get("status", "")
    rev = o.add_result.get("revision", "")
    if proc.returncode == 0 and status == "skipped-duplicate":
        return o.done("skipped-duplicate", "%s is already in the vault (%s); --recapture makes a new revision" % (vid, rev or "?"))
    if proc.returncode == 0 and status in ("extracted", "bookmarked"):
        return o.done("captured", "Sources/%s/ · %s (%s) · %d chapter(s) · completeness %s" % (
            rev, track["code"], track["kind"], len(rec["chapters"]), o.add_result.get("completeness", "?")))
    if proc.returncode == 4:
        return o.done("captured-pending-extract", "Sources/%s/ published; finish with: mnemon add --resume %s" % (rev, rev or o.add_result.get("capture_id", "?")))
    if proc.returncode == 75:
        return o.done("locked", "the vault is busy; the bundle stays — retry: %s" % " ".join(argv))
    return o.done("add-failed", "mnemon add exit %d%s; the bundle stays — retry: %s" % (
        proc.returncode, (" (%s%s)" % (o.add_result.get("error_class", ""), (": " + stderr.strip().splitlines()[-1]) if stderr.strip() else "")) if (o.add_result.get("error_class") or stderr.strip()) else "", " ".join(argv)))


def main() -> int:
    ap = argparse.ArgumentParser(prog="mnemon browser-lane", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    yt = sub.add_parser("youtube", help="one caption track per video through the reader's browser cookies, then mnemon add")
    yt.add_argument("urls", nargs="+", metavar="url", help="YouTube video URLs (any form); processed one at a time, in order")
    yt.add_argument("--browser", default="safari", help="yt-dlp's --cookies-from-browser value (default safari; chrome asks for its keychain item on screen)")
    yt.add_argument("--out", default=None, help="where bundles go (default $XDG_STATE_HOME/mnemon/browser-lane; one <video_id>/<timestamp>/ per run)")
    yt.add_argument("--no-add", action="store_true", help="write the bundle and print the mnemon add argv; do not capture")
    yt.add_argument("--context", default=None, help="passed to mnemon add --context")
    yt.add_argument("--intent", default=None, help="passed to mnemon add --intent")
    yt.add_argument("--recapture", action="store_true", help="passed to mnemon add (a new revision of a video already in the vault)")
    yt.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="seconds between videos (default %g)" % DEFAULT_PAUSE)
    yt.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="budget in seconds for one video's yt-dlp calls (default %g)" % DEFAULT_TIMEOUT)
    yt.add_argument("--dry-run", action="store_true", help="print the plan per video; run nothing")
    yt.add_argument("--json", action="store_true", help="one mnemon-browser-lane/1 document on stdout instead of lines")
    yt.add_argument("--mnemon", default=None, help="the dispatcher to run for add (default: $MNEMON_DISPATCHER as `mnemon browser-lane` sets it, else bin/mnemon beside this script)")
    args = ap.parse_args()

    if not _BROWSER_RE.match(args.browser or ""):
        print("ERROR: --browser takes yt-dlp's BROWSER[+KEYRING][:PROFILE][::CONTAINER] name, e.g. safari, chrome, firefox", file=sys.stderr)
        return EXIT_USAGE
    if args.pause < 0 or args.timeout <= 0 or args.timeout != args.timeout:
        print("ERROR: --pause must be ≥ 0 and --timeout a positive number of seconds", file=sys.stderr)
        return EXIT_USAGE
    args.out = os.path.abspath(args.out or default_out_dir())
    # the dispatcher that ran this producer names itself in MNEMON_DISPATCHER, so `add` goes back through it with the
    # instance it resolved; --mnemon (tests, a hand run) wins; bin/mnemon beside this script is the last resort
    mnemon = args.mnemon or os.environ.get("MNEMON_DISPATCHER") or os.path.join(BIN_DIR, "mnemon")
    if not args.dry_run:
        if not shutil.which("yt-dlp"):
            print("ERROR: yt-dlp not found. Install: brew install yt-dlp", file=sys.stderr)
            return 3
        os.makedirs(args.out, mode=0o700, exist_ok=True)
        if not args.no_add and not (os.path.isfile(mnemon) and os.access(mnemon, os.X_OK)):
            print("ERROR: the dispatcher %s is missing or not executable (--mnemon <path>)" % mnemon, file=sys.stderr)
            return EXIT_USAGE

    lines = []

    def emit(line):
        if args.json:
            lines.append(line)
        else:
            print(line, flush=True)

    outcomes = []
    for i, url in enumerate(args.urls):
        if i and not args.dry_run and args.pause:
            time.sleep(args.pause)
        o = one_video(url, args, mnemon, emit)
        outcomes.append(o)
        emit("%s %s — %s" % (o.outcome, o.video_id or url, o.detail))
    counts = {}
    for o in outcomes:
        counts[o.outcome] = counts.get(o.outcome, 0) + 1
    ok = {"captured", "skipped-duplicate", "bundled", "planned", "captured-pending-extract"}
    rc = 0 if all(o.outcome in ok for o in outcomes) else EXIT_FAILED
    summary = "browser-lane: %d video(s) · %s" % (len(outcomes), " · ".join("%s %d" % (k, counts[k]) for k in sorted(counts)))
    if args.json:
        print(json.dumps({"schema": SCHEMA, "browser": args.browser, "out": args.out, "dry_run": bool(args.dry_run),
                          "videos": [o.as_json() for o in outcomes], "counts": counts, "lines": lines, "summary": summary,
                          "exit": rc}, ensure_ascii=False, indent=1))
    else:
        print(summary)
    return rc


if __name__ == "__main__":
    sys.exit(main())
