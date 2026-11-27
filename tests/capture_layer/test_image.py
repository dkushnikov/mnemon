"""§21.2 — the envelope check of an image file (`bin/capture/image.py`): every envelope fixture of §21.13
against `expected.json`; the byte, pixel and long-edge caps at and over the limit; the signature heads and
`sniff`; `extension_matches`; the PNG and JPEG container rules; the EXIF orientation; bounded, linear,
non-inflating reads; the `--signature` entry; the generator's determinism."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from _helpers import BIN, FIX, PY, read_json

from capture import image as I

IMAGE_FIX = os.path.join(FIX, "image")
EXPECTED = read_json(os.path.join(IMAGE_FIX, "expected.json"))
LIMITS = EXPECTED["limits"]
GENERATOR = os.path.join(IMAGE_FIX, "generate.py")


def _load_generator():
    spec = importlib.util.spec_from_file_location("image_fixture_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G = _load_generator()


def fixture(name: str) -> str:
    return os.path.join(IMAGE_FIX, name)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mnemon-image-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, data: bytes, name: str = "probe.png") -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def check(self, data: bytes, name: str = "probe.png", **limits) -> I.Envelope:
        merged = dict(LIMITS)
        merged.update(limits)
        return I.inspect(self.write(data, name), input_name=name, **merged)

    def assertOutcome(self, env: I.Envelope, outcome: str, reason=None):
        self.assertEqual((env.outcome, env.reason), (outcome, reason), env)


class EnvelopeFixtures(unittest.TestCase):
    """Every fixture of §21.13 → its outcome, reason, `detected`, format, dimensions, orientation and
    trailing bytes, as `expected.json` states them for the file the generator built."""

    SPEC_NAMES = [
        "base.png", "base.jpg", "truncated.png", "chunk-past-end.png", "ihdr0.png", "ihdr-twice.png",
        "no-idat.png", "apng.png", "huge.png", "idat-broken.png", "trailing.png", "exif6.png", "exif-bad.png",
        "exif1.jpg", "exif6.jpg", "exif3.jpg", "exif-mirrored.jpg", "exif-bad-ifd.jpg", "exif-type.jpg",
        "noeoi.jpg", "truncated.jpg", "nosof.jpg", "sof-conflict.jpg", "scan-broken.jpg",
        "jpeg-named.png", "text-named.png",
    ]
    HEADS = {"heif", "avif", "webp", "gif", "tiff", "bmp", "pdf"}

    def test_every_spec_fixture_and_every_head_is_in_expected(self):
        names = set(EXPECTED["fixtures"])
        for name in self.SPEC_NAMES:
            self.assertIn(name, names)
        heads = {EXPECTED["fixtures"][n]["detected"] for n in names if n.startswith("heads/")}
        self.assertEqual(heads, self.HEADS)
        self.assertEqual(len(names), len(self.SPEC_NAMES) + len(self.HEADS))

    def test_every_fixture_gives_its_expected_envelope(self):
        for name, exp in sorted(EXPECTED["fixtures"].items()):
            with self.subTest(fixture=name):
                path = fixture(name)
                env = I.inspect(path, input_name=os.path.basename(name), **LIMITS)
                got = {k: getattr(env, k) for k in exp}
                self.assertEqual(got, exp)
                self.assertEqual(env.bytes, os.path.getsize(path))
                if env.width is None:
                    self.assertIsNone(env.pixels)
                else:
                    self.assertEqual(env.pixels, env.width * env.height)
                self.assertEqual(env.input_extension, os.path.splitext(name)[1][1:])

    def test_base_jpg_is_pinned_and_holds_no_app1(self):
        with open(fixture("base.jpg"), "rb") as fh:
            data = fh.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(), EXPECTED["base_jpg_sha256"])
        self.assertNotIn(b"\xff\xe1", data)
        self.assertNotIn(b"Exif", data)
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertEqual(data[2:4], b"\xff\xe0", "a JFIF APP0 follows SOI")

    def test_decode_failures_pass_the_envelope(self):
        """§21.2 «What it does not prove»: a broken IDAT stream and a damaged later scan are the witness's."""
        for name in ("idat-broken.png", "scan-broken.jpg"):
            env = I.inspect(fixture(name), input_name=name, **LIMITS)
            self.assertEqual(env.outcome, "ok", name)
            self.assertIsNone(I.failure_of(env), name)

    def test_the_name_never_decides(self):
        env = I.inspect(fixture("jpeg-named.png"), input_name="jpeg-named.png", **LIMITS)
        self.assertEqual((env.outcome, env.format, env.detected), ("ok", "jpeg", "jpeg"))
        self.assertEqual((env.input_extension, env.extension_matches), ("png", False))
        with open(fixture("jpeg-named.png"), "rb") as fh:
            self.assertEqual(I.raw_extension(fh.read(16)), "jpg")
        env = I.inspect(fixture("text-named.png"), input_name="text-named.png", **LIMITS)
        self.assertEqual((env.outcome, env.detected, env.format), ("wrong-format", "unknown", None))
        self.assertEqual(I.failure_of(env)["class"], "unsupported")


