#!/usr/bin/env python3
"""
knowledge-reindex.py — rebuild a Mnemon vault's index.md from current Sources/.

Called from knowledge-gateway.sh as a post-extract hook and by `mnemon reindex`, so index.md
never drifts from the files (docs/specs/2026-10-15-capture-layer.md §3.9, D14, §12 A1).

Behavior:
- Every row is regenerated from the files — one reverse-chronological catalog entry per folder
  with an extract.md: `- [[Sources/<folder>/extract|<title>]] (<source_type>, <domains>,
  rating <n>[, <status>])`, plus two derived suffixes:
    ` — <index_note>` — `index_note:` of extract.md (one line, the §4.4 serialization subset,
      decoded and display-escaped like a title): the place for a durable annotation;
    ` ⤳ superseded by [[Sources/<new>/extract|<new title>]]` — derived from every source.md's
      `supersedes:` (`<revision>` or `Sources/<revision>/extract`; one scalar, or a YAML block
      list when one re-capture supersedes several revisions), one marker per successor
      (fan-out lists each; a successor without extract.md links its source; a cycle is reported
      on stderr and not marked; a `supersedes:` naming a folder that no longer exists is counted
      as dangling and marks nothing). Nothing is ever written into source.md or extract.md.
- Preservation transaction (A1, lossless): before index.md is replaced, every existing row whose
  exact text differs from the regenerated row (and every row of a folder that gets no row) is
  appended verbatim under a dated heading to <vault>/_inputs/index-hand-edits-<YYYY-MM-DD>.md —
  deduplicated by (folder, sha256(row)) across that day's file, so a second edit on one day is
  kept too — and that file and its directory are fsynced first. If the file cannot be written
  the index is left untouched and the run exits 1. --no-hand-edit-report skips the step.
- `## Collections` lists every `Collections/*.md` through the research reader (`lib_research.py`,
  phase 3a): status, item count, the question; a document the reader fails is listed with its
  first error. The Status table carries a Collections row.
- index.md is written atomically (tmp + fsync + rename); counts Sources / Extracted / Synthesis /
  Superseded; the pending note names `mnemon add --resume <revision>` and counts legacy
  placeholders (`status: bookmark`, `fetch_status: failed` in source.md) apart; lists Synthesis/.
- Headers are read whole (through the closing `---`, cap 256 KB): a source.md whose header has no
  closing delimiter within the cap is `malformed-frontmatter <folder>` on stderr and its
  `supersedes:` is not read (§12 A2).

Usage:
    knowledge-reindex.py [VAULT_PATH] [--no-hand-edit-report]

VAULT_PATH defaults to $MNEMON_VAULT_PATH then $PWD. The vault must have Sources/ and
(optionally) Synthesis/.

Exit codes:
    0  rebuilt
    1  vault path missing / not a Mnemon vault, or the hand-edit report could not be written
       (index.md untouched)
    2  unexpected error during rebuild
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Iterator

HEADER_CAP = 256 * 1024          # §12 A2: the whole header, never a silent truncation
NOTE_SEP = ' — '                 # the index_note suffix
SUPERSEDED_SEP = ' ⤳ superseded by '
HAND_EDIT_MARK = '<!-- mnemon:hand-edit folder={folder} sha256={sha} -->'
HAND_EDIT_RE = re.compile(r'^<!-- mnemon:hand-edit folder=(\S+) sha256=([0-9a-f]{64}) -->$', re.M)


def _load_research():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("lib_research", os.path.join(here, "lib_research.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collection_rows(vault: Path) -> list[str]:
    """One row per `Collections/*.md` (sorted by name), from the research reader:
    `- [[Collections/<name>|<name>]] (<status>, <n> items[, <k> excluded][, <m> malformed]) — <question>`;
    a document whose schema fails: `(fail: <first error>)`. The folder absent → []."""
    lr = _load_research()
    rows: list[str] = []
    existing = lr.existing_revisions(str(vault))
    try:
        records = lr.scan_collections(str(vault), existing=existing)
    except OSError as exc:
        print(f"knowledge-reindex: Collections/ could not be listed ({exc}); the section is not regenerated", file=sys.stderr)
        return [f"- _Collections/ could not be listed ({type(exc).__name__}); the rows are not regenerated_"]
    for rec in records:
        name = rec["name"]
        if rec["state"] == "unreadable":
            rows.append(f"- [[Collections/{name}|{display_text(name)}]] (unreadable)")
            continue
        c = rec["counts"]
        parts = [rec["status"] or "?", f"{c['items']} items"]
        if c["excluded"]:
            parts.append(f"{c['excluded']} excluded")
        if c["malformed"]:
            parts.append(f"{c['malformed']} malformed")
        row = f"- [[Collections/{name}|{display_text(name)}]] ({', '.join(parts)})"
        if rec["errors"]:
            row += f" (fail: {rec['errors'][0]})"
        if rec["question"]:
            row += f" — {display_text(rec['question'])}"
        rows.append(row)
    return rows


def wiki_rows(vault: Path) -> list[str]:
    """One row per `Wiki/*.md` (sorted by name), from the research reader — the stored facts only,
    the derived states are the doctor's: `- [[Wiki/<name>|<name>]] (<kind>, <n> sources, <k> collections[,
    updated <date>])`; a page whose schema fails: `(fail: <first error>)`; unreadable: `(unreadable)`."""
    lr = _load_research()
    rows: list[str] = []
    try:
        records = lr.scan_wiki(str(vault))
    except OSError as exc:
        print(f"knowledge-reindex: Wiki/ could not be listed ({exc}); the section is not regenerated", file=sys.stderr)
        return [f"- _Wiki/ could not be listed ({type(exc).__name__}); the rows are not regenerated_"]
    for rec in records:
        name = rec["name"]
        if rec["state"] == "unreadable":
            rows.append(f"- [[Wiki/{name}|{display_text(name)}]] (unreadable)")
            continue
        c = rec["counts"]
        parts = [rec["kind"] or "?", f"{c['based_on']} sources", f"{c['collections']} collections"]
        if rec["updated"]:
            parts.append(f"updated {rec['updated']}")
        row = f"- [[Wiki/{name}|{display_text(name)}]] ({', '.join(parts)})"
        if rec["errors"]:
            row += f" (fail: {rec['errors'][0]})"
        rows.append(row)
    return rows


class PreservationError(Exception):
    """The hand-edit report could not be written: the index must not be replaced."""


def decode_dq(inner: str) -> str:
    """Body of a double-quoted scalar: exactly `\\"` → `"` and `\\\\` → `\\` (spec §4.4), one pass.

    Any other backslash sequence is kept as it is; a single pass means a value that ends
    in a backslash followed by an escaped quote (`\\\\\\"`) decodes to `\\"`, not to `"`.
    """
    out = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == '\\' and i + 1 < n and inner[i + 1] in ('"', '\\'):
            out.append(inner[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def frontmatter_block(text: str) -> str:
    """The text between the opening `---` line and the closing `---`/`...` line, or the
    whole text when there is no frontmatter block (legacy files)."""
    if not text.startswith('---'):
        return text
    lines = text.split('\n')
    if lines[0].strip() != '---':
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() in ('---', '...'):
            return '\n'.join(lines[1:i])
    return text


def read_header(path: Path) -> tuple[str | None, str]:
    """The frontmatter block of a file read whole, through the closing delimiter (§12 A2).

    Returns (block, state): state is `ok` (block = the header's lines), `none` (the file does
    not start with `---`: no header, block None) or `malformed` (an opening `---` without a
    closing `---`/`...` within HEADER_CAP bytes, or an unreadable file; block None).
    """
    try:
        with open(path, 'rb') as fh:
            data = fh.read(HEADER_CAP + 1)
    except OSError:
        return None, 'malformed'
    text = data.decode('utf-8', errors='replace')
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        return None, 'none'
    pos = len(lines[0]) + 1
    for i in range(1, len(lines)):
        if lines[i].strip() in ('---', '...'):
            if pos > HEADER_CAP:
                return None, 'malformed'
            return '\n'.join(lines[1:i]), 'ok'
        pos += len(lines[i]) + 1
    return None, 'malformed'


def decode_scalar(raw: str) -> str | None:
    """A frontmatter scalar as written: a double-quoted value decodes `\\"` and `\\\\` and nothing
    else (no tilde expansion, no other escapes — the serialization subset of spec §4.4); a
    single-quoted value is taken as it is; an unquoted value is trimmed. Empty → None."""
    v = raw.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        v = decode_dq(v[1:-1])
    elif len(v) >= 2 and v.startswith("'") and v.endswith("'"):
        v = v[1:-1]
    return v or None


def field_in_block(block: str, key: str) -> str | None:
    """A top-level 'key: value' of a frontmatter block (nested, indented keys never match),
    decoded by `decode_scalar`."""
    m = re.search(rf'^{re.escape(key)}:[ \t]*(.*)$', block, re.M)
    if not m:
        return None
    return decode_scalar(m.group(1))


def parse_field(text: str, key: str) -> str | None:
    """Parse a top-level 'key: value' from a file's frontmatter block (lenient: a file without a
    closing delimiter is read whole, as legacy extract.md files are)."""
    return field_in_block(frontmatter_block(text), key)


def display_text(title: str) -> str:
    """Link display text that cannot close the wikilink or start a new alias (spec §4.4)."""
    return title.replace(']]', '] ]').replace('|', '/')


def parse_list_field(text: str, key: str) -> list[str]:
    """Parse 'key: [a, b, c]' or 'key:\\n  - a\\n  - b' frontmatter lists."""
    m = re.search(rf'^{key}:\s*\[([^\]]*)\]', text, re.M)
    if m:
        return [x.strip().strip('"').strip("'") for x in m.group(1).split(',') if x.strip()]
    m = re.search(rf'^{key}:\s*\n((?:[ \t]+-[^\n]*\n)+)', text, re.M)
    if m:
        return [
            line.strip()[1:].strip().strip('"').strip("'")
            for line in m.group(1).splitlines() if line.strip().startswith('-')
        ]
    return []


def parse_existing_entries(index_text: str) -> dict[str, list[str]]:
    """{folder_name: [row, …]} for every existing `- [[Sources/<folder>/…` row, in order."""
    out: dict[str, list[str]] = {}
    for line in index_text.splitlines():
        m = re.match(r'^- \[\[Sources/([\w-]+)/', line)
        if m:
            out.setdefault(m.group(1), []).append(line)
    return out


def supersedes_target(value: str | None) -> str | None:
    """The folder a `supersedes:` value names: `<revision>` or `Sources/<revision>/extract`
    (a wikilink form and a `/source` suffix are tolerated), prefix and suffix stripped."""
    if not value:
        return None
    v = value.strip()
    if v.startswith('[[') and v.endswith(']]'):
        v = v[2:-2].split('|', 1)[0].strip()
    if v.startswith('Sources/'):
        v = v[len('Sources/'):]
    for suffix in ('/extract.md', '/extract', '/source.md', '/source'):
        if v.endswith(suffix):
            v = v[:-len(suffix)]
            break
    v = v.strip('/')
    return v or None


def supersedes_values(block: str) -> list[str]:
    """The values a header's `supersedes:` carries, as written: the scalar on the key's line, or —
    when the key has no value on its line — the `  - <value>` items of the block list that follows
    it (through the next unindented line; other indented lines are ignored). Each value is decoded
    by `decode_scalar`; empty values are dropped."""
    m = re.search(r'^supersedes:[ \t]*(.*)$', block, re.M)
    if not m:
        return []
    if m.group(1).strip():
        v = decode_scalar(m.group(1))
        return [v] if v else []
    out: list[str] = []
    for line in block[m.end():].split('\n')[1:]:
        if not line.strip():
            continue
        if line[0] not in ' \t':
            break
        item = re.match(r'^[ \t]+-[ \t]*(.*)$', line)
        if item:
            v = decode_scalar(item.group(1))
            if v:
                out.append(v)
    return out


def supersedes_targets(block: str) -> list[str]:
    """The folders a header's `supersedes:` names — one value or a block list, each in either form
    (`supersedes_target`) — in the order written, without repeats."""
    out: list[str] = []
    for v in supersedes_values(block):
        t = supersedes_target(v)
        if t and t not in out:
            out.append(t)
    return out


def cycle_components(edges: dict[str, list[str]]) -> tuple[set[tuple[str, str]], list[list[str]]]:
    """(the edges that lie on a cycle, the cycles) of a `new → [old…]` graph: Tarjan's strongly
    connected components, iterative (a chain of any length never recurses); a component of two or
    more nodes is a cycle, listed from its smallest node along its edges — the order a simple cycle
    is walked — with any node the walk did not reach appended sorted. Self-edges are not part of
    this graph (the scan drops them; the vault store counts them apart)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comps: list[list[str]] = []
    for root in sorted(edges):
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, Iterator[str]]] = [(root, iter(edges.get(root, [])))]
        while work:
            node, it = work[-1]
            pushed = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(edges.get(nxt, []))))
                    pushed = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if pushed:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) >= 2:
                    comps.append(comp)
    cycle_edges: set[tuple[str, str]] = set()
    cycles: list[list[str]] = []
    for comp in comps:
        members = set(comp)
        for u in comp:
            for v in edges.get(u, []):
                if v in members:
                    cycle_edges.add((u, v))
        walk = [min(comp)]
        while True:
            nxt = sorted(v for v in edges.get(walk[-1], []) if v in members and v not in walk)
            if not nxt:
                break
            walk.append(nxt[0])
        walk += sorted(members - set(walk))
        cycles.append(walk)
    cycles.sort()
    return cycle_edges, cycles


