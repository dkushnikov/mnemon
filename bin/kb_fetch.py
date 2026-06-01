#!/usr/bin/env python3
"""kb_fetch — SSRF-guarded fetch + render + defuddle helpers for Mnemon depth.

Importable (underscore). Shared by kb-depth-fold.py; kb-probe-deeper.py keeps an
inline copy for now (TODO: probe should import this too — keep logic identical).
"""
from __future__ import annotations
import os
import socket
import ipaddress
import subprocess
from urllib.parse import urlsplit

try:
    import requests
except ImportError:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mnemon-depth/1.0 (+https://github.com/dkushnikov/mnemon)"
TIMEOUT = 8
MAX_BYTES = 2_000_000


def host_is_safe(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for *_, sockaddr in infos:
        try:
            a = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified):
            return False
    return True


def fetch(url: str, cap: int = MAX_BYTES):
    """SSRF-guarded GET → (final_url|None, status:int|str, text)."""
    if requests is None:
        return (None, "no-requests", "")
    if not host_is_safe(urlsplit(url).hostname or ""):
        return (None, "ssrf-blocked", "")
    try:
        s = requests.Session()
        s.max_redirects = 5
        r = s.get(url, timeout=TIMEOUT, headers={"User-Agent": UA},
                  stream=True, allow_redirects=True)
        if not host_is_safe(urlsplit(r.url).hostname or ""):
            return (None, "ssrf-redirect", "")
        chunks, total = [], 0
        for c in r.iter_content(8192):
            chunks.append(c)
            total += len(c)
            if total >= cap:
                break
        return (r.url, r.status_code,
                b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore"))
    except Exception as e:
        return (None, f"fetch-error:{type(e).__name__}", "")


def render_html(url: str):
    rs = os.path.join(HERE, "render-url.sh")
    if not os.path.exists(rs):
        return None
    try:
        r = subprocess.run([rs, url, "--html", "--timeout", "12000"],
                           capture_output=True, text=True, timeout=45)
        return r.stdout if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def defuddle_md(url: str) -> str:
    """Clean markdown via defuddle CLI, or '' on failure."""
    try:
        r = subprocess.run(["defuddle", "parse", url, "--md"],
                           capture_output=True, text=True, timeout=40)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""
