"""§7.7 — sanitation on the fixtures: both levels, every repair counted, idempotent; lib_table."""
from __future__ import annotations

import unittest

from _helpers import read_fixture

from capture import lib_table, sanitize


class Tables(unittest.TestCase):
    def test_merged_cells_expanded_and_headers_stacked(self):
        html = ("<table><thead><tr><th rowspan='2'>Model</th><th colspan='2'>Task A</th></tr>"
                "<tr><th>Acc</th><th>F1</th></tr></thead><tbody><tr><td>m1</td><td>1</td><td>2</td></tr>"
                "<tr><td rowspan='2'>m2</td><td>3</td><td>4</td></tr><tr><td>5</td><td>6</td></tr></tbody></table>")
        out, changed = lib_table.expand_table(html)
        self.assertTrue(changed)
        self.assertNotIn("rowspan", out)
        self.assertNotIn("colspan", out)
        self.assertIn("<th>Model</th><th>Task A · Acc</th><th>Task A · F1</th>", out)
        self.assertIn("<tr><td>m2</td><td>3</td><td>4</td></tr>", out)
        self.assertIn("<tr><td>m2</td><td>5</td><td>6</td></tr>", out, "the spanned cell is duplicated into its rows")

    def test_plain_table_untouched_and_paragraphs_joined(self):
        html = "<table><tr><td>a</td><td>b</td></tr></table>"
        self.assertEqual(lib_table.expand_table(html), (html, False))
        html = "<table><caption>Cap</caption><tr><td colspan='2'><p>one</p><p>two</p></td></tr><tr><td>x</td><td>y</td></tr></table>"
        out, changed = lib_table.expand_table(html)
        self.assertTrue(changed)
        self.assertIn("<caption>Cap</caption>", out)
        self.assertIn("<td>one two</td><td>one two</td>", out)

    def test_nested_table_kept_inside_its_cell(self):
        html = "<table><tr><td rowspan='2'>outer<table><tr><td>inner</td></tr></table></td><td>b</td></tr><tr><td>c</td></tr></table>"
        out, changed = lib_table.expand_table(html)
        self.assertTrue(changed)
        self.assertEqual(out.count("<table>"), 3)


class HtmlLevel(unittest.TestCase):
    def test_latexml_fixture(self):
        out, counts = sanitize.html(read_fixture("latexml.html"), "arxiv")
        self.assertEqual(counts["latexml_listings"], 1)
        self.assertEqual(counts["data_links"], 1)
        self.assertEqual(counts["tables_rewritten"], 1)
        self.assertIn("<pre><code>def example(x):\n    return x + 1\nprint(example(41))</code></pre>", out)
        self.assertNotIn("ltx_listingline", out)
        self.assertNotIn('href="data:', out)
        self.assertNotIn("rowspan", out)
        self.assertIn("<th>Task A · Acc</th>", out)
        self.assertIn('<img src="data:image/png;base64,', out, "base64 figures are a markdown-level repair")

    def test_svg_fixture(self):
        out, counts = sanitize.html(read_fixture("svg.html"), "page")
        self.assertEqual((counts["svg_diagrams"], counts["svg_icons"]), (1, 1))
        self.assertIn("<p>[diagram: Client · Server · Database]</p>", out)
        self.assertNotIn("<svg", out)

    def test_two_merged_tables_rewritten_in_place(self):
        out, counts = sanitize.html(read_fixture("table-wrong.html"), "page")
        self.assertEqual(counts["tables_rewritten"], 2)
        self.assertLess(out.index("Fruit · Name"), out.index("Population · 2020"))
        self.assertIn("<th>Fruit · Name</th><th>Fruit · Colour</th><th>Price</th>", out)
        self.assertIn("<th>City</th><th>Population · 2020</th><th>Population · 2030</th>", out)

    def test_idempotent(self):
        for name, kind in (("latexml.html", "arxiv"), ("svg.html", "page"), ("table-wrong.html", "page"),
                           ("nextjs.html", "page"), ("comments.html", "page")):
            once, counts = sanitize.html(read_fixture(name), kind)
            twice, counts2 = sanitize.html(once, kind)
            self.assertEqual(once, twice, name)
            self.assertEqual(sum(counts2.values()), 0, name)