class Caps(_Tmp):
    """Steps 1 and 6: more bytes than `max_bytes`, more pixels than `max_pixels`, a longer side over
    `max_edge` — each at the limit (accepted) and one over it (`too-large`)."""

    def test_bytes_at_and_over_the_cap(self):
        data = G.png_file()
        env = self.check(data, max_bytes=len(data))
        self.assertOutcome(env, "ok")
        env = self.check(data, max_bytes=len(data) - 1)
        self.assertOutcome(env, "too-large", "bytes")
        self.assertEqual(env.bytes, len(data))
        self.assertEqual((env.detected, env.format, env.width, env.orientation), (None, None, None, None))
        self.assertEqual(I.failure_of(env), {"class": "too-large", "acquisition": "too-large", "reason": "bytes",
                                             "retryable": False})

    def test_pixels_at_and_over_the_cap(self):
        png, jpg = G.png_file(), G.jpeg_file()
        self.assertOutcome(self.check(png, max_pixels=256), "ok")
        env = self.check(png, max_pixels=255)
        self.assertOutcome(env, "too-large", "pixels")
        self.assertEqual(env.pixels, 256)
        self.assertOutcome(self.check(jpg, "p.jpg", max_pixels=64), "ok")
        self.assertOutcome(self.check(jpg, "p.jpg", max_pixels=63), "too-large", "pixels")

    def test_the_long_edge_cap(self):
        """§21.13: a header of 1170 × 8 001 with `max_edge=8000` → `too-large`, reason `edge`;
        1170 × 8 000 → `ok`. Either side may be the long one; PNG and JPEG alike."""
        for build, name in ((G.png_file, "tall.png"), (G.jpeg_file, "tall.jpg")):
            env = self.check(build(1170, 8001), name, max_edge=8000)
            self.assertOutcome(env, "too-large", "edge")
            self.assertEqual((env.width, env.height, env.pixels), (1170, 8001, 1170 * 8001))
            self.assertLess(env.pixels, LIMITS["max_pixels"], "the edge decides, not the pixels")
            self.assertOutcome(self.check(build(1170, 8000), name, max_edge=8000), "ok")
            self.assertOutcome(self.check(build(8001, 1170), name, max_edge=8000), "too-large", "edge")
            self.assertOutcome(self.check(build(8000, 1170), name, max_edge=8000), "ok")

    def test_pixels_are_checked_before_the_edge(self):
        env = self.check(G.png_file(60000, 60000))
        self.assertOutcome(env, "too-large", "pixels")

    def test_a_giant_header_returns_at_once(self):
        for path, name in ((fixture("huge.png"), "huge.png"), (self.write(G.jpeg_file(65535, 65535), "g.jpg"), "g.jpg")):
            t0 = time.monotonic()
            env = I.inspect(path, input_name=name, **LIMITS)
            elapsed = time.monotonic() - t0
            self.assertOutcome(env, "too-large", "pixels")
            self.assertLess(elapsed, 0.25, "%s: %.3f s" % (name, elapsed))

    def test_the_cap_comes_before_the_signature(self):
        env = self.check(b"not an image at all", max_bytes=4)
        self.assertOutcome(env, "too-large", "bytes")


