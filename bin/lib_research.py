#!/usr/bin/env python3
"""lib_research.py — the typed reader and validator of research documents.

docs/specs/2026-10-27-research-interface.md (v1.1): one module reads `Collections/*.md` and
`Wiki/*.md` for `reindex`, `search`, the vault scanner (`lib_vault.py`) and the doctor, so every
consumer sees the same items, the same prints, the same states and the same failures. Schema
`collection/1`: a flat YAML subset in the frontmatter (scalars and flat lists, no nested
mappings, no duplicate keys) and one representation of the items — the lines of the `## Items`
section, by the grammar below. Schema `wiki/1` (phase 3b): a nested subset (`based_on` as a list
of {rev, text_sha256, seal} entries, `inputs` as a mapping with the dossier fingerprints), the
evidential body (from the first heading to `## Atlas reading` / `## Log`, LF, trailing spaces
stripped) and its sha256, the locators of the body (`[[Sources/<rev>/source#<heading>]]` — the
heading exists once in source.md; `> "<fragment>" — [[Sources/<rev>/source]]` — a substring after
whitespace normalization), and the derived states `validate_wiki` computes against the vault's
research context (lib_vault.research_context): `unbound` · `suppressed` (fail) · `edited-outside-
update` · `stale` · `inputs-changed` · `orphan` · `weak-evidence` · `budget` (warn), `not probed`
when a dependency could not be read; `is_suppressed` is the one veto predicate (an open supporting
collection with `wiki: off` or the page's name in `suppressed_pages`).

    python3 lib_research.py --vault <path> [--json]        every Collections/*.md and Wiki/*.md (with states), exit 1 on a fail
    python3 lib_research.py --file <path> [--json]         one document (a wiki page by its `type: wiki`; no states)

Grammar of an `## Items` line (interface §3; accepted / rejected):

- a list line (`- ` or `* `, any indentation) with exactly one wikilink of the form
  `[[Sources/<rev>/extract]]`, `[[Sources/<rev>/source]]` or `[[Sources/<rev>]]` (optional `.md`,
  `|display`, `#fragment`) is an item; the role is the first `**…**` segment right after the
  dash that follows the link, the note is the rest; `**excluded**` = considered, not cited;
- a nested line (indented two spaces or more, with or without a list marker) without a link
  continues the previous item's note; a nested line with a Sources link is an item of its own;
- a line with no `Sources/` link, with two or more wikilinks, with `..` or an absolute path in
  the target, or with a target outside this vault's `Sources/` is malformed → the document
  fails (the other items still count, the line is named);
- text between items without a list marker is malformed; a second `## Items` heading fails the
  document; fenced blocks are skipped; blank lines, trailing punctuation after the link and the
  three dashes (`—`, `–`, `-`) are tolerated.

Observational: nothing is written, nothing is resolved through the file system beyond reading
the document itself; `existing` (the set of revision names) is handed in by the caller. Stdlib
only, python 3.9.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

INTERFACE = "research-interface/1"
COLLECTION_SCHEMA = "collection/1"
WIKI_SCHEMA = "wiki/1"
COLLECTIONS_DIR = "Collections"
WIKI_DIR = "Wiki"

# wiki/1 (interface §9): the stored keys, the prints, the consumable sections, the versions a page's
# `inputs` is compared with (phase 3c's research-update writes them; a mismatch is `inputs-changed`)
WIKI_KINDS = ("concept", "comparison", "contradiction", "open-question")
WIKI_REQUIRED = ("type", "kind", "collections", "based_on", "inputs", "body_sha256")
WIKI_SCALAR_KEYS = ("type", "kind", "maintained_by", "created", "updated", "visibility", "body_sha256")
WIKI_LIST_KEYS = ("collections", "domains")
WIKI_PRINT_KEYS = ("based_on", "inputs")
WIKI_CONSUMABLE_SECTIONS = ("Atlas reading", "Log")
WIKI_SEALS = ("capture", "observed")
WIKI_INPUT_KEYS = ("page", "kind", "collections", "charter_sha256", "protocol", "template")
WIKI_INPUT_REQUIRED = ("page", "kind", "collections", "protocol", "template")
RESEARCH_PROTOCOL = "research/1"
WIKI_TEMPLATE_VERSION = 1
WIKI_STATE_ORDER = ("unbound", "suppressed", "edited-outside-update", "stale", "inputs-changed", "orphan",
                    "weak-evidence", "budget")
WIKI_FAIL_STATES = ("unbound", "suppressed")
DEFAULT_WIKI_PAGES_PER_COLLECTION = 5
DEFAULT_WIKI_WORDS = 3000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUOTE_RE = re.compile(r'^>\s*["«“](.*?)["»”]\s*[—–-]\s*!?\[\[([^\]]*)\]\]\s*[.,;:]?\s*$')

COLLECTION_STATUS = ("open", "inbox", "closed")
ONOFF = ("on", "off")
COLLECTION_REQUIRED = ("type", "status", "question", "curated_by", "created")
COLLECTION_LIST_KEYS = ("domains", "tags", "aliases", "suppressed_pages")
COLLECTION_SCALAR_KEYS = ("type", "status", "question", "curated_by", "created", "updated", "wiki",
                          "recusal", "migrated_from", "source_of")
COLLECTION_KNOWN_SECTIONS = ("Why", "Items", "Open questions", "Notes (owner)")
ITEMS_HEADING = "Items"
ROLE_EXCLUDED = "excluded"
ITEM_FILES = {"": None, "extract": "extract", "extract.md": "extract", "source": "source", "source.md": "source"}

REV_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[0-9a-f]{8}(-\d+)?$")
WIKILINK_RE = re.compile(r"!?\[\[([^\]]*)\]\]")
LIST_RE = re.compile(r"^([ \t]*)([-*])[ \t]+(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
FENCE_CLOSE_RE = re.compile(r"^(`{3,}|~{3,})[ \t]*$")
KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*):(?:[ \t]+(.*))?$")
DASH_RE = re.compile(r"^[ \t]*[—–-][ \t]*")
ROLE_RE = re.compile(r"^\*\*(.+?)\*\*[ \t]*(.*)$", re.S)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------------------------
# The YAML subset
# ---------------------------------------------------------------------------------------------

def decode_dq(inner: str) -> str:
    """The body of a double-quoted scalar: `\\"` → `"` and `\\\\` → `\\`, nothing else (the
    serialization subset of the capture contracts §4.4)."""
    out: List[str] = []
    i, n = 0, len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n and inner[i + 1] in ('"', "\\"):
            out.append(inner[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def scalar(raw: str) -> str:
    """A frontmatter scalar as the engine reads it: double-quoted decodes `\\"` and `\\\\`;
    single-quoted is verbatim to its closing quote; a bare value loses a trailing ` # comment`
    and surrounding whitespace; `#…` is empty."""
    v = raw.strip()
    if v.startswith('"'):
        i = 1
        while i < len(v):
            if v[i] == "\\":
                i += 2
                continue
            if v[i] == '"':
                break
            i += 1
        if i < len(v) and (i == len(v) - 1 or re.match(r"^\s+#", v[i + 1:])):
            return decode_dq(v[1:i])
    if v.startswith("'"):
        m = re.match(r"^'((?:[^']|'')*)'(?:\s+#.*)?$", v)
        if m:
            return m.group(1).replace("''", "'")
    if v.startswith("#"):
        return ""
    return re.sub(r"\s#.*$", "", v).strip()


def inline_list(inner: str) -> List[str]:
    """The items of `[a, "b, c", d]` (commas outside quotes), each a scalar."""
    items: List[str] = []
    buf = ""
    quote = ""
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            buf += ch
        elif ch == ",":
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf)
    return [scalar(x) for x in items if x.strip()]


def split_frontmatter(text: str) -> Tuple[Optional[str], str, int]:
    """(block, body, body_line_offset): the lines between the opening `---` and the closing
    `---`/`...`, the text after it and the 1-based line number of the body's first line;
    (None, text, 1) without a closed header."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text, 1
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]), i + 2
    return None, text, 1


