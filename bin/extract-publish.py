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
    body = data["body"]
    if "\x00" in body:
        problems.append("body: must not contain NUL")
    if body.lstrip("\r\n").startswith("---"):
        problems.append("body: must not start with ---")
    if not body.strip():
        problems.append("body: must not be empty")
    missing = [h for h in body_headings(template_text) if not re.search(r"^" + re.escape(h) + r"[ \t]*$", body, re.M)]
    for h in missing:
        problems.append(f"body: must contain the heading {h}")
    out["body"] = body.replace("\r\n", "\n").rstrip() + "\n"
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
    sub.add_parser("schema")
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
    args = ap.parse_args()
    if args.cmd == "schema":
        print(json.dumps(SCHEMA, indent=1))
        return 0
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
