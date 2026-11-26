#!/usr/bin/env python3
"""text-mismatch-audit.py — text mismatch candidates: revisions whose `source.md` text is not found in the
text their retained raw converts to now (capture layer §18, A17).

    text-mismatch-audit.py [--config PATH] [--all-url] [--cited] [--json] [--timeout S]
    mnemon audit text-mismatch [--all-url] [--cited] [--json] [--timeout S]

The population is every current revision with `origin: url`, no `capture.completeness` and not of the
bookmark tier — the captures made before the byte-exact capture contract, whose body was never checked
against a raw. `--all-url` widens it to every url revision that has a raw (current or superseded, any
completeness); `--cited` keeps only the revisions a collection's `## Items`, a wiki page's `based_on` or a
synthesis note links (the citation scan of `bin/lib_vault.py`).

For each revision the raw is looked up — the file the `archive:` field names, else `<revision>.<ext>` in the
archive, else in the vault's state dir — and converted by a read-only call in a bounded subprocess: an HTML
raw through `capture-text.py` (the converter a re-capture would run, writing only into a private temp dir),
a PDF through `pdftotext -layout`, a site bundle through `capture-site.py assemble`, a text raw read as it
is. The comparison is the reversed containment rule `capture.check.source_presence`: every eligible line of
the body (the forward rule's eligibility — six words, a two-word heading, twenty spaceless characters, the
chrome list) must occur in the raw's text. One class per revision:

    match                              every eligible line found
    mismatch                           the share of eligible lines not found, up to three of them
    unprobed: no raw                   no raw in the archive or the state dir
    unprobed: no eligible lines        nothing in the body the rule can require
    unprobed: converter unavailable    no converter here (pdftotext missing, an unsupported raw), or it
                                       timed out or failed on its own
    unprobed: raw unreadable           the raw cannot be read (permissions, undecodable bytes, a cloud
                                       placeholder whose bytes are not on this machine — never downloaded)

A mismatch is a candidate, not a finding: a body that is not the page's text produces one, and so do
converter drift (today's converter keeps other text than the one of the capture), old selection rules (a body
that kept a sidebar, a comment section, a header the content subtree drops) and text the old capture added
itself. The report names the lines, never a cause.

Read-only (vault store D6): nothing is written into the vault, the archive or the state dir, no lock is
taken, nothing is fetched. The converters run with a private temp dir under TMPDIR, removed afterwards; the
run refuses a TMPDIR inside the vault, the archive or the state dir. Exit 0 when the audit ran, whatever it
found; 1 when `Sources/` cannot be listed; 2 on a configuration or usage error. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Dict, List, NamedTuple, Optional, Tuple

BIN = os.path.dirname(os.path.realpath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)

import lib_config  # noqa: E402
import lib_vault as lv  # noqa: E402
from capture import check, proc, resolve  # noqa: E402

SCHEMA = "mnemon-text-mismatch-audit/1"
MATCH = "match"
MISMATCH = "mismatch"
NO_RAW = "unprobed: no raw"
NO_LINES = "unprobed: no eligible lines"
NO_CONVERTER = "unprobed: converter unavailable"
UNREADABLE = "unprobed: raw unreadable"
CLASSES = (MATCH, MISMATCH, NO_RAW, NO_LINES, NO_CONVERTER, UNREADABLE)
SAMPLES = 3
SAMPLE_CHARS = check.MISSING_EXAMPLE_CHARS
# the order a revision-named raw is looked up in the archive and in the state dir
RAW_EXTS = (".html", ".htm", ".pdf", ".site", ".txt")
HTML_EXTS = (".html", ".htm")
CAPTURE_TEXT_RAW_KINDS = ("response", "rendered", "site", "external")
# macOS `SF_DATALESS` (sys/stat.h): a file whose bytes live in the cloud; reading it downloads them
SF_DATALESS = 0x40000000
OUTER_GRACE_S = 15.0
SHARE_BUCKETS = ((0.10, "0.00-0.10"), (0.25, "0.10-0.25"), (0.50, "0.25-0.50"), (0.75, "0.50-0.75"),
                 (1.00, "0.75-1.00"))
DEFAULT_TIMEOUT = 120.0
CAPTURE_TEXT = os.path.join(BIN, "capture-text.py")
CAPTURE_SITE = os.path.join(BIN, "capture-site.py")

HELP_EPILOG = """\
A mismatch is a candidate, not a finding: it says that lines of the body are not in the raw's text, never
why. A body that is not the page's text produces one; so do converter drift (today's converter keeps other
text than the one that made the capture), old selection rules (a body that kept a sidebar or a comment
section the content subtree now drops) and text the old capture added itself. Read the samples and the raw
before you re-capture (`mnemon add --url <url> --recapture`).

