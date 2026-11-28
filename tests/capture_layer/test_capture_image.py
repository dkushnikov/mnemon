"""bin/capture-image.py — the image worker and its owner (capture layer A20, sections 21.6 and 21.7; the report
`mnemon-capture-image/2` of A21, section 22.3).

Hermetic: the witness is the stub tesseract (through the test hook OCR_CMD), the reader is the stub `claude`
(through --claude); no real tool, no network. Every run gets an attempt dir of its own. Stdlib only, python 3.9.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest

from _helpers import BIN, FIX, PY, REPORTS_FIX, ROOT, apply_env, clean_env, pid_alive, read_json, restore_env, wait_gone

from capture import image as imagemod
from capture import image_reader as readermod
from capture import ocr_tesseract as ocrmod
from capture import proc

CLI = os.path.join(BIN, "capture-image.py")
IMG = os.path.join(FIX, "image")
STUB_TESSERACT = os.path.join(IMG, "stub-tesseract.py")
STUB_CLAUDE = os.path.join(IMG, "stub-claude-image.py")
FIXTURE = read_json(os.path.join(REPORTS_FIX, "image.json"))
FIXTURE_V1 = read_json(os.path.join(REPORTS_FIX, "image-v1.json"))
PROBES = read_json(os.path.join(REPORTS_FIX, "ocr-probe.json"))

with open(os.path.join(IMG, "stub-text.txt"), "r", encoding="utf-8") as _fh:
    STUB_TEXT = _fh.read()
with open(os.path.join(IMG, "stub-text-other.txt"), "r", encoding="utf-8") as _fh:
    STUB_TEXT_OTHER = _fh.read()


def load_cli():
    spec = importlib.util.spec_from_file_location("capture_image_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lines_of(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().split()
    except OSError:
        return []


def wait_line(path: str, wanted: str, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if wanted in lines_of(path):
            return True
        time.sleep(0.02)
    return False


def group_gone(pgid: int, seconds: float = 6.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.05)
    return False


def worker_of(supervisor_pid: int, seconds: float = 10.0):
    """(pid, pgid) of the worker the supervisor started, from `ps`; (None, None) when none is seen in time."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,command="], capture_output=True).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4 and parts[1] == str(supervisor_pid) and "--_worker" in parts[3]:
                return int(parts[0]), int(parts[2])
        time.sleep(0.05)
    return None, None


def start_supervisor(b, env, timeout=60):
    """The supervisor as the gateway starts it; its output goes to files of the bench (no pipe left open)."""
    with open(os.path.join(b.work, "supervisor.out"), "wb") as out, \
            open(os.path.join(b.work, "supervisor.err"), "wb") as err:
        return subprocess.Popen(b.argv(timeout=timeout), env=env, stdout=out, stderr=err)


def keys_match(testcase, actual, fixture, where: str) -> None:
    """The key set of a report against the fixture's: recursively, wherever both sides are objects."""
    testcase.assertEqual(sorted(actual.keys()), sorted(fixture.keys()), "%s: key set differs" % where)
    for key, value in fixture.items():
        if isinstance(value, dict) and isinstance(actual[key], dict) and key not in ("data",):
            keys_match(testcase, actual[key], value, "%s.%s" % (where, key))


def check_v2_keys(testcase, rep, where: str) -> None:
    """Section 22.3: a /2 report has no key named `missing` — neither `witness.missing` nor `comparison.missing`;
    when a comparison ran, each list's length equals its count when nothing was cut, and the two directions
    agree on the matched numbers (section 22.6)."""
    testcase.assertEqual(rep["schema"], "mnemon-capture-image/2", where)
    testcase.assertIn("absent", rep["witness"], where)
    testcase.assertNotIn("missing", rep["witness"], where)
    c = rep["comparison"]
    if c is None:
        return
    testcase.assertNotIn("missing", c, where)
    testcase.assertEqual(c["rule"], "mnemon-read-compare/2", where)
    for obj, words_key, numbers_key in (("missing_lists", "missing_words", "missing_numbers"),
                                        ("unconfirmed", "unconfirmed_words", "unconfirmed_numbers")):
        if not c[obj]["truncated"]:
            testcase.assertEqual(len(c[obj]["words"]), c[words_key], "%s: %s.words" % (where, obj))
            testcase.assertEqual(len(c[obj]["numbers"]), c[numbers_key], "%s: %s.numbers" % (where, obj))
    testcase.assertEqual(c["witness_numbers"] - c["missing_numbers"], c["reading_numbers"] - c["unconfirmed_numbers"],
                         where)


class Bench:
    """One attempt: a work dir, a language model directory, the two counter files, the environment."""

    def __init__(self, testcase, languages=("eng",)):
        self.t = testcase
        self.work = os.path.realpath(tempfile.mkdtemp(prefix="mnemon-img-"))
        self.attempt = os.path.join(self.work, "attempt")
        os.mkdir(self.attempt)
        self.tessdata = os.path.join(self.work, "tessdata")
        os.mkdir(self.tessdata)
        for code in languages:
            with open(os.path.join(self.tessdata, code + ".traineddata"), "wb") as fh:
                fh.write(("model " + code).encode("ascii"))
        self.tess_calls = os.path.join(self.work, "tesseract-calls")
        self.reader_calls = os.path.join(self.work, "reader-calls")
        self.facts = os.path.join(self.work, "reader-facts.json")
        self.out = os.path.join(self.attempt, "capture.md")
        self.report = os.path.join(self.attempt, "capture-image.json")

    def env(self, tesseract="ok", reader="ok", extra=None, stub=True) -> dict:
        env = clean_env(hooks=True)
        env.pop("MNEMON_POLICY_ALLOW_LOOPBACK", None)
        if stub:
            env["OCR_CMD"] = STUB_TESSERACT
        env["MNEMON_TEST_TESSDATA_DIR"] = self.tessdata
        env["MNEMON_TEST_TESSERACT_CALLS"] = self.tess_calls
        env["MNEMON_TEST_READER_CALLS"] = self.reader_calls
        env["MNEMON_TEST_READER_FACTS"] = self.facts
        env["MNEMON_TEST_TESSERACT_MODE"] = tesseract
        env["MNEMON_TEST_READER_MODE"] = reader
        env.pop("TESSDATA_PREFIX", None)
        if extra:
            env.update(extra)
        return env

    def argv(self, raw="base.png", languages="eng", timeout=None, extra=()) -> list:
        raw_path = raw if os.path.isabs(raw) else os.path.join(IMG, raw)
        argv = [PY, CLI, raw_path, "--input-name", os.path.basename(raw_path), "--languages", languages,
                "--out", self.out, "--report", self.report, "--reader-model", "haiku", "--reader-effort", "high",
                "--reader-max-usd", "0.05", "--claude", STUB_CLAUDE]
        if timeout is not None:
            argv += ["--timeout", str(timeout)]
        return argv + list(extra)

    def run(self, env, **kw):
        limit = kw.pop("limit", 60)
        done = subprocess.run(self.argv(**kw), env=env, capture_output=True, timeout=limit)
        rep = None
        if os.path.exists(self.report):
            rep = read_json(self.report)
        return done.returncode, rep, done.stderr.decode("utf-8", "replace")

    def reader_calls_made(self) -> int:
        return len(lines_of(self.reader_calls))

    def runs_made(self) -> int:
        return lines_of(self.tess_calls).count("run")


