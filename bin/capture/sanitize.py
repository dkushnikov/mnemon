"""capture.sanitize — sanitation before the check, every repair counted, no position guessed (spec D8).

HTML level (`html`), on the raw before the reference and the converters see it: every `<math>` element
becomes the text `$<alttext>$` (display math `$$…$$`; without `alttext` the `<annotation
encoding="application/x-tex">`, without either the element's visible text) so the reference, the converters
and the published text all carry the formula's source instead of glyphs glued to it (`θ\\theta`, G15); an
SVG with `<text>` becomes a `<p>` placeholder carrying its labels, an icon SVG is removed; LaTeXML `div.ltx_listingline` runs
become `<pre><code>` (indentation from `ltx_lst_space`); `<a href="data:…">` listing buttons are removed (an
anchor with real text is unwrapped to its text); merged-cell tables are rewritten into simple tables
(lib_table) so pandoc never emits `[TABLE]` for them — a table whose expansion would exceed the cell cap
is left as it is and counted `tables_unrepaired`, and the conversion deadline is checked between tables.
Markdown level (`markdown`), after conversion and fence-aware (nothing inside ``` or ~~~ fences is touched):
`![alt](data:image/…)` → `*[inline figure omitted at capture: alt]*`, remaining `data:` links dropped,
`_next/image?url=…` (also `_next/image/?url=…`) → `*[img: alt]*`, `\\iow_now:Ne` residue → line breaks inside a `~~~~text` fence,
LaTeXML macro residue before the title dropped (arXiv only; LaTeX3 `\\tl_set:Ne…` lines included), reader-comment sections cut, runs of blank lines
collapsed; a `[TABLE]` pandoc still emits stays and is counted as `tables_unrepaired`. Both levels are
idempotent: a second pass changes nothing.

The HTML level never runs a `.*?` regex over the document: every element it rewrites is found by the
bounded find loops of `check` (`elements`, `find_start_tag`, `find_end_tag`), so an `<svg`, `<table` or
`<a href="data:…">` that is never closed leaves the rest of the document as it is at a linear cost (G1).
Stdlib only, python 3.9.
"""
from __future__ import annotations

import html as htmlmod
import re
from typing import Callable, List, Tuple

from . import lib_table
from .check import COMMENTS, ascii_lower, elements, find_end_tag, find_start_tag, strip_tags, tag_attrs, tag_name

HTML_KEYS = ("svg_diagrams", "svg_icons", "latexml_listings", "data_links", "tables_rewritten", "tables_unrepaired",
             "math_sources")
MD_KEYS = ("data_uris", "data_links", "next_images", "tcolorbox_residue", "residue_lines", "comment_sections",
           "tables_unrepaired")


class Timeout(Exception):
    """The conversion deadline passed during sanitation (checked between tables)."""

_WS = re.compile(r"\s+")
_ONLY_SPACE = re.compile(r"^(?:&nbsp;|\s){0,100000}$")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# The markdown-level patterns use bounded, nesting-free character classes (G26): the alt text never holds a
# bracket or a newline, a destination never a parenthesis or whitespace (a base64 figure is one unbroken
# run, megabytes long — its bound is the raw cap), so a line of 100 000 `[` or `(` is linear.
_MD_DATA_IMAGE = re.compile(r"!\[([^\[\]\n]{0,2000})\]\([ \t]{0,20}<?data:image/[^()\s]{0,20000000}\)")
_MD_DATA_LINK = re.compile(r"\[([^\[\]\n]{0,2000})\]\([ \t]{0,20}<?data:[^()\s]{0,20000000}\)")
_MD_NEXT_IMAGE = re.compile(r"!\[([^\[\]\n]{0,2000})\]\([^()\s]{0,4000}?/_next/image/?\?[^()\s]{0,4000}\)")
# a link whose target is a Next.js image: its text may already be the image placeholder `*[img: …]*`
_MD_NEXT_LINK = re.compile(r"\[((?:[^\[\]\n]|\*\[img: [^\[\]\n]{0,2000}\]\*){0,2000})\]\([^()\s]{0,4000}?/_next/image/?\?[^()\s]{0,4000}\)")
_IOW = re.compile(r"\\iow_now:Ne\s*")
# macro residue before the title: a command opening a group, the preamble macros, and LaTeX3 residue — the
# `ltx_ERROR` span of an undefined expl3 macro, a backslash command with `_` and `:` in its name
# (`\\tl_set:Ne\\tcboxmath`, `tcboxmath \\tl_set:Ne\\tcbhighmath…`, arXiv 2605.12978v2)
_TEX_RESIDUE = re.compile(r"^\s*\\[A-Za-z@]+\s*[{\[]|\\(?:newcommand|renewcommand|providecommand|def\\|DeclareMathOperator|"
                          r"RequirePackage|usepackage|IfFileExists|makeatletter|newenvironment|newtheorem|DeclarePairedDelimiter)\b"
                          r"|\\[A-Za-z_]+:[A-Za-z]*")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s")
