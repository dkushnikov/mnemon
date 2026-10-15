#!/usr/bin/env bash
# test_chain.sh — the real dispatch chain (docs/specs/2026-10-15-capture-contracts.md §9.14 and the
# §12 amendments A1, A2, A5, A6): a temp instance, the real dispatcher (bin/mnemon), the real
# consumer (bin/mnemon-ingest.py), bin/vault-lock.py, bin/knowledge-gateway.sh with
# bin/lib-capture.sh, bin/extract-run.py, bin/extract-publish.py and bin/knowledge-reindex.py.
# Only the edges are stubbed: `claude` (answers as 2.1.296, with a probe record for that version),
# and every tool that would reach the network or a converter (curl, render-url.sh, kb-depth-fold.py,
# pdftotext, pdfinfo, media-extract.py). Nothing between the engine's own parts is mocked.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MNEMON="$MNEMON_ROOT/bin/mnemon"
LOCK_PY="$MNEMON_ROOT/bin/vault-lock.py"
FIX="$MNEMON_ROOT/tests/fixtures"

echo "=== Real Dispatch Chain Tests ==="

# --- isolation: temp XDG config/state, instance `t` (the fixtures' instance), no inherited selectors,
#     hooks or lock descriptors --------------------------------------------------------------------
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-chain-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_HOME MNEMON_VAULT_LOCK_FD MNEMON_VAULT_LOCK_INODE
unset MNEMON_VAULT_STATE_DIR MNEMON_TEST_HOOKS MNEMON_TEST_CRASH_AT MNEMON_INGEST_ADD_CMD STUB_FAIL_ONCE
VAULT="$TEST_TMPDIR/vault"
STUBS="$TEST_TMPDIR/stubs"
STUB_CALLS="$TEST_TMPDIR/stub-calls"
INBOX="$TEST_TMPDIR/inbox"
mkdir -p "$STUBS" "$STUB_CALLS" "$XDG_STATE_HOME/mnemon"

# --- stubs ----------------------------------------------------------------------------------------
cat > "$STUBS/claude" <<'STUB'
#!/usr/bin/env bash
# Stub Claude Code 2.1.296: --version / --help answer like the real binary (the help lists
# --restricted); a run reads the prompt from stdin and prints a --output-format json document whose
# structured_output is $STUB_FIXTURE, with `body` rebuilt from the `### ` headings of the prompt's
# template Body section (the real publisher requires every heading of the source type's template).
# Every run is recorded under $STUB_CALLS: argv, cwd, stdin and the state of the vault lock
# descriptor in its environment (A1: unset — closed and scrubbed for claude). A file at
# $STUB_FAIL_ONCE makes the next run exit 1 and removes the file.
case "${1:-}" in
  --version) echo "2.1.296 (Claude Code)"; exit 0 ;;
  --help)
    printf 'Usage: claude [options] [command] [prompt]\nOptions:\n  -p, --print                  Print response and exit\n'
    printf '  --restricted                 Restrict file tools to the working directory and disable shell tools\n'
    printf '  --tools <tools...>           Tool names the model may use\n  --json-schema <schema>       JSON schema for structured output\n'
    exit 0 ;;
esac
n=$(( $(cat "$STUB_CALLS/count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_CALLS/count"
printf '%s\n' "$@" > "$STUB_CALLS/call-$n.argv"
pwd > "$STUB_CALLS/call-$n.cwd"
cat > "$STUB_CALLS/call-$n.stdin"
python3 - > "$STUB_CALLS/call-$n.lockfd" <<'PY'
import os
fd, ino = os.environ.get("MNEMON_VAULT_LOCK_FD"), os.environ.get("MNEMON_VAULT_LOCK_INODE")
if fd is None and ino is None:
    print("unset")
else:
    try:
        os.fstat(int(fd))
        print("open")
    except (ValueError, OSError, TypeError):
        print("closed")
PY
if [[ -n "${STUB_FAIL_ONCE:-}" && -f "$STUB_FAIL_ONCE" ]]; then
  rm -f "$STUB_FAIL_ONCE"
  echo "stub claude: simulated failure" >&2
  exit 1
fi
python3 - "$STUB_FIXTURE" "$STUB_CALLS/call-$n.stdin" <<'PY'
import json, sys
so = json.load(open(sys.argv[1], encoding="utf-8"))
prompt = open(sys.argv[2], encoding="utf-8", errors="replace").read()
template = prompt.split("=== EXTRACTION TEMPLATE ===", 1)[-1].split("=== CONTENT ===", 1)[0]
parts = template.split("\n## Body", 1)
headings = []
if len(parts) == 2:
    for line in parts[1].split("\n")[1:]:
        if line.startswith("## "):
            break
        if line.startswith("### "):
            headings.append(line[4:].strip())
if headings:
    so["body"] = "".join("### %s\n\nStub text for %s.\n\n" % (h, h.lower()) for h in headings)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                  "duration_ms": 100, "num_turns": 1, "permission_denials": [], "structured_output": so}))
