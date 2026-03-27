"""
Tests for navig.contracts — Node, Mission, ExecutionReceipt, Capability, TrustScore, RuntimeStore.

Coverage:
  - Model validation (required fields, types, defaults)
  - Node lifecycle state machine
  - Mission lifecycle state machine (all transitions)
  - ExecutionReceipt immutability + factory
  - TrustScore.compute() algorithm
  - RuntimeStore CRUD + persistence + restart recovery
"""

import pytest

from navig.contracts import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    Capability,
    ExecutionReceipt,
    Mission,
    MissionStatus,
    Node,
    NodeOS,
    NodeStatus,
    ReceiptOutcome,
    RuntimeStore,
    TrustScore,
    reset_runtime_store,
)

# ═══════════════════════════════════════════════════════════════════════════════
# NODE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNode:
    def test_default_fields(self):
        n = Node(hostname="test-host")
        assert n.hostname == "test-host"
        assert n.node_id  # auto UUID
        assert len(n.node_id) == 36
        assert n.status == NodeStatus.PROVISIONING
        assert n.os == NodeOS.UNKNOWN
        assert n.capabilities == []
        assert 0.0 <= n.trust_score <= 1.0

    def test_unique_node_ids(self):
        a = Node(hostname="a")
        b = Node(hostname="b")
        assert a.node_id != b.node_id

    def test_go_online_from_provisioning(self):
        n = Node(hostname="h")
        n.go_online()
        assert n.status == NodeStatus.ONLINE

    def test_go_online_from_offline(self):
        n = Node(hostname="h", status=NodeStatus.OFFLINE)
        n.go_online()
        assert n.status == NodeStatus.ONLINE

    def test_go_offline(self):
        n = Node(hostname="h", status=NodeStatus.ONLINE)
        n.go_offline()
        assert n.status == NodeStatus.OFFLINE

    def test_suspend_only_from_online(self):
        n = Node(hostname="h", status=NodeStatus.ONLINE)
        n.suspend()
        assert n.status == NodeStatus.SUSPENDED

    def test_suspend_from_non_online_raises(self):
        n = Node(hostname="h", status=NodeStatus.OFFLINE)
        with pytest.raises(ValueError):
            n.suspend()

    def test_decommission(self):
        n = Node(hostname="h", status=NodeStatus.ONLINE)
        n.decommission()
        assert n.status == NodeStatus.DECOMMISSIONED

    def test_go_offline_decommissioned_raises(self):
        n = Node(hostname="h", status=NodeStatus.DECOMMISSIONED)
        with pytest.raises(ValueError):
            n.go_offline()

    def test_capability_helpers(self):
        n = Node(hostname="h")
        assert not n.has_capability("llm")
        n.add_capability("llm")
        assert n.has_capability("llm")
        n.add_capability("llm")  # idempotent
        assert n.capabilities.count("llm") == 1
        n.remove_capability("llm")
        assert not n.has_capability("llm")

    def test_touch_updates_last_seen(self):
        n = Node(hostname="h")
        old_ts = n.last_seen
        import time

        time.sleep(0.01)
        n.touch()
        assert n.last_seen >= old_ts

    def test_serialisation_round_trip(self):
        n = Node(hostname="h", os=NodeOS.LINUX, capabilities=["ssh", "llm"])
        n.go_online()
        raw = n.to_json()
        n2 = Node.from_json(raw)
        assert n2.node_id == n.node_id
        assert n2.status == NodeStatus.ONLINE
        assert n2.capabilities == ["ssh", "llm"]

    def test_to_dict_os_and_status_are_strings(self):
        n = Node(hostname="h", os=NodeOS.WINDOWS, status=NodeStatus.ONLINE)
        d = n.to_dict()
        assert d["os"] == "windows"
        assert d["status"] == "online"


