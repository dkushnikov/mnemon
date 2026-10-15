#!/usr/bin/env python3
"""stub-add.py — a stand-in for `mnemon add` used by tests/test_ingest.sh.

Wired through MNEMON_TEST_HOOKS=1 MNEMON_INGEST_ADD_CMD="python3 <this file>", it receives
the argv the consumer built (the arguments after `add`), the same stdin and the same
environment, records them, and answers from tests/fixtures/contracts/gateway-outcomes.json.

Environment (set by the test):
  STUB_DIR         where call records go: <STUB_DIR>/calls/<seq>.json (+ <seq>.stdin)
  STUB_OUTCOMES    path of gateway-outcomes.json
  STUB_SCENARIOS   optional JSON file: envelope_id -> scenario name, or an inline scenario
                   object {"exit": N, "stdout": [...], "stderr": "...", "sleep": s}
  STUB_SCENARIO    optional scenario name for every call (after STUB_SCENARIOS)
  STUB_STATE_DIR   the ingest state dir (to check that dispatch.json exists before the call)
  STUB_LOCK_PATH   the vault lock file (to check that the lock is held while the stub runs)
Without a match the fixture's default_scenario_by_case decides; otherwise `extracted`.
Stdlib only, python 3.9.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    stub_dir = os.environ.get("STUB_DIR", "")
    with open(os.environ["STUB_OUTCOMES"], encoding="utf-8") as fh:
        outcomes = json.load(fh)
    envelope_id = ""
    if "--capture-id" in argv:
        envelope_id = argv[argv.index("--capture-id") + 1]
    case = envelope_id.split(":", 1)[1] if ":" in envelope_id else envelope_id

    stdin_data = b""
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.buffer.read()

    scenario_name = None
    scenario = None
    scen_file = os.environ.get("STUB_SCENARIOS", "")
    if scen_file and os.path.isfile(scen_file):
        with open(scen_file, encoding="utf-8") as fh:
            chosen = json.load(fh).get(envelope_id)
        if isinstance(chosen, dict):
            scenario, scenario_name = chosen, "inline"
        elif isinstance(chosen, str):
            scenario_name = chosen
    if scenario is None and scenario_name is None and os.environ.get("STUB_SCENARIO"):
        scenario_name = os.environ["STUB_SCENARIO"]
    if scenario is None and scenario_name is None:
        scenario_name = outcomes["default_scenario_by_case"].get(case, "extracted")
    if scenario is None:
        scenario = outcomes["scenarios"][scenario_name]

    lock_fd_valid = False
    fd_text = os.environ.get("MNEMON_VAULT_LOCK_FD", "")
    if fd_text:
        try:
            st = os.fstat(int(fd_text))
            lock_fd_valid = str(st.st_ino) == os.environ.get("MNEMON_VAULT_LOCK_INODE", "")
        except (ValueError, OSError):
            lock_fd_valid = False
    lock_held = None
    lock_path = os.environ.get("STUB_LOCK_PATH", "")
    if lock_path and os.path.exists(lock_path):
        probe = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe, fcntl.LOCK_UN)
            lock_held = False
        except OSError:
            lock_held = True
        finally:
            os.close(probe)

    dispatch_exists = None
    state_dir = os.environ.get("STUB_STATE_DIR", "")
    if state_dir and envelope_id:
        dispatch_exists = os.path.isfile(os.path.join(state_dir, "payloads", envelope_id, "dispatch.json"))
    file_sha = None
    if "--file" in argv:
        fpath = argv[argv.index("--file") + 1]
        if os.path.isfile(fpath):
            with open(fpath, "rb") as fh:
                file_sha = hashlib.sha256(fh.read()).hexdigest()
    provenance_ok = None
    if "--provenance-file" in argv:
        provenance_ok = os.path.isfile(argv[argv.index("--provenance-file") + 1])

    if stub_dir:
        calls = os.path.join(stub_dir, "calls")
        os.makedirs(calls, exist_ok=True)
        seq = len([n for n in os.listdir(calls) if n.endswith(".json")]) + 1
        record = {
            "seq": seq,
            "argv": argv,
            "envelope_id": envelope_id,
            "scenario": scenario_name,
            "stdin_sha256": hashlib.sha256(stdin_data).hexdigest() if stdin_data else None,
            "stdin_bytes": len(stdin_data),
            "env": {k: os.environ.get(k) for k in ("MNEMON_INSTANCE", "MNEMON_CONFIG", "MNEMON_VAULT_LOCK_FD",
                                                    "MNEMON_VAULT_LOCK_INODE", "MNEMON_TEST_HOOKS")},
            "lock_fd_valid": lock_fd_valid,
            "lock_held": lock_held,
            "dispatch_json_exists": dispatch_exists,
            "provenance_exists": provenance_ok,
            "file_sha256": file_sha,
            "cwd": os.getcwd(),
        }
        with open(os.path.join(calls, "%04d.json" % seq), "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1)
        with open(os.path.join(calls, "%04d.stdin" % seq), "wb") as fh:
            fh.write(stdin_data)

    if scenario.get("sleep"):
        time.sleep(float(scenario["sleep"]))
    for line in scenario.get("stdout", []):
        sys.stdout.write(line.replace("<envelope_id>", envelope_id) + "\n")
    sys.stdout.flush()
    if scenario.get("stderr"):
        sys.stderr.write(scenario["stderr"].replace("<envelope_id>", envelope_id) + "\n")
        sys.stderr.flush()
    return int(scenario.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
