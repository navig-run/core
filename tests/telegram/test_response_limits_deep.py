"""Regression tests for Telegram response length handling.

Two fixes:
- The old MAX_SINGLE_MSG=2000 / SPLIT_THRESHOLD=500 shattered a normal in-depth
  answer into ~5 fragments, splitting mid-format. A ~2.5k-char answer must now
  land as ONE message.
- verbosity="brief" truncated ALL replies to 280 chars. A deep, tool-grounded
  answer (is_deep=True) must be exempt, or the depth path is undone at the last
  step.
"""

from navig.gateway.channels.telegram_templates import (
    DEFAULT_LIMIT,
    MAX_SINGLE_MSG,
    enforce_response_limits,
)


def test_split_threshold_keeps_a_normal_answer_single():
    # A realistic in-depth answer (~2.8k chars) — over the OLD 2000 cap that
    # split it into ~5 parts, but under the new 3800 single-message cap.
    para = "Fight Club (1999), directed by David Fincher, stars Brad Pitt and Edward Norton. "
    text = "\n\n".join([para for _ in range(35)])
    assert 2000 < len(text) < MAX_SINGLE_MSG, f"fixture is {len(text)} chars"
    fmt = enforce_response_limits(text, verbosity="normal")
    assert fmt.parts is None, "a sub-3800 answer must not be split"
    assert fmt.text == text


def test_deep_answer_is_not_truncated_under_brief():
    long_answer = "A" * 1200
    # Non-deep + brief → truncated (unchanged behaviour).
    shallow = enforce_response_limits(long_answer, verbosity="brief")
    assert len(shallow.text) <= DEFAULT_LIMIT + 1

    # Deep + brief → NOT truncated.
    deep = enforce_response_limits(long_answer, verbosity="brief", is_deep=True)
    assert deep.text == long_answer


def test_oversized_deep_answer_still_splits():
    huge = "word " * 1500  # ~7500 chars, over the 3800 single-message cap
    fmt = enforce_response_limits(huge, verbosity="normal", is_deep=True)
    assert fmt.parts is not None and len(fmt.parts) > 1
