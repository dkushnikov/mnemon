#!/usr/bin/env python3
"""stub-claude-image.py — the stub reader of the image origin (capture layer §21.13). Never talks to a model.

Used as the reader's `claude` (through `CLAUDE_CMD`, or the worker's `--claude`). `--version` answers like
the extraction stub (`9.9.9-stub (Claude Code)`) and `--help` lists `--restricted`; neither is a call. Every
other invocation is a reading call:

1. one line `read` is appended to the counter file `MNEMON_TEST_READER_CALLS` (when it is set), at once;
2. the argv must be the call of §21.4 — the fence flags, `--input-format stream-json --output-format
   stream-json --verbose`, the schema `image-reading/1`, `--max-turns 4`, `--model <name>`, `--effort <level>`,
   `--max-budget-usd <usd>`, in that order and nothing else;
3. stdin must be the message of §21.4 — one JSON line: a `user` message whose content is an `image` block
   (base64, `image/png` or `image/jpeg`, bytes with that signature) and a `text` block equal to the adapter's
   prompt, and no other key;
4. when `MNEMON_TEST_READER_FACTS` names a file, the stub writes there, as JSON, what a test may assert
   about the call: whether `CLAUDE_CODE_EFFORT_LEVEL` is set and equals `--effort`, the names of the
   `MNEMON_VAULT_LOCK_*` variables it sees, its working directory, the sha256 and media type of the image, and
   in mode `hang-child` the pid of its child. Names and yes/no only, never a value of the environment;
5. it answers by `MNEMON_TEST_READER_MODE` (default `ok`) on stdout, as a stream-json event stream built from
   the recorded streams in `reader/` (`success.jsonl`, `budget.jsonl`).

A wrong argv, a wrong message or an unknown mode → exit 97 with one line on stderr.

Modes: ok · other-text · empty · whitespace · control-chars · nul · surrogate · no-result · no-structured ·
text-not-string · error · auth · budget · max-turns · no-init · init-no-tools · tool-in-init · mcp-in-init ·
denial · stdout-flood · hang · hang-child · one-char. `ok` returns the text of `stub-text.txt`, `other-text` the
text of `stub-text-other.txt` (both in this directory), `one-char` the one visible character `x`, inside the
recorded success stream with its text replaced.
Stdlib only, python 3.9.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
sys.path.insert(0, os.path.join(ROOT, "bin"))

from capture.image_reader import PROMPT  # noqa: E402  (the message must carry the adapter's prompt)

FENCE = ["-p", "--restricted", "--tools", "", "--permission-prompts", "none", "--strict-mcp-config",
         "--no-session-persistence", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
          "additionalProperties": False}
LEVELS = ("low", "medium", "high", "xhigh", "max")
SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}

HELP = """Usage: claude [options] [command] [prompt]
Options:
  -p, --print                  Print response and exit
  --restricted                 Restrict file tools to the working directory and disable shell tools
  --tools <tools...>           Tool names the model may use
  --permission-prompts <mode>  acceptEdits|none|...
  --strict-mcp-config          Only use MCP servers from --mcp-config
  --input-format <format>      text|stream-json
  --output-format <format>     text|json|stream-json
  --json-schema <schema>       JSON schema for structured output
