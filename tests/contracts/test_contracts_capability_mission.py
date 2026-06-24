"""Unit tests for navig.contracts.capability and navig.contracts.mission.

Pure-logic tests — no I/O, no network, no mocking required.
"""

from __future__ import annotations

import json
import unittest

from navig.contracts.capability import Capability, TrustScore
from navig.contracts.mission import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    Mission,
    MissionPriority,
    MissionStatus,
)

# ===========================================================================
# TestCapability
# ===========================================================================


class TestCapability(unittest.TestCase):
    """Capability dataclass — construction, serialization, repr."""

    def test_minimal_construction(self):
        cap = Capability(slug="llm")
        self.assertEqual(cap.slug, "llm")
        self.assertEqual(cap.version, "1.0.0")
        self.assertEqual(cap.description, "")
        self.assertEqual(cap.parameters, {})
        self.assertEqual(cap.metadata, {})

    def test_full_construction(self):
        cap = Capability(
            slug="ssh",
            version="2.0.0",
            description="SSH executor",
            parameters={"command": {"type": "string"}},
            metadata={"author": "team"},
        )
        self.assertEqual(cap.slug, "ssh")
        self.assertEqual(cap.version, "2.0.0")
        self.assertEqual(cap.parameters["command"]["type"], "string")
        self.assertEqual(cap.metadata["author"], "team")

    def test_to_dict_returns_all_fields(self):
        cap = Capability(slug="browser", description="Web automation")
        d = cap.to_dict()
        self.assertEqual(d["slug"], "browser")
        self.assertEqual(d["version"], "1.0.0")
        self.assertEqual(d["description"], "Web automation")
        self.assertIn("parameters", d)
        self.assertIn("metadata", d)

    def test_to_json_is_valid_json(self):
        cap = Capability(slug="llm")
        raw = cap.to_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["slug"], "llm")

    def test_from_dict_roundtrip(self):
        original = Capability(slug="db", version="1.2.3", description="DB ops")
        restored = Capability.from_dict(original.to_dict())
        self.assertEqual(restored.slug, original.slug)
        self.assertEqual(restored.version, original.version)
        self.assertEqual(restored.description, original.description)

    def test_from_json_roundtrip(self):
        cap = Capability(slug="llm", metadata={"tier": "cloud"})
        restored = Capability.from_json(cap.to_json())
        self.assertEqual(restored.metadata["tier"], "cloud")

    def test_repr_contains_slug(self):
        cap = Capability(slug="memory")
        r = repr(cap)
        self.assertIn("memory", r)
        self.assertIn("1.0.0", r)

    def test_separate_mutable_defaults(self):
        a = Capability(slug="a")
        b = Capability(slug="b")
        a.parameters["x"] = 1
        self.assertNotIn("x", b.parameters)


# ===========================================================================
# TestTrustScore
# ===========================================================================


class TestTrustScore(unittest.TestCase):
    """TrustScore — clamping, success_rate, serialization."""

    def test_default_score_is_one(self):
        ts = TrustScore(node_id="node-1")
        self.assertEqual(ts.score, 1.0)

    def test_score_clamped_above_one(self):
        ts = TrustScore(node_id="n", score=2.5)
        self.assertEqual(ts.score, 1.0)

    def test_score_clamped_below_zero(self):
        ts = TrustScore(node_id="n", score=-0.5)
        self.assertEqual(ts.score, 0.0)

    def test_score_valid_mid_value(self):
        ts = TrustScore(node_id="n", score=0.75)
        self.assertAlmostEqual(ts.score, 0.75)

    def test_success_rate_zero_missions(self):
        ts = TrustScore(node_id="n", total_missions=0)
        self.assertEqual(ts.success_rate, 1.0)

    def test_success_rate_calculated(self):
        ts = TrustScore(node_id="n", total_missions=10, success_count=7)
        self.assertAlmostEqual(ts.success_rate, 0.7)

    def test_success_rate_all_success(self):
        ts = TrustScore(node_id="n", total_missions=5, success_count=5)
        self.assertEqual(ts.success_rate, 1.0)

    def test_to_dict_contains_node_id(self):
        ts = TrustScore(node_id="abc-123")
        d = ts.to_dict()
        self.assertEqual(d["node_id"], "abc-123")

    def test_to_json_roundtrip(self):
        ts = TrustScore(node_id="n1", score=0.8, total_missions=20, success_count=16)
        restored = TrustScore.from_json(ts.to_json())
        self.assertEqual(restored.node_id, "n1")
        self.assertAlmostEqual(restored.score, 0.8)

    def test_from_dict_roundtrip(self):
        ts = TrustScore(node_id="x", success_count=3, failure_count=1, total_missions=4)
        restored = TrustScore.from_dict(ts.to_dict())
        self.assertEqual(restored.success_count, 3)
        self.assertEqual(restored.failure_count, 1)

    def test_repr_shows_score_and_node(self):
        ts = TrustScore(node_id="abcde123", score=0.9, success_count=9, total_missions=10)
        r = repr(ts)
        self.assertIn("abcde12", r)
        self.assertIn("0.90", r)


