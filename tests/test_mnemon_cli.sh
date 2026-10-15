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
assert_contains "$cfg" "instance: \"alpha\"" "config has instance: <name> (double-quoted)"
assert_contains "$cfg" "vault_path: \"$VAULT_A\"" "config has the absolute vault path (double-quoted)"
assert_contains "$cfg" "reader_context_path: \"$VAULT_A/reader-context.md\"" "config has reader_context_path (double-quoted)"
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
run "$MNEMON" init beta --vault "$VAULT_B" --collection kb-beta --archive "$TEST_TMPDIR/archive-b" \
  --writer-host "$THIS_HOST" --charter "$VAULT_B/_meta/charter.md" --non-interactive
assert_eq "$rc" "0" "init beta exits 0"
assert_eq "$(cat "$CFG_DIR/default")" "alpha" "second init leaves the default unchanged"
cfgb=$(cat "$CFG_DIR/instances/beta.yaml")
assert_contains "$cfgb" "qmd_collection: \"kb-beta\"" "--collection written"
assert_contains "$cfgb" "archive_dir: \"$TEST_TMPDIR/archive-b\"" "--archive written (absolute)"
assert_dir_exists "$TEST_TMPDIR/archive-b" "init --archive creates the archive dir"
assert_contains "$out" "created archive dir $TEST_TMPDIR/archive-b" "init reports the created archive dir"
assert_contains "$cfgb" "writer_host: \"$THIS_HOST\"" "--writer-host written"
assert_contains "$cfgb" "charter_path: \"$VAULT_B/_meta/charter.md\"" "--charter written"
assert_contains "$out" "mnemon --instance beta doctor" "init hints the --instance form for a non-default instance"

# --reader-context copies the given file into the vault
printf '# my reader context\n' > "$TEST_TMPDIR/rc.md"
run "$MNEMON" init gamma --vault "$TEST_TMPDIR/vault-g" --reader-context "$TEST_TMPDIR/rc.md" --default --non-interactive
assert_eq "$rc" "0" "init gamma --default exits 0"
assert_eq "$(cat "$TEST_TMPDIR/vault-g/reader-context.md")" "# my reader context" "--reader-context copied into the vault"
assert_eq "$(cat "$CFG_DIR/default")" "gamma" "--default switches the default instance"

# a default naming a missing instance is stale: config list marks it, init takes it over
printf 'ghost\n' > "$CFG_DIR/default"
run "$MNEMON" config list
assert_contains "$out" "! ghost" "config list marks a stale default"
assert_contains "$out" "stale default" "...and says why"
run "$MNEMON" init epsilon --vault "$TEST_TMPDIR/vault-e" --non-interactive
assert_eq "$rc" "0" "init with a stale default exits 0"
assert_eq "$(cat "$CFG_DIR/default")" "epsilon" "init takes over a stale default"
assert_contains "$out" "it named 'ghost'" "init notices the stale default"
run "$MNEMON" config path
assert_eq "$out" "$CFG_DIR/instances/epsilon.yaml" "commands resolve again after the takeover"
printf 'alpha\n' > "$CFG_DIR/default"

# ~user/... is a literal relative path (only ~ and ~/ expand)
run "$MNEMON" init tilde --vault "~someone/vault-t" --non-interactive
assert_eq "$rc" "0" "init with a ~user path exits 0"
assert_contains "$(cat "$CFG_DIR/instances/tilde.yaml")" "vault_path: \"$TEST_TMPDIR/cwd/~someone/vault-t\"" "~user/ is not expanded to HOME (literal path under the cwd)"
rm -f "$CFG_DIR/instances/tilde.yaml"

