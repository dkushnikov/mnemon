"""capture.check — the acquisition decision table, the conversion check, the inventory, the outcome
(spec §3.4, D6, D7, §12 A7).

`acquisition` names what a fetched raw is (`ok`, `thin`, `js-shell`, `bot-wall`, `forbidden`, `login`,
`paywall`, `rate-limit`, `not-found`, `server-error`, `unavailable`, `http`) by one ordered table — the
chain decides from it. `conversion` compares a sanitized candidate with the reference projected from the
same raw bytes: a reference line is present when its *key* (NFKC, casefold, soft hyphens and zero-width
characters removed, every alphanumeric character of any script kept) is a substring of the candidate's key
**and** its word sequence occurs as a contiguous token sequence in the candidate's tokens — in the spaced
form, or with the boundaries the page itself writes without whitespace (`SOFT`) joined — a key match
without the token match is a *glued* line. Both checks are pure functions over strings; nothing here touches
the network or the vault. Stdlib only, python 3.9.
"""
from __future__ import annotations

import html as htmlmod
import re
import unicodedata
from collections import defaultdict
from typing import Dict, List, NamedTuple, Optional

WALL_MAX_WORDS = 200
JS_SHELL_MAX_WORDS = 30
# The reference projection marks a whitespace-free inline boundary (a drop cap `T`+`he`, an anchor inside a
# word) with this invisible separator: the token check then accepts a candidate holding either the spaced
# sequence or the sequence with those boundary tokens joined. It is not alphanumeric, so keys ignore it.
SOFT = "⁣"
THIN_WORDS_DEFAULT = 60
CHROME_MAX_WORDS = 12
MISSING_EXAMPLE_CHARS = 120

BOT_WALL_MARKERS = ("cf-chl", "challenge-platform", "just a moment", "attention required", "vercel security checkpoint",
                    "access denied", "verify you are human", "_incapsula_", "please enable javascript and cookies",
                    "checking your browser", "px-captcha", "datadome", "request blocked")
LOGIN_MARKERS = ("sign in to continue", "log in to view", "login to continue", "you must be logged in")
PAYWALL_MARKERS = ("subscribe to continue reading", "this post is for paid subscribers",
                   "continue reading with a subscription", "subscribers only", 'class="paywall"', "data-paywall",
                   'id="paywall"')

# chain actions and retryability per kind (§3.4; A7 for the amended rows)
RULES = {
    "ok": ("done", None), "thin": ("render-once", None), "js-shell": ("escalate", None),
    "bot-wall": ("escalate", False), "forbidden": ("escalate", False), "login": ("fail", False),
    "paywall": ("publish-blocked", None), "rate-limit": ("fail", True), "not-found": ("fail", False),
    "server-error": ("fail", True), "unavailable": ("fail", True), "http": ("fail", False),
}
WALL_KINDS = ("login", "paywall", "bot-wall", "forbidden", "rate-limit")

CHROME = re.compile(
    r"(?:^(?:skip to (?:main )?content|share(?: this)?(?: (?:post|article|page|story))?(?: on \w+)?|subscribe(?: now| today)?|"
    r"sign (?:in|up)(?: (?:to|with|for) .*)?|log ?in|log ?out|register|menu|home|search|close|open|next|previous|"
    r"back(?: to top)?|read more|learn more|copy link|print|download|loading\.*…?|table of contents|on this page|"
    r"in this article|related (?:posts|articles|stories)|follow us.*|contact(?: us)?|about(?: us)?|"
    r"privacy(?: policy| notice)?|terms(?: (?:of|and) (?:service|use|conditions))?|cookie(?:s)?(?: (?:policy|settings|preferences|notice))?|"
    r"accept(?: all)?(?: cookies)?|reject(?: all)?(?: cookies)?|manage (?:cookies|preferences)|got it|dismiss|"
    r"edit (?:this )?(?:page|on github)|last updated.*|was this (?:page |article )?helpful\??|yes|no|"
    r"comments?|leave a (?:comment|reply)|reply|show more|view all|toggle (?:navigation|menu|dark mode)|"
    r"dark mode|light mode|get started(?: for free)?|try (?:it )?(?:for )?free|book a demo|request a demo|"
    r"sign up for free|start (?:your )?free trial|powered by .*|copyright.*|©.*|all rights reserved\.?|"
    r"skip to .*|newsletter|share|like|tweet|pin it|email|\d+ (?:comments?|likes?|shares?|min(?:ute)?s? read))$"
    r"|\bwe use cookies\b|\baccept all cookies\b|\bcookie (?:settings|policy|preferences|consent|notice)\b|"
    r"\ball rights reserved\b|©|\bprivacy policy\b|\bterms of (?:service|use)\b|\bskip to (?:main )?content\b|"
    r"\bpowered by\b|\bsign in to\b|\blog in to\b|\bcontinue reading\b|\bsubscribe to\b|\bshare this\b|\bfollow us\b)",
    re.IGNORECASE)

