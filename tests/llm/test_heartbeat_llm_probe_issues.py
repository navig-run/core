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
    import tempfile

    # Per-test config dir: the probe day is persisted, so a shared session-wide
    # dir would let one test's probe make the next one skip.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", tempfile.mkdtemp(prefix="navig_hb_probe_"))
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(
            global_config={
                "heartbeat": {"check_llm_modes": True},
                # AI intent: without a declared provider the no-AI gate skips the
                # probe entirely (tested below — `test_a_box_with_no_ai_*`).
                "ai": {"default_provider": "nvidia"},
            }
        ),
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
    # The daily beat also refreshes OpenRouter's price list — a real GET plus a
    # cache write. Stubbed: this harness must never touch the network.
    import navig.agent.openrouter_prices as orp

    monkeypatch.setattr(orp, "refresh", lambda **_kw: 0)

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


async def test_a_rejected_key_names_the_reconnect_not_a_mode_change(monkeypatch):
    """A 401 means the CREDENTIAL is broken, not the model: re-pointing the mode
    (the old advice) discards a working route; the fix is to sign in again."""
    monkeypatch.setattr("navig.providers.inference.reconnect_hint",
                        lambda p: "navig connect login claude-max")
    row = {**_row("coding", "auth"), "provider": "anthropic", "model": "claude-sonnet-4-6"}
    runner = _runner(monkeypatch, [row, _row("research", "dead")])
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    by_mode = {("coding" if "'coding'" in i else "research"): i for i in result.issues_found}
    assert by_mode["coding"].endswith("Fix: navig connect login claude-max")
    assert "navig mode set research" in by_mode["research"]  # a dead MODEL still gets re-pointed


async def test_a_restart_does_not_re_probe_the_same_day(monkeypatch):
    """Measured ~19 gateway restarts in 24h, each re-running every probe call:
    the once-a-day contract must survive a NEW runner (a restarted process)."""
    calls = {"n": 0}

    def counting_probe(*_a, **_kw):
        calls["n"] += 1
        # What the real probe does: each verdict persists in model_health. That is
        # what makes a same-day re-probe after a restart pointless.
        from navig.llm import model_health

        model_health.record("nvidia", "nvidia/nemotron-3-super-120b-a12b", "live", "ok")
        return [_row("research", "live")]

    import navig.llm.liveness as liveness

    first = _runner(monkeypatch, [])
    monkeypatch.setattr(liveness, "probe_modes", counting_probe)
    await first._maybe_probe_llm_modes(_result())

    from navig.heartbeat.runner import HeartbeatConfig, HeartbeatRunner

    restarted = HeartbeatRunner(first.gateway, HeartbeatConfig())   # same config dir
    await restarted._maybe_probe_llm_modes(_result())
    assert calls["n"] == 1, "a restart re-probed on the same day"


async def test_a_failed_probe_does_not_mark_the_day_done(monkeypatch):
    import navig.llm.liveness as liveness
    from navig.heartbeat import runner as runner_mod

    r = _runner(monkeypatch, [])

    def broken(*_a, **_kw):
        raise RuntimeError("router unavailable")

    monkeypatch.setattr(liveness, "probe_modes", broken)
    await r._maybe_probe_llm_modes(_result())
    assert runner_mod._read_probe_day() is None   # the next beat/restart must retry


# ── a wiped health cache is re-seeded the same day ───────────────────────────


async def test_an_empty_health_cache_is_reprobed_despite_todays_marker(monkeypatch):
    """The daily throttle assumed verdicts persist. A deleted model_health file left
    the runtime fallback with no healthy stand-in until TOMORROW — re-probe once now."""
    from datetime import date

    import navig.llm.liveness as liveness
    from navig.heartbeat import runner as runner_mod

    calls = {"n": 0}

    def counting_probe(*_a, **_kw):
        calls["n"] += 1
        return []

    runner = _runner(monkeypatch, [])
    monkeypatch.setattr(liveness, "probe_modes", counting_probe)
    runner_mod._write_probe_day(date.today().isoformat())  # "already probed today"

    await runner._maybe_probe_llm_modes(_result())
    await runner._maybe_probe_llm_modes(_result())

    assert calls["n"] == 1, "a wiped cache must be re-probed exactly once per process"


