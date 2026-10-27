#!/usr/bin/env python3
"""lib_research.py — the typed reader and validator of research documents.

docs/specs/2026-10-27-research-interface.md (v1): one module reads `Collections/*.md` for
`reindex`, `search`, the vault scanner (`lib_vault.py`) and the doctor, so every consumer sees
the same items, the same roles and the same failures. Schema `collection/1`: a flat YAML subset
in the frontmatter (scalars and flat lists, no nested mappings, no duplicate keys) and one
representation of the items — the lines of the `## Items` section, by the grammar below. The
`wiki/1` schema is declared here and read in phase 3b.

    python3 lib_research.py --vault <path> [--json]        every Collections/*.md, exit 1 on a fail
    python3 lib_research.py --file <path> [--json]         one document

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
WIKI_SCHEMA = "wiki/1"            # declared; read in phase 3b
COLLECTIONS_DIR = "Collections"
WIKI_DIR = "Wiki"

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


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Read and validate research documents (collection/1)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--vault", help="validate every Collections/*.md of this vault")
    g.add_argument("--file", help="validate one document")
    ap.add_argument("--json", action="store_true", help="the reader records as JSON")
    args = ap.parse_args(argv[1:])
    if args.vault:
        vault = os.path.abspath(os.path.expanduser(args.vault))
        try:
            records = scan_collections(vault, existing=existing_revisions(vault))
        except OSError as exc:
            print("lib_research: %s/ could not be listed (%s): nothing validated" % (COLLECTIONS_DIR, exc), file=sys.stderr)
            return 1
    else:
        path = os.path.abspath(os.path.expanduser(args.file))
        records = [read_collection(path, file=os.path.basename(path))]
    if args.json:
        print(json.dumps({"schema": INTERFACE, "collections": records}, ensure_ascii=False, indent=1))
    else:
        for rec in records:
            print(summary_line(rec))
        print("collections: %d · fail %d · unreadable %d" % (
            len(records), sum(1 for r in records if r["state"] == "fail"),
            sum(1 for r in records if r["state"] == "unreadable")))
    return 1 if any(r["state"] != "ok" for r in records) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
