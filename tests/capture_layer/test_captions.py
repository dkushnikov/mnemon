"""The browser lane's converter (capture layer A13): `capture/captions.py` on the synthetic YouTube-shaped track,
and the `capture-captions.py` CLI with its `mnemon-capture-text/2` report against the frozen text fixture."""
from __future__ import annotations

import json
import os
import unittest

import tempfile

from _helpers import BIN, FIX, assert_same_keys, expected_shapes, run_cli

from capture import captions as C

MEDIA_FIX = os.path.join(FIX, "media")
VTT = os.path.join(MEDIA_FIX, "captions-sample.en-orig.vtt")
CLI = os.path.join(BIN, "capture-captions.py")
URL = "https://www.youtube.com/watch?v=abc123xyz00"
MEDIA = {
    "schema": "mnemon-media/1", "method": "captions:en:auto", "audio_path": None, "title": "An example talk about agents",
    "duration": "0:01:40", "upload_date": "20260901", "video_id": "abc123xyz00", "language": "en", "original_language": "en-US",
    "track": {"lang": "en", "kind": "auto", "orig": True}, "channel": "Example Channel",
    "chapters": [{"start": 0.0, "end": 52.0, "title": "Intro"}, {"start": 52.0, "end": 94.0, "title": "Why personal agents took off"},
                 {"start": 94.0, "end": 100.0, "title": "Why that breaks in finance"}],
    "captions_path": "abc123xyz00.en-orig.vtt", "captions_code": "en-orig",
    "producer": {"lane": "browser", "browser": "safari", "tool": "yt-dlp", "tool_version": "2026.10.26", "acquired_at": "2026-11-05T00:00:00Z"},
}


def read_vtt() -> str:
    with open(VTT, "r", encoding="utf-8") as fh:
        return fh.read()


class Parse(unittest.TestCase):
    def test_cues_in_order_with_tags_and_entities_removed(self):
        cues = C.parse_vtt(read_vtt())
        self.assertEqual(len(cues), 16)
        self.assertEqual(cues[0].start, 0.16)
        self.assertEqual(cues[0].end, 3.35)
        self.assertEqual(cues[0].text, "[Music] >> I was previously an analyst")   # the timing tags and <c> spans gone, &gt;&gt; decoded
        self.assertEqual(cues[2].text, "[Music] >> I was previously an analyst\nin a previous life at a fund")
        self.assertIn("proactivity and integrations that matter", cues[12].text)   # <v Speaker>, <i>, <b> stripped

    def test_header_note_identifier_crlf_bom_and_srt(self):
        doc = "﻿WEBVTT\r\n\r\nNOTE a note\r\nmore note\r\n\r\n1\r\n00:00:01.000 --> 00:00:02.000\r\nfirst line\r\n\r\nSTYLE\r\n::cue { color: red }\r\n\r\n00:01.500 --> 00:02.000 position:10%\r\nsecond <00:00:01.700>line\r\n"
        cues = C.parse_vtt(doc)
        self.assertEqual([(c.start, c.end, c.text) for c in cues], [(1.0, 2.0, "first line"), (1.5, 2.0, "second line")])
        srt = "1\n00:00:01,000 --> 00:00:02,500\nsrt line one\n\n2\n00:00:02,500 --> 00:00:02,000\nend before start\n"
        cues = C.parse_vtt(srt)
        self.assertEqual([(c.start, c.end, c.text) for c in cues], [(1.0, 2.5, "srt line one"), (2.5, 2.5, "end before start")])

    def test_malformed_and_empty_cues_are_skipped(self):
        doc = "WEBVTT\n\nnot a timing line\npayload\n\n00:00:01.000 --> 00:00:02.000\n<c></c>\n\n00:00:03.000 --> 00:00:04.000\nkept\n"
        self.assertEqual([c.text for c in C.parse_vtt(doc)], ["kept"])
        self.assertEqual(C.parse_vtt(""), [])
        self.assertEqual(C.parse_vtt("WEBVTT\n"), [])

    def test_timestamps(self):
        self.assertEqual(C.parse_timestamp("01:02:03.004"), 3723.004)
        self.assertEqual(C.parse_timestamp("02:03.004"), 123.004)
        self.assertEqual(C.format_timestamp(52), "0:52")
        self.assertEqual(C.format_timestamp(3723.9), "1:02:03")
        with self.assertRaises(ValueError):
            C.parse_timestamp("1:2")