Read-only: nothing is written into the vault, the archive or the state dir; nothing is fetched.
"""


class Converted(NamedTuple):
    text: Optional[str]          # the raw's text; None when the class below says why there is none
    converter: Optional[str]
    title: Optional[str]         # the converter's <title>, when it reports one
    klass: Optional[str]         # an unprobed class
    detail: Optional[str]
    tools: Optional[dict]        # capture-text's tool versions (defuddle, pandoc)


def is_dataless(st) -> bool:
    """A cloud placeholder (macOS `SF_DATALESS`): its bytes are not on this machine."""
    return sys.platform == "darwin" and bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def unreadable_reason(path: str) -> Optional[str]:
    """Why a raw cannot be read without side effects, or None. A placeholder is never opened: opening it
    would download it, and the audit makes no network request, its own or the system's on its behalf."""
    paths = [path]
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            dirs.sort()
            paths.extend(os.path.join(root, f) for f in sorted(files))
    for p in paths:
        try:
            st = os.stat(p)
        except OSError as exc:
            return "cannot stat %s: %s" % (os.path.basename(p), exc.strerror or exc)
        if is_dataless(st):
            return "%s is a cloud placeholder whose bytes are not on this machine (the audit does not download it)" \
                % os.path.basename(p)
        if os.path.isdir(p):
            continue
        try:
            with open(p, "rb") as fh:
                fh.read(1)
        except OSError as exc:
            return "cannot read %s: %s" % (os.path.basename(p), exc.strerror or exc)
    return None


def read_body(path: str) -> Optional[str]:
    """The text after the frontmatter of a source.md (the whole file when no header closes), or None."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    text = data.decode("utf-8", "replace")
    block, body = lv.split_frontmatter(text)
    return body if block is not None else text


def find_raw(rec: dict, archive_dir: Optional[str], entries: set, state_raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(store, path) of a revision's raw: the archive member `archive:` names, else `<revision>.<ext>` in the
    archive, else in the state dir's `raw/` (the engine's store without an archive); (None, None) otherwise."""
    names: List[str] = []
    name, _form = lv.archive_reference(rec.get("archive"))
    if name:
        names.append(os.path.basename(name))
    names.extend(rec["name"] + ext for ext in RAW_EXTS)
    if archive_dir:
        for n in names:
            if n in entries:
                return "archive", os.path.join(archive_dir, n)
    if state_raw:
        for ext in RAW_EXTS:
            p = os.path.join(state_raw, rec["name"] + ext)
            if os.path.lexists(p):
                return "state", p
    return None, None


def no_raw_detail(rec: dict) -> str:
    name, form = lv.archive_reference(rec.get("archive"))
    if form == "unarchivable":
        return "archive: unarchivable (the raw was never kept)"
    if name:
        return "archive: names %s, which the archive does not hold" % os.path.basename(name)
    return "no archive: field and no revision-named raw"


