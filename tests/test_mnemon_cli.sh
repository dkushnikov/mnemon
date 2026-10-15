#!/usr/bin/env bash
# test_mnemon_cli.sh — the `mnemon` dispatcher: help/unknown, init, config,
# doctor, subcommand wiring, release (in a temp repo + temp release worktree).
source "$(dirname "$0")/test-helper.sh"

MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MNEMON="$MNEMON_ROOT/bin/mnemon"

echo "=== mnemon CLI Tests ==="

# Isolation: temp XDG config/state, no inherited instance selection, no
# MNEMON_HOME (so the engine check is deterministic), cwd without mnemon.yaml.
TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-cli-XXXX)" && pwd -P)"
export XDG_CONFIG_HOME="$TEST_TMPDIR/xdg"
export XDG_STATE_HOME="$TEST_TMPDIR/state"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_HOME
CFG_DIR="$XDG_CONFIG_HOME/mnemon"
mkdir -p "$TEST_TMPDIR/cwd"
cd "$TEST_TMPDIR/cwd"
THIS_HOST="$(hostname -s 2>/dev/null || hostname)"
THIS_HOST="${THIS_HOST%%.*}"

# run <cmd...>: captures combined output in $out and the exit code in $rc.
run() { rc=0; out=$("$@" 2>&1) || rc=$?; }
jget() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

# --- help / unknown / reserved ---
echo "--- help / unknown ---"
run "$MNEMON" help
assert_eq "$rc" "0" "help exits 0"
assert_contains "$out" "Usage: mnemon [--instance <name>] [--config <path>] <command>" "help prints usage"
run "$MNEMON" --help
assert_eq "$rc" "0" "--help exits 0"
run "$MNEMON" bogus
assert_eq "$rc" "2" "unknown command exits 2"
assert_contains "$out" "unknown command: bogus" "unknown command is named"
assert_contains "$out" "Usage: mnemon" "unknown command prints usage"
run "$MNEMON"
assert_eq "$rc" "2" "no command exits 2"
run "$MNEMON" --nope status
assert_eq "$rc" "2" "unknown global option exits 2"
for c in ingest scan monitor; do
  run "$MNEMON" "$c"
  assert_eq "$rc" "2" "$c exits 2"
  assert_contains "$out" "mnemon $c: not implemented yet (program phase 4)" "$c reports not implemented"
done
run "$MNEMON" version
assert_eq "$rc" "0" "version exits 0"
assert_contains "$out" "mnemon " "version prints the name"
assert_contains "$out" "$MNEMON_ROOT" "version prints the root"

# --- init ---
echo "--- init ---"
VAULT_A="$TEST_TMPDIR/vault-a"
run "$MNEMON" init alpha --vault "$VAULT_A" --non-interactive
assert_eq "$rc" "0" "init alpha exits 0"
assert_file_exists "$CFG_DIR/instances/alpha.yaml" "init writes instances/alpha.yaml"
assert_file_exists "$CFG_DIR/default" "init writes the default file"
assert_eq "$(cat "$CFG_DIR/default")" "alpha" "first instance becomes the default"
assert_dir_exists "$VAULT_A/Sources" "init scaffolds Sources/"
assert_dir_exists "$VAULT_A/Synthesis" "init scaffolds Synthesis/"
assert_dir_exists "$VAULT_A/_meta" "init scaffolds _meta/"
assert_file_exists "$VAULT_A/CLAUDE.md" "init copies CLAUDE.md"
assert_file_exists "$VAULT_A/_meta/Protocol.md" "init copies _meta/Protocol.md"
assert_file_exists "$VAULT_A/reader-context.md" "init places the reader-context template"
assert_file_exists "$VAULT_A/mnemon-seed.md" "init places mnemon-seed.md"
cfg=$(cat "$CFG_DIR/instances/alpha.yaml")
assert_contains "$cfg" "instance: alpha" "config has instance: <name>"
assert_contains "$cfg" "vault_path: $VAULT_A" "config has the absolute vault path"
assert_contains "$cfg" "reader_context_path: $VAULT_A/reader-context.md" "config has reader_context_path"
assert_not_contains "$cfg" "{{" "no unrendered placeholders"
assert_eq "$(printf '%s\n' "$cfg" | grep -E '^[a-z_]+:' | grep -c '~' || true)" "0" "no tilde in generated values (comments may show ~ examples)"
assert_contains "$out" "mnemon doctor" "init prints the next step"