# F3: a vault path with a space, a ' #' and a quote round-trips — the config
# values are double-quoted (an unquoted value would end at the ' #')
VAULT_ODD="$TEST_TMPDIR/we ird #3 \"q\""
run "$MNEMON" init odd --vault "$VAULT_ODD" --archive "$TEST_TMPDIR/arch #ive" --non-interactive
assert_eq "$rc" "0" "init accepts a vault path with a space, ' #' and a quote"
assert_contains "$(cat "$CFG_DIR/instances/odd.yaml")" 'vault_path: "'"$TEST_TMPDIR"'/we ird #3 \"q\""' "the odd path is written double-quoted, the quote escaped"
run "$MNEMON" --instance odd config get vault_path
assert_eq "$out" "$VAULT_ODD" "config get vault_path returns the odd path unchanged"
run "$MNEMON" --instance odd config get archive_dir
assert_eq "$out" "$TEST_TMPDIR/arch #ive" "config get archive_dir returns a path with ' #' unchanged"
run "$MNEMON" --instance odd config get reader_context_path
assert_eq "$out" "$VAULT_ODD/reader-context.md" "reader_context_path derives from the odd vault path"
run "$MNEMON" --instance odd doctor
assert_contains "$out" "ok vault — $VAULT_ODD" "doctor finds the odd vault"
assert_contains "$out" "ok reader-context — $VAULT_ODD/reader-context.md" "doctor finds the reader context in the odd vault"
assert_contains "$out" "ok archive — $TEST_TMPDIR/arch #ive (writable)" "doctor finds the odd archive dir"
run "$MNEMON" --instance odd status
assert_eq "$rc" "0" "status works on the odd vault"
assert_contains "$out" "Vault: $VAULT_ODD" "status names the odd vault"
run "$MNEMON" --instance odd audit
assert_eq "$rc" "0" "audit (python, via lib_config) resolves the odd vault"

# bad input
run "$MNEMON" init adhoc --vault "$TEST_TMPDIR/vault-x" --non-interactive
assert_eq "$rc" "1" "init adhoc is refused (reserved name)"
run "$MNEMON" init "bad name" --vault "$TEST_TMPDIR/vault-x" --non-interactive
assert_eq "$rc" "1" "init rejects an invalid instance name"
run "$MNEMON" init nova --non-interactive
assert_eq "$rc" "2" "init without --vault is a usage error"
run "$MNEMON" init nova --vault "$TEST_TMPDIR/vault-x" --reader-context "$TEST_TMPDIR/missing.md" --non-interactive
assert_eq "$rc" "1" "init fails on a missing --reader-context file"
[[ -e "$TEST_TMPDIR/vault-x" ]] && fail "a missing --reader-context refuses before the scaffold" "vault-x exists" || pass "a missing --reader-context refuses before the scaffold"
printf 'not a dir\n' > "$TEST_TMPDIR/a-file"
run "$MNEMON" init nova --vault "$TEST_TMPDIR/vault-x" --archive "$TEST_TMPDIR/a-file/archive" --non-interactive
assert_eq "$rc" "1" "init fails when the --archive dir cannot be created"
assert_contains "$out" "cannot create archive dir" "...and says so"
assert_eq "$(ls "$CFG_DIR/instances" | grep -c '^nova' || true)" "0" "a refused init leaves no instance file"
[[ -e "$TEST_TMPDIR/vault-x" ]] && fail "an unwritable --archive refuses before the scaffold" "vault-x exists" || pass "an unwritable --archive refuses before the scaffold (no vault dir created)"

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
assert_eq "$rc" "2" "missing instance fails config path with exit 2"
assert_contains "$out" "instances/nosuch.yaml" "missing instance error names the path"
run "$MNEMON" config get config_source
assert_eq "$out" "default" "config get config_source names the winning selector"

