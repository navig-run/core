"""
Tests for navig.gateway.policy_gate and navig.gateway.audit_log
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path

import pytest

from navig.gateway.audit_log import AuditLog
from navig.gateway.policy_gate import PolicyDecision, PolicyGate

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# PolicyGate tests
# ---------------------------------------------------------------------------


class TestPolicyGateDefaults(unittest.TestCase):
    """Gate with no config — everything is ALLOW by default."""

    def setUp(self):
        self.gate = PolicyGate()

    def test_default_allow(self):
        result = self.gate.check("db.query")
        self.assertEqual(result.decision, PolicyDecision.ALLOW)
        self.assertIsNone(result.matched_rule)
        self.assertTrue(result.is_allowed)
        self.assertFalse(result.needs_approval)
        self.assertFalse(result.is_denied)

    def test_hard_deny_delete_all(self):
        result = self.gate.check("system.delete_all")
        self.assertEqual(result.decision, PolicyDecision.DENY)
        self.assertIsNotNone(result.matched_rule)
        self.assertTrue(result.matched_rule.startswith("hard:"))
        self.assertTrue(result.is_denied)

    def test_hard_deny_drop_all_wildcard(self):
        result = self.gate.check("db.drop_all")
        self.assertTrue(result.is_denied)

    def test_result_attributes(self):
        result = self.gate.check("run.shell", actor="telegram:999")
        self.assertEqual(result.action, "run.shell")


class TestPolicyGateFromConfig(unittest.TestCase):
    """Gate loaded from a raw gateway config dict."""

    def _make_gate(self, rules, default="allow"):
        raw = {
            "policy": {
                "default": default,
                "rules": rules,
            }
        }
        return PolicyGate.from_config(raw)

    def test_require_approval_db(self):
        gate = self._make_gate([{"pattern": "db.*", "action": "require_approval"}])
        result = gate.check("db.query")
        self.assertTrue(result.needs_approval)
        self.assertEqual(result.matched_rule, "db.*")

    def test_deny_explicit_rule(self):
        gate = self._make_gate([{"pattern": "run.delete", "action": "deny"}])
        result = gate.check("run.delete")
        self.assertTrue(result.is_denied)

    def test_first_match_wins(self):
        gate = self._make_gate(
            [
                {"pattern": "run.*", "action": "require_approval"},
                {"pattern": "run.shell", "action": "allow"},
            ]
        )
        # First rule matches "run.shell" before the more specific one
        result = gate.check("run.shell")
        self.assertTrue(result.needs_approval)

    def test_default_deny(self):
        gate = self._make_gate([], default="deny")
        result = gate.check("anything.goes")
        self.assertTrue(result.is_denied)
        self.assertIsNone(result.matched_rule)

    def test_unknown_default_fails_closed_to_deny(self):
        """A typo in gateway.policy.default must not silently claim 'allow'."""
        gate = PolicyGate.from_config({"policy": {"default": "bogus"}})
        result = gate.check("foo.bar")
        self.assertTrue(result.is_denied)

    def test_unknown_rule_action_denies_that_pattern(self):
        """A gating rule with a typo'd action denies its pattern instead of vanishing."""
        gate = self._make_gate(
            [
                {"pattern": "db.*", "action": "UNKNOWN_ACTION"},
            ]
        )
        result = gate.check("db.query")
        self.assertTrue(result.is_denied)
        # Actions outside the typo'd pattern are unaffected (still the default).
        other = gate.check("unrelated.thing")
        self.assertTrue(other.is_allowed)

    def test_bad_action_rule_still_loses_to_a_later_explicit_match_order(self):
        """First-match-wins is unchanged: a later rule for another pattern is untouched."""
        gate = self._make_gate(
            [
                {"pattern": "db.*", "action": "UNKNOWN_ACTION"},
                {"pattern": "run.*", "action": "require_approval"},
            ]
        )
        self.assertTrue(gate.check("db.query").is_denied)
        self.assertTrue(gate.check("run.restart").needs_approval)

    def test_skips_rule_with_empty_pattern(self):
        gate = self._make_gate(
            [
                {"pattern": "", "action": "deny"},
            ]
        )
        result = gate.check("db.query")
        self.assertTrue(result.is_allowed)  # empty pattern rule skipped -> default allow

    def test_from_config_none(self):
        gate = PolicyGate.from_config(None)
        result = gate.check("anything")
        self.assertTrue(result.is_allowed)

    def test_summary(self):
        gate = self._make_gate([{"pattern": "db.*", "action": "require_approval"}])
        s = gate.summary()
        self.assertIn("default", s)
        self.assertIn("rules", s)
        self.assertIn("hard_deny_patterns", s)
        self.assertEqual(len(s["rules"]), 1)


class TestPolicyGateHardDeny(unittest.TestCase):
    """Hard-deny overrides user-level allow rules."""

    def test_hard_deny_cannot_be_overridden(self):
        raw = {
            "policy": {
                "default": "allow",
                "rules": [{"pattern": "system.delete_all", "action": "allow"}],
            }
        }
        gate = PolicyGate.from_config(raw)
        result = gate.check("system.delete_all")
        # Hard deny fires BEFORE user rules
        self.assertTrue(result.is_denied)
        self.assertTrue(result.matched_rule.startswith("hard:"))


