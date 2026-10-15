#!/usr/bin/env python3
"""extract-publish.py — validate the extractor's structured output and publish extract.md
(docs/specs/2026-10-15-capture-contracts.md §4.3–4.4). The publisher is the only writer of
extract.md frontmatter; the model's strings never reach a file unvalidated.

    extract-publish.py schema
        print the `extract/1` JSON schema (what the gateway passes to `claude --json-schema`)

    extract-publish.py publish --result <result.json> --template <template.md> --meta <meta.json> \
                               --out <extract.md> [--notes <notes.json>]
        validate `structured_output` of the claude result document against the schema (types and
        bounds), normalize, re-validate the bounds, render extract.md exactly as §4.4, write it
        atomically (`.extract.md.tmp` + fsync + rename) and print its sha256.
        On failure: print one `<field>: <rule>` line per problem on stdout — gateway-authored
        text, never the model's — and exit 1 (the gateway re-runs the extractor once with these
        lines appended as `=== VALIDATION ===`, then fails `invalid-output`).
        Elements that cannot be made to fit (tags, people, domains) are dropped and listed in
        --notes (a JSON array of strings); the bounds are re-checked afterwards.

    extract-publish.py normalize [--max N] [--] <value> | -
        the §3.3 normalizer for metadata that does not come from the model (--title, an
        acquired <title>, pdfinfo, media.json): control characters removed, whitespace collapsed
        to single spaces, trimmed, truncated to N characters (default 300). Never rejects.
        `-` reads the value from stdin.

    extract-publish.py quote <value>
        print the value as a double-quoted YAML scalar of the serialization subset
        (`"` → `\\"`, `\\` → `\\\\`, control characters removed) — used for source.md scalars.

    extract-publish.py body-headings --template <template.md>
        print the `### ` headings the template's Body section declares (one per line).

    extract-publish.py schema --part
        print the `extract-part/1` schema of a chunked extraction (capture layer §3.8, D13).

    extract-publish.py split --in <capture.md> --max-bytes N --max-parts M --out-dir <dir>
        byte-preserving split of an oversized capture-text: the parts concatenate to the input;
        cuts only at line boundaries outside fenced blocks (``` and ~~~ tracked), preferring
        `^#{1,3} ` lines, else blank lines, packed greedily; writes part-<n>.md and parts.json
        ([{n, bytes, first_line}]); exit 1 `unsupported-size` when one unsplittable span exceeds
        N or the count exceeds M.

    extract-publish.py validate-part --result <result.json> --out <part.json>
        validate a part call's `structured_output` against extract-part/1 with the same
        normalization and serialization rules as `publish` (scalar limits, control characters,
        the list-element rules; each key_ideas[].domain must match the extract/1 domain regex),
        write the validated object and print its sha256; on failure print the diagnostics and
        exit 1.

    extract-publish.py render-parts <part.json>…
        render validated part extracts for the merge prompt: `## Part n — <title>`, the summary,
        the claims as `- claim #domain`, the quotes as `> …`.

    `publish --meta` also reads `extraction` (single | chunked) and `parts` (N) and writes them
    into the frontmatter.

The serialization subset (shared by source.md, extract.md and RESULT:): scalars are single-line
(no control characters, no leading/trailing whitespace), written double-quoted with exactly two
escapes; list elements are unquoted flow-list items that are safe unquoted (no `, [ ] { } " ' #
: & * ! | > % @ \\``, start with a letter — or a digit for tags/domains — are not a YAML reserved
word and do not parse as a number); `tags`/`domains` match ^[a-z0-9][a-z0-9_/-]{0,63}$ after
lowercasing, deduplicated with order kept; `people` ≤ 120 with inner whitespace collapsed;
`language` matches ^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$; `body` must not start with `---`, must
contain every `### ` heading of the template's Body section, and has no NUL. Unknown properties
are dropped. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

SCHEMA_ID = "extract/1"
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "author": {"type": "string", "maxLength": 200},
        "language": {"type": "string", "minLength": 2, "maxLength": 12},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 12},
        "people": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 30},
        "domains": {"type": "array", "items": {"type": "string", "maxLength": 64}, "minItems": 1, "maxItems": 3},
        "rating": {"type": "integer", "minimum": 1, "maximum": 10},
        "pages": {"type": "integer", "minimum": 1},
        "body": {"type": "string", "minLength": 1, "maxLength": 200000},
    },
    "required": ["title", "author", "language", "tags", "people", "domains", "rating", "body"],
}

PART_SCHEMA_ID = "extract-part/1"
PART_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "summary": {"type": "string", "minLength": 50, "maxLength": 1500},
        "key_ideas": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "minLength": 1, "maxLength": 200},
                    "domain": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_/-]*$"},
                },
                "required": ["claim", "domain"],
                "additionalProperties": False,
            },
        },
        "quotes": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 400}},
    },
    "required": ["title", "summary", "key_ideas", "quotes"],
    "additionalProperties": False,
}
PART_TEMPLATE_MARKER = "<!-- mnemon:part-template -->"

LIMITS = {"title": 300, "author": 200, "session": 300, "intent": 2000, "import_source": 200, "import_batch": 200}
LIST_RULES = {"tags": (64, 12, 0), "people": (120, 30, 0), "domains": (64, 3, 1)}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_WS = re.compile(r"\s+")
_FORBIDDEN = set(',[]{}"\'#:&*!|>%@`')
_RESERVED = {"null", "~", "true", "false", "yes", "no", "on", "off"}
_NUMBER = re.compile(r"^(?:[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?|0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+)$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9_/-]{0,63}$")
_LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def normalize(value: str, limit: int) -> str:
    """§3.3: never rejects — control characters out, whitespace collapsed, trimmed, truncated."""
    value = _CTRL.sub(" ", str(value))
    value = _WS.sub(" ", value).strip()
    return value[:limit]


def quote(value: str) -> str:
    value = _CTRL.sub("", str(value))
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plain_or_quoted(value: str) -> str:
    value = str(value)
    return value if _PLAIN.match(value) and value.lower() not in _RESERVED and not _NUMBER.match(value) else quote(value)


def flow_list(items: list) -> str:
    return "[" + ", ".join(items) + "]"


def body_headings(template_text: str) -> list:
    """`### ` headings of the template's `## Body` section, outside fenced blocks."""
    headings = []
    in_body = False
    in_fence = False
    for line in template_text.splitlines():
        stripped = line.rstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^#{1,2} ", stripped) or stripped in ("#",):
            in_body = stripped.strip() == "## Body"
            continue
        if in_body and stripped.startswith("### "):
            headings.append(stripped.strip())
    return headings


