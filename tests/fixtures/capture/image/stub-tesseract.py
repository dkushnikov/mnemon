#!/usr/bin/env python3
"""A stub tesseract for the witness's tests (capture layer §21.13), used through the test hook `OCR_CMD`.

Three calls, as the adapter makes them: `--version`, `--list-langs` and the run
`<raw> <outbase> --tessdata-dir <dir> -l <sequence> --psm 3 -c tessedit_create_txt=1 -c tessedit_create_tsv=1`.
Each call appends one line — `version`, `list-langs` or `run` — to the file `MNEMON_TEST_TESSERACT_CALLS`
names, when the call starts; with the variable unset nothing is counted. `--list-langs` names the directory
`MNEMON_TEST_TESSDATA_DIR` in its header and lists the `<code>.traineddata` files in it; with the variable
unset it fails as the real tool does when it finds no language model directory.

The run honours the argv of §21.3: it writes `<outbase>.txt` only under `-c tessedit_create_txt=1` and
`<outbase>.tsv` only under `-c tessedit_create_tsv=1`, and it fails loudly (exit 64) on an argv that names a
configuration file (`txt`, `tsv`) or an option it does not know. It reads no image.

Modes, by `MNEMON_TEST_TESSERACT_MODE` (unset or empty: `ok`):
  ok            `stub-text.txt` (this directory) as `out.txt`, and a matching twelve-column `out.tsv`
  empty         an empty `out.txt`;  whitespace  a `out.txt` of whitespace only
  short         the first line of `stub-text.txt`
  junk          a valid text that shares no word with `stub-text.txt`
  half          the first half of the words of `stub-text.txt`, its lines kept
  double        `stub-text.txt` and as many other words again (words of three letters or more, by the rule of
                §21.5): more than the allowance, so the reading lacks half of what the witness saw
  non-utf8      bytes in `out.txt` that do not decode as UTF-8
  no-tsv        `out.txt` alone;  bad-tsv  an `out.tsv` with a row that does not parse
  big-out       an `out.txt` of 16 MiB and one byte (a sparse file: cheap to write)
  stdout-flood, stderr-flood   8 MiB on the stream, then exit 0
  decode        exit 1 with the libpng messages of a broken IDAT
  too-large     exit 1, `Image too large`
  fail          exit 2 with a message that names no class
  partial-lang  exit 0, the outputs of `ok`, and `Failed loading language 'rus'` on stderr
  no-lang       exit 1, `Tesseract couldn't load any languages!`, no output
  hang          the run sleeps;  hang-child  the run starts a child of its own, then both sleep
  version-b     another `--version` (5.5.5);  version-old  `tesseract 3.05.02`;  no-version  a first line
                that is no version;  version-hang  `--version` sleeps;  version-flood  `--version` writes 8 MiB
  langs-fail    `--list-langs` exits 1;  langs-no-dir  its header names no directory (the 4.x form)
A mode that names another call answers the run as `ok`. Stdlib only, python 3.9.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODE = os.environ.get("MNEMON_TEST_TESSERACT_MODE") or "ok"
OUTPUT_MAX = 16 * 1024 * 1024
FLOOD = 8 * 1024 * 1024
SLEEP = 120

VERSION = ("tesseract 5.5.4\n leptonica-1.87.0\n  libgif 5.2.2 : libjpeg 8d (libjpeg-turbo 3.1.3) : libpng 1.6.59 : "
           "libtiff 4.7.2 : zlib 1.2.12 : libwebp 1.6.0 : libopenjp2 2.5.4\n Found NEON\n")
VERSION_B = "tesseract 5.5.5\n leptonica-1.87.0\n  libpng 1.6.59 : zlib 1.2.12\n"
VERSION_OLD = "tesseract 3.05.02\n leptonica-1.74.4\n  libpng 1.6.34 : zlib 1.2.11\n"
NO_VERSION = "stub build without a version line\n leptonica-1.87.0\n"
TAKES_VALUE = ("--tessdata-dir", "-l", "--psm", "--oem", "--dpi", "--user-words", "--user-patterns", "--loglevel")
HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
OTHER_WORDS = ("brillig", "slithy", "toves", "gyre", "gimble", "wabe", "mimsy", "borogoves", "mome", "raths",
               "outgrabe", "jabberwock", "frumious", "bandersnatch", "vorpal", "manxome", "tumtum", "uffish", "tulgey",
               "galumphing", "beamish", "frabjous", "chortled", "callooh", "callay")
JUNK = "Brillig slithy toves gyre gimble wabe.\nMimsy borogoves mome raths outgrabe.\n"


def count(kind: str) -> None:
    path = os.environ.get("MNEMON_TEST_TESSERACT_CALLS")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(kind + "\n")


def say(stream, text: str) -> None:
    stream.write(text)
    stream.flush()


def flood(stream) -> None:
    block = b"f" * 65536
    try:
        for _ in range(FLOOD // len(block)):
            stream.buffer.write(block)
            stream.buffer.flush()
    except (BrokenPipeError, OSError):
        pass


def die(message: str, code: int = 64) -> None:
    say(sys.stderr, "stub-tesseract: %s\n" % message)
    sys.exit(code)


def stub_text() -> str:
    with open(os.path.join(HERE, "stub-text.txt"), "r", encoding="utf-8") as fh:
        return fh.read()


def letter_words(text: str) -> int:
    """Runs of letters of three code points or more: the words of §21.5."""
    n, run = 0, 0
    for ch in text + " ":
        if ch.isalpha():
            run += 1
        else:
            if run >= 3:
                n += 1
            run = 0
    return n


def half_text(text: str) -> str:
    words = text.split()
    keep = len(words) // 2
    out, seen = [], 0
    for line in text.split("\n"):
        tokens = line.split()
        if seen + len(tokens) <= keep:
            out.append(line)
            seen += len(tokens)
            continue
        out.append(" ".join(tokens[:keep - seen]))
        break
    return "\n".join(out).rstrip() + "\n"


def double_text(text: str) -> str:
    n = letter_words(text)
    words = [OTHER_WORDS[i % len(OTHER_WORDS)] for i in range(n)]
    lines = [" ".join(words[i:i + 10]) for i in range(0, n, 10)]
    return text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"


def text_of_mode() -> str:
    if MODE == "empty":
        return ""
    if MODE == "whitespace":
        return " \t\n\f\n   \n"
    base = stub_text()
    if MODE == "short":
        return base.split("\n", 1)[0] + "\n"
    if MODE == "junk":
        return JUNK
    if MODE == "half":
        return half_text(base)
    if MODE == "double":
        return double_text(base)
    return base


def tsv_of(text: str) -> str:
    """A twelve-column `.tsv` of the text, in tesseract's row shape: the page, one block and paragraph, a row per
    line and a row per word; each word's confidence a fixed function of its place."""
    rows = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t1200\t660\t-1\t"]
    lines = [ln for ln in text.split("\n") if ln.split()]
    if lines:
        rows.append("2\t1\t1\t0\t0\t0\t80\t100\t1000\t%d\t-1\t" % (40 * len(lines)))
        rows.append("3\t1\t1\t1\t0\t0\t80\t100\t1000\t%d\t-1\t" % (40 * len(lines)))
    k = 0
    for n, line in enumerate(lines, 1):
        top = 100 + 40 * (n - 1)
        rows.append("4\t1\t1\t1\t%d\t0\t80\t%d\t1000\t36\t-1\t" % (n, top))
        for m, word in enumerate(line.split(), 1):
            k += 1
            conf = 90.0 + (k * 37 % 97) / 10.0
            rows.append("5\t1\t1\t1\t%d\t%d\t%d\t%d\t%d\t36\t%.6f\t%s" % (n, m, 80 + 60 * (m - 1), top, 12 * len(word),
                                                                          conf, word))
    return "\n".join(rows) + "\n"