# --- A1: explicit selectors outrank the inherited environment; conflicts fail closed ---
echo "--- selectors (A1) ---"
ALPHA_YAML="$CFG_DIR/instances/alpha.yaml"
BETA_YAML="$CFG_DIR/instances/beta.yaml"
run env MNEMON_CONFIG="$ALPHA_YAML" "$MNEMON" --instance beta config get vault_path
assert_eq "$out" "$VAULT_B" "--instance beats an inherited MNEMON_CONFIG"
run env MNEMON_INSTANCE=alpha "$MNEMON" --config "$BETA_YAML" config get vault_path
assert_eq "$out" "$VAULT_B" "--config beats an inherited MNEMON_INSTANCE"
run env MNEMON_CONFIG="$ALPHA_YAML" MNEMON_INSTANCE=beta "$MNEMON" config path
assert_eq "$rc" "2" "MNEMON_CONFIG + MNEMON_INSTANCE naming different configs → exit 2"
assert_contains "$out" "conflicting selectors in the environment (MNEMON_CONFIG=$ALPHA_YAML, MNEMON_INSTANCE=beta)" "environment conflict is named"
run env MNEMON_CONFIG="$BETA_YAML" MNEMON_INSTANCE=beta "$MNEMON" config get vault_path
assert_eq "$out" "$VAULT_B" "a consistent environment pair resolves"
run "$MNEMON" --config "$ALPHA_YAML" --instance beta config path
assert_eq "$rc" "2" "--config together with --instance → exit 2"
assert_contains "$out" "conflicting selectors" "flag conflict message"
run "$MNEMON" --config "$ALPHA_YAML" doctor --instance beta
assert_eq "$rc" "2" "global --config with doctor --instance → exit 2"
run "$MNEMON" --instance beta config dump
assert_contains "$out" "MNEMON_CONFIG=$BETA_YAML" "dump: the child-facing MNEMON_CONFIG is the resolved file"
assert_contains "$out" "MNEMON_INSTANCE=beta" "dump: MNEMON_INSTANCE is the resolved name"
assert_contains "$out" "MNEMON_CONFIG_SOURCE=--instance" "dump: the winning selector is reported"
run env MNEMON_INSTANCE=adhoc "$MNEMON" config path
assert_eq "$rc" "2" "MNEMON_INSTANCE=adhoc without MNEMON_CONFIG → exit 2"
run "$MNEMON" --instance adhoc config path
assert_eq "$rc" "2" "--instance adhoc → exit 2 (reserved name)"
run "$MNEMON" add --instance beta --url https://example.com/x --dry-run
assert_eq "$rc" "2" "a selector after the command is refused (dispatcher resolves once)"
assert_contains "$out" "--instance belongs before the command" "...with the hint"
assert_contains "$out" "mnemon --instance <name> <command> …" "...showing the global-option form with its argument"
run "$MNEMON" add --instance=beta --url https://example.com/x --dry-run
assert_contains "$out" "--instance belongs before the command: mnemon --instance <name> <command> …" "the --instance=<name> form gets the same hint"
run "$MNEMON" status --config "$BETA_YAML"
assert_eq "$rc" "2" "--config after the command is refused"
assert_contains "$out" "--config belongs before the command: mnemon --config <path> <command> …" "...with the --config form and its argument"
# a by-path copy declaring instance: alpha — the gateway child must see a consistent pair
cp "$ALPHA_YAML" "$TEST_TMPDIR/copy-of-alpha.yaml"
run "$MNEMON" --config "$TEST_TMPDIR/copy-of-alpha.yaml" config dump
assert_contains "$out" "MNEMON_INSTANCE=alpha" "by-path copy: instance from the yaml key"
assert_contains "$out" "MNEMON_CONFIG=$TEST_TMPDIR/copy-of-alpha.yaml" "by-path copy: MNEMON_CONFIG is the copy"
run "$MNEMON" --config "$TEST_TMPDIR/copy-of-alpha.yaml" status
assert_eq "$rc" "0" "by-path copy: the gateway child resolves the exported pair without a conflict"
assert_contains "$out" "Vault: $VAULT_A" "...and lands on the same vault"
# gateway called directly: its flags follow the same rule
run "$MNEMON_ROOT/bin/knowledge-gateway.sh" status --config "$ALPHA_YAML" --instance beta
assert_eq "$rc" "2" "gateway: --config with --instance → exit 2"
run env MNEMON_CONFIG="$ALPHA_YAML" "$MNEMON_ROOT/bin/knowledge-gateway.sh" status --instance beta
assert_eq "$rc" "0" "gateway: --instance beats an inherited MNEMON_CONFIG"
assert_contains "$out" "Vault: $VAULT_B" "...and selects beta"
run env MNEMON_CONFIG="$ALPHA_YAML" MNEMON_INSTANCE=beta "$MNEMON_ROOT/bin/knowledge-gateway.sh" status
assert_eq "$rc" "2" "gateway: an inconsistent environment pair → exit 2"

