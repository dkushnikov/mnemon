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
for c in scan monitor; do
  run "$MNEMON" "$c"
  assert_eq "$rc" "2" "$c exits 2"
  assert_contains "$out" "mnemon $c: not implemented yet (program phase 4)" "$c reports not implemented"
done
run "$MNEMON" ingest
assert_eq "$rc" "2" "ingest without an inbox dir exits 2 (a usage error needs no config)"
assert_contains "$out" "inbox-dir" "ingest names the missing argument"
run "$MNEMON" ingest --help
assert_eq "$rc" "0" "ingest --help exits 0 without a config"
assert_contains "$out" "--max-attempts" "ingest --help documents its options"
if [[ -f "$MNEMON_ROOT/bin/fence-probe.sh" ]]; then
  echo "  ○ skipped: bin/fence-probe.sh is present (the real probe is never run by this suite)"
else
  run "$MNEMON" fence-probe
  assert_eq "$rc" "2" "fence-probe without bin/fence-probe.sh exits 2"
  assert_contains "$out" "mnemon fence-probe: bin/fence-probe.sh not present in this engine" "...with the message"
fi
run "$MNEMON" help
assert_contains "$out" "ingest <inbox-dir>" "help lists ingest"
assert_contains "$out" "fence-probe" "help lists fence-probe"
assert_contains "$out" "scan | monitor             Reserved" "help keeps scan and monitor reserved"
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

# A8: --search-provider, --reader-context-path, and an existing vault's own reader context
echo "--- init: existing vault (A8) ---"
run "$MNEMON" init sp --vault "$TEST_TMPDIR/vault-sp" --search-provider qmd --non-interactive
assert_eq "$rc" "0" "init --search-provider qmd exits 0"
assert_contains "$(cat "$CFG_DIR/instances/sp.yaml")" "search_provider: qmd" "--search-provider qmd is written bare (greppable)"
run "$MNEMON" --instance sp config get search_provider
assert_eq "$out" "qmd" "config get search_provider → qmd"
run "$MNEMON" config get search_provider
assert_eq "$out" "grep" "without the flag the search provider is grep"
run "$MNEMON" init sp2 --vault "$TEST_TMPDIR/vault-sp2" --search-provider sqlite --non-interactive
assert_eq "$rc" "2" "init rejects an unknown --search-provider"
[[ -e "$TEST_TMPDIR/vault-sp2" ]] && fail "a rejected --search-provider scaffolds nothing" "vault-sp2 exists" || pass "a rejected --search-provider scaffolds nothing"

printf '# profile kept elsewhere\n' > "$TEST_TMPDIR/elsewhere-rc.md"
run "$MNEMON" init rp --vault "$TEST_TMPDIR/vault-rp" --reader-context-path "$TEST_TMPDIR/elsewhere-rc.md" --non-interactive
assert_eq "$rc" "0" "init --reader-context-path exits 0"
assert_contains "$out" "referenced in place, nothing copied" "--reader-context-path says it copies nothing"
assert_contains "$(cat "$CFG_DIR/instances/rp.yaml")" "reader_context_path: \"$TEST_TMPDIR/elsewhere-rc.md\"" "reader_context_path points at the given file"
[[ -e "$TEST_TMPDIR/vault-rp/reader-context.md" ]] && fail "--reader-context-path places no template in the vault" "exists" || pass "--reader-context-path places no template in the vault"
run "$MNEMON" --instance rp config get reader_context_path
assert_eq "$out" "$TEST_TMPDIR/elsewhere-rc.md" "config get reader_context_path → the referenced file"
run "$MNEMON" --instance rp doctor
assert_contains "$out" "ok reader-context — $TEST_TMPDIR/elsewhere-rc.md" "doctor finds the referenced reader context"
run "$MNEMON" init rp2 --vault "$TEST_TMPDIR/vault-rp2" --reader-context-path "$TEST_TMPDIR/nope.md" --non-interactive
assert_eq "$rc" "1" "init --reader-context-path refuses a missing file"
assert_contains "$out" "no such file: $TEST_TMPDIR/nope.md" "...naming it"
[[ -e "$TEST_TMPDIR/vault-rp2" ]] && fail "a missing --reader-context-path refuses before the scaffold" "exists" || pass "a missing --reader-context-path refuses before the scaffold"
run "$MNEMON" init rp3 --vault "$TEST_TMPDIR/vault-rp3" --reader-context "$TEST_TMPDIR/rc.md" --reader-context-path "$TEST_TMPDIR/elsewhere-rc.md" --non-interactive
assert_eq "$rc" "2" "--reader-context together with --reader-context-path is a usage error"
assert_contains "$out" "exclude each other" "...and says so"

# an existing vault with its own _meta/reader-context.md: pointed at, template not placed
mkdir -p "$TEST_TMPDIR/vault-ex/_meta"
printf '# existing meta profile\n' > "$TEST_TMPDIR/vault-ex/_meta/reader-context.md"
run "$MNEMON" init ex --vault "$TEST_TMPDIR/vault-ex" --non-interactive
assert_eq "$rc" "0" "init on an existing vault exits 0"
assert_contains "$out" "existing _meta/reader-context.md (kept; template not placed)" "init reports the existing _meta/reader-context.md"
assert_contains "$(cat "$CFG_DIR/instances/ex.yaml")" "reader_context_path: \"$TEST_TMPDIR/vault-ex/_meta/reader-context.md\"" "reader_context_path points at the existing _meta file"
[[ -e "$TEST_TMPDIR/vault-ex/reader-context.md" ]] && fail "no template next to an existing _meta/reader-context.md" "exists" || pass "no template placed next to an existing _meta/reader-context.md"
assert_eq "$(cat "$TEST_TMPDIR/vault-ex/_meta/reader-context.md")" "# existing meta profile" "the existing _meta profile is untouched"
run "$MNEMON" --instance ex doctor
assert_contains "$out" "ok reader-context — $TEST_TMPDIR/vault-ex/_meta/reader-context.md" "doctor finds the _meta reader context"
# an existing vault with reader-context.md at its root: kept and pointed at
mkdir -p "$TEST_TMPDIR/vault-ex2"
printf '# existing root profile\n' > "$TEST_TMPDIR/vault-ex2/reader-context.md"
run "$MNEMON" init ex2 --vault "$TEST_TMPDIR/vault-ex2" --non-interactive
assert_eq "$rc" "0" "init on a vault with a root reader-context.md exits 0"
assert_contains "$out" "existing reader-context.md (kept)" "init reports the existing root reader-context.md"
assert_eq "$(cat "$TEST_TMPDIR/vault-ex2/reader-context.md")" "# existing root profile" "the existing root profile is untouched"
run "$MNEMON" --instance ex2 config get reader_context_path
assert_eq "$out" "$TEST_TMPDIR/vault-ex2/reader-context.md" "reader_context_path points at the existing root file"
# an explicit --reader-context still copies, even next to a _meta profile
run "$MNEMON" init ex3 --vault "$TEST_TMPDIR/vault-ex" --reader-context "$TEST_TMPDIR/rc.md" --non-interactive
assert_eq "$rc" "0" "init --reader-context on a vault with a _meta profile exits 0"
assert_eq "$(cat "$TEST_TMPDIR/vault-ex/reader-context.md")" "# my reader context" "--reader-context copies into the vault regardless of _meta/reader-context.md"
run "$MNEMON" --instance ex3 config get reader_context_path
assert_eq "$out" "$TEST_TMPDIR/vault-ex/reader-context.md" "...and reader_context_path points at the copy"

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
assert_contains "$out" "MNEMON_VAULT_STATE_DIR=$XDG_STATE_HOME/mnemon/vaults/" "config dump has MNEMON_VAULT_STATE_DIR (the vault's capture ledger, D6)"
assert_contains "$out" "EXTRACT_TIMEOUT=900" "config dump has the extract_* keys with their defaults"
assert_contains "$out" "EXTRACT_INLINE_MAX_BYTES=400000" "...extract_inline_max_bytes"
assert_contains "$out" "EXTRACT_MAX_USD=" "...extract_max_usd (unset)"
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
assert_not_contains "$out" "ok state — $XDG_STATE_HOME/mnemon/alpha (absent" "...without the absent note"
rm -rf "$XDG_STATE_HOME"
assert_contains "$out" "warn engine — MNEMON_HOME not set" "doctor: warn engine without MNEMON_HOME"
assert_contains "$out" "ok writer — writer_host not set" "doctor: ok writer when unset"
assert_contains "$out" "ok pending-writes — none" "doctor: ok pending-writes"
assert_contains "$out" " deps — " "doctor: deps line present"
assert_contains "$out" " deps-optional — " "doctor: deps-optional line present"
assert_contains "$out" " deps-python — " "doctor: deps-python line present (pyyaml)"
assert_eq "$(printf '%s\n' "$out" | grep ' deps-python — ' | grep -c 'requests' || true)" "0" "doctor: deps-python no longer names requests (capture layer §3.10)"
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
run "$MNEMON" init qinst --vault "$TEST_TMPDIR/vault-q" --collection kb-q --search-provider qmd --non-interactive
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