# ═══════════════════════════════════════════════════════════════════════════════
# MISSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMission:
    def test_default_fields(self):
        m = Mission(title="Do something")
        assert m.title == "Do something"
        assert m.mission_id  # auto UUID
        assert m.status == MissionStatus.QUEUED
        assert m.is_terminal is False

    def test_queued_to_running(self):
        m = Mission(title="t")
        m.start()
        assert m.status == MissionStatus.RUNNING
        assert m.started_at is not None

    def test_running_to_succeed(self):
        m = Mission(title="t")
        m.start()
        m.succeed(result={"answer": 42})
        assert m.status == MissionStatus.SUCCEEDED
        assert m.result == {"answer": 42}
        assert m.completed_at is not None
        assert m.is_terminal is True

    def test_running_to_fail(self):
        m = Mission(title="t")
        m.start()
        m.fail("oops")
        assert m.status == MissionStatus.FAILED
        assert m.error == "oops"
        assert m.is_terminal

    def test_queued_cancel(self):
        m = Mission(title="t")
        m.cancel("no longer needed")
        assert m.status == MissionStatus.CANCELLED
        assert m.is_terminal

    def test_running_cancel(self):
        m = Mission(title="t")
        m.start()
        m.cancel()
        assert m.status == MissionStatus.CANCELLED

    def test_timeout(self):
        m = Mission(title="t")
        m.start()
        m.timeout()
        assert m.status == MissionStatus.TIMED_OUT
        assert m.is_terminal

    def test_invalid_transition_raises(self):
        m = Mission(title="t")
        m.start()
        m.succeed()
        with pytest.raises(ValueError):
            m.cancel()  # cannot cancel succeeded mission

    def test_double_start_raises(self):
        m = Mission(title="t")
        m.start()
        with pytest.raises(ValueError):
            m.start()

    def test_terminal_states_set(self):
        for s in TERMINAL_STATES:
            assert s in list(MissionStatus)

    def test_allowed_transitions_complete(self):
        for status in list(MissionStatus):
            assert status in ALLOWED_TRANSITIONS

    def test_duration_secs(self):
        m = Mission(title="t")
        m.start()
        m.succeed()
        assert m.duration_secs is not None
        assert m.duration_secs >= 0

    def test_duration_secs_none_if_not_started(self):
        m = Mission(title="t")
        m.cancel()
        assert m.duration_secs is None

    def test_serialisation_round_trip(self):
        m = Mission(title="test mission", capability="llm", priority=10)
        m.start()
        m.succeed(result="done")
        raw = m.to_json()
        m2 = Mission.from_json(raw)
        assert m2.mission_id == m.mission_id
        assert m2.status == MissionStatus.SUCCEEDED
        assert m2.result == "done"

    def test_to_dict_status_is_string(self):
        m = Mission(title="t")
        assert m.to_dict()["status"] == "queued"


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION RECEIPT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecutionReceipt:
    def _make(self, outcome=ReceiptOutcome.SUCCEEDED, **kw) -> ExecutionReceipt:
        defaults = dict(
            mission_id="a" * 36,
            node_id="b" * 36,
            title="test",
            capability="llm",
            outcome=outcome,
            completed_at="2026-01-01T00:00:00+00:00",
        )
        defaults.update(kw)
        return ExecutionReceipt(**defaults)

    def test_immutable(self):
        r = self._make()
        with pytest.raises((AttributeError, TypeError)):
            r.outcome = ReceiptOutcome.FAILED  # type: ignore

    def test_is_success(self):
        assert self._make(ReceiptOutcome.SUCCEEDED).is_success
        assert not self._make(ReceiptOutcome.FAILED).is_success

    def test_is_failure(self):
        assert self._make(ReceiptOutcome.FAILED).is_failure
        assert self._make(ReceiptOutcome.TIMED_OUT).is_failure
        assert not self._make(ReceiptOutcome.SUCCEEDED).is_failure

    def test_serialisation_round_trip(self):
        r = self._make(ReceiptOutcome.FAILED, error="boom", duration_secs=1.5)
        r2 = ExecutionReceipt.from_json(r.to_json())
        assert r2.outcome == ReceiptOutcome.FAILED
        assert r2.error == "boom"
        assert r2.duration_secs == 1.5

    def test_to_dict_outcome_is_string(self):
        r = self._make()
        assert r.to_dict()["outcome"] == "succeeded"