# ---------------------------------------------------------------------------
# AuditLog tests
# ---------------------------------------------------------------------------


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._log_path = Path(self._tmpdir.name) / "audit.jsonl"
        self.log = AuditLog(path=self._log_path)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_record_creates_file(self):
        self.log.record(actor="test", action="db.query", policy="allow", status="success")
        self.assertTrue(self._log_path.exists())

    def test_record_returns_dict(self):
        rec = self.log.record(actor="a", action="run.shell", policy="allow", status="success")
        self.assertIsInstance(rec, dict)
        self.assertEqual(rec["actor"], "a")
        self.assertEqual(rec["action"], "run.shell")
        self.assertEqual(rec["policy"], "allow")
        self.assertEqual(rec["status"], "success")
        self.assertIn("ts", rec)

    def test_input_is_hashed_not_stored(self):
        rec = self.log.record(
            actor="bot",
            action="db.query",
            policy="allow",
            status="success",
            raw_input="SELECT * FROM users WHERE password='secret'",
        )
        self.assertIn("input_hash", rec)
        self.assertTrue(rec["input_hash"].startswith("sha256:"))
        self.assertNotIn("password", json.dumps(rec))

    def test_output_length_stored_not_content(self):
        rec = self.log.record(
            actor="bot",
            action="db.query",
            policy="allow",
            status="success",
            raw_output="result data " * 10,
        )
        self.assertIn("output_len", rec)
        self.assertIsInstance(rec["output_len"], int)
        self.assertNotIn("result data", json.dumps(rec))

    def test_metadata_included(self):
        rec = self.log.record(
            actor="bot",
            action="run.shell",
            policy="require_approval",
            status="pending_approval",
            metadata={"request_id": "abc-123"},
        )
        self.assertEqual(rec.get("metadata", {}).get("request_id"), "abc-123")

    def test_multiple_records_appended(self):
        for i in range(5):
            self.log.record(actor="a", action=f"cmd.{i}", policy="allow", status="success")
        lines = self._log_path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 5)
        for line in lines:
            json.loads(line)  # must be valid JSON

    def test_tail_returns_last_n(self):
        for i in range(20):
            self.log.record(actor="a", action=f"cmd.{i}", policy="allow", status="success")
        tail = self.log.tail(5)
        self.assertEqual(len(tail), 5)
        self.assertEqual(tail[-1]["action"], "cmd.19")

    def test_tail_empty_when_no_file(self):
        empty_log = AuditLog(path=Path(self._tmpdir.name) / "nonexistent.jsonl")
        self.assertEqual(empty_log.tail(), [])

    def test_thread_safe_concurrent_writes(self):
        errors = []

        def writer(n):
            try:
                for _i in range(10):
                    self.log.record(
                        actor=f"thread-{n}",
                        action="concurrent.write",
                        policy="allow",
                        status="success",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        records = self.log.tail(100)
        self.assertEqual(len(records), 50)  # 5 threads × 10 records each

    def test_creates_parent_dirs(self):
        deep_path = Path(self._tmpdir.name) / "a" / "b" / "c" / "audit.jsonl"
        log = AuditLog(path=deep_path)
        log.record(actor="a", action="test", policy="allow", status="success")
        self.assertTrue(deep_path.exists())


if __name__ == "__main__":
    unittest.main()


class TestGate0Baseline(unittest.TestCase):
    """The Gate 0 baseline covers only slugs with no pre-existing call sites."""

    def _gate(self, rules=None, default="allow"):
        return PolicyGate.from_config({"policy": {"default": default, "rules": rules or []}})

    def test_dangerous_remote_slug_requires_approval_even_under_default_allow(self):
        result = self._gate().check("remote.exec.dangerous")
        self.assertTrue(result.needs_approval)
        self.assertEqual(result.matched_rule, "baseline")

    def test_baseline_require_approval_slugs(self):
        gate = self._gate()
        for slug in (
            "remote.exec.dangerous",
            "remote.docker.mutate",
            "remote.host.select",
            "remote.deploy",
            "cli.exec.dangerous",
        ):
            self.assertTrue(gate.check(slug).needs_approval, slug)

    def test_baseline_allow_slugs_stay_allowed(self):
        gate = self._gate()
        for slug in ("remote.exec", "remote.read", "remote.docker.read", "cli.exec"):
            self.assertTrue(gate.check(slug).is_allowed, slug)

    def test_user_rule_beats_the_baseline(self):
        gate = self._gate([{"pattern": "remote.exec.dangerous", "action": "allow"}])
        self.assertTrue(gate.check("remote.exec.dangerous").is_allowed)

    def test_user_deny_rule_beats_the_baseline_allow(self):
        gate = self._gate([{"pattern": "remote.read", "action": "deny"}])
        self.assertTrue(gate.check("remote.read").is_denied)

    def test_baseline_does_not_touch_unrelated_slugs(self):
        """Regression pin: the baseline must not change any pre-Gate-0 decision."""
        gate = self._gate()
        self.assertTrue(gate.check("mission.create").is_allowed)
        self.assertTrue(gate.check("db.query").is_allowed)
        self.assertTrue(gate.check("system.restart").is_allowed)

    def test_never_slugs_are_hard_denied_and_not_overridable(self):
        gate = self._gate([{"pattern": "remote.exec.never", "action": "allow"}])
        for slug in ("remote.exec.never", "cli.exec.never"):
            result = gate.check(slug)
            self.assertTrue(result.is_denied, slug)
            self.assertTrue(str(result.matched_rule).startswith("hard:"), slug)
