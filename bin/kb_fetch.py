#!/usr/bin/env python3
"""kb_fetch — the depth helpers' fetch, render and defuddle functions over the capture policy.

Importable (underscore). Shared by kb-depth-fold.py and kb-probe-deeper.py. Every request goes through
`capture.policy` (spec §3.2, last paragraph; D3): SSRF checks on every hop, pinned connections, redirect and
byte caps, no proxies, no credentials — `requests` is gone. The return shapes of phase 1 are kept for the
callers: `fetch` → (final_url | None, status: int | str, text); `defuddle_md` fetches the bytes through the
policy and converts them with `capture.convert.defuddle_file`, never `defuddle parse <url>`.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from capture import convert, proc  # noqa: E402
from capture import policy as policymod  # noqa: E402

UA = policymod.DEFAULT_USER_AGENT
TIMEOUT = 8
MAX_BYTES = 2_000_000

_policy: Optional[policymod.Policy] = None


def configure(config=None) -> policymod.Policy:
    """The helpers' policy: the instance keys when given (`--policy`), else the depth defaults."""
    global _policy
    if config:
        _policy = policymod.Policy.from_config(config)
    else:
        _policy = policymod.Policy.from_timeout(TIMEOUT, max_bytes=MAX_BYTES)
    return _policy


def policy() -> policymod.Policy:
    return _policy if _policy is not None else configure()


def host_is_safe(host: str) -> bool:
    """Whether a host resolves only to public addresses (kept for callers; the policy checks every hop)."""
    if not host:
        return False
    try:
        policy().check("https://%s/" % host)
    except policymod.PolicyError:
        return False
    return True


def fetch_response(url: str, cap: int = MAX_BYTES, deadline: Optional[proc.Deadline] = None):
    """(Response | None, error kind | None) through the policy with a byte cap."""
    try:
        return policy().with_max_bytes(cap).get(url, deadline=deadline), None
    except policymod.PolicyError as exc:
        return None, exc.kind


def fetch(url: str, cap: int = MAX_BYTES, deadline: Optional[proc.Deadline] = None):
    """Policy-guarded GET → (final_url | None, status: int | str, text) — the phase-1 shape."""
    resp, kind = fetch_response(url, cap, deadline)
    if resp is None:
        return (None, kind, "")
    return (resp.final_url, resp.status, resp.text("ignore"))


def render_html(url: str, timeout_s: float = 45.0, deadline: Optional[proc.Deadline] = None):
    """Post-JS rendered HTML via render-url.sh --html (its own process group), or None."""
    cmd = os.environ.get("RENDER_URL_CMD") or os.path.join(HERE, "render-url.sh")
    if not os.path.exists(cmd):
        return None
    try:
        policy().check(url, deadline=deadline)
    except policymod.PolicyError:
        return None
    res = proc.run([cmd, url, "--html", "--timeout", "12000"], timeout=timeout_s, deadline=deadline)
    if res.timed_out or res.rc != 0 or not res.stdout.strip():
        return None
    return res.stdout.decode("utf-8", "ignore")


def defuddle_bytes(data: bytes, deadline: Optional[proc.Deadline] = None) -> str:
    """Clean markdown of fetched bytes via `capture.convert.defuddle_file`, or '' on failure."""
    try:
        text, _ = convert.decode(data, None)
    except convert.ConvertError:
        text = data.decode("utf-8", "ignore")
    md, _note = convert.defuddle_file(text, deadline=deadline)
    return md or ""


def defuddle_md(url: str, cap: int = MAX_BYTES, deadline: Optional[proc.Deadline] = None) -> str:
    """Clean markdown of a URL: the bytes through the policy, the conversion on the bytes, or ''."""
    resp, _kind = fetch_response(url, cap, deadline)
    if resp is None or resp.status != 200 or not resp.body.strip():
        return ""
    return defuddle_bytes(resp.body, deadline)