class Signatures(_Tmp):
    """Step 2 and `sniff`: the signature decides the format; anything else is `wrong-format` with its
    `detected` name."""

    def test_sniff_by_signature(self):
        cases = [
            (G.PNG_SIG + b"\x00" * 8, ("png", "png")),
            (b"\xff\xd8\xff\xe0" + b"\x00" * 12, ("jpeg", "jpeg")),
            (b"\xff\xd8\xff", ("jpeg", "jpeg")),
            (b"GIF87a" + b"\x00" * 6, (None, "gif")),
            (b"GIF89a" + b"\x00" * 6, (None, "gif")),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 ", (None, "webp")),
            (b"RIFF\x00\x00\x00\x00WAVEfmt ", (None, "unknown")),
            (b"II*\x00\x08\x00\x00\x00", (None, "tiff")),
            (b"MM\x00*\x00\x00\x00\x08", (None, "tiff")),
            (b"BM\x00\x00", (None, "bmp")),
            (b"%PDF-1.4\n", (None, "pdf")),
            (b"\x00\x00\x00\x20ftypmp42", (None, "unknown")),
            (b"", (None, "unknown")),
            (b"\xff\xd8", (None, "unknown")),
            (G.PNG_SIG[:7], (None, "unknown")),
            (b"\x89PNG\r\n\x1a\x00", (None, "unknown")),
        ]
        for brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"):
            cases.append((b"\x00\x00\x00\x18ftyp" + brand, (None, "heif")))
        for brand in (b"avif", b"avis"):
            cases.append((b"\x00\x00\x00\x1cftyp" + brand, (None, "avif")))
        for head, want in cases:
            with self.subTest(head=head):
                self.assertEqual(I.sniff(head), want)

    def test_sniff_reads_only_the_first_sixteen_bytes(self):
        self.assertEqual(I.sniff(b"\x00" * 16 + G.PNG_SIG), (None, "unknown"))

    def test_raw_extension(self):
        self.assertEqual(I.raw_extension(G.png_file()[:16]), "png")
        self.assertEqual(I.raw_extension(G.jpeg_file()[:16]), "jpg")
        for head in (b"GIF89a", b"BM", b"%PDF-", b"", b"\xff\xd8", b"text"):
            self.assertIsNone(I.raw_extension(head), head)

    def test_every_head_is_wrong_format_with_its_name(self):
        for name, exp in EXPECTED["fixtures"].items():
            if not name.startswith("heads/"):
                continue
            with self.subTest(head=name):
                with open(fixture(name), "rb") as fh:
                    data = fh.read()
                self.assertEqual(len(data), 12)
                self.assertEqual(I.sniff(data), (None, exp["detected"]))
                env = I.inspect(fixture(name), input_name=os.path.basename(name), **LIMITS)
                self.assertOutcome(env, "wrong-format", "signature")
                self.assertEqual(I.failure_of(env), {"class": "unsupported", "acquisition": "wrong-format",
                                                     "reason": "signature", "retryable": False})

    def test_an_empty_file_is_wrong_format(self):
        env = self.check(b"")
        self.assertOutcome(env, "wrong-format", "signature")
        self.assertEqual((env.detected, env.bytes), ("unknown", 0))


class Extensions(_Tmp):
    """`input_extension` is the lowercased extension of the name; `extension_matches` compares it with the
    format (`jpg` and `jpeg` both match `jpeg`); a mismatch with a valid signature is accepted."""

    def test_extension_matches(self):
        png, jpg = G.png_file(), G.jpeg_file()
        cases = [
            (jpg, "shot.jpg", "jpg", True), (jpg, "shot.jpeg", "jpeg", True), (jpg, "SHOT.JPG", "jpg", True),
            (jpg, "shot.png", "png", False), (jpg, "shot", None, None),
            (png, "shot.png", "png", True), (png, "Shot.PNG", "png", True), (png, "shot.jpg", "jpg", False),
            (png, "shot.jpeg", "jpeg", False), (png, "shot", None, None), (png, ".png", None, None),
            (png, "shot.", None, None), (png, "dir.d/shot", None, None), (png, "a/b/shot.Png", "png", True),
        ]
        for data, name, ext, matches in cases:
            with self.subTest(name=name):
                env = I.inspect(self.write(data, "x.bin"), input_name=name, **LIMITS)
                self.assertEqual(env.outcome, "ok")
                self.assertEqual((env.input_extension, env.extension_matches), (ext, matches))

    def test_no_name_and_no_format(self):
        env = I.inspect(self.write(G.png_file()), input_name=None, **LIMITS)
        self.assertEqual((env.input_extension, env.extension_matches), (None, None))
        env = self.check(b"GIF89a" + b"\x00" * 20, "anim.gif")
        self.assertEqual((env.input_extension, env.extension_matches, env.format), ("gif", None, None))


