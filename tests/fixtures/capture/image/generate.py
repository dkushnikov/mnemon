#!/usr/bin/env python3
"""Generate the envelope fixtures of the image origin (docs/specs/2026-10-15-capture-layer.md §21.2, §21.13
«Envelope fixtures»).

    python3 tests/fixtures/capture/image/generate.py     # rewrites the fixtures next to this script

A base pair and byte surgery on it. `base.png` (16 × 16, 8-bit RGB) is written here with `zlib` and
`struct`; its deflate stream is one stored block, so the bytes are the same under every zlib build.
`base.jpg` is an 8 × 8 baseline JPEG (SOF0) built here from literal segments: a JFIF APP0, one
quantization table of ones, three components without subsampling, a DC and an AC Huffman table of one
code each, and three non-interleaved scans of one block each (DC difference 0, end of block) — a flat
mid-grey image. It holds no APP1 segment and no text but the `JFIF` tag; its sha256 is pinned below and
the generator refuses to write a base that differs. Every other fixture is byte surgery on the pair; the
twelve-byte heads under `heads/` are the signatures of the formats the envelope refuses by name.

`expected.json` holds, per fixture, the values the rules of §21.2 give for the file as built here — the
outcome, its reason token (`bin/capture/image.py`), `detected`, `format`, `extension_matches`, the
dimensions, the orientation and the trailing bytes — under the default limits of §21.8. They are written
by hand beside each construction, never read back from the check. Edit this generator, never the files;
the hygiene suite checks that the committed files equal its output. Stdlib only, python 3.9.
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent
LIMITS = {"max_bytes": 7000000, "max_pixels": 50000000, "max_edge": 8000}
BASE_JPG_SHA256 = "710fa5d84ef47a3d4dbc47b800123d9f1ed8f7acd94fa8e8e27512dd9ccc6aa8"

# --- PNG ----------------------------------------------------------------------------------------------------

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def chunk(ctype: bytes, data: bytes) -> bytes:
    """One PNG chunk with a valid CRC."""
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)


def ihdr(width: int, height: int, depth: int = 8, colour: int = 2) -> bytes:
    return chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0))


def stored_zlib(raw: bytes) -> bytes:
    """A zlib stream of stored deflate blocks: no compressor, so no build-dependent bytes."""
    out = bytearray(b"\x78\x01")
    blocks = [raw[i:i + 65535] for i in range(0, len(raw), 65535)] or [b""]
    for i, block in enumerate(blocks):
        out += bytes([1 if i == len(blocks) - 1 else 0])
        out += struct.pack("<HH", len(block), len(block) ^ 0xFFFF) + block
    out += struct.pack(">I", zlib.adler32(raw) & 0xFFFFFFFF)
    return bytes(out)


def base_pixels(width: int = 16, height: int = 16) -> bytes:
    """Filter byte 0, then RGB: red grows to the right, green downwards, blue fixed."""
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row += bytes([(x * 16) & 0xFF, (y * 16) & 0xFF, 0x80])
        rows.append(bytes(row))
    return b"".join(rows)


IDAT = chunk(b"IDAT", stored_zlib(base_pixels()))
IEND = chunk(b"IEND", b"")


def png_file(width: int = 16, height: int = 16, *, before=(), idat: bytes = IDAT, after=()) -> bytes:
    """Signature, IHDR, the chunks `before`, IDAT, the chunks `after`, IEND."""
    return PNG_SIG + ihdr(width, height) + b"".join(before) + idat + b"".join(after) + IEND


# --- JPEG ---------------------------------------------------------------------------------------------------

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


APP0 = segment(0xE0, b"JFIF\x00" + b"\x01\x01" + b"\x00" + b"\x00\x01\x00\x01" + b"\x00\x00")
DQT = segment(0xDB, b"\x00" + bytes([1] * 64))
DHT_DC = segment(0xC4, b"\x00" + bytes([1] + [0] * 15) + b"\x00")      # one code '0': DC category 0
DHT_AC = segment(0xC4, b"\x10" + bytes([1] + [0] * 15) + b"\x00")      # one code '0': end of block
BLOCK_DATA = b"\x3f"                     # '0' (DC difference 0) + '0' (EOB), padded with ones
BROKEN_DATA = b"\xff\x00\xff\x00"        # all ones: no Huffman code of either table starts with '1'


def sof0(width: int, height: int) -> bytes:
    return segment(0xC0, struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00")


def sos(component: int) -> bytes:
    return segment(0xDA, bytes([1, component, 0x00, 0, 63, 0]))


def scans(third: bytes = BLOCK_DATA) -> bytes:
    return sos(1) + BLOCK_DATA + sos(2) + BLOCK_DATA + sos(3) + third


def jpeg_file(width: int = 8, height: int = 8, *, app=(), frames=None, third: bytes = BLOCK_DATA,
              eoi: bool = True) -> bytes:
    """SOI, APP0, the segments `app`, DQT, the frame headers, the two DHT, the three scans, EOI."""
    frames = [sof0(width, height)] if frames is None else list(frames)
    return (SOI + APP0 + b"".join(app) + DQT + b"".join(frames) + DHT_DC + DHT_AC + scans(third)
            + (EOI if eoi else b""))


# --- EXIF ---------------------------------------------------------------------------------------------------

def tiff(orientation: int = 1, *, order: str = "II", typ: int = 3, count: int = 1, ifd: int = 8,
         entries=None, header: bytes = None) -> bytes:
    """A TIFF structure: the header, IFD0 with Orientation and ResolutionUnit, the next-IFD offset 0.

    `entries` overrides the IFD's declared entry count; `ifd` the offset of IFD0; `header` the first four
    bytes."""
    o = "<" if order == "II" else ">"
    head = header if header is not None else (b"II*\x00" if order == "II" else b"MM\x00*")
    if typ == 3:
        orient = struct.pack(o + "HHIHH", 0x0112, typ, count, orientation, 0)
    else:
        orient = struct.pack(o + "HHII", 0x0112, typ, count, orientation)
    unit = struct.pack(o + "HHIHH", 0x0128, 3, 1, 2, 0)
    body = struct.pack(o + "H", 2 if entries is None else entries) + orient + unit + struct.pack(o + "I", 0)
    return head + struct.pack(o + "I", ifd) + body


def app1_exif(payload: bytes) -> bytes:
    return segment(0xE1, b"Exif\x00\x00" + payload)


def exif_chunk(payload: bytes) -> bytes:
    return chunk(b"eXIf", payload)


# --- the fixtures -------------------------------------------------------------------------------------------

def expect(outcome, reason, detected, fmt, matches, width, height, orientation, trailing) -> dict:
    return {"outcome": outcome, "reason": reason, "detected": detected, "format": fmt,
            "extension_matches": matches, "width": width, "height": height,
            "orientation": orientation, "trailing_bytes": trailing}


def png_ok() -> dict:
    return expect("ok", None, "png", "png", True, 16, 16, 1, 0)


def build():
    """{name: (bytes, expected)} for every fixture."""
    base_png = png_file()
    base_jpg = jpeg_file()
    digest = hashlib.sha256(base_jpg).hexdigest()
    if digest != BASE_JPG_SHA256:
        raise SystemExit("base.jpg differs from its pinned sha256 %s: %s" % (BASE_JPG_SHA256, digest))
    if b"Exif" in base_jpg or b"\xff\xe1" in base_jpg:
        raise SystemExit("base.jpg must hold no APP1 segment")

    cut = base_png.index(b"IDAT") - 4                         # the IDAT chunk's start
    idat_mid = cut + 8 + 100                                  # inside its data
    iend_at = len(base_png) - len(IEND)
    jpg_dht_ac = base_jpg.index(DHT_AC)

    T = 10
    fx = {
        # --- PNG ---
        "base.png": (base_png, png_ok()),
        "truncated.png": (base_png[:iend_at],
                          expect("truncated", "no-iend", "png", "png", True, 16, 16, None, None)),
        "chunk-past-end.png": (base_png[:idat_mid],
                               expect("truncated", "chunk-past-end", "png", "png", True, 16, 16, None, None)),
        "ihdr0.png": (png_file(0, 0),
                      expect("corrupt", "ihdr-zero", "png", "png", True, 0, 0, None, None)),
        "ihdr-twice.png": (png_file(before=[ihdr(16, 16)]),
                           expect("corrupt", "ihdr-twice", "png", "png", True, 16, 16, None, None)),
        "no-idat.png": (PNG_SIG + ihdr(16, 16) + IEND,
                        expect("corrupt", "no-idat", "png", "png", True, 16, 16, None, 0)),
        "apng.png": (png_file(before=[chunk(b"acTL", struct.pack(">II", 1, 0))]),
                     expect("wrong-format", "animated", "apng", "png", True, 16, 16, None, None)),
        "huge.png": (png_file(60000, 60000, idat=chunk(b"IDAT", stored_zlib(b"\x00" * 16))),
                     expect("too-large", "pixels", "png", "png", True, 60000, 60000, 1, 0)),
        "idat-broken.png": (png_file(idat=chunk(b"IDAT", b"\x78\x9c" + bytes(range(7, 247)))),
                            png_ok()),
        "trailing.png": (base_png + bytes(range(1, T + 1)),
                         expect("ok", None, "png", "png", True, 16, 16, 1, T)),
        "exif6.png": (png_file(before=[exif_chunk(tiff(6, order="MM"))]),
                      expect("rotated", "orientation", "png", "png", True, 16, 16, 6, 0)),
        "exif-bad.png": (png_file(before=[exif_chunk(tiff(1, header=b"XX*\x00"))]),
                         expect("corrupt", "exif-header", "png", "png", True, 16, 16, None, 0)),
        # --- JPEG ---
        "base.jpg": (base_jpg, expect("ok", None, "jpeg", "jpeg", True, 8, 8, 1, 0)),
        "exif1.jpg": (jpeg_file(app=[app1_exif(tiff(1))]),
                      expect("ok", None, "jpeg", "jpeg", True, 8, 8, 1, 0)),
        "exif6.jpg": (jpeg_file(app=[app1_exif(tiff(6, order="MM"))]),
                      expect("rotated", "orientation", "jpeg", "jpeg", True, 8, 8, 6, 0)),
        "exif3.jpg": (jpeg_file(app=[app1_exif(tiff(3))]),
                      expect("rotated", "orientation", "jpeg", "jpeg", True, 8, 8, 3, 0)),
        "exif-mirrored.jpg": (jpeg_file(app=[app1_exif(tiff(2, order="MM"))]),
                              expect("rotated", "orientation", "jpeg", "jpeg", True, 8, 8, 2, 0)),
        "exif-bad-ifd.jpg": (jpeg_file(app=[app1_exif(tiff(1, ifd=0x1000))]),
                             expect("corrupt", "exif-ifd", "jpeg", "jpeg", True, 8, 8, None, 0)),
        "exif-type.jpg": (jpeg_file(app=[app1_exif(tiff(1, typ=4))]),
                          expect("corrupt", "exif-type", "jpeg", "jpeg", True, 8, 8, None, 0)),
        "noeoi.jpg": (jpeg_file(eoi=False),
                      expect("truncated", "no-eoi", "jpeg", "jpeg", True, 8, 8, None, None)),
        "truncated.jpg": (base_jpg[:jpg_dht_ac + 10],
                          expect("truncated", "segment-past-end", "jpeg", "jpeg", True, 8, 8, None, None)),
        "nosof.jpg": (jpeg_file(frames=[]),
                      expect("corrupt", "no-frame", "jpeg", "jpeg", True, None, None, None, None)),
        "sof-conflict.jpg": (jpeg_file(frames=[sof0(8, 8), sof0(16, 8)]),
                             expect("corrupt", "frame-conflict", "jpeg", "jpeg", True, 8, 8, None, None)),
        "scan-broken.jpg": (jpeg_file(third=BROKEN_DATA),
                            expect("ok", None, "jpeg", "jpeg", True, 8, 8, 1, 0)),
        # --- kept apart: the name never decides ---
        "jpeg-named.png": (base_jpg, expect("ok", None, "jpeg", "jpeg", False, 8, 8, 1, 0)),
        "text-named.png": (b"This file is text, not an image.\n",
                           expect("wrong-format", "signature", "unknown", None, None, None, None, None, None)),
        # --- twelve-byte signature heads of the formats refused by name ---
        "heads/heif.heic": (b"\x00\x00\x00\x18ftypheic",
                            expect("wrong-format", "signature", "heif", None, None, None, None, None, None)),
        "heads/avif.avif": (b"\x00\x00\x00\x1cftypavif",
                            expect("wrong-format", "signature", "avif", None, None, None, None, None, None)),
        "heads/webp.webp": (b"RIFF\x24\x00\x00\x00WEBP",
                            expect("wrong-format", "signature", "webp", None, None, None, None, None, None)),
        "heads/gif.gif": (b"GIF89a\x10\x00\x10\x00\x80\x00",
                          expect("wrong-format", "signature", "gif", None, None, None, None, None, None)),
        "heads/tiff.tif": (b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00",
                           expect("wrong-format", "signature", "tiff", None, None, None, None, None, None)),
        "heads/bmp.bmp": (b"BM\x46\x00\x00\x00\x00\x00\x00\x00\x36\x00",
                          expect("wrong-format", "signature", "bmp", None, None, None, None, None, None)),
        "heads/pdf.pdf": (b"%PDF-1.7\n1 0",
                          expect("wrong-format", "signature", "pdf", None, None, None, None, None, None)),
    }
    for name, (data, _) in fx.items():
        if name.startswith("heads/") and len(data) != 12:
            raise SystemExit("%s: a head is twelve bytes, not %d" % (name, len(data)))
    return fx, digest


def main() -> None:
    fx, digest = build()
    (OUT / "heads").mkdir(exist_ok=True)
    for name, (data, _) in fx.items():
        (OUT / name).write_bytes(data)
    expected = {
        "schema": "mnemon-image-envelope-fixtures/1",
        "envelope": "mnemon-image-envelope/1",
        "limits": LIMITS,
        "base_jpg_sha256": digest,
        "fixtures": {name: exp for name, (_, exp) in sorted(fx.items())},
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
