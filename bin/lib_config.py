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
    "MNEMON_VAULT_STATE_DIR",
    "EXTRACT_TIMEOUT",
    "EXTRACT_INLINE_MAX_BYTES",
    "EXTRACT_MAX_USD",
    "EXTERNAL_FETCH",
    "EXTERNAL_FETCHERS",
    "FETCH_USER_AGENT",
    "FETCH_TIMEOUT",
    "FETCH_CONNECT_TIMEOUT",
    "FETCH_READ_TIMEOUT",
    "FETCH_MAX_BYTES",
    "RENDER_TIMEOUT",
    "RENDER_MAX_BYTES",
    "ACQUIRE_TIMEOUT",
    "CONVERT_TIMEOUT",
    "TRANSCRIBE_TIMEOUT",
    "SITE_MAX_PAGES",
    "SITE_TIMEOUT",
    "THIN_WORDS",
    "EXTRACT_MAX_PARTS",
    "DOCTOR_THIN_WORDS",
    "DOCTOR_STUB_WORDS",
    "DOCTOR_ARXIV_WORDS",
    "DOCTOR_RATING_HI_SHARE",
)

# The scalar keys with defaults (capture contracts §7, capture layer §5): one row per
# key as (key, VAR, type, default) — the same table as MNEMON_CONFIG_KEYS in
# bin/mnemon-config.sh (the loader validates and exports it) and as `mnemon config
# keys` prints it; tests/test_config.sh asserts the three agree. Types: str ·
# enum:a,b · int · num; an empty default means unset.
CONFIG_KEYS = (
    ("extract_timeout", "EXTRACT_TIMEOUT", "num", "900"),
    ("extract_inline_max_bytes", "EXTRACT_INLINE_MAX_BYTES", "int", "400000"),
    ("extract_max_usd", "EXTRACT_MAX_USD", "num", ""),
    ("external_fetch", "EXTERNAL_FETCH", "enum:off,auto", "off"),
    ("external_fetchers", "EXTERNAL_FETCHERS", "str", "firecrawl,jina"),
    ("fetch_user_agent", "FETCH_USER_AGENT", "str",
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    ("fetch_timeout", "FETCH_TIMEOUT", "num", "60"),
    ("fetch_max_bytes", "FETCH_MAX_BYTES", "int", "20000000"),
    ("render_timeout", "RENDER_TIMEOUT", "num", "90"),
    ("render_max_bytes", "RENDER_MAX_BYTES", "int", "20000000"),
    ("acquire_timeout", "ACQUIRE_TIMEOUT", "num", "300"),
    ("convert_timeout", "CONVERT_TIMEOUT", "num", "120"),
    ("transcribe_timeout", "TRANSCRIBE_TIMEOUT", "num", "3600"),
    ("site_max_pages", "SITE_MAX_PAGES", "int", "40"),
    ("site_timeout", "SITE_TIMEOUT", "num", "900"),
    ("thin_words", "THIN_WORDS", "int", "60"),
    ("extract_max_parts", "EXTRACT_MAX_PARTS", "int", "8"),
    # the data doctor's thresholds (vault store S3/S7, O4): the radius lives in the charter, not here
    ("doctor_thin_words", "DOCTOR_THIN_WORDS", "int", "300"),
    ("doctor_stub_words", "DOCTOR_STUB_WORDS", "int", "40"),
    ("doctor_arxiv_words", "DOCTOR_ARXIV_WORDS", "int", "600"),
    ("doctor_rating_hi_share", "DOCTOR_RATING_HI_SHARE", "int", "20"),
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
