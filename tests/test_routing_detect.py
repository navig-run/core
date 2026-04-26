"""Tests for routing/detect.py — detect_mode, _is_greeting, _is_casual."""
from __future__ import annotations

import pytest

from navig.routing.detect import _is_casual, _is_greeting, detect_mode


# ──────────────────────────────────────────────────────────────────────────────
# _is_greeting
# ──────────────────────────────────────────────────────────────────────────────


class TestIsGreeting:
    def test_hey(self):
        assert _is_greeting("hey") is True

    def test_hi(self):
        assert _is_greeting("hi") is True

    def test_hello(self):
        assert _is_greeting("hello") is True

    def test_case_insensitive(self):
        assert _is_greeting("Hello") is True

    def test_trailing_punctuation_stripped(self):
        assert _is_greeting("hey!") is True

    def test_non_greeting_word(self):
        assert _is_greeting("how are you doing today") is False

    def test_empty(self):
        assert _is_greeting("") is False

    def test_good_morning(self):
        assert _is_greeting("good morning") is True


# ──────────────────────────────────────────────────────────────────────────────
# _is_casual
# ──────────────────────────────────────────────────────────────────────────────


class TestIsCasual:
    def test_thanks(self):
        assert _is_casual("thanks") is True

    def test_ok(self):
        assert _is_casual("ok") is True

    def test_cool(self):
        assert _is_casual("cool") is True

    def test_lol(self):
        assert _is_casual("lol") is True

    def test_trailing_punctuation(self):
        assert _is_casual("okay!") is True

    def test_non_casual(self):
        assert _is_casual("please help me") is False

    def test_empty(self):
        assert _is_casual("") is False


# ──────────────────────────────────────────────────────────────────────────────
# detect_mode — core routing logic
# ──────────────────────────────────────────────────────────────────────────────


class TestDetectModeReturnType:
    def test_returns_three_tuple(self):
        result = detect_mode("hello")
        assert len(result) == 3

    def test_mode_is_string(self):
        mode, _, _ = detect_mode("hello")
        assert isinstance(mode, str)

    def test_confidence_is_float(self):
        _, confidence, _ = detect_mode("hello")
        assert isinstance(confidence, float)

    def test_confidence_range(self):
        _, confidence, _ = detect_mode("hello")
        assert 0.0 <= confidence <= 1.0

    def test_reasons_is_list(self):
        _, _, reasons = detect_mode("hello")
        assert isinstance(reasons, list)


class TestDetectModeSmallTalk:
    def test_greeting_small_talk(self):
        mode, conf, _ = detect_mode("hello")
        assert mode == "small_talk"
        assert conf >= 0.9

    def test_casual_small_talk(self):
        mode, _, _ = detect_mode("thanks")
        assert mode == "small_talk"

    def test_empty_is_small_talk(self):
        mode, _, reasons = detect_mode("")
        assert mode == "small_talk"
        assert "empty_input" in reasons

    def test_short_question(self):
        mode, _, _ = detect_mode("what time is it?")
        assert mode == "small_talk"


class TestDetectModeCoding:
    def test_backticks_detected(self):
        mode, _, _ = detect_mode("here is a ```python snippet```")
        assert mode == "coding"

    def test_def_keyword(self):
        mode, _, _ = detect_mode("def my_function(): pass")
        assert mode == "coding"

    def test_class_keyword(self):
        mode, _, _ = detect_mode("class MyClass: pass")
        assert mode == "coding"

    def test_fix_bug_phrase(self):
        mode, _, _ = detect_mode("please fix the bug in this code")
        assert mode == "coding"

    def test_write_function(self):
        mode, _, _ = detect_mode("write a function to sort a list")
        assert mode == "coding"

    def test_import_statement(self):
        mode, _, _ = detect_mode("from navig import something")
        assert mode == "coding"

    def test_confidence_at_least_70(self):
        _, conf, _ = detect_mode("def hello(): return 'hi'")
        assert conf >= 0.7


class TestDetectModeSummarize:
    def test_summarize_keyword(self):
        mode, _, _ = detect_mode("please summarize this long document")
        assert mode == "summarize"

    def test_tldr(self):
        mode, _, _ = detect_mode("tl;dr of this article please")
        assert mode == "summarize"

    def test_key_points(self):
        mode, _, _ = detect_mode("give me the key points")
        assert mode == "summarize"

    def test_confidence_high(self):
        _, conf, _ = detect_mode("summarize this")
        assert conf >= 0.8


class TestDetectModeResearch:
    def test_compare_keyword(self):
        mode, _, _ = detect_mode("compare Python vs JavaScript for web development")
        assert mode == "research"

    def test_analyze_keyword(self):
        mode, _, _ = detect_mode("analyze the pros and cons of microservices")
        assert mode == "research"

    def test_pros_and_cons(self):
        mode, _, _ = detect_mode("what are the pros and cons of Docker")
        assert mode == "research"


class TestDetectModeBigTask:
    def test_plan_keyword(self):
        mode, _, _ = detect_mode("plan a full migration of our database to postgres")
        assert mode == "big_tasks"

    def test_design_keyword(self):
        mode, _, _ = detect_mode("design a comprehensive architecture for the system")
        assert mode == "big_tasks"

    def test_roadmap_keyword(self):
        mode, _, _ = detect_mode("create a roadmap for the product launch")
        assert mode == "big_tasks"