# ===========================================================================
# TestMissionStatus
# ===========================================================================


class TestMissionStatus(unittest.TestCase):
    """MissionStatus enum — values, terminal states, transitions."""

    def test_all_statuses_exist(self):
        for name in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"):
            self.assertIsNotNone(getattr(MissionStatus, name))

    def test_is_str_enum(self):
        self.assertIsInstance(MissionStatus.QUEUED, str)
        self.assertEqual(MissionStatus.QUEUED, "queued")

    def test_terminal_states_contains_right_members(self):
        for s in (
            MissionStatus.SUCCEEDED,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
            MissionStatus.TIMED_OUT,
        ):
            self.assertIn(s, TERMINAL_STATES)

    def test_non_terminal_not_in_terminal_states(self):
        self.assertNotIn(MissionStatus.QUEUED, TERMINAL_STATES)
        self.assertNotIn(MissionStatus.RUNNING, TERMINAL_STATES)

    def test_allowed_transitions_keys_cover_all_states(self):
        for s in MissionStatus:
            self.assertIn(s, ALLOWED_TRANSITIONS)

    def test_queued_can_transition_to_running(self):
        self.assertIn(MissionStatus.RUNNING, ALLOWED_TRANSITIONS[MissionStatus.QUEUED])

    def test_running_can_transition_to_all_terminal(self):
        targets = ALLOWED_TRANSITIONS[MissionStatus.RUNNING]
        for s in (MissionStatus.SUCCEEDED, MissionStatus.FAILED, MissionStatus.TIMED_OUT):
            self.assertIn(s, targets)

    def test_terminal_succeeded_has_no_allowed(self):
        self.assertEqual(len(ALLOWED_TRANSITIONS[MissionStatus.SUCCEEDED]), 0)


# ===========================================================================
# TestMissionPriority
# ===========================================================================


class TestMissionPriority(unittest.TestCase):
    """MissionPriority enum — ordering and int values."""

    def test_critical_is_lowest_int(self):
        self.assertLess(MissionPriority.CRITICAL.value, MissionPriority.HIGH.value)

    def test_low_is_highest_int(self):
        self.assertGreater(MissionPriority.LOW.value, MissionPriority.NORMAL.value)

    def test_is_int_enum(self):
        self.assertIsInstance(MissionPriority.NORMAL, int)

    def test_normal_is_50(self):
        self.assertEqual(MissionPriority.NORMAL.value, 50)


# ===========================================================================
# TestMission
# ===========================================================================


