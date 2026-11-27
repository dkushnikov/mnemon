#!/usr/bin/env python3
"""score.py — the scoring helper of the live image gate (capture layer §21.13, the last row).

The gate's own thresholds, apart from the engine's comparison (§21.5), so that the shell stays thin:

  words   every word of the known text stands in the reading, with multiplicity (contract K3): each word of
          the truth stands in the reading at least as often as in the truth. A word is what the scorer of the
          measurement counted (§21.9; the OCR benchmark of 2026-11-26): `\\w+` over the NFC text, lowercased.
          The engine has no such threshold; this is the gate's.
  order   the two columns of the page in reading order: the last word of the left column comes before the
          first word of the right one. Each is located by an anchor — the shortest run of at least three
          words, ending with the last word (starting with the first), that stands once in the truth — and
          its first occurrence in the reading is taken.
  ai      the stand-alone `AI` read right: the truth's and the reading's runs of letters (case kept) are
          aligned (difflib, no junk heuristic), and a truth `AI` is right when it falls in an equal block.
          A real `Al` written `AI` (the model's error of §21.9) is not a wrong `AI`; it is counted apart.

Subcommands (they print numbers and verdicts only, never a text of the image; texts and lists of words go
to the JSON files in the snapshot):

  score.py words  --reading <file> --truth <file> [--truth <file>]… [--repeat <n>]
  score.py order  --reading <file> --truth <file> --left <a-b> --right <c-d>     (1-based truth line ranges)
  score.py ai     --reading <file> --truth <file>
  score.py judge  --seq <n> --name <item> --dir <item dir> --rc <exit> --checks <c1,c2,…> [--truth …]
                  [--repeat <n>] [--left <a-b> --right <c-d>] [--same-as <reading>] --out <item.json>
  score.py mark   --seq <n> --name <item> --status <"not run"|fail> --reason <why> --out <item.json>
  score.py probe-missing --probe <mnemon-ocr-probe/1 json> --token <lang:rus>
  score.py summary --run <run dir> --effort <level> --model <model> --max-usd <usd> --tessdata-prefix <set|unset>

Exit 0 for a pass (`mark` of an item not run, `summary` with no failure), 1 for a failure, 2 on a usage
error. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import difflib
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter

WORD = re.compile(r"\w+")
AI = "AI"
ANCHOR_MIN = 3


# --- tokens ---------------------------------------------------------------------------------------------------

def words(text: str):
    """The measurement's words: `\\w+` over the NFC text, lowercased."""
    return WORD.findall(unicodedata.normalize("NFC", text).lower())


def letter_runs(text: str):
    """Maximal runs of letters (`str.isalpha`), case kept."""
    out = []
    run = []
    for ch in unicodedata.normalize("NFC", text):
        if ch.isalpha():
            run.append(ch)
        elif run:
            out.append("".join(run))
            run = []
    if run:
        out.append("".join(run))
    return out


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def line_range(spec: str):
    a, _, b = spec.partition("-")
    a, b = int(a), int(b or a)
    if a < 1 or b < a:
        raise ValueError("a line range is <first>-<last>, 1-based: %r" % spec)
    return a, b


# --- the three checks -----------------------------------------------------------------------------------------

def words_check(reading: str, truths, repeat: int = 1) -> dict:
    """K3: each word of the truth (the truths joined, `repeat` times) stands in the reading at least as often."""
    need = Counter()
    for truth in truths:
        need.update(words(truth))
    for key in list(need):
        need[key] *= repeat
    have = Counter(words(reading))
    short = {w: need[w] - have[w] for w in need if have[w] < need[w]}
    return {"truth_words": sum(need.values()), "reading_words": sum(have.values()),
            "missing": sum(short.values()), "missing_words": sorted(short.items()),
            "pass": not short}


