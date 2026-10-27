#!/usr/bin/env python3
"""mnemon-search.py — search a Mnemon knowledge library.

Run as `mnemon search --query "<q>" [...]` (or directly). Resolves the vault
through the engine's config loader (bin/mnemon-config.sh, honouring
MNEMON_INSTANCE / MNEMON_CONFIG), or takes `--vault <path>` as an explicit
override, then searches Sources/*/extract.md (and optionally source.md,
Synthesis/ and Collections/) for files matching the query. Results are ranked by
rating DESC, then date DESC, then title. The JSON is `mnemon-search/2`
(docs/specs/2026-10-27-research-interface.md §7): every result carries
`result_type` (extract · source · synthesis · collection; wiki in phase 3b) apart
from `tier`, which is the capture tier of a Sources result (`full` · `bookmark`,
read from the revision's frontmatter; `null` for a synthesis or collection
result) — until v2 the `tier` key carried what `result_type` carries now.

Design notes
------------
- Tokenization: the query is split into lowercase tokens >= 2 chars long. A
  file matches when it contains ALL tokens (AND semantics) — "karpathy
  knowledge bases" should match files that contain all three words, not the
  literal phrase.
- Fallback: if no file matches all tokens, relax to the most *specific* token
  that exists in the corpus (fewest files, not longest string) and flag the
  result with `fallback_applied` so the caller can tell the user the search
  was broadened.
- Frontmatter: parsed with a small stdlib parser covering what Mnemon extracts
  use — scalar strings (quoted or not), integers, inline lists `[a, b]`, block
  lists (`- item`) and one-level nested maps (`capture_context:`). Dates stay
  strings ("YYYY-MM-DD"), which is also what the JSON output needs. No PyYAML.
- Output: JSON to stdout by default (consumed by the plugin skill); `--human`
  prints a readable list for ad-hoc CLI use.
- Provider: this script always performs the grep-style scan above — there is
  no qmd-backed search yet. The JSON says so honestly: `provider` is the
  method that ran ("grep"), `configured_provider` is the instance's
  `search_provider` (null when `--vault` bypasses the config).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_config  # noqa: E402
import lib_research  # noqa: E402  (the one reader of Collections/*.md)

SEARCH_SCHEMA = "mnemon-search/2"
RESULT_TYPES = ("extract", "source", "synthesis", "collection", "wiki")
SOURCE_RESULT_TYPES = ("extract", "source")     # results of a Sources/ revision: tier applies


# ---------------------------------------------------------------------------
# Frontmatter parsing — stdlib subset of YAML
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^([A-Za-z_][\w./-]*):(?:\s+(.*))?$")


def _decode_dq(inner: str) -> str:
    """Body of a double-quoted scalar: exactly `\\"` → `"` and `\\\\` → `\\` in one pass
    (the serialization subset of docs/specs/2026-10-15-capture-contracts.md §4.4); every
    other backslash sequence stays as it is, and a tilde is never expanded."""
    out: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n and inner[i + 1] in ('"', "\\"):
            out.append(inner[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1]
        if raw[0] == '"':
            return _decode_dq(inner)
        return inner.replace("''", "'")
    return raw


def _split_inline_list(inner: str) -> list[str]:
    """Split `a, "b, c", d` on commas outside quotes."""
    items: list[str] = []
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
    return [x for x in (i.strip() for i in items) if x]


def _parse_scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ("'", '"'):
        return _unquote(raw)
    raw = re.sub(r"\s+#.*$", "", raw).strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [_unquote(x) for x in _split_inline_list(raw[1:-1])]
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    return raw


def parse_frontmatter_text(block: str) -> dict:
    """Parse a frontmatter block (without the --- fences) into a dict."""
    data: dict = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or line[0] in " \t":
            i += 1
            continue
        m = _KEY_RE.match(line.rstrip())
        if not m:
            i += 1
            continue
        key, raw = m.group(1), (m.group(2) or "")
        if raw.strip() and not raw.strip().startswith("#"):
            data[key] = _parse_scalar(raw)
            i += 1
            continue
        # Value continues on indented lines: a block list or a nested map.
        items: list = []
        nested: dict = {}
        j = i + 1
        while j < len(lines):
            cont = lines[j]
            if not cont.strip():
                j += 1
                continue
            if cont[0] not in " \t":
                # YAML allows a block sequence at the key's own indentation:
                # `domains:` followed by `- career` at column zero.
                if not nested and (cont.startswith("- ") or cont.rstrip() == "-"):
                    pass
                else:
                    break
            s = cont.strip()
            if s == "-":
                items.append("")
            elif s.startswith("- "):
                items.append(_parse_scalar(s[2:]))
            else:
                mm = _KEY_RE.match(s)
                if mm:
                    nested[mm.group(1)] = _parse_scalar(mm.group(2) or "")
            j += 1
        if items:
            data[key] = items
        elif nested:
            data[key] = nested
        else:
            data[key] = ""
        i = j
    return data


def parse_frontmatter(path: Path) -> dict:
    """Extract frontmatter from a markdown file. Returns {} if absent or malformed."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    return parse_frontmatter_text("\n".join(lines[1:end_idx]))


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    if value in (None, ""):
        return []
    return [str(value)]


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def tokenize(query: str) -> list[str]:
    """Lowercase, unicode-aware tokenization. Drops tokens shorter than 2 chars."""
    tokens = re.findall(r"\w+", query.lower(), flags=re.UNICODE)
    return [t for t in tokens if len(t) >= 2]


