#!/usr/bin/env bash
# test_ingest.sh — `mnemon ingest` (bin/mnemon-ingest.py) against the shared contract
# fixtures (tests/fixtures/contracts/) with a stub `add` wired through
# MNEMON_TEST_HOOKS=1 MNEMON_INGEST_ADD_CMD: §9.13 of docs/specs/2026-10-15-capture-contracts.md.
# The stub records argv, stdin and environment per call and answers from
# gateway-outcomes.json. The real dispatch chain (§9.14) is tests/test_chain.sh.
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MNEMON="$MNEMON_ROOT/bin/mnemon"
INGEST_PY="$MNEMON_ROOT/bin/mnemon-ingest.py"
FIX="$MNEMON_ROOT/tests/fixtures/contracts"
ENVELOPES="$FIX/envelopes.json"
OUTCOMES="$FIX/gateway-outcomes.json"
EXPECTED_ARGV="$FIX/expected-argv.json"
STUB="$MNEMON_ROOT/tests/fixtures/inbox/stub-add.py"
MATERIALIZE="$MNEMON_ROOT/tests/fixtures/inbox/materialize.py"

echo "=== Ingest Tests ==="

# Isolation: temp XDG config/state, instance `t` (the fixtures' instance), no inherited
# selectors, hooks or lock descriptors.
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-ingest-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg"
export XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_HOME MNEMON_TEST_CRASH_AT
unset MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE MNEMON_VAULT_STATE_DIR STUB_SCENARIOS STUB_SCENARIO
mkdir -p "$TEST_TMPDIR/cwd"
cd "$TEST_TMPDIR/cwd"
VAULT="$TEST_TMPDIR/vault"
"$MNEMON" init t --vault "$VAULT" --non-interactive >/dev/null 2>&1
THIS_HOST="$(hostname -s 2>/dev/null || hostname)"
THIS_HOST="${THIS_HOST%%.*}"

# D6: <XDG_STATE_HOME>/mnemon/vaults/<sha256(physical vault path)[:16]>
VSTATE="$(python3 -c 'import hashlib, os, sys; print(os.path.join(os.environ["XDG_STATE_HOME"], "mnemon", "vaults", hashlib.sha256(os.path.realpath(sys.argv[1]).encode("utf-8")).hexdigest()[:16]))' "$VAULT")"
LOCK="$VSTATE/lock"
export MNEMON_TEST_HOOKS=1
export MNEMON_INGEST_ADD_CMD="python3 '$STUB'"
export STUB_OUTCOMES="$OUTCOMES"
export STUB_LOCK_PATH="$LOCK"
PAST="2026-01-01T00:00:00+00:00"
OLD="$(python3 -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds"))')"
REV1="2026-10-15_aaaaaaaa"
REV2="2026-10-15_bbbbbbbb"

# --- helpers ---------------------------------------------------------------
run() { rc=0; out=$("$@" 2>&1) || rc=$?; }
inbox_of() { printf '%s' "$TEST_TMPDIR/inbox-$1"; }
# §5.2: <vault state>/ingest/<sha256(abs inbox path)[:8]>
state_dir() { python3 -c 'import hashlib, os, sys; print(os.path.join(sys.argv[1], "ingest", hashlib.sha256(os.path.realpath(sys.argv[2]).encode("utf-8")).hexdigest()[:8]))' "$VSTATE" "$(inbox_of "$1")"; }
# materialize <inbox-name> [materialize.py options and fixture names]
materialize() { local name="$1"; shift; python3 "$MATERIALIZE" "$ENVELOPES" "$(inbox_of "$name")" "$@" >/dev/null; }
# ingest <inbox-name> [ingest options]: through the dispatcher, stub records in stub-<name>
ingest() {
  local name="$1"; shift
  STUB_DIR="$TEST_TMPDIR/stub-$name" STUB_STATE_DIR="$(state_dir "$name")" run "$MNEMON" --instance t ingest "$(inbox_of "$name")" "$@"
}
# jq_ <json file> <python expression over d>; '<missing>' when the file is absent
jq_() {
  if [[ ! -f "$1" ]]; then printf '<missing>'; return 0; fi
  python3 -c 'import json, sys; d = json.load(open(sys.argv[1], encoding="utf-8")); print(eval(sys.argv[2]))' "$1" "$2"
}
res_file() { printf '%s/results/%s.json' "$(state_dir "$1")" "$2"; }
# res <inbox-name> <envelope stem> <expr>
res() { jq_ "$(res_file "$1" "$2")" "$3"; }
# calls <inbox-name>: how many times the stub ran for that inbox
calls() { ls "$TEST_TMPDIR/stub-$1/calls" 2>/dev/null | grep -c '\.json$' || true; }
# call <inbox-name> <seq> <expr>
call() { jq_ "$(printf '%s/stub-%s/calls/%04d.json' "$TEST_TMPDIR" "$1" "$2")" "$3"; }
# call_for <inbox-name> <envelope_id> <expr>: the last stub call for that envelope
call_for() {
  python3 - "$TEST_TMPDIR/stub-$1/calls" "$2" "$3" <<'PY'
import glob, json, os, sys
calls = sorted(glob.glob(os.path.join(sys.argv[1], "*.json")))
found = None
for path in calls:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if d["envelope_id"] == sys.argv[2]:
        found = d
if found is None:
    print("<no call>")
else:
    d = found
    print(eval(sys.argv[3]))
PY
}
# set_field <inbox-name> <stem> <key> <json value>: edit a result record (the tests' clock)
set_field() {
  python3 - "$(res_file "$1" "$2")" "$3" "$4" <<'PY'
import json, sys
path, key, value = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
with open(path, encoding="utf-8") as fh:
    d = json.load(fh)
d[key] = value
with open(path, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False, indent=1)
PY
}
# scenarios <inbox-name> '<json object: envelope_id -> scenario name | inline scenario>'
scenarios() { printf '%s' "$2" > "$TEST_TMPDIR/scenarios-$1.json"; export STUB_SCENARIOS="$TEST_TMPDIR/scenarios-$1.json"; }
future() { python3 -c 'import sys; from datetime import datetime, timezone; t = sys.argv[1]; t = t[:-1] + "+00:00" if t.endswith("Z") else t; print("future" if datetime.fromisoformat(t) > datetime.now(timezone.utc) else "past:" + t)' "$1"; }
# pyreport: lines "PASS <msg>" / "FAIL <msg> :: <detail>" from a python check → pass/fail
pyreport() {
  local line
  while IFS= read -r line; do
    case "$line" in
      PASS\ *) pass "${line#PASS }" ;;
      FAIL\ *) fail "${line#FAIL }" "" ;;
      *) echo "  ? $line" ;;
    esac
  done
}
tmp_left() { find "$1" -name '*.tmp' 2>/dev/null | wc -l | tr -d ' '; }
mode_of() { python3 -c 'import os, stat, sys; print("%04o" % stat.S_IMODE(os.stat(sys.argv[1]).st_mode))' "$1"; }

# --- 0. usage --------------------------------------------------------------
echo "--- usage ---"
run "$MNEMON" ingest
assert_eq "$rc" "2" "ingest without an inbox dir exits 2"
assert_contains "$out" "inbox-dir" "...naming the missing argument"
run "$MNEMON" --instance t ingest "$TEST_TMPDIR/no-such-dir"
assert_eq "$rc" "2" "ingest of a missing directory exits 2"
assert_contains "$out" "not a directory" "...and says so"
mkdir -p "$(inbox_of empty)"
run "$MNEMON" --instance t ingest "$(inbox_of empty)" --limit 0
assert_eq "$rc" "2" "--limit 0 is a usage error"
run "$MNEMON" --instance t ingest "$(inbox_of empty)" --only 'bad id!'
assert_eq "$rc" "2" "--only with an invalid envelope_id is a usage error"
run "$MNEMON" ingest "$(inbox_of empty)" --instance t
assert_eq "$rc" "2" "a selector after the command is refused"
assert_contains "$out" "--instance belongs before the command" "...with the dispatcher's hint"
run "$MNEMON" --instance t ingest "$(inbox_of empty)"
assert_eq "$rc" "0" "an empty inbox: exit 0"
assert_eq "$out" "ingest: processed 0 · captured 0 · failed 0 · needs_input 0 · waiting 0 · deferred 0 · done-before 0 · other-instance 0 · unsupported 0 · quarantined 0" "an empty inbox prints the summary line with zeros"
rm -rf "$VSTATE"

