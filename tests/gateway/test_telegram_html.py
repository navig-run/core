"""Unit tests for navig.gateway.channels.telegram_html.

Pure string-formatting tests — no I/O, no network, no mocks required.
"""

from __future__ import annotations

import unittest

from navig.gateway.channels.telegram_html import (
    blockquote,
    bold,
    code,
    html_escape,
    italic,
    link,
    md_to_html,
    mention,
    pre,
    spoiler,
    strike,
    underline,
    v1_to_html,
)

# ===========================================================================
# TestHtmlEscape
# ===========================================================================


class TestHtmlEscape(unittest.TestCase):
    """html_escape — escapes & < > but NOT "."""

    def test_ampersand_escaped(self):
        self.assertEqual(html_escape("a & b"), "a &amp; b")

    def test_less_than_escaped(self):
        self.assertEqual(html_escape("a < b"), "a &lt; b")

    def test_greater_than_escaped(self):
        self.assertEqual(html_escape("a > b"), "a &gt; b")

    def test_double_quote_not_escaped(self):
        result = html_escape('say "hello"')
        self.assertIn('"', result)

    def test_safe_text_unchanged(self):
        self.assertEqual(html_escape("hello world"), "hello world")

    def test_coerces_non_string(self):
        # html_escape calls str() on input
        self.assertEqual(html_escape(42), "42")

    def test_multiple_entities(self):
        self.assertEqual(html_escape("1 < 2 & 3 > 0"), "1 &lt; 2 &amp; 3 &gt; 0")


# ===========================================================================
# TestTagHelpers
# ===========================================================================


class TestTagHelpers(unittest.TestCase):
    """bold, italic, underline, strike, spoiler — trivial wrapping."""

    def test_bold(self):
        self.assertEqual(bold("hi"), "<b>hi</b>")

    def test_italic(self):
        self.assertEqual(italic("hi"), "<i>hi</i>")

    def test_underline(self):
        self.assertEqual(underline("hi"), "<u>hi</u>")

    def test_strike(self):
        self.assertEqual(strike("hi"), "<s>hi</s>")

    def test_spoiler(self):
        self.assertEqual(spoiler("hi"), "<tg-spoiler>hi</tg-spoiler>")

    def test_nested(self):
        self.assertEqual(bold(italic("hi")), "<b><i>hi</i></b>")


# ===========================================================================
# TestCode
# ===========================================================================


class TestCode(unittest.TestCase):
    """code() — escapes content."""

    def test_basic(self):
        self.assertEqual(code("print()"), "<code>print()</code>")

    def test_escapes_lt_gt(self):
        result = code("a < b > c")
        self.assertIn("&lt;", result)
        self.assertIn("&gt;", result)
        self.assertTrue(result.startswith("<code>"))
        self.assertTrue(result.endswith("</code>"))

    def test_escapes_ampersand(self):
        result = code("a & b")
        self.assertIn("&amp;", result)


# ===========================================================================
# TestPre
# ===========================================================================


class TestPre(unittest.TestCase):
    """pre() — plain and with language class."""

    def test_no_lang(self):
        result = pre("x = 1")
        self.assertEqual(result, "<pre>x = 1</pre>")

    def test_with_lang(self):
        result = pre("x = 1", lang="python")
        self.assertIn("language-python", result)
        self.assertIn("<pre>", result)
        self.assertIn("<code", result)

    def test_content_escaped(self):
        result = pre("<script>")
        self.assertIn("&lt;script&gt;", result)

    def test_lang_escaped(self):
        result = pre("x", lang="py<script>")
        self.assertNotIn("<script>", result)


# ===========================================================================
# TestBlockquote
# ===========================================================================


class TestBlockquote(unittest.TestCase):
    """blockquote() — plain and expandable."""

    def test_plain(self):
        self.assertEqual(blockquote("hello"), "<blockquote>hello</blockquote>")

    def test_expandable(self):
        result = blockquote("hello", expandable=True)
        self.assertIn("expandable", result)
        self.assertIn("<blockquote", result)
        self.assertIn("hello", result)

    def test_not_expandable_by_default(self):
        result = blockquote("hello")
        self.assertNotIn("expandable", result)


# ===========================================================================
# TestLink
# ===========================================================================


class TestLink(unittest.TestCase):
    """link() — href attribute set correctly."""

    def test_basic(self):
        result = link("Click here", "https://example.com")
        self.assertEqual(result, '<a href="https://example.com">Click here</a>')

    def test_display_text_included(self):
        self.assertIn("Go now", link("Go now", "https://x.com"))


# ===========================================================================
# TestMention
# ===========================================================================


