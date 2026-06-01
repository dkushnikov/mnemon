#!/usr/bin/env python3
"""lib_url — URL normalization + entity helpers for Mnemon.

Shared by kb-probe-deeper.py (depth-aware capture) and intended for
triage-urls.py (dedup) to prevent parsing drift. Importable module
(underscore name); the design specs refer to it as `lib-url.py`.

No third-party deps — stdlib only.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# tracking/junk query params stripped during normalization
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|si$|ref$|ref_src$|s$|igshid$|mc_)", re.I)

# minimal multi-part public suffixes (no PSL dependency); extend as needed
_MULTI_TLD = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "com.au", "com.br",
    "co.in", "com.cn", "github.io",
}


def normalize_url(url: str) -> str:
    """Canonical comparison form: lowercase scheme+host, drop default port,
    drop fragment, strip tracking params, collapse trailing slash, drop a
    leading www. Path case is preserved (paths can be case-sensitive)."""
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    s = urlsplit(url)
    scheme = (s.scheme or "https").lower()
    host = (s.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if s.port and not ((scheme == "https" and s.port == 443) or (scheme == "http" and s.port == 80)):
        netloc = f"{host}:{s.port}"
    q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=False)
         if not _TRACKING.match(k)]
    query = urlencode(sorted(q))
    path = s.path or ""
    if path == "/":
        path = ""
    elif len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def registrable_domain(url_or_host: str) -> str:
    """Approximate eTLD+1 (no PSL). Good enough for the common cases:
    docs.openviking.ai -> openviking.ai ; github.com -> github.com ;
    foo.github.io -> foo.github.io."""
    host = url_or_host
    if "://" in url_or_host or "/" in url_or_host:
        host = (urlsplit(url_or_host if "://" in url_or_host else "//" + url_or_host).hostname or "")
    host = host.lower().strip(".")
    if not host or _is_ip(host):
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:])
    if last2 in _MULTI_TLD:
        return last3
    return last2


def same_site(a: str, b: str) -> bool:
    """True if two URLs share a registrable domain."""
    ra, rb = registrable_domain(a), registrable_domain(b)
    return bool(ra) and ra == rb


def github_repo(url: str):
    """Return 'owner/repo' for any github.com URL (stripping blob/tree/
    issues/...), else None. github.com/owner/repo/tree/main -> owner/repo."""
    s = urlsplit(url if "://" in url else "https://" + url)
    if (s.hostname or "").lower().lstrip("www.") not in ("github.com",):
        return None
    parts = [p for p in s.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if owner.lower() in ("orgs", "sponsors", "settings", "marketplace", "topics", "search", "features"):
        return None
    repo = re.sub(r"\.git$", "", repo)
    return f"{owner}/{repo}"


def _is_ip(host: str) -> bool:
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host)) or ":" in host


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        print(f"{u}\n  normalize={normalize_url(u)}\n  domain={registrable_domain(u)}\n  gh={github_repo(u)}")
