#!/usr/bin/env python3
"""tests/test_text_audit.py — `mnemon audit text-mismatch` (bin/text-mismatch-audit.py; capture layer §18, A17).

Synthetic vaults only: legacy and managed revisions, raws in the archive and in the state dir, one fixture per
result class, the Markdown-link case, a CJK page, a legacy revision without frontmatter fields, the population
and its two flags, the JSON document, the dispatcher, the conversion deadline, and the no-write property (the
vault, the archive and the state dir hashed before and after; the temp dir left empty). The real converters run
(`capture-text.py` with whatever of defuddle / pandoc is installed — the outcomes here do not depend on which
candidate wins — and `pdftotext` when present). No network. Stdlib only, python 3.9.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
AUDIT = os.path.join(BIN, "text-mismatch-audit.py")
MNEMON = os.path.join(BIN, "mnemon")
CLASSES = ["match", "mismatch", "unprobed: no raw", "unprobed: no eligible lines",
           "unprobed: converter unavailable", "unprobed: raw unreadable"]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, BIN)
    spec.loader.exec_module(mod)
    return mod


TMA = load("text_mismatch_audit", "text-mismatch-audit.py")
VL = load("vault_lock_for_tests", "vault-lock.py")


def page(title, *paragraphs, lang=""):
    attr = ' lang="%s"' % lang if lang else ""
    body = "".join("<p>%s</p>" % p for p in paragraphs)
    return ('<!doctype html><html%s><head><meta charset="utf-8"><title>%s</title></head><body><article><h1>%s</h1>%s'
            '</article><nav>Home About Contact</nav></body></html>' % (attr, title, title, body))


def tiny_pdf(text):
    """A one-page PDF whose only text is `text` (Helvetica, one line) — enough for pdftotext."""
    stream = "BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("ascii")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode("ascii")
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)).encode("ascii")
    return out


GENUINE_B = "The beta report counts forty seven new members in the spring quarter."
INVENTED_B = ["In summary the club grew a lot and everyone was happy about it.",
              "Members praised the new schedule and asked for more evening sessions.",
              "The author argues that growth will continue for the foreseeable future.",
              "Overall the piece is an optimistic take on community building today."]
CJK = "知识管理系统需要把原始页面的文字完整地保存下来以便以后核对"
PDF_LINE = "The portable document carries a sentence of more than six words."

MANAGED_CAPTURE = ("capture_id: \"cap-k\"\nresource_sha256: \"%s\"\nrevision: \"2026-01-11_kkkkkkkk\"\ncapture:\n"
                   "  raw_store: state\n  raw_kind: response\n  text_sha256: \"x\"\n  completeness: complete\n" % ("0" * 64))


class Vault:
    """A temp vault with an archive, a state dir under a private XDG_STATE_HOME, an instance file, a TMPDIR."""

    def __init__(self, with_state=True):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="mnemon-tma-"))
        self.vault = os.path.join(self.root, "vault")
        self.archive = os.path.join(self.root, "archive")
        self.xdg_state = os.path.join(self.root, "xdg-state")
        self.tmp = os.path.join(self.root, "tmp")
        for d in (os.path.join(self.vault, "Sources"), self.archive, self.xdg_state, self.tmp):
            os.makedirs(d)
        self.config = os.path.join(self.root, "instance.yaml")
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write('vault_path: "%s"\narchive_dir: "%s"\n' % (self.vault, self.archive))
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.xdg_state}):
            self.state = VL.vault_state_dir(self.vault)
        if with_state:
            os.makedirs(os.path.join(self.state, "raw"))

    def env(self, extra=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MNEMON_")}
        env.update({"XDG_STATE_HOME": self.xdg_state, "XDG_CONFIG_HOME": os.path.join(self.root, "xdg-config"),
                    "TMPDIR": self.tmp})
        if extra:
            env.update(extra)
        return env

    def source(self, rev, front, body, raw=None, raw_name=None, store="archive"):
        d = os.path.join(self.vault, "Sources", rev)
        os.makedirs(d)
        with open(os.path.join(d, "source.md"), "w", encoding="utf-8") as fh:
            fh.write("---\n%s---\n%s" % (front, body) if front is not None else body)
        if raw is not None:
            where = self.archive if store == "archive" else os.path.join(self.state, "raw")
            data = raw.encode("utf-8") if isinstance(raw, str) else raw
            with open(os.path.join(where, raw_name or rev + ".html"), "wb") as fh:
                fh.write(data)

    def write(self, rel, text):
        p = os.path.join(self.vault, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def tree(self):
        """Every path under the vault, the archive and the state home: kind, mode, size, inode, mtime and the
        bytes' hash — a write, a create-and-delete inside a directory (its mtime) or a chmod shows."""
        out = {}
        for top in (self.vault, self.archive, self.xdg_state):
            for root, dirs, files in os.walk(top):
                for name in dirs + files:
                    p = os.path.join(root, name)
                    st = os.lstat(p)
                    digest = None
                    if os.path.isfile(p) and os.access(p, os.R_OK):
                        with open(p, "rb") as fh:
                            digest = hashlib.sha256(fh.read()).hexdigest()
                    out[p] = (st.st_mode, st.st_size, st.st_ino, st.st_mtime_ns, digest)
                st = os.lstat(root)
                out[root] = (st.st_mode, st.st_ino, st.st_mtime_ns)
        return out

    def run(self, *args, env_extra=None, via_dispatcher=False, timeout=300):
        if via_dispatcher:
            argv = [MNEMON, "--config", self.config, "audit", "text-mismatch"] + list(args)
        else:
            argv = [sys.executable, AUDIT, "--config", self.config] + list(args)
        p = subprocess.run(argv, capture_output=True, env=self.env(env_extra), timeout=timeout, cwd=self.root)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def close(self):
        for root, dirs, files in os.walk(self.root):
            for n in dirs + files:
                try:
                    os.chmod(os.path.join(root, n), 0o700)
                except OSError:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)