# refuses to overwrite; --force rewrites the config but never vault files
echo "# user customized" >> "$VAULT_A/CLAUDE.md"
run "$MNEMON" init alpha --vault "$VAULT_A" --non-interactive
assert_eq "$rc" "1" "init refuses to overwrite an existing instance"
assert_contains "$out" "already exists" "overwrite refusal names the problem"
assert_contains "$out" "--force" "overwrite refusal mentions --force"
run "$MNEMON" init alpha --vault "$VAULT_A" --non-interactive --force
assert_eq "$rc" "0" "init --force exits 0"
assert_eq "$(tail -1 "$VAULT_A/CLAUDE.md")" "# user customized" "--force never overwrites vault files"

# second instance with options; the default stays
VAULT_B="$TEST_TMPDIR/vault-b"
mkdir -p "$TEST_TMPDIR/archive-b"
run "$MNEMON" init beta --vault "$VAULT_B" --collection kb-beta --archive "$TEST_TMPDIR/archive-b" \
  --writer-host "$THIS_HOST" --charter "$VAULT_B/_meta/charter.md" --non-interactive
assert_eq "$rc" "0" "init beta exits 0"
assert_eq "$(cat "$CFG_DIR/default")" "alpha" "second init leaves the default unchanged"
cfgb=$(cat "$CFG_DIR/instances/beta.yaml")
assert_contains "$cfgb" "qmd_collection: kb-beta" "--collection written"
assert_contains "$cfgb" "archive_dir: $TEST_TMPDIR/archive-b" "--archive written (absolute)"
assert_contains "$cfgb" "writer_host: $THIS_HOST" "--writer-host written"
assert_contains "$cfgb" "charter_path: $VAULT_B/_meta/charter.md" "--charter written"
assert_contains "$out" "mnemon --instance beta doctor" "init hints the --instance form for a non-default instance"

# --reader-context copies the given file into the vault
printf '# my reader context\n' > "$TEST_TMPDIR/rc.md"
run "$MNEMON" init gamma --vault "$TEST_TMPDIR/vault-g" --reader-context "$TEST_TMPDIR/rc.md" --default --non-interactive
assert_eq "$rc" "0" "init gamma --default exits 0"
assert_eq "$(cat "$TEST_TMPDIR/vault-g/reader-context.md")" "# my reader context" "--reader-context copied into the vault"
assert_eq "$(cat "$CFG_DIR/default")" "gamma" "--default switches the default instance"
printf 'alpha\n' > "$CFG_DIR/default"

# bad input
run "$MNEMON" init adhoc --vault "$TEST_TMPDIR/vault-x" --non-interactive
assert_eq "$rc" "1" "init adhoc is refused (reserved name)"
run "$MNEMON" init "bad name" --vault "$TEST_TMPDIR/vault-x" --non-interactive
assert_eq "$rc" "1" "init rejects an invalid instance name"
run "$MNEMON" init nova --non-interactive
assert_eq "$rc" "2" "init without --vault is a usage error"
run "$MNEMON" init nova --vault "$TEST_TMPDIR/vault-x" --reader-context "$TEST_TMPDIR/missing.md" --non-interactive
assert_eq "$rc" "1" "init fails on a missing --reader-context file"