class PngContainer(_Tmp):
    """Step 3: the chunk walk — IHDR first, once, 13 bytes, non-zero, a valid depth/colour pair; an IDAT;
    no acTL; every chunk inside the file; an IEND; trailing bytes counted."""

    def test_valid_depth_colour_pairs_and_invalid_ones(self):
        valid = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
        for colour in range(0, 8):
            for depth in (1, 2, 4, 8, 16, 0, 3, 32):
                data = G.PNG_SIG + G.ihdr(16, 16, depth, colour) + G.IDAT + G.IEND
                env = self.check(data)
                ok = depth in valid.get(colour, ())
                with self.subTest(colour=colour, depth=depth):
                    self.assertOutcome(env, "ok" if ok else "corrupt", None if ok else "ihdr-depth")

    def test_ihdr_rules(self):
        self.assertOutcome(self.check(G.PNG_SIG + G.IDAT + G.ihdr(16, 16) + G.IEND), "corrupt", "ihdr-missing")
        bad_len = G.PNG_SIG + G.chunk(b"IHDR", struct.pack(">IIBBBB", 16, 16, 8, 2, 0, 0)) + G.IDAT + G.IEND
        self.assertOutcome(self.check(bad_len), "corrupt", "ihdr-length")
        self.assertOutcome(self.check(G.png_file(after=[G.ihdr(16, 16)])), "corrupt", "ihdr-twice")
        for w, h in ((0, 16), (16, 0)):
            env = self.check(G.png_file(w, h))
            self.assertOutcome(env, "corrupt", "ihdr-zero")
            self.assertEqual((env.width, env.height, env.pixels), (w, h, 0))

    def test_truncation_shapes(self):
        base = G.png_file()
        self.assertOutcome(self.check(G.PNG_SIG), "truncated", "no-iend")
        self.assertOutcome(self.check(G.PNG_SIG + b"\x00\x00\x00"), "truncated", "chunk-past-end")
        self.assertOutcome(self.check(base[:-1]), "truncated", "chunk-past-end")
        self.assertOutcome(self.check(base[:-4]), "truncated", "chunk-past-end")
        claim = G.PNG_SIG + G.ihdr(16, 16) + struct.pack(">I", 0xFFFFFFFF) + b"IDAT" + b"\x00" * 32
        self.assertOutcome(self.check(claim), "truncated", "chunk-past-end")

    def test_animated_png_is_wrong_format_wherever_actl_stands(self):
        actl = G.chunk(b"acTL", struct.pack(">II", 2, 0))
        for data in (G.png_file(before=[actl]), G.png_file(after=[actl])):
            env = self.check(data)
            self.assertOutcome(env, "wrong-format", "animated")
            self.assertEqual((env.detected, env.format), ("apng", "png"))

    def test_other_chunks_are_skipped_and_trailing_bytes_counted(self):
        text = G.chunk(b"tEXt", b"Comment\x00synthetic")
        env = self.check(G.png_file(before=[text], after=[text]) + b"\x00" * 3)
        self.assertOutcome(env, "ok")
        self.assertEqual(env.trailing_bytes, 3)
        two_idat = G.png_file(after=[G.IDAT])
        self.assertOutcome(self.check(two_idat), "ok")

    def test_no_crc_is_computed(self):
        data = bytearray(G.png_file())
        data[33 + 8] ^= 0xFF          # a byte of the IDAT data: its CRC no longer matches
        self.assertOutcome(self.check(bytes(data)), "ok")


