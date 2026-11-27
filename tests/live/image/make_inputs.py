#!/usr/bin/env python3
"""make_inputs.py — the derived inputs of the live image gate (capture layer §21.13, the last row).

    make_inputs.py --out <dir>

Writes into <dir>, from two committed images beside this file, and prints one JSON line per file
(`name`, `width`, `height`, `bytes`, `sha256`):

  column-4200.png   the 8-bit grayscale post (img/post-en-gray.png, 1170 × 2100) stacked twice: 1170 × 4200
  column-8000.png   four copies of the post's rows 36 to 2035 — every row with ink, the white margins cut to
                    2 000 rows a copy, so that each copy holds the whole post: 1170 × 8000, the long-edge cap
  column-8001.png   column-8000 with one white row under it: 1170 × 8001, one pixel over the cap
  quote-exif6.jpg   the quote card (img/quote-short.jpg) with its EXIF segment replaced by one that holds
                    the orientation tag alone, value 6; every other byte is the card's (the pixels stay upright:
                    the envelope check refuses by the tag, before any decoding)

PNG is decoded and encoded with `zlib` and `struct` (8-bit grayscale, not interlaced — the only kind this
script reads); the PNG it writes has one IDAT, compressed at level 9, every row under filter 0. The same
interpreter writes the same bytes on every run. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
GRAY_POST = os.path.join(HERE, "img", "post-en-gray.png")
QUOTE_CARD = os.path.join(HERE, "img", "quote-short.jpg")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CROP_TOP = 36           # the first row of a copy in column-8000
CROP_ROWS = 2000        # the rows of a copy in column-8000
WHITE = 255
ORIENTATION_TAG = 0x0112


class InputError(Exception):
    pass


# --- PNG ------------------------------------------------------------------------------------------------------

def _chunks(data: bytes):
    if data[:8] != PNG_SIGNATURE:
        raise InputError("not a PNG signature")
    off = 8
    while off + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[off:off + 8])
        body = data[off + 8:off + 8 + length]
        if len(body) != length or off + 12 + length > len(data):
            raise InputError("a chunk runs past the end of the file")
        yield kind, body
        off += 12 + length
        if kind == b"IEND":
            return
    raise InputError("no IEND chunk")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_gray8(data: bytes):
    """(width, height, rows) of an 8-bit grayscale PNG that is not interlaced; `rows` is a list of bytes."""
    ihdr = None
    idat = []
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
    if ihdr is None:
        raise InputError("no IHDR chunk")
    width, height, depth, colour, compression, filtering, interlace = ihdr
    if (depth, colour, compression, filtering, interlace) != (8, 0, 0, 0, 0):
        raise InputError("not an 8-bit grayscale PNG without interlace")
    raw = zlib.decompress(b"".join(idat))
    if len(raw) != height * (width + 1):
        raise InputError("the image data has %d bytes, not %d" % (len(raw), height * (width + 1)))
    rows = []
    prev = bytearray(width)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + width])
        pos += 1 + width
        if kind == 1:
            for i in range(1, width):
                line[i] = (line[i] + line[i - 1]) & 0xFF
        elif kind == 2:
            for i in range(width):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:
            line[0] = (line[0] + (prev[0] >> 1)) & 0xFF
            for i in range(1, width):
                line[i] = (line[i] + ((line[i - 1] + prev[i]) >> 1)) & 0xFF
        elif kind == 4:
            line[0] = (line[0] + prev[0]) & 0xFF
            for i in range(1, width):
                line[i] = (line[i] + _paeth(line[i - 1], prev[i], prev[i - 1])) & 0xFF
        elif kind != 0:
            raise InputError("unknown filter type %d" % kind)
        rows.append(bytes(line))
        prev = line
    return width, height, rows


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def encode_gray8(width: int, rows) -> bytes:
    """An 8-bit grayscale PNG of `rows` (each `width` bytes): one IDAT at level 9, filter 0 on every row."""
    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, len(rows), 8, 0, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def png_size(data: bytes):
    """(width, height) from the IHDR of a PNG."""
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            return struct.unpack(">II", body[:8])
    raise InputError("no IHDR chunk")


# --- JPEG -----------------------------------------------------------------------------------------------------

def exif_orientation_segment(value: int) -> bytes:
    """An APP1 segment `Exif\\0\\0` + a big-endian TIFF header + IFD0 with one entry: tag 0x0112, SHORT, count 1."""
    tiff = (b"MM\x00*" + struct.pack(">I", 8) + struct.pack(">H", 1)
            + struct.pack(">HHIHH", ORIENTATION_TAG, 3, 1, value, 0) + struct.pack(">I", 0))
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _jpeg_segments(data: bytes):
    """(marker, start, end) of every marker segment from SOI up to the first SOS (that one included)."""
    if data[:2] != b"\xff\xd8":
        raise InputError("not a JPEG signature")
    off = 2
    while off + 4 <= len(data):
        if data[off] != 0xFF:
            raise InputError("no marker at offset %d" % off)
        marker = data[off + 1]
        if marker == 0xFF:
            off += 1
            continue
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            off += 2
            continue
        length = struct.unpack(">H", data[off + 2:off + 4])[0]
        end = off + 2 + length
        if length < 2 or end > len(data):
            raise InputError("a segment runs past the end of the file")
        yield marker, off, end
        if marker == 0xDA:
            return
        off = end
    raise InputError("no SOS marker")


def jpeg_size(data: bytes):
    """(width, height) from the first frame header of a JPEG."""
    for marker, start, _ in _jpeg_segments(data):
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[start + 5:start + 9])
            return width, height
    raise InputError("no frame header")


def with_orientation(data: bytes, value: int) -> bytes:
    """The JPEG with its one EXIF APP1 segment replaced by `exif_orientation_segment(value)`; without one, the
    segment goes after APP0 (when APP0 comes first) or after SOI."""
    exif = None
    insert_at = 2
    first = True
    for marker, start, end in _jpeg_segments(data):
        if first and marker == 0xE0:
            insert_at = end
        first = False
        if marker == 0xE1 and data[start + 4:start + 10] == b"Exif\x00\x00":
            if exif is not None:
                raise InputError("two EXIF segments")
            exif = (start, end)
    segment = exif_orientation_segment(value)
    if exif is not None:
        return data[:exif[0]] + segment + data[exif[1]:]
    return data[:insert_at] + segment + data[insert_at:]


# --- the inputs -----------------------------------------------------------------------------------------------

def columns(width: int, rows):
    """{name: png bytes} of the three columns."""
    blank = bytes([WHITE]) * width
    for index, row in enumerate(rows):
        if (index < CROP_TOP or index >= CROP_TOP + CROP_ROWS) and row != blank:
            raise InputError("row %d of the post has ink: a copy of %d rows from row %d would cut it"
                             % (index, CROP_ROWS, CROP_TOP))
    copy = rows[CROP_TOP:CROP_TOP + CROP_ROWS]
    tall = copy * 4
    return {"column-4200.png": encode_gray8(width, rows * 2),
            "column-8000.png": encode_gray8(width, tall),
            "column-8001.png": encode_gray8(width, tall + [blank])}


def write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.rename(tmp, path)


def make(out_dir: str, gray_post: str = GRAY_POST, quote_card: str = QUOTE_CARD):
    """Write the four inputs into `out_dir`; return one description per file, in the order written."""
    with open(gray_post, "rb") as fh:
        width, _, rows = decode_gray8(fh.read())
    with open(quote_card, "rb") as fh:
        card = fh.read()
    outputs = columns(width, rows)
    outputs["quote-exif6.jpg"] = with_orientation(card, 6)
    os.makedirs(out_dir, exist_ok=True)
    described = []
    for name in sorted(outputs):
        data = outputs[name]
        write(os.path.join(out_dir, name), data)
        w, h = png_size(data) if name.endswith(".png") else jpeg_size(data)
        described.append({"name": name, "width": w, "height": h, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
    return described


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the derived inputs of the live image gate")
    ap.add_argument("--out", required=True, help="the directory to write into (outside the repository)")
    args = ap.parse_args(argv)
    try:
        for item in make(args.out):
            print(json.dumps(item, sort_keys=True))
    except (InputError, OSError, zlib.error) as exc:
        print("make_inputs: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