# --- doctor: capture-deps, egress, canonical (capture layer §3.10, §7.15, §12 A12) ---
echo "--- doctor: capture-deps / egress / canonical (phase 2) ---"
PY_VER="$(python3 --version 2>&1)"
echo "  toolchain: $PY_VER ($(command -v python3)) · bash $BASH_VERSION"
# the legacy run is the PATH prefix /usr/bin:/bin (test-helper.sh), never "python3 is /usr/bin/python3" —
# on ubuntu that is the current toolchain
assert_toolchain_legacy
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY
CSTUB="$TEST_TMPDIR/capstub"; mkdir -p "$CSTUB"
mk_stub() { printf '#!/bin/sh\n%s\n' "$2" > "$CSTUB/$1"; chmod +x "$CSTUB/$1"; }
mk_stub defuddle 'echo 0.8.0'
mk_stub pandoc 'echo "pandoc 3.9.0.2"'
mk_stub pdftotext 'echo "pdftotext version 26.04.0" >&2'
mk_stub pdfinfo 'exit 0'
mk_stub google-chrome 'echo "Google Chrome 130.0.6723.91"'
run env PATH="$CSTUB:/usr/bin:/bin" "$MNEMON" doctor
assert_contains "$out" "ok capture-deps — defuddle 0.8.0 · pandoc 3.9.0.2 · chrome Google Chrome " "doctor: capture-deps ok with versions (defuddle ≥ 0.6, pandoc ≥ 3, Chrome by render-url.sh's candidates)"
assert_contains "$out" " · poppler 26.04.0" "...and Poppler's version"
mk_stub defuddle 'echo 0.5.2'
mk_stub pandoc 'echo "pandoc 2.19.2"'
rm -f "$CSTUB/pdftotext"
run env PATH="$CSTUB:/usr/bin:/bin" "$MNEMON" doctor
assert_contains "$out" "warn capture-deps — defuddle 0.5.2 (< 0.6 — upgrade: npm install -g defuddle) · pandoc 2.19.2 (< 3 — upgrade: brew install pandoc)" "doctor: capture-deps warns below the version floors"
assert_contains "$out" " · poppler missing (PDF captures refused: brew install poppler)" "...and on a missing Poppler"
rm -f "$CSTUB/defuddle" "$CSTUB/pandoc"
run env PATH="$CSTUB:/usr/bin:/bin" "$MNEMON" doctor
assert_contains "$out" "warn capture-deps — defuddle missing (pandoc and the DOM text remain as converters) · pandoc missing (html conversion falls back to the DOM text)" "doctor: capture-deps names missing converters"
assert_eq "$(printf '%s\n' "$out" | grep -c '^fail capture-deps' || true)" "0" "doctor: capture-deps never fails (every tool is optional)"

run "$MNEMON" doctor
assert_contains "$out" "ok egress — external_fetch off (never past the local chain); firecrawl: FIRECRAWL_API_KEY not set · jina: JINA_API_KEY not set" "doctor: egress off by default, every fetcher's key reported as not set"
run env FIRECRAWL_API_KEY=secret-firecrawl-key-123 "$MNEMON" doctor
assert_contains "$out" "firecrawl: FIRECRAWL_API_KEY set · jina: JINA_API_KEY not set" "doctor: egress reports a set key"
assert_not_contains "$out" "secret-firecrawl-key-123" "...and never prints the key"
printf 'external_fetch: auto\n' >> "$CFG_DIR/instances/alpha.yaml"
run "$MNEMON" doctor
assert_contains "$out" "warn egress — external_fetch auto but no fetcher has a key (external stage always skipped); firecrawl: FIRECRAWL_API_KEY not set · jina: JINA_API_KEY not set" "doctor: egress auto without any key warns"
run env JINA_API_KEY=secret-jina-key-456 "$MNEMON" doctor
assert_contains "$out" "ok egress — external_fetch auto (after direct and rendered fail on a bot wall, a 403 or a JavaScript shell); firecrawl: FIRECRAWL_API_KEY not set · jina: JINA_API_KEY set" "doctor: egress auto with a key is ok"
assert_not_contains "$out" "secret-jina-key-456" "...key never printed"
printf 'external_fetchers: jina\n' >> "$CFG_DIR/instances/alpha.yaml"
run "$MNEMON" doctor
assert_contains "$out" "; jina: JINA_API_KEY not set" "doctor: egress lists the configured fetchers"
assert_not_contains "$out" "firecrawl:" "...and only those"
sed -i.bak '/^external_fetch/d' "$CFG_DIR/instances/alpha.yaml"; rm -f "$CFG_DIR/instances/alpha.yaml.bak"

run "$MNEMON" doctor
assert_contains "$out" " canonical — canonicalization v1" "doctor: the canonical line names canonicalization v1"
if [[ -f "$MNEMON_ROOT/bin/capture-fetch.py" ]]; then
  assert_contains "$out" "ok canonical — canonicalization v1 (capture-fetch.py --canonical-only)" "...answered by capture-fetch.py --canonical-only"
else
  assert_contains "$out" "warn canonical — canonicalization v1 expected, but bin/capture-fetch.py is not present" "...warns while the capture layer is absent from this engine"
fi
run "$MNEMON" doctor --json
names=$(printf '%s' "$out" | jget '" ".join(c["name"] for c in d["checks"])')
assert_contains "$names" "deps-python capture-deps egress canonical" "doctor --json lists the three phase-2 checks after deps-python"
run "$MNEMON" help
assert_contains "$out" "audit --canonical" "help lists audit --canonical"
assert_contains "$out" "reindex [--no-hand-edit-report]" "help lists reindex --no-hand-edit-report"

# --- audit --canonical dispatch: a copy of bin/ with a stub canonical-audit.py (the real one is the capture layer's) ---
echo "--- audit --canonical dispatch ---"
ENG="$TEST_TMPDIR/engine"; mkdir -p "$ENG"
cp -R "$MNEMON_ROOT/bin" "$ENG/bin"
printf 'import sys\nprint("canonical-audit argv: " + " ".join(sys.argv[1:]))\n' > "$ENG/bin/canonical-audit.py"
run "$ENG/bin/mnemon" audit --canonical
assert_eq "$rc" "0" "audit --canonical exits 0 (stub)"
assert_eq "$out" "canonical-audit argv: --vault $VAULT_A" "audit --canonical runs canonical-audit.py --vault <vault>"
run "$ENG/bin/mnemon" audit --canonical --json
assert_eq "$out" "canonical-audit argv: --vault $VAULT_A --json" "...passing the other arguments through"
run "$ENG/bin/mnemon" --instance beta audit --json --canonical
assert_eq "$out" "canonical-audit argv: --vault $VAULT_B --json" "...for the selected instance, the flag anywhere"
run "$ENG/bin/mnemon" audit
assert_not_contains "$out" "canonical-audit" "plain audit still runs archive-audit.py"
rm -rf "$ENG"

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