def write(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)


def write_outputs(outbase: str, variables: dict, text_bytes: bytes, tsv: str) -> None:
    if variables.get("tessedit_create_txt") == "1":
        write(outbase + ".txt", text_bytes)
    if variables.get("tessedit_create_tsv") == "1" and MODE != "no-tsv":
        write(outbase + ".tsv", tsv.encode("utf-8"))


def version() -> None:
    if MODE == "version-hang":
        time.sleep(SLEEP)
    if MODE == "version-flood":
        flood(sys.stdout)
        return
    text = {"version-b": VERSION_B, "version-old": VERSION_OLD, "no-version": NO_VERSION}.get(MODE, VERSION)
    say(sys.stdout, text)


def list_langs() -> None:
    directory = os.environ.get("MNEMON_TEST_TESSDATA_DIR")
    if MODE == "langs-fail" or not directory:
        say(sys.stderr, "Error opening data file ./eng.traineddata\n"
                        "Please make sure the TESSDATA_PREFIX environment variable is set to your \"tessdata\" "
                        "directory.\nFailed loading language 'eng'\n")
        sys.exit(1)
    try:
        codes = sorted(f[:-len(".traineddata")] for f in os.listdir(directory) if f.endswith(".traineddata"))
    except OSError:
        codes = []
    if MODE == "langs-no-dir":
        say(sys.stdout, "List of available languages (%d):\n" % len(codes))
    else:
        say(sys.stdout, 'List of available languages in "%s/" (%d):\n' % (directory.rstrip("/"), len(codes)))
    say(sys.stdout, "".join(code + "\n" for code in codes))


