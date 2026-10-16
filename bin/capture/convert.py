"""capture.convert — one projection pipeline: reference, candidates and inventory from the same sanitized
subtree (spec §3.5, D7, D8).

Order of work: decode (BOM → `<meta charset>` → the fetch report's charset → UTF-8; invalid → `invalid-utf8`)
→ `sanitize.html` → the content subtree (`<article class="ltx_document">` for arXiv, else `<body>`, with
`<head>`, scripts, styles, `<noscript>`, `<template>`, `<nav>`, `<footer>`, forms, cookie/consent containers
and reader-comment sections removed, `<header>` kept) → the reference = `dom_text(subtree, reference=True)`
(block elements break lines, h1–h3 carry `## `, whitespace-free inline boundaries marked so the check can
require word boundaries or accept the page's own joined form, nothing else dropped) → the candidates in
order — `defuddle` on the sanitized HTML (skipped for rendered, site and external raws and discarded on
mojibake), `pandoc` on the same subtree (`-f html -t gfm-raw_html-tex_math_dollars --wrap=none`), the DOM text with the
page's own word boundaries — each through `sanitize.markdown` and `check.conversion` until one passes;
otherwise the DOM text, which satisfies the reference by construction — and when it does not (a check
defect), it is still published, as `partial` with the counts of its own check and the note `dom-text failed
its own check` (G17); the conversion fails only when the reference is actually empty (no text at all). The
inventory counts the same subtree. Stdlib only, python 3.9.
"""
from __future__ import annotations

import codecs
import html as htmlmod
import json
import os
import re
import shutil
import tempfile
from html.parser import HTMLParser
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import check, proc, sanitize

DEFAULT_TIMEOUT = 300.0
BLOAT_BYTES_PER_WORD = 40.0
MOJIBAKE = re.compile(r"[ÃÂ][\u0080-¿]|â€")
VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                  "track", "wbr"))
BLOCK = frozenset(("address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "dialog",
                   "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
                   "h6", "header", "hr", "html", "legend", "li", "main", "menu", "nav", "ol", "option", "p", "pre",
                   "section", "select", "summary", "table", "tbody", "td", "textarea", "tfoot", "th", "thead", "title",
                   "tr", "ul", "br"))
REMOVE_TAGS = frozenset(("head", "script", "style", "noscript", "template", "nav", "footer", "form", "iframe", "svg",
                         "canvas", "object"))
HEADINGS_PREFIXED = frozenset(("h1", "h2", "h3"))
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_CHARSET_VALUE = re.compile(rb"[\"']?\s*([A-Za-z0-9_\-:.]+)")
_META_HEAD_BYTES = 65536
_DATE_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_DATE_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})")
DATED_TYPES = ("Article", "BlogPosting", "NewsArticle", "ScholarlyArticle")


class ConvertError(Exception):
    def __init__(self, error_class: str, message: str, retryable: bool = True, candidates=None):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable      # false for `nesting`: a document no walk can finish never converts
        self.candidates = list(candidates or [])    # what was tried before the failure (the failure report carries it)


class Result(NamedTuple):
    text: str
    report: dict


# --- decoding ----------------------------------------------------------------------------------------------

def meta_charset(raw: bytes) -> Optional[str]:
    """The `charset=` of the first `<meta …>` tag naming one, from the first 64 KB of the raw: a bounded find
    loop over the bytes (a `<meta` with no `>` after it ends the search), never a `[^>]*` regex (G1)."""
    head = raw[:_META_HEAD_BYTES]
    low = head.lower()
    pos = 0
    while True:
        lt = low.find(b"<meta", pos)
        if lt < 0:
            return None
        after = low[lt + 5:lt + 6]
        if after and after not in b" \t\n\r\f/>":
            pos = lt + 1
            continue
        gt = low.find(b">", lt + 5)
        if gt < 0:
            return None
        tag = low[lt + 5:gt]
        k = tag.find(b"charset")
        while k >= 0:
            before = tag[k - 1:k]
            if k == 0 or not (before.isalnum() or before == b"_"):
                j = k + 7
                while j < len(tag) and tag[j:j + 1] in b" \t\n\r\f":
                    j += 1
                if tag[j:j + 1] == b"=":
                    j += 1
                    while j < len(tag) and tag[j:j + 1] in b" \t\n\r\f":
                        j += 1
                    m = _CHARSET_VALUE.match(tag, j)
                    if m:
                        return m.group(1).decode("ascii", "replace").lower()
            k = tag.find(b"charset", k + 1)
        pos = gt + 1