class HappyPath(unittest.TestCase):

    def test_the_reading_is_published_when_the_two_texts_agree(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env())
        self.assertEqual(rc, 0, err)
        keys_match(self, rep, FIXTURE["done"], "done")
        check_v2_keys(self, rep, "done")
        self.assertEqual((rep["schema"], rep["stage"], rep["failure"]), ("mnemon-capture-image/2", "done", None))
        self.assertEqual(list(rep["comparison"]), list(FIXTURE["done"]["comparison"]), "the key order of section 22.3")
        self.assertEqual((rep["comparison"]["missing_numbers"], rep["comparison"]["missing_lists"]),
                         (0, {"words": [], "numbers": [], "truncated": False}), "the same text: nothing lacks")
        self.assertEqual(rep["comparison"]["witness_numbers"], rep["comparison"]["reading_numbers"])
        self.assertEqual(rep["converter"], "image-reader")
        self.assertEqual(rep["completeness"], "unknown")
        self.assertRegex(rep["method"], r"^read:[A-Za-z0-9._:@-]+$")
        self.assertEqual(rep["method"], "read:" + rep["reader"]["model_id"])
        self.assertEqual(rep["comparison"]["verdict"], "agree")
        self.assertEqual(rep["comparison"]["floor"], {"min_share": 90.0, "allowance": 20})
        self.assertEqual(rep["validation"]["outcome"], "ok")
        self.assertEqual(rep["validation"]["limits"], {"max_bytes": 7000000, "max_pixels": 50000000, "max_edge": 8000})
        self.assertEqual(rep["witness"]["languages"], "eng")
        self.assertRegex(rep["execution"], r"^[0-9a-f]{64}$")
        with open(b.out, "rb") as fh:
            published = fh.read()
        self.assertEqual(published.decode("utf-8"), readermod.normalize(STUB_TEXT))
        self.assertEqual(rep["reader"]["text_sha256"], hashlib.sha256(published).hexdigest())
        self.assertEqual(rep["result"], readermod.counts(published.decode("utf-8")))
        self.assertIsNone(rep["previous_text"])
        for name in ("witness.txt", "reading.txt", "argv.txt", "argv.json", "progress.json"):
            self.assertTrue(os.path.exists(os.path.join(b.attempt, name)), name)
        self.assertFalse(os.path.exists(os.path.join(b.attempt, "witness-out")), "the witness's scratch files are gone")
        self.assertEqual((b.runs_made(), b.reader_calls_made()), (1, 1))
        facts = read_json(b.facts)
        self.assertEqual(os.path.realpath(facts["cwd"]), b.attempt)

    def test_the_reader_gets_the_raw_bytes_after_the_witness_ran(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(), raw="jpeg-named.png")
        self.assertEqual(rc, 0, err)
        self.assertEqual((rep["validation"]["format"], rep["validation"]["extension_matches"]), ("jpeg", False))
        with open(os.path.join(IMG, "jpeg-named.png"), "rb") as fh:
            raw = fh.read()
        facts = read_json(b.facts)
        self.assertEqual(facts["image_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(facts["media_type"], "image/jpeg")

    def test_previous_text_is_named_only_when_the_reading_differs(self):
        b = Bench(self)
        same = hashlib.sha256(readermod.normalize(STUB_TEXT).encode("utf-8")).hexdigest()
        rc, rep, err = b.run(b.env(), extra=["--previous-text", same])
        self.assertEqual((rc, rep["previous_text"]), (0, None), err)
        b2 = Bench(self)
        other = "0" * 64
        rc, rep, err = b2.run(b2.env(), extra=["--previous-text", other])
        self.assertEqual((rc, rep["previous_text"]), (0, other), err)

    def test_a_second_attempt_may_read_another_text(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(reader="other-text"))
        self.assertEqual(rc, 0, err)
        with open(b.out, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), readermod.normalize(STUB_TEXT_OTHER))
        self.assertGreater(rep["comparison"]["unconfirmed_words"] + rep["comparison"]["unconfirmed_numbers"], 0)
        self.assertEqual(b.reader_calls_made(), 1, "other-text: one call per attempt")

    def test_one_visible_character_is_a_text(self):
        # the edge row of section 21.5: neither text has a word, the reading is not empty → agree, share null
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="empty", reader="one-char"))
        self.assertEqual(rc, 0, err)
        self.assertEqual((rep["stage"], rep["failure"], rep["comparison"]["verdict"]), ("done", None, "agree"))
        self.assertIsNone(rep["comparison"]["share"])
        self.assertEqual((rep["comparison"]["witness_words"], rep["comparison"]["reading_words"]), (0, 0))
        with open(b.out, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "x\n")
        self.assertEqual(rep["result"], {"lines": 1, "words": 1, "chars": 2, "bytes": 2})
        self.assertEqual(b.reader_calls_made(), 1, "one-char: one call per attempt")