def legacy(url, extra=""):
    return 'type: source\nsource_type: article\norigin: url\nurl: "%s"\ncaptured: 2026-01-01\n%s' % (url, extra)


def build_main(v: Vault):
    # match — the Markdown-link case: the body links what the page writes as plain anchor text
    v.source("2026-01-01_aaaaaaaa", legacy("https://example.test/alpha", 'archive: "2026-01-01_aaaaaaaa.html"\n'),
             "# Alpha page title\n\nThe first paragraph of the alpha page has more than six words.\n\n"
             "A second paragraph carries a link to the [documentation pages](https://example.test/docs) of the project.\n",
             page("Alpha page title", "The first paragraph of the alpha page has more than six words.",
                  'A second paragraph carries a link to the <a href="/docs">documentation pages</a> of the project.'))
    # mismatch — one line of the page, four that are not in it
    v.source("2026-01-02_bbbbbbbb", legacy("https://example.test/beta", 'archive: "2026-01-02_bbbbbbbb.html"\n'),
             GENUINE_B + "\n\n" + "\n\n".join(INVENTED_B) + "\n", page("Beta", GENUINE_B))
    # no raw
    v.source("2026-01-03_cccccccc", legacy("https://example.test/gamma"), "A body with more than six words but no raw kept.\n")
    # no eligible lines
    v.source("2026-01-04_dddddddd", legacy("https://example.test/delta"), "Too short.\nAlso short here.\n",
             page("Delta", "Too short."))
    # converter unavailable — a raw of a kind no read-only converter reads
    v.source("2026-01-05_eeeeeeee", legacy("https://example.test/epsilon.mp3", 'archive: "2026-01-05_eeeeeeee.mp3"\n'),
             "A transcript body with more than six words in it.\n", b"ID3\x03\x00binary", raw_name="2026-01-05_eeeeeeee.mp3")
    # raw unreadable — a text raw that is not UTF-8
    v.source("2026-01-06_ffffffff", legacy("https://example.test/zeta", 'archive: "2026-01-06_ffffffff.txt"\n'),
             "A rendered body with more than six words in it.\n", b"caf\xe9 \xff\xfe broken", raw_name="2026-01-06_ffffffff.txt")
    # a legacy revision without frontmatter fields: no archive, no type, no captured — its raw found by name
    v.source("2026-01-07_gggggggg", 'origin: url\nurl: "https://example.test/eta"\n',
             "The eta page body is short but has enough words.\n", page("Eta", "The eta page body is short but has enough words."))
    # CJK
    v.source("2026-01-08_hhhhhhhh", legacy("https://example.test/theta"), CJK + "\n", page("Theta", CJK + "。", lang="zh"))
    # a raw with no text at all
    v.source("2026-01-09_iiiiiiii", legacy("https://example.test/iota"), "The body says something the raw cannot hold at all.\n",
             "<html><head><title></title></head><body></body></html>")
    # bookmark: never in the population
    v.source("2026-01-10_jjjjjjjj", legacy("https://example.test/kappa", "tier: bookmark\n"), "A card body with six words or more.\n")
    # managed with a completeness record, raw in the state dir: only with --all-url
    v.source("2026-01-11_kkkkkkkk", legacy("https://example.test/lambda", MANAGED_CAPTURE),
             "The lambda page has a managed capture with a state raw.\n",
             page("Lambda", "The lambda page has a managed capture with a state raw."), store="state")
    # superseded legacy (with a raw) and its current successor (without one)
    v.source("2026-01-12_llllllll", legacy("https://example.test/mu", 'archive: "2026-01-12_llllllll.html"\n'),
             "The mu page was captured before its successor arrived.\n",
             page("Mu", "The mu page was captured before its successor arrived."))
    v.source("2026-01-13_mmmmmmmm", legacy("https://example.test/mu", 'supersedes: "2026-01-12_llllllll"\n'),
             "The successor of the mu page has six words at least.\n")
    # not a url origin
    v.source("2026-01-14_nnnnnnnn", 'type: source\norigin: file\ncaptured: 2026-01-01\n', "A file body with six words in it.\n",
             page("Nu", "A file body with six words in it."))
    # a PDF raw
    v.source("2026-01-15_oooooooo", legacy("https://example.test/paper.pdf", 'archive: "2026-01-15_oooooooo.pdf"\n'),
             PDF_LINE + "\n", tiny_pdf(PDF_LINE), raw_name="2026-01-15_oooooooo.pdf")
    # citations: a synthesis note links bbbb and cccc, a collection lists aaaa
    v.write("Synthesis/note.md", "---\nstatus: seed\n---\nSee [[Sources/2026-01-02_bbbbbbbb/extract]] and "
                                 "[[Sources/2026-01-03_cccccccc]].\n")
    v.write("Collections/Dossier.md", "---\ntype: collection\nstatus: open\nquestion: \"q?\"\ncurated_by: owner\n"
                                      "created: 2026-01-01\ndomains: [learning]\n---\n\n# Dossier\n\n## Items\n\n"
                                      "- [[Sources/2026-01-01_aaaaaaaa/extract]] — **an item** — why\n")


HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None


class MainVault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = Vault()
        build_main(cls.v)
        cls.before = cls.v.tree()
        cls.rc, cls.out, cls.err = cls.v.run("--json")
        cls.doc = json.loads(cls.out) if cls.rc == 0 else None
        cls.after = cls.v.tree()

    @classmethod
    def tearDownClass(cls):
        cls.v.close()

    def by_rev(self, doc=None):
        return {r["revision"]: r for r in (doc or self.doc)["results"]}

    def test_exit_and_schema(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertEqual(self.doc["schema"], "mnemon-text-mismatch-audit/1")
        self.assertEqual(sorted(self.doc["counts"]), sorted(CLASSES))
        self.assertEqual(self.doc["population"], {"scope": "pre-contract", "cited_only": False, "revisions": 11})
        self.assertTrue(self.doc["snapshot"]["complete"])

    def test_one_fixture_per_class(self):
        r = self.by_rev()
        self.assertEqual(r["2026-01-01_aaaaaaaa"]["class"], "match")
        self.assertEqual(r["2026-01-02_bbbbbbbb"]["class"], "mismatch")
        self.assertEqual(r["2026-01-03_cccccccc"]["class"], "unprobed: no raw")
        self.assertEqual(r["2026-01-04_dddddddd"]["class"], "unprobed: no eligible lines")
        self.assertEqual(r["2026-01-05_eeeeeeee"]["class"], "unprobed: converter unavailable")
        self.assertIn(".mp3", r["2026-01-05_eeeeeeee"]["detail"])
        self.assertEqual(r["2026-01-06_ffffffff"]["class"], "unprobed: raw unreadable")
        self.assertIn("not UTF-8", r["2026-01-06_ffffffff"]["detail"])

    def test_markdown_link_case_matches(self):
        a = self.by_rev()["2026-01-01_aaaaaaaa"]
        self.assertEqual((a["class"], a["not_found"], a["share"]), ("match", 0, 0.0))
        self.assertEqual(a["eligible_lines"], 2, "the title heading is not required, both paragraphs are")
        self.assertTrue(a["converter"].startswith("capture-text:"), a["converter"])
        self.assertEqual((a["raw"], a["raw_store"]), ("2026-01-01_aaaaaaaa.html", "archive"))

    def test_mismatch_share_and_samples(self):
        b = self.by_rev()["2026-01-02_bbbbbbbb"]
        self.assertEqual((b["eligible_lines"], b["not_found"], b["share"]), (5, 4, 0.8))
        self.assertEqual(b["samples"], INVENTED_B[:3])
        ms = self.doc["mismatch_share"]
        self.assertEqual(ms["count"], 2)
        self.assertEqual([x["count"] for x in ms["buckets"]], [0, 0, 0, 0, 2])

    def test_legacy_revision_without_fields_found_by_name(self):
        g = self.by_rev()["2026-01-07_gggggggg"]
        self.assertEqual((g["class"], g["raw"]), ("match", "2026-01-07_gggggggg.html"))

    def test_cjk(self):
        h = self.by_rev()["2026-01-08_hhhhhhhh"]
        self.assertEqual((h["class"], h["eligible_lines"]), ("match", 1))

    def test_a_raw_without_text_is_a_mismatch(self):
        i = self.by_rev()["2026-01-09_iiiiiiii"]
        self.assertEqual((i["class"], i["share"], i["detail"]), ("mismatch", 1.0, "the raw converts to no text"))
        self.assertEqual((i["raw_acquisition"], i["converted_words"]), ("thin", 0))

    def test_raw_acquisition_only_for_html_raws(self):
        r = self.by_rev()
        self.assertEqual(r["2026-01-01_aaaaaaaa"]["raw_acquisition"], "thin")
        self.assertIsNone(r["2026-01-06_ffffffff"]["raw_acquisition"])
        self.assertIsNone(r["2026-01-03_cccccccc"]["raw_acquisition"])

    def test_population_excludes_bookmarks_managed_superseded_and_other_origins(self):
        revs = set(self.by_rev())
        for rev in ("2026-01-10_jjjjjjjj", "2026-01-11_kkkkkkkk", "2026-01-12_llllllll", "2026-01-14_nnnnnnnn"):
            self.assertNotIn(rev, revs)
        self.assertEqual(self.by_rev()["2026-01-13_mmmmmmmm"]["class"], "unprobed: no raw")

    def test_pdf(self):
        o = self.by_rev()["2026-01-15_oooooooo"]
        if HAVE_PDFTOTEXT:
            self.assertEqual((o["class"], o["converter"]), ("match", "pdftotext"))
        else:
            self.assertEqual(o["class"], "unprobed: converter unavailable")

    def test_cited_flag_on_every_result(self):
        r = self.by_rev()
        self.assertTrue(r["2026-01-01_aaaaaaaa"]["cited"])
        self.assertTrue(r["2026-01-02_bbbbbbbb"]["cited"])
        self.assertFalse(r["2026-01-04_dddddddd"]["cited"])

    def test_no_write(self):
        self.assertEqual(self.before, self.after)
        self.assertEqual(os.listdir(self.v.tmp), [], "the private temp dirs are removed")
        self.assertFalse(os.path.exists(os.path.join(self.v.state, "lock")), "no lock taken")


class Flags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = Vault()
        build_main(cls.v)

    @classmethod
    def tearDownClass(cls):
        cls.v.close()

    def run_json(self, *args, **kw):
        rc, out, err = self.v.run("--json", *args, **kw)
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def test_all_url_widens_to_every_url_revision_with_a_raw(self):
        before = self.v.tree()
        doc = self.run_json("--all-url")
        self.assertEqual(self.v.tree(), before)
        revs = {r["revision"]: r for r in doc["results"]}
        self.assertEqual(doc["population"]["scope"], "all-url")
        self.assertEqual(sorted(revs), sorted([
            "2026-01-01_aaaaaaaa", "2026-01-02_bbbbbbbb", "2026-01-04_dddddddd", "2026-01-05_eeeeeeee",
            "2026-01-06_ffffffff", "2026-01-07_gggggggg", "2026-01-08_hhhhhhhh", "2026-01-09_iiiiiiii",
            "2026-01-11_kkkkkkkk", "2026-01-12_llllllll", "2026-01-15_oooooooo"]))
        k = revs["2026-01-11_kkkkkkkk"]
        self.assertEqual((k["class"], k["raw_store"], k["completeness"]), ("match", "state", "complete"))
        self.assertEqual((revs["2026-01-12_llllllll"]["class"], revs["2026-01-12_llllllll"]["current"]), ("match", False))

    def test_cited_limits_to_cited_revisions(self):
        doc = self.run_json("--cited")
        self.assertEqual(sorted(r["revision"] for r in doc["results"]),
                         ["2026-01-01_aaaaaaaa", "2026-01-02_bbbbbbbb", "2026-01-03_cccccccc"])
        self.assertTrue(doc["population"]["cited_only"])
        self.assertTrue(doc["snapshot"]["cited_scan_complete"])

    def test_human_table_names_candidates_never_a_cause(self):
        before = self.v.tree()
        rc, out, err = self.v.run()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.v.tree(), before)
        self.assertIn("Text mismatch candidates", out)
        self.assertIn("  mismatch                               2", out)
        self.assertIn("not found: " + INVENTED_B[0], out)
        self.assertIn("2026-01-05_eeeeeeee  unprobed: converter unavailable", out)
        self.assertIn("converter drift", out)
        self.assertNotIn("paraphras", out.lower())

    def test_dispatcher(self):
        rc, out, err = self.v.run("--json", "--cited", via_dispatcher=True)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["population"]["revisions"], 3)

    def test_dispatcher_help_needs_no_instance_and_selectors_belong_before_the_command(self):
        env = self.v.env()
        p = subprocess.run([MNEMON, "audit", "text-mismatch", "--help"], capture_output=True, env=env, cwd=self.v.root)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(b"converter drift", p.stdout)
        p = subprocess.run([MNEMON, "audit", "text-mismatch", "--config", self.v.config], capture_output=True, env=env,
                           cwd=self.v.root)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"belongs before the command", p.stderr)

    def test_bad_config_exits_2(self):
        p = subprocess.run([sys.executable, AUDIT, "--config", os.path.join(self.v.root, "nope.yaml")],
                           capture_output=True, env=self.v.env())
        self.assertEqual(p.returncode, 2)

    def test_a_timeout_that_is_not_a_positive_finite_number_exits_2(self):
        for t in ("0", "-1", "inf", "nan"):
            rc, out, err = self.v.run("--timeout", t)
            self.assertEqual(rc, 2, t)

    def test_tmpdir_inside_the_vault_is_refused(self):
        inner = os.path.join(self.v.vault, "_tmp_inside")
        os.makedirs(inner)
        try:
            rc, out, err = self.v.run(env_extra={"TMPDIR": inner})
            self.assertEqual(rc, 2)
            self.assertIn("set TMPDIR elsewhere", err)
            self.assertEqual(os.listdir(inner), [])
        finally:
            os.rmdir(inner)