_TABLE_PLACEHOLDER = re.compile(r"^\s*\[TABLE\]\s*$")
_WORDISH = re.compile(r"[^\W_]")


def _counts(keys) -> dict:
    return {k: 0 for k in keys}


# --- HTML level --------------------------------------------------------------------------------------------

def _sub_elements(text: str, name: str, repl: Callable[[str, str], str]) -> str:
    """Every complete `<name …>…</name>` element replaced by `repl(block, inner)` — a bounded find loop
    over the ASCII-lowered document, never a `.*?` regex; an unclosed element and what follows it stay."""
    low = ascii_lower(text)
    out: List[str] = []
    pos = 0
    for lt, gt, elt, egt in elements(low, name):
        out.append(text[pos:lt])
        out.append(repl(text[lt:egt + 1], text[gt + 1:elt]))
        pos = egt + 1
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _math_source(inner: str, attrs: dict) -> str:
    """The TeX source of one `<math>` element: `alttext`, else the `application/x-tex` annotation, else the
    element's visible text — one line, HTML-escaped so it reads as a text node."""
    src = (attrs.get("alttext") or "").strip()
    if not src:
        low = ascii_lower(inner)
        for lt, gt, elt, egt in elements(low, "annotation"):
            if tag_attrs(inner[lt + 11:gt]).get("encoding", "").strip().lower() == "application/x-tex":
                src = htmlmod.unescape(inner[gt + 1:elt]).strip()
                if src:
                    break
    if not src:
        src = htmlmod.unescape(strip_tags(inner)).strip()
    return htmlmod.escape(" ".join(src.split()), quote=False)


def _math_repl(counts):
    def repl(block: str, inner: str) -> str:
        gt = block.find(">")
        attrs = tag_attrs(block[5:gt]) if gt > 0 else {}
        src = _math_source(inner, attrs)
        counts["math_sources"] += 1
        if not src:
            return ""
        if attrs.get("display", "").strip().lower() == "block":
            return "$$%s$$" % src
        return "$%s$" % src
    return repl


def _svg_repl(counts):
    def repl(block: str, inner: str) -> str:
        labels = []
        low = ascii_lower(inner)
        for lt, gt, elt, egt in elements(low, "text"):
            label = _WS.sub(" ", htmlmod.unescape(strip_tags(inner[gt + 1:elt]))).strip()
            if label and label not in labels:
                labels.append(label)
        if labels:
            counts["svg_diagrams"] += 1
            return "<p>[diagram: %s]</p>" % htmlmod.escape(" · ".join(labels), quote=False)
        counts["svg_icons"] += 1
        return ""
    return repl


def _class_token(inner: str, token: str) -> bool:
    return token in tag_attrs(inner).get("class", "").split()


def _listing_line_text(inner: str) -> str:
    """One LaTeXML listing line: an `ltx_lst_space` span holding only whitespace or `&nbsp;` becomes a space,
    `<br>` a line break, every other tag is dropped, `&nbsp;` is a space."""
    low = ascii_lower(inner)
    out: List[str] = []
    pos, n = 0, len(inner)
    spans_end = True            # False once a space span proved unclosed: no later one can be closed either
    while True:
        lt = low.find("<", pos)
        if lt < 0:
            out.append(inner[pos:])
            break
        gt = low.find(">", lt + 1)
        if gt < 0:
            out.append(inner[pos:])
            break
        out.append(inner[pos:lt])
        name = tag_name(low, lt, gt)
        if name == "br":
            out.append("\n")
        elif name == "span" and spans_end and _class_token(inner[lt + 5:gt], "ltx_lst_space"):
            elt, egt = find_end_tag(low, "span", gt + 1)
            if elt < 0:
                spans_end = False
            elif _ONLY_SPACE.match(inner[gt + 1:elt]):
                out.append(" ")
                pos = egt + 1
                continue
        pos = gt + 1
    return "".join(out).replace("&nbsp;", " ").replace("\xa0", " ")


