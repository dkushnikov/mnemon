"""capture.policy — the one network policy of the engine's own requests (spec §3.2, D3).

`Policy.get(url)` is the only way the capture layer talks HTTP: `http`/`https` only, no userinfo, every
hop's host resolved and **every** returned address checked (private, loopback, link-local, reserved,
multicast, unspecified → `ssrf-blocked` on the first hop, `ssrf-redirect` after a redirect), the connection
pinned to the checked address (the socket is opened on the resolved sockaddr — never a second lookup — and
TLS is verified against the host name with SNI = host; the `Host` header is the host name), at most
`max_redirects` redirects each re-checked before it is followed, a byte cap measured on the decoded body
(`too-large` is an error, never a truncated body; a `Content-Length` above the cap stops before any body is
read), connect/read timeouts bounded by the caller's deadline, one User-Agent, no cookies, no
`Authorization`, no proxy of any kind (`http.client` directly; the `*_PROXY` environment is never read).
DNS resolution runs in a daemon thread bounded by the remaining deadline (§12 A9). An HTTP error status is
not an exception: the response comes back with its status and body for classification (§3.4).

Test hooks, honoured only under `MNEMON_TEST_HOOKS=1`: `MNEMON_POLICY_ALLOW_LOOPBACK=1` admits a local test
server; `MNEMON_FETCH_URL_REWRITE="https://arxiv.org=http://127.0.0.1:PORT;…"` rewrites the prefix of the
transport URL (the logical URL, the hops and the identity are untouched); `MNEMON_EXTERNAL_FETCH_CMD` is read
by the fetch chain. Without the master switch the three are ignored and a warning names them.
Stdlib only, python 3.9.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import sys
import threading
import time
import zlib
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from . import proc

DEFAULT_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/130.0 Safari/537.36")
DEFAULT_FETCH_TIMEOUT = 60
DEFAULT_MAX_BYTES = 20000000
DEFAULT_MAX_REDIRECTS = 5
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
RETRYABLE_KINDS = ("timeout", "connection", "tls")
HOOK_VARS = ("MNEMON_POLICY_ALLOW_LOOPBACK", "MNEMON_FETCH_URL_REWRITE", "MNEMON_EXTERNAL_FETCH_CMD")
CHUNK = 65536

_warned = False


class PolicyError(Exception):
    """A refused or failed request; `kind` is the class the reports carry."""

    def __init__(self, kind: str, message: str = "", url: Optional[str] = None):
        super().__init__(message or kind)
        self.kind = kind
        self.message = message or kind
        self.url = url

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    def __str__(self) -> str:
        return self.message


class Response:
    """One finished request: the final logical URL, the status, the headers, the decoded body and the hops."""

    def __init__(self, final_url: str, status: int, headers: Dict[str, str], body: bytes, hops: List[dict],
                 elapsed_ms: int, reason: str = ""):
        self.final_url = final_url
        self.status = status
        self.headers = headers
        self.body = body
        self.hops = hops
        self.elapsed_ms = elapsed_ms
        self.reason = reason

    @property
    def retry_after(self) -> Optional[str]:
        return self.headers.get("retry-after")

    @property
    def content_type(self) -> Optional[str]:
        ct = self.headers.get("content-type")
        return ct.split(";", 1)[0].strip().lower() if ct else None

    @property
    def charset(self) -> Optional[str]:
        ct = self.headers.get("content-type") or ""
        for part in ct.split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.strip().lower() == "charset" and v.strip():
                return v.strip().strip('"').lower()
        return None

    def text(self, errors: str = "replace") -> str:
        return self.body.decode(self.charset or "utf-8", errors) if self.body else ""


# --- test hooks --------------------------------------------------------------------------------------------

def hooks_enabled() -> bool:
    return os.environ.get("MNEMON_TEST_HOOKS") == "1"


def hook(name: str) -> Optional[str]:
    """The value of a test hook, or None when the master switch is off (the hook is then ignored)."""
    if not hooks_enabled():
        return None
    value = os.environ.get(name)
    return value if value else None


def warn_ignored_hooks() -> List[str]:
    """Name, once per process, every test variable set without MNEMON_TEST_HOOKS=1; returns the names."""
    global _warned
    if hooks_enabled():
        return []
    names = [v for v in HOOK_VARS if os.environ.get(v)]
    if names and not _warned:
        _warned = True
        print("WARNING: %s ignored: MNEMON_TEST_HOOKS=1 is not set (test hooks are off)" % ", ".join(names),
              file=sys.stderr)
    return names


def allow_loopback() -> bool:
    return hook("MNEMON_POLICY_ALLOW_LOOPBACK") == "1"


def rewrite_url(url: str) -> str:
    """The transport form of a fetch URL under MNEMON_FETCH_URL_REWRITE (prefix pairs `src=dst;…`)."""
    spec = hook("MNEMON_FETCH_URL_REWRITE")
    if not spec:
        return url
    for pair in spec.split(";"):
        src, sep, dst = pair.partition("=")
        if sep and src and url.startswith(src):
            return dst + url[len(src):]
    return url


# --- address checks ----------------------------------------------------------------------------------------

def address_problem(address: str, loopback_ok: bool = False) -> Optional[str]:
    """Why an address is refused (private, loopback, link-local, reserved, multicast, unspecified), or None."""
    text = address.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return "unparseable address %r" % address
    candidates = [ip]
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    for cand in candidates:
        if cand.is_loopback:
            if loopback_ok:
                continue
            return "loopback address %s" % cand
        if cand.is_unspecified:
            return "unspecified address %s" % cand
        if cand.is_multicast:
            return "multicast address %s" % cand
        if cand.is_link_local:
            return "link-local address %s" % cand
        if cand.is_private:
            return "private address %s" % cand
        if cand.is_reserved:
            return "reserved address %s" % cand
    return None


def _resolve(host: str, port: int, deadline: proc.Deadline):
    """getaddrinfo in a daemon thread bounded by the remaining deadline (abandoned on expiry → `timeout`)."""
    result = {}

    def work():
        try:
            result["infos"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:       # a hostname getaddrinfo cannot encode is unresolvable
            result["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    bound = deadline.remaining()
    thread.join(bound if bound is not None else 60.0)
    if thread.is_alive():
        raise PolicyError("timeout", "DNS resolution of %s exceeded the deadline" % host)
    if "error" in result:
        raise PolicyError("connection", "cannot resolve %s: %s" % (host, result["error"]))
    infos = result.get("infos") or []
    if not infos:
        raise PolicyError("connection", "cannot resolve %s: no addresses" % host)
    return infos


class _PinnedConnection(http.client.HTTPConnection):
    """An HTTP(S) connection opened on a checked sockaddr; TLS verified against the host name (SNI = host)."""

    def __init__(self, host: str, port: int, family, sockaddr, tls: bool, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._pin_family = family
        self._pin_sockaddr = sockaddr
        self._pin_tls = tls
        self._tls_host = host

    def connect(self):
        sock = socket.socket(self._pin_family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._pin_sockaddr)
            if self._pin_tls:
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=self._tls_host)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _split_url(url: str, first: bool):
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise PolicyError("scheme", "scheme %r is not http or https" % (parts.scheme or ""), url)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise PolicyError("userinfo", "userinfo in a URL is refused", url)
    try:
        port = parts.port
    except ValueError:
        raise PolicyError("invalid-url", "malformed port", url)
    host = parts.hostname or ""
    if not host:
        raise PolicyError("invalid-url", "missing host", url)
    if port is None:
        port = 443 if scheme == "https" else 80
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, port, path


class Policy:
    """The fetch policy of one instance (`fetch_user_agent`, `fetch_timeout`, `fetch_max_bytes`)."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, connect_timeout: float = 15.0,
                 read_timeout: float = 45.0, max_bytes: int = DEFAULT_MAX_BYTES,
                 max_redirects: int = DEFAULT_MAX_REDIRECTS, min_interval_s: float = 0.0):
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_bytes = int(max_bytes)
        self.max_redirects = int(max_redirects)
        self.min_interval_s = float(min_interval_s)
        self._last_request = {}
        warn_ignored_hooks()

    @classmethod
    def from_timeout(cls, fetch_timeout: float, **kw) -> "Policy":
        """connect = min(15, T/4), read = T − connect (§3.2)."""
        t = float(fetch_timeout)
        connect = min(15.0, t / 4.0)
        return cls(connect_timeout=connect, read_timeout=max(0.1, t - connect), **kw)

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "Policy":
        """A policy from the instance config keys or from a report's `policy` block (both accepted)."""
        config = dict(config or {})
        ua = config.get("user_agent") or config.get("fetch_user_agent") or DEFAULT_USER_AGENT
        max_bytes = config.get("max_bytes", config.get("fetch_max_bytes", DEFAULT_MAX_BYTES))
        max_redirects = config.get("max_redirects", DEFAULT_MAX_REDIRECTS)
        if "connect_timeout" in config or "read_timeout" in config:
            return cls(ua, config.get("connect_timeout", 15), config.get("read_timeout", 45), max_bytes, max_redirects)
        return cls.from_timeout(config.get("fetch_timeout", DEFAULT_FETCH_TIMEOUT), user_agent=ua,
                                max_bytes=max_bytes, max_redirects=max_redirects)

    def as_report(self) -> dict:
        return {"user_agent": self.user_agent, "max_bytes": self.max_bytes, "max_redirects": self.max_redirects,
                "connect_timeout": self.connect_timeout, "read_timeout": self.read_timeout}

    def with_max_bytes(self, max_bytes: int) -> "Policy":
        p = Policy(self.user_agent, self.connect_timeout, self.read_timeout, max_bytes, self.max_redirects,
                   self.min_interval_s)
        p._last_request = self._last_request
        return p

    # --- the request -------------------------------------------------------------------------------------

    def _pace(self, host: str, deadline: proc.Deadline) -> None:
        if self.min_interval_s <= 0:
            return
        last = self._last_request.get(host)
        if last is not None:
            wait = last + self.min_interval_s - time.monotonic()
            if wait > 0:
                rem = deadline.remaining()
                if rem is not None and wait > rem:
                    raise PolicyError("timeout", "pacing %s exceeds the deadline" % host)
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, *, deadline: Optional[proc.Deadline] = None,
            hop_check: Optional[Callable[[str], Optional[str]]] = None, method: str = "GET") -> Response:
        """GET a URL under the policy; raises PolicyError(kind) — an HTTP error status is returned, not raised."""
        deadline = deadline or proc.Deadline.never()
        start = time.monotonic()
        hops: List[dict] = []
        current = url.strip()
        while True:
            if deadline.expired():
                raise PolicyError("timeout", "deadline reached before the request to %s" % current, current)
            first = not hops
            if hop_check is not None:
                reason = hop_check(current)
                if reason:
                    raise PolicyError("hop-rejected", reason, current)
            transport = rewrite_url(current)
            scheme, host, port, path = _split_url(transport, first)
            _split_url(current, first)   # the logical URL is held to the same rules
            infos = _resolve(host, port, deadline)
            loopback_ok = allow_loopback()
            for info in infos:
                problem = address_problem(info[4][0], loopback_ok)
                if problem:
                    raise PolicyError("ssrf-blocked" if first else "ssrf-redirect",
                                      "%s resolves to a %s" % (host, problem), current)
            family, _, _, _, sockaddr = infos[0]
            address = sockaddr[0]
            self._pace(host, deadline)
            connect_bound = deadline.bound(self.connect_timeout)
            conn = _PinnedConnection(host, port, family, sockaddr, scheme == "https", connect_bound or 0.1)
            try:
                try:
                    conn.connect()
                    conn.sock.settimeout(deadline.bound(self.read_timeout) or 0.1)
                    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                    hosthdr = "[%s]" % host if ":" in host else host
                    conn.putheader("Host", hosthdr if port in (80, 443) else "%s:%d" % (hosthdr, port))
                    conn.putheader("User-Agent", self.user_agent)
                    conn.putheader("Accept", ACCEPT)
                    conn.putheader("Accept-Encoding", "gzip, deflate")
                    conn.putheader("Accept-Language", "en")
                    conn.putheader("Connection", "close")
                    conn.endheaders()
                    resp = conn.getresponse()
                    headers = {}
                    for name, value in resp.getheaders():
                        key = name.lower()
                        headers[key] = value if key not in headers else headers[key] + ", " + value
                    hops.append({"url": current, "status": resp.status, "address": address})
                    location = headers.get("location")
                    if resp.status in REDIRECT_STATUSES and location:
                        if len(hops) > self.max_redirects:
                            raise PolicyError("too-many-redirects",
                                              "more than %d redirects" % self.max_redirects, current)
                        target = urljoin(current, location.strip())
                        tscheme = urlsplit(target).scheme.lower()
                        if tscheme not in ("http", "https"):
                            raise PolicyError("scheme", "redirect to a %s: URL refused" % (tscheme or "relative"), target)
                        current = target
                        continue
                    body = self._read_body(conn, resp, headers, deadline, current)
                except socket.timeout:
                    raise PolicyError("timeout", "timed out talking to %s" % host, current)
                except ssl.SSLError as exc:
                    raise PolicyError("tls", "TLS failure with %s: %s" % (host, exc), current)
                except (http.client.HTTPException, ConnectionError, OSError) as exc:
                    if isinstance(exc, socket.timeout):
                        raise PolicyError("timeout", "timed out talking to %s" % host, current)
                    raise PolicyError("connection", "connection to %s failed: %s" % (host, exc), current)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            elapsed = int((time.monotonic() - start) * 1000)
            return Response(current, resp.status, headers, body, hops, elapsed, resp.reason or "")

    def _read_body(self, conn, resp, headers: Dict[str, str], deadline: proc.Deadline, url: str) -> bytes:
        length = headers.get("content-length")
        if length is not None:
            try:
                if int(length.split(",")[0].strip()) > self.max_bytes:
                    raise PolicyError("too-large", "Content-Length %s exceeds the cap %d" % (length, self.max_bytes), url)
            except ValueError:
                pass
        encoding = (headers.get("content-encoding") or "").strip().lower()
        decomp = None
        raw_deflate = False
        if encoding in ("gzip", "x-gzip"):
            decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decomp = zlib.decompressobj()
        chunks = []
        total = 0
        while True:
            if deadline.expired():
                raise PolicyError("timeout", "deadline reached while reading %s" % url, url)
            if conn.sock is not None:
                conn.sock.settimeout(deadline.bound(self.read_timeout) or 0.1)
            piece = resp.read(CHUNK)
            if not piece:
                break
            if decomp is not None:
                try:
                    piece = decomp.decompress(piece)
                except zlib.error:
                    if encoding == "deflate" and not raw_deflate and not chunks:
                        raw_deflate = True
                        decomp = zlib.decompressobj(-zlib.MAX_WBITS)
                        piece = decomp.decompress(piece)
                    else:
                        raise PolicyError("connection", "corrupt %s body from %s" % (encoding, url), url)
            total += len(piece)
            if total > self.max_bytes:
                raise PolicyError("too-large", "body exceeds the cap of %d bytes" % self.max_bytes, url)
            chunks.append(piece)
        if decomp is not None:
            tail = decomp.flush()
            total += len(tail)
            if total > self.max_bytes:
                raise PolicyError("too-large", "body exceeds the cap of %d bytes" % self.max_bytes, url)
            chunks.append(tail)
        return b"".join(chunks)


def default_policy() -> Policy:
    """The policy of a helper without an instance config (defaults of §5)."""
    return Policy.from_timeout(DEFAULT_FETCH_TIMEOUT)