# --- config ---
echo "--- config ---"
run "$MNEMON" config get vault_path
assert_eq "$out" "$VAULT_A" "config get vault_path (default instance)"
run "$MNEMON" config get instance
assert_eq "$out" "alpha" "config get instance (default instance)"
run "$MNEMON" --instance beta config get qmd_collection
assert_eq "$out" "kb-beta" "--instance selects beta"
run "$MNEMON" --instance=beta config get instance
assert_eq "$out" "beta" "--instance=<name> form"
run env MNEMON_INSTANCE=beta "$MNEMON" config get instance
assert_eq "$out" "beta" "MNEMON_INSTANCE selects beta"
run "$MNEMON" --config "$CFG_DIR/instances/beta.yaml" config get instance
assert_eq "$out" "beta" "--config path: instance from the yaml key"
run "$MNEMON" config path
assert_eq "$out" "$CFG_DIR/instances/alpha.yaml" "config path prints the resolved file"
run "$MNEMON" config get config_file
assert_eq "$out" "$CFG_DIR/instances/alpha.yaml" "config get config_file"
run "$MNEMON" config get state_dir
assert_eq "$out" "$XDG_STATE_HOME/mnemon/alpha" "config get state_dir (default)"
run "$MNEMON" config get charter_path
assert_eq "$out" "$VAULT_A/_meta/charter.md" "config get charter_path (default)"
run "$MNEMON" config get writer_host
assert_eq "$rc" "0" "config get writer_host exits 0 when unset"
assert_eq "$out" "" "config get writer_host is empty when unset"
run "$MNEMON" --instance beta config get archive_dir
assert_eq "$out" "$TEST_TMPDIR/archive-b" "config get archive_dir"
run "$MNEMON" config get search_provider
assert_eq "$out" "grep" "config get search_provider"
run "$MNEMON" config get nope
assert_eq "$rc" "2" "config get unknown key exits 2"
run "$MNEMON" config
assert_eq "$rc" "2" "config without subcommand exits 2"
run "$MNEMON" config list
assert_eq "$rc" "0" "config list exits 0"
assert_contains "$out" "* alpha" "config list marks the default with *"
assert_contains "$out" "  beta" "config list shows other instances"
assert_contains "$out" "$VAULT_B" "config list shows vault paths"
run "$MNEMON" config dump
assert_contains "$out" "VAULT_PATH=$VAULT_A" "config dump has VAULT_PATH"
assert_contains "$out" "MNEMON_INSTANCE=alpha" "config dump has MNEMON_INSTANCE"
assert_contains "$out" "MNEMON_CONFIG_FILE=$CFG_DIR/instances/alpha.yaml" "config dump has MNEMON_CONFIG_FILE"
assert_contains "$out" "MNEMON_STATE_DIR=$XDG_STATE_HOME/mnemon/alpha" "config dump has MNEMON_STATE_DIR"
assert_contains "$out" "CHARTER_PATH=" "config dump has CHARTER_PATH"
assert_contains "$out" "WRITER_HOST=" "config dump has WRITER_HOST"
run "$MNEMON" --instance nosuch config path
assert_eq "$rc" "1" "missing instance fails config path"
assert_contains "$out" "instances/nosuch.yaml" "missing instance error names the path"

# --- doctor ---
echo "--- doctor ---"
run "$MNEMON" doctor
assert_contains "$out" "ok config — $CFG_DIR/instances/alpha.yaml (instance alpha)" "doctor: ok config"
assert_contains "$out" "ok vault — $VAULT_A" "doctor: ok vault"
assert_contains "$out" "ok reader-context — $VAULT_A/reader-context.md" "doctor: ok reader-context"
assert_contains "$out" "warn charter — missing $VAULT_A/_meta/charter.md" "doctor: warn charter when missing"
assert_contains "$out" "ok archive — archive_dir not set (skipped)" "doctor: archive skipped when unset"
assert_contains "$out" "ok state — $XDG_STATE_HOME/mnemon/alpha" "doctor: ok state"
assert_dir_exists "$XDG_STATE_HOME/mnemon/alpha" "doctor creates the state dir"
assert_contains "$out" "warn engine — MNEMON_HOME not set" "doctor: warn engine without MNEMON_HOME"
assert_contains "$out" "ok writer — writer_host not set" "doctor: ok writer when unset"
assert_contains "$out" "ok pending-writes — none" "doctor: ok pending-writes"
assert_contains "$out" " deps — " "doctor: deps line present"
fails=$(printf '%s\n' "$out" | grep -c '^fail ' || true)
depfails=$(printf '%s\n' "$out" | grep -c '^fail deps' || true)
if [[ "$rc" -eq 0 ]]; then
  assert_eq "$fails" "0" "doctor rc 0 ⇒ no fail lines"
else
  assert_eq "$rc" "1" "doctor rc is 0 or 1"
  assert_eq "$fails" "$depfails" "doctor rc 1 only because of deps"
fi
doctor_rc=$rc

run "$MNEMON" doctor --json
assert_eq "$rc" "$doctor_rc" "doctor --json has the same rc"
jexit=$(printf '%s' "$out" | jget 'd["exit"]' 2>&1) || jexit="parse-error: $jexit"
assert_eq "$jexit" "$rc" "doctor --json parses and its exit field matches rc"
names=$(printf '%s' "$out" | jget '" ".join(c["name"] for c in d["checks"])')
assert_contains "$names" "config vault reader-context charter archive state deps" "doctor --json lists the checks in order"
assert_contains "$out" '{"name":"config","status":"ok","detail":"' "doctor --json check shape"
assert_not_contains "$out" "ok config —" "doctor --json emits no text lines"

