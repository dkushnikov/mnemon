#!/usr/bin/env python3
"""mnemon-triage.py — the bookmark queue of a vault (phase 3-capture, capture layer §13 B4).

    mnemon triage list [--status bookmarked|dismissed|promoted|all] [--json]
    mnemon triage dismiss <revision> [--why "<reason>"]
    mnemon triage restore <revision>
    mnemon triage promote <revision> [<mnemon add flags>…]

`mnemon triage <input-file> …` (the URL-list triage, bin/triage-urls.py) is untouched: the
dispatcher routes the four verbs here and everything else there.

The queue is every **current** revision of effective tier `bookmark` (`tier: bookmark` in source.md,
or the legacy placeholder `status: bookmark`), read through bin/lib_vault.py — the same scan the
doctor and `status --brief` use. Statuses are derived, never stored (vault store §4):

- `bookmarked` — current, no judgement;
- `dismissed` — current, the judgement is stored: in `extract.md` `status:` when the revision has
  one, else in `Sources/<revision>/triage.md` (the carrier of a judgement for a revision without
  extract.md — O6); `source.md` is never touched;
- `promoted` — a bookmark with a full descendant in the supersedes graph (`promoted(rev)`); such a
  bookmark is superseded, so it is listed only on request.

`dismissed` is a judgement about the **resource**, carried by its latest revision: dismissing a
superseded revision is refused naming the head. A later full capture (promotion) supersedes the
carrier and the judgement lapses with it. `dismiss` and `restore` write under the vault lock (the
dispatcher runs them through bin/vault-lock.py); `promote` execs `mnemon add`, which takes the lock
itself — a full capture of a bookmarked resource supersedes the card (B2).

Exit codes: 0 · 2 usage, an unknown or superseded revision, a revision that is not a bookmark ·
1 a write failed. Stdlib only, python 3.9.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from typing import List, Optional

BIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lv = _load_module("lib_vault", "lib_vault.py")

STATUSES = ("bookmarked", "dismissed", "promoted")


def die(msg: str, code: int = 2) -> "NoReturn":   # type: ignore[name-defined]
    print("mnemon triage: %s" % msg, file=sys.stderr)
    sys.exit(code)


def quoted(value: str) -> str:
    """A double-quoted scalar of the serialization subset (capture contracts §4.4)."""
    v = "".join(ch for ch in value if ch >= " " and ch != "\x7f")
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def one_line(value: str, limit: int = 500) -> str:
    return " ".join(value.split())[:limit]


# ---------------------------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------------------------

def queue(scan: dict) -> List[dict]:
    """Every bookmark-tier revision with its derived status (bookmarked · dismissed · promoted)."""
    folders = scan["folders"]
    successors = scan["graph"]["successors"]
    tier_of = {r["name"]: r["effective_tier"] for r in folders}

    def promoted(name: str) -> bool:
        seen: set = set()
        todo = list(successors.get(name, []))
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            if tier_of.get(n, "full") == "full":
                return True
            todo.extend(successors.get(n, []))
        return False

    rows: List[dict] = []
    for r in folders:
        if not r["has_source"] or r["effective_tier"] != "bookmark":
            continue
        if r["current"]:
            status = "dismissed" if r["judgement"] == "dismissed" else "bookmarked"
        elif promoted(r["name"]):
            status = "promoted"
        else:
            continue   # a superseded card without a full descendant (refreshed, then re-bookmarked): not a queue entry
        src = r["src"]
        when = (r["triage"].get("dismissed") if r["has_triage"] else None) or r["ex"].get("dismissed") or None
        why = (r["triage"].get("why") if r["has_triage"] else None) or r["ex"].get("dismissed_why") or None
        rows.append({
            "revision": r["name"], "status": status,
            "captured": src.get("captured") or r["name"][:10],
            "title": src.get("title") or r["ex"].get("title") or "",
            "url": src.get("url") or "", "canonical_url": src.get("canonical_url") or "",
            "origin": src.get("origin") or "", "context": src.get("context") or "",
            "has_extract": r["has_extract"], "legacy": r["tier"] is None,
            "dismissed": when if status == "dismissed" else None,
            "why": why if status == "dismissed" else None,
            "superseded_by": sorted(successors.get(r["name"], [])),
        })
    rows.sort(key=lambda x: (x["captured"], x["revision"]), reverse=True)
    return rows


def cmd_list(args, scan: dict) -> int:
    rows = queue(scan)
    want = args.status
    if want != "all":
        rows = [x for x in rows if x["status"] == want]
    if args.json:
        counts = {s: sum(1 for x in queue(scan) if x["status"] == s) for s in STATUSES}
        print(json.dumps({"schema": "mnemon-triage/1", "vault": scan["vault"], "status": want,
                          "counts": counts, "count": len(rows), "items": rows}, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("no %s bookmarks" % ("" if want == "all" else want).strip() if want != "all" else "no bookmarks")
        return 0
    for x in rows:
        what = x["url"] or ("text" if x["origin"] in ("text", "idea") else x["origin"] or "?")
        line = "%s\t%s\t%s\t%s\t%s" % (x["revision"], x["status"], x["captured"], x["title"] or "(untitled)", what)
        if x["status"] == "dismissed" and (x["dismissed"] or x["why"]):
            line += "\t%s%s" % (x["dismissed"] or "", (" — " + x["why"]) if x["why"] else "")
        print(line)
    return 0


# ---------------------------------------------------------------------------------------------
# The judgement
# ---------------------------------------------------------------------------------------------

def resolve(scan: dict, revision: str) -> dict:
    rec = scan["by_name"].get(revision)
    if rec is None or not rec["has_source"]:
        die("no revision named %r in %s/Sources/" % (revision, scan["vault"]))
    if rec["effective_tier"] != "bookmark":
        die("Sources/%s/ is not a bookmark (tier %s) — the judgement about a full extract is its `status:` "
            "(dismissed), a vault operation; triage handles bookmarks" % (revision, rec["effective_tier"]))
    if not rec["current"]:
        heads = sorted(scan["graph"]["successors"].get(revision, []))
        die("Sources/%s/ is superseded by %s — a judgement is about the resource and goes on its latest revision"
            % (revision, ", ".join(heads) or "a later revision"))
    return rec


def atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".triage-", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def edit_extract_status(path: str, status: str, today: str, why: Optional[str]) -> None:
    """Rewrite the `status:` line of extract.md (the curation subset, vault store §3.2) and keep or
    drop the `dismissed:` / `dismissed_why:` lines after it; every other byte stays."""
    data = open(path, "rb").read()
    text = data.decode("utf-8", "surrogateescape")
    if not text.startswith("---\n"):
        die("Sources/%s: extract.md has no frontmatter — not touched" % os.path.basename(os.path.dirname(path)), 1)
    end = text.find("\n---\n", 4)
    if end < 0:
        die("Sources/%s: extract.md has no closing --- — not touched" % os.path.basename(os.path.dirname(path)), 1)
    header = text[4:end].split("\n")
    out: List[str] = []
    placed = False
    for line in header:
        if re.match(r"^dismissed(_why)?:", line):
            continue
        if re.match(r"^status:", line):
            out.append("status: %s" % status)
            if status == "dismissed":
                out.append("dismissed: %s" % today)
                if why:
                    out.append("dismissed_why: %s" % quoted(one_line(why)))
            placed = True
            continue
        out.append(line)
    if not placed:
        out.append("status: %s" % status)
        if status == "dismissed":
            out.append("dismissed: %s" % today)
            if why:
                out.append("dismissed_why: %s" % quoted(one_line(why)))
    new = "---\n" + "\n".join(out) + text[end:]
    atomic_write(path, new.encode("utf-8", "surrogateescape"))


def cmd_dismiss(args, scan: dict) -> int:
    rec = resolve(scan, args.revision)
    folder = os.path.join(scan["vault"], "Sources", rec["name"])
    if rec["judgement"] == "dismissed":
        print("Sources/%s/ is already dismissed" % rec["name"])
        return 0
    today = args.today or datetime.date.today().isoformat()
    if rec["has_extract"]:
        edit_extract_status(os.path.join(folder, "extract.md"), "dismissed", today, args.why)
        where = "extract.md (status: dismissed)"
    else:
        lines = ["---", "type: triage", "revision: %s" % quoted(rec["name"]), "status: dismissed", "dismissed: %s" % today]
        if args.why:
            lines.append("why: %s" % quoted(one_line(args.why)))
        lines += ["---", "", "The judgement about this resource (phase 3-capture): dismissed on %s%s. "
                  "`mnemon triage restore %s` reverses it; a full capture of the resource supersedes this card and the judgement with it."
                  % (today, (" — " + one_line(args.why)) if args.why else "", rec["name"]), ""]
        atomic_write(os.path.join(folder, "triage.md"), "\n".join(lines).encode("utf-8"))
        where = "triage.md"
    print("dismissed Sources/%s/ — %s%s" % (rec["name"], where, (" (" + one_line(args.why) + ")") if args.why else ""))
    return 0


def cmd_restore(args, scan: dict) -> int:
    rec = resolve(scan, args.revision)
    folder = os.path.join(scan["vault"], "Sources", rec["name"])
    if rec["judgement"] != "dismissed":
        print("Sources/%s/ is not dismissed" % rec["name"])
        return 0
    if rec["has_extract"]:
        edit_extract_status(os.path.join(folder, "extract.md"), "extracted", args.today or datetime.date.today().isoformat(), None)
        where = "extract.md (status: extracted)"
    else:
        os.unlink(os.path.join(folder, "triage.md"))
        where = "triage.md removed"
    print("restored Sources/%s/ — %s" % (rec["name"], where))
    return 0


# ---------------------------------------------------------------------------------------------
# Promotion: a full capture of the resource through `mnemon add` (B2 makes it supersede the card)
# ---------------------------------------------------------------------------------------------

def body_of(path: str) -> bytes:
    data = open(path, "rb").read()
    if not data.startswith(b"---\n"):
        return data
    i = data.find(b"\n---\n", 3)
    return data[i + 5:] if i >= 0 else b""


def cmd_promote(args, scan: dict) -> int:
    rec = resolve(scan, args.revision)
    src = rec["src"]
    folder = os.path.join(scan["vault"], "Sources", rec["name"])
    mnemon = args.mnemon or os.path.join(BIN_DIR, "mnemon")
    if not os.access(mnemon, os.X_OK):
        die("the dispatcher %s is not executable" % mnemon, 1)
    extra = list(args.add_args)
    if extra and extra[0] == "--":
        extra = extra[1:]
    # `--dry-run` after the revision is the triage's own: print the mnemon add command, run nothing
    # (argparse hands everything after the revision to add_args)
    dry_run = args.dry_run or "--dry-run" in extra
    extra = [a for a in extra if a != "--dry-run"]
    argv = [mnemon, "add"]
    origin = (src.get("origin") or "").strip()
    url = src.get("url") or ""
    if origin in ("text", "idea"):
        # the card's body is the text itself: the same bytes make the same resource (B1)
        tmp = tempfile.NamedTemporaryFile(prefix="mnemon-promote-", suffix=".md", delete=False)
        tmp.write(body_of(os.path.join(folder, "source.md")))
        tmp.close()
        argv += ["--origin", origin, "--file-text", tmp.name]
        if url:
            argv += ["--canonical-url", url]
    elif url:
        if origin and origin != "url":
            argv += ["--origin", origin]
        argv += ["--url", url]
    else:
        die("Sources/%s/ has neither a url nor a text to re-capture" % rec["name"])
    for key, flag in (("title", "--title"), ("author", "--author"), ("context", "--context"), ("source_type", "--source-type")):
        value = (src.get(key) or "").strip()
        if value and flag not in extra:
            argv += [flag, value]
    argv += extra
    if dry_run:
        print(" ".join(argv))
        return 0
    print("promoting Sources/%s/: %s" % (rec["name"], " ".join(argv[1:])), file=sys.stderr)
    sys.stdout.flush()
    os.execv(mnemon, argv)
    return 1   # unreachable


# ---------------------------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="The bookmark queue of a Mnemon vault: list · dismiss · restore · promote")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--today", default=None, help="fix the clock (YYYY-MM-DD; tests)")
    ap.add_argument("--mnemon", default=None, help="the dispatcher to exec for promote (default: bin/mnemon beside this script)")
    sub = ap.add_subparsers(dest="verb")
    p = sub.add_parser("list", help="the queue (default: bookmarked)")
    p.add_argument("--status", default="bookmarked", choices=STATUSES + ("all",))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("dismiss", help="store the judgement about the resource on its latest revision")
    p.add_argument("revision")
    p.add_argument("--why", default=None)
    p = sub.add_parser("restore", help="remove the judgement")
    p.add_argument("revision")
    p = sub.add_parser("promote", help="capture the resource in full (mnemon add; the new revision supersedes the card)")
    p.add_argument("revision")
    p.add_argument("--dry-run", action="store_true", help="print the mnemon add command, run nothing")
    p.add_argument("add_args", nargs=argparse.REMAINDER, help="flags passed on to mnemon add (after --)")
    args = ap.parse_args(argv[1:])
    if not args.verb:
        ap.print_help()
        return 2
    vault = os.path.abspath(args.vault)
    if not os.path.isdir(os.path.join(vault, "Sources")):
        die("no Sources/ at %s" % vault)
    today = datetime.date.fromisoformat(args.today) if args.today else None
    scan = lv.scan_vault(vault, state_dir=args.state_dir, today=today)
    return {"list": cmd_list, "dismiss": cmd_dismiss, "restore": cmd_restore, "promote": cmd_promote}[args.verb](args, scan)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
