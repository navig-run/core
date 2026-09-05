"""Regression tests for the Telegram answer-depth classifier and the self-talk
false-positive that made real questions shallow.

Background: a question like "do you know Fight Club?" was classified as casual
self-talk (because "you" + the generic auxiliary "do" matched) and routed to the
fast TALK path with no web grounding — the "shallow answer" complaint. And every
REASON question was hard-pinned to the small tier. classify_depth() now decides
quick vs deep, and the self-talk detector vetoes anything with an entity signal.
"""

import pytest

from navig.gateway.channels.telegram_mode_classifier import (
    _is_casual_self_talk,
    classify_depth,
    classify_mode,
)


class TestClassifyDepth:
    @pytest.mark.parametrize(
        "text",
        [
            "do you know Fight Club?",  # the reported bug
            "info about Fight Club",
            "tell me about Inception",
            "explain how tcp works",
            "what is the capital of France?",
            "compare rust and go",
        ],
    )
    def test_real_questions_are_deep(self, text):
        depth, conf = classify_depth(text)
        assert depth == "deep", f"{text!r} should be deep, got {depth} ({conf})"

    @pytest.mark.parametrize(
        "text",
        [
            "hey",
            "thanks",
            "ok",
            "what are you doing",  # self-talk, not an info question
            "who are you",
            "I am lying in bed trying to sleep",  # narrative, no question/entity
            "lol nice",
        ],
    )
    def test_chit_chat_stays_quick(self, text):
        depth, _conf = classify_depth(text)
        assert depth == "quick", f"{text!r} should be quick, got {depth}"

    def test_empty_is_quick(self):
        assert classify_depth("")[0] == "quick"
        assert classify_depth("   ")[0] == "quick"


class TestSelfTalkEntityVeto:
    def test_entity_question_is_not_self_talk(self):
        # "do you know Fight Club?" — directed at bot but ABOUT an entity.
        assert _is_casual_self_talk("do you know Fight Club?") is False

    def test_genuine_self_talk_still_detected(self):
        assert _is_casual_self_talk("what are you doing") is True
        assert _is_casual_self_talk("who are you") is True

    def test_bare_do_no_longer_forces_self_talk(self):
        # "do you like pizza" has no entity and is genuine self-talk → still True,
        # but the point of the fix is that an entity beside "do you" wins:
        assert _is_casual_self_talk("do you know Breaking Bad?") is False


class TestModeRoutingForReportedBug:
    def test_fight_club_question_reaches_reason_not_talk(self):
        # The whole point: it must not be swallowed into TALK.
        assert classify_mode("do you know Fight Club?") == "REASON"
