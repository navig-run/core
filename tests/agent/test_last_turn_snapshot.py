"""`<config_dir>/perf/last_turn.json` — the cross-process cache-hit snapshot.

``CostTracker`` is per-``run_agentic``-call and in-memory, so the CLI can never
see what the *daemon* just did — which is exactly the number an operator needs to
answer "is my prompt actually being cached?". This snapshot closes that gap, and
it is best-effort in every direction: a telemetry note must never raise into a
live turn.
"""

from __future__ import annotations

import json

import pytest

from navig.agent.usage_tracker import (
    CostTracker,
    UsageEvent,
    _last_turn_path,
    read_last_turn,
    record_last_turn,
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Pin config_dir so the snapshot never touches a real install."""
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: tmp_path)
    return tmp_path


def _event(**kw) -> UsageEvent:
    base = dict(
        turn=3,
        model="claude-sonnet-4-6",
        provider="anthropic",
        prompt_tokens=8_000,
        completion_tokens=250,
        cache_read_tokens=7_800,
        cache_write_tokens=0,
    )
    base.update(kw)
    return UsageEvent(**base)


class TestRoundTrip:
    def test_records_and_reads_back(self, cfg):
        record_last_turn(_event())
        data = read_last_turn()
        assert data is not None
        assert data["model"] == "claude-sonnet-4-6"
        assert data["cache_read_tokens"] == 7_800
        assert data["cache_write_tokens"] == 0

    def test_writes_under_perf(self, cfg):
        record_last_turn(_event())
        assert _last_turn_path() == cfg / "perf" / "last_turn.json"
        assert _last_turn_path().exists()

    def test_creates_the_perf_directory(self, cfg):
        assert not (cfg / "perf").exists()
        record_last_turn(_event())
        assert (cfg / "perf").is_dir()

    def test_latest_turn_replaces_the_previous_one(self, cfg):
        record_last_turn(_event(turn=1, model="gpt-4o"))
        record_last_turn(_event(turn=2, model="claude-haiku-4-5"))
        data = read_last_turn()
        assert data["turn"] == 2 and data["model"] == "claude-haiku-4-5"

    def test_cost_and_timestamp_are_recorded(self, cfg):
        record_last_turn(_event())
        data = read_last_turn()
        assert data["cost_usd"] > 0
        assert "at" in data and data["at"]

    def test_leaves_no_temp_file_behind(self, cfg):
        """The write is atomic (tmp + os.replace); the tmp must not survive."""
        record_last_turn(_event())
        assert list((cfg / "perf").glob("*.tmp")) == []

    def test_cost_tracker_record_writes_the_snapshot(self, cfg):
        CostTracker().record(_event(turn=9))
        assert read_last_turn()["turn"] == 9


class TestNeverRaises:
    """A health note is not worth an outage."""

    def test_read_returns_none_when_absent(self, cfg):
        assert read_last_turn() is None

    def test_read_survives_corrupt_json(self, cfg):
        path = _last_turn_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json at all", encoding="utf-8")
        assert read_last_turn() is None

    def test_read_rejects_a_non_dict_document(self, cfg):
        path = _last_turn_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert read_last_turn() is None

    def test_record_survives_an_unwritable_location(self, cfg, monkeypatch):
        def _boom():
            raise OSError("disk full")

        monkeypatch.setattr("navig.agent.usage_tracker._last_turn_path", _boom)
        record_last_turn(_event())  # must not raise

    def test_record_survives_an_unpriceable_model(self, cfg):
        record_last_turn(_event(model="some-model-nobody-has-priced"))
        assert read_last_turn()["cost_usd"] == 0.0

    def test_cost_tracker_record_survives_a_failing_snapshot(self, cfg, monkeypatch):
        """The turn must complete even if telemetry cannot be written."""

        def _boom(_event):
            raise RuntimeError("nope")

        monkeypatch.setattr("navig.agent.usage_tracker.record_last_turn", _boom)
        tracker = CostTracker()
        # A sink that raises must not reach the caller: record() is on the
        # agentic hot path, and a telemetry note is not worth a dead turn.
        tracker.record(_event())
        assert len(tracker.session_cost().events) == 1