def parse_flat_frontmatter(block: str) -> Tuple[Dict[str, object], List[str]]:
    """The flat subset: `key: scalar`, `key: [a, b]`, `key:` + `- item` lines. A duplicate key,
    a nested mapping or an unparsable line is an error (the first value is kept)."""
    data: Dict[str, object] = {}
    errors: List[str] = []
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            errors.append("line %d: unexpected indented line outside a block list: %s" % (i + 2, line.strip()[:60]))
            i += 1
            continue
        m = KEY_RE.match(line.rstrip())
        if not m:
            errors.append("line %d: not a `key: value` line: %s" % (i + 2, line.strip()[:60]))
            i += 1
            continue
        key, raw = m.group(1), (m.group(2) or "")
        value: object
        j = i + 1
        if raw.strip() and not raw.strip().startswith("#"):
            r = raw.strip()
            if r.startswith("["):
                # an inline list: the trailing ` # comment` after the closing bracket is dropped;
                # a bracket inside it (outside quotes) is a nested list — not in the subset
                end = r.rfind("]")
                if end > 0 and (end == len(r) - 1 or re.match(r"^\s+#", r[end + 1:])):
                    inner = r[1:end]
                    if re.search(r"[\[\]]", re.sub(r"\"[^\"]*\"|'[^']*'", "", inner)):
                        errors.append("nested list under `%s` (flat scalars and lists only)" % key)
                        value = ""
                    else:
                        value = inline_list(inner)
                else:
                    value = scalar(r)
            else:
                value = scalar(r)
        else:
            items: List[str] = []
            nested = None
            first_indent = None
            while j < len(lines):
                cont = lines[j]
                s = cont.strip()
                if not s or s.startswith("#"):
                    j += 1
                    continue
                if cont[0] not in " \t" and not (s.startswith("- ") or s == "-"):
                    break
                lead = len(cont.replace("\t", "  ")) - len(cont.replace("\t", "  ").lstrip(" "))
                if s == "-" or s.startswith("- "):
                    if first_indent is None:
                        first_indent = lead
                    elif lead > first_indent:
                        nested = "list"
                    items.append("" if s == "-" else scalar(s[2:]))
                else:
                    nested = nested or "mapping"
                j += 1
            if nested:
                errors.append("nested %s under `%s` (flat scalars and lists only)" % (nested, key))
                value = ""
            else:
                value = items if items else ""
        if key in data:
            errors.append("duplicate key `%s`" % key)
        else:
            data[key] = value
        i = j
    return data, errors


# ---------------------------------------------------------------------------------------------
# Links and the `## Items` grammar
# ---------------------------------------------------------------------------------------------

def classify_link(raw: str) -> Tuple[Optional[dict], Optional[str]]:
    """(link, reason): the parts of one wikilink target — `rev`, `file`, `fragment`, `display` —
    or the reason it is not an item link. The raw target is read before any resolver: `..`, an
    absolute path and a target outside `Sources/` are rejected here."""
    target = raw
    display = None
    fragment = None
    if "|" in target:
        target, display = target.split("|", 1)
        display = display.strip() or None
    if "#" in target:
        target, fragment = target.split("#", 1)
        fragment = fragment.strip() or None
    target = target.strip()
    if not target:
        return None, "empty link target"
    if "\\" in target:
        return None, "backslash in link target"
    if target.startswith("/"):
        return None, "absolute path in link target"
    segments = target.split("/")
    if any(seg in ("..", ".") for seg in segments):
        return None, "path traversal in link target"
    if segments[0] != "Sources":
        return None, "link outside Sources/ (another folder or another instance)"
    if len(segments) < 2 or not segments[1]:
        return None, "link names no revision"
    rev = segments[1]
    rest = "/".join(segments[2:])
    if not REV_RE.match(rev):
        return None, "revision name malformed: %s" % rev
    if rest not in ITEM_FILES:
        return None, "unknown file in revision: %s" % rest
    return {"rev": rev, "file": ITEM_FILES[rest], "fragment": fragment, "display": display}, None


def parse_item_tail(tail: str) -> Tuple[Optional[str], str]:
    """(role, note) of the text after an item's link: trailing punctuation and one dash are
    dropped; a leading `**…**` is the role (normalized to lower case), the rest the note."""
    t = tail.strip()
    t = re.sub(r"^[.,;:]+", "", t).strip()
    t = DASH_RE.sub("", t, count=1)
    m = ROLE_RE.match(t)
    if m:
        role = " ".join(m.group(1).split()).strip("*_ ").rstrip(":.,;").strip().lower()
        note = DASH_RE.sub("", re.sub(r"^[*_\s]*[:.,;]*\s*", "", m.group(2)), count=1).strip()
        return role or None, note
    return None, t


def _sections(body: str, offset: int) -> Tuple[List[dict], List[str]]:
    """The `## ` sections of a body outside fences: [{name, start, end, lines}] (1-based line
    numbers; `start` is the heading's line) and the headings in order. Lines before the first
    `## ` heading form the preamble (name None)."""
    lines = body.split("\n")
    sections: List[dict] = []
    current = {"name": None, "start": offset, "lines": []}
    fence = None            # (char, run length) of the open fence
    for i, line in enumerate(lines):
        ln = offset + i
        stripped = line.lstrip()
        if fence is None:
            fo = FENCE_OPEN_RE.match(stripped)
            if fo:
                fence = (fo.group(1)[0], len(fo.group(1)))
                current["lines"].append((ln, line, True))
                continue
        else:
            fc = FENCE_CLOSE_RE.match(stripped)
            current["lines"].append((ln, line, True))
            if fc and fc.group(1)[0] == fence[0] and len(fc.group(1)) >= fence[1]:
                fence = None
            continue
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            sections.append(current)
            current = {"name": " ".join(m.group(2).split()), "start": ln, "lines": []}
            continue
        current["lines"].append((ln, line, False))
    sections.append(current)
    names = [s["name"] for s in sections if s["name"] is not None]
    return sections, names


def parse_items(section_lines: List[Tuple[int, str, bool]]) -> Tuple[List[dict], List[dict]]:
    """(items, malformed) of the lines of one `## Items` section."""
    items: List[dict] = []
    malformed: List[dict] = []
    seen: Dict[str, int] = {}

    def bad(ln: int, text: str, reason: str) -> None:
        malformed.append({"line": ln, "reason": reason, "text": text.strip()[:120]})

    for ln, line, fenced in section_lines:
        if fenced or not line.strip():
            continue
        m = LIST_RE.match(line)
        expanded = line.replace("\t", "  ")
        indent = len(expanded) - len(expanded.lstrip(" "))
        content = m.group(3) if m else line.strip()
        links = WIKILINK_RE.findall(content)
        if not links and indent >= 2 and items:
            # an indented line without a link — with or without a marker — continues the item above
            # it (Markdown's lazy continuation), unless a malformed line stands between them
            if not malformed or malformed[-1]["line"] < items[-1]["line"]:
                items[-1]["note"] = (items[-1]["note"] + "\n" + content.strip()).strip()
            else:
                bad(ln, line, "continuation under a malformed line")
            continue
        if not m:
            bad(ln, line, "free text between items (no list marker)")
            continue
        if len(links) >= 2:
            bad(ln, line, "two or more links on one line")
            continue
        if not links:
            bad(ln, line, "list line without a Sources/ link")
            continue
        link, reason = classify_link(links[0])
        if link is None:
            bad(ln, line, reason or "not an item link")
            continue
        lm = WIKILINK_RE.search(content)
        role, note = parse_item_tail(content[lm.end():] if lm else "")
        rev = link["rev"]
        if rev in seen:
            bad(ln, line, "duplicate revision %s (first at line %d)" % (rev, seen[rev]))
            continue
        seen[rev] = ln
        items.append({"line": ln, "rev": rev, "file": link["file"], "fragment": link["fragment"],
                      "display": link["display"], "role": role, "excluded": role == ROLE_EXCLUDED,
                      "note": note, "exists": None})
    return items, malformed


