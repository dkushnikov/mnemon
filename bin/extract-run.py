#!/usr/bin/env python3
"""extract-run.py — run the fenced extractor once (spec §4.2, D4, D14).

    extract-run.py --attempt-dir <dir> --prompt <prompt.md> --schema <schema.json> \
                   --model <model> --timeout <seconds> [--max-turns 4] [--max-budget-usd X] \
                   [--claude <command>]

Builds exactly this argv — never a positional prompt, never --settings, --add-dir, --mcp-config,
--allowedTools, --permission-mode or --dangerously-skip-permissions:

    claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config \
           --output-format json --json-schema <schema> --max-turns N --model M [--max-budget-usd X]

writes it to <attempt-dir>/argv.txt (one shell-style line, `""` for the empty --tools value) and
<attempt-dir>/argv.json (the list, for the receipt's `argv`), runs it with the attempt dir as the
working directory, the prompt on stdin, stdout → result.json and
stderr → stderr.txt, in a new session (its own process group). On `--timeout` the whole group is
SIGTERMed, then SIGKILLed, and the child is reaped before the attempt is reported. Nothing else
kills a capture.

Stdout is one word, the class the gateway maps: `ok` (exit 0, result.json parses as a JSON
object) · `timeout` · `claude-exit` (non-zero exit; result.json kept for its subtype) ·
`invalid-json` (exit 0 but no JSON object). Exit status 0 for `ok`, 1 otherwise.

The inherited vault-lock descriptor (MNEMON_VAULT_LOCK_FD, see bin/vault-lock.py) is passed to the
child on purpose: every process that can touch capture state keeps the kernel lock alive.
Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

TERM_GRACE = 5.0


def shell_line(argv: list) -> str:
    out = []
    for a in argv:
        if a == "":
            out.append('""')
        elif any(c.isspace() or c in "\"'$`\\" for c in a):
            out.append('"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"')
        else:
            out.append(a)
    return " ".join(out)


def inherited_lock_fd():
    raw = os.environ.get("MNEMON_VAULT_LOCK_FD")
    if not raw:
        return None
    try:
        fd = int(raw)
        os.fstat(fd)
    except (ValueError, OSError):
        return None
    return fd


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # members we cannot signal still exist
    return True


def kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the extractor's process group, then SIGKILL it, reaping the child in between.

    The direct child is reaped with wait() before the group is re-checked: on macOS a zombie
    member makes killpg(pgid, 0) answer EPERM, which would read as "still running" forever.
    """
    pgid = proc.pid
    for sig, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        deadline = time.time() + grace
        while group_alive(pgid) and time.time() < deadline:
            time.sleep(0.05)
        if not group_alive(pgid):
            break
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the fenced extractor once")
    ap.add_argument("--attempt-dir", required=True)
    ap.add_argument("--prompt", required=True, help="prompt file, passed on stdin")
    ap.add_argument("--schema", required=True, help="JSON schema file for --json-schema")
    ap.add_argument("--model", required=True)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--max-budget-usd", default=None)
    ap.add_argument("--claude", default="claude")
    args = ap.parse_args()

    attempt = os.path.abspath(args.attempt_dir)
    with open(args.schema, "r", encoding="utf-8") as fh:
        schema = json.dumps(json.load(fh), separators=(",", ":"), ensure_ascii=False)

    argv = [args.claude, "-p", "--restricted", "--tools", "", "--permission-prompts", "none",
            "--strict-mcp-config", "--output-format", "json", "--json-schema", schema,
            "--max-turns", str(args.max_turns), "--model", args.model]
    if args.max_budget_usd:
        argv += ["--max-budget-usd", str(args.max_budget_usd)]

    with open(os.path.join(attempt, "argv.txt"), "w", encoding="utf-8") as fh:
        fh.write(shell_line(argv) + "\n")
    with open(os.path.join(attempt, "argv.json"), "w", encoding="utf-8") as fh:
        json.dump(argv, fh, ensure_ascii=False)
        fh.write("\n")

    result_path = os.path.join(attempt, "result.json")
    stderr_path = os.path.join(attempt, "stderr.txt")
    lock_fd = inherited_lock_fd()
    pass_fds = (lock_fd,) if lock_fd is not None else ()

    with open(args.prompt, "rb") as stdin_fh, open(result_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
        try:
            proc = subprocess.Popen(argv, stdin=stdin_fh, stdout=out_fh, stderr=err_fh, cwd=attempt,
                                    start_new_session=True, pass_fds=pass_fds)
        except OSError as exc:
            err_fh.write(f"extract-run: cannot start {args.claude}: {exc.strerror}\n".encode("utf-8"))
            print("claude-exit")
            return 1
        try:
            rc = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            err_fh.write(f"extract-run: timeout after {args.timeout:g}s; process group {proc.pid} terminated\n".encode("utf-8"))
            print("timeout")
            return 1

    if rc != 0:
        print("claude-exit")
        return 1
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        print("invalid-json")
        return 1
    if not isinstance(doc, dict):
        print("invalid-json")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