# --- 1. dry-run before any state exists ----------------------------------------
echo "--- dry-run ---"
materialize all
ingest all --dry-run
assert_eq "$rc" "0" "--dry-run exits 0"
[[ -e "$VSTATE" ]] && fail "--dry-run writes nothing" "vault state dir exists" || pass "--dry-run creates no vault state dir (no results dir, no lock)"
assert_contains "$out" "eligible test-producer:text-plain — mnemon add --origin text --capture-id test-producer:text-plain --import-source test-producer --import-ref test-producer:text-plain --producer-fidelity exact --provenance-file $(state_dir all)/payloads/test-producer:text-plain/provenance.json --context personal --title 'Plain T' --author 'A. Author' --intent why" "--dry-run prints the argv it would dispatch"
assert_contains "$out" "deferred test-producer:text-plain~2 — predecessor-pending" "--dry-run: ~2 is predecessor-pending while text-plain has no result"
assert_contains "$out" "needs_input test-producer:none-no-url" "--dry-run reports needs_input"
assert_contains "$out" "eligible 7 (dry-run)" "--dry-run counts the eligible items"
ingest all --dry-run --json
assert_eq "$rc" "0" "--dry-run --json exits 0"
assert_eq "$(printf '%s' "$out" | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d["dry_run"], d["counts"]["eligible"], d["schema"])')" "True 7 mnemon-ingest-pass/1" "--dry-run --json reports dry_run and the eligible count"
[[ -e "$VSTATE" ]] && fail "--dry-run --json writes nothing" "vault state dir exists" || pass "--dry-run --json writes nothing"

