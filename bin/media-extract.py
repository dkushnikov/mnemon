#!/usr/bin/env python3
"""Media extraction helper for knowledge-gateway.
Extracts transcripts and metadata from YouTube videos and audio files.

Two output contracts (docs/specs/2026-10-15-capture-contracts.md §6):

  media-extract.py youtube <url> [--whisper-model M] [--no-whisper]
  media-extract.py audio (--url U | --file F) [--title T] [--whisper-model M]
      → the legacy stdout contract: a metadata header followed by the transcript.

  … --out-dir <dir>
      → writes <dir>/transcript.txt (the transcript bytes the gateway uses as capture-text)
        and <dir>/media.json (`mnemon-media/1`):
          {"schema": "mnemon-media/1",
           "method": "captions:<lang>:<manual|auto>" | "whisper:<model>",
           "audio_path": "<the audio that was transcribed, kept under <dir>>" | null,
           "title": …, "duration": "H:MM:SS", "upload_date": …, "video_id": …}
        Everything downloaded or produced stays under <dir> — nothing under --out-dir is ever
        deleted by this script (the gateway archives `audio_path` and merges the metadata).
        Stdout stays empty in this mode.

Division of labour with the gateway (spec §12 A7): for the `audio` origin the gateway acquires
the audio itself (curl by URL, a copy of the given file) and publishes it as the raw BEFORE
calling `audio --file <published raw> --out-dir …` for the transcription only — `audio --url`
stays for callers outside the gateway. For `youtube` the captions path acquires no audio; the
whisper fallback downloads and transcribes in this one run and reports the audio in
`audio_path`, which the gateway publishes as the raw afterwards — a documented phase-1
limitation: a crash between the download and the transcription loses only that unrecorded
download, which is refetched.

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

AUDIO_EXTS = (".wav", ".opus", ".m4a", ".mp3", ".webm", ".ogg", ".flac", ".aac")


def check_dependency(cmd, install_hint):
    if not shutil.which(cmd):
        print(f"ERROR: {cmd} not found. Install: {install_hint}", file=sys.stderr)
        sys.exit(2)


def format_duration(seconds):
    s = int(seconds)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})', url)
    if not m:
        print(f"ERROR: Cannot extract video ID from {url}", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def get_youtube_metadata(url):
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: yt-dlp metadata failed for {url}: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    return {
        "title": data.get("title", ""),
        "channel": data.get("channel", ""),
        "duration": format_duration(data.get("duration", 0)),
        "duration_seconds": data.get("duration", 0),
        "upload_date": data.get("upload_date", ""),
        "description": data.get("description", "")[:500],
    }


def _find_audio(workdir):
    audio_file = os.path.join(workdir, "audio.wav")
    if os.path.exists(audio_file):
        return audio_file
    found = [f for f in sorted(os.listdir(workdir)) if f.startswith("audio.") and f.endswith(AUDIO_EXTS)]
    return os.path.join(workdir, found[0]) if found else None


def _whisper_transcribe(audio_file, whisper_model, workdir):
    """Run whisper into workdir and return the transcript text (never deletes anything)."""
    before = set(os.listdir(workdir))
    subprocess.run(
        ["whisper", audio_file, "--model", whisper_model,
         "--output_format", "txt", "--output_dir", workdir],
        capture_output=True, check=True
    )
    new_txt = [f for f in sorted(os.listdir(workdir)) if f.endswith(".txt") and f not in before]
    txt_files = new_txt or [f for f in sorted(os.listdir(workdir)) if f.endswith(".txt") and f != "transcript.txt"]
    if not txt_files:
        print("ERROR: Whisper produced no output", file=sys.stderr)
        sys.exit(1)
    with open(os.path.join(workdir, txt_files[0]), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def get_youtube_transcript(video_id, no_whisper=False, whisper_model="large-v3",
                           url=None, duration_seconds=0, workdir=None):
    """Return (text, language, method, audio_path).

    method is the §6 form: `captions:<lang>:<manual|auto>` or `whisper:<model>`; audio_path
    is the downloaded audio when whisper ran (under workdir, or in a temp dir that is removed
    unless workdir was given), else None.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    # Try captions first
    try:
        # v1.x uses instance-based API; v0.x used class methods
        try:
            api = YouTubeTranscriptApi()
            ts = api.list(video_id)
        except TypeError:
            ts = YouTubeTranscriptApi.list_transcripts(video_id)

        transcript_obj = None
        kind = None
        for lang in ["en", "ru"]:
            try:
                transcript_obj = ts.find_manually_created_transcript([lang])
                kind = "manual"
                break
            except Exception:
                pass
        if not transcript_obj:
            for lang in ["en", "ru"]:
                try:
                    transcript_obj = ts.find_generated_transcript([lang])
                    kind = "auto"
                    break
                except Exception:
                    pass
        if not transcript_obj:
            transcript_obj = list(ts)[0]
            kind = "auto" if getattr(transcript_obj, "is_generated", False) else "manual"

        entries = transcript_obj.fetch()
        # v1.x returns FetchedTranscriptSnippet objects; v0.x returns dicts
        def get_text(e):
            return e.text if hasattr(e, "text") else e["text"]
        text = "\n".join(get_text(e) for e in entries)
        lang_code = transcript_obj.language_code
        return text, lang_code, f"captions:{lang_code}:{kind}", None
    except Exception as e:
        if no_whisper:
            print(f"ERROR: No captions and --no-whisper set: {e}", file=sys.stderr)
            sys.exit(1)

    # Whisper fallback
    check_dependency("whisper", "pip install openai-whisper")
    if duration_seconds > 3600:
        print(
            f"WARNING: Video is {format_duration(duration_seconds)} — "
            f"whisper transcription will be slow",
            file=sys.stderr
        )

    keep = workdir is not None
    tmpdir = workdir or tempfile.mkdtemp(prefix="kg-yt-")
    if not keep:
        signal.signal(signal.SIGINT,
                      lambda *_: (shutil.rmtree(tmpdir, ignore_errors=True), sys.exit(130)))
    try:
        subprocess.run(
            ["yt-dlp", "-f", "ba/b", "-x", "--audio-format", "wav",
             "-o", f"{tmpdir}/audio.%(ext)s", url],
            capture_output=True, check=True
        )
        audio_file = _find_audio(tmpdir)
        if not audio_file:
            print("ERROR: yt-dlp produced no audio file", file=sys.stderr)
            sys.exit(1)
        text = _whisper_transcribe(audio_file, whisper_model, tmpdir)
        return text, "auto", f"whisper:{whisper_model}", (audio_file if keep else None)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Whisper fallback failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _write_out_dir(out_dir, transcript, media):
    """transcript.txt + media.json under out_dir (created if needed; nothing removed)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "transcript.txt"), "wb") as fh:
        fh.write(transcript.encode("utf-8"))
    media = dict(media)
    media["schema"] = "mnemon-media/1"
    with open(os.path.join(out_dir, "media.json"), "w", encoding="utf-8") as fh:
        json.dump(media, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def cmd_youtube(args):
    check_dependency("yt-dlp", "brew install yt-dlp")

    video_id = extract_video_id(args.url)
    meta = get_youtube_metadata(args.url)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else None
    transcript, lang, method, audio_path = get_youtube_transcript(
        video_id,
        no_whisper=args.no_whisper,
        whisper_model=args.whisper_model,
        url=args.url,
        duration_seconds=meta["duration_seconds"],
        workdir=out_dir,
    )

    if out_dir:
        _write_out_dir(out_dir, transcript, {
            "method": method,
            "audio_path": audio_path,
            "title": meta["title"],
            "channel": meta["channel"],
            "duration": meta["duration"],
            "duration_seconds": meta["duration_seconds"],
            "upload_date": meta["upload_date"],
            "video_id": video_id,
            "language": lang,
            "url": args.url,
        })
        return

    print("--- YouTube Source Metadata ---")
    print(f"Title: {meta['title']}")
    print(f"Channel: {meta['channel']}")
    print(f"Duration: {meta['duration']}")
    print(f"Published: {meta['upload_date']}")
    print(f"URL: {args.url}")
    print(f"Language: {lang}")
    print(f"Transcript method: {method.split(':', 1)[0]}")
    print(f"Description: {meta['description']}")
    print("--- Transcript ---")
    print(transcript)


def cmd_audio(args):
    check_dependency("whisper", "pip install openai-whisper")

    source = args.url or args.file
    is_url = bool(args.url)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else None
    keep = out_dir is not None
    if keep:
        os.makedirs(out_dir, exist_ok=True)
    tmpdir = out_dir or tempfile.mkdtemp(prefix="kg-audio-")
    if not keep:
        signal.signal(signal.SIGINT,
                      lambda *_: (shutil.rmtree(tmpdir, ignore_errors=True), sys.exit(130)))

    try:
        if is_url:
            check_dependency("yt-dlp", "brew install yt-dlp")
            subprocess.run(
                ["yt-dlp", "-f", "ba/b", "-x", "--audio-format", "wav",
                 "-o", f"{tmpdir}/audio.%(ext)s", source],
                capture_output=True, check=True
            )
            audio_path = _find_audio(tmpdir)
            if not audio_path or not os.path.exists(audio_path):
                print(f"ERROR: Cannot download audio from {source}", file=sys.stderr)
                sys.exit(1)
        else:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", source],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"ERROR: Not a valid audio file: {source}", file=sys.stderr)
                sys.exit(1)
            audio_path = os.path.abspath(source)

        # Get duration
        duration = "unknown"
        dur_result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", audio_path],
            capture_output=True, text=True
        )
        if dur_result.returncode == 0 and dur_result.stdout.strip():
            dur_seconds = float(dur_result.stdout.strip())
            duration = format_duration(dur_seconds)
            if dur_seconds > 3600:
                print(
                    f"WARNING: Audio is {duration} — whisper transcription will be slow",
                    file=sys.stderr
                )

        # Transcribe
        transcript = _whisper_transcribe(audio_path, args.whisper_model, tmpdir)

        if not transcript.strip():
            print("ERROR: Empty transcript", file=sys.stderr)
            sys.exit(1)

        title = args.title or os.path.splitext(os.path.basename(source))[0]
        if keep:
            _write_out_dir(out_dir, transcript, {
                "method": f"whisper:{args.whisper_model}",
                "audio_path": audio_path,
                "title": title,
                "duration": duration,
                "upload_date": None,
                "video_id": None,
                "url": source if is_url else None,
            })
            return

        print("--- Audio Source Metadata ---")
        print(f"Title: {title}")
        print(f"URL: {source if is_url else 'local file'}")
        print(f"File: {os.path.basename(source)}")
        print(f"Duration: {duration}")
        print(f"Language: auto-detected")
        print(f"Transcript method: whisper-{args.whisper_model}")
        print("--- Transcript ---")
        print(transcript)

    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)


def drop_vault_lock_descriptor() -> None:
    """§12 A1: this helper never mutates the vault, so it must not keep the vault lock alive through
    its own subprocesses (yt-dlp, ffmpeg, whisper): close the inherited descriptor and scrub the
    MNEMON_VAULT_LOCK_* variables before anything is spawned. The gateway keeps its own descriptor."""
    raw = os.environ.pop("MNEMON_VAULT_LOCK_FD", None)
    for key in [k for k in os.environ if k.startswith("MNEMON_VAULT_LOCK_")]:
        os.environ.pop(key, None)
    if raw:
        try:
            os.close(int(raw))
        except (ValueError, OSError):
            pass


def main():
    drop_vault_lock_descriptor()
    parser = argparse.ArgumentParser(description="Media extraction for knowledge-gateway")
    sub = parser.add_subparsers(dest="command", required=True)

    yt = sub.add_parser("youtube", help="Extract YouTube video transcript + metadata")
    yt.add_argument("url", help="YouTube video URL")
    yt.add_argument("--whisper-model", default="large-v3", help="Whisper model (default: large-v3)")
    yt.add_argument("--no-whisper", action="store_true", help="Disable whisper fallback")
    yt.add_argument("--out-dir", default=None,
                    help="Write transcript.txt + media.json here (mnemon-media/1) instead of stdout; nothing under it is deleted")

    au = sub.add_parser("audio", help="Extract audio transcript via whisper")
    au.add_argument("--url", default=None, help="Audio URL to download")
    au.add_argument("--file", default=None, help="Local audio file path")
    au.add_argument("--title", default=None, help="Override title")
    au.add_argument("--whisper-model", default="large-v3", help="Whisper model (default: large-v3)")
    au.add_argument("--out-dir", default=None,
                    help="Write transcript.txt + media.json here (mnemon-media/1) instead of stdout; nothing under it is deleted")

    args = parser.parse_args()
    if args.command == "youtube":
        cmd_youtube(args)
    elif args.command == "audio":
        if not args.url and not args.file:
            print("ERROR: --url or --file required for audio", file=sys.stderr)
            sys.exit(1)
        cmd_audio(args)


if __name__ == "__main__":
    main()
