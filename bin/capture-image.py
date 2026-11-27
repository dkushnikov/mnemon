#!/usr/bin/env python3
"""capture-image.py — the capture-text of a retained image raw: a model reads, tesseract witnesses
(capture layer A20, section 21.6; capture contracts A15).

    capture-image.py <raw image> --input-name <basename> --languages <sequence> --out <capture.md> --report <json>
                     --reader-model <model> --reader-effort <level> --reader-max-usd <usd> [--claude <command>]
                     [--timeout <s>] [--max-bytes <n>] [--max-pixels <n>] [--max-edge <px>]
                     [--min-share <percent>] [--miss-allowance <n>] [--previous-text <hex>]
    capture-image.py --probe --languages <sequence> [--timeout <s>]     # one JSON document on stdout

One attempt, in this order: the envelope check of the raw (capture/image.py), the witness's probe and run
(capture/ocr_tesseract.py), the reader's one call with the raw's bytes (capture/image_reader.py), the
normalization of the reading, the comparison of the two texts (capture/read_compare.py). The reading is
written to --out only when the comparison's verdict is `agree`. The report `mnemon-capture-image/1` is
written on every outcome; the directory of --report is the attempt dir, where `progress.json`, `witness.txt`,
`reading.txt`, `argv.txt` and `argv.json` stay.

The owner (D11, A9): the process the gateway starts is a thin supervisor. It computes the deadline and
starts itself again as the worker (`--_worker`), the leader of a process group of its own. The unit of
cleanup is that group, never a pid: tesseract and the reader's `claude` run inside it, so one signal to the
group ends the worker and both commands. The supervisor kills the group on the deadline and on its own
SIGTERM, and tears down what a worker that ended left behind; the worker kills its own group when the
supervisor is gone. Only the supervisor writes a timeout report.

Exits (the supervisor's): 0 read and agreed · 1 failed (the report's `failure`) · 2 usage · 3 environment ·
124 timeout · 143 after a SIGTERM (no report).

Test hooks (honoured only under MNEMON_TEST_HOOKS=1): OCR_CMD replaces `tesseract`;
MNEMON_TEST_IMAGE_WORKER_CRASH=1 makes the worker SIGKILL itself once the stub's recognition call is in its
counter file, =reader once the stub reader's call is; MNEMON_TEST_IMAGE_ENVELOPE_HANG=1 makes the envelope
check wait in python code; MNEMON_TEST_WORKER_START_DELAY=<s> delays the worker's start
(MNEMON_TEST_WORKER_START_FILE=<path> is touched first). Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import image as imagemod  # noqa: E402
from capture import image_reader as readermod  # noqa: E402
from capture import ocr_tesseract as ocrmod  # noqa: E402
from capture import policy as policymod  # noqa: E402
from capture import proc  # noqa: E402
from capture import read_compare as comparemod  # noqa: E402

SCHEMA = "mnemon-capture-image/1"
CONVERTER = "image-reader"
EXIT_TIMEOUT = 124
STAGES = ("envelope", "probe", "witness", "output", "reader", "text", "compare")
DEFAULTS = {"timeout": 120.0, "max_bytes": 7000000, "max_pixels": 50000000, "max_edge": 8000,
            "min_share": "90", "miss_allowance": 20}


# --- small file helpers -------------------------------------------------------------------------------------

def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def write_text(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def one_line(text, limit: int = 300):
    if text is None:
        return None
    line = " ".join(str(text).split())
    return line[:limit] if line else None


def as_number(text: str) -> float:
    return float(text)


# --- the report's objects -----------------------------------------------------------------------------------

def witness_request(languages: str) -> dict:
    """The `witness` object before the probe ran: the request, the rest null (section 21.7)."""
    return ocrmod.witness_report(None, languages, None)


def reader_request(args) -> dict:
    return readermod.reader_report(None, model=args.reader_model, effort=args.reader_effort,
                                   max_usd=args.reader_max_usd, text_sha256=None)


def limits_of(args) -> dict:
    return {"max_bytes": args.max_bytes, "max_pixels": args.max_pixels, "max_edge": args.max_edge}


def report_of(stage: str, failure, *, validation, witness, execution, reader, previous_text=None,
              comparison=None, result=None, method=None, error=None, started: float) -> dict:
    done = stage == "done"
    return {"schema": SCHEMA, "stage": stage, "failure": failure,
            "method": method if done else None,
            "converter": CONVERTER if done else None,
            "completeness": "unknown" if done else None,
            "validation": validation, "witness": witness, "execution": execution, "reader": reader,
            "previous_text": previous_text, "comparison": comparison, "result": result,
            "error": one_line(error), "elapsed_ms": int((time.monotonic() - started) * 1000)}


def mismatch_sentence(comparison: dict, languages: str, confidence) -> str:
    """The engine's own sentence for a refused reading: numbers only, never text of the image."""
    mean = "none" if not confidence else "%.1f" % confidence.get("mean", 0.0)
    floor = comparison["floor"]
    share = "none" if comparison["share"] is None else "%.1f %%" % comparison["share"]
    if comparison["verdict"] == "no-confirmation":
        # the class's sentence names the share, the missing count and the floor too (contracts A15 §3.6)
        head = ("the local recognizer confirms none of the reading's %d words (it read %d words itself: %s found, "
                "%d missing, the floor is %g %% and %d words)"
                % (comparison["reading_words"], comparison["witness_words"], share, comparison["missing"],
                   floor["min_share"], floor["allowance"]))
    else:
        head = ("the reading lacks %d of the %d words the local recognizer read: %s found, the floor is %g %% and %d words"
                % (comparison["missing"], comparison["witness_words"], share, floor["min_share"], floor["allowance"]))
    return "%s; witness languages %s, mean word confidence %s; the image is kept" % (head, languages, mean)


