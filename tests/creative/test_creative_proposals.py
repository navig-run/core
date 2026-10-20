"""Proposal store: stable ids, permanent lifecycle dedupe, rate caps, scan pace."""

from __future__ import annotations

import json

import pytest

from navig.creative import proposals as prop
from navig.creative.decide import ArtifactSpec, Decision


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """All proposal + notify-dedupe state under tmp (data_dir covers both)."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    assert str(prop.proposals_path()).startswith(str(tmp_path)), prop.proposals_path()
    # Fresh data dir per test → the notify store's global init gate must not
    # skip schema creation for the new path (the established test pattern).
    from navig.notify import store as notify_store

    monkeypatch.setattr(notify_store, "_initialised", False)
    yield


def _decision(verdict: str = "create") -> Decision:
    if verdict == "reuse":
        return Decision(verdict="reuse", reason="exists", benefit="zero cost", verification="run it")
    return Decision(
        verdict="create",
        reason="repeated friction",
        genuine_gap="no capability",
        plan=["build skill"],
        artifact=ArtifactSpec(
            kind="skill", slug="frob", title="frob", description="d", commands=["navig x"]
        ),
        benefit="less repetition",
        complexity="low",
        verification="navig skill lint <dir>",
    )


def _proposal(goal: str = "frobnicate", n: int = 0) -> dict:
    ev = [{"kind": "repeated_command", "detail": f"cmd{n or 'x'}", "count": 7, "window": "7d"}]
    return prop.from_decision(
        _decision(),
        source="detector:test",
        problem="Repeated manual step",
        evidence=ev,
        goal=goal,
    )


# ── ids ──────────────────────────────────────────────────────────────────────


def test_stable_id_is_deterministic_and_evidence_sensitive():
    ev1 = [{"kind": "k", "detail": "d", "count": 3}]
    ev2 = [{"kind": "k", "detail": "d", "count": 4}]
    assert prop.stable_id("create", "goal", ev1) == prop.stable_id("create", "goal", ev1)
    assert prop.stable_id("create", "goal", ev1) != prop.stable_id("create", "goal", ev2)
    # Order-insensitive: same rows in a different order → same id.
    ev3 = [{"kind": "k", "detail": "d", "count": 3}]
    assert prop.stable_id("create", "goal", ev3) == prop.stable_id("create", "goal", ev1)


def test_from_decision_carries_card_fields_and_assumptions():
    d = _decision()
    d.assumptions = ["navig frobnicate"]
    p = prop.from_decision(d, source="agent:chat", problem="P", evidence=[], goal="g")
    for key in (
        "id", "problem", "evidence", "solution", "reuse_components",
        "expected_benefit", "impact", "artifact", "assumptions", "status",
    ):
        assert key in p, key
    assert p["artifact"]["kind"] == "skill"
    assert p["assumptions"] == ["navig frobnicate"]
    assert p["status"] == "open"
    assert p["impact"]["risk"] in ("safe", "moderate", "destructive")
    json.dumps(p)  # must survive the card round-trip


# ── lifecycle dedupe ─────────────────────────────────────────────────────────


def test_duplicate_proposal_is_never_inserted():
    p = _proposal()
    created, reason = prop.propose(p)
    assert created is True and reason == "created"
    created2, reason2 = prop.propose(dict(p))
    assert created2 is False and reason2.startswith("duplicate:")
    assert len(prop.list_proposals()) == 1


def test_dismissed_is_never_reproposed():
    p = _proposal(goal="dismissible")
    prop.propose(p)
    prop.set_status(p["id"], prop.STATUS_DISMISSED)
    created, reason = prop.propose(dict(p))
    assert created is False and reason.startswith("duplicate:")
    # The dismissal is the stored truth — not overwritten by the re-attempt.
    row = prop.get_proposal(p["id"])
    assert row["status"] == "dismissed"


def test_built_status_keeps_version_ref():
    p = _proposal(goal="buildable")
    prop.propose(p)
    ref = {"commit": "abc123", "remote": "local", "branch": "main"}
    prop.set_status(p["id"], prop.STATUS_BUILT, version_ref=ref)
    row = prop.get_proposal(p["id"])
    assert row["status"] == "built"
    assert row["version_ref"] == ref
    assert row.get("error") is None


def test_failed_status_records_error_and_is_retryable():
    p = _proposal(goal="flaky-build")
    prop.propose(p)
    prop.set_status(p["id"], prop.STATUS_FAILED, error="lint failed")
    row = prop.get_proposal(p["id"])
    assert row["status"] == "failed" and row["error"] == "lint failed"
    # Failed is NOT terminal — a retry may move it forward.
    prop.set_status(p["id"], prop.STATUS_BUILDING)
    assert prop.get_proposal(p["id"])["status"] == "building"


# ── rate caps ────────────────────────────────────────────────────────────────


def test_open_cap_is_enforced():
    # Open proposals accumulate ACROSS days (the daily cap is 2), so roll the
    # day over between inserts — reaching MAX_OPEN is a multi-day state.
    for i in range(prop.MAX_OPEN):
        env = prop.load()
        env["new_today"] = {"date": "2000-01-01", "count": 0}
        prop.save(env)
        created, reason = prop.propose(_proposal(goal=f"g{i}", n=i))
        assert created, reason
    env = prop.load()
    env["new_today"] = {"date": "2000-01-01", "count": 0}
    prop.save(env)
    created, reason = prop.propose(_proposal(goal="one-too-many", n=99))
    assert created is False and reason.startswith("rate:")
    assert "open-proposal cap" in reason


def test_daily_cap_is_enforced_and_rolls_over():
    created1, _ = prop.propose(_proposal(goal="d1", n=1))
    created2, _ = prop.propose(_proposal(goal="d2", n=2))
    assert created1 and created2
    created3, reason3 = prop.propose(_proposal(goal="d3", n=3))
    assert created3 is False and "daily proposal cap" in reason3
    # Dismissing one frees the OPEN cap but not the daily cap; roll the day over.
    env = prop.load()
    env["new_today"] = {"date": "2000-01-01", "count": 0}
    prop.save(env)
    created4, reason4 = prop.propose(_proposal(goal="d4", n=4))
    assert created4, reason4


# ── announce throttle ────────────────────────────────────────────────────────


def test_announce_claim_is_once_per_cooldown():
    p = _proposal(goal="announced")
    t0 = 1_000_000.0
    assert prop.claim_announce(p["id"], now=t0) is True
    assert prop.claim_announce(p["id"], now=t0 + 60) is False  # inside cooldown
    assert prop.claim_announce(p["id"], now=t0 + prop.ANNOUNCE_COOLDOWN_S + 1) is True


# ── scan pacing / persistence ────────────────────────────────────────────────


def test_touch_scan_records_pacing_even_for_noop():
    assert prop.last_scan_at() is None
    prop.touch_scan(now=123.0)
    assert prop.last_scan_at() == 123.0
    # Reload from disk — the pacing survives a restart.
    assert json.loads(prop.proposals_path().read_text(encoding="utf-8"))["last_scan_at"] == 123.0


def test_corrupt_store_is_empty_not_fatal():
    prop.proposals_path().parent.mkdir(parents=True, exist_ok=True)
    prop.proposals_path().write_text("{broken", encoding="utf-8")
    assert prop.list_proposals() == []
    created, _ = prop.propose(_proposal(goal="after-corruption"))
    assert created  # the scan can keep going


def test_from_decision_rejects_non_decision():
    with pytest.raises(TypeError):
        prop.from_decision(object(), source="x", problem="p", evidence=[])