run "$MNEMON" --instance beta doctor
assert_contains "$out" "ok archive — $TEST_TMPDIR/archive-b (writable)" "doctor: ok archive when set and writable"
assert_contains "$out" "ok writer — this host ($THIS_HOST) is the writer" "doctor: ok writer when the host matches"
assert_contains "$out" "ok config — $CFG_DIR/instances/beta.yaml (instance beta)" "doctor: --instance (global) selects beta"
mkdir -p "$VAULT_B/_meta" && printf '# charter\n' > "$VAULT_B/_meta/charter.md"
run "$MNEMON" doctor --instance beta
assert_contains "$out" "ok charter — $VAULT_B/_meta/charter.md" "doctor: ok charter when present (--instance after doctor)"

run "$MNEMON" init delta --vault "$TEST_TMPDIR/vault-d" --writer-host not-this-host --non-interactive
run "$MNEMON" --instance delta doctor
assert_eq "$rc" "1" "doctor fails on a non-writer host"
assert_contains "$out" "fail writer — this host must not write vault $TEST_TMPDIR/vault-d" "doctor: fail writer detail"

mkdir -p "$VAULT_A/_inputs/pending-writes"
touch "$VAULT_A/_inputs/pending-writes/a.json" "$VAULT_A/_inputs/pending-writes/b.json"
run "$MNEMON" doctor
assert_contains "$out" "warn pending-writes — 2 file(s) in $VAULT_A/_inputs/pending-writes" "doctor: warn pending-writes count"
rm -rf "$VAULT_A/_inputs"

run env MNEMON_HOME="$MNEMON_ROOT" "$MNEMON" doctor
assert_contains "$out" " engine — $MNEMON_ROOT" "doctor: engine check names MNEMON_HOME when it is the running tree"
mkdir -p "$TEST_TMPDIR/elsewhere"
run env MNEMON_HOME="$TEST_TMPDIR/elsewhere" "$MNEMON" doctor
assert_contains "$out" "warn engine — running from $MNEMON_ROOT, installed engine is $TEST_TMPDIR/elsewhere" "doctor: warn engine on a different root"

run "$MNEMON" --instance nosuch doctor
assert_eq "$rc" "1" "doctor fails when config does not resolve"
assert_contains "$out" "fail config — " "doctor: fail config line"
assert_not_contains "$out" " vault — " "doctor: config-dependent checks skipped when config fails"
rm -rf "$TEST_TMPDIR/vault-g/Sources"
run "$MNEMON" --instance gamma doctor
assert_eq "$rc" "1" "doctor fails on a broken vault"
assert_contains "$out" "fail vault — $TEST_TMPDIR/vault-g (missing: Sources/)" "doctor: fail vault names what is missing"

run "$MNEMON" doctor --bogus
assert_eq "$rc" "2" "doctor rejects unknown options"

# --- subcommand wiring ---
echo "--- subcommands ---"
run "$MNEMON" status
assert_eq "$rc" "0" "status via dispatcher exits 0"
assert_contains "$out" "Vault: $VAULT_A" "status uses the default instance vault"
run "$MNEMON" --instance beta status
assert_contains "$out" "Vault: $VAULT_B" "status honours --instance"
run "$MNEMON_ROOT/bin/knowledge-gateway.sh" status --instance beta
assert_eq "$rc" "0" "gateway accepts --instance"
assert_contains "$out" "Vault: $VAULT_B" "gateway --instance selects the instance"
run "$MNEMON_ROOT/bin/knowledge-gateway.sh" --help
assert_contains "$out" "--instance <name>" "gateway usage documents --instance"
run "$MNEMON" add --url "https://example.com/dispatch" --dry-run
assert_eq "$rc" "0" "add via dispatcher exits 0 (dry-run)"
assert_contains "$out" "DRY RUN" "add reaches the gateway"
assert_contains "$out" "https://example.com/dispatch" "add passes its arguments through"
run "$MNEMON" reindex
assert_eq "$rc" "0" "reindex via dispatcher exits 0"
assert_contains "$out" "reindex:" "reindex runs knowledge-reindex.py on the vault"
run "$MNEMON" --instance beta audit
assert_eq "$rc" "0" "audit via dispatcher exits 0"
assert_contains "$out" "GAP A" "audit runs archive-audit.py with the instance config"
run python3 "$MNEMON_ROOT/bin/archive-audit.py" --cutoff 2026-01-01
assert_eq "$rc" "0" "archive-audit.py resolves config through the engine when --config is absent"
run "$MNEMON" triage --help
assert_eq "$rc" "0" "triage --help via dispatcher exits 0"
assert_contains "$out" "--vault" "triage help mentions --vault"
run python3 "$MNEMON_ROOT/bin/triage-urls.py" "$CFG_DIR/default"
assert_eq "$rc" "2" "triage-urls.py without --vault is an error (no hardcoded vault)"
run "$MNEMON" --instance nosuch reindex
assert_eq "$rc" "1" "config-dependent subcommand fails with the loader message"
assert_contains "$out" "instances/nosuch.yaml" "...naming the missing instance file"