# --- the worker ---------------------------------------------------------------------------------------------

class Progress:
    """`progress.json` in the attempt dir: rewritten before each stage starts (tmp + rename)."""

    def __init__(self, attempt_dir: str, args):
        self.path = os.path.join(attempt_dir, "progress.json")
        self.validation = None
        self.witness = witness_request(args.languages)
        self.execution = None
        self.reader = reader_request(args)

    def enter(self, stage: str) -> None:
        write_json(self.path, {"stage": stage, "validation": self.validation, "witness": self.witness,
                               "execution": self.execution, "reader": self.reader})


def crash_when_called(counter_path: str, wanted) -> None:
    """Test hook: SIGKILL this process once the stub's call is in its counter file."""
    def _watch():
        while True:
            try:
                with open(counter_path, "r", encoding="utf-8") as fh:
                    lines = fh.read().split()
            except OSError:
                lines = []
            if any(wanted is None or line == wanted for line in lines):
                os.kill(os.getpid(), signal.SIGKILL)
            time.sleep(0.02)
    threading.Thread(target=_watch, name="test-crash", daemon=True).start()


def worker(args) -> int:
    started = time.monotonic()
    start_delay = policymod.hook("MNEMON_TEST_WORKER_START_DELAY")
    if start_delay:
        mark = policymod.hook("MNEMON_TEST_WORKER_START_FILE")
        if mark:
            with open(mark, "w"):
                pass
        time.sleep(float(start_delay))
    # the unit of cleanup is this process's own group: a worker that does not lead its group never signals it
    if os.getpgrp() != os.getpid():
        print("capture-image: the worker must lead its process group (start it through the supervisor)", file=sys.stderr)
        return 2
    proc.watch_parent(parent_pid=args.parent_pid, own_group=True)
    deadline = proc.Deadline(end=args.deadline_end) if args.deadline_end is not None else proc.Deadline(args.timeout)
    attempt_dir = os.path.dirname(os.path.abspath(args.report))
    progress = Progress(attempt_dir, args)
    remove(args.report)
    remove(args.out)

    def finish(stage, failure, code, **extra) -> int:
        rep = report_of(stage, failure, validation=progress.validation, witness=progress.witness,
                        execution=progress.execution, reader=progress.reader, started=started, **extra)
        write_json(args.report, rep)
        if failure is not None:
            print("capture-image: %s at stage %s: %s" % (failure["class"], stage, rep["error"] or failure["reason"]),
                  file=sys.stderr)
        return code

    def code_of(failure) -> int:
        return 3 if failure["class"] == "environment" else 1

    crash = policymod.hook("MNEMON_TEST_IMAGE_WORKER_CRASH")
    reader_calls = policymod.hook("MNEMON_TEST_READER_CALLS")
    tesseract_calls = policymod.hook("MNEMON_TEST_TESSERACT_CALLS")
    if crash == "reader" and reader_calls:
        crash_when_called(reader_calls, None)
    elif crash and crash != "reader" and tesseract_calls:
        crash_when_called(tesseract_calls, "run")

    # --- envelope ---
    progress.enter("envelope")
    if policymod.hook("MNEMON_TEST_IMAGE_ENVELOPE_HANG"):
        time.sleep(600)         # interruptible python code: the deadline falls inside the envelope stage
    try:
        envelope = imagemod.inspect(args.raw, input_name=args.input_name, max_bytes=args.max_bytes,
                                    max_pixels=args.max_pixels, max_edge=args.max_edge)
    except OSError as exc:
        return finish("envelope", {"class": "convert", "acquisition": None, "reason": "raw-unreadable", "retryable": True}, 1,
                      error="cannot read the raw image: %s" % (exc.strerror or exc))
    progress.validation = dict(imagemod.as_dict(envelope), limits=limits_of(args))
    failure = imagemod.failure_of(envelope)
    if failure is not None:
        return finish("envelope", failure, 1, error="the image envelope is %s (%s)" % (envelope.outcome, envelope.reason))
    if deadline.expired():
        return EXIT_TIMEOUT

    # --- the witness's probe ---
    progress.enter("probe")
    probe = ocrmod.probe(args.languages, deadline=deadline)
    if probe.deadline_hit:
        return EXIT_TIMEOUT
    progress.witness = ocrmod.witness_report(probe, args.languages, None)
    if not probe.ok:
        return finish("probe", {"class": "environment", "acquisition": None, "reason": probe.reason, "retryable": True}, 3,
                      error=probe.error or "the local recognizer is not ready: %s (missing: %s)" % (probe.reason, ", ".join(probe.missing)))
    progress.execution = ocrmod.execution_key(probe, args.languages)

    # --- the witness's run, then its output files ---
    progress.enter("witness")
    scratch = os.path.join(attempt_dir, "witness-out")
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch)
    try:
        witness = ocrmod.recognize(args.raw, scratch, probe, args.languages, deadline=deadline,
                                   on_stage=progress.enter)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if witness.deadline_hit:
        return EXIT_TIMEOUT
    progress.witness = ocrmod.witness_report(probe, args.languages, witness)
    if witness.failure is not None:
        return finish(witness.stage, witness.failure, code_of(witness.failure), error=witness.error)
    write_text(os.path.join(attempt_dir, "witness.txt"), witness.text)

    # --- the reader's one call: only now does the image leave the machine (I9) ---
    progress.enter("reader")
    reading = readermod.read(args.raw, envelope.format, attempt_dir, claude=args.claude, model=args.reader_model,
                             effort=args.reader_effort, max_usd=args.reader_max_usd, deadline=deadline)
    if reading.deadline_hit:
        return EXIT_TIMEOUT
    progress.reader = readermod.reader_report(reading, model=args.reader_model, effort=args.reader_effort,
                                              max_usd=args.reader_max_usd, text_sha256=None)
    if reading.failure is not None:
        return finish("reader", reading.failure, code_of(reading.failure), error=reading.error)

    # --- the normalization of the reading and its zero-text check ---
    progress.enter("text")
    zero = readermod.is_zero_text(reading.text)
    text = "" if zero else readermod.normalize(reading.text)
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    write_text(os.path.join(attempt_dir, "reading.txt"), text)
    progress.reader = readermod.reader_report(reading, model=args.reader_model, effort=args.reader_effort,
                                              max_usd=args.reader_max_usd, text_sha256=None if zero else text_sha256)
    previous = args.previous_text if (args.previous_text and not zero and args.previous_text != text_sha256) else None
    if zero:
        return finish("text", {"class": "no-text", "acquisition": None, "reason": "zero-reading", "retryable": False}, 1,
                      result={"lines": 0, "words": 0, "chars": 0, "bytes": 0},
                      error="the model read no text in the image (the local recognizer read %d word(s))" % (witness.words or 0))
    result = readermod.counts(text)

    # --- the comparison of the two texts ---
    progress.enter("compare")
    verdict = comparemod.compare(text, witness.text, min_share=args.min_share, allowance=args.miss_allowance)
    comparison = comparemod.as_report(verdict, min_share=args.min_share, allowance=args.miss_allowance)
    failure = comparemod.failure_of(verdict)
    if failure is not None:
        return finish("compare", failure, 1, previous_text=previous, comparison=comparison, result=result,
                      error=mismatch_sentence(comparison, args.languages, witness.confidence))
    write_text(args.out, text)
    return finish("done", None, 0, previous_text=previous, comparison=comparison, result=result,
                  method="read:%s" % reading.model_id)