def _report(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _tail(data: bytes, n: int = 200) -> str:
    return data.decode("utf-8", "replace").strip().replace("\n", " ")[-n:]


def sidecar_fetch_report(archive_dir: Optional[str], rev: str, work: str) -> Optional[str]:
    """The `fetch` object of the revision's frozen sidecar `<rev>.report.json`, written to the work dir for
    `--fetch-report` (what only the fetch saw: the charset, the egress); None without one."""
    if not archive_dir:
        return None
    side = _report(os.path.join(archive_dir, rev + ".report.json"))
    fetch = side.get("fetch")
    if not isinstance(fetch, dict) or not fetch:
        return None
    out = os.path.join(work, "fetch.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(fetch, fh)
    return out


def convert_html(path: str, rec: dict, timeout: float, work: str, archive_dir: Optional[str]) -> Converted:
    url = rec.get("url") or ""
    try:
        kind = resolve.resolve(url).kind if url else "page"
    except resolve.InvalidURL:
        kind = "page"
    raw_kind = (rec.get("src_nested", {}).get("capture", {}).get("raw_kind") or "").strip()
    if raw_kind not in CAPTURE_TEXT_RAW_KINDS:
        raw_kind = "response"
    out, rep = os.path.join(work, "capture.md"), os.path.join(work, "capture-text.json")
    argv = [sys.executable, CAPTURE_TEXT, path, "--url", url or "about:blank", "--out", out, "--report", rep,
            "--raw-kind", raw_kind, "--kind", kind, "--timeout", "%g" % timeout]
    fr = sidecar_fetch_report(archive_dir, rec["name"], work)
    if fr:
        if (_report(fr).get("egress") or "") == "external":
            argv[argv.index("--raw-kind") + 1] = "external"
        argv += ["--fetch-report", fr]
    res = proc.run(argv, timeout=timeout + OUTER_GRACE_S)
    report = _report(rep)
    tools = report.get("tools") if isinstance(report.get("tools"), dict) else None
    if res.timed_out or res.rc == 124:
        return Converted(None, "capture-text", None, NO_CONVERTER, "conversion exceeded %g s" % timeout, tools)
    if not res.started:
        return Converted(None, "capture-text", None, NO_CONVERTER, "capture-text did not start: %s" % _tail(res.stderr), tools)
    if res.rc == 0 and os.path.isfile(out):
        with open(out, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        return Converted(text, "capture-text:%s" % (report.get("converter") or "?"), report.get("title"), None, None, tools)
    ec, err = report.get("error_class"), str(report.get("error") or "")
    if ec == "invalid-utf8" or (ec == "convert" and err.startswith("cannot read raw")):
        return Converted(None, "capture-text", None, UNREADABLE, "%s: %s" % (ec, err), tools)
    if ec == "convert" and ("no candidate produced text" in err or "empty conversion output" in err):
        # the raw holds no text at all: the body's lines are judged against an empty text
        return Converted("", "capture-text:none", report.get("title"), None, None, tools)
    if ec:
        return Converted(None, "capture-text", None, NO_CONVERTER, "capture-text %s: %s" % (ec, err), tools)
    return Converted(None, "capture-text", None, NO_CONVERTER, "capture-text exited %s: %s" % (res.rc, _tail(res.stderr)), tools)


def convert_pdf(path: str, timeout: float, work: str) -> Converted:
    exe = shutil.which("pdftotext")
    if not exe:
        return Converted(None, "pdftotext", None, NO_CONVERTER, "pdftotext (Poppler) is not installed", None)
    out = os.path.join(work, "pdf.txt")
    res = proc.run([exe, "-layout", path, out], timeout=timeout)
    if res.timed_out:
        return Converted(None, "pdftotext", None, NO_CONVERTER, "pdftotext exceeded %g s" % timeout, None)
    if not res.started:
        return Converted(None, "pdftotext", None, NO_CONVERTER, _tail(res.stderr), None)
    if res.rc != 0 or not os.path.isfile(out):
        return Converted(None, "pdftotext", None, UNREADABLE, "pdftotext exited %s: %s" % (res.rc, _tail(res.stderr)), None)
    with open(out, "rb") as fh:
        return Converted(fh.read().decode("utf-8", "replace"), "pdftotext", None, None, None, None)


def convert_site(path: str, timeout: float, work: str) -> Converted:
    out, rep = os.path.join(work, "site.md"), os.path.join(work, "site.json")
    res = proc.run([sys.executable, CAPTURE_SITE, "assemble", path, "--out", out, "--report", rep,
                    "--timeout", "%g" % timeout], timeout=timeout + OUTER_GRACE_S)
    if res.timed_out or res.rc == 124:
        return Converted(None, "site-assemble", None, NO_CONVERTER, "assembly exceeded %g s" % timeout, None)
    if res.rc == 0 and os.path.isfile(out):
        with open(out, "rb") as fh:
            return Converted(fh.read().decode("utf-8", "replace"), "site-assemble", None, None, None, None)
    report = _report(rep)
    return Converted(None, "site-assemble", None, NO_CONVERTER, "capture-site assemble exited %s: %s" % (
        res.rc, report.get("error") or _tail(res.stderr)), None)


def convert_text(path: str) -> Converted:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return Converted(None, "text", None, UNREADABLE, "cannot read: %s" % (exc.strerror or exc), None)
    try:
        return Converted(data.decode("utf-8"), "text", None, None, None, None)
    except UnicodeDecodeError as exc:
        return Converted(None, "text", None, UNREADABLE, "not UTF-8 at byte %d" % exc.start, None)


def convert_raw(path: str, rec: dict, timeout: float, work: str, archive_dir: Optional[str]) -> Converted:
    """The raw's text through the read-only converter of its kind, or the class that says why not."""
    if os.path.isdir(path):
        if path.endswith(".site"):
            return convert_site(path, timeout, work)
        return Converted(None, None, None, NO_CONVERTER, "a directory raw that is not a site bundle", None)
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
    except OSError as exc:
        return Converted(None, None, None, UNREADABLE, "cannot read: %s" % (exc.strerror or exc), None)
    if ext == ".pdf" or head == b"%PDF-":
        return convert_pdf(path, timeout, work)
    if ext in HTML_EXTS:
        return convert_html(path, rec, timeout, work, archive_dir)
    if ext == ".txt":
        return convert_text(path)
    return Converted(None, None, None, NO_CONVERTER, "no read-only converter for %s raws" % (ext or "extension-less"), None)


def titles_of(rec: dict, conv_title: Optional[str]) -> List[str]:
    src = rec.get("src") or {}
    return [t for t in (src.get("title"), src.get("video_title"), conv_title) if t]


def new_result(rec: dict, cited: set) -> dict:
    return {"revision": rec["name"], "class": None, "detail": None, "raw": None, "raw_store": None,
            "converter": None, "eligible_lines": None, "not_found": None, "share": None, "samples": [],
            "cited": rec["name"] in cited, "current": bool(rec.get("current")),
            "completeness": rec.get("completeness"), "source_type": rec.get("source_type"),
            "source_words": rec.get("body_words"), "converted_words": None, "seconds": None}


def audit_one(rec: dict, ctx: dict) -> Optional[dict]:
    """One revision's result; None when its source.md vanished or became unreadable (counted apart)."""
    t0 = time.monotonic()
    out = new_result(rec, ctx["cited"])
    store, path = find_raw(rec, ctx["archive_dir"], ctx["entries"], ctx["state_raw"])
    if path is None:
        out.update({"class": NO_RAW, "detail": no_raw_detail(rec)})
        return out
    out.update({"raw": os.path.basename(path), "raw_store": store})
    body = read_body(os.path.join(ctx["vault"], "Sources", rec["name"], "source.md"))
    if body is None:
        ctx["snapshot"]["unreadable"] += 1
        return None
    reason = unreadable_reason(path)
    if reason:
        out.update({"class": UNREADABLE, "detail": reason})
        return out
    # the body alone decides whether anything is required at all: no conversion for a body the rule
    # cannot probe (the converter's title can only take lines away)
    if check.source_presence(body, "", titles_of(rec, None)).eligible_count == 0:
        out.update({"class": NO_LINES, "detail": "no line of the body is eligible", "eligible_lines": 0})
        return out
    work = tempfile.mkdtemp(prefix="rev-", dir=ctx["tmp"])
    try:
        conv = convert_raw(path, rec, ctx["timeout"], work, ctx["archive_dir"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out["converter"] = conv.converter
    if conv.tools and not ctx["tools"]:
        ctx["tools"] = conv.tools
    if conv.klass:
        out.update({"class": conv.klass, "detail": conv.detail})
        out["seconds"] = round(time.monotonic() - t0, 2)
        return out
    pres = check.source_presence(body, conv.text or "", titles_of(rec, conv.title))
    out["converted_words"] = len((conv.text or "").split())
    out["eligible_lines"] = pres.eligible_count
    if pres.eligible_count == 0:
        out.update({"class": NO_LINES, "detail": "the only eligible lines equal the page's title"})
    elif not pres.not_found:
        out.update({"class": MATCH, "not_found": 0, "share": 0.0})
    else:
        n = len(pres.not_found)
        out.update({"class": MISMATCH, "not_found": n, "share": round(n / pres.eligible_count, 3),
                    "samples": [line[:SAMPLE_CHARS] for line in pres.not_found[:SAMPLES]]})
        if not conv.text:
            out["detail"] = "the raw converts to no text"
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out


def cited_revisions(vault: str, folders: List[dict], snapshot: dict) -> Tuple[set, bool]:
    """The revisions a synthesis note links, a collection lists in `## Items` or a wiki page names in
    `based_on` — the relations scan of lib_vault (vault store §4: `integrated` and `filed`, any status).
    Second value: whether the scan saw every document (an unlisted folder or unreadable file = False)."""
    existing = {r["name"] for r in folders}
    before = snapshot["unreadable"]
    cited: set = set()
    syn = lv.scan_synthesis(vault, snapshot)
    for n in syn["notes"]:
        cited.update(rev for rev in n["links"] if rev in existing)
    cols = lv.scan_collections(vault, snapshot, existing={
        r["name"]: {f for f, has in (("extract", r["has_extract"]), ("source", r["has_source"])) if has} for r in folders})
    for col in cols["notes"]:
        cited.update(it["rev"] for it in col.get("items", []) if it.get("rev") in existing)
    pages = lv.scan_wiki(vault, snapshot)
    for page in pages["notes"]:
        cited.update(e["rev"] for e in page.get("based_on", []) if e.get("rev") in existing)
    complete = snapshot["unreadable"] == before and not cols.get("unlisted") and not pages.get("unlisted")
    return cited, complete


def share_summary(results: List[dict]) -> dict:
    shares = sorted(r["share"] for r in results if r["class"] == MISMATCH)
    buckets = []
    lower = 0.0
    for upper, label in SHARE_BUCKETS:
        buckets.append({"range": label, "count": sum(1 for s in shares if lower < s <= upper)})
        lower = upper
    if not shares:
        return {"count": 0, "min": None, "median": None, "max": None, "buckets": buckets}
    mid = len(shares) // 2
    median = shares[mid] if len(shares) % 2 else round((shares[mid - 1] + shares[mid]) / 2, 3)
    return {"count": len(shares), "min": shares[0], "median": median, "max": shares[-1], "buckets": buckets}


def inside(path: str, roots: List[Optional[str]]) -> Optional[str]:
    real = os.path.realpath(path)
    for root in roots:
        if not root:
            continue
        r = os.path.realpath(root)
        if real == r or real.startswith(r.rstrip("/") + "/"):
            return root
    return None


def print_human(doc: dict) -> None:
    pop = doc["population"]
    print("Text mismatch candidates — %s (%s)" % (doc.get("instance") or "-", doc["vault"]))
    scope = ("every url revision with a raw" if pop["scope"] == "all-url" else
             "current url revisions without a completeness record, bookmarks excluded")
    print("Population: %d %s%s" % (pop["revisions"], scope, " — cited only" if pop["cited_only"] else ""))
    print("Rule: every eligible line of source.md must occur in the text its raw converts to now "
          "(capture layer A17); nothing fetched, nothing written.")
    print()
    for k in CLASSES:
        line = "  %-34s %5d" % (k, doc["counts"][k])
        if k == MISMATCH and doc["mismatch_share"]["count"]:
            line += "   share: " + " · ".join("%s %d" % (b["range"], b["count"]) for b in doc["mismatch_share"]["buckets"])
        print(line)
    mism = sorted((r for r in doc["results"] if r["class"] == MISMATCH), key=lambda r: (-r["share"], r["revision"]))
    if mism:
        print()
        print("Mismatch candidates (share of eligible lines not found, highest first):")
        for r in mism:
            print("  %s  %.2f  %d/%d  %s%s  raw %s" % (r["revision"], r["share"], r["not_found"], r["eligible_lines"],
                                                    r["converter"] or "-", "  cited" if r["cited"] else "", r["raw"]))
            for s in r["samples"]:
                print("      not found: %s" % s)
    other = [r for r in doc["results"] if r["class"] in (NO_CONVERTER, UNREADABLE)]
    if other:
        print()
        print("Not probed (converter or raw):")
        for r in sorted(other, key=lambda r: (r["class"], r["revision"])):
            print("  %s  %s — %s" % (r["revision"], r["class"], r["detail"]))
    if not doc["snapshot"]["complete"]:
        print()
        print("warning: the snapshot is incomplete (%d unreadable file(s)%s)" % (
            doc["snapshot"]["unreadable"], "; the citation scan missed documents" if not doc["snapshot"]["cited_scan_complete"] else ""))
    print()
    print("A mismatch is a candidate, not a finding: converter drift, old selection rules and text an old capture")
    print("added itself produce one too. Look at the samples and the raw before `mnemon add --url <url> --recapture`.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mnemon audit text-mismatch",
        description="Text mismatch candidates: revisions whose source.md text does not occur in the text their "
                    "retained raw converts to now (read-only; capture layer A17).",
        epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="instance config file (default: the engine's resolved config)")
    ap.add_argument("--all-url", action="store_true",
                    help="every url revision that has a raw (current or superseded, any completeness), "
                         "not only the pre-contract ones")
    ap.add_argument("--cited", action="store_true",
                    help="only revisions a collection item, a wiki page's based_on or a synthesis note cites")
    ap.add_argument("--json", action="store_true", help="the %s document on stdout" % SCHEMA)
    ap.add_argument("--timeout", type=float, default=None,
                    help="seconds for one raw's conversion (default: the instance's convert_timeout, else %g)" % DEFAULT_TIMEOUT)
    args = ap.parse_args(argv)

    try:
        cfg = lib_config.load_config(args.config)
    except lib_config.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    vault = cfg.get("VAULT_PATH") or ""
    archive_dir = cfg.get("ARCHIVE_DIR") or None
    state_dir = cfg.get("MNEMON_VAULT_STATE_DIR") or (lv.vl.vault_state_dir(vault) if vault else None)
    timeout = args.timeout
    if timeout is None:
        try:
            timeout = float(cfg.get("CONVERT_TIMEOUT") or DEFAULT_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_TIMEOUT
    if timeout <= 0:
        print("mnemon audit text-mismatch: --timeout must be positive", file=sys.stderr)
        return 2
    if not vault:
        print("mnemon audit text-mismatch: the configuration names no vault_path", file=sys.stderr)
        return 2

    snapshot = {"unreadable": 0, "complete": True, "folders_listed": 0, "folders_scanned": 0, "sources_unlisted": None}
    folders, _graph = lv.scan_sources(vault, snapshot)
    if snapshot.get("sources_unlisted"):
        print("mnemon audit text-mismatch: cannot list Sources/ (%s)" % snapshot["sources_unlisted"], file=sys.stderr)
        return 1
    cited, cited_complete = cited_revisions(vault, folders, snapshot)

    entries: set = set()
    if archive_dir and os.path.isdir(archive_dir):
        try:
            entries = {n for n in os.listdir(archive_dir) if not n.startswith(".")}
        except OSError:
            snapshot["unreadable"] += 1
            snapshot["complete"] = False
    state_raw = os.path.join(state_dir, "raw") if state_dir else None
    if state_raw and not os.path.isdir(state_raw):
        state_raw = None

    pop = []
    for r in folders:
        if not r["has_source"] or r["source_state"] == "unreadable" or r["origin"] != "url":
            continue
        if args.all_url:
            if find_raw(r, archive_dir, entries, state_raw)[1] is not None:
                pop.append(r)
        elif r["current"] and r["completeness"] is None and r["effective_tier"] != "bookmark":
            pop.append(r)
    if args.cited:
        pop = [r for r in pop if r["name"] in cited]

    proc.install_term_handler()
    tmp = tempfile.mkdtemp(prefix="mnemon-text-audit-")
    where = inside(tmp, [vault, archive_dir, state_dir])
    if where:
        shutil.rmtree(tmp, ignore_errors=True)
        print("mnemon audit text-mismatch: the temp dir %s is inside %s; set TMPDIR elsewhere (the audit writes "
              "nothing there)" % (tmp, where), file=sys.stderr)
        return 2
    ctx = {"vault": vault, "archive_dir": archive_dir if archive_dir and os.path.isdir(archive_dir) else None,
           "entries": entries, "state_raw": state_raw, "timeout": timeout, "tmp": tmp, "cited": cited,
           "snapshot": snapshot, "tools": None}
    results: List[dict] = []
    progress = sys.stderr.isatty() and not args.json
    try:
        for i, rec in enumerate(pop, 1):
            if progress:
                sys.stderr.write("\r[%d/%d] %s " % (i, len(pop), rec["name"]))
                sys.stderr.flush()
            res = audit_one(rec, ctx)
            if res is not None:
                results.append(res)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if progress:
            sys.stderr.write("\n")
    if snapshot["unreadable"] or not cited_complete:
        snapshot["complete"] = False

    counts: Dict[str, int] = {k: 0 for k in CLASSES}
    for r in results:
        counts[r["class"]] += 1
    doc = {
        "schema": SCHEMA,
        "instance": cfg.get("MNEMON_INSTANCE") or None,
        "vault": vault,
        "archive_dir": archive_dir,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "population": {"scope": "all-url" if args.all_url else "pre-contract", "cited_only": bool(args.cited),
                       "revisions": len(results)},
        "timeout_s": timeout,
        "tools": ctx["tools"],
        "counts": counts,
        "mismatch_share": share_summary(results),
        "results": results,
        "snapshot": {"unreadable": snapshot["unreadable"], "complete": snapshot["complete"],
                     "cited_scan_complete": cited_complete},
    }
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        print_human(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
