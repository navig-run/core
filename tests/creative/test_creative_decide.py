"""The reuse-first ladder: verdicts, thresholds, and the mission's scenario rows."""

from __future__ import annotations

from navig.creative.decide import (
    FAIL_CMD_MIN,
    REPEAT_CMD_MIN,
    Decision,
    Evidence,
    IndexView,
    decide,
)


def _entry(
    eid: str,
    *,
    kind: str = "command",
    name: str | None = None,
    summary: str = "",
    state: str = "available",
    config: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": eid,
        "kind": kind,
        "name": name or eid.split(":", 1)[-1],
        "summary": summary,
        "how_to_invoke": {"type": "cli", "ref": eid.split(":", 1)[-1]},
        "inputs": None,
        "outputs": None,
        "safety": "unknown",
        "requires": {"config": config or [], "plugins": [], "entitlement": None},
        "declared": {"origin": "test", "status": "stable"},
        "observed": {"state": state, "degraded": state not in ("available", "wired"),
                     "checked_at": "t", "error": None},
        "provenance": "runtime",
        "tags": tags or [],
        "provider": None,
        "source_ref": "",
    }


def _view(*entries: dict) -> IndexView:
    return IndexView({"schema_version": 1, "built_at": "t", "entries": list(entries)})


# ── 1. reuse ─────────────────────────────────────────────────────────────────


def test_existing_capability_reused():
    idx = _view(
        _entry(
            "command:navig docker ps",
            name="navig docker ps",
            summary="List docker containers",
        )
    )
    d = decide("list docker containers", index=idx)
    assert d.verdict == "reuse"
    assert d.artifact is None, "reuse must never carry a build artifact"
    assert d.reused_components == ["command:navig docker ps"]
    assert d.reason and d.benefit and d.verification
    assert d.complexity == "none"


def test_reuse_beats_create_when_evidence_is_heavy():
    """A repeated command that EXISTS is reuse — never a duplicate capability."""
    idx = _view(
        _entry(
            "command:navig docker ps",
            name="navig docker ps",
            summary="List docker containers",
        )
    )
    ev = Evidence(repeated_commands=[("navig docker ps", 12)])
    d = decide("list docker containers", evidence=ev, index=idx)
    assert d.verdict == "reuse"
    assert d.artifact is None
    # Exact evidence hit feeds the ladder even with an empty goal.
    d2 = decide("", evidence=ev, index=idx)
    assert d2.verdict == "reuse"
    assert d2.artifact is None


# ── 2. configure / compose ───────────────────────────────────────────────────


def test_compose_missing_config_only():
    """Two wired capabilities + only glue/config missing → plan carries no artifact."""
    idx = _view(
        _entry("command:navig a", summary="step a"),
        _entry("command:navig b", summary="step b"),
    )
    ev = Evidence(sequences=[(("navig a", "navig b"), 4)])
    d = decide("", evidence=ev, index=idx)
    assert d.verdict == "compose"
    assert d.artifact is None
    assert set(d.reused_components) == {"command:navig a", "command:navig b"}
    assert d.genuine_gap and "glue" in d.genuine_gap
    assert d.plan


def test_configure_when_required_config_missing():
    idx = _view(
        _entry(
            "command:navig remote up",
            name="navig remote up",
            summary="Bring up the remote tunnel",
            config=["remote.host", "remote.token"],
        )
    )
    d = decide("bring up the remote tunnel", config={}, index=idx)
    assert d.verdict == "configure"
    assert d.artifact is None
    assert "remote.host" in d.genuine_gap and "remote.token" in d.genuine_gap
    assert any("remote.host" in step for step in d.plan)


def test_configure_resolves_to_reusable_when_config_present():
    idx = _view(
        _entry(
            "command:navig remote up",
            name="navig remote up",
            summary="Bring up the remote tunnel",
            config=["remote.host"],
        )
    )
    d = decide("bring up the remote tunnel", config={"remote.host": "box1"}, index=idx)
    assert d.verdict == "reuse"


# ── 4. real gap → create ─────────────────────────────────────────────────────


