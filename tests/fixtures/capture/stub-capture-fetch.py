#!/usr/bin/env python3
"""capture-fetch.py stub (capture layer §3.1, §3.3): the seam contract as the fixtures freeze it."""
import json, os, re, subprocess, sys, hashlib
from urllib.parse import urlsplit, urlunsplit

STUB_DIR = os.environ.get("STUB_DIR", "")
TRACKING = {"fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid", "ttclid", "igshid", "igsh",
            "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hsCtaTracking", "mkt_tok", "_ga", "_gl", "ref_src", "ref_url",
            "hide_intro_popup"}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def bump(name):
    if not STUB_DIR:
        return
    p = os.path.join(STUB_DIR, name)
    n = 0
    if os.path.exists(p):
        n = int(open(p).read().strip() or 0)
    open(p, "w").write(str(n + 1) + "\n")


def canonical(url, version=2):
    u = url.strip()
    parts = urlsplit(u)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path
    query = "&".join(p for p in parts.query.split("&") if p and not (p.split("=", 1)[0] in TRACKING or p.split("=", 1)[0].startswith("utm_")))
    frag = parts.fragment if parts.fragment.startswith(("!", "/")) else ""
    kind, requested = "page", None
    m = re.match(r"^(?:www\.|m\.|music\.)?youtube\.com$", host)
    if m or host == "youtu.be":
        vid = None
        if host == "youtu.be":
            vid = path.strip("/").split("/")[0]
        else:
            mm = re.match(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]+)", path)
            if mm:
                vid = mm.group(1)
            elif path == "/watch":
                for p in parts.query.split("&"):
                    if p.startswith("v="):
                        vid = p[2:]
        if vid:
            return {"canonical_url": f"https://www.youtube.com/watch?v={vid}", "kind": "youtube",
                    "fetch_url": f"https://www.youtube.com/watch?v={vid}", "requested_version": None}
    if host == "arxiv.org":
        mm = re.match(r"^/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z-]+/[0-9]{7})(?:v([0-9]+))?(?:\.pdf)?/?$", path)
        if mm:
            requested = int(mm.group(2)) if mm.group(2) else None
            return {"canonical_url": f"https://arxiv.org/abs/{mm.group(1)}", "kind": "arxiv",
                    "fetch_url": f"https://arxiv.org/abs/{mm.group(1)}" + (f"v{requested}" if requested else ""),
                    "requested_version": requested}
    if host in ("github.com", "www.github.com"):
        kind = "github"
    # version 2 (GH #12): the host-scoped share parameters — X's s/t, Substack's r — mirrored here so the
    # gateway tests see the same identity the real resolver computes; v1 keeps them
    if version >= 2 and query:
        share = ("s", "t") if host in ("x.com", "www.x.com", "mobile.x.com", "m.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com", "m.twitter.com") \
            else ("r",) if host.endswith(".substack.com") and host != "www.substack.com" else ()
        if share:
            query = "&".join(p for p in query.split("&") if p.split("=", 1)[0] not in share)
    cu = urlunsplit((parts.scheme.lower(), netloc, path, query, frag))
    return {"canonical_url": cu, "kind": kind, "fetch_url": cu, "requested_version": requested}


def canonical_obj(url, version=2):
    c = canonical(url, version)
    if c is None:
        return {"error_class": "invalid-url", "error": f"not an http(s) URL without userinfo: {url.strip()}"}
    return {"schema": "mnemon-canonical/1", "url": url.strip(), "canonical_url": c["canonical_url"], "canonical_version": version,
            "kind": c["kind"], "fetch_url": c["fetch_url"], "requested_version": c["requested_version"]}