# --- doctor ---
echo "--- doctor ---"
run "$MNEMON" doctor
assert_contains "$out" "ok config — $CFG_DIR/instances/alpha.yaml (instance alpha; selected by default)" "doctor: ok config names the file, instance and selector"
assert_contains "$out" "ok vault — $VAULT_A" "doctor: ok vault"
assert_contains "$out" "ok reader-context — $VAULT_A/reader-context.md" "doctor: ok reader-context"
assert_contains "$out" "warn charter — missing $VAULT_A/_meta/charter.md" "doctor: warn charter when missing"
assert_contains "$out" "ok archive — archive_dir not set (skipped)" "doctor: archive skipped when unset"
assert_contains "$out" "ok state — $XDG_STATE_HOME/mnemon/alpha (absent, parent writable)" "doctor: ok state when the dir is absent but creatable"
[[ -e "$XDG_STATE_HOME/mnemon/alpha" ]] && fail "doctor is observational" "state dir was created" || pass "doctor does not create the state dir"
[[ -e "$XDG_STATE_HOME" ]] && fail "doctor is observational" "XDG_STATE_HOME was created" || pass "doctor does not create XDG_STATE_HOME"
mkdir -p "$XDG_STATE_HOME/mnemon/alpha"
run "$MNEMON" doctor
assert_contains "$out" "ok state — $XDG_STATE_HOME/mnemon/alpha" "doctor: ok state when the dir exists"
assert_not_contains "$out" "(absent" "...without the absent note"
rm -rf "$XDG_STATE_HOME"
assert_contains "$out" "warn engine — MNEMON_HOME not set" "doctor: warn engine without MNEMON_HOME"
assert_contains "$out" "ok writer — writer_host not set" "doctor: ok writer when unset"
assert_contains "$out" "ok pending-writes — none" "doctor: ok pending-writes"
assert_contains "$out" " deps — " "doctor: deps line present"
assert_contains "$out" " deps-optional — " "doctor: deps-optional line present"
assert_contains "$out" " deps-python — " "doctor: deps-python line present (pyyaml, requests)"
assert_eq "$(printf '%s\n' "$out" | grep -c '^fail deps-python' || true)" "0" "doctor: missing python packages warn, never fail"
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
assert_contains "$out" "ok config — $CFG_DIR/instances/beta.yaml (instance beta; selected by --instance)" "doctor: --instance (global) selects beta and is named as the selector"
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
assert_contains "$out" "fail vault — $TEST_TMPDIR/vault-g (missing: Sources/) — run: mnemon init gamma --vault $TEST_TMPDIR/vault-g --force" "doctor: fail vault names what is missing and how to fix it"
printf 'vault_path: %s/no-such-vault\n' "$TEST_TMPDIR" > "$TEST_TMPDIR/adhoc.yaml"
run "$MNEMON" --config "$TEST_TMPDIR/adhoc.yaml" doctor
assert_contains "$out" "fail vault — $TEST_TMPDIR/no-such-vault (missing: vault directory) — fix vault_path in $TEST_TMPDIR/adhoc.yaml" "doctor: by-path config gets a fix-the-file hint, not 'init adhoc'"
assert_not_contains "$out" "init adhoc" "doctor never suggests the reserved name"

run "$MNEMON" doctor --bogus
assert_eq "$rc" "2" "doctor rejects unknown options"

# F4: doctor never writes — `qmd collection list` creates qmd's index on a host
# where qmd never ran, so the collection probe runs only when qmd's cache dir
# (${XDG_CACHE_HOME:-$HOME/.cache}/qmd) already exists. A stub qmd logs every call.
echo "--- doctor: qmd probe (F4) ---"
QSTUB="$TEST_TMPDIR/qmdstub"; mkdir -p "$QSTUB"
QMD_LOG="$TEST_TMPDIR/qmd.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\n[ "$1 $2" = "collection list" ] && echo "kb-q /some/vault hybrid"\nexit 0\n' "$QMD_LOG" > "$QSTUB/qmd"
chmod +x "$QSTUB/qmd"
run "$MNEMON" init qinst --vault "$TEST_TMPDIR/vault-q" --collection kb-q --non-interactive
sed -i '' 's/^search_provider: grep/search_provider: qmd/' "$CFG_DIR/instances/qinst.yaml"
QCACHE="$TEST_TMPDIR/cache"
run env XDG_CACHE_HOME="$QCACHE" PATH="$QSTUB:$PATH" "$MNEMON" --instance qinst doctor
assert_contains "$out" "ok qmd — $QSTUB/qmd" "doctor: ok qmd when search_provider is qmd and qmd is on PATH"
assert_contains "$out" "warn qmd-collection — not probed (qmd never ran here" "doctor: no qmd cache dir → the collection is not probed"
assert_contains "$out" "no $QCACHE/qmd" "...naming the missing cache dir"
[[ -e "$QMD_LOG" ]] && fail "doctor runs no qmd command without its cache dir" "$(cat "$QMD_LOG")" || pass "doctor runs no qmd command without its cache dir"
[[ -e "$QCACHE/qmd" ]] && fail "doctor creates no qmd cache dir" "created" || pass "doctor creates no qmd cache dir"
mkdir -p "$QCACHE/qmd"
run env XDG_CACHE_HOME="$QCACHE" PATH="$QSTUB:$PATH" "$MNEMON" --instance qinst doctor
assert_contains "$out" "ok qmd-collection — kb-q" "doctor: with the cache dir present the collection is probed"
assert_eq "$(cat "$QMD_LOG" 2>/dev/null)" "collection list" "doctor ran exactly one 'qmd collection list'"
run env -u XDG_CACHE_HOME HOME="$TEST_TMPDIR/home-q" PATH="$QSTUB:$PATH" "$MNEMON" --instance qinst doctor
assert_contains "$out" "warn qmd-collection — not probed (qmd never ran here: no $TEST_TMPDIR/home-q/.cache/qmd" "doctor: without XDG_CACHE_HOME the default HOME/.cache/qmd is checked"
assert_eq "$(cat "$QMD_LOG" 2>/dev/null)" "collection list" "...and qmd was not run again"
run env PATH="/usr/bin:/bin" "$MNEMON" --instance qinst doctor
assert_contains "$out" "fail qmd — search_provider is qmd but qmd is not installed" "doctor: fail qmd when search_provider is qmd and qmd is missing"

