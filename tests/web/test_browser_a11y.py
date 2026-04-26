"""Tests for navig.browser.a11y — annotate_a11y_snapshot."""
from __future__ import annotations

from navig.browser.a11y import annotate_a11y_snapshot


class TestAnnotateA11ySnapshot:
    def test_empty_string_returns_empty(self):
        text, ref_map = annotate_a11y_snapshot("")
        assert text == ""
        assert ref_map == {}

    def test_none_equivalent_empty(self):
        # Only str is expected, but guard handles falsy
        text, ref_map = annotate_a11y_snapshot("")
        assert text == ""

    def test_single_element_annotated(self):
        raw = '- button "Submit"'
        text, ref_map = annotate_a11y_snapshot(raw)
        assert "[0]" in text
        assert ref_map[0]["role"] == "button"
        assert ref_map[0]["name"] == "Submit"

    def test_ref_ids_sequential(self):
        raw = '- button "A"\n- link "B"\n- input "C"'
        text, ref_map = annotate_a11y_snapshot(raw)
        assert 0 in ref_map
        assert 1 in ref_map
        assert 2 in ref_map

    def test_slash_lines_not_annotated(self):
        raw = "- /document"
        text, ref_map = annotate_a11y_snapshot(raw)
        assert ref_map == {}
        assert "- /document" in text

    def test_raw_line_stored_in_ref_map(self):
        raw = '  - button "OK"'
        _, ref_map = annotate_a11y_snapshot(raw)
        assert ref_map[0]["raw_line"] == raw

    def test_non_list_lines_passthrough(self):
        raw = "Some heading line\n- button \"Go\""
        text, ref_map = annotate_a11y_snapshot(raw)
        assert "Some heading line" in text
        assert len(ref_map) == 1

    def test_bracket_name_form(self):
        raw = "- textbox [Enter name]"
        text, ref_map = annotate_a11y_snapshot(raw)
        assert ref_map[0]["name"] == "Enter name"

    def test_indentation_preserved(self):
        raw = '    - button "Nested"'
        text, ref_map = annotate_a11y_snapshot(raw)
        assert text.startswith("    ")

    def test_multiple_mixed_lines(self):
        raw = "\n".join([
            "- /document",
            '- button "Save"',
            "Header text",
            '  - link "Home"',
        ])
        text, ref_map = annotate_a11y_snapshot(raw)
        assert len(ref_map) == 2
        lines = text.splitlines()
        assert "- /document" in lines[0]