# A reader-comment section is cut at a heading only when the heading names a comment widget
# (`N comments`, `comments (N)`, `leave a comment|reply`, `reader comments`, `post a comment`); a bare
# `Comments`, `Discussion`, `Responses` or `Replies` heading is body text and never a cut.
COMMENTS = re.compile(r"^(?:#{1,6}\s*)?(?:\d+\s+comments?|comments?\s*\(\d+\)|leave a (?:comment|reply)|reader comments?|"
                      r"post a comment)\s*$", re.IGNORECASE)
# Container patterns match a whole id/class token (`comments`, `comment-list`, `disqus_thread`, …, or a
# `comments-*` family), never a substring of one: Prism's `token comment` and highlight.js's
# `hljs-comment` are code, not a widget. They apply to block containers outside <pre>/<code> only.
COMMENTS_CONTAINER = re.compile(r"^(?:comments|comments?[-_]list|comments?[-_]section|comments?[-_]area|comments?[-_]thread|"
                                r"comments?[-_]wrapper|comment[-_]respond|post[-_]comments|disqus(?:[-_]thread)?|giscus|utterances|"
                                r"commento|respond|comments[-_][a-z0-9_-]+)$", re.IGNORECASE)
COOKIE_CONTAINER = re.compile(r"^(?:cookies?|consent|gdpr|cc[-_]banner|cc[-_]window|cmp[-_]container|cookieconsent|cookiebanner|"
                              r"cookienotice|onetrust[-_][a-z0-9_-]+|cookies?[-_][a-z0-9_-]+|consent[-_][a-z0-9_-]+|"
                              r"gdpr[-_][a-z0-9_-]+)$", re.IGNORECASE)
CONTAINER_TAGS = frozenset(("div", "section", "aside", "article", "ol", "ul", "dl", "li", "main", "header", "footer", "figure",
                            "details", "dialog", "fieldset"))

