#!/usr/bin/env python3
"""lifecycle.py — the live image gate's check of the real client's lifecycle (capture layer §21.13; contract K9).

    lifecycle.py watch --supervisor <pid> --out <json> [--record <wrapper record>] [--scratch <dev:ino>]
                       [--go <file>] [--interval <s>] [--max-seconds <s>]
    lifecycle.py control --record <wrapper record> --scratch <dev:ino> --rc <exit> --seq <n> --out <item.json>

`watch` runs beside one call of the worker (`bin/capture-image.py`, the supervisor, whose pid the gate holds).
While the supervisor lives it samples the process table (`ps -A -o pid=,ppid=,pgid=,stat=,lstart=`) — back to
back for the first three seconds (one sample costs some 35 ms on a laptop with 1 800 processes), then every
`--interval` seconds — and keeps every descendant of the supervisor it sees, with its process group and start
time: the worker (the supervisor's child that leads its own group), tesseract, the reader's `claude` and
whatever that starts. With `--go` it creates that file after its first sample: the gate starts the supervisor
through a launcher that waits for it, so that sampling runs before the worker starts (a worker refused at the
envelope lives a tenth of a second). A process that lives between two samples is not seen. Once the
supervisor has ended — gone from the table, a zombie, or its pid now another process — it checks at once,
from one more sample:

  - no member of the worker's process group is left (the group from the samples, and the one the wrapper
    recorded); `killpg(pgid, 0)` finds no process either; and no sample, once the worker had ended, saw a
    member of its group (a worker that ends normally has waited for every command it started, so such a
    member outlived it even when the supervisor then tore it down);
  - no descendant of the reader's `claude` (the wrapper's pid, which exec keeps) is left, the client itself
    included, nor any other descendant of the supervisor — a process counts as left when its pid is in the
    table with the start time it was seen with;
  - the wrapper record (when the reader was called): no `MNEMON_VAULT_LOCK_*` variable name, and no open
    descriptor on the file the gate opened as the vault-lock descriptor (`--scratch`, its `st_dev:st_ino`).

It writes the result to `--out` and prints one line of numbers; exit 0 when the run passes, 1 when not.
A worker that ended before the first sample is `group_observed: false` — recorded, not a failure, when no
command was started (no wrapper record); with a wrapper record the group is the one the wrapper saw.

`control` judges the gate's own instrument: the wrapper started directly with the descriptor open and the
variables set, and the real client replaced by `true` — the record must name both variables and list the
descriptor. A check that cannot see an open descriptor would pass every run. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

PS_FIELDS = "pid=,ppid=,pgid=,stat=,lstart="     # no `comm`: it nearly doubles the cost of a sample on macOS
FAST_SECONDS = 3.0      # the first seconds are sampled back to back: a refused envelope ends the worker in ~0.1 s


def _ps_env() -> dict:
    return dict(os.environ, LC_ALL="C")


def ps_table() -> dict:
    """{pid: {"ppid", "pgid", "stat", "lstart"}} of every process."""
    res = subprocess.run(["ps", "-A", "-o", PS_FIELDS], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, env=_ps_env())
    table = {}
    for line in res.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        table[pid] = {"ppid": ppid, "pgid": pgid, "stat": parts[3], "lstart": " ".join(parts[4:9])}
    return table


def command_name(pid: int) -> str:
    """The basename of a live process's command, for the record of what was left."""
    res = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, env=_ps_env())
    return os.path.basename(res.stdout.decode("utf-8", "replace").strip())


def descendants(table: dict, root: int) -> set:
    children = {}
    for pid, row in table.items():
        children.setdefault(row["ppid"], []).append(pid)
    out = set()
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in out and child != root:
                out.add(child)
                stack.append(child)
    return out


def load_record(path):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def scratch_id(text):
    if not text:
        return None
    dev, _, ino = text.partition(":")
    return int(dev), int(ino)