async def test_a_populated_health_cache_keeps_the_daily_throttle(monkeypatch):
    from datetime import date

    import navig.llm.liveness as liveness
    from navig.heartbeat import runner as runner_mod
    from navig.llm import model_health

    calls = {"n": 0}

    def counting_probe(*_a, **_kw):
        calls["n"] += 1
        return []

    runner = _runner(monkeypatch, [])
    monkeypatch.setattr(liveness, "probe_modes", counting_probe)
    runner_mod._write_probe_day(date.today().isoformat())
    model_health.record("nvidia", "nvidia/nemotron-3-super-120b-a12b", "live", "ok")

    await runner._maybe_probe_llm_modes(_result())

    assert calls["n"] == 0


# ── a box that never opted into AI is not a health problem ───────────────────
#
# Measured 2026-10-17, fresh Ubuntu server install (`server` profile: no brain,
# no keys): every canonical mode resolved to the SHIPPED default, ollama, and
# the daily probe turned that into "Health check: 5 problems" a day — five
# "unreachable" rows on a machine where unreachable is simply the truth.


def _drop_ai_intent(runner) -> None:
    """Strip the ai-intent key the shared fixture carries, leaving a no-AI box."""
    runner.gateway.config_manager.global_config.pop("ai", None)


def _stub_detection(monkeypatch, provider: str, redetect: str | None = None) -> None:
    """Pin AIClient provider detection: *provider* now, *redetect* after re-detect."""
    from types import SimpleNamespace

    import navig.agent.ai_client as ai_client

    client = SimpleNamespace(
        provider=provider,
        re_detect_provider=lambda: provider if redetect is None else redetect,
    )
    monkeypatch.setattr(ai_client, "get_ai_client", lambda: client)


async def test_a_box_with_no_ai_probes_nothing(monkeypatch):
    """The regression: a no-brain server must not alarm about its own defaults."""
    runner = _runner(monkeypatch, [_row("research", "unreachable")])
    _drop_ai_intent(runner)
    _stub_detection(monkeypatch, "none")

    import navig.llm.liveness as liveness

    def _probed(**_kw):
        raise AssertionError("probed LLM modes on a box with no AI configured")

    monkeypatch.setattr(liveness, "probe_routes", _probed)
    result = _result()

    await runner._maybe_probe_llm_modes(result)
    await runner._maybe_probe_llm_modes(result)  # and every following beat again

    assert not result.issues_found, "a no-AI box raised a health issue"
    assert result.suppressed, "the beat must stay quiet"
    from navig.heartbeat import runner as runner_mod

    assert runner_mod._read_probe_day() is None, (
        "the skip must not burn the day marker — the first beat after AI is "
        "configured has to probe the same day"
    )


async def test_the_first_beat_after_ai_appears_probes_the_same_day(monkeypatch):
    runner = _runner(monkeypatch, [])
    _drop_ai_intent(runner)
    _stub_detection(monkeypatch, "none")

    import navig.llm.liveness as liveness

    calls = {"n": 0}

    def _counting(**_kw):
        calls["n"] += 1
        return []

    monkeypatch.setattr(liveness, "probe_routes", _counting)

    await runner._maybe_probe_llm_modes(_result())
    assert calls["n"] == 0, "probed before any AI existed"

    # `navig connect` equivalent: detection now finds a provider.
    _stub_detection(monkeypatch, "none", redetect="openrouter")
    await runner._maybe_probe_llm_modes(_result())
    assert calls["n"] == 1, "the first beat after AI appeared must probe"


async def test_an_unreadable_ai_check_never_silences_the_probe(monkeypatch):
    """Fail OPEN: an unreadable config must not become 'no alarms ever'."""
    runner = _runner(monkeypatch, [_row("research", "dead")])
    _drop_ai_intent(runner)

    import navig.agent.ai_client as ai_client

    def _boom():
        raise RuntimeError("credential store unreadable")

    monkeypatch.setattr(ai_client, "get_ai_client", _boom)
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert result.issues_found, "an unreadable AI check silenced a dead-model alarm"


async def test_declared_config_intent_skips_detection(monkeypatch):
    """A configured box must probe even when detection itself would say none
    (ollama configured but momentarily down is exactly what must be alerted)."""
    runner = _runner(monkeypatch, [_row("research", "unreachable")])
    runner.gateway.config_manager.global_config["ai"] = {"default_provider": "ollama"}

    import navig.agent.ai_client as ai_client

    def _must_not_run():
        raise AssertionError("detection ran despite declared config intent")

    monkeypatch.setattr(ai_client, "get_ai_client", _must_not_run)
    result = _result()

    await runner._maybe_probe_llm_modes(result)

    assert result.issues_found, "a declared provider's unreachable probe was dropped"