# --- engine maintenance lock (A3) ---
echo "--- engine lock ---"
LOCK="$XDG_STATE_HOME/mnemon/engine-release.lock"
mkdir -p "$(dirname "$LOCK")"
printf 'pid=99999\ntimestamp=%s\ndate=2026-01-01T00:00:00Z\ntarget=abc1234\n' "$(date +%s)" > "$LOCK"
run "$MNEMON" help
assert_eq "$rc" "75" "fresh lock: mnemon help exits 75"
assert_contains "$out" "mnemon: engine is being updated, retry later" "fresh lock: the message"
assert_contains "$out" "target abc1234" "fresh lock: names the target"
run "$MNEMON" --help
assert_eq "$rc" "75" "fresh lock: mnemon --help exits 75 too"
run "$MNEMON" config path
assert_eq "$rc" "75" "fresh lock: config path exits 75"
run "$MNEMON" version
assert_eq "$rc" "75" "fresh lock: version exits 75"
run "$MNEMON_ROOT/bin/knowledge-gateway.sh" source-add --url https://example.com/locked --dry-run
assert_eq "$rc" "75" "fresh lock: the gateway exits 75"
assert_not_contains "$out" "DRY RUN" "fresh lock: the gateway does nothing"
run "$MNEMON" doctor
assert_contains "$out" "warn engine-lock — engine update in progress since 2026-01-01T00:00:00Z (pid 99999, target abc1234)" "fresh lock: doctor reports it"
assert_contains "$out" "ok config — " "fresh lock: doctor still runs (exempt)"
run env MNEMON_ENGINE_LOCK_OWNER=99999 "$MNEMON" version
assert_eq "$rc" "0" "the lock owner's children pass the guard"
run env MNEMON_ENGINE_LOCK_OWNER=99999 "$MNEMON" doctor
assert_contains "$out" "ok engine-lock — held by the running release (pid 99999, target abc1234)" "the release's own smoke doctor reports its lock as ok"
assert_not_contains "$out" "warn engine-lock" "...and not as a warning"
run env MNEMON_ENGINE_LOCK_OWNER=1 "$MNEMON" version
assert_eq "$rc" "75" "another pid as owner does not pass"
printf 'pid=99999\ntimestamp=%s\ndate=2026-01-01T00:00:00Z\ntarget=abc1234\n' "$(( $(date +%s) - 3600 ))" > "$LOCK"
run "$MNEMON" help
assert_eq "$rc" "0" "stale lock (timestamp older than 30 min): ignored"
assert_contains "$out" "mnemon: ignoring stale engine lock $LOCK" "stale lock: reported on stderr"
run "$MNEMON" doctor
assert_contains "$out" "warn engine-lock — stale lock $LOCK" "stale lock: doctor reports it as stale"
printf 'pid=99999\n' > "$LOCK"
touch -t 202001010000 "$LOCK"
run "$MNEMON" help
assert_eq "$rc" "0" "stale lock (no timestamp line, old mtime): ignored"
printf 'pid=99999\n' > "$LOCK"
run "$MNEMON" help
assert_eq "$rc" "75" "lock without a timestamp line but a fresh mtime blocks"
rm -f "$LOCK"
run "$MNEMON" doctor
assert_contains "$out" "ok engine-lock — none ($LOCK)" "no lock: doctor ok"
run "$MNEMON" help
assert_eq "$rc" "0" "no lock: help exits 0 again"

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
assert_eq "$rc" "2" "config-dependent subcommand fails with the loader's status (2: missing instance)"
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

