"""Signal detectors + the proactive scan (mission scenarios 4, 5, 1-at-scan, dedupe)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from navig.creative import proposals as prop
from navig.creative import signals
from navig.creative.decide import IndexView


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    assert str(prop.proposals_path()).startswith(str(tmp_path))
    # Fresh data dir per test → the notify store's global init gate must not
    # skip schema creation for the new path (the established test pattern).
    from navig.notify import store as notify_store

    monkeypatch.setattr(notify_store, "_initialised", False)
    yield


def _ts(*, minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _rec(command: str, *, minutes_ago: float = 1.0, ok: bool = True):
    return SimpleNamespace(
        command=command,
        timestamp=_ts(minutes_ago=minutes_ago),
        status=SimpleNamespace(value="success" if ok else "failed"),
    )


def _entry(eid: str, *, state: str = "available", name: str | None = None, summary: str = "") -> dict:
    return {
        "id": eid,
        "kind": "command",
        "name": name or eid.split(":", 1)[-1],
        "summary": summary,
        "how_to_invoke": {"type": "cli", "ref": eid.split(":", 1)[-1]},
        "inputs": None,
        "outputs": None,
        "safety": "unknown",
        "requires": {"config": [], "plugins": [], "entitlement": None},
        "declared": {"origin": "test", "status": "stable"},
        "observed": {"state": state, "degraded": state != "available",
                     "checked_at": "t", "error": None},
        "provenance": "runtime",
        "tags": [],
        "provider": None,
        "source_ref": "",
    }


def _view(*entries: dict) -> IndexView:
    return IndexView({"schema_version": 1, "built_at": "t", "entries": list(entries)})


# ── detectors ────────────────────────────────────────────────────────────────


def test_repeated_commands_count_window_and_normalization():
    now = time.time()
    records = [
        _rec("navig  docker   ps", minutes_ago=60),
        _rec("navig docker ps", minutes_ago=120),
        _rec("navig docker ps", minutes_ago=180),
        _rec("navig docker ps", minutes_ago=300),
        _rec("navig docker ps", minutes_ago=400),
        _rec("navig docker ps", minutes_ago=500),
        # Outside the 7-day window — must not count.
        _rec("navig docker ps", minutes_ago=8 * 24 * 60),
    ]
    hits = signals.detect_repeated_commands(records, now=now)
    assert hits == [("navig docker ps", 6)]
    # Below threshold stays quiet.
    assert signals.detect_repeated_commands(records[:4], now=now) == []


def test_recurring_failures_only_count_failed_status():
    now = time.time()
    records = [
        _rec("navig flaky", minutes_ago=10, ok=False),
        _rec("navig flaky", minutes_ago=20, ok=False),
        _rec("navig flaky", minutes_ago=30, ok=False),
        _rec("navig flaky", minutes_ago=40, ok=True),  # success must not count
    ]
    hits = signals.detect_recurring_failures(records, now=now)
    assert hits == [("navig flaky", 3)]


def test_repeated_sequences_group_by_time_gap():
    now = time.time()
    records = []
    for round_i in range(3):  # the same 2-step run, three times, ~1 h apart
        base = round_i * 60
        # dump BEFORE verify → dump is OLDER → larger minutes_ago.
        records.append(_rec("navig backup dump", minutes_ago=base + 11))
        records.append(_rec("navig backup verify", minutes_ago=base + 10))
    hits = signals.detect_repeated_sequences(records, now=now)
    assert len(hits) == 1
    seq, count = hits[0]
    assert seq == ("navig backup dump", "navig backup verify")
    assert count == 3

    # A gap beyond SEQUENCE_GAP_S splits the run — no phantom sequences.
    spread = [
        _rec("navig a", minutes_ago=5),
        _rec("navig b", minutes_ago=5 + signals.SEQUENCE_GAP_S / 60 + 2),
        _rec("navig a", minutes_ago=200),
        _rec("navig b", minutes_ago=200 + signals.SEQUENCE_GAP_S / 60 + 2),
        _rec("navig a", minutes_ago=400),
        _rec("navig b", minutes_ago=400 + signals.SEQUENCE_GAP_S / 60 + 2),
    ]
    assert signals.detect_repeated_sequences(spread, now=now) == []


def test_blocked_goals_read_stopped_and_failing_streaks():
    goals = [
        {"objective": "ship digest", "fail_streak": 3, "run_state": "active"},
        {"objective": "nightly backup", "fail_streak": 0, "run_state": "stopped"},
        {"objective": "healthy one", "fail_streak": 1, "run_state": "active"},
        {"objective": "", "fail_streak": 9, "run_state": "active"},  # no label → skip
    ]
    notes = signals.detect_blocked_goals(goals)
    assert "ship digest (failing 3× in a row)" in notes
    assert "nightly backup (goal stopped)" in notes
    assert not any("healthy one" in n for n in notes)


def test_broken_capabilities_come_from_the_index():
    view = _view(
        _entry("command:navig fine"),
        _entry("command:navig broken", state="broken"),
    )
    broken = signals.detect_broken_capabilities(view)
    assert [b["id"] for b in broken] == ["command:navig broken"]


# ── evidence assembly ────────────────────────────────────────────────────────


def test_collect_evidence_builds_per_signal_candidates():
    records = [_rec("navig frobnicate", minutes_ago=i * 5) for i in range(6)]
    goals = [{"objective": "deploy", "fail_streak": 4, "run_state": "active"}]
    view = _view(_entry("command:navig rot", state="broken"))

    evidence, candidates = signals.collect_evidence(
        records=records, goals=goals, index=view
    )
    assert evidence.repeated_commands == [("navig frobnicate", 6)]
    assert evidence.blocked_goals

    kinds = {c["kind"] for c in candidates}
    assert kinds == {"repeated_command", "blocked_goal", "broken_capability"}
    repeat = next(c for c in candidates if c["kind"] == "repeated_command")
    assert repeat["evidence"][0]["count"] == 6
    assert repeat["goal"] == "navig frobnicate"


# ── the scan ─────────────────────────────────────────────────────────────────


def test_standard_mode_skips_without_reading_history():
    captured: list[dict] = []

    class _Explode:
        def __iter__(self):
            raise AssertionError("Standard mode must not read history")

    summary = signals.run_scan(
        mode="standard", records=_Explode(), dispatch=captured.append
    )
    assert summary["skipped"] is True
    assert summary["created"] == [] and summary["dispatched"] == []
    assert captured == []
    assert prop.last_scan_at() is not None, "pacing heartbeat still records the run"


def test_quiet_history_is_noop_with_scan_stamp():
    captured: list[dict] = []
    summary = signals.run_scan(
        mode="creative",
        records=[_rec("ls", minutes_ago=1)],
        goals=[],
        index=_view(),
        dispatch=captured.append,
    )
    assert summary["candidates"] == 0
    assert summary["created"] == [] and summary["dispatched"] == []
    assert summary["skipped"] is False
    assert prop.last_scan_at() is not None


def test_repeated_friction_yields_grounded_proposal_dispatched_once():
    records = [_rec("navig frobnicate reports", minutes_ago=i * 90) for i in range(6)]
    captured: list[dict] = []
    first = signals.run_scan(
        mode="creative", records=records, goals=[], index=_view(),
        dispatch=captured.append,
    )
    assert first["created"] and len(first["created"]) == 1
    assert first["dispatched"] == first["created"]
    assert len(captured) == 1

    # Grounded in evidence: counts match what was seeded.
    row = prop.get_proposal(first["created"][0])
    assert row["evidence"][0]["count"] == 6
    assert row["evidence"][0]["detail"] == "navig frobnicate reports"
    assert row["source"] == "detector:repeated_command"
    assert row["verdict"] == "create"

    # Second scan: same friction → permanent dedupe, no second dispatch.
    second = signals.run_scan(
        mode="creative", records=records, goals=[], index=_view(),
        dispatch=captured.append,
    )
    assert second["created"] == []
    assert second["duplicates"] == 1
    assert len(captured) == 1, "already-announced proposal must not re-page"


def test_existing_capability_scan_proposes_reuse_not_duplicate():
    records = [_rec("navig docker ps", minutes_ago=i * 100) for i in range(6)]
    view = _view(
        _entry("command:navig docker ps", name="navig docker ps", summary="List docker containers")
    )
    captured: list[dict] = []
    summary = signals.run_scan(
        mode="creative", records=records, goals=[], index=view, dispatch=captured.append
    )
    assert len(summary["created"]) == 1
    row = prop.get_proposal(summary["created"][0])
    assert row["verdict"] == "reuse"
    assert row["reuse_components"] == ["command:navig docker ps"]


def test_dismissed_proposal_is_never_resurrected_by_a_scan():
    records = [_rec("navig noisy", minutes_ago=i * 30) for i in range(6)]
    first = signals.run_scan(
        mode="creative", records=records, goals=[], index=_view(), dispatch=lambda p: None
    )
    pid = first["created"][0]
    prop.set_status(pid, prop.STATUS_DISMISSED)

    second = signals.run_scan(
        mode="creative", records=records, goals=[], index=_view(), dispatch=lambda p: None
    )
    assert second["created"] == []
    assert second["duplicates"] >= 1
    assert prop.get_proposal(pid)["status"] == "dismissed"


def test_dispatch_failure_releases_the_claim_and_keeps_the_scan_alive():
    records = [_rec("navig x y", minutes_ago=i * 30) for i in range(6)]

    def _bad_dispatch(p):
        raise RuntimeError("telegram down")

    summary = signals.run_scan(
        mode="creative", records=records, goals=[], index=_view(), dispatch=_bad_dispatch
    )
    # The proposal survives; the announce claim is given back so a later scan
    # may retry the notification.
    assert len(summary["created"]) == 1
    assert summary["dispatched"] == []
    assert prop.claim_announce(summary["created"][0]) is True