# ═══════════════════════════════════════════════════════════════════════════════
# CAPABILITY & TRUST SCORE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCapability:
    def test_defaults(self):
        c = Capability(slug="llm")
        assert c.version == "1.0.0"
        assert c.description == ""

    def test_round_trip(self):
        c = Capability(slug="ssh", version="2.0.0", description="remote shell")
        c2 = Capability.from_json(c.to_json())
        assert c2.slug == "ssh"
        assert c2.version == "2.0.0"


class TestTrustScore:
    def _receipt(
        self, outcome: ReceiptOutcome, node_id="n1", dur=1.0
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            mission_id="m" * 36,
            node_id=node_id,
            title="t",
            capability="llm",
            outcome=outcome,
            completed_at="2026-01-01T00:00:00+00:00",
            started_at="2026-01-01T00:00:00+00:00",
            duration_secs=dur,
        )

    def test_new_node_score_is_conservative(self):
        ts = TrustScore.compute("n1", [])
        assert ts.score == pytest.approx(0.5, abs=0.01)  # prior = 0.5

    def test_all_success_converges_high(self):
        receipts = [self._receipt(ReceiptOutcome.SUCCEEDED) for _ in range(50)]
        ts = TrustScore.compute("n1", receipts)
        assert ts.score > 0.9
        assert ts.success_rate == 1.0

    def test_all_fail_converges_low(self):
        receipts = [self._receipt(ReceiptOutcome.FAILED) for _ in range(50)]
        ts = TrustScore.compute("n1", receipts)
        assert ts.score < 0.1

    def test_score_clamped(self):
        ts = TrustScore(node_id="x", score=2.5)
        assert ts.score == 1.0
        ts2 = TrustScore(node_id="x", score=-0.5)
        assert ts2.score == 0.0

    def test_counts(self):
        receipts = [
            self._receipt(ReceiptOutcome.SUCCEEDED),
            self._receipt(ReceiptOutcome.FAILED),
            self._receipt(ReceiptOutcome.CANCELLED),
        ]
        ts = TrustScore.compute("n1", receipts)
        assert ts.total_missions == 3
        assert ts.success_count == 1
        assert ts.failure_count == 1
        assert ts.cancel_count == 1

    def test_avg_duration(self):
        receipts = [
            self._receipt(ReceiptOutcome.SUCCEEDED, dur=d) for d in (1.0, 2.0, 3.0)
        ]
        ts = TrustScore.compute("n1", receipts)
        assert ts.avg_duration_secs == pytest.approx(2.0)

    def test_round_trip(self):
        ts = TrustScore(node_id="x", score=0.75, total_missions=5)
        ts2 = TrustScore.from_json(ts.to_json())
        assert ts2.score == 0.75
        assert ts2.total_missions == 5