class TestMention(unittest.TestCase):
    """mention() — tg://user?id link with escaped name."""

    def test_basic(self):
        result = mention("Alice", 12345)
        self.assertIn("tg://user?id=12345", result)
        self.assertIn("Alice", result)

    def test_name_escaped(self):
        result = mention("A <B>", 1)
        self.assertIn("&lt;B&gt;", result)


# ===========================================================================
# TestMdToHtml — core conversions
# ===========================================================================


class TestMdToHtml(unittest.TestCase):
    """md_to_html() — Markdown to Telegram HTML."""

    # --- Empty / trivial ---

    def test_empty_string(self):
        self.assertEqual(md_to_html(""), "")

    def test_plain_text_passthrough(self):
        result = md_to_html("Hello world")
        self.assertEqual(result, "Hello world")

    # --- HTML escaping ---

    def test_html_chars_escaped(self):
        result = md_to_html("a & b < c > d")
        self.assertIn("&amp;", result)
        self.assertIn("&lt;", result)
        self.assertIn("&gt;", result)

    # --- Bold ---

    def test_double_star_bold(self):
        result = md_to_html("**hello**")
        self.assertIn("<b>hello</b>", result)

    def test_double_underscore_bold(self):
        result = md_to_html("__hello__")
        self.assertIn("<b>hello</b>", result)

    # --- Italic ---

    def test_single_star_italic(self):
        result = md_to_html("*hello*")
        self.assertIn("<i>hello</i>", result)

    def test_single_underscore_italic(self):
        result = md_to_html("_hello_")
        self.assertIn("<i>hello</i>", result)

    # --- Strikethrough ---

    def test_tilde_strike(self):
        result = md_to_html("~~hello~~")
        self.assertIn("<s>hello</s>", result)

    # --- Inline code ---

    def test_inline_code(self):
        result = md_to_html("`print()`")
        self.assertIn("<code>print()</code>", result)

    # --- Fenced code blocks ---

    def test_fenced_code_no_lang(self):
        result = md_to_html("```\nx = 1\n```")
        self.assertIn("<pre>", result)
        self.assertIn("x = 1", result)

    def test_fenced_code_with_lang(self):
        result = md_to_html("```python\nx = 1\n```")
        self.assertIn("language-python", result)
        self.assertIn("x = 1", result)

    def test_fenced_code_content_escaped(self):
        result = md_to_html("```\na < b\n```")
        self.assertIn("&lt;", result)

    # --- Headings ---

    def test_h1(self):
        result = md_to_html("# Title")
        self.assertIn("<b>Title</b>", result)

    def test_h2(self):
        result = md_to_html("## Subtitle")
        self.assertIn("<b>Subtitle</b>", result)

    def test_h3(self):
        result = md_to_html("### Sub-subtitle")
        self.assertIn("<b>Sub-subtitle</b>", result)

    # --- Blockquote ---

    def test_blockquote_line(self):
        result = md_to_html("> some quote")
        self.assertIn("<blockquote>", result)
        self.assertIn("some quote", result)

    # --- Bullets ---

    def test_star_bullet(self):
        result = md_to_html("* item")
        self.assertIn("\u2022", result)
        self.assertIn("item", result)

    def test_dash_bullet(self):
        result = md_to_html("- item")
        self.assertIn("\u2022", result)

    def test_plus_sub_bullet(self):
        result = md_to_html("+ sub")
        self.assertIn("\u25e6", result)

    # --- Links ---

    def test_markdown_link(self):
        result = md_to_html("[Click](https://example.com)")
        self.assertIn('<a href="https://example.com">Click</a>', result)

    # --- Multiple blank lines collapsed ---

    def test_excess_blank_lines_collapsed(self):
        result = md_to_html("a\n\n\n\nb")
        self.assertNotIn("\n\n\n", result)


# ===========================================================================
# TestV1ToHtml
# ===========================================================================


class TestV1ToHtml(unittest.TestCase):
    """v1_to_html() — Telegram V1 Markdown to HTML."""

    def test_bold_single_star(self):
        result = v1_to_html("*bold*")
        self.assertIn("<b>bold</b>", result)

    def test_italic_underscore(self):
        result = v1_to_html("_italic_")
        self.assertIn("<i>italic</i>", result)

    def test_inline_code(self):
        result = v1_to_html("`code`")
        self.assertIn("<code>code</code>", result)

    def test_link(self):
        result = v1_to_html("[text](https://x.com)")
        self.assertIn('<a href="https://x.com">text</a>', result)

    def test_plain_text_unchanged(self):
        self.assertEqual(v1_to_html("hello"), "hello")


if __name__ == "__main__":
    unittest.main()