# ---------------------------------------------------------------------------------------------
# collection/1
# ---------------------------------------------------------------------------------------------

def _is_date(v: object) -> bool:
    return isinstance(v, str) and bool(ISO_RE.match(v.strip())) and _valid_date(v.strip())


def _valid_date(s: str) -> bool:
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _str_or_none(v: object) -> Optional[str]:
    if isinstance(v, str):
        return v if v != "" else None
    return None


def _list(v: object) -> Optional[list]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if v == "" or v is None:
        return []
    return None


def read_collection_text(text: str, name: str, existing: Optional[Set[str]] = None,
                         file: Optional[str] = None) -> dict:
    """The collection/1 record of one document (the interface's reader record)."""
    rec: dict = {
        "schema": COLLECTION_SCHEMA, "name": name, "file": file or "%s/%s.md" % (COLLECTIONS_DIR, name),
        "state": "ok", "errors": [],
        "status": None, "question": None, "curated_by": None, "created": None, "updated": None,
        "domains": [], "tags": [], "aliases": [], "wiki": "on", "suppressed_pages": [], "recusal": None,
        "migrated_from": None, "source_of": None, "extra": {},
        "sections": [], "items": [], "malformed": [],
        "counts": {"items": 0, "excluded": 0, "malformed": 0, "unresolved": None, "links_outside_items": 0},
    }
    errors = rec["errors"]
    if text.startswith("\ufeff"):
        text = text[1:]
    block, body, offset = split_frontmatter(text)
    if block is None:
        errors.append("no frontmatter (an opening `---` line closed by `---`)")
        data: Dict[str, object] = {}
    else:
        data, ferr = parse_flat_frontmatter(block)
        errors.extend(ferr)

    for key in COLLECTION_REQUIRED:
        if key not in data or data[key] == "" or data[key] == []:
            errors.append("missing required `%s`" % key)
    t = data.get("type")
    if t is not None and t != "collection":
        errors.append("`type` must be `collection` (got %r)" % (t,))
    st = data.get("status")
    if isinstance(st, str) and st:
        if st not in COLLECTION_STATUS:
            errors.append("`status` must be one of %s (got %r)" % ("|".join(COLLECTION_STATUS), st))
        else:
            rec["status"] = st
    elif st is not None and not isinstance(st, str):
        errors.append("`status` must be a scalar")
    for key in ("question", "curated_by", "migrated_from", "source_of"):
        v = data.get(key)
        if v is not None and not isinstance(v, str):
            errors.append("`%s` must be a scalar" % key)
        else:
            rec[key] = _str_or_none(v)
    for key in ("created", "updated"):
        v = data.get(key)
        if v is None or v == "":
            continue
        if not _is_date(v):
            errors.append("`%s` is not an ISO date (YYYY-MM-DD)" % key)
        else:
            rec[key] = v.strip()
    for key in ("wiki", "recusal"):
        v = data.get(key)
        if v is None or v == "":
            continue
        if not isinstance(v, str) or v.strip().lower() not in ONOFF:
            errors.append("`%s` must be on|off (got %r)" % (key, v))
        else:
            rec[key] = v.strip().lower()
    for key in COLLECTION_LIST_KEYS:
        if key not in data:
            continue
        lst = _list(data[key])
        if lst is None:
            errors.append("`%s` must be a list" % key)
        else:
            rec[key] = lst
    for page in rec["suppressed_pages"]:
        if not page.strip() or "/" in page or "[[" in page or "]]" in page or page.endswith(".md"):
            errors.append("`suppressed_pages` entry must be a page file name without .md (got %r)" % page)
    known = set(COLLECTION_SCALAR_KEYS) | set(COLLECTION_LIST_KEYS)
    rec["extra"] = {k: v for k, v in data.items() if k not in known}

    sections, names = _sections(body, offset)
    rec["sections"] = names
    items_sections = [s for s in sections if s["name"] == ITEMS_HEADING]
    if not items_sections:
        errors.append("no `## %s` section" % ITEMS_HEADING)
    elif len(items_sections) > 1:
        errors.append("duplicate `## %s` heading (lines %s)" % (ITEMS_HEADING, ", ".join(str(s["start"]) for s in items_sections)))
    if items_sections:
        items, malformed = parse_items(items_sections[0]["lines"])
        rec["items"], rec["malformed"] = items, malformed
    outside = 0
    for s in sections:
        if s["name"] == ITEMS_HEADING and s is (items_sections[0] if items_sections else None):
            continue
        for _ln, line, fenced in s["lines"]:
            if not fenced:
                outside += len(WIKILINK_RE.findall(line))
    rec["counts"]["links_outside_items"] = outside
    if existing is not None:
        # `existing` is the set of revision names, or a mapping revision → the files it holds
        # ({"extract", "source"}): then an item's target file must be there too (the bare form
        # needs only the folder)
        for it in rec["items"]:
            if isinstance(existing, dict):
                files = existing.get(it["rev"])
                it["exists"] = files is not None and (it["file"] is None or it["file"] in files)
            else:
                it["exists"] = it["rev"] in existing
        rec["counts"]["unresolved"] = sum(1 for it in rec["items"] if not it["exists"])
    rec["counts"]["items"] = len(rec["items"])
    rec["counts"]["excluded"] = sum(1 for it in rec["items"] if it["excluded"])
    rec["counts"]["malformed"] = len(rec["malformed"])
    if errors or rec["malformed"]:
        rec["state"] = "fail"
    return rec


def unreadable_record(name: str, reason: str, file: Optional[str] = None) -> dict:
    rec = read_collection_text("---\n---\n", name, file=file)
    rec.update({"state": "unreadable", "errors": [reason], "items": [], "malformed": [], "sections": []})
    rec["counts"] = {"items": 0, "excluded": 0, "malformed": 0, "unresolved": None, "links_outside_items": 0}
    return rec


def read_collection(path: str, existing: Optional[Set[str]] = None, name: Optional[str] = None,
                    file: Optional[str] = None) -> dict:
    stem = name or os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return unreadable_record(stem, "unreadable: %s" % exc, file=file)
    return read_collection_text(data.decode("utf-8", "replace"), stem, existing=existing, file=file)


def list_collections(vault: str) -> List[str]:
    """The `Collections/*.md` stems, sorted; [] without the folder. A folder that exists but cannot be
    listed raises OSError — an unreadable folder is never an empty one (the callers count it)."""
    d = os.path.join(vault, COLLECTIONS_DIR)
    if not os.path.isdir(d):
        return []
    return sorted(n[:-3] for n in os.listdir(d)
                  if n.endswith(".md") and not n.startswith(".") and os.path.isfile(os.path.join(d, n)))


def scan_collections(vault: str, existing: Optional[Set[str]] = None) -> List[dict]:
    d = os.path.join(vault, COLLECTIONS_DIR)
    return [read_collection(os.path.join(d, n + ".md"), existing=existing, name=n) for n in list_collections(vault)]


def existing_revisions(vault: str) -> Dict[str, Set[str]]:
    """Every `Sources/<rev>/` → the files it holds (`extract`, `source`) — the `existing` of the reader."""
    d = os.path.join(vault, "Sources")
    out: Dict[str, Set[str]] = {}
    try:
        names = [n for n in os.listdir(d) if not n.startswith(".") and os.path.isdir(os.path.join(d, n))]
    except OSError:
        return out
    for n in names:
        files = set()
        if os.path.isfile(os.path.join(d, n, "extract.md")):
            files.add("extract")
        if os.path.isfile(os.path.join(d, n, "source.md")):
            files.add("source")
        out[n] = files
    return out