class StageReports(unittest.TestCase):
    """The report of every stage has the key set of the fixture (section 21.7)."""

    def check(self, name, rc_want, stage, cls, **kw):
        b = Bench(self, languages=kw.pop("models", ("eng",)))
        env = b.env(tesseract=kw.pop("tesseract", "ok"), reader=kw.pop("reader", "ok"), extra=kw.pop("extra", None),
                    stub=kw.pop("stub", True))
        rc, rep, err = b.run(env, **kw)
        self.assertEqual(rc, rc_want, err)
        self.assertIsNotNone(rep, err)
        keys_match(self, rep, FIXTURE[name], name)
        check_v2_keys(self, rep, name)
        self.assertEqual((rep["stage"], rep["failure"]["class"]), (stage, cls), rep)
        for key in ("method", "converter", "completeness"):
            self.assertIsNone(rep[key], key)
        self.assertFalse(os.path.exists(b.out), "nothing at --out after a failure")
        return b, rep

    def test_envelope(self):
        b, rep = self.check("envelope", 1, "envelope", "unsupported", raw="exif6.jpg")
        self.assertEqual(rep["failure"], {"class": "unsupported", "acquisition": "rotated",
                                          "reason": rep["validation"]["reason"], "retryable": False})
        self.assertEqual(rep["validation"]["outcome"], "rotated")
        self.assertIsNone(rep["execution"])
        self.assertIsNone(rep["witness"]["version"])
        self.assertEqual(rep["reader"]["model"], "haiku")
        self.assertIsNone(rep["reader"]["model_id"])
        self.assertEqual((b.runs_made(), b.reader_calls_made()), (0, 0))

    def test_a_text_file_named_png_never_reaches_the_recognizer(self):
        b, rep = self.check("envelope", 1, "envelope", "unsupported", raw="text-named.png")
        self.assertEqual(rep["failure"]["acquisition"], "wrong-format")
        self.assertEqual(lines_of(b.tess_calls), [])

    def test_probe(self):
        b, rep = self.check("probe", 3, "probe", "environment", languages="rus+eng")
        self.assertEqual(rep["failure"], {"class": "environment", "acquisition": None, "reason": "model-missing",
                                          "retryable": True})
        self.assertEqual(rep["witness"]["absent"], ["lang:rus"], "absent holds what missing held at the stage probe")
        self.assertIsNone(rep["execution"])
        self.assertEqual(rep["validation"]["outcome"], "ok")
        self.assertEqual((b.runs_made(), b.reader_calls_made()), (0, 0))

    def test_witness(self):
        b, rep = self.check("witness", 1, "witness", "unsupported", tesseract="decode", raw="idat-broken.png")
        self.assertEqual(rep["failure"], {"class": "unsupported", "acquisition": "corrupt", "reason": "recognizer-decode",
                                          "retryable": False})
        self.assertEqual(rep["validation"]["outcome"], "ok", "the envelope's own verdict is never rewritten")
        self.assertRegex(rep["execution"], r"^[0-9a-f]{64}$")
        self.assertEqual(b.reader_calls_made(), 0)

    def test_output(self):
        b, rep = self.check("output", 1, "output", "convert", tesseract="non-utf8")
        self.assertEqual((rep["failure"]["reason"], rep["failure"]["retryable"]), ("output-encoding", False))
        self.assertEqual(b.reader_calls_made(), 0)

    def test_reader(self):
        b, rep = self.check("reader", 1, "reader", "convert", reader="budget")
        self.assertEqual((rep["failure"]["reason"], rep["failure"]["retryable"]), ("reader-budget", False))
        self.assertIsNotNone(rep["reader"]["cost_usd"], "an over-budget call still has a cost")
        self.assertIsNone(rep["reader"]["text_sha256"])
        self.assertIsNotNone(rep["witness"]["text_sha256"])

    def test_text(self):
        b, rep = self.check("text", 1, "text", "no-text", reader="empty")
        self.assertEqual(rep["failure"], {"class": "no-text", "acquisition": None, "reason": "zero-reading",
                                          "retryable": False})
        self.assertEqual(rep["result"], {"lines": 0, "words": 0, "chars": 0, "bytes": 0})
        self.assertIsNone(rep["comparison"])

    def test_compare(self):
        b, rep = self.check("compare", 1, "compare", "reading-mismatch", tesseract="double")
        self.assertEqual(rep["failure"], {"class": "reading-mismatch", "acquisition": "words-missing", "reason": "loss",
                                          "retryable": False})
        self.assertEqual(rep["comparison"]["verdict"], "loss")
        self.assertIsNotNone(rep["result"])
        # section 22.3: after a refusal the object is complete — after a loss it holds the words whose absence refused
        self.assertEqual(len(rep["comparison"]["missing_lists"]["words"]), min(200, rep["comparison"]["missing_words"]))
        self.assertGreater(rep["comparison"]["missing_words"], 20)

    def test_timeout(self):
        b, rep = self.check("timeout", 124, "reader", "timeout", reader="hang", timeout=1.5)
        self.assertEqual(rep["failure"], {"class": "timeout", "acquisition": None, "reason": "deadline", "retryable": False})

    def test_worker(self):
        b, rep = self.check("worker", 1, "worker", "convert", tesseract="hang",
                            extra={"MNEMON_TEST_IMAGE_WORKER_CRASH": "1"}, timeout=30)
        self.assertEqual((rep["failure"]["reason"], rep["failure"]["retryable"]), ("members-left", True))


class WitnessOutcomes(unittest.TestCase):

    def outcome(self, tesseract, **kw):
        b = Bench(self, languages=kw.pop("models", ("eng",)))
        rc, rep, err = b.run(b.env(tesseract=tesseract, stub=kw.pop("stub", True), extra=kw.pop("extra", None)), **kw)
        self.assertIsNotNone(rep, err)
        return b, rc, rep

    def test_the_broken_images_pass_the_envelope_and_are_the_witnesss_to_refuse(self):
        for raw in ("idat-broken.png", "scan-broken.jpg"):
            b, rc, rep = self.outcome("decode", raw=raw)
            self.assertEqual((rc, rep["stage"], rep["validation"]["outcome"]), (1, "witness", "ok"), raw)
            self.assertEqual((rep["failure"]["class"], rep["failure"]["acquisition"]), ("unsupported", "corrupt"), raw)

    def test_the_recognizers_own_limit(self):
        b, rc, rep = self.outcome("too-large")
        self.assertEqual((rc, rep["stage"]), (1, "witness"))
        self.assertEqual((rep["failure"]["class"], rep["failure"]["acquisition"], rep["failure"]["retryable"]),
                         ("too-large", "too-large", False))

    def test_another_exit_is_a_convert_failure(self):
        b, rc, rep = self.outcome("fail")
        self.assertEqual((rc, rep["stage"], rep["failure"]["class"], rep["failure"]["retryable"]),
                         (1, "witness", "convert", False))

    def test_output_faults_at_the_witness_boundary(self):
        for mode, reason in (("no-tsv", "output"), ("bad-tsv", "output"), ("big-out", "output"),
                             ("non-utf8", "output-encoding")):
            b, rc, rep = self.outcome(mode)
            self.assertEqual((rc, rep["stage"], rep["failure"]["class"], rep["failure"]["reason"]),
                             (1, "output", "convert", reason), mode)
            self.assertEqual(b.reader_calls_made(), 0, mode)

    def test_floods_are_capped(self):
        for mode in ("stdout-flood", "stderr-flood"):
            b, rc, rep = self.outcome(mode)
            self.assertEqual((rc, rep["stage"], rep["failure"]["class"], rep["failure"]["reason"]),
                             (1, "witness", "convert", "output-cap"), mode)

    def test_every_bound_language_must_load_exit_0_is_not_proof(self):
        b, rc, rep = self.outcome("partial-lang", languages="rus+eng", models=("eng", "rus"))
        self.assertEqual(rc, 3)
        self.assertEqual(rep["stage"], "witness")
        self.assertEqual(rep["failure"], {"class": "environment", "acquisition": None, "reason": "model-load",
                                          "retryable": True})
        self.assertEqual(rep["witness"]["absent"], ["lang:rus"], "absent holds what missing held at the stage witness")
        self.assertNotIn("missing", rep["witness"])
        self.assertFalse(os.path.exists(b.out))
        self.assertEqual(b.reader_calls_made(), 0)
        b, rc, rep = self.outcome("no-lang", languages="rus+eng", models=("eng", "rus"))
        self.assertEqual((rc, rep["failure"]["reason"]), (3, "model-load"))

    def test_no_model_call_without_the_witness(self):
        cases = [dict(tesseract="ok", stub=False, extra={"OCR_CMD": os.path.join(IMG, "no-such-tesseract")}),
                 dict(tesseract="ok", languages="rus+eng"),
                 dict(tesseract="partial-lang", languages="rus+eng", models=("eng", "rus")),
                 dict(tesseract="decode")]
        for case in cases:
            mode = case.pop("tesseract")
            b, rc, rep = self.outcome(mode, **case)
            self.assertNotEqual(rc, 0)
            self.assertEqual(b.reader_calls_made(), 0, "%s %r: the image left the machine without a witness" % (mode, case))

    def test_the_probes_failures_never_fall_back_to_another_sequence(self):
        for mode, reason, missing in (("version-old", "version", ["version"]), ("no-version", "version", ["version"])):
            b, rc, rep = self.outcome(mode)
            self.assertEqual((rc, rep["stage"], rep["failure"]["reason"], rep["witness"]["absent"]),
                             (3, "probe", reason, missing), mode)
            self.assertEqual(b.runs_made(), 0)
        b, rc, rep = self.outcome("ok", languages="rus")
        self.assertEqual((rc, rep["failure"]["reason"], rep["witness"]["absent"]), (3, "model-missing", ["lang:rus"]))
        self.assertEqual(b.runs_made(), 0, "no run under eng in place of rus")

    def test_a_probe_command_over_its_output_cap(self):
        b, rc, rep = self.outcome("version-flood")
        self.assertEqual((rc, rep["stage"], rep["failure"]["class"], rep["failure"]["reason"]),
                         (3, "probe", "environment", "probe-output-cap"))

    def test_a_probe_command_over_its_own_ten_seconds(self):
        start = time.monotonic()
        b, rc, rep = self.outcome("version-hang", timeout=60, limit=40)
        took = time.monotonic() - start
        self.assertEqual((rc, rep["stage"], rep["failure"]["class"], rep["failure"]["reason"]),
                         (3, "probe", "environment", "probe-timeout"))
        self.assertLess(took, ocrmod.PROBE_TIMEOUT + 6.0)