# --- doctor: the capture ledger (capture contracts §7) — fence, vault-state, vault-lock, captures, ingest ---
echo "--- doctor: fence ---"
# fence — a stub claude with --version / --help variants and a probe record (observational)
FSTUB="$TEST_TMPDIR/fencestub"; mkdir -p "$FSTUB"
make_claude() {  # make_claude <--version line> <--help text>
  printf '#!/bin/sh\ncase "$1" in --version) echo "%s" ;; --help) echo "%s" ;; esac\nexit 0\n' "$1" "$2" > "$FSTUB/claude"
  chmod +x "$FSTUB/claude"
}
PROBES="$XDG_STATE_HOME/mnemon/fence-probes.json"
mkdir -p "$(dirname "$PROBES")"
rm -f "$PROBES"
make_claude "2.1.300 (Claude Code)" "Options: --restricted  --tools <list>"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "warn fence — claude 2.1.300 has no probe record; run: mnemon fence-probe" "doctor: fence warns when no probe record exists for the running version"
printf '{"2.1.300": {"pass": true, "date": "2026-10-15", "probes": {}}}\n' > "$PROBES"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "ok fence — claude 2.1.300, --restricted available, probed 2026-10-15" "doctor: fence ok with a pass record for the exact version"
printf '{"2.1.300 (Claude Code)": {"pass": true, "date": "2026-10-16", "probes": {}}}\n' > "$PROBES"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "ok fence — claude 2.1.300, --restricted available, probed 2026-10-16" "doctor: a record keyed by the full --version line counts too"
printf '{"2.1.300": {"pass": false, "date": "2026-10-15", "probes": {}}}\n' > "$PROBES"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "warn fence — claude 2.1.300 failed its fence probe on 2026-10-15; run: mnemon fence-probe" "doctor: a failed probe record warns"
printf '{"2.1.299": {"pass": true, "date": "2026-10-15", "probes": {}}}\n' > "$PROBES"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "warn fence — claude 2.1.300 has no probe record; run: mnemon fence-probe" "doctor: a record for another version does not count"
printf '{"2.1.300": {"pass": true, "date": "2026-10-15", "model": "sonnet", "probes": {"no-tools": true, "ambient-project": true, "ambient-user": "skipped", "mcp": true, "session": true}, "skipped": {"ambient-user": "a second login would be needed"}}}\n' > "$PROBES"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "ok fence — claude 2.1.300, --restricted available, probed 2026-10-15 — probed: no-tools, ambient-project, mcp, session; ambient-user: documented" "doctor: fence prints the probed list and the documented (skipped) probe from the record (A9)"
# the probe script itself (never run here): every claude call is under the real HOME, probe 3 is recorded as skipped
assert_eq "$(grep -cE '(^|[[:space:]])(HOME|XDG_CONFIG_HOME|XDG_STATE_HOME)=' "$MNEMON_ROOT/bin/fence-probe.sh" || true)" "0" "fence-probe.sh never overrides HOME or XDG_* for claude (A9: the scratch dir is the cwd only)"
assert_contains "$(grep -E '^record ambient-user' "$MNEMON_ROOT/bin/fence-probe.sh")" "skipped" "fence-probe.sh records ambient-user as skipped (A9)"
assert_eq "$(grep -c 'SCRATCH_HOME' "$MNEMON_ROOT/bin/fence-probe.sh" || true)" "0" "fence-probe.sh plants nothing in a scratch HOME"
make_claude "2.1.200 (Claude Code)" "Options: --tools <list>"
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_eq "$rc" "1" "doctor fails when claude lacks --restricted"
assert_contains "$out" "fail fence — claude 2.1.200 lacks --restricted (>= 2.1.296 required)" "doctor: fence fails without --restricted"
make_claude "" ""
run env PATH="$FSTUB:$PATH" "$MNEMON" doctor
assert_contains "$out" "warn fence — claude --version printed no version token" "doctor: a claude that prints no version warns, never fails"
if env PATH=/usr/bin:/bin sh -c 'command -v claude' >/dev/null 2>&1; then
  echo "  ○ skipped fence-without-claude: claude is on /usr/bin:/bin"
else
  run env PATH="/usr/bin:/bin" "$MNEMON" doctor
  assert_contains "$out" "warn fence — claude not on PATH (see deps)" "doctor: fence warns (deps fails) when claude is missing"
fi
rm -f "$PROBES"

echo "--- doctor: vault-state, vault-lock ---"
# D6: <XDG_STATE_HOME>/mnemon/vaults/<sha256(physical vault path)[:16]>
VSTATE_A="$(python3 -c 'import hashlib, os, sys; print(os.path.join(os.environ["XDG_STATE_HOME"], "mnemon", "vaults", hashlib.sha256(os.path.realpath(sys.argv[1]).encode("utf-8")).hexdigest()[:16]))' "$VAULT_A")"
run "$MNEMON" doctor
assert_contains "$out" "ok vault-state — $VSTATE_A (absent, parent writable)" "doctor: vault-state ok when absent and creatable"
[[ -e "$VSTATE_A" ]] && fail "doctor creates no vault state dir" "created" || pass "doctor creates no vault state dir"
assert_contains "$out" "ok vault-lock — free (no $VSTATE_A/lock yet)" "doctor: vault-lock free when no lock file exists"
[[ -e "$VSTATE_A/lock" ]] && fail "doctor creates no lock file" "created" || pass "doctor creates no lock file"
assert_contains "$out" "ok captures — none (no $VSTATE_A/captures yet)" "doctor: captures none without a ledger"
assert_contains "$out" "ok ingest — none (no $VSTATE_A/ingest yet)" "doctor: ingest none without inbox state"
mkdir -p "$VSTATE_A" && chmod 0700 "$VSTATE_A"
run "$MNEMON" doctor
assert_contains "$out" "ok vault-state — $VSTATE_A" "doctor: vault-state ok when present with mode 0700"
assert_not_contains "$out" "vault-state — $VSTATE_A (absent" "...without the absent note"
chmod 0755 "$VSTATE_A"
run "$MNEMON" doctor
assert_contains "$out" "warn vault-state — $VSTATE_A mode 0755, expected 0700" "doctor: vault-state warns on a loose mode"
chmod 0700 "$VSTATE_A"
touch "$VSTATE_A/lock"
run "$MNEMON" doctor
assert_contains "$out" "ok vault-lock — free ($VSTATE_A/lock)" "doctor: vault-lock free when the file exists unlocked"
python3 - "$VSTATE_A/lock" "$TEST_TMPDIR/held-cli" <<'PY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
open(sys.argv[2], "w").close()
time.sleep(30)
PY
HOLDER=$!
wait_file "$TEST_TMPDIR/held-cli"
run "$MNEMON" doctor
assert_contains "$out" "warn vault-lock — held ($VSTATE_A/lock) — a capture or an ingest pass is running" "doctor: vault-lock held while another process holds flock"
assert_eq "$(printf '%s\n' "$out" | grep -c '^fail vault-lock' || true)" "0" "...and a held vault lock is a warning, not a failure"
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null || true
run "$MNEMON" doctor
assert_contains "$out" "ok vault-lock — free ($VSTATE_A/lock)" "doctor: vault-lock free again once released"