def _find(seq, sub):
    """The first index of the list `sub` inside the list `seq`, or -1; and the number of occurrences."""
    n = len(sub)
    hits = [i for i in range(len(seq) - n + 1) if seq[i:i + n] == sub]
    return (hits[0] if hits else -1), len(hits)


def _anchor(truth_tokens, column, at_end: bool):
    """The shortest run of at least ANCHOR_MIN words at the end (or start) of `column` that stands once in
    the truth; the whole column when no shorter run does."""
    for k in range(min(ANCHOR_MIN, len(column)), len(column) + 1):
        cand = column[-k:] if at_end else column[:k]
        if _find(truth_tokens, cand)[1] == 1:
            return cand
    return list(column)


def order_check(reading: str, truth: str, left, right) -> dict:
    """The last word of the left column (truth lines `left`) comes before the first word of the right column
    (truth lines `right`) in the reading."""
    lines = truth.split("\n")
    left_tokens = words("\n".join(lines[left[0] - 1:left[1]]))
    right_tokens = words("\n".join(lines[right[0] - 1:right[1]]))
    if not left_tokens or not right_tokens:
        raise ValueError("a column range holds no word")
    truth_tokens = words(truth)
    la = _anchor(truth_tokens, left_tokens, at_end=True)
    ra = _anchor(truth_tokens, right_tokens, at_end=False)
    tokens = words(reading)
    li, ln = _find(tokens, la)
    ri, rn = _find(tokens, ra)
    found = li >= 0 and ri >= 0
    return {"left_anchor_words": len(la), "right_anchor_words": len(ra),
            "left_found": ln, "right_found": rn,
            "left_end": (li + len(la) - 1) if li >= 0 else None, "right_start": ri if ri >= 0 else None,
            "pass": found and li + len(la) - 1 < ri}


def ai_check(reading: str, truth: str) -> dict:
    """The truth's stand-alone `AI` read right, by alignment of the runs of letters."""
    t = letter_runs(truth)
    r = letter_runs(reading)
    total = sum(1 for x in t if x == AI)
    right = 0
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, t, r, autojunk=False).get_opcodes():
        if tag == "equal":
            right += sum(1 for i in range(i1, i2) if t[i] == AI)
    return {"truth_ai": total, "right": right, "truth_al": sum(1 for x in t if x == "Al"),
            "reading_ai": sum(1 for x in r if x == AI), "reading_al": sum(1 for x in r if x == "Al"),
            "pass": total > 0 and right == total}


# --- the report -----------------------------------------------------------------------------------------------