def lock_findings(record: dict, scratch) -> dict:
    """What the wrapper saw of the vault lock: variable names, and descriptors on the scratch file."""
    names = [n for n in (record.get("lock_vars") or []) if isinstance(n, str)]
    fds = []
    for entry in record.get("fds") or []:
        if isinstance(entry, list) and len(entry) >= 3 and scratch is not None and (entry[1], entry[2]) == scratch:
            fds.append(entry[0])
    return {"lock_vars_seen": names, "lock_fds": fds}


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch(args) -> dict:
    sup = args.supervisor
    seen = {}               # pid → the row it was first seen with, and whether it is in the client's tree
    worker_pgids = set()
    outlived = set()        # members of the worker's group seen after the worker had ended
    sup_start = None
    samples = 0
    record = None
    started = time.monotonic()
    while True:
        table = ps_table()
        samples += 1
        row = table.get(sup)
        if sup_start is None and row is not None:
            sup_start = row["lstart"]
        if samples == 1 and args.go:
            with open(args.go, "w"):        # the gate's launcher waits for it: sampling runs before the worker starts
                pass
        ended = row is None or row["stat"].startswith("Z") or (sup_start is not None and row["lstart"] != sup_start)
        if not ended:
            if record is None:
                record = load_record(args.record)
            tree = descendants(table, sup)
            client = set()
            if record is not None and isinstance(record.get("pid"), int):
                cpid = record["pid"]
                if cpid in tree:
                    client = {cpid} | descendants(table, cpid)
            for pid in tree:
                r = table[pid]
                if pid not in seen:
                    seen[pid] = {"ppid": r["ppid"], "pgid": r["pgid"], "lstart": r["lstart"], "client": False}
                if pid in client:
                    seen[pid]["client"] = True
                if r["ppid"] == sup and r["pgid"] == pid:
                    worker_pgids.add(pid)
            # a worker that ends normally has waited for every command it started: a member of its group
            # seen once the worker is gone (or a zombie) outlived it, even if the supervisor then tears it down
            for pgid in worker_pgids:
                leader = table.get(pgid)
                if leader is None or leader["stat"].startswith("Z"):
                    outlived.update(pid for pid, r in table.items() if r["pgid"] == pgid and pid != pgid)
        if ended or time.monotonic() - started > args.max_seconds:
            break
        if time.monotonic() - started > FAST_SECONDS:
            time.sleep(args.interval)

    # the check, at once after the supervisor's end
    final = ps_table()
    if record is None:
        record = load_record(args.record)
    failures = []
    timed_out = not ended
    if timed_out:
        failures.append("the supervisor still ran after %g s" % args.max_seconds)
    if record is not None and isinstance(record.get("pgid"), int):
        worker_pgids.add(record["pgid"])
    members = sorted(pid for pid, r in final.items() if r["pgid"] in worker_pgids)
    alive_groups = sorted(g for g in worker_pgids if group_alive(g))
    left = sorted(pid for pid, s in seen.items() if pid in final and final[pid]["lstart"] == s["lstart"])
    client_seen = sorted(pid for pid, s in seen.items() if s["client"])
    client_left = sorted(pid for pid in left if seen[pid]["client"])
    if members:
        failures.append("%d member(s) of the worker's process group left" % len(members))
    elif alive_groups:
        failures.append("the worker's process group still answers a signal")
    torn_down = outlived - set(members)
    if torn_down:
        failures.append("%d member(s) of the worker's process group outlived the worker" % len(torn_down))
    outlived |= set(members)     # at the final sample the worker has ended: what is left outlived it too
    if client_left:
        failures.append("%d process(es) of the reader's client left" % len(client_left))
    if len(left) > len(client_left):
        failures.append("%d other descendant(s) of the worker left" % (len(left) - len(client_left)))
    lock = {"lock_vars_seen": [], "lock_fds": []}
    if record is not None:
        if not client_seen:
            failures.append("the reader's client was never seen in the process table")
        lock = lock_findings(record, scratch_id(args.scratch))
        if lock["lock_vars_seen"]:
            failures.append("the reader's process saw %d vault-lock variable(s)" % len(lock["lock_vars_seen"]))
        if lock["lock_fds"]:
            failures.append("the reader's process had the vault-lock descriptor open")
    result = {"pass": not failures, "failures": failures, "samples": samples,
              "model_call": record is not None, "group_observed": bool(worker_pgids),
              "worker_pgids": sorted(worker_pgids), "members_left": len(members), "groups_alive": len(alive_groups),
              "members_after_worker": len(outlived),
              "descendants_seen": len(seen), "descendants_left": len(left),
              "claude_tree_seen": len(client_seen), "claude_tree_left": len(client_left),
              "lock_vars_seen": len(lock["lock_vars_seen"]), "lock_fd_open": bool(lock["lock_fds"]),
              "seen": {str(pid): s for pid, s in sorted(seen.items())},
              "left": [{"pid": pid, "comm": command_name(pid), "stat": final[pid]["stat"],
                        "client": seen[pid]["client"]} for pid in left]}
    return result


