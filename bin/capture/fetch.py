"""capture.fetch — the fetch chain direct → rendered → external, `--expect pdf|audio`, the arXiv version
resolution (spec §3.3, §3.4, D4, D10, D11, §12 A7, A11).

`acquire(url, out_path, options)` runs one acquisition under its own deadline (`acquire_timeout`): the
direct stage is one policy GET classified by the decision table; `thin` is rendered once and the richer
result kept; `js-shell`, `bot-wall` and `forbidden` escalate to the renderer (`render-url.sh --html`, its
own process group under `render_timeout`) and then, only when the instance allows it, to the external
fetchers (Firecrawl `rawHtml`, Jina Reader HTML) — each answer classified like any other, a relayed wall
never a raw. Nothing in the chain is a way past a login, a paywall or a rate limit. For `kind: arxiv` the
version is resolved once from the abstract page and the content fetched for that version (HTML, else the
PDF). `--expect pdf|audio` is one policy GET with its own status mapping and no escalation; audio is
sniffed (`file -b --mime-type`). The report is `mnemon-fetch/1`; exit codes 0 fetched · 1 failed · 2 usage
or invalid-url · 3 environment · 124 timeout. Stdlib only, python 3.9.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import tempfile
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

from . import check, proc, resolve
from . import policy as policymod
from . import report as reportmod

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_ENVIRONMENT, EXIT_TIMEOUT = 0, 1, 2, 3, 124
RENDER_VIRTUAL_MS = 15000
RENDER_EXIT_NO_CHROME, RENDER_EXIT_THIN = 10, 11
ESCALATING = ("js-shell", "bot-wall", "forbidden")
TERMINAL_VENDOR = ("rate-limit", "login", "paywall")
FETCHER_KEYS = {"firecrawl": "FIRECRAWL_API_KEY", "jina": "JINA_API_KEY"}
AUDIO_EXT = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/x-wav": "wav", "audio/wav": "wav",
             "audio/x-ogg": "ogg", "audio/ogg": "ogg", "audio/flac": "flac", "audio/x-flac": "flac", "audio/aac": "aac",
             "video/mp4": "mp4", "video/webm": "webm", "audio/webm": "webm"}
KNOWN_AUDIO_EXTS = frozenset(AUDIO_EXT.values())
_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")
_CANONICAL = re.compile(r"<link\b[^>]*\brel=[\"']?canonical[\"']?[^>]*>", re.IGNORECASE)
_ABS_VERSION = re.compile(r"/abs/[^\"'\s<>]*?v(\d+)\b")


class Options:
    """The knobs of one acquisition (config keys of §5 and the CLI flags of §3.3)."""

    def __init__(self, policy: policymod.Policy, *, expect: str = "page", render: str = "auto",
                 external_mode: str = "off", fetchers=("firecrawl", "jina"), acquire_timeout: float = 300.0,
                 render_timeout: float = 90.0, render_cmd: Optional[str] = None, render_max_bytes: int = 20000000,
                 thin_words: int = 60, kind: Optional[str] = None, hop_check=None, deadline: Optional[proc.Deadline] = None,
                 pacer=None):
        self.policy = policy
        self.expect = expect
        self.render = render                    # auto | force | off
        self.external_mode = external_mode      # auto | off
        self.fetchers = list(fetchers)
        self.acquire_timeout = float(acquire_timeout)
        self.render_timeout = float(render_timeout)
        self.render_cmd = render_cmd
        self.render_max_bytes = int(render_max_bytes)
        self.thin_words = int(thin_words)
        self.kind = kind
        self.hop_check = hop_check
        self.deadline = deadline
        self.pacer = pacer


def empty_report(url: str, policy: policymod.Policy) -> dict:
    """Every key of mnemon-fetch/1 with its null shape."""
    return {
        "schema": reportmod.FETCH, "url": url, "fetch_url": None, "canonical_url": None, "canonical_version": 1,
        "kind": None, "requested_version": None, "final_url": None, "method": None, "raw_kind": None,
        "http_status": None, "content_type": None, "charset": None, "bytes": 0, "ext": None, "audio_sniff": None,
        "egress": "direct", "stages": [], "acquisition": {"outcome": None, "kind": None, "evidence": None, "visible_words": 0},
        "metadata": {"title": None, "authors": [], "published": None, "published_source": None, "arxiv_id": None,
                     "arxiv_version": None, "arxiv_requested_version": None, "declared_canonical_url": None},
        "hops": [], "elapsed_ms": 0, "error_class": None, "error": None, "retryable": None, "stage": None,
        "policy": policy.as_report(),
    }


def renderer_command(explicit: Optional[str] = None) -> str:
    return explicit or os.environ.get("RENDER_URL_CMD") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render-url.sh")


# --- small HTML helpers (metadata only; the converter owns the real parsing) -------------------------------

def _meta_map(html: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for m in _META.finditer(html):
        attrs = {k.lower(): (v1 or v2 or v3 or "") for k, v1, v2, v3 in _ATTR.findall(m.group(0))}
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key and attrs.get("content"):
            out.setdefault(key, []).append(attrs["content"].strip())
    return out


def _declared_canonical(html: str) -> Optional[str]:
    m = _CANONICAL.search(html)
    if not m:
        return None
    attrs = {k.lower(): (v1 or v2 or v3 or "") for k, v1, v2, v3 in _ATTR.findall(m.group(0))}
    href = (attrs.get("href") or "").strip()
    return href or None


def _unescape(s: str) -> str:
    import html as htmlmod
    return htmlmod.unescape(s)


def sniff_mime(path: str) -> Optional[str]:
    """`file -b --mime-type` on a downloaded file (None when `file` is unavailable or fails)."""
    exe = shutil.which("file")
    if not exe:
        return None
    res = proc.run([exe, "-b", "--mime-type", path], timeout=30)
    if res.rc != 0:
        return None
    mime = res.stdout.decode("utf-8", "replace").strip().lower()
    return mime or None


def url_extension(url: str) -> Optional[str]:
    path = urlsplit(url).path
    base = path.rsplit("/", 1)[-1]
    if "." in base:
        ext = base.rsplit(".", 1)[-1].lower()
        if re.match(r"^[a-z0-9]{1,5}$", ext):
            return ext
    return None


# --- vendor adapters (pure request builders and response parsers; A11) -------------------------------------

def firecrawl_request(url: str, key: str) -> Tuple[str, str, str, dict, bytes]:
    body = json.dumps({"url": url, "formats": ["rawHtml"], "onlyMainContent": False}).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"}
    return "POST", "api.firecrawl.dev", "/v1/scrape", headers, body


def firecrawl_parse(status: int, headers: Dict[str, str], body: bytes) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """(raw html, target status, error): `data.rawHtml` is the raw; markdown is never accepted."""
    if status < 200 or status >= 300:
        return None, None, "firecrawl answered HTTP %d" % status
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None, None, "firecrawl answered non-JSON"
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("rawHtml"), str) or not data["rawHtml"].strip():
        return None, None, "firecrawl answered without rawHtml"
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    code = meta.get("statusCode")
    return data["rawHtml"], (int(code) if isinstance(code, int) else None), None


def jina_request(url: str, key: str) -> Tuple[str, str, str, dict, Optional[bytes]]:
    """Hash routes go as a POST document for SPA routing; other URLs as GET /<url>."""
    headers = {"Authorization": "Bearer " + key, "X-Return-Format": "html", "X-Respond-With": "html",
               "X-No-Cache": "true", "Accept": "text/html"}
    fragment = urlsplit(url).fragment
    if fragment.startswith("!") or fragment.startswith("/"):
        headers["Content-Type"] = "application/json"
        return "POST", "r.jina.ai", "/", headers, json.dumps({"url": url}).encode("utf-8")
    return "GET", "r.jina.ai", "/" + quote(url, safe=":/?&=#%+@!$,;'()*[]~"), headers, None


def jina_parse(status: int, headers: Dict[str, str], body: bytes) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """(raw html, target status, error): the body is the raw; a non-HTML body is a fetcher failure."""
    if status < 200 or status >= 300:
        return None, None, "jina answered HTTP %d" % status
    text = body.decode("utf-8", "replace")
    head = text.lstrip()[:512].lower()
    if not (head.startswith("<!doctype") or head.startswith("<html") or "<head" in head or "<body" in head or "<div" in head):
        return None, None, "jina answered a non-HTML body"
    target = None
    code = headers.get("x-status-code")
    if code and code.strip().isdigit():
        target = int(code.strip())
    return text, target, None


def vendor_call(method: str, host: str, path: str, headers: dict, body: Optional[bytes],
                deadline: proc.Deadline, timeout: float = 60.0) -> Tuple[int, Dict[str, str], bytes]:
    """One authenticated request to a fixed vendor endpoint (trusted by configuration; no proxy)."""
    bound = deadline.bound(timeout) or 0.1
    conn = http.client.HTTPSConnection(host, 443, timeout=bound)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


# --- the chain -------------------------------------------------------------------------------------------

class _Render:
    __slots__ = ("state", "body", "acq", "elapsed_ms", "note", "rc")

    def __init__(self, state, body=b"", acq=None, elapsed_ms=0, note=None, rc=None):
        self.state = state          # ok | unavailable | timeout | failed
        self.body = body
        self.acq = acq
        self.elapsed_ms = elapsed_ms
        self.note = note
        self.rc = rc


class Chain:
    def __init__(self, url: str, out_path: str, opts: Options, res: resolve.Resolution, kind: str):
        self.url = url
        self.out_path = out_path
        self.opts = opts
        self.res = res
        self.kind = kind
        self.report = empty_report(url, opts.policy)
        self.report.update({"fetch_url": res.fetch_url, "canonical_url": res.canonical.url,
                            "canonical_version": res.canonical.version, "kind": kind,
                            "requested_version": res.requested_version})
        self.report["metadata"]["arxiv_requested_version"] = res.requested_version
        self.deadline = opts.deadline or proc.Deadline(opts.acquire_timeout)
        self.start = time.monotonic()
        self.last_acq = None

    # -- bookkeeping --
    def stage(self, name, outcome, kind=None, status=None, nbytes=0, elapsed_ms=0, note=None):
        self.report["stages"].append({"stage": name, "outcome": outcome, "kind": kind, "status": status,
                                      "bytes": nbytes, "elapsed_ms": elapsed_ms, "note": note})

    def get(self, url: str) -> policymod.Response:
        if self.opts.pacer is not None:
            self.opts.pacer(url, self.deadline)
        return self.opts.policy.get(url, deadline=self.deadline, hop_check=self.opts.hop_check)

    def finish(self, code: int) -> int:
        self.report["elapsed_ms"] = int((time.monotonic() - self.start) * 1000)
        return code

    def classify(self, status, headers, body: bytes) -> check.Acq:
        acq = check.acquisition(status, headers, body, thin_words=self.opts.thin_words)
        self.last_acq = acq
        return acq

    def fail(self, error_class: str, error: str, retryable, stage: str, acq: Optional[check.Acq] = None) -> int:
        self.report.update({"error_class": error_class, "error": error, "retryable": retryable, "stage": stage})
        acq = acq or self.last_acq
        if acq is not None:
            self.report["acquisition"] = acq.as_report()
        if self.report["http_status"] is None:       # the last HTTP status the chain saw, when any
            for st in reversed(self.report["stages"]):
                if st.get("status") is not None:
                    self.report["http_status"] = st["status"]
                    break
        if error_class == "timeout":
            return self.finish(EXIT_TIMEOUT)
        if error_class == "environment":
            return self.finish(EXIT_ENVIRONMENT)
        return self.finish(EXIT_FAILED)

    def policy_fail(self, exc: policymod.PolicyError, stage: str) -> int:
        kind = exc.kind
        if kind == "hop-rejected":
            return self.fail("blocked", "hop rejected: %s" % exc.message, False, stage)
        return self.fail(kind, exc.message, exc.retryable, stage)

    def acq_fail(self, acq: check.Acq, stage: str) -> int:
        if acq.kind in check.WALL_KINDS:
            label = {"login": "login wall", "rate-limit": "rate limit", "bot-wall": "bot wall", "forbidden": "forbidden",
                     "paywall": "paywall"}.get(acq.kind, acq.kind)
            return self.fail("blocked", "%s: %s (no escalation past it)" % (label, acq.evidence), acq.retryable, stage, acq)
        return self.fail(acq.kind, "%s: %s" % (acq.kind, acq.evidence), acq.retryable, stage, acq)

    def done(self, body: bytes, *, method: str, raw_kind: str, acq: check.Acq, status=None, final_url=None,
             content_type=None, charset=None, egress="direct", hops=None, ext=None, audio_sniff=None) -> int:
        reportmod.write_bytes(self.out_path, body)
        self.report.update({"method": method, "raw_kind": raw_kind, "http_status": status, "final_url": final_url,
                            "content_type": content_type, "charset": charset, "bytes": len(body), "egress": egress,
                            "acquisition": acq.as_report(), "ext": ext, "audio_sniff": audio_sniff})
        if hops is not None:
            self.report["hops"] = hops
        if acq.title and not self.report["metadata"]["title"]:
            self.report["metadata"]["title"] = acq.title
        return self.finish(EXIT_OK)

    def note_html_metadata(self, body: bytes) -> None:
        text = body[:262144].decode("utf-8", "replace")
        meta = self.report["metadata"]
        if meta["declared_canonical_url"] is None:
            meta["declared_canonical_url"] = _declared_canonical(text)

    # -- --expect pdf|audio --
    def expect_file(self) -> int:
        """One policy GET of the literal (trimmed) URL with the A7 status mapping and no escalation. For
        `kind: arxiv` under `--expect pdf` the target is `https://arxiv.org/pdf/<id>vN` — the requested or
        the resolved version — never the resolver's HTML fetch_url."""
        target = self.url.strip()
        if self.kind == "arxiv" and self.opts.expect == "pdf":
            code, version, arxiv_id = self.arxiv_resolve()
            if code is not None:
                return code
            target = "https://arxiv.org/pdf/%s%s" % (arxiv_id, "v%d" % version if version else "")
        self.report["fetch_url"] = target
        t0 = time.monotonic()
        try:
            resp = self.get(target)
        except policymod.PolicyError as exc:
            self.stage("direct", exc.kind, note=exc.message)
            return self.policy_fail(exc, "direct")
        ms = int((time.monotonic() - t0) * 1000)
        self.report["hops"] = resp.hops
        status = resp.status
        mapping = None
        if status in (401, 403, 407):
            mapping = ("blocked", "forbidden", False)
        elif status in (404, 410):
            mapping = ("not-found", "not-found", False)
        elif status == 429:
            mapping = ("blocked", "rate-limit", True)
        elif status == 503:
            mapping = ("unavailable", "unavailable", True)
        elif 500 <= status <= 599:
            mapping = ("server-error", "server-error", True)
        elif status < 200 or status >= 300:
            mapping = ("http", "http", False)
        if mapping:
            error_class, kind, retryable = mapping
            self.stage("direct", kind, kind, status, len(resp.body), ms, "status %d" % status)
            return self.fail(error_class, "%s: HTTP %d for %s" % (kind, status, target), retryable, "direct",
                             check.Acq(kind, kind, "status %d" % status, 0, None))
        body = resp.body
        if self.opts.expect == "pdf":
            if not body.startswith(b"%PDF-"):
                self.stage("direct", "not-pdf", None, status, len(body), ms, "no %PDF- magic")
                return self.fail("not-pdf", "the download is not a PDF (content-type %s)" % (resp.content_type or "unknown"),
                                 False, "direct", check.Acq("not-pdf", "not-pdf", "no %PDF- magic", 0, None))
            self.stage("direct", "ok", None, status, len(body), ms, "application/pdf")
            return self.done(body, method="direct", raw_kind="file", acq=check.Acq("ok", None, None, 0, None),
                             status=status, final_url=resp.final_url, content_type="application/pdf",
                             charset=None, hops=resp.hops, ext="pdf")
        # audio: sniff the bytes, then the URL's extension, then the content type; `bin` last
        tmp = tempfile.NamedTemporaryFile(prefix="mnemon-audio-", delete=False)
        try:
            tmp.write(body)
            tmp.close()
            mime = sniff_mime(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        header_ct = resp.content_type
        ext = AUDIO_EXT.get(mime or "")
        content_type = mime if ext else header_ct
        if ext is None:
            url_ext = url_extension(target)
            if url_ext in KNOWN_AUDIO_EXTS:
                ext = url_ext
            elif header_ct and (header_ct.startswith("audio/") or header_ct in AUDIO_EXT):
                ext = AUDIO_EXT.get(header_ct) or (url_ext or "bin")
            elif (mime or "").startswith("audio/") or (mime or "").startswith("video/"):
                ext = url_ext or "bin"
        if ext is None:
            self.stage("direct", "not-audio", None, status, len(body), ms, "sniffed %s" % (mime or "unknown"))
            return self.fail("not-audio", "the download is not audio (sniffed %s, content-type %s)" % (mime or "unknown", header_ct or "unknown"),
                             False, "direct", check.Acq("not-audio", "not-audio", "sniffed %s" % (mime or "unknown"), 0, None))
        self.stage("direct", "ok", None, status, len(body), ms, "sniffed %s → %s" % (mime or "unknown", ext))
        return self.done(body, method="direct", raw_kind="file", acq=check.Acq("ok", None, None, 0, None), status=status,
                         final_url=resp.final_url, content_type=content_type, charset=None, hops=resp.hops, ext=ext,
                         audio_sniff=mime)

    # -- the page chain --
    def page(self) -> int:
        if self.opts.render == "force":
            return self.escalate(None, forced=True)
        t0 = time.monotonic()
        try:
            resp = self.get(self.res.fetch_url)
        except policymod.PolicyError as exc:
            self.stage("direct", exc.kind, note=exc.message)
            return self.policy_fail(exc, "direct")
        ms = int((time.monotonic() - t0) * 1000)
        self.report["hops"] = resp.hops
        return self.classify_direct(resp, ms)

    def classify_direct(self, resp: policymod.Response, ms: int) -> int:
        body = resp.body
        if resp.content_type == "application/pdf" or body.startswith(b"%PDF-"):
            self.stage("direct", "ok", None, resp.status, len(body), ms, "application/pdf")
            return self.done(body, method="direct", raw_kind="file", acq=check.Acq("ok", None, None, 0, None),
                             status=resp.status, final_url=resp.final_url, content_type="application/pdf",
                             charset=None, hops=resp.hops, ext="pdf")
        acq = self.classify(resp.status, resp.headers, body)
        self.stage("direct", acq.outcome, acq.kind, resp.status, len(body), ms, acq.evidence)
        self.note_html_metadata(body)
        if acq.outcome in ("ok", "paywall"):
            return self.done(body, method="direct", raw_kind="response", acq=acq, status=resp.status,
                             final_url=resp.final_url, content_type=resp.content_type, charset=resp.charset, hops=resp.hops)
        if acq.outcome == "thin":
            return self.thin_once(resp, acq)
        if acq.outcome in ESCALATING:
            return self.escalate(acq)
        return self.acq_fail(acq, "direct")

    def thin_once(self, resp: policymod.Response, acq: check.Acq) -> int:
        """A thin direct page is rendered once; the richer of the two is kept (A7 iv keeps thin on failure)."""
        keep_direct = True
        if self.opts.render != "off":
            r = self.render(trigger="thin")
            if r.state == "ok" and r.acq.outcome in ("ok", "thin") and r.acq.visible_words > acq.visible_words:
                keep_direct = False
                self.report["stages"][-1]["note"] = ((self.report["stages"][-1]["note"] or "") + "; kept (richer than the direct result)").lstrip("; ")
                return self.done(r.body, method="render", raw_kind="rendered", acq=r.acq, status=None, final_url=None,
                                 content_type="text/html", charset="utf-8")
            if r.state == "ok" and r.acq.outcome in ("login",):
                return self.acq_fail(r.acq, "render")
            if r.state == "ok" and r.acq.outcome == "paywall":
                return self.done(r.body, method="render", raw_kind="rendered", acq=r.acq, status=None, final_url=None,
                                 content_type="text/html", charset="utf-8")
            if r.state == "ok" and r.acq.outcome == "rate-limit":
                return self.acq_fail(r.acq, "render")
            self.report["stages"][-1]["note"] = ((self.report["stages"][-1]["note"] or "") + "; the direct result is kept").lstrip("; ")
        if keep_direct:
            return self.done(resp.body, method="direct", raw_kind="response", acq=acq, status=resp.status,
                             final_url=resp.final_url, content_type=resp.content_type, charset=resp.charset, hops=resp.hops)
        return EXIT_FAILED

    def render(self, trigger: Optional[str]) -> _Render:
        """`render-url.sh <url> --html` under render_timeout in its own process group; the table with status None."""
        t0 = time.monotonic()
        cmd = renderer_command(self.opts.render_cmd)
        try:
            self.opts.policy.check(self.res.fetch_url, deadline=self.deadline)
        except policymod.PolicyError as exc:
            self.stage("render", exc.kind, None, None, 0, 0, "pre-validation: %s" % exc.message)
            return _Render("failed", note=exc.message)
        if not os.path.exists(cmd) or not os.access(cmd, os.X_OK):
            self.stage("render", "unavailable", None, None, 0, 0, "renderer not found: %s" % cmd)
            return _Render("unavailable", note="renderer not found", rc=RENDER_EXIT_NO_CHROME)
        virtual_ms = int(min(RENDER_VIRTUAL_MS, max(1000, self.opts.render_timeout * 1000 / 2)))
        res = proc.run([cmd, self.res.fetch_url, "--html", "--timeout", str(virtual_ms), "--max-bytes", str(self.opts.render_max_bytes)],
                       timeout=self.opts.render_timeout, deadline=self.deadline)
        ms = int((time.monotonic() - t0) * 1000)
        if res.timed_out:
            self.stage("render", "timeout", None, None, 0, ms, "renderer killed with its process group after %.0f s" % self.opts.render_timeout)
            return _Render("timeout", elapsed_ms=ms, note="render stage exceeded render_timeout=%g" % self.opts.render_timeout)
        if not res.started or res.rc == RENDER_EXIT_NO_CHROME:
            self.stage("render", "unavailable", None, None, 0, ms, "no renderer (Chrome missing)")
            return _Render("unavailable", elapsed_ms=ms, note="no renderer", rc=res.rc)
        if res.rc != 0 or not res.stdout.strip():
            note = "renderer exit %s" % res.rc if res.rc != 0 else "renderer produced no output"
            if res.rc == RENDER_EXIT_THIN:
                note = "renderer output empty, thin or above render_max_bytes (exit 11)"
            self.stage("render", "failed", None, None, len(res.stdout), ms, note)
            return _Render("failed", elapsed_ms=ms, note=note, rc=res.rc)
        acq = self.classify(None, {}, res.stdout)
        self.stage("render", acq.outcome, acq.kind, None, len(res.stdout), ms, acq.evidence)
        self.note_html_metadata(res.stdout)
        return _Render("ok", res.stdout, acq, ms, rc=0)

    def escalate(self, acq: Optional[check.Acq], forced: bool = False) -> int:
        last_kind = acq.kind if acq else None
        renderer_missing = False
        if self.opts.render != "off":
            r = self.render(trigger=last_kind or "forced")
            if r.state == "timeout":
                return self.fail("timeout", r.note or "render timed out", True, "render")
            if r.state == "unavailable":
                renderer_missing = True
                if forced:
                    return self.fail("environment", "--render requested but no renderer is available", False, "render")
            if r.state == "ok":
                if r.acq.outcome in ("ok", "thin", "paywall"):
                    return self.done(r.body, method="render", raw_kind="rendered", acq=r.acq, status=None, final_url=None,
                                     content_type="text/html", charset="utf-8")
                if r.acq.outcome in ESCALATING:
                    last_kind = r.acq.kind
                else:
                    return self.acq_fail(r.acq, "render")
        elif forced:
            return self.fail("environment", "--render requested with rendering disabled", False, "render")
        if forced and last_kind is None:
            return self.fail("fetch", "the forced render produced no page", True, "render")
        external_ran = False
        if self.opts.external_mode == "auto" and last_kind in ESCALATING:
            for fetcher in self.opts.fetchers:
                code, ran, kind = self.external(fetcher)
                external_ran = external_ran or ran
                if code is not None:
                    return code
                if kind in ESCALATING:
                    last_kind = kind
        if last_kind == "js-shell" and not external_ran:
            why = "a JavaScript shell with no renderer" if renderer_missing else "a JavaScript shell the renderer could not render"
            return self.fail("environment", "%s and no external fetcher allowed" % why, False, "render" if not renderer_missing else "direct")
        return self.fail("blocked", "%s: every stage ended on a wall" % (last_kind or "blocked"), False,
                         "external" if external_ran else "render" if self.opts.render != "off" else "direct")

    def external(self, fetcher: str) -> Tuple[Optional[int], bool, Optional[str]]:
        """One external fetcher: (exit code when the chain ends here, whether it ran, the classified kind)."""
        name = "external:%s" % fetcher
        key_var = FETCHER_KEYS.get(fetcher)
        if key_var is None:
            self.stage(name, "skipped", note="unknown fetcher")
            return None, False, None
        key = os.environ.get(key_var)
        if not key:
            self.stage(name, "skipped", note="no key (%s unset)" % key_var)
            return None, False, None
        t0 = time.monotonic()
        html, target_status, error = None, None, None
        stub = policymod.hook("MNEMON_EXTERNAL_FETCH_CMD")
        if stub:
            res = proc.run([stub, fetcher, self.res.fetch_url], timeout=self.opts.render_timeout, deadline=self.deadline)
            if res.timed_out:
                self.stage(name, "timeout", None, None, 0, res.elapsed_ms, "external fetch killed after the deadline")
                return self.fail("timeout", "external stage exceeded the deadline", True, "external"), True, None
            if res.rc != 0 or not res.stdout.strip():
                error = "fetcher exit %s" % res.rc
            else:
                html = res.stdout.decode("utf-8", "replace")
        else:
            try:
                if fetcher == "firecrawl":
                    method, host, path, headers, body = firecrawl_request(self.res.fetch_url, key)
                    status, rheaders, rbody = vendor_call(method, host, path, headers, body, self.deadline)
                    html, target_status, error = firecrawl_parse(status, rheaders, rbody)
                else:
                    method, host, path, headers, body = jina_request(self.res.fetch_url, key)
                    status, rheaders, rbody = vendor_call(method, host, path, headers, body, self.deadline)
                    html, target_status, error = jina_parse(status, rheaders, rbody)
            except (OSError, http.client.HTTPException) as exc:
                error = "vendor request failed: %s" % exc
        ms = int((time.monotonic() - t0) * 1000)
        if html is None:
            self.stage(name, "failed", None, target_status, 0, ms, error)
            return None, True, None
        raw = html.encode("utf-8")
        acq = self.classify(target_status, {}, raw)
        self.stage(name, acq.outcome, acq.kind, target_status, len(raw), ms, acq.evidence)
        if acq.outcome in ("ok", "thin", "paywall"):
            return self.done(raw, method=fetcher, raw_kind="response", acq=acq, status=target_status, final_url=None,
                             content_type="text/html", charset="utf-8", egress="external"), True, acq.kind
        if acq.outcome in TERMINAL_VENDOR:
            return self.acq_fail(acq, "external"), True, acq.kind
        return None, True, acq.kind

    # -- arXiv --
    def arxiv_resolve(self) -> Tuple[Optional[int], Optional[int], str]:
        """The version resolved once from the abstract page of the requested (or the latest) version, with
        the citation_* metadata recorded (D10): (exit code when the chain ends here, the version, the id)."""
        arxiv_id = self.res.canonical.url.rsplit("/abs/", 1)[1]
        meta = self.report["metadata"]
        meta["arxiv_id"] = arxiv_id
        t0 = time.monotonic()
        try:
            resp = self.get(self.res.metadata_url)
        except policymod.PolicyError as exc:
            self.stage("metadata", exc.kind, note=exc.message)
            return self.policy_fail(exc, "metadata"), None, arxiv_id
        ms = int((time.monotonic() - t0) * 1000)
        if resp.status != 200:
            acq = self.classify(resp.status, resp.headers, resp.body)
            self.stage("metadata", acq.outcome, acq.kind, resp.status, len(resp.body), ms, acq.evidence)
            return self.acq_fail(acq, "metadata"), None, arxiv_id
        text = resp.body.decode("utf-8", "replace")
        metas = _meta_map(text)
        version = None
        for pdf_url in metas.get("citation_pdf_url", []):
            m = re.search(r"v(\d+)(?:\.pdf)?/?$", pdf_url)
            if m:
                version = int(m.group(1))
                break
        if version is None:
            found = [int(v) for v in _ABS_VERSION.findall(text)]
            if found:
                version = max(found) if self.res.requested_version is None else self.res.requested_version
        if version is None:
            version = self.res.requested_version
        meta["title"] = _unescape(metas["citation_title"][0]) if metas.get("citation_title") else None
        meta["authors"] = [_unescape(a) for a in metas.get("citation_author", [])]
        dates = metas.get("citation_date") or metas.get("citation_publication_date") or []
        if dates:
            d = dates[0].strip()
            m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", d)
            meta["published"] = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else d
            meta["published_source"] = "citation_date"
        meta["arxiv_version"] = version
        meta["declared_canonical_url"] = _declared_canonical(text)
        self.stage("metadata", "ok", None, resp.status, len(resp.body), ms, "version %s" % (version if version else "unknown"))
        return None, version, arxiv_id

    def arxiv(self) -> int:
        code, version, arxiv_id = self.arxiv_resolve()
        if code is not None:
            return code
        suffix = "v%d" % version if version else ""
        html_url = "https://arxiv.org/html/%s%s" % (arxiv_id, suffix)
        self.report["fetch_url"] = html_url
        t0 = time.monotonic()
        try:
            resp = self.get(html_url)
        except policymod.PolicyError as exc:
            self.stage("direct", exc.kind, note=exc.message)
            return self.policy_fail(exc, "direct")
        ms = int((time.monotonic() - t0) * 1000)
        if resp.status == 200 and (resp.content_type or "").startswith("text/html") and resp.body.strip():
            self.report["hops"] = resp.hops
            acq = self.classify(resp.status, resp.headers, resp.body)
            self.stage("direct", acq.outcome, acq.kind, resp.status, len(resp.body), ms, acq.evidence)
            if acq.outcome in ("ok", "thin"):
                return self.done(resp.body, method="direct", raw_kind="response", acq=acq, status=resp.status,
                                 final_url=resp.final_url, content_type=resp.content_type, charset=resp.charset, hops=resp.hops)
            if acq.outcome == "paywall":
                return self.done(resp.body, method="direct", raw_kind="response", acq=acq, status=resp.status,
                                 final_url=resp.final_url, content_type=resp.content_type, charset=resp.charset, hops=resp.hops)
            if acq.outcome in ESCALATING:
                return self.escalate(acq)
            return self.acq_fail(acq, "direct")
        self.stage("direct", "no-html", None, resp.status, len(resp.body), ms, "no HTML for version %s; falling back to the PDF" % (version or "?"))
        pdf_url = "https://arxiv.org/pdf/%s%s" % (arxiv_id, suffix)
        self.report["fetch_url"] = pdf_url
        t0 = time.monotonic()
        try:
            resp = self.get(pdf_url)
        except policymod.PolicyError as exc:
            self.stage("pdf", exc.kind, note=exc.message)
            return self.policy_fail(exc, "pdf")
        ms = int((time.monotonic() - t0) * 1000)
        self.report["hops"] = resp.hops
        if resp.status == 200 and resp.body.startswith(b"%PDF-"):
            self.stage("pdf", "ok", None, resp.status, len(resp.body), ms, "application/pdf")
            return self.done(resp.body, method="direct", raw_kind="file", acq=check.Acq("ok", None, None, 0, None),
                             status=resp.status, final_url=resp.final_url, content_type="application/pdf", charset=None,
                             hops=resp.hops, ext="pdf")
        acq = self.classify(resp.status, resp.headers, resp.body)
        self.stage("pdf", acq.outcome if resp.status != 200 else "not-pdf", acq.kind, resp.status, len(resp.body), ms, acq.evidence)
        if resp.status == 200:
            return self.fail("not-pdf", "neither HTML nor a PDF is served for version %s" % (version or "?"), False, "pdf", acq)
        return self.acq_fail(acq, "pdf")


def acquire(url: str, out_path: str, opts: Options) -> Tuple[int, dict]:
    """One acquisition; (exit code, the mnemon-fetch/1 report). The raw is at out_path on exit 0."""
    try:
        res = resolve.resolve(url)
    except resolve.InvalidURL as exc:
        report = empty_report(url, opts.policy)
        report.update({"error_class": "invalid-url", "error": str(exc), "retryable": False, "stage": "resolve"})
        return EXIT_USAGE, report
    kind = opts.kind or res.kind
    chain = Chain(url, out_path, opts, res, kind)
    try:
        if opts.expect in ("pdf", "audio"):
            code = chain.expect_file()
        elif kind == "arxiv":
            code = chain.arxiv()
        else:
            code = chain.page()
    except policymod.PolicyError as exc:
        code = chain.policy_fail(exc, "direct")
    if code != EXIT_OK and chain.report["error_class"] is None:
        chain.report.update({"error_class": "fetch", "error": "the chain ended without a raw", "retryable": True})
    return code, chain.report