# ---------------------------------------------------------------------------
# Target collection and matching
# ---------------------------------------------------------------------------

def collect_targets(
    vault: Path, include_source: bool, include_synthesis: bool, include_collections: bool = False
) -> list[tuple[Path, str]]:
    """Return list of (file_path, result_type) tuples to search."""
    targets: list[tuple[Path, str]] = []
    sources_dir = vault / "Sources"
    if sources_dir.is_dir():
        for extract in sorted(sources_dir.glob("*/extract.md")):
            targets.append((extract, "extract"))
        if include_source:
            for src in sorted(sources_dir.glob("*/source.md")):
                targets.append((src, "source"))
    if include_synthesis:
        synth_dir = vault / "Synthesis"
        if synth_dir.is_dir():
            for md in sorted(synth_dir.rglob("*.md")):
                targets.append((md, "synthesis"))
    if include_collections:
        col_dir = vault / "Collections"
        if col_dir.is_dir():
            for md in sorted(col_dir.glob("*.md")):
                if not md.name.startswith("."):
                    targets.append((md, "collection"))
    return targets


def scan_file(path: Path, tokens: list[str]) -> tuple[set[str], str | None]:
    """Scan a file and return (set of matched tokens, snippet).

    Returns the *subset* of query tokens that appear in the file, not just a
    boolean verdict, so the caller can (1) keep files matching ALL tokens and
    (2) fall back to the most specific token using per-token counts from the
    same pass. Returns (empty set, None) if unreadable or nothing matches.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set(), None
    text_lower = text.lower()
    matched = {t for t in tokens if t in text_lower}
    if not matched:
        return set(), None
    return matched, _extract_snippet(text, list(matched))


def _extract_snippet(text: str, tokens: list[str], width: int = 200) -> str | None:
    """Return the first non-frontmatter line containing the longest matched token."""
    if not tokens:
        return None
    seed = max(tokens, key=len)
    lines = text.split("\n")
    in_frontmatter = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if seed in line.lower():
            snippet = stripped
            if len(snippet) > width:
                snippet = snippet[:width] + "…"
            return snippet
    # fallback: match anywhere (frontmatter hit — e.g. author field)
    for line in lines:
        if seed in line.lower():
            snippet = line.strip()
            if len(snippet) > width:
                snippet = snippet[:width] + "…"
            return snippet
    return None


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def capture_tier(source_meta: dict, extract_meta: dict) -> str:
    """The capture tier of a Sources revision: `tier:` of source.md, else of extract.md
    (a legacy placeholder), else `full` (absent = full, vault store §4)."""
    for meta in (source_meta, extract_meta):
        v = meta.get("tier")
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return "full"


def collection_meta(path: Path) -> dict:
    """A collection's metadata through the research reader (the same subset, the same duplicate-key
    and `...` rules as reindex and the doctor), in the shape parse_frontmatter() gives."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    rec = lib_research.read_collection_text(text, path.stem)
    meta = {"status": rec["status"], "domains": rec["domains"], "tags": rec["tags"], "created": rec["created"]}
    return {k: v for k, v in meta.items() if v not in (None, "", [])}


