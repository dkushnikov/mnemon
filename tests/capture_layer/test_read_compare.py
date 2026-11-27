"""§21.5 (the comparison of two readings of one image, rule `mnemon-read-compare/1`), §21.7 (the `comparison`
object of the report and the `failure` of a refusal) and §21.13 «Comparison fixtures»: every fixture of
`tests/fixtures/capture/image/compare/` gives its whole comparison object; the floor's arithmetic is exact; the
share is rounded half to even from the integers; the unconfirmed lists are cut at 200; the function is pure."""
from __future__ import annotations

import ast
import builtins
import io
import json
import math
import os
import subprocess
import unittest
from decimal import Decimal
from fractions import Fraction
from unittest import mock

from _helpers import BIN, FIX, read_json

from capture import read_compare as RC

COMPARE_FIX = os.path.join(FIX, "image", "compare")

# §21.13 «Comparison fixtures», item by item → the fixture that holds it
SPEC_FIXTURES = {
    "equal texts": ["equal-texts"],
    "the I/l pair": ["fold-i-l"],
    "a number with and without its separator": ["number-separator"],
    "junk of one and two letters": ["junk-short-runs"],
    "a superscript digit beside a word": ["superscript-digit"],
    "a word broken at a line end in the witness text and whole in the reading": ["hyphen-witness-broken"],
    "and the reverse": ["hyphen-reading-broken"],
    "a hyphen with a space before it (no pair)": ["hyphen-space-before"],
    "a hyphen without a line end (no pair)": ["hyphen-no-line-end"],
    "a pair whose half the other text also holds as a word of its own": ["hyphen-half-also-word",
                                                                          "hyphen-pool-one-word"],
    "two pairs that would share a run (the left one wins)": ["hyphen-pairs-share-run"],
    "a hyphen kept inside the reading's line (no join)": ["hyphen-kept-in-line"],
    "the same texts with the reading's word joined (one join)": ["hyphen-kept-then-joined"],
    "two short halves that make one word": ["hyphen-short-halves"],
    "188 of 209 words matched at a floor of 90 (loss)": ["floor-188-of-209"],
    "20 and 21 words missing at a share under and over the floor": [
        "floor-20-missing-under", "floor-21-missing-under", "floor-21-missing-over", "floor-20-missing-over"],
    "a short text with three junk words": ["quote-card-junk"],
    "a block dropped": ["block-dropped"],
    "a block added": ["block-added"],
    "a block written twice": ["block-twice"],
    "no word in common": ["no-word-in-common"],
    "a reading without a word of three letters": ["reading-no-word"],
    "an empty witness text": ["empty-witness"],
}

# the table of edges of §21.5, row by row: fixture → (verdict, share); `no-text` is refused before the comparison
EDGE_ROWS = {
    "edge-empty-reading": ("no-text", None),
    "edge-whitespace-reading": ("no-text", None),
    "edge-witness-no-word": ("no-confirmation", None),
    "edge-neither-word": ("agree", None),
    "edge-reading-no-word-20": ("agree", 0.0),
    "edge-reading-no-word-21": ("loss", 0.0),
    "edge-none-matched-loss-holds": ("no-confirmation", 0.0),
    "hyphen-short-halves": ("agree", 100.0),
}

REPORT_KEYS = ["rule", "verdict", "floor", "witness_words", "found", "missing", "share", "reading_words",
               "unconfirmed_words", "reading_numbers", "unconfirmed_numbers", "hyphen_joined", "unconfirmed"]
COUNT_KEYS = ["witness_words", "found", "missing", "reading_words", "unconfirmed_words", "reading_numbers",
              "unconfirmed_numbers", "hyphen_joined"]
FIXTURE_KEYS = {"name", "what", "reading", "witness", "min_share", "allowance", "expected"}

LOSS_FAILURE = {"class": "reading-mismatch", "acquisition": "words-missing", "reason": "loss", "retryable": False}
NO_CONFIRMATION_FAILURE = {"class": "reading-mismatch", "acquisition": "no-confirmation",
                           "reason": "no-confirmation", "retryable": False}


def load_fixtures():
    out = []
    for name in sorted(os.listdir(COMPARE_FIX)):
        if name.endswith(".json"):
            fixture = read_json(os.path.join(COMPARE_FIX, name))
            fixture["_file"] = name
            out.append(fixture)
    return out


FIXTURES = load_fixtures()
BY_NAME = {f["name"]: f for f in FIXTURES}
COMPARED = [f for f in FIXTURES if f["expected"] is not None]