# --- 2. the first pass over every contract fixture ----------------------------------
echo "--- first pass: states, argv, stdin, environment ---"
ingest all
assert_eq "$rc" "0" "the pass exits 0"
assert_contains "$out" "ingest: processed 18 · captured 6 · failed 3 · needs_input 1 · waiting 0 · deferred 1 · done-before 0 · other-instance 1 · unsupported 1 · quarantined 5" "summary line of the first pass"
assert_contains "$out" "ERROR: context 'mc' is not declared in charter" "the gateway's stderr is forwarded"
assert_eq "$(calls all)" "8" "the stub ran once per dispatchable envelope (8)"
SD_ALL="$(state_dir all)"
python3 - "$OUTCOMES" "$ENVELOPES" "$SD_ALL" <<'PY' | pyreport
import json, os, sys
outcomes = json.load(open(sys.argv[1], encoding="utf-8"))
cases = {c["name"]: c for c in json.load(open(sys.argv[2], encoding="utf-8"))}
results = os.path.join(sys.argv[3], "results")
def load(stem):
    p = os.path.join(results, stem + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None
for name, expected in outcomes["expected_without_gateway"].items():
    stem = cases[name]["file"][:-5]
    rec = load(stem)
    if expected == "other-instance":
        print(("PASS" if rec is None else "FAIL") + " %s: other-instance leaves no result record" % name)
        continue
    state, _, cls = expected.partition("/")
    if rec is None:
        print("FAIL %s: no result record" % name); continue
    ok = rec["state"] == state and (not cls or rec.get("error_class") == cls) and rec["attempts"] == 0
    print(("PASS" if ok else "FAIL") + " %s settles as %s without a gateway call%s" % (name, expected, "" if ok else " :: got %s/%s attempts %s" % (rec["state"], rec.get("error_class"), rec["attempts"])))
for name, scen_name in outcomes["default_scenario_by_case"].items():
    scen = outcomes["scenarios"][scen_name]
    rec = load("test-producer:" + name)
    if rec is None:
        print("FAIL %s: no result record" % name); continue
    problems = []
    if rec["state"] != scen["state"]: problems.append("state %s" % rec["state"])
    if "duplicate" in scen and rec["duplicate"] != scen["duplicate"]: problems.append("duplicate %s" % rec["duplicate"])
    if "revision" in scen and rec["revision"] != scen["revision"]: problems.append("revision %s" % rec["revision"])
    if "error_class" in scen and rec["error_class"] != scen["error_class"]: problems.append("error_class %s" % rec["error_class"])
    if "retryable" in scen and rec["retryable"] != scen["retryable"]: problems.append("retryable %s" % rec["retryable"])
    if rec["attempts"] != (1 if scen["attempt_consumed"] else 0): problems.append("attempts %s" % rec["attempts"])
    if rec["last_exit"] != scen["exit"]: problems.append("last_exit %s" % rec["last_exit"])
    if scen.get("next_attempt_at") == "future":
        from datetime import datetime, timezone
        if not rec["next_attempt_at"] or datetime.fromisoformat(rec["next_attempt_at"]) <= datetime.now(timezone.utc):
            problems.append("next_attempt_at %s" % rec["next_attempt_at"])
    if scen["exit"] == 2 and "not declared in charter" not in (rec.get("last_error") or ""): problems.append("last_error lacks the stderr")
    print(("PASS" if not problems else "FAIL") + " %s → %s per §5.4%s" % (name, scen_name, "" if not problems else " :: " + ", ".join(problems)))
PY
# argv per envelope == expected-argv.json after placeholder substitution; stdin bytes identical
python3 - "$EXPECTED_ARGV" "$SD_ALL" "$TEST_TMPDIR/stub-all/calls" <<'PY' | pyreport
import glob, json, os, sys
expected = json.load(open(sys.argv[1], encoding="utf-8"))["cases"]
state_dir, calls_dir = sys.argv[2], sys.argv[3]
calls = {}
for path in sorted(glob.glob(os.path.join(calls_dir, "*.json"))):
    d = json.load(open(path, encoding="utf-8"))
    calls[d["envelope_id"]] = d
def revision_of(name):
    return json.load(open(os.path.join(state_dir, "results", "test-producer:" + name + ".json"), encoding="utf-8"))["revision"]
for name, spec in expected.items():
    eid = "test-producer:" + name
    want = []
    for a in spec["argv"]:
        if a == "<envelope_id>": a = eid
        elif a == "<provenance>": a = os.path.join(state_dir, "payloads", eid, "provenance.json")
        elif a.startswith("<snapshot:"): a = os.path.join(state_dir, "payloads", eid, *a[len("<snapshot:"):-1].split("/"))
        elif a.startswith("<revision:"): a = revision_of(a[len("<revision:"):-1])
        want.append(a)
    call = calls.get(eid)
    if call is None:
        print("FAIL %s: the stub was not called" % name); continue
    if call["argv"] != want:
        print("FAIL %s argv == expected-argv.json :: got %r" % (name, call["argv"])); continue
    print("PASS %s argv == expected-argv.json" % name)
    ok = call["stdin_sha256"] == spec["stdin_sha256"] and call["stdin_bytes"] == spec["stdin_bytes"]
    print(("PASS" if ok else "FAIL") + " %s stdin bytes (%s) :: got %s/%s" % (name, spec["stdin_bytes"], call["stdin_sha256"], call["stdin_bytes"]) if not ok else "PASS %s stdin: %d byte(s), sha as expected" % (name, spec["stdin_bytes"]))
    ok = call["lock_fd_valid"] and call["lock_held"] is True
    print(("PASS" if ok else "FAIL") + " %s: MNEMON_VAULT_LOCK_FD is a descriptor on the lock file and the lock is held" % name)
    print(("PASS" if call["dispatch_json_exists"] else "FAIL") + " %s: dispatch.json exists before the stub runs" % name)
    print(("PASS" if call["provenance_exists"] else "FAIL") + " %s: provenance.json exists before the stub runs" % name)
    print(("PASS" if call["env"]["MNEMON_INSTANCE"] == "t" and call["env"]["MNEMON_CONFIG"] else "FAIL") + " %s: the stub sees the resolved MNEMON_INSTANCE/MNEMON_CONFIG pair" % name)
# order: text-plain was dispatched before text-plain~2
print(("PASS" if calls["test-producer:text-plain"]["seq"] < calls["test-producer:text-plain~2"]["seq"] else "FAIL") + " text-plain is dispatched before text-plain~2 (numeric suffix order)")
print(("PASS" if "test-producer:text-plain~10" not in calls else "FAIL") + " text-plain~10 never reaches the gateway (predecessor ~9 absent)")
PY
# provenance.json shape (§5.3) with request_url
PROV="$SD_ALL/payloads/test-producer:file-pdf/provenance.json"
assert_eq "$(jq_ "$PROV" 'list(d.keys())')" "['schema', 'envelope_id', 'producer', 'instance', 'created_at', 'fidelity', 'request_url', 'request', 'provenance']" "provenance.json has exactly the mnemon-provenance/1 keys, in order"
assert_eq "$(jq_ "$PROV" 'd["schema"] + " " + d["request_url"] + " " + d["provenance"]["fixture"] + " " + d["producer"]["id"]')" "mnemon-provenance/1 https://example.com/paper.pdf file-pdf test-producer" "provenance.json carries request_url (the file payload's URL never reaches argv), the producer and the opaque provenance"
assert_eq "$(call_for all test-producer:file-pdf '"--url" in d["argv"]')" "False" "a file payload is dispatched with --file and never --url"
# dispatch.json (mnemon-dispatch/1) and the claim's dispatch_sha256
DISP="$SD_ALL/payloads/test-producer:text-plain/dispatch.json"
assert_eq "$(jq_ "$DISP" 'd["schema"] + " " + d["stdin"] + " " + d["env"]["MNEMON_INSTANCE"] + " " + str(d["argv"][:2])')" "mnemon-dispatch/1 stdin t ['--origin', 'text']" "dispatch.json: schema, stdin name, env and argv"
assert_eq "$(res all test-producer:text-plain 'd["dispatch_sha256"]')" "$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$DISP")" "the claim records sha256 of dispatch.json"
assert_eq "$(res all test-producer:text-plain 'd["dispatch_mode"] + " " + d["capture_id"] + " " + d["writer"] + " " + d["source_sha256"]')" "request test-producer:text-plain $THIS_HOST 35523eb5be09c122fd11c762197353b24bf36a9871495bbef659e6e81a6981e7" "a captured record: dispatch mode, capture_id, writer host, source_sha256 from RESULT"
assert_eq "$(res all test-producer:text-plain 'str(d["receipt_bound"])') $(res all test-producer:text-with-url 'str(d["receipt_bound"])') $(res all test-producer:context-undeclared 'str(d["receipt_bound"])')" "True True False" "receipt_bound after exit 0 (extracted, duplicate); not after exit 2 (A2)"
assert_eq "$(res all test-producer:text-with-url 'd["revision"] + " " + d["path"] + " " + d["source_sha256"]')" "2026-10-12_aaaaaaaa Sources/2026-10-12_aaaaaaaa/ 35523eb5be09c122fd11c762197353b24bf36a9871495bbef659e6e81a6981e7" "a duplicate records the existing folder's revision, path and source hash (A6)"
assert_eq "$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$SD_ALL/payloads/test-producer:text-plain/stdin")" "35523eb5be09c122fd11c762197353b24bf36a9871495bbef659e6e81a6981e7" "the text snapshot holds the exact UTF-8 bytes (CRLF and trailing newline kept)"
# ~2: --recapture --supersedes <revision of text-plain>; ~10 stays deferred
assert_eq "$(call_for all test-producer:text-plain~2 'd["argv"][-3:]')" "['--recapture', '--supersedes', '$REV1']" "~2 is dispatched with --recapture --supersedes <revision of text-plain>"
assert_eq "$(res all test-producer:text-plain~2 'd["revision"] + " " + str(d["supersedes_unresolved"])')" "$REV2 False" "~2 captured as the recapture revision, supersedes resolved"
assert_eq "$(res all test-producer:text-plain~10 'd["state"] + "/" + d["error_class"] + " attempts " + str(d["attempts"])')" "deferred/predecessor-pending attempts 0" "~10 is deferred/predecessor-pending, no attempt consumed"
assert_eq "$(future "$(res all test-producer:text-plain~10 'd["deadline_at"]')")" "future" "~10 carries a deadline_at in the future"
# exit 4 → failed with the revision and a future next_attempt_at
assert_eq "$(res all test-producer:file-origin-audio 'd["state"] + "/" + d["error_class"] + " " + str(d["retryable"]) + " " + d["revision"] + " " + d["path"]')" "failed/transient True $REV1 Sources/$REV1/" "exit 4 → failed with the published revision recorded"
assert_eq "$(future "$(res all test-producer:file-origin-audio 'd["next_attempt_at"]')")" "future" "exit 4 → next_attempt_at in the future"
assert_eq "$(python3 -c 'import sys; from datetime import datetime; a = datetime.fromisoformat(sys.argv[1]); b = datetime.fromisoformat(sys.argv[2]); print(int((a - b).total_seconds()))' "$(res all test-producer:file-origin-audio 'd["next_attempt_at"]')" "$(res all test-producer:file-origin-audio 'd["updated_at"]')")" "900" "first backoff = 15 min"
# quarantine copies, the inbox note, permissions, no temp files
for q in test-producer:broken-schema.json test-producer:conflict-copy.sync-conflict-20261015-120000-ABCDEFG.json test-producer:path-escape.json test-producer:sha-mismatch.json test-producer:incomplete.json; do
  assert_file_exists "$SD_ALL/quarantine/$q" "quarantine copy: $q"
done
assert_eq "$(jq_ "$SD_ALL/inbox.json" 'd["inbox"]')" "$(inbox_of all)" "inbox.json names the inbox dir for doctor"
assert_eq "$(mode_of "$VSTATE")" "0700" "the vault state dir is 0700"
assert_eq "$(mode_of "$SD_ALL/results")" "0700" "the results dir is 0700"
assert_eq "$(mode_of "$(res_file all test-producer:text-plain)")" "0600" "a result record is 0600"
assert_eq "$(tmp_left "$VSTATE")" "0" "no .tmp files are left under the vault state"
assert_file_exists "$LOCK" "the vault lock file exists after a pass"
FIRST_SEEN="$(res all test-producer:text-plain 'd["first_seen"]')"

# --- 3. second pass: a no-op ------------------------------------------------------
echo "--- second pass ---"
ingest all
assert_eq "$rc" "0" "second pass exits 0"
assert_contains "$out" "ingest: processed 18 · captured 0 · failed 0 · needs_input 0 · waiting 2 · deferred 0 · done-before 15 · other-instance 1 · unsupported 0 · quarantined 0" "second pass: done-before for every settled envelope, waiting for the two not yet due"
assert_contains "$out" "done-before test-producer:text-plain — captured" "done-before names the stored state"
assert_contains "$out" "waiting test-producer:file-origin-audio — failed/transient, next attempt at" "a retryable failure before next_attempt_at is reported as waiting"
assert_contains "$out" "waiting test-producer:text-plain~10 — deferred/predecessor-pending until" "a deferred envelope before deadline_at is reported as waiting"
assert_eq "$(calls all)" "8" "the second pass calls the stub for nothing"
assert_eq "$(res all test-producer:text-plain 'd["first_seen"]')" "$FIRST_SEEN" "first_seen is kept across passes"
ingest all --json
assert_eq "$rc" "0" "--json exits 0"
assert_eq "$(printf '%s' "$out" | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d["schema"], d["counts"]["done-before"], len(d["items"]), d["instance"], d["state_dir"] == sys.argv[1], d["exit"])' "$SD_ALL")" "mnemon-ingest-pass/1 15 18 t True 0" "--json: schema, counts, items, instance, state_dir, exit"
assert_eq "$(printf '%s' "$out" | head -c 1)" "{" "--json prints only the JSON document on stdout"