class MarkdownLevel(unittest.TestCase):
    def test_data_uris_and_links(self):
        md = "![Figure 1](data:image/png;base64,AAAA)\n\n[⬇](data:text/plain;base64,BBBB)\n\n[source](data:text/plain,hello)\n"
        out, counts = sanitize.markdown(md, "page")
        self.assertEqual(out, "*[inline figure omitted at capture: Figure 1]*\n\nsource\n")
        self.assertEqual((counts["data_uris"], counts["data_links"]), (1, 2))

    def test_next_images_outside_fences_only(self):
        md = "![Hero](/_next/image?url=%2Fhero.png&w=3840&q=75)\n\n```\n![x](/_next/image?url=y)\n```\n\n![Other](https://h/_next/image?url=z)\n"
        out, counts = sanitize.markdown(md, "page")
        self.assertIn("*[img: Hero]*", out)
        self.assertIn("*[img: Other]*", out)
        self.assertIn("![x](/_next/image?url=y)", out)
        self.assertEqual(counts["next_images"], 2)

    def test_iow_residue_becomes_a_text_fence(self):
        md = "Intro paragraph.\n\n\\iow_now:Ne line one \\iow_now:Ne line two \\iow_now:Ne line three\n\nAfter.\n"
        out, counts = sanitize.markdown(md, "arxiv")
        self.assertIn("~~~~text\nline one\nline two\nline three\n~~~~", out)
        self.assertNotIn("\\iow_now", out)
        self.assertEqual(counts["tcolorbox_residue"], 1)

    def test_residue_before_title_arxiv_only(self):
        md = "\\newcommand{\\x}{y} \\providecommand{\\z}{w}\n\n# Title\n\n\\newcommand later is kept\n"
        out, counts = sanitize.markdown(md, "arxiv")
        self.assertTrue(out.startswith("# Title"))
        self.assertIn("\\newcommand later is kept", out)
        self.assertEqual(counts["residue_lines"], 1)
        out2, counts2 = sanitize.markdown(md, "page")
        self.assertEqual(counts2["residue_lines"], 0)
        self.assertTrue(out2.startswith("\\newcommand"))

    def test_table_placeholder_counted_outside_fences_untouched_inside(self):
        md = "[TABLE]\n\n```\n[TABLE]\nrows\n```\n"
        out, counts = sanitize.markdown(md, "page")
        self.assertEqual(counts["tables_unrepaired"], 1)
        self.assertEqual(out, md)

    def test_comment_sections_cut_and_blank_lines_collapsed(self):
        md = "# Post\n\nBody text.\n\n\n\n## 12 Comments\n\nReader one.\n\n```\ncode in a comment\n```\n\nReader two.\n"
        out, counts = sanitize.markdown(md, "page")
        self.assertEqual(out, "# Post\n\nBody text.\n")
        self.assertEqual(counts["comment_sections"], 1)
        md = "# Post\n\n## Comments (4)\n\nReader.\n\n## Next section\n\nKept text.\n"
        out, _ = sanitize.markdown(md, "page")
        self.assertEqual(out, "# Post\n\n## Next section\n\nKept text.\n")
        for heading in ("## Leave a comment", "## Reader comments", "## Post a comment", "### Leave a Reply"):
            out, counts = sanitize.markdown("# Post\n\n%s\n\nReader.\n" % heading, "page")
            self.assertEqual((out, counts["comment_sections"]), ("# Post\n", 1), heading)

    def test_bare_discussion_and_comments_headings_are_body_text(self):
        """A heading named Discussion, Responses, Replies or just Comments is never a cut (a widget is
        identified by its container, or by a counted / call-to-action heading)."""
        for heading in ("## Discussion", "## Responses", "## Replies", "## Comments", "## Comment"):
            md = "# Paper\n\n%s\n\nThe discussion section interprets the results in full.\n\n## Conclusion\n\nEnd.\n" % heading
            out, counts = sanitize.markdown(md, "page")
            self.assertEqual(out, md, heading)
            self.assertEqual(counts["comment_sections"], 0, heading)

    def test_fence_segments_track_tilde_and_backtick_fences(self):
        md = "a\n~~~~text\n```\ninner\n~~~~\nb\n```\nc\n```\n"
        segs = sanitize.fence_segments(md)
        self.assertEqual([(c, len(l)) for c, l in segs], [(False, 1), (True, 4), (False, 1), (True, 3), (False, 1)])

    def test_idempotent(self):
        samples = [
            "![F](data:image/png;base64,AA)\n\n\\iow_now:Ne a b \\iow_now:Ne c d\n\n\n\n## Comments\n\nx\n",
            read_fixture("dropped-section.defuddle.md"),
            "![Hero](/_next/image?url=a&w=1)\n\n```\n![x](/_next/image?url=y)\n```\n",
        ]
        for md in samples:
            once, _ = sanitize.markdown(md, "arxiv")
            twice, counts = sanitize.markdown(once, "arxiv")
            self.assertEqual(once, twice)
            self.assertEqual(sum(v for k, v in counts.items() if k != "tables_unrepaired"), 0)

    def test_reference_lines_mirror_the_repairs(self):
        lines = ["\\newcommand{\\x}{y}", "## Title", "\\iow_now:Ne one \\iow_now:Ne two", "## 3 Comments", "reader"]
        self.assertEqual(sanitize.reference_lines(lines, "arxiv"), ["## Title", "one", "two"])
        self.assertEqual(sanitize.reference_lines(lines, "page"), ["\\newcommand{\\x}{y}", "## Title", "one", "two"])


if __name__ == "__main__":
    unittest.main()
