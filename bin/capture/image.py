"""capture.image — the envelope check of an image file (capture layer §21.2, decisions I1 and I7).

`inspect` decides what the engine accepts as a screenshot, before any reader sees the file: the size, the
signature, a walk of the container, the EXIF orientation, the pixels and the long edge. It is one linear
pass over the file, forward only, bounded by `max_bytes` (a file over the cap is refused from its size,
before a byte is read). It never inflates compressed data and computes no CRC: a PNG chunk and a JPEG
segment are skipped by their declared lengths, and a header that claims 60 000 × 60 000 costs what a
header of 16 × 16 costs. Whether the compressed pixel data decodes is the witness's verdict (§21.3),
never this module's.

The steps run in the spec's order, and the first rule that fires decides: (1) the size; (2) the
signature; (3) the PNG chunk walk or (4) the JPEG marker walk, rule by rule in file order; (5) the EXIF
orientation of the one `eXIf` chunk or `Exif` APP1 segment the walk kept; (6) the pixels, then the long
edge. A field holds what the check had read when it stopped: `bytes` always; `detected`, `format` and
`extension_matches` once the signature is read; `width`, `height` and `pixels` once the image header
(`IHDR`, the first frame header) is read, zeros included; `trailing_bytes` once the end marker (`IEND`,
the last `EOI`) is found; `orientation` once step 5 is reached — 1 without EXIF, the value read
otherwise, `None` when the EXIF cannot be read. `input_extension` comes from the name, never from the
file. `extension_matches` is `None` when the format or the extension is unknown.

Reasons (`Envelope.reason`, a short lowercase token; `None` when the outcome is `ok`):

    too-large     bytes · pixels · edge
    wrong-format  signature (not a PNG or JPEG signature) · animated (a PNG with an `acTL` chunk)
    truncated     no-iend · chunk-past-end (PNG) · no-sos · segment-past-end · no-eoi (JPEG) ·
                  short-read (the file shrank while it was read)
    corrupt       ihdr-missing · ihdr-twice · ihdr-length · ihdr-zero · ihdr-depth · no-idat (PNG) ·
                  marker · segment-length · frame-length · frame-zero · frame-conflict · no-frame ·
                  no-scan (JPEG) · exif-twice · exif-header · exif-ifd · exif-entry · exif-type ·
                  orientation-value (EXIF)
    rotated       orientation

`python3 bin/capture/image.py --signature <file>` prints `png`, `jpg` or nothing and exits 0: the
gateway takes the raw's extension from it (capture contracts A15 §3.2). Stdlib only, python 3.9.
"""
from __future__ import annotations

import errno
import os
import stat
import struct
import sys
from typing import NamedTuple, Optional, Tuple

ENVELOPE = "mnemon-image-envelope/1"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1")
AVIF_BRANDS = (b"avif", b"avis")

# PNG colour type → the bit depths it allows (PNG specification, IHDR)
PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
# JPEG frame headers: SOF0–SOF15, except DHT (C4), JPG (C8) and DAC (CC)
JPEG_FRAMES = frozenset(m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC))
EXIF_PREFIX = b"Exif\x00\x00"
ORIENTATION_TAG = 0x0112
SHORT = 3

BLOCK = 65536

UNSUPPORTED = ("wrong-format", "truncated", "corrupt", "rotated")
OUTCOMES = ("ok",) + UNSUPPORTED + ("too-large",)


class Envelope(NamedTuple):
    outcome: str                    # ok | wrong-format | truncated | corrupt | rotated | too-large
    reason: Optional[str]           # None when ok; a short lowercase token otherwise (module docstring)
    detected: Optional[str]         # png | jpeg | apng | gif | webp | tiff | bmp | heif | avif | pdf | unknown
    format: Optional[str]           # png | jpeg, None unless the signature is one of the two
    input_extension: Optional[str]  # lowercased extension of input_name, without the dot; None without one
    extension_matches: Optional[bool]
    width: Optional[int]
    height: Optional[int]
    pixels: Optional[int]
    bytes: Optional[int]
    orientation: Optional[int]
    trailing_bytes: Optional[int]


FIELDS = Envelope._fields


class _Short(Exception):
    """The file ended before a length its own size promised: it changed while it was read."""


class _Refusal(Exception):
    def __init__(self, outcome: str, reason: str):
        Exception.__init__(self, outcome, reason)
        self.outcome = outcome
        self.reason = reason


class _Reader:
    """Exact reads at absolute positions inside [0, size); a short read raises `_Short`."""

    def __init__(self, fh, size: int):
        self.fh = fh
        self.size = size

    def read(self, pos: int, n: int) -> bytes:
        self.fh.seek(pos)
        data = self.fh.read(n)
        if len(data) != n:
            raise _Short()
        return data


# --- signature (step 2) -----------------------------------------------------------------------------------