# --- 4. --retry eligibility (step 13) ------------------------------------------
echo "--- --retry eligibility ---"
for id in test-producer:context-undeclared test-producer:broken-schema test-producer:text-plain test-producer:none-no-url test-producer:bookmark test-producer:file-origin-audio test-producer:text-origin-url-invalid; do
  ingest all --retry "$id"
  assert_eq "$rc" "2" "--retry $id is refused (exit 2)"
  assert_contains "$out" "not retryable" "...with the reason"
done
ingest all --retry test-producer:never-seen
assert_eq "$rc" "2" "--retry of an unknown envelope is refused"
assert_eq "$(calls all)" "8" "refused retries dispatch nothing"
assert_eq "$(res all test-producer:context-undeclared 'd["retries"]')" "0" "a refused --retry leaves the record unchanged"

# --- 5. --only ---------------------------------------------------------------
echo "--- --only ---"
materialize only text-plain url-auto
ingest only --only test-producer:url-auto
assert_eq "$rc" "0" "--only exits 0"
assert_eq "$(calls only)" "1" "--only dispatches that envelope only"
assert_eq "$(call only 1 'd["envelope_id"]')" "test-producer:url-auto" "...the named one"
assert_eq "$(res only test-producer:text-plain 'd["state"]')" "<missing>" "--only leaves the other envelopes untouched (no record)"
assert_contains "$out" "ingest: processed 1 · captured 1" "--only counts one processed envelope"
ingest only --only test-producer:nope
assert_eq "$rc" "0" "--only with an unknown id exits 0"
assert_contains "$out" "no envelope 'test-producer:nope' in" "...with a note"

# --- 6. --limit counts eligible items -------------------------------------------
echo "--- --limit ---"
materialize limit text-plain url-auto
ingest limit --limit 1
assert_eq "$rc" "0" "--limit 1 exits 0"
assert_eq "$(calls limit)" "1" "--limit 1 claims one envelope"
assert_eq "$(call limit 1 'd["envelope_id"]')" "test-producer:text-plain" "...the first eligible in order"
assert_contains "$out" "waiting test-producer:url-auto — eligible, beyond --limit 1" "the next eligible one is reported as waiting"
assert_eq "$(res limit test-producer:url-auto 'd["state"]')" "<missing>" "an envelope beyond the limit is not claimed (no record)"
ingest limit --limit 1
assert_eq "$(calls limit)" "2" "the next pass with --limit 1 claims the next one"
assert_eq "$(call limit 2 'd["envelope_id"]')" "test-producer:url-auto" "...url-auto"
materialize limit2 text-plain url-auto
ingest limit2 --only test-producer:text-plain
ingest limit2 --limit 1
assert_eq "$(calls limit2)" "2" "--limit 1 with one captured item and one eligible item processes the eligible one"
assert_eq "$(call limit2 2 'd["envelope_id"]')" "test-producer:url-auto" "...the eligible one, not the captured one"
assert_contains "$out" "done-before test-producer:text-plain" "...the captured one is done-before, not counted against the limit"

# --- 7. a held vault lock → exit 75; --dry-run never locks ------------------------------
echo "--- vault lock ---"
python3 - "$LOCK" <<'PY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
time.sleep(30)
PY
HOLDER=$!
sleep 0.5
materialize locked text-plain
ingest locked
assert_eq "$rc" "75" "a vault lock held by another process → exit 75"
assert_contains "$out" "another writer holds the vault lock" "...with the message"
assert_eq "$(calls locked)" "0" "...and nothing is dispatched"
assert_eq "$(res locked test-producer:text-plain 'd["state"]')" "<missing>" "...and nothing is recorded"
ingest locked --dry-run
assert_eq "$rc" "0" "--dry-run works while the lock is held (it never locks)"
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
ingest locked
assert_eq "$rc" "0" "once released, the pass runs"
assert_eq "$(res locked test-producer:text-plain 'd["state"]')" "captured" "...and captures"
run "$MNEMON" --instance t doctor
assert_contains "$out" "ok vault-lock — free ($LOCK)" "doctor: the vault lock is free afterwards"

# --- 7b. the §12 A1 proof: this vault's lock file, re-asserted; another vault's descriptor is none ---
echo "--- vault lock: the A1 proof ---"
materialize a1 text-plain
python3 - "$LOCK" <<'PY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
time.sleep(30)
PY
HOLDER=$!
sleep 0.5
# a descriptor open on the lock file that holds nothing, while another process holds the lock
STUB_DIR="$TEST_TMPDIR/stub-a1" run python3 - "$LOCK" "$MNEMON" "$(inbox_of a1)" <<'PY'
import os, subprocess, sys
fd = os.open(sys.argv[1], os.O_RDWR)
os.set_inheritable(fd, True)
env = dict(os.environ, MNEMON_VAULT_LOCK_FD=str(fd), MNEMON_VAULT_LOCK_INODE=str(os.fstat(fd).st_ino))
sys.exit(subprocess.run([sys.argv[2], "--instance", "t", "ingest", sys.argv[3]], env=env, pass_fds=(fd,)).returncode)
PY
assert_eq "$rc" "75" "a descriptor on the lock file that holds nothing, lock held elsewhere: the re-asserted flock decides → 75"
assert_contains "$out" "another writer holds the vault lock" "...with the message"
assert_eq "$(calls a1)" "0" "...nothing dispatched"
# a descriptor for another vault's lock: acquire this vault's lock afresh
"$MNEMON" init v2 --vault "$TEST_TMPDIR/vault-v2" --non-interactive >/dev/null 2>&1
STUB_DIR="$TEST_TMPDIR/stub-a1" run python3 "$MNEMON_ROOT/bin/vault-lock.py" --vault "$TEST_TMPDIR/vault-v2" -- "$MNEMON" --instance t ingest "$(inbox_of a1)"
assert_eq "$rc" "75" "an ingest started under another vault's lock descriptor acquires this vault's lock itself: 75 while it is held"
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
STUB_DIR="$TEST_TMPDIR/stub-a1" run python3 "$MNEMON_ROOT/bin/vault-lock.py" --vault "$TEST_TMPDIR/vault-v2" -- "$MNEMON" --instance t ingest "$(inbox_of a1)"
assert_eq "$rc" "0" "...and runs once it is free"
assert_eq "$(res a1 test-producer:text-plain 'd["state"]')" "captured" "...capturing under its own lock"
assert_eq "$(call a1 1 'd["lock_held"]')" "True" "...which the stub saw held"

# --- 8. crash injection and recovery (MNEMON_TEST_CRASH_AT) ---------------------------
echo "--- crash injection ---"
materialize crash1 text-plain
MNEMON_TEST_CRASH_AT=after-dispatch-saved ingest crash1
assert_eq "$rc" "99" "crash at after-dispatch-saved kills the pass"
assert_file_exists "$(state_dir crash1)/payloads/test-producer:text-plain/dispatch.json" "after-dispatch-saved: dispatch.json was persisted"
assert_eq "$(res crash1 test-producer:text-plain 'd["state"]')" "<missing>" "after-dispatch-saved: no claim was written"
assert_eq "$(calls crash1)" "0" "after-dispatch-saved: the stub never ran"
ingest crash1
assert_eq "$(res crash1 test-producer:text-plain 'd["state"] + " " + str(d["attempts"])')" "captured 1" "the next pass claims (attempt 1) and captures"
assert_eq "$(call crash1 1 'd["argv"]')" "$(jq_ "$(state_dir crash1)/payloads/test-producer:text-plain/dispatch.json" 'd["argv"]')" "...dispatching exactly the saved request"