# ---------------------------------------------------------------------------
# Validation (§4.3 bounds, §4.4 normalization, bounds again)
# ---------------------------------------------------------------------------

class Rejected(Exception):
    def __init__(self, problems: list):
        super().__init__("; ".join(problems))
        self.problems = problems


def _check_schema(data) -> list:
    problems = []
    if not isinstance(data, dict):
        return ["structured_output: must be a JSON object"]
    props = SCHEMA["properties"]
    for key in SCHEMA["required"]:
        if key not in data:
            problems.append(f"{key}: required")
    for key, rule in props.items():
        if key not in data:
            continue
        val = data[key]
        t = rule["type"]
        if t == "string":
            if not isinstance(val, str):
                problems.append(f"{key}: must be a string")
                continue
            if "minLength" in rule and len(val) < rule["minLength"]:
                problems.append(f"{key}: must not be empty")
            if "maxLength" in rule and len(val) > rule["maxLength"]:
                problems.append(f"{key}: at most {rule['maxLength']} characters")
        elif t == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                problems.append(f"{key}: must be an integer")
                continue
            if "minimum" in rule and val < rule["minimum"]:
                problems.append(f"{key}: at least {rule['minimum']}")
            if "maximum" in rule and val > rule["maximum"]:
                problems.append(f"{key}: at most {rule['maximum']}")
        elif t == "array":
            if not isinstance(val, list):
                problems.append(f"{key}: must be a list of strings")
                continue
            if "maxItems" in rule and len(val) > rule["maxItems"]:
                problems.append(f"{key}: at most {rule['maxItems']} items")
            if "minItems" in rule and len(val) < rule["minItems"]:
                problems.append(f"{key}: at least {rule['minItems']} item(s)")
            for item in val:
                if not isinstance(item, str):
                    problems.append(f"{key}: every item must be a string")
                    break
                if len(item) > rule["items"]["maxLength"]:
                    problems.append(f"{key}: items at most {rule['items']['maxLength']} characters")
                    break
    return problems


