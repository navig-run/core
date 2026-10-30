"""The sustained rate-limit watch — a week-long 429 storm must reach the operator.

Measured incident (2026-10): ``anthropic:claude-opus-4-8`` was rate-limited for
a WEEK. Every chat turn silently fast-retried onto the small model, the
fallback ledger stayed empty (the fast-retry is by design not a fallback
switch), and no notification ever fired — the 429 is classified ``transient``,
which `dead_modes()` deliberately never reports. The operator found out by
reading their provider's usage page.

These tests pin the whole chain:

    probe / real call 429 -> note_rate_limited -> sustained() -> [MEDIUM] issue
"""

from __future__ import annotations

import tempfile

import pytest

from navig.llm import rate_limit_watch as watch

HOUR = 3600.0
BASE = 1_000_000.0


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Per-test config dir AND a clean in-process cache. The watch persists to
    ``<config_dir>/perf/llm_rate_limit.json`` — without the env, a test would
    write the operator's real store (the ad-hoc-probe incident class).

    The clock is pinned just after the shared BASE storm (which otherwise lives
    in 1970 and would be filtered as stale by the currency check); a test that
    needs another moment re-patches ``time.time`` itself."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", tempfile.mkdtemp(prefix="navig_rlw_"))
    monkeypatch.setattr("time.time", lambda: BASE + 7 * HOUR)
    watch.reset_for_tests()
    yield
    watch.reset_for_tests()


# ── the watcher's own semantics ──────────────────────────────────────────


def test_five_events_over_hours_are_sustained():
    for i in range(7):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)

    rows = watch.sustained()

    assert len(rows) == 1
    assert rows[0]["provider"] == "anthropic"
    assert rows[0]["model"] == "claude-opus-4-8"
    assert rows[0]["count"] == 7


def test_five_events_within_minutes_are_a_blip_not_a_storm():
    """Both bars are required: count alone would fire on a retry loop."""
    for i in range(5):
        watch.note_rate_limited("xai", "grok-4.6", now=BASE + i * 60.0)

    assert watch.sustained() == [], "five 429s a minute apart paged the operator"


def test_fewer_events_than_the_bar_never_report():
    for i in range(4):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)

    assert watch.sustained() == []


def test_a_one_off_success_does_not_clear_the_storm():
    """The property a clear-on-success design got wrong: a capped account
    answers occasional small requests (measured mid-week). There IS no success
    hook — the storm only ages out when the 429s stop."""
    for i in range(7):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)

    # ...a real request squeaks through the cap — nothing to call, by design...
    rows = watch.sustained()
    assert len(rows) == 1 and rows[0]["count"] == 7


def test_a_storm_that_aged_out_is_not_reported():
    """A cap that ENDED stops producing 429s; its entry goes stale."""
    for i in range(7):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)

    rows = watch.sustained(now=BASE + 31 * HOUR)

    assert rows == [], "a cap that ended a day ago is still alarming"


def test_a_new_storm_after_expiry_starts_at_one():
    for i in range(7):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)
    watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + 31 * HOUR)

    assert not watch.sustained(now=BASE + 30 * HOUR)
    import navig.platform.paths as paths
    from navig.core.json_io import load_json_for_update

    data = load_json_for_update(paths.config_dir() / "perf" / watch.FILE_NAME, default={})
    entry = data["anthropic:claude-opus-4-8"]
    assert entry["count"] == 1, f"the old storm leaked into the new one: {entry}"
    assert entry["first_seen"] == BASE + 31 * HOUR


def test_empty_provider_or_model_is_ignored():
    watch.note_rate_limited("", "m", now=BASE)
    watch.note_rate_limited("p", "", now=BASE)

    assert watch._load_cached() == {}


# ── the writers: probe + real call ───────────────────────────────────────


def test_a_probe_429_counts_toward_the_watch(monkeypatch):
    import navig.llm.generate as gen
    import navig.llm.liveness as liveness

    def boom(*_a, **_kw):
        raise Exception("Client error '429 Too Many Requests'")

    monkeypatch.setattr(gen, "llm_generate", boom)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    status, detail = liveness.probe_model("anthropic", "claude-opus-4-8")

    assert status == "transient"
    assert "429" in detail, "the 429 detail is what the watch keys on"
    rows = watch.sustained(min_events=1, min_span_s=0)
    assert len(rows) == 1 and rows[0]["provider"] == "anthropic"


