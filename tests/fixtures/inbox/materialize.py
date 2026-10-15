#!/usr/bin/env python3
"""materialize.py — write contract-fixture envelopes into a temp inbox (tests/test_ingest.sh).

    materialize.py <envelopes.json> <inbox-dir> [options] [fixture name ...]

With no names every fixture is written. Payload files are written first (as a producer
must), then the envelope as `.<file>.tmp` + rename. Options:
  --clone NAME=NEW_ID   write fixture NAME under a new envelope_id (file NEW_ID.json)
  --no-payloads         skip the payload files (to exercise `incomplete`)
  --instance NAME       override the envelope's instance field
Stdlib only, python 3.9.
"""
from __future__ import annotations

import json
import os
import sys


def write_envelope(inbox: str, file_name: str, envelope: dict) -> None:
    final = os.path.join(inbox, file_name)
    tmp = os.path.join(inbox, "." + file_name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(envelope, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, final)


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2:
        sys.stderr.write(__doc__)
        return 2
    fixtures_path, inbox = args[0], args[1]
    names = []
    clones = []
    payloads = True
    instance = None
    i = 2
    while i < len(args):
        a = args[i]
        if a == "--clone":
            name, _, new_id = args[i + 1].partition("=")
            clones.append((name, new_id))
            i += 2
        elif a == "--no-payloads":
            payloads = False
            i += 1
        elif a == "--instance":
            instance = args[i + 1]
            i += 2
        else:
            names.append(a)
            i += 1
    with open(fixtures_path, encoding="utf-8") as fh:
        cases = {c["name"]: c for c in json.load(fh)}
    os.makedirs(inbox, exist_ok=True)
    todo = []
    for name in names or ([] if clones else list(cases)):
        todo.append((cases[name], None))
    for name, new_id in clones:
        todo.append((cases[name], new_id))
    for case, new_id in todo:
        envelope = json.loads(json.dumps(case["envelope"]))
        file_name = case["file"]
        if new_id:
            envelope["envelope_id"] = new_id
            file_name = new_id + ".json"
        if instance:
            envelope["instance"] = instance
        if payloads:
            for rel, hexdata in case.get("payload_files", {}).items():
                path = os.path.join(inbox, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(bytes.fromhex(hexdata))
        write_envelope(inbox, file_name, envelope)
        print(file_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