def decode(raw: bytes, charset_hint: Optional[str] = None) -> Tuple[str, str]:
    """BOM → <meta charset> → the hint → UTF-8; strict; (text, charset). Raises ConvertError(invalid-utf8)."""
    if raw.startswith(codecs.BOM_UTF8):
        order = [("utf-8", raw[3:])]
    elif raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        order = [("utf-16", raw)]
    else:
        order = []
        declared = meta_charset(raw)
        if declared:
            order.append((declared, raw))
        if charset_hint:
            order.append((charset_hint.lower(), raw))
        order.append(("utf-8", raw))
    tried = []
    for name, data in order:
        try:
            codecs.lookup(name)
        except LookupError:
            tried.append(name)
            continue
        try:
            return data.decode(name), name
        except (UnicodeDecodeError, ValueError):
            tried.append(name)
    raise ConvertError("invalid-utf8", "the raw does not decode as %s" % ", ".join(tried))


# --- a small DOM -------------------------------------------------------------------------------------------
# Every walk over the tree is iterative (an explicit stack, never recursion), and the builder caps the
# depth of the tree at MAX_DEPTH: elements opened deeper than that are flattened into the capped node
# as a run of siblings and text. So 20 000 nested <div> or 6 000 never-closed <table><tr><td> cost one
# pass and no walk can raise RecursionError (G8).

MAX_DEPTH = 2000


class Node:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag: Optional[str], attrs=None, text: str = ""):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: List["Node"] = []
        self.text = text

    @property
    def is_text(self) -> bool:
        return self.tag is None

    def attr(self, name: str) -> str:
        return self.attrs.get(name) or ""

    def find(self, pred):
        """The first element in document order for which pred holds (iterative, depth-first)."""
        stack = [self]
        while stack:
            n = stack.pop()
            if n.is_text:
                continue
            if pred(n):
                return n
            stack.extend(reversed(n.children))
        return None

    def elements(self):
        """Every element of the subtree in document order, this node included (iterative)."""
        stack = [self]
        while stack:
            n = stack.pop()
            if n.is_text:
                continue
            yield n
            stack.extend(reversed(n.children))


_CLOSE_ON_OPEN = {
    "p": ("p",), "li": ("li",), "td": ("td", "th"), "th": ("td", "th"), "tr": ("td", "th", "tr"),
    "thead": ("td", "th", "tr"), "tbody": ("td", "th", "tr"), "tfoot": ("td", "th", "tr"),
    "dt": ("dt", "dd"), "dd": ("dt", "dd"), "option": ("option",),
}
_P_CLOSERS = frozenset(("address", "article", "aside", "blockquote", "div", "dl", "fieldset", "figure", "footer", "form",
                        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav", "ol", "pre", "section",
                        "table", "ul"))
_SCOPE = frozenset(("table", "ul", "ol", "dl", "select"))


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self.overflow = 0       # elements opened past MAX_DEPTH, flattened into the capped node

    def _close_until(self, tags):
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in tags:
                del self.stack[i:]
                return
            if tag in _SCOPE:
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.overflow or len(self.stack) > MAX_DEPTH:
            # past the cap: the element becomes a flat child of the capped node, its content too
            self.stack[-1].children.append(Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}))
            if tag not in VOID:
                self.overflow += 1
            return
        if tag in _CLOSE_ON_OPEN:
            self._close_until(_CLOSE_ON_OPEN[tag])
        elif tag in _P_CLOSERS and self.stack[-1].tag == "p":
            self.stack.pop()
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.overflow:
            self.overflow -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(Node(None, text=data))


