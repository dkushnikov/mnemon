"""capture.ocr_tesseract — the witness: tesseract as the adapter `ocr-tesseract/1` (capture layer A20, §21.3).

The witness reads the raw image on this machine. Its text is compared with the model's reading (§21.5) and is
never published; its word confidence is a diagnostic. Every command runs through the bounded runner of §21.6
(`proc.run_bounded`) inside the caller's process group, so one signal to the worker's group ends them.

- `resolve`: `tesseract` on PATH; the test hook `OCR_CMD` (an absolute path, honoured under
  `MNEMON_TEST_HOOKS=1`; an empty value counts as unset) replaces it. Nothing else is searched.
- `probe`: `--version` (each line stripped; the first `tesseract <version>`, major 4 or later; the first
  `leptonica-` line is `detail`), the language model directory (`TESSDATA_PREFIX`, else the header of
  `--list-langs`), every bound code a regular file, its sha256 read in chunks. Never a fallback to another
  language sequence.
- `recognize`: the argv of §21.3, no configuration file named; stderr read on every exit, 0 included, for
  the two load messages before the outputs are looked at; the outputs bounded by `stat` before a read.
- `execution_key`: sha256 of the canonical JSON of §21.3.
- `normalize`: `mnemon-ocr-text/1`, the line-ending and whitespace steps of §21.6 «Normalization».

tesseract runs in the scratch dir, so every path of its argv is absolute: the command, the language model
directory (made absolute where the probe takes it) and the raw and the scratch dir (made absolute where
`recognize` takes them), each resolved against the caller's working directory; an absolute path is kept as
given. A command cut by the deadline classifies nothing: the result carries `deadline_hit` and the worker
exits 124 without a report (§21.6 «Bounded reads»). Stdlib only, python 3.9.
"""
from __future__ import annotations

import decimal
import hashlib
import json
import os
import re
import shutil
import stat
from typing import Callable, List, Optional, Tuple

from . import policy, proc

ADAPTER = "ocr-tesseract/1"
BACKEND = "tesseract"
NORMALIZER = "mnemon-ocr-text/1"
ENVELOPE = "mnemon-image-envelope/1"
PROBE_SCHEMA = "mnemon-ocr-probe/1"
OPTIONS = {"psm": "3", "oem": "default", "variables": {"tessedit_create_tsv": "1", "tessedit_create_txt": "1"}}
PROBE_TIMEOUT = 10.0
OUTPUT_MAX = 16 * 1024 * 1024
STREAM_MAX = 1024 * 1024        # bytes kept of a command's stdout and of its stderr (§21.6 «Bounded reads»)
MIN_MAJOR = 4
ERROR_MAX = 300
HASH_CHUNK = 1024 * 1024
CONFIDENCE_UNIT = "tesseract-word-0-100"

TSV_HEADER = ("level", "page_num", "block_num", "par_num", "line_num", "word_num", "left", "top", "width", "height",
              "conf", "text")

_LANGS = re.compile(r"[a-z]{3}(_[a-z0-9]+)*(\+[a-z]{3}(_[a-z0-9]+)*){0,7}")
_VERSION_LINE = re.compile(r"tesseract v?(\S+)")
_MAJOR = re.compile(r"(\d+)")
_LIST_HEADER = re.compile(r'List of available languages(?: in "(.*)")?')
_FAILED_LOADING = re.compile(r"Failed loading language '([^']*)'")
_NONE_LOADED = "Tesseract couldn't load any languages!"
_TOO_LARGE = "Image too large"
_DECODE = ("Error in pixRead", "pixReadStream", "libpng error", "Unsupported image type")
_INT = re.compile(r"-?[0-9]+")
_REAL = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?")


class Probe:
    """The outcome of the probe of §21.3. `ok` only when every bound language model is a regular file (and,
    when hashed, was read). `reason` is None on success and on `deadline_hit`."""
    __slots__ = ("ok", "command", "version", "detail", "tessdata_dir", "available", "present", "missing", "data",
                 "reason", "error", "deadline_hit")

    def __init__(self, ok=False, command=None, version=None, detail=None, tessdata_dir=None, available=None,
                 present=None, missing=None, data=None, reason=None, error=None, deadline_hit=False):
        self.ok = ok
        self.command = command
        self.version = version
        self.detail = detail
        self.tessdata_dir = tessdata_dir
        self.available = available
        self.present = list(present or [])
        self.missing = list(missing or [])
        self.data = data
        self.reason = reason
        self.error = error
        self.deadline_hit = deadline_hit

    def __repr__(self) -> str:
        return "Probe(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.__slots__)