materialize crash2 url-auto
MNEMON_TEST_CRASH_AT=after-claim ingest crash2
assert_eq "$rc" "99" "crash at after-claim kills the pass"
assert_eq "$(res crash2 test-producer:url-auto 'd["state"] + " " + str(d["attempts"])')" "started 1" "after-claim: the record is started with attempts 1"
assert_eq "$(calls crash2)" "0" "after-claim: the stub never ran"
ingest crash2
assert_eq "$(res crash2 test-producer:url-auto 'd["state"] + " " + str(d["attempts"]) + " " + d["dispatch_mode"]')" "captured 2 request" "recovery of a started result: claimed again (attempts 2), the saved request dispatched"
assert_eq "$(call crash2 1 'd["argv"]')" "$(jq_ "$(state_dir crash2)/payloads/test-producer:url-auto/dispatch.json" 'd["argv"]')" "...with exactly the argv of dispatch.json"
assert_contains "$out" "captured test-producer:url-auto" "...and reports it captured"

materialize crash3 text-plain
MNEMON_TEST_CRASH_AT=after-claim ingest crash3
mkdir -p "$VSTATE/captures"
printf '{"schema":"mnemon-capture-receipt/1","capture_id":"test-producer:text-plain","state":"source-published","revision":"%s"}\n' "$REV1" > "$VSTATE/captures/test-producer:text-plain.json"
ingest crash3
assert_eq "$(call crash3 1 'd["argv"]')" "$(jq_ "$(state_dir crash3)/payloads/test-producer:text-plain/dispatch.json" 'd["argv"]')" "A2: a started result is recovered with the full saved request even when the ledger already holds a receipt for the id (never a silent acknowledgement)"
assert_eq "$(call crash3 1 'd["stdin_bytes"]')" "14" "...with the snapshot on stdin"
assert_eq "$(res crash3 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + " " + d["dispatch_mode"] + " bound=" + str(d["receipt_bound"])')" "captured 2 request bound=True" "...attempts 2, dispatch mode request; exit 0 with RESULT:capture_id=<envelope_id> binds the record"
rm -rf "$VSTATE/captures"
# A2: once a dispatch of the full request came back exit 0 or 4 with RESULT:capture_id=<envelope_id>
# (receipt_bound), later dispatches replay by id alone — retries and crash recoveries alike
materialize crash5 text-plain
scenarios crash5 '{"test-producer:text-plain": "exit4-transient"}'
ingest crash5
assert_eq "$(res crash5 test-producer:text-plain 'd["state"] + "/" + d["error_class"] + " bound=" + str(d["receipt_bound"]) + " " + d["dispatch_mode"]')" "failed/transient bound=True request" "exit 4 with RESULT:capture_id=<envelope_id> binds the record (the dispatch was the full request)"
set_field crash5 test-producer:text-plain next_attempt_at "\"$PAST\""
scenarios crash5 '{"test-producer:text-plain": "extracted"}'
ingest crash5
assert_eq "$(call crash5 2 'd["argv"]')" "['--capture-id', 'test-producer:text-plain']" "a bound record is retried by id alone"
assert_eq "$(call crash5 2 'd["stdin_bytes"]')" "0" "...and no stdin"
assert_eq "$(res crash5 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + " " + d["dispatch_mode"]')" "captured 2 replay" "...attempts 2, dispatch mode replay"
materialize crash6 text-plain
scenarios crash6 '{"test-producer:text-plain": "exit4-transient"}'
ingest crash6
set_field crash6 test-producer:text-plain next_attempt_at "\"$PAST\""
MNEMON_TEST_CRASH_AT=after-claim ingest crash6
assert_eq "$(res crash6 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + " bound=" + str(d["receipt_bound"])')" "started 2 bound=True" "fixture: a bound record claimed again, then a crash"
scenarios crash6 '{"test-producer:text-plain": "extracted"}'
ingest crash6
assert_eq "$(call crash6 2 'd["argv"]')" "['--capture-id', 'test-producer:text-plain']" "a started record that was bound before its crash is recovered by id"
assert_eq "$(res crash6 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + " " + d["dispatch_mode"]')" "captured 3 replay" "...attempts 3, dispatch mode replay"
unset STUB_SCENARIOS
materialize crash7 text-plain
scenarios crash7 '{"test-producer:text-plain": "exit1-fetch"}'
ingest crash7
assert_eq "$(res crash7 test-producer:text-plain 'd["state"] + "/" + d["error_class"] + " bound=" + str(d["receipt_bound"])')" "failed/fetch bound=False" "exit 1 never binds the record"
set_field crash7 test-producer:text-plain next_attempt_at "\"$PAST\""
scenarios crash7 '{"test-producer:text-plain": "extracted"}'
ingest crash7
assert_eq "$(call crash7 2 'd["argv"][:2]')" "['--origin', 'text']" "...so its retry sends the full saved request again"
assert_eq "$(res crash7 test-producer:text-plain 'd["state"] + " " + d["dispatch_mode"] + " bound=" + str(d["receipt_bound"])')" "captured request bound=True" "...which then binds it"
unset STUB_SCENARIOS

materialize crash4 file-pdf
MNEMON_TEST_CRASH_AT=after-snapshot ingest crash4
assert_eq "$rc" "99" "crash at after-snapshot kills the pass"
SNAP="$(state_dir crash4)/payloads/test-producer:file-pdf/attachments/file-pdf/paper.pdf"
assert_file_exists "$SNAP" "after-snapshot: the payload snapshot exists"
assert_file_exists "$(state_dir crash4)/payloads/test-producer:file-pdf/provenance.json" "after-snapshot: provenance.json exists"
[[ -e "$(state_dir crash4)/payloads/test-producer:file-pdf/dispatch.json" ]] && fail "after-snapshot: no dispatch.json yet" "exists" || pass "after-snapshot: no dispatch.json yet"
printf 'REPLACED AFTER THE SNAPSHOT' > "$(inbox_of crash4)/attachments/file-pdf/paper.pdf"
ingest crash4
assert_eq "$(res crash4 test-producer:file-pdf 'd["state"]')" "captured" "a payload replaced after the snapshot: the capture proceeds from the snapshot"
assert_eq "$(call crash4 1 'd["file_sha256"]')" "dcd498d39e609ba89482d59eaf55f3263bad1d6ba6f0b1739bf9a2806ffaa448" "...the stub receives the snapshot bytes, not the replaced file"
assert_eq "$(call crash4 1 'd["argv"][1]')" "$SNAP" "...through --file <snapshot path>"

# --- 9. the outcome matrix (§5.4) ------------------------------------------------
echo "--- outcome matrix ---"
materialize matrix --clone text-plain=test-producer:m-exit0-malformed --clone text-plain=test-producer:m-exit4-permanent \
  --clone text-plain=test-producer:m-exit4-malformed --clone text-plain=test-producer:m-exit1-fetch \
  --clone text-plain=test-producer:m-exit1-unsupported --clone text-plain=test-producer:m-exit1-archive-exists \
  --clone text-plain=test-producer:m-exit1-integrity --clone text-plain=test-producer:m-exit3-environment \
  --clone text-plain=test-producer:m-exit75-locked --clone text-plain=test-producer:m-other-exit
scenarios matrix '{"test-producer:m-exit0-malformed": "exit0-malformed", "test-producer:m-exit4-permanent": "exit4-permanent",
  "test-producer:m-exit4-malformed": "exit4-malformed", "test-producer:m-exit1-fetch": "exit1-fetch",
  "test-producer:m-exit1-unsupported": "exit1-unsupported", "test-producer:m-exit1-archive-exists": "exit1-archive-exists",
  "test-producer:m-exit1-integrity": "exit1-integrity", "test-producer:m-exit3-environment": "exit3-environment",
  "test-producer:m-exit75-locked": "exit75-locked", "test-producer:m-other-exit": "other-exit"}'