def run(args: list) -> None:
    if len(args) < 2:
        die("usage: stub-tesseract <image> <outbase> [options]", 1)
    outbase = args[1]
    variables, configs, tessdata = {}, [], "."
    i = 2
    while i < len(args):
        arg = args[i]
        if arg in TAKES_VALUE or arg == "-c":
            if i + 1 >= len(args):
                die("option %s without a value" % arg)
            if arg == "-c":
                name, sep, value = args[i + 1].partition("=")
                if not sep:
                    die("-c %s is not VAR=VALUE" % args[i + 1])
                variables[name] = value
            elif arg == "--tessdata-dir":
                tessdata = args[i + 1]
            i += 2
        elif arg.startswith("-"):
            die("unknown option %s" % arg)
        else:
            configs.append(arg)
            i += 1
    if configs:
        die("the argv names a configuration file: %s" % " ".join(configs))

    if MODE == "hang":
        time.sleep(SLEEP)
        return
    if MODE == "hang-child":
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(%d)" % SLEEP])
        time.sleep(SLEEP)
        return
    if MODE == "stdout-flood":
        flood(sys.stdout)
        return
    if MODE == "stderr-flood":
        flood(sys.stderr)
        return
    if MODE == "decode":
        say(sys.stderr, "libpng error: IDAT: invalid stored block lengths\n"
                        "Error in pixReadStreamPng: internal png error\n"
                        "Error in pixReadStream: png: no pix returned\n"
                        "Leptonica Error in pixRead: pix not read: %s\n"
                        "Error during processing.\n" % os.path.basename(args[0]))
        sys.exit(1)
    if MODE == "too-large":
        say(sys.stderr, "Image too large: (33000, 8)\nError during processing.\n")
        sys.exit(1)
    if MODE == "fail":
        say(sys.stderr, "Error during processing.\n")
        sys.exit(2)
    if MODE == "no-lang":
        say(sys.stderr, "Error opening data file %s/rus.traineddata\n"
                        "Please make sure the TESSDATA_PREFIX environment variable is set to your \"tessdata\" "
                        "directory.\nFailed loading language 'rus'\nTesseract couldn't load any languages!\n"
                        "Could not initialize tesseract.\n" % tessdata.rstrip("/"))
        sys.exit(1)

    text = text_of_mode()
    if MODE == "non-utf8":
        write_outputs(outbase, variables, b"Field notes \xff\xfe on a \xc3\x28 small machine\n", tsv_of("Field notes"))
    elif MODE == "big-out":
        if variables.get("tessedit_create_txt") == "1":
            with open(outbase + ".txt", "wb") as fh:
                fh.truncate(OUTPUT_MAX + 1)
        write_outputs(outbase, dict(variables, tessedit_create_txt="0"), b"", tsv_of(text))
    elif MODE == "bad-tsv":
        write_outputs(outbase, variables, text.encode("utf-8"), HEADER + "\n5\t1\tnot-a-row\n")
    else:
        write_outputs(outbase, variables, text.encode("utf-8"), tsv_of(text))
    if MODE == "partial-lang":
        say(sys.stderr, "Error opening data file %s/rus.traineddata\n"
                        "Please make sure the TESSDATA_PREFIX environment variable is set to your \"tessdata\" "
                        "directory.\nFailed loading language 'rus'\n" % tessdata.rstrip("/"))
    say(sys.stderr, "Estimating resolution as 492\n")


def main() -> None:
    args = sys.argv[1:]
    if "--version" in args or "-v" in args:
        count("version")
        version()
    elif "--list-langs" in args:
        count("list-langs")
        list_langs()
    else:
        count("run")
        run(args)


if __name__ == "__main__":
    main()