class Dedupe(unittest.TestCase):
    def test_rolling_repeats_and_redisplays_collapse_to_one_line_each(self):
        lines = C.dedupe(C.parse_vtt(read_vtt()))
        texts = [ln.text for ln in lines]
        self.assertEqual(texts, [
            "[Music] >> I was previously an analyst",
            "in a previous life at a fund",
            "spent way too much time in spreadsheets",
            "so I decided to build tools",
            "why did personal agents take off",
            "three answers reliable background agents",
            "proactivity and integrations that matter",
            "yeah",
            "but none of it translates to institutional finance",
        ])
        # each line keeps the span of the cue that introduced it; a 10 ms re-display extends the previous line's end
        self.assertEqual((lines[0].start, lines[0].end), (0.16, 3.36))
        self.assertEqual(lines[4].start, 52.0)

    def test_a_genuine_repeat_outside_the_window_is_kept(self):
        cues = [C.Cue(0, 1, "one"), C.Cue(1, 2, "two"), C.Cue(2, 3, "three"), C.Cue(3, 4, "four"), C.Cue(4, 5, "one")]
        self.assertEqual([ln.text for ln in C.dedupe(cues)], ["one", "two", "three", "four", "one"])
        cues = [C.Cue(0, 1, "one"), C.Cue(1, 2, "ONE"), C.Cue(2, 3, "o n e")]   # the key is case- and whitespace-insensitive
        self.assertEqual([ln.text for ln in C.dedupe(cues)], ["one"])


class Render(unittest.TestCase):
    def setUp(self):
        self.lines = C.dedupe(C.parse_vtt(read_vtt()))
        self.chapters = C.chapters_of(MEDIA)

    def test_chapters_become_headings_in_order_and_paragraphs_follow_the_speech(self):
        text = C.render(self.lines, self.chapters)
        self.assertEqual(text.count("\n## "), 2)
        self.assertTrue(text.startswith("## Intro (0:00)\n\n[Music] >> I was previously"))
        self.assertIn("\n\n## Why personal agents took off (0:52)\n\nwhy did personal agents take off", text)
        self.assertIn("\n\n## Why that breaks in finance (1:34)\n\nyeah but none of it", text)
        self.assertTrue(text.endswith("institutional finance\n"))
        # a chapter no line falls into still gets its heading, in order
        chapters = self.chapters + [{"start": 99.0, "end": 100.0, "title": "Outro"}]
        self.assertTrue(C.render(self.lines, chapters).endswith("## Outro (1:39)\n"))

    def test_without_chapters_paragraphs_break_at_pauses(self):
        text = C.render(self.lines, [])
        self.assertNotIn("## ", text)
        paras = text.strip().split("\n\n")
        self.assertEqual(len(paras), 3)   # 13.13 → 52.0 and 62.3 → 94.0 are pauses longer than PARAGRAPH_GAP
        self.assertTrue(paras[1].startswith("why did personal agents"))

    def test_empty(self):
        self.assertEqual(C.render([], []), "")
        self.assertEqual(C.render([], [{"start": 0, "end": None, "title": "Only"}]), "## Only (0:00)\n")


class Check(unittest.TestCase):
    def setUp(self):
        self.lines = C.dedupe(C.parse_vtt(read_vtt()))
        self.chapters = C.chapters_of(MEDIA)
        self.text = C.render(self.lines, self.chapters)

    def test_complete_by_construction(self):
        chk = C.check(self.text, self.lines, self.chapters)
        self.assertEqual(chk["completeness"], "complete")
        self.assertEqual((chk["reference_count"], chk["eligible_count"], chk["missing_count"], chk["headings_missing_count"]), (9, 8, 0, 0))
        self.assertEqual(chk["missing_examples"], [])

    def test_a_dropped_line_and_a_dropped_heading_are_partial(self):
        broken = self.text.replace("spent way too much time in spreadsheets ", "")
        chk = C.check(broken, self.lines, self.chapters)
        self.assertEqual(chk["completeness"], "partial")
        self.assertEqual(chk["missing_count"], 1)
        self.assertEqual(chk["missing_examples"], ["spent way too much time in spreadsheets"])
        no_heading = self.text.replace("## Intro (0:00)\n\n", "")
        chk = C.check(no_heading, self.lines, self.chapters)
        self.assertEqual((chk["completeness"], chk["headings_missing_count"], chk["missing_count"]), ("partial", 1, 0))

    def test_short_lines_are_not_required_and_markup_does_not_count(self):
        chk = C.check(self.text.replace("yeah but", "but"), self.lines, self.chapters)
        self.assertEqual(chk["completeness"], "complete")   # "yeah" has one token: never required
        chk = C.check(self.text.replace("in a previous life", "in **a** previous *life*"), self.lines, self.chapters)
        self.assertEqual(chk["missing_count"], 0)


