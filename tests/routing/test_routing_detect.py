"""Tests for navig/routing/detect.py"""
from __future__ import annotations

import pytest

from navig.routing.detect import (
    _is_casual,
    _is_greeting,
    detect_mode,
)


# ---------------------------------------------------------------------------
# _is_greeting
# ---------------------------------------------------------------------------


class TestIsGreeting:
    def test_hello(self):
        assert _is_greeting("hello") is True

    def test_hi(self):
        assert _is_greeting("hi") is True

    def test_hey(self):
        assert _is_greeting("hey") is True

    def test_with_trailing_punctuation(self):
        assert _is_greeting("hello!") is True

    def test_with_leading_whitespace(self):
        assert _is_greeting("  hello  ") is True

    def test_case_insensitive(self):
        assert _is_greeting("Hello") is True
        assert _is_greeting("HELLO") is True

    def test_not_greeting(self):
        assert _is_greeting("what is python?") is False

    def test_empty_string(self):
        assert _is_greeting("") is False

    def test_good_morning(self):
        assert _is_greeting("good morning") is True


# ---------------------------------------------------------------------------
# _is_casual
# ---------------------------------------------------------------------------


class TestIsCasual:
    def test_ok(self):
        assert _is_casual("ok") is True

    def test_thanks(self):
        assert _is_casual("thanks") is True

    def test_cool(self):
        assert _is_casual("cool") is True

    def test_lol(self):
        assert _is_casual("lol") is True

    def test_with_trailing_punctuation(self):
        assert _is_casual("ok!") is True

    def test_case_insensitive(self):
        assert _is_casual("OK") is True

    def test_not_casual(self):
        assert _is_casual("write a function to sort a list") is False

    def test_empty_not_casual(self):
        assert _is_casual("") is False


# ---------------------------------------------------------------------------
# detect_mode — return structure
# ---------------------------------------------------------------------------


class TestDetectModeReturnStructure:
    def test_returns_three_tuple(self):
        result = detect_mode("hello")
        assert len(result) == 3

    def test_mode_is_string(self):
        mode, _, _ = detect_mode("hello")
        assert isinstance(mode, str)

    def test_confidence_is_float(self):
        _, conf, _ = detect_mode("hello")
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0

    def test_reasons_is_list(self):
        _, _, reasons = detect_mode("hello")
        assert isinstance(reasons, list)

    def test_reasons_non_empty(self):
        _, _, reasons = detect_mode("hello")
        assert len(reasons) > 0


# ---------------------------------------------------------------------------
# detect_mode — empty/small_talk
# ---------------------------------------------------------------------------


class TestDetectModeSmallTalk:
    def test_empty_is_small_talk(self):
        mode, conf, reasons = detect_mode("")
        assert mode == "small_talk"
        assert conf >= 0.9
        assert "empty_input" in reasons

    def test_greeting_is_small_talk(self):
        mode, conf, _ = detect_mode("hello")
        assert mode == "small_talk"
        assert conf >= 0.9

    def test_casual_is_small_talk(self):
        mode, conf, _ = detect_mode("thanks")
        assert mode == "small_talk"
        assert conf >= 0.9

    def test_short_question_is_small_talk(self):
        mode, _, reasons = detect_mode("what time is it?")
        assert mode == "small_talk"

    def test_hi_with_exclamation(self):
        mode, _, _ = detect_mode("Hi!")
        assert mode == "small_talk"


# ---------------------------------------------------------------------------
# detect_mode — coding
# ---------------------------------------------------------------------------


class TestDetectModeCoding:
    def test_code_block_is_coding(self):
        mode, _, reasons = detect_mode("```python\nprint('hi')\n```")
        assert mode == "coding"
        assert any("code_pattern" in r for r in reasons)

    def test_def_keyword_is_coding(self):
        mode, _, _ = detect_mode("def my_function(x): return x * 2")
        assert mode == "coding"

    def test_class_keyword_is_coding(self):
        mode, _, _ = detect_mode("class MyClass: pass")
        assert mode == "coding"

    def test_import_statement_is_coding(self):
        mode, _, _ = detect_mode("import os\nimport sys")
        assert mode == "coding"

    def test_fix_bug_is_coding(self):
        mode, _, _ = detect_mode("fix the bug in this function")
        assert mode == "coding"

    def test_write_function_is_coding(self):
        mode, _, _ = detect_mode("write a function to parse JSON")
        assert mode == "coding"

    def test_high_confidence_for_multiple_code_patterns(self):
        text = "```python\ndef foo():\n    class Bar: pass\n    import os\n```"
        mode, conf, _ = detect_mode(text)
        assert mode == "coding"
        assert conf >= 0.85


# ---------------------------------------------------------------------------
# detect_mode — research override for code + research
# ---------------------------------------------------------------------------


class TestDetectModeResearch:
    def test_research_keywords(self):
        mode, _, reasons = detect_mode("research the differences between async and sync I/O")
        assert mode == "research"

    def test_compare_keyword(self):
        mode, _, _ = detect_mode("compare React vs Vue for web development")
        assert mode == "research"

    def test_code_with_research_override(self):
        # Both code and research patterns — research wins
        mode, _, reasons = detect_mode(
            "compare the performance of def foo() vs class Foo in Python benchmarks"
        )
        assert mode == "research"
        assert "research_override" in reasons

    def test_analyze_keyword(self):
        mode, _, _ = detect_mode("analyze the architecture of this system")
        assert mode == "research"


# ---------------------------------------------------------------------------
# detect_mode — summarize
# ---------------------------------------------------------------------------


class TestDetectModeSummarize:
    def test_summarize_keyword(self):
        mode, _, _ = detect_mode("summarize this document for me")
        assert mode == "summarize"

    def test_tl_dr(self):
        mode, _, _ = detect_mode("tldr this article")
        assert mode == "summarize"

    def test_key_points(self):
        mode, _, _ = detect_mode("give me the key points of this report")
        assert mode == "summarize"


# ---------------------------------------------------------------------------
# detect_mode — big_tasks
# ---------------------------------------------------------------------------


class TestDetectModeBigTasks:
    def test_plan_keyword(self):
        mode, _, _ = detect_mode("plan the migration of our database to Postgres")
        assert mode == "big_tasks"

    def test_design_keyword(self):
        mode, _, _ = detect_mode("design an architecture for a microservices platform")
        assert mode == "big_tasks"

    def test_long_input_failup(self):
        text = " ".join(["word"] * 90)
        mode, _, reasons = detect_mode(text)
        assert mode == "big_tasks"

    def test_multiline_failup(self):
        text = "\n".join(["line"] * 7)
        mode, _, reasons = detect_mode(text)
        assert mode == "big_tasks"

    def test_default_failup(self):
        # medium sentence with no recognised patterns
        mode, _, reasons = detect_mode("I would like some information about cloud storage")
        assert mode in ("big_tasks", "small_talk")  # might be small_talk if short enough


# ---------------------------------------------------------------------------
# detect_mode — confidence range
# ---------------------------------------------------------------------------


class TestDetectModeConfidence:
    def test_confidence_in_range_for_all_modes(self):
        texts = [
            "hello",
            "write a function to sort a list",
            "summarize this paper",
            "research tensorflow vs pytorch",
            "build a comprehensive platform",
            "",
        ]
        for text in texts:
            _, conf, _ = detect_mode(text)
            assert 0.0 <= conf <= 1.0, f"confidence out of range for: {text!r}"