class Comparison(unittest.TestCase):

    def test_a_gross_loss_refuses_and_keeps_the_evidence(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="double"))
        self.assertEqual(rc, 1, err)
        self.assertEqual(rep["failure"]["acquisition"], "words-missing")
        self.assertFalse(rep["failure"]["retryable"])
        self.assertFalse(os.path.exists(b.out))
        for name in ("reading.txt", "witness.txt"):
            self.assertTrue(os.path.getsize(os.path.join(b.attempt, name)) > 0, name)
        # the engine's sentence holds numbers and names of settings, never a word of either text
        sentence = rep["error"]
        self.assertIn("witness languages eng", sentence)
        for word in ("picture", "machine", "brillig", "slithy", "witness keeps", "Field notes"):
            self.assertNotIn(word, sentence)
        for word in ("picture", "brillig", "Field notes"):
            self.assertNotIn(word, err)

    def test_no_confirmation_refuses(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="junk"))
        self.assertEqual(rc, 1, err)
        self.assertEqual(rep["failure"], {"class": "reading-mismatch", "acquisition": "no-confirmation",
                                          "reason": "no-confirmation", "retryable": False})
        self.assertEqual(rep["comparison"]["verdict"], "no-confirmation")
        self.assertFalse(os.path.exists(b.out))
        # the class's message (contracts A15 §3.6): the share, the missing count, the floor, the witness's
        # languages and its mean confidence — and no word of either text
        sentence = rep["error"]
        share = "none" if rep["comparison"]["share"] is None else "%.1f %%" % rep["comparison"]["share"]
        for part in ("%s found" % share, "%d missing" % rep["comparison"]["missing_words"], "the floor is 90 % and 20 words",
                     "witness languages eng", "mean word confidence"):
            self.assertIn(part, sentence)
        for word in ("picture", "brillig", "Field notes"):
            self.assertNotIn(word, sentence)

    def test_additions_are_counted_and_never_refused(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="half"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["comparison"]["verdict"], "agree")
        self.assertGreater(rep["comparison"]["unconfirmed_words"], 0)
        self.assertEqual(rep["comparison"]["missing_words"], 0)
        self.assertEqual(len(rep["comparison"]["unconfirmed"]["words"]), rep["comparison"]["unconfirmed_words"])
        self.assertTrue(os.path.exists(b.out))

    def test_numbers_the_reading_lacks_are_counted_and_listed_and_never_refused(self):
        # section 22.2: the witness's `numbers` mode is the text of `ok` and a line `10:15 - 11:30` that the stub
        # reader's text does not hold — four numbers, no word
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="numbers"))
        self.assertEqual(rc, 0, err)
        check_v2_keys(self, rep, "numbers")
        c = rep["comparison"]
        self.assertEqual((c["verdict"], c["missing_words"], c["unconfirmed_numbers"]), ("agree", 0, 0))
        self.assertEqual((c["missing_numbers"], c["witness_numbers"]), (4, c["reading_numbers"] + 4))
        self.assertEqual(c["missing_lists"], {"words": [], "numbers": ["10", "15", "11", "30"], "truncated": False})
        self.assertTrue(os.path.exists(b.out), "a missing number never refuses")
        for number in ("10", "15", "11", "30"):
            self.assertNotIn(number, err.split(), "the worker prints no list on stderr")

    def test_the_sentence_of_a_refused_reading_reads_missing_words(self):
        cli = load_cli()
        comparison = FIXTURE["compare"]["comparison"]
        sentence = cli.mismatch_sentence(comparison, "eng", FIXTURE["compare"]["witness"]["confidence"])
        self.assertEqual(sentence, FIXTURE["compare"]["error"], "the same sentence as for a /1 report")
        self.assertIn("lacks %d of the %d words" % (comparison["missing_words"], comparison["witness_words"]), sentence)
        no_conf = dict(comparison, verdict="no-confirmation")
        self.assertIn("%d missing" % comparison["missing_words"], cli.mismatch_sentence(no_conf, "eng", None))
        # no word and no number of either text: none of the two list objects reaches the sentence
        listed = (comparison["missing_lists"]["words"] + comparison["missing_lists"]["numbers"]
                  + comparison["unconfirmed"]["words"] + comparison["unconfirmed"]["numbers"])
        for text in (sentence, cli.mismatch_sentence(no_conf, "eng", None)):
            for token in listed:
                self.assertNotIn(token, text)
        with self.assertRaises(KeyError, msg="a /1 comparison has no missing_words: the sentence takes /2 only"):
            cli.mismatch_sentence(dict(FIXTURE_V1["done"]["comparison"], verdict="loss"), "eng", None)

    def test_the_floors_two_keys_are_limits_read_at_run_time(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="double"), extra=["--min-share", "40", "--miss-allowance", "5"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["comparison"]["floor"], {"min_share": 40.0, "allowance": 5})
        self.assertEqual(rep["comparison"]["verdict"], "agree")
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="double"), extra=["--miss-allowance", "500"])
        self.assertEqual((rc, rep["comparison"]["floor"]["allowance"]), (0, 500), err)