class JpegContainer(_Tmp):
    """Step 4: the marker walk from SOI to the first SOS (FF fill allowed), the frame header rules, an
    EOI after the first SOS, trailing bytes after the last EOI."""

    def splice(self, before: bytes, insert: bytes) -> bytes:
        base = G.jpeg_file()
        at = base.index(before)
        return base[:at] + insert + base[at:]

    def test_fill_bytes_and_a_tem_marker_are_allowed(self):
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\xff\xff"), "f.jpg"), "ok")
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\x01"), "f.jpg"), "ok")

    def test_marker_rules(self):
        self.assertOutcome(self.check(self.splice(G.DQT, b"\x00"), "m.jpg"), "corrupt", "marker")
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\x00"), "m.jpg"), "corrupt", "marker")
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\xd0"), "m.jpg"), "corrupt", "marker")
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\xd8"), "m.jpg"), "corrupt", "marker")
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\xd9"), "m.jpg"), "corrupt", "no-scan")
        self.assertOutcome(self.check(self.splice(G.DQT, b"\xff\xfe\x00\x01"), "m.jpg"), "corrupt",
                           "segment-length")

    def test_frame_rules(self):
        short = G.segment(0xC0, b"\x08\x00\x08\x00")
        self.assertOutcome(self.check(G.jpeg_file(frames=[short]), "s.jpg"), "corrupt", "frame-length")
        env = self.check(G.jpeg_file(8, 0), "z.jpg")
        self.assertOutcome(env, "corrupt", "frame-zero")
        self.assertEqual((env.width, env.height), (8, 0))
        self.assertOutcome(self.check(G.jpeg_file(frames=[G.sof0(8, 8), G.sof0(8, 8)]), "d.jpg"), "ok")
        progressive = G.segment(0xC2, G.sof0(8, 8)[4:])
        self.assertOutcome(self.check(G.jpeg_file(frames=[progressive]), "p.jpg"), "ok")
        for not_a_frame in (0xC4, 0xC8, 0xCC):
            seg = G.segment(not_a_frame, G.sof0(8, 8)[4:])
            with self.subTest(marker=hex(not_a_frame)):
                self.assertOutcome(self.check(G.jpeg_file(frames=[seg]), "n.jpg"), "corrupt", "no-frame")

    def test_truncation_shapes(self):
        base = G.jpeg_file()
        before_sos = base[:base.index(b"\xff\xda")]
        self.assertOutcome(self.check(before_sos, "t.jpg"), "truncated", "no-sos")
        self.assertOutcome(self.check(before_sos + b"\xff\xff", "t.jpg"), "truncated", "no-sos")
        self.assertOutcome(self.check(base[:3], "t.jpg"), "truncated", "no-sos")
        self.assertOutcome(self.check(base[:4], "t.jpg"), "truncated", "segment-past-end")
        self.assertOutcome(self.check(base[:base.index(b"\xff\xda") + 5], "t.jpg"), "truncated",
                           "segment-past-end")
        self.assertOutcome(self.check(base[:-1], "t.jpg"), "truncated", "no-eoi")

    def test_bytes_after_the_last_eoi_are_counted(self):
        base = G.jpeg_file()
        env = self.check(base + b"\x00\x01\x02\x03\x04", "t.jpg")
        self.assertOutcome(env, "ok")
        self.assertEqual(env.trailing_bytes, 5)
        env = self.check(base + b"\x00" * 7 + b"\xff\xd9" + b"\x01\x02", "t.jpg")
        self.assertEqual(env.trailing_bytes, 2, "the last EOI counts")

    def test_the_scans_are_not_interpreted(self):
        env = self.check(G.jpeg_file(third=b"\x00" * 1000 + b"\xff\xc0\x00\x11" + b"\xff\xe1"), "s.jpg")
        self.assertOutcome(env, "ok")


