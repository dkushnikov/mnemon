"""Capture layer §18 (A17): the reversed rule `check.source_presence` — the text of a source.md body in the
text of its raw — and the projection of the body (`check.source_reference`). Pure functions over strings."""
from __future__ import annotations

import unittest

from _helpers import BIN  # noqa: F401  (puts bin/ on sys.path)

from capture import check as C

PLAIN = "See the docs for more details about the setup before you start."
LINKED = "See [the docs](https://example.test/docs \"Docs\") for more details about the setup before you start."


class MarkdownLink(unittest.TestCase):
    """The second reader's probe: the forward rule accepts a linked candidate for a plain reference; the naive
    reversal (the body as the reference, unnormalized) rejects the same pair; the reversed rule accepts it."""

    def test_forward_direction_accepts_the_link(self):
        self.assertTrue(C.conversion(LINKED, PLAIN, None).passed)

    def test_naive_reverse_fails_on_the_link(self):
        conv = C.conversion(PLAIN, LINKED, None)
        self.assertFalse(conv.passed)
        self.assertEqual(len(conv.missing), 1)

    def test_reversed_rule_finds_the_linked_line_in_the_plain_raw(self):
        p = C.source_presence(LINKED, PLAIN)
        self.assertEqual((p.not_found, p.eligible_count), ([], 1))

    def test_and_the_plain_line_in_a_markdown_raw(self):
        p = C.source_presence(PLAIN, "Intro.\n\n" + LINKED + "\n")
        self.assertEqual((p.not_found, p.eligible_count), ([], 1))

    def test_reference_links_autolinks_and_link_definitions(self):
        body = ("Read [the manual][m] and <https://example.test/a> before the upgrade starts tonight.\n\n"
                "[m]: https://example.test/manual\n")
        raw = "Read the manual and https://example.test/a before the upgrade starts tonight."
        p = C.source_presence(body, raw)
        self.assertEqual((p.not_found, p.eligible_count), ([], 1))


class Projection(unittest.TestCase):
    def test_headings_lists_quotes_and_images(self):
        body = ("# Top level heading\n\n### Third level heading\n\n#### A fourth level heading with six words ##\n\n"
                "1. First item of the ordered list goes here\n- [x] a done task with enough words in it\n"
                "> ## Quoted heading words\n\n![a picture of a cat sitting on the mat](cat.png)\n\n"
                "*[img: a figure the sanitizer replaced with its alt text]*\n")
        self.assertEqual(C.source_reference(body), [
            "## Top level heading", "## Third level heading", "A fourth level heading with six words",
            "First item of the ordered list goes here", "a done task with enough words in it", "## Quoted heading words"])

    def test_entities_are_decoded_after_the_markup_is_gone(self):
        body = "Use the &lt;div&gt; element &amp; a class on every card of the grid.\n"
        self.assertEqual(C.source_reference(body), ["Use the <div> element & a class on every card of the grid."])
        p = C.source_presence(body, "<p>ignored</p>\nUse the <div> element & a class on every card of the grid.")
        self.assertEqual(p.not_found, [])

    def test_an_entity_inside_a_code_span_stays_literal(self):
        """Second reader, P2: the decoding ran inside code spans too, so an unchanged capture mismatched."""
        line = "Write `&amp;` in the example to demonstrate the escaped entity."
        self.assertEqual(C.source_reference(line + "\n"), [line])
        self.assertEqual(C.source_presence(line + "\n", line).not_found, [])
        self.assertEqual(C.source_presence(line + "\n", "Intro.\n\n" + line + "\n\nOutro.").not_found, [])

    def test_entities_on_both_sides_decode_alike(self):
        line = "Use the &lt;div&gt; element &amp; a class on every card of the grid."
        self.assertEqual(C.source_presence(line + "\n", line).not_found, [])
        encoded, decoded = "Fish &amp; chips are served on every Friday at noon.", "Fish & chips are served on every Friday at noon."
        self.assertEqual(C.source_presence(decoded + "\n", encoded).not_found, [], "a decoded body against an encoded raw")
        self.assertEqual(C.source_presence(encoded + "\n", decoded).not_found, [], "an encoded body against a decoded raw")

    def test_an_inline_image_leaves_the_same_gap_on_both_sides(self):
        """Second reader, P2: the body lost its inline image while the raw kept the alt, so identical texts
        mismatched."""
        for line in ("The page shows ![a cat](cat.png) in the middle of the main paragraph.",
                     "The page shows *[img: a cat]* in the middle of the main paragraph.",
                     "The page shows *[inline figure omitted at capture: a cat]* in the middle of the main paragraph."):
            self.assertEqual(C.source_presence(line + "\n", line).not_found, [], line)
        with_image = "The page shows ![a cat](cat.png) in the middle of the main paragraph."
        self.assertEqual(C.source_presence("The page shows in the middle of the main paragraph.\n", with_image).not_found, [])
        self.assertEqual(C.source_presence(with_image + "\n", "The page shows in the middle of the main paragraph.").not_found, [])

    def test_intraword_emphasis_is_one_word(self):
        p = C.source_presence("The *emph*asis falls on the second syllable of the word here.\n",
                              "The emphasis falls on the second syllable of the word here.")
        self.assertEqual((p.not_found, p.eligible_count), ([], 1))

    def test_fenced_code_is_kept_as_it_is(self):
        body = "```python\nresult = compute(alpha, beta, gamma, delta)  # six words\n```\n"
        self.assertIn("result = compute(alpha, beta, gamma, delta)  # six words", C.source_reference(body))
        p = C.source_presence(body, "    result = compute(alpha, beta, gamma, delta)  # six words\n")
        self.assertEqual(p.not_found, [])

    def test_an_image_inside_a_code_span_stays(self):
        self.assertEqual(C.source_reference("Write `![alt](x.png)` to embed one image in the page body.\n"),
                         ["Write `![alt](x.png)` to embed one image in the page body."])