# --- release (temp dev repo + temp release worktree + temp bare origin) ---
echo "--- release ---"
REPO="$TEST_TMPDIR/dev"
RWT="$TEST_TMPDIR/engine"
ORIGIN="$TEST_TMPDIR/origin.git"
g() { local d="$1"; shift; git -C "$d" -c commit.gpgsign=false -c user.name="mnemon test" -c user.email="mnemon-test@example.invalid" "$@"; }
mkdir -p "$REPO/bin" "$REPO/tests"
cp "$MNEMON_ROOT/bin/mnemon" "$MNEMON_ROOT/bin/mnemon-config.sh" "$REPO/bin/"
cp "$MNEMON_ROOT/tests/test-helper.sh" "$MNEMON_ROOT/tests/test_config.sh" "$REPO/tests/"
git init -q "$REPO"
git -C "$REPO" symbolic-ref HEAD refs/heads/main
g "$REPO" add bin tests
g "$REPO" commit -q -m "init"
git init -q --bare "$ORIGIN"
git -C "$REPO" remote add origin "$ORIGIN"
g "$REPO" push -q origin main
git -C "$REPO" worktree add -q -b release "$RWT" main
assert_eq "$(git -C "$RWT" rev-parse --abbrev-ref HEAD)" "release" "fixture: release worktree on branch release"

run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "0" "release --dry-run exits 0"
assert_contains "$out" "git -C $RWT merge --ff-only main" "dry-run prints the ff-merge step with real paths"
assert_contains "$out" "git -C $RWT push origin release" "dry-run prints the push step"
assert_contains "$out" "$RWT/bin/mnemon doctor" "dry-run prints the smoke step"
assert_contains "$out" "→ clean" "dry-run reports the dev tree as clean"
assert_contains "$out" "→ yes" "dry-run reports release as an ancestor of main"
assert_not_contains "$out" "node ping" "dry-run without --now says nothing about ping"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run --now
assert_contains "$out" "node ping not implemented (program phase 4)" "--now prints the phase-4 notice"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "dry-run changes nothing"

run "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "1" "release without MNEMON_HOME fails"
run env MNEMON_HOME="$REPO" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "1" "release refuses a MNEMON_HOME that is not on branch release"
assert_contains "$out" "expected 'release'" "...and says why"
mkdir -p "$TEST_TMPDIR/notarepo"
run env MNEMON_HOME="$TEST_TMPDIR/notarepo" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "1" "release refuses a MNEMON_HOME that is not a git worktree"

printf '# dirty\n' >> "$REPO/tests/test-helper.sh"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release refuses a dirty dev tree"
assert_contains "$out" "uncommitted tracked changes" "...naming the reason"
g "$REPO" checkout -q -- tests/test-helper.sh

# success: a new commit on main is promoted; required deps the smoke doctor
# needs are stubbed only when this machine lacks them
printf '# release test\n' >> "$REPO/tests/test-helper.sh"
g "$REPO" commit -q -am "change on main"
STUBBIN="$TEST_TMPDIR/stubbin"
mkdir -p "$STUBBIN"
for d in claude jq shasum; do
  if ! command -v "$d" >/dev/null 2>&1; then
    printf '#!/bin/sh\nexit 0\n' > "$STUBBIN/$d"
    chmod +x "$STUBBIN/$d"
  fi
done
run env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --now
assert_eq "$rc" "0" "release succeeds (ff-merge + push + smoke)"
assert_contains "$out" "released $(git -C "$REPO" rev-parse --short main) → $RWT" "release prints the released sha and target"
assert_contains "$out" "node ping not implemented (program phase 4)" "release --now prints the phase-4 notice"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "release worktree now at main"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$(git -C "$REPO" rev-parse main)" "origin/release pushed"
assert_contains "$out" "ok config — " "release smoke ran doctor"
assert_contains "$out" "Results: " "release smoke ran test_config.sh"

printf '# hotfix on release\n' >> "$RWT/tests/test-helper.sh"
g "$RWT" commit -q -am "hotfix on release"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release fails when release has diverged"
assert_contains "$out" "release has diverged from main" "...with the specified message"

cd /
rm -rf "$TEST_TMPDIR"
summary
