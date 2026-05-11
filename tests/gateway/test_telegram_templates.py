"""Unit tests for navig.gateway.channels.telegram_templates.

Pure logic tests — character limits, template formatters, auto-detect.
No I/O, no network, no mocks required.
"""

from __future__ import annotations

import unittest

from navig.gateway.channels.telegram_templates import (
    ACK_LIMIT,
    DEFAULT_LIMIT,
    GREETING_LIMIT,
    TASK_DONE_LIMIT,
    FormattedMessage,
    TemplateID,
    _enforce_limit,
    _smart_split,
    auto_detect_template,
    enforce_response_limits,
    t1_greeting,
    t2_status,
    t3_incident,
    t4_task_done,
    t5_briefing,
    t6_clarification,
    t7_ack,
    t8_approval,
    t10_error,
)

# ===========================================================================
# TestEnforceLimit
# ===========================================================================


class TestEnforceLimit(unittest.TestCase):
    """_enforce_limit — truncates with ellipsis."""

    def test_short_text_unchanged(self):
        self.assertEqual(_enforce_limit("hi", 100), "hi")

    def test_exactly_at_limit_unchanged(self):
        text = "a" * 100
        self.assertEqual(_enforce_limit(text, 100), text)

    def test_too_long_truncated(self):
        text = "a" * 200
        result = _enforce_limit(text, 100)
        self.assertEqual(len(result), 100)
        self.assertTrue(result.endswith("…"))

    def test_truncated_length_matches_limit(self):
        result = _enforce_limit("x" * 50, 30)
        self.assertEqual(len(result), 30)


# ===========================================================================
# TestSmartSplit
# ===========================================================================


class TestSmartSplit(unittest.TestCase):
    """_smart_split — splits at boundaries."""

    def test_short_text_single_part(self):
        result = _smart_split("hello world", 500)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "hello world")

    def test_splits_long_text(self):
        text = ("word " * 200).strip()
        result = _smart_split(text, 100)
        self.assertGreater(len(result), 1)
        # All parts non-empty
        for part in result:
            self.assertTrue(len(part) > 0)

    def test_splits_at_paragraph(self):
        text = "part one\n\n" + "word " * 200
        result = _smart_split(text, 100)
        self.assertGreater(len(result), 1)

    def test_full_text_preserved(self):
        text = "sentence one. sentence two. " * 20
        result = _smart_split(text, 50)
        joined = " ".join(result)
        # All words from original are present
        self.assertIn("sentence one", joined)


# ===========================================================================
# TestFormattedMessage
# ===========================================================================


class TestFormattedMessage(unittest.TestCase):
    """FormattedMessage dataclass defaults and attributes."""

    def test_minimal(self):
        m = FormattedMessage(text="hello")
        self.assertEqual(m.text, "hello")
        self.assertIsNone(m.template_id)
        self.assertIsNone(m.keyboard_profile)
        self.assertIsNone(m.parts)

    def test_full(self):
        m = FormattedMessage(
            text="hi",
            template_id=TemplateID.GREETING,
            keyboard_profile="none",
            parts=["hi", "there"],
        )
        self.assertEqual(m.template_id, TemplateID.GREETING)
        self.assertEqual(m.keyboard_profile, "none")
        self.assertEqual(m.parts, ["hi", "there"])


# ===========================================================================
# TestTemplateID
# ===========================================================================


class TestTemplateID(unittest.TestCase):
    """TemplateID enum — values match spec."""

    def test_greeting_is_t1(self):
        self.assertEqual(TemplateID.GREETING, "T1")

    def test_error_is_t10(self):
        self.assertEqual(TemplateID.ERROR, "T10")

    def test_all_template_ids_exist(self):
        for name in (
            "GREETING",
            "STATUS",
            "INCIDENT",
            "TASK_DONE",
            "BRIEFING",
            "CLARIFICATION",
            "ACK",
            "APPROVAL",
            "THINKING",
            "ERROR",
        ):
            self.assertIsNotNone(getattr(TemplateID, name))


# ===========================================================================
# TestT1Greeting
# ===========================================================================


class TestT1Greeting(unittest.TestCase):
    def test_returns_formatted_message(self):
        result = t1_greeting()
        self.assertIsInstance(result, FormattedMessage)

    def test_template_id_is_greeting(self):
        self.assertEqual(t1_greeting().template_id, TemplateID.GREETING)

    def test_keyboard_is_none(self):
        self.assertEqual(t1_greeting().keyboard_profile, "none")

    def test_text_within_greeting_limit(self):
        text = t1_greeting().text
        self.assertLessEqual(len(text), GREETING_LIMIT)

    def test_detailed_with_priority_longer(self):
        result = t1_greeting(
            top_priority="urgent task",
            overnight_summary="3 alerts resolved",
            verbosity="detailed",
        )
        self.assertIsInstance(result.text, str)
        self.assertTrue(len(result.text) > 0)


# ===========================================================================
# TestT2Status
# ===========================================================================


class TestT2Status(unittest.TestCase):
    def test_basic(self):
        result = t2_status("all good", tasks_done=3, tasks_pending=0)
        self.assertIsInstance(result, FormattedMessage)
        self.assertEqual(result.template_id, TemplateID.STATUS)

    def test_checkmark_when_none_pending(self):
        result = t2_status("ok", tasks_done=1, tasks_pending=0)
        self.assertIn("✅", result.text)

    def test_warning_when_pending(self):
        result = t2_status("ok", tasks_done=0, tasks_pending=2)
        self.assertIn("⚠️", result.text)

    def test_detailed_adds_bullets(self):
        result = t2_status("ok", items=["a", "b", "c"], verbosity="detailed")
        self.assertIn("•", result.text)

    def test_detailed_many_items_expands(self):
        result = t2_status("ok", items=["a"] * 7, verbosity="detailed")
        self.assertEqual(result.keyboard_profile, "expand")