def folder_title(folder: Path) -> tuple[str, str]:
    """(title, link target) of a folder: extract.md's title when it exists (target `extract`),
    else source.md's title or `# ` heading or the folder name (target `source`)."""
    ex_path = folder / 'extract.md'
    if ex_path.exists():
        ex = ex_path.read_text(encoding='utf-8', errors='ignore')
        title = parse_field(ex, 'title')
        if title:
            return title, 'extract'
    src_path = folder / 'source.md'
    src = src_path.read_text(encoding='utf-8', errors='ignore') if src_path.exists() else ''
    title = parse_field(src, 'title')
    if not title:
        m = re.search(r'^#\s+(.+)$', src, re.M)
        title = m.group(1).strip() if m else folder.name
    return title, ('extract' if ex_path.exists() else 'source')


def generate_entry(folder: Path) -> str | None:
    """The base index row of a folder (without the derived suffixes), or None without extract.md."""
    ex_path = folder / 'extract.md'
    if not ex_path.exists():
        return None
    ex = ex_path.read_text(encoding='utf-8', errors='ignore')
    src_path = folder / 'source.md'
    src = src_path.read_text(encoding='utf-8', errors='ignore') if src_path.exists() else ''

    title = parse_field(ex, 'title')
    if not title:
        m = re.search(r'^#\s+(.+)$', src, re.M)
        title = m.group(1).strip() if m else folder.name

    source_type = parse_field(ex, 'source_type') or parse_field(src, 'source_type') or '?'
    rating = parse_field(ex, 'rating')
    status = parse_field(ex, 'status')
    domains = parse_list_field(ex, 'domains')
    domains_str = '/'.join(domains) if domains else 'no-domain'

    parts = [source_type, domains_str]
    if rating and rating not in ('null', 'None'):
        parts.append(f'rating {rating}')
    if status and status not in ('extracted', None):
        parts.append(status)

    return f"- [[Sources/{folder.name}/extract|{display_text(title)}]] ({', '.join(parts)})"