class Exif(_Tmp):
    """Step 5 for an `eXIf` chunk and an `Exif` APP1: orientation 1 upright, 2–8 rotated, any other value
    corrupt; the TIFF header, IFD0 and every IFD0 entry inside the segment or chunk; tag 0x0112 a SHORT of
    count 1; a second EXIF corrupt."""

    def png(self, payload: bytes, *more) -> bytes:
        return G.png_file(before=[G.exif_chunk(payload)] + [G.exif_chunk(m) for m in more])

    def jpg(self, payload: bytes, *more) -> bytes:
        return G.jpeg_file(app=[G.app1_exif(payload)] + [G.app1_exif(m) for m in more])

    def both(self):
        return ((self.png, "e.png"), (self.jpg, "e.jpg"))

    def test_orientation_values(self):
        for build, name in self.both():
            for order in ("II", "MM"):
                with self.subTest(container=name, order=order):
                    env = self.check(build(G.tiff(1, order=order)), name)
                    self.assertOutcome(env, "ok")
                    self.assertEqual(env.orientation, 1)
                    for value in range(2, 9):
                        env = self.check(build(G.tiff(value, order=order)), name)
                        self.assertOutcome(env, "rotated", "orientation")
                        self.assertEqual(env.orientation, value)
                        self.assertEqual(I.failure_of(env)["class"], "unsupported")
                    for value in (0, 9, 0xFFFF):
                        env = self.check(build(G.tiff(value, order=order)), name)
                        self.assertOutcome(env, "corrupt", "orientation-value")
                        self.assertEqual(env.orientation, value)

    def test_no_exif_or_no_tag_is_orientation_one(self):
        for build, name in self.both():
            unit_only = b"II*\x00" + struct.pack("<IH", 8, 1) + struct.pack("<HHIHH", 0x0128, 3, 1, 2, 0) + b"\x00" * 4
            env = self.check(build(unit_only), name)
            self.assertOutcome(env, "ok")
            self.assertEqual(env.orientation, 1)
            empty_ifd = b"MM\x00*" + struct.pack(">IH", 8, 0)
            self.assertOutcome(self.check(build(empty_ifd), name), "ok")
        self.assertEqual(self.check(G.png_file()).orientation, 1)

    def test_a_second_exif_is_corrupt(self):
        env = self.check(self.png(G.tiff(1), G.tiff(1)))
        self.assertOutcome(env, "corrupt", "exif-twice")
        env = self.check(G.png_file(before=[G.exif_chunk(G.tiff(1))], after=[G.exif_chunk(G.tiff(1))]))
        self.assertOutcome(env, "corrupt", "exif-twice")
        self.assertOutcome(self.check(self.jpg(G.tiff(1), G.tiff(1)), "e.jpg"), "corrupt", "exif-twice")

    def test_an_app1_that_is_not_exif_is_not_counted(self):
        xmp = G.segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>")
        data = G.jpeg_file(app=[xmp, G.app1_exif(G.tiff(1)), G.segment(0xE1, b"Exi")])
        self.assertOutcome(self.check(data, "x.jpg"), "ok")

    def test_an_entry_outside_its_segment(self):
        for build, name in self.both():
            with self.subTest(container=name):
                env = self.check(build(G.tiff(1, entries=3)), name)
                self.assertOutcome(env, "corrupt", "exif-entry")
                self.assertIsNone(env.orientation)
                cut = G.tiff(1)[:8 + 2 + 12 + 5]      # the second entry runs past the segment
                self.assertOutcome(self.check(build(cut), name), "corrupt", "exif-entry")

    def test_an_unreadable_header_or_ifd0(self):
        for build, name in self.both():
            with self.subTest(container=name):
                self.assertOutcome(self.check(build(G.tiff(1, ifd=0x1000)), name), "corrupt", "exif-ifd")
                payload = G.tiff(1)
                self.assertOutcome(self.check(build(G.tiff(1, ifd=len(payload) - 1)), name), "corrupt", "exif-ifd")
                self.assertOutcome(self.check(build(b"II*\x00"), name), "corrupt", "exif-header")
                self.assertOutcome(self.check(build(b""), name), "corrupt", "exif-header")
                self.assertOutcome(self.check(build(G.tiff(1, header=b"MM*\x00")), name), "corrupt", "exif-header")
                self.assertOutcome(self.check(build(b"Exif\x00\x00" + G.tiff(1)), name), "corrupt", "exif-header")

    def test_the_tag_must_be_one_short(self):
        for build, name in self.both():
            with self.subTest(container=name):
                for typ, count in ((4, 1), (1, 1), (3, 2), (3, 0)):
                    env = self.check(build(G.tiff(1, typ=typ, count=count)), name)
                    self.assertOutcome(env, "corrupt", "exif-type")
                    self.assertIsNone(env.orientation)

    def test_the_container_is_judged_before_the_exif(self):
        """Steps in order: a cut container with a rotated EXIF is `truncated`, a rotated image over the
        caps is `rotated`."""
        env = self.check(G.jpeg_file(app=[G.app1_exif(G.tiff(6))], eoi=False), "c.jpg")
        self.assertOutcome(env, "truncated", "no-eoi")
        self.assertIsNone(env.orientation)
        env = self.check(G.png_file(60000, 60000, before=[G.exif_chunk(G.tiff(6))]))
        self.assertOutcome(env, "rotated", "orientation")