ingest matrix
assert_eq "$rc" "0" "the matrix pass exits 0"
assert_eq "$(calls matrix)" "10" "every matrix envelope reached the stub"
mx() { res matrix "test-producer:$1" 'd["state"] + "/" + str(d["error_class"]) + " retryable=" + str(d["retryable"]) + " attempts=" + str(d["attempts"]) + " revision=" + str(d["revision"])'; }
assert_eq "$(mx m-exit0-malformed)" "failed/malformed-result retryable=True attempts=1 revision=None" "exit 0 without RESULT:status → failed/malformed-result, retryable"
assert_eq "$(mx m-exit4-permanent)" "failed/invalid-output retryable=False attempts=1 revision=$REV1" "exit 4 retryable=false → permanent failed with the revision"
assert_eq "$(mx m-exit4-malformed)" "failed/malformed-result retryable=True attempts=1 revision=None" "exit 4 without a RESULT block → failed/malformed-result, retryable"
assert_eq "$(mx m-exit1-fetch)" "failed/fetch retryable=True attempts=1 revision=None" "exit 1 fetch → failed/fetch, retryable"
assert_eq "$(mx m-exit1-unsupported)" "failed/unsupported retryable=False attempts=1 revision=None" "exit 1 unsupported → permanent"
assert_eq "$(mx m-exit1-archive-exists)" "failed/archive-exists retryable=False attempts=1 revision=None" "exit 1 archive-exists → permanent"
assert_eq "$(mx m-exit1-integrity)" "failed/integrity-error retryable=False attempts=1 revision=$REV1" "exit 1 integrity-error → permanent, revision recorded"
assert_eq "$(mx m-exit3-environment)" "deferred/environment retryable=True attempts=0 revision=None" "exit 3 → deferred/environment, attempt refunded"
assert_eq "$(mx m-exit75-locked)" "deferred/locked retryable=True attempts=0 revision=None" "exit 75 → deferred/locked, attempt refunded"
assert_eq "$(mx m-other-exit)" "failed/unknown-exit retryable=True attempts=1 revision=None" "an unknown exit → failed/unknown-exit, retryable once"
bound() { res matrix "test-producer:$1" 'str(d["receipt_bound"])'; }
assert_eq "$(bound m-exit4-permanent) $(bound m-exit4-malformed) $(bound m-exit0-malformed) $(bound m-exit1-fetch) $(bound m-exit1-integrity) $(bound m-exit3-environment) $(bound m-exit75-locked) $(bound m-other-exit)" "True False False False False False False False" "A2: only exit 0 or 4 with RESULT:capture_id=<envelope_id> binds the record"
assert_eq "$(future "$(res matrix test-producer:m-exit3-environment 'd["deadline_at"]')")" "future" "exit 3 → deadline_at in the future"
assert_eq "$(future "$(res matrix test-producer:m-exit75-locked 'd["deadline_at"]')")" "future" "exit 75 → deadline_at in the future"
assert_eq "$(res matrix test-producer:m-exit4-permanent 'd["next_attempt_at"]')" "None" "a permanent failure schedules no next attempt"
ingest matrix
assert_eq "$(calls matrix)" "10" "a pass before the deadlines dispatches nothing"
assert_contains "$out" "waiting test-producer:m-exit3-environment — deferred/environment until" "deferred/environment waits for its deadline"
assert_contains "$out" "waiting test-producer:m-exit75-locked — deferred/locked until" "deferred/locked waits for its deadline"
assert_contains "$out" "done-before test-producer:m-exit4-permanent — failed/invalid-output" "a permanent failure is done-before"
assert_contains "$out" "done-before test-producer:m-exit1-integrity — failed/integrity-error" "an integrity error is done-before"
# deadlines honoured: once past, deferred envelopes are claimed again; the fetch retry backs off 30 min; unknown-exit twice → permanent
set_field matrix test-producer:m-exit3-environment deadline_at "\"$PAST\""
set_field matrix test-producer:m-exit75-locked deadline_at "\"$PAST\""
set_field matrix test-producer:m-exit1-fetch next_attempt_at "\"$PAST\""
set_field matrix test-producer:m-other-exit next_attempt_at "\"$PAST\""
scenarios matrix '{"test-producer:m-exit3-environment": "extracted", "test-producer:m-exit75-locked": "duplicate",
  "test-producer:m-exit1-fetch": "exit1-fetch", "test-producer:m-other-exit": "other-exit"}'
ingest matrix
assert_eq "$(calls matrix)" "14" "once the deadlines passed, the four due envelopes are dispatched"
assert_eq "$(mx m-exit3-environment)" "captured/None retryable=None attempts=1 revision=$REV1" "deferred/environment past its deadline: claimed (attempt 1) and captured"
assert_eq "$(res matrix test-producer:m-exit75-locked 'd["state"] + " " + str(d["duplicate"]) + " " + str(d["attempts"]) + " " + d["revision"]')" "captured True 1 2026-10-12_aaaaaaaa" "deferred/locked past its deadline: captured as a duplicate"
assert_eq "$(mx m-exit1-fetch)" "failed/fetch retryable=True attempts=2 revision=None" "a second fetch failure: attempts 2, still retryable"
assert_eq "$(python3 -c 'import sys; from datetime import datetime; print(int((datetime.fromisoformat(sys.argv[1]) - datetime.fromisoformat(sys.argv[2])).total_seconds()))' "$(res matrix test-producer:m-exit1-fetch 'd["next_attempt_at"]')" "$(res matrix test-producer:m-exit1-fetch 'd["updated_at"]')")" "1800" "second backoff = 30 min (15 min × 2^(attempts-1))"
assert_eq "$(mx m-other-exit)" "failed/unknown-exit retryable=False attempts=2 revision=None" "a second unknown exit is permanent"
unset STUB_SCENARIOS