"""


def refuse(why: str) -> None:
    sys.stderr.write("stub-claude-image: " + why + "\n")
    sys.stderr.flush()
    sys.exit(97)


def check_argv(args):
    """None when the argv is the call of §21.4, else why not."""
    if args[:len(FENCE)] != FENCE:
        for i, want in enumerate(FENCE):
            if i >= len(args) or args[i] != want:
                return "argv[%d] is %r, the call of 21.4 has %r there" % (i + 1, args[i] if i < len(args) else None, want)
    rest = args[len(FENCE):]
    if len(rest) != 10:
        return "%d arguments after the fence flags, the call of 21.4 has 10" % len(rest)
    if rest[0] != "--json-schema":
        return "--json-schema expected, got %r" % rest[0]
    try:
        schema = json.loads(rest[1])
    except ValueError:
        return "the --json-schema value is not JSON"
    if schema != SCHEMA:
        return "the --json-schema value is not image-reading/1"
    if rest[2:4] != ["--max-turns", "4"]:
        return "--max-turns 4 expected, got %r" % (rest[2:4],)
    if rest[4] != "--model" or not rest[5] or rest[5].startswith("-"):
        return "--model <name> expected, got %r" % (rest[4:6],)
    if rest[6] != "--effort" or rest[7] not in LEVELS:
        return "--effort <level> expected, got %r" % (rest[6:8],)
    if rest[8] != "--max-budget-usd":
        return "--max-budget-usd expected, got %r" % rest[8]
    try:
        usd = float(rest[9])
    except ValueError:
        return "the --max-budget-usd value is not a number"
    if not math.isfinite(usd) or usd <= 0:
        return "the --max-budget-usd value is not a positive number"
    return None


def check_message(data: bytes):
    """(media type, image bytes) when stdin is the message of §21.4; refuses otherwise."""
    if not data.endswith(b"\n") or data.count(b"\n") != 1:
        refuse("stdin is not one JSON line")
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        refuse("stdin is not JSON")
    if not isinstance(msg, dict) or set(msg) != {"type", "message"} or msg["type"] != "user":
        refuse("stdin is not one user message with the keys type and message")
    inner = msg["message"]
    if not isinstance(inner, dict) or set(inner) != {"role", "content"} or inner["role"] != "user":
        refuse("the message is not {role: user, content}")
    content = inner["content"]
    if not isinstance(content, list) or len(content) != 2:
        refuse("the content is not two blocks")
    image, text = content
    if not isinstance(image, dict) or set(image) != {"type", "source"} or image["type"] != "image":
        refuse("the first block is not an image block with the keys type and source")
    source = image["source"]
    if not isinstance(source, dict) or set(source) != {"type", "media_type", "data"} or source["type"] != "base64":
        refuse("the image source is not {type: base64, media_type, data}")
    media = source["media_type"]
    if media not in SIGNATURES:
        refuse("the media type is neither image/png nor image/jpeg")
    if not isinstance(source["data"], str):
        refuse("the image data is not a string")
    try:
        raw = base64.b64decode(source["data"].encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        refuse("the image data is not base64")
    if base64.b64encode(raw).decode("ascii") != source["data"]:
        refuse("the image data is not the canonical base64 of its bytes")
    if not raw.startswith(SIGNATURES[media]):
        refuse("the image bytes do not carry the signature of " + media)
    if not isinstance(text, dict) or set(text) != {"type", "text"} or text["type"] != "text":
        refuse("the second block is not a text block with the keys type and text")
    if text["text"] != PROMPT:
        refuse("the text block is not the adapter's prompt")
    return media, raw


def load(name: str):
    with open(os.path.join(HERE, "reader", name), "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def stub_text(name: str) -> str:
    with open(os.path.join(HERE, name), "r", encoding="utf-8") as fh:
        return fh.read()


def with_text(events, text):
    """The events with the model's text replaced wherever the recorded stream carries it."""
    out = copy.deepcopy(events)
    for ev in out:
        if ev.get("type") == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                    block["input"]["text"] = text
            for value in (ev.get("wire_tool_inputs") or {}).values():
                value["text"] = text
        elif ev.get("type") == "result" and isinstance(ev.get("structured_output"), dict):
            ev["structured_output"] = {"text": text}
            ev["result"] = json.dumps(ev["structured_output"], separators=(",", ":"), ensure_ascii=False)
    return out


def error_result(subtype: str, errors=None, result=None):
    """A result event of an error, shaped as the recorded over-budget result."""
    ev = copy.deepcopy(load("budget.jsonl")[-1])
    ev["subtype"] = subtype
    ev["is_error"] = True
    if errors is None:
        ev.pop("errors", None)
    else:
        ev["errors"] = errors
    if result is not None:
        ev["result"] = result
    return ev


def replace(events, kind: str, fn):
    for ev in events:
        if (kind == "init" and ev.get("type") == "system" and ev.get("subtype") == "init") or ev.get("type") == kind:
            fn(ev)
    return events