def _sub_listing_runs(text: str, counts) -> str:
    """Runs of consecutive `div.ltx_listingline` elements (whitespace between and after them included)
    become one `<pre><code>` block each."""
    low = ascii_lower(text)
    out: List[str] = []
    pos = 0
    n = len(text)
    i = 0
    while True:
        lt, gt = find_start_tag(low, "div", i)
        if lt < 0:
            break
        if not _class_token(text[lt + 4:gt], "ltx_listingline"):
            i = gt + 1
            continue
        lines: List[str] = []
        run_end = gt + 1
        cur_gt = gt
        unclosed = False
        while True:
            elt, egt = find_end_tag(low, "div", cur_gt + 1)
            if elt < 0:
                unclosed = True
                break
            lines.append(_listing_line_text(text[cur_gt + 1:elt]).rstrip())
            k = egt + 1
            while k < n and text[k].isspace():
                k += 1
            run_end = k
            nlt, ngt = find_start_tag(low, "div", k)
            if nlt == k and _class_token(text[nlt + 4:ngt], "ltx_listingline"):
                cur_gt = ngt
                continue
            break
        if not lines:               # the first line is never closed, so no later one can be: the pass ends
            break
        counts["latexml_listings"] += 1
        out.append(text[pos:lt])
        out.append("<pre><code>%s</code></pre>\n" % "\n".join(lines))
        pos = run_end
        i = run_end
        if unclosed:                # a later line of the run is never closed: nothing after it can be
            break
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _sub_data_links(text: str, counts) -> str:
    """A `data:` anchor is removed when its text has no word characters (a download button); otherwise it
    is unwrapped to its content, so the text it carried stays in the reference and in every candidate."""
    low = ascii_lower(text)
    out: List[str] = []
    pos = 0
    i = 0
    while True:
        lt, gt = find_start_tag(low, "a", i)
        if lt < 0:
            break
        if not tag_attrs(text[lt + 2:gt]).get("href", "").startswith("data:"):
            i = gt + 1
            continue
        elt, egt = find_end_tag(low, "a", gt + 1)
        if elt < 0:
            break
        inner = text[gt + 1:elt]
        counts["data_links"] += 1
        out.append(text[pos:lt])
        out.append(inner if _WORDISH.search(htmlmod.unescape(strip_tags(inner))) else "")
        pos = egt + 1
        i = egt + 1
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _table_repl(counts, deadline=None):
    def repl(block: str, inner: str) -> str:
        if deadline is not None and deadline.expired():
            raise Timeout("the conversion deadline passed while rewriting tables")
        try:
            rewritten, changed = lib_table.expand_table(block)
        except lib_table.TooLarge:
            counts["tables_unrepaired"] += 1
            return block
        if changed:
            counts["tables_rewritten"] += 1
        return rewritten
    return repl


def html(raw_html: str, kind: str = "page", deadline=None) -> Tuple[str, dict]:
    """The HTML-level repairs; (sanitized html, counts). `deadline` (a proc.Deadline) is checked between
    tables; past it `Timeout` is raised."""
    counts = _counts(HTML_KEYS)
    out = _sub_elements(raw_html, "math", _math_repl(counts))
    out = _sub_elements(out, "svg", _svg_repl(counts))
    out = _sub_listing_runs(out, counts)
    out = _sub_data_links(out, counts)
    out = _sub_elements(out, "table", _table_repl(counts, deadline))
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
        # a link around an already reduced image keeps the one placeholder (no nested `*[img: *[img: …]*]*`)
        text, n = _MD_NEXT_LINK.subn(lambda m: m.group(1).strip() if m.group(1).strip().startswith("*[img: ") else "*[img: %s]*" % m.group(1).strip(), text)
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