# --- the supervisor -----------------------------------------------------------------------------------------

def supervisor_report(args, stage_default: str, failure: dict, error: str, started: float, fixed_stage=None) -> dict:
    """A report the supervisor writes: the stage reached and what the attempt knew, from progress.json."""
    attempt_dir = os.path.dirname(os.path.abspath(args.report))
    stage, validation, witness, execution, reader = stage_default, None, None, None, None
    try:
        progress = read_json(os.path.join(attempt_dir, "progress.json"))
        if isinstance(progress, dict) and progress.get("stage") in STAGES:
            stage = progress["stage"]
            validation, witness = progress.get("validation"), progress.get("witness")
            execution, reader = progress.get("execution"), progress.get("reader")
    except (OSError, ValueError):
        pass
    if not isinstance(witness, dict):
        witness = witness_request(args.languages)
    if not isinstance(reader, dict):
        reader = reader_request(args)
    return report_of(fixed_stage or stage, failure, validation=validation if isinstance(validation, dict) else None,
                     witness=witness, execution=execution if isinstance(execution, str) else None, reader=reader,
                     error=error % {"stage": stage} if "%(stage)s" in error else error, started=started)


def report_contradicts(rc: int, rep) -> bool:
    if not isinstance(rep, dict) or rep.get("schema") != SCHEMA:
        return True
    failure = rep.get("failure")
    if rc == 0:
        return failure is not None or rep.get("stage") != "done"
    if not isinstance(failure, dict):
        return True
    cls = failure.get("class")
    if rc == 1:
        return cls in ("environment", "timeout")
    return cls != "environment"         # rc == 3