class Rules(unittest.TestCase):
    def test_paraphrase_shaped_body_is_reported_line_by_line(self):
        raw = "The committee met on Tuesday and approved the new budget for the coming year after a long debate."
        body = ("The committee met on Tuesday and approved the new budget for the coming year after a long debate.\n\n"
                "In short, the board agreed to spend more money next year on most programs.\n\n"
                "Overall this signals a shift toward expansion that observers had long expected.\n")
        p = C.source_presence(body, raw)
        self.assertEqual(p.eligible_count, 3)
        self.assertEqual(p.not_found, ["In short, the board agreed to spend more money next year on most programs.",
                                       "Overall this signals a shift toward expansion that observers had long expected."])

    def test_a_glued_raw_does_not_hold_the_spaced_line(self):
        p = C.source_presence("Deploy the company brain today with great care.\n",
                              "Deploythecompanybraintoday with great care.")
        self.assertEqual(len(p.not_found), 1)

    def test_cjk_lines_by_the_key_alone(self):
        cjk = "知识管理系统需要把原始页面的文字完整地保存下来以便以后核对"     # 29 ideographs, no spaces
        self.assertGreaterEqual(C.spaceless_chars(cjk), 20)
        self.assertEqual(C.source_presence(cjk + "\n", "前言\n" + cjk + "。\n").not_found, [])
        other = "这一段文字在原始页面里根本不存在所以必须被报告为没有找到的行"
        self.assertEqual(C.source_presence(other + "\n", cjk).not_found, [other])
        self.assertEqual(C.source_presence("短的中文句子不要求\n", "").eligible_count, 0)

    def test_heading_rule_two_words(self):
        p = C.source_presence("## Release notes\n\n## Intro\n", "Release notes")
        self.assertEqual((p.eligible_count, p.not_found), (1, []))
        p = C.source_presence("## Release notes\n", "Something else entirely")
        self.assertEqual(p.not_found, ["Release notes"])

    def test_chrome_lines_and_short_lines_are_not_required(self):
        body = "This site uses cookies to improve your experience.\nShort line here.\n"
        self.assertEqual(C.source_presence(body, "").eligible_count, 0)
        long_cookie = ("The essay explains in detail why this site uses cookies for analytics and what the "
                       "reader can do about it today.\n")
        self.assertEqual(C.source_presence(long_cookie, "").eligible_count, 1)

    def test_titles_are_not_required(self):
        body = "# The Page Title Here\n\nThe body text of the page has at least six words in it.\n"
        raw = "The body text of the page has at least six words in it."
        self.assertEqual(C.source_presence(body, raw).not_found, ["The Page Title Here"])
        p = C.source_presence(body, raw, titles=["The page title here"])
        self.assertEqual((p.eligible_count, p.not_found), (1, []))
        self.assertEqual(C.source_presence(body, raw, titles=[None, "", "Other"]).eligible_count, 2)

    def test_an_empty_raw_holds_nothing(self):
        p = C.source_presence(PLAIN + "\n", "")
        self.assertEqual((p.eligible_count, len(p.not_found)), (1, 1))

    def test_plain_raw_prose_that_looks_like_a_reference_link_is_kept(self):
        line = "The value out[r][min(cols)] is the smallest entry of the row r."
        self.assertEqual(C.source_presence("The value `out[r][min(cols)]` is the smallest entry of the row r.\n",
                                           line).not_found, [])

    def test_the_forward_rule_is_unchanged_by_the_shared_presence_test(self):
        conv = C.conversion("Deploythecompanybrain now and later today.", "Deploy the company brain now and later today.", None)
        self.assertEqual((conv.missing, len(conv.glued)), ([], 1))
        conv = C.conversion("nothing", "## Missing heading here\nA line with six words in it.", None)
        self.assertEqual((conv.headings_missing, conv.missing), (["Missing heading here"], ["A line with six words in it."]))


if __name__ == "__main__":
    unittest.main()