def index_note(folder: Path) -> str | None:
    """`index_note:` of extract.md, decoded and display-escaped; None when absent or empty."""
    ex_path = folder / 'extract.md'
    if not ex_path.exists():
        return None
    note = parse_field(ex_path.read_text(encoding='utf-8', errors='ignore'), 'index_note')
    if not note:
        return None
    note = ' '.join(note.split())   # single line, single spaces (the subset of §4.4)
    return display_text(note) if note else None


def scan_sources(all_dirs: list[Path]) -> dict:
    """One whole-header scan of every Sources/*/source.md (§12 A2): the supersedes map
    old → [new…] (sorted), the dangling targets, the cycles (and the number of edges on them),
    the legacy placeholders and the malformed headers."""
    names = {d.name for d in all_dirs}
    edges: dict[str, list[str]] = {}    # new → [old…] (a supersedes: names one or several revisions)
    legacy: set[str] = set()
    malformed: list[str] = []
    for d in all_dirs:
        src = d / 'source.md'
        if not src.exists():
            continue
        block, state = read_header(src)
        if state == 'malformed':
            malformed.append(d.name)
            continue
        if block is None:
            continue
        targets = [t for t in supersedes_targets(block) if t != d.name]
        if targets:
            edges[d.name] = targets
        if (field_in_block(block, 'status') or '') == 'bookmark' or \
           (field_in_block(block, 'fetch_status') or '') == 'failed':
            legacy.add(d.name)

    # cycles: a folder may have several outgoing edges, so the cycles are the strongly connected
    # components of the graph; an edge inside one marks nothing
    cycle_edges, cycles = cycle_components(edges)

    successors: dict[str, list[str]] = {}
    dangling: list[tuple[str, str]] = []
    for new, olds in sorted(edges.items()):
        for old in olds:
            if (new, old) in cycle_edges:
                continue
            if old not in names:
                dangling.append((new, old))
                continue
            successors.setdefault(old, []).append(new)
    return {'successors': successors, 'dangling': dangling, 'cycles': cycles,
            'cycle_edges': len(cycle_edges), 'legacy': legacy, 'malformed': malformed}