def watch_line(result: dict) -> str:
    return ("lifecycle: %s — %d samples, model call %s, group observed %s, members after the worker %d, members "
            "left %d, client processes seen %d left %d, other descendants left %d, lock variables seen %d, lock "
            "descriptor %s"
            % ("pass" if result["pass"] else "FAIL", result["samples"], "yes" if result["model_call"] else "no",
               "yes" if result["group_observed"] else "no", result["members_after_worker"], result["members_left"],
               result["claude_tree_seen"],
               result["claude_tree_left"], result["descendants_left"] - result["claude_tree_left"],
               result["lock_vars_seen"], "open" if result["lock_fd_open"] else "closed"))


def control(args) -> dict:
    record = load_record(args.record)
    failures = []
    if args.rc != 0:
        failures.append("the wrapper exited %d" % args.rc)
    if record is None:
        failures.append("no wrapper record")
        lock = {"lock_vars_seen": [], "lock_fds": []}
    else:
        lock = lock_findings(record, scratch_id(args.scratch))
        if sorted(lock["lock_vars_seen"]) != ["MNEMON_VAULT_LOCK_FD", "MNEMON_VAULT_LOCK_INODE"]:
            failures.append("the record names %d of the 2 vault-lock variables" % len(lock["lock_vars_seen"]))
        if not lock["lock_fds"]:
            failures.append("the record does not list the open descriptor")
    ok = not failures
    line = ("lifecycle-instrument: %s — the wrapper, started with the descriptor open, listed it %d time(s) and "
            "named %d vault-lock variable(s)%s" % ("pass" if ok else "FAIL", len(lock["lock_fds"]),
                                                   len(lock["lock_vars_seen"]),
                                                   "" if ok else " (%s)" % "; ".join(failures)))
    return {"seq": args.seq, "name": "lifecycle-instrument", "kind": "instrument",
            "status": "pass" if ok else "fail", "failures": failures, "line": line}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The live image gate's lifecycle check")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("watch")
    p.add_argument("--supervisor", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--record")
    p.add_argument("--scratch")
    p.add_argument("--go")
    p.add_argument("--interval", type=float, default=0.05)
    p.add_argument("--max-seconds", dest="max_seconds", type=float, default=900.0)
    p = sub.add_parser("control")
    p.add_argument("--record", required=True)
    p.add_argument("--scratch", required=True)
    p.add_argument("--rc", type=int, required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "watch":
        result = watch(args)
        line = watch_line(result)
    elif args.cmd == "control":
        result = control(args)
        line = result["line"]
    else:
        ap.print_usage(sys.stderr)
        return 2
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.rename(tmp, args.out)
    print(line)
    return 0 if result.get("pass", result.get("status") == "pass") else 1


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(main())