# the drain pattern is the engine path with every ERE metacharacter escaped (the
# temp dir may contain a '.'), followed by /bin/
re_escape() { printf '%s' "$1" | sed -e 's/[]\\.^$*+?(){}|[]/\\&/g'; }
RWT_RE="$(re_escape "$RWT")"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "0" "release --dry-run exits 0"
assert_contains "$out" "1. lock:   $LOCK" "dry-run prints the lock step"
assert_contains "$out" "2. drain:  wait up to 120s for processes matching: pgrep -f $RWT_RE/bin/" "dry-run prints the drain step with the escaped pattern"
assert_contains "$out" "git -C $RWT merge --ff-only main" "dry-run prints the ff-merge step with real paths"
assert_contains "$out" "$RWT/bin/mnemon doctor" "dry-run prints the smoke step"
assert_contains "$out" "$RWT/bin/mnemon version (reports $(git -C "$REPO" rev-parse --short main))" "dry-run names the commit the smoke expects"
assert_contains "$out" "8. push:   git -C $RWT push origin release   (only after a green smoke)" "dry-run prints the push step after the smoke"
smoke_at=$(printf '%s\n' "$out" | grep -n ' smoke:' | head -1 | cut -d: -f1)
push_at=$(printf '%s\n' "$out" | grep -n ' push:' | head -1 | cut -d: -f1)
[[ -n "$smoke_at" && -n "$push_at" && "$smoke_at" -lt "$push_at" ]] && pass "dry-run orders smoke before push" || fail "dry-run orders smoke before push" "smoke line $smoke_at, push line $push_at"
assert_contains "$out" "unlock:    rm $LOCK" "dry-run prints the unlock step"
assert_contains "$out" "mnemon release --rollback" "dry-run prints the rollback command"
assert_contains "$out" "→ clean" "dry-run reports the dev tree as clean"
assert_contains "$out" "→ yes" "dry-run reports release as an ancestor of main"
assert_not_contains "$out" "node ping" "dry-run without --now says nothing about ping"
[[ -e "$LOCK" ]] && fail "dry-run takes no lock" "lock exists" || pass "dry-run takes no lock"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run --drain-timeout 7
assert_contains "$out" "wait up to 7s" "--drain-timeout is honoured"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --drain-timeout soon
assert_eq "$rc" "2" "--drain-timeout must be numeric"
run env MNEMON_HOME="$RWT" "$RWT/bin/mnemon" release --dry-run
assert_eq "$rc" "1" "release refuses to run from the installed engine itself"
assert_contains "$out" "run it from the dev tree" "...and says why"
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
printf 'pid=99999\ntimestamp=%s\ndate=2026-01-01T00:00:00Z\ntarget=abc1234\n' "$(date +%s)" > "$LOCK"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "75" "release refuses while another update holds a fresh lock"
assert_contains "$out" "another engine update holds $LOCK" "...naming the lock"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main~1)" "a refused release changes nothing"
rm -f "$LOCK"

run env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --now
assert_eq "$rc" "0" "release succeeds (lock + drain + ff-merge + smoke + push)"
assert_contains "$out" "release: lock $LOCK (pid " "release takes the lock"
assert_contains "$out" "release: drain — no engine processes running from $RWT/bin/" "release drains before moving files"
assert_contains "$out" "released $(git -C "$REPO" rev-parse --short main) → $RWT" "release prints the released sha and target"
assert_contains "$out" "node ping not implemented (program phase 4)" "release --now prints the phase-4 notice"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "release worktree now at main"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$(git -C "$REPO" rev-parse main)" "origin/release pushed"
assert_contains "$out" "release: smoke — $RWT/bin/mnemon config path" "release smoke resolves config"
assert_contains "$out" "mnemon $(git -C "$REPO" rev-parse --short main) (release, $(git -C "$REPO" rev-parse --short main)) $RWT" "release smoke: version reports the new commit"
assert_contains "$out" "ok config — " "release smoke ran doctor"
assert_contains "$out" "ok engine-lock — held by the running release" "release smoke: doctor does not warn about the release's own lock"
assert_not_contains "$out" "warn engine-lock" "release smoke: no warn engine-lock"
assert_contains "$out" "Results: " "release smoke ran test_config.sh"
assert_contains "$out" "release: lock removed ($LOCK)" "release removes the lock last"
[[ -e "$LOCK" ]] && fail "lock removed after a successful release" "lock exists" || pass "lock removed after a successful release"

