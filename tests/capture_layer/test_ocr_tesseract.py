"""capture.ocr_tesseract — the witness, adapter `ocr-tesseract/1` (capture layer A20, §21.3, §21.6, §21.7, §21.13).

Hermetic: the stub `tests/fixtures/capture/image/stub-tesseract.py` stands in for tesseract through the test
hook `OCR_CMD` (`MNEMON_TEST_HOOKS=1`), and the recorded output of the real tool (`tesseract/`) is replayed by a
small stub this suite writes. No test runs the real tesseract. Stdlib only, python 3.9.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from fractions import Fraction
from unittest import mock

from _helpers import FIX, REPORTS_FIX, apply_env, assert_same_keys, clean_env, read_json, restore_env, write_stub

from capture import ocr_tesseract as ot
from capture import policy, proc

IMAGE_FIX = os.path.join(FIX, "image")
STUB = os.path.join(IMAGE_FIX, "stub-tesseract.py")
RECORDED = os.path.join(IMAGE_FIX, "tesseract")
with open(os.path.join(IMAGE_FIX, "stub-text.txt"), "r", encoding="utf-8") as _fh:
    STUB_TEXT = _fh.read()

SPEC_OPTIONS = {"psm": "3", "oem": "default", "variables": {"tessedit_create_tsv": "1", "tessedit_create_txt": "1"}}
WITNESS_KEYS = ["adapter", "backend", "version", "detail", "tessdata_dir", "languages", "data", "options", "missing",
                "text_sha256", "words", "confidence"]
PROBE_KEYS = ["schema", "ok", "adapter", "backend", "version", "detail", "tessdata_dir", "languages", "available",
              "present", "missing", "reason", "error"]

# Replays the recorded output of tesseract 5.5.4: `--version` from `version.txt`, a `--list-langs` header for the
# suite's model directory, and a run that writes the recorded stderr (REPLAY_STDERR), the recorded `out.txt` and
# `out.tsv` when REPLAY_OUTPUTS=1, and exits REPLAY_EXIT.
REPLAY = r'''#!/usr/bin/env python3
import os, shutil, sys
rec = os.environ["REPLAY_DIR"]
args = sys.argv[1:]
def cat(name, stream):
    with open(os.path.join(rec, name), "rb") as fh:
        stream.buffer.write(fh.read())
    stream.flush()
if "--version" in args:
    cat("version.txt", sys.stdout)
    sys.exit(0)
if "--list-langs" in args:
    d = os.environ["MNEMON_TEST_TESSDATA_DIR"]
    codes = sorted(f[:-len(".traineddata")] for f in os.listdir(d) if f.endswith(".traineddata"))
    sys.stdout.write('List of available languages in "%s/" (%d):\n' % (d, len(codes)) + "".join(c + "\n" for c in codes))
    sys.exit(0)
if os.environ.get("REPLAY_OUTPUTS") == "1":
    shutil.copyfile(os.path.join(rec, "out.txt"), args[1] + ".txt")
    shutil.copyfile(os.path.join(rec, "out.tsv"), args[1] + ".tsv")
cat(os.environ["REPLAY_STDERR"], sys.stderr)
sys.exit(int(os.environ["REPLAY_EXIT"]))
'''


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def spec_key(p, languages: str) -> str:
    """The execution key as §21.3 defines it, written out from the spec's text rather than from the module."""
    doc = {"adapter": "ocr-tesseract/1", "backend": "tesseract", "version": p.version, "detail": p.detail,
           "tessdata_dir": p.tessdata_dir, "languages": languages, "data": p.data, "options": SPEC_OPTIONS,
           "envelope": "mnemon-image-envelope/1", "normalizer": "mnemon-ocr-text/1"}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def group_members(pgid: int) -> list:
    out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "pgid="], capture_output=True, timeout=30).stdout
    members = []
    for line in out.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == str(pgid):
            members.append(int(parts[0]))
    return members


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _Witness(unittest.TestCase):
    """A temporary language model directory, the stub through OCR_CMD under the hooks, a counter file."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mnemon-ocr-")
        self.tessdata = os.path.join(self.work, "tessdata")
        os.mkdir(self.tessdata)
        self.model("eng", b"an eng language model\n")
        self.model("osd", b"an osd language model\n")
        self.scratch = os.path.join(self.work, "scratch")
        os.mkdir(self.scratch)
        self.raw = os.path.join(self.work, "raw.png")
        with open(self.raw, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        self.calls = os.path.join(self.work, "calls")
        env = clean_env(extra={"OCR_CMD": STUB, "MNEMON_TEST_TESSDATA_DIR": self.tessdata,
                               "MNEMON_TEST_TESSERACT_CALLS": self.calls})
        env.pop("TESSDATA_PREFIX", None)
        self.old_env = apply_env(env)

    def tearDown(self):
        restore_env(self.old_env)
        for root, dirs, files in os.walk(self.work):
            for name in files:
                try:
                    os.chmod(os.path.join(root, name), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.work, ignore_errors=True)

    def model(self, code: str, body: bytes = b"a language model\n", directory: str = None) -> str:
        path = os.path.join(directory or self.tessdata, code + ".traineddata")
        with open(path, "wb") as fh:
            fh.write(body)
        return path

    def mode(self, name: str) -> None:
        os.environ["MNEMON_TEST_TESSERACT_MODE"] = name

    def call_lines(self) -> list:
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, "r", encoding="utf-8") as fh:
            return fh.read().splitlines()

    def probe(self, languages: str = "eng", deadline=None, **kw):
        return ot.probe(languages, deadline=deadline or proc.Deadline(30), **kw)

    def recognize(self, languages: str = "eng", deadline=None):
        p = self.probe(languages)
        self.assertTrue(p.ok, p)
        return p, ot.recognize(self.raw, self.scratch, p, languages, deadline=deadline or proc.Deadline(30))

    def assertFailure(self, w, cls, acquisition, reason, retryable, stage):
        self.assertEqual(w.failure, {"class": cls, "acquisition": acquisition, "reason": reason,
                                     "retryable": retryable}, w)
        self.assertEqual(w.stage, stage, w)
        self.assertFalse(w.deadline_hit)
        self.assertIsNone(w.text)
        self.assertIsNone(w.text_sha256)
        self.assertIsNone(w.words)
        self.assertIsNone(w.confidence)
        self.assertIsNotNone(w.error)
        self.assertNotIn("\n", w.error)
        self.assertLessEqual(len(w.error), 300)


# --- the sequence and the text -------------------------------------------------------------------------------

class ConstantsTests(unittest.TestCase):

    def test_the_fixed_names_limits_and_options(self):
        self.assertEqual((ot.ADAPTER, ot.BACKEND, ot.NORMALIZER), ("ocr-tesseract/1", "tesseract", "mnemon-ocr-text/1"))
        self.assertEqual(ot.OPTIONS, SPEC_OPTIONS)
        self.assertEqual(ot.PROBE_TIMEOUT, 10.0)
        self.assertEqual(ot.OUTPUT_MAX, 16 * 1024 * 1024)
        self.assertEqual(ot.STREAM_MAX, 1024 * 1024)


class LanguageSequenceTests(unittest.TestCase):

    def test_the_grammar_of_langs(self):
        self.assertEqual(ot.parse_languages("eng"), ["eng"])
        self.assertEqual(ot.parse_languages("rus+eng"), ["rus", "eng"])
        self.assertEqual(ot.parse_languages("eng+rus"), ["eng", "rus"])
        self.assertEqual(ot.parse_languages("chi_sim"), ["chi_sim"])
        self.assertEqual(ot.parse_languages("chi_sim_vert+eng"), ["chi_sim_vert", "eng"])
        eight = "+".join(["eng", "rus", "deu", "fra", "spa", "ita", "por", "nld"])
        self.assertEqual(len(ot.parse_languages(eight)), 8)

    def test_bad_sequences_are_refused(self):
        for bad in ("", "en", "ENG", "eng+", "+eng", "eng++rus", "eng\n", "eng ", "eng_", "eng-rus", "123",
                    "+".join(["eng", "rus", "deu", "fra", "spa", "ita", "por", "nld", "pol"]), None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ot.parse_languages(bad)

    def test_a_code_named_twice_is_refused(self):
        for bad in ("eng+eng", "rus+eng+rus"):
            with self.assertRaises(ValueError):
                ot.parse_languages(bad)


class NormalizeTests(unittest.TestCase):
    """`mnemon-ocr-text/1`: the line-ending and whitespace steps of §21.6 «Normalization», rule by rule."""

    def test_crlf_becomes_lf_first(self):
        self.assertEqual(ot.normalize("a\r\nb\r\n"), "a\nb\n")
        self.assertEqual(ot.normalize("a\r\n\r\nb"), "a\n\nb\n")     # one blank line, not three

    def test_cr_ff_and_vt_become_lf(self):
        self.assertEqual(ot.normalize("a\rb\fc\vd"), "a\nb\nc\nd\n")
        self.assertEqual(ot.normalize("a\f"), "a\n")

    def test_trailing_spaces_and_tabs_are_removed_from_every_line(self):
        self.assertEqual(ot.normalize("a  \t\nb\t \n"), "a\nb\n")

    def test_other_trailing_whitespace_and_leading_spaces_stay(self):
        self.assertEqual(ot.normalize("  a \n\tb"), "  a \n\tb\n")

    def test_leading_and_trailing_blank_lines_are_removed(self):
        self.assertEqual(ot.normalize("\n \n\t\na\n\n \n"), "a\n")

    def test_a_run_of_blank_lines_becomes_one(self):
        self.assertEqual(ot.normalize("a\n\n\n \n\t\nb\n\nc"), "a\n\nb\n\nc\n")

    def test_one_final_newline(self):
        self.assertEqual(ot.normalize("a"), "a\n")
        self.assertEqual(ot.normalize("a\n\n\n"), "a\n")

    def test_an_empty_or_whitespace_text_stays_empty(self):
        self.assertEqual(ot.normalize(""), "")
        self.assertEqual(ot.normalize(" \t\n\f\n\r\n\v"), "")

    def test_nothing_else_changes(self):
        text = "Ｆｕｌｌ­width — ‘quotes’ AI Al 1,240\n"
        self.assertEqual(ot.normalize(text), text)


# --- resolution ----------------------------------------------------------------------------------------------

class ResolveTests(_Witness):

    def setUp(self):
        _Witness.setUp(self)
        self.path_dir = os.path.join(self.work, "path")
        os.mkdir(self.path_dir)
        self.on_path = os.path.join(self.path_dir, "tesseract")
        shutil.copyfile(STUB, self.on_path)
        os.chmod(self.on_path, 0o755)

    def test_ocr_cmd_under_the_hooks_replaces_tesseract(self):
        os.environ["PATH"] = self.path_dir
        self.assertEqual(ot.resolve(), STUB)

    def test_an_empty_ocr_cmd_counts_as_unset(self):
        os.environ["PATH"] = self.path_dir
        os.environ["OCR_CMD"] = ""
        self.assertEqual(ot.resolve(), self.on_path)

    def test_ocr_cmd_naming_no_executable_is_no_tesseract_and_path_is_not_searched(self):
        os.environ["PATH"] = self.path_dir
        os.environ["OCR_CMD"] = os.path.join(self.work, "no-such-stub")
        self.assertIsNone(ot.resolve())

    def test_ocr_cmd_is_ignored_without_the_hooks_and_named_on_stderr(self):
        os.environ["PATH"] = self.path_dir
        del os.environ["MNEMON_TEST_HOOKS"]
        policy._warned = False
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ot.resolve(), self.on_path)
        self.assertIn("OCR_CMD", err.getvalue())
        self.assertIn("ignored", err.getvalue())

    def test_without_the_hooks_and_without_tesseract_on_path_the_probe_finds_none(self):
        empty = os.path.join(self.work, "empty")
        os.mkdir(empty)
        os.environ["PATH"] = empty
        del os.environ["MNEMON_TEST_HOOKS"]
        with contextlib.redirect_stderr(io.StringIO()):
            p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing, p.command), (False, "no-tesseract", ["tesseract"], None))
        self.assertEqual(self.call_lines(), [], "the stub named by OCR_CMD never ran")


# --- the probe -----------------------------------------------------------------------------------------------

class ProbeTests(_Witness):

    def test_a_complete_probe(self):
        p = self.probe("eng")
        self.assertTrue(p.ok, p)
        self.assertEqual(p.command, STUB)
        self.assertEqual((p.version, p.detail), ("5.5.4", "leptonica-1.87.0"))
        self.assertEqual(p.tessdata_dir, self.tessdata)
        self.assertEqual(p.available, ["eng", "osd"])
        self.assertEqual((p.present, p.missing, p.reason, p.error, p.deadline_hit), (["eng"], [], None, None, False))
        self.assertEqual(p.data, [["eng", sha256_file(os.path.join(self.tessdata, "eng.traineddata"))]])
        self.assertEqual(self.call_lines(), ["version", "list-langs"])

    def test_the_sequence_order_is_kept_in_present_and_data(self):
        self.model("rus", b"a rus language model\n")
        p = self.probe("rus+eng")
        self.assertTrue(p.ok, p)
        self.assertEqual(p.present, ["rus", "eng"])
        self.assertEqual([c for c, _ in p.data], ["rus", "eng"])
        self.assertEqual(p.available, ["eng", "osd", "rus"])

    def test_without_hashing_the_models_are_checked_but_not_hashed(self):
        p = self.probe("eng", hash_models=False)
        self.assertTrue(p.ok, p)
        self.assertIsNone(p.data)

    def test_the_language_model_directory_is_tessdata_prefix_when_it_is_set(self):
        other = os.path.join(self.work, "prefix")
        os.mkdir(other)
        path = self.model("eng", b"another eng language model\n", directory=other)
        os.environ["TESSDATA_PREFIX"] = other + "/"
        p = self.probe("eng")
        self.assertTrue(p.ok, p)
        self.assertEqual(p.tessdata_dir, other)
        self.assertEqual(p.data, [["eng", sha256_file(path)]])

    def test_a_missing_language_model_is_named_and_nothing_falls_back(self):
        p = self.probe("rus+eng")
        self.assertEqual((p.ok, p.reason, p.missing, p.present), (False, "model-missing", ["lang:rus"], ["eng"]))
        self.assertIsNone(p.data)
        with self.assertRaises(ValueError):
            ot.execution_key(p, "rus+eng")
        with self.assertRaises(ValueError):
            ot.recognize(self.raw, self.scratch, p, "rus+eng", deadline=proc.Deadline(30))
        self.assertEqual(self.call_lines(), ["version", "list-langs"], "no recognition under another sequence")

    def test_every_missing_model_is_named_in_the_sequences_order(self):
        p = self.probe("deu+eng+rus")
        self.assertEqual((p.reason, p.missing, p.present), ("model-missing", ["lang:deu", "lang:rus"], ["eng"]))

    def test_a_model_that_is_no_regular_file_is_missing(self):
        os.mkdir(os.path.join(self.tessdata, "rus.traineddata"))
        p = self.probe("rus+eng")
        self.assertEqual((p.reason, p.missing), ("model-missing", ["lang:rus"]))

    def test_no_stub_is_no_tesseract(self):
        os.environ["OCR_CMD"] = os.path.join(self.work, "no-such-stub")
        p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing), (False, "no-tesseract", ["tesseract"]))
        self.assertIsNone(p.version)
        self.assertEqual(self.call_lines(), [])

    def test_a_version_older_than_4(self):
        self.mode("version-old")
        p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing), (False, "version", ["version"]))
        self.assertEqual(p.version, "3.05.02")
        self.assertIsNone(p.tessdata_dir)
        self.assertEqual(self.call_lines(), ["version"])

    def test_no_version_line(self):
        self.mode("no-version")
        p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing, p.version), (False, "version", ["version"], None))

    def test_a_version_command_that_fails(self):
        stub = write_stub(self.work, "t-exit", "#!/bin/sh\nprintf 'tesseract 5.5.4\\n'\nexit 1\n")
        os.environ["OCR_CMD"] = stub
        p = self.probe()
        self.assertEqual((p.reason, p.missing), ("version", ["version"]))

    def test_version_hang_is_probe_timeout(self):
        self.mode("version-hang")
        start = time.monotonic()
        with mock.patch.object(ot, "PROBE_TIMEOUT", 0.5):
            p = self.probe()
        self.assertLess(time.monotonic() - start, 0.5 + proc.TERM_GRACE + 1.5)
        self.assertEqual((p.ok, p.reason, p.missing, p.deadline_hit), (False, "probe-timeout", ["version"], False))

    def test_version_hang_under_a_shorter_deadline_is_the_deadline(self):
        self.mode("version-hang")
        start = time.monotonic()
        p = self.probe(deadline=proc.Deadline(0.5))
        self.assertLess(time.monotonic() - start, 0.5 + proc.TERM_GRACE + 1.5)
        self.assertTrue(p.deadline_hit)
        self.assertEqual((p.ok, p.reason, p.missing), (False, None, []), "a deadline classifies nothing")

    def test_version_flood_is_probe_output_cap(self):
        self.mode("version-flood")
        p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing), (False, "probe-output-cap", ["version"]))

    def test_list_langs_over_its_limit_and_over_the_cap_name_the_model_directory(self):
        head = "#!/bin/sh\ncase \"$1\" in\n--version) printf 'tesseract 5.5.4\\n leptonica-1.87.0\\n';;\n"
        os.environ["OCR_CMD"] = write_stub(self.work, "t-sleep", head + "--list-langs) exec sleep 30;;\nesac\n")
        with mock.patch.object(ot, "PROBE_TIMEOUT", 0.5):
            p = self.probe()
        self.assertEqual((p.reason, p.missing, p.version), ("probe-timeout", ["model-directory"], "5.5.4"))
        os.environ["OCR_CMD"] = write_stub(self.work, "t-yes", head + "--list-langs) exec yes;;\nesac\n")
        p = self.probe()
        self.assertEqual((p.reason, p.missing), ("probe-output-cap", ["model-directory"]))

    # contract K2 ---------------------------------------------------------------------------------------------

    def test_k2_list_langs_failed_is_model_directory(self):
        self.mode("langs-fail")
        p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing), (False, "model-directory", ["model-directory"]))
        self.assertIsNone(p.tessdata_dir)

    def test_k2_a_header_without_a_directory_is_model_directory(self):
        self.mode("langs-no-dir")
        p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing), (False, "model-directory", ["model-directory"]))

    def test_k2_no_directory_known_to_the_stub_is_model_directory(self):
        del os.environ["MNEMON_TEST_TESSDATA_DIR"]
        p = self.probe()
        self.assertEqual((p.reason, p.missing), ("model-directory", ["model-directory"]))

    def test_k2_does_not_apply_when_tessdata_prefix_is_set(self):
        self.mode("langs-no-dir")
        os.environ["TESSDATA_PREFIX"] = self.tessdata
        p = self.probe()
        self.assertTrue(p.ok, p)
        self.assertEqual(p.tessdata_dir, self.tessdata)

    def test_k2_an_unreadable_language_model(self):
        path = self.model("rus", b"a rus language model\n")
        os.chmod(path, 0)
        if os.access(path, os.R_OK):
            self.skipTest("a file with mode 000 is readable to this user")
        for hashed in (True, False):
            p = self.probe("rus+eng", hash_models=hashed)
            self.assertEqual((p.ok, p.reason, p.missing), (False, "model-unreadable", ["lang:rus"]), hashed)
            self.assertIsNone(p.data)

    def test_k2_a_command_that_cannot_be_started(self):
        cmd = os.path.join(self.work, "not-executable")
        with open(cmd, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(cmd, 0o644)
        with mock.patch.object(ot, "resolve", return_value=cmd):
            p = self.probe()
        self.assertEqual((p.ok, p.reason, p.missing), (False, "tesseract-start", ["tesseract"]))

    # the recorded output of the real tool ----------------------------------------------------------------------

    def test_the_recorded_version_lines(self):
        with open(os.path.join(RECORDED, "version.txt"), "rb") as fh:
            data = fh.read()
        self.assertTrue(data.split(b"\n")[1].startswith(b" leptonica-"), "the recording keeps the leading space")
        self.assertEqual(ot.parse_version(data), ("5.5.4", "leptonica-1.87.0", True))

    def test_the_recorded_list_langs(self):
        with open(os.path.join(RECORDED, "list-langs.txt"), "rb") as fh:
            self.assertEqual(ot.parse_list_langs(fh.read()), ("/opt/homebrew/share/tessdata", ["eng", "osd"]))

    def test_the_list_langs_header_forms(self):
        self.assertEqual(ot.parse_list_langs(b'List of available languages in "/a/b/" (2):\neng\nosd\n'),
                         ("/a/b", ["eng", "osd"]))
        self.assertEqual(ot.parse_list_langs(b"List of available languages (2):\nosd\neng\n"), (None, ["eng", "osd"]))
        self.assertEqual(ot.parse_list_langs(b"eng\nosd\n"), (None, []))
        self.mode("langs-no-dir")
        os.environ["TESSDATA_PREFIX"] = self.tessdata
        self.assertEqual(self.probe().available, ["eng", "osd"])

    def test_the_recorded_version_through_the_probe(self):
        os.environ["OCR_CMD"] = write_stub(self.work, "replay", REPLAY)
        os.environ["REPLAY_DIR"] = RECORDED
        p = self.probe()
        self.assertTrue(p.ok, p)
        self.assertEqual((p.version, p.detail), ("5.5.4", "leptonica-1.87.0"))

    def test_version_lines_are_stripped_and_a_leading_v_is_dropped(self):
        self.assertEqual(ot.parse_version(b"  tesseract v5.0.0-alpha.20201127  \n  leptonica-1.80.0\n"),
                         ("5.0.0-alpha.20201127", "leptonica-1.80.0", True))
        self.assertEqual(ot.parse_version(b"tesseract 5.5.4\n"), ("5.5.4", None, True))
        self.assertFalse(ot.parse_version(b"")[2])
        self.assertFalse(ot.parse_version(b"tesseract vx\n")[2])


class ProbeDocumentTests(_Witness):
    """`mnemon-ocr-probe/1` against the fixture `tests/fixtures/capture/reports/ocr-probe.json`, case by case."""

    def setUp(self):
        _Witness.setUp(self)
        self.fixture = read_json(os.path.join(REPORTS_FIX, "ocr-probe.json"))

    def check(self, case: str, doc: dict):
        fix = self.fixture[case]
        self.assertEqual(list(doc), PROBE_KEYS)
        assert_same_keys(self, doc, fix, "ocr-probe %s" % case)
        for key in ("schema", "ok", "adapter", "backend", "languages", "missing", "reason"):
            self.assertEqual(doc[key], fix[key], "%s: %s" % (case, key))
        json.dumps(doc)

    def test_every_case_of_the_fixture(self):
        self.model("rus", b"a rus language model\n")
        self.check("present", ot.probe_document(self.probe("rus+eng", hash_models=False), "rus+eng"))
        os.remove(os.path.join(self.tessdata, "rus.traineddata"))
        self.check("model-missing", ot.probe_document(self.probe("rus+eng", hash_models=False), "rus+eng"))
        self.mode("version-old")
        self.check("version-old", ot.probe_document(self.probe("rus+eng", hash_models=False), "rus+eng"))
        self.mode("version-hang")
        with mock.patch.object(ot, "PROBE_TIMEOUT", 0.5):
            self.check("probe-timeout", ot.probe_document(self.probe("rus+eng", hash_models=False), "rus+eng"))
        os.environ["OCR_CMD"] = os.path.join(self.work, "no-such-stub")
        self.check("no-tesseract", ot.probe_document(self.probe("rus+eng", hash_models=False), "rus+eng"))

    def test_a_probe_cut_by_its_own_deadline_is_probe_timeout(self):
        self.mode("version-hang")
        doc = ot.probe_document(self.probe(deadline=proc.Deadline(0.5)), "eng")
        self.assertEqual((doc["ok"], doc["reason"], doc["missing"]), (False, "probe-timeout", ["version"]))
        head = "#!/bin/sh\ncase \"$1\" in\n--version) printf 'tesseract 5.5.4\\n';;\n"
        os.environ["OCR_CMD"] = write_stub(self.work, "t-sleep", head + "--list-langs) exec sleep 30;;\nesac\n")
        doc = ot.probe_document(self.probe(deadline=proc.Deadline(0.7)), "eng")
        self.assertEqual((doc["reason"], doc["missing"]), ("probe-timeout", ["model-directory"]))

    def test_the_document_of_a_complete_probe(self):
        doc = ot.probe_document(self.probe("eng", hash_models=False), "eng")
        self.assertEqual(doc, {"schema": "mnemon-ocr-probe/1", "ok": True, "adapter": "ocr-tesseract/1",
                               "backend": "tesseract", "version": "5.5.4", "detail": "leptonica-1.87.0",
                               "tessdata_dir": self.tessdata, "languages": "eng", "available": ["eng", "osd"],
                               "present": ["eng"], "missing": [], "reason": None, "error": None})


# --- the execution key ---------------------------------------------------------------------------------------

class ExecutionKeyTests(_Witness):

    def test_the_key_is_the_sha256_of_the_canonical_json(self):
        p = self.probe("eng")
        key = ot.execution_key(p, "eng")
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(key, spec_key(p, "eng"))

    def test_one_stub_version_and_one_model_set_give_one_key(self):
        first = ot.execution_key(self.probe("eng"), "eng")
        self.assertEqual(ot.execution_key(self.probe("eng"), "eng"), first)
        copy = os.path.join(self.work, "another-place-for-the-stub.py")
        shutil.copyfile(STUB, copy)
        os.chmod(copy, 0o755)
        os.environ["OCR_CMD"] = copy
        self.assertEqual(ot.execution_key(self.probe("eng"), "eng"), first, "the command's path is not in the key")

    def test_another_version_gives_another_key(self):
        first = ot.execution_key(self.probe("eng"), "eng")
        self.mode("version-b")
        p = self.probe("eng")
        self.assertEqual(p.version, "5.5.5")
        self.assertNotEqual(ot.execution_key(p, "eng"), first)

    def test_another_language_model_file_gives_another_key(self):
        first = ot.execution_key(self.probe("eng"), "eng")
        self.model("eng", b"an eng language model, retrained\n")
        self.assertNotEqual(ot.execution_key(self.probe("eng"), "eng"), first)

    def test_another_language_model_directory_gives_another_key(self):
        before = self.probe("eng")
        other = os.path.join(self.work, "other-tessdata")
        shutil.copytree(self.tessdata, other)
        os.environ["MNEMON_TEST_TESSDATA_DIR"] = other
        p = self.probe("eng")
        self.assertEqual(p.data, before.data, "the same language model bytes")
        self.assertEqual(p.tessdata_dir, other)
        self.assertNotEqual(ot.execution_key(p, "eng"), ot.execution_key(before, "eng"))

    def test_another_adapter_version_gives_another_key(self):
        p = self.probe("eng")
        first = ot.execution_key(p, "eng")
        with mock.patch.object(ot, "ADAPTER", "ocr-tesseract/2"):
            self.assertNotEqual(ot.execution_key(p, "eng"), first)

    def test_the_sequence_as_bound_is_in_the_key(self):
        self.model("rus", b"a rus language model\n")
        a = ot.execution_key(self.probe("rus+eng"), "rus+eng")
        b = ot.execution_key(self.probe("eng+rus"), "eng+rus")
        self.assertNotEqual(a, b)

    def test_the_detail_line_is_in_the_key(self):
        p = self.probe("eng")
        first = ot.execution_key(p, "eng")
        p.detail = "leptonica-1.88.0"
        self.assertNotEqual(ot.execution_key(p, "eng"), first)

    def test_no_key_without_hashed_models(self):
        with self.assertRaises(ValueError):
            ot.execution_key(self.probe("eng", hash_models=False), "eng")


# --- the run -------------------------------------------------------------------------------------------------

class RecognizeTests(_Witness):

    def test_the_argv_of_21_3(self):
        seen = []
        real = proc.run_bounded

        def spy(argv, **kw):
            seen.append((list(argv), kw))
            return real(argv, **kw)

        p = self.probe("eng")
        deadline = proc.Deadline(30)
        with mock.patch.object(proc, "run_bounded", spy):
            w = ot.recognize(self.raw, self.scratch, p, "eng", deadline=deadline)
        self.assertIsNone(w.failure, w)
        argv, kw = seen[-1]
        self.assertEqual(argv, [STUB, self.raw, os.path.join(self.scratch, "out"), "--tessdata-dir", self.tessdata,
                                "-l", "eng", "--psm", "3", "-c", "tessedit_create_txt=1", "-c",
                                "tessedit_create_tsv=1"])
        self.assertNotIn("txt", argv)
        self.assertNotIn("tsv", argv)
        self.assertIs(kw.get("deadline"), deadline)
        self.assertTrue(kw.get("own_group"))
        self.assertEqual(kw.get("max_stdout"), 1024 * 1024)
        self.assertEqual(kw.get("max_stderr"), 1024 * 1024)

    def test_a_clean_run(self):
        p, w = self.recognize("eng")
        self.assertIsNone(w.failure, w)
        self.assertEqual(w.stage, "output")
        expected = ot.normalize(STUB_TEXT)
        self.assertEqual(w.text, expected)
        self.assertEqual(w.text_sha256, hashlib.sha256(expected.encode("utf-8")).hexdigest())
        self.assertEqual(w.words, len(expected.split()))
        self.assertEqual(w.words, 100)
        self.assertEqual((w.missing, w.error, w.deadline_hit), ([], None, False))
        with open(os.path.join(self.scratch, "out.tsv"), "r", encoding="utf-8") as fh:
            rows = [r.split("\t") for r in fh.read().splitlines()[1:]]
        confs = [Fraction(r[10]) for r in rows if r[0] == "5" and r[11] != "" and Fraction(r[10]) >= 0]
        self.assertEqual(w.confidence["items"], 100)
        self.assertEqual(w.confidence["unit"], "tesseract-word-0-100")
        self.assertAlmostEqual(w.confidence["mean"], float(sum(confs) / len(confs)), delta=0.05)
        self.assertAlmostEqual(w.confidence["min"], float(min(confs)), delta=0.05)
        self.assertEqual(self.call_lines(), ["version", "list-langs", "run"], "one line per call")

    def test_the_text_modes(self):
        for mode, text, words in (("empty", "", None), ("whitespace", "", None),
                                  ("short", "Field notes on a small reading machine\n", 7)):
            self.mode(mode)
            _, w = self.recognize("eng")
            self.assertIsNone(w.failure, (mode, w))
            self.assertEqual(w.text, text, mode)
            self.assertEqual(w.words, len(text.split()), mode)
            self.assertEqual(w.text_sha256, hashlib.sha256(text.encode("utf-8")).hexdigest(), mode)
            if words is None:
                self.assertIsNone(w.confidence, "%s: no word row" % mode)
            else:
                self.assertEqual(w.confidence["items"], words, mode)

    def test_half_double_and_junk(self):
        stub_words = set(STUB_TEXT.lower().split())
        self.mode("half")
        _, w = self.recognize("eng")
        self.assertEqual(w.words, 50)
        self.assertTrue(ot.normalize(STUB_TEXT).startswith(w.text.rstrip("\n")))
        self.mode("double")
        _, w = self.recognize("eng")
        self.assertTrue(w.text.startswith(ot.normalize(STUB_TEXT).rstrip("\n")))
        extra = w.text[len(ot.normalize(STUB_TEXT)):].split()
        self.assertGreater(len(extra), 20, "more than the allowance")
        self.assertFalse(stub_words & set(x.lower() for x in extra))
        self.mode("junk")
        _, w = self.recognize("eng")
        self.assertGreater(w.words, 0)
        self.assertFalse(stub_words & set(x.lower().strip(".") for x in w.text.split()))

    def test_decode(self):
        self.mode("decode")
        _, w = self.recognize()
        self.assertFailure(w, "unsupported", "corrupt", "recognizer-decode", False, "witness")
        self.assertIn("libpng error", w.error)

    def test_too_large(self):
        self.mode("too-large")
        _, w = self.recognize()
        self.assertFailure(w, "too-large", "too-large", "recognizer-limit", False, "witness")

    def test_another_exit(self):
        self.mode("fail")
        _, w = self.recognize()
        self.assertFailure(w, "convert", None, "recognizer-exit", False, "witness")

    def test_stdout_and_stderr_floods(self):
        for mode in ("stdout-flood", "stderr-flood"):
            self.mode(mode)
            _, w = self.recognize()
            self.assertFailure(w, "convert", None, "output-cap", False, "witness")

    def test_partial_lang_exit_0_is_not_proof(self):
        self.model("rus", b"a rus language model\n")
        self.mode("partial-lang")
        _, w = self.recognize("rus+eng")
        self.assertFailure(w, "environment", None, "model-load", True, "witness")
        self.assertEqual(w.missing, ["lang:rus"])
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "out.txt")), "the outputs are discarded")
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "out.tsv")))

    def test_no_lang(self):
        self.model("rus", b"a rus language model\n")
        self.mode("no-lang")
        for languages in ("rus+eng", "rus"):
            _, w = self.recognize(languages)
            self.assertFailure(w, "environment", None, "model-load", True, "witness")
            self.assertEqual(w.missing, ["lang:rus"])

    def test_non_utf8(self):
        self.mode("non-utf8")
        _, w = self.recognize()
        self.assertFailure(w, "convert", None, "output-encoding", False, "output")

    def test_missing_and_unparsable_outputs(self):
        for mode in ("no-tsv", "bad-tsv", "big-out"):
            self.mode(mode)
            _, w = self.recognize()
            self.assertFailure(w, "convert", None, "output", False, "output")

    def test_the_output_cap_is_checked_by_stat(self):
        _, w = self.recognize()
        self.assertIsNone(w.failure)
        biggest = max(os.path.getsize(os.path.join(self.scratch, n)) for n in ("out.txt", "out.tsv"))
        with mock.patch.object(ot, "OUTPUT_MAX", biggest):
            _, w = self.recognize()
            self.assertIsNone(w.failure, "a file at the cap is within it")
        with mock.patch.object(ot, "OUTPUT_MAX", biggest - 1):
            _, w = self.recognize()
            self.assertFailure(w, "convert", None, "output", False, "output")

    def test_a_command_that_cannot_be_started(self):
        copy = os.path.join(self.work, "stub-copy.py")
        shutil.copyfile(STUB, copy)
        os.chmod(copy, 0o755)
        os.environ["OCR_CMD"] = copy
        p = self.probe()
        self.assertTrue(p.ok, p)
        os.chmod(copy, 0o644)
        w = ot.recognize(self.raw, self.scratch, p, "eng", deadline=proc.Deadline(30))
        self.assertFailure(w, "environment", None, "tesseract-start", True, "witness")

    def test_a_hang_cut_by_the_deadline_classifies_nothing(self):
        self.mode("hang")
        p = self.probe()
        start = time.monotonic()
        w = ot.recognize(self.raw, self.scratch, p, "eng", deadline=proc.Deadline(1.0))
        self.assertLess(time.monotonic() - start, 1.0 + proc.TERM_GRACE + 1.5)
        self.assertTrue(w.deadline_hit)
        self.assertIsNone(w.failure)
        self.assertIsNone(w.text)
        self.assertEqual(self.call_lines(), ["version", "list-langs", "run"])

    def test_the_output_stage_is_announced_before_the_outputs_are_looked_at(self):
        p = self.probe()
        for mode, announced in (("ok", ["output"]), ("non-utf8", ["output"]), ("no-tsv", ["output"]),
                                ("decode", []), ("partial-lang", []), ("stdout-flood", [])):
            self.mode(mode)
            stages = []
            w = ot.recognize(self.raw, self.scratch, p, "eng", deadline=proc.Deadline(30), on_stage=stages.append)
            self.assertEqual(stages, announced, mode)
            self.assertEqual(w.stage, "output" if announced else "witness", mode)
        self.mode("hang")
        stages = []
        w = ot.recognize(self.raw, self.scratch, p, "eng", deadline=proc.Deadline(0.5), on_stage=stages.append)
        self.assertTrue(w.deadline_hit)
        self.assertEqual(stages, [])

    def test_a_probe_that_did_not_succeed_is_refused(self):
        with self.assertRaises(ValueError):
            ot.recognize(self.raw, self.scratch, ot.Probe(), "eng", deadline=proc.Deadline(30))

    # the recorded stderr of the real tool ----------------------------------------------------------------------

    def replay(self, case: str):
        cases = read_json(os.path.join(RECORDED, "cases.json"))["cases"]
        c = cases[case]
        os.environ["OCR_CMD"] = write_stub(self.work, "replay", REPLAY)
        os.environ["REPLAY_DIR"] = RECORDED
        os.environ["REPLAY_STDERR"] = c["stderr"]
        os.environ["REPLAY_EXIT"] = str(c["exit"])
        os.environ["REPLAY_OUTPUTS"] = "1" if c["exit"] == 0 else "0"
        self.model("rus", b"a rus language model\n")
        return self.recognize(c["languages"])[1]

    def test_the_recorded_clean_run(self):
        w = self.replay("run")
        self.assertIsNone(w.failure, w)
        with open(os.path.join(RECORDED, "out.txt"), "r", encoding="utf-8") as fh:
            self.assertEqual(w.text, ot.normalize(fh.read()))
        self.assertIsNone(w.error, "the diagnostic of a clean run goes nowhere")
        self.assertEqual(w.confidence, {"unit": "tesseract-word-0-100", "mean": 94.1, "min": 73.3, "items": 17})

    def test_the_recorded_partial_and_no_language_loads(self):
        for case in ("partial-lang", "no-lang"):
            w = self.replay(case)
            self.assertFailure(w, "environment", None, "model-load", True, "witness")
            self.assertEqual(w.missing, ["lang:rus"], case)
        for case in ("partial-lang", "no-lang"):
            with open(os.path.join(RECORDED, case + ".stderr"), "rb") as fh:
                self.assertEqual(ot.load_failures(fh.read(), ["rus", "eng"]), ["lang:rus"], case)

    def test_the_recorded_failed_runs(self):
        w = self.replay("too-large")
        self.assertFailure(w, "too-large", "too-large", "recognizer-limit", False, "witness")
        w = self.replay("decode")
        self.assertFailure(w, "unsupported", "corrupt", "recognizer-decode", False, "witness")


class StderrRulesTests(unittest.TestCase):

    def test_the_load_messages(self):
        self.assertIsNone(ot.load_failures(b"Estimating resolution as 424\n", ["eng"]))
        self.assertEqual(ot.load_failures(b"  Failed loading language 'rus'  \n", ["rus", "eng"]), ["lang:rus"])
        self.assertEqual(ot.load_failures(b"Tesseract couldn't load any languages!\n", ["rus", "eng"]),
                         ["lang:rus", "lang:eng"])
        self.assertEqual(ot.load_failures(b"Failed loading language 'osd'\n", ["eng"]), ["lang:osd"],
                         "a language that a language model loads on its own")
        self.assertEqual(ot.load_failures(b"Failed loading language 'osd'\nFailed loading language 'eng'\n"
                                          b"Failed loading language 'eng'\n", ["rus", "eng"]), ["lang:eng", "lang:osd"])

    def test_the_failed_run_messages(self):
        self.assertEqual(ot.exit_failure(b"Image too large: (40000, 10)\n")["reason"], "recognizer-limit")
        for line in (b"Error in pixRead: pix not read", b"Error in pixReadStream", b"libpng error: CRC error",
                     b"Unsupported image type."):
            self.assertEqual(ot.exit_failure(line)["reason"], "recognizer-decode", line)
        self.assertEqual(ot.exit_failure(b"Error during processing.\n")["reason"], "recognizer-exit")
        self.assertEqual(ot.exit_failure(b"")["reason"], "recognizer-exit")


class ConfidenceTests(unittest.TestCase):
    """The `.tsv` header and the confidence arithmetic of §21.3 on hand-made files."""

    HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"

    def tsv(self, *rows) -> bytes:
        return (self.HEAD + "".join(r + "\n" for r in rows)).encode("utf-8")

    @staticmethod
    def word(conf: str, text: str = "word", level: int = 5) -> str:
        return "%d\t1\t1\t1\t1\t1\t10\t10\t40\t20\t%s\t%s" % (level, conf, text)

    def test_word_rows_only(self):
        data = self.tsv("1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t", self.word("90"), self.word("91.000000"),
                        self.word("0"), self.word("-1"), self.word("99", ""), self.word("50", "line", level=4))
        self.assertEqual(ot.parse_tsv(data), {"unit": "tesseract-word-0-100", "mean": 60.3, "min": 0.0, "items": 3})

    def test_one_decimal_rounded_half_to_even(self):
        self.assertEqual(ot.parse_tsv(self.tsv(self.word("10.25"), self.word("10.25")))["mean"], 10.2)
        self.assertEqual(ot.parse_tsv(self.tsv(self.word("10.35"), self.word("10.35")))["mean"], 10.4)
        self.assertEqual(ot.parse_tsv(self.tsv(self.word("10.2"), self.word("10.3")))["mean"], 10.2)
        self.assertEqual(ot.parse_tsv(self.tsv(self.word("1.05"), self.word("2")))["min"], 1.0)
        self.assertEqual(ot.parse_tsv(self.tsv(self.word("1.15"), self.word("2")))["min"], 1.2)

    def test_no_word_row_is_null(self):
        self.assertIsNone(ot.parse_tsv(self.tsv()))
        self.assertIsNone(ot.parse_tsv(self.tsv("1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t", self.word("-1"))))

    def test_what_does_not_parse(self):
        bad = (b"", b"level\tpage_num\n", self.HEAD.replace("conf", "confidence").encode("utf-8"),
               self.tsv("5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t90"), self.tsv("5\t1\tx\t1\t1\t1\t10\t10\t40\t20\t90\tw"),
               self.tsv(self.word("high")), self.tsv(self.word("nan")), self.tsv(""), self.tsv(self.word("90", "a\tb")),
               self.tsv(self.word("90")).replace(b"word", b"\xff\xfe"))
        for data in bad:
            with self.assertRaises(ot.OutputError, msg=repr(data[-60:])):
                ot.parse_tsv(data)

    def test_the_recorded_tsv_parses(self):
        with open(os.path.join(RECORDED, "out.tsv"), "rb") as fh:
            self.assertEqual(ot.parse_tsv(fh.read()),
                             {"unit": "tesseract-word-0-100", "mean": 94.1, "min": 73.3, "items": 17})


# --- the report object ---------------------------------------------------------------------------------------

class WitnessReportTests(_Witness):
    """The `witness` object of §21.7, stage by stage, against `tests/fixtures/capture/reports/image.json`."""

    def setUp(self):
        _Witness.setUp(self)
        self.fixture = read_json(os.path.join(REPORTS_FIX, "image.json"))

    def check(self, stage: str, obj: dict):
        fix = self.fixture[stage]["witness"]
        self.assertEqual(list(obj), WITNESS_KEYS)
        assert_same_keys(self, obj, fix, "witness at %s" % stage)
        self.assertEqual({k: obj[k] is None for k in obj}, {k: fix[k] is None for k in fix}, stage)
        self.assertEqual((obj["adapter"], obj["backend"], obj["options"]), ("ocr-tesseract/1", "tesseract", SPEC_OPTIONS))
        json.dumps(obj)

    def test_envelope(self):
        self.check("envelope", ot.witness_report(None, "eng", None))

    def test_probe(self):
        p = self.probe("rus+eng")
        obj = ot.witness_report(p, "rus+eng", None)
        self.check("probe", obj)
        self.assertEqual(obj["missing"], ["lang:rus"])

    def test_witness_and_output(self):
        self.mode("decode")
        p, w = self.recognize("eng")
        self.check("witness", ot.witness_report(p, "eng", w))
        self.mode("non-utf8")
        p, w = self.recognize("eng")
        self.check("output", ot.witness_report(p, "eng", w))

    def test_done(self):
        p, w = self.recognize("eng")
        obj = ot.witness_report(p, "eng", w)
        self.check("done", obj)
        self.assertEqual((obj["text_sha256"], obj["words"], obj["confidence"], obj["missing"]),
                         (w.text_sha256, 100, w.confidence, []))
        self.assertEqual(obj["data"], p.data)

    def test_a_language_that_did_not_load(self):
        self.model("rus", b"a rus language model\n")
        self.mode("partial-lang")
        p, w = self.recognize("rus+eng")
        obj = ot.witness_report(p, "rus+eng", w)
        self.check("witness", obj)
        self.assertEqual(obj["missing"], ["lang:rus"])


# --- the stub itself -----------------------------------------------------------------------------------------

class StubTests(_Witness):

    def test_the_stub_refuses_a_configuration_file(self):
        out = subprocess.run([STUB, self.raw, os.path.join(self.scratch, "out"), "--tessdata-dir", self.tessdata,
                              "-l", "eng", "txt", "tsv"], capture_output=True, timeout=30)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn(b"configuration file", out.stderr)
        self.assertEqual(os.listdir(self.scratch), [])

    def test_the_stub_writes_only_what_the_variables_ask_for(self):
        base = [STUB, self.raw, os.path.join(self.scratch, "out"), "--tessdata-dir", self.tessdata, "-l", "eng",
                "--psm", "3"]
        out = subprocess.run(base + ["-c", "tessedit_create_tsv=1"], capture_output=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(os.listdir(self.scratch)), ["out.tsv"])
        os.remove(os.path.join(self.scratch, "out.tsv"))
        out = subprocess.run(base, capture_output=True, timeout=30)
        self.assertEqual((out.returncode, os.listdir(self.scratch)), (0, []))
        self.assertEqual(self.call_lines(), ["run", "run"])

    def test_hang_child_leaves_a_child_in_the_stubs_group(self):
        env = dict(os.environ, MNEMON_TEST_TESSERACT_MODE="hang-child")
        child = subprocess.Popen([STUB, self.raw, os.path.join(self.scratch, "out")], env=env, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pgid = child.pid
        try:
            end = time.monotonic() + 10
            while len(group_members(pgid)) < 2 and time.monotonic() < end:
                time.sleep(0.05)
            self.assertGreaterEqual(len(group_members(pgid)), 2, "the stub and its child share the stub's group")
            os.kill(child.pid, signal.SIGKILL)
            child.wait()
            self.assertTrue(group_alive(pgid), "the child outlives the stub inside its group")
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            child.wait()
        end = time.monotonic() + 5
        while group_alive(pgid) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertFalse(group_alive(pgid))


if __name__ == "__main__":
    unittest.main()