# ═══════════════════════════════════════════════════════════════════════════════
# RUNTIME STORE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRuntimeStore:
    @pytest.fixture()
    def store(self, tmp_path):
        return reset_runtime_store(tmp_path / "runtime")

    def test_register_and_get_node(self, store):
        n = Node(hostname="srv1")
        store.register_node(n)
        assert store.get_node(n.node_id) is n

    def test_list_nodes_filter_by_status(self, store):
        n1 = Node(hostname="a", status=NodeStatus.ONLINE)
        n2 = Node(hostname="b", status=NodeStatus.OFFLINE)
        store.register_node(n1)
        store.register_node(n2)
        online = store.list_nodes(status=NodeStatus.ONLINE)
        assert len(online) == 1
        assert online[0].hostname == "a"

    def test_create_and_get_mission(self, store):
        m = Mission(title="deploy")
        store.create_mission(m)
        assert store.get_mission(m.mission_id) is m

    def test_advance_mission_start(self, store):
        m = Mission(title="t")
        store.create_mission(m)
        store.advance_mission(m.mission_id, "start")
        assert m.status == MissionStatus.RUNNING

    def test_advance_mission_invalid_action_raises(self, store):
        m = Mission(title="t")
        store.create_mission(m)
        with pytest.raises(ValueError):
            store.advance_mission(m.mission_id, "teleport")

    def test_advance_mission_retry_requeues_failed(self, store):
        m = Mission(title="t")
        store.create_mission(m)
        store.advance_mission(m.mission_id, "start")
        store.advance_mission(m.mission_id, "fail:oops")
        assert m.status == MissionStatus.FAILED
        store.advance_mission(m.mission_id, "retry")
        assert m.status == MissionStatus.QUEUED
        assert m.error is None
        assert m.started_at is None

    def test_advance_mission_retry_requeues_timed_out(self, store):
        m = Mission(title="t")
        store.create_mission(m)
        store.advance_mission(m.mission_id, "start")
        store.advance_mission(m.mission_id, "timeout")
        assert m.status == MissionStatus.TIMED_OUT
        store.advance_mission(m.mission_id, "retry")
        assert m.status == MissionStatus.QUEUED

    def test_complete_mission_success(self, store):
        n = Node(hostname="n")
        store.register_node(n)
        m = Mission(title="t", node_id=n.node_id)
        store.create_mission(m)
        store.advance_mission(m.mission_id, "start")
        receipt = store.complete_mission(m.mission_id, succeeded=True, result={"x": 1})
        assert receipt.outcome == ReceiptOutcome.SUCCEEDED
        assert store.get_receipt(receipt.receipt_id) is receipt

    def test_complete_mission_failure(self, store):
        m = Mission(title="t", node_id="n1")
        store.create_mission(m)
        store.advance_mission(m.mission_id, "start")
        receipt = store.complete_mission(m.mission_id, succeeded=False, error="crash")
        assert receipt.outcome == ReceiptOutcome.FAILED
        assert receipt.error == "crash"

    def test_list_receipts_filter(self, store):
        n1_id = "n1-" + "a" * 33
        n2_id = "n2-" + "b" * 33
        for i in range(3):
            m = Mission(title=f"t{i}", node_id=n1_id)
            store.create_mission(m)
            store.advance_mission(m.mission_id, "start")
            store.complete_mission(m.mission_id, succeeded=True)
        m2 = Mission(title="other", node_id=n2_id)
        store.create_mission(m2)
        store.advance_mission(m2.mission_id, "start")
        store.complete_mission(m2.mission_id, succeeded=False, error="x")
        assert len(store.list_receipts(node_id=n1_id)) == 3
        assert len(store.list_receipts(node_id=n2_id)) == 1

    def test_compute_trust_score(self, store):
        node_id = "node-" + "x" * 31
        for _ in range(10):
            m = Mission(title="t", node_id=node_id)
            store.create_mission(m)
            store.advance_mission(m.mission_id, "start")
            store.complete_mission(m.mission_id, succeeded=True)
        ts = store.compute_trust_score(node_id)
        assert ts.total_missions == 10
        assert ts.score > 0.5

    def test_persistence_flush_and_reload(self, tmp_path):
        store_dir = tmp_path / "runtime"
        s1 = RuntimeStore(store_dir=store_dir)
        n = Node(hostname="persisted")
        s1.register_node(n)
        m = Mission(title="saved")
        s1.create_mission(m)
        s1.flush()

        s2 = RuntimeStore(store_dir=store_dir)
        assert s2.get_node(n.node_id) is not None
        assert s2.get_node(n.node_id).hostname == "persisted"
        assert s2.get_mission(m.mission_id) is not None

    def test_stats(self, store):
        store.register_node(Node(hostname="x"))
        store.create_mission(Mission(title="y"))
        s = store.stats()
        assert s["nodes"] == 1
        assert s["missions"] == 1
        assert s["receipts"] == 0

    def test_get_missing_node_returns_none(self, store):
        assert store.get_node("nonexistent") is None

    def test_get_missing_mission_returns_none(self, store):
        assert store.get_mission("nonexistent") is None

    def test_advance_missing_mission_raises(self, store):
        with pytest.raises(KeyError):
            store.advance_mission("not-there", "start")