def assemble_result(path: Path, result_type: str, snippet: str | None, vault: Path) -> dict:
    meta = collection_meta(path) if result_type == "collection" else parse_frontmatter(path)
    tier: str | None = None
    status = meta.get("status")
    if result_type == "source":
        # source.md carries capture metadata only (no title/domains/rating);
        # the paired extract.md has the curated fields. Keep the raw file's
        # snippet and type, take metadata from the extract where it has any.
        paired = path.with_name("extract.md")
        extract_meta = parse_frontmatter(paired) if paired.is_file() else {}
        tier = capture_tier(meta, extract_meta)
        status = extract_meta.get("status")      # a source result's status is its extract's; none → null
        merged = dict(meta)
        for key, value in extract_meta.items():
            if value not in (None, "", []):
                merged[key] = value
        meta = merged
    elif result_type == "extract":
        paired = path.with_name("source.md")
        tier = capture_tier(parse_frontmatter(paired) if paired.is_file() else {}, meta)
    # folder path relative to vault (e.g. Sources/2026-04-04_5c78d63f/)
    try:
        rel = path.relative_to(vault)
        folder_rel = str(rel.parent) + "/"
    except ValueError:
        folder_rel = str(path.parent)
    rating = meta.get("rating")
    created = meta.get("created") or meta.get("extracted") or ""
    return {
        "path": folder_rel,
        "file": path.name,
        "result_type": result_type,
        "tier": tier,
        "status": str(status) if isinstance(status, str) and status else None,
        "title": str(meta.get("title") or path.stem),
        "author": str(meta.get("author") or ""),
        "url": str(meta.get("url") or ""),
        "domains": _as_list(meta.get("domains")),
        "tags": _as_list(meta.get("tags")),
        "rating": rating if isinstance(rating, int) and not isinstance(rating, bool) else None,
        "created": str(created),
        "snippet": snippet or "",
    }


def rank_results(results: list[dict]) -> list[dict]:
    """Sort by rating DESC (None last), then created DESC, then title."""
    def key(r: dict):
        rating = r.get("rating")
        has_rating = 0 if rating is None else 1
        return (-has_rating, -(rating or 0), -_date_sort_key(r.get("created", "")), r.get("title", ""))
    return sorted(results, key=key)


def _date_sort_key(date_str: str) -> int:
    """Turn 'YYYY-MM-DD' into a sortable integer. Unknown dates sort last."""
    if not date_str:
        return 0
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(date_str))
    if not m:
        return 0
    return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))


def apply_domain_filter(results: list[dict], domain: str) -> list[dict]:
    if not domain:
        return results
    needle = domain.lower()
    return [r for r in results if any(needle == str(d).lower() for d in _as_list(r.get("domains")))]


def dedupe_by_folder(results: list[dict]) -> list[dict]:
    """If both extract.md and source.md match the same folder, keep extract.md.

    Sources/<folder>/ holds one source, so extract and source results share the
    folder key. Synthesis notes and collections are plain files — several in one
    folder are distinct results — so they are keyed by folder + file name
    (interface §7: deduplication by file for every non-Sources result type).
    """
    by_folder: dict[str, dict] = {}
    type_priority = {"extract": 0, "source": 1}
    for r in results:
        key = r["path"]
        if r["result_type"] not in SOURCE_RESULT_TYPES:
            key = key + r.get("file", "")
        if key not in by_folder:
            by_folder[key] = r
            continue
        current = by_folder[key]
        if type_priority.get(r["result_type"], 99) < type_priority.get(current["result_type"], 99):
            by_folder[key] = r
    return list(by_folder.values())


# ---------------------------------------------------------------------------
# Vault resolution
# ---------------------------------------------------------------------------

SEARCH_METHOD = "grep"  # the only implemented provider


