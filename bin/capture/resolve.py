"""capture.resolve — canonical identity (versions 1 and 2) and the per-host resolvers (spec §3.1, D5, D10).

`canonicalize(url, version=VERSION)` applies the frozen steps of the named version and nothing else;
`resolve(url)` adds the fetch URL, the kind (page · arxiv · youtube · github), the metadata URL (arXiv: the
abstract page) and the requested arXiv version. `version` selects a frozen rule set: v1 (2026-10-15) is the
six steps below; v2 (2026-10-28, GH #12) is v1 plus the host-scoped share parameters of step 6
(`HOST_SHARE_KEYS`). Every version stays callable: a replay binds under the version its receipt recorded
(`cap_binding_check` → `--canonical-only --canonical-version N`), new captures write the current one, and
the lookup compares a folder's URLs under the current rules (a folder recorded under v1 with a share-tagged
URL coincides with a v2 request for the same post).

Not done, on purpose: `www.` stripping, http → https, trailing-slash collapsing, path lowercasing, AMP and
mobile variants, following redirects, GitHub path rewriting. `lib_url.normalize_url` is the depth helpers'
same-site comparison form and never an identity.
"""
from __future__ import annotations

import re
import string
from typing import List, NamedTuple, Optional
from urllib.parse import unquote, urlsplit

VERSION = 2
VERSIONS = (1, 2)

TRACKING_KEYS = (
    "utm_*", "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid", "ttclid", "igshid",
    "igsh", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hsCtaTracking", "mkt_tok", "_ga", "_gl", "ref_src", "ref_url",
    "hide_intro_popup",
)
# The YouTube rule keeps `v` alone: every other parameter — the navigation context `list`, `index`, `t`,
# `si`, `feature`, `pp` among them — is dropped, because the video is the resource (§3.1 step 6).

# Host-scoped share parameters (§3.1 step 6, 2026-10-28, GH #12): a key that is a page key elsewhere
# (`?s=alpha` and `?s=beta` are two pages on an unknown host) is a share tag on these hosts and is dropped
# there only — X's `s` (the share source) and `t` (the share token), Substack's `r` (the sharer's referral).
HOST_SHARE_KEYS = {
    "x": ("s", "t"),
    "substack": ("r",),
}
_X_HOSTS = frozenset(("x.com", "www.x.com", "mobile.x.com", "m.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com", "m.twitter.com"))

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")
_BAD_PCT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_HOST_OK = re.compile(r"^[^\s/?#@\\\[\]]+$")
_ARXIV_HOSTS = frozenset(("arxiv.org", "www.arxiv.org"))
_ARXIV_PATH = re.compile(
    r"^/(?:abs|pdf|html)/(?P<id>\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(?:v(?P<v>\d+))?(?:\.pdf)?/?$")
_YT_HOSTS = frozenset(("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"))
_YT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_YT_PATH = re.compile(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]+)/?$")
_GITHUB_HOSTS = frozenset(("github.com", "www.github.com"))


class InvalidURL(ValueError):
    """A URL the resolver refuses (`error_class: invalid-url`)."""
    error_class = "invalid-url"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Canonical(NamedTuple):
    url: str
    version: int
    host_rule: Optional[str]
    dropped_params: List[str]
    dropped_fragment: Optional[str]


class Resolution(NamedTuple):
    canonical: Canonical
    fetch_url: str
    kind: str
    metadata_url: Optional[str]
    requested_version: Optional[int]


def is_tracking_key(key: str) -> bool:
    """A decoded query key that version 1 removes."""
    for pattern in TRACKING_KEYS:
        if pattern.endswith("*"):
            if key.lower().startswith(pattern[:-1].lower()):
                return True
        elif key == pattern:
            return True
    return False


def _host_share_rule(host: str) -> Optional[str]:
    """The name of the host-scoped share rule for this host (`x` · `substack`), else None."""
    if host in _X_HOSTS:
        return "x"
    if host.endswith(".substack.com") and host != "www.substack.com":
        return "substack"
    return None


def _drop_share_keys(kept: List[str], rule: str):
    """Split the kept query parts into (kept, dropped keys) under a host share rule."""
    keys = HOST_SHARE_KEYS[rule]
    still, dropped = [], []
    for part in kept:
        key = part.split("=", 1)[0]
        if unquote(key) in keys:
            dropped.append(key)
        else:
            still.append(part)
    return still, dropped


def _normalize_pct(s: str) -> str:
    """Percent-encoding normalized: hex uppercase, unreserved characters decoded, reserved ones left encoded."""
    def repl(m):
        ch = chr(int(m.group(1), 16))
        if ch in _UNRESERVED:
            return ch
        return "%" + m.group(1).upper()
    return _PCT.sub(repl, s)


