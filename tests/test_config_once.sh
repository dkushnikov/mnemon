#!/usr/bin/env bash
# test_config_once.sh — the instance is resolved once per command (B7, tracker #39; docs/install.md § Variant C):
# load_config exports MNEMON_RESOLVED, a digest over the loader, the config file, the inputs and every export; a
# later load_config without a path argument — the gateway, the gateway under the vault lock, a Python tool through
# lib_config — reuses the environment while the digest holds, and loads the file again when anything moved (a selector
# flag, another instance, an edited config, a changed or missing export, a forged stamp). Counted through the loader's
# test hook (MNEMON_TEST_HOOKS=1 + MNEMON_TEST_LOADER_LOG: one `full|reuse <pid> sh|py` line per load). No network, no
# model, a temp XDG tree. bash 3.2 / python 3.9.
source "$(dirname "$0")/test-helper.sh"
MNEMON_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

echo "=== Resolve-once Tests ==="
print_toolchain; assert_toolchain_legacy

TEST_TMPDIR="$(cd "$(mktemp -d -t mnemon-once-XXXX)" && pwd -P)"
# hermetic (AGENTS.md): a temporary HOME and XDG tree, and stubs first on PATH for the tools the doctor runs — the
# fixture `claude` of the capture suites (it answers --version and --help, nothing else is called here) and a `yt-dlp`
# whose debug header the media-tools probe reads; the real ones never run
export HOME="$TEST_TMPDIR/home" XDG_CONFIG_HOME="$TEST_TMPDIR/xdg" XDG_STATE_HOME="$TEST_TMPDIR/state"
STUBS="$TEST_TMPDIR/stubs"
mkdir -p "$HOME" "$STUBS" "$TEST_TMPDIR/stub-calls"
cp "$MNEMON_ROOT/tests/fixtures/capture/stub-claude.sh" "$STUBS/claude"
printf '#!/bin/sh\nprintf "[debug] yt-dlp version stable@2026.11.01 from yt-dlp/yt-dlp [c0ffee000] (pip)\\n[debug] JS runtimes: none\\n" >&2\nexit 2\n' > "$STUBS/yt-dlp"
chmod +x "$STUBS/claude" "$STUBS/yt-dlp"
export PATH="$STUBS:$PATH" STUB_DIR="$TEST_TMPDIR/stub-calls"
unset MNEMON_CONFIG MNEMON_INSTANCE MNEMON_CONFIG_DIR MNEMON_RESOLVED MNEMON_CONFIG_FILE MNEMON_CONFIG_SOURCE
CFG_DIR="$XDG_CONFIG_HOME/mnemon"
LOG="$TEST_TMPDIR/loader.log"
mkdir -p "$CFG_DIR/instances" "$TEST_TMPDIR/va/Sources" "$TEST_TMPDIR/vb/Sources" "$TEST_TMPDIR/cwd"
printf 'instance: alpha\nvault_path: %s/va\nsearch_provider: grep\n' "$TEST_TMPDIR" > "$CFG_DIR/instances/alpha.yaml"
printf 'instance: beta\nvault_path: %s/vb\nsearch_provider: grep\n' "$TEST_TMPDIR" > "$CFG_DIR/instances/beta.yaml"
printf 'alpha\n' > "$CFG_DIR/default"
M="$MNEMON_ROOT/bin/mnemon"
GW="$MNEMON_ROOT/bin/knowledge-gateway.sh"