class ReaderOutcomes(unittest.TestCase):
    """Each mode of the stub reader → its row of section 21.4's table, through the worker."""

    ROWS = [
        # mode, exit, class, reason, retryable
        ("no-result", 1, "convert", "reader-output", True),
        ("no-structured", 1, "convert", "reader-output", True),
        ("text-not-string", 1, "convert", "reader-output", True),
        ("nul", 1, "convert", "reader-output", True),
        ("surrogate", 1, "convert", "reader-output", True),
        ("error", 1, "convert", "reader-error", True),
        ("auth", 3, "environment", "reader-auth", True),
        ("budget", 1, "convert", "reader-budget", False),
        ("max-turns", 1, "convert", "reader-max-turns", False),
        ("no-init", 1, "convert", "reader-fence", False),
        ("init-no-tools", 1, "convert", "reader-fence", False),
        ("tool-in-init", 1, "convert", "reader-fence", False),
        ("mcp-in-init", 1, "convert", "reader-fence", False),
        ("denial", 1, "convert", "reader-fence", False),
        ("stdout-flood", 1, "convert", "output-cap", False),
    ]

    def test_every_row(self):
        for mode, code, cls, reason, retryable in self.ROWS:
            b = Bench(self)
            rc, rep, err = b.run(b.env(reader=mode))
            self.assertIsNotNone(rep, "%s: %s" % (mode, err))
            self.assertEqual((rc, rep["stage"]), (code, "reader"), mode)
            self.assertEqual(rep["failure"], {"class": cls, "acquisition": None, "reason": reason, "retryable": retryable}, mode)
            self.assertFalse(os.path.exists(b.out), mode)
            self.assertEqual(b.reader_calls_made(), 1, "%s: one call per attempt" % mode)
            self.assertIsNone(rep["comparison"], mode)

    def test_a_reader_that_cannot_start(self):
        b = Bench(self)
        argv = b.argv()
        argv[argv.index("--claude") + 1] = os.path.join(b.work, "no-such-claude")
        done = subprocess.run(argv, env=b.env(), capture_output=True, timeout=60)
        rep = read_json(b.report)
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(rep["failure"], {"class": "environment", "acquisition": None, "reason": "reader-missing",
                                          "retryable": True})

    def test_zero_readings_are_no_text_whatever_the_witness_wrote(self):
        for mode in ("empty", "whitespace"):
            b = Bench(self)
            rc, rep, err = b.run(b.env(reader=mode))
            self.assertEqual((rc, rep["stage"], rep["failure"]["class"], rep["failure"]["retryable"]),
                             (1, "text", "no-text", False), mode)
            self.assertGreater(rep["witness"]["words"], 0)
            self.assertEqual(b.reader_calls_made(), 1, "%s: one call per attempt" % mode)

    def test_control_characters_stay_as_the_model_wrote_them(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(reader="control-chars"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(b.reader_calls_made(), 1, "control-chars: one call per attempt")
        with open(b.out, "r", encoding="utf-8") as fh:
            text = fh.read()
        for char in ("\x1c", "\x7f", "﻿"):
            self.assertIn(char, text)


class Deadline(unittest.TestCase):

    def killed(self, tesseract="ok", reader="ok", extra=None, stage=None, child=False):
        b = Bench(self)
        start = time.monotonic()
        sup = start_supervisor(b, b.env(tesseract=tesseract, reader=reader, extra=extra), timeout=1.5)
        try:
            worker, pgid = worker_of(sup.pid)
            rc = sup.wait(timeout=60)
        finally:
            sup.kill()
            sup.wait()
        took = time.monotonic() - start
        with open(os.path.join(b.work, "supervisor.err"), "r", encoding="utf-8", errors="replace") as fh:
            err = fh.read()
        self.assertEqual(rc, 124, err)
        self.assertLess(took, 1.5 + 2.0 + 2.0 + 3.0, "within the budget plus the grace")
        rep = read_json(b.report)
        self.assertEqual(rep["failure"], {"class": "timeout", "acquisition": None, "reason": "deadline", "retryable": False})
        self.assertEqual(rep["stage"], stage)
        self.assertFalse(os.path.exists(b.out))
        # the worker, the command it ran and whatever that started are gone with the worker's group (kill -0)
        self.assertIsNotNone(worker, "the worker was seen")
        self.assertEqual(worker, pgid, "the worker leads its group")
        self.assertTrue(wait_gone(worker, 1.0), "the worker is gone")
        self.assertTrue(group_gone(pgid, 1.0), "the worker's group is empty: the stub and its child went with it")
        return b, rep

    def test_a_hanging_recognizer_and_its_child(self):
        for mode in ("hang", "hang-child"):
            b, rep = self.killed(tesseract=mode, stage="witness")
            self.assertRegex(rep["execution"], r"^[0-9a-f]{64}$", "the key is known once the probe succeeded")
            self.assertEqual(rep["validation"]["outcome"], "ok")
            self.assertEqual(b.reader_calls_made(), 0)

    def test_a_hanging_reader_and_its_child(self):
        for mode in ("hang", "hang-child"):
            b, rep = self.killed(reader=mode, stage="reader")
            self.assertEqual(b.reader_calls_made(), 1, "%s: one call per attempt, never repeated after the kill" % mode)
            facts = read_json(b.facts) if os.path.exists(b.facts) else {}
            for key in ("pid", "child_pid"):
                if facts.get(key):
                    self.assertTrue(wait_gone(int(facts[key]), 4.0), "%s %s is gone" % (mode, key))
            self.assertIsNotNone(rep["witness"]["text_sha256"])

    def test_a_probe_cut_by_the_deadline_is_the_supervisors_timeout(self):
        self.killed(tesseract="version-hang", stage="probe")

    def test_a_deadline_inside_the_envelope_check(self):
        b, rep = self.killed(extra={"MNEMON_TEST_IMAGE_ENVELOPE_HANG": "1"}, stage="envelope")
        self.assertIsNone(rep["validation"])
        self.assertEqual(lines_of(b.tess_calls), [])


class Cleanup(unittest.TestCase):
    """The unit of cleanup is the worker's process group."""

    def start(self, b, env, timeout=60):
        return start_supervisor(b, env, timeout)

    def worker_of(self, supervisor_pid: int, seconds: float = 10.0):
        return worker_of(supervisor_pid, seconds)

    def test_the_supervisor_killed_during_recognition_leaves_nothing(self):
        b = Bench(self)
        sup = self.start(b, b.env(tesseract="hang"))
        try:
            self.assertTrue(wait_line(b.tess_calls, "run"), "the recognition call started")
            worker, pgid = self.worker_of(sup.pid)
            self.assertIsNotNone(worker)
            self.assertEqual(worker, pgid, "the worker leads its group")
            os.kill(sup.pid, signal.SIGKILL)
            sup.wait()
            self.assertTrue(wait_gone(worker, 1.0 + 2.0), "the worker is gone")
            self.assertTrue(group_gone(pgid, 1.0 + 2.0), "the worker's group is empty: the stub went with it")
        finally:
            sup.kill()

    def test_the_supervisor_killed_during_the_readers_call_leaves_nothing(self):
        b = Bench(self)
        sup = self.start(b, b.env(reader="hang-child"))
        try:
            self.assertTrue(wait_line(b.reader_calls, "read"), "the reader's call started")
            worker, pgid = self.worker_of(sup.pid)
            os.kill(sup.pid, signal.SIGKILL)
            sup.wait()
            self.assertTrue(group_gone(pgid, 1.0 + 2.0), "the reader's claude and its child are in the worker's group")
        finally:
            sup.kill()

    def test_a_worker_crash_with_a_live_recognizer(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(tesseract="hang", extra={"MNEMON_TEST_IMAGE_WORKER_CRASH": "1"}), timeout=30)
        self.assertEqual(rc, 1, err)
        self.assertEqual((rep["stage"], rep["failure"]["class"], rep["failure"]["reason"], rep["failure"]["retryable"]),
                         ("worker", "convert", "members-left", True))
        self.assertEqual(rep["validation"]["outcome"], "ok", "the fault report keeps what progress.json knew")

    def test_a_sigterm_after_the_worker_crashed_leaves_no_member_of_its_group(self):
        # the worker dies with the stub alive in its group; the supervisor is still checking that group when its
        # SIGTERM comes: exit 143 (the teardown) or 1 (the fault report, the group already emptied) — never a member left
        b = Bench(self)
        # the start delay keeps the worker visible to `ps` long enough to read its group before it crashes
        sup = self.start(b, b.env(tesseract="hang", extra={"MNEMON_TEST_IMAGE_WORKER_CRASH": "1",
                                                           "MNEMON_TEST_WORKER_START_DELAY": "1.0"}), timeout=30)
        pgid = None
        try:
            worker, pgid = self.worker_of(sup.pid)
            self.assertIsNotNone(worker)
            self.assertTrue(wait_line(b.tess_calls, "run"), "the recognition call started")
            self.assertTrue(wait_gone(worker, 5.0), "the worker crashed and was reaped")
            sup.send_signal(signal.SIGTERM)
            self.assertIn(sup.wait(timeout=20), (143, 1))
            self.assertTrue(group_gone(pgid, 1.0), "no member of the worker's group is left")
        finally:
            sup.kill()
            sup.wait()
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, TypeError, NameError):
                pass

    def test_a_worker_crash_with_the_reader_alive(self):
        b = Bench(self)
        rc, rep, err = b.run(b.env(reader="hang", extra={"MNEMON_TEST_IMAGE_WORKER_CRASH": "reader"}), timeout=30)
        self.assertEqual(rc, 1, err)
        self.assertEqual((rep["stage"], rep["failure"]["reason"]), ("worker", "members-left"))
        facts = read_json(b.facts)
        if facts.get("pid"):
            self.assertTrue(wait_gone(int(facts["pid"]), 4.0), "no stub reader left")

    def test_the_supervisor_killed_while_the_worker_starts(self):
        b = Bench(self)
        mark = os.path.join(b.work, "worker-start")
        sup = self.start(b, b.env(tesseract="hang", extra={"MNEMON_TEST_WORKER_START_DELAY": "1.5",
                                                           "MNEMON_TEST_WORKER_START_FILE": mark}))
        try:
            end = time.monotonic() + 10
            while not os.path.exists(mark) and time.monotonic() < end:
                time.sleep(0.02)
            self.assertTrue(os.path.exists(mark), "the worker is inside its start delay")
            worker, pgid = self.worker_of(sup.pid)
            os.kill(sup.pid, signal.SIGKILL)
            sup.wait()
            self.assertTrue(wait_gone(worker, 5.0), "the worker saw at its start that its supervisor is gone")
            self.assertEqual(lines_of(b.tess_calls), [], "no recognizer was started")
        finally:
            sup.kill()

    def test_the_gateway_killed_during_recognition(self):
        b = Bench(self)
        pids = os.path.join(b.work, "supervisor-pid")
        gateway = ("import os, subprocess, sys, time\n"
                   "p = subprocess.Popen(%r, env=os.environ)\n"
                   "open(%r, 'w').write(str(p.pid))\n"
                   "time.sleep(600)\n" % (b.argv(timeout=60), pids))
        gw = subprocess.Popen([PY, "-c", gateway], env=b.env(tesseract="hang"))
        try:
            self.assertTrue(wait_line(b.tess_calls, "run"), "the recognition call started")
            sup_pid = int(lines_of(pids)[0])
            worker, pgid = self.worker_of(sup_pid)
            os.kill(gw.pid, signal.SIGKILL)
            gw.wait()
            self.assertTrue(wait_gone(sup_pid, 4.0), "the supervisor's watcher saw the gateway go")
            self.assertTrue(group_gone(pgid, 4.0), "and killed the worker's group")
        finally:
            gw.kill()

    def test_a_worker_outside_a_group_of_its_own_exits_2_and_signals_nothing(self):
        b = Bench(self)
        argv = b.argv()
        argv.insert(2, "--_worker")
        done = subprocess.run(argv, env=b.env(), capture_output=True, timeout=30)   # a child shares this test's group
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertFalse(os.path.exists(b.report))
        self.assertTrue(pid_alive(os.getpid()))

    def test_sigterm_ends_the_supervisor_with_143_and_no_report(self):
        b = Bench(self)
        sup = self.start(b, b.env(tesseract="hang"))
        try:
            self.assertTrue(wait_line(b.tess_calls, "run"))
            worker, pgid = self.worker_of(sup.pid)
            sup.send_signal(signal.SIGTERM)
            self.assertEqual(sup.wait(timeout=15), 143)
            self.assertFalse(os.path.exists(b.report))
            self.assertTrue(group_gone(pgid, 3.0))
        finally:
            sup.kill()

    def test_a_worker_that_dies_without_a_report_is_a_worker_fault(self):
        cli = load_cli()
        b = Bench(self)
        # a report the worker's exit contradicts, row by row
        done = dict(FIXTURE["done"])
        failed = dict(FIXTURE["compare"])
        environment = dict(FIXTURE["probe"])
        timeout = dict(FIXTURE["timeout"])
        self.assertFalse(cli.report_contradicts(0, done))
        self.assertFalse(cli.report_contradicts(1, failed))
        self.assertFalse(cli.report_contradicts(3, environment))
        for rc, rep in ((0, failed), (0, environment), (1, done), (1, environment), (1, timeout), (3, done), (3, failed),
                        (0, None), (1, {}), (3, {"schema": "other"}), (0, dict(FIXTURE_V1["done"]))):
            self.assertTrue(cli.report_contradicts(rc, rep), (rc, rep and rep.get("stage")))


class SupervisorExits(unittest.TestCase):
    """The supervisor's reading of the worker's end (section 21.6 «Exits»), with the runner replaced in-process:
    «A worker that ends at or after the deadline, whatever its exit, is the timeout: the supervisor's report,
    exit 124» — also when the worker's own exit 124 is seen before the runner's bound passed."""

    def supervise(self, timeout: str, rc: int, delay: float, progress=None):
        cli = load_cli()
        b = Bench(self)
        args = cli.parse(b.argv(timeout=timeout)[2:])
        seen = {}

        def fake_run_bounded(argv, **kw):
            seen["argv"] = argv
            if progress is not None:
                cli.write_json(os.path.join(b.attempt, "progress.json"), progress)
            time.sleep(delay)
            return proc.BoundedResult(rc, b"", b"", False, int(delay * 1000))

        saved = (cli.proc.run_bounded, cli.proc.watch_parent)
        cli.proc.run_bounded, cli.proc.watch_parent = fake_run_bounded, (lambda *a, **kw: None)
        try:
            code = cli.supervise(args)
        finally:
            cli.proc.run_bounded, cli.proc.watch_parent = saved
        self.assertIn("--_worker", seen["argv"])
        return code, read_json(b.report)

    def test_a_worker_that_exits_124_after_the_deadline_is_the_timeout(self):
        progress = {"stage": "reader", "validation": {"outcome": "ok"}, "witness": {"languages": "eng"},
                    "execution": "ab" * 32, "reader": {"model": "haiku"}}
        code, rep = self.supervise("0.05", 124, 0.2, progress)
        self.assertEqual(code, 124)
        self.assertEqual(rep["failure"], {"class": "timeout", "acquisition": None, "reason": "deadline", "retryable": False})
        self.assertEqual((rep["stage"], rep["execution"], rep["validation"]), ("reader", "ab" * 32, {"outcome": "ok"}),
                         "the stage reached and the metadata come from progress.json")

    def test_a_worker_that_exits_124_before_the_deadline_is_a_worker_fault(self):
        code, rep = self.supervise("60", 124, 0.0)
        self.assertEqual(code, 1)
        self.assertEqual((rep["stage"], rep["failure"]), ("worker", {"class": "convert", "acquisition": None,
                                                                     "reason": "exit-124", "retryable": True}))

    def test_a_worker_that_dies_without_a_report_or_by_a_signal(self):
        progress = {"stage": "witness", "validation": {"outcome": "ok"}, "witness": {"languages": "eng"},
                    "execution": "cd" * 32, "reader": {"model": "haiku"}}
        for rc, reason in ((1, "no-report"), (3, "no-report"), (0, "no-report"), (-9, "signal-9")):
            code, rep = self.supervise("60", rc, 0.0, progress)
            self.assertEqual(code, 1, rc)
            self.assertEqual((rep["stage"], rep["failure"]), ("worker", {"class": "convert", "acquisition": None,
                                                                         "reason": reason, "retryable": True}), rc)
            self.assertEqual((rep["validation"], rep["execution"]), ({"outcome": "ok"}, "cd" * 32),
                             "%s: the fault report keeps the metadata of progress.json" % rc)


class RelativePaths(unittest.TestCase):
    """Every path of the call may be relative to the caller's working directory (the second code review):
    tesseract runs in the witness's scratch dir and the reader's `claude` in the attempt dir, so each path is
    made absolute before a command runs in another directory."""

    def run_relative(self, b, env):
        rel = lambda path: os.path.relpath(path, b.work)
        argv = [PY, CLI, rel(os.path.join(IMG, "base.png")), "--input-name", "base.png", "--languages", "eng",
                "--out", rel(b.out), "--report", rel(b.report), "--reader-model", "haiku", "--reader-effort", "high",
                "--reader-max-usd", "0.05", "--claude", rel(STUB_CLAUDE)]
        self.assertFalse(any(os.path.isabs(a) for a in argv[2:]), argv)
        done = subprocess.run(argv, env=env, cwd=b.work, capture_output=True, timeout=60)
        return done.returncode, done.stderr.decode("utf-8", "replace")

    def test_relative_paths_reach_both_commands_resolved(self):
        for name, extra in (("TESSDATA_PREFIX", {"TESSDATA_PREFIX": "tessdata"}),
                            ("the --list-langs header", {"MNEMON_TEST_TESSDATA_DIR": "tessdata"})):
            with self.subTest(name):
                b = Bench(self)
                rc, err = self.run_relative(b, b.env(extra=extra))
                self.assertEqual(rc, 0, err)
                rep = read_json(b.report)
                self.assertEqual((rep["stage"], rep["failure"]), ("done", None))
                self.assertEqual(rep["witness"]["tessdata_dir"], b.tessdata)
                with open(b.out, "r", encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), readermod.normalize(STUB_TEXT))
                self.assertEqual(os.path.realpath(read_json(b.facts)["cwd"]), b.attempt)
                self.assertTrue(os.path.exists(os.path.join(b.attempt, "witness.txt")))
                self.assertEqual((b.runs_made(), b.reader_calls_made()), (1, 1))