def run(fixture, min_share=None, allowance=None):
    ms = fixture["min_share"] if min_share is None else min_share
    al = fixture["allowance"] if allowance is None else allowance
    c = RC.compare(fixture["reading"], fixture["witness"], min_share=ms, allowance=al)
    return c, RC.as_report(c, min_share=ms, allowance=al)


def words(n):
    """n distinct synthetic words of letters other than `l` (so that no two fold alike), three letters or more."""
    alphabet = "abcdefghijkmnopqrstuvwxyz"
    out = []
    for i in range(n):
        digits = ""
        for _ in range(3):
            digits = alphabet[i % 25] + digits
            i //= 25
        out.append("qq" + digits)
    return out


class FixtureSet(unittest.TestCase):

    def test_every_item_of_the_spec_list_has_its_fixture(self):
        for item, names in SPEC_FIXTURES.items():
            for name in names:
                self.assertIn(name, BY_NAME, "§21.13 «%s»: fixture %s missing" % (item, name))

    def test_every_row_of_the_edge_table_has_its_fixture(self):
        for name in EDGE_ROWS:
            self.assertIn(name, BY_NAME, "§21.5 edge row: fixture %s missing" % name)

    def test_fixture_shape(self):
        self.assertGreaterEqual(len(FIXTURES), 30)
        for f in FIXTURES:
            with self.subTest(fixture=f["_file"]):
                self.assertEqual(f["_file"], f["name"] + ".json")
                keys = set(f) - {"_file"}
                if f["expected"] is None:
                    self.assertEqual(keys, FIXTURE_KEYS | {"raises"})
                    self.assertEqual(f["raises"], "ValueError")
                else:
                    self.assertEqual(keys, FIXTURE_KEYS)
                    self.assertEqual(list(f["expected"]), REPORT_KEYS, "the key order of §21.7")
                    self.assertEqual(list(f["expected"]["floor"]), ["min_share", "allowance"])
                    self.assertEqual(list(f["expected"]["unconfirmed"]), ["words", "numbers", "truncated"])
                self.assertTrue(f["what"].strip())
                self.assertIsInstance(f["reading"], str)
                self.assertIsInstance(f["witness"], str)


class EveryFixture(unittest.TestCase):

    def test_every_fixture_gives_its_comparison_object(self):
        for f in FIXTURES:
            with self.subTest(fixture=f["name"]):
                if f["expected"] is None:
                    with self.assertRaises(ValueError):
                        run(f)
                    continue
                _, report = run(f)
                self.assertEqual(report, f["expected"])
                self.assertEqual(list(report), REPORT_KEYS)

    def test_counts_hold_together(self):
        for f in COMPARED:
            with self.subTest(fixture=f["name"]):
                c, report = run(f)
                self.assertEqual(c.found + c.missing, c.witness_words)
                self.assertEqual(c.reading_words - c.unconfirmed_words, c.found)
                self.assertEqual(len(c.unconfirmed_word_list), c.unconfirmed_words)
                self.assertEqual(len(c.unconfirmed_number_list), c.unconfirmed_numbers)
                self.assertLessEqual(c.unconfirmed_numbers, c.reading_numbers)
                self.assertEqual(c.share is None, c.witness_words == 0)
                for key in COUNT_KEYS:
                    self.assertIs(type(report[key]), int, key)

    def test_the_rows_of_the_edge_table(self):
        for name, (verdict, share) in EDGE_ROWS.items():
            f = BY_NAME[name]
            with self.subTest(fixture=name):
                if verdict == "no-text":
                    self.assertTrue(all(ch.isspace() or ch == chr(0xFEFF) for ch in f["reading"]))
                    with self.assertRaises(ValueError):
                        run(f)
                    continue
                c, _ = run(f)
                self.assertEqual((c.verdict, c.share), (verdict, share))
        # the conditions of the rows, read off the fixtures
        c, _ = run(BY_NAME["edge-witness-no-word"])
        self.assertEqual(c.witness_words, 0)
        self.assertGreater(c.reading_words, 0)
        c, _ = run(BY_NAME["edge-neither-word"])
        self.assertEqual((c.witness_words, c.reading_words), (0, 0))
        self.assertGreater(c.reading_numbers, 0, "the numbers are still counted")
        self.assertEqual((run(BY_NAME["edge-reading-no-word-20"])[0].reading_words,
                          run(BY_NAME["edge-reading-no-word-20"])[0].witness_words), (0, 20))
        self.assertEqual((run(BY_NAME["edge-reading-no-word-21"])[0].reading_words,
                          run(BY_NAME["edge-reading-no-word-21"])[0].witness_words), (0, 21))
        c, _ = run(BY_NAME["edge-none-matched-loss-holds"])
        self.assertEqual(c.found, 0)
        self.assertGreater(c.missing, BY_NAME["edge-none-matched-loss-holds"]["allowance"],
                           "the condition of loss holds too")
        c, _ = run(BY_NAME["hyphen-short-halves"])
        self.assertEqual((c.witness_words, c.hyphen_joined), (1, 1))

    def test_failure_of_follows_the_verdict(self):
        seen = set()
        for f in COMPARED:
            c, _ = run(f)
            seen.add(c.verdict)
            want = {"agree": None, "loss": LOSS_FAILURE, "no-confirmation": NO_CONFIRMATION_FAILURE}[c.verdict]
            self.assertEqual(RC.failure_of(c), want, f["name"])
        self.assertEqual(seen, {"agree", "loss", "no-confirmation"}, "the fixtures hold all three verdicts")

    def test_the_report_is_json_with_float_shares(self):
        for f in COMPARED:
            with self.subTest(fixture=f["name"]):
                _, report = run(f)
                self.assertEqual(json.loads(json.dumps(report)), report)
                self.assertIs(type(report["floor"]["min_share"]), float)
                self.assertIs(type(report["floor"]["allowance"]), int)
                self.assertIn(type(report["share"]), (float, type(None)))
                self.assertIs(type(report["unconfirmed"]["truncated"]), bool)
                self.assertEqual(report["rule"], "mnemon-read-compare/1")