def resolve_vault(vault_override: str | None) -> tuple[Path, str | None]:
    """Return (vault path, configured search_provider) from --vault or the engine config.

    The configured provider is reported, not dispatched on: the scan is always
    grep-style (see the module docstring). None when --vault bypasses the config.
    """
    if vault_override:
        return Path(vault_override).expanduser(), None
    cfg = lib_config.load_config()
    return Path(cfg["VAULT_PATH"]), cfg.get("SEARCH_PROVIDER") or "grep"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Search the Mnemon knowledge library.")
    parser.add_argument("--query", required=True, help="Search query (tokenized, AND semantics)")
    parser.add_argument("--domain", default="", help="Filter by domain tag (e.g. career, learning)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--include-source", action="store_true",
                        help="Also search raw source.md (default: only extract.md)")
    parser.add_argument("--include-synthesis", action="store_true",
                        help="Also search Synthesis/ directory")
    parser.add_argument("--include-collections", action="store_true",
                        help="Also search Collections/ (result_type collection)")
    parser.add_argument("--vault", default=None,
                        help="Vault path (overrides the configured instance)")
    parser.add_argument("--human", action="store_true", help="Human-readable output instead of JSON")
    args = parser.parse_args()

    try:
        vault, configured_provider = resolve_vault(args.vault)
    except lib_config.ConfigError as e:
        print(f"{e}", file=sys.stderr)
        return 2

    if not vault.is_dir():
        print(f"ERROR: vault not found at {vault}", file=sys.stderr)
        return 2

    tokens = tokenize(args.query)
    if not tokens:
        print("ERROR: query contains no usable tokens (>=2 chars)", file=sys.stderr)
        return 2

    targets = collect_targets(vault, args.include_source, args.include_synthesis, args.include_collections)

    # Single pass: every file matching at least one token, with the set of
    # tokens it matched. Primary results: matched == all tokens (AND).
    all_hits: list[tuple[Path, str, set, str | None]] = []
    for path, result_type in targets:
        matched, snippet = scan_file(path, tokens)
        if matched:
            all_hits.append((path, result_type, matched, snippet))

    token_set = set(tokens)
    primary_hits = [(p, t, m, s) for p, t, m, s in all_hits if m == token_set]

    fallback_applied = False
    fallback_token: str | None = None
    if primary_hits:
        selected_hits = primary_hits
    elif len(tokens) > 1:
        # Fallback: the most *specific* token that exists in the corpus —
        # fewest files but at least one. String length is a poor proxy for
        # specificity: a long token with zero matches must never win.
        token_counts: dict[str, int] = {t: 0 for t in tokens}
        for _, _, m, _ in all_hits:
            for t in m:
                token_counts[t] += 1
        viable = {t: c for t, c in token_counts.items() if c > 0}
        if viable:
            fallback_token = min(viable, key=lambda t: (viable[t], -len(t)))
            selected_hits = [h for h in all_hits if fallback_token in h[2]]
            fallback_applied = True
        else:
            selected_hits = []
    else:
        selected_hits = []

    results = [assemble_result(p, t, s, vault) for p, t, _, s in selected_hits]
    results = dedupe_by_folder(results)
    results = apply_domain_filter(results, args.domain)
    results = rank_results(results)
    results = results[: args.limit]

    output = {
        "schema": SEARCH_SCHEMA,
        "query": args.query,
        "tokens": tokens,
        "provider": SEARCH_METHOD,
        "configured_provider": configured_provider,
        "fallback_applied": fallback_applied,
        "fallback_token": fallback_token,
        "count": len(results),
        "results": results,
    }

    if args.human:
        _print_human(output)
    else:
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    return 0


def _print_human(output: dict) -> None:
    if output["count"] == 0:
        print(f"No sources found for '{output['query']}'.")
        return
    if output["fallback_applied"]:
        note = f" (fallback: no file matched all tokens; relaxed to '{output['fallback_token']}')"
    else:
        note = ""
    configured = output.get("configured_provider")
    if configured not in (None, SEARCH_METHOD):
        print(f"note: search_provider is '{configured}' in the instance config, but mnemon search "
              f"scans with {SEARCH_METHOD} — a {configured}-backed search is not implemented.")
    print(f"Found {output['count']} result(s) for '{output['query']}'{note}:\n")
    for i, r in enumerate(output["results"], 1):
        rating = f"rating {r['rating']}" if r["rating"] is not None else "unrated"
        domains = ", ".join(r["domains"]) if r["domains"] else "—"
        print(f"{i}. {r['title']}")
        if r["author"]:
            print(f"   by {r['author']}")
        kind = r["result_type"] + (f" · tier: {r['tier']}" if r.get("tier") else "")
        if r.get("status") and r["result_type"] in ("synthesis", "collection"):
            kind += f" · status: {r['status']}"
        print(f"   {rating} · {r['created']} · domains: {domains} · {kind}")
        if r["url"]:
            print(f"   {r['url']}")
        if r["snippet"]:
            print(f"   → {r['snippet']}")
        print(f"   path: {r['path']}")
        print()


if __name__ == "__main__":
    sys.exit(main())