class TestMission(unittest.TestCase):
    """Mission — state machine transitions, serialization, properties."""

    def _make(self, **kwargs) -> Mission:
        return Mission(title="Test mission", **kwargs)

    # --- Construction ---

    def test_default_status_is_queued(self):
        m = self._make()
        self.assertEqual(m.status, MissionStatus.QUEUED)

    def test_mission_id_is_uuid(self):
        m = self._make()
        import uuid

        uuid.UUID(m.mission_id)  # raises if not valid

    def test_unique_ids(self):
        a = self._make()
        b = self._make()
        self.assertNotEqual(a.mission_id, b.mission_id)

    def test_priority_defaults_to_normal(self):
        m = self._make()
        self.assertEqual(m.priority, MissionPriority.NORMAL.value)

    def test_is_not_terminal_when_queued(self):
        self.assertFalse(self._make().is_terminal)

    # --- State transitions ---

    def test_start_sets_running(self):
        m = self._make()
        m.start()
        self.assertEqual(m.status, MissionStatus.RUNNING)
        self.assertIsNotNone(m.started_at)

    def test_succeed_after_start(self):
        m = self._make()
        m.start()
        m.succeed(result={"output": 42})
        self.assertEqual(m.status, MissionStatus.SUCCEEDED)
        self.assertEqual(m.result["output"], 42)
        self.assertTrue(m.is_terminal)
        self.assertIsNotNone(m.completed_at)

    def test_fail_after_start(self):
        m = self._make()
        m.start()
        m.fail(error="connection refused")
        self.assertEqual(m.status, MissionStatus.FAILED)
        self.assertEqual(m.error, "connection refused")
        self.assertTrue(m.is_terminal)

    def test_cancel_from_queued(self):
        m = self._make()
        m.cancel()
        self.assertEqual(m.status, MissionStatus.CANCELLED)
        self.assertTrue(m.is_terminal)

    def test_cancel_from_running(self):
        m = self._make()
        m.start()
        m.cancel(reason="user aborted")
        self.assertEqual(m.status, MissionStatus.CANCELLED)
        self.assertEqual(m.error, "user aborted")

    def test_timeout_from_running(self):
        m = self._make()
        m.start()
        m.timeout()
        self.assertEqual(m.status, MissionStatus.TIMED_OUT)
        self.assertTrue(m.is_terminal)

    def test_retry_from_failed(self):
        m = self._make()
        m.start()
        m.fail("oops")
        m.retry()
        self.assertEqual(m.status, MissionStatus.QUEUED)
        self.assertIsNone(m.error)
        self.assertIsNone(m.result)
        self.assertIsNone(m.started_at)

    def test_invalid_transition_raises(self):
        m = self._make()  # QUEUED
        with self.assertRaises(ValueError):
            m.succeed()  # can't succeed from QUEUED

    def test_double_transition_raises(self):
        m = self._make()
        m.start()
        m.succeed()
        with self.assertRaises(ValueError):
            m.fail("late")  # already terminal

    # --- duration_secs ---

    def test_duration_none_before_completion(self):
        m = self._make()
        m.start()
        self.assertIsNone(m.duration_secs)

    def test_duration_calculated_after_complete(self):
        m = self._make()
        m.start()
        m.succeed()
        dur = m.duration_secs
        self.assertIsNotNone(dur)
        self.assertGreaterEqual(dur, 0.0)

    # --- Serialization ---

    def test_to_dict_status_is_string(self):
        m = self._make()
        d = m.to_dict()
        self.assertIsInstance(d["status"], str)
        self.assertEqual(d["status"], "queued")

    def test_to_json_is_valid(self):
        m = self._make()
        raw = m.to_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["title"], "Test mission")

    def test_from_dict_roundtrip(self):
        m = self._make(node_id="node-abc", capability="llm")
        m.start()
        restored = Mission.from_dict(m.to_dict())
        self.assertEqual(restored.node_id, "node-abc")
        self.assertEqual(restored.status, MissionStatus.RUNNING)
        self.assertEqual(restored.capability, "llm")

    def test_from_json_roundtrip(self):
        m = self._make()
        m.start()
        m.succeed(result={"answer": "yes"})
        restored = Mission.from_json(m.to_json())
        self.assertEqual(restored.status, MissionStatus.SUCCEEDED)
        self.assertEqual(restored.result["answer"], "yes")

    def test_repr_contains_status_and_title(self):
        m = self._make()
        r = repr(m)
        self.assertIn("queued", r)
        self.assertIn("Test mission", r)


if __name__ == "__main__":
    unittest.main()
