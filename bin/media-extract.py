#!/usr/bin/env python3
"""Media extraction helper for knowledge-gateway.
Extracts transcripts and metadata from YouTube videos and audio files."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile


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


def get_youtube_transcript(video_id, no_whisper=False, whisper_model="large-v3",
                           url=None, duration_seconds=0):
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
        for lang in ["en", "ru"]:
            try:
                transcript_obj = ts.find_manually_created_transcript([lang])
                break
            except Exception:
                pass
        if not transcript_obj:
            for lang in ["en", "ru"]:
                try:
                    transcript_obj = ts.find_generated_transcript([lang])
                    break
                except Exception:
                    pass
        if not transcript_obj:
            transcript_obj = list(ts)[0]

        entries = transcript_obj.fetch()
        # v1.x returns FetchedTranscriptSnippet objects; v0.x returns dicts
        def get_text(e):
            return e.text if hasattr(e, "text") else e["text"]
        text = "\n".join(get_text(e) for e in entries)
        return text, transcript_obj.language_code, "captions"
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

    tmpdir = tempfile.mkdtemp(prefix="kg-yt-")
    signal.signal(signal.SIGINT,
                  lambda *_: (shutil.rmtree(tmpdir, ignore_errors=True), sys.exit(130)))
    try:
        subprocess.run(
            ["yt-dlp", "-f", "ba/b", "-x", "--audio-format", "wav",
             "-o", f"{tmpdir}/audio.%(ext)s", url],
            capture_output=True, check=True
        )
        audio_file = os.path.join(tmpdir, "audio.wav")
        if not os.path.exists(audio_file):
            wavs = [f for f in os.listdir(tmpdir) if f.endswith((".wav", ".opus", ".m4a", ".mp3"))]
            if wavs:
                audio_file = os.path.join(tmpdir, wavs[0])
            else:
                print("ERROR: yt-dlp produced no audio file", file=sys.stderr)
                sys.exit(1)

        subprocess.run(
            ["whisper", audio_file, "--model", whisper_model,
             "--output_format", "txt", "--output_dir", tmpdir],
            capture_output=True, check=True
        )
        txt_files = [f for f in os.listdir(tmpdir) if f.endswith(".txt")]
        if not txt_files:
            print("ERROR: Whisper produced no output", file=sys.stderr)
            sys.exit(1)
        text = open(os.path.join(tmpdir, txt_files[0])).read()
        return text, "auto", "whisper"
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Whisper fallback failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def cmd_youtube(args):
    check_dependency("yt-dlp", "brew install yt-dlp")

    video_id = extract_video_id(args.url)
    meta = get_youtube_metadata(args.url)
    transcript, lang, method = get_youtube_transcript(
        video_id,
        no_whisper=args.no_whisper,
        whisper_model=args.whisper_model,
        url=args.url,
        duration_seconds=meta["duration_seconds"]
    )

    print("--- YouTube Source Metadata ---")
    print(f"Title: {meta['title']}")
    print(f"Channel: {meta['channel']}")
    print(f"Duration: {meta['duration']}")
    print(f"Published: {meta['upload_date']}")
    print(f"URL: {args.url}")
    print(f"Language: {lang}")
    print(f"Transcript method: {method}")
    print(f"Description: {meta['description']}")
    print("--- Transcript ---")
    print(transcript)


def cmd_audio(args):
    check_dependency("whisper", "pip install openai-whisper")

    source = args.url or args.file
    is_url = bool(args.url)
    tmpdir = tempfile.mkdtemp(prefix="kg-audio-")
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
            audio_path = os.path.join(tmpdir, "audio.wav")
            if not os.path.exists(audio_path):
                wavs = [f for f in os.listdir(tmpdir)
                        if f.endswith((".wav", ".opus", ".m4a", ".mp3"))]
                audio_path = os.path.join(tmpdir, wavs[0]) if wavs else None
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
            audio_path = source

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
        subprocess.run(
            ["whisper", audio_path, "--model", args.whisper_model,
             "--output_format", "txt", "--output_dir", tmpdir],
            capture_output=True, check=True
        )
        txt_files = [f for f in os.listdir(tmpdir) if f.endswith(".txt")]
        if not txt_files:
            print("ERROR: Whisper produced no output", file=sys.stderr)
            sys.exit(1)
        transcript = open(os.path.join(tmpdir, txt_files[0])).read()

        if not transcript.strip():
            print("ERROR: Empty transcript", file=sys.stderr)
            sys.exit(1)

        title = args.title or os.path.splitext(os.path.basename(source))[0]
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
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Media extraction for knowledge-gateway")
    sub = parser.add_subparsers(dest="command", required=True)

    yt = sub.add_parser("youtube", help="Extract YouTube video transcript + metadata")
    yt.add_argument("url", help="YouTube video URL")
    yt.add_argument("--whisper-model", default="large-v3", help="Whisper model (default: large-v3)")
    yt.add_argument("--no-whisper", action="store_true", help="Disable whisper fallback")

    au = sub.add_parser("audio", help="Extract audio transcript via whisper")
    au.add_argument("--url", default=None, help="Audio URL to download")
    au.add_argument("--file", default=None, help="Local audio file path")
    au.add_argument("--title", default=None, help="Override title")
    au.add_argument("--whisper-model", default="large-v3", help="Whisper model (default: large-v3)")

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