class StageTransitions(unittest.TestCase):
    """`progress.json` names the stage `output` before the witness's output files are looked at (section 21.6
    «Stage transitions»): the worker hands `Progress.enter` to the witness's run as its `on_stage`."""

    def setUp(self):
        self.b = Bench(self)
        self.old_env = apply_env(self.b.env())
        self.cli = load_cli()
        self.args = self.cli.parse(self.b.argv()[2:])

    def tearDown(self):
        restore_env(self.old_env)

    def run_witness(self, mode: str):
        os.environ["MNEMON_TEST_TESSERACT_MODE"] = mode
        progress = self.cli.Progress(self.b.attempt, self.args)
        probe = ocrmod.probe("eng", deadline=proc.Deadline(30))
        self.assertTrue(probe.ok, probe)
        progress.witness = ocrmod.witness_report(probe, "eng", None)
        progress.execution = ocrmod.execution_key(probe, "eng")
        progress.enter("witness")
        scratch = os.path.join(self.b.work, "scratch")
        os.mkdir(scratch)
        stages = []

        def on_stage(stage):
            # the output files are still where the run left them: the call comes before they are read
            stages.append((stage, sorted(os.listdir(scratch))))
            progress.enter(stage)

        witness = ocrmod.recognize(os.path.join(IMG, "base.png"), scratch, probe, "eng", deadline=proc.Deadline(30),
                                   on_stage=on_stage)
        return witness, stages, read_json(progress.path)

    def test_the_output_stage_is_recorded_before_the_outputs_are_read(self):
        witness, stages, progress = self.run_witness("ok")
        self.assertIsNone(witness.failure)
        self.assertEqual(stages, [("output", ["out.tsv", "out.txt"])])
        self.assertEqual(progress["stage"], "output")
        self.assertEqual(progress["execution"], ocrmod.execution_key(ocrmod.probe("eng", deadline=proc.Deadline(30)), "eng"),
                         "the metadata the attempt knows so far")

    def test_an_output_fault_belongs_to_the_output_stage(self):
        witness, stages, progress = self.run_witness("non-utf8")
        self.assertEqual((witness.stage, witness.failure["reason"]), ("output", "output-encoding"))
        self.assertEqual([s for s, _ in stages], ["output"])
        self.assertEqual(progress["stage"], "output")

    def test_a_witness_stage_failure_never_enters_the_output_stage(self):
        witness, stages, progress = self.run_witness("fail")
        self.assertEqual((witness.stage, witness.failure["reason"]), ("witness", "recognizer-exit"))
        self.assertEqual(stages, [])
        self.assertEqual(progress["stage"], "witness")