class Witness:
    """The outcome of the run of §21.3. `failure` None means the witness text is in `text`."""
    __slots__ = ("failure", "stage", "text", "text_sha256", "words", "confidence", "missing", "error",
                 "deadline_hit")

    def __init__(self, failure=None, stage="witness", text=None, text_sha256=None, words=None, confidence=None,
                 missing=None, error=None, deadline_hit=False):
        self.failure = failure
        self.stage = stage
        self.text = text
        self.text_sha256 = text_sha256
        self.words = words
        self.confidence = confidence
        self.missing = list(missing or [])
        self.error = error
        self.deadline_hit = deadline_hit

    def __repr__(self) -> str:
        return "Witness(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.__slots__)


# --- the sequence, the command, the text ---------------------------------------------------------------------

def parse_languages(sequence: str) -> List[str]:
    """The codes of a `langs` value (§21.8): one to eight codes joined by `+`, none twice. ValueError otherwise."""
    if not isinstance(sequence, str) or _LANGS.fullmatch(sequence) is None:
        raise ValueError("not a language sequence: %r" % (sequence,))
    codes = sequence.split("+")
    seen = set()
    for code in codes:
        if code in seen:
            raise ValueError("language %s named twice in %r" % (code, sequence))
        seen.add(code)
    return codes


def resolve() -> Optional[str]:
    """The witness's executable: `OCR_CMD` under the test hooks (an absolute path to an executable file; an
    empty value counts as unset), else `tesseract` on PATH, made absolute when a relative PATH entry found it.
    None when there is none. Without the hooks a set `OCR_CMD` is ignored and named on stderr (§3.2's rule for
    test hooks)."""
    policy.warn_ignored_hooks()
    cmd = policy.hook("OCR_CMD")
    if cmd is not None:
        if os.path.isabs(cmd) and os.path.isfile(cmd) and os.access(cmd, os.X_OK):
            return cmd
        return None
    found = shutil.which("tesseract")
    return _absolute(found) if found else None     # a relative PATH entry gives a relative path


def _absolute(path: str) -> str:
    """A path for tesseract's argv: an absolute one exactly as given, a relative one resolved against this
    process's working directory. The run starts tesseract in the scratch dir, where a relative path the probe
    or the envelope check resolved here would name another file."""
    return path if os.path.isabs(path) else os.path.abspath(path)


def normalize(text: str) -> str:
    """`mnemon-ocr-text/1`: `\\r\\n` → `\\n`, then `\\r`, `\\f`, `\\v` → `\\n`; trailing spaces and tabs removed from
    every line; leading and trailing blank lines removed; a run of blank lines → one; one final `\\n`. A blank
    line is a line that is empty once its trailing spaces and tabs are gone. An empty text stays empty."""
    t = text.replace("\r\n", "\n")
    t = t.replace("\r", "\n").replace("\f", "\n").replace("\v", "\n")
    lines = [line.rstrip(" \t") for line in t.split("\n")]
    start, end = 0, len(lines)
    while start < end and lines[start] == "":
        start += 1
    while end > start and lines[end - 1] == "":
        end -= 1
    out = []
    for line in lines[start:end]:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out) + "\n" if out else ""


def execution_key(probe: Probe, languages: str) -> str:
    """`witness_execution` (§21.3): sha256 of the canonical JSON — keys sorted, no whitespace, UTF-8 — of the
    requested configuration. Known once the probe succeeded with the language models hashed."""
    if not probe.ok or probe.data is None:
        raise ValueError("execution_key needs a successful probe that hashed the language models")
    doc = {"adapter": ADAPTER, "backend": BACKEND, "version": probe.version, "detail": probe.detail,
           "tessdata_dir": probe.tessdata_dir, "languages": languages, "data": probe.data, "options": OPTIONS,
           "envelope": ENVELOPE, "normalizer": NORMALIZER}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --- one line of error ---------------------------------------------------------------------------------------

def _one_line(text: str, tail: bool = False) -> str:
    """Whitespace runs (line ends included) collapsed to one space; at most ERROR_MAX characters — the start,
    or with `tail` the end."""
    s = " ".join(text.split())
    if len(s) <= ERROR_MAX:
        return s
    return "…" + s[-(ERROR_MAX - 1):] if tail else s[:ERROR_MAX - 1] + "…"


