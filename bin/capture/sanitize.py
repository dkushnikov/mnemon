"""capture.sanitize — sanitation before the check, every repair counted, no position guessed (spec D8).

HTML level (`html`), on the raw before the reference and the converters see it: an SVG with `<text>`
becomes a `<p>` placeholder carrying its labels, an icon SVG is removed; LaTeXML `div.ltx_listingline` runs
become `<pre><code>` (indentation from `ltx_lst_space`); `<a href="data:…">` listing buttons are removed;
merged-cell tables are rewritten into simple tables (lib_table) so pandoc never emits `[TABLE]` for them.
Markdown level (`markdown`), after conversion and fence-aware (nothing inside ``` or ~~~ fences is touched):
`![alt](data:image/…)` → `*[inline figure omitted at capture: alt]*`, remaining `data:` links dropped,
`_next/image?url=…` → `*[img: alt]*`, `\\iow_now:Ne` residue → line breaks inside a `~~~~text` fence,
LaTeXML macro residue before the title dropped (arXiv only), reader-comment sections cut, runs of blank lines
collapsed; a `[TABLE]` pandoc still emits stays and is counted as `tables_unrepaired`. Both levels are
idempotent: a second pass changes nothing. Stdlib only, python 3.9.
"""
from __future__ import annotations

import html as htmlmod
import re
from typing import List, Tuple

from . import lib_table
from .check import COMMENTS

HTML_KEYS = ("svg_diagrams", "svg_icons", "latexml_listings", "data_links", "tables_rewritten")
MD_KEYS = ("data_uris", "data_links", "next_images", "tcolorbox_residue", "residue_lines", "comment_sections",
           "tables_unrepaired")

_SVG = re.compile(r"<svg\b[^>]*>.*?</svg\s*>", re.IGNORECASE | re.DOTALL)
_SVG_TEXT = re.compile(r"<text\b[^>]*>(.*?)</text\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_LISTING_LINE = re.compile(r"<div\b[^>]*\bclass=[\"'][^\"']*\bltx_listingline\b[^\"']*[\"'][^>]*>(.*?)</div>",
                           re.IGNORECASE | re.DOTALL)
_LISTING_RUN = re.compile(r"(?:<div\b[^>]*\bclass=[\"'][^\"']*\bltx_listingline\b[^\"']*[\"'][^>]*>.*?</div>\s*)+",
                          re.IGNORECASE | re.DOTALL)
_LST_SPACE = re.compile(r"<span\b[^>]*\bltx_lst_space\b[^>]*>\s*(?:&nbsp;|\s)*</span>", re.IGNORECASE | re.DOTALL)
_DATA_LINK = re.compile(r"<a\b[^>]*\bhref=[\"']data:[^\"']*[\"'][^>]*>.*?</a\s*>", re.IGNORECASE | re.DOTALL)
_TABLE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_MD_DATA_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?data:image/[^)]*\)")
_MD_DATA_LINK = re.compile(r"\[([^\]]*)\]\(\s*<?data:[^)]*\)")
_MD_NEXT_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*?/_next/image\?[^)]*\)")
_MD_NEXT_LINK = re.compile(r"\[([^\]]*)\]\([^)]*?/_next/image\?[^)]*\)")
_IOW = re.compile(r"\\iow_now:Ne\s*")
_TEX_RESIDUE = re.compile(r"^\s*\\[A-Za-z@]+\s*[{\[]|\\(?:newcommand|renewcommand|providecommand|def\\|DeclareMathOperator|"
                          r"RequirePackage|usepackage|IfFileExists|makeatletter|newenvironment|newtheorem|DeclarePairedDelimiter)\b")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s")
_TABLE_PLACEHOLDER = re.compile(r"^\s*\[TABLE\]\s*$")
_WORDISH = re.compile(r"[^\W_]")


def _counts(keys) -> dict:
    return {k: 0 for k in keys}


# --- HTML level --------------------------------------------------------------------------------------------

def _svg_repl(counts):
    def repl(m):
        labels = []
        for t in _SVG_TEXT.finditer(m.group(0)):
            label = _WS.sub(" ", htmlmod.unescape(_TAG.sub(" ", t.group(1)))).strip()
            if label and label not in labels:
                labels.append(label)
        if labels:
            counts["svg_diagrams"] += 1
            return "<p>[diagram: %s]</p>" % htmlmod.escape(" · ".join(labels), quote=False)
        counts["svg_icons"] += 1
        return ""
    return repl


def _listing_repl(counts):
    def repl(m):
        lines = []
        for line in _LISTING_LINE.finditer(m.group(0)):
            inner = _LST_SPACE.sub(" ", line.group(1))
            inner = re.sub(r"<br\s*/?>", "\n", inner, flags=re.IGNORECASE)
            text = _TAG.sub("", inner)
            text = text.replace("&nbsp;", " ").replace("\xa0", " ")
            lines.append(text.rstrip())
        counts["latexml_listings"] += 1
        return "<pre><code>%s</code></pre>\n" % "\n".join(lines)
    return repl


def _table_repl(counts):
    def repl(m):
        rewritten, changed = lib_table.expand_table(m.group(0))
        if changed:
            counts["tables_rewritten"] += 1
        return rewritten
    return repl


def html(raw_html: str, kind: str = "page") -> Tuple[str, dict]:
    """The HTML-level repairs; (sanitized html, counts)."""
    counts = _counts(HTML_KEYS)
    out = _SVG.sub(_svg_repl(counts), raw_html)
    out = _LISTING_RUN.sub(_listing_repl(counts), out)
    before = out
    out = _DATA_LINK.sub("", out)
    counts["data_links"] = len(_DATA_LINK.findall(before))
    out = _TABLE.sub(_table_repl(counts), out)
    return out, counts


