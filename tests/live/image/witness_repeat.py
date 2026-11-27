#!/usr/bin/env python3
"""witness_repeat.py — the witness alone, N runs of one image (capture layer §21.13, the live gate; §21.9 «Repeatability»).

    witness_repeat.py --root <repo> --image <file> --languages <sequence> --runs 5 --seq <n> --out-dir <dir>

It runs the worker's own witness — `capture.ocr_tesseract` from `<repo>/bin`: the probe of §21.3 (the language
models hashed), then `recognize` with the adapter's argv, once per run, each in a scratch directory of its
own — and compares the witness texts (after the adapter's `mnemon-ocr-text/1`) by their sha256. The real
tesseract runs: no test hook is honoured here (the gate refuses to start under MNEMON_TEST_HOOKS).

It writes `item.json` and `witness-<i>.txt` (the texts stay in the snapshot) into `--out-dir` and prints one
line of numbers; exit 0 when every run succeeded with one and the same text, 1 otherwise. Stdlib only,
python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

DEADLINE_S = 120.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The witness alone, N runs of one image")
    ap.add_argument("--root", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--languages", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seq", type=int, required=True)
    ap.add_argument("--out-dir", dest="out_dir", required=True)
    args = ap.parse_args(argv)
    if "MNEMON_TEST_HOOKS" in os.environ:
        print("witness_repeat: refused: MNEMON_TEST_HOOKS is set (this runs the real tesseract)", file=sys.stderr)
        return 2
    sys.path.insert(0, os.path.join(os.path.abspath(args.root), "bin"))
    from capture import ocr_tesseract as ocr  # noqa: E402
    from capture import proc  # noqa: E402

    os.makedirs(args.out_dir, exist_ok=True)
    failures = []
    runs = []
    probe = ocr.probe(args.languages, deadline=proc.Deadline(DEADLINE_S))
    key = None
    if not probe.ok:
        failures.append("the probe failed: %s (missing %d)" % (probe.reason, len(probe.missing or [])))
    else:
        key = ocr.execution_key(probe, args.languages)
        for i in range(1, args.runs + 1):
            scratch = os.path.join(args.out_dir, "run-%d" % i)
            shutil.rmtree(scratch, ignore_errors=True)
            os.makedirs(scratch)
            t0 = time.monotonic()
            try:
                w = ocr.recognize(os.path.abspath(args.image), scratch, probe, args.languages,
                                  deadline=proc.Deadline(DEADLINE_S))
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            seconds = time.monotonic() - t0
            if w.failure is not None or w.text is None:
                failures.append("run %d failed: %s/%s" % (i, (w.failure or {}).get("class"), (w.failure or {}).get("reason")))
                runs.append({"run": i, "ok": False, "seconds": round(seconds, 3)})
                continue
            with open(os.path.join(args.out_dir, "witness-%d.txt" % i), "w", encoding="utf-8") as fh:
                fh.write(w.text)
            runs.append({"run": i, "ok": True, "seconds": round(seconds, 3), "text_sha256": w.text_sha256,
                         "words": w.words, "confidence_mean": (w.confidence or {}).get("mean")})
    texts = sorted({r["text_sha256"] for r in runs if r.get("ok")})
    if probe.ok and len(texts) != 1:
        failures.append("%d distinct texts" % len(texts))
    if probe.ok and len(runs) != args.runs:
        failures.append("%d runs of %d" % (len(runs), args.runs))
    ok = not failures
    secs = [r["seconds"] for r in runs]
    line = ("witness: %s — %d runs of %s (%s), %d distinct text(s), %s words, %s"
            % ("pass" if ok else "FAIL (%s)" % "; ".join(failures), len(runs), os.path.basename(args.image),
               args.languages, len(texts), runs[0].get("words", "-") if runs else "-",
               ("%.2f–%.2f s each" % (min(secs), max(secs))) if secs else "no run"))
    item = {"seq": args.seq, "name": "witness", "kind": "witness", "status": "pass" if ok else "fail",
            "failures": failures, "runs": runs, "distinct_texts": len(texts), "execution": key,
            "version": probe.version, "detail": probe.detail, "line": line}
    tmp = os.path.join(args.out_dir, "item.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(item, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.rename(tmp, os.path.join(args.out_dir, "item.json"))
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