echo "--- doctor: captures, ingest ---"
mkdir -p "$VSTATE_A/captures" "$VAULT_A/Sources/2026-10-15_aaaaaaaa" "$VAULT_A/Sources/2026-10-15_bbbbbbbb"
printf -- '---\ntype: source\n---\nbody\n' > "$VAULT_A/Sources/2026-10-15_aaaaaaaa/source.md"
receipt() { printf '{"schema":"mnemon-capture-receipt/1","capture_id":"%s","state":"%s","revision":%s}\n' "$1" "$2" "$3" > "$VSTATE_A/captures/$1.json"; }
receipt c-ok extracted '"2026-10-15_aaaaaaaa"'
run "$MNEMON" doctor
assert_contains "$out" "ok captures — 1 receipt(s), none failed" "doctor: captures ok with a complete receipt whose files exist"
receipt c-failed failed null
receipt c-xfailed extract-failed '"2026-10-15_aaaaaaaa"'
receipt c-gone source-published '"2026-10-15_zzzzzzzz"'
receipt c-nosrc extracted '"2026-10-15_bbbbbbbb"'
receipt c-card bookmarked '"2026-10-15_aaaaaaaa"'      # a card whose source.md exists: fine
receipt c-card-gone bookmarked '"2026-10-15_yyyyyyyy"' # a card whose folder is gone: missing (phase 3-capture)
printf 'not a receipt\n' > "$VSTATE_A/captures/junk.json"
run "$MNEMON" doctor
assert_contains "$out" "warn captures — 2 failed/extract-failed receipt(s) — finish with: mnemon add --resume c-failed; 3 receipt(s) point at missing files; 1 unreadable receipt(s)" "doctor: captures warns about failed receipts (with the --resume hint), missing files (a bookmarked receipt's gone card among them) and unreadable receipts"
assert_eq "$(printf '%s\n' "$out" | grep -c '^fail captures' || true)" "0" "...as a warning, never a failure"
receipt c-integ integrity-error '"2026-10-15_aaaaaaaa"'
run "$MNEMON" doctor
assert_contains "$out" "; 1 integrity-error receipt(s) — a published source.md is missing or was edited (operator: restore it, then replay); 3 receipt(s) point at missing files" "doctor: captures counts integrity-error receipts (A3) as an operator item, apart from the --resume hint"
rm -rf "$VSTATE_A/captures" "$VAULT_A/Sources/2026-10-15_aaaaaaaa" "$VAULT_A/Sources/2026-10-15_bbbbbbbb"
# §12 A2 — receipt-independent: a source.md whose header is never closed is malformed-frontmatter
mkdir -p "$VAULT_A/Sources/2026-10-15_mmmmmmmm" "$VAULT_A/Sources/2026-10-15_nnnnnnnn"
printf -- '---\ntype: source\nurl: "https://example.com/open"\n' > "$VAULT_A/Sources/2026-10-15_mmmmmmmm/source.md"
printf -- '---\ntype: source\n---\nbody\n' > "$VAULT_A/Sources/2026-10-15_nnnnnnnn/source.md"
run "$MNEMON" doctor
assert_contains "$out" "warn captures — none (no $VSTATE_A/captures yet); 1 malformed-frontmatter folder(s) — source.md header without a closing --- within 256 KB, unreadable to the lookup: 2026-10-15_mmmmmmmm" "doctor: captures lists malformed-frontmatter folders without a ledger (A2)"
assert_not_contains "$out" "2026-10-15_nnnnnnnn" "...a closed header is not listed"
mkdir -p "$VSTATE_A/captures"
run "$MNEMON" doctor
assert_contains "$out" "warn captures — 1 malformed-frontmatter folder(s)" "doctor: ...and with a ledger"
rm -rf "$VSTATE_A/captures" "$VAULT_A/Sources/2026-10-15_mmmmmmmm" "$VAULT_A/Sources/2026-10-15_nnnnnnnn"
IRES="$VSTATE_A/ingest/abcd1234/results"
mkdir -p "$IRES"
printf '{"inbox": "/srv/inbox-a", "instance": "alpha"}\n' > "$VSTATE_A/ingest/abcd1234/inbox.json"
result() { printf '{"schema":"mnemon-ingest-result/1","envelope_id":"%s","state":"%s"}\n' "$1" "$2" > "$IRES/$1.json"; }
result p:1 captured
run "$MNEMON" doctor
assert_contains "$out" "ok ingest — 1 result(s), nothing pending" "doctor: ingest ok when every result is settled"
result p:2 failed; result p:3 failed; result p:4 exhausted; result p:5 incomplete; result p:6 deferred; result p:7 quarantined
run "$MNEMON" doctor
assert_contains "$out" "warn ingest — /srv/inbox-a: failed 2, exhausted 1, incomplete 1, deferred 1" "doctor: ingest warns with per-inbox counts, naming the inbox from inbox.json"
# permanent failures (failed, retryable false) are reported apart and never raise the status
printf '{"schema":"mnemon-ingest-result/1","envelope_id":"p:8","state":"failed","retryable":false,"error_class":"contract"}\n' > "$IRES/p:8.json"
run "$MNEMON" doctor
assert_contains "$out" "warn ingest — /srv/inbox-a: failed 2, exhausted 1, incomplete 1, deferred 1, permanent 1 (not retried)" "doctor: a permanent failure is counted apart as 'permanent N (not retried)', not under failed"
for p in p:2 p:3 p:4 p:5 p:6; do rm -f "$IRES/$p.json"; done
run "$MNEMON" doctor
assert_contains "$out" "ok ingest — 3 result(s), nothing pending; /srv/inbox-a: permanent 1 (not retried)" "doctor: an inbox whose only failures are permanent stays ok, with the permanent count in the detail"
result p:2 failed; result p:3 failed; result p:4 exhausted; result p:5 incomplete; result p:6 deferred
rm -f "$IRES/p:8.json"
mkdir -p "$VSTATE_A/ingest/ef012345/results"
printf '{"schema":"mnemon-ingest-result/1","envelope_id":"q:1","state":"deferred"}\n' > "$VSTATE_A/ingest/ef012345/results/q:1.json"
run "$MNEMON" doctor
assert_contains "$out" "; ef012345: deferred 1" "doctor: an inbox without inbox.json is named by its key"
run "$MNEMON" doctor --json
names=$(printf '%s' "$out" | jget '" ".join(c["name"] for c in d["checks"])')
assert_contains "$names" "engine-lock fence writer pending-writes vault-state vault-lock captures ingest" "doctor --json lists the ledger checks after the existing ones"
rm -rf "$VSTATE_A"

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
# a stray test-hook variable in the environment, the master switch unset (§3.2): the warning that
# names it reaches the user on stderr and never the canonical JSON the gateway parses (G2)
rc=0; MNEMON_POLICY_ALLOW_LOOPBACK=1 "$MNEMON" add --url "https://example.com/" --dry-run >"$TEST_TMPDIR/warn.out" 2>"$TEST_TMPDIR/warn.err" || rc=$?
assert_eq "$rc" "0" "add --dry-run with MNEMON_POLICY_ALLOW_LOOPBACK=1 exported and MNEMON_TEST_HOOKS unset exits 0"
assert_contains "$(cat "$TEST_TMPDIR/warn.err")" "WARNING: MNEMON_POLICY_ALLOW_LOOPBACK ignored: MNEMON_TEST_HOOKS=1 is not set" "...the ignored-hook warning is on stderr"
assert_contains "$(cat "$TEST_TMPDIR/warn.out")" "DRY RUN" "...and the dry run proceeds: the warning never reached the JSON parser"
run "$MNEMON" reindex
assert_eq "$rc" "0" "reindex via dispatcher exits 0"
assert_contains "$out" "reindex:" "reindex runs knowledge-reindex.py on the vault"
run "$MNEMON" --instance beta audit
assert_eq "$rc" "0" "audit via dispatcher exits 0"
assert_contains "$out" "GAP A" "audit runs archive-audit.py with the instance config"
run python3 "$MNEMON_ROOT/bin/archive-audit.py" --cutoff 2026-01-01
assert_eq "$rc" "0" "archive-audit.py resolves config through the engine when --config is absent"
# the URL-list triage spike was retired (GH #29): `triage <file>` is an unknown subcommand, usage error
printf 'https://example.com/a\n' > "$TEST_TMPDIR/urls.txt"
run "$MNEMON" triage "$TEST_TMPDIR/urls.txt"
assert_eq "$rc" "2" "triage <file> is an unknown subcommand (the URL-list spike is retired)"
assert_contains "$out" "triage: expected list | dismiss <rev> | restore <rev> | promote <rev>" "...naming the four verbs"
run "$MNEMON" triage
assert_eq "$rc" "2" "triage without a verb is a usage error"
[[ -e "$MNEMON_ROOT/bin/triage-urls.py" ]] && fail "bin/triage-urls.py is gone" "still present" || pass "bin/triage-urls.py is gone"
run "$MNEMON" --instance nosuch reindex
assert_eq "$rc" "2" "config-dependent subcommand fails with the loader's status (2: missing instance)"
assert_contains "$out" "instances/nosuch.yaml" "...naming the missing instance file"
run "$MNEMON" --instance nosuch ingest "$TEST_TMPDIR"
assert_eq "$rc" "2" "ingest with a missing instance fails with the loader's status"