def build_row(folder: Path, successors: dict[str, list[str]], sources_dir: Path) -> str | None:
    base = generate_entry(folder)
    if base is None:
        return None
    row = base
    note = index_note(folder)
    if note:
        row += NOTE_SEP + note
    for new in successors.get(folder.name, []):
        title, target = folder_title(sources_dir / new)
        row += f"{SUPERSEDED_SEP}[[Sources/{new}/{target}|{display_text(title)}]]"
    return row


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def fsync_dir(path: Path) -> None:
    dfd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def preserve_rows(vault: Path, replaced: list[tuple[str, str]], today: str) -> tuple[int, Path]:
    """Append every (folder, old row) not yet in today's report (dedup by (folder, sha256(row))),
    fsync the file and its directory. Returns (rows written, path). Raises PreservationError."""
    inputs = vault / '_inputs'
    path = inputs / f'index-hand-edits-{today}.md'
    try:
        inputs.mkdir(parents=True, exist_ok=True)
        existing_text = path.read_text(encoding='utf-8') if path.exists() else ''
    except OSError as exc:
        raise PreservationError(f'{path}: {exc.strerror or exc}')
    known = {(f, h) for f, h in HAND_EDIT_RE.findall(existing_text)}
    new: list[tuple[str, str, str]] = []
    for folder, row in replaced:
        h = sha256_text(row)
        if (folder, h) in known:
            continue
        known.add((folder, h))
        new.append((folder, h, row))
    if not new:
        return 0, path
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    chunk = []
    if not existing_text:
        chunk.append(f"# index.md rows replaced on {today}\n\n"
                     "`mnemon reindex` regenerates every row of `index.md` from `Sources/*/extract.md` and "
                     "`source.md`. Each row below differed from the regenerated one when it was replaced "
                     "and is kept here verbatim, once per distinct text (lossless). A durable annotation "
                     "belongs in `index_note:` of the folder's `extract.md` — the index shows it as "
                     "` — <note>`; `supersedes:` markers are derived from `source.md`.\n")
    chunk.append(f"\n## {stamp} — mnemon reindex\n\n")
    for folder, h, row in new:
        chunk.append(HAND_EDIT_MARK.format(folder=folder, sha=h) + '\n' + row + '\n\n')
    try:
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(''.join(chunk))
            fh.flush()
            os.fsync(fh.fileno())
        fsync_dir(inputs)
    except OSError as exc:
        raise PreservationError(f'{path}: {exc.strerror or exc}')
    return len(new), path