# --- 10. exhaustion, the last allowed attempt, --retry ------------------------------------
echo "--- exhaustion and --retry ---"
materialize exh text-plain
scenarios exh '{"test-producer:text-plain": "exit4-transient"}'
ingest exh --max-attempts 1
assert_eq "$(res exh test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + "/" + str(d["attempts_allowed"]) + " " + d["error_class"] + " next=" + str(d["next_attempt_at"])')" "exhausted 1/1 transient next=None" "the last allowed attempt failing retryably → exhausted"
assert_contains "$out" "exhausted test-producer:text-plain" "...reported as exhausted"
assert_contains "$out" "failed 1" "...and counted under failed"
ingest exh --max-attempts 1
assert_contains "$out" "done-before test-producer:text-plain — exhausted" "exhausted is terminal"
ingest exh --max-attempts 1 --retry test-producer:text-plain
assert_eq "$rc" "0" "--retry of an exhausted envelope is accepted"
assert_contains "$out" "--retry test-producer:text-plain: reopened (retries 1" "...and says so"
assert_eq "$(res exh test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + "/" + str(d["attempts_allowed"]) + " retries=" + str(d["retries"]) + " " + str(bool(d["retry_requested_at"]))')" "exhausted 2/2 retries=1 True" "--retry raises attempts_allowed (max × (1 + retries)), keeps lifetime attempts, dispatches in the same pass; still failing → exhausted again"
assert_eq "$(calls exh)" "2" "...one dispatch per allowed attempt"
scenarios exh '{"test-producer:text-plain": "extracted"}'
ingest exh --max-attempts 1 --retry test-producer:text-plain
assert_eq "$(res exh test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + "/" + str(d["attempts_allowed"]) + " retries=" + str(d["retries"]) + " " + d["revision"]')" "captured 3/3 retries=2 $REV1" "a second --retry with a working gateway → captured"
materialize exh2 text-plain
scenarios exh2 '{"test-producer:text-plain": "exit4-transient"}'
ingest exh2 --max-attempts 2
assert_eq "$(res exh2 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + "/" + str(d["attempts_allowed"])')" "failed 1/2" "with two attempts allowed the first failure is failed, not exhausted"
set_field exh2 test-producer:text-plain next_attempt_at "\"$PAST\""
scenarios exh2 '{"test-producer:text-plain": "extracted"}'
ingest exh2 --max-attempts 2
assert_eq "$(res exh2 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + "/" + str(d["attempts_allowed"])')" "captured 2/2" "the last allowed attempt succeeding → captured"
materialize exh3 text-plain
scenarios exh3 '{"test-producer:text-plain": "exit4-transient"}'
ingest exh3 --max-attempts 2
set_field exh3 test-producer:text-plain next_attempt_at "\"$PAST\""
ingest exh3 --max-attempts 2
assert_eq "$(res exh3 test-producer:text-plain 'd["state"] + " " + str(d["attempts"]) + "/" + str(d["attempts_allowed"])')" "exhausted 2/2" "the last allowed attempt failing → exhausted"
# a permanent fetch failure (retryable=false from the gateway) can be reopened; contract cannot
materialize retryp --clone text-plain=test-producer:m-fetch-perm --clone context-undeclared=test-producer:m-contract
scenarios retryp '{"test-producer:m-fetch-perm": {"exit": 1, "stdout": ["RESULT:capture_id=<envelope_id>", "RESULT:revision=", "RESULT:path=", "RESULT:status=failed", "RESULT:title=", "RESULT:source_sha256=", "RESULT:extract=none", "RESULT:error_class=fetch", "RESULT:retryable=false"]}, "test-producer:m-contract": "exit2-contract"}'
ingest retryp
assert_eq "$(res retryp test-producer:m-fetch-perm 'd["state"] + "/" + d["error_class"] + " " + str(d["retryable"])')" "failed/fetch False" "a fetch failure reported retryable=false is permanent"
assert_eq "$(res retryp test-producer:m-contract 'd["state"] + "/" + d["error_class"] + " " + str(d["retryable"])')" "failed/contract False" "exit 2 → failed/contract, permanent"
ingest retryp --retry test-producer:m-contract
assert_eq "$rc" "2" "--retry refuses a contract failure"
scenarios retryp '{"test-producer:m-fetch-perm": "extracted"}'
ingest retryp --retry test-producer:m-fetch-perm
assert_eq "$rc" "0" "--retry reopens a permanent fetch failure"
assert_eq "$(res retryp test-producer:m-fetch-perm 'd["state"] + " " + str(d["attempts"]) + " retries=" + str(d["retries"])')" "captured 2 retries=1" "...and the same pass captures it"
unset STUB_SCENARIOS

# --- 11. a replaced envelope in every stored state → quarantined/envelope-replaced ------------
echo "--- envelope replaced under the same id ---"
materialize repl text-plain context-undeclared none-no-url bookmark broken-schema \
  --clone text-plain=test-producer:m-transient --clone text-plain=test-producer:m-env --clone text-plain=test-producer:m-exh
materialize repl --no-payloads file-pdf
scenarios repl '{"test-producer:m-transient": "exit4-transient", "test-producer:m-env": "exit3-environment", "test-producer:m-exh": "exit4-transient", "test-producer:m-started": "extracted"}'
ingest repl --only test-producer:m-exh --max-attempts 1
ingest repl
materialize repl --clone text-plain=test-producer:m-started
MNEMON_TEST_CRASH_AT=after-claim ingest repl --only test-producer:m-started
before="$(calls repl)"
python3 - "$(state_dir repl)/results" <<'PY' | pyreport
import glob, json, os, sys
want = {"test-producer:text-plain": "captured", "test-producer:m-transient": "failed", "test-producer:context-undeclared": "failed",
        "test-producer:none-no-url": "needs_input", "test-producer:bookmark": "unsupported", "test-producer:broken-schema": "quarantined",
        "test-producer:m-env": "deferred", "test-producer:m-exh": "exhausted", "test-producer:file-pdf": "incomplete", "test-producer:m-started": "started"}
for stem, state in want.items():
    d = json.load(open(os.path.join(sys.argv[1], stem + ".json"), encoding="utf-8"))
    print(("PASS" if d["state"] == state else "FAIL") + " fixture: %s is %s :: got %s" % (stem, state, d["state"]))
PY
python3 - "$(inbox_of repl)" <<'PY'
import glob, json, os, sys
for path in glob.glob(os.path.join(sys.argv[1], "*.json")):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    d["provenance"]["touched"] = True
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
PY
ingest repl
assert_eq "$rc" "0" "the pass over replaced envelopes exits 0"
assert_contains "$out" "quarantined 10" "every replaced envelope is quarantined"
assert_eq "$(calls repl)" "$before" "replaced envelopes never reach the gateway"
python3 - "$(state_dir repl)/results" <<'PY' | pyreport
import glob, json, os, sys
want = {"test-producer:text-plain": "captured", "test-producer:m-transient": "failed", "test-producer:context-undeclared": "failed",
        "test-producer:none-no-url": "needs_input", "test-producer:bookmark": "unsupported", "test-producer:broken-schema": "quarantined",
        "test-producer:m-env": "deferred", "test-producer:m-exh": "exhausted", "test-producer:file-pdf": "incomplete", "test-producer:m-started": "started"}
for stem, prev_state in want.items():
    d = json.load(open(os.path.join(sys.argv[1], stem + ".json"), encoding="utf-8"))
    ok = d["state"] == "quarantined" and d["error_class"] == "envelope-replaced" and d["history"] and d["history"][-1]["state"] == prev_state and "history" not in d["history"][-1]
    print(("PASS" if ok else "FAIL") + " replaced while %s → quarantined/envelope-replaced with the previous record under history :: got %s/%s history %s" % (prev_state, d["state"], d["error_class"], [h.get("state") for h in d["history"]]))
PY
ingest repl
assert_contains "$out" "done-before 10" "replacement is terminal"
unset STUB_SCENARIOS

# --- 12. incomplete payloads ------------------------------------------------------
echo "--- incomplete ---"
materialize inc --no-payloads file-pdf
ingest inc
assert_eq "$(res inc test-producer:file-pdf 'd["state"] + "/" + d["error_class"] + " attempts=" + str(d["attempts"])')" "incomplete/payload-missing attempts=0" "a missing payload file → incomplete, no attempt spent"
assert_eq "$(future "$(res inc test-producer:file-pdf 'd["deadline_at"]')")" "future" "incomplete carries a re-check deadline"
assert_contains "$out" "deferred 1" "incomplete is counted with deferred"
assert_eq "$(calls inc)" "0" "incomplete: nothing dispatched"
ingest inc
assert_contains "$out" "waiting test-producer:file-pdf — incomplete/payload-missing until" "before the deadline: waiting"
materialize inc file-pdf
set_field inc test-producer:file-pdf deadline_at "\"$PAST\""
ingest inc
assert_eq "$(res inc test-producer:file-pdf 'd["state"] + " " + str(d["attempts"])')" "captured 1" "once the payload arrives (and the deadline passed): captured"
assert_eq "$(call inc 1 'd["file_sha256"]')" "dcd498d39e609ba89482d59eaf55f3263bad1d6ba6f0b1739bf9a2806ffaa448" "...from a verified snapshot"
materialize inc2 --no-payloads file-pdf
ingest inc2
set_field inc2 test-producer:file-pdf deadline_at "\"$PAST\""
set_field inc2 test-producer:file-pdf first_seen "\"$OLD\""
ingest inc2
assert_eq "$(res inc2 test-producer:file-pdf 'd["state"] + "/" + d["error_class"]')" "quarantined/incomplete-expired" "still missing 24 h after first_seen → quarantined/incomplete-expired"
materialize inc3 --no-payloads file-pdf
mkdir -p "$(inbox_of inc3)/attachments/file-pdf"
printf 'half-synced' > "$(inbox_of inc3)/attachments/file-pdf/paper.pdf"
ingest inc3
assert_eq "$(res inc3 test-producer:file-pdf 'd["state"] + "/" + d["error_class"]')" "incomplete/payload-missing" "a payload whose bytes do not match yet → incomplete"
[[ -e "$(state_dir inc3)/payloads/test-producer:file-pdf/attachments/file-pdf/paper.pdf" ]] && fail "a mismatching copy is not kept as the snapshot" "exists" || pass "a mismatching copy is not kept as the snapshot"
materialize inc3 file-pdf
set_field inc3 test-producer:file-pdf deadline_at "\"$PAST\""
ingest inc3
assert_eq "$(res inc3 test-producer:file-pdf 'd["state"]')" "captured" "...captured once the bytes match"

