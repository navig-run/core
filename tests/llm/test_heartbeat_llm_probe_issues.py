"""End-to-end: which probe outcomes become a heartbeat issue.

This is the chain that reaches the operator:

    probe_modes() -> dead_modes() -> HeartbeatResult.issues_found
      -> gateway._on_heartbeat_issues -> a "Remediate health issues" mission
      -> an approval prompt on Telegram

`_maybe_probe_llm_modes` is the hinge and had no test at all. A transient
provider blip travelling this path produces a prompt the operator cannot act on
— they can approve it, and the model was never broken.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from navig.heartbeat.runner import HeartbeatConfig, HeartbeatResult, HeartbeatRunner


def _runner(monkeypatch, probe_rows, tiers=(), heads=()):
    """A real HeartbeatRunner whose probe returns *probe_rows*.

    ⚠ HERMETIC BY CONSTRUCTION. The runner calls `probe_routes`, which enumerates
    the REAL configured tiers and would probe them over the network — measured
    outside pytest, `_configured_tiers()` returns three live NVIDIA models. The
    enumerators are stubbed here so a unit test can never make a provider call,
    and `probe_model` is stubbed as a backstop that FAILS LOUDLY if one is ever
    attempted.
    """
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(global_config={"heartbeat": {"check_llm_modes": True}}),
        event_queue=None,
    )
    runner = HeartbeatRunner(gateway, HeartbeatConfig())

    import navig.llm.liveness as liveness

    monkeypatch.setattr(liveness, "probe_modes", lambda *_a, **_kw: probe_rows)
    monkeypatch.setattr(liveness, "_configured_tiers", lambda: list(tiers))
    monkeypatch.setattr(liveness, "_configured_fallbacks", lambda: [("never", "p", "m")])

    # The catalog-head probe reads the REAL key store to decide what it may
    # probe, so leaving it live would make this test's behaviour depend on which
    # providers this machine happens to hold a key for. Stubbed by default;
    # the tests that exercise it pass `heads=`.
    monkeypatch.setattr(liveness, "probe_catalog_heads", lambda **_kw: list(heads))

    def _no_network(provider, model, **_kw):
        raise AssertionError(f"probed {provider}:{model} — this test is not hermetic")

    monkeypatch.setattr(liveness, "probe_model", _no_network)
    return runner


def _result() -> HeartbeatResult:
    return HeartbeatResult(
        success=True,
        response="HEARTBEAT_OK",
        duration_seconds=0.1,
        timestamp=datetime.now(),
        suppressed=True,
    )


def _row(mode: str, status: str) -> dict:
    return {
        "mode": mode,
        "provider": "nvidia",
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "status": status,
        "detail": f"{status} detail",
    }


async def test_a_busy_provider_raises_no_heartbeat_issue(monkeypatch):
    """The regression: a 503/429/504 must not reach the operator as [HIGH]."""
    runner = _runner(monkeypatch, [_row("research", "transient")])
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert not result.issues_found, "a busy provider became a health issue"
    assert result.suppressed, "a busy provider un-suppressed an otherwise-OK beat"


@pytest.mark.parametrize("status", ["dead", "auth", "unreachable"])
async def test_an_actionable_failure_still_raises_an_issue(monkeypatch, status):
    """Anti-over-suppression floor — the signal this path exists for."""
    runner = _runner(monkeypatch, [_row("research", status)])
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert result.issues_found, f"{status} should reach the operator"
    assert len(result.issues_found) == 1
    assert "research" in result.issues_found[0]
    assert not result.suppressed, f"{status} must un-suppress the beat so it notifies"


async def test_a_mixed_beat_reports_only_the_actionable_one(monkeypatch):
    runner = _runner(
        monkeypatch,
        [_row("research", "transient"), _row("coding", "dead"), _row("summarize", "live")],
    )
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert len(result.issues_found) == 1
    assert "coding" in result.issues_found[0]


async def test_the_probe_is_throttled_to_once_a_day(monkeypatch):
    """Each probe costs a real call per mode; the daily gate must hold."""
    calls = {"n": 0}

    def counting_probe(*_a, **_kw):
        calls["n"] += 1
        return [_row("research", "dead")]

    import navig.llm.liveness as liveness

    runner = _runner(monkeypatch, [])
    monkeypatch.setattr(liveness, "probe_modes", counting_probe)

    await runner._maybe_probe_llm_modes(_result())
    await runner._maybe_probe_llm_modes(_result())

    assert calls["n"] == 1, "probed twice in one day"


async def test_the_probe_can_be_switched_off(monkeypatch):
    runner = _runner(monkeypatch, [_row("research", "dead")])
    runner.gateway.config_manager.global_config["heartbeat"]["check_llm_modes"] = False
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert not result.issues_found


# ── routing tiers reach the operator too ─────────────────────────────────
#
# `navig mode doctor` (PULL) saw all 13 routes; the heartbeat (PUSH) saw only the
# five primaries. All three of one install's routing tiers answered 410 GONE for
# weeks and nothing told the operator, because the only surface that could see
# them had to be run by hand.


def _tier_row(name: str, status: str) -> dict:
    return {
        "kind": "tier",
        "label": name,
        "provider": "nvidia",
        "model": "some/retired-model",
        "status": status,
        "detail": f"{status} detail",
    }


async def test_a_dead_routing_tier_reaches_the_operator(monkeypatch):
    runner = _runner(monkeypatch, [])
    result = _result()
    monkeypatch.setattr(
        "navig.llm.liveness.probe_routes", lambda **_kw: [_tier_row("coder_big", "dead")]
    )

    await runner._maybe_probe_llm_modes(result)

    assert result.issues_found, "a dead routing tier was invisible to the heartbeat"
    assert not result.suppressed, "a dead tier must un-suppress the beat so it notifies"


async def test_a_tier_is_told_to_use_the_TIER_command(monkeypatch):
    """`navig mode set small …` would be wrong twice: wrong command, and "small"
    is not a mode name — the mode-set guard rejects it. Advice that does not work
    is the phantom-hint class this repo already gates."""
    runner = _runner(monkeypatch, [])
    result = _result()
    monkeypatch.setattr(
        "navig.llm.liveness.probe_routes", lambda **_kw: [_tier_row("coder_big", "dead")]
    )

    await runner._maybe_probe_llm_modes(result)

    issue = result.issues_found[0]
    assert "navig mode route set coder_big" in issue
    assert "navig mode set coder_big" not in issue
    assert "routing tier" in issue and "LLM mode" not in issue


async def test_a_mode_still_gets_the_MODE_command(monkeypatch):
    """The other half of the same branch — it must not regress into the tier form."""
    runner = _runner(monkeypatch, [])
    result = _result()
    monkeypatch.setattr(
        "navig.llm.liveness.probe_routes",
        lambda **_kw: [{**_row("coding", "dead"), "kind": "mode", "label": "coding"}],
    )

    await runner._maybe_probe_llm_modes(result)

    issue = result.issues_found[0]
    assert "navig mode set coding" in issue
    assert "mode route set" not in issue


async def test_the_heartbeat_does_not_alert_on_fallbacks(monkeypatch):
    """A dead fallback breaks nothing today and would fire on every machine that
    simply does not run ollama. Same split `navig mode doctor` uses for its exit
    code, so the pull and push surfaces cannot disagree about what is broken."""
    seen: dict = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("navig.llm.liveness.probe_routes", capture)
    runner = _runner(monkeypatch, [])

    await runner._maybe_probe_llm_modes(_result())

    assert seen.get("include_fallbacks") is False
    assert seen.get("include_tiers") is True


async def test_a_row_without_a_kind_still_works(monkeypatch):
    """Back-compat: `probe_modes` rows carry `mode`, not `kind`/`label`. Reading
    the wrong key would KeyError inside the daemon's own alarm."""
    runner = _runner(monkeypatch, [])
    result = _result()
    legacy = {"mode": "research", "provider": "nvidia", "model": "m",
              "status": "dead", "detail": "gone"}
    monkeypatch.setattr("navig.llm.liveness.probe_routes", lambda **_kw: [legacy])

    await runner._maybe_probe_llm_modes(result)

    assert "LLM mode 'research'" in result.issues_found[0]