def test_a_probe_5xx_does_not_count(monkeypatch):
    """A 503 is the provider failing, NOT a quota — the watch must not count it."""
    import navig.llm.generate as gen
    import navig.llm.liveness as liveness

    def boom(*_a, **_kw):
        raise Exception("Server error '503 Service Unavailable'")

    monkeypatch.setattr(gen, "llm_generate", boom)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    status, _detail = liveness.probe_model("nvidia", "m")

    assert status == "transient"
    assert watch._load_cached() == {}


def test_a_real_call_429_counts():
    from navig.llm import model_fallback

    model_fallback.note_primary_failed("anthropic", "claude-opus-4-8", "rate_limited")

    rows = watch.sustained(min_events=1, min_span_s=0)
    assert len(rows) == 1


def test_a_real_call_auth_does_not_count():
    from navig.llm import model_fallback

    model_fallback.note_primary_failed("anthropic", "claude-opus-4-8", "auth")

    assert watch._load_cached() == {}


# ── the reader: the heartbeat ─────────────────────────────────────────────


def _runner(monkeypatch):
    """The hermetic harness from test_heartbeat_llm_probe_issues: every network
    touchpoint stubbed, `probe_model` failing LOUDLY if ever reached."""
    from datetime import datetime
    from types import SimpleNamespace

    from navig.heartbeat.runner import (
        HeartbeatConfig,
        HeartbeatResult,
        HeartbeatRunner,
    )

    # The config dir comes from the fixture — the heartbeat must read the SAME
    # perf store the storm above was written to.
    watch.reset_for_tests()
    gateway = SimpleNamespace(
        config_manager=SimpleNamespace(
            global_config={
                "heartbeat": {"check_llm_modes": True},
                # AI intent — keeps this harness past the heartbeat's no-AI gate.
                "ai": {"default_provider": "nvidia"},
            }
        ),
        event_queue=None,
    )
    runner = HeartbeatRunner(gateway, HeartbeatConfig())

    import navig.agent.openrouter_prices as orp
    import navig.llm.liveness as liveness

    monkeypatch.setattr(liveness, "probe_modes", lambda *_a, **_kw: [])
    monkeypatch.setattr(liveness, "_configured_tiers", lambda: [])
    monkeypatch.setattr(liveness, "_configured_fallbacks", lambda: [])
    monkeypatch.setattr(liveness, "probe_catalog_heads", lambda **_kw: [])

    def _no_network(provider, model, **_kw):
        raise AssertionError(f"probed {provider}:{model} — this test is not hermetic")

    monkeypatch.setattr(liveness, "probe_model", _no_network)
    monkeypatch.setattr(orp, "refresh", lambda **_kw: 0)

    result = HeartbeatResult(success=True, response="HEARTBEAT_OK",
                             duration_seconds=0.1, timestamp=datetime.now(),
                             suppressed=True)
    return runner, result


async def test_a_sustained_storm_reaches_the_operator(monkeypatch):
    """The incident, end to end: probes stay green (429 = transient, no HIGH),
    the watch says sustained, and the beat un-suppresses with a [MEDIUM]."""
    for i in range(7):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)

    runner, result = _runner(monkeypatch)

    await runner._maybe_probe_llm_modes(result)

    assert result.issues_found, "a week-long cap raised nothing"
    issue = result.issues_found[0]
    assert "[MEDIUM]" in issue
    assert "anthropic:claude-opus-4-8" in issue
    assert "rate-limited" in issue
    assert "navig mode set" in issue, "the fix advice must be actionable"
    assert result.suppressed is False, "the beat must notify"


async def test_a_blip_stays_silent(monkeypatch):
    """Anti-over-suppression floor, the other direction: five 429s in a minute
    must not page the operator (still CURRENT — only the span bar excludes it)."""
    for i in range(5):
        watch.note_rate_limited("xai", "grok-4.6", now=BASE + i * 60.0)

    runner, result = _runner(monkeypatch)
    monkeypatch.setattr("time.time", lambda: BASE + 6 * 60.0)

    await runner._maybe_probe_llm_modes(result)

    assert not result.issues_found
    assert result.suppressed is True


async def test_a_stale_storm_stays_silent(monkeypatch):
    for i in range(7):
        watch.note_rate_limited("anthropic", "claude-opus-4-8", now=BASE + i * HOUR)

    runner, result = _runner(monkeypatch)
    monkeypatch.setattr("time.time", lambda: BASE + 31 * HOUR)

    await runner._maybe_probe_llm_modes(result)

    assert not result.issues_found