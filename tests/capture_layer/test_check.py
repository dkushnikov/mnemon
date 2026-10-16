"""§7.4 (the decision table, one fixture per row), §7.5 (the conversion check, pure), §7.8 (inventory and
outcome)."""
from __future__ import annotations

import os
import unittest

from _helpers import FIX, read_json

from capture import check as C

WORDS300 = " ".join("word%d" % i for i in range(300))


def expand(body: str) -> bytes:
    return body.replace("WORDS300", WORDS300).replace("SCRIPT2000", "x" * 2000).encode("utf-8")


class DecisionTable(unittest.TestCase):
    rows = read_json(os.path.join(FIX, "acquisition.json"))

    def test_every_row(self):
        covered = set()
        for case in self.rows:
            acq = C.acquisition(case["status"], case["headers"], expand(case["body"]), thin_words=case["thin_words"])
            label = "row %s: %s" % (case["row"], case.get("note") or case["outcome"])
            self.assertEqual(acq.outcome, case["outcome"], label)
            self.assertEqual(acq.chain, case["chain"], label)
            self.assertEqual(acq.retryable, case["retryable"], label)
            if acq.outcome == "ok":
                self.assertIsNone(acq.kind, label)
                self.assertIsNone(acq.evidence, label)
            else:
                self.assertEqual(acq.kind, acq.outcome, label)
                self.assertTrue(acq.evidence, label)
            if "evidence" in case:
                self.assertEqual(acq.evidence, case["evidence"], label)
            covered.add(str(case["row"]))
        self.assertEqual(sorted(covered, key=lambda s: (len(s), s)), [str(i) for i in range(1, 17)] + ["3xx"])

    def test_report_shape_and_title(self):
        acq = C.acquisition(200, {}, b"<html><head><title> A &amp; B\n title </title></head><body>%s</body></html>" % WORDS300.encode(),
                            thin_words=60)
        self.assertEqual(acq.as_report(), {"outcome": "ok", "kind": None, "evidence": None, "visible_words": 300})
        self.assertEqual(acq.title, "A & B title")

    def test_policy_error_kinds_never_reach_the_table(self):
        from capture.policy import PolicyError
        for kind, retryable in (("timeout", True), ("connection", True), ("tls", True), ("ssrf-blocked", False),
                                ("ssrf-redirect", False), ("too-large", False), ("scheme", False)):
            self.assertEqual(PolicyError(kind).retryable, retryable, kind)


class ConversionCheck(unittest.TestCase):
    REF = ("## Deploy the company brain\n"
           "This is the first paragraph with more than six words in it.\n"
           "Short line here\n"
           "Skip to content\n"
           "We use cookies to improve your experience okay\n"
           "We use cookies to improve your experience on this site and we track every single click\n"
           "## Second heading\n"
           "Comments\n"
           "A final paragraph that also carries more than six words.\n")

    def complete(self):
        return ("# Deploy the company brain\n\nThis is the **first** paragraph with more than six words in it.\n\n"
                "We use cookies to improve your experience on this site and we track every single click\n\n"
                "## Second heading\n\nA final paragraph that also carries more than six words.\n")

    def test_complete_candidate_passes(self):
        conv = C.conversion(self.complete(), self.REF, None)
        self.assertTrue(conv.passed, conv)
        self.assertEqual(conv.reference_count, 9)
        self.assertEqual(conv.eligible_count, 5)

    def test_missing_six_word_line(self):
        cand = self.complete().replace("A final paragraph that also carries more than six words.", "")
        conv = C.conversion(cand, self.REF, None)
        self.assertEqual(len(conv.missing), 1)
        self.assertEqual(conv.missing[0], "A final paragraph that also carries more than six words.")
        self.assertFalse(conv.passed)

    def test_glued_line_is_counted_apart_and_rejected(self):
        cand = self.complete().replace("A final paragraph that also carries more than six words.",
                                       "Afinalparagraphthatalsocarriesmorethansixwords.")
        conv = C.conversion(cand, self.REF, None)
        self.assertEqual(len(conv.glued), 1)
        self.assertEqual(len(conv.missing), 0)
        self.assertFalse(conv.passed)

    def test_markup_whitespace_case_and_nfkc_forms_are_present(self):
        ref = "The ﬁne print mentions ０１２ and sees ten figures here\nA soft­hyphen line with more than six words\n"
        cand = ("THE   *fine*   `print`  mentions  012 and\nsees ten figures here\n\nA softhyphen line with more than six words\n")
        conv = C.conversion(cand, ref, None)
        self.assertTrue(conv.passed, conv)

    def test_links_in_candidates_do_not_break_sequences(self):
        ref = "Read the docs for more information about this feature\n"
        cand = "Read [the docs](https://example.com/docs/x) for more *information* about this feature\n"
        self.assertTrue(C.conversion(cand, ref, None).passed)

    def test_chrome_lines_not_required_but_long_cookie_line_is(self):
        self.assertIsNone(C.classify_line("Accept all cookies and continue to the site", None))
        self.assertIsNone(C.classify_line("We use cookies to improve your experience okay", None))
        self.assertEqual(C.classify_line("We use cookies to improve your experience on this site and we track every single click", None),
                         "body")
        self.assertIsNone(C.classify_line("Copyright © 2026 Example Inc. All rights reserved.", None))

    def test_title_line_not_required(self):
        self.assertIsNone(C.classify_line("## The Example Title Of This Page", C.line_key("The example title of this page")))
        self.assertIsNone(C.classify_line("The Example Title Of This Page Here", C.line_key("the example title of this page here")))

    def test_short_lines_and_headings(self):
        self.assertIsNone(C.classify_line("Five words are not enough", None))
        self.assertEqual(C.classify_line("## Five words are not enough", None), "heading")
        self.assertEqual(C.classify_line("## Two words", None), "heading")
        self.assertIsNone(C.classify_line("## Comments", None))
        self.assertIsNone(C.classify_line("## One", None))
        conv = C.conversion("nothing here at all\n", "## Two words\n", None)
        self.assertEqual(conv.headings_missing, ["Two words"])
        self.assertEqual(conv.eligible_count, 1)

    def test_cjk_line_is_eligible_and_token_check_free(self):
        ref = "日本語のテキストが二十文字以上続く場合は適格とみなされます\n"
        cand = "日本語の\nテキストが二十文字以上続く場合は適格とみなされます\n"
        conv = C.conversion(cand, ref, None)
        self.assertEqual(conv.eligible_count, 1)
        self.assertTrue(conv.passed)
        self.assertEqual(C.classify_line("短い", None), None)
        self.assertFalse(C.conversion("unrelated text\n", ref, None).passed)

    def test_no_eligible_line_is_unknown(self):
        conv = C.conversion("x\n", "Short line\n## One\n", None)
        self.assertEqual(conv.eligible_count, 0)
        self.assertEqual(C.outcome(None, conv, None), "unknown")

    def test_comment_sections_cut_from_the_reference_side(self):
        from capture import sanitize as S
        lines, n = S.cut_comment_sections(["## Body heading", "Real text with more than six words here", "## 3 Comments",
                                           "Great post, loved every single word of it!", "## Another top heading", "After"])
        self.assertEqual(n, 1)
        self.assertEqual(lines, ["## Body heading", "Real text with more than six words here", "## Another top heading", "After"])

    def test_comment_heading_rule_names_widgets_only(self):
        for line in ("## 12 Comments", "## Comments (3)", "## Leave a comment", "## Leave a Reply", "## Reader comments",
                     "## Post a comment", "3 comments"):
            self.assertTrue(C.COMMENTS.match(line), line)
        for line in ("## Discussion", "## Responses", "## Replies", "## Comments", "## Comment", "## Discussion of the results",
                     "## 12 Comments on the method"):
            self.assertIsNone(C.COMMENTS.match(line), line)

    def test_container_tokens_match_whole_tokens_only(self):
        for token in ("comments", "comment-list", "comments-section", "disqus_thread", "giscus", "respond", "comments__list"):
            self.assertTrue(C.COMMENTS_CONTAINER.match(token), token)
        for token in ("comment", "hljs-comment", "token", "commentary", "nocomments", "comment-count"):
            self.assertIsNone(C.COMMENTS_CONTAINER.match(token), token)
        for token in ("cookie-consent", "cookie-banner", "cc-banner", "onetrust-banner-sdk", "gdpr", "consent"):
            self.assertTrue(C.COOKIE_CONTAINER.match(token), token)
        for token in ("cookiecutter-docs", "ccc", "consented-reads"):
            self.assertIsNone(C.COOKIE_CONTAINER.match(token), token)


