"""§3.10 / §7.15 (the audit half): `canonical-audit.py` groups folders whose URLs coincide under v1, reads
nothing but headers, writes nothing."""
from __future__ import annotations

import json
import os
import shutil
import unittest

from _helpers import BIN, run_cli, tmpdir

AUDIT = os.path.join(BIN, "canonical-audit.py")


class Audit(unittest.TestCase):
    def setUp(self):
        self.vault = tmpdir("mnemon-audit-")
        self.folder("2026-10-01_aaaaaaaa", 'url: "https://example.com/post?utm_source=a"\n', extract=True)
        self.folder("2026-10-02_bbbbbbbb", 'url: "https://example.com/post?utm_source=b"\nsupersedes: "2026-10-01_aaaaaaaa"\n')
        self.folder("2026-10-03_cccccccc", "url: https://example.com/other\n", extract=True)
        self.folder("2026-10-04_dddddddd", 'url: "https://example.com/third#intro"\ncanonical_url: "https://example.com/other"\n')
        self.folder("2026-10-05_eeeeeeee", "url: not a url\n")
        os.makedirs(os.path.join(self.vault, "Sources", "2026-10-06_ffffffff"))
        with open(os.path.join(self.vault, "Sources", "2026-10-06_ffffffff", "source.md"), "w") as fh:
            fh.write("---\ntype: source\nurl: https://example.com/unterminated\n")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def folder(self, name, lines, extract=False):
        d = os.path.join(self.vault, "Sources", name)
        os.makedirs(d)
        with open(os.path.join(d, "source.md"), "w") as fh:
            fh.write("---\ntype: source\n%scaptured: 2026-10-01\n---\nbody\n" % lines)
        if extract:
            with open(os.path.join(d, "extract.md"), "w") as fh:
                fh.write("---\ntype: extract\n---\n")

    def snapshot(self):
        out = {}
        for root, dirs, files in os.walk(self.vault):
            for f in files:
                p = os.path.join(root, f)
                with open(p, "rb") as fh:
                    out[p] = fh.read()
        return out

    def test_groups_and_never_writes(self):
        before = self.snapshot()
        rc, so, se = run_cli([AUDIT, self.vault, "--json"])
        self.assertEqual(rc, 0, se)
        self.assertEqual(self.snapshot(), before)
        d = json.loads(so)
        self.assertEqual(d["schema"], "mnemon-canonical-audit/1")
        self.assertEqual(d["canonical_version"], 1)
        groups = {g["canonical_url"]: g for g in d["groups"]}
        self.assertEqual(sorted(groups), ["https://example.com/other", "https://example.com/post"])
        post = groups["https://example.com/post"]["revisions"]
        self.assertEqual([r["revision"] for r in post], ["2026-10-01_aaaaaaaa", "2026-10-02_bbbbbbbb"])
        self.assertEqual([r["extract"] for r in post], ["ok", "missing"])
        self.assertEqual(post[1]["supersedes"], "2026-10-01_aaaaaaaa")
        other = groups["https://example.com/other"]["revisions"]
        self.assertEqual([r["revision"] for r in other], ["2026-10-03_cccccccc", "2026-10-04_dddddddd"],
                         "canonical_url: joins the group of url:")
        self.assertIn("malformed-frontmatter 2026-10-06_ffffffff", se)

    def test_text_output(self):
        rc, so, se = run_cli([AUDIT, self.vault])
        self.assertEqual(rc, 0)
        self.assertIn("https://example.com/post\n  2026-10-01_aaaaaaaa", so)
        self.assertIn("supersedes=2026-10-01_aaaaaaaa", so)
        self.assertIn("2 group(s) coincide", so)
        empty = tmpdir("mnemon-audit-empty-")
        os.makedirs(os.path.join(empty, "Sources"))
        rc, so, se = run_cli([AUDIT, empty])
        self.assertEqual((rc, so.strip()), (0, "no canonical duplicates (canonicalization v1)"))
        shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