PY
STUB
cat > "$STUBS/curl" <<'STUB'
#!/bin/sh
echo $(( $(cat "$STUB_CALLS/curl-count" 2>/dev/null || echo 0) + 1 )) > "$STUB_CALLS/curl-count"
exit 1
STUB
printf '#!/bin/sh\nexit 11\n' > "$STUBS/render-url.sh"
printf '#!/bin/sh\nprintf %%s "{\\"applied\\": false, \\"reason\\": \\"stub\\"}"\n' > "$STUBS/kb-depth-fold.py"
cat > "$STUBS/pdftotext" <<'STUB'
#!/bin/sh
out=""; for a in "$@"; do out="$a"; done
printf 'PDF text page one\n' > "$out"
STUB
printf '#!/bin/sh\nprintf "Title: Stub Paper\\nPages: 3\\n"\n' > "$STUBS/pdfinfo"
cat > "$STUBS/media-extract.py" <<'STUB'
#!/usr/bin/env bash
cmd="$1"; shift; out=""; file=""; prev=""
for a in "$@"; do case "$prev" in --out-dir) out="$a" ;; --file) file="$a" ;; esac; prev="$a"; done
mkdir -p "$out"
printf 'transcript line one\n' > "$out/transcript.txt"
printf '{"schema":"mnemon-media/1","method":"whisper:tiny","audio_path":"%s","title":"Stub Audio","duration":"0:01:00","upload_date":null,"video_id":null}\n' "$file" > "$out/media.json"
STUB
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH" STUB_CALLS STUB_FIXTURE="$FIX/capture/extract-good.json"
export RENDER_URL_CMD="$STUBS/render-url.sh" MEDIA_EXTRACT_CMD="$STUBS/media-extract.py" MNEMON_DEPTH_FOLD_CMD="$STUBS/kb-depth-fold.py"
# the probe record for the stub's version (D13), keyed by the exact `claude --version` line
printf '{"2.1.296 (Claude Code)": {"pass": true, "date": "2026-10-15", "probes": {"no-tools": true}}}\n' > "$XDG_STATE_HOME/mnemon/fence-probes.json"

"$MNEMON" init t --vault "$VAULT" --non-interactive >/dev/null 2>&1 || { echo "mnemon init failed"; exit 1; }