def sniff(head: bytes) -> Tuple[Optional[str], str]:
    """(format, detected) from the first 16 bytes: the signature rule of §21.2 step 2, alone."""
    head = bytes(head[:16])
    if head.startswith(PNG_SIGNATURE):
        return "png", "png"
    if head.startswith(JPEG_SIGNATURE):
        return "jpeg", "jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return None, "gif"
    if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return None, "webp"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return None, "tiff"
    if head.startswith(b"BM"):
        return None, "bmp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in HEIF_BRANDS:
            return None, "heif"
        if brand in AVIF_BRANDS:
            return None, "avif"
    if head.startswith(b"%PDF-"):
        return None, "pdf"
    return None, "unknown"


def raw_extension(head: bytes) -> Optional[str]:
    """The raw's extension by signature (capture contracts A15 §3.2): "png", "jpg" or None."""
    fmt, _ = sniff(head)
    return {"png": "png", "jpeg": "jpg"}.get(fmt) if fmt else None


def _extension(input_name) -> Optional[str]:
    if not input_name:
        return None
    ext = os.path.splitext(os.path.basename(str(input_name)))[1]
    return ext[1:].lower() if len(ext) > 1 else None


def _matches(fmt: Optional[str], ext: Optional[str]) -> Optional[bool]:
    if fmt is None or ext is None:
        return None
    if fmt == "jpeg":
        return ext in ("jpg", "jpeg")
    return ext == fmt


# --- EXIF orientation (step 5) ----------------------------------------------------------------------------

