"""§7.3 — the network policy: SSRF checks before any socket, pinned connections, redirects, caps, no proxies,
no credentials, the test hooks."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from _helpers import BIN, LocalServer, PY, apply_env, clean_env, restore_env

from capture import policy as P
from capture import proc


def addrinfo(*addresses):
    out = []
    for a in addresses:
        family = socket.AF_INET6 if ":" in a else socket.AF_INET
        sockaddr = (a, 80, 0, 0) if family == socket.AF_INET6 else (a, 80)
        out.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return out


class PolicyBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalServer()
        cls.old_env = apply_env(clean_env())

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        restore_env(cls.old_env)

    def setUp(self):
        self.server.reset()
        self.server.routes.clear()

    def policy(self, **kw):
        return P.Policy(connect_timeout=5, read_timeout=5, max_bytes=kw.pop("max_bytes", 1000000), **kw)


class AddressChecks(PolicyBase):
    def test_private_ranges_refused_before_any_socket(self):
        os.environ.pop("MNEMON_POLICY_ALLOW_LOOPBACK", None)
        try:
            for answer in (["127.0.0.1"], ["10.0.0.1"], ["169.254.1.1"], ["::1"], ["192.168.1.9"], ["0.0.0.0"],
                           ["224.0.0.1"], ["fe80::1"], ["8.8.8.8", "10.0.0.1"], ["::ffff:127.0.0.1"],
                           ["100.64.0.1"], ["100.127.255.254"], ["192.0.0.8"], ["198.18.0.1"]):
                with mock.patch.object(socket, "getaddrinfo", return_value=addrinfo(*answer)), \
                        mock.patch.object(P._PinnedConnection, "connect", side_effect=AssertionError("socket opened")):
                    with self.assertRaises(P.PolicyError) as ctx:
                        self.policy().get("http://victim.test/x")
                    self.assertEqual(ctx.exception.kind, "ssrf-blocked", answer)
                    self.assertFalse(ctx.exception.retryable)
        finally:
            os.environ["MNEMON_POLICY_ALLOW_LOOPBACK"] = "1"

    def test_public_address_accepted_by_the_checker(self):
        self.assertIsNone(P.address_problem("8.8.8.8"))
        self.assertIsNotNone(P.address_problem("203.0.113.9"), "a documentation range is reserved")
        self.assertIsNone(P.address_problem("2606:4700::1111"))
        self.assertIsNotNone(P.address_problem("127.0.0.1"))
        self.assertIsNone(P.address_problem("127.0.0.1", loopback_ok=True))
        self.assertIsNotNone(P.address_problem("10.1.1.1", loopback_ok=True))

    def test_non_global_ranges_refused_on_every_python(self):
        """RFC 6598 shared space (a tailnet, carrier NAT), the IETF protocol block, benchmarking and
        documentation ranges, and IPv6 addresses embedding a private IPv4 one (6to4, Teredo, NAT64)."""
        for a in ("100.64.0.1", "100.127.255.254", "192.0.0.8", "198.18.0.1", "198.19.255.1", "192.0.2.1", "240.0.0.1",
                  "2001:db8::1", "2002:c0a8:101::1", "2002:0a00:0001::1", "64:ff9b::a00:1", "::ffff:10.0.0.1",
                  "2001:0:4136:e378:8000:63bf:3fff:fdd2"):
            self.assertIsNotNone(P.address_problem(a), a)
        for a in ("100.63.255.255", "100.128.0.1", "198.17.255.255", "198.20.0.1", "1.1.1.1", "2606:4700::1111"):
            self.assertIsNone(P.address_problem(a), a)

    def test_loopback_allowed_only_under_the_hook(self):
        self.server.add("/ok", "<html><body><p>fine</p></body></html>")
        os.environ["MNEMON_TEST_HOOKS"] = "1"
        os.environ["MNEMON_POLICY_ALLOW_LOOPBACK"] = "1"
        r = self.policy().get(self.server.url("/ok"))
        self.assertEqual(r.status, 200)
        self.assertEqual(r.hops[0]["address"], "127.0.0.1")
        os.environ.pop("MNEMON_POLICY_ALLOW_LOOPBACK")
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get(self.server.url("/ok"))
        self.assertEqual(ctx.exception.kind, "ssrf-blocked")
        os.environ["MNEMON_POLICY_ALLOW_LOOPBACK"] = "1"

    def test_redirect_to_private_host_refused_at_the_hop(self):
        self.server.add("/r", "", status=302, headers=[("Location", "http://10.0.0.1/secret")])
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get(self.server.url("/r"))
        self.assertEqual(ctx.exception.kind, "ssrf-redirect")
        self.assertEqual(self.server.count("/secret"), 0)

    def test_connection_pinned_to_the_first_checked_address(self):
        """A rebinding answer (the second resolution differs) cannot move the connection."""
        self.server.add("/pin", "<html><body><p>pinned</p></body></html>")
        port = self.server.port
        answers = iter([
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))],
        ])
        calls = []

        def fake_getaddrinfo(host, port_, *a, **kw):
            calls.append(host)
            return next(answers)

        with mock.patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
            r = self.policy().get("http://pinned.test:%d/pin" % port)
            self.assertEqual(r.status, 200)
            self.assertEqual(r.hops[0]["address"], "127.0.0.1")
            self.assertEqual(calls, ["pinned.test"], "one resolution per hop, never a second lookup at connect")
            self.assertEqual(self.server.count("/pin"), 1)
            self.assertEqual(self.server.requests("/pin")[0][2].get("host"), "pinned.test:%d" % port)
            with self.assertRaises(P.PolicyError) as ctx:    # the rebinding answer itself is refused
                self.policy().get("http://pinned.test:%d/pin" % port)
            self.assertEqual(ctx.exception.kind, "ssrf-blocked")


class Redirects(PolicyBase):
    def test_five_redirects_followed_sixth_refused(self):
        for i in range(1, 8):
            self.server.add("/r%d" % i, "", status=301, headers=[("Location", "/r%d" % (i + 1))])
        self.server.add("/r6", "<html><body>end</body></html>")
        r = self.policy().get(self.server.url("/r1"))
        self.assertEqual(r.status, 200)
        self.assertEqual(len(r.hops), 6)
        self.assertEqual(r.final_url, self.server.url("/r6"))
        self.server.add("/r6", "", status=302, headers=[("Location", "/r7")])
        self.server.add("/r7", "<html><body>end</body></html>")
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get(self.server.url("/r1"))
        self.assertEqual(ctx.exception.kind, "too-many-redirects")
        self.assertEqual(self.server.count("/r7"), 0)

    def test_redirect_to_file_data_ftp_refused(self):
        for target in ("file:///etc/passwd", "data:text/html,hi", "ftp://example.com/x"):
            self.server.add("/r", "", status=302, headers=[("Location", target)])
            with self.assertRaises(P.PolicyError) as ctx:
                self.policy().get(self.server.url("/r"))
            self.assertEqual(ctx.exception.kind, "scheme", target)

    def test_relative_location_and_retry_after(self):
        self.server.add("/a", "", status=307, headers=[("Location", "b?x=1")])
        self.server.add("/b", "<html><body>b</body></html>", headers=[("Retry-After", "120")])
        r = self.policy().get(self.server.url("/a"))
        self.assertEqual(r.final_url, self.server.url("/b?x=1"))
        self.assertEqual(r.retry_after, "120")
        self.assertEqual([h["status"] for h in r.hops], [307, 200])

    def test_hop_check_rejects_before_the_request(self):
        self.server.add("/a", "", status=302, headers=[("Location", "/b")])
        self.server.add("/b", "<html><body>b</body></html>")
        seen = []

        def hop(url):
            seen.append(url)
            return "robots" if url.endswith("/b") else None

        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get(self.server.url("/a"), hop_check=hop)
        self.assertEqual(ctx.exception.kind, "hop-rejected")
        self.assertEqual(ctx.exception.message, "robots")
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.server.count("/b"), 0)


class Caps(PolicyBase):
    def test_content_length_above_the_cap_refused_before_the_body(self):
        self.server.add("/big", b"x" * 5000)
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy(max_bytes=4999).get(self.server.url("/big"))
        self.assertEqual(ctx.exception.kind, "too-large")
        r = self.policy(max_bytes=5000).get(self.server.url("/big"))
        self.assertEqual(len(r.body), 5000)

    def test_chunked_body_refused_at_exactly_cap_plus_one(self):
        self.server.add("/c1000", b"y" * 1000, chunked=True)
        self.server.add("/c1001", b"y" * 1001, chunked=True)
        self.assertEqual(len(self.policy(max_bytes=1000).get(self.server.url("/c1000")).body), 1000)
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy(max_bytes=1000).get(self.server.url("/c1001"))
        self.assertEqual(ctx.exception.kind, "too-large")

    def test_gzip_body_counted_decoded(self):
        self.server.add("/gz", b"z" * 1000, gzip_body=True)
        r = self.policy(max_bytes=1000).get(self.server.url("/gz"))
        self.assertEqual(r.body, b"z" * 1000)
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy(max_bytes=999).get(self.server.url("/gz"))
        self.assertEqual(ctx.exception.kind, "too-large")

    def test_decompression_bounded_per_chunk(self):
        """A few KB of gzip that decode to megabytes never expand past the room left under the cap."""
        self.server.add("/bomb", b"\0" * 3000000, gzip_body=True)
        sizes = []
        real = P.zlib.decompressobj

        class Recording:
            def __init__(self, *a, **kw):
                self.d = real(*a, **kw)

            def decompress(self, data, max_length=0):
                out = self.d.decompress(data, max_length)
                sizes.append(len(out))
                return out

            @property
            def unconsumed_tail(self):
                return self.d.unconsumed_tail

            def flush(self):
                return self.d.flush()

        with mock.patch.object(P.zlib, "decompressobj", Recording):
            with self.assertRaises(P.PolicyError) as ctx:
                self.policy(max_bytes=10000).get(self.server.url("/bomb"))
        self.assertEqual(ctx.exception.kind, "too-large")
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), 10001, "one chunk decoded past the cap")

    def test_trickling_body_is_cut_at_the_absolute_deadline(self):
        """One byte every 50 ms keeps every recv under the inactivity timeout; the absolute deadline still wins."""
        def trickle(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html")
            handler.send_header("Content-Length", "100000")
            handler.send_header("Connection", "close")
            handler.end_headers()
            try:
                for _ in range(200):
                    handler.wfile.write(b"x")
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass
            return None

        self.server.add("/trickle", handler=trickle)
        p = P.Policy(connect_timeout=5, read_timeout=5, max_bytes=1000000)
        t0 = time.monotonic()
        with self.assertRaises(P.PolicyError) as ctx:
            p.get(self.server.url("/trickle"), deadline=proc.Deadline(1.0))
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertLess(time.monotonic() - t0, 4.0, "the read outlived the deadline")


class Scheme(PolicyBase):
    def test_ftp_and_userinfo_refused(self):
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get("ftp://example.com/x")
        self.assertEqual(ctx.exception.kind, "scheme")
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get("https://user:pw@h/")
        self.assertEqual(ctx.exception.kind, "userinfo")
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get("https://h:abc/")
        self.assertEqual(ctx.exception.kind, "invalid-url")


class Transport(PolicyBase):
    def test_no_cookie_or_authorization_ever_sent(self):
        self.server.add("/h", "<html><body>h</body></html>")
        self.policy().get(self.server.url("/h"))
        headers = self.server.requests("/h")[0][2]
        self.assertNotIn("cookie", headers)
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers.get("user-agent"), P.DEFAULT_USER_AGENT)
        self.assertEqual(headers.get("host"), "127.0.0.1:%d" % self.server.port)

    def test_proxy_environment_ignored(self):
        seen = []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(0.2)
        stop = threading.Event()

        def accept_loop():
            while not stop.is_set():
                try:
                    c, _ = listener.accept()
                except socket.timeout:
                    continue
                seen.append(1)
                c.close()

        t = threading.Thread(target=accept_loop, daemon=True)
        t.start()
        proxy = "http://127.0.0.1:%d" % listener.getsockname()[1]
        self.server.add("/p", "<html><body>p</body></html>")
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            os.environ[var] = proxy
        try:
            r = self.policy().get(self.server.url("/p"))
        finally:
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
                os.environ.pop(var, None)
            stop.set()
            t.join(1)
            listener.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(seen, [], "the proxy stub saw a connection")

    def test_http_error_status_is_returned_not_raised(self):
        self.server.add("/500", "<html><body>down</body></html>", status=500, headers=[("Retry-After", "30")])
        r = self.policy().get(self.server.url("/500"))
        self.assertEqual(r.status, 500)
        self.assertEqual(r.retry_after, "30")
        self.assertEqual(r.content_type, "text/html")
        self.assertEqual(r.charset, "utf-8")

    def test_dns_failure_is_connection_and_read_timeout_is_timeout(self):
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get("http://nonexistent.invalid/x")
        self.assertEqual(ctx.exception.kind, "connection")
        self.assertTrue(ctx.exception.retryable)
        self.server.add("/slow", "<html><body>slow</body></html>", delay=2.0)
        p = P.Policy(connect_timeout=2, read_timeout=0.4, max_bytes=1000)
        with self.assertRaises(P.PolicyError) as ctx:
            p.get(self.server.url("/slow"))
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_deadline_bounds_the_request(self):
        self.server.add("/slow2", "<html><body>slow</body></html>", delay=2.0)
        with self.assertRaises(P.PolicyError) as ctx:
            self.policy().get(self.server.url("/slow2"), deadline=proc.Deadline(0.5))
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_header_phase_watchdog_cuts_a_trickling_header(self):
        """G11: one header byte per second keeps every receive under the inactivity timeout, and the status
        line and headers are read with no per-chunk check of ours; the watchdog armed at the deadline shuts
        the socket down, so the request is a `timeout` at the deadline, not after the last header byte."""
        def trickle_headers(handler):
            handler.wfile.write(b"HTTP/1.1 200 OK\r\n")
            handler.wfile.flush()
            try:
                for ch in b"X-Slow: " + b"a" * 20 + b"\r\nContent-Length: 2\r\n\r\nok":
                    handler.wfile.write(bytes([ch]))
                    handler.wfile.flush()
                    time.sleep(1.0)
            except OSError:
                pass
            return None

        self.server.add("/slow-headers", handler=trickle_headers)
        p = P.Policy(connect_timeout=5, read_timeout=5, max_bytes=1000)
        t0 = time.monotonic()
        with self.assertRaises(P.PolicyError) as ctx:
            p.get(self.server.url("/slow-headers"), deadline=proc.Deadline(2.0))
        elapsed = time.monotonic() - t0
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertIn("headers", ctx.exception.message)
        self.assertTrue(ctx.exception.retryable)
        self.assertLess(elapsed, 5.0, "the header read outlived the deadline (%.1f s)" % elapsed)
        self.assertGreaterEqual(elapsed, 1.5, "the watchdog fired before the deadline")

    def test_url_rewrite_hook_keeps_the_logical_url(self):
        self.server.add("/abs/2401.00001", "<html><body>abs</body></html>")
        os.environ["MNEMON_FETCH_URL_REWRITE"] = "https://arxiv.org=%s;https://www.youtube.com=%s" % (self.server.base, self.server.base)
        try:
            r = self.policy().get("https://arxiv.org/abs/2401.00001")
        finally:
            os.environ.pop("MNEMON_FETCH_URL_REWRITE")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.final_url, "https://arxiv.org/abs/2401.00001")
        self.assertEqual(r.hops[0]["url"], "https://arxiv.org/abs/2401.00001")
        self.assertEqual(self.server.count("/abs/2401.00001"), 1)

    def test_pacing_between_requests_to_one_host(self):
        self.server.add("/p1", "<html><body>1</body></html>")
        p = self.policy(min_interval_s=0.4)
        t0 = time.monotonic()
        p.get(self.server.url("/p1"))
        p.get(self.server.url("/p1"))
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)

    def test_timeout_split_and_report(self):
        p = P.Policy.from_timeout(60)
        self.assertEqual((p.connect_timeout, p.read_timeout), (15.0, 45.0))
        p = P.Policy.from_timeout(8)
        self.assertEqual((p.connect_timeout, p.read_timeout), (2.0, 6.0))
        p = P.Policy.from_config({"fetch_user_agent": "ua/1", "fetch_timeout": 20, "fetch_max_bytes": 12})
        self.assertEqual(p.as_report(), {"user_agent": "ua/1", "max_bytes": 12, "max_redirects": 5,
                                         "connect_timeout": 5.0, "read_timeout": 15.0})
        p2 = P.Policy.from_config(p.as_report())
        self.assertEqual(p2.as_report(), p.as_report())


class HooksOff(unittest.TestCase):
    def test_hooks_ignored_without_master_switch_with_a_warning(self):
        server = LocalServer()
        try:
            server.add("/x", "<html><body>x</body></html>")
            env = clean_env(hooks=False)
            env["MNEMON_POLICY_ALLOW_LOOPBACK"] = "1"
            env["MNEMON_FETCH_URL_REWRITE"] = "https://a=https://b"
            env["MNEMON_EXTERNAL_FETCH_CMD"] = "/bin/false"
            env["MNEMON_TEST_CONVERT_HANG"] = "1"      # the conversion hook of G25 is named like the three of §3.2
            code = ("import sys; sys.path.insert(0, %r)\n"
                    "from capture import policy as P\n"
                    "try:\n    P.Policy().get(%r)\nexcept P.PolicyError as e:\n    print(e.kind)\n") % (BIN, server.url("/x"))
            out = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env)
            self.assertEqual(out.stdout.strip(), "ssrf-blocked", out.stderr)
            for name in P.HOOK_VARS:
                self.assertIn(name, out.stderr)
            self.assertIn("MNEMON_TEST_HOOKS", out.stderr)
            self.assertEqual(server.count("/x"), 0)
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