def parse(html_text: str) -> Node:
    builder = _Builder()
    builder.feed(html_text)
    builder.close()
    return builder.root


PANDOC_AS_DIV = frozenset(("header", "hgroup", "main", "section", "article", "aside", "address"))


def to_html(node: Node, for_pandoc: bool = False) -> str:
    """The subtree serialized; `for_pandoc` projects semantic containers onto `<div>` (pandoc keeps only
    `<main>` when one exists and drops `<header>`) and attributes bare `<pre>` elements so pandoc fences them
    — the same content, so the candidate can keep what the fetch got and the fence rules apply."""
    parts: List[str] = []
    stack = [(node, False)]                 # (node, leaving)
    while stack:
        n, leaving = stack.pop()
        if n.is_text:
            parts.append(htmlmod.escape(n.text, quote=False))
            continue
        tag = n.tag
        if for_pandoc and tag in PANDOC_AS_DIV:
            tag = "div"
        if leaving:
            if n.tag != "#root" and n.tag not in VOID:
                parts.append("</%s>" % tag)
            continue
        if n.tag != "#root":
            attrs = "".join(' %s="%s"' % (k, htmlmod.escape(v, quote=True)) for k, v in n.attrs.items())
            if for_pandoc and tag == "pre" and not any(k in ("id", "class") or k.startswith("data-") for k in n.attrs):
                attrs += ' data-mnemon="pre"'      # pandoc fences an attributed <pre>; it indents a bare one
            parts.append("<%s%s>" % (tag, attrs))
        stack.append((n, True))
        stack.extend((c, False) for c in reversed(n.children))
    return "".join(parts)