# ---------------------------------------------------------------------------------------------
# The nested YAML subset of a wiki page (interface §9)
# ---------------------------------------------------------------------------------------------

TREE_KEY_RE = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[A-Za-z_][\w.-]*):(?:[ \t]+(.*))?$""")


def _split_outside_quotes(inner: str, sep: str = ",") -> List[str]:
    parts: List[str] = []
    buf = ""
    quote = ""
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            buf += ch
        elif ch == sep:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return parts


def _inline_map(inner: str, where: str, errors: List[str]) -> Dict[str, str]:
    """`{"a": "b", c: d}` → a flat mapping of scalars (the form of `inputs.collections`)."""
    out: Dict[str, str] = {}
    for pair in _split_outside_quotes(inner):
        p = pair.strip()
        if not p:
            continue
        if p[0] in ('"', "'"):
            q = p[0]
            j = 1
            while j < len(p):
                if p[j] == "\\" and q == '"':
                    j += 2
                    continue
                if p[j] == q:
                    break
                j += 1
            key = scalar(p[:j + 1])
            rest = p[j + 1:].strip()
        else:
            if ":" not in p:
                errors.append("%s: `%s` is not a `key: value` pair" % (where, p[:40]))
                continue
            key, rest = p.split(":", 1)
            key = key.strip()
            rest = ":" + rest
        if not rest.startswith(":"):
            errors.append("%s: `%s` is not a `key: value` pair" % (where, p[:40]))
            continue
        if key in out:
            errors.append("%s: duplicate key `%s`" % (where, key))
            continue
        out[key] = scalar(rest[1:])
    return out


def parse_frontmatter_tree(block: str) -> Tuple[Dict[str, object], List[str]]:
    """The nested subset a wiki page needs: block mappings and sequences by indentation, a sequence
    item that opens a mapping (`- rev: …` + deeper `seal: …` lines), inline lists and inline
    mappings, scalars by the shared rules. A duplicate key is an error (the first value is kept);
    a line that is neither `key: value` nor `- item` is an error."""
    errors: List[str] = []
    lines: List[Tuple[int, str, int]] = []
    for n, raw in enumerate(block.split("\n")):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        exp = raw.replace("\t", "  ")
        lines.append((len(exp) - len(exp.lstrip(" ")), exp.strip(), n + 2))
    pos = [0]

    def value_of(rest: str, indent: int, where: str, ln: int) -> object:
        r = rest.strip()
        if r.startswith("#"):
            r = ""
        if not r:
            if pos[0] < len(lines):
                nind, ntext, _ = lines[pos[0]]
                if nind > indent:
                    return node(nind)
                if nind == indent and (ntext == "-" or ntext.startswith("- ")):
                    return sequence(indent)
            return ""
        if r.startswith("["):
            end = r.rfind("]")
            if end > 0 and (end == len(r) - 1 or re.match(r"^\s+#", r[end + 1:])):
                inner = r[1:end]
                if re.search(r"[\[\]{}]", re.sub(r"\"[^\"]*\"|'[^']*'", "", inner)):
                    errors.append("line %d: nested structure inside the inline list of `%s`" % (ln, where))
                    return ""
                return inline_list(inner)
            return scalar(r)
        if r.startswith("{"):
            end = r.rfind("}")
            if end > 0 and (end == len(r) - 1 or re.match(r"^\s+#", r[end + 1:])):
                return _inline_map(r[1:end], "line %d: `%s`" % (ln, where), errors)
            return scalar(r)
        return scalar(r)

    def mapping(indent: int) -> Dict[str, object]:
        out: Dict[str, object] = {}
        while pos[0] < len(lines):
            ind, text, ln = lines[pos[0]]
            if ind < indent:
                break
            if ind > indent:
                errors.append("line %d: unexpected indented line: %s" % (ln, text[:60]))
                pos[0] += 1
                continue
            if text == "-" or text.startswith("- "):
                break
            m = TREE_KEY_RE.match(text)
            if not m:
                errors.append("line %d: not a `key: value` line: %s" % (ln, text[:60]))
                pos[0] += 1
                continue
            key = scalar(m.group(1)) if m.group(1)[0] in ('"', "'") else m.group(1)
            pos[0] += 1
            value = value_of(m.group(2) or "", indent, key, ln)
            if key in out:
                errors.append("duplicate key `%s`" % key)
            else:
                out[key] = value
        return out

    def sequence(indent: int) -> List[object]:
        out: List[object] = []
        while pos[0] < len(lines):
            ind, text, ln = lines[pos[0]]
            if ind < indent or not (text == "-" or text.startswith("- ")):
                break
            if ind > indent:
                errors.append("line %d: unexpected indented list item: %s" % (ln, text[:60]))
                pos[0] += 1
                continue
            rest = "" if text == "-" else text[2:].strip()
            if rest and TREE_KEY_RE.match(rest) and not rest[0] in ('"', "'", "[", "{"):
                # `- key: value`: a mapping whose keys stand at the column after the marker
                lines[pos[0]] = (indent + 2, rest, ln)
                out.append(mapping(indent + 2))
                continue
            pos[0] += 1
            out.append(value_of(rest, indent, "item", ln))
        return out

    def node(indent: int) -> object:
        _ind, text, _ln = lines[pos[0]]
        if text == "-" or text.startswith("- "):
            return sequence(indent)
        return mapping(indent)

    data = mapping(lines[0][0]) if lines else {}
    while pos[0] < len(lines):
        ind, text, ln = lines[pos[0]]
        errors.append("line %d: unexpected line: %s" % (ln, text[:60]))
        pos[0] += 1
    return data, errors


# ---------------------------------------------------------------------------------------------
# wiki/1 — the reader
# ---------------------------------------------------------------------------------------------

def collection_ref(raw: object) -> Tuple[Optional[str], Optional[str]]:
    """(name, reason) of a `collections` entry: `[[Collections/<name>]]` (the brackets, `.md` and
    `|display` optional) → the file stem; anything else is the reason it is not a collection."""
    if not isinstance(raw, str):
        return None, "collections entry must be a scalar"
    t = raw.strip()
    if t.startswith("[[") and t.endswith("]]"):
        t = t[2:-2]
    if "|" in t:
        t = t.split("|", 1)[0]
    t = t.strip()
    if "#" in t:
        t = t.split("#", 1)[0].strip()
    if not t:
        return None, "empty collections entry"
    if "\\" in t or t.startswith("/") or any(seg in ("..", ".") for seg in t.split("/")):
        return None, "collections entry outside the instance: %s" % raw
    if not t.startswith(COLLECTIONS_DIR + "/"):
        return None, "collections entry must be [[Collections/<name>]] (got %s)" % raw
    name = t[len(COLLECTIONS_DIR) + 1:]
    if name.endswith(".md"):
        name = name[:-3]
    if not name or "/" in name:
        return None, "collections entry names no collection: %s" % raw
    return name, None


def rev_ref(raw: object) -> Tuple[Optional[dict], Optional[str]]:
    """(link, reason) of a `based_on.rev`: the wikilink forms of classify_link, brackets optional."""
    if not isinstance(raw, str):
        return None, "rev must be a scalar"
    t = raw.strip()
    if t.startswith("![["):
        t = t[1:]
    if t.startswith("[[") and t.endswith("]]"):
        t = t[2:-2]
    return classify_link(t)


def heading_text(line: str) -> Optional[str]:
    """The text of an ATX heading line (any level), spaces normalized; None for other lines."""
    m = HEADING_RE.match(line)
    return " ".join(m.group(2).split()) if m else None


def evidential_body(body: str, offset: int) -> dict:
    """The evidential body of a page (interface §9): the lines from the first heading after the
    frontmatter to the first `## Atlas reading` / `## Log` heading (excluded), outside fences,
    line endings LF, trailing spaces stripped, trailing blank lines dropped, one final LF.
    Returns {text, sha256, words, start, end, cut, lines: [(ln, line, fenced)…]} — `start`/`end` are
    1-based line numbers (end exclusive, the cut heading's line or one past the last)."""
    raw_lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start: Optional[int] = None
    end: Optional[int] = None
    cut: Optional[str] = None
    fence = None
    kept: List[Tuple[int, str, bool]] = []
    for i, line in enumerate(raw_lines):
        ln = offset + i
        stripped = line.lstrip()
        if fence is None:
            fo = FENCE_OPEN_RE.match(stripped)
            if fo and start is not None:
                fence = (fo.group(1)[0], len(fo.group(1)))
                kept.append((ln, line, True))
                continue
        else:
            fc = FENCE_CLOSE_RE.match(stripped)
            kept.append((ln, line, True))
            if fc and fc.group(1)[0] == fence[0] and len(fc.group(1)) >= fence[1]:
                fence = None
            continue
        m = HEADING_RE.match(line)
        if m:
            name = " ".join(m.group(2).split())
            if start is None:
                start = ln
            elif len(m.group(1)) == 2 and name in WIKI_CONSUMABLE_SECTIONS:
                end = ln
                cut = name
                break
        if start is not None:
            kept.append((ln, line, False))
    if start is None:
        return {"text": "", "sha256": None, "words": 0, "start": None, "end": None, "cut": None, "lines": []}
    if end is None:
        end = offset + len(raw_lines)
    text_lines = [line.rstrip(" \t") for _ln, line, _f in kept]
    while text_lines and not text_lines[-1]:
        text_lines.pop()
    text = "\n".join(text_lines) + "\n"
    import hashlib
    return {"text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "words": len(text.split()),
            "start": start, "end": end, "cut": cut, "lines": kept}


def classify_body_link(raw: str) -> dict:
    """One wikilink of the evidential body: `{"kind": sources|local|outside|unresolved, …}` —
    `sources` carries the parts of classify_link (or `reason` when the Sources target is malformed:
    then `kind` is `outside`), `local` names a `Collections/`, `Wiki/` or `Synthesis/` file."""
    target = raw
    if "|" in target:
        target = target.split("|", 1)[0]
    fragment = None
    if "#" in target:
        target, fragment = target.split("#", 1)
        fragment = fragment.strip() or None
    target = target.strip()
    if not target:
        return {"kind": "unresolved", "target": raw, "reason": "empty link target"}
    if "\\" in target or target.startswith("/") or any(seg in ("..", ".") for seg in target.split("/")):
        return {"kind": "outside", "target": raw, "reason": "link outside the instance: %s" % raw}
    if target.split("/")[0] == "Sources":
        link, reason = classify_link(raw)
        if link is None:
            return {"kind": "outside", "target": raw, "reason": "%s: %s" % (reason, raw)}
        return dict(link, kind="sources", target=raw)
    first = target.split("/")[0]
    if first in (COLLECTIONS_DIR, WIKI_DIR, "Synthesis") and "/" in target:
        path = target[:-3] if target.endswith(".md") else target
        return {"kind": "local", "target": raw, "path": path, "fragment": fragment}
    return {"kind": "unresolved", "target": raw, "reason": "not a link into Sources/, Collections/, Wiki/ or Synthesis/"}


def wiki_links(body_lines: List[Tuple[int, str, bool]]) -> Tuple[List[dict], List[dict]]:
    """(links, locators) of an evidential body: every wikilink outside fences classified, and the
    locators among them — a heading locator `[[Sources/<rev>/source#<heading>]]` and a quote locator
    `> "<fragment>" — [[Sources/<rev>/source]]` on one blockquote line."""
    links: List[dict] = []
    locators: List[dict] = []
    for ln, line, fenced in body_lines:
        if fenced:
            continue
        qm = QUOTE_RE.match(line.strip())
        for m in WIKILINK_RE.finditer(line):
            link = classify_body_link(m.group(1))
            link["line"] = ln
            links.append(link)
            if link["kind"] != "sources":
                continue
            if link["file"] == "source" and link.get("fragment"):
                loc = {"line": ln, "rev": link["rev"], "form": "heading", "heading": " ".join(link["fragment"].split()),
                       "verified": None, "reason": None}
                locators.append(loc)
                link["locator"] = loc
            elif link["file"] == "source" and qm and qm.group(2) == m.group(1):
                loc = {"line": ln, "rev": link["rev"], "form": "quote", "quote": qm.group(1), "verified": None, "reason": None}
                locators.append(loc)
                link["locator"] = loc
    return links, locators


def _wiki_record(name: str, file: Optional[str]) -> dict:
    return {
        "schema": WIKI_SCHEMA, "name": name, "file": file or "%s/%s.md" % (WIKI_DIR, name),
        "state": "ok", "errors": [],
        "kind": None, "collections": [], "domains": [], "maintained_by": None, "created": None, "updated": None,
        "visibility": None, "body_sha256": None,
        "based_on": [], "inputs": None, "extra": {},
        "sections": [], "body": {"sha256": None, "words": 0, "start": None, "end": None, "cut": None},
        "links": [], "locators": [],
        "counts": {"based_on": 0, "collections": 0, "links": 0, "locators": 0, "links_consumable": 0},
        "states": [], "reasons": {}, "not_probed": [], "severity": None, "usable": None, "cited": [],
        "_body_lines": [],
    }


def read_wiki_text(text: str, name: str, file: Optional[str] = None) -> dict:
    """The wiki/1 record of one page: the stored keys and the three prints, the evidential body and
    its hash, the links and locators of the body — nothing resolved (validate_wiki does that)."""
    rec = _wiki_record(name, file)
    errors = rec["errors"]
    if text.startswith("﻿"):
        text = text[1:]
    block, body, offset = split_frontmatter(text)
    if block is None:
        errors.append("no frontmatter (an opening `---` line closed by `---`)")
        data: Dict[str, object] = {}
    else:
        data, ferr = parse_frontmatter_tree(block)
        errors.extend(ferr)
    for key in WIKI_REQUIRED:
        if key not in data or data[key] == "":
            errors.append("missing required `%s`" % key)
    t = data.get("type")
    if t is not None and t != "wiki":
        errors.append("`type` must be `wiki` (got %r)" % (t,))
    kind = data.get("kind")
    if isinstance(kind, str) and kind:
        if kind not in WIKI_KINDS:
            errors.append("`kind` must be one of %s (got %r)" % ("|".join(WIKI_KINDS), kind))
        else:
            rec["kind"] = kind
    elif kind not in (None, ""):
        errors.append("`kind` must be a scalar")
    for key in ("maintained_by", "visibility"):
        v = data.get(key)
        if v is not None and not isinstance(v, str):
            errors.append("`%s` must be a scalar" % key)
        else:
            rec[key] = _str_or_none(v)
    for key in ("created", "updated"):
        v = data.get(key)
        if v in (None, ""):
            continue
        if not _is_date(v):
            errors.append("`%s` is not an ISO date (YYYY-MM-DD)" % key)
        else:
            rec[key] = v.strip()
    v = data.get("body_sha256")
    if isinstance(v, str) and v:
        if not SHA256_RE.match(v.strip().lower()):
            errors.append("`body_sha256` is not a sha256 hex digest")
        else:
            rec["body_sha256"] = v.strip().lower()
    elif v not in (None, ""):
        errors.append("`body_sha256` must be a scalar")
    # collections: the supporting dossiers
    cols = data.get("collections")
    if cols is not None:
        lst = _list_or_none(cols)
        if lst is None:
            errors.append("`collections` must be a list")
        else:
            seen: set = set()
            for raw in lst:
                cname, reason = collection_ref(raw)
                if cname is None:
                    errors.append(reason or "bad collections entry")
                elif cname in seen:
                    errors.append("duplicate collection `%s` in `collections`" % cname)
                else:
                    seen.add(cname)
                    rec["collections"].append(cname)
    dom = data.get("domains")
    if dom is not None:
        lst = _list(dom)
        if lst is None:
            errors.append("`domains` must be a list")
        else:
            rec["domains"] = lst
    # based_on: the print of the revisions
    bo = data.get("based_on")
    if bo is not None and bo != "":
        if not isinstance(bo, list):
            errors.append("`based_on` must be a list of {rev, text_sha256, seal} entries")
        else:
            seen_revs: Dict[str, int] = {}
            for i, entry in enumerate(bo):
                where = "based_on[%d]" % i
                if not isinstance(entry, dict):
                    errors.append("%s: not a {rev, text_sha256, seal} entry" % where)
                    continue
                unknown = sorted(k for k in entry if k not in ("rev", "text_sha256", "seal"))
                if unknown:
                    errors.append("%s: unknown key %s" % (where, ", ".join("`%s`" % k for k in unknown)))
                link, reason = rev_ref(entry.get("rev"))
                if link is None:
                    errors.append("%s: rev: %s" % (where, reason or "missing"))
                    continue
                sha = entry.get("text_sha256")
                if not isinstance(sha, str) or not SHA256_RE.match(sha.strip().lower()):
                    errors.append("%s: `text_sha256` is not a sha256 hex digest" % where)
                    sha = None
                else:
                    sha = sha.strip().lower()
                seal = entry.get("seal")
                if not isinstance(seal, str) or seal.strip().lower() not in WIKI_SEALS:
                    errors.append("%s: `seal` must be capture|observed (got %r)" % (where, seal))
                    seal = None
                else:
                    seal = seal.strip().lower()
                if link["rev"] in seen_revs:
                    errors.append("%s: duplicate revision %s (first at based_on[%d])" % (where, link["rev"], seen_revs[link["rev"]]))
                    continue
                seen_revs[link["rev"]] = i
                rec["based_on"].append({"rev": link["rev"], "file": link["file"], "text_sha256": sha, "seal": seal})
    # inputs: the print of the build package
    inp = data.get("inputs")
    if inp is not None and inp != "":
        if not isinstance(inp, dict):
            errors.append("`inputs` must be a mapping")
        else:
            unknown = sorted(k for k in inp if k not in WIKI_INPUT_KEYS)
            if unknown:
                errors.append("inputs: unknown key %s" % ", ".join("`%s`" % k for k in unknown))
            for key in WIKI_INPUT_REQUIRED:
                if key not in inp or inp[key] == "":
                    if key != "collections":
                        errors.append("inputs: missing `%s`" % key)
            out: Dict[str, object] = {}
            for key in ("page", "kind", "charter_sha256", "protocol", "template"):
                v = inp.get(key)
                if v is not None and not isinstance(v, str):
                    errors.append("inputs: `%s` must be a scalar" % key)
                    out[key] = None
                else:
                    out[key] = _str_or_none(v)
            if out["kind"] is not None and out["kind"] not in WIKI_KINDS:
                errors.append("inputs: `kind` must be one of %s (got %r)" % ("|".join(WIKI_KINDS), out["kind"]))
            if out["page"] is not None and out["page"] != name:
                errors.append("inputs: `page` %r is not this page's file name %r" % (out["page"], name))
            if out["charter_sha256"] is not None and not SHA256_RE.match(out["charter_sha256"].lower()):
                errors.append("inputs: `charter_sha256` is not a sha256 hex digest")
            ic = inp.get("collections")
            cmap: Dict[str, Optional[str]] = {}
            if ic is None or ic == "" or ic == []:
                if "collections" not in inp:
                    errors.append("inputs: missing `collections`")
            elif not isinstance(ic, dict):
                errors.append("inputs: `collections` must be a mapping {\"Collections/<name>\": \"<sha256>\"}")
            else:
                for k, v in ic.items():
                    cname, reason = collection_ref(k)
                    if cname is None:
                        errors.append("inputs.collections: %s" % reason)
                        continue
                    if not isinstance(v, str) or not SHA256_RE.match(v.strip().lower()):
                        errors.append("inputs.collections: `%s` is not a sha256 hex digest" % cname)
                        cmap[cname] = None
                    else:
                        cmap[cname] = v.strip().lower()
            out["collections"] = cmap
            rec["inputs"] = out
    known = set(WIKI_SCALAR_KEYS) | set(WIKI_LIST_KEYS) | set(WIKI_PRINT_KEYS)
    rec["extra"] = {k: v for k, v in data.items() if k not in known}

    sections, names_ = _sections(body, offset)
    rec["sections"] = names_
    eb = evidential_body(body, offset)
    rec["body"] = {k: eb[k] for k in ("sha256", "words", "start", "end", "cut")}
    if eb["start"] is None:
        errors.append("no evidential body (no heading after the frontmatter)")
    rec["_body_lines"] = eb["lines"]
    links, locators = wiki_links(eb["lines"])
    rec["links"], rec["locators"] = links, locators
    consumable = 0
    for s in sections:
        if s["name"] in WIKI_CONSUMABLE_SECTIONS:
            for _ln, line, fenced in s["lines"]:
                if not fenced:
                    consumable += len(WIKILINK_RE.findall(line))
    rec["counts"] = {"based_on": len(rec["based_on"]), "collections": len(rec["collections"]), "links": len(links),
                     "locators": len(locators), "links_consumable": consumable}
    if errors:
        rec["state"] = "fail"
    return rec


def _list_or_none(v: object) -> Optional[list]:
    if isinstance(v, list):
        return v
    if v == "" or v is None:
        return []
    return None


def unreadable_wiki_record(name: str, reason: str, file: Optional[str] = None) -> dict:
    rec = _wiki_record(name, file)
    rec.update({"state": "unreadable", "errors": [reason]})
    return rec


def read_wiki(path: str, name: Optional[str] = None, file: Optional[str] = None) -> dict:
    stem = name or os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return unreadable_wiki_record(stem, "unreadable: %s" % exc, file=file)
    return read_wiki_text(data.decode("utf-8", "replace"), stem, file=file)


def list_wiki(vault: str) -> List[str]:
    """The `Wiki/*.md` stems, sorted; [] without the folder; OSError when the folder exists but
    cannot be listed (never an empty one)."""
    d = os.path.join(vault, WIKI_DIR)
    if not os.path.isdir(d):
        return []
    return sorted(n[:-3] for n in os.listdir(d)
                  if n.endswith(".md") and not n.startswith(".") and os.path.isfile(os.path.join(d, n)))


def scan_wiki(vault: str) -> List[dict]:
    d = os.path.join(vault, WIKI_DIR)
    return [read_wiki(os.path.join(d, n + ".md"), name=n) for n in list_wiki(vault)]


# ---------------------------------------------------------------------------------------------
# wiki/1 — the validator: the veto predicate and the derived states (interface §9)
# ---------------------------------------------------------------------------------------------

def is_suppressed(page_name: str, page_collections: List[str], collections: Dict[str, dict]) -> List[str]:
    """The one veto predicate: the names of the collections that veto the page — ∃ C ∈
    page.collections with C.status = open and (C.wiki = off or page ∈ C.suppressed_pages).
    `inbox` and `closed` collections give no veto; a collection that does not exist gives none."""
    vetoes: List[str] = []
    for cname in page_collections:
        c = collections.get(cname)
        if c is None or c.get("status") != "open":
            continue
        if c.get("wiki") == "off" or page_name in (c.get("suppressed_pages") or []):
            vetoes.append(cname)
    return vetoes


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def source_headings(text: str) -> List[str]:
    """The heading texts of a source.md body (any level, outside fences), spaces normalized."""
    out: List[str] = []
    fence = None
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.lstrip()
        if fence is None:
            fo = FENCE_OPEN_RE.match(stripped)
            if fo:
                fence = (fo.group(1)[0], len(fo.group(1)))
                continue
        else:
            fc = FENCE_CLOSE_RE.match(stripped)
            if fc and fc.group(1)[0] == fence[0] and len(fc.group(1)) >= fence[1]:
                fence = None
            continue
        h = heading_text(line)
        if h is not None:
            out.append(h)
    return out


def validate_wiki(rec: dict, ctx: dict) -> dict:
    """The derived states of one page, never stored (interface §9): fills `states` (in priority
    order), `reasons` (state → the named reasons), `not_probed` (what could not be checked),
    `severity` (fail · warn · ok · None when not probed or the schema failed), `usable` (false when
    search and research-ask must not rest on it) and `cited` (the revisions its verified locators
    cite). `ctx` is the research context of the vault (lib_vault.research_context): `revisions`
    (rev → has_source · klass · capture_sha256 · body_sha256 · completeness · superseded ·
    source_type · unreadable), `graph_complete`, `source_text(rev)`, `collections` (name → record),
    `charter_sha256`, `local_exists(path)`, `budget_words`."""
    reasons: Dict[str, List[str]] = {}
    not_probed: List[str] = []

    def add(state: str, reason: str) -> None:
        reasons.setdefault(state, []).append(reason)

    rec["cited"] = []
    if rec["state"] == "unreadable":
        rec.update({"states": [], "reasons": {}, "not_probed": ["unreadable"], "severity": None, "usable": False})
        return rec
    if rec["state"] == "fail":
        rec.update({"states": ["schema"], "reasons": {"schema": list(rec["errors"])}, "not_probed": [], "severity": "fail",
                    "usable": False})
        return rec
    collections = ctx["collections"]
    revisions = ctx["revisions"]

    vetoes = is_suppressed(rec["name"], rec["collections"], collections)
    for cname in vetoes:
        c = collections[cname]
        add("suppressed", "vetoed by open collection `%s` (%s)" % (
            cname, "wiki: off" if c.get("wiki") == "off" else "named in suppressed_pages"))

    supporting = [c for c in rec["collections"] if c in collections and collections[c].get("status") == "open"]
    if not supporting:
        if not rec["collections"]:
            add("orphan", "no collections listed")
        for cname in rec["collections"]:
            c = collections.get(cname)
            if c is None:
                add("orphan", "`%s` does not exist" % cname)
            else:
                add("orphan", "`%s` is %s" % (cname, c.get("status") or "without a valid status"))

    inp = rec["inputs"] or {}
    if inp.get("kind") and rec["kind"] and inp["kind"] != rec["kind"]:
        add("unbound", "kind `%s` ≠ inputs.kind `%s`" % (rec["kind"], inp["kind"]))
    for cname in sorted(inp.get("collections") or {}):
        if cname not in rec["collections"]:
            add("unbound", "inputs.collections names `%s`, which is not in `collections`" % cname)
    for cname, sha in sorted((inp.get("collections") or {}).items()):
        c = collections.get(cname)
        if c is None:
            add("inputs-changed", "collection `%s` is gone" % cname)
        elif c.get("state") == "unreadable" or not c.get("sha256"):
            not_probed.append("collection `%s` could not be read" % cname)
        elif sha and c["sha256"] != sha:
            add("inputs-changed", "collection `%s` changed (%s… ≠ printed %s…)" % (cname, c["sha256"][:12], sha[:12]))
    if inp:
        charter_now = ctx.get("charter_sha256")
        printed = inp.get("charter_sha256")
        if (printed or None) != (charter_now or None):
            add("inputs-changed", "charter changed (%s ≠ printed %s)" % (
                charter_now[:12] + "…" if charter_now else "none", printed[:12] + "…" if printed else "none"))
        if inp.get("protocol") and inp["protocol"] != RESEARCH_PROTOCOL:
            add("inputs-changed", "protocol `%s` ≠ engine `%s`" % (inp["protocol"], RESEARCH_PROTOCOL))
        expected_template = "wiki/%s@%d" % (rec["kind"], WIKI_TEMPLATE_VERSION)
        if inp.get("template") and inp["template"] != expected_template:
            add("inputs-changed", "template `%s` ≠ engine `%s`" % (inp["template"], expected_template))

    if rec["body_sha256"] and rec["body"]["sha256"] and rec["body"]["sha256"] != rec["body_sha256"]:
        add("edited-outside-update", "evidential body sha256 %s… ≠ printed %s…" % (rec["body"]["sha256"][:12], rec["body_sha256"][:12]))

    listed = {e["rev"] for e in rec["based_on"]}
    for e in rec["based_on"]:
        r = revisions.get(e["rev"])
        if r is None or not r.get("has_source"):
            add("unbound", "based_on names %s, which has no source.md" % e["rev"])
            continue
        if r.get("unreadable"):
            not_probed.append("source.md of %s could not be read" % e["rev"])
            continue
        if e["seal"] == "capture":
            if r.get("klass") != "managed" or not r.get("capture_sha256"):
                add("unbound", "seal capture on %s, which carries no capture.text_sha256 (%s revision)" % (e["rev"], r.get("klass") or "unknown"))
            elif r["capture_sha256"] != e["text_sha256"]:
                add("unbound", "seal capture on %s: printed %s… ≠ capture.text_sha256 %s…" % (e["rev"], (e["text_sha256"] or "")[:12], r["capture_sha256"][:12]))
            elif r.get("body_sha256") != e["text_sha256"]:
                add("unbound", "seal capture on %s: the body %s… ≠ capture.text_sha256 (integrity error)" % (e["rev"], (r.get("body_sha256") or "none")[:12]))
                add("weak-evidence", "%s: integrity error" % e["rev"])
        else:
            if r.get("klass") == "managed":
                add("unbound", "seal observed on the managed revision %s (seal capture required)" % e["rev"])
            elif r.get("body_sha256") != e["text_sha256"]:
                add("unbound", "seal observed on %s: printed %s… ≠ body %s…" % (e["rev"], (e["text_sha256"] or "")[:12], (r.get("body_sha256") or "none")[:12]))
                add("inputs-changed", "text of %s changed" % e["rev"])
        if r.get("superseded"):
            if ctx.get("graph_complete", True):
                add("stale", "%s is superseded" % e["rev"])
            else:
                not_probed.append("stale: the supersedes graph is incomplete (%s)" % e["rev"])
        comp = r.get("completeness")
        if comp in ("partial", "blocked"):
            add("weak-evidence", "%s: completeness %s" % (e["rev"], comp))
        elif comp == "unknown" and (r.get("source_type") or "") in ("pdf", "video", "podcast", "audio"):
            reasons.setdefault("info", []).append("%s: completeness unknown (%s)" % (e["rev"], r["source_type"]))

    cited: set = set()
    headings_cache: Dict[str, Optional[List[str]]] = {}
    bodies_cache: Dict[str, Optional[str]] = {}

    def body_of(rev: str) -> Optional[str]:
        if rev not in bodies_cache:
            bodies_cache[rev] = ctx["source_text"](rev)
        return bodies_cache[rev]

    for link in rec["links"]:
        if link["kind"] == "outside":
            add("unbound", "line %d: %s" % (link["line"], link["reason"]))
        elif link["kind"] == "local":
            if not ctx["local_exists"](link["path"]):
                add("unresolved", "line %d: [[%s]] does not exist" % (link["line"], link["target"]))
        elif link["kind"] == "unresolved":
            add("unresolved", "line %d: [[%s]] (%s)" % (link["line"], link["target"], link["reason"]))
        elif link["kind"] == "sources":
            rev = link["rev"]
            r = revisions.get(rev)
            if r is None or (link["file"] == "source" and not r.get("has_source")) or (
                    link["file"] == "extract" and not r.get("has_extract")) or (link["file"] is None and not r.get("has_source")):
                add("unbound", "line %d: [[%s]] does not exist" % (link["line"], link["target"]))
                continue
            if rev not in listed:
                add("unbound", "line %d: unlisted — [[%s]] names a revision outside based_on" % (link["line"], link["target"]))
                continue
            loc = link.get("locator")
            if loc is None:
                continue
            if r.get("unreadable"):
                not_probed.append("line %d: source.md of %s could not be read" % (loc["line"], rev))
                continue
            text = body_of(rev)
            if text is None:
                not_probed.append("line %d: source.md of %s could not be read" % (loc["line"], rev))
                continue
            if loc["form"] == "heading":
                if rev not in headings_cache:
                    headings_cache[rev] = source_headings(text)
                n = sum(1 for h in headings_cache[rev] if h == loc["heading"])
                if n == 1:
                    loc["verified"] = True
                    cited.add(rev)
                else:
                    loc["verified"] = False
                    loc["reason"] = "heading `%s` %s in %s/source.md" % (loc["heading"], "not found" if n == 0 else "found %d times" % n, rev)
                    add("unbound", "line %d: %s" % (loc["line"], loc["reason"]))
            else:
                q = _norm_ws(loc["quote"])
                if not q:
                    loc["verified"] = False
                    loc["reason"] = "empty quote"
                    add("unbound", "line %d: empty quote for %s" % (loc["line"], rev))
                elif q in _norm_ws(text):
                    loc["verified"] = True
                    cited.add(rev)
                else:
                    loc["verified"] = False
                    loc["reason"] = "quote not found in %s/source.md" % rev
                    add("unbound", "line %d: %s" % (loc["line"], loc["reason"]))

    budget_words = ctx.get("budget_words")
    if budget_words is not None and rec["body"]["words"] > budget_words:
        add("budget", "%d words in the evidential body (budget %d)" % (rec["body"]["words"], budget_words))

    states = [s for s in WIKI_STATE_ORDER if s in reasons]
    rec["states"] = states
    rec["reasons"] = reasons
    rec["not_probed"] = not_probed
    rec["cited"] = sorted(cited)
    if not_probed and not any(s in WIKI_FAIL_STATES for s in states):
        rec["severity"] = None
    elif any(s in WIKI_FAIL_STATES for s in states):
        rec["severity"] = "fail"
    elif states or reasons.get("unresolved"):
        rec["severity"] = "warn"
    else:
        rec["severity"] = "ok"
    rec["usable"] = rec["severity"] in ("ok", "warn")
    return rec


def wiki_summary_line(rec: dict) -> str:
    c = rec["counts"]
    parts = ["%d based_on" % c["based_on"], "%d collections" % c["collections"], "%d locators" % c["locators"]]
    head = "%s %s — %s · %s" % (rec["state"], rec["name"], rec["kind"] or "?", " · ".join(parts))
    if rec.get("states"):
        head += " · " + ", ".join(rec["states"])
    if rec.get("not_probed"):
        head += " · not probed"
    line = head
    for e in rec["errors"]:
        line += "\n  ! %s" % e
    for state in rec.get("states") or []:
        if state == "schema":
            continue            # the errors above are the reasons
        for reason in rec["reasons"].get(state, []):
            line += "\n  %s %s: %s" % ("!" if state in WIKI_FAIL_STATES else "·", state, reason)
    for reason in rec["reasons"].get("unresolved", []) if rec.get("reasons") else []:
        line += "\n  · unresolved: %s" % reason
    for reason in rec.get("not_probed") or []:
        line += "\n  ? not probed: %s" % reason
    return line


def summary_line(rec: dict) -> str:
    c = rec["counts"]
    parts = ["%d items" % c["items"]]
    if c["excluded"]:
        parts.append("%d excluded" % c["excluded"])
    if c["unresolved"]:
        parts.append("%d unresolved" % c["unresolved"])
    if c["malformed"]:
        parts.append("%d malformed" % c["malformed"])
    line = "%s %s — %s · %s" % (rec["state"], rec["name"], rec["status"] or "?", " · ".join(parts))
    for e in rec["errors"]:
        line += "\n  ! %s" % e
    for m in rec["malformed"]:
        line += "\n  ! line %d: %s: %s" % (m["line"], m["reason"], m["text"])
    return line


def public_wiki_record(rec: dict) -> dict:
    """The record without its working copy of the body lines."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def _sniff_type(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536).decode("utf-8", "replace")
    except OSError:
        return None
    if head.startswith("﻿"):
        head = head[1:]
    block, _b, _o = split_frontmatter(head)
    if block is None:
        return None
    data, _errs = parse_flat_frontmatter(block)
    t = data.get("type")
    return t if isinstance(t, str) else None


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Read and validate research documents (collection/1, wiki/1)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--vault", help="validate every Collections/*.md and Wiki/*.md of this vault")
    g.add_argument("--file", help="validate one document (a collection, or a wiki page by its `type: wiki` — no states)")
    ap.add_argument("--json", action="store_true", help="the reader records as JSON")
    args = ap.parse_args(argv[1:])
    pages: List[dict] = []
    if args.vault:
        vault = os.path.abspath(os.path.expanduser(args.vault))
        try:
            records = scan_collections(vault, existing=existing_revisions(vault))
        except OSError as exc:
            print("lib_research: %s/ could not be listed (%s): nothing validated" % (COLLECTIONS_DIR, exc), file=sys.stderr)
            return 1
        try:
            list_wiki(vault)
        except OSError as exc:
            print("lib_research: %s/ could not be listed (%s): nothing validated" % (WIKI_DIR, exc), file=sys.stderr)
            return 1
        if os.path.isdir(os.path.join(vault, WIKI_DIR)):
            # the states need the vault's snapshot (revisions, the graph, the collections): the one
            # scanner builds it — loaded here, not at import (lib_vault imports this module)
            import importlib.util
            spec = importlib.util.spec_from_file_location("lib_vault", os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib_vault.py"))
            lv = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(lv)
            scan = lv.scan_vault(vault)
            ctx = lv.research_context(scan)
            pages = [validate_wiki(p, ctx) for p in scan["wiki"]["notes"]]
    else:
        path = os.path.abspath(os.path.expanduser(args.file))
        if _sniff_type(path) == "wiki":
            records = []
            pages = [read_wiki(path, file=os.path.basename(path))]
        else:
            records = [read_collection(path, file=os.path.basename(path))]
    if args.json:
        print(json.dumps({"schema": INTERFACE, "collections": records, "wiki": [public_wiki_record(p) for p in pages]},
                         ensure_ascii=False, indent=1))
    else:
        for rec in records:
            print(summary_line(rec))
        if records or not pages:
            print("collections: %d · fail %d · unreadable %d" % (
                len(records), sum(1 for r in records if r["state"] == "fail"),
                sum(1 for r in records if r["state"] == "unreadable")))
        for p in pages:
            print(wiki_summary_line(p))
        if pages:
            print("wiki: %d pages · fail %d · warn %d · not probed %d" % (
                len(pages), sum(1 for p in pages if p["severity"] == "fail" or p["state"] != "ok"),
                sum(1 for p in pages if p["severity"] == "warn"),
                sum(1 for p in pages if p["state"] == "ok" and p["severity"] is None and p.get("not_probed"))))
    bad = any(r["state"] != "ok" for r in records) or any(
        p["state"] != "ok" or p["severity"] == "fail" or (p["severity"] is None and p.get("not_probed")) for p in pages)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