# ── the catalog HEAD: models[0] breaks while every route stays green ────────


def _head(provider: str, status: str) -> dict:
    return {"kind": "catalog", "label": provider, "provider": provider,
            "model": "prov/first-model", "where": "both",
            "status": status, "detail": f"{status} detail"}


async def test_a_retired_catalog_head_reaches_the_operator(monkeypatch):
    """The gap this closes: `models[0]` is the credential probe AND the routing
    substitution default, so it can die while every CONFIGURED route answers —
    and the route probe, by construction, sees nothing."""
    runner = _runner(monkeypatch, [], heads=[_head("groq", "dead")])
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert result.suppressed is False, "a beat must not stay quiet about this"
    assert len(result.issues_found) == 1
    issue = result.issues_found[0]
    assert "[HIGH]" in issue and "groq's first catalog model" in issue
    # The advice must be the sweep, not a route command: there is no route to
    # re-point — the catalog itself is wrong.
    assert "navig ai models --check --provider groq" in issue
    assert "navig mode set" not in issue


@pytest.mark.parametrize("status", ["live", "auth", "unreachable", "slow", "transient"])
async def test_only_a_retired_head_is_reported(monkeypatch, status):
    """A bad key or a slow endpoint is a different fact, and the route rows
    already carry it. Reporting it twice would page the operator for a
    credential problem under a heading about the catalog."""
    runner = _runner(monkeypatch, [], heads=[_head("groq", status)])
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert not result.issues_found
    assert result.suppressed is True


async def test_a_head_probe_failure_never_breaks_the_beat(monkeypatch):
    import navig.llm.liveness as liveness

    runner = _runner(monkeypatch, [])
    result = _result()

    def _boom(**_kw):
        raise RuntimeError("key store unreadable")

    monkeypatch.setattr(liveness, "probe_catalog_heads", _boom)

    await runner._maybe_probe_llm_modes(result)  # must not raise

    assert not result.issues_found


async def test_routes_and_heads_share_one_probe_cache(monkeypatch):
    """A model reached both ways must cost ONE call and cannot be reported with
    two statuses — the rule `probe_modes` already learned."""
    import navig.llm.liveness as liveness

    runner = _runner(monkeypatch, [])
    seen: dict[str, object] = {}
    monkeypatch.setattr(liveness, "probe_routes",
                        lambda **kw: (seen.__setitem__("routes", kw.get("probed_cache")), [])[1])
    monkeypatch.setattr(liveness, "probe_catalog_heads",
                        lambda **kw: (seen.__setitem__("heads", kw.get("probed_cache")), [])[1])

    await runner._maybe_probe_llm_modes(_result())

    assert seen["routes"] is not None
    assert seen["heads"] is seen["routes"], "the two probes used separate caches"