class Probe(unittest.TestCase):
    """`capture-image.py --probe`: one JSON document on stdout, exit 0 or 3, exit 2 on a bad sequence."""

    def probe(self, env, languages, timeout=None):
        argv = [PY, CLI, "--probe", "--languages", languages]
        if timeout is not None:
            argv += ["--timeout", str(timeout)]
        done = subprocess.run(argv, env=env, capture_output=True, timeout=60)
        return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8", "replace")

    def document(self, env, languages, timeout=None):
        rc, out, err = self.probe(env, languages, timeout)
        self.assertEqual(len(out.splitlines()), 1, "one JSON document: %r %s" % (out, err))
        doc = json.loads(out)
        self.assertEqual(sorted(doc.keys()), sorted(PROBES["present"].keys()))
        return rc, doc

    def test_all_present(self):
        b = Bench(self, languages=("eng", "osd", "rus"))
        rc, doc = self.document(b.env(), "rus+eng")
        self.assertEqual(rc, 0)
        want = dict(PROBES["present"], tessdata_dir=doc["tessdata_dir"])
        self.assertEqual(doc, want)
        self.assertEqual(os.path.realpath(doc["tessdata_dir"]), b.tessdata)
        self.assertEqual(lines_of(b.tess_calls), ["version", "list-langs"], "no recognition, no model call")
        self.assertEqual(b.reader_calls_made(), 0)

    def test_no_tesseract(self):
        b = Bench(self)
        rc, doc = self.document(b.env(stub=False, extra={"OCR_CMD": os.path.join(b.work, "nothing-here")}), "rus+eng")
        self.assertEqual(rc, 3)
        for key in ("ok", "version", "detail", "tessdata_dir", "available", "present", "missing", "reason"):
            self.assertEqual(doc[key], PROBES["no-tesseract"][key], key)

    def test_a_version_older_than_4(self):
        b = Bench(self)
        rc, doc = self.document(b.env(tesseract="version-old"), "rus+eng")
        self.assertEqual((rc, doc["ok"], doc["missing"], doc["reason"]), (3, False, ["version"], "version"))

    def test_a_missing_language_model(self):
        b = Bench(self, languages=("eng", "osd"))
        rc, doc = self.document(b.env(), "rus+eng")
        self.assertEqual(rc, 3)
        for key in ("ok", "version", "detail", "available", "present", "missing", "reason"):
            self.assertEqual(doc[key], PROBES["model-missing"][key], key)

    def test_a_probe_cut_by_its_own_timeout(self):
        b = Bench(self)
        rc, doc = self.document(b.env(tesseract="version-hang"), "rus+eng", timeout=1)
        self.assertEqual((rc, doc["ok"], doc["reason"]), (3, False, "probe-timeout"))
        self.assertEqual(doc["missing"], PROBES["probe-timeout"]["missing"])

    def test_a_bad_sequence_is_a_usage_error_with_nothing_on_stdout(self):
        b = Bench(self)
        for sequence in ("english", "eng+eng", "eng+", "ENG", ""):
            rc, out, err = self.probe(b.env(), sequence)
            self.assertEqual((rc, out), (2, ""), sequence)

    def test_the_hook_is_ignored_without_the_master_switch(self):
        b = Bench(self)
        env = b.env()
        del env["MNEMON_TEST_HOOKS"]
        env["PATH"] = b.work                # no tesseract on PATH either
        rc, out, err = self.probe(env, "eng")
        self.assertEqual((rc, json.loads(out)["reason"]), (3, "no-tesseract"), err)
        self.assertIn("OCR_CMD", err)
        self.assertIn("ignored", err)