# --- markdown level ----------------------------------------------------------------------------------------

def fence_segments(md: str) -> List[Tuple[bool, List[str]]]:
    """The lines of a document grouped into (inside_fence, lines) runs; fences belong to the code run."""
    segments: List[Tuple[bool, List[str]]] = []
    current: List[str] = []
    in_code = False
    fence = None
    for line in md.split("\n"):
        m = _FENCE.match(line)
        if not in_code:
            if m:
                if current:
                    segments.append((False, current))
                current = [line]
                in_code = True
                fence = m.group(1)
            else:
                current.append(line)
        else:
            current.append(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                segments.append((True, current))
                current = []
                in_code = False
                fence = None
    if current or not segments:
        segments.append((in_code, current))
    return segments


def _heading_level(line: str) -> int:
    m = re.match(r"^ {0,3}(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def cut_comment_sections(lines: List[str]) -> Tuple[List[str], int]:
    """Cut every reader-comment section (a COMMENTS heading to the next heading of the same or a higher level, or the end)."""
    out: List[str] = []
    count = 0
    i = 0
    in_code = False
    fence = None
    while i < len(lines):
        line = lines[i]
        m = _FENCE.match(line)
        if not in_code and m:
            in_code, fence = True, m.group(1)
        elif in_code and m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            in_code, fence = False, None
        level = 0 if in_code else _heading_level(line)
        if level and COMMENTS.match(line.strip()):
            count += 1
            j = i + 1
            code2, fence2 = False, None
            while j < len(lines):
                m2 = _FENCE.match(lines[j])
                if not code2 and m2:
                    code2, fence2 = True, m2.group(1)
                elif code2 and m2 and m2.group(1)[0] == fence2[0] and len(m2.group(1)) >= len(fence2) and not m2.group(2).strip():
                    code2, fence2 = False, None
                elif not code2:
                    lvl = _heading_level(lines[j])
                    if lvl and lvl <= level:
                        break
                j += 1
            i = j
            continue
        out.append(line)
        i += 1
    return out, count


def _drop_residue_before_title(lines: List[str], counts) -> List[str]:
    """arXiv: LaTeXML macro residue before the first heading dropped (only when a heading exists)."""
    if not any(_HEADING.match(line) for line in lines):
        return lines
    out: List[str] = []
    seen_heading = False
    for line in lines:
        if not seen_heading:
            if _HEADING.match(line):
                seen_heading = True
            elif line.strip() and _TEX_RESIDUE.search(line):
                counts["residue_lines"] += 1
                continue
        out.append(line)
    return out


def reference_lines(lines: List[str], kind: str = "page") -> List[str]:
    """The reference projected like the candidates: comment sections cut, arXiv macro residue before the
    first heading dropped, `\\iow_now:Ne` residue split into lines — so a repair never reads as a loss."""
    lines, _ = cut_comment_sections(lines)
    if kind == "arxiv":
        lines = _drop_residue_before_title(lines, _counts(MD_KEYS))
    out: List[str] = []
    for line in lines:
        if _IOW.search(line):
            out.extend(p.strip() for p in _IOW.split(line) if p.strip())
        else:
            out.append(line)
    return out


def _data_link_repl(counts):
    def repl(m):
        counts["data_links"] += 1
        text = m.group(1)
        return text if _WORDISH.search(text) else ""
    return repl


def markdown(md: str, kind: str = "page") -> Tuple[str, dict]:
    """The markdown-level repairs outside fences; (sanitized markdown, counts)."""
    counts = _counts(MD_KEYS)
    lines = md.split("\n")
    lines, counts["comment_sections"] = cut_comment_sections(lines)
    if kind == "arxiv":
        lines = _drop_residue_before_title(lines, counts)
    out_lines: List[str] = []
    for in_code, seg in fence_segments("\n".join(lines)):
        if in_code:
            out_lines.extend(seg)
            continue
        text = "\n".join(seg)
        text, n = _MD_DATA_IMAGE.subn(lambda m: "*[inline figure omitted at capture: %s]*" % m.group(1).strip(), text)
        counts["data_uris"] += n
        text = _MD_DATA_LINK.sub(_data_link_repl(counts), text)
        text, n = _MD_NEXT_IMAGE.subn(lambda m: "*[img: %s]*" % m.group(1).strip(), text)
        counts["next_images"] += n
        text, n = _MD_NEXT_LINK.subn(lambda m: "*[img: %s]*" % m.group(1).strip(), text)
        counts["next_images"] += n
        paragraphs = []
        for para in text.split("\n\n"):
            if _IOW.search(para):
                counts["tcolorbox_residue"] += 1
                body = "\n".join(p.strip() for p in _IOW.split(para) if p.strip())
                para = "~~~~text\n%s\n~~~~" % body
            paragraphs.append(para)
        text = "\n\n".join(paragraphs)
        text = re.sub(r"\n{3,}", "\n\n", text)
        seg_lines = text.split("\n")
        counts["tables_unrepaired"] += sum(1 for l in seg_lines if _TABLE_PLACEHOLDER.match(l))
        out_lines.extend(seg_lines)
    result = "\n".join(out_lines)
    result = re.sub(r"\n{3,}", "\n\n", result).lstrip("\n")
    if result.strip():
        result = result.rstrip("\n") + "\n"
    return result, counts