# --- helpers --------------------------------------------------------------------------------------
run() { rc=0; out=$("$@" 2>&1) || rc=$?; }
result() { printf '%s\n' "$out" | grep "^RESULT:$1=" | head -1 | cut -d= -f2-; }
# D6: <XDG_STATE_HOME>/mnemon/vaults/<sha256(physical vault path)[:16]>; §5.2: ingest/<sha256(abs inbox)[:8]>
VSTATE="$XDG_STATE_HOME/mnemon/vaults/$(printf '%s' "$VAULT" | shasum -a 256 | cut -c1-16)"
LOCK="$VSTATE/lock"
SD="$VSTATE/ingest/$(printf '%s' "$INBOX" | shasum -a 256 | cut -c1-8)"
receipt() { printf '%s/captures/%s.json' "$VSTATE" "$1"; }
jq_() {
  if [[ ! -f "$1" ]]; then printf '<missing>'; return 0; fi
  python3 -c 'import json, sys; d = json.load(open(sys.argv[1], encoding="utf-8")); print(eval(sys.argv[2]))' "$1" "$2"
}
res() { jq_ "$SD/results/$1.json" "$2"; }
rcp() { jq_ "$(receipt "$1")" "$2"; }
# fm <file> <key>: a top-level frontmatter scalar, read only between the --- markers, the two escapes decoded
fm() {
  python3 - "$1" "$2" <<'PY'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
if lines and lines[0] == "---":
    for ln in lines[1:]:
        if ln == "---":
            break
        if ln.startswith(sys.argv[2] + ":"):
            v = ln[len(sys.argv[2]) + 1:].strip()
            if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            print(v)
            break
PY
}
body_sha() {
  python3 - "$1" <<'PY'
import hashlib, sys
d = open(sys.argv[1], "rb").read()
i = d.find(b"\n---\n", 3)
print(hashlib.sha256(d[i + 5:]).hexdigest())
PY
}
nfolders() { (shopt -s nullglob; for d in "$VAULT"/Sources/*/; do basename "$d"; done) | wc -l | tr -d ' '; }
claude_calls() { cat "$STUB_CALLS/count" 2>/dev/null || echo 0; }
curl_calls() { cat "$STUB_CALLS/curl-count" 2>/dev/null || echo 0; }
set_field() {   # <envelope stem> <key> <json value> — the tests' clock on a result record
  python3 - "$SD/results/$1.json" "$2" "$3" <<'PY'
import json, sys
path, key, value = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
d = json.load(open(path, encoding="utf-8"))
d[key] = value
with open(path, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False, indent=1)
PY
}
write_text_envelope() {   # <envelope_id> <text without the trailing newline>: a valid mnemon-envelope/1 for instance t
  python3 - "$INBOX" "$1" "$2" <<'PY'
import hashlib, json, os, sys
inbox, eid, text = sys.argv[1], sys.argv[2], sys.argv[3] + "\n"
raw = text.encode("utf-8")
env = {"schema": "mnemon-envelope/1", "envelope_id": eid,
       "producer": {"id": "test-producer", "kind": "fixture", "host": "node-a", "version": "1"},
       "instance": "t", "created_at": "2026-10-15T12:00:00+00:00", "supersedes": None,
       "request": {"origin": "text", "url": None, "title": "Late", "author": None, "intent": None, "context": None,
                   "source_type": None, "tier": "full"},
       "payload": {"kind": "text", "text": text, "path": None, "media_type": "text/plain",
                   "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
       "fidelity": "exact", "provenance": {"test": "chain"}, "complete": True}
tmp, final = os.path.join(inbox, "." + eid + ".json.tmp"), os.path.join(inbox, eid + ".json")
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(env, fh, ensure_ascii=False, indent=1)
os.replace(tmp, final)
PY
}
PAST='"2026-01-01T00:00:00+00:00"'
TEXT_SHA="35523eb5be09c122fd11c762197353b24bf36a9871495bbef659e6e81a6981e7"    # sha256 of 'hello\r\n\nworld\n'
TEXT2_SHA="252e502db2c449b2f59472fde3e9c35bae976c1902b8a1394f783c95fed49dcb"   # … + 'edited\n' (text-plain~2)

# ==================================================================================================
echo "--- (a) mnemon add: byte fidelity, receipt, index, lock ---"
rc=0; printf 'hello\r\n\nworld\n' | "$MNEMON" --instance t add --origin text --title T --capture-id e1 >"$TEST_TMPDIR/a.out" 2>"$TEST_TMPDIR/a.err" || rc=$?
out="$(cat "$TEST_TMPDIR/a.out")"
assert_eq "$rc" "0" "mnemon add --origin text --capture-id e1 through the dispatcher and vault-lock.py exits 0"
R1="$(result revision)"
assert_eq "$(result status)" "extracted" "...RESULT:status=extracted"
[[ "$R1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9a-f]{8}$ ]] && pass "...revision $R1 has the YYYY-MM-DD_hash8 form" || fail "...revision form" "got '$R1'"
assert_eq "$(result path)" "Sources/$R1/" "...RESULT:path names the folder"
assert_eq "$(body_sha "$VAULT/Sources/$R1/source.md")" "$TEXT_SHA" "source.md body == the stdin bytes (CRLF and the trailing newline kept)"
assert_eq "$(result source_sha256)" "$TEXT_SHA" "...RESULT:source_sha256 is that hash"
assert_eq "$(fm "$VAULT/Sources/$R1/source.md" capture_id)" "e1" "...source.md carries capture_id e1"
assert_file_exists "$VAULT/Sources/$R1/extract.md" "extract.md published by the gateway"
assert_eq "$(fm "$VAULT/Sources/$R1/extract.md" title)" "Plain T" "...with the validated model title"
assert_eq "$(rcp e1 'd["state"] + " " + d["revision"] + " " + str(d["source_published"]) + " " + str(d["replayed"]) + " " + d["claude_code"]')" "extracted $R1 True False 2.1.296 (Claude Code)" "receipt e1: extracted, the revision, not a replay, the stub's version"
assert_file_exists "$VAULT/index.md" "index.md rebuilt before the gateway exits"
assert_contains "$(cat "$VAULT/index.md")" "$R1" "...listing the new revision"
assert_eq "$(python3 "$LOCK_PY" --vault "$VAULT" --probe)" "free" "the vault lock is free after the capture"
assert_eq "$(claude_calls)" "1" "the stub claude ran once"
assert_eq "$(cat "$STUB_CALLS/call-1.cwd")" "$VSTATE/runs/e1/attempt-1" "...with the attempt dir as its working directory"
assert_contains "$(tr '\n' ' ' < "$STUB_CALLS/call-1.argv")" "-p --restricted --tools  --permission-prompts none --strict-mcp-config --no-session-persistence --output-format json --json-schema" "...behind the fence argv"
assert_contains "$(cat "$STUB_CALLS/call-1.stdin")" "=== CONTENT ===" "...with the prompt on stdin"
assert_eq "$(cat "$STUB_CALLS/call-1.lockfd")" "unset" "...and without the vault lock descriptor (A1: closed and scrubbed for claude)"
assert_file_exists "$VSTATE/runs/e1/attempt-1/capture.md" "the stdin locator attempt-1/capture.md is kept"

# ==================================================================================================
echo "--- (b) ingest: the first pass over the contract fixtures through the real chain ---"
# The fixtures already name instance `t` (wrong-instance stays `other` on purpose). text-plain carries
# the bytes of e1, so the real identity rule makes it a duplicate of $R1 — which is what the ~2
# re-capture then supersedes (A6 records the duplicate's revision; A5 resolves it in the same pass).
python3 "$FIX/inbox/materialize.py" "$FIX/contracts/envelopes.json" "$INBOX" >/dev/null
run "$MNEMON" --instance t ingest "$INBOX"
assert_eq "$rc" "0" "the pass exits 0"
assert_not_contains "$out" "exit 75" "no exit 75 inside the chain: every dispatched gateway ran under the pass's lock (A1)"
assert_not_contains "$out" "another writer holds the vault lock" "...and none reported the lock as held"
assert_contains "$out" "ingest: processed 18 · captured 5 · failed 4 · needs_input 1 · waiting 0 · deferred 1 · done-before 0 · other-instance 1 · unsupported 1 · quarantined 5" "summary line of the first pass"
printf '%s\n' "$out" | grep -E '^(captured|failed|deferred|needs_input|unsupported|quarantined|other-instance) ' | sed 's/^/  · /'
assert_eq "$(res test-producer:text-plain 'd["state"] + " dup=" + str(d["duplicate"]) + " " + str(d["revision"]) + " " + str(d["path"]) + " bound=" + str(d["receipt_bound"]) + " " + d["dispatch_mode"]')" "captured dup=True $R1 Sources/$R1/ bound=True request" "text-plain (the bytes of e1) → captured as a duplicate of $R1 with revision and path (A6); receipt bound by the full request (A2)"
assert_eq "$(res test-producer:text-plain 'd["source_sha256"]')" "$TEXT_SHA" "...source_sha256 recomputed from the existing folder (A6)"
assert_eq "$(rcp test-producer:text-plain 'd["state"] + " " + str(d["resolved_to_revision"]) + " " + str(d["resolved_to_capture_id"])')" "extracted $R1 e1" "...an alias receipt resolved to e1's revision"
assert_eq "$(res test-producer:text-with-url 'd["state"] + " dup=" + str(d["duplicate"]) + " " + str(d["revision"])')" "captured dup=True $R1" "text-with-url → captured, duplicate of the same text resource"
assert_eq "$(res test-producer:url-auto 'd["state"] + "/" + d["error_class"] + " retryable=" + str(d["retryable"]) + " exit=" + str(d["last_exit"]) + " attempts=" + str(d["attempts"]) + " bound=" + str(d["receipt_bound"])')" "failed/fetch retryable=True exit=1 attempts=1 bound=False" "url-auto → failed/fetch (stub curl and renderer fail); exit 1 never binds the receipt (A2)"
assert_eq "$(rcp test-producer:url-auto 'd["state"] + "/" + d["error_class"]')" "failed/fetch" "...the gateway's receipt agrees"
assert_eq "$(res test-producer:url-explicit 'd["state"] + "/" + d["error_class"]')" "failed/fetch" "url-explicit → failed/fetch"
assert_eq "$(res test-producer:context-undeclared 'd["state"] + "/" + d["error_class"] + " retryable=" + str(d["retryable"]) + " exit=" + str(d["last_exit"])')" "failed/contract retryable=False exit=2" "context-undeclared → failed/contract (the gateway refused context 'mc')"
assert_contains "$(res test-producer:context-undeclared 'd["last_error"]')" "context 'mc' is not declared" "...with the gateway's message kept"
[[ -f "$(receipt test-producer:context-undeclared)" ]] && fail "a contract refusal writes no receipt" "exists" || pass "a contract refusal writes no receipt"
R2="$(res test-producer:text-plain~2 'd["revision"]')"
assert_eq "$(res test-producer:text-plain~2 'd["state"] + " dup=" + str(d["duplicate"]) + " unresolved=" + str(d["supersedes_unresolved"]) + " bound=" + str(d["receipt_bound"])')" "captured dup=False unresolved=False bound=True" "text-plain~2 → captured on the FIRST pass (A5: same-pass resolution of its predecessor)"
[[ -n "$R2" && "$R2" != "$R1" && "$R2" != "None" ]] && pass "...in a new revision $R2" || fail "...in a new revision" "got '$R2'"
assert_eq "$(fm "$VAULT/Sources/$R2/source.md" supersedes)" "$R1" "...whose source.md records supersedes: $R1"
assert_eq "$(rcp test-producer:text-plain~2 'str(d["request"]["recapture"]) + " " + str(d["request"]["supersedes"])')" "True $R1" "...through --recapture --supersedes $R1 (the receipt's bound request)"
[[ "$(fm "$VAULT/Sources/$R2/source.md" resource_sha256)" != "$(fm "$VAULT/Sources/$R1/source.md" resource_sha256)" ]] && pass "...with a different resource_sha256 (edited text is a new resource)" || fail "...with a different resource_sha256" "equal"
assert_eq "$(body_sha "$VAULT/Sources/$R2/source.md")" "$TEXT2_SHA" "...and a body equal to the edited bytes"
assert_eq "$(res test-producer:text-plain~10 'd["state"] + "/" + d["error_class"] + " attempts=" + str(d["attempts"])')" "deferred/predecessor-pending attempts=0" "text-plain~10 stays deferred/predecessor-pending (its ~9 never appears)"
RPDF="$(res test-producer:file-pdf 'd["revision"]')"
assert_eq "$(res test-producer:file-pdf 'd["state"] + " dup=" + str(d["duplicate"])')" "captured dup=False" "file-pdf → captured through --file <snapshot> (stub pdftotext/pdfinfo)"
assert_eq "$(fm "$VAULT/Sources/$RPDF/source.md" origin)" "pdf" "...as origin pdf"
assert_eq "$(fm "$VAULT/Sources/$RPDF/source.md" import_ref)" "test-producer:file-pdf" "...import_ref = the envelope id"
RAUD="$(res test-producer:file-origin-audio 'd["revision"]')"
assert_eq "$(res test-producer:file-origin-audio 'd["state"] + " dup=" + str(d["duplicate"])')" "captured dup=False" "file-origin-audio → captured (stub media-extract.py transcribes the snapshot)"
assert_eq "$(fm "$VAULT/Sources/$RAUD/source.md" origin)" "audio" "...as origin audio"
python3 - "$FIX/contracts/gateway-outcomes.json" "$FIX/contracts/envelopes.json" "$SD/results" <<'PY' | while IFS= read -r line; do case "$line" in PASS\ *) pass "${line#PASS }" ;; *) fail "${line#FAIL }" "" ;; esac; done
import json, os, sys
outcomes = json.load(open(sys.argv[1], encoding="utf-8"))
cases = {c["name"]: c for c in json.load(open(sys.argv[2], encoding="utf-8"))}
for name, expected in outcomes["expected_without_gateway"].items():
    if name == "text-plain~10":
        continue  # asserted above
    stem = cases[name]["file"][:-5]
    path = os.path.join(sys.argv[3], stem + ".json")
    rec = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else None
    if expected == "other-instance":
        print(("PASS" if rec is None else "FAIL") + " %s: other-instance leaves no result record" % name)
        continue
    state, _, cls = expected.partition("/")
    ok = rec is not None and rec["state"] == state and (not cls or rec.get("error_class") == cls) and rec["attempts"] == 0
    print(("PASS" if ok else "FAIL") + " %s settles as %s without a gateway call" % (name, expected))
PY
assert_eq "$(claude_calls)" "4" "the stub claude ran for the three fresh captures of the pass (and e1): duplicates and failed acquisitions never reach it"
assert_eq "$(cat "$STUB_CALLS"/call-*.lockfd | sort -u | tr '\n' ' ')" "unset " "no claude run saw the vault lock descriptor (A1)"
assert_eq "$(nfolders)" "4" "four revision folders: $R1, $R2, the pdf and the audio"
assert_eq "$(python3 "$LOCK_PY" --vault "$VAULT" --probe)" "free" "the vault lock is free after the pass"
assert_eq "$(find "$VAULT/Sources" -maxdepth 1 -name '.*.staging-*' | wc -l | tr -d ' ')" "0" "no staging directory left behind"
CALLS_B="$(claude_calls)"

# ==================================================================================================
echo "--- (c) second and third pass: done-before for everything settled ---"
run "$MNEMON" --instance t ingest "$INBOX"
assert_eq "$rc" "0" "the second pass exits 0"
assert_contains "$out" "ingest: processed 18 · captured 0 · failed 0 · needs_input 0 · waiting 3 · deferred 0 · done-before 14 · other-instance 1 · unsupported 0 · quarantined 0" "second pass: done-before for the 14 settled ones (5 captured, 2 permanent failures, needs_input, unsupported, 5 quarantined), waiting for the three not yet due"
assert_contains "$out" "done-before test-producer:text-plain — captured" "...the captured ones are done-before"
assert_contains "$out" "done-before test-producer:text-plain~2 — captured" "...the re-capture too"
assert_contains "$out" "waiting test-producer:url-auto — failed/fetch, next attempt at" "...a retryable failure waits for its backoff"
run "$MNEMON" --instance t ingest "$INBOX"
assert_contains "$out" "done-before 14" "the third pass is the same no-op"
assert_eq "$(claude_calls)" "$CALLS_B" "...the gateway ran for nothing"
assert_eq "$(nfolders)" "4" "...and published nothing"

# ==================================================================================================
echo "--- (d) doctor sees the ledger ---"
run "$MNEMON" --instance t doctor
assert_contains "$out" "ok vault-lock — free ($LOCK)" "doctor: ok vault-lock (free)"
assert_contains "$out" "ok vault-state — $VSTATE" "doctor: ok vault-state (0700)"
assert_contains "$out" "ok fence — claude 2.1.296, --restricted available, probed 2026-10-15" "doctor: ok fence for the stub's version"
assert_contains "$out" "warn captures — 2 failed/extract-failed receipt(s) — finish with: mnemon add --resume test-producer:url-auto" "doctor: captures names the two failed url receipts"
assert_contains "$out" "warn ingest — $INBOX: failed 2, deferred 1, permanent 2 (not retried)" "doctor: ingest counts the retryable failed and deferred results of the inbox, the two permanent failures (contract, invalid-request) apart"

# ==================================================================================================
echo "--- (e) a second config on the same vault sees the same ledger ---"
"$MNEMON" init t2 --vault "$VAULT" --non-interactive >/dev/null 2>&1 || fail "mnemon init t2 on the same vault" "init failed"
assert_eq "$("$MNEMON" --instance t2 config dump | grep '^MNEMON_VAULT_STATE_DIR=' | cut -d= -f2-)" "$VSTATE" "instance t2 resolves the same vault state dir"
n0="$(nfolders)"; c0="$(claude_calls)"
rc=0; printf 'hello\r\n\nworld\n' | "$MNEMON" --instance t2 add --origin text --title T --capture-id e1 >"$TEST_TMPDIR/e.out" 2>"$TEST_TMPDIR/e.err" || rc=$?
out="$(cat "$TEST_TMPDIR/e.out")"
assert_eq "$rc" "0" "mnemon --instance t2 add --capture-id e1 with the same stdin exits 0"
assert_eq "$(result replayed)" "true" "...RESULT:replayed=true (the receipt of instance t answered)"
assert_eq "$(result revision)" "$R1" "...the same revision"
assert_eq "$(result status)" "extracted" "...status extracted"
assert_eq "$(nfolders)" "$n0" "...no new folder"
assert_eq "$(claude_calls)" "$c0" "...no extractor run"
assert_eq "$(rcp e1 'str(d["replayed"]) + " " + d["instance"]')" "True t" "...the receipt is marked replayed and still records instance t"
rc=0; printf 'hello\r\n\nworld\n' | "$MNEMON" --instance t2 add --origin text --title T --capture-id e1 --context work >"$TEST_TMPDIR/e2.out" 2>"$TEST_TMPDIR/e2.err" || rc=$?
assert_eq "$rc" "2" "...a replay with a different bound field (--context) is a contract error (exit 2)"

# ==================================================================================================
echo "--- A2: an exit 4 binds the receipt; the retry replays by id ---"
write_text_envelope test-producer:late-text "late text"
export STUB_FAIL_ONCE="$TEST_TMPDIR/fail-once"
touch "$STUB_FAIL_ONCE"
run "$MNEMON" --instance t ingest "$INBOX" --only test-producer:late-text
assert_eq "$rc" "0" "a pass whose extractor fails exits 0"
[[ -f "$STUB_FAIL_ONCE" ]] && fail "the stub consumed the failure flag" "still there" || pass "the stub consumed the failure flag"
assert_eq "$(res test-producer:late-text 'd["state"] + "/" + d["error_class"] + " retryable=" + str(d["retryable"]) + " exit=" + str(d["last_exit"]) + " bound=" + str(d["receipt_bound"]) + " " + d["dispatch_mode"] + " attempts=" + str(d["attempts"])')" "failed/claude-exit retryable=True exit=4 bound=True request attempts=1" "exit 4 (claude-exit) after the full request: failed, retryable, the receipt bound (A2)"
RL="$(res test-producer:late-text 'd["revision"]')"
assert_file_exists "$VAULT/Sources/$RL/source.md" "...source.md was published before the extractor ran"
[[ -f "$VAULT/Sources/$RL/extract.md" ]] && fail "...no extract.md yet" "exists" || pass "...no extract.md yet"
assert_eq "$(rcp test-producer:late-text 'd["state"] + "/" + d["error_class"]')" "extract-failed/claude-exit" "...receipt extract-failed/claude-exit"
set_field test-producer:late-text next_attempt_at "$PAST"
run "$MNEMON" --instance t ingest "$INBOX" --only test-producer:late-text
assert_eq "$(res test-producer:late-text 'd["state"] + " " + d["dispatch_mode"] + " attempts=" + str(d["attempts"]) + " " + d["revision"] + " dup=" + str(d["duplicate"])')" "captured replay attempts=2 $RL dup=False" "the retry replays by id alone (dispatch_mode replay) and finishes the same revision"
assert_eq "$(res test-producer:late-text 'd["last_result"]["replayed"]')" "true" "...the gateway reports replayed=true"
assert_file_exists "$VAULT/Sources/$RL/extract.md" "...extract.md published on the resume"
assert_eq "$(rcp test-producer:late-text 'd["state"] + " attempts=" + str(d["attempts"]) + " " + str(d["replayed"])')" "extracted attempts=2 True" "...receipt extracted under its second attempt"
unset STUB_FAIL_ONCE

echo "--- A2: an unbound receipt gets the full request again (the gateway's binding check) ---"
set_field test-producer:url-auto next_attempt_at "$PAST"
curl0="$(curl_calls)"
run "$MNEMON" --instance t ingest "$INBOX" --only test-producer:url-auto
assert_eq "$(res test-producer:url-auto 'd["state"] + "/" + d["error_class"] + " attempts=" + str(d["attempts"]) + " " + d["dispatch_mode"] + " bound=" + str(d["receipt_bound"])')" "failed/fetch attempts=2 request bound=False" "url-auto's retry sends the full saved request (exit 1 never bound it); still failed/fetch"
assert_eq "$(rcp test-producer:url-auto 'str(d["attempts"]) + " " + str(d["replayed"])')" "2 True" "...the gateway accepted the request against its receipt and re-ran the acquisition (attempt 2)"
assert_eq "$(( $(curl_calls) - curl0 ))" "1" "...fetching once more (nothing was published, so a refetch is right)"

# ==================================================================================================
echo "--- A1: reindex runs under the vault lock; another vault's descriptor is no proof ---"
python3 "$LOCK_PY" --vault "$VAULT" -- bash -c ': > "$0"; sleep 3' "$TEST_TMPDIR/held-1" & holder=$!
wait_file "$TEST_TMPDIR/held-1"
run "$MNEMON" --instance t reindex
assert_eq "$rc" "75" "mnemon reindex while another writer holds the vault lock → 75"
wait "$holder" 2>/dev/null || true
run "$MNEMON" --instance t reindex
assert_eq "$rc" "0" "...and runs once the lock is free"
"$MNEMON" init u --vault "$TEST_TMPDIR/vault-u" --non-interactive >/dev/null 2>&1 || fail "mnemon init u" "init failed"
rc=0; printf 'foreign one\n' | python3 "$LOCK_PY" --vault "$TEST_TMPDIR/vault-u" -- "$MNEMON" --instance t add --origin text --capture-id f1 >"$TEST_TMPDIR/f1.out" 2>"$TEST_TMPDIR/f1.err" || rc=$?
out="$(cat "$TEST_TMPDIR/f1.out")"
assert_eq "$rc" "0" "a descriptor for vault u's lock in the environment: the capture into vault t acquires its own lock and succeeds"
assert_eq "$(result status)" "extracted" "...extracted"
python3 "$LOCK_PY" --vault "$VAULT" -- bash -c ': > "$0"; sleep 3' "$TEST_TMPDIR/held-2" & holder=$!
wait_file "$TEST_TMPDIR/held-2"
rc=0; printf 'foreign two\n' | python3 "$LOCK_PY" --vault "$TEST_TMPDIR/vault-u" -- "$MNEMON" --instance t add --origin text --capture-id f2 >"$TEST_TMPDIR/f2.out" 2>"$TEST_TMPDIR/f2.err" || rc=$?
assert_eq "$rc" "75" "...and gets 75 when vault t's lock is really held (vault u's descriptor was never taken as proof)"
wait "$holder" 2>/dev/null || true
[[ -f "$(receipt f2)" ]] && fail "...no receipt on 75" "exists" || pass "...no receipt on 75"

cd /
rm -rf "$TEST_TMPDIR"
summary