def main():
    argv = sys.argv[1:]
    if "--canonical-only" in argv:
        bump("canon-count")
        version = 2
        if "--canonical-version" in argv:
            version = int(argv[argv.index("--canonical-version") + 1])
            del argv[argv.index("--canonical-version"):argv.index("--canonical-version") + 2]
        if "--stdin" in argv:
            for line in sys.stdin.read().splitlines():
                if line.strip():
                    print(json.dumps(canonical_obj(line, version), ensure_ascii=False))
            return 0
        url = [a for a in argv if not a.startswith("--")][0]
        obj = canonical_obj(url, version)
        if "error_class" in obj:
            print(json.dumps(obj), file=sys.stderr)
            return 2
        print(json.dumps(obj, ensure_ascii=False))
        return 0
    opts = {"--out": None, "--report": None, "--expect": "page", "--policy": None, "--external": None,
            "--acquire-timeout": None, "--render-timeout": None, "--render-cmd": None, "--kind": None}
    flags = set()
    url = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in opts:
            opts[a] = argv[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            flags.add(a)
        else:
            url = a
        i += 1
    bump("fetch-count")
    if STUB_DIR:
        with open(os.path.join(STUB_DIR, "fetch-calls"), "a") as fh:
            fh.write(" ".join(argv) + "\n")
    mode = os.environ.get("STUB_FETCH", os.environ.get("STUB_CURL", "html"))
    if "--render" in flags and mode == "html":
        mode = "rendered"   # a forced render starts at stage 2 (A7): the raw is the rendered DOM
    c = canonical(url) or {"canonical_url": url, "kind": "page", "fetch_url": url, "requested_version": None}
    kind = opts["--kind"] or c["kind"]
    expect = opts["--expect"]
    policy = json.loads(opts["--policy"]) if opts["--policy"] else {}
    report = {
        "schema": "mnemon-fetch/1", "url": url, "fetch_url": c["fetch_url"], "canonical_url": c["canonical_url"],
        "canonical_version": 1, "kind": kind, "requested_version": c["requested_version"], "final_url": c["fetch_url"],
        "method": "direct", "raw_kind": "response", "http_status": 200, "content_type": "text/html", "charset": "utf-8",
        "bytes": 0, "ext": None, "audio_sniff": None, "egress": "direct",
        "stages": [{"stage": "direct", "outcome": "ok", "kind": None, "status": 200, "bytes": 0, "elapsed_ms": 12, "note": None}],
        "acquisition": {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 120},
        "metadata": {"title": None, "authors": [], "published": None, "published_source": None, "arxiv_id": None,
                     "arxiv_version": None, "arxiv_requested_version": None, "declared_canonical_url": None},
        "hops": [{"url": c["fetch_url"], "status": 200, "address": "203.0.113.10"}],
        "elapsed_ms": 12, "error_class": None, "error": None, "retryable": None, "stage": None,
        "policy": {"user_agent": policy.get("user_agent", UA), "max_bytes": policy.get("max_bytes", 20000000),
                   "max_redirects": 5, "connect_timeout": policy.get("connect_timeout", 15),
                   "read_timeout": policy.get("read_timeout", 45)},
    }
    pub = os.environ.get("STUB_FETCH_PUBLISHED", "")
    if pub:
        value, _, source = pub.partition("|")
        report["metadata"]["published"], report["metadata"]["published_source"] = value, source or None
    if kind == "arxiv":
        aid = c["canonical_url"].rsplit("/", 1)[-1]
        ver = c["requested_version"] or 3
        report["metadata"].update({"title": "ArXiv Stub Paper", "authors": ["Ada Lovelace", "Charles Babbage"],
                                   "published": "2026-01-02", "published_source": "citation_date", "arxiv_id": aid,
                                   "arxiv_version": ver, "arxiv_requested_version": c["requested_version"]})
        report["final_url"] = f"https://arxiv.org/html/{aid}v{ver}"
    raw = None
    rc = 0
    if mode == "html":
        meta = os.environ.get("STUB_FETCH_HTML_META", "").encode("utf-8")
        raw = b'<html><head><title> Stub &amp; Page\n  Title </title>' + meta + b'</head><body><h1>Hello</h1><p>World <b>bold</b> from fetch.</p></body></html>'
    elif mode == "empty":
        raw = b"<html><head><title>Empty</title></head><body></body></html>"
    elif mode == "pdf":
        raw = b"%PDF-1.7\n\x00\x00binary\n%%EOF\n"
        report.update({"content_type": "application/pdf", "raw_kind": "file"})
    elif mode == "audio":
        raw = b"RIFFstubaudio"
        report.update({"content_type": "audio/mpeg", "raw_kind": "file"})
    elif mode == "rendered":
        raw = b"<html><head><title>Rendered Page</title></head><body><h1>Rendered</h1><p>rendered text content from the DOM after scripts ran.</p></body></html>"
        report.update({"method": "render", "raw_kind": "rendered", "http_status": None, "final_url": None,
                       "content_type": None, "charset": None,
                       "stages": [{"stage": "direct", "outcome": "js-shell", "kind": "js-shell", "status": 200, "bytes": 1800, "elapsed_ms": 90, "note": "empty #root"},
                                  {"stage": "render", "outcome": "ok", "kind": None, "status": None, "bytes": 130, "elapsed_ms": 2400, "note": None}]})
    elif mode == "external":
        raw = b"<html><head><title>Vendor Page</title></head><body><h1>Vendor</h1><p>text relayed by the vendor after the wall.</p></body></html>"
        report.update({"method": "firecrawl", "egress": "external",
                       "stages": [{"stage": "direct", "outcome": "bot-wall", "kind": "bot-wall", "status": 403, "bytes": 900, "elapsed_ms": 50, "note": "cf-chl"},
                                  {"stage": "render", "outcome": "bot-wall", "kind": "bot-wall", "status": None, "bytes": 900, "elapsed_ms": 3000, "note": None},
                                  {"stage": "external", "outcome": "ok", "kind": None, "status": 200, "bytes": 120, "elapsed_ms": 900, "note": "firecrawl"}]})
    elif mode == "blocked-paywall":
        raw = b"<html><head><title>Teaser</title></head><body><p>the first paragraphs before the wall, real text that stays readable</p><div class=\"paywall\">subscribe to continue reading</div></body></html>"
        report["acquisition"] = {"outcome": "paywall", "kind": "paywall", "evidence": "subscribe to continue reading", "visible_words": 70}
        report["stages"][0].update({"outcome": "paywall", "kind": "paywall"})
    elif mode in ("blocked-botwall", "blocked-login", "rate-limit", "fail", "js-shell-noenv", "timeout"):
        rc = 1
        report.update({"method": None, "raw_kind": None, "bytes": 0})
        if mode == "blocked-botwall":
            report.update({"http_status": 403, "error_class": "blocked", "error": "bot wall: cf-chl (direct and render)", "retryable": False, "stage": "render",
                           "acquisition": {"outcome": "bot-wall", "kind": "bot-wall", "evidence": "cf-chl", "visible_words": 20},
                           "stages": [{"stage": "direct", "outcome": "bot-wall", "kind": "bot-wall", "status": 403, "bytes": 900, "elapsed_ms": 50, "note": "cf-chl"},
                                      {"stage": "render", "outcome": "bot-wall", "kind": "bot-wall", "status": None, "bytes": 900, "elapsed_ms": 3000, "note": None}]})
        elif mode == "blocked-login":
            report.update({"error_class": "blocked", "error": "login wall: sign in to continue (no escalation past a login)", "retryable": False, "stage": "direct",
                           "acquisition": {"outcome": "login", "kind": "login", "evidence": "sign in to continue", "visible_words": 41},
                           "stages": [{"stage": "direct", "outcome": "login", "kind": "login", "status": 200, "bytes": 2210, "elapsed_ms": 150, "note": "marker: sign in to continue; visible words 41"}]})
        elif mode == "rate-limit":
            report.update({"http_status": 429, "error_class": "blocked", "error": "rate limited (429, Retry-After 120)", "retryable": True, "stage": "direct",
                           "acquisition": {"outcome": "rate-limit", "kind": "rate-limit", "evidence": "429", "visible_words": 0},
                           "stages": [{"stage": "direct", "outcome": "rate-limit", "kind": "rate-limit", "status": 429, "bytes": 0, "elapsed_ms": 40, "note": "Retry-After: 120"}]})
        elif mode == "fail":
            report.update({"http_status": None, "final_url": None, "hops": [], "error_class": "connection", "error": "connection refused", "retryable": True, "stage": "direct",
                           "acquisition": {"outcome": "unavailable", "kind": None, "evidence": None, "visible_words": 0},
                           "stages": [{"stage": "direct", "outcome": "connection", "kind": None, "status": None, "bytes": 0, "elapsed_ms": 30, "note": "connection refused"}]})
        elif mode == "js-shell-noenv":
            rc = 3
            report.update({"error_class": "environment", "error": "js-shell with no renderer and no external fetcher allowed", "retryable": True, "stage": "render",
                           "acquisition": {"outcome": "js-shell", "kind": "js-shell", "evidence": "empty #root", "visible_words": 12},
                           "stages": [{"stage": "direct", "outcome": "js-shell", "kind": "js-shell", "status": 200, "bytes": 1800, "elapsed_ms": 90, "note": "empty #root; script bytes 71%"}]})
        elif mode == "timeout":
            rc = 124
            report.update({"error_class": "timeout", "error": "render stage exceeded render_timeout=90", "retryable": True, "stage": "render", "elapsed_ms": 90090,
                           "acquisition": {"outcome": "js-shell", "kind": "js-shell", "evidence": "empty #root", "visible_words": 12},
                           "stages": [{"stage": "direct", "outcome": "js-shell", "kind": "js-shell", "status": 200, "bytes": 1800, "elapsed_ms": 90, "note": "empty #root; script bytes 71%"},
                                      {"stage": "render", "outcome": "timeout", "kind": None, "status": None, "bytes": 0, "elapsed_ms": 90000, "note": "render-url.sh killed with its process group after 90 s"}]})
    else:
        print(f"stub capture-fetch: unknown STUB_FETCH {mode}", file=sys.stderr)
        return 22
    if rc == 0 and expect == "pdf":
        raw = b"%PDF-1.7\n\x00\x00binary\n%%EOF\n"
        report.update({"content_type": "application/pdf", "raw_kind": "file"})
    if rc == 0 and expect == "audio":
        raw = b"RIFFstubaudio"
        ext = os.environ.get("STUB_FETCH_EXT") or None
        report.update({"content_type": "audio/mpeg" if ext == "mp3" else "application/octet-stream", "raw_kind": "file",
                       "ext": ext, "audio_sniff": ("audio/mpeg" if ext == "mp3" else None)})
    if rc == 0:
        with open(opts["--out"], "wb") as fh:
            fh.write(raw)
        report["bytes"] = len(raw)
        report["stages"][-1]["bytes"] = len(raw)
    with open(opts["--report"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    hook = os.environ.get("STUB_FETCH_HOOK") or os.environ.get("STUB_CURL_HOOK")
    if hook and rc == 0:
        subprocess.run(hook, shell=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
