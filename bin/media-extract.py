#!/usr/bin/env python3
"""Media extraction helper for knowledge-gateway.
Extracts transcripts and metadata from YouTube videos and audio files.

Two output contracts (docs/specs/2026-10-15-capture-contracts.md §6, extended by
docs/specs/2026-10-15-capture-layer.md §3.7):

  media-extract.py youtube <url> [--whisper-model M] [--no-whisper] [--timeout S]
  media-extract.py audio (--url U | --file F) [--title T] [--whisper-model M] [--timeout S]
      → the legacy stdout contract: a metadata header followed by the transcript.

  … --out-dir <dir>
      → writes <dir>/transcript.txt (the transcript bytes the gateway uses as capture-text)
        and <dir>/media.json (`mnemon-media/1`, flat — every key top-level, D12):
          {"schema": "mnemon-media/1",
           "method": "captions:<lang>:<manual|auto>" | "whisper:<model>",
           "audio_path": "<the audio that was transcribed, kept under <dir>>" | null,
           "title": …, "duration": "H:MM:SS", "upload_date": …, "video_id": …,
           "language": "<the transcript's language>" | null,
           "original_language": "<the video's language per yt-dlp>" | null,
           "track": {"lang", "kind": "manual"|"auto", "orig": bool} | null,
           "channel": … | null}
        Everything downloaded or produced stays under <dir> — nothing under --out-dir is ever
        deleted by this script (the gateway archives `audio_path` and merges the metadata).
        Stdout stays empty in this mode. A transcription-only run (`audio --file …`) reports
        only what it can know: the video keys are null, the receipt is authoritative on replay.

  media-extract.py _captions <video_id> --out <file> [--original-language L]
      → hidden: the caption listing, choice and fetch in a process of its own, so a hung HTTP
        call inside youtube_transcript_api is killable (D11). Prints one JSON line
        {"language", "track", "method"}; exit 1 when no usable captions, 3 without the library.

Timeouts (D11, §12 A9): `--timeout S` is one budget for the whole run. Every tool — the
captions child, yt-dlp, ffprobe, whisper — runs in its own process group under the remaining
budget; on expiry (or on SIGTERM/SIGINT/SIGHUP from the parent) every tracked group gets
SIGTERM, a 2 s grace, SIGKILL for what remains, is reaped, and only then does this process
exit: on expiry with `error.json` under --out-dir ({"schema": "mnemon-media-error/1",
"error_class": "timeout", "stage": "download"|"probe"|"captions"|"transcribe", "elapsed_s"}),
nothing deleted, exit 124. Without the flag there is no timeout (phase-1 behaviour).

Track choice (D12): `choose_track(tracks, original_language)` picks, in order, a manual track
in the video's language · an automatic one in it (the `-orig` track first) · a manual en or ru
track · an automatic en or ru track · the first manual · the first automatic · None (whisper).
`list_tracks(ts)` adapts the transcript API's listing (v1.x and v0.x) to that list. Both are
pure and fixture-tested (tests/fixtures/capture/tracks.json).

Division of labour with the gateway (contracts §12 A7): for the `audio` origin the gateway
acquires the audio itself and publishes it as the raw BEFORE calling `audio --file <published
raw> --out-dir …` for the transcription only — `audio --url` stays for callers outside the
gateway. For `youtube` the captions path acquires no audio; the whisper fallback downloads and
transcribes in this one run and reports the audio in `audio_path`, which the gateway publishes
as the raw afterwards (a crash between the download and the transcription loses only that
unrecorded download, which is refetched).

Exit codes: 0 · 1 extraction failed (no captions with --no-whisper, a tool failed, empty
transcript) · 2 usage · 3 environment (a required tool is missing) · 124 timeout.
Stdlib only, python 3.9; external tools: yt-dlp, whisper, ffprobe, youtube_transcript_api.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

AUDIO_EXTS = (".wav", ".opus", ".m4a", ".mp3", ".webm", ".ogg", ".flac", ".aac")
PREFERRED_FALLBACK = ("en", "ru")   # D12: after the video's own language
TERM_GRACE = 2.0                     # §12 A9: SIGTERM → 2 s → SIGKILL
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_TIMEOUT = 124
SCRIPT = os.path.abspath(__file__)


def check_dependency(cmd, install_hint):
    if not shutil.which(cmd):
        print(f"ERROR: {cmd} not found. Install: {install_hint}", file=sys.stderr)
        sys.exit(EXIT_ENVIRONMENT)


def format_duration(seconds):
    try:
        s = int(float(seconds or 0))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})', url)
    if not m:
        print(f"ERROR: Cannot extract video ID from {url}", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    return m.group(1)


# ---------------------------------------------------------------------------------------------
# Track choice (D12) — pure functions, no I/O
# ---------------------------------------------------------------------------------------------

def _primary(lang):
    """`pt-BR` → `pt`; None → ``. Matching is by primary subtag, case-insensitive."""
    return (lang or "").strip().split("-")[0].lower()


def list_tracks(ts):
    """Adapt a caption listing to `[{"lang", "kind": "manual"|"auto", "orig": bool}]`, in order.

    Accepts the transcript API's TranscriptList (v1.x and v0.x: objects with `language_code`
    and `is_generated`) or any iterable of such objects or of dicts with those keys. A code
    ending in `-orig` (YouTube's automatic track in the video's original language) is marked
    `orig: true` and its `lang` loses the suffix.
    """
    out = []
    for t in ts:
        if isinstance(t, dict):
            code = t.get("language_code") or t.get("lang") or ""
            generated = bool(t.get("is_generated", t.get("kind") == "auto"))
        else:
            code = getattr(t, "language_code", "") or ""
            generated = bool(getattr(t, "is_generated", False))
        code = str(code).strip()
        orig = code.lower().endswith("-orig")
        lang = code[:-5] if orig else code
        out.append({"lang": lang, "kind": "auto" if generated else "manual", "orig": orig})
    return out


def choose_track(tracks, original_language):
    """D12 / §3.7 — the caption track for a video, in exactly this order:

    1. a manual track in the video's language (`original_language`)
    2. an automatic track in it — the `-orig` track first
    3. a manual `en` track, else a manual `ru` track
    4. an automatic `en` track, else an automatic `ru` track
    5. the first manual track
    6. the first automatic track
    7. None — transcribe with whisper

    `tracks` is the list `list_tracks` returns; the chosen element is returned as it is (the
    same object), so callers can map it back to the listing. Pure: no I/O, no network.
    """
    tracks = [t for t in (tracks or []) if isinstance(t, dict) and t.get("lang")]
    orig = _primary(original_language)
    if orig:
        for t in tracks:
            if t.get("kind") == "manual" and _primary(t["lang"]) == orig:
                return t
        auto_in = [t for t in tracks if t.get("kind") == "auto" and _primary(t["lang"]) == orig]
        if auto_in:
            for t in auto_in:
                if t.get("orig"):
                    return t
            return auto_in[0]
    for lang in PREFERRED_FALLBACK:
        for t in tracks:
            if t.get("kind") == "manual" and _primary(t["lang"]) == lang:
                return t
    for lang in PREFERRED_FALLBACK:
        for t in tracks:
            if t.get("kind") == "auto" and _primary(t["lang"]) == lang:
                return t
    for t in tracks:
        if t.get("kind") == "manual":
            return t
    for t in tracks:
        if t.get("kind") == "auto":
            return t
    return None


# ---------------------------------------------------------------------------------------------
# Supervision (D11, §12 A9): one budget, every child in its own process group
# ---------------------------------------------------------------------------------------------

class Timeout(Exception):
    def __init__(self, stage, elapsed):
        super().__init__(f"timeout in stage {stage} after {elapsed:.1f}s")
        self.stage = stage
        self.elapsed = elapsed


def _signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # members we cannot signal still exist
    return True


class Supervisor:
    """Runs every external tool as a direct child in a new session (its own process group) and
    tears every tracked group down together — on the run's deadline or on a signal from the
    parent: SIGTERM → TERM_GRACE → SIGKILL → reap (§12 A9). `timeout=None` means no deadline."""

    def __init__(self, timeout=None):
        self.start = time.monotonic()
        self.deadline = (self.start + float(timeout)) if timeout is not None else None
        self.children = {}
        self.stage = None
        self._tearing_down = False
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, self._on_signal)

    def elapsed(self):
        return time.monotonic() - self.start

    def remaining(self):
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def run(self, argv, stage):
        """Run argv to completion under the remaining budget; returns (rc, stdout, stderr) as
        bytes, raises Timeout after tearing every tracked group down. Child output goes to
        unnamed temporary files, so a child that fills a pipe can never wedge the wait."""
        self.stage = stage
        if self.deadline is not None and self.remaining() <= 0:
            self.teardown()
            raise Timeout(stage, self.elapsed())
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        start_new_session=True)
            except OSError as exc:
                return 127, b"", f"{argv[0]}: {exc.strerror or exc}".encode("utf-8")
            self.children[proc.pid] = proc
            try:
                proc.wait(timeout=self.remaining())
            except subprocess.TimeoutExpired:
                self.teardown()
                raise Timeout(stage, self.elapsed())
            finally:
                self.children.pop(proc.pid, None)
            out.seek(0)
            err.seek(0)
            return proc.returncode, out.read(), err.read()

    def teardown(self):
        """SIGTERM every tracked child group, wait the grace, SIGKILL what remains, reap."""
        if self._tearing_down:
            return
        self._tearing_down = True
        try:
            procs = list(self.children.values())
            for p in procs:
                _signal_group(p.pid, signal.SIGTERM)
            end = time.monotonic() + TERM_GRACE
            for p in procs:
                try:
                    p.wait(timeout=max(0.0, end - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
            # the direct child is reaped before the group is re-checked (a zombie member reads
            # as "alive" on macOS); grandchildren may outlive it until the kill below
            while time.monotonic() < end and any(_group_alive(p.pid) for p in procs):
                time.sleep(0.05)
            for p in procs:
                if _group_alive(p.pid):
                    _signal_group(p.pid, signal.SIGKILL)
            for p in procs:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self.children.clear()
        finally:
            self._tearing_down = False

    def _on_signal(self, signum, frame):
        if self._tearing_down:
            return
        self.teardown()
        print(f"ERROR: interrupted by signal {signum}; process groups terminated", file=sys.stderr)
        sys.exit(128 + signum)


def write_error_json(out_dir, stage, elapsed):
    """`mnemon-media-error/1` under --out-dir (tmp + rename); nothing else is touched."""
    doc = {"schema": "mnemon-media-error/1", "error_class": "timeout", "stage": stage,
           "elapsed_s": round(float(elapsed), 1)}
    try:
        os.makedirs(out_dir, exist_ok=True)
        tmp = os.path.join(out_dir, ".error.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, os.path.join(out_dir, "error.json"))
    except OSError as exc:
        print(f"WARNING: could not write error.json under {out_dir}: {exc}", file=sys.stderr)


def _tail(data, n=3):
    text = data.decode("utf-8", errors="replace").strip() if data else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-n:]) if lines else ""


# ---------------------------------------------------------------------------------------------
# yt-dlp, whisper, ffprobe
# ---------------------------------------------------------------------------------------------

def get_youtube_metadata(sup, url):
    """`yt-dlp --dump-single-json`: title, channel, duration, upload_date, language (null when
    the video declares none), description."""
    rc, out, err = sup.run(["yt-dlp", "--dump-single-json", "--no-download", url], "download")
    if rc != 0:
        print(f"ERROR: yt-dlp metadata failed for {url}: {_tail(err)}", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
    except ValueError:
        print(f"ERROR: yt-dlp metadata for {url} is not JSON", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    if not isinstance(data, dict):
        data = {}
    language = data.get("language")
    channel = data.get("channel") or data.get("uploader")
    return {
        "title": data.get("title") or "",
        "channel": str(channel) if channel else None,
        "duration": format_duration(data.get("duration") or 0),
        "duration_seconds": data.get("duration") or 0,
        "upload_date": str(data.get("upload_date")) if data.get("upload_date") else None,
        "language": str(language) if isinstance(language, str) and language.strip() else None,
        "description": (data.get("description") or "")[:500],
    }


def _find_audio(workdir):
    audio_file = os.path.join(workdir, "audio.wav")
    if os.path.exists(audio_file):
        return audio_file
    found = [f for f in sorted(os.listdir(workdir)) if f.startswith("audio.") and f.endswith(AUDIO_EXTS)]
    return os.path.join(workdir, found[0]) if found else None


def _download_audio(sup, url, workdir):
    rc, out, err = sup.run(["yt-dlp", "-f", "ba/b", "-x", "--audio-format", "wav",
                            "-o", f"{workdir}/audio.%(ext)s", url], "download")
    if rc != 0:
        print(f"ERROR: yt-dlp audio download failed for {url}: {_tail(err)}", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    audio_file = _find_audio(workdir)
    if not audio_file:
        print("ERROR: yt-dlp produced no audio file", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    return audio_file


def _whisper_transcribe(sup, audio_file, whisper_model, workdir):
    """Run whisper into workdir and return the transcript text (never deletes anything)."""
    before = set(os.listdir(workdir))
    rc, out, err = sup.run(["whisper", audio_file, "--model", whisper_model,
                            "--output_format", "txt", "--output_dir", workdir], "transcribe")
    if rc != 0:
        print(f"ERROR: whisper failed (exit {rc}): {_tail(err)}", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    new_txt = [f for f in sorted(os.listdir(workdir)) if f.endswith(".txt") and f not in before]
    txt_files = new_txt or [f for f in sorted(os.listdir(workdir)) if f.endswith(".txt") and f != "transcript.txt"]
    if not txt_files:
        print("ERROR: Whisper produced no output", file=sys.stderr)
        sys.exit(EXIT_FAILED)
    with open(os.path.join(workdir, txt_files[0]), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _probe_duration(sup, path):
    """ffprobe's duration in seconds, or None when it cannot be read."""
    rc, out, err = sup.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], "probe")
    if rc != 0:
        return None
    text = out.decode("utf-8", errors="replace").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# Captions — the hidden child, and its driver
