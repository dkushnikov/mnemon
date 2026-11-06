"""capture.captions — a WebVTT caption track → capture-text, with the diagnostic completeness check.

The browser lane's media form (GH #12, #26): a caption track that a producer acquired outside the fetch chain
(`mnemon browser-lane youtube …`, `yt-dlp --cookies-from-browser`) is the retained raw of a YouTube capture
(`raw_kind: captions`, `<revision>.vtt`). This module turns that raw into the capture-text and checks the text
against the raw it came from — the same contract capture-text.py applies to a page and its saved HTML:
`capture.completeness` is defined relative to the snapshot, and the check is a diagnostic, never a refusal.

    parse_vtt(text)                 → [Cue]      the cues in order; timing tags, voice/colour tags, positioning
                                                 settings, NOTE/STYLE/REGION blocks and cue identifiers dropped,
                                                 HTML entities decoded
    dedupe(cues)                    → [Line]     YouTube's rolling repeats removed: an automatic track shows the
                                                 previous cue's text as the first line of the next cue, and
                                                 re-displays a cue for a few milliseconds before the next one
    render(lines, chapters)         → str        the capture-text: paragraphs of speech (a new one at a pause of
                                                 PARAGRAPH_GAP seconds or after PARAGRAPH_SPAN seconds), one
                                                 `## <title> (m:ss)` heading per chapter, in chapter order
    check(text, lines, chapters)    → dict       every eligible line (≥ ELIGIBLE_TOKENS tokens) present in the text
                                                 as a token sequence, every chapter heading present → complete,
                                                 else partial, with the counts and up to five missing examples
    convert(vtt_bytes, media, url)  → (text, report)
                                                 the whole: the report is a `mnemon-capture-text/2` document
                                                 (the key set of tests/fixtures/capture/reports/text.json), so the
                                                 gateway reads it through the one path it reads a page's report

Pure: no network, no file I/O (the CLI capture-captions.py does that), no subprocess. Stdlib only, python 3.9.
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import check as checkmod

CONVERTER = "vtt-text"
CONVERTER_VERSION = "1"
FETCH = "browser"                  # the lane that acquired the raw (capture.fetch)
ELIGIBLE_TOKENS = 3                # a line shorter than this is not required by the check (noise: "yeah", "[music]")
REPEAT_WINDOW = 3                  # a line equal to one of the last N emitted lines is a rolling repeat
PARAGRAPH_GAP = 3.0                # seconds of silence that start a new paragraph
PARAGRAPH_SPAN = 60.0              # seconds of speech after which a paragraph ends at the next line
REDISPLAY_MAX = 0.2                # a cue shorter than this that repeats the previous text is a re-display, not speech

_TS = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{3})")
_CUE_LINE = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3})(.*)$")
_TAGS = re.compile(r"<[^>\n]*>")   # <00:00:01.000>, <c>, </c>, <c.colorE5E5E5>, <v Speaker>, <b>, <i>, <u>, <ruby>, <rt>, <lang …>


class Cue:
    __slots__ = ("start", "end", "text")

    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text

    def __repr__(self) -> str:   # pragma: no cover — debugging aid
        return "Cue(%.3f, %.3f, %r)" % (self.start, self.end, self.text)


class Line:
    """One deduplicated line of speech: the text and the time span of the cue that introduced it."""
    __slots__ = ("start", "end", "text")

    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text

    def __repr__(self) -> str:   # pragma: no cover — debugging aid
        return "Line(%.3f, %.3f, %r)" % (self.start, self.end, self.text)


def parse_timestamp(s: str) -> float:
    """`HH:MM:SS.mmm` or `MM:SS.mmm` → seconds (a comma decimal separator, as SRT writes it, is accepted)."""
    m = _TS.fullmatch(s.strip())
    if not m:
        raise ValueError("not a WebVTT timestamp: %r" % s)
    h = int(m.group(1)) if m.group(1) else 0
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0


def format_timestamp(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` from one hour, for a chapter heading."""
    s = max(0, int(seconds))
    if s >= 3600:
        return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)
    return "%d:%02d" % (s // 60, s % 60)


def clean_cue_text(raw: str) -> str:
    """A cue's payload as plain text: every tag removed, entities decoded, whitespace collapsed per line."""
    text = _TAGS.sub("", raw)
    text = html.unescape(text)
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


_BLOCK_HEADS = ("WEBVTT", "NOTE", "STYLE", "REGION")


def _block_head(stripped: str) -> bool:
    """A line that opens a header or comment block: exactly the keyword, or the keyword and a space or tab."""
    for head in _BLOCK_HEADS:
        if stripped == head or stripped.startswith(head + " ") or stripped.startswith(head + "\t"):
            return True
    return False


def parse_vtt(text: str) -> List[Cue]:
    """The cues of a WebVTT document, in file order. A cue starts at a timing line and its payload runs to the
    next timing line (the one line before that timing line, when a blank line separates it from the payload, is
    the next cue's identifier and is dropped). Tolerant of what the real files carry: a missing `WEBVTT` header,
    CRLF, a BOM, `NOTE` / `STYLE` / `REGION` blocks and header metadata (`Kind:`, `Language:`), and the blank or
    whitespace-only first line yt-dlp writes into every YouTube cue — a blank line inside a cue does not end it.
    A cue whose end precedes its start keeps its start as the end; a malformed timing line and its payload are
    skipped; a cue without text is dropped. An SRT body (`,` decimals, numeric identifiers) parses the same way."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: List[Cue] = []
    start = end = None
    payload: List[str] = []       # the current cue's payload lines (settled)
    pending: List[str] = []       # non-blank lines seen after a blank line: the next identifier, or more payload
    skipping = False              # inside a WEBVTT / NOTE / STYLE / REGION block (ends at a blank line)
    at_block_start = True

    def close():
        nonlocal start, end, payload, pending
        if start is not None:
            payload.extend(pending)
            body = clean_cue_text("\n".join(payload))
            if body:
                cues.append(Cue(start, end, body))
        start = end = None
        payload, pending = [], []

    for ln in text.split("\n"):
        stripped = ln.strip()
        if skipping:
            if not stripped:
                skipping = False
                at_block_start = True
            continue
        if not stripped:
            if start is not None:
                payload.extend(pending)
                pending = []
            at_block_start = True
            continue
        m = _CUE_LINE.match(ln)
        if m:
            # the one pending line is the identifier of this cue, not the previous cue's text
            if len(pending) == 1:
                pending = []
            close()
            try:
                start = parse_timestamp(m.group(1))
                end = parse_timestamp(m.group(2))
            except ValueError:
                start = end = None
                continue
            if end < start:
                end = start
            at_block_start = False
            continue
        if at_block_start and _block_head(stripped):
            skipping = True
            continue
        if at_block_start and start is None and not pending:
            # a stray non-cue line between blocks (an identifier whose timing line is malformed, header metadata)
            pending.append(ln)
            at_block_start = False
            continue
        if start is None:
            continue
        if at_block_start:
            pending.append(ln)
            at_block_start = False
        elif pending:
            pending.append(ln)
        else:
            payload.append(ln)
    close()
    return cues


def _key(s: str) -> str:
    return checkmod.line_key(s)


def dedupe(cues: List[Cue]) -> List[Line]:
    """The lines of speech, each once. Two shapes of repetition are removed, both YouTube's: a cue that repeats
    the previous cue's text for a few milliseconds (a re-display between two cues), and the rolling window of
    an automatic track, where every cue's first line is the previous cue's last line. A line equal (under
    `check.line_key`) to one of the last REPEAT_WINDOW emitted lines is dropped; anything else is kept, in
    order, with the time span of the cue that introduced it."""
    out: List[Line] = []
    recent: List[str] = []
    for cue in cues:
        for ln in cue.text.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            k = _key(ln)
            if not k:
                continue
            if k in recent:
                # a re-display or a rolling repeat: extend the previous line's span instead of a new line
                if out and _key(out[-1].text) == k and cue.end > out[-1].end and (cue.end - cue.start) <= REDISPLAY_MAX:
                    out[-1].end = cue.end
                continue
            out.append(Line(cue.start, cue.end, ln))
            recent.append(k)
            if len(recent) > REPEAT_WINDOW:
                recent.pop(0)
    return out


def chapters_of(media: Optional[dict]) -> List[dict]:
    """The chapters of a `mnemon-media/1` record, each `{start, end, title}` with numbers and a one-line title,
    sorted by start; malformed entries are skipped (a producer's chapters are data, never a crash)."""
    raw = (media or {}).get("chapters")
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for ch in raw:
        if not isinstance(ch, dict):
            continue
        title = ch.get("title")
        try:
            start = float(ch.get("start", ch.get("start_time")))
        except (TypeError, ValueError):
            continue
        end = ch.get("end", ch.get("end_time"))
        try:
            end_f = float(end) if end is not None else None
        except (TypeError, ValueError):
            end_f = None
        if not isinstance(title, str) or not title.strip():
            continue
        out.append({"start": start, "end": end_f, "title": " ".join(title.split())})
    out.sort(key=lambda c: c["start"])
    return out


def heading(ch: dict) -> str:
    return "## %s (%s)" % (ch["title"], format_timestamp(ch["start"]))


def render(lines: List[Line], chapters: Optional[List[dict]] = None) -> str:
    """The capture-text. Paragraphs are the lines joined with single spaces; a paragraph ends at a pause of
    PARAGRAPH_GAP seconds, after PARAGRAPH_SPAN seconds of speech, or at a chapter boundary. Every chapter
    gets exactly one heading `## <title> (m:ss)`, in order, at the first line that starts at or after its
    start — a chapter no line falls into still gets its heading (before the next chapter's), so the text's
    structure is the video's. Without chapters the text is paragraphs only; the last line ends with `\\n`."""
    chapters = list(chapters or [])
    parts: List[str] = []
    para: List[str] = []
    para_start = None
    prev_end = None
    ci = 0

    def flush():
        nonlocal para, para_start
        if para:
            parts.append(" ".join(para))
            para = []
            para_start = None

    for ln in lines:
        while ci < len(chapters) and ln.start >= chapters[ci]["start"] - 1e-9:
            flush()
            parts.append(heading(chapters[ci]))
            ci += 1
        if para and ((prev_end is not None and ln.start - prev_end >= PARAGRAPH_GAP)
                     or (para_start is not None and ln.start - para_start >= PARAGRAPH_SPAN)):
            flush()
        if not para:
            para_start = ln.start
        para.append(ln.text)
        prev_end = ln.end
    flush()
    while ci < len(chapters):
        parts.append(heading(chapters[ci]))
        ci += 1
    return ("\n\n".join(parts) + "\n") if parts else ""


class _Index:
    def __init__(self, toks: List[str]):
        self.toks = toks
        self.first: Dict[str, List[int]] = defaultdict(list)
        for i, t in enumerate(toks):
            self.first[t].append(i)

    def contains(self, seq: List[str]) -> bool:
        if not seq:
            return True
        n = len(seq)
        for i in self.first.get(seq[0], ()):
            if self.toks[i:i + n] == seq:
                return True
        return False


def check(text: str, lines: List[Line], chapters: Optional[List[dict]] = None) -> dict:
    """The conversion check of a captions capture: the reference is the deduplicated lines, a line is eligible
    when it has at least ELIGIBLE_TOKENS tokens, and it is present when its token sequence occurs in the text's
    token stream (`check.tokens`: NFKC, casefold, alphanumeric runs — so markup and punctuation never count);
    every chapter heading must be a `## ` line of the text. `complete` when nothing is missing, else `partial`.
    Glued lines cannot occur (the renderer joins with spaces), so `glued_count` is always 0 and recorded as such."""
    body_lines = text.split("\n")
    idx = _Index(checkmod.tokens(text))
    reference = [ln.text for ln in lines]
    eligible = [ln for ln in reference if len(checkmod.tokens(ln)) >= ELIGIBLE_TOKENS]
    missing = [ln for ln in eligible if not idx.contains(checkmod.tokens(ln))]
    heads = [heading(ch) for ch in (chapters or [])]
    present_heads = set(bl.strip() for bl in body_lines if bl.startswith("## "))
    heads_missing = [h for h in heads if h not in present_heads]
    return {
        "reference_count": len(reference),
        "eligible_count": len(eligible),
        "missing_count": len(missing),
        "glued_count": 0,
        "headings_count": len(heads),
        "headings_missing_count": len(heads_missing),
        "missing_examples": missing[:5],
        "completeness": "complete" if not missing and not heads_missing else "partial",
    }


def _duration_seconds(media: Optional[dict]) -> Optional[float]:
    """`duration` of a media record: `H:MM:SS` (the record's form) or a number of seconds."""
    d = (media or {}).get("duration")
    if d is None:
        return None
    if isinstance(d, (int, float)):
        return float(d)
    if isinstance(d, str):
        parts = d.strip().split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        secs = 0.0
        for n in nums:
            secs = secs * 60 + n
        return secs
    return None


def _published_candidates(media: Optional[dict]) -> List[dict]:
    """The dated candidate of the record (`upload_date` as YYYYMMDD → ISO), in the shape of the text report."""
    u = (media or {}).get("upload_date")
    if isinstance(u, str) and re.fullmatch(r"\d{8}", u.strip()):
        u = u.strip()
        return [{"value": "%s-%s-%s" % (u[:4], u[4:6], u[6:]), "source": "yt-dlp upload_date"}]
    return []


def convert(vtt: bytes, media: Optional[dict], url: str) -> Tuple[str, dict]:
    """The capture-text and the `mnemon-capture-text/2` report of a caption raw. `media` is the bundle's
    `mnemon-media/1` record (its `chapters`, `title`, `channel`, `duration`, `upload_date` are used; None is
    allowed). An undecodable raw raises UnicodeDecodeError (the CLI maps it to `invalid-utf8`)."""
    text_in = vtt.decode("utf-8")
    cues = parse_vtt(text_in)
    lines = dedupe(cues)
    chapters = chapters_of(media)
    text = render(lines, chapters)
    chk = check(text, lines, chapters)
    words = len(text.split())
    nbytes = len(text.encode("utf-8"))
    duration = _duration_seconds(media)
    covered = max((ln.end for ln in lines), default=0.0)
    coverage = (round(covered / duration, 3) if duration and duration > 0 else None)
    title = (media or {}).get("title")
    channel = (media or {}).get("channel")
    note = "%d cue(s) → %d line(s); %d chapter(s)" % (len(cues), len(lines), len(chapters))
    if coverage is not None:
        note += "; captions cover %.0f%% of the video's duration" % (coverage * 100)
    report = {
        "schema": "mnemon-capture-text/2",
        "url": url,
        "method": "%s+%s" % (FETCH, CONVERTER),
        "bytes": nbytes,
        "words": words,
        "title": title if isinstance(title, str) and title.strip() else None,
        "completeness": chk["completeness"] if text else "unknown",
        "tool": {"name": CONVERTER, "version": CONVERTER_VERSION},
        "error_class": None if text else "convert",
        "error": None if text else "the caption track holds no text",
        "retryable": None if text else False,
        "stage": None if text else "convert",
        "converter": CONVERTER,
        "acquisition": "ok",
        "candidates": [{"converter": CONVERTER, "missing_count": chk["missing_count"], "glued_count": 0,
                        "headings_missing_count": chk["headings_missing_count"], "chosen": bool(text), "note": note}],
        "reference_count": chk["reference_count"],
        "eligible_count": chk["eligible_count"],
        "missing_count": chk["missing_count"],
        "glued_count": 0,
        "headings_missing_count": chk["headings_missing_count"],
        "missing_examples": chk["missing_examples"],
        "inventory": {"headings": {"html": chk["headings_count"], "md": chk["headings_count"] - chk["headings_missing_count"]},
                      "list_items": {"html": 0, "md": 0}, "tables": {"html": 0, "md": 0},
                      "code_blocks": {"html": 0, "md": 0}, "links": {"html": 0, "md": 0}, "images": {"html": 0, "md": 0}},
        "repairs": {"html": {}, "markdown": {}},
        "bytes_per_word": (round(nbytes / words, 1) if words else 0.0),
        "bloat": bool(words and nbytes / words > 40),
        "published_candidates": _published_candidates(media),
        "authors": [channel] if isinstance(channel, str) and channel.strip() else [],
        "arxiv_id": None,
        "arxiv_version": None,
        "arxiv_requested_version": None,
        "limits": ["lines under %d tokens are not required" % ELIGIBLE_TOKENS,
                   "the reference is the deduplicated caption lines, so a repeat the deduplication kept is not a miss",
                   "coverage of the video's duration is recorded in the candidate note, never judged"],
        "tools": {CONVERTER: CONVERTER_VERSION},
    }
    return text, report
