"""Shared scaffolding of the capture-layer suite: paths, a local HTTP server, the test-hook environment.

Every suite here is hermetic: pages come from `tests/fixtures/capture/html/`, requests go to the thread
server below under the §3.2 test hooks (`MNEMON_TEST_HOOKS=1`, loopback allowed, prefix rewrite), and the
external commands are stubs. Stdlib only, python 3.9.
"""
from __future__ import annotations

import gzip
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BIN = os.path.join(ROOT, "bin")
FIX = os.path.join(ROOT, "tests", "fixtures", "capture")
HTML_FIX = os.path.join(FIX, "html")
REPORTS_FIX = os.path.join(FIX, "reports")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

PY = sys.executable


def read_fixture(name: str, binary: bool = False):
    path = os.path.join(HTML_FIX, name)
    if binary:
        with open(path, "rb") as fh:
            return fh.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def expected_shapes(fixture_name: str) -> dict:
    return read_json(os.path.join(REPORTS_FIX, fixture_name))


def key_shape(obj):
    """The key set of an object: name → a type label (None for null); nested dicts recurse one level."""
    out = {}
    for k, v in obj.items():
        if v is None:
            out[k] = None
        elif isinstance(v, bool):
            out[k] = "bool"
        elif isinstance(v, (int, float)):
            out[k] = "number"
        elif isinstance(v, str):
            out[k] = "str"
        elif isinstance(v, list):
            out[k] = "list"
        elif isinstance(v, dict):
            out[k] = "dict"
    return out


def assert_same_keys(testcase, actual: dict, fixture: dict, where: str, nullable=()):
    """A producer's output has the fixture's keys; non-null fixture values keep their type unless listed."""
    testcase.assertEqual(sorted(actual.keys()), sorted(fixture.keys()), "%s: key set differs" % where)
    fshape, ashape = key_shape(fixture), key_shape(actual)
    for k, ftype in fshape.items():
        if ftype is None or k in nullable:
            continue
        if ashape[k] is None:
            continue    # a null where the fixture shows a value: allowed only when the spec says nullable
        testcase.assertEqual(ashape[k], ftype, "%s: %s has type %s, fixture %s" % (where, k, ashape[k], ftype))


def clean_env(extra=None, hooks=True, port=None, rewrite=None) -> dict:
    """The suite's environment: inherited keys and proxies cleared, the test hooks set when `hooks`."""
    env = dict(os.environ)
    for k in list(env):
        if k in ("FIRECRAWL_API_KEY", "JINA_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                 "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy", "RENDER_URL_CMD") or k.startswith("MNEMON_"):
            del env[k]
    if hooks:
        env["MNEMON_TEST_HOOKS"] = "1"
        env["MNEMON_POLICY_ALLOW_LOOPBACK"] = "1"
    if rewrite:
        env["MNEMON_FETCH_URL_REWRITE"] = rewrite
    if extra:
        env.update(extra)
    return env


def apply_env(env: dict):
    """Apply an environment to this process (for in-process policy tests); returns the previous one."""
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    return old


def restore_env(old: dict):
    os.environ.clear()
    os.environ.update(old)


class Route:
    """One route of the local server: a status, headers and a body — or a callable building the response."""

    def __init__(self, body=b"", status=200, headers=None, content_type="text/html; charset=utf-8",
                 handler=None, delay=0.0, chunked=False, gzip_body=False, raw=False):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = list(headers or [])
        self.content_type = content_type
        self.handler = handler
        self.delay = delay
        self.chunked = chunked
        self.gzip_body = gzip_body
        self.raw = raw


class LocalServer:
    """A threaded HTTP server with a routes dict and a request log (method, path, headers)."""

    def __init__(self):
        self.routes = {}
        self.log = []
        self.lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _serve(self):
                with server.lock:
                    server.log.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))
                route = server.routes.get(self.path)
                if route is None:
                    base = self.path.split("?", 1)[0]
                    route = server.routes.get(base)
                if route is None:
                    self.send_response(404)
                    body = b"<html><body><p>not found</p></body></html>"
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                    return
                if route.handler is not None:
                    route = route.handler(self)
                    if route is None:
                        return
                if route.delay:
                    time.sleep(route.delay)
                body = route.body
                if route.gzip_body:
                    body = gzip.compress(body)
                self.send_response(route.status)
                if route.content_type:
                    self.send_header("Content-Type", route.content_type)
                for k, v in route.headers:
                    self.send_header(k, v)
                if route.gzip_body:
                    self.send_header("Content-Encoding", "gzip")
                if route.chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command == "HEAD":
                    return
                if route.chunked:
                    for i in range(0, len(body), 4096):
                        chunk = body[i:i + 4096]
                        self.wfile.write(("%x\r\n" % len(chunk)).encode("ascii") + chunk + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(body)

            do_GET = _serve
            do_HEAD = _serve
            do_POST = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def url(self, path: str) -> str:
        return self.base + path

    def add(self, path: str, body=b"", **kw) -> None:
        self.routes[path] = Route(body, **kw)

    def requests(self, path=None):
        with self.lock:
            return [r for r in self.log if path is None or r[1] == path or r[1].split("?")[0] == path]

    def count(self, path=None) -> int:
        return len(self.requests(path))

    def reset(self) -> None:
        with self.lock:
            self.log = []

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_cli(argv, env=None, stdin=None, timeout=120, cwd=None):
    """Run a CLI of bin/ and return (rc, stdout, stderr) as text."""
    out = subprocess.run([PY] + list(argv), input=stdin, capture_output=True, env=env or clean_env(),
                         timeout=timeout, cwd=cwd)
    return out.returncode, out.stdout.decode("utf-8", "replace"), out.stderr.decode("utf-8", "replace")


def write_stub(directory: str, name: str, body: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)
    return path


def tmpdir(prefix="mnemon-cl-") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_gone(pid: int, seconds: float = 6.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)
