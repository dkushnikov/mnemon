"""The live image gate's own tools, without the real tools: the generator, the scoring helper, the wrapper,
the lifecycle check and the gate's refusals (capture layer §21.13, the last row).

    python3 -m unittest discover -s tests/live/image -p 'test_*.py'

No network, no `claude`, no tesseract: the lifecycle cases start a fake supervisor, worker and client of
their own. Stdlib only, python 3.9.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
GATE = os.path.join(ROOT, "tests", "live", "image-gate.sh")
WRAP = os.path.join(HERE, "claude-wrap.py")
LIFECYCLE = os.path.join(HERE, "lifecycle.py")
sys.path.insert(0, HERE)

import make_inputs  # noqa: E402
import score  # noqa: E402


def truth(name: str) -> str:
    with open(os.path.join(HERE, "truth", name + ".txt"), encoding="utf-8") as fh:
        return fh.read()


def file_id(path: str) -> str:
    st = os.stat(path)
    return "%d:%d" % (st.st_dev, st.st_ino)


class Generator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="image-gate-inputs-")
        cls.first = make_inputs.make(os.path.join(cls.tmp, "a"))
        cls.second = make_inputs.make(os.path.join(cls.tmp, "b"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, run: str, name: str) -> bytes:
        with open(os.path.join(self.tmp, run, name), "rb") as fh:
            return fh.read()

    def test_two_runs_write_identical_bytes(self):
        self.assertEqual([d["name"] for d in self.first],
                         ["column-4200.png", "column-8000.png", "column-8001.png", "quote-exif6.jpg"])
        for d in self.first:
            self.assertEqual(self.read("a", d["name"]), self.read("b", d["name"]), d["name"])
        self.assertEqual(self.first, self.second)

    def test_the_columns_have_the_heights_named_in_their_headers(self):
        heights = {}
        for name in ("column-4200.png", "column-8000.png", "column-8001.png"):
            data = self.read("a", name)
            self.assertEqual(data[:8], make_inputs.PNG_SIGNATURE)
            length, kind = struct.unpack(">I4s", data[8:16])
            self.assertEqual((length, kind), (13, b"IHDR"))
            width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
            self.assertEqual((width, depth, colour), (1170, 8, 0), name)
            heights[name] = height
        self.assertEqual(heights, {"column-4200.png": 4200, "column-8000.png": 8000, "column-8001.png": 8001})

    def test_the_columns_are_copies_of_the_gray_post(self):
        with open(make_inputs.GRAY_POST, "rb") as fh:
            width, height, rows = make_inputs.decode_gray8(fh.read())
        self.assertEqual((width, height), (1170, 2100))
        _, _, c4200 = make_inputs.decode_gray8(self.read("a", "column-4200.png"))
        self.assertEqual(c4200, rows * 2)
        _, _, c8000 = make_inputs.decode_gray8(self.read("a", "column-8000.png"))
        copy = rows[make_inputs.CROP_TOP:make_inputs.CROP_TOP + make_inputs.CROP_ROWS]
        self.assertEqual(c8000, copy * 4)
        blank = bytes([255]) * width
        ink = [i for i, row in enumerate(rows) if row != blank]
        self.assertTrue(make_inputs.CROP_TOP <= ink[0] and ink[-1] < make_inputs.CROP_TOP + make_inputs.CROP_ROWS,
                        "every row with ink is inside a copy")
        _, _, c8001 = make_inputs.decode_gray8(self.read("a", "column-8001.png"))
        self.assertEqual(c8001, c8000 + [blank])

    def test_the_rotated_jpeg_is_the_card_with_one_exif_segment_of_orientation_6(self):
        with open(make_inputs.QUOTE_CARD, "rb") as fh:
            card = fh.read()
        rotated = self.read("a", "quote-exif6.jpg")
        segment = make_inputs.exif_orientation_segment(6)
        exif = [(s, e) for m, s, e in make_inputs._jpeg_segments(card) if m == 0xE1]
        self.assertEqual(len(exif), 1)
        s, e = exif[0]
        self.assertEqual(rotated, card[:s] + segment + card[e:])
        tiff = segment[10:]
        self.assertEqual(tiff[:4], b"MM\x00*")
        self.assertEqual(struct.unpack(">HHHIH", tiff[8:20]), (1, 0x0112, 3, 1, 6))
        self.assertEqual(make_inputs.jpeg_size(rotated), (1200, 660))

    def test_the_engine_envelope_check_reads_them_as_the_gate_expects(self):
        sys.path.insert(0, os.path.join(ROOT, "bin"))
        try:
            from capture import image
        except ImportError as exc:     # the envelope check belongs to another package of the build
            self.skipTest("capture.image is not importable: %s" % exc)
        want = {"column-4200.png": ("ok", None, 1), "column-8000.png": ("ok", None, 1),
                "column-8001.png": ("too-large", "edge", 1), "quote-exif6.jpg": ("rotated", None, 6)}
        for name, (outcome, reason, orientation) in want.items():
            env = image.inspect(os.path.join(self.tmp, "a", name), input_name=name, max_bytes=7000000,
                                max_pixels=50000000, max_edge=8000)
            self.assertEqual(env.outcome, outcome, name)
            if reason is not None:
                self.assertEqual(env.reason, reason, name)
            self.assertEqual(env.orientation, orientation, name)


class Scoring(unittest.TestCase):
    def test_a_truth_against_itself_passes(self):
        for name in ("post-en", "paper-2col", "quote-short", "cyrillic", "post-ai"):
            r = score.words_check(truth(name), [truth(name)])
            self.assertTrue(r["pass"], name)
            self.assertEqual(r["missing"], 0)
        self.assertEqual(score.words_check(truth("post-en"), [truth("post-en")])["truth_words"], 196)

    def test_one_word_removed_fails(self):
        text = truth("post-en")
        cut = text.replace(" nobody had changed", " had changed", 1)
        self.assertNotEqual(cut, text)
        r = score.words_check(cut, [text])
        self.assertFalse(r["pass"])
        self.assertEqual((r["missing"], r["missing_words"]), (1, [("nobody", 1)]))

    def test_multiplicity_counts(self):
        post = truth("post-en")
        once = score.words_check(post, [post], repeat=2)
        self.assertFalse(once["pass"])
        self.assertEqual(once["missing"], once["truth_words"] // 2)
        self.assertTrue(score.words_check(post + post, [post], repeat=2)["pass"])
        self.assertTrue(score.words_check(post * 4, [post], repeat=4)["pass"])
        # a word written once where the truth has it twice is missing once
        self.assertEqual(score.words_check(post.replace("twelve", "ten", 1), [post])["missing"], 1)

    def test_both_texts_of_the_block(self):
        both = [truth("post-en"), truth("paper-2col")]
        self.assertTrue(score.words_check(both[0] + both[1], both)["pass"])
        self.assertFalse(score.words_check(both[0], both)["pass"])

    def test_the_columns_in_order_pass_and_swapped_fail(self):
        page = truth("paper-2col")
        lines = page.split("\n")
        self.assertTrue(lines[5].startswith("1 Introduction") and lines[8].startswith("Our contributions"))
        self.assertTrue(score.order_check(page, page, (6, 8), (9, 11))["pass"])
        swapped = "\n".join(lines[:5] + lines[8:11] + lines[5:8] + lines[11:])
        self.assertEqual(sorted(score.words(swapped)), sorted(score.words(page)), "the same words")
        self.assertTrue(score.words_check(swapped, [page])["pass"], "the multiset check cannot see the order")
        r = score.order_check(swapped, page, (6, 8), (9, 11))
        self.assertFalse(r["pass"])
        self.assertEqual((r["left_found"], r["right_found"]), (1, 1))
        # a reading that lost the right column's first words cannot show the order
        lost = page.replace("Our contributions are threefold. ", "")
        self.assertFalse(score.order_check(lost, page, (6, 8), (9, 11))["pass"])

    def test_ai_right_25_of_25(self):
        text = truth("post-ai")
        r = score.ai_check(text, text)
        self.assertEqual((r["truth_ai"], r["right"], r["truth_al"], r["pass"]), (25, 25, 3, True))
        one_wrong = text.replace("AI safety", "Al safety", 1)
        r = score.ai_check(one_wrong, text)
        self.assertEqual((r["right"], r["pass"]), (24, False))
        # the model's measured error: the three real Al written AI — the 25 stand-alone AI are still right
        al_as_ai = text.replace("friend Al ", "friend AI ").replace(". Al says", ". AI says").replace("of Al and", "of AI and")
        r = score.ai_check(al_as_ai, text)
        self.assertEqual((r["right"], r["reading_ai"], r["reading_al"], r["pass"]), (25, 28, 0, True))
        self.assertFalse(score.ai_check(text.replace("AI", "Al"), text)["pass"])

    def test_the_cli_prints_numbers_only(self):
        tmp = tempfile.mkdtemp(prefix="image-gate-score-")
        try:
            path = os.path.join(tmp, "reading.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(truth("post-en").replace(" nobody had changed", " had changed", 1))
            res = subprocess.run([sys.executable, os.path.join(HERE, "score.py"), "words", "--reading", path,
                                  "--truth", os.path.join(HERE, "truth", "post-en.txt")],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(res.returncode, 1)
            self.assertNotIn(b"nobody", res.stdout + res.stderr)
            self.assertIn(b"missing 1", res.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --- the wrapper and the lifecycle check ----------------------------------------------------------------------

FAKE_CLIENT = r'''
import os, subprocess, sys, time
mode = sys.argv[1]
if mode == "detached":          # a descendant in a session of its own: it leaves the group, not the tree
    subprocess.Popen(["sleep", "30"], start_new_session=True, stdin=subprocess.DEVNULL)
elif mode == "in-group":        # a member of the worker's group that nobody waits for
    subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
time.sleep(0.6)
'''

FAKE_WORKER = r'''
import os, subprocess, sys
wrap, client, mode = sys.argv[1:4]
subprocess.run([wrap, mode], stdin=subprocess.DEVNULL)
'''

FAKE_SUPERVISOR = r'''
import subprocess, sys
subprocess.run([sys.executable, "-c", sys.argv[1]] + sys.argv[2:], start_new_session=True)
'''


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="image-gate-lifecycle-")
        self.client = os.path.join(self.tmp, "fake-client")
        with open(self.client, "w", encoding="utf-8") as fh:
            fh.write("#!%s\n%s" % (sys.executable, FAKE_CLIENT))
        os.chmod(self.client, 0o755)
        self.scratch = os.path.join(self.tmp, "lock-scratch")
        open(self.scratch, "w").close()
        self.left = []

    def tearDown(self):
        for pid in self.left:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wrap(self, with_lock: bool) -> dict:
        record = os.path.join(self.tmp, "control.json")
        env = dict(os.environ, IMAGE_GATE_RECORD=record, IMAGE_GATE_CLAUDE="/usr/bin/true")
        pass_fds = ()
        fd = None
        if with_lock:
            fd = os.open(self.scratch, os.O_WRONLY | os.O_APPEND)
            env["MNEMON_VAULT_LOCK_FD"] = str(fd)
            env["MNEMON_VAULT_LOCK_INODE"] = str(os.stat(self.scratch).st_ino)
            pass_fds = (fd,)
        else:
            env.pop("MNEMON_VAULT_LOCK_FD", None)
            env.pop("MNEMON_VAULT_LOCK_INODE", None)
        try:
            rc = subprocess.run([WRAP, "-p"], env=env, pass_fds=pass_fds, stdin=subprocess.DEVNULL).returncode
        finally:
            if fd is not None:
                os.close(fd)
        out = os.path.join(self.tmp, "control-item.json")
        res = subprocess.run([sys.executable, LIFECYCLE, "control", "--record", record, "--scratch",
                              file_id(self.scratch), "--rc", str(rc), "--seq", "0", "--out", out],
                             stdout=subprocess.PIPE)
        with open(out, encoding="utf-8") as fh:
            item = json.load(fh)
        with open(record, encoding="utf-8") as fh:
            item["record"] = json.load(fh)
        item["exit"] = res.returncode
        return item

    def test_the_wrapper_lists_an_open_descriptor_and_names_the_variables(self):
        item = self.wrap(with_lock=True)
        self.assertEqual((item["status"], item["exit"]), ("pass", 0))
        self.assertEqual(item["record"]["lock_vars"], ["MNEMON_VAULT_LOCK_FD", "MNEMON_VAULT_LOCK_INODE"])
        self.assertNotIn(str(os.stat(self.scratch).st_ino), json.dumps(item["record"]["lock_vars"]),
                         "names, never values")

    def test_the_control_fails_when_the_descriptor_is_not_there(self):
        item = self.wrap(with_lock=False)
        self.assertEqual((item["status"], item["exit"]), ("fail", 1))
        self.assertEqual(item["record"]["lock_vars"], [])

    def watch(self, mode: str) -> dict:
        record = os.path.join(self.tmp, "wrapper-%s.json" % mode)
        out = os.path.join(self.tmp, "lifecycle-%s.json" % mode)
        env = dict(os.environ, IMAGE_GATE_RECORD=record, IMAGE_GATE_CLAUDE=self.client)
        env.pop("MNEMON_VAULT_LOCK_FD", None)
        env.pop("MNEMON_VAULT_LOCK_INODE", None)
        sup = subprocess.Popen([sys.executable, "-c", FAKE_SUPERVISOR, FAKE_WORKER, WRAP, self.client, mode],
                               env=env, stdin=subprocess.DEVNULL)
        watcher = subprocess.Popen([sys.executable, LIFECYCLE, "watch", "--supervisor", str(sup.pid), "--record",
                                    record, "--scratch", file_id(self.scratch), "--max-seconds", "20",
                                    "--out", out], stdout=subprocess.PIPE)
        sup.wait(timeout=20)
        stdout, _ = watcher.communicate(timeout=30)
        with open(out, encoding="utf-8") as fh:
            result = json.load(fh)
        self.left.extend(entry["pid"] for entry in result["left"])
        result["exit"] = watcher.returncode
        result["stdout"] = stdout.decode("utf-8")
        return result

    def test_a_clean_run_passes(self):
        r = self.watch("clean")
        self.assertEqual((r["pass"], r["exit"]), (True, 0), r["failures"])
        self.assertTrue(r["model_call"] and r["group_observed"])
        self.assertGreaterEqual(r["claude_tree_seen"], 1)
        self.assertEqual((r["members_after_worker"], r["members_left"], r["claude_tree_left"], r["lock_vars_seen"],
                          r["lock_fd_open"]), (0, 0, 0, 0, False))

    def test_a_descendant_of_the_client_in_a_session_of_its_own_fails(self):
        r = self.watch("detached")
        self.assertEqual((r["pass"], r["exit"]), (False, 1))
        self.assertEqual(r["claude_tree_left"], 1)
        self.assertEqual(r["members_left"], 0, "it left the group: only the tree shows it")

    def test_a_member_left_in_the_worker_group_fails(self):
        r = self.watch("in-group")
        self.assertEqual((r["pass"], r["exit"]), (False, 1))
        self.assertGreaterEqual(r["members_after_worker"], 1)
        self.assertGreaterEqual(r["members_left"], 1)
        self.assertEqual(r["claude_tree_left"], 1)


class Gate(unittest.TestCase):
    def run_gate(self, env_extra, args=()):
        env = {k: v for k, v in os.environ.items() if k != "MNEMON_TEST_HOOKS"}
        env.update(env_extra)
        return subprocess.run(["/bin/bash", GATE] + list(args), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)

    def test_without_claude_on_path_not_run_exit_0(self):
        res = self.run_gate({"PATH": "/usr/bin:/bin"})
        self.assertEqual(res.returncode, 0)
        self.assertTrue(res.stdout.decode().startswith("not run: claude"), res.stdout)

    def test_refused_under_the_test_hooks(self):
        res = self.run_gate({"PATH": "/usr/bin:/bin", "MNEMON_TEST_HOOKS": "1"})
        self.assertEqual(res.returncode, 2)
        self.assertIn(b"refused", res.stderr)
        self.assertEqual(res.stdout, b"")

    def test_a_usage_error_exits_2(self):
        self.assertEqual(self.run_gate({"PATH": "/usr/bin:/bin"}, ["--effort", "huge"]).returncode, 2)
        self.assertEqual(self.run_gate({"PATH": "/usr/bin:/bin"}, ["--only", "nothing"]).returncode, 2)


class Inputs(unittest.TestCase):
    def test_no_image_is_committed_twice(self):
        seen = {}
        for name in sorted(os.listdir(os.path.join(HERE, "img"))):
            with open(os.path.join(HERE, "img", name), "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            self.assertNotIn(digest, seen, "%s repeats %s" % (name, seen.get(digest)))
            seen[digest] = name
        self.assertEqual(len(seen), 9)

    def test_every_html_source_has_its_truth(self):
        html = sorted(n[:-5] for n in os.listdir(os.path.join(HERE, "html")) if n.endswith(".html"))
        txt = sorted(n[:-4] for n in os.listdir(os.path.join(HERE, "truth")) if n.endswith(".txt"))
        self.assertEqual(html, txt)
        self.assertEqual(html, ["cyrillic", "paper-2col", "post-ai", "post-en", "quote-short"])


if __name__ == "__main__":
    unittest.main()
