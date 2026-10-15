#!/usr/bin/env python3
"""lib_config.py — read Mnemon's resolved configuration from Python.

The configuration logic (instance resolution, defaults, legacy fallbacks)
lives in exactly one place: bin/mnemon-config.sh. Python tools do not
re-implement it; they run the bash loader in a child shell and read its
exports back. MNEMON_CONFIG / MNEMON_INSTANCE are honoured from the
environment, exactly as for the shell tools.

Stdlib only, python3.9+.

    import lib_config
    cfg = lib_config.load_config()            # dict of export name -> value
    vault = cfg["VAULT_PATH"]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# bin/ -> repo root. Derived from this file (symlinks resolved), never from the
# environment, so a stale MNEMON_ROOT cannot point a tool at another checkout.
ENGINE_ROOT = Path(__file__).resolve().parent.parent

EXPORTS = (
    "MNEMON_CONFIG",
    "MNEMON_CONFIG_FILE",
    "MNEMON_CONFIG_SOURCE",
    "MNEMON_INSTANCE",
    "MNEMON_STATE_DIR",
    "VAULT_PATH",
    "READER_CONTEXT_PATH",
    "CHARTER_PATH",
    "SEARCH_PROVIDER",
    "QMD_COLLECTION",
    "DEFAULT_MODEL",
    "DEFAULT_LANGUAGE",
    "WHISPER_MODEL",
    "AUTO_DETECT_ORIGIN",
    "ARCHIVE_DIR",
    "WRITER_HOST",
)


# The loader only reads small files; a longer wait means a hung mount, not work.
LOADER_TIMEOUT = 15


class ConfigError(RuntimeError):
    """The bash loader refused: the message is its stderr, ready to print."""


def load_config(config_path: str | None = None, env: dict | None = None) -> dict[str, str]:
    """Run `load_config [path]` from bin/mnemon-config.sh and return its exports.

    Warnings the loader prints (legacy config locations) are forwarded to
    stderr. Raises ConfigError with the loader's message on failure.
    """
    child_env = dict(os.environ if env is None else env)
    child_env["MNEMON_ROOT"] = str(ENGINE_ROOT)
    script = (
        'source "$MNEMON_ROOT/bin/mnemon-config.sh" && load_config "$@" && '
        "for v in " + " ".join(EXPORTS) + '; do printf "%s=%s\\n" "$v" "${!v}"; done'
    )
    argv = ["bash", "-c", script, "mnemon-config"]
    if config_path:
        argv.append(str(config_path))
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              env=child_env, timeout=LOADER_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ConfigError(f"mnemon config loader did not finish within {LOADER_TIMEOUT}s "
                          "(a config on a stalled mount?)") from None
    if proc.returncode != 0:
        raise ConfigError(proc.stderr.strip() or "mnemon config loader failed")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    cfg: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            cfg[key] = value
    return cfg