# --- 13. supersedes: unknown predecessor expires; unresolved predecessor -------------------
echo "--- supersedes ---"
set_field all test-producer:text-plain~10 deadline_at "\"$PAST\""
set_field all test-producer:text-plain~10 first_seen "\"$OLD\""
ingest all --only test-producer:text-plain~10
assert_eq "$(res all test-producer:text-plain~10 'd["state"] + "/" + d["error_class"]')" "quarantined/supersedes-unknown" "predecessor-pending 24 h after first_seen → quarantined/supersedes-unknown"
materialize sup text-plain text-plain~2
scenarios sup '{"test-producer:text-plain": "exit1-unsupported"}'
ingest sup
assert_eq "$(res sup test-producer:text-plain 'd["state"] + "/" + d["error_class"] + " " + str(d["revision"])')" "failed/unsupported None" "fixture: the predecessor failed permanently without a revision"
assert_eq "$(res sup test-producer:text-plain~2 'd["state"] + " unresolved=" + str(d["supersedes_unresolved"])')" "captured unresolved=True" "a terminal predecessor without a revision → captured without supersedes, supersedes_unresolved: true"
assert_eq "$(call_for sup test-producer:text-plain~2 '"--supersedes" in d["argv"] or "--recapture" in d["argv"]')" "False" "...and the argv carries no --recapture/--supersedes"
unset STUB_SCENARIOS
materialize sup2 text-plain text-plain~2
scenarios sup2 '{"test-producer:text-plain": "exit4-transient"}'
ingest sup2
assert_eq "$(res sup2 test-producer:text-plain~2 'd["state"] + " " + str(d["supersedes_unresolved"])')" "captured False" "a failed predecessor with a published revision resolves to that revision"
assert_eq "$(call_for sup2 test-producer:text-plain~2 'd["argv"][-2:]')" "['--supersedes', '$REV1']" "...--supersedes <that revision>"
unset STUB_SCENARIOS
materialize sup3 text-plain text-plain~2
scenarios sup3 '{"test-producer:text-plain": "exit1-fetch"}'
ingest sup3
assert_eq "$(res sup3 test-producer:text-plain~2 'd["state"] + "/" + d["error_class"]')" "deferred/predecessor-pending" "a retryable predecessor without a revision keeps ~2 pending"
unset STUB_SCENARIOS

# --- 14. listing rules: symlinks, dotfiles, temp files, directories, wrong instance -----------
echo "--- listing ---"
materialize list text-plain
ln -s "$(inbox_of list)/test-producer:text-plain.json" "$(inbox_of list)/test-producer:linked.json"
printf '{}' > "$(inbox_of list)/.test-producer:hidden.json"
printf '{}' > "$(inbox_of list)/.test-producer:partial.json.tmp"
printf '{}' > "$(inbox_of list)/test-producer:partial2.json.tmp"
mkdir -p "$(inbox_of list)/test-producer:dir.json"
printf 'not json' > "$(inbox_of list)/notes.txt"
ingest list
assert_eq "$rc" "0" "a mixed inbox exits 0"
assert_contains "$out" "ingest: processed 2 · captured 1" "dotfiles, .tmp files, directories and non-json files are skipped; the symlink is counted"
assert_eq "$(res list test-producer:linked 'd["state"] + "/" + d["error_class"]')" "quarantined/not-regular" "a symlinked envelope → quarantined/not-regular"
ingest list
assert_contains "$out" "done-before test-producer:linked — quarantined/not-regular" "...terminal"
assert_eq "$(res list test-producer:text-plain 'd["state"]')" "captured" "the regular envelope next to it is captured"
materialize wrong --instance other text-plain
ingest wrong
assert_contains "$out" "other-instance test-producer:text-plain" "an envelope for another instance is reported other-instance"
assert_eq "$(res wrong test-producer:text-plain 'd["state"]')" "<missing>" "...and left untouched (no record)"

# --- 15. the test hook is ignored without MNEMON_TEST_HOOKS=1 --------------------------------
echo "--- hook discipline ---"
materialize hook text-plain
ELOCK="$XDG_STATE_HOME/mnemon/engine-release.lock"
mkdir -p "$(dirname "$ELOCK")"
printf 'pid=99999\ntimestamp=%s\ndate=2026-01-01T00:00:00Z\ntarget=abc1234\n' "$(date +%s)" > "$ELOCK"
STUB_DIR="$TEST_TMPDIR/stub-hook" run env -u MNEMON_TEST_HOOKS MNEMON_INSTANCE=t python3 "$INGEST_PY" "$(inbox_of hook)"
assert_eq "$rc" "0" "without MNEMON_TEST_HOOKS the pass still runs"
assert_contains "$out" "ignoring MNEMON_INGEST_ADD_CMD — a test hook, honoured only with MNEMON_TEST_HOOKS=1" "...but the hook is ignored with a notice"
assert_eq "$(calls hook)" "0" "...the stub never ran"
assert_eq "$(res hook test-producer:text-plain 'd["state"] + "/" + d["error_class"] + " attempts=" + str(d["attempts"])')" "deferred/locked attempts=0" "...the real dispatcher was called (its engine lock answered 75 → deferred/locked, attempt refunded)"
set_field hook test-producer:text-plain deadline_at "\"$PAST\""
STUB_DIR="$TEST_TMPDIR/stub-hook" run env -u MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT=after-claim MNEMON_INSTANCE=t python3 "$INGEST_PY" "$(inbox_of hook)"
rm -f "$ELOCK"
assert_eq "$rc" "0" "MNEMON_TEST_CRASH_AT without MNEMON_TEST_HOOKS never crashes"
assert_eq "$(res hook test-producer:text-plain 'd["state"] + "/" + d["error_class"]')" "deferred/locked" "...the pass ran through the claim to the dispatcher again"

# --- 16. writer_host ---------------------------------------------------------------
echo "--- writer host ---"
"$MNEMON" init w --vault "$TEST_TMPDIR/vault-w" --writer-host not-this-host --non-interactive >/dev/null 2>&1
mkdir -p "$(inbox_of w)"
run "$MNEMON" --instance w ingest "$(inbox_of w)"
assert_eq "$rc" "2" "a non-writer host must not run ingest (exit 2)"
assert_contains "$out" "must not write vault" "...with the reason"
run "$MNEMON" --instance w ingest "$(inbox_of w)" --dry-run
assert_eq "$rc" "0" "--dry-run is allowed on a non-writer host (it writes nothing)"

# --- 17. doctor sees the ingest state -------------------------------------------------
echo "--- doctor ingest ---"
run "$MNEMON" --instance t doctor
assert_contains "$out" "warn ingest — " "doctor warns about pending ingest results"
assert_contains "$out" "$(inbox_of all): failed 3" "...per inbox, by state (the inbox named through inbox.json)"
assert_contains "$out" "$(inbox_of matrix): failed 8" "...every inbox with pending results is listed"
assert_not_contains "$out" "$(inbox_of repl)" "...an inbox whose results are all terminal is not"
assert_contains "$out" "ok vault-state — $VSTATE" "doctor: the vault state dir is ok (0700)"

cd /
rm -rf "$TEST_TMPDIR"
summary