# ===========================================================================
# TestT3Incident
# ===========================================================================


class TestT3Incident(unittest.TestCase):
    def test_basic(self):
        result = t3_incident("redis", "DB down", "restarted service")
        self.assertIn("redis", result.text)
        self.assertEqual(result.template_id, TemplateID.INCIDENT)
        self.assertEqual(result.keyboard_profile, "action")

    def test_human_needed(self):
        result = t3_incident("api", "slow", "scaled up", human_needed=True)
        self.assertIn("need your call", result.text)

    def test_handled_automatically(self):
        result = t3_incident("api", "slow", "scaled up", human_needed=False)
        self.assertIn("handled it", result.text)


# ===========================================================================
# TestT4TaskDone
# ===========================================================================


class TestT4TaskDone(unittest.TestCase):
    def test_basic(self):
        result = t4_task_done("Deploy")
        self.assertIn("Deploy", result.text)
        self.assertIn("done", result.text)
        self.assertEqual(result.template_id, TemplateID.TASK_DONE)

    def test_with_result(self):
        result = t4_task_done("Build", result="3 files updated")
        self.assertIn("3 files updated", result.text)

    def test_text_within_limit_normal(self):
        result = t4_task_done("A" * 200)
        self.assertLessEqual(len(result.text), TASK_DONE_LIMIT)

    def test_detailed_adds_method(self):
        result = t4_task_done("Deploy", method_summary="via CI pipeline", verbosity="detailed")
        self.assertIn("via CI pipeline", result.text)


# ===========================================================================
# TestT5Briefing
# ===========================================================================


class TestT5Briefing(unittest.TestCase):
    def test_basic(self):
        result = t5_briefing(["item 1", "item 2"])
        self.assertIn("•", result.text)
        self.assertEqual(result.template_id, TemplateID.BRIEFING)

    def test_many_items_expand_keyboard(self):
        result = t5_briefing(["item"] * 10)
        self.assertEqual(result.keyboard_profile, "expand")

    def test_few_items_no_expand(self):
        result = t5_briefing(["a", "b"])
        self.assertEqual(result.keyboard_profile, "none")


# ===========================================================================
# TestT6Clarification
# ===========================================================================


class TestT6Clarification(unittest.TestCase):
    def test_basic(self):
        result = t6_clarification("the target", "which server")
        self.assertIn("which server", result.text)
        self.assertEqual(result.template_id, TemplateID.CLARIFICATION)

    def test_with_options_uses_action_keyboard(self):
        result = t6_clarification("a", "b", options=["opt1", "opt2"])
        self.assertEqual(result.keyboard_profile, "action")


# ===========================================================================
# TestT7Ack
# ===========================================================================


class TestT7Ack(unittest.TestCase):
    def test_basic(self):
        result = t7_ack()
        self.assertEqual(result.text, "noted.")
        self.assertEqual(result.template_id, TemplateID.ACK)

    def test_with_suggestion(self):
        result = t7_ack(next_suggestion="want me to summarise?")
        self.assertIn("want me to summarise", result.text)

    def test_within_ack_limit(self):
        result = t7_ack(next_suggestion="x" * 200)
        self.assertLessEqual(len(result.text), ACK_LIMIT)


# ===========================================================================
# TestT8Approval
# ===========================================================================


class TestT8Approval(unittest.TestCase):
    def test_basic(self):
        result = t8_approval("delete files", "data loss", "skip deletion")
        self.assertIn("delete files", result.text)
        self.assertEqual(result.template_id, TemplateID.APPROVAL)
        self.assertEqual(result.keyboard_profile, "action")


# ===========================================================================
# TestT10Error
# ===========================================================================


class TestT10Error(unittest.TestCase):
    def test_basic(self):
        result = t10_error("SSH failed", "timeout", "retry in 30s")
        self.assertIn("SSH failed", result.text)
        self.assertEqual(result.template_id, TemplateID.ERROR)
        self.assertEqual(result.keyboard_profile, "feedback")


# ===========================================================================
# TestEnforceResponseLimits
# ===========================================================================


class TestEnforceResponseLimits(unittest.TestCase):
    def test_brief_truncates(self):
        result = enforce_response_limits("x" * 500, verbosity="brief")
        self.assertLessEqual(len(result.text), DEFAULT_LIMIT)

    def test_normal_short_no_split(self):
        result = enforce_response_limits("hello world", verbosity="normal")
        self.assertIsNone(result.parts)

    def test_normal_long_splits(self):
        long_text = "word " * 500
        result = enforce_response_limits(long_text, verbosity="normal", max_single=100)
        self.assertIsNotNone(result.parts)
        self.assertGreater(len(result.parts), 1)


# ===========================================================================
# TestAutoDetectTemplate
# ===========================================================================


class TestAutoDetectTemplate(unittest.TestCase):
    def test_error_prefix_detected(self):
        self.assertEqual(auto_detect_template("❌ task failed"), TemplateID.ERROR)

    def test_incident_prefix_detected(self):
        self.assertEqual(auto_detect_template("🚨 redis is down"), TemplateID.INCIDENT)

    def test_briefing_prefix_detected(self):
        self.assertEqual(auto_detect_template("📊 Daily Status"), TemplateID.BRIEFING)

    def test_greet_detected(self):
        result = auto_detect_template("Hello! How can I help?")
        self.assertEqual(result, TemplateID.GREETING)

    def test_ack_detected(self):
        result = auto_detect_template("Got it.")
        self.assertEqual(result, TemplateID.ACK)

    def test_no_match_returns_none(self):
        result = auto_detect_template("the quick brown fox jumps over the lazy dog " * 5)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