def supervise(args) -> int:
    started = time.monotonic()
    timeout = float(args.timeout)
    deadline = proc.Deadline(timeout)
    proc.watch_parent()         # the gateway gone: the worker's group is killed, then this process exits
    remove(args.report)
    remove(args.out)
    attempt_dir = os.path.dirname(os.path.abspath(args.report))
    remove(os.path.join(attempt_dir, "progress.json"))
    argv = [sys.executable, os.path.abspath(__file__), "--_worker"] + sys.argv[1:] + \
           ["--_parent", str(os.getpid()), "--_deadline", repr(deadline.end)]
    res = proc.run_bounded(argv, timeout=timeout, deadline=deadline, check_group=True)
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
        sys.stderr.flush()

    def publish(rep: dict) -> None:
        # a report that cannot be written leaves none: the gateway then reads the exit alone (section 21.6)
        try:
            write_json(args.report, rep)
        except OSError as exc:
            print("capture-image: cannot write the report %s: %s" % (args.report, exc.strerror or exc), file=sys.stderr)

    def fault(reason: str, detail: str) -> int:
        remove(args.out)
        rep = supervisor_report(args, "worker", {"class": "convert", "acquisition": None, "reason": reason, "retryable": True},
                                "the image worker failed (%s): %s" % (reason, detail), started, fixed_stage="worker")
        publish(rep)
        print("capture-image: worker fault (%s): %s" % (reason, detail), file=sys.stderr)
        return 1

    # a worker that ends at or after the deadline, whatever its exit, is the timeout (section 21.6 «Exits»): the
    # worker's own exit 124 for a command cut by the deadline can be seen a moment before the runner's bound.
    # A worker the runner ended for its output cap, or whose group the runner had to empty, is judged by that
    # fault: the teardown itself may take the clock past the deadline.
    if res.timed_out or (res.started and not res.capped and not res.members_left and deadline.expired()):
        # the worker's group is empty (the runner killed and reaped it): the only timeout report
        remove(args.out)
        rep = supervisor_report(args, "envelope", {"class": "timeout", "acquisition": None, "reason": "deadline", "retryable": False},
                                "the image worker exceeded image_timeout=%g s at stage %%(stage)s (the worker and its process group were killed)" % timeout,
                                started)
        publish(rep)
        print("capture-image: timeout: %s" % rep["error"], file=sys.stderr)
        return EXIT_TIMEOUT
    if not res.started:
        return fault("no-report", "the worker could not start: %s" % res.stderr.decode("utf-8", "replace").strip())
    if res.capped:
        return fault("output-cap", "the worker wrote more than %d bytes on its %s" % (proc.BOUNDED_MAX, res.capped))
    if res.members_left:
        return fault("members-left", "the worker ended and its process group still had a member")
    rc = res.rc
    if rc is not None and rc < 0:
        return fault("signal-%d" % -rc, "the worker was ended by signal %d" % -rc)
    if rc not in (0, 1, 3):
        return fault("exit-%s" % rc, "the worker exited %s" % rc)
    try:
        rep = read_json(args.report)
    except (OSError, ValueError):
        return fault("no-report", "the worker exited %d without a readable report" % rc)
    if report_contradicts(rc, rep):
        return fault("report-contradicts-exit", "the worker exited %d and its report says otherwise" % rc)
    if rc != 0:
        remove(args.out)
    return rc


# --- the probe ------------------------------------------------------------------------------------------------