def _stderr_tail(stderr: bytes) -> Optional[str]:
    lines = [ln.strip() for ln in stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
    return _one_line(" | ".join(lines), tail=True) if lines else None


def _lines(data: bytes) -> List[str]:
    return [ln.strip() for ln in data.decode("utf-8", "replace").splitlines()]


# --- the probe -----------------------------------------------------------------------------------------------

def parse_version(stdout: bytes) -> Tuple[Optional[str], Optional[str], bool]:
    """`(version, detail, readable)` from the output of `tesseract --version`. Each line is stripped first; the
    first line must read `tesseract <version>` (a leading `v` dropped) and the version must start with its
    major number; `detail` is the first line that starts with `leptonica-`."""
    lines = _lines(stdout)
    detail = None
    for line in lines:
        if line.startswith("leptonica-"):
            detail = line
            break
    if not lines:
        return None, detail, False
    m = _VERSION_LINE.fullmatch(lines[0])
    if m is None or _MAJOR.match(m.group(1)) is None:
        return None, detail, False
    return m.group(1), detail, True


def parse_list_langs(stdout: bytes) -> Tuple[Optional[str], List[str]]:
    """`(directory, codes)` from the output of `tesseract --list-langs`: the directory its header names
    (`List of available languages in "<dir>/" (n):`, the trailing `/` dropped), None without one (the 4.x
    header `List of available languages (n):` names none); the codes the lines after the header name,
    sorted."""
    lines = _lines(stdout)
    for i, line in enumerate(lines):
        m = _LIST_HEADER.match(line)
        if m is None:
            continue
        named = m.group(1)
        directory = (_strip_slash(named) or None) if named else None
        return directory, sorted(ln for ln in lines[i + 1:] if ln)
    return None, []


def _strip_slash(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped if stripped or not path else "/"


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _readable(path: str) -> None:
    with open(path, "rb") as fh:
        fh.read(1)


def _probe_command(cmd: str, flag: str, deadline) -> proc.BoundedResult:
    return proc.run_bounded([cmd, flag], timeout=PROBE_TIMEOUT, deadline=deadline, own_group=True,
                            max_stdout=STREAM_MAX, max_stderr=STREAM_MAX)


def _probe_failure(result: Probe, res: proc.BoundedResult, asked: str, what: str) -> Optional[Probe]:
    """Classify a probe command that did not complete: the deadline (nothing classified), a start failure, its
    own limit, the output cap. None when the command ran to its end."""
    if res.timed_out and res.by_deadline:
        result.deadline_hit = True
        result.error = _one_line("the deadline passed during %s" % what)
        return result
    if not res.started and not res.timed_out:
        result.reason = "tesseract-start"
        result.missing = ["tesseract"]
        result.error = _one_line(res.stderr.decode("utf-8", "replace") or "cannot start %s" % what)
        return result
    if res.timed_out:
        result.reason = "probe-timeout"
        result.missing = [asked]
        result.error = _one_line("%s exceeded %g s" % (what, PROBE_TIMEOUT))
        return result
    if res.capped:
        result.reason = "probe-output-cap"
        result.missing = [asked]
        result.error = _one_line("%s wrote more than %d bytes on %s" % (what, STREAM_MAX, res.capped))
        return result
    return None


def probe(languages: str, *, deadline, hash_models: bool = True) -> Probe:
    """The probe of §21.3, before recognition. Every command through the bounded runner, at most
    PROBE_TIMEOUT seconds each within `deadline`. ValueError on a sequence outside the grammar of §21.8."""
    codes = parse_languages(languages)
    result = Probe()
    cmd = resolve()
    if cmd is None:
        result.reason = "no-tesseract"
        result.missing = ["tesseract"]
        if policy.hook("OCR_CMD") is None:
            result.error = "tesseract is not on PATH"
        else:
            result.error = "OCR_CMD names no executable file"
        return result
    result.command = cmd

    res = _probe_command(cmd, "--version", deadline)
    if _probe_failure(result, res, "version", "tesseract --version") is not None:
        return result
    version, detail, readable = parse_version(res.stdout)
    result.version, result.detail = version, detail
    if res.rc != 0 or not readable:
        result.reason = "version"
        result.missing = ["version"]
        first = (_lines(res.stdout) or [""])[0]
        if res.rc != 0:
            result.error = _one_line("tesseract --version exited %s" % res.rc)
        else:
            result.error = _one_line("tesseract --version: the first line %r is not 'tesseract <version>'" % first)
        return result
    if int(_MAJOR.match(version).group(1)) < MIN_MAJOR:
        result.reason = "version"
        result.missing = ["version"]
        result.error = _one_line("tesseract %s is older than %d" % (version, MIN_MAJOR))
        return result

    res = _probe_command(cmd, "--list-langs", deadline)
    if _probe_failure(result, res, "model-directory", "tesseract --list-langs") is not None:
        return result
    listed_dir, listed = (None, [])
    if res.rc == 0:
        listed_dir, listed = parse_list_langs(res.stdout)
        result.available = listed
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        result.tessdata_dir = _absolute(_strip_slash(prefix))
    elif res.rc != 0 or listed_dir is None:
        result.reason = "model-directory"
        result.missing = ["model-directory"]
        if res.rc != 0:
            result.error = _one_line("tesseract --list-langs exited %s: %s"
                                     % (res.rc, _stderr_tail(res.stderr) or "no message"))
        else:
            result.error = "tesseract --list-langs names no language model directory and TESSDATA_PREFIX is unset"
        return result
    else:
        result.tessdata_dir = _absolute(listed_dir)    # the header of a `--list-langs` run in this directory

    missing, unreadable, present = [], [], []
    for code in codes:
        path = os.path.join(result.tessdata_dir, code + ".traineddata")
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            missing.append(code)
            continue
        except OSError:
            unreadable.append(code)
            continue
        if not stat.S_ISREG(st.st_mode):
            missing.append(code)
            continue
        present.append(code)
    result.present = present
    if missing:
        result.reason = "model-missing"
        result.missing = ["lang:%s" % c for c in missing]
        names = ", ".join(c + ".traineddata" for c in missing)
        result.error = _one_line("the language model%s %s %s not in %s"
                                 % ("s" if len(missing) > 1 else "", names, "are" if len(missing) > 1 else "is",
                                    result.tessdata_dir))
        return result

    data = []
    for code in present:
        path = os.path.join(result.tessdata_dir, code + ".traineddata")
        try:
            if hash_models:
                data.append([code, _hash_file(path)])
            else:
                _readable(path)
        except OSError:
            unreadable.append(code)
    if unreadable:
        order = [c for c in codes if c in unreadable]
        result.reason = "model-unreadable"
        result.missing = ["lang:%s" % c for c in order]
        result.error = _one_line("language model cannot be read: %s" % ", ".join(order))
        return result
    result.data = data if hash_models else None
    result.ok = True
    return result


def probe_document(p: Probe, languages: str) -> dict:
    """`mnemon-ocr-probe/1` (§21.6 «The probe»): every key present, None for what the probe did not learn. A
    probe cut by its deadline — the `--probe` run's own `--timeout` — is `probe-timeout`, and `missing` names
    what the cut command was asked for."""
    reason, missing, ok = p.reason, list(p.missing), p.ok
    if p.deadline_hit:
        ok = False
        reason = "probe-timeout"
        missing = ["version"] if p.version is None else ["model-directory"]
    return {"schema": PROBE_SCHEMA, "ok": ok, "adapter": ADAPTER, "backend": BACKEND, "version": p.version,
            "detail": p.detail, "tessdata_dir": p.tessdata_dir, "languages": languages, "available": p.available,
            "present": list(p.present), "missing": missing, "reason": reason, "error": p.error}


# --- the run -------------------------------------------------------------------------------------------------

def argv_for(command: str, raw_path: str, scratch_dir: str, tessdata_dir: str, languages: str) -> List[str]:
    """The call of §21.3, a function of OPTIONS: one process, two output files, no configuration file named."""
    argv = [command, raw_path, os.path.join(scratch_dir, "out"), "--tessdata-dir", tessdata_dir, "-l", languages,
            "--psm", OPTIONS["psm"]]
    if OPTIONS["oem"] != "default":
        argv += ["--oem", OPTIONS["oem"]]
    for name in ("tessedit_create_txt", "tessedit_create_tsv"):
        argv += ["-c", "%s=%s" % (name, OPTIONS["variables"][name])]
    return argv


def load_failures(stderr: bytes, codes: List[str]) -> Optional[List[str]]:
    """The model-load check of §21.3 on a run's stderr, whatever its exit: each line stripped; a line
    `Failed loading language '<code>'` or `Tesseract couldn't load any languages!` is a language that did
    not load. Returns the `missing` tokens — the bound sequence's codes first, in its order, then any other
    language a language model tried to load — or None when every language loaded."""
    failed, none_loaded = [], False
    for line in _lines(stderr):
        m = _FAILED_LOADING.fullmatch(line)
        if m is not None:
            if m.group(1) not in failed:
                failed.append(m.group(1))
        elif line == _NONE_LOADED:
            none_loaded = True
    if not failed and not none_loaded:
        return None
    if not failed:
        failed = list(codes)
    ordered = [c for c in codes if c in failed] + [c for c in failed if c not in codes]
    return ["lang:%s" % c for c in ordered]


def exit_failure(stderr: bytes) -> dict:
    """The class of a run that exited non-zero (after the model-load check), by its stderr (§21.3)."""
    text = stderr.decode("utf-8", "replace")
    if _TOO_LARGE in text:
        return _failure("too-large", "too-large", "recognizer-limit", False)
    if any(marker in text for marker in _DECODE):
        return _failure("unsupported", "corrupt", "recognizer-decode", False)
    return _failure("convert", None, "recognizer-exit", False)


def _failure(cls: str, acquisition: Optional[str], reason: str, retryable: bool) -> dict:
    return {"class": cls, "acquisition": acquisition, "reason": reason, "retryable": retryable}


class OutputError(Exception):
    def __init__(self, reason: str, message: str):
        Exception.__init__(self, message)
        self.reason = reason
        self.message = message


def _stat_output(path: str) -> int:
    name = os.path.basename(path)
    try:
        st = os.stat(path)
    except OSError:
        raise OutputError("output", "%s missing" % name)
    if not stat.S_ISREG(st.st_mode):
        raise OutputError("output", "%s is not a regular file" % name)
    if st.st_size > OUTPUT_MAX:
        raise OutputError("output", "%s is %d bytes, over %d" % (name, st.st_size, OUTPUT_MAX))
    return st.st_size


def _read_output(path: str) -> bytes:
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            data = fh.read(OUTPUT_MAX + 1)
    except OSError as exc:
        raise OutputError("output", "%s cannot be read: %s" % (name, exc.strerror or exc))
    if len(data) > OUTPUT_MAX:
        raise OutputError("output", "%s grew over %d bytes" % (name, OUTPUT_MAX))
    return data


def parse_tsv(data: bytes) -> Optional[dict]:
    """The witness's confidence from tesseract's `.tsv` (§21.3): the twelve-column header first; every row has
    twelve fields, integers in the first ten and a number in `conf`. Word rows — `level` 5, a non-empty `text`,
    `conf` ≥ 0 — give `items`, `mean` (one decimal, rounded half to even) and `min` (one decimal, the same
    rounding). None when there is no word row. OutputError when the file does not parse."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise OutputError("output", "out.tsv is not UTF-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or tuple(lines[0].split("\t")) != TSV_HEADER:
        raise OutputError("output", "out.tsv does not start with tesseract's twelve-column header")
    confs = []
    for n, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != len(TSV_HEADER):
            raise OutputError("output", "out.tsv line %d has %d fields, not %d" % (n, len(fields), len(TSV_HEADER)))
        if not all(_INT.fullmatch(f) for f in fields[:10]) or _REAL.fullmatch(fields[10]) is None:
            raise OutputError("output", "out.tsv line %d does not parse" % n)
        conf = decimal.Decimal(fields[10])
        if int(fields[0]) == 5 and fields[11] != "" and conf >= 0:
            confs.append(conf)
    if not confs:
        return None
    one = decimal.Decimal("0.1")
    with decimal.localcontext() as ctx:
        ctx.prec = 60
        mean = (sum(confs) / len(confs)).quantize(one, rounding=decimal.ROUND_HALF_EVEN)
        low = min(confs).quantize(one, rounding=decimal.ROUND_HALF_EVEN)
    return {"unit": CONFIDENCE_UNIT, "mean": float(mean), "min": float(low), "items": len(confs)}


def _discard_outputs(scratch_dir: str) -> None:
    for name in ("out.txt", "out.tsv"):
        try:
            os.unlink(os.path.join(scratch_dir, name))
        except OSError:
            pass


def recognize(raw_path: str, scratch_dir: str, p: Probe, languages: str, *, deadline,
              on_stage: Optional[Callable[[str], None]] = None) -> Witness:
    """The run of §21.3 on the raw image, after a successful probe. The outputs land in `scratch_dir`
    (`out.txt`, `out.tsv`). The witness text is `out.txt` decoded and normalized (`mnemon-ocr-text/1`).
    tesseract runs with `scratch_dir` as its working directory, so a relative `raw_path` or `scratch_dir` is
    first resolved against the caller's; an absolute one reaches the argv as given.
    `on_stage`, when given, is called once with "output" after the run passed its model-load and exit checks
    and before the output files are looked at: the worker's stage transition of §21.6."""
    if not p.ok or not p.command or not p.tessdata_dir:
        raise ValueError("recognize needs a successful probe")
    codes = parse_languages(languages)
    raw_path, scratch_dir = _absolute(raw_path), _absolute(scratch_dir)     # tesseract runs in scratch_dir
    argv = argv_for(p.command, raw_path, scratch_dir, p.tessdata_dir, languages)
    res = proc.run_bounded(argv, deadline=deadline, own_group=True, cwd=scratch_dir,
                           max_stdout=STREAM_MAX, max_stderr=STREAM_MAX)
    if res.timed_out and res.by_deadline:
        return Witness(deadline_hit=True, error="the deadline passed during the witness's run")
    if not res.started and not res.timed_out:
        return Witness(_failure("environment", None, "tesseract-start", True),
                       error=_one_line(res.stderr.decode("utf-8", "replace") or "cannot start tesseract"))
    if res.capped:
        return Witness(_failure("convert", None, "output-cap", False),
                       error=_one_line("tesseract wrote more than %d bytes on %s" % (STREAM_MAX, res.capped)))
    if res.timed_out:
        return Witness(_failure("convert", None, "recognizer-exit", False),
                       error="the runner ended tesseract")
    missing = load_failures(res.stderr, codes)
    if missing is not None:
        _discard_outputs(scratch_dir)
        return Witness(_failure("environment", None, "model-load", True), missing=missing,
                       error=_stderr_tail(res.stderr))
    if res.rc != 0:
        tail = _stderr_tail(res.stderr)
        return Witness(exit_failure(res.stderr),
                       error=_one_line("tesseract exited %s: %s" % (res.rc, tail or "no message"), tail=True))

    if on_stage is not None:
        on_stage("output")
    txt_path = os.path.join(scratch_dir, "out.txt")
    tsv_path = os.path.join(scratch_dir, "out.tsv")
    try:
        _stat_output(txt_path)
        _stat_output(tsv_path)
        raw_text = _read_output(txt_path)
        try:
            decoded = raw_text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OutputError("output-encoding", "out.txt is not UTF-8 (byte %d)" % exc.start)
        confidence = parse_tsv(_read_output(tsv_path))
    except OutputError as exc:
        return Witness(_failure("convert", None, exc.reason, False), stage="output", error=_one_line(exc.message))
    text = normalize(decoded)
    return Witness(None, stage="output", text=text, text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   words=len(text.split()), confidence=confidence)


def witness_report(p: Optional[Probe], languages: str, w: Optional[Witness]) -> dict:
    """The `witness` object of §21.7, under the key names of §22.3. Without a probe (a refusal of the envelope)
    only the request is known; after the probe what it read; after the run its text's hash, words and
    confidence, when it succeeded. What the installation lacks is the key `absent` (it was `missing` in a
    `mnemon-capture-image/1` report); the probe document keeps its key `missing` (probe_document)."""
    done = w is not None and w.failure is None and not w.deadline_hit
    if p is None:
        absent = None
    elif w is not None:
        absent = list(w.missing)
    else:
        absent = list(p.missing)
    return {"adapter": ADAPTER, "backend": BACKEND,
            "version": p.version if p else None, "detail": p.detail if p else None,
            "tessdata_dir": p.tessdata_dir if p else None, "languages": languages,
            "data": p.data if p else None, "options": OPTIONS, "absent": absent,
            "text_sha256": w.text_sha256 if done else None, "words": w.words if done else None,
            "confidence": w.confidence if done else None}