def _scalar(value: str, key: str, limit: int, min_len: int, problems: list):
    """Model scalar: trimmed, inner whitespace runs collapsed; control characters reject."""
    v = re.sub(r"[ \t]+", " ", value).strip()
    if _CTRL.search(v):
        problems.append(f"{key}: must be a single line without control characters")
        return None
    if len(v) < min_len:
        problems.append(f"{key}: must not be empty")
        return None
    if len(v) > limit:
        problems.append(f"{key}: at most {limit} characters")
        return None
    return v


def _fit_element(raw: str, key: str, max_len: int):
    """Make an element safe unquoted, or return None when it cannot be."""
    v = _WS.sub(" ", raw).strip()
    if key in ("tags", "domains"):
        v = v.lower()
    if not v or len(v) > max_len:
        return None
    if _CTRL.search(v) or any(ch in _FORBIDDEN for ch in v):
        return None
    first = v[0]
    if not (first.isalpha() or (key in ("tags", "domains") and first.isdigit())):
        return None
    if v.lower() in _RESERVED or _NUMBER.match(v):
        return None
    if key in ("tags", "domains") and not _TAG.match(v):
        return None
    return v


def validate(data: dict, template_text: str):
    """Return (fields, notes); raise Rejected with gateway-authored diagnostics."""
    problems = _check_schema(data)
    if problems:
        raise Rejected(problems)
    notes = []
    out = {}
    out["title"] = _scalar(data["title"], "title", 300, 1, problems)
    out["author"] = _scalar(data["author"], "author", 200, 0, problems)
    lang = data["language"].strip()
    parts = lang.split("-", 1)
    lang = parts[0].lower() + ("-" + parts[1] if len(parts) > 1 else "")
    if not _LANG.match(lang):
        problems.append("language: must match ^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    out["language"] = lang
    for key, (max_len, max_items, min_items) in LIST_RULES.items():
        kept = []
        dropped = 0
        for raw in data[key]:
            fitted = _fit_element(raw, key, max_len)
            if fitted is None:
                dropped += 1
                continue
            if fitted in kept:
                continue
            kept.append(fitted)
        if dropped:
            notes.append(f"{key}: dropped {dropped} element(s) that cannot be written unquoted")
        if len(kept) < min_items:
            problems.append(f"{key}: at least {min_items} valid item(s) (lowercase [a-z0-9][a-z0-9_/-]*)"
                            if key != "people" else f"{key}: at least {min_items} valid item(s)")
        if len(kept) > max_items:
            problems.append(f"{key}: at most {max_items} items")
        out[key] = kept
    out["rating"] = data["rating"]
    if "pages" in data:
        out["pages"] = data["pages"]
    body = data["body"].replace("\r\n", "\n")   # normalized first: a CRLF body is checked as the text that is published
    if "\x00" in body:
        problems.append("body: must not contain NUL")
    if body.lstrip("\n").startswith("---"):
        problems.append("body: must not start with ---")
    if not body.strip():
        problems.append("body: must not be empty")
    missing = [h for h in body_headings(template_text) if not re.search(r"^" + re.escape(h) + r"[ \t]*$", body, re.M)]
    for h in missing:
        problems.append(f"body: must contain the heading {h}")
    out["body"] = body.rstrip() + "\n"
    if problems:
        raise Rejected(problems)
    return out, notes


# ---------------------------------------------------------------------------
# Rendering (§4.4 layout, exactly)
# ---------------------------------------------------------------------------

def render(fields: dict, meta: dict) -> str:
    def m(key, default=""):
        v = meta.get(key)
        return default if v is None else str(v)

    lines = ["---",
             "type: extract",
             f"source_type: {plain_or_quoted(m('source_type', 'article'))}",
             f"content_format: {plain_or_quoted(m('content_format', 'text'))}",
             f"origin: {plain_or_quoted(m('origin', 'url'))}",
             "visibility: personal",
             "status: extracted",
             f"context: {quote(normalize(m('context', 'personal'), 200))}",
             f"title: {quote(fields['title'])}",
             f"author: {quote(fields['author'])}"]
    if m("url"):
        lines.append(f"url: {quote(normalize(m('url'), 2000))}")
    lines += [f"created: {m('created')}",
              f"extracted: {m('extracted')}",
              f"language: {fields['language']}",
              f"tags: {flow_list(fields['tags'])}",
              f"people: {flow_list(fields['people'])}",
              f"domains: {flow_list(fields['domains'])}",
              f"rating: {fields['rating']}"]
    if "pages" in fields:
        lines.append(f"pages: {fields['pages']}")
    extraction = m("extraction", "single")
    lines.append(f"extraction: {plain_or_quoted(extraction)}")
    if extraction == "chunked":
        lines.append(f"parts: {int(meta.get('parts') or 0)}")
    lines += [f"capture_id: {quote(m('capture_id'))}",
              f"revision: {quote(m('revision'))}",
              "capture_context:",
              f"  vault: {plain_or_quoted(m('vault', 'adhoc'))}",
              f"  session: {quote(normalize(m('session'), LIMITS['session']))}",
              f"  intent: {quote(normalize(m('intent'), LIMITS['intent']))}",
              f"  import_source: {quote(normalize(m('import_source', 'gateway'), LIMITS['import_source']))}",
              f"  import_batch: {quote(normalize(m('import_batch'), LIMITS['import_batch']))}",
              "extractor:",
              f"  schema: {SCHEMA_ID}",
              f"  model: {plain_or_quoted(m('model', 'sonnet'))}",
              f"  claude_code: {quote(normalize(m('claude_code'), 100))}",
              f"  template: {plain_or_quoted(m('template', 'article'))}",
              f"  template_sha256: {quote(m('template_sha256'))}",
              f"  reader_context_sha256: {quote(m('reader_context_sha256'))}",
              f"  input_sha256: {quote(m('input_sha256'))}",
              f"  attempts: {int(meta.get('attempts') or 1)}",
              "---"]
    return "\n".join(lines) + "\n" + fields["body"]


def write_atomic(path: str, text: str) -> str:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(directory, ".extract.md.tmp")
    data = text.encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_publish(args) -> int:
    with open(args.result, "r", encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except ValueError:
            print("structured_output: the result is not a JSON document")
            return 1
    if not isinstance(doc, dict) or "structured_output" not in doc or doc["structured_output"] is None:
        print("structured_output: missing")
        return 1
    with open(args.template, "r", encoding="utf-8", errors="replace") as fh:
        template_text = fh.read()
    with open(args.meta, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    try:
        fields, notes = validate(doc["structured_output"], template_text)
    except Rejected as exc:
        for p in exc.problems:
            print(p)
        if args.notes:
            with open(args.notes, "w", encoding="utf-8") as fh:
                json.dump({"problems": exc.problems}, fh)
        return 1
    if args.notes:
        with open(args.notes, "w", encoding="utf-8") as fh:
            json.dump({"notes": notes}, fh)
    print(write_atomic(args.out, render(fields, meta)))
    return 0


# ---------------------------------------------------------------------------
# Chunked extraction (capture layer §3.8, D13): the part schema, split, validate-part, render
# ---------------------------------------------------------------------------

_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9_/-]*$")


def validate_part(data):
    """Return the validated part object; raise Rejected with gateway-authored diagnostics."""
    problems = []
    if not isinstance(data, dict):
        raise Rejected(["structured_output: must be a JSON object"])
    for key in PART_SCHEMA["required"]:
        if key not in data:
            problems.append(f"{key}: required")
    if problems:
        raise Rejected(problems)
    out = {}
    if not isinstance(data["title"], str):
        problems.append("title: must be a string")
    else:
        out["title"] = _scalar(data["title"], "title", 300, 1, problems)
    if not isinstance(data["summary"], str):
        problems.append("summary: must be a string")
    else:
        summary = _scalar(_WS.sub(" ", data["summary"]), "summary", 1500, 1, problems)
        if summary is not None and len(summary) < 50:
            problems.append("summary: at least 50 characters")
        out["summary"] = summary
    ideas = data["key_ideas"]
    if not isinstance(ideas, list):
        problems.append("key_ideas: must be a list of {claim, domain} objects")
    else:
        if len(ideas) > 20:
            problems.append("key_ideas: at most 20 items")
        kept = []
        for i, item in enumerate(ideas):
            if not isinstance(item, dict) or not isinstance(item.get("claim"), str) or not isinstance(item.get("domain"), str):
                problems.append(f"key_ideas[{i}]: must be an object with string claim and domain")
                continue
            claim = _scalar(item["claim"], f"key_ideas[{i}].claim", 200, 1, problems)
            domain = _WS.sub(" ", item["domain"]).strip().lower()
            if not _DOMAIN.match(domain):
                problems.append(f"key_ideas[{i}].domain: must match ^[a-z0-9][a-z0-9_/-]*$")
            if claim is not None:
                kept.append({"claim": claim, "domain": domain})
        out["key_ideas"] = kept
    quotes = data["quotes"]
    if not isinstance(quotes, list):
        problems.append("quotes: must be a list of strings")
    else:
        if len(quotes) > 8:
            problems.append("quotes: at most 8 items")
        kept = []
        for i, q in enumerate(quotes):
            if not isinstance(q, str):
                problems.append(f"quotes[{i}]: must be a string")
                continue
            v = _scalar(_WS.sub(" ", q), f"quotes[{i}]", 400, 0, problems)
            if v:
                kept.append(v)
        out["quotes"] = kept
    if problems:
        raise Rejected(problems)
    return out


def cmd_validate_part(args) -> int:
    with open(args.result, "r", encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except ValueError:
            print("structured_output: the result is not a JSON document")
            return 1
    if not isinstance(doc, dict) or doc.get("structured_output") is None:
        print("structured_output: missing")
        return 1
    try:
        fields = validate_part(doc["structured_output"])
    except Rejected as exc:
        for p in exc.problems:
            print(p)
        return 1
    data = (json.dumps(fields, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    tmp = args.out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, args.out)
    print(hashlib.sha256(data).hexdigest())
    return 0


_HEADING = re.compile(rb"^#{1,3} ")


def split_text(data: bytes, max_bytes: int, max_parts: int):
    """Split at line boundaries outside fences — a heading cut when one fits, else a blank-line
    cut — packing greedily; parts concatenate to the input. Returns the list of byte strings or
    raises ValueError('unsupported-size: …')."""
    lines = data.splitlines(keepends=True)
    # cut points: byte offset → "heading" | "blank" (the cut goes before that offset's line)
    offsets = []
    pos = 0
    fence = None
    prev_blank = False
    for line in lines:
        stripped = line.lstrip(b" ")
        if fence is None and (stripped.startswith(b"```") or stripped.startswith(b"~~~")):
            fence = stripped[:3]
            kind = None
        elif fence is not None and stripped.startswith(fence):
            fence = None
            kind = None
        elif fence is None and _HEADING.match(line):
            kind = "heading"
        elif fence is None and prev_blank and line.strip():
            kind = "blank"
        else:
            kind = None
        if kind and pos > 0:
            offsets.append((pos, kind))
        prev_blank = (line.strip() == b"") and fence is None
        pos += len(line)
    total = len(data)
    parts = []
    start = 0
    while start < total:
        if total - start <= max_bytes:
            parts.append(data[start:total])
            break
        limit = start + max_bytes
        best_heading = None
        best_blank = None
        for off, kind in offsets:
            if off <= start:
                continue
            if off > limit:
                break
            if kind == "heading":
                best_heading = off
            else:
                best_blank = off
        cut = best_heading if best_heading is not None else best_blank
        if cut is None:
            raise ValueError(f"unsupported-size: a span of more than {max_bytes} bytes cannot be split at a heading or a blank line outside fences (offset {start})")
        parts.append(data[start:cut])
        start = cut
        if len(parts) > max_parts:
            raise ValueError(f"unsupported-size: more than {max_parts} parts needed (extract_max_parts)")
    if len(parts) > max_parts:
        raise ValueError(f"unsupported-size: more than {max_parts} parts needed (extract_max_parts)")
    return parts


def cmd_split(args) -> int:
    with open(args.infile, "rb") as fh:
        data = fh.read()
    try:
        parts = split_text(data, args.max_bytes, args.max_parts)
    except ValueError as exc:
        print(str(exc))
        return 1
    os.makedirs(args.out_dir, exist_ok=True)
    index = []
    for n, chunk in enumerate(parts, 1):
        path = os.path.join(args.out_dir, f"part-{n}.md")
        with open(path, "wb") as fh:
            fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        first = chunk.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        index.append({"n": n, "bytes": len(chunk), "first_line": normalize(first, 120)})
    with open(os.path.join(args.out_dir, "parts.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"parts={len(parts)}")
    return 0


def render_parts(files: list) -> str:
    out = []
    for n, path in enumerate(files, 1):
        with open(path, "r", encoding="utf-8") as fh:
            part = json.load(fh)
        out.append(f"## Part {n} — {part.get('title', '')}")
        out.append("")
        out.append(part.get("summary", ""))
        out.append("")
        for idea in part.get("key_ideas", []):
            out.append(f"- {idea['claim']} #{idea['domain']}")
        if part.get("key_ideas"):
            out.append("")
        for q in part.get("quotes", []):
            out.append(f"> {q}")
        if part.get("quotes"):
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def cmd_render_parts(args) -> int:
    sys.stdout.write(render_parts(args.files))
    return 0


def cmd_normalize(args) -> int:
    value = sys.stdin.read() if args.value == "-" else args.value
    sys.stdout.write(normalize(value, args.max) + "\n")
    return 0


def cmd_quote(args) -> int:
    sys.stdout.write(quote(normalize(args.value, args.max)) + "\n")
    return 0


def cmd_body_headings(args) -> int:
    with open(args.template, "r", encoding="utf-8", errors="replace") as fh:
        for h in body_headings(fh.read()):
            print(h)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate and publish extract.md")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("schema")
    sc.add_argument("--part", action="store_true", help="print the extract-part/1 schema")
    p = sub.add_parser("publish")
    p.add_argument("--result", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--notes", default=None)
    n = sub.add_parser("normalize")
    n.add_argument("--max", type=int, default=300)
    n.add_argument("value")
    q = sub.add_parser("quote")
    q.add_argument("--max", type=int, default=2000)
    q.add_argument("value")
    b = sub.add_parser("body-headings")
    b.add_argument("--template", required=True)
    vp = sub.add_parser("validate-part")
    vp.add_argument("--result", required=True)
    vp.add_argument("--out", required=True)
    sp = sub.add_parser("split")
    sp.add_argument("--in", dest="infile", required=True)
    sp.add_argument("--max-bytes", type=int, required=True)
    sp.add_argument("--max-parts", type=int, required=True)
    sp.add_argument("--out-dir", required=True)
    rp = sub.add_parser("render-parts")
    rp.add_argument("files", nargs="+")
    args = ap.parse_args()
    if args.cmd == "schema":
        print(json.dumps(PART_SCHEMA if args.part else SCHEMA, indent=1))
        return 0
    if args.cmd == "validate-part":
        return cmd_validate_part(args)
    if args.cmd == "split":
        return cmd_split(args)
    if args.cmd == "render-parts":
        return cmd_render_parts(args)
    if args.cmd == "publish":
        return cmd_publish(args)
    if args.cmd == "normalize":
        return cmd_normalize(args)
    if args.cmd == "quote":
        return cmd_quote(args)
    if args.cmd == "body-headings":
        return cmd_body_headings(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