# ---------------------------------------------------------------------------------------------

def _list_transcripts(api_cls, video_id):
    """v1.x: an instance with `.list()`; v0.x: the class method `list_transcripts`."""
    if hasattr(api_cls, "list"):
        return api_cls().list(video_id)
    return api_cls.list_transcripts(video_id)


def _entry_text(e):
    # v1.x returns FetchedTranscriptSnippet objects; v0.x returns dicts
    return e.text if hasattr(e, "text") else e["text"]


def cmd_captions(argv):
    """`_captions <video_id> --out <file> [--original-language L]` — runs in its own process
    under the parent's supervision; everything the library does (HTTP included) is killable."""
    ap = argparse.ArgumentParser(prog="media-extract.py _captions")
    ap.add_argument("video_id")
    ap.add_argument("--out", required=True)
    ap.add_argument("--original-language", default=None)
    args = ap.parse_args(argv)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        print(f"ERROR: youtube_transcript_api is not importable ({exc}); pip install youtube-transcript-api",
              file=sys.stderr)
        return EXIT_ENVIRONMENT
    try:
        objs = list(_list_transcripts(YouTubeTranscriptApi, args.video_id))
    except Exception as exc:  # the library raises its own hierarchy; every one means "no captions"
        print(f"ERROR: cannot list captions for {args.video_id}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    tracks = list_tracks(objs)
    chosen = choose_track(tracks, args.original_language)
    if chosen is None:
        print(f"ERROR: no caption track for {args.video_id} ({len(tracks)} listed)", file=sys.stderr)
        return EXIT_FAILED
    obj = objs[tracks.index(chosen)]
    try:
        entries = obj.fetch()
        text = "\n".join(_entry_text(e) for e in entries)
    except Exception as exc:
        print(f"ERROR: caption fetch failed for {args.video_id} ({chosen['lang']}, {chosen['kind']}): {exc}",
              file=sys.stderr)
        return EXIT_FAILED
    with open(args.out, "wb") as fh:
        fh.write(text.encode("utf-8"))
    print(json.dumps({"language": chosen["lang"], "track": chosen,
                      "method": f"captions:{chosen['lang']}:{chosen['kind']}"}, ensure_ascii=False))
    return 0


def fetch_captions(sup, video_id, original_language, out_file):
    """Run the captions child under supervision. Returns (info, note): info is the child's JSON
    (language, track, method) with the transcript in out_file, or None with a one-line note."""
    argv = [sys.executable, SCRIPT, "_captions", video_id, "--out", out_file]
    if original_language:
        argv += ["--original-language", original_language]
    rc, out, err = sup.run(argv, "captions")
    if rc != 0:
        return None, _tail(err, 1) or f"captions helper exit {rc}"
    try:
        info = json.loads(out.decode("utf-8"))
    except ValueError:
        return None, "captions helper printed no JSON"
    if not isinstance(info, dict) or not os.path.isfile(out_file):
        return None, "captions helper wrote no transcript"
    return info, None


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------

def _write_out_dir(out_dir, transcript, media):
    """transcript.txt + media.json under out_dir (created if needed; nothing removed)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "transcript.txt"), "wb") as fh:
        fh.write(transcript.encode("utf-8"))
    doc = {"schema": "mnemon-media/1"}
    doc.update(media)
    with open(os.path.join(out_dir, "media.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def media_record(method, audio_path, title, duration, upload_date=None, video_id=None,
                 language=None, original_language=None, track=None, channel=None):
    """The flat `mnemon-media/1` record (D12): the phase-1 keys first, then the phase-2 ones."""
    return {
        "method": method,
        "audio_path": audio_path,
        "title": title,
        "duration": duration,
        "upload_date": upload_date,
        "video_id": video_id,
        "language": language,
        "original_language": original_language,
        "track": track,
        "channel": channel,
    }


def cmd_youtube(args, sup):
    check_dependency("yt-dlp", "brew install yt-dlp")

    video_id = extract_video_id(args.url)
    meta = get_youtube_metadata(sup, args.url)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else None
    work = tempfile.mkdtemp(prefix="kg-yt-")
    try:
        caps_file = os.path.join(work, "captions.txt")
        info, note = fetch_captions(sup, video_id, meta["language"], caps_file)
        if info is not None:
            with open(caps_file, encoding="utf-8", errors="replace") as fh:
                transcript = fh.read()
            language, track, method, audio_path = info.get("language"), info.get("track"), info["method"], None
        else:
            if args.no_whisper:
                print(f"ERROR: No captions and --no-whisper set: {note}", file=sys.stderr)
                sys.exit(EXIT_FAILED)
            print(f"NOTE: captions unavailable ({note}); transcribing with whisper", file=sys.stderr)
            check_dependency("whisper", "pip install openai-whisper")
            if meta["duration_seconds"] and meta["duration_seconds"] > 3600:
                print(f"WARNING: Video is {meta['duration']} — whisper transcription will be slow", file=sys.stderr)
            audio_dir = out_dir or work
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            audio_path = _download_audio(sup, args.url, audio_dir)
            transcript = _whisper_transcribe(sup, audio_path, args.whisper_model, audio_dir)
            language, track, method = None, None, f"whisper:{args.whisper_model}"
            if not out_dir:
                audio_path = None

        if out_dir:
            _write_out_dir(out_dir, transcript, media_record(
                method, audio_path, meta["title"], meta["duration"], upload_date=meta["upload_date"],
                video_id=video_id, language=language, original_language=meta["language"], track=track,
                channel=meta["channel"]))
            return

        print("--- YouTube Source Metadata ---")
        print(f"Title: {meta['title']}")
        print(f"Channel: {meta['channel'] or ''}")
        print(f"Duration: {meta['duration']}")
        print(f"Published: {meta['upload_date'] or ''}")
        print(f"URL: {args.url}")
        print(f"Language: {language or 'auto-detected'}")
        print(f"Original language: {meta['language'] or 'unknown'}")
        print(f"Transcript method: {method.split(':', 1)[0]}")
        print(f"Description: {meta['description']}")
        print("--- Transcript ---")
        print(transcript)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cmd_audio(args, sup):
    check_dependency("whisper", "pip install openai-whisper")

    source = args.url or args.file
    is_url = bool(args.url)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else None
    keep = out_dir is not None
    if keep:
        os.makedirs(out_dir, exist_ok=True)
    tmpdir = out_dir or tempfile.mkdtemp(prefix="kg-audio-")

    try:
        if is_url:
            check_dependency("yt-dlp", "brew install yt-dlp")
            audio_path = _download_audio(sup, source, tmpdir)
        else:
            audio_path = os.path.abspath(source)

        duration = "unknown"
        dur_seconds = _probe_duration(sup, audio_path)
        if dur_seconds is None and not is_url:
            print(f"ERROR: Not a valid audio file: {source}", file=sys.stderr)
            sys.exit(EXIT_FAILED)
        if dur_seconds is not None:
            duration = format_duration(dur_seconds)
            if dur_seconds > 3600:
                print(f"WARNING: Audio is {duration} — whisper transcription will be slow", file=sys.stderr)

        transcript = _whisper_transcribe(sup, audio_path, args.whisper_model, tmpdir)
        if not transcript.strip():
            print("ERROR: Empty transcript", file=sys.stderr)
            sys.exit(EXIT_FAILED)

        title = args.title or os.path.splitext(os.path.basename(source))[0]
        if keep:
            _write_out_dir(out_dir, transcript, media_record(
                f"whisper:{args.whisper_model}", audio_path, title, duration))
            return

        print("--- Audio Source Metadata ---")
        print(f"Title: {title}")
        print(f"URL: {source if is_url else 'local file'}")
        print(f"File: {os.path.basename(source)}")
        print(f"Duration: {duration}")
        print("Language: auto-detected")
        print(f"Transcript method: whisper-{args.whisper_model}")
        print("--- Transcript ---")
        print(transcript)
    finally:
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)


def drop_vault_lock_descriptor() -> None:
    """§12 A1: this helper never mutates the vault, so it must not keep the vault lock alive through
    its own subprocesses (yt-dlp, ffmpeg, whisper, the captions child): close the inherited
    descriptor and scrub the MNEMON_VAULT_LOCK_* variables before anything is spawned. The
    gateway keeps its own descriptor."""
    raw = os.environ.pop("MNEMON_VAULT_LOCK_FD", None)
    for key in [k for k in os.environ if k.startswith("MNEMON_VAULT_LOCK_")]:
        os.environ.pop(key, None)
    if raw:
        try:
            os.close(int(raw))
        except (ValueError, OSError):
            pass


def _timeout_arg(value):
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--timeout must be a number of seconds, got {value!r}")
    if f <= 0 or f != f:
        raise argparse.ArgumentTypeError(f"--timeout must be a positive number of seconds, got {value!r}")
    return f


def main():
    drop_vault_lock_descriptor()
    if len(sys.argv) > 1 and sys.argv[1] == "_captions":
        sys.exit(cmd_captions(sys.argv[2:]))

    parser = argparse.ArgumentParser(description="Media extraction for knowledge-gateway")
    sub = parser.add_subparsers(dest="command", required=True)

    yt = sub.add_parser("youtube", help="Extract YouTube video transcript + metadata")
    yt.add_argument("url", help="YouTube video URL")
    yt.add_argument("--whisper-model", default="large-v3", help="Whisper model (default: large-v3)")
    yt.add_argument("--no-whisper", action="store_true", help="Disable whisper fallback")
    yt.add_argument("--out-dir", default=None,
                    help="Write transcript.txt + media.json here (mnemon-media/1) instead of stdout; nothing under it is deleted")
    yt.add_argument("--timeout", type=_timeout_arg, default=None,
                    help="One budget in seconds for the whole run (captions, yt-dlp, whisper); on expiry every "
                         "tool is killed with its process group, error.json is written under --out-dir, exit 124")

    au = sub.add_parser("audio", help="Extract audio transcript via whisper")
    au.add_argument("--url", default=None, help="Audio URL to download")
    au.add_argument("--file", default=None, help="Local audio file path")
    au.add_argument("--title", default=None, help="Override title")
    au.add_argument("--whisper-model", default="large-v3", help="Whisper model (default: large-v3)")
    au.add_argument("--out-dir", default=None,
                    help="Write transcript.txt + media.json here (mnemon-media/1) instead of stdout; nothing under it is deleted")
    au.add_argument("--timeout", type=_timeout_arg, default=None,
                    help="One budget in seconds for the whole run (yt-dlp, ffprobe, whisper); exit 124 on expiry")

    args = parser.parse_args()
    if args.command == "audio" and not args.url and not args.file:
        print("ERROR: --url or --file required for audio", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    sup = Supervisor(args.timeout)
    try:
        if args.command == "youtube":
            cmd_youtube(args, sup)
        else:
            cmd_audio(args, sup)
    except Timeout as t:
        if args.out_dir:
            write_error_json(os.path.abspath(args.out_dir), t.stage, t.elapsed)
        print(f"ERROR: timeout after {t.elapsed:.1f}s in stage {t.stage} (budget {args.timeout:g}s); "
              f"process groups terminated", file=sys.stderr)
        sys.exit(EXIT_TIMEOUT)


if __name__ == "__main__":
    main()
