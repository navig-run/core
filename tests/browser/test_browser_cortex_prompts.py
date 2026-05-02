"""
Unit tests for navig/browser/prompts.py.

Covers structural integrity of the two prompt strings (CORTEX_A11Y_PROMPT
and CORTEX_VISION_PROMPT) and the backward-compat alias CORTEX_SYSTEM_PROMPT.
"""

from __future__ import annotations

from navig.browser.prompts import (
    CORTEX_A11Y_PROMPT,
    CORTEX_SYSTEM_PROMPT,
    CORTEX_VISION_PROMPT,
)


class TestCortexPrompts:
    def test_a11y_prompt_is_string(self):
        assert isinstance(CORTEX_A11Y_PROMPT, str)

    def test_vision_prompt_is_string(self):
        assert isinstance(CORTEX_VISION_PROMPT, str)

    def test_system_prompt_alias_is_string(self):
        assert isinstance(CORTEX_SYSTEM_PROMPT, str)

    def test_system_prompt_is_vision_prompt(self):
        # Backward-compat alias must point to the same object
        assert CORTEX_SYSTEM_PROMPT is CORTEX_VISION_PROMPT

    def test_a11y_prompt_non_empty(self):
        assert len(CORTEX_A11Y_PROMPT) > 0

    def test_vision_prompt_non_empty(self):
        assert len(CORTEX_VISION_PROMPT) > 0

    def test_a11y_prompt_mentions_action(self):
        assert "action" in CORTEX_A11Y_PROMPT.lower()

    def test_vision_prompt_mentions_action(self):
        assert "action" in CORTEX_VISION_PROMPT.lower()

    def test_a11y_prompt_mentions_selector(self):
        assert "selector" in CORTEX_A11Y_PROMPT.lower()

    def test_vision_prompt_mentions_selector(self):
        assert "selector" in CORTEX_VISION_PROMPT.lower()

    def test_a11y_prompt_mentions_json(self):
        assert "json" in CORTEX_A11Y_PROMPT.lower()

    def test_vision_prompt_mentions_json(self):
        assert "json" in CORTEX_VISION_PROMPT.lower()

    def test_a11y_prompt_contains_click_action(self):
        assert "click" in CORTEX_A11Y_PROMPT

    def test_a11y_prompt_contains_done_action(self):
        assert "done" in CORTEX_A11Y_PROMPT

    def test_a11y_prompt_contains_fail_action(self):
        assert "fail" in CORTEX_A11Y_PROMPT

    def test_vision_prompt_contains_click_action(self):
        assert "click" in CORTEX_VISION_PROMPT

    def test_prompts_are_different(self):
        # A11Y and Vision prompts serve different use-cases — must differ
        assert CORTEX_A11Y_PROMPT != CORTEX_VISION_PROMPT

    def test_a11y_prompt_mentions_a11y_mode(self):
        assert "A11Y" in CORTEX_A11Y_PROMPT or "a11y" in CORTEX_A11Y_PROMPT.lower()