def run_probe(args) -> int:
    timeout = 10.0 if args.timeout is None else float(args.timeout)
    probe = ocrmod.probe(args.languages, deadline=proc.Deadline(timeout), hash_models=False)
    doc = ocrmod.probe_document(probe, args.languages)
    if probe.deadline_hit:
        doc["ok"] = False
        doc["reason"] = "probe-timeout"
        if not doc.get("missing"):
            doc["missing"] = ["version"]
        doc["error"] = doc.get("error") or "the probe exceeded its timeout of %g s" % timeout
    sys.stdout.write(json.dumps(doc, ensure_ascii=False) + "\n")
    return 0 if doc.get("ok") else 3


# --- arguments ------------------------------------------------------------------------------------------------

class Usage(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage(message)


def parse(argv):
    ap = Parser(description="Capture-text of an image raw: a model reads, tesseract witnesses", add_help=True)
    ap.add_argument("raw", nargs="?", help="the retained raw image (PNG or JPEG)")
    ap.add_argument("--probe", action="store_true", help="probe the local recognizer and print mnemon-ocr-probe/1")
    ap.add_argument("--input-name", default=None, help="the basename of the input as given (for the extension diagnostics)")
    ap.add_argument("--languages", default=None, help="the witness's languages: tesseract codes joined by +")
    ap.add_argument("--out", default=None, help="where to write the capture-text")
    ap.add_argument("--report", default=None, help="where to write the report (mnemon-capture-image/1); its directory is the attempt dir")
    ap.add_argument("--reader-model", default=None)
    ap.add_argument("--reader-effort", default=None)
    ap.add_argument("--reader-max-usd", default=None)
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--timeout", default=None, help="the deadline in seconds (image_timeout; default 120, the probe's 10)")
    ap.add_argument("--max-bytes", type=int, default=DEFAULTS["max_bytes"])
    ap.add_argument("--max-pixels", type=int, default=DEFAULTS["max_pixels"])
    ap.add_argument("--max-edge", type=int, default=DEFAULTS["max_edge"])
    ap.add_argument("--min-share", default=DEFAULTS["min_share"])
    ap.add_argument("--miss-allowance", type=int, default=DEFAULTS["miss_allowance"])
    ap.add_argument("--previous-text", default=None)
    ap.add_argument("--_worker", dest="worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_parent", dest="parent_pid", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--_deadline", dest="deadline_end", type=float, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if not args.languages:
        raise Usage("--languages is required")
    try:
        ocrmod.parse_languages(args.languages)
    except ValueError as exc:
        raise Usage("--languages: %s" % exc)
    if args.timeout is not None:
        try:
            args.timeout = float(args.timeout)
        except ValueError:
            raise Usage("--timeout takes a number of seconds")
        if args.timeout <= 0:
            raise Usage("--timeout takes a positive number of seconds")
    if args.probe:
        return args
    for name in ("raw", "input_name", "out", "report", "reader_model", "reader_effort", "reader_max_usd"):
        if not getattr(args, name):
            raise Usage("%s is required" % ("<raw image>" if name == "raw" else "--" + name.replace("_", "-")))
    if args.reader_effort not in readermod.EFFORT_LEVELS:
        raise Usage("--reader-effort takes one of %s" % ", ".join(readermod.EFFORT_LEVELS))
    try:
        if as_number(args.reader_max_usd) <= 0:
            raise ValueError
    except ValueError:
        raise Usage("--reader-max-usd takes a positive number of dollars")
    try:
        share = as_number(str(args.min_share))
        if not 0 <= share <= 100:
            raise ValueError
    except ValueError:
        raise Usage("--min-share takes a percentage between 0 and 100")
    for name in ("max_bytes", "max_pixels", "max_edge"):
        if getattr(args, name) <= 0:
            raise Usage("--%s takes a positive integer" % name.replace("_", "-"))
    if args.miss_allowance < 0:
        raise Usage("--miss-allowance takes a non-negative integer")
    if args.previous_text is not None and not (len(args.previous_text) == 64 and all(c in "0123456789abcdef" for c in args.previous_text)):
        raise Usage("--previous-text takes a sha256 in lowercase hex")
    if args.timeout is None:
        args.timeout = DEFAULTS["timeout"]
    return args


def main() -> int:
    try:
        args = parse(sys.argv[1:])
    except Usage as exc:
        print("capture-image: %s" % exc, file=sys.stderr)
        return 2
    proc.close_lock_fd()
    if args.worker:
        return worker(args)
    policymod.warn_ignored_hooks()
    if args.probe:
        return run_probe(args)
    proc.install_term_handler()
    return supervise(args)


if __name__ == "__main__":
    sys.exit(main())