class Deadline(unittest.TestCase):
    def test_a_hung_conversion_is_killed_and_reported(self):
        v = Vault(with_state=False)
        try:
            v.source("2026-02-01_aaaaaaaa", legacy("https://example.test/slow"), "The slow page body has six words or more.\n",
                     page("Slow", "The slow page body has six words or more."))
            before = v.tree()
            t0 = time.monotonic()
            rc, out, err = v.run("--json", "--timeout", "2",
                                 env_extra={"MNEMON_TEST_HOOKS": "1", "MNEMON_TEST_CONVERT_HANG": "sleep"})
            secs = time.monotonic() - t0
            self.assertEqual(rc, 0, err)
            r = json.loads(out)["results"][0]
            self.assertEqual(r["class"], "unprobed: converter unavailable")
            self.assertIn("exceeded 2 s", r["detail"])
            self.assertLess(secs, 30, "the deadline bounds the run (%.1f s)" % secs)
            self.assertEqual(v.tree(), before)
            self.assertFalse(os.path.exists(v.state), "the state dir is not created")
            self.assertEqual(os.listdir(v.tmp), [])
        finally:
            v.close()


class Units(unittest.TestCase):
    def test_dataless_placeholder(self):
        class St:
            st_flags = 0x40000000
        self.assertEqual(TMA.is_dataless(St()), sys.platform == "darwin")
        St.st_flags = 0
        self.assertFalse(TMA.is_dataless(St()))

    def test_a_placeholder_is_never_opened(self):
        d = tempfile.mkdtemp(prefix="mnemon-tma-u-")
        try:
            p = os.path.join(d, "x.html")
            with open(p, "w") as fh:
                fh.write("<p>x</p>")
            with mock.patch.object(TMA, "is_dataless", return_value=True), \
                    mock.patch("builtins.open", side_effect=AssertionError("opened")):
                self.assertIn("cloud placeholder", TMA.unreadable_reason(p))
        finally:
            shutil.rmtree(d)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_raw_without_read_permission(self):
        d = tempfile.mkdtemp(prefix="mnemon-tma-u-")
        try:
            p = os.path.join(d, "x.html")
            with open(p, "w") as fh:
                fh.write("<p>x</p>")
            os.chmod(p, 0)
            self.assertIn("cannot read", TMA.unreadable_reason(p))
        finally:
            os.chmod(p, 0o600)
            shutil.rmtree(d)

    def test_pdftotext_missing_is_converter_unavailable(self):
        d = tempfile.mkdtemp(prefix="mnemon-tma-u-")
        try:
            with mock.patch.object(TMA.shutil, "which", return_value=None):
                c = TMA.convert_pdf(os.path.join(d, "x.pdf"), 5, d)
            self.assertEqual((c.klass, c.detail), ("unprobed: converter unavailable", "pdftotext (Poppler) is not installed"))
        finally:
            shutil.rmtree(d)

    def test_a_site_bundle_goes_through_assemble(self):
        d = tempfile.mkdtemp(prefix="mnemon-tma-u-")
        try:
            bundle = os.path.join(d, "2026-01-01_aaaaaaaa.site")
            os.makedirs(bundle)
            stub = os.path.join(d, "stub-site.py")
            with open(stub, "w") as fh:
                fh.write("import sys\na = sys.argv\nassert a[1] == 'assemble'\n"
                         "open(a[a.index('--out') + 1], 'w').write('assembled text of the bundle')\n"
                         "open(a[a.index('--report') + 1], 'w').write('{}')\n")
            with mock.patch.object(TMA, "CAPTURE_SITE", stub):
                c = TMA.convert_raw(bundle, {"name": "2026-01-01_aaaaaaaa"}, 10, d, None)
            self.assertEqual((c.text, c.converter, c.klass), ("assembled text of the bundle", "site-assemble", None))
        finally:
            shutil.rmtree(d)

    def test_raw_acquisition_classifies_the_html_raw(self):
        d = tempfile.mkdtemp(prefix="mnemon-tma-u-")
        try:
            wall = os.path.join(d, "w.html")
            with open(wall, "w") as fh:
                fh.write("<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>")
            ok = os.path.join(d, "ok.html")
            with open(ok, "w") as fh:
                fh.write("<html><body><p>%s</p></body></html>" % " ".join("word%d" % i for i in range(80)))
            txt = os.path.join(d, "t.txt")
            with open(txt, "w") as fh:
                fh.write("text")
            self.assertEqual(TMA.raw_acquisition(wall, 60), "bot-wall")
            self.assertEqual(TMA.raw_acquisition(ok, 60), "ok")
            self.assertIsNone(TMA.raw_acquisition(txt, 60))
        finally:
            shutil.rmtree(d)

    def test_raw_lookup_order(self):
        rec = {"name": "2026-01-01_aaaaaaaa", "archive": "other-name.html"}
        entries = {"other-name.html", "2026-01-01_aaaaaaaa.html"}
        self.assertEqual(TMA.find_raw(rec, "/a", entries, None), ("archive", "/a/other-name.html"))
        self.assertEqual(TMA.find_raw(dict(rec, archive=None), "/a", entries, None),
                         ("archive", "/a/2026-01-01_aaaaaaaa.html"))
        self.assertEqual(TMA.find_raw(dict(rec, archive="unarchivable"), "/a", set(), None), (None, None))
        self.assertIn("unarchivable", TMA.no_raw_detail(dict(rec, archive="unarchivable")))
        self.assertIn("does not hold", TMA.no_raw_detail(rec))


if __name__ == "__main__":
    unittest.main(verbosity=1)