class ChaptersOf(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(C.chapters_of(None), [])
        self.assertEqual(C.chapters_of({"chapters": "nope"}), [])
        got = C.chapters_of({"chapters": [{"start_time": 5, "end_time": 9, "title": " B "}, {"start": 1, "title": "A"}, {"start": "x", "title": "bad"},
                                          {"start": 2}, "junk"]})
        self.assertEqual(got, [{"start": 1.0, "end": None, "title": "A"}, {"start": 5.0, "end": 9.0, "title": "B"}])


class Convert(unittest.TestCase):
    def test_report_has_the_text_fixtures_keys_and_the_lanes_facts(self):
        with open(VTT, "rb") as fh:
            raw = fh.read()
        text, rep = C.convert(raw, MEDIA, URL)
        assert_same_keys(self, rep, expected_shapes("text.json"), "captions report", nullable=("title",))
        self.assertEqual((rep["schema"], rep["converter"], rep["method"], rep["acquisition"], rep["completeness"]),
                         ("mnemon-capture-text/2", "vtt-text", "browser+vtt-text", "ok", "complete"))
        self.assertEqual(rep["title"], "An example talk about agents")
        self.assertEqual(rep["authors"], ["Example Channel"])
        self.assertEqual(rep["published_candidates"], [{"value": "2026-09-01", "source": "yt-dlp upload_date"}])
        self.assertEqual(rep["inventory"]["headings"], {"html": 3, "md": 3})
        self.assertIn("16 cue(s) → 9 line(s); 3 chapter(s); captions cover 100% of the video's duration", rep["candidates"][0]["note"])
        self.assertEqual(rep["bytes"], len(text.encode("utf-8")))
        self.assertEqual(rep["words"], len(text.split()))

    def test_no_media_and_an_empty_track(self):
        with open(VTT, "rb") as fh:
            raw = fh.read()
        text, rep = C.convert(raw, None, URL)
        self.assertNotIn("## ", text)
        self.assertEqual((rep["title"], rep["authors"], rep["published_candidates"]), (None, [], []))
        text, rep = C.convert(b"WEBVTT\n", None, URL)
        self.assertEqual((text, rep["completeness"], rep["error_class"], rep["retryable"]), ("", "unknown", "convert", False))
        with self.assertRaises(UnicodeDecodeError):
            C.convert(b"\xff\xfe", None, URL)


class Cli(unittest.TestCase):
    def test_converts_and_writes_the_report(self):
        with tempfile.TemporaryDirectory() as d:
            media = os.path.join(d, "media.json")
            with open(media, "w", encoding="utf-8") as fh:
                json.dump(MEDIA, fh)
            out, rep = os.path.join(d, "capture.md"), os.path.join(d, "report.json")
            rc, so, se = run_cli([CLI, VTT, "--url", URL, "--out", out, "--report", rep, "--media", media])
            self.assertEqual(rc, 0, se)
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("## Intro (0:00)\n"))
            with open(rep, encoding="utf-8") as fh:
                report = json.load(fh)
            assert_same_keys(self, report, expected_shapes("text.json"), "capture-captions.py report", nullable=("title",))
            self.assertEqual(report["completeness"], "complete")

    def test_failures(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "empty.vtt")
            with open(empty, "w", encoding="utf-8") as fh:
                fh.write("WEBVTT\n\nNOTE nothing\n")
            out, rep = os.path.join(d, "capture.md"), os.path.join(d, "report.json")
            rc, so, se = run_cli([CLI, empty, "--url", URL, "--out", out, "--report", rep])
            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(out))
            with open(rep, encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertEqual((report["error_class"], report["retryable"], report["completeness"]), ("convert", False, "unknown"))
            bad = os.path.join(d, "bad.vtt")
            with open(bad, "wb") as fh:
                fh.write(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n\xff\xfe\n")
            rc, so, se = run_cli([CLI, bad, "--url", URL, "--out", out, "--report", rep])
            self.assertEqual(rc, 1)
            with open(rep, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["error_class"], "invalid-utf8")
            rc, so, se = run_cli([CLI, os.path.join(d, "missing.vtt"), "--url", URL, "--out", out, "--report", rep])
            self.assertEqual(rc, 2)
            # an unreadable media record converts without chapters and says so
            with open(os.path.join(d, "junk.json"), "w") as fh:
                fh.write("{not json")
            rc, so, se = run_cli([CLI, VTT, "--url", URL, "--out", out, "--report", rep, "--media", os.path.join(d, "junk.json")])
            self.assertEqual(rc, 0)
            self.assertIn("converting without chapters", se)


if __name__ == "__main__":
    unittest.main()
