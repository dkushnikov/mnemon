"""capture.lib_table — merged-cell tables rewritten into simple tables (spec D8).

`expand_table(table_html)` turns one `<table>` whose cells carry `rowspan`/`colspan` into a table of plain
cells: a spanned cell's content is duplicated into every grid position it covered, stacked header rows are
joined per column with " · " (consecutive duplicates collapsed), paragraphs inside a cell are joined with a
space. pandoc then emits a pipe table instead of `[TABLE]`, and nothing has to guess which placeholder was
which table. A table without spans is returned unchanged. Stdlib only, python 3.9.
"""
from __future__ import annotations

import html as htmlmod
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

_SPAN = re.compile(r"\b(?:row|col)span\s*=\s*[\"']?\s*(\d+)", re.IGNORECASE)
_P_TAG = re.compile(r"</?p\b[^>]*>", re.IGNORECASE)
_WS = re.compile(r"\s+")


def has_merged_cells(table_html: str) -> bool:
    return any(int(m.group(1)) > 1 for m in _SPAN.finditer(table_html))


class _Cell:
    __slots__ = ("tag", "rowspan", "colspan", "parts")

    def __init__(self, tag: str, attrs):
        self.tag = tag
        self.rowspan = _span(attrs, "rowspan")
        self.colspan = _span(attrs, "colspan")
        self.parts: List[str] = []

    @property
    def html(self) -> str:
        inner = "".join(self.parts)
        inner = _P_TAG.sub(" ", inner)
        return _WS.sub(" ", inner).strip()


def _span(attrs, name: str) -> int:
    for k, v in attrs:
        if k.lower() == name:
            try:
                return max(1, int(str(v).strip()))
            except (TypeError, ValueError):
                return 1
    return 1


def _attrs_html(attrs) -> str:
    return "".join(' %s="%s"' % (k, htmlmod.escape(v or "", quote=True)) for k, v in attrs)


class _TableParser(HTMLParser):
    """Rows of cells of one table; nested tables are kept as inline HTML inside their cell."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.caption_parts: List[str] = []
        self.rows: List[Tuple[str, List[_Cell]]] = []
        self.section = "tbody"
        self.row: Optional[List[_Cell]] = None
        self.cell: Optional[_Cell] = None
        self.in_caption = False
        self.depth = 0

    def _sink(self) -> Optional[List[str]]:
        if self.in_caption:
            return self.caption_parts
        if self.cell is not None:
            return self.cell.parts
        return None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.depth += 1
            if self.depth > 1:
                sink = self._sink()
                if sink is not None:
                    sink.append("<table%s>" % _attrs_html(attrs))
            return
        if self.depth > 1:
            sink = self._sink()
            if sink is not None:
                sink.append("<%s%s>" % (tag, _attrs_html(attrs)))
            return
        if tag in ("thead", "tbody", "tfoot"):
            self._end_row()
            self.section = tag
        elif tag == "caption":
            self.in_caption = True
        elif tag == "tr":
            self._end_row()
            self.row = []
        elif tag in ("td", "th"):
            if self.row is None:
                self.row = []
            self.cell = _Cell(tag, attrs)
            self.row.append(self.cell)
        else:
            sink = self._sink()
            if sink is not None:
                sink.append("<%s%s>" % (tag, _attrs_html(attrs)))

    def handle_startendtag(self, tag, attrs):
        sink = self._sink()
        if sink is not None:
            sink.append("<%s%s/>" % (tag.lower(), _attrs_html(attrs)))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            if self.depth > 1:
                sink = self._sink()
                if sink is not None:
                    sink.append("</table>")
            self.depth = max(0, self.depth - 1)
            return
        if self.depth > 1:
            sink = self._sink()
            if sink is not None:
                sink.append("</%s>" % tag)
            return
        if tag in ("td", "th"):
            self.cell = None
        elif tag == "tr":
            self._end_row()
        elif tag == "caption":
            self.in_caption = False
        elif tag in ("thead", "tbody", "tfoot"):
            self._end_row()
            self.section = "tbody"
        else:
            sink = self._sink()
            if sink is not None:
                sink.append("</%s>" % tag)

    def handle_data(self, data):
        sink = self._sink()
        if sink is not None:
            sink.append(data)

    def handle_entityref(self, name):
        sink = self._sink()
        if sink is not None:
            sink.append("&%s;" % name)

    def handle_charref(self, name):
        sink = self._sink()
        if sink is not None:
            sink.append("&#%s;" % name)

    def _end_row(self):
        if self.row is not None:
            if self.row:
                self.rows.append((self.section, self.row))
            self.row = None
            self.cell = None

    def close(self):
        super().close()
        self._end_row()


def _grid(rows: List[List[_Cell]]) -> List[List[str]]:
    """Cells placed on a grid; a spanned cell's content is duplicated into every position it covers."""
    grid: List[dict] = []
    for r, row in enumerate(rows):
        while len(grid) <= r:
            grid.append({})
        col = 0
        for cell in row:
            while col in grid[r]:
                col += 1
            for dr in range(cell.rowspan):
                while len(grid) <= r + dr:
                    grid.append({})
                for dc in range(cell.colspan):
                    grid[r + dr].setdefault(col + dc, cell.html)
            col += cell.colspan
    width = max((max(d.keys()) + 1 for d in grid if d), default=0)
    return [[d.get(c, "") for c in range(width)] for d in grid if d]


def expand_table(table_html: str) -> Tuple[str, bool]:
    """A merged-cell table rewritten into a simple one; (html, changed)."""
    if not has_merged_cells(table_html):
        return table_html, False
    parser = _TableParser()
    try:
        parser.feed(table_html)
        parser.close()
    except Exception:
        return table_html, False
    if not parser.rows:
        return table_html, False
    header_rows: List[List[_Cell]] = []
    body_rows: List[List[_Cell]] = []
    leading = True
    for section, row in parser.rows:
        is_header = section == "thead" or (leading and all(c.tag == "th" for c in row))
        if is_header and not body_rows:
            header_rows.append(row)
        else:
            leading = False
            body_rows.append(row)
    header_grid = _grid(header_rows) if header_rows else []
    body_grid = _grid(body_rows) if body_rows else []
    width = max([len(r) for r in header_grid + body_grid] or [0])
    out = ["<table>"]
    caption = _WS.sub(" ", "".join(parser.caption_parts)).strip()
    if caption:
        out.append("<caption>%s</caption>" % caption)
    if header_grid:
        labels = []
        for c in range(width):
            stack: List[str] = []
            for row in header_grid:
                label = row[c] if c < len(row) else ""
                if label and (not stack or stack[-1] != label):
                    stack.append(label)
            labels.append(" · ".join(stack))
        out.append("<thead><tr>" + "".join("<th>%s</th>" % l for l in labels) + "</tr></thead>")
    if body_grid:
        out.append("<tbody>")
        for row in body_grid:
            cells = list(row) + [""] * (width - len(row))
            out.append("<tr>" + "".join("<td>%s</td>" % c for c in cells) + "</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out), True