# a failing smoke never publishes: nothing pushed, rollback printed, lock removed
PREV_REL=$(git -C "$RWT" rev-parse --short HEAD)
PREV_FULL=$(git -C "$RWT" rev-parse HEAD)
printf '# second change\n' >> "$REPO/tests/test-helper.sh"
g "$REPO" commit -q -am "second change on main"
NEW_MAIN=$(git -C "$REPO" rev-parse --short main)
run env MNEMON_TEST_HOOKS=1 MNEMON_SMOKE_CMD=false MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "a failing smoke fails the release"
assert_contains "$out" "MNEMON_SMOKE_CMD override (MNEMON_TEST_HOOKS=1): false" "the smoke override is honoured with MNEMON_TEST_HOOKS=1"
assert_contains "$out" "smoke failed on $RWT @ $NEW_MAIN. Nothing was pushed" "smoke failure says nothing was pushed"
assert_contains "$out" "mnemon release --rollback $PREV_REL" "smoke failure prints the rollback command"
assert_contains "$out" "git -C $RWT reset --hard $PREV_REL" "smoke failure prints the reset"
assert_contains "$out" "git -C $RWT push --force-with-lease origin release" "smoke failure prints the force-with-lease push"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$PREV_FULL" "a failing smoke does not move origin/release"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "the worktree stays at the new commit until an explicit rollback"
[[ -e "$LOCK" ]] && fail "lock removed after a failed smoke" "lock exists" || pass "lock removed after a failed smoke"

# --rollback <ref>: reset under the lock, print (not run) the force-with-lease push
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --rollback "$PREV_REL"
assert_eq "$rc" "0" "release --rollback exits 0"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$PREV_FULL" "--rollback resets the engine worktree to the ref"
assert_contains "$out" "rolled back $RWT to $PREV_REL" "--rollback reports the new HEAD"
assert_contains "$out" "git -C $RWT push --force-with-lease origin release" "--rollback prints the push instead of running it"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$PREV_FULL" "--rollback does not push"
[[ -e "$LOCK" ]] && fail "lock removed after --rollback" "lock exists" || pass "lock removed after --rollback"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --rollback nosuchref
assert_eq "$rc" "1" "--rollback with an unknown ref fails"
assert_contains "$out" "unknown ref 'nosuchref'" "...and names it"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --rollback "$PREV_REL" --dry-run
assert_contains "$out" "3. reset:  git -C $RWT reset --hard $PREV_REL" "--rollback --dry-run prints the reset step"

# drain: an engine process still running from $MNEMON_HOME/bin/ blocks the release until --drain-timeout
printf '#!/bin/sh\nsleep 3\n' > "$RWT/bin/busy.sh"
bash "$RWT/bin/busy.sh" &
BUSY_PID=$!
sleep 0.3
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --drain-timeout 1
assert_eq "$rc" "1" "release fails when engine processes outlive --drain-timeout"
assert_contains "$out" "release: drain — waiting up to 1s for engine processes to finish ($BUSY_PID" "drain names the running process"
assert_contains "$out" "engine busy after 1s" "drain timeout message"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$PREV_FULL" "a drain timeout touches no files"
[[ -e "$LOCK" ]] && fail "lock removed after a drain timeout" "lock exists" || pass "lock removed after a drain timeout"
wait "$BUSY_PID" 2>/dev/null || true