class Usage(unittest.TestCase):

    def test_usage_errors_exit_2(self):
        b = Bench(self)
        base = b.argv()
        for change in (["--reader-effort", "extreme"], ["--languages", "eng+eng"], ["--min-share", "101"],
                       ["--reader-max-usd", "0"], ["--timeout", "0"], ["--previous-text", "abc"]):
            argv = list(base)
            if change[0] in argv:
                argv[argv.index(change[0]) + 1] = change[1]
            else:
                argv += change
            done = subprocess.run(argv, env=b.env(), capture_output=True, timeout=30)
            self.assertEqual(done.returncode, 2, change)
            self.assertFalse(os.path.exists(b.report), change)
        done = subprocess.run([PY, CLI, "--languages", "eng"], env=b.env(), capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 2)


class Signature(unittest.TestCase):
    """The gateway's signature rule (bin/lib-capture.sh cap_image_signature) and capture/image.py agree."""

    def test_one_rule_in_two_places(self):
        names = sorted(n for n in os.listdir(IMG) if n.endswith((".png", ".jpg")))
        names += [os.path.join("heads", n) for n in sorted(os.listdir(os.path.join(IMG, "heads")))]
        script = ('source "$1"; shift; for f in "$@"; do printf "%s=%s\\n" "$f" "$(cap_image_signature "$f")"; done')
        paths = [os.path.join(IMG, n) for n in names]
        done = subprocess.run(["bash", "-c", script, "_", os.path.join(BIN, "lib-capture.sh")] + paths,
                              capture_output=True, timeout=60, env=clean_env(hooks=False))
        self.assertEqual(done.returncode, 0, done.stderr)
        got = dict(line.rsplit("=", 1) for line in done.stdout.decode("utf-8").splitlines())
        self.assertEqual(len(got), len(paths))
        for path in paths:
            with open(path, "rb") as fh:
                head = fh.read(16)
            self.assertEqual(got[path], imagemod.raw_extension(head) or "", path)


if __name__ == "__main__":
    unittest.main()