class InventoryAndOutcome(unittest.TestCase):
    HTML = ("<body><h1>T</h1><h2>S</h2><ul><li>a</li><li>b</li></ul><table><tr><td>1</td></tr></table>"
            "<pre>code</pre><a href='x'>l</a><img src='i'></body>")

    def test_inventory_counts(self):
        md = "# T\n\n## S\n\n- a\n- b\n\n| 1 |\n|---|\n| 2 |\n\n```\ncode\n```\n\n[l](x) ![i](i)\n"
        inv = C.inventory(self.HTML, md)
        self.assertEqual(inv, {"headings": {"html": 2, "md": 2}, "list_items": {"html": 2, "md": 2},
                               "tables": {"html": 1, "md": 1}, "code_blocks": {"html": 1, "md": 1},
                               "links": {"html": 1, "md": 1}, "images": {"html": 1, "md": 1}})

    def test_table_lost_is_partial_with_zero_missing_lines(self):
        conv = C.Conv([], [], [], 10, 4)
        inv = C.inventory(self.HTML, "# T\n\n## S\n\n- a\n- b\n\n```\ncode\n```\n")
        self.assertEqual(C.outcome(None, conv, inv), "partial")
        inv_ok = C.inventory(self.HTML, "| 1 |\n|---|\n| 2 |\n\n```\ncode\n```\n")
        self.assertEqual(C.outcome(None, conv, inv_ok), "complete")
        self.assertEqual(C.outcome(None, C.Conv(["x"], [], [], 10, 4), inv_ok), "partial")

    def test_outcome_order_blocked_unknown_partial(self):
        acq = C.Acq("paywall", "paywall", "subscribers only", 300, None)
        self.assertEqual(C.outcome(acq, C.Conv([], [], [], 3, 0), None), "blocked")
        self.assertEqual(C.outcome(C.Acq("ok", None, None, 300, None), C.Conv([], [], [], 3, 0), None), "unknown")
        self.assertEqual(C.outcome(C.Acq("ok", None, None, 300, None), C.Conv(["m"], [], [], 3, 2), None), "partial")
        self.assertEqual(C.outcome(C.Acq("thin", "thin", "visible words 12", 12, None), C.Conv([], [], [], 3, 2), None), "complete")

    def test_markdown_counts_ignore_fences(self):
        md = "```\n# not a heading\n- not an item\n| a |\n|---|\n```\n# real\n"
        counts = C.markdown_counts(md)
        self.assertEqual((counts["headings"], counts["list_items"], counts["tables"], counts["code_blocks"]), (1, 0, 0, 1))


if __name__ == "__main__":
    unittest.main()