_SCRIPT = re.compile(r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_HEAD = re.compile(r"<head\b[^>]*>.*?</head\s*>|<title\b[^>]*>.*?</title\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_ONLY = re.compile(r"<script\b[^>]*>.*?</script\s*>|<script\b[^>]*/>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_EMPTY_ROOT = re.compile(r"<(?:div|main|section|body)\b[^>]*\bid=[\"'](?:root|app|__next|__nuxt)[\"'][^>]*>\s*</(?:div|main|section|body)>",
                         re.IGNORECASE)
_NOSCRIPT = re.compile(r"<noscript\b[^>]*>(.*?)</noscript\s*>", re.IGNORECASE | re.DOTALL)
_FORM = re.compile(r"<form\b", re.IGNORECASE)
_PASSWORD = re.compile(r"<input\b[^>]*\btype=[\"']?password", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_ZW = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_TOKEN = re.compile(r"[^\W_]+")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_REFLINK = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_MD_LINKDEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)
_MD_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_MD_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")


class Acq(NamedTuple):
    outcome: str
    kind: Optional[str]
    evidence: Optional[str]
    visible_words: int
    title: Optional[str]

    def as_report(self) -> dict:
        return {"outcome": self.outcome, "kind": self.kind, "evidence": self.evidence,
                "visible_words": self.visible_words}

    @property
    def chain(self) -> str:
        return RULES.get(self.outcome, ("fail", False))[0]

    @property
    def retryable(self):
        return RULES.get(self.outcome, ("fail", False))[1]


class Conv(NamedTuple):
    missing: List[str]
    glued: List[str]
    headings_missing: List[str]
    reference_count: int
    eligible_count: int

    @property
    def passed(self) -> bool:
        return not (self.missing or self.glued or self.headings_missing)


# --- the acquisition table ---------------------------------------------------------------------------------

def visible_text(body_html: str) -> str:
    text = _HEAD.sub(" ", body_html)
    text = _SCRIPT.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = htmlmod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def visible_words(body_html: str) -> int:
    return len(visible_text(body_html).split())


def page_title(doc_html: str) -> Optional[str]:
    m = _TITLE.search(doc_html)
    if not m:
        return None
    title = htmlmod.unescape(_TAG.sub(" ", m.group(1)))
    title = re.sub(r"[\x00-\x1f\x7f]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()[:300]
    return title or None


def _find_marker(haystack: str, markers) -> Optional[str]:
    for m in markers:
        if m in haystack:
            return m
    return None


def js_shell_signal(body_html: str) -> Optional[str]:
    """An empty root container, script bytes above 60 % of the body, or a <noscript> naming JavaScript."""
    if _EMPTY_ROOT.search(body_html):
        return "empty root container"
    total = len(body_html)
    if total:
        script_bytes = sum(len(m.group(0)) for m in _SCRIPT_ONLY.finditer(body_html))
        if script_bytes * 100 > total * 60:
            return "script bytes %d%%" % (script_bytes * 100 // total)
    for m in _NOSCRIPT.finditer(body_html):
        if "javascript" in m.group(1).lower():
            return "<noscript> names JavaScript"
    return None


def acquisition(status: Optional[int], headers: Optional[Dict[str, str]], body: bytes, *,
                thin_words: int = THIN_WORDS_DEFAULT) -> Acq:
    """The decision table of §3.4 as amended by A7, first match wins. `status` is None for a rendered page."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    lower = text.lower()
    header_text = "\n".join("%s: %s" % (k, v) for k, v in headers.items()).lower()
    haystack = lower + "\n" + header_text
    W = visible_words(text)
    title = page_title(text)
    retry_after = headers.get("retry-after")
    bot = _find_marker(haystack, BOT_WALL_MARKERS)
    login = _find_marker(haystack, LOGIN_MARKERS)
    if login is None and W < WALL_MAX_WORDS and len(_FORM.findall(text)) == 1 and _PASSWORD.search(text):
        login = "a single form with a password field"
    paywall = _find_marker(haystack, PAYWALL_MARKERS)

    def row(kind, evidence=None):
        return Acq(kind, kind, evidence, W, title)

    if status == 429:
        return row("rate-limit", "status 429")
    if status == 503:
        if retry_after is not None:
            return row("rate-limit", "status 503 with Retry-After")
        if bot:
            return row("bot-wall", bot)
        return row("unavailable", "status 503")
    if status is not None and 500 <= status <= 599:
        return row("server-error", "status %d" % status)
    if status == 401:
        return row("login", "status 401")
    # A7 (i): login and paywall rows before the 403/407 rows, whatever the status
    if login and W < WALL_MAX_WORDS:
        return row("login", login)
    if paywall:
        if W >= thin_words:
            return row("paywall", paywall)
        return row("login", "%s (nothing readable)" % paywall)
    if status == 407:
        return row("forbidden", "status 407")
    if status == 403:
        if bot:
            return row("bot-wall", bot)
        return row("forbidden", "status 403")
    if status in (404, 410):
        return row("not-found", "status %d" % status)
    if status is not None and status >= 400:
        return row("http", "status %d" % status)
    if status is not None and 300 <= status <= 399:
        return row("http", "status %d without a followed Location" % status)
    if bot and W < WALL_MAX_WORDS:
        return row("bot-wall", bot)
    signal = js_shell_signal(text)
    if signal and W < JS_SHELL_MAX_WORDS:
        return row("js-shell", signal)
    if W < thin_words:
        return Acq("thin", "thin", "visible words %d < %d" % (W, thin_words), W, title)
    return Acq("ok", None, None, W, title)


# --- the conversion check ----------------------------------------------------------------------------------

def normalize(s: str) -> str:
    return unicodedata.normalize("NFKC", s).translate(_ZW).casefold()


def line_key(s: str) -> str:
    """NFKC, casefold, soft hyphens and zero-width characters removed, every alphanumeric character kept."""
    return "".join(ch for ch in normalize(s) if ch.isalnum())


def tokens(s: str) -> List[str]:
    """The alphanumeric tokens of a line, in order (digits included, any script)."""
    return _TOKEN.findall(normalize(s))


def spaceless_chars(s: str) -> int:
    """Characters of scripts written without spaces (CJK ideographs, kana, hangul, Thai)."""
    n = 0
    for ch in s:
        o = ord(ch)
        if (0x2E80 <= o <= 0x2FDF or 0x3040 <= o <= 0x30FF or 0x3100 <= o <= 0x312F or 0x3400 <= o <= 0x4DBF
                or 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xAC00 <= o <= 0xD7AF or 0x0E00 <= o <= 0x0E7F
                or 0x20000 <= o <= 0x3134F):
            n += 1
    return n


def candidate_text(md: str) -> str:
    """A candidate reduced to its text: link and image targets dropped (their tokens are not page text)."""
    text = _MD_LINKDEF.sub("", md)
    text = _MD_IMAGE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_REFLINK.sub(r"\1", text)
    text = _MD_AUTOLINK.sub(r"\1", text)
    text = _MD_HTML_TAG.sub(" ", text)
    return text


def classify_line(line: str, title_key: Optional[str]) -> Optional[str]:
    """`heading` (h1–h3, ≥ 2 words), `body` (≥ 6 words), `spaceless` (≥ 20 CJK/Thai characters), or None."""
    is_heading = line.startswith("## ")
    text = line[3:] if is_heading else line
    words = text.split()
    key = line_key(text)
    if not key:
        return None
    if title_key and key == title_key:
        return None
    if len(words) <= CHROME_MAX_WORDS and CHROME.search(text.strip()):
        return None
    if is_heading:
        return "heading" if len(words) >= 2 else None
    if len(words) >= 6:
        return "body"
    if spaceless_chars(text) >= 20:
        return "spaceless"
    return None


class _TokenIndex:
    def __init__(self, toks: List[str]):
        self.toks = toks
        self.first = defaultdict(list)
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


def reference_lines(reference: str) -> List[str]:
    return [ln.strip() for ln in reference.split("\n") if ln.strip()]


def token_forms(line: str) -> List[List[str]]:
    """The token sequences a reference line may be present as: the spaced one (every boundary a space) and,
    when the line carries SOFT boundaries, the one with those boundary tokens joined."""
    spaced = tokens(line.replace(SOFT, " "))
    if SOFT not in line:
        return [spaced]
    joined = tokens(line.replace(SOFT, ""))
    return [spaced, joined] if joined != spaced else [spaced]


def plain(line: str) -> str:
    """A reference line for display: the page's own word boundaries (SOFT boundaries joined)."""
    return line.replace(SOFT, "")


def conversion(candidate_md: str, reference: str, title: Optional[str]) -> Conv:
    """Every eligible reference line and every h1–h3 heading must be present (key + contiguous tokens, in
    the spaced form or with the SOFT boundaries joined)."""
    lines = reference_lines(reference)
    text = candidate_text(candidate_md)
    cand_key = line_key(text)
    index = _TokenIndex(tokens(text))
    title_key = line_key(title) if title else None
    missing, glued, headings_missing = [], [], []
    eligible = 0
    for line in lines:
        cls = classify_line(line, title_key)
        if cls is None:
            continue
        eligible += 1
        body = line[3:] if cls == "heading" else line
        key = line_key(body)
        if key not in cand_key:
            (headings_missing if cls == "heading" else missing).append(plain(body))
            continue
        if cls == "spaceless":
            continue
        if not any(index.contains(form) for form in token_forms(body)):
            (headings_missing if cls == "heading" else glued).append(plain(body))
    return Conv(missing, glued, headings_missing, len(lines), eligible)


# --- inventory and outcome ---------------------------------------------------------------------------------

_H_HTML = re.compile(r"<h[1-6]\b", re.IGNORECASE)
_LI_HTML = re.compile(r"<li\b", re.IGNORECASE)
_TABLE_HTML = re.compile(r"<table\b", re.IGNORECASE)
_PRE_HTML = re.compile(r"<pre\b", re.IGNORECASE)
_A_HTML = re.compile(r"<a\b[^>]*\bhref=", re.IGNORECASE)
_IMG_HTML = re.compile(r"<img\b", re.IGNORECASE)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_H_MD = re.compile(r"^ {0,3}#{1,6}\s")
_LI_MD = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_TABLE_SEP_MD = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$")
_LINK_MD = re.compile(r"(?<!!)\[[^\]]*\]\([^)]+\)|<https?://[^>\s]+>")
_IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]+\)|\*\[(?:img|inline figure omitted at capture): [^\]]*\]\*")


def markdown_counts(md: str) -> dict:
    headings = items = tables = code = links = images = 0
    fence = None
    lines = md.split("\n")
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
            code += 1
            continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            continue
        if _H_MD.match(line):
            headings += 1
        if _LI_MD.match(line):
            items += 1
        if _TABLE_SEP_MD.match(line) and "|" in line and i > 0 and "|" in lines[i - 1]:
            tables += 1
        links += len(_LINK_MD.findall(line))
        images += len(_IMG_MD.findall(line))
    return {"headings": headings, "list_items": items, "tables": tables, "code_blocks": code, "links": links,
            "images": images}


def html_counts(subtree_html: str) -> dict:
    return {"headings": len(_H_HTML.findall(subtree_html)), "list_items": len(_LI_HTML.findall(subtree_html)),
            "tables": len(_TABLE_HTML.findall(subtree_html)), "code_blocks": len(_PRE_HTML.findall(subtree_html)),
            "links": len(_A_HTML.findall(subtree_html)), "images": len(_IMG_HTML.findall(subtree_html))}


def inventory(subtree_html: str, md: str) -> dict:
    h, m = html_counts(subtree_html), markdown_counts(md)
    return {k: {"html": h[k], "md": m[k]} for k in ("headings", "list_items", "tables", "code_blocks", "links", "images")}


def outcome(acq: Optional[Acq], conv: Optional[Conv], inv: Optional[dict]) -> str:
    """blocked → unknown → partial/complete (A7 vi)."""
    if acq is not None and acq.kind == "paywall":
        return "blocked"
    if conv is None or conv.eligible_count == 0:
        return "unknown"
    if conv.missing or conv.glued or conv.headings_missing:
        return "partial"
    if inv:
        if inv["tables"]["md"] < inv["tables"]["html"] or inv["code_blocks"]["md"] < inv["code_blocks"]["html"]:
            return "partial"
    return "complete"


def missing_examples(conv: Conv, limit: int = 5) -> List[str]:
    out = []
    for line in conv.missing + conv.glued + conv.headings_missing:
        out.append(line[:MISSING_EXAMPLE_CHARS])
        if len(out) >= limit:
            break
    return out


LIMITS = ["lines under six words that are not headings are not required",
          "table cells, equations and operators are not required",
          "the CHROME regex is applied only to lines of at most 12 words"]