class Floor(unittest.TestCase):

    def test_min_share_as_int_float_str_or_decimal_gives_one_report(self):
        for f in COMPARED:
            if f["min_share"] != 90:
                continue
            with self.subTest(fixture=f["name"]):
                reports = [run(f, min_share=form)[1]
                           for form in (90, 90.0, "90", "90.0", Decimal("90"), Decimal("90.00"))]
                for report in reports:
                    self.assertEqual(report, f["expected"])

    def test_the_ratio_is_exact_not_a_float_product(self):
        f = BY_NAME["floor-exact-tie"]
        self.assertTrue(100 * 161 < 64.4 * 250, "control: the float product lands above the exact 16100")
        self.assertFalse(Fraction(100 * 161) < Fraction("64.4") * 250)
        for form in (64.4, "64.4", Decimal("64.4"), Decimal("64.40")):
            c, report = run(f, min_share=form)
            self.assertEqual(c.verdict, "agree", repr(form))
            self.assertEqual(report["floor"]["min_share"], 64.4)

    def test_188_of_209_is_loss_shown_as_90(self):
        c, report = run(BY_NAME["floor-188-of-209"])
        self.assertEqual((c.found, c.witness_words, c.missing), (188, 209, 21))
        self.assertEqual(c.verdict, "loss", "89.95 % is under 90 %: the rounded share is never used")
        self.assertEqual(report["share"], 90.0)
        self.assertEqual(RC.failure_of(c), LOSS_FAILURE)

    def test_both_conditions_must_fail_for_a_refusal(self):
        f = BY_NAME["floor-188-of-209"]
        self.assertEqual(run(f, allowance=21)[0].verdict, "agree", "21 missing within an allowance of 21")
        self.assertEqual(run(f, min_share="89.9")[0].verdict, "agree", "18800 is not under 89.9 * 209 = 18789.1")
        self.assertEqual(run(f, min_share=Decimal("89.96"))[0].verdict, "loss")
        moved = run(f, min_share=80, allowance=40)[1]
        self.assertEqual(moved["verdict"], "agree")
        self.assertEqual(moved["floor"], {"min_share": 80.0, "allowance": 40}, "the report records its floor")

    def test_a_bad_floor_is_refused(self):
        f = BY_NAME["equal-texts"]
        for bad in ("ninety", "", float("nan"), float("inf"), Decimal("NaN"), None, "1/0"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RC.compare(f["reading"], f["witness"], min_share=bad, allowance=20)
        with self.assertRaises(TypeError):
            RC.compare(f["reading"], f["witness"], min_share=True, allowance=20)
        for bad in (20.0, "20", True, None):
            with self.assertRaises(TypeError, msg=repr(bad)):
                RC.compare(f["reading"], f["witness"], min_share=90, allowance=bad)
        with self.assertRaises(TypeError):
            RC.compare(b"bytes", "text", min_share=90, allowance=20)


class Share(unittest.TestCase):

    def share(self, found, total):
        vocabulary = words(total)
        reading = " ".join(vocabulary[:found]) + "\n"
        witness = " ".join(vocabulary) + "\n"
        c = RC.compare(reading, witness, min_share=90, allowance=20)
        self.assertEqual((c.found, c.witness_words), (found, total))
        return c.share

    def test_half_to_even_from_the_integers(self):
        # by hand: 100 * found / total, then half to even at the first decimal
        cases = [
            (1, 2000, 0.0),        # 0.05 → 0.0 (a float division gives 0.05000000000000000277 → 0.1)
            (3, 2000, 0.2),        # 0.15 → 0.2 (a float gives 0.14999999999999999445 → 0.1)
            (5, 2000, 0.2),        # 0.25 → 0.2
            (7, 2000, 0.4),        # 0.35 → 0.4
            (1799, 2000, 90.0),    # 89.95 → 90.0
            (1, 16, 6.2),          # 6.25 → 6.2
            (3, 16, 18.8),         # 18.75 → 18.8
            (2, 3, 66.7),          # 66.666… → 66.7
            (188, 209, 90.0),      # 89.952… → 90.0
            (186, 194, 95.9),      # 95.876… → 95.9, the example of §21.7
        ]
        for found, total, want in cases:
            with self.subTest(found=found, total=total):
                got = self.share(found, total)
                self.assertIs(type(got), float)
                self.assertEqual(got, want)
        self.assertNotEqual(round(100 * 3 / 2000, 1), 0.2, "control: the float route lands on the other side")

    def test_no_witness_word_no_share(self):
        c = RC.compare("hello there\n", "a 1 b\n", min_share=90, allowance=20)
        self.assertIsNone(c.share)
        self.assertIsNone(RC.as_report(c, min_share=90, allowance=20)["share"])


class Lists(unittest.TestCase):

    def test_each_list_is_cut_at_200_and_marked(self):
        extra = words(260)
        reading = "anchor " + " ".join(extra[:250]) + " " + " ".join(str(n) for n in range(3)) + "\n"
        c = RC.compare(reading, "anchor\n", min_share=90, allowance=20)
        self.assertEqual((c.verdict, c.unconfirmed_words, c.unconfirmed_numbers), ("agree", 250, 3))
        self.assertEqual(c.unconfirmed_word_list, extra[:250], "the comparison keeps all of them, in order")
        report = RC.as_report(c, min_share=90, allowance=20)
        self.assertEqual(RC.LIST_LIMIT, 200)
        self.assertEqual(report["unconfirmed"]["words"], extra[:200])
        self.assertEqual(report["unconfirmed"]["numbers"], ["0", "1", "2"])
        self.assertTrue(report["unconfirmed"]["truncated"])
        self.assertEqual(report["unconfirmed_words"], 250, "the count is the whole count")

    def test_the_numbers_list_alone_cut(self):
        reading = "anchor " + " ".join(str(n) for n in range(201)) + "\n"
        c = RC.compare(reading, "anchor\n", min_share=90, allowance=20)
        report = RC.as_report(c, min_share=90, allowance=20)
        self.assertEqual(len(report["unconfirmed"]["numbers"]), 200)
        self.assertEqual(report["unconfirmed"]["numbers"][-1], "199")
        self.assertEqual(report["unconfirmed"]["words"], [])
        self.assertTrue(report["unconfirmed"]["truncated"])

    def test_exactly_200_is_not_cut(self):
        extra = words(200)
        reading = "anchor " + " ".join(extra) + " " + " ".join(str(n) for n in range(200)) + "\n"
        c = RC.compare(reading, "anchor\n", min_share=90, allowance=20)
        report = RC.as_report(c, min_share=90, allowance=20)
        self.assertEqual(len(report["unconfirmed"]["words"]), 200)
        self.assertEqual(len(report["unconfirmed"]["numbers"]), 200)
        self.assertFalse(report["unconfirmed"]["truncated"])


class Tokens(unittest.TestCase):

    def counts(self, reading, witness):
        c = RC.compare(reading, witness, min_share=90, allowance=20)
        return c.witness_words, c.found, c.hyphen_joined

    def test_the_three_hyphens_make_a_pair_and_no_other_dash_does(self):
        for hyphen in ("-", chr(0x00AD), chr(0x2010)):
            with self.subTest(hyphen=hex(ord(hyphen))):
                self.assertEqual(self.counts("information\n", "infor" + hyphen + "\nmation\n"), (1, 1, 1))
        for dash in (chr(0x2011), chr(0x2012), chr(0x2013), chr(0x2014), chr(0x2212), "_"):
            with self.subTest(dash=hex(ord(dash))):
                self.assertEqual(self.counts("information\n", "infor" + dash + "\nmation\n"), (2, 0, 0))

    def test_the_gap_of_a_pair(self):
        reading = "information\n"
        self.assertEqual(self.counts(reading, "infor-\r\nmation\n"), (1, 1, 1), "a CRLF line end")
        self.assertEqual(self.counts(reading, "infor-\rmation\n"), (1, 1, 1), "a lone CR line end")
        self.assertEqual(self.counts(reading, "infor- \t \n\n \t mation\n"), (1, 1, 1), "spaces, tabs, blank lines")
        self.assertEqual(self.counts(reading, "infor--\nmation\n"), (2, 0, 0), "two hyphens")
        self.assertEqual(self.counts(reading, "infor-\n2 mation\n"), (2, 0, 0), "a digit in the gap")
        self.assertEqual(self.counts(reading, "infor-" + chr(0x00A0) + "\nmation\n"), (2, 0, 0),
                         "only spaces or tabs before the line end")

    def test_a_joined_pair_answers_once_and_its_halves_are_no_words(self):
        c = RC.compare("information infor mation\n", "infor-\nmation\n", min_share=90, allowance=20)
        self.assertEqual((c.witness_words, c.found, c.hyphen_joined), (1, 1, 1))
        self.assertEqual(c.unconfirmed_word_list, ["infor", "mation"])

    def test_numbers_are_matched_as_written(self):
        c = RC.compare("1,000 1.000 007 7\n", "1.000 7\n", min_share=90, allowance=20)
        self.assertEqual((c.reading_numbers, c.unconfirmed_number_list), (4, ["1,000", "007"]),
                         "a comma is not a dot, and 007 is not 7")
        c = RC.compare("12\n34\n", "12-\n34\n", min_share=90, allowance=20)
        self.assertEqual((c.reading_numbers, c.unconfirmed_numbers, c.hyphen_joined), (2, 0, 0))

    def test_letters_are_letters_of_any_script_and_a_mark_breaks_a_run(self):
        self.assertEqual(self.counts("дом λόγος\n", "дом λόγος\n"), (2, 2, 0))
        # a combining mark (category Mn) is no letter: 'cafe' + U+0301 + 'xyz' is two runs
        c = RC.compare("cafexyz\n", "cafe" + chr(0x0301) + "xyz\n", min_share=90, allowance=20)
        self.assertEqual((c.witness_words, c.found), (2, 0))


class Purity(unittest.TestCase):

    def test_reads_no_file_and_starts_no_process(self):
        def refuse(*a, **k):
            raise AssertionError("the comparison touched the system")
        with mock.patch.object(builtins, "open", refuse), mock.patch.object(io, "open", refuse), \
                mock.patch.object(os, "open", refuse), mock.patch.object(subprocess, "Popen", refuse), \
                mock.patch.object(os, "system", refuse):
            for f in COMPARED:
                c, _ = run(f)
                RC.failure_of(c)

    def test_same_input_twice_gives_equal_output(self):
        for f in COMPARED:
            with self.subTest(fixture=f["name"]):
                reading, witness = f["reading"], f["witness"]
                c1, r1 = run(f)
                c2, r2 = run(f)
                self.assertEqual(c1, c2)
                self.assertEqual(r1, r2)
                self.assertEqual((f["reading"], f["witness"]), (reading, witness))
                r1["unconfirmed"]["words"].append("x")
                self.assertEqual(c1.unconfirmed_word_list, c2.unconfirmed_word_list, "the report shares no list")

    def test_the_module_imports_no_file_process_or_clock(self):
        path = os.path.join(BIN, "capture", "read_compare.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, {"__future__", "re", "collections", "fractions", "typing"}, imported)

    def test_the_interface(self):
        self.assertEqual(RC.RULE, "mnemon-read-compare/1")
        self.assertEqual(RC.LIST_LIMIT, 200)
        self.assertEqual(list(RC.Comparison._fields), [
            "verdict", "witness_words", "found", "missing", "share", "reading_words", "unconfirmed_words",
            "reading_numbers", "unconfirmed_numbers", "hyphen_joined", "unconfirmed_word_list",
            "unconfirmed_number_list"])
        with self.assertRaises(ValueError):
            RC.failure_of(RC.Comparison("maybe", 0, 0, 0, None, 0, 0, 0, 0, 0, [], []))
        self.assertTrue(math.isclose(RC.as_report(run(BY_NAME["equal-texts"])[0], min_share="90.5",
                                                  allowance=0)["floor"]["min_share"], 90.5))


if __name__ == "__main__":
    unittest.main()