def _get(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def facts(report) -> dict:
    """The numbers of one report (mnemon-capture-image/1) that the gate records — never a text."""
    return {
        "stage": _get(report, "stage"),
        "failure_class": _get(report, "failure", "class"),
        "failure_acquisition": _get(report, "failure", "acquisition"),
        "failure_reason": _get(report, "failure", "reason"),
        "validation_outcome": _get(report, "validation", "outcome"),
        "validation_reason": _get(report, "validation", "reason"),
        "width": _get(report, "validation", "width"),
        "height": _get(report, "validation", "height"),
        "orientation": _get(report, "validation", "orientation"),
        "verdict": _get(report, "comparison", "verdict"),
        "share": _get(report, "comparison", "share"),
        "witness_words": _get(report, "comparison", "witness_words"),
        "found": _get(report, "comparison", "found"),
        "missing": _get(report, "comparison", "missing"),
        "reading_words": _get(report, "comparison", "reading_words"),
        "unconfirmed_words": _get(report, "comparison", "unconfirmed_words"),
        "reading_numbers": _get(report, "comparison", "reading_numbers"),
        "unconfirmed_numbers": _get(report, "comparison", "unconfirmed_numbers"),
        "hyphen_joined": _get(report, "comparison", "hyphen_joined"),
        "reader_claude_code": _get(report, "reader", "claude_code"),
        "reader_model_id": _get(report, "reader", "model_id"),
        "reader_effort": _get(report, "reader", "effort"),
        "reader_cost_usd": _get(report, "reader", "cost_usd"),
        "reader_duration_ms": _get(report, "reader", "duration_ms"),
        "reader_turns": _get(report, "reader", "turns"),
        "reader_output_tokens": _get(report, "reader", "output_tokens"),
        "reader_thinking_tokens": _get(report, "reader", "thinking_tokens"),
        "reader_text_sha256": _get(report, "reader", "text_sha256"),
        "witness_version": _get(report, "witness", "version"),
        "witness_detail": _get(report, "witness", "detail"),
        "witness_languages": _get(report, "witness", "languages"),
        "witness_text_words": _get(report, "witness", "words"),
        "witness_confidence_mean": _get(report, "witness", "confidence", "mean"),
        "elapsed_ms": _get(report, "elapsed_ms"),
    }


def _sha_of(path: str):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def judge(args) -> dict:
    """The item's verdict: every named check must pass."""
    d = args.dir
    attempt = os.path.join(d, "attempt")
    report = _load_json(os.path.join(attempt, "report.json"))
    lifecycle = _load_json(os.path.join(d, "lifecycle.json"))
    reading_path = os.path.join(attempt, "reading.txt")
    reading = read_text(reading_path) if os.path.isfile(reading_path) else None
    f = facts(report) if isinstance(report, dict) else {}
    checks = {}

    def check(name: str, ok: bool, why: str = "") -> None:
        checks[name] = {"pass": bool(ok), "why": why}

    truths = [read_text(p) for p in (args.truth or [])]
    words_result = order_result = ai_result = None
    if reading is not None and truths:
        words_result = words_check(reading, truths, args.repeat)
    wanted = [c for c in args.checks.split(",") if c]
    if report is None:
        check("report", False, "no readable report")
    for name in wanted:
        if name == "agree":
            ok = (args.rc == 0 and f.get("stage") == "done" and f.get("failure_class") is None
                  and f.get("verdict") == "agree")
            check(name, ok, "" if ok else "exit %s, stage %s, failure %s, verdict %s"
                  % (args.rc, f.get("stage"), f.get("failure_class"), f.get("verdict")))
        elif name == "words":
            ok = words_result is not None and words_result["pass"]
            check(name, ok, "" if ok else ("no reading" if words_result is None
                                            else "%d of %d truth words missing"
                                            % (words_result["missing"], words_result["truth_words"])))
        elif name == "order":
            if reading is None or not truths:
                check(name, False, "no reading")
            else:
                order_result = order_check(reading, truths[0], line_range(args.left), line_range(args.right))
                check(name, order_result["pass"], "" if order_result["pass"] else
                      "left column end %s, right column start %s" % (order_result["left_end"], order_result["right_start"]))
        elif name == "ai":
            if reading is None or not truths:
                check(name, False, "no reading")
            else:
                ai_result = ai_check(reading, truths[0])
                check(name, ai_result["pass"], "" if ai_result["pass"] else
                      "%d of %d right" % (ai_result["right"], ai_result["truth_ai"]))
        elif name == "too-large":
            ok = (args.rc == 1 and f.get("stage") == "envelope" and f.get("failure_class") == "too-large"
                  and f.get("validation_outcome") == "too-large" and f.get("validation_reason") == "edge")
            check(name, ok, "" if ok else "exit %s, stage %s, failure %s/%s, envelope %s/%s"
                  % (args.rc, f.get("stage"), f.get("failure_class"), f.get("failure_reason"),
                     f.get("validation_outcome"), f.get("validation_reason")))
        elif name == "rotated":
            ok = (args.rc == 1 and f.get("stage") == "envelope" and f.get("failure_class") == "unsupported"
                  and f.get("failure_acquisition") == "rotated" and f.get("validation_outcome") == "rotated"
                  and f.get("orientation") == 6)
            check(name, ok, "" if ok else "exit %s, stage %s, failure %s/%s, envelope %s, orientation %s"
                  % (args.rc, f.get("stage"), f.get("failure_class"), f.get("failure_acquisition"),
                     f.get("validation_outcome"), f.get("orientation")))
        elif name == "no-call":
            wrapped = os.path.exists(os.path.join(d, "wrapper.json"))
            argv = os.path.exists(os.path.join(attempt, "argv.json"))
            ok = (not wrapped and not argv and f.get("reader_claude_code") is None
                  and f.get("reader_cost_usd") is None)
            check(name, ok, "" if ok else "the reader's client was started (wrapper record %s, argv %s)"
                  % ("present" if wrapped else "absent", "present" if argv else "absent"))
        elif name == "lifecycle":
            ok = isinstance(lifecycle, dict) and lifecycle.get("pass") is True
            check(name, ok, "" if ok else ("no lifecycle record" if not isinstance(lifecycle, dict)
                                            else "; ".join(lifecycle.get("failures") or [])))
        else:
            raise ValueError("unknown check %r" % name)
    same = None
    if args.same_as:
        other = _sha_of(args.same_as)
        mine = _sha_of(reading_path)
        same = None if other is None or mine is None else other == mine
    item = {"seq": args.seq, "name": args.name, "rc": args.rc, "status": "pass" if all(
        c["pass"] for c in checks.values()) else "fail", "checks": checks, "facts": f,
            "words": words_result, "order": order_result, "ai": ai_result, "same_reading": same,
            "reading_sha256": _sha_of(reading_path),
            "lifecycle": {k: lifecycle.get(k) for k in ("pass", "model_call", "group_observed", "members_after_worker",
                                                       "members_left",
                                                       "claude_tree_seen", "claude_tree_left", "descendants_left",
                                                       "lock_vars_seen", "lock_fd_open")}
            if isinstance(lifecycle, dict) else None}
    return item


def _num(value, fmt="%s"):
    return "-" if value is None else fmt % value


def _failure_text(f: dict) -> str:
    """`class/acquisition (reason)` of a report's failure, as §21.7 names them."""
    return "%s/%s (%s)" % (f.get("failure_class"), _num(f.get("failure_acquisition")), _num(f.get("failure_reason")))


def item_line(item: dict) -> str:
    f = item.get("facts") or {}
    parts = ["exit %s" % item["rc"], "stage %s" % _num(f.get("stage"))]
    if f.get("failure_class"):
        parts.append("failure %s" % _failure_text(f))
    if f.get("verdict"):
        parts.append("verdict %s, share %s, missing %s, unconfirmed %s words %s numbers"
                     % (f["verdict"], _num(f.get("share")), _num(f.get("missing")),
                        _num(f.get("unconfirmed_words")), _num(f.get("unconfirmed_numbers"))))
    if item.get("words"):
        parts.append("truth words %d, missing %d" % (item["words"]["truth_words"], item["words"]["missing"]))
    if item.get("order"):
        parts.append("columns %s" % ("in order" if item["order"]["pass"] else "out of order"))
    if item.get("ai"):
        parts.append("AI right %d of %d" % (item["ai"]["right"], item["ai"]["truth_ai"]))
    if f.get("reader_duration_ms") is not None or f.get("reader_cost_usd") is not None:
        parts.append("reader %s s, $%s, thinking %s"
                     % (_num(None if f.get("reader_duration_ms") is None else f["reader_duration_ms"] / 1000.0, "%.1f"),
                        _num(f.get("reader_cost_usd"), "%.4f"), _num(f.get("reader_thinking_tokens"))))
    elif "no-call" in item["checks"]:
        parts.append("no model call" if item["checks"]["no-call"]["pass"] else "a model call")
    if item.get("same_reading") is not None:
        parts.append("same reading as the first: %s" % ("yes" if item["same_reading"] else "no"))
    if "lifecycle" in item["checks"]:
        parts.append("lifecycle %s" % ("pass" if item["checks"]["lifecycle"]["pass"] else "FAIL"))
    failed = [name for name, c in item["checks"].items() if not c["pass"]]
    head = "%s: %s" % (item["name"], "pass" if not failed else "FAIL (%s)" % ", ".join(
        "%s: %s" % (n, item["checks"][n]["why"]) for n in failed))
    return head + " — " + ", ".join(parts)


def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.rename(tmp, path)


# --- the summary ----------------------------------------------------------------------------------------------

COLUMNS = ("#", "item", "result", "exit", "stage", "failure", "verdict", "share %", "missing", "unconfirmed words",
           "unconfirmed numbers", "hyphen joined", "truth words missing", "AI right", "columns", "reader s",
           "cost $", "thinking tokens", "output tokens", "elapsed s", "same reading", "lifecycle")


def _row(item: dict):
    f = item.get("facts") or {}
    w = item.get("words") or {}
    status = item.get("status")
    lc = item.get("lifecycle") or {}
    return [str(item.get("seq")), item.get("name", "?"), status or "?", _num(item.get("rc")), _num(f.get("stage")),
            "-" if not f.get("failure_class") else _failure_text(f),
            _num(f.get("verdict")), _num(f.get("share")), _num(f.get("missing")), _num(f.get("unconfirmed_words")),
            _num(f.get("unconfirmed_numbers")), _num(f.get("hyphen_joined")),
            "-" if not w else "%d of %d" % (w["missing"], w["truth_words"]),
            "-" if not item.get("ai") else "%d of %d" % (item["ai"]["right"], item["ai"]["truth_ai"]),
            "-" if not item.get("order") else ("in order" if item["order"]["pass"] else "out of order"),
            _num(None if f.get("reader_duration_ms") is None else f["reader_duration_ms"] / 1000.0, "%.1f"),
            _num(f.get("reader_cost_usd"), "%.4f"), _num(f.get("reader_thinking_tokens")),
            _num(f.get("reader_output_tokens")),
            _num(None if f.get("elapsed_ms") is None else f["elapsed_ms"] / 1000.0, "%.1f"),
            "-" if item.get("same_reading") is None else ("yes" if item["same_reading"] else "no"),
            "-" if not lc else ("pass" if lc.get("pass") else "fail")]


def summary(args) -> int:
    items = []
    for path in glob.glob(os.path.join(args.run, "*", "item.json")):
        item = _load_json(path)
        if isinstance(item, dict):
            items.append(item)
    items.sort(key=lambda i: (i.get("seq") is None, i.get("seq")))
    passed = sum(1 for i in items if i.get("status") == "pass")
    failed = sum(1 for i in items if i.get("status") == "fail")
    not_run = sum(1 for i in items if i.get("status") == "not run")
    def first(key):
        return next((i["facts"][key] for i in items if (i.get("facts") or {}).get(key)), None)

    lines =["# Image gate — %s" % os.path.basename(os.path.normpath(args.run)), "",
             "- reader: model %s (id %s), effort %s, budget cap $%s, Claude Code %s"
             % (args.model, _num(first("reader_model_id")), args.effort, args.max_usd, _num(first("reader_claude_code"))),
             "- witness: tesseract %s (%s); TESSDATA_PREFIX %s" % (_num(first("witness_version")),
                                                                 _num(first("witness_detail")), args.tessdata_prefix),
             "- result: passed %d, failed %d, not run %d" % (passed, failed, not_run), "",
             "Thresholds: every truth word in the reading with multiplicity (post, page, columns, block), the columns "
             "of the page in order, `AI` right 25 of 25, `agree` on every read image, the 8 001 px column `too-large`, "
             "the rotated JPEG `unsupported`/`rotated` with no model call, five equal witness texts, the lifecycle "
             "of every run. Recorded with no threshold: the reader's time, cost and tokens, the share, the missing "
             "and unconfirmed counts, whether two readings of the post are equal.", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    other = []
    for item in items:
        if item.get("kind") in (None, "not run", "fail"):
            lines.append("| " + " | ".join(_row(item)) + " |")
        else:
            other.append(item)
    if other:
        lines.append("")
    for item in other:
        lines.append("- %s" % item.get("line", "%s: %s" % (item.get("name"), item.get("status"))))
    lines.append("")
    with open(os.path.join(args.run, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("image gate: passed %d, failed %d, not run %d — summary: %s"
          % (passed, failed, not_run, os.path.join(args.run, "summary.md")))
    return 1 if failed else 0


# --- entry ----------------------------------------------------------------------------------------------------

def _print_check(name: str, result: dict, numbers: str) -> int:
    print("%s: %s — %s" % (name, "pass" if result["pass"] else "FAIL", numbers))
    return 0 if result["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The scoring helper of the live image gate")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("words")
    p.add_argument("--reading", required=True)
    p.add_argument("--truth", action="append", required=True)
    p.add_argument("--repeat", type=int, default=1)
    p = sub.add_parser("order")
    p.add_argument("--reading", required=True)
    p.add_argument("--truth", required=True)
    p.add_argument("--left", required=True)
    p.add_argument("--right", required=True)
    p = sub.add_parser("ai")
    p.add_argument("--reading", required=True)
    p.add_argument("--truth", required=True)
    p = sub.add_parser("judge")
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--rc", type=int, required=True)
    p.add_argument("--checks", required=True)
    p.add_argument("--truth", action="append")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--left")
    p.add_argument("--right")
    p.add_argument("--same-as", dest="same_as")
    p.add_argument("--out", required=True)
    p = sub.add_parser("mark")
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--status", choices=("not run", "fail"), required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("probe-missing")
    p.add_argument("--probe", required=True)
    p.add_argument("--token", required=True)
    p = sub.add_parser("summary")
    p.add_argument("--run", required=True)
    p.add_argument("--effort", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--max-usd", dest="max_usd", required=True)
    p.add_argument("--tessdata-prefix", dest="tessdata_prefix", default="unset")
    args = ap.parse_args(argv)

    if args.cmd == "words":
        r = words_check(read_text(args.reading), [read_text(t) for t in args.truth], args.repeat)
        return _print_check("words", r, "truth words %d, reading words %d, missing %d"
                            % (r["truth_words"], r["reading_words"], r["missing"]))
    if args.cmd == "order":
        r = order_check(read_text(args.reading), read_text(args.truth), line_range(args.left), line_range(args.right))
        return _print_check("order", r, "left column end %s, right column start %s"
                            % (_num(r["left_end"]), _num(r["right_start"])))
    if args.cmd == "ai":
        r = ai_check(read_text(args.reading), read_text(args.truth))
        return _print_check("ai", r, "AI right %d of %d; the reading writes AI %d times, Al %d times"
                            % (r["right"], r["truth_ai"], r["reading_ai"], r["reading_al"]))
    if args.cmd == "judge":
        item = judge(args)
        line = item_line(item)
        item["line"] = line
        write_json(args.out, item)
        print(line)
        return 0 if item["status"] == "pass" else 1
    if args.cmd == "mark":
        if args.status == "not run":
            line = "%s: not run: %s" % (args.name, args.reason)
        else:
            line = "%s: FAIL — %s" % (args.name, args.reason)
        write_json(args.out, {"seq": args.seq, "name": args.name, "status": args.status, "kind": args.status,
                              "reason": args.reason, "line": line})
        print(line)
        return 0 if args.status == "not run" else 1
    if args.cmd == "probe-missing":
        doc = _load_json(args.probe)
        missing = doc.get("missing") if isinstance(doc, dict) else None
        print("missing" if isinstance(missing, list) and args.token in missing else "present")
        return 0
    if args.cmd == "summary":
        return summary(args)
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