def reindex(vault: Path, hand_edit_report: bool = True) -> dict:
    """Rebuild vault/index.md. Returns the counters."""
    sources_dir = vault / 'Sources'
    synthesis_dir = vault / 'Synthesis'
    index_path = vault / 'index.md'

    if not sources_dir.is_dir():
        raise FileNotFoundError(f"No Sources/ directory in {vault}")

    all_source_dirs = sorted(d for d in sources_dir.iterdir() if d.is_dir() and not d.name.startswith('.'))
    extracted_dirs = sorted(
        (d for d in all_source_dirs if (d / 'extract.md').exists()),
        key=lambda d: d.name,
        reverse=True,
    )
    no_extract = [d for d in all_source_dirs if not (d / 'extract.md').exists()]

    scan = scan_sources(all_source_dirs)
    for name in scan['malformed']:
        print(f"knowledge-reindex: malformed-frontmatter {name} (header without a closing --- within "
              f"{HEADER_CAP // 1024} KB; its supersedes: is not read)", file=sys.stderr)
    for cyc in scan['cycles']:
        print(f"knowledge-reindex: supersedes cycle: {' → '.join(cyc + [cyc[0]])} (not marked)", file=sys.stderr)

    existing = parse_existing_entries(
        index_path.read_text(encoding='utf-8') if index_path.exists() else ''
    )

    entries = []
    regenerated: dict[str, str] = {}
    for d in extracted_dirs:
        row = build_row(d, scan['successors'], sources_dir)
        if row:
            entries.append(row)
            regenerated[d.name] = row

    # A1 — every existing row whose exact text is not the regenerated row of its folder (and every
    # row of a folder that gets no row at all) is preserved before index.md is replaced.
    replaced: list[tuple[str, str]] = []
    for folder, rows in existing.items():
        for row in rows:
            if regenerated.get(folder) != row:
                replaced.append((folder, row))
    preserved = 0
    report_path = None
    if replaced:
        if hand_edit_report:
            preserved, report_path = preserve_rows(vault, replaced, datetime.date.today().isoformat())
            if preserved:
                print(f"knowledge-reindex: {preserved} replaced row(s) preserved in "
                      f"{report_path.relative_to(vault)}", file=sys.stderr)
        else:
            print(f"knowledge-reindex: {len(replaced)} replaced row(s) not preserved (--no-hand-edit-report)",
                  file=sys.stderr)
    if os.environ.get('MNEMON_TEST_HOOKS') == '1' and os.environ.get('MNEMON_TEST_CRASH_AT') == 'after-hand-edit-report':
        os._exit(99)

    syn_files = sorted(
        [f for f in synthesis_dir.iterdir() if f.suffix == '.md']
        if synthesis_dir.is_dir() else [],
        key=lambda f: f.name,
    )
    syn_lines = []
    for f in syn_files:
        head = f.read_text(encoding='utf-8', errors='ignore')[:600]
        m = re.search(r'^#\s+(.+)$', head, re.M)
        title = m.group(1).strip() if m else f.stem
        syn_lines.append(f"- [[Synthesis/{f.stem}|{display_text(title)}]]")

    collections_present = (vault / 'Collections').is_dir()
    col_lines = collection_rows(vault) if collections_present else []
    collections_block = (
        f"## Collections\n\n{chr(10).join(col_lines)}\n\n" if collections_present else "")
    wiki_present = (vault / 'Wiki').is_dir()
    wiki_lines = wiki_rows(vault) if wiki_present else []
    wiki_block = f"## Wiki\n\n{chr(10).join(wiki_lines)}\n\n" if wiki_present else ""

    sources_count = len(all_source_dirs)
    extracted_count = len(extracted_dirs)
    synthesis_count = len(syn_files)
    collections_count = sum(1 for r in col_lines if r.startswith("- [[Collections/")) if not any(
        r.startswith("- _Collections/ could not be listed") for r in col_lines) else "?"
    wiki_count = sum(1 for r in wiki_lines if r.startswith("- [[Wiki/")) if not any(
        r.startswith("- _Wiki/ could not be listed") for r in wiki_lines) else "?"
    superseded_count = len(scan['successors'])
    today = datetime.date.today().isoformat()

    legacy = sorted(d.name for d in no_extract if d.name in scan['legacy'])
    pending = sorted(d.name for d in no_extract if d.name not in scan['legacy'])
    notes = []
    if pending:
        shown = ', '.join(pending[:5]) + (', …' if len(pending) > 5 else '')
        notes.append(f"_{len(pending)} source(s) captured without extract.md "
                     f"(finish each with `mnemon add --resume <revision>`: {shown})._")
    if legacy:
        shown = ', '.join(legacy[:5]) + (', …' if len(legacy) > 5 else '')
        notes.append(f"_{len(legacy)} legacy placeholder(s) without extract.md "
                     f"(`status: bookmark` or `fetch_status: failed` in source.md — not resumable; capture again): {shown}._")
    pending_note = '\n'.join(notes)

    out = f"""---
type: index
created: 2026-03-29
updated: {today}
---

# Knowledge Store

Universal reference storage. Sources captured and extracted by AI, synthesis written by human.

## Status

| Metric | Count |
|--------|-------|
| Sources | {sources_count} |
| Extracted | {extracted_count} |
| Superseded | {superseded_count} |
| Collections | {collections_count} |
| Wiki | {wiki_count} |
| Synthesis | {synthesis_count} |

{pending_note}

## Recent Sources

{chr(10).join(entries)}

{collections_block}{wiki_block}## Synthesis Notes

{chr(10).join(syn_lines)}

## Commands

- `/source-add <URL or title>` — capture a new source
- `/source-search <query>` — search the library
- `/source-status` — library dashboard
- `mnemon reindex` — rebuild this index from Sources/
"""

    # Atomic: the vault never holds a half-written index.md (D15, §3.5).
    tmp_path = index_path.with_name('.index.md.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        fh.write(out)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, index_path)
    try:
        fsync_dir(vault)
    except OSError:
        pass
    return {'sources': sources_count, 'extracted': extracted_count, 'synthesis': synthesis_count,
            'collections': collections_count, 'collections_present': collections_present,
            'wiki': wiki_count, 'wiki_present': wiki_present,
            'pending': len(pending), 'legacy': len(legacy), 'superseded': superseded_count,
            'dangling': len(scan['dangling']), 'preserved': preserved, 'malformed': len(scan['malformed'])}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Rebuild a Mnemon vault's index.md from Sources/")
    ap.add_argument('vault', nargs='?', default=None, help='the vault (default: $MNEMON_VAULT_PATH, else the cwd)')
    ap.add_argument('--no-hand-edit-report', action='store_true',
                    help='replace differing rows without preserving them in _inputs/index-hand-edits-<date>.md')
    args = ap.parse_args(argv[1:])
    vault_arg = args.vault or os.environ.get('MNEMON_VAULT_PATH', os.getcwd())
    vault = Path(vault_arg).expanduser().resolve()

    if not (vault / 'Sources').is_dir():
        print(f"knowledge-reindex: no Sources/ at {vault}", file=sys.stderr)
        return 1

    try:
        c = reindex(vault, hand_edit_report=not args.no_hand_edit_report)
    except PreservationError as exc:
        print(f"knowledge-reindex: hand-edit report not written ({exc}) — index.md left untouched; "
              f"fix the vault's _inputs/ or run: mnemon reindex --no-hand-edit-report", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"knowledge-reindex: failed — {exc}", file=sys.stderr)
        return 2

    line = (f"reindex: {vault.name} — Sources={c['sources']}, Extracted={c['extracted']}, "
            f"Synthesis={c['synthesis']}, Superseded={c['superseded']}")
    if c['collections_present']:
        line += f", Collections={c['collections']}"
    if c['wiki_present']:
        line += f", Wiki={c['wiki']}"
    for key, label in (('pending', 'Pending'), ('legacy', 'Legacy'), ('dangling', 'Dangling-supersedes'),
                       ('preserved', 'Preserved'), ('malformed', 'Malformed-frontmatter')):
        if c[key]:
            line += f", {label}={c[key]}"
    print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