def _orientation(tiff: bytes) -> Tuple[Optional[int], Optional[str]]:
    """(orientation, None) or (value or None, a corrupt reason) of one TIFF structure, bounded by it."""
    if len(tiff) < 8 or tiff[0:4] not in (b"II*\x00", b"MM\x00*"):
        return None, "exif-header"
    order = "<" if tiff[0:2] == b"II" else ">"
    ifd = struct.unpack(order + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return None, "exif-ifd"
    count = struct.unpack(order + "H", tiff[ifd:ifd + 2])[0]
    if ifd + 2 + 12 * count > len(tiff):
        return None, "exif-entry"
    for i in range(count):
        at = ifd + 2 + 12 * i
        tag, typ, n = struct.unpack(order + "HHI", tiff[at:at + 8])
        if tag != ORIENTATION_TAG:
            continue
        if typ != SHORT or n != 1:
            return None, "exif-type"
        value = struct.unpack(order + "H", tiff[at + 8:at + 10])[0]
        if not 1 <= value <= 8:
            return value, "orientation-value"
        return value, None
    return 1, None


# --- PNG container (step 3) -------------------------------------------------------------------------------

def _walk_png(r: _Reader, f: dict):
    """The chunk walk from offset 8. Returns the EXIF result of the one `eXIf` chunk, or None."""
    pos, first, idat, exif = 8, True, False, None
    while True:
        if pos + 8 > r.size:
            raise _Refusal("truncated", "no-iend" if pos == r.size else "chunk-past-end")
        length, ctype = struct.unpack(">I4s", r.read(pos, 8))
        if first and ctype != b"IHDR":
            raise _Refusal("corrupt", "ihdr-missing")
        if not first and ctype == b"IHDR":
            raise _Refusal("corrupt", "ihdr-twice")
        if ctype == b"IHDR" and length != 13:
            raise _Refusal("corrupt", "ihdr-length")
        end = pos + 12 + length                      # length, type, data, CRC
        if end > r.size:
            raise _Refusal("truncated", "chunk-past-end")
        if ctype == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", r.read(pos + 8, 10))
            f["width"], f["height"], f["pixels"] = width, height, width * height
            if width == 0 or height == 0:
                raise _Refusal("corrupt", "ihdr-zero")
            if depth not in PNG_DEPTHS.get(colour, ()):
                raise _Refusal("corrupt", "ihdr-depth")
        elif ctype == b"IDAT":
            idat = True
        elif ctype == b"acTL":
            f["detected"] = "apng"
            raise _Refusal("wrong-format", "animated")
        elif ctype == b"eXIf":
            if exif is not None:
                raise _Refusal("corrupt", "exif-twice")
            exif = _orientation(r.read(pos + 8, length))
        elif ctype == b"IEND":
            f["trailing_bytes"] = r.size - end
            if not idat:
                raise _Refusal("corrupt", "no-idat")
            return exif
        first = False
        pos = end


# --- JPEG container (step 4) ------------------------------------------------------------------------------

def _skip_fill(r: _Reader, pos: int) -> int:
    """The position of the first byte at or after `pos` that is not an `FF` fill byte (or the size).

    The blocks start small and double, so a marker costs a few bytes and a run of fill bytes stays linear."""
    n = 16
    while pos < r.size:
        block = r.read(pos, min(n, r.size - pos))
        rest = block.lstrip(b"\xff")
        if rest:
            return pos + len(block) - len(rest)
        pos += len(block)
        n = min(2 * n, BLOCK)
    return pos


def _last_eoi(r: _Reader, pos: int) -> int:
    """The position of the last `FF D9` at or after `pos`, or -1. Forward, block by block."""
    last, carry = -1, b""
    while pos < r.size:
        n = min(BLOCK, r.size - pos)
        block = carry + r.read(pos, n)
        i = block.rfind(b"\xff\xd9")
        if i >= 0:
            last = pos - len(carry) + i
        carry = block[-1:]
        pos += n
    return last


def _walk_jpeg(r: _Reader, f: dict):
    """The marker walk from SOI to the first SOS, then the last EOI. Returns the EXIF result, or None."""
    pos, frame, exif = 2, None, None
    while True:
        if pos >= r.size:
            raise _Refusal("truncated", "no-sos")
        if r.read(pos, 1) != b"\xff":
            raise _Refusal("corrupt", "marker")
        pos = _skip_fill(r, pos)
        if pos >= r.size:
            raise _Refusal("truncated", "no-sos")
        marker = r.read(pos, 1)[0]
        pos += 1
        if marker == 0x01:                           # TEM: a marker without a segment
            continue
        if marker == 0xD9:
            raise _Refusal("corrupt", "no-scan")
        if marker == 0x00 or 0xD0 <= marker <= 0xD8:
            raise _Refusal("corrupt", "marker")
        if pos + 2 > r.size:
            raise _Refusal("truncated", "segment-past-end")
        length = struct.unpack(">H", r.read(pos, 2))[0]
        if length < 2:
            raise _Refusal("corrupt", "segment-length")
        end = pos + length
        if end > r.size:
            raise _Refusal("truncated", "segment-past-end")
        if marker in JPEG_FRAMES:
            if length < 8:
                raise _Refusal("corrupt", "frame-length")
            height, width = struct.unpack(">HH", r.read(pos + 3, 4))
            if frame is None:
                frame = (width, height)
                f["width"], f["height"], f["pixels"] = width, height, width * height
                if width == 0 or height == 0:
                    raise _Refusal("corrupt", "frame-zero")
            elif (width, height) != frame:
                raise _Refusal("corrupt", "frame-conflict")
        elif marker == 0xE1 and length - 2 >= len(EXIF_PREFIX):
            if r.read(pos + 2, len(EXIF_PREFIX)) == EXIF_PREFIX:
                if exif is not None:
                    raise _Refusal("corrupt", "exif-twice")
                start = pos + 2 + len(EXIF_PREFIX)
                exif = _orientation(r.read(start, end - start))
        elif marker == 0xDA:
            if frame is None:
                raise _Refusal("corrupt", "no-frame")
            last = _last_eoi(r, end)
            if last < 0:
                raise _Refusal("truncated", "no-eoi")
            f["trailing_bytes"] = r.size - (last + 2)
            return exif
        pos = end


# --- the check --------------------------------------------------------------------------------------------

def _envelope(f: dict, outcome: str, reason: Optional[str]) -> Envelope:
    f["outcome"], f["reason"] = outcome, reason
    return Envelope(**f)


def inspect(path, *, input_name, max_bytes, max_pixels, max_edge) -> Envelope:
    """The envelope check of §21.2. Raises OSError when the file cannot be opened or read."""
    f = dict.fromkeys(FIELDS)
    f["input_extension"] = _extension(input_name)
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise OSError(errno.EINVAL, "not a regular file", str(path))
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        f["bytes"] = size
        if size > max_bytes:                                            # step 1
            return _envelope(f, "too-large", "bytes")
        fmt, detected = sniff(fh.read(16))                              # step 2
        f["format"], f["detected"] = fmt, detected
        f["extension_matches"] = _matches(fmt, f["input_extension"])
        if fmt is None:
            return _envelope(f, "wrong-format", "signature")
        r = _Reader(fh, size)
        try:
            exif = _walk_png(r, f) if fmt == "png" else _walk_jpeg(r, f)    # steps 3 and 4
        except _Refusal as refusal:
            return _envelope(f, refusal.outcome, refusal.reason)
        except _Short:
            return _envelope(f, "truncated", "short-read")
    orientation, bad = exif if exif is not None else (1, None)          # step 5
    f["orientation"] = orientation
    if bad:
        return _envelope(f, "corrupt", bad)
    if orientation != 1:
        return _envelope(f, "rotated", "orientation")
    if f["pixels"] > max_pixels:                                        # step 6
        return _envelope(f, "too-large", "pixels")
    if max(f["width"], f["height"]) > max_edge:
        return _envelope(f, "too-large", "edge")
    return _envelope(f, "ok", None)


def as_dict(envelope: Envelope) -> dict:
    """The report's `validation` object without `limits` (the worker adds them): the twelve keys, in order."""
    return {k: getattr(envelope, k) for k in FIELDS}


def failure_of(envelope: Envelope) -> Optional[dict]:
    """None when ok; else the effective failure classification of an envelope refusal (§21.2, §21.7)."""
    if envelope.outcome == "ok":
        return None
    klass = "too-large" if envelope.outcome == "too-large" else "unsupported"
    return {"class": klass, "acquisition": envelope.outcome, "reason": envelope.reason, "retryable": False}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--signature":
        sys.stderr.write("usage: image.py --signature <file>\n")
        return 2
    try:
        with open(args[1], "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        sys.stderr.write("image.py: cannot read the file: %s\n" % (exc.strerror or exc.__class__.__name__))
        return 0
    ext = raw_extension(head)
    if ext:
        sys.stdout.write(ext + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