def test_gap_creates_skill_artifact_from_evidence():
    idx = _view()  # nothing exists
    ev = Evidence(repeated_commands=[("navig frobnicate reports", REPEAT_CMD_MIN + 2)])
    d = decide("frobnicate reports", evidence=ev, index=idx)
    assert d.verdict == "create"
    assert d.artifact is not None
    assert d.artifact.kind == "skill"
    assert d.artifact.slug
    assert "navig frobnicate reports" in d.artifact.commands
    assert "skill lint" in d.verification
    assert d.genuine_gap and d.benefit and d.complexity == "low"


def test_blocked_goal_alone_justifies_create():
    idx = _view()
    d = decide("ship the nightly digest", evidence=Evidence(blocked_goals=["digest goal stopped"]), index=idx)
    assert d.verdict == "create"
    assert d.artifact is not None


# ── 9. broken → scoped extend ────────────────────────────────────────────────


def test_broken_capability_repair_scoped():
    idx = _view(
        _entry(
            "command:navig tunnel open",
            name="navig tunnel open",
            summary="Open the tunnel",
            state="broken",
        )
    )
    d = decide("open the tunnel", index=idx)
    assert d.verdict == "extend"
    assert d.artifact is None, "repair must not mint a replacement"
    assert d.reused_components == ["command:navig tunnel open"]
    assert "broken" in d.genuine_gap
    assert "scoped" in d.plan[0]


# ── 5. no-op ─────────────────────────────────────────────────────────────────


def test_quiet_history_is_noop():
    d = decide("anything at all", evidence=Evidence(), index=_view())
    assert d.verdict == "noop"
    assert d.is_noop
    assert d.artifact is None
    assert d.reason


def test_sub_threshold_evidence_is_noop():
    idx = _view()
    ev = Evidence(repeated_commands=[("navig rare", REPEAT_CMD_MIN - 1)])
    d = decide("", evidence=ev, index=idx)
    assert d.verdict == "noop", "thresholds exist to keep suggestions quiet"
    assert d.artifact is None


def test_incidental_summary_words_never_fake_a_reuse():
    """Regression: substring scoring let 'the' match inside 'synthesize' (score 6)
    and two summary words ('the' + 'weekly') reach the strong threshold — a fake
    goal then 'reused' an unrelated command. Identity must come from name/id."""
    idx = _view(
        _entry(
            "command:navig body checkin",
            name="navig body checkin",
            summary="Preview or send to Telegram the weigh-in prompt or the weekly card.",
        )
    )
    d = decide("the weekly report", index=idx)
    assert d.verdict != "reuse", d
    assert d.artifact is None
    # …while a real name word still identifies the capability.
    d2 = decide("send the body checkin", index=idx)
    assert d2.verdict == "reuse"


def test_failure_threshold_is_explicit():
    idx = _view()
    ev = Evidence(failing_commands=[("navig flaky", FAIL_CMD_MIN)])
    d = decide("", evidence=ev, index=idx)
    assert d.verdict == "create"
    ev2 = Evidence(failing_commands=[("navig flaky", FAIL_CMD_MIN - 1)])
    d2 = decide("", evidence=ev2, index=idx)
    assert d2.verdict == "noop"


# ── assumptions (mission provenance: AI claims the index cannot confirm) ─────


def test_unconfirmed_command_named_in_goal_becomes_an_assumption():
    idx = _view(_entry("command:navig docker ps", summary="List docker containers"))
    d = decide("then run navig frobnicate to finish", index=idx)
    assert any(a == "navig frobnicate" for a in d.assumptions), d.assumptions


def test_confirmed_command_is_not_an_assumption():
    idx = _view(_entry("command:navig docker ps", name="navig docker ps", summary="List docker"))
    d = decide("run navig docker ps", index=idx)
    assert d.assumptions == []


def test_decision_is_json_serializable_for_the_card():
    import json

    idx = _view()
    d = decide("", evidence=Evidence(repeated_commands=[("navig x y", 9)]), index=idx)
    assert isinstance(d, Decision)
    json.dumps(
        {
            "verdict": d.verdict,
            "reason": d.reason,
            "plan": d.plan,
            "artifact": None if d.artifact is None else d.artifact.__dict__,
            "assumptions": d.assumptions,
        }
    )