# --- dispatch: add under bin/vault-lock.py, fence-probe, ingest (capture contracts §7) ---
# A copy of the dispatcher next to stub scripts: the gateway prints its argv, vault-lock.py
# records that it ran and execs its command, fence-probe.sh and mnemon-ingest.py echo.
echo "--- dispatch: vault-lock.py, fence-probe, ingest ---"
ENG="$TEST_TMPDIR/eng-copy"
mkdir -p "$ENG/bin"
cp "$MNEMON_ROOT/bin/mnemon" "$MNEMON_ROOT/bin/mnemon-config.sh" "$ENG/bin/"
printf '#!/bin/sh\necho "gateway-stub: $*"\nexit 0\n' > "$ENG/bin/knowledge-gateway.sh"
chmod +x "$ENG/bin/knowledge-gateway.sh"
run "$ENG/bin/mnemon" add --url https://example.com/a
assert_eq "$rc" "2" "add without bin/vault-lock.py is an error, never an unlocked capture"
assert_not_contains "$out" "gateway-stub:" "...the gateway does not run"
run "$ENG/bin/mnemon" add --url https://example.com/a --dry-run
assert_contains "$out" "gateway-stub: source-add --url https://example.com/a --dry-run" "add --dry-run: no wrapper (a dry run takes no lock), straight to the gateway"
cat > "$ENG/bin/vault-lock.py" <<'PY'
import os, sys
args = sys.argv[1:]
sep = args.index("--")
with open(os.environ["VL_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(args[:sep]) + "\n")
os.execv(args[sep + 1], args[sep + 1:])
PY
VL_LOG="$TEST_TMPDIR/vault-lock.log"
run env VL_LOG="$VL_LOG" "$ENG/bin/mnemon" add --url https://example.com/b --title T
assert_eq "$rc" "0" "add with bin/vault-lock.py present exits 0"
assert_eq "$(cat "$VL_LOG")" "--vault $VAULT_A" "add runs python3 bin/vault-lock.py --vault <vault> -- <gateway> source-add …"
assert_contains "$out" "gateway-stub: source-add --url https://example.com/b --title T" "...which execs the gateway with the same arguments"
run env VL_LOG="$VL_LOG" "$ENG/bin/mnemon" --instance beta add --url https://example.com/b2
assert_eq "$(tail -1 "$VL_LOG")" "--vault $VAULT_B" "add locks the selected instance's vault"
run env VL_LOG="$VL_LOG" "$ENG/bin/mnemon" add --url https://example.com/c --dry-run
assert_eq "$(grep -c . "$VL_LOG")" "2" "add --dry-run bypasses vault-lock.py"
assert_contains "$out" "gateway-stub: source-add --url https://example.com/c --dry-run" "...and reaches the gateway directly"
printf '#!/bin/sh\necho "fence-probe-stub: $*"\nexit 0\n' > "$ENG/bin/fence-probe.sh"
chmod +x "$ENG/bin/fence-probe.sh"
run "$ENG/bin/mnemon" fence-probe --quick
assert_eq "$rc" "0" "fence-probe with bin/fence-probe.sh present execs it"
assert_contains "$out" "fence-probe-stub: --quick" "...passing its arguments"
printf 'import os, sys\nprint("ingest-stub:", os.environ.get("MNEMON_INSTANCE"), os.environ.get("MNEMON_CONFIG") == os.environ.get("MNEMON_CONFIG_FILE"), sys.argv[1:])\n' > "$ENG/bin/mnemon-ingest.py"
run "$ENG/bin/mnemon" --instance beta ingest /some/inbox --limit 2
assert_eq "$rc" "0" "ingest resolves the instance and execs bin/mnemon-ingest.py"
assert_contains "$out" "ingest-stub: beta True ['/some/inbox', '--limit', '2']" "...with the resolved pair exported and the arguments passed through"
run "$ENG/bin/mnemon" ingest /some/inbox --instance beta
assert_eq "$rc" "2" "ingest refuses a selector after the command"
assert_contains "$out" "--instance belongs before the command" "...with the hint"

# --- release (temp dev repo + temp release worktree + temp bare origin) ---
echo "--- release ---"
REPO="$TEST_TMPDIR/dev"
RWT="$TEST_TMPDIR/engine"
ORIGIN="$TEST_TMPDIR/origin.git"
g() { local d="$1"; shift; git -C "$d" -c commit.gpgsign=false -c user.name="mnemon test" -c user.email="mnemon-test@example.invalid" "$@"; }
mkdir -p "$REPO/bin" "$REPO/tests"
# the smoke runs tests/test_config.sh, which reads the config key table from bin/lib_config.py too (§5)
cp "$MNEMON_ROOT/bin/mnemon" "$MNEMON_ROOT/bin/mnemon-config.sh" "$MNEMON_ROOT/bin/lib_config.py" "$REPO/bin/"
cp "$MNEMON_ROOT/tests/test-helper.sh" "$MNEMON_ROOT/tests/test_config.sh" "$REPO/tests/"
git init -q "$REPO"
git -C "$REPO" symbolic-ref HEAD refs/heads/main
# the fixture's own identity: `mnemon release` runs `git tag -a` in this repo (the worktree shares
# its config), and a CI runner has no global one; repo-local, so nothing outside the temp dir changes
git -C "$REPO" config user.name "mnemon test"
git -C "$REPO" config user.email "mnemon-test@example.invalid"
git -C "$REPO" config commit.gpgsign false
git -C "$REPO" config tag.gpgsign false
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
# the release date is frozen through the test hook (honoured only with MNEMON_TEST_HOOKS=1), so the
# tag names this fixture expects — release/2001-01-01, -2 — never depend on the clock
FROZEN=2001-01-01
hooked() { env MNEMON_TEST_HOOKS=1 MNEMON_RELEASE_DATE="$FROZEN" "$@"; }
run hooked env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_eq "$rc" "0" "release --dry-run exits 0"
assert_contains "$out" "1. lock:   $LOCK" "dry-run prints the lock step"
assert_contains "$out" "2. drain:  wait up to 120s for processes matching: pgrep -f $RWT_RE/bin/" "dry-run prints the drain step with the escaped pattern"
assert_contains "$out" "git -C $RWT merge --ff-only main" "dry-run prints the ff-merge step with real paths"
assert_contains "$out" "$RWT/bin/mnemon doctor" "dry-run prints the smoke step"
assert_contains "$out" "$RWT/bin/mnemon version (reports $(git -C "$REPO" rev-parse --short main))" "dry-run names the commit the smoke expects"
TODAY_TAG="release/$FROZEN"
assert_contains "$out" "6. tag:    git -C $RWT tag -a $TODAY_TAG -m 'mnemon release $TODAY_TAG' $(git -C "$REPO" rev-parse --short main)   (only after a green smoke)" "dry-run prints the tag step with the release name (the frozen date), after the smoke"
assert_contains "$out" "check:     git -C $RWT var GIT_COMMITTER_IDENT   → yes   (before the lock)" "dry-run lists the tagger-identity check among the pre-lock checks"
assert_contains "$out" "3. fetch:  git -C $REPO fetch origin --tags" "dry-run fetches tags too (a name taken on origin is seen before it is chosen)"
run env MNEMON_RELEASE_DATE="$FROZEN" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_contains "$out" "ignoring MNEMON_RELEASE_DATE" "MNEMON_RELEASE_DATE without MNEMON_TEST_HOOKS=1 is ignored, with a notice"
real_line="$(printf '%s\n' "$out" | grep '^  6. tag:' || true)"
[[ "$real_line" =~ tag\ -a\ release/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\  && "$real_line" != *"$FROZEN"* ]] && pass "...and the real date names the tag" || fail "...and the real date names the tag" "line: '$real_line'"
run hooked env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run   # back to the frozen date for the assertions below
assert_contains "$out" "7. push:   git -C $RWT push --atomic origin release refs/tags/$TODAY_TAG   (the branch and the tag together)" "dry-run prints the push of the branch and the tag together"
smoke_at=$(printf '%s\n' "$out" | grep -n ' smoke:' | head -1 | cut -d: -f1)
tag_at=$(printf '%s\n' "$out" | grep -n ' tag:' | head -1 | cut -d: -f1)
push_at=$(printf '%s\n' "$out" | grep -n ' push:' | head -1 | cut -d: -f1)
[[ -n "$smoke_at" && -n "$tag_at" && -n "$push_at" && "$smoke_at" -lt "$tag_at" && "$tag_at" -lt "$push_at" ]] && pass "dry-run orders smoke before tag before push" || fail "dry-run orders smoke before tag before push" "smoke line $smoke_at, tag line $tag_at, push line $push_at"
assert_eq "$(git -C "$REPO" tag -l 'release/*' | wc -l | tr -d ' ')" "0" "dry-run creates no tag"
# G3: the tree checks (dirty dev tree, release ⊂ main) need no lock — they are
# listed, and run, before the lock and the drain
check_at=$(printf '%s\n' "$out" | grep -n ' check: ' | head -1 | cut -d: -f1)
lock_at=$(printf '%s\n' "$out" | grep -n ' lock: ' | head -1 | cut -d: -f1)
[[ -n "$check_at" && -n "$lock_at" && "$check_at" -lt "$lock_at" ]] && pass "dry-run lists the tree checks before the lock" || fail "dry-run lists the tree checks before the lock" "check line $check_at, lock line $lock_at"
assert_contains "$out" "(before the lock)" "dry-run says the tree checks run before the lock"
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
# G3: the same refusals without --dry-run happen before the lock — the lock
# acquire always prints "release: lock …", so its absence proves no lock was taken
run env MNEMON_HOME="$REPO" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release (not dry-run) refuses a MNEMON_HOME that is not on branch release"
assert_not_contains "$out" "release: lock" "...before taking the lock"
run env MNEMON_HOME="$TEST_TMPDIR/notarepo" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release (not dry-run) refuses a MNEMON_HOME that is not a git worktree"
assert_not_contains "$out" "release: lock" "...before taking the lock"
[[ -e "$LOCK" ]] && fail "preflight refusals leave no lock file" "lock exists" || pass "preflight refusals leave no lock file"

printf '# dirty\n' >> "$REPO/tests/test-helper.sh"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release refuses a dirty dev tree"
assert_contains "$out" "uncommitted tracked changes" "...naming the reason"
assert_not_contains "$out" "release: lock" "...before taking the lock (G3)"
assert_not_contains "$out" "drain" "...and before the drain"
[[ -e "$LOCK" ]] && fail "a dirty-tree refusal leaves no lock file" "lock exists" || pass "a dirty-tree refusal leaves no lock file"
g "$REPO" checkout -q -- tests/test-helper.sh

# no tagger identity: refused before the lock (an annotated tag needs one). user.useConfigOnly stops
# git from guessing one from the account and the hostname, so the refusal is deterministic
git -C "$REPO" config user.useConfigOnly true
git -C "$REPO" config --unset user.name; git -C "$REPO" config --unset user.email
run env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_contains "$out" "check:     git -C $RWT var GIT_COMMITTER_IDENT   → NO — git has no committer identity" "dry-run reports a missing tagger identity"
run env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release refuses without a committer identity"
assert_contains "$out" "git has no committer identity for the release tag — set user.name and user.email" "...naming the fix"
assert_not_contains "$out" "release: lock" "...before taking the lock"
[[ -e "$LOCK" ]] && fail "an identity refusal leaves no lock file" "lock exists" || pass "an identity refusal leaves no lock file"
git -C "$REPO" config user.name "mnemon test"; git -C "$REPO" config user.email "mnemon-test@example.invalid"
git -C "$REPO" config --unset user.useConfigOnly

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

run hooked env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --now
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
# the release tag: release/<today>, annotated, on the released commit, pushed with the branch
REL1_SHORT="$(git -C "$REPO" rev-parse --short main)"
assert_eq "$(git -C "$REPO" tag -l 'release/*')" "$TODAY_TAG" "release creates the tag release/<date> — and no other"
assert_eq "$(git -C "$REPO" cat-file -t "$TODAY_TAG")" "tag" "...annotated"
assert_eq "$(git -C "$REPO" rev-parse "$TODAY_TAG^{commit}")" "$(git -C "$REPO" rev-parse main)" "...on the released commit"
assert_contains "$(git -C "$REPO" tag -l -n1 "$TODAY_TAG")" "mnemon release $TODAY_TAG: " "...with the release message"
assert_eq "$(git -C "$ORIGIN" rev-parse "refs/tags/$TODAY_TAG^{commit}")" "$(git -C "$REPO" rev-parse main)" "...pushed to origin with the branch"
assert_contains "$out" "release: tag $TODAY_TAG → $REL1_SHORT" "release prints the tag it created"
assert_contains "$out" "released $REL1_SHORT → $RWT ($TODAY_TAG)" "...and names it in the last line"
tag_line=$(printf '%s\n' "$out" | grep -n '^release: tag ' | head -1 | cut -d: -f1)
smoke_line=$(printf '%s\n' "$out" | grep -n '^release: smoke — ' | tail -1 | cut -d: -f1)
[[ -n "$tag_line" && -n "$smoke_line" && "$smoke_line" -lt "$tag_line" ]] && pass "the tag is created after the smoke" || fail "the tag is created after the smoke" "smoke line $smoke_line, tag line $tag_line"
run env MNEMON_HOME="$RWT" "$RWT/bin/mnemon" version
assert_eq "$rc" "0" "mnemon version on the released engine exits 0"
assert_contains "$out" "mnemon $TODAY_TAG (release, $REL1_SHORT) $RWT" "mnemon version shows the release tag, the branch and the commit"
run "$REPO/bin/mnemon" version
assert_contains "$out" "mnemon $TODAY_TAG (main, $REL1_SHORT) $REPO" "the dev tree at the same commit shows the same tag"

# a failing smoke never publishes: nothing pushed, rollback printed, lock removed
PREV_REL=$(git -C "$RWT" rev-parse --short HEAD)
PREV_FULL=$(git -C "$RWT" rev-parse HEAD)
printf '# second change\n' >> "$REPO/tests/test-helper.sh"
g "$REPO" commit -q -am "second change on main"
NEW_MAIN=$(git -C "$REPO" rev-parse --short main)
# the tag step itself can fail after a green smoke (here: tag.gpgsign with a gpg that fails) — exit 1
# with git's own message, nothing pushed, no tag, the rollback printed; the engine stays at the new commit
printf '#!/bin/sh\necho "gpg-stub: no secret key" >&2\nexit 2\n' > "$STUBBIN/gpg-fail"; chmod +x "$STUBBIN/gpg-fail"
git -C "$REPO" config tag.gpgsign true; git -C "$REPO" config gpg.program "$STUBBIN/gpg-fail"
run hooked env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "a tag that cannot be created fails the release"
assert_contains "$out" "could not create the tag $TODAY_TAG-2 on $NEW_MAIN — nothing was pushed; the engine stands at $NEW_MAIN" "...saying what happened and where the engine is"
assert_contains "$out" "  git: " "...with git's own message"
assert_contains "$out" "mnemon release --rollback $TODAY_TAG   # = $PREV_REL" "...and the rollback"
assert_eq "$(git -C "$REPO" tag -l 'release/*')" "$TODAY_TAG" "...no tag was created"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$PREV_FULL" "...origin/release did not move"
[[ -e "$LOCK" ]] && fail "lock removed after a failed tag" "lock exists" || pass "lock removed after a failed tag"
git -C "$REPO" config --unset tag.gpgsign; git -C "$REPO" config --unset gpg.program
git -C "$RWT" reset -q --hard "$PREV_FULL"
run hooked env MNEMON_SMOKE_CMD=false MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "a failing smoke fails the release"
assert_contains "$out" "MNEMON_SMOKE_CMD override (MNEMON_TEST_HOOKS=1): false" "the smoke override is honoured with MNEMON_TEST_HOOKS=1"
assert_contains "$out" "smoke failed on $RWT @ $NEW_MAIN. Nothing was pushed" "smoke failure says nothing was pushed"
assert_contains "$out" "mnemon release --rollback $TODAY_TAG   # = $PREV_REL" "smoke failure prints the rollback command, naming the previous release by its tag"
assert_contains "$out" "git -C $RWT reset --hard $PREV_REL" "smoke failure prints the reset"
assert_contains "$out" "git -C $RWT push --force-with-lease origin release" "smoke failure prints the force-with-lease push"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$PREV_FULL" "a failing smoke does not move origin/release"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "the worktree stays at the new commit until an explicit rollback"
assert_eq "$(git -C "$REPO" tag -l 'release/*')" "$TODAY_TAG" "a failing smoke creates no tag"
run env MNEMON_HOME="$RWT" "$RWT/bin/mnemon" version
assert_contains "$out" "mnemon $TODAY_TAG-1-g$NEW_MAIN (release, $NEW_MAIN) $RWT" "mnemon version past a release tag: the tag, the distance and the commit"
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

# --rollback <tag>: a release tag is a ref like any other
git -C "$RWT" merge -q --ff-only main
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "fixture: the engine worktree moved forward again"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --rollback "$TODAY_TAG"
assert_eq "$rc" "0" "release --rollback <release tag> exits 0"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$PREV_FULL" "--rollback by tag resets the engine worktree to the tagged commit"
assert_contains "$out" "release: rollback $RWT  $NEW_MAIN → $PREV_REL (ref $TODAY_TAG)" "--rollback names the ref it was given"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --rollback "$TODAY_TAG" --dry-run
assert_contains "$out" "3. reset:  git -C $RWT reset --hard $TODAY_TAG" "--rollback <tag> --dry-run prints the reset with the tag"
assert_eq "$(git -C "$REPO" tag -l 'release/*')" "$TODAY_TAG" "a rollback creates no tag and deletes none"

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

# G1: pgrep exit 0 = busy, 1 = idle, anything else = pgrep itself failed — the
# drain must abort, not read the failure as "idle" and move files under a
# running process. A stub pgrep first on PATH exits 3 while busy.sh runs.
PGSTUB="$TEST_TMPDIR/pgrepstub"; mkdir -p "$PGSTUB"
printf '#!/bin/sh\necho "pgrep: stub cannot read the process table" >&2\nexit 3\n' > "$PGSTUB/pgrep"
chmod +x "$PGSTUB/pgrep"
bash "$RWT/bin/busy.sh" &
BUSY_PID=$!
sleep 0.3
run env PATH="$PGSTUB:$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --drain-timeout 5
assert_eq "$rc" "1" "a failing pgrep (exit 3) aborts the release"
assert_contains "$out" "pgrep -f exited 3: pgrep: stub cannot read the process table" "...naming the pgrep failure and its message"
assert_contains "$out" "cannot tell whether engine processes are running" "...and saying the drain could not look"
assert_not_contains "$out" "no engine processes running" "a failing pgrep is not read as idle"
assert_not_contains "$out" "ff-only merge" "a failing pgrep merges nothing"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$PREV_FULL" "a failing pgrep leaves the engine worktree unchanged"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$PREV_FULL" "a failing pgrep leaves origin/release unchanged"
[[ -e "$LOCK" ]] && fail "lock removed after a failing pgrep" "lock exists" || pass "lock removed after a failing pgrep"
kill "$BUSY_PID" 2>/dev/null || true
wait "$BUSY_PID" 2>/dev/null || true

# G3: a dirty dev tree is refused before the lock and the drain. With a busy
# engine the old order took the lock, waited the whole --drain-timeout and then
# blamed the drain, not the tree. The lock acquire always prints "release: lock …":
# its absence from the output proves no lock file existed at any point.
printf '# dirty again\n' >> "$REPO/tests/test-helper.sh"
bash "$RWT/bin/busy.sh" &
BUSY_PID=$!
sleep 0.3
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --drain-timeout 2
assert_eq "$rc" "1" "dirty tree + busy engine: refused"
assert_contains "$out" "dev tree $REPO has uncommitted tracked changes" "...naming the dirty tree, not the drain"
assert_not_contains "$out" "release: lock" "...before any lock was taken"
assert_not_contains "$out" "drain" "...and before the drain"
assert_not_contains "$out" "engine busy" "...so the drain timeout is never reached"
[[ -e "$LOCK" ]] && fail "dirty tree + busy engine: no lock file left behind" "lock exists" || pass "dirty tree + busy engine: no lock file left behind"
kill "$BUSY_PID" 2>/dev/null || true
wait "$BUSY_PID" 2>/dev/null || true
g "$REPO" checkout -q -- tests/test-helper.sh
rm -f "$RWT/bin/busy.sh" "$TEST_TMPDIR/eng.ine"

# with the engine idle again, the pending main commit releases cleanly; a stray
# MNEMON_SMOKE_CMD without MNEMON_TEST_HOOKS=1 is ignored (F5), the real smoke runs.
# Without MNEMON_TEST_HOOKS the stray MNEMON_RELEASE_DATE is ignored too: the real date names the tag
run env PATH="$STUBBIN:$PATH" MNEMON_SMOKE_CMD=false MNEMON_RELEASE_DATE="$FROZEN" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "0" "release succeeds once the engine is idle (MNEMON_SMOKE_CMD without MNEMON_TEST_HOOKS is ignored)"
assert_contains "$out" "ignoring MNEMON_SMOKE_CMD" "...with a one-line notice"
assert_contains "$out" "ignoring MNEMON_RELEASE_DATE" "...and so is a stray MNEMON_RELEASE_DATE"
assert_contains "$out" "release: smoke — $RWT/bin/mnemon config path" "...and the real smoke ran"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "release worktree at the new main"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$(git -C "$REPO" rev-parse main)" "origin/release pushed after the green smoke"
REAL_TAG="$(printf '%s\n' "$out" | sed -n 's/^release: tag \(release\/[0-9-]*\) → .*/\1/p' | head -1)"
[[ "$REAL_TAG" =~ ^release/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$ && "$REAL_TAG" != "$TODAY_TAG"* ]] && pass "the tag carries the real date ($REAL_TAG), not the frozen one" || fail "the tag carries the real date, not the frozen one" "tag line: '$REAL_TAG'"
assert_eq "$(git -C "$REPO" tag -l 'release/*' | sort)" "$(printf '%s\n%s' "$REAL_TAG" "$TODAY_TAG" | sort)" "two release tags now: the frozen first one and the real-dated second"
assert_eq "$(git -C "$ORIGIN" rev-parse "refs/tags/$REAL_TAG^{commit}")" "$(git -C "$REPO" rev-parse main)" "...the second is on origin with the branch"
assert_contains "$out" "released $NEW_MAIN → $RWT ($REAL_TAG)" "...and the last line names it"
run env MNEMON_HOME="$RWT" "$RWT/bin/mnemon" version
assert_contains "$out" "mnemon $REAL_TAG (release, $NEW_MAIN) $RWT" "mnemon version shows the real-dated tag"
# a second release on the frozen date: -2 (the dry run names it before anything moves)
printf '# fourth change\n' >> "$REPO/tests/test-helper.sh"
g "$REPO" commit -q -am "fourth change on main"
run hooked env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_contains "$out" "6. tag:    git -C $RWT tag -a $TODAY_TAG-2 -m 'mnemon release $TODAY_TAG-2' $(git -C "$REPO" rev-parse --short main)" "dry-run names the next free tag: -2 on a date that already has a release"
run hooked env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "0" "the second release on one date succeeds"
assert_eq "$(git -C "$REPO" rev-parse "$TODAY_TAG-2^{commit}")" "$(git -C "$REPO" rev-parse main)" "...tagged release/<date>-2 on the released commit"
assert_eq "$(git -C "$ORIGIN" rev-parse "refs/tags/$TODAY_TAG-2^{commit}")" "$(git -C "$REPO" rev-parse main)" "...pushed with the branch"
assert_contains "$out" "released $(git -C "$REPO" rev-parse --short main) → $RWT ($TODAY_TAG-2)" "...and the last line names the -2 tag"
run env MNEMON_HOME="$RWT" "$RWT/bin/mnemon" version
assert_contains "$out" "mnemon $TODAY_TAG-2 (release, $(git -C "$REPO" rev-parse --short main)) $RWT" "mnemon version shows the -2 tag"

# F5: a failed push is not a warning — the local release stands but nodes will not see it: exit 3
printf '# third change\n' >> "$REPO/tests/test-helper.sh"
g "$REPO" commit -q -am "third change on main"
THIRD_MAIN=$(git -C "$REPO" rev-parse --short main)
BEFORE_PUSH=$(git -C "$ORIGIN" rev-parse release)
git -C "$REPO" remote set-url origin "$TEST_TMPDIR/no-such-origin.git"
run hooked env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "3" "a failed push exits 3"
assert_contains "$out" "released locally to $RWT, push failed: nodes will not see $THIRD_MAIN" "push failure names the engine and the sha nodes will miss"
assert_contains "$out" "git -C $RWT push --atomic origin release refs/tags/$TODAY_TAG-3" "push failure prints the push of the branch and the tag to retry by hand"
assert_contains "$out" "  git: " "push failure shows git's own message"
assert_not_contains "$out" "released $THIRD_MAIN →" "push failure does not claim a release"
assert_eq "$(git -C "$RWT" rev-parse HEAD)" "$(git -C "$REPO" rev-parse main)" "the local release stays after a failed push"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$BEFORE_PUSH" "origin/release did not move"
assert_eq "$(git -C "$REPO" rev-parse "$TODAY_TAG-3^{commit}")" "$(git -C "$REPO" rev-parse main)" "the tag of the local release exists (it stands with the local release)"
assert_eq "$(git -C "$ORIGIN" rev-parse -q --verify "refs/tags/$TODAY_TAG-3" 2>/dev/null || echo none)" "none" "...and did not reach origin (atomic push: neither the branch nor the tag)"
# a rerun after a failed push keeps the name: the tag already on the commit is reused, not re-minted
run hooked env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --dry-run
assert_contains "$out" "6. tag:    $TODAY_TAG-3 already names $THIRD_MAIN — kept, not re-created" "dry-run after a failed push: the existing tag is kept, no -4"
assert_contains "$out" "7. push:   git -C $RWT push --atomic origin release refs/tags/$TODAY_TAG-3" "...and would be pushed"
[[ -e "$LOCK" ]] && fail "lock removed after a failed push" "lock exists" || pass "lock removed after a failed push"
git -C "$REPO" remote set-url origin "$ORIGIN"

# G2: a doctor that crashes (exit code other than 0 or 1) fails the smoke. The
# old rule read only `fail` lines and discarded doctor's exit status, so a
# crashing doctor passed. The fixture's dispatcher is changed on main so that
# `doctor` exits 9 — config path, version and test_config.sh still pass, only
# the doctor rule can catch it. Rule: rc 0 → ok; rc 1 with every fail line in
# deps/qmd/writer → ok; anything else → smoke failure.
sed -e 's|^  doctor)   cmd_doctor .*$|  doctor)   echo "doctor: simulated crash" >\&2; exit 9 ;;|' "$REPO/bin/mnemon" > "$REPO/bin/mnemon.new"
mv "$REPO/bin/mnemon.new" "$REPO/bin/mnemon"
chmod +x "$REPO/bin/mnemon"
assert_eq "$(grep -c 'simulated crash' "$REPO/bin/mnemon" || true)" "1" "fixture: the doctor dispatch line was replaced"
g "$REPO" commit -q -am "doctor crashes"
CRASH_MAIN=$(git -C "$REPO" rev-parse --short main)
THIRD_REL=$(git -C "$RWT" rev-parse --short HEAD)
BEFORE_CRASH=$(git -C "$ORIGIN" rev-parse release)
run hooked env PATH="$STUBBIN:$PATH" MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "a crashing doctor (exit 9) fails the smoke"
assert_contains "$out" "doctor: simulated crash" "the smoke shows doctor's output"
assert_contains "$out" "doctor exited 9" "...and names the unexpected exit code"
assert_contains "$out" "smoke failed on $RWT @ $CRASH_MAIN. Nothing was pushed" "a crashing doctor publishes nothing"
assert_contains "$out" "mnemon release --rollback $TODAY_TAG-3   # = $THIRD_REL" "...and prints the rollback, naming the previous release by its (local) tag"
assert_eq "$(git -C "$REPO" tag -l 'release/*' | wc -l | tr -d ' ')" "4" "a crashing doctor creates no tag"
assert_eq "$(git -C "$ORIGIN" rev-parse release)" "$BEFORE_CRASH" "a crashing doctor does not move origin/release"
[[ -e "$LOCK" ]] && fail "lock removed after a crashing doctor" "lock exists" || pass "lock removed after a crashing doctor"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release --rollback "$THIRD_REL"
assert_eq "$rc" "0" "rollback after a crashing doctor exits 0"
g "$REPO" revert --no-edit HEAD >/dev/null

printf '# hotfix on release\n' >> "$RWT/tests/test-helper.sh"
g "$RWT" commit -q -am "hotfix on release"
run env MNEMON_HOME="$RWT" "$REPO/bin/mnemon" release
assert_eq "$rc" "1" "release fails when release has diverged"
assert_contains "$out" "release has diverged from main" "...with the specified message"
assert_not_contains "$out" "release: lock" "...before taking the lock (G3)"
[[ -e "$LOCK" ]] && fail "a diverged-release refusal leaves no lock file" "lock exists" || pass "a diverged-release refusal leaves no lock file"

cd /
rm -rf "$TEST_TMPDIR"
summary