class _CountingFileIO(io.FileIO):
    def __init__(self, *args, **kwargs):
        io.FileIO.__init__(self, *args, **kwargs)
        self.count = 0

    def readinto(self, buf):
        n = io.FileIO.readinto(self, buf)
        self.count += n or 0
        return n

    def read(self, size=-1):
        data = io.FileIO.read(self, size)
        self.count += len(data or b"")
        return data

    def readall(self):
        data = io.FileIO.readall(self)
        self.count += len(data)
        return data


class BoundedReads(_Tmp):
    """One linear pass bounded by `max_bytes`; no compressed data is read for inflation; hostile shapes
    take bounded time."""

    def counted(self, path: str, name: str, **limits):
        raws = []

        def opener(file, mode="r", *args, **kwargs):
            raw = _CountingFileIO(file, "r")
            raws.append(raw)
            return io.BufferedReader(raw)

        merged = dict(LIMITS)
        merged.update(limits)
        with mock.patch.object(I, "open", opener, create=True):
            env = I.inspect(path, input_name=name, **merged)
        self.assertEqual(len(raws), 1)
        return env, raws[0].count

    def test_a_larger_file_is_not_read_past_max_bytes(self):
        path = os.path.join(self.tmp, "big.png")
        with open(path, "wb") as fh:
            fh.write(G.png_file())
            fh.truncate(LIMITS["max_bytes"] + (1 << 20))
        env, read = self.counted(path, "big.png")
        self.assertOutcome(env, "too-large", "bytes")
        self.assertLessEqual(read, 64)

    def test_a_file_at_the_cap_is_read_at_most_once(self):
        base = G.jpeg_file()
        scan_data = b"\x00" * (200000 - len(base))
        data = base[:-2] + scan_data + base[-2:]
        env, read = self.counted(self.write(data, "cap.jpg"), "cap.jpg", max_bytes=len(data))
        self.assertOutcome(env, "ok")
        self.assertLessEqual(read, len(data) + 65536)

    def test_idat_data_is_skipped_not_read(self):
        """A 6 MB IDAT costs what a small one costs: the headers around it and the buffer's read-ahead."""
        idat = G.chunk(b"IDAT", b"\x78\x01" + b"\x00" * (6 << 20))
        data = G.png_file(idat=idat)
        env, read = self.counted(self.write(data), "skip.png")
        self.assertOutcome(env, "ok")
        self.assertLessEqual(read, 2 * io.DEFAULT_BUFFER_SIZE + 1024, "read %d of %d bytes" % (read, len(data)))
        self.assertLess(read, len(data) // 10)

    def test_hostile_shapes_take_bounded_time(self):
        empty = G.chunk(b"zzZz", b"")
        shapes = {
            "100 000 empty PNG chunks": (G.png_file(before=[empty] * 100000), "h.png", "ok"),
            "4 MB of JPEG fill bytes": (G.SOI + b"\xff" * (4 << 20), "h.jpg", "truncated"),
            "4 MB of JPEG scan data": (G.jpeg_file(third=b"\x00" * (4 << 20)), "h.jpg", "ok"),
            "4 MB of scan data without EOI": (G.jpeg_file(third=b"\x00" * (4 << 20), eoi=False), "h.jpg", "truncated"),
        }
        for label, (data, name, outcome) in shapes.items():
            path = self.write(data, name)
            t0 = time.monotonic()
            env = I.inspect(path, input_name=name, **LIMITS)
            elapsed = time.monotonic() - t0
            with self.subTest(shape=label):
                self.assertEqual(env.outcome, outcome)
                self.assertLess(elapsed, 3.0, "%s: %.2f s" % (label, elapsed))


class Interface(_Tmp):
    """The names fixed for the other packages: `ENVELOPE`, the twelve fields, `as_dict`, `failure_of`,
    OSError on an unreadable path."""

    FIELDS = ("outcome", "reason", "detected", "format", "input_extension", "extension_matches", "width",
              "height", "pixels", "bytes", "orientation", "trailing_bytes")

    def test_names(self):
        self.assertEqual(I.ENVELOPE, "mnemon-image-envelope/1")
        self.assertEqual(I.Envelope._fields, self.FIELDS)

    def test_as_dict_is_the_twelve_keys_in_order(self):
        env = I.inspect(fixture("base.png"), input_name="base.png", **LIMITS)
        d = I.as_dict(env)
        self.assertEqual(tuple(d.keys()), self.FIELDS)
        self.assertEqual(d, {"outcome": "ok", "reason": None, "detected": "png", "format": "png",
                             "input_extension": "png", "extension_matches": True, "width": 16, "height": 16,
                             "pixels": 256, "bytes": os.path.getsize(fixture("base.png")), "orientation": 1,
                             "trailing_bytes": 0})
        self.assertNotIn("limits", d)

    def test_failure_of_every_outcome(self):
        self.assertIsNone(I.failure_of(I.inspect(fixture("base.png"), input_name="base.png", **LIMITS)))
        for name, klass in (("text-named.png", "unsupported"), ("truncated.png", "unsupported"),
                            ("ihdr0.png", "unsupported"), ("exif6.png", "unsupported"),
                            ("huge.png", "too-large")):
            env = I.inspect(fixture(name), input_name=name, **LIMITS)
            self.assertEqual(I.failure_of(env), {"class": klass, "acquisition": env.outcome,
                                                 "reason": env.reason, "retryable": False}, name)

    def test_an_unreadable_path_raises_oserror(self):
        with self.assertRaises(OSError):
            I.inspect(os.path.join(self.tmp, "absent.png"), input_name="absent.png", **LIMITS)
        with self.assertRaises(OSError):
            I.inspect(self.tmp, input_name="dir.png", **LIMITS)


class SignatureEntry(_Tmp):
    """`python3 bin/capture/image.py --signature <file>` prints `png`, `jpg` or nothing and exits 0."""

    def run_entry(self, *args):
        return subprocess.run([PY, os.path.join(BIN, "capture", "image.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def test_signature_entry(self):
        for name, out in (("base.png", b"png\n"), ("base.jpg", b"jpg\n"), ("jpeg-named.png", b"jpg\n"),
                          ("text-named.png", b""), ("heads/gif.gif", b""), ("truncated.png", b"png\n")):
            proc = self.run_entry("--signature", fixture(name))
            self.assertEqual((proc.returncode, proc.stdout), (0, out), name)

    def test_an_unreadable_file_prints_nothing(self):
        proc = self.run_entry("--signature", os.path.join(self.tmp, "absent.png"))
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))
        self.assertTrue(proc.stderr)

    def test_bad_usage_exits_2(self):
        for args in ((), ("--signature",), ("--other", fixture("base.png"))):
            proc = self.run_entry(*args)
            self.assertEqual((proc.returncode, proc.stdout), (2, b""), args)


class Generator(unittest.TestCase):
    """`generate.py` writes the same bytes every time, and exactly the files `expected.json` names."""

    def run_in_copy(self) -> str:
        tmp = tempfile.mkdtemp(prefix="mnemon-image-gen-")
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(GENERATOR, tmp)
        proc = subprocess.run([PY, os.path.join(tmp, "generate.py")], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return tmp

    @staticmethod
    def tree(root: str) -> dict:
        out = {}
        for d, _, files in os.walk(root):
            for f in files:
                path = os.path.join(d, f)
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root)] = fh.read()
        return out

    def test_two_runs_write_identical_bytes(self):
        a, b = self.tree(self.run_in_copy()), self.tree(self.run_in_copy())
        self.assertEqual(sorted(a), sorted(b))
        for name in a:
            self.assertEqual(a[name], b[name], name)
        written = set(a) - {"generate.py", "expected.json"}
        self.assertEqual(written, set(EXPECTED["fixtures"]))
        self.assertEqual(read_json(os.path.join(IMAGE_FIX, "expected.json")),
                         json.loads(a["expected.json"].decode("utf-8")))


if __name__ == "__main__":
    unittest.main()
