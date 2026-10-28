#!/usr/bin/env python3
"""mnemon-triage.py — the bookmark queue of a vault (phase 3-capture, capture layer §13 B4).

    mnemon triage list [--status bookmarked|dismissed|promoted|all] [--json]
    mnemon triage dismiss <revision> [--why "<reason>"]
    mnemon triage restore <revision>
    mnemon triage promote <revision> [<mnemon add flags>…]

The dispatcher routes exactly these four verbs here; the URL-list triage spike (`triage <file>`,
bin/triage-urls.py) was retired 2026-10-28 (GH #29) — anything else is an unknown subcommand.

The queue is every **current** revision of effective tier `bookmark` (`tier: bookmark` in source.md,
or the legacy placeholder `status: bookmark`), read through bin/lib_vault.py — the same scan the
doctor and `status --brief` use. Statuses are derived, never stored (vault store §4):

- `bookmarked` — current, no judgement;
- `dismissed` — current, the judgement is stored: in `extract.md` `status:` when the revision has
  one, else in `Sources/<revision>/triage.md` (the carrier of a judgement for a card without
  extract.md — O6); `source.md` is never touched;
- `promoted` — a bookmark with a full descendant in the supersedes graph (`promoted(rev)`); such a
  bookmark is superseded, so it is listed only on request.

`dismissed` is a judgement about the **resource**, carried by its latest revision **whatever its
tier** (O6, GH #30): `dismiss`/`restore` take a full revision too — the judgement goes into its
extract.md (`status: dismissed` + `dismissed_from`), and `list --status dismissed|all` shows it
beside the cards (a `tier` column in --json). A full revision without extract.md is an unfinished
capture, not a carrier: dismissing it is refused (`mnemon add --resume` first). Dismissing a
superseded revision is refused naming the head. A later full capture (promotion) supersedes the
carrier and the judgement lapses with it. `promote` is for bookmark cards only. `dismiss` and `restore` write under the vault lock (the
dispatcher runs them through bin/vault-lock.py); `promote` execs `mnemon add`, which takes the lock
itself — a full capture of a bookmarked resource supersedes the card (B2).

Exit codes: 0 · 2 usage, an unknown or superseded revision, a revision that is not a bookmark ·
1 a write failed. Stdlib only, python 3.9.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
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
    rows: List[dict] = []
    for r in folders:
        if not r["has_source"]:
            continue
        if r["effective_tier"] != "bookmark":
            # a full revision is in the listing only as a carrier of the judgement (GH #30)
            if r["current"] and r["judgement"] == "dismissed":
                status = "dismissed"
            else:
                continue
        elif r["current"]:
            status = "dismissed" if r["judgement"] == "dismissed" else "bookmarked"
        elif lv.has_full_descendant(scan, r["name"]):
            status = "promoted"
        else:
            continue   # a superseded card without a full descendant (refreshed, then re-bookmarked): not a queue entry
        src = r["src"]
        when = (r["triage"].get("dismissed") if r["has_triage"] else None) or r["ex"].get("dismissed") or None
        why = (r["triage"].get("why") if r["has_triage"] else None) or r["ex"].get("dismissed_why") or None
        rows.append({
            "revision": r["name"], "status": status, "tier": r["effective_tier"],
            "captured": src.get("captured") or r["name"][:10],
            "title": src.get("title") or r["ex"].get("title") or "",
            "url": src.get("url") or "", "canonical_url": src.get("canonical_url") or "",
            "origin": src.get("origin") or "", "context": src.get("context") or "",
            "has_extract": r["has_extract"], "legacy": not (src.get("tier") or "").strip(),
            "dismissed": when if status == "dismissed" else None,
            "why": why if status == "dismissed" else None,
            "superseded_by": sorted(successors.get(r["name"], [])),
        })
    rows.sort(key=lambda x: (x["captured"], x["revision"]), reverse=True)
    return rows


def cmd_list(args, scan: dict) -> int:
    everything = queue(scan)
    want = args.status
    rows = everything if want == "all" else [x for x in everything if x["status"] == want]
    if args.json:
        counts = {s: sum(1 for x in everything if x["status"] == s) for s in STATUSES}
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

def resolve(scan: dict, revision: str, verb: str = "dismiss") -> dict:
    """The current revision named, as the verb needs it: `promote` wants a bookmark card; `dismiss` and
    `restore` take any latest revision — a full one carries the judgement in its extract.md (GH #30),
    so a full revision without extract.md (an unfinished capture) is refused."""
    rec = scan["by_name"].get(revision)
    if rec is None or not rec["has_source"]:
        die("no revision named %r in %s/Sources/" % (revision, scan["vault"]))
    if verb == "promote" and rec["effective_tier"] != "bookmark":
        die("Sources/%s/ is not a bookmark (tier %s) — promote captures a card in full; a full revision is "
            "re-captured with `mnemon add --recapture`" % (revision, rec["effective_tier"]))
    if verb != "promote" and rec["effective_tier"] != "bookmark" and not rec["has_extract"]:
        die("Sources/%s/ is a full capture without extract.md (unfinished) — the judgement about a full "
            "capture goes into its extract.md: finish it first (mnemon add --resume %s), or leave it"
            % (revision, revision))
    if not rec["current"]:
        heads = lv.current_heads(scan, revision)
        die("Sources/%s/ is superseded — a judgement is about the resource and goes on its latest revision%s"
            % (revision, (": " + ", ".join(heads)) if heads else " (none of its successors is current: a cycle?)"))
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


def extract_header(path: str):
    """(text, end) of an extract.md whose frontmatter the triage can edit; exit 1 otherwise."""
    rev = os.path.basename(os.path.dirname(path))
    data = open(path, "rb").read()
    text = data.decode("utf-8", "surrogateescape")
    if text.startswith("---\r\n"):
        die("Sources/%s: extract.md uses CRLF line endings — the triage edits LF frontmatter only; not touched" % rev, 1)
    if not text.startswith("---\n"):
        die("Sources/%s: extract.md has no frontmatter — not touched" % rev, 1)
    end = text.find("\n---\n", 4)
    if end < 0:
        die("Sources/%s: extract.md has no closing --- — not touched" % rev, 1)
    return text, end


def dismiss_extract(path: str, today: str, why: Optional[str]) -> None:
    """Set `status: dismissed` in extract.md (the curation subset, vault store §3.2) with
    `dismissed:`, `dismissed_why:` and `dismissed_from:` — the word the status had (empty when the
    file had no status line), so `restore` can put it back; every other byte stays."""
    text, end = extract_header(path)
    header = text[4:end].split("\n")
    out: List[str] = []
    prior = None
    placed = False
    for line in header:
        if re.match(r"^dismissed(_why|_from)?:", line):
            continue
        m = re.match(r"^status:(.*)$", line)
        if m and not placed:
            prior = m.group(1).strip()
            out.append("status: dismissed")
            out.append("dismissed: %s" % today)
            if why:
                out.append("dismissed_why: %s" % quoted(one_line(why)))
            out.append("dismissed_from: %s" % quoted(prior))
            placed = True
            continue
        out.append(line)
    if not placed:
        out += ["status: dismissed", "dismissed: %s" % today]
        if why:
            out.append("dismissed_why: %s" % quoted(one_line(why)))
        out.append('dismissed_from: ""')
    new = "---\n" + "\n".join(out) + text[end:]
    atomic_write(path, new.encode("utf-8", "surrogateescape"))


def restore_extract(path: str) -> str:
    """The reverse of dismiss_extract: the status word recorded in `dismissed_from:` comes back (a
    file that had no status line loses the appended one); the three dismissed lines go. Returns
    the restored word (or '' when the line was dropped)."""
    text, end = extract_header(path)
    header = text[4:end].split("\n")
    prior = "extracted"
    for line in header:
        m = re.match(r"^dismissed_from:(.*)$", line)
        if m:
            v = m.group(1).strip()
            prior = v[1:-1].replace('\\"', '"').replace("\\\\", "\\") if len(v) >= 2 and v[0] == '"' and v[-1] == '"' else v
            break
    out: List[str] = []
    for line in header:
        if re.match(r"^dismissed(_why|_from)?:", line):
            continue
        if re.match(r"^status:", line):
            if prior:
                out.append("status: %s" % prior)
            continue
        out.append(line)
    new = "---\n" + "\n".join(out) + text[end:]
    atomic_write(path, new.encode("utf-8", "surrogateescape"))
    return prior


def cmd_dismiss(args, scan: dict) -> int:
    rec = resolve(scan, args.revision, "dismiss")
    folder = os.path.join(scan["vault"], "Sources", rec["name"])
    if rec["judgement"] == "dismissed":
        print("Sources/%s/ is already dismissed" % rec["name"])
        return 0
    today = args.today or datetime.date.today().isoformat()
    if rec["has_extract"]:
        dismiss_extract(os.path.join(folder, "extract.md"), today, args.why)
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
    rec = resolve(scan, args.revision, "restore")
    folder = os.path.join(scan["vault"], "Sources", rec["name"])
    if rec["judgement"] != "dismissed":
        print("Sources/%s/ is not dismissed" % rec["name"])
        return 0
    if rec["has_extract"]:
        prior = restore_extract(os.path.join(folder, "extract.md"))
        where = "extract.md (status: %s)" % prior if prior else "extract.md (the appended status line dropped)"
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
    rec = resolve(scan, args.revision, "promote")
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
    stdin_bytes: Optional[bytes] = None
    if origin in ("text", "idea"):
        # the card's body is the text itself: the same bytes make the same resource (B1) — verified
        # against the card's own capture.text_sha256 first (an edited card is integrity-error, never
        # promoted as an unrelated resource), then handed to `mnemon add` on stdin (no file on disk)
        body = body_of(os.path.join(folder, "source.md"))
        want = rec["text_sha256"]
        if want and hashlib.sha256(body).hexdigest() != want:
            die("integrity-error: Sources/%s/source.md body differs from its capture.text_sha256 (edited?) — not promoted" % rec["name"], 1)
        stdin_bytes = body
        argv += ["--origin", origin]
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
        if value and not any(a == flag or a.startswith(flag + "=") for a in extra):
            argv += [flag, value]
    argv += extra
    if dry_run:
        print(" ".join(argv) + (" < <the card's text on stdin>" if stdin_bytes is not None else ""))
        return 0
    print("promoting Sources/%s/: %s" % (rec["name"], " ".join(argv[1:])), file=sys.stderr)
    sys.stdout.flush()
    if stdin_bytes is not None:
        # an unlinked temporary file on fd 0: the bytes reach the gateway, nothing stays in $TMPDIR
        fd, tmp = tempfile.mkstemp(prefix="mnemon-promote-")
        os.unlink(tmp)
        os.write(fd, stdin_bytes)
        os.lseek(fd, 0, os.SEEK_SET)
        os.dup2(fd, 0)
        os.close(fd)
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
    p = sub.add_parser("dismiss", help="store the judgement about the resource on its latest revision (a card or a full capture)")
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
