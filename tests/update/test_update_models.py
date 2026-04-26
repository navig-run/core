"""Hermetic unit tests for navig.update.models — pure data models."""

from __future__ import annotations

import pytest

from navig.update.models import (
    NodeResult,
    UpdatePlan,
    UpdateResult,
    VersionInfo,
    _version_lt,
)

# ---------------------------------------------------------------------------
# _version_lt — version comparison helper
# ---------------------------------------------------------------------------


class TestVersionLt:
    def test_older_is_less(self):
        assert _version_lt("1.0.0", "2.0.0")

    def test_same_is_not_less(self):
        assert not _version_lt("1.2.3", "1.2.3")

    def test_newer_is_not_less(self):
        assert not _version_lt("2.0.0", "1.0.0")

    def test_minor_version(self):
        assert _version_lt("1.9.0", "1.10.0")

    def test_patch_version(self):
        assert _version_lt("1.0.1", "1.0.2")

    def test_v_prefixed_strings(self):
        assert _version_lt("v1.0.0", "v2.0.0")


# ---------------------------------------------------------------------------
# VersionInfo.needs_update
# ---------------------------------------------------------------------------


class TestVersionInfoNeedsUpdate:
    def test_needs_update_when_behind(self):
        vi = VersionInfo(node_id="n1", current="1.0.0", latest="1.2.0")
        assert vi.needs_update

    def test_no_update_when_current(self):
        vi = VersionInfo(node_id="n1", current="1.2.0", latest="1.2.0")
        assert not vi.needs_update

    def test_no_update_when_ahead(self):
        vi = VersionInfo(node_id="n1", current="2.0.0", latest="1.9.9")
        assert not vi.needs_update

    def test_no_update_when_current_unknown(self):
        vi = VersionInfo(node_id="n1", current="unknown", latest="1.0.0")
        assert not vi.needs_update

    def test_no_update_when_latest_none(self):
        vi = VersionInfo(node_id="n1", current="1.0.0", latest=None)
        assert not vi.needs_update

    def test_no_update_when_error_present(self):
        vi = VersionInfo(node_id="n1", current="1.0.0", latest="2.0.0", error="timeout")
        assert not vi.needs_update


# ---------------------------------------------------------------------------
# VersionInfo.reachable
# ---------------------------------------------------------------------------


class TestVersionInfoReachable:
    def test_reachable_without_error(self):
        vi = VersionInfo(node_id="n1")
        assert vi.reachable

    def test_not_reachable_with_error(self):
        vi = VersionInfo(node_id="n1", error="connection refused")
        assert not vi.reachable


# ---------------------------------------------------------------------------
# VersionInfo.to_dict
# ---------------------------------------------------------------------------


class TestVersionInfoToDict:
    def test_keys(self):
        vi = VersionInfo(node_id="n1", current="1.0.0", latest="2.0.0")
        d = vi.to_dict()
        assert set(d.keys()) == {"node_id", "current", "latest", "install_type", "source_name", "needs_update", "error"}

    def test_needs_update_reflected(self):
        vi = VersionInfo(node_id="n1", current="1.0.0", latest="2.0.0")
        assert vi.to_dict()["needs_update"] is True

    def test_error_passthrough(self):
        vi = VersionInfo(node_id="n1", error="timeout")
        assert vi.to_dict()["error"] == "timeout"


# ---------------------------------------------------------------------------
# NodeResult.to_dict
# ---------------------------------------------------------------------------


class TestNodeResultToDict:
    def test_keys_present(self):
        r = NodeResult(node_id="n1")
        d = r.to_dict()
        assert "node_id" in d and "ok" in d and "steps" in d

    def test_elapsed_rounded(self):
        r = NodeResult(node_id="n1", elapsed_seconds=1.23456)
        assert r.to_dict()["elapsed_seconds"] == 1.23

    def test_steps_passthrough(self):
        r = NodeResult(node_id="n1", steps=["step1", "step2"])
        assert r.to_dict()["steps"] == ["step1", "step2"]


# ---------------------------------------------------------------------------
# UpdateResult.success / failed_nodes / node
# ---------------------------------------------------------------------------


class TestUpdateResult:
    def test_success_when_all_ok(self):
        r = UpdateResult(node_results=[NodeResult(node_id="n1", ok=True)])
        assert r.success

    def test_success_when_skipped(self):
        r = UpdateResult(node_results=[NodeResult(node_id="n1", ok=False, skipped=True)])
        assert r.success

    def test_not_success_when_failed(self):
        r = UpdateResult(node_results=[NodeResult(node_id="n1", ok=False)])
        assert not r.success

    def test_failed_nodes_returned(self):
        nr = NodeResult(node_id="bad", ok=False)
        r = UpdateResult(node_results=[NodeResult(node_id="ok", ok=True), nr])
        assert r.failed_nodes == [nr]

    def test_node_lookup(self):
        nr = NodeResult(node_id="target")
        r = UpdateResult(node_results=[nr])
        assert r.node("target") is nr

    def test_node_lookup_missing(self):
        r = UpdateResult(node_results=[])
        assert r.node("missing") is None

    def test_to_dict_structure(self):
        r = UpdateResult(node_results=[NodeResult(node_id="n1")], total_elapsed_seconds=3.5)
        d = r.to_dict()
        assert "success" in d and "nodes" in d
        assert d["total_elapsed_seconds"] == 3.5


# ---------------------------------------------------------------------------
# UpdatePlan.summary
# ---------------------------------------------------------------------------


class _FakeTarget:
    """Minimal stand-in for UpdateTarget used only by UpdatePlan.summary."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


class TestUpdatePlanSummary:
    def test_summary_counts(self):
        plan = UpdatePlan(
            to_update=[_FakeTarget("a")],
            up_to_date=[_FakeTarget("b"), _FakeTarget("c")],
            unreachable=[],
            version_infos={},
        )
        s = plan.summary()
        assert "1 to update" in s
        assert "2 already up-to-date" in s

    def test_summary_empty_plan(self):
        plan = UpdatePlan()
        s = plan.summary()
        assert "0 to update" in s
