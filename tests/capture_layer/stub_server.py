#!/usr/bin/env python3
"""stub_server.py — a file-backed local HTTP server for the bash suites (tests/test_depth.sh).

    stub_server.py --root <dir> [--delay <path>=<seconds>]… [--status <path>=<code>]…

Serves the files under --root on 127.0.0.1 (a directory serves its index.html; the content type follows
the extension), prints the port on stdout and serves until killed. Used under the §3.2 test hooks
(`MNEMON_TEST_HOOKS=1 MNEMON_POLICY_ALLOW_LOOPBACK=1 MNEMON_FETCH_URL_REWRITE=…`) so a suite can point the
capture policy at it without any network. Stdlib only, python 3.9.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TYPES = {".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".json": "application/json",
         ".xml": "application/xml", ".md": "text/plain; charset=utf-8", ".pdf": "application/pdf"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--delay", action="append", default=[], metavar="PATH=SECONDS")
    ap.add_argument("--status", action="append", default=[], metavar="PATH=CODE")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    delays = {k: float(v) for k, _, v in (d.partition("=") for d in args.delay)}
    statuses = {k: int(v) for k, _, v in (s.partition("=") for s in args.status)}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in delays:
                time.sleep(delays[path])
            rel = path.strip("/")
            full = os.path.normpath(os.path.join(root, rel)) if rel else root
            if not full.startswith(root):
                full = ""
            if full and os.path.isdir(full):
                full = os.path.join(full, "index.html")
            status = statuses.get(path)
            if not full or not os.path.isfile(full):
                body = b"<html><body><p>not found</p></body></html>"
                self.send_response(status or 404)
                self.send_header("Content-Type", "text/html")
            else:
                with open(full, "rb") as fh:
                    body = fh.read()
                self.send_response(status or 200)
                self.send_header("Content-Type", TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    print(httpd.server_address[1], flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