# loads <cmd…>: run a command with the loader hook on; prints "<full> <reuse>" (the counts of the log)
loads() {
  : > "$LOG"
  (cd "$TEST_TMPDIR/cwd" && MNEMON_TEST_HOOKS=1 MNEMON_TEST_LOADER_LOG="$LOG" "$@" >"$TEST_TMPDIR/out" 2>"$TEST_TMPDIR/err") || true
  printf '%s %s' "$(grep -c '^full ' "$LOG" || true)" "$(grep -c '^reuse ' "$LOG" || true)"
}
# audited_vault: the vault the last `audit text-mismatch --json` run (loads) reports, or "-" when it printed no document
audited_vault() { python3 -c 'import json, sys
try:
    print(json.load(open(sys.argv[1]))["vault"])
except Exception:
    print("-")' "$TEST_TMPDIR/out"; }
# resolved_env <instance>: the environment the dispatcher hands to its command, as `export NAME='VALUE'` lines
resolved_env() { (cd "$TEST_TMPDIR/cwd" && bash -c 'source "$1/bin/mnemon-config.sh"; export MNEMON_INSTANCE="$2"; unset MNEMON_CONFIG; load_config >/dev/null; for v in MNEMON_RESOLVED $MNEMON_EXPORT_NAMES; do printf "export %s='"'"'%s'"'"'\n" "$v" "${!v}"; done' _ "$MNEMON_ROOT" "$1"); }

# ---------------------------------------------------------------------------------------------
echo "--- one load per command ---"
assert_eq "$(loads "$M" search --query anything)" "1 1" "mnemon search: the dispatcher loads, the Python tool reuses (1 full, 1 reuse)"
assert_contains "$(cat "$LOG")" "reuse" "...the reuse is the Python tool's (lib_config)"
assert_eq "$(grep '^reuse ' "$LOG" | awk '{print $3}')" "py" "...from lib_config, not a second shell load"
assert_eq "$(loads "$M" add --origin text --title T --dry-run)" "1 1" "mnemon add --dry-run: the dispatcher loads, the gateway reuses"
printf 'a text\n' > "$TEST_TMPDIR/in.txt"
# a real add that runs no extractor (a bookmark card): the dispatcher, vault-lock.py, the gateway under the lock
assert_eq "$(loads "$M" add --file-text "$TEST_TMPDIR/in.txt" --title T --tier bookmark)" "1 1" "mnemon add (a write under the vault lock): one load; the gateway behind vault-lock.py reuses it"
assert_contains "$(cat "$TEST_TMPDIR/out")" "RESULT:status=bookmarked" "...and the capture ran (the card is published)"
assert_eq "$(loads "$M" doctor)" "1 0" "mnemon doctor: one load"
assert_contains "$(cat "$TEST_TMPDIR/out")" " fence — claude 9.9.9-stub" "...the doctor ran the stub claude, never a real one"
assert_contains "$(cat "$TEST_TMPDIR/out")" " media-tools — yt-dlp 2026.11.01" "...and the stub yt-dlp"
assert_eq "$(loads "$M" audit --cutoff 2026-01-01)" "1 1" "mnemon audit: the dispatcher loads, archive-audit.py reuses (no --config handed down)"
assert_eq "$(loads "$M" audit text-mismatch --json) $(audited_vault)" "1 1 $TEST_TMPDIR/va" "mnemon audit text-mismatch: the dispatcher loads, text-mismatch-audit.py reuses (no --config handed down) — the default instance's vault"
assert_eq "$(grep '^reuse ' "$LOG" | awk '{print $3}')" "py" "...the reuse is the audit's (lib_config)"
assert_eq "$(loads "$M" status)" "1 1" "mnemon status: the dispatcher loads, the gateway's status reuses"

echo "--- equivalence with the two-pass result ---"
two_pass="$(cd "$TEST_TMPDIR/cwd" && bash -c 'source "$1/bin/mnemon-config.sh"; load_config >/dev/null; first="$MNEMON_RESOLVED"; unset MNEMON_RESOLVED; load_config >/dev/null; for v in $MNEMON_EXPORT_NAMES; do printf "%s=%s\n" "$v" "${!v}"; done; [[ "$first" == "$MNEMON_RESOLVED" ]] && echo same-stamp' _ "$MNEMON_ROOT")"
one_pass="$(cd "$TEST_TMPDIR/cwd" && bash -c 'source "$1/bin/mnemon-config.sh"; load_config >/dev/null; MNEMON_TEST_HOOKS=1 MNEMON_TEST_LOADER_LOG="$2" load_config >/dev/null; for v in $MNEMON_EXPORT_NAMES; do printf "%s=%s\n" "$v" "${!v}"; done; [[ -n "$MNEMON_RESOLVED" ]] && echo same-stamp' _ "$MNEMON_ROOT" "$LOG")"
assert_eq "$one_pass" "$two_pass" "every export after a reuse equals a second full load (the two-pass result)"
assert_contains "$two_pass" "same-stamp" "...and two full loads give the same stamp (the digest is deterministic)"
py_eq="$(cd "$TEST_TMPDIR/cwd" && bash -c 'source "$1/bin/mnemon-config.sh"; load_config >/dev/null; python3 - "$1/bin" <<'"'"'PY'"'"'
import os, sys
sys.path.insert(0, sys.argv[1])
import lib_config
reused = lib_config.inherited_resolution(os.environ)
env = {k: v for k, v in os.environ.items() if k != "MNEMON_RESOLVED"}
full = lib_config.load_config(env=env)
print("reused" if reused is not None else "not-reused", "equal" if reused == full else "differ")
PY' _ "$MNEMON_ROOT")"
assert_eq "$py_eq" "reused equal" "lib_config: the reused dict equals a full load through the shell loader"
names_sh="$(bash -c 'source "$1/bin/mnemon-config.sh"; echo $MNEMON_EXPORT_NAMES' _ "$MNEMON_ROOT")"
names_py="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import lib_config; print(" ".join(lib_config.EXPORTS))' "$MNEMON_ROOT/bin")"
assert_eq "$names_py" "$names_sh" "bin/lib_config.py EXPORTS == MNEMON_EXPORT_NAMES, in order (the digest's field order)"
dump_names="$(sed -n 's/^CONFIG_EXPORTS="\(.*\)"$/\1/p' "$M" | tr ' ' '\n' | grep -v '^MNEMON_ROOT$' | sort | tr '\n' ' ')"
assert_eq "$dump_names" "$(printf '%s\n' $names_sh | sort | tr '\n' ' ')" "...and \`mnemon config dump\` lists the same names (MNEMON_ROOT beside them)"

echo "--- explicit selectors outrank the inherited resolution ---"
eval "$(resolved_env alpha)"
assert_eq "$MNEMON_INSTANCE $(basename "$VAULT_PATH")" "alpha va" "(setup) the environment carries alpha's resolution"
assert_eq "$(loads "$M" config get vault_path) $(cat "$TEST_TMPDIR/out")" "0 1 $TEST_TMPDIR/va" "inherited and unchanged: reused, no load"
assert_eq "$(loads "$M" --instance beta config get vault_path) $(cat "$TEST_TMPDIR/out")" "1 0 $TEST_TMPDIR/vb" "--instance beta: loaded, beta's vault"
assert_eq "$(loads "$M" --config "$CFG_DIR/instances/beta.yaml" config get vault_path) $(cat "$TEST_TMPDIR/out")" "1 0 $TEST_TMPDIR/vb" "--config beta.yaml: loaded, beta's vault"
assert_eq "$(loads "$M" --config "$CFG_DIR/instances/alpha.yaml" config get vault_path) $(cat "$TEST_TMPDIR/out")" "1 0 $TEST_TMPDIR/va" "--config naming the very file: an explicit selection is loaded too"
assert_eq "$(loads "$GW" source-add --instance beta --origin text --title T --dry-run) $(grep '^Vault: ' "$TEST_TMPDIR/out" | cut -d' ' -f2)" "1 0 $TEST_TMPDIR/vb" "the gateway's own --instance beta: loaded, beta's vault"
assert_eq "$(loads "$M" audit text-mismatch --json) $(audited_vault)" "0 2 $TEST_TMPDIR/va" "audit text-mismatch, inherited and unchanged: the dispatcher and the audit both reuse — alpha's vault"
assert_eq "$(loads "$M" --instance beta audit text-mismatch --json) $(audited_vault)" "1 1 $TEST_TMPDIR/vb" "--instance beta audit text-mismatch: the dispatcher loads beta, the audit reuses it — beta's vault"
assert_eq "$(loads "$M" --config "$CFG_DIR/instances/beta.yaml" audit text-mismatch --json) $(audited_vault)" "1 1 $TEST_TMPDIR/vb" "--config beta.yaml audit text-mismatch: the dispatcher loads beta, the audit reuses it — beta's vault"
assert_eq "$(loads "$M" audit text-mismatch --config "$CFG_DIR/instances/beta.yaml") $(grep -c -- '--config belongs before the command' "$TEST_TMPDIR/err")" "0 0 1" "a selector after the subcommand is still refused, before any load"

echo "--- a direct entry point without the dispatcher ---"
unset MNEMON_RESOLVED
for v in $names_sh; do unset "$v"; done
assert_eq "$(loads "$GW" source-add --config "$CFG_DIR/instances/beta.yaml" --origin text --title T --dry-run) $(grep '^Vault: ' "$TEST_TMPDIR/out" | cut -d' ' -f2)" "1 0 $TEST_TMPDIR/vb" "the gateway called directly with --config: one load"
assert_eq "$(loads python3 "$MNEMON_ROOT/bin/mnemon-search.py" --query x)" "1 0" "a Python tool called directly (no stamp): one load through the shell loader"
assert_eq "$(MNEMON_INSTANCE=beta loads python3 "$MNEMON_ROOT/bin/mnemon-search.py" --query x)" "1 0" "...with MNEMON_INSTANCE set by the caller: loaded (no stamp to reuse)"

echo "--- a nested invocation for another instance ---"
eval "$(resolved_env alpha)"
nested="$(cd "$TEST_TMPDIR/cwd" && MNEMON_TEST_HOOKS=1 MNEMON_TEST_LOADER_LOG="$LOG" bash -c '"$1" config get vault_path; "$1" --instance beta config get vault_path; "$1" config get vault_path' _ "$M")"
assert_eq "$(printf '%s\n' "$nested" | tr '\n' ' ')" "$TEST_TMPDIR/va $TEST_TMPDIR/vb $TEST_TMPDIR/va " "alpha's environment → a nested --instance beta answers beta, and alpha's next command alpha again"

echo "--- a stale or incomplete inherited export is never reused ---"
eval "$(resolved_env alpha)"
assert_eq "$(VAULT_PATH=/elsewhere loads "$M" config get vault_path) $(cat "$TEST_TMPDIR/out")" "1 0 $TEST_TMPDIR/va" "a changed export (VAULT_PATH overridden): loaded again, the file's value"
assert_eq "$(unset ARCHIVE_DIR; loads "$M" config get vault_path)" "1 0" "a missing export (ARCHIVE_DIR unset): loaded again"
assert_eq "$(MNEMON_RESOLVED=0000 loads "$M" config get vault_path)" "1 0" "a forged stamp: loaded again"
assert_eq "$(HOME="$TEST_TMPDIR" loads "$M" config get vault_path)" "1 0" "another HOME (tilde expansion, state dirs): loaded again"
printf 'instance: alpha\nvault_path: %s/vb\nsearch_provider: grep\n' "$TEST_TMPDIR" > "$CFG_DIR/instances/alpha.yaml"
assert_eq "$(loads "$M" config get vault_path) $(cat "$TEST_TMPDIR/out")" "1 0 $TEST_TMPDIR/vb" "the config file edited after the resolution: loaded again, the new value"
printf 'instance: alpha\nvault_path: %s/va\nsearch_provider: grep\n' "$TEST_TMPDIR" > "$CFG_DIR/instances/alpha.yaml"
assert_eq "$(MNEMON_INSTANCE=beta loads "$M" config get vault_path) $(grep -c 'conflicting selectors' "$TEST_TMPDIR/err")" "1 0 1" "MNEMON_INSTANCE changed under an inherited MNEMON_CONFIG: loaded, and the conflict is refused as before"
py_stale="$(VAULT_PATH=/elsewhere python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import lib_config, os; print(lib_config.inherited_resolution(os.environ) is None, lib_config.load_config()["VAULT_PATH"])' "$MNEMON_ROOT/bin")"
assert_eq "$py_stale" "True $TEST_TMPDIR/va" "lib_config: a changed export is not reused; the full load answers the file's value"
py_path="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import lib_config; print(lib_config.load_config(sys.argv[2])["VAULT_PATH"])' "$MNEMON_ROOT/bin" "$CFG_DIR/instances/beta.yaml")"
assert_eq "$py_path" "$TEST_TMPDIR/vb" "lib_config: a path argument is always loaded, whatever the environment carries"
py_nobash="$(python3 - "$MNEMON_ROOT/bin" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
import lib_config
env = dict(os.environ, PATH="/nonexistent")
ok = lib_config.load_config(env=env)["MNEMON_INSTANCE"]
env["VAULT_PATH"] = "/elsewhere"
try:
    lib_config.load_config(env=env)
    second = "loaded"
except (OSError, lib_config.ConfigError):
    second = "needs-the-loader"
print(ok, second)
PY
)"
assert_eq "$py_nobash" "alpha needs-the-loader" "lib_config: a reuse runs no shell (PATH without bash still answers); a stale one needs the loader"

cd /
rm -rf "$TEST_TMPDIR"
summary