# F2: MNEMON_HOME is normalized before the drain pattern is built — a trailing
# slash used to yield "engine//bin/", which matches nothing (the drain failed open)
run env MNEMON_HOME="$RWT/" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "0" "release --dry-run accepts MNEMON_HOME with a trailing slash"
assert_contains "$out" "installed: $RWT (release @ $PREV_REL)" "trailing slash: MNEMON_HOME is reported normalized"
assert_contains "$out" "pgrep -f $RWT_RE/bin/" "trailing slash: the drain pattern has one slash before bin/"
assert_not_contains "$out" "//bin/" "trailing slash: no // in the drain pattern"
bash "$RWT/bin/busy.sh" &
BUSY_PID=$!
sleep 0.3
run env MNEMON_HOME="$RWT/" "$REPO/bin/mnemon" release --drain-timeout 1
assert_eq "$rc" "1" "trailing slash: a process running from \$MNEMON_HOME/bin/ is still detected"
assert_contains "$out" "waiting up to 1s for engine processes to finish ($BUSY_PID" "trailing slash: drain names the running process"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$PREV_FULL" "trailing slash: the drain timeout touches no files"
wait "$BUSY_PID" 2>/dev/null || true
# a symlinked MNEMON_HOME with a regex metacharacter in its name: the pattern
# carries both spellings (a process started through the symlink has the symlink
# path on its command line), each escaped
ln -s "$RWT" "$TEST_TMPDIR/eng.ine"
ENG_RE="$(re_escape "$TEST_TMPDIR/eng.ine")"
run env MNEMON_HOME="$TEST_TMPDIR/eng.ine/" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "0" "release --dry-run accepts a symlinked MNEMON_HOME"
assert_contains "$out" "installed: $RWT (release @ $PREV_REL; MNEMON_HOME=$TEST_TMPDIR/eng.ine/)" "symlink: the physical path is reported next to the given MNEMON_HOME"
assert_contains "$out" "pgrep -f ($RWT_RE|$ENG_RE)/bin/" "symlink: the drain pattern carries both spellings, metacharacters escaped"
assert_contains "$out" 'eng\.ine' "symlink: the '.' in the name is escaped"
bash "$TEST_TMPDIR/eng.ine/bin/busy.sh" &
BUSY_PID=$!
sleep 0.3
run env MNEMON_HOME="$TEST_TMPDIR/eng.ine/" "$REPO/bin/mnemon" release --drain-timeout 1
assert_eq "$rc" "1" "symlink: a process started through the symlink spelling is detected"
assert_contains "$out" "engine processes to finish ($BUSY_PID" "symlink: drain names it"
wait "$BUSY_PID" 2>/dev/null || true
rm -f "$RWT/bin/busy.sh" "$TEST_TMPDIR/eng.ine"

# with the engine idle again, the pending main commit releases cleanly; a stray
# MNEMON_SMOKE_CMD without MNEMON_TEST_HOOKS=1 is ignored (F5), the real smoke runs
run env PATH="$STUBBIN:$PATH" MNEMON_SMOKE_CMD=false MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "0" "release succeeds once the engine is idle (MNEMON_SMOKE_CMD without MNEMON_TEST_HOOKS is ignored)"
assert_contains "$out" "ignoring MNEMON_SMOKE_CMD" "...with a one-line notice"
assert_contains "$out" "release: smoke — $RWT/bin/mnemon config path" "...and the real smoke ran"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "release worktree at the new main"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$(git -C "$REPO" rev-parse main)" "origin/release pushed after the green smoke"

# F5: a failed push is not a warning — the local release stands but nodes will not see it: exit 3
printf '# third change\n' >> "$REPO/tests/test-helper.sh"
g "$REPO" commit -q -am "third change on main"
THIRD_MAIN=$(git -C "$REPO" rev-parse --short main)
BEFORE_PUSH=$(git -C "$ORIGIN" rev-parse release)
git -C "$REPO" remote set-url origin "$TEST_TMPDIR/no-such-origin.git"
run env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "3" "a failed push exits 3"
assert_contains "$out" "released locally to $RWT, push failed: nodes will not see $THIRD_MAIN" "push failure names the engine and the sha nodes will miss"
assert_contains "$out" "git -C $RWT push origin release" "push failure prints the push to retry by hand"
assert_not_contains "$out" "released $THIRD_MAIN →" "push failure does not claim a release"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "the local release stays after a failed push"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$BEFORE_PUSH" "origin/release did not move"
[[ -e "$LOCK" ]] && fail "lock removed after a failed push" "lock exists" || pass "lock removed after a failed push"
git -C "$REPO" remote set-url origin "$ORIGIN"

printf '# hotfix on release\n' >> "$RWT/tests/test-helper.sh"
g "$RWT" commit -q -am "hotfix on release"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release fails when release has diverged"
assert_contains "$out" "release has diverged from main" "...with the specified message"

cd /
rm -rf "$TEST_TMPDIR"
summary
