"""capture.image_reader — the reader of the image origin, adapter `image-reader/1` (capture layer A20, §21.4,
§21.6 «Normalization» and «Counts», §21.7 `reader`, §21.13). Hermetic: the stub reader
`tests/fixtures/capture/image/stub-claude-image.py` stands in for `claude`; the recorded streams in
`tests/fixtures/capture/image/reader/` are parsed as they are. Stdlib only, python 3.9.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from _helpers import BIN, FIX, PY, ROOT, clean_env, pid_alive, wait_gone, write_stub

from capture import image_reader as ir
from capture import proc

IMAGE_FIX = os.path.join(FIX, "image")
STUB = os.path.join(IMAGE_FIX, "stub-claude-image.py")
READER_FIX = os.path.join(IMAGE_FIX, "reader")
LAYER_SPEC = os.path.join(ROOT, "docs", "specs", "2026-10-15-capture-layer.md")
CONTRACTS_SPEC = os.path.join(ROOT, "docs", "specs", "2026-10-15-capture-contracts.md")
SYNTH = "A fixed synthetic sentence stands here in place of the text the model wrote."

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 3 + b"\x00\xff\n\r"
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(255, -1, -1)) * 2 + b"\xff\xd9"

FENCE = ("convert", "reader-fence", False)
OUTPUT = ("convert", "reader-output", True)
FAILURES = {
    "nul": OUTPUT, "surrogate": OUTPUT, "no-result": OUTPUT, "no-structured": OUTPUT, "text-not-string": OUTPUT,
    "error": ("convert", "reader-error", True),
    "auth": ("environment", "reader-auth", True),
    "budget": ("convert", "reader-budget", False),
    "max-turns": ("convert", "reader-max-turns", False),
    "no-init": FENCE, "init-no-tools": FENCE, "tool-in-init": FENCE, "mcp-in-init": FENCE, "denial": FENCE,
}
READING_MODES = ("ok", "other-text", "empty", "whitespace", "control-chars")
# words of the stub's texts: none of them may stand in an error line
TEXT_WORDS = ("machine", "picture", "witness", "quote", "diagnostic", "posted", "views", "replies", "synthetic")


def text_of(name: str) -> str:
    with open(os.path.join(IMAGE_FIX, name), "r", encoding="utf-8") as fh:
        return fh.read()


def events_of(name: str) -> list:
    with open(os.path.join(READER_FIX, name), "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def stream(events) -> bytes:
    return b"".join((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8") for ev in events)


def bounded(stdout=b"", rc=0, stderr=b"", timed_out=False, by_deadline=False, capped=None, started=True):
    return proc.BoundedResult(rc, stdout, stderr, timed_out, 5, started=started, by_deadline=by_deadline,
                              capped=capped)


def triple(reading):
    f = reading.failure
    return None if f is None else (f["class"], f["reason"], f["retryable"])


def load_extract_run():
    spec = importlib.util.spec_from_file_location("extract_run_for_test", os.path.join(BIN, "extract-run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def spec_section(path: str, heading: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    start = text.index(heading)
    nxt = text.find("\n### ", start + len(heading))
    return text[start:nxt if nxt != -1 else len(text)]


@contextlib.contextmanager
def environ(**values):
    """Set (a str) or remove (None) variables of this process for the block; restore them after it."""
    old = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def lines_of(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


class _Work(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mnemon-reader-")
        self.to_kill = []

    def tearDown(self):
        for pid in self.to_kill:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def raw(self, data=PNG, ext="png") -> str:
        path = os.path.join(self.work, "raw-%d.%s" % (len(os.listdir(self.work)), ext))
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def call(self, mode, *, deadline=None, effort="high", image_format="png", data=None, claude=STUB, env=None):
        """One read() with the stub in `mode`: (reading, attempt dir, counter lines, facts or None)."""
        attempt = tempfile.mkdtemp(dir=self.work, prefix="attempt-")
        calls = attempt + ".calls"
        facts = attempt + ".facts"
        raw = self.raw(data if data is not None else (PNG if image_format == "png" else JPEG), image_format)
        values = {"MNEMON_TEST_READER_MODE": mode, "MNEMON_TEST_READER_CALLS": calls, "MNEMON_TEST_READER_FACTS": facts}
        values.update(env or {})
        with environ(**values):
            r = ir.read(raw, image_format, attempt, claude=claude, model="haiku", effort=effort, max_usd="0.05",
                        deadline=deadline if deadline is not None else proc.Deadline(60))
        doc = None
        if os.path.exists(facts):
            with open(facts, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if doc.get("child_pid"):
                self.to_kill.append(doc["child_pid"])
        return r, attempt, lines_of(calls), doc

    def assert_error_line(self, reading):
        self.assertIsInstance(reading.error, str)
        self.assertTrue(reading.error)
        self.assertLessEqual(len(reading.error), 300)
        self.assertNotIn("\n", reading.error)
        self.assertNotIn("\r", reading.error)
        low = reading.error.lower()
        for word in TEXT_WORDS:
            self.assertNotIn(word, low, "the error line holds no text of the image: %r" % reading.error)


# ------------------------------------------------------------------------------------------------- the call

class ArgvTests(_Work):

    def extractor_argv(self, model="haiku"):
        """The argv bin/extract-run.py builds, from a dry call against a stub that answers nothing."""
        attempt = tempfile.mkdtemp(dir=self.work, prefix="extract-")
        prompt = os.path.join(self.work, "prompt.md")
        schema = os.path.join(self.work, "schema.json")
        with open(prompt, "w", encoding="utf-8") as fh:
            fh.write("a prompt\n")
        with open(schema, "w", encoding="utf-8") as fh:
            json.dump(ir.SCHEMA, fh)
        claude = write_stub(self.work, "claude-dry", "#!/bin/sh\ncat >/dev/null\nexit 0\n")
        out = subprocess.run([PY, os.path.join(BIN, "extract-run.py"), "--attempt-dir", attempt, "--prompt", prompt,
                              "--schema", schema, "--model", model, "--timeout", "30", "--claude", claude],
                             capture_output=True, env=clean_env(), timeout=60)
        with open(os.path.join(attempt, "argv.json"), "r", encoding="utf-8") as fh:
            return json.load(fh), out

    def test_the_fence_flags_are_the_extractors_element_by_element(self):
        ext, _out = self.extractor_argv()
        i = ext.index("--output-format")
        fence = ext[1:i]
        mine = ir.fence_flags()
        self.assertEqual(len(fence), len(mine))
        for k, (theirs, ours) in enumerate(zip(fence, mine)):
            self.assertEqual(ours, theirs, "fence flag %d" % k)

    def test_the_argv_is_the_extractors_fence_plus_exactly_the_differences(self):
        ext, _out = self.extractor_argv(model="haiku")
        i = ext.index("--output-format")
        self.assertEqual(ext[i:i + 2], ["--output-format", "json"])
        expected = ([ext[0]] + ext[1:i] + ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
                    + ext[i + 2:] + ["--effort", "high", "--max-budget-usd", "0.05"])
        self.assertEqual(ir.build_argv(ext[0], model="haiku", effort="high", max_usd="0.05"), expected)

    def test_the_argv_is_the_code_block_of_21_4_in_its_order(self):
        section = spec_section(LAYER_SPEC, "### 21.4 The reader")
        block = section.split("```", 2)[1]
        tokens = shlex.split(block.replace("\\\n", " "))
        tokens = tokens[:tokens.index("<")]
        schema = json.dumps(ir.SCHEMA, separators=(",", ":"), ensure_ascii=False)
        values = {"claude": "/opt/stub/claude", "$IMAGE_READING_SCHEMA": schema, "$IMAGE_READER_MODEL": "sonnet",
                  "$IMAGE_READER_EFFORT": "max", "$IMAGE_READER_MAX_USD": "0.25"}
        expected = [values.get(t, t) for t in tokens]
        self.assertEqual(ir.build_argv("/opt/stub/claude", model="sonnet", effort="max", max_usd="0.25"), expected)

    def test_never_a_forbidden_flag_and_never_a_positional_prompt(self):
        argv = ir.build_argv("claude", model="haiku", effort="high", max_usd=0.05)
        for flag in ("--settings", "--add-dir", "--mcp-config", "--allowedTools", "--allowed-tools", "--permission-mode",
                     "--dangerously-skip-permissions"):
            self.assertNotIn(flag, argv)
        self.assertNotIn("json", argv, "no --output-format json")
        switches = {"-p", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--verbose"}
        k = 1
        while k < len(argv):
            self.assertTrue(argv[k].startswith("-"), "argv[%d] %r is a positional argument" % (k, argv[k]))
            k += 1 if argv[k] in switches else 2
        self.assertEqual(k, len(argv))

    def test_the_effort_level_the_model_and_the_budget_are_checked(self):
        for level in ir.EFFORT_LEVELS:
            argv = ir.build_argv("claude", model="haiku", effort=level, max_usd="0.05")
            self.assertEqual(argv[argv.index("--effort") + 1], level)
        self.assertEqual(ir.EFFORT_LEVELS, ("low", "medium", "high", "xhigh", "max"))
        with self.assertRaises(ValueError):
            ir.build_argv("claude", model="haiku", effort="turbo", max_usd="0.05")
        for model in ("", "--settings"):
            with self.assertRaises(ValueError):
                ir.build_argv("claude", model=model, effort="high", max_usd="0.05")
        for usd in ("0", "-1", "nan", "inf", "x", None, True, 0):
            with self.assertRaises(ValueError):
                ir.build_argv("claude", model="haiku", effort="high", max_usd=usd)
        for usd, text in (("0.05", "0.05"), (0.05, "0.05"), (1, "1"), (" 0.5 ", "0.5")):
            argv = ir.build_argv("claude", model="haiku", effort="high", max_usd=usd)
            self.assertEqual(argv[-2:], ["--max-budget-usd", text])

    def test_argv_txt_and_argv_json_are_written_and_the_message_is_not_kept(self):
        r, attempt, calls, _facts = self.call("ok", effort="medium")
        self.assertIsNone(r.failure)
        argv = ir.build_argv(STUB, model="haiku", effort="medium", max_usd="0.05")
        with open(os.path.join(attempt, "argv.json"), "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), argv)
        with open(os.path.join(attempt, "argv.txt"), "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), load_extract_run().shell_line(argv) + "\n")
        self.assertEqual(sorted(os.listdir(attempt)), ["argv.json", "argv.txt"], "nothing of the message is kept")

    def test_a_bad_request_raises_before_any_call(self):
        attempt = tempfile.mkdtemp(dir=self.work)
        calls = attempt + ".calls"
        with environ(MNEMON_TEST_READER_CALLS=calls, MNEMON_TEST_READER_MODE="ok"):
            with self.assertRaises(ValueError):
                ir.read(self.raw(), "png", attempt, claude=STUB, model="haiku", effort="extreme", max_usd="0.05",
                        deadline=proc.Deadline(30))
            with self.assertRaises(ValueError):
                ir.read(self.raw(), "gif", attempt, claude=STUB, model="haiku", effort="high", max_usd="0.05",
                        deadline=proc.Deadline(30))
        self.assertEqual(lines_of(calls), [])
        self.assertEqual(os.listdir(attempt), [])


class MessageTests(_Work):

    def test_one_line_two_blocks_the_raw_bytes_unaltered_and_nothing_else(self):
        for data, fmt, media in ((PNG, "png", "image/png"), (JPEG, "jpeg", "image/jpeg")):
            msg = ir.build_message(data, fmt)
            self.assertTrue(msg.endswith(b"\n"))
            self.assertEqual(msg.count(b"\n"), 1)
            doc = json.loads(msg.decode("utf-8"))
            self.assertEqual(doc, {"type": "user", "message": {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media,
                                             "data": base64.b64encode(data).decode("ascii")}},
                {"type": "text", "text": ir.PROMPT}]}})
            self.assertEqual(base64.b64decode(doc["message"]["content"][0]["source"]["data"], validate=True), data)
        with self.assertRaises(ValueError):
            ir.build_message(PNG, "gif")

    def test_the_call_carries_the_raws_bytes_and_the_prompt(self):
        for data, fmt, media in ((PNG, "png", "image/png"), (JPEG, "jpeg", "image/jpeg")):
            r, _attempt, calls, facts = self.call("ok", image_format=fmt, data=data)
            self.assertIsNone(r.failure, r.error)
            self.assertEqual(calls, ["read"])
            self.assertEqual(facts["media_type"], media)
            self.assertEqual(facts["image_sha256"], hashlib.sha256(data).hexdigest())

    def test_the_prompt_and_the_schema_are_the_specs(self):
        section = spec_section(LAYER_SPEC, "### 21.4 The reader")
        lines = section.splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith("- **The prompt**"))
        quote = next(line for line in lines[at + 1:] if line.strip().startswith("> "))
        self.assertEqual(ir.PROMPT, quote.strip()[2:])
        m = re.search(r"\*\*The schema\*\* \(`image-reading/1`\): `(\{.*?\})`", section)
        self.assertEqual(json.loads(m.group(1)), ir.SCHEMA)
        self.assertEqual((ir.ADAPTER, ir.SCHEMA_NAME, ir.NORMALIZER), ("image-reader/1", "image-reading/1",
                                                                       "mnemon-image-text/1"))
        self.assertEqual((ir.STDOUT_MAX, ir.STDERR_MAX), (8 * 1024 * 1024, 1024 * 1024))
        with open(CONTRACTS_SPEC, "r", encoding="utf-8") as fh:
            self.assertIn("`%s`" % ir.MODEL_ID_RE, fh.read())


class EnvironmentTests(_Work):

    def test_the_effort_level_holds_in_argv_and_environment_and_no_vault_lock_variable_reaches_the_call(self):
        for configured, inherited in (("xhigh", "low"), ("high", "max"), ("low", None)):
            env = {"CLAUDE_CODE_EFFORT_LEVEL": inherited, "MNEMON_VAULT_LOCK_FD": "9", "MNEMON_VAULT_LOCK_INODE": "4242"}
            r, attempt, calls, facts = self.call("ok", effort=configured, env=env)
            self.assertIsNone(r.failure, r.error)
            with open(os.path.join(attempt, "argv.json"), "r", encoding="utf-8") as fh:
                argv = json.load(fh)
            self.assertEqual(argv[argv.index("--effort") + 1], configured)
            self.assertTrue(facts["effort_env_set"])
            self.assertTrue(facts["effort_env_matches_argv"], "CLAUDE_CODE_EFFORT_LEVEL is the configured level")
            self.assertEqual(facts["vault_lock_vars"], [])
            self.assertEqual(os.path.realpath(facts["cwd"]), os.path.realpath(attempt), "the attempt dir is the cwd")
            self.assertEqual(calls, ["read"])


# ---------------------------------------------------------------------------------------- the stub's modes

class ModeTests(_Work):

    def test_each_mode_that_answers_a_string_gives_the_reading_with_what_the_stream_gave(self):
        ok = text_of("stub-text.txt")
        expected = {"ok": ok, "other-text": text_of("stub-text-other.txt"), "empty": "",
                    "whitespace": "  \f   　 ﻿  "}
        for mode in READING_MODES:
            with self.subTest(mode=mode):
                r, _attempt, calls, _facts = self.call(mode)
                self.assertIsNone(r.failure, r.error)
                self.assertFalse(r.deadline_hit)
                self.assertIsNone(r.error)
                self.assertEqual(calls, ["read"], "one call per attempt")
                if mode in expected:
                    self.assertEqual(r.text, expected[mode])
                self.assertEqual((r.claude_code, r.model_id), ("2.1.328", "claude-haiku-5-5"))
                self.assertEqual((r.cost_usd, r.duration_ms, r.turns), (0.00080457, 1337, 2))
                self.assertEqual((r.output_tokens, r.thinking_tokens), (84, 0))

    def test_control_chars_are_a_reading_with_every_code_point_kept(self):
        r, _attempt, _calls, _facts = self.call("control-chars")
        self.assertIsNone(r.failure)
        for ch in ("\x1c", "\x7f", "﻿"):
            self.assertIn(ch, r.text)
            self.assertIn(ch, ir.normalize(r.text), "normalization keeps %r" % ch)
        self.assertFalse(ir.is_zero_text(r.text))
        self.assertEqual(ir.normalize(r.text), r.text, "no control character is taken for a line end or removed")

    def test_each_failing_mode_is_its_row_of_the_outcome_table(self):
        for mode, want in sorted(FAILURES.items()):
            with self.subTest(mode=mode):
                r, _attempt, calls, _facts = self.call(mode)
                self.assertEqual(triple(r), want)
                self.assertIsNone(r.failure["acquisition"])
                self.assertIsNone(r.text, "nothing is published")
                self.assertFalse(r.deadline_hit)
                self.assertEqual(calls, ["read"], "one call per attempt, never a repeat")
                self.assert_error_line(r)

    def test_the_budget_mode_keeps_the_cost(self):
        r, _attempt, _calls, _facts = self.call("budget")
        self.assertEqual(triple(r), ("convert", "reader-budget", False))
        self.assertAlmostEqual(r.cost_usd, 0.00079597, places=10)
        self.assertEqual((r.output_tokens, r.turns, r.claude_code), (82, 2, "2.1.328"))
        self.assertIn("error_max_budget_usd", r.error)

    def test_the_fence_breach_names_the_installed_version(self):
        for mode in ("tool-in-init", "mcp-in-init", "init-no-tools", "denial"):
            with self.subTest(mode=mode):
                r, _attempt, _calls, _facts = self.call(mode)
                self.assertIn("2.1.328", r.error)
        r, _attempt, _calls, _facts = self.call("denial")
        self.assertNotIn("command", r.error, "a denial's tool input never reaches the error line")

    def test_stdout_flood_is_output_cap_never_reader_error(self):
        start = time.monotonic()
        with mock.patch.object(ir, "STDOUT_MAX", 256 * 1024):
            r, _attempt, calls, _facts = self.call("stdout-flood")
        self.assertEqual(triple(r), ("convert", "output-cap", False))
        self.assertIsNone(r.text, "the complete stream before the flood is not a reading")
        self.assertEqual(calls, ["read"])
        self.assertLess(time.monotonic() - start, 15.0)
        self.assert_error_line(r)

    def test_hang_under_a_short_deadline_is_the_deadline_and_nothing_is_classified(self):
        start = time.monotonic()
        r, _attempt, calls, _facts = self.call("hang", deadline=proc.Deadline(0.5))
        took = time.monotonic() - start
        self.assertTrue(r.deadline_hit)
        self.assertIsNone(r.failure)
        self.assertIsNone(r.text)
        self.assertIsNone(r.error)
        self.assertEqual(calls, ["read"])
        self.assertLess(took, 0.5 + proc.TERM_GRACE + proc.KILL_GRACE + 3.0)

    def test_hang_child_stays_in_the_callers_process_group(self):
        # read() runs in a process that leads a group of its own, as the image worker does; after the deadline the
        # stub is ended by its pid, and its child is still a member of that group — the worker's group kill
        # (capture layer 21.6) is what ends it
        attempt = tempfile.mkdtemp(dir=self.work)
        calls, facts = attempt + ".calls", attempt + ".facts"
        code = ("import json, os, sys\nsys.path.insert(0, %r)\nfrom capture import image_reader as ir, proc\n"
                "r = ir.read(%r, 'png', %r, claude=%r, model='haiku', effort='high', max_usd='0.05', "
                "deadline=proc.Deadline(0.5))\n"
                "print(json.dumps({'deadline_hit': r.deadline_hit, 'failure': r.failure, 'pgrp': os.getpgrp()}))\n"
                % (BIN, self.raw(), attempt, STUB))
        env = clean_env({"MNEMON_TEST_READER_MODE": "hang-child", "MNEMON_TEST_READER_CALLS": calls,
                         "MNEMON_TEST_READER_FACTS": facts}, hooks=False)
        out = subprocess.run([PY, "-c", code], capture_output=True, env=env, timeout=60, start_new_session=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        doc = json.loads(out.stdout.decode("utf-8"))
        with open(facts, "r", encoding="utf-8") as fh:
            child = json.load(fh)["child_pid"]
        self.to_kill.append(child)
        try:
            self.assertTrue(doc["deadline_hit"])
            self.assertIsNone(doc["failure"])
            self.assertEqual(lines_of(calls), ["read"])
            self.assertTrue(pid_alive(child))
            self.assertEqual(os.getpgid(child), doc["pgrp"], "the stub's child is in the caller's process group")
        finally:
            try:
                os.killpg(doc["pgrp"], signal.SIGKILL)
            except OSError:
                pass
        self.assertTrue(wait_gone(child, 3.0))

    def test_a_claude_that_cannot_start_is_reader_missing(self):
        not_exec = os.path.join(self.work, "claude-not-executable")
        with open(not_exec, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        for claude in (os.path.join(self.work, "no-such-claude"), not_exec):
            with self.subTest(claude=os.path.basename(claude)):
                r, _attempt, calls, _facts = self.call("ok", claude=claude)
                self.assertEqual(triple(r), ("environment", "reader-missing", True))
                self.assertEqual(calls, [], "the stub never ran")
                self.assertIn("cannot start", r.error)
                self.assert_error_line(r)


class StubTests(_Work):

    def run_stub(self, args, stdin=b"", mode="ok"):
        calls = os.path.join(self.work, "calls-%d" % len(os.listdir(self.work)))
        env = clean_env({"MNEMON_TEST_READER_MODE": mode, "MNEMON_TEST_READER_CALLS": calls}, hooks=False)
        out = subprocess.run([STUB] + args, input=stdin, capture_output=True, env=env, timeout=60)
        return out.returncode, out.stdout, out.stderr.decode("utf-8", "replace"), lines_of(calls)

    def test_version_and_help_answer_and_are_no_call(self):
        rc, so, _se, calls = self.run_stub(["--version"])
        self.assertEqual((rc, so, calls), (0, b"9.9.9-stub (Claude Code)\n", []))
        rc, so, _se, calls = self.run_stub(["--help"])
        self.assertEqual((rc, calls), (0, []))
        self.assertIn(b"--restricted", so)

    def test_a_wrong_argv_exits_97_and_still_counts_the_call(self):
        good = ir.build_argv(STUB, model="haiku", effort="high", max_usd="0.05")[1:]
        msg = ir.build_message(PNG, "png")
        extractor = ["-p", "--restricted", "--tools", "", "--permission-prompts", "none", "--strict-mcp-config",
                     "--no-session-persistence", "--output-format", "json", "--json-schema", "{}", "--max-turns", "4",
                     "--model", "haiku"]
        bad = [extractor, good + ["a positional prompt"], good[:-2], [a for a in good if a != "--verbose"],
               good[:good.index("--effort")] + ["--effort", "turbo"] + good[good.index("--effort") + 2:],
               good + ["--settings", "x.json"]]
        for args in bad:
            rc, so, se, calls = self.run_stub(args, msg)
            self.assertEqual(rc, 97, se)
            self.assertIn("stub-claude-image:", se)
            self.assertEqual(so, b"")
            self.assertEqual(calls, ["read"])

    def test_a_wrong_message_exits_97(self):
        args = ir.build_argv(STUB, model="haiku", effort="high", max_usd="0.05")[1:]
        good = json.loads(ir.build_message(PNG, "png").decode("utf-8"))

        def variant(fn):
            doc = json.loads(json.dumps(good))
            fn(doc)
            return (json.dumps(doc) + "\n").encode("utf-8")

        blocks = lambda d: d["message"]["content"]   # noqa: E731
        bad = [b"", ir.build_message(PNG, "png")[:-1], ir.build_message(PNG, "png") * 2,
               variant(lambda d: d.update(session_id="x")),
               variant(lambda d: blocks(d).reverse()),
               variant(lambda d: blocks(d).append({"type": "text", "text": "the file name"})),
               variant(lambda d: blocks(d)[1].update(text=ir.PROMPT + " Also describe it.")),
               variant(lambda d: blocks(d)[0]["source"].update(media_type="image/gif")),
               variant(lambda d: blocks(d)[0]["source"].update(media_type="image/jpeg")),
               variant(lambda d: blocks(d)[0]["source"].update(data="!!not base64!!")),
               variant(lambda d: blocks(d)[0].update(title="a title")),
               variant(lambda d: d["message"].update(role="assistant"))]
        for stdin in bad:
            rc, _so, se, calls = self.run_stub(args, stdin)
            self.assertEqual(rc, 97, se)
            self.assertEqual(calls, ["read"])
        rc, _so, se, _calls = self.run_stub(args, ir.build_message(PNG, "png"), mode="no-such-mode")
        self.assertEqual(rc, 97, se)
        rc, so, se, _calls = self.run_stub(args, ir.build_message(PNG, "png"))
        self.assertEqual(rc, 0, se)
        self.assertTrue(so.endswith(b"\n"))


# -------------------------------------------------------------------------------- the stream, row by row

class StreamTests(unittest.TestCase):

    def test_the_recorded_success_gives_the_text_the_model_id_the_cost_the_turns_and_the_tokens(self):
        r = ir._interpret(bounded(stream(events_of("success.jsonl"))))
        self.assertIsNone(r.failure)
        self.assertEqual(r.text, SYNTH)
        self.assertEqual((r.claude_code, r.model_id), ("2.1.328", "claude-haiku-5-5"))
        self.assertEqual((r.cost_usd, r.duration_ms, r.turns, r.output_tokens, r.thinking_tokens),
                         (0.00080457, 1337, 2, 84, 0))
        self.assertIsNone(r.error)

    def test_the_recorded_budget_stream_is_reader_budget_with_its_cost_and_no_text(self):
        for rc in (1, 0):
            r = ir._interpret(bounded(stream(events_of("budget.jsonl")), rc=rc))
            self.assertEqual(triple(r), ("convert", "reader-budget", False))
            self.assertIsNone(r.text)
            self.assertAlmostEqual(r.cost_usd, 0.00079597, places=10)
            self.assertEqual((r.duration_ms, r.turns, r.output_tokens, r.thinking_tokens), (1431, 2, 82, 0))

    def test_a_result_without_a_cost_has_cost_none(self):
        for cost in (None, "0.1", True, -1):
            events = events_of("success.jsonl")
            if cost is None:
                del events[-1]["total_cost_usd"]
            else:
                events[-1]["total_cost_usd"] = cost
            r = ir._interpret(bounded(stream(events)))
            self.assertIsNone(r.failure)
            self.assertIsNone(r.cost_usd, "unknown, not zero")
        r = ir._interpret(bounded(stream(events_of("success.jsonl")[:3]), rc=1))
        self.assertIsNone(r.cost_usd, "no result event: unknown cost")
        self.assertIsNone(r.output_tokens)

    def test_a_model_id_outside_the_pattern_is_reader_output(self):
        for model in ("claude haiku", "a" * 101, "", 42, None, "claude-haiku\n5", "claude/haiku"):
            events = events_of("success.jsonl")
            if model is None:
                del events[0]["model"]
            else:
                events[0]["model"] = model
            r = ir._interpret(bounded(stream(events)))
            self.assertEqual(triple(r), ("convert", "reader-output", True), repr(model))
            self.assertIsNone(r.model_id)
            self.assertIsNone(r.text)
        for model in ("claude-haiku-5-5", "a" * 100, "us.vendor.model:1@v2_x"):
            events = events_of("success.jsonl")
            events[0]["model"] = model
            r = ir._interpret(bounded(stream(events)))
            self.assertIsNone(r.failure, repr(model))
            self.assertEqual(r.model_id, model)

    def test_lines_that_are_not_json_objects_are_skipped(self):
        events = events_of("success.jsonl")
        junk = [b"not json\n", b"[1, 2]\n", b"\"a string\"\n", b"{\"broken\": \n", b"\xff\xfe\xfd\n",
                b"{\"type\": \"result\", \"total_cost_usd\": NaN}\n", b"\n", b"   \n"]
        data = stream(events[:1]) + b"".join(junk) + stream(events[1:]) + b"".join(junk[:-2])
        r = ir._interpret(bounded(data))
        self.assertIsNone(r.failure)
        self.assertEqual(r.text, SYNTH)
        # a result line that is not strict JSON is no result
        bad = stream(events[:-1]) + json.dumps(events[-1]).replace('"total_cost_usd": 0.00080457',
                                                                   '"total_cost_usd": NaN').encode("utf-8") + b"\n"
        self.assertEqual(triple(ir._interpret(bounded(bad))), OUTPUT)

    def test_no_text_of_an_assistant_event_is_ever_used(self):
        events = events_of("success.jsonl")
        del events[-1]["structured_output"]
        r = ir._interpret(bounded(stream(events)))
        self.assertEqual(triple(r), OUTPUT)
        self.assertIsNone(r.text)
        no_result = ir._interpret(bounded(stream(events_of("success.jsonl")[:4])))
        self.assertEqual(triple(no_result), OUTPUT)

    def test_the_last_result_event_decides(self):
        events = events_of("success.jsonl")
        failed = json.loads(json.dumps(events_of("budget.jsonl")[-1]))
        failed.update(subtype="error_during_execution", errors=["boom"])
        r = ir._interpret(bounded(stream(events + [failed])))
        self.assertEqual(triple(r), ("convert", "reader-error", True))
        r = ir._interpret(bounded(stream(events[:-1] + [failed, events[-1]])))
        self.assertIsNone(r.failure)

    def test_a_malformed_result_is_no_result(self):
        for key in ("is_error", "subtype", "permission_denials"):
            events = events_of("success.jsonl")
            del events[-1][key]
            self.assertEqual(triple(ir._interpret(bounded(stream(events)))), OUTPUT, key)

    def test_every_shape_of_a_fence_breach(self):
        def init_with(**change):
            events = events_of("success.jsonl")
            for k, v in change.items():
                if v is KeyError:
                    del events[0][k]
                else:
                    events[0][k] = v
            return events

        cases = [init_with(tools=[]), init_with(tools="StructuredOutput"), init_with(tools=["StructuredOutput"] * 2),
                 init_with(tools=["Read"]), init_with(mcp_servers=KeyError), init_with(mcp_servers={}),
                 init_with(mcp_servers=["x"]), init_with(tools=KeyError)]
        second = events_of("success.jsonl")
        extra = json.loads(json.dumps(second[0]))
        extra["tools"] = ["StructuredOutput", "WebFetch"]
        cases.append([second[0], extra] + second[1:])
        earlier = events_of("success.jsonl")
        denied = json.loads(json.dumps(earlier[-1]))
        denied["permission_denials"] = [{"tool_name": "Bash", "tool_input": {"command": SYNTH}}]
        cases.append(earlier[:-1] + [denied, earlier[-1]])
        cases.append([e for e in events_of("success.jsonl") if e.get("type") != "system"])
        for events in cases:
            r = ir._interpret(bounded(stream(events)))
            self.assertEqual(triple(r), FENCE)
            self.assertIsNone(r.text)
        # the fence comes before the budget, the login and the exit
        budget = events_of("budget.jsonl")
        budget[0]["tools"] = ["StructuredOutput", "Bash"]
        self.assertEqual(triple(ir._interpret(bounded(stream(budget), rc=1))), FENCE)
        self.assertEqual(triple(ir._interpret(bounded(b"", rc=1, stderr=b"Not logged in"))), FENCE)

    def test_a_non_zero_exit_is_never_a_reading(self):
        for rc in (1, 2, -9):
            r = ir._interpret(bounded(stream(events_of("success.jsonl")), rc=rc, stderr=b"something broke\n"))
            self.assertEqual(triple(r), ("convert", "reader-error", True))
            self.assertIsNone(r.text)
            self.assertIn("something broke", r.error)

    def test_the_login_by_the_patterns_of_cap_result_error_class(self):
        init = events_of("success.jsonl")[:1]
        template = events_of("budget.jsonl")[-1]

        def err(**change):
            ev = json.loads(json.dumps(template))
            del ev["errors"]
            ev.update(change)
            return ev

        auth = [(init + [err(subtype="success", result="Not logged in · Please run /login")], 1, b""),
                (init + [err(subtype="success", result="Invalid API key")], 0, b""),
                (init + [err(subtype="error_during_execution", errors=["OAuth token has expired"])], 1, b""),
                (init + [err(subtype="error_during_execution", errors=["HTTP 401"])], 1, b""),
                (init, 1, b"Error: Unauthorized\n"),
                (init, 1, b"authentication_error: login required\n")]
        for events, rc, stderr in auth:
            r = ir._interpret(bounded(stream(events), rc=rc, stderr=stderr))
            self.assertEqual(triple(r), ("environment", "reader-auth", True))
            self.assertIsNone(r.text)
        # stderr's patterns hold no `oauth` (cap_result_error_class); a clean success's text is never matched
        r = ir._interpret(bounded(stream(init), rc=1, stderr=b"oauth\n"))
        self.assertEqual(triple(r), ("convert", "reader-error", True))
        events = events_of("success.jsonl")
        events[-1]["structured_output"] = {"text": "Error 401: not logged in"}
        events[-1]["result"] = json.dumps(events[-1]["structured_output"])
        self.assertEqual(triple(ir._interpret(bounded(stream(events), rc=1))), ("convert", "reader-error", True))
        self.assertIsNone(ir._interpret(bounded(stream(events), rc=0)).failure)

    def test_max_turns_comes_before_the_login(self):
        init = events_of("success.jsonl")[:1]
        ev = json.loads(json.dumps(events_of("budget.jsonl")[-1]))
        ev.update(subtype="error_max_turns", errors=["not logged in"])
        self.assertEqual(triple(ir._interpret(bounded(stream(init + [ev]), rc=1))),
                         ("convert", "reader-max-turns", False))

    def test_the_runners_limits_come_first(self):
        full = stream(events_of("success.jsonl"))
        r = ir._interpret(bounded(full, rc=-15, capped="stdout"))
        self.assertEqual(triple(r), ("convert", "output-cap", False))
        self.assertIsNone(r.text)
        self.assertEqual(r.model_id, "claude-haiku-5-5", "what the stream gave is kept")
        r = ir._interpret(bounded(full, rc=0, capped="stderr"))
        self.assertEqual(triple(r), ("convert", "output-cap", False))
        r = ir._interpret(bounded(full, rc=-15, timed_out=True, by_deadline=True))
        self.assertTrue(r.deadline_hit)
        self.assertIsNone(r.failure)
        self.assertIsNone(r.text)
        self.assertIsNone(r.cost_usd)
        r = ir._interpret(bounded(b"", rc=None, timed_out=True, by_deadline=True, started=False))
        self.assertTrue(r.deadline_hit)
        r = ir._interpret(bounded(b"", rc=None, stderr=b"cannot start claude: No such file or directory", started=False))
        self.assertEqual(triple(r), ("environment", "reader-missing", True))

    def test_text_values_that_are_not_a_reading(self):
        for text, why in (("a\x00b", "U+0000"), ("a\ud800b", "UTF-8"), (["a"], "not a string"), (None, "not a string")):
            events = events_of("success.jsonl")
            events[-1]["structured_output"] = {"text": text} if text is not None else {}
            data = b"".join((json.dumps(e) + "\n").encode("utf-8") for e in events)    # ASCII: \u escapes
            r = ir._interpret(bounded(data))
            self.assertEqual(triple(r), OUTPUT, repr(text))
            self.assertIn(why, r.error)
        events = events_of("success.jsonl")
        events[-1]["structured_output"] = "text"
        self.assertEqual(triple(ir._interpret(bounded(stream(events)))), OUTPUT)

    def test_an_error_line_is_one_line_of_at_most_300_characters_without_the_text(self):
        noisy = ("line\n" * 2000 + "the last words of stderr").encode("utf-8")
        r = ir._interpret(bounded(stream(events_of("success.jsonl")[:1]), rc=1, stderr=noisy))
        self.assertEqual(triple(r), ("convert", "reader-error", True))
        self.assertLessEqual(len(r.error), 300)
        self.assertNotIn("\n", r.error)
        self.assertTrue(r.error.endswith("the last words of stderr"), r.error)
        events = events_of("success.jsonl")
        failed = json.loads(json.dumps(events_of("budget.jsonl")[-1]))
        failed.update(subtype="error_during_execution", errors=["e" * 1000], result=SYNTH)
        r = ir._interpret(bounded(stream(events[:1] + [failed]), rc=1))
        self.assertLessEqual(len(r.error), 300)
        self.assertNotIn("synthetic", r.error, "an error result's `result` string is not copied")


# ------------------------------------------------------------------------------------------- the text rules

class NormalizationTests(unittest.TestCase):

    def test_line_ends(self):
        self.assertEqual(ir.normalize("a\r\nb"), "a\nb\n")
        self.assertEqual(ir.normalize("a\rb\fc\vd\x0be"), "a\nb\nc\nd\ne\n")
        self.assertEqual(ir.normalize("a\r\n\r\nb"), "a\n\nb\n", "\\r\\n is one line end")
        self.assertEqual(ir.normalize("a\n\rb"), "a\n\nb\n", "\\n then \\r are two")

    def test_trailing_spaces_and_tabs_only(self):
        self.assertEqual(ir.normalize("a \t \nb\t"), "a\nb\n")
        self.assertEqual(ir.normalize("  a\n\tb"), "  a\n\tb\n", "leading whitespace stays")
        self.assertEqual(ir.normalize("a \nb　"), "a \nb　\n", "only spaces and tabs are trailing")

    def test_blank_lines(self):
        self.assertEqual(ir.normalize("\n\n \t\na\n\n"), "a\n", "leading and trailing blank lines")
        self.assertEqual(ir.normalize("a\n\n\n\nb"), "a\n\nb\n", "a run of blank lines is one")
        self.assertEqual(ir.normalize("a\n  \n\t\n\nb"), "a\n\nb\n", "a line of spaces and tabs is blank")
        self.assertEqual(ir.normalize("a\n \nb"), "a\n \nb\n", "a line of U+00A0 is not blank")

    def test_one_final_line_end(self):
        for text in ("a", "a\n", "a\n\n\n", "a\r\n", "a \n \n"):
            self.assertEqual(ir.normalize(text), "a\n")
        self.assertEqual(ir.normalize(""), "")
        self.assertEqual(ir.normalize(" \n\t\r\n"), "")

    def test_every_other_code_point_stays(self):
        text = "x\x1cy\x1dz\x1e\x1f\x7fw﻿v u\x85t s r​́éé \U0001f600"
        self.assertEqual(ir.normalize(text), text + "\n", "no Unicode normalization, no line end but \\n")
        self.assertEqual(ir.normalize("﻿a"), "﻿a\n")

    def test_idempotent(self):
        for text in ("a\r\n\r\n\r\nb \n", "\f\fa\v", "  x\n\n\ny\t", ""):
            once = ir.normalize(text)
            self.assertEqual(ir.normalize(once), once)

    def test_zero_text(self):
        for text in ("", " ", "\t\n", "\f", "\v", " ", "　", "﻿", "  \f   　 ﻿  ",
                     "  \x85"):
            self.assertTrue(ir.is_zero_text(text), repr(text))
            self.assertTrue(ir.is_zero_text(ir.normalize(text)), "the answer does not change: %r" % text)
        for text in ("a", ".", "﻿a", "\x00", "\x7f", "​", "  x"):
            self.assertFalse(ir.is_zero_text(text), repr(text))
            self.assertFalse(ir.is_zero_text(ir.normalize(text)), repr(text))

    def test_counts(self):
        text = ir.normalize("Café notes — two\n\n\n  three  words here \n")
        self.assertEqual(text, "Café notes — two\n\n  three  words here\n")
        self.assertEqual(ir.counts(text), {"lines": 2, "words": 7, "chars": len(text),
                                           "bytes": len(text.encode("utf-8"))})
        self.assertEqual(ir.counts(text)["bytes"], ir.counts(text)["chars"] + 1 + 2)
        self.assertEqual(ir.counts(""), {"lines": 0, "words": 0, "chars": 0, "bytes": 0})
        self.assertEqual(ir.counts(ir.normalize(text_of("stub-text.txt")))["lines"], 6)


# ------------------------------------------------------------------------------------------------- the report

class ReportTests(unittest.TestCase):

    def spec_reader_keys(self):
        section = spec_section(LAYER_SPEC, "### 21.7 The report")
        block = section.split("```json", 1)[1].split("```", 1)[0]
        return list(json.loads(block)["reader"].keys())

    def test_the_request_alone_when_no_call_was_made(self):
        rep = ir.reader_report(None, model="haiku", effort="high", max_usd="0.05", text_sha256="ab" * 32)
        self.assertEqual(list(rep.keys()), self.spec_reader_keys())
        self.assertEqual(rep, {"adapter": "image-reader/1", "claude_code": None, "model": "haiku", "model_id": None,
                               "effort": "high", "max_usd": 0.05, "cost_usd": None, "duration_ms": None,
                               "turns": None, "output_tokens": None, "thinking_tokens": None, "text_sha256": None})
        self.assertIsInstance(rep["max_usd"], float)

    def test_what_the_call_gave(self):
        r = ir._interpret(bounded(stream(events_of("success.jsonl"))))
        rep = ir.reader_report(r, model="haiku", effort="xhigh", max_usd=0.05, text_sha256="cd" * 32)
        self.assertEqual(list(rep.keys()), self.spec_reader_keys())
        self.assertEqual(rep, {"adapter": "image-reader/1", "claude_code": "2.1.328", "model": "haiku",
                               "model_id": "claude-haiku-5-5", "effort": "xhigh", "max_usd": 0.05,
                               "cost_usd": 0.00080457, "duration_ms": 1337, "turns": 2, "output_tokens": 84,
                               "thinking_tokens": 0, "text_sha256": "cd" * 32})
        budget = ir._interpret(bounded(stream(events_of("budget.jsonl")), rc=1))
        rep = ir.reader_report(budget, model="haiku", effort="high", max_usd="0.0001", text_sha256=None)
        self.assertEqual((rep["max_usd"], rep["cost_usd"] is not None, rep["text_sha256"]), (0.0001, True, None))


# ------------------------------------------------------------------------------------------- the fixtures

class FixtureTests(unittest.TestCase):

    UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
    ID = re.compile(r"\b(toolu|msg|req|sess)_0[0-9A-Za-z]{10,}")

    def strings(self, value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from self.strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from self.strings(v)
        elif isinstance(value, str):
            yield value

    def test_the_recorded_streams_are_scrubbed_and_keep_what_the_adapter_reads(self):
        shapes = {"success.jsonl": ["system", "assistant", "user", "rate_limit_event", "result"],
                  "budget.jsonl": ["system", "assistant", "user", "result"]}
        for name, types in shapes.items():
            with self.subTest(name=name):
                events = events_of(name)
                self.assertEqual([e["type"] for e in events], types)
                for s in self.strings(events):
                    self.assertIsNone(self.UUID.search(s), "no identifier-shaped string")
                    self.assertIsNone(self.ID.search(s), "no message, tool-use or request id")
                    self.assertFalse(s.startswith("/") or "/Users/" in s or "/home/" in s or s.startswith("~"),
                                     "no path: %r" % s[:40])
                init, result = events[0], events[-1]
                for key in ("plugins", "skills", "slash_commands", "terminal_slash_commands", "agents"):
                    self.assertEqual(init[key], [])
                for key in ("cwd", "original_cwd", "session_id", "uuid", "apiKeySource"):
                    self.assertEqual(init[key], "<scrubbed>")
                for key in ("session_id", "uuid", "credential_expires_at"):
                    self.assertEqual(result[key], "<scrubbed>")
                for ev in events:
                    if "timestamp" in ev:
                        self.assertEqual(ev["timestamp"], "<scrubbed>")
                self.assertEqual((init["tools"], init["mcp_servers"], init["model"], init["claude_code_version"]),
                                 (["StructuredOutput"], [], "claude-haiku-5-5", "2.1.328"))
                for key, kind in (("subtype", str), ("is_error", bool), ("total_cost_usd", float), ("duration_ms", int),
                                  ("num_turns", int), ("permission_denials", list), ("modelUsage", dict)):
                    self.assertIsInstance(result[key], kind, key)
                assistant = events[1]["message"]["content"][0]["input"]["text"]
                self.assertEqual(assistant, SYNTH, "the model's text is replaced")
        self.assertEqual(events_of("success.jsonl")[-1]["structured_output"], {"text": SYNTH})
        self.assertEqual(json.loads(events_of("success.jsonl")[-1]["result"]), {"text": SYNTH})
        budget = events_of("budget.jsonl")[-1]
        self.assertNotIn("structured_output", budget)
        self.assertEqual((budget["subtype"], budget["is_error"]), ("error_max_budget_usd", True))
        self.assertTrue(all(isinstance(e, str) for e in budget["errors"]))
        self.assertTrue(os.path.isfile(os.path.join(READER_FIX, "README.md")))
        self.assertTrue(os.access(STUB, os.X_OK), "the stub is executable")


if __name__ == "__main__":
    unittest.main()