def stream(mode: str):
    """(events, exit status, stderr text) of a mode that answers at once."""
    ok = stub_text("stub-text.txt")
    success = load("success.jsonl")
    if mode == "ok":
        return with_text(success, ok), 0, ""
    if mode == "other-text":
        return with_text(success, stub_text("stub-text-other.txt")), 0, ""
    if mode == "empty":
        return with_text(success, ""), 0, ""
    if mode == "one-char":
        return with_text(success, "x"), 0, ""
    if mode == "whitespace":
        return with_text(success, "  \f   　 ﻿  "), 0, ""
    if mode == "control-chars":
        first, _, rest = ok.partition("\n")
        return with_text(success, first.replace(" ", " \x1c", 1) + "\x7f and﻿ more\n" + rest), 0, ""
    if mode == "nul":
        return with_text(success, ok.replace(" ", " \x00", 1)), 0, ""
    if mode == "surrogate":
        return with_text(success, ok.replace(" ", " \ud800", 1)), 0, ""
    if mode == "no-result":
        return [ev for ev in with_text(success, ok) if ev.get("type") != "result"], 0, ""
    if mode == "no-structured":
        def drop(ev):
            ev.pop("structured_output", None)
            ev["result"] = "stub: an answer without structured output"
        return replace(with_text(success, ok), "result", drop), 0, ""
    if mode == "text-not-string":
        def wrong(ev):
            ev["structured_output"] = {"text": ["not", "a", "string"]}
            ev["result"] = json.dumps(ev["structured_output"], separators=(",", ":"))
        return replace(with_text(success, ok), "result", wrong), 0, ""
    if mode == "error":
        return [success[0], error_result("error_during_execution", ["stub: the call failed"])], 1, "stub: the call failed\n"
    if mode == "auth":
        return [success[0], error_result("success", None, "Not logged in · Please run /login")], 1, ""
    if mode == "budget":
        return load("budget.jsonl"), 1, ""
    if mode == "max-turns":
        events = with_text(success, ok)[:3]
        return events + [error_result("error_max_turns", ["Reached maximum number of turns (4)"])], 1, ""
    if mode == "no-init":
        return [ev for ev in with_text(success, ok) if ev.get("type") != "system"], 0, ""
    if mode == "init-no-tools":
        return replace(with_text(success, ok), "init", lambda ev: ev.pop("tools", None)), 0, ""
    if mode == "tool-in-init":
        return replace(with_text(success, ok), "init", lambda ev: ev.update(tools=["StructuredOutput", "Bash"])), 0, ""
    if mode == "mcp-in-init":
        return replace(with_text(success, ok), "init",
                       lambda ev: ev.update(mcp_servers=[{"name": "stub-server", "status": "connected"}])), 0, ""
    if mode == "denial":
        denial = [{"tool_name": "Bash", "tool_use_id": "stub-tool-use", "tool_input": {"command": "true"}}]
        return replace(with_text(success, ok), "result", lambda ev: ev.update(permission_denials=denial)), 0, ""
    return None


def emit(events, ascii_only: bool = False) -> None:
    out = sys.stdout.buffer
    for ev in events:
        out.write((json.dumps(ev, separators=(",", ":"), ensure_ascii=ascii_only) + "\n").encode("utf-8"))
        out.flush()


def write_facts(args, media: str, raw: bytes, child_pid=None) -> None:
    path = os.environ.get("MNEMON_TEST_READER_FACTS")
    if not path:
        return
    effort = args[args.index("--effort") + 1]
    level = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    facts = {"effort_env_set": level is not None,
             "effort_env_matches_argv": level == effort,
             "vault_lock_vars": sorted(k for k in os.environ if k.startswith("MNEMON_VAULT_LOCK_")),
             "cwd": os.getcwd(),
             "media_type": media,
             "image_sha256": hashlib.sha256(raw).hexdigest(),
             "child_pid": child_pid}
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(facts, fh)
    os.replace(path + ".tmp", path)


MODES = ("ok", "other-text", "empty", "whitespace", "control-chars", "nul", "surrogate", "no-result", "no-structured",
         "text-not-string", "error", "auth", "budget", "max-turns", "no-init", "init-no-tools", "tool-in-init",
         "mcp-in-init", "denial", "stdout-flood", "hang", "hang-child", "one-char")


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        print("9.9.9-stub (Claude Code)")
        return 0
    if args[:1] == ["--help"]:
        sys.stdout.write(HELP)
        return 0
    calls = os.environ.get("MNEMON_TEST_READER_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as fh:
            fh.write("read\n")
    why = check_argv(args)
    if why:
        refuse(why)
    media, raw = check_message(sys.stdin.buffer.read())
    mode = os.environ.get("MNEMON_TEST_READER_MODE") or "ok"
    if mode not in MODES:
        refuse("unknown MNEMON_TEST_READER_MODE %r" % mode)
    if mode == "hang-child":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        write_facts(args, media, raw, child.pid)
        time.sleep(600)
        return 0
    write_facts(args, media, raw)
    if mode == "hang":
        time.sleep(600)
        return 0
    if mode == "stdout-flood":
        emit(with_text(load("success.jsonl"), stub_text("stub-text.txt")))
        line = b"x" * 65535 + b"\n"
        while True:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    events, rc, err = stream(mode)
    emit(events, ascii_only=(mode == "surrogate"))
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    return rc


if __name__ == "__main__":
    sys.exit(main())
