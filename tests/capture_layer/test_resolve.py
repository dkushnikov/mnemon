"""§7.1 — canonicalization v1 on the fixture cases, the resolver, `--canonical-only` and its schema."""
from __future__ import annotations

import json
import os
import unittest

from _helpers import BIN, FIX, assert_same_keys, expected_shapes, read_json, run_cli

from capture import resolve as R


class CanonicalFixture(unittest.TestCase):
    cases = read_json(os.path.join(FIX, "canonical.json"))

    def test_fixture_size(self):
        self.assertGreaterEqual(len(self.cases), 30)

    def test_every_case(self):
        for case in self.cases:
            url = case["url"]
            if "error" in case:
                with self.assertRaises(R.InvalidURL, msg=case["note"]):
                    R.canonicalize(url)
                continue
            res = R.resolve(url)
            self.assertEqual(res.canonical.url, case["canonical_url"], case["note"])
            self.assertEqual(res.kind, case["kind"], case["note"])
            self.assertEqual(res.requested_version, case["requested_version"], case["note"])
            self.assertEqual(res.canonical.version, 1)

    def test_canonical_is_idempotent(self):
        for case in self.cases:
            if "error" in case:
                continue
            once = R.canonicalize(case["url"]).url
            self.assertEqual(R.canonicalize(once).url, once, case["note"])


class Rules(unittest.TestCase):
    def test_version_constant(self):
        self.assertEqual(R.VERSION, 1)
        with self.assertRaises(ValueError):
            R.canonicalize("https://example.com/", version=2)
        self.assertEqual(R.canonicalize("https://example.com/", version=1).url, "https://example.com/")

    def test_tracking_keys_constant(self):
        for key in ("utm_source", "utm_x", "fbclid", "hsCtaTracking", "hide_intro_popup", "_ga"):
            self.assertTrue(R.is_tracking_key(key), key)
        for key in ("s", "ref", "q", "page", "id", "utm"):
            self.assertFalse(R.is_tracking_key(key), key)

    def test_dropped_params_and_fragment_recorded(self):
        c = R.canonicalize("https://example.com/p?utm_source=a&s=1&fbclid=2#intro")
        self.assertEqual(c.dropped_params, ["utm_source", "fbclid"])
        self.assertEqual(c.dropped_fragment, "intro")
        self.assertIsNone(c.host_rule)
        self.assertEqual(R.canonicalize("https://arxiv.org/abs/2401.12345v1").host_rule, "arxiv")
        self.assertEqual(R.canonicalize("https://youtu.be/abc").host_rule, "youtube")

    def test_youtube_keeps_v_alone(self):
        """The video is the resource: the navigation context (list, index, t, si, feature, pp) and every other
        parameter are dropped — there is no separate list of navigation keys to keep in step."""
        c = R.canonicalize("https://www.youtube.com/watch?v=abc123&list=PLx&index=3&t=10s&si=zzz&feature=share&pp=ygU&foo=bar")
        self.assertEqual(c.url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(c.dropped_params, ["list", "index", "t", "si", "feature", "pp", "foo"])
        self.assertFalse(hasattr(R, "YOUTUBE_NAV_KEYS"))

    def test_arxiv_resolution(self):
        res = R.resolve("https://arxiv.org/pdf/2401.12345v2")
        self.assertEqual(res.kind, "arxiv")
        self.assertEqual(res.canonical.url, "https://arxiv.org/abs/2401.12345")
        self.assertEqual(res.metadata_url, "https://arxiv.org/abs/2401.12345v2")
        self.assertEqual(res.fetch_url, "https://arxiv.org/html/2401.12345v2")
        self.assertEqual(res.requested_version, 2)
        res = R.resolve("https://arxiv.org/abs/2401.12345")
        self.assertEqual(res.metadata_url, "https://arxiv.org/abs/2401.12345")
        self.assertIsNone(res.requested_version)
        self.assertEqual(R.arxiv_parts("https://arxiv.org/abs/2401.12345v7"), ("2401.12345", 7))
        self.assertIsNone(R.arxiv_parts("https://example.com/abs/2401.12345"))

    def test_invalid_url_is_typed(self):
        try:
            R.canonicalize("ftp://x/")
        except R.InvalidURL as exc:
            self.assertEqual(exc.error_class, "invalid-url")
            self.assertIsInstance(exc, ValueError)
        else:
            self.fail("ftp accepted")


class CanonicalOnlyCLI(unittest.TestCase):
    def test_object_matches_the_fixture_schema(self):
        rc, out, err = run_cli([os.path.join(BIN, "capture-fetch.py"), "--canonical-only",
                                "https://Example.com/post?utm_source=x&s=alpha#intro"])
        self.assertEqual(rc, 0, err)
        obj = json.loads(out)
        fixture = expected_shapes("canonical.json")
        assert_same_keys(self, obj, fixture, "mnemon-canonical/1")
        self.assertEqual(obj["schema"], "mnemon-canonical/1")
        self.assertEqual(obj["canonical_url"], "https://example.com/post?s=alpha")
        self.assertEqual(obj["canonical_version"], 1)
        self.assertEqual(obj["kind"], "page")
        self.assertEqual(obj["fetch_url"], "https://example.com/post?s=alpha")
        self.assertIsNone(obj["requested_version"])
        self.assertEqual(obj["url"], "https://Example.com/post?utm_source=x&s=alpha#intro")

    def test_trimmed_url_and_arxiv(self):
        rc, out, _ = run_cli([os.path.join(BIN, "capture-fetch.py"), "--canonical-only", "  https://arxiv.org/abs/2401.12345v3 "])
        obj = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(obj["url"], "https://arxiv.org/abs/2401.12345v3")
        self.assertEqual(obj["kind"], "arxiv")
        self.assertEqual(obj["requested_version"], 3)

    def test_invalid_exits_2(self):
        rc, out, err = run_cli([os.path.join(BIN, "capture-fetch.py"), "--canonical-only", "ftp://example.com/x"])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        obj = json.loads(err.strip().splitlines()[-1])
        self.assertEqual(obj["error_class"], "invalid-url")

    def test_stdin_batch(self):
        rc, out, err = run_cli([os.path.join(BIN, "capture-fetch.py"), "--canonical-only", "--stdin"],
                               stdin=b"https://example.com/a?utm_source=1\nftp://bad/\n\nhttps://youtu.be/abc\n")
        self.assertEqual(rc, 0, err)
        lines = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]["canonical_url"], "https://example.com/a")
        self.assertEqual(lines[1]["error_class"], "invalid-url")
        self.assertEqual(lines[1]["url"], "ftp://bad/")
        self.assertEqual(lines[2]["kind"], "youtube")


if __name__ == "__main__":
    unittest.main()