def _split(url: str):
    """Steps 1–2: trim, scheme and userinfo and port checks, lowercase scheme and host, drop the default port."""
    url = url.strip()
    if not url:
        raise InvalidURL("empty URL")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidURL("malformed URL: %s" % exc)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidURL("scheme must be http or https" if scheme else "no scheme")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidURL("userinfo is not allowed")
    try:
        port = parts.port
    except ValueError:
        raise InvalidURL("malformed port")
    hostname = parts.hostname or ""
    if not hostname or not _HOST_OK.match(hostname):
        raise InvalidURL("missing or malformed host")
    if ":" in hostname:                 # an IPv6 literal (urlsplit strips the brackets)
        hostname = "[" + hostname + "]"
    for component in (parts.path, parts.query, parts.fragment):
        if _BAD_PCT.search(component):
            raise InvalidURL("malformed percent-escape")
    if port is not None and ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        port = None
    return scheme, hostname, port, parts.path, parts.query, parts.fragment


def canonicalize(url: str, version: int = VERSION) -> Canonical:
    """The canonical form of a URL under a frozen rule set (1 or 2); raises InvalidURL."""
    if version not in VERSIONS:
        raise ValueError("unknown canonicalization version: %r (versions: %s)" % (version, ", ".join(map(str, VERSIONS))))
    scheme, host, port, path, query, fragment = _split(url)
    path = _normalize_pct(path)
    kept, dropped = [], []
    if query:
        for part in query.split("&"):
            key = part.split("=", 1)[0]
            if is_tracking_key(unquote(key)):
                dropped.append(key)
            else:
                kept.append(part)
    dropped_fragment = None
    if fragment and not (fragment.startswith("!") or fragment.startswith("/")):
        dropped_fragment, fragment = fragment, ""
    host_rule = None
    netloc = host if port is None else "%s:%d" % (host, port)
    # step 6 — per-host resolvers on the result of 1–5
    if host in _ARXIV_HOSTS:
        m = _ARXIV_PATH.match(path)
        if m:
            return Canonical("https://arxiv.org/abs/" + m.group("id"), version, "arxiv", dropped, dropped_fragment)
    if host in _YT_HOSTS or host == "youtu.be":
        video = _youtube_id(host, path, kept)
        if video:
            # every parameter but `v` goes (list, index, t, si, feature, pp, …): the video is the resource
            dropped_yt = [p.split("=", 1)[0] for p in kept if unquote(p.split("=", 1)[0]) != "v"]
            return Canonical("https://www.youtube.com/watch?v=" + video, version, "youtube", dropped + dropped_yt,
                             dropped_fragment)
    # version 2: the host-scoped share parameters (v1 keeps them — a replay of a v1 receipt binds the same key)
    share_rule = _host_share_rule(host) if version >= 2 else None
    if share_rule:
        kept, dropped_share = _drop_share_keys(kept, share_rule)
        if dropped_share:
            dropped = dropped + dropped_share
            host_rule = share_rule
    result = scheme + "://" + netloc + path
    if kept:
        result += "?" + "&".join(kept)
    if fragment:
        result += "#" + fragment
    return Canonical(result, version, host_rule, dropped, dropped_fragment)


def _youtube_id(host: str, path: str, query_parts) -> Optional[str]:
    if host == "youtu.be":
        m = re.match(r"^/([A-Za-z0-9_-]+)/?$", path)
        return m.group(1) if m else None
    if path.rstrip("/") == "/watch":
        for part in query_parts:
            key, _, value = part.partition("=")
            if unquote(key) == "v":
                value = unquote(value)
                return value if value and _YT_ID.match(value) else None
        return None
    m = _YT_PATH.match(path)
    return m.group(1) if m else None


def arxiv_parts(url: str):
    """(id, requested_version|None) of an arXiv abs/pdf/html URL, else None."""
    try:
        _, host, _, path, _, _ = _split(url)
    except InvalidURL:
        return None
    if host not in _ARXIV_HOSTS:
        return None
    m = _ARXIV_PATH.match(_normalize_pct(path))
    if not m:
        return None
    return m.group("id"), (int(m.group("v")) if m.group("v") else None)


def resolve(url: str, version: int = VERSION) -> Resolution:
    """Canonical identity plus the fetch URL, the kind, the metadata URL and the requested arXiv version."""
    canonical = canonicalize(url, version)
    if canonical.host_rule == "arxiv":
        parts = arxiv_parts(url)
        arxiv_id, requested = parts if parts else (canonical.url.rsplit("/abs/", 1)[1], None)
        suffix = "v%d" % requested if requested else ""
        return Resolution(canonical, "https://arxiv.org/html/%s%s" % (arxiv_id, suffix), "arxiv",
                          "https://arxiv.org/abs/%s%s" % (arxiv_id, suffix), requested)
    if canonical.host_rule == "youtube":
        return Resolution(canonical, canonical.url, "youtube", None, None)
    host = urlsplit(canonical.url).hostname or ""
    if host in _GITHUB_HOSTS:
        return Resolution(canonical, canonical.url, "github", None, None)
    return Resolution(canonical, canonical.url, "page", None, None)


def canonical_object(url: str, version: int = VERSION) -> dict:
    """The `mnemon-canonical/1` object of `capture-fetch.py --canonical-only` (no network)."""
    res = resolve(url, version)
    return {"schema": "mnemon-canonical/1", "url": url.strip(), "canonical_url": res.canonical.url,
            "canonical_version": res.canonical.version, "kind": res.kind, "fetch_url": res.fetch_url,
            "requested_version": res.requested_version}