def normalize_scalar(value: str, limit: int) -> str:
    value = _CTRL.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def node_text(node: Node) -> str:
    parts: List[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.is_text:
            parts.append(n.text)
        else:
            stack.extend(reversed(n.children))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def find_title(root: Node) -> Optional[str]:
    t = root.find(lambda n: n.tag == "title")
    if t is None:
        return None
    title = normalize_scalar(node_text(t), 300)
    return title or None


def _is_container(node: Node, pattern) -> bool:
    """A block container whose id or one of whose class tokens matches the pattern as a whole token."""
    if node.tag not in check.CONTAINER_TAGS:
        return False
    tokens = node.attr("id").split() + node.attr("class").split()
    return any(pattern.match(t) for t in tokens)


def content_subtree(root: Node, kind: str) -> Node:
    """The content subtree, pruned in place (D7): removed tags, cookie/consent and reader-comment containers
    (whole id/class tokens on block elements, never inside <pre>/<code>)."""
    sub = None
    if kind == "arxiv":
        sub = root.find(lambda n: n.tag == "article" and "ltx_document" in n.attr("class").split())
    if sub is None:
        sub = root.find(lambda n: n.tag == "body")
    if sub is None:
        sub = root

    stack = [(sub, False)]                  # (node, inside <pre>/<code>)
    while stack:
        n, in_code = stack.pop()
        kept = []
        for c in n.children:
            if c.is_text:
                kept.append(c)
                continue
            if c.tag in REMOVE_TAGS:
                continue
            code = in_code or c.tag in ("pre", "code")
            if not code and (_is_container(c, check.COOKIE_CONTAINER) or _is_container(c, check.COMMENTS_CONTAINER)):
                continue
            kept.append(c)
            stack.append((c, code))
        n.children = kept
    return sub


SOFT = check.SOFT


def dom_text(node: Node, reference: bool = False) -> str:
    """Block elements break lines, h1–h3 carry `## `. The published text keeps the page's own word boundaries:
    a space stands at an inline boundary only when the HTML carries whitespace there. The *reference*
    projection (`reference=True`) marks every whitespace-free inline boundary with `check.SOFT` instead, so
    the check can require the spaced token sequence or accept the joined one (a drop cap `T`+`he`, an anchor
    inside a word) while per-word spans separated by real whitespace still read as glued when a converter
    joins them."""
    lines: List[str] = []
    buf: List[str] = []
    state = {"heading": False}

    def flush():
        if reference:
            text = re.sub(r"\s+", " ", SOFT.join(buf))
            text = re.sub(r"[ ⁣]*⁣[ ⁣]*", lambda m: " " if " " in m.group(0) else SOFT, text)
            text = text.strip(" " + SOFT)
        else:
            text = re.sub(r"\s+", " ", "".join(buf)).strip()
        del buf[:]
        if state["heading"]:
            state["heading"] = False
            if text:
                lines.append("## " + text)
        elif text:
            lines.append(text)

    stack = [(node, False, False)]          # (node, inside <pre>, leaving)
    while stack:
        n, in_pre, leaving = stack.pop()
        if n.is_text:
            if in_pre and "\n" in n.text:
                pieces = n.text.split("\n")
                buf.append(pieces[0])
                for piece in pieces[1:]:
                    flush()
                    buf.append(piece)
            else:
                buf.append(n.text)
            continue
        block = n.tag in BLOCK
        if leaving:
            if block:
                flush()
            continue
        if block:
            flush()
        if n.tag in HEADINGS_PREFIXED:
            state["heading"] = True
        stack.append((n, in_pre, True))
        child_pre = in_pre or n.tag == "pre"
        stack.extend((c, child_pre, False) for c in reversed(n.children))
    flush()
    return "\n".join(lines)


# --- metadata ----------------------------------------------------------------------------------------------

def _meta_values(root: Node, *names) -> List[str]:
    wanted = set(n.lower() for n in names)
    out = []
    for n in root.elements():
        if n.tag == "meta":
            key = (n.attr("name") or n.attr("property") or n.attr("itemprop")).lower()
            if key in wanted and n.attr("content").strip():
                out.append(n.attr("content").strip())
    return out


def normalize_date(value: str) -> str:
    value = value.strip()
    m = _DATE_SLASH.match(value)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = _DATE_ISO.match(value)
    if m:
        return m.group(1)
    return value


def _jsonld_objects(data):
    """Every JSON object of a parsed JSON-LD document, outermost first (iterative)."""
    stack = [data]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            yield obj
            stack.extend(reversed(list(obj.values())))
        elif isinstance(obj, list):
            stack.extend(reversed(obj))


def _jsonld_dates(root: Node) -> List[dict]:
    out = []
    for n in root.elements():
        if n.tag != "script" or "ld+json" not in n.attr("type").lower():
            continue
        try:
            data = json.loads("".join(c.text for c in n.children if c.is_text))
        except ValueError:
            continue
        for obj in _jsonld_objects(data):
            types = obj.get("@type")
            if isinstance(types, str):
                types = [types]
            if not isinstance(types, list):
                continue
            for t in types:
                if t in DATED_TYPES and isinstance(obj.get("datePublished"), str):
                    out.append({"value": normalize_date(obj["datePublished"]), "source": "jsonld:%s" % t})
                    break
    return out


def page_metadata(root: Node) -> dict:
    """Dated candidates, authors and the declared canonical of a page (the gateway chooses `published:`)."""
    candidates: List[dict] = []
    for v in _meta_values(root, "citation_date", "citation_publication_date"):
        candidates.append({"value": normalize_date(v), "source": "citation_date"})
    for v in _meta_values(root, "article:published_time"):
        candidates.append({"value": normalize_date(v), "source": "article:published_time"})
    candidates.extend(_jsonld_dates(root))
    authors = [normalize_scalar(a, 200) for a in _meta_values(root, "citation_author")]
    link = root.find(lambda n: n.tag == "link" and "canonical" in n.attr("rel").lower().split())
    declared = link.attr("href").strip() if link is not None else None
    return {"published_candidates": candidates, "authors": [a for a in authors if a],
            "declared_canonical_url": declared or None}


# --- the converters ----------------------------------------------------------------------------------------

def tool_version(cmd: str) -> Optional[str]:
    path = shutil.which(cmd)
    if not path:
        return None
    res = proc.run([path, "--version"], timeout=30)
    text = (res.stdout or res.stderr).decode("utf-8", "replace").strip().splitlines()
    return text[0].strip() if text else None


def defuddle_file(html_text: str, *, deadline: Optional[proc.Deadline] = None) -> Tuple[Optional[str], Optional[str]]:
    """`defuddle parse <file> --markdown` on a temp file holding the HTML; (markdown, note)."""
    injected = os.environ.get("MNEMON_TEST_DEFUDDLE_OUTPUT") if os.environ.get("MNEMON_TEST_HOOKS") == "1" else None
    if injected:
        try:
            with open(injected, "r", encoding="utf-8") as fh:
                return fh.read(), "injected candidate (test hook)"
        except OSError as exc:
            return None, "defuddle failed: %s" % exc
    path = shutil.which("defuddle")
    if not path:
        return None, "skipped: defuddle not installed"
    tmpdir = tempfile.mkdtemp(prefix="mnemon-defuddle-")
    try:
        src = os.path.join(tmpdir, "page.html")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(html_text)
        res = proc.run([path, "parse", src, "--markdown"], timeout=DEFAULT_TIMEOUT, deadline=deadline)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if res.timed_out:
        return None, "defuddle timed out after %d ms" % res.elapsed_ms
    out = res.stdout.decode("utf-8", "replace")
    err = res.stderr.decode("utf-8", "replace")
    if res.rc != 0 or not out.strip():
        return None, "defuddle produced no content"
    if "Partial conversion completed with errors" in out or "Partial conversion completed with errors" in err:
        return None, "discarded: partial conversion with errors"
    stripped = out.lstrip()
    if stripped.startswith("<") and re.search(r"<(html|body|div|p|span|article)\b", stripped[:2000], re.IGNORECASE):
        return None, "discarded: returned HTML"
    return out, None


def pandoc_subtree(subtree_html: str, *, deadline: Optional[proc.Deadline] = None) -> Tuple[Optional[str], Optional[str]]:
    path = shutil.which("pandoc")
    if not path:
        return None, "skipped: pandoc not installed"
    # tex_math_dollars off: the `$…$` text nodes the sanitizer made of <math> elements (G15) pass through as
    # text instead of being escaped to `\$…\$`, so the published text carries the formula source as written
    res = proc.run([path, "-f", "html", "-t", "gfm-raw_html-tex_math_dollars", "--wrap=none"], timeout=DEFAULT_TIMEOUT,
                   deadline=deadline, stdin=subtree_html.encode("utf-8"))
    if res.timed_out:
        return None, "pandoc timed out after %d ms" % res.elapsed_ms
    out = res.stdout.decode("utf-8", "replace")
    if res.rc != 0 or not out.strip():
        return None, "pandoc produced no content" if res.rc == 0 else "pandoc failed: %s" % res.stderr.decode("utf-8", "replace").strip()[:200]
    return out, None


def empty_report(url: str) -> dict:
    """Every key of mnemon-capture-text/2 with its null shape."""
    return {
        "schema": "mnemon-capture-text/2", "url": url, "method": None, "bytes": 0, "words": 0, "title": None,
        "completeness": "unknown", "tool": None, "error_class": None, "error": None, "retryable": None, "converter": None,
        "acquisition": "unknown", "candidates": [], "reference_count": 0, "eligible_count": 0, "missing_count": 0,
        "glued_count": 0, "headings_missing_count": 0, "missing_examples": [],
        "inventory": {k: {"html": 0, "md": 0} for k in ("headings", "list_items", "tables", "code_blocks", "links", "images")},
        "repairs": {"html": {k: 0 for k in sanitize.HTML_KEYS}, "markdown": {k: 0 for k in sanitize.MD_KEYS}},
        "bytes_per_word": 0.0, "bloat": False, "published_candidates": [], "authors": [], "arxiv_id": None,
        "arxiv_version": None, "arxiv_requested_version": None, "limits": list(check.LIMITS),
        "tools": {"defuddle": None, "pandoc": None},
    }


def fetch_label(raw_kind: str, fetch_report: Optional[dict]) -> str:
    """The `<fetch>` half of `method`: the fetch report's method, else the phase-1 label by raw kind."""
    if fetch_report and fetch_report.get("method"):
        return str(fetch_report["method"])
    return {"rendered": "render", "site": "site", "external": "external"}.get(raw_kind, "curl")


def convert(raw: bytes, url: str, raw_kind: str = "response", kind: str = "page",
            fetch_report: Optional[dict] = None, *, deadline: Optional[proc.Deadline] = None) -> Result:
    """The three-way conversion with the diagnostic check; raises ConvertError (invalid-utf8, convert)."""
    report = empty_report(url)
    fetch_report = fetch_report or {}
    meta_in = fetch_report.get("metadata") or {}
    acq_in = fetch_report.get("acquisition") or {}
    charset = fetch_report.get("charset")
    text, used_charset = decode(raw, charset)
    kind = kind or fetch_report.get("kind") or "page"
    if kind not in ("page", "arxiv", "youtube", "github"):
        kind = "page"
    try:
        sanitized, html_repairs = sanitize.html(text, kind, deadline=deadline)
    except sanitize.Timeout as exc:
        raise ConvertError("timeout", str(exc))
    try:
        root = parse(sanitized)
        meta_title = meta_in.get("title") if isinstance(meta_in.get("title"), str) else None
        meta_title = normalize_scalar(meta_title, 300) if meta_title else None
        if kind == "arxiv" and meta_title:
            # the <title> of a LaTeXML page glues words ("Become FaultyWhen"): the fetch report's
            # citation_title wins for a paper (G18); a page keeps its own <title> first
            title = meta_title
        else:
            title = find_title(root) or meta_title
        page_meta = page_metadata(root)
        subtree = content_subtree(root, kind)
        subtree_html = to_html(subtree)
        reference = "\n".join(sanitize.reference_lines(dom_text(subtree, reference=True).split("\n"), kind))
        dom_candidate = dom_text(subtree)
    except RecursionError:
        # the walks are iterative and the tree is depth-capped; what is left is a JSON-LD document nested
        # past the interpreter's limit — a page no parse can finish, permanent (G8)
        raise ConvertError("convert", "nesting", retryable=False)
    acq = None
    if acq_in.get("outcome"):
        acq = check.Acq(acq_in.get("outcome"), acq_in.get("kind"), acq_in.get("evidence"), int(acq_in.get("visible_words") or 0), None)

    tools = {"defuddle": tool_version("defuddle"), "pandoc": tool_version("pandoc")}
    candidates: List[dict] = []
    chosen: Optional[Tuple[str, str, dict, check.Conv]] = None

    def consider(name: str, md: Optional[str], note: Optional[str]):
        """Judge one candidate; (entry, cleaned, md_repairs, conv) — the last three None when it produced
        nothing to judge."""
        nonlocal chosen
        entry = {"converter": name, "missing_count": None, "glued_count": None, "headings_missing_count": None,
                 "chosen": False, "note": note}
        candidates.append(entry)
        if md is None:
            return entry, None, None, None
        if name == "defuddle" and MOJIBAKE.search(md) and not MOJIBAKE.search(sanitized):
            entry["note"] = "discarded: mojibake"
            return entry, None, None, None
        cleaned, md_repairs = sanitize.markdown(md, kind)
        # the DOM text is plain text: compared as it is, never through the markdown link rewrites (G16)
        conv = check.conversion(cleaned, reference, title, markdown=(name != "dom-text"))
        entry.update({"missing_count": len(conv.missing), "glued_count": len(conv.glued),
                      "headings_missing_count": len(conv.headings_missing)})
        if conv.passed and chosen is None and cleaned.strip():
            entry["chosen"] = True
            chosen = (name, cleaned, md_repairs, conv)
        elif chosen is None and entry["note"] is None and not cleaned.strip():
            entry["note"] = "empty after sanitation"
        return entry, cleaned, md_repairs, conv

    if raw_kind in ("rendered", "site", "external"):
        candidates.append({"converter": "defuddle", "missing_count": None, "glued_count": None,
                           "headings_missing_count": None, "chosen": False, "note": "skipped: %s raw" % raw_kind})
    else:
        md, note = defuddle_file(sanitized, deadline=deadline)
        consider("defuddle", md, note)
    if chosen is None:
        md, note = pandoc_subtree(to_html(subtree, for_pandoc=True), deadline=deadline)
        consider("pandoc", md, note)
    if chosen is None:
        entry, cleaned, md_repairs, conv = consider("dom-text", dom_candidate, None)
        if chosen is None and cleaned is not None and cleaned.strip():
            # D7: "otherwise the DOM text" — the fallback of last resort, never a failure: when even the DOM
            # text fails the check (a check defect, a projection the check cannot follow), it is published
            # as partial with the counts and examples of its own check, so the defect shows instead of a
            # lost capture (G17)
            entry["chosen"] = True
            entry["note"] = "dom-text failed its own check"
            chosen = ("dom-text", cleaned, md_repairs, conv)
    if chosen is None:
        # the reference is actually empty: no eligible line and no text at all (every candidate recorded)
        raise ConvertError("convert", "no candidate produced text (reference is empty)", candidates=candidates)
    name, body, md_repairs, conv = chosen
    if not body.endswith("\n"):
        body += "\n"
    inventory = check.inventory(subtree_html, body)
    outcome = check.outcome(acq, conv, inventory)
    words = len(body.split())
    nbytes = len(body.encode("utf-8"))
    bpw = round(nbytes / words, 2) if words else 0.0
    tool = None
    if name in ("defuddle", "pandoc"):
        tool = {"name": name, "version": tools.get(name)}
    report.update({
        "method": "%s+%s" % (fetch_label(raw_kind, fetch_report), name), "bytes": nbytes, "words": words,
        "title": title, "completeness": outcome, "tool": tool, "converter": name,
        "acquisition": acq_in.get("outcome") or "unknown", "candidates": candidates,
        "reference_count": conv.reference_count, "eligible_count": conv.eligible_count,
        "missing_count": len(conv.missing), "glued_count": len(conv.glued),
        "headings_missing_count": len(conv.headings_missing), "missing_examples": check.missing_examples(conv),
        "inventory": inventory, "repairs": {"html": html_repairs, "markdown": md_repairs},
        "bytes_per_word": bpw, "bloat": bpw > BLOAT_BYTES_PER_WORD,
        "published_candidates": list(page_meta["published_candidates"]),
        "authors": page_meta["authors"] or list(meta_in.get("authors") or []),
        "arxiv_id": meta_in.get("arxiv_id"), "arxiv_version": meta_in.get("arxiv_version"),
        "arxiv_requested_version": meta_in.get("arxiv_requested_version"), "tools": tools,
    })
    if meta_in.get("published") and not any(c["source"] == str(meta_in.get("published_source")) for c in report["published_candidates"]):
        report["published_candidates"].insert(0, {"value": normalize_date(str(meta_in["published"])),
                                                  "source": str(meta_in.get("published_source") or "fetch")})
    return Result(body, report)
