"""§7.7 — sanitation on the fixtures: both levels, every repair counted, idempotent; lib_table; the bounded
HTML-level loops (G1)."""
from __future__ import annotations

import time
import unittest

from _helpers import read_fixture

from capture import lib_table, proc, sanitize


class Bounded(unittest.TestCase):
    """The HTML level finds every element it rewrites by bounded find loops: an element that is never
    closed costs nothing more than a closed one and leaves the rest of the document as it is."""

    def test_unclosed_elements_are_left_and_linear(self):
        for label, html in (("svg", ("<svg>" + "x" * 20) * 50000),
                            ("table", "<table><tr><td>" * 60000),
                            ("data anchor", ('<a href="data:text/plain,x">' + "y" * 10) * 40000),
                            ("listing line", ('<div class="ltx_listingline">' + "z" * 10) * 30000)):
            t0 = time.monotonic()
            out, counts = sanitize.html(html, "arxiv")
            self.assertLess(time.monotonic() - t0, 2.0, label)
            self.assertEqual(out, html, "%s: an unclosed element and what follows it stay" % label)
            self.assertEqual(sum(counts.values()), 0, label)

    def test_closed_elements_before_an_unclosed_one_are_still_rewritten(self):
        html = ("<p>a</p><svg><text>Lbl</text></svg><table><tr><td colspan='2'>c</td></tr></table>"
                "<a href=\"data:text/plain,x\">⬇</a><svg><text>never closed")
        out, counts = sanitize.html(html, "page")
        self.assertIn("<p>[diagram: Lbl]</p>", out)
        self.assertIn("<td>c</td><td>c</td>", out)
        self.assertNotIn("⬇", out)
        self.assertTrue(out.endswith("<svg><text>never closed"), out)
        self.assertEqual((counts["svg_diagrams"], counts["tables_rewritten"], counts["data_links"]), (1, 1, 1))


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

    def test_expansion_is_bounded(self):
        """Spans are clamped and the grid is bounded before any cell is placed (a million-span table is refused
        at once); a table whose padded grid would exceed the cap is refused too."""
        bomb = "<table><tr><td rowspan='1000000' colspan='1000000'>x</td></tr></table>"
        t0 = time.monotonic()
        with self.assertRaises(lib_table.TooLarge):
            lib_table.expand_table(bomb)
        self.assertLess(time.monotonic() - t0, 1.0)
        wide = "<table><tr><td colspan='150'>w</td></tr>" + "<tr><td>r</td></tr>" * 200 + "</table>"
        with self.assertRaises(lib_table.TooLarge):
            lib_table.expand_table(wide)
        fine = "<table><tr><td colspan='100'>w</td></tr>" + "<tr><td>r</td></tr>" * 100 + "</table>"
        out, changed = lib_table.expand_table(fine)
        self.assertTrue(changed)
        html = "<p>before</p>" + bomb + "<p>after</p>"
        out, counts = sanitize.html(html, "page")
        self.assertEqual(counts["tables_unrepaired"], 1)
        self.assertEqual(counts["tables_rewritten"], 0)
        self.assertIn(bomb, out, "the oversized table is left as the original HTML")

    def test_deadline_checked_between_tables(self):
        html = "<table><tr><td colspan='2'>a</td></tr></table>" * 3
        with self.assertRaises(sanitize.Timeout):
            sanitize.html(html, "page", deadline=proc.Deadline(0))
        out, counts = sanitize.html(html, "page", deadline=proc.Deadline(60))
        self.assertEqual(counts["tables_rewritten"], 3)


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

    def test_math_elements_become_their_tex_source(self):
        """G15: every <math> becomes the text `$<alttext>$` (display math `$$…$$`), the annotation when there is
        no alttext, the visible text when there is neither — before the reference and the converters see it."""
        html = ('<p>weights <math alttext="\\theta" display="inline"><mi>θ</mi><annotation encoding="application/x-tex">'
                '\\theta</annotation></math> here</p>'
                '<table><tr><td><math alttext="A_{1}=\\mathcal{M}" display="block"><mrow><mi>A</mi></mrow></math></td></tr></table>'
                '<p><math display="inline"><mi>τ</mi><annotation encoding="application/x-tex"> \\tau_{g} </annotation></math>'
                ' and <math><mi>x</mi><mo>&lt;</mo><mi>y</mi></math> and <math alttext="a &amp;&amp; b"><mi>a</mi></math></p>')
        out, counts = sanitize.html(html, "arxiv")
        self.assertEqual(counts["math_sources"], 5)
        self.assertIn("<p>weights $\\theta$ here</p>", out)
        self.assertIn("<td>$$A_{1}=\\mathcal{M}$$</td>", out, "display math is $$…$$")
        self.assertIn("<p>$\\tau_{g}$ and $x &lt; y$ and $a &amp;&amp; b$</p>", out,
                      "annotation, visible-text fallback (tags become spaces) and the source re-escaped as a text node")
        self.assertNotIn("<math", out)
        self.assertNotIn("θ", out)
        twice, counts2 = sanitize.html(out, "arxiv")
        self.assertEqual((twice, counts2["math_sources"]), (out, 0), "idempotent")
        out, counts = sanitize.html(read_fixture("math.html"), "arxiv")
        self.assertEqual(counts["math_sources"], 4)
        self.assertNotIn("<math", out)

    def test_data_links_keep_their_text(self):
        html = ('<p><a href="data:text/plain;base64,AAAA">⬇</a> <a href="data:text/plain,x">Read the <b>full</b> sentence here</a>'
                ' <a href="data:image/png;base64,BB"><img src="x.png"></a></p>')
        out, counts = sanitize.html(html, "page")
        self.assertEqual(counts["data_links"], 3)
        self.assertNotIn("data:", out)
        self.assertNotIn("⬇", out)
        self.assertIn("Read the <b>full</b> sentence here", out, "an anchor with real text is unwrapped, not dropped")
        self.assertNotIn("<img", out, "an anchor without word characters is removed with its content")

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
