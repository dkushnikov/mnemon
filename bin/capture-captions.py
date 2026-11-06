#!/usr/bin/env python3
"""capture-captions.py — the capture-text of a retained caption raw (the browser lane's media form).

    capture-captions.py <raw.vtt> --url <url> --out <file> --report <json> [--media <media.json>]

Converts a WebVTT caption track — the raw a producer acquired outside the fetch chain (`mnemon browser-lane
youtube …`) and the gateway published as `<revision>.vtt` (`raw_kind: captions`) — into capture-text through
`bin/capture/captions.py`: the cues, YouTube's rolling repeats removed, paragraphs of speech, one `## <title> (m:ss)`
heading per chapter of the bundle's `media.json`; and checks the text against the raw it came from (every
eligible caption line present as a token sequence, every chapter heading present). The report is a
`mnemon-capture-text/2` document (the key set of tests/fixtures/capture/reports/text.json) with `converter:
vtt-text`, `method: browser+vtt-text`, `acquisition: ok` — the gateway reads it through `cap_text_fields` exactly
as it reads a page's conversion report. It never fetches, never runs a tool.

Exit 0 · 1 when the track holds no text (`error_class: convert`, not retryable — the same bytes convert the same
way) or the raw is not UTF-8 (`invalid-utf8`) · 2 usage. The conversion is linear in the raw's size and needs no
deadline owner: a caption track is a few hundred kilobytes at most. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import captions, report as reportmod  # noqa: E402

EXIT_FAILED = 1
EXIT_USAGE = 2


def _failed_report(url: str, error_class: str, error: str) -> dict:
    _, rep = captions.convert(b"", None, url)
    rep.update({"completeness": "unknown", "error_class": error_class, "error": error, "retryable": False,
                "stage": "convert", "candidates": [], "converter": captions.CONVERTER})
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(prog="capture-captions.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("raw", help="the retained caption raw (WebVTT)")
    ap.add_argument("--url", required=True, help="the capture's URL (recorded in the report)")
    ap.add_argument("--out", required=True, help="where the capture-text goes (written atomically)")
    ap.add_argument("--report", required=True, help="where the mnemon-capture-text/2 report goes")
    ap.add_argument("--media", default=None, help="the bundle's media.json (mnemon-media/1): chapters, title, channel, duration")
    args = ap.parse_args()

    try:
        with open(args.raw, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        print("ERROR: cannot read %s: %s" % (args.raw, exc), file=sys.stderr)
        return EXIT_USAGE
    media = None
    if args.media:
        try:
            with open(args.media, "r", encoding="utf-8") as fh:
                media = json.load(fh)
        except (OSError, ValueError) as exc:
            print("WARNING: media record %s unreadable (%s); converting without chapters" % (args.media, exc), file=sys.stderr)
            media = None
        if media is not None and not isinstance(media, dict):
            media = None

    try:
        text, rep = captions.convert(raw, media, args.url)
    except UnicodeDecodeError as exc:
        reportmod.write_report(args.report, _failed_report(args.url, "invalid-utf8", "the caption raw is not valid UTF-8: %s" % exc))
        print("ERROR: the caption raw is not valid UTF-8", file=sys.stderr)
        return EXIT_FAILED
    reportmod.write_report(args.report, rep)
    if not text:
        print("ERROR: the caption track holds no text (no cue survived parsing)", file=sys.stderr)
        return EXIT_FAILED
    reportmod.write_bytes(args.out, text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
