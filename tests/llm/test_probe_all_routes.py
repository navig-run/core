"""Every configured LLM route is probed, and `dead` must be earned.

Two gaps this closes:

* A **fallback** is exercised only at the moment its primary fails — exactly
  when you cannot afford it to be dead too. Measured on a real install: three
  fallbacks pointed at an ollama that was not running, invisible until the
  primary's credential lapsed.
* The **hybrid routing tiers** were covered by nothing at all. All three of that
  install's pointed at models answering 410 GONE and no surface said so.

And `dead` is the destructive verdict — `dead_modes()` turns it into a `[HIGH]`
heartbeat issue, the gateway turns that into an approval prompt, and it is what
justifies denylisting a model. A bare 404 does not earn it on one call:
NVIDIA answered 404 once for a model that then answered 200 five times running.
"""

from __future__ import annotations

import time

import pytest

from navig.llm import liveness
from navig.llm.liveness import _worth_retrying, probe_model, probe_routes


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _patch_generate(monkeypatch, outcomes):
    calls = {"n": 0}

    def fake(*_a, **_kw):
        calls["n"] += 1
        item = outcomes[min(calls["n"] - 1, len(outcomes) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    import navig.llm.generate as gen

    monkeypatch.setattr(gen, "llm_generate", fake)
    return calls


# ── which verdicts must be confirmed ─────────────────────────────────────


@pytest.mark.parametrize(
    "status,detail,expected",
    [
        ("transient", "provider busy", True),
        ("dead", "model not found (404)", True),
        # 410 carries an explicit end-of-life message and was stable 5/5.
        ("dead", "model retired (410 EOL)", False),
        ("dead", "known-retired model (denylisted)", False),
        ("auth", "auth failed (401)", False),
        # Was False until 2026-09-09: a plain socket TIMEOUT lands in
        # `unreachable`, and 12 probes of a HEALTHY model gave 11x 200 + 1x
        # TimeoutError. A real one is deterministic and survives the retry.
        ("unreachable", "endpoint unreachable / timed out", True),
        ("nokey", "no credential configured", False),
        ("live", "ok", False),
    ],
)
def test_only_unreliable_verdicts_are_retried(status, detail, expected):
    assert _worth_retrying(status, detail) is expected


def test_a_one_off_404_is_not_reported_as_dead(monkeypatch, no_sleep):
    """The measured case: 404 once, then the model answers normally."""
    calls = _patch_generate(
        monkeypatch, [Exception("Client error '404 Not Found'"), "pong"]
    )

    status, _ = probe_model("nvidia", "nvidia/some-live-model")

    assert status == "live", "a transient 404 was reported as a dead model"
    assert calls["n"] == 2


def test_a_real_404_survives_the_retry_and_is_still_dead(monkeypatch, no_sleep):
    """Anti-over-suppression floor: a withdrawn model must still be caught."""
    calls = _patch_generate(monkeypatch, [Exception("Client error '404 Not Found'")])

    status, _ = probe_model("nvidia", "nvidia/withdrawn-model")

    assert status == "dead"
    assert calls["n"] == liveness._TRANSIENT_RETRIES + 1


def test_a_410_is_believed_immediately(monkeypatch, no_sleep):
    """410 is definitive — retrying it just spends a call to learn the same thing."""
    calls = _patch_generate(
        monkeypatch, [Exception("Client error '410 Gone' — reached its end of life")]
    )

    status, _ = probe_model("nvidia", "nvidia/eol-model")

    assert status == "dead"
    assert calls["n"] == 1


# ── route coverage ───────────────────────────────────────────────────────


def test_probe_routes_covers_modes_fallbacks_and_tiers(monkeypatch):
    probed: list[tuple[str, str]] = []

    monkeypatch.setattr(
        liveness,
        "probe_modes",
        lambda **kw: [
            {"mode": "coding", "provider": "openai", "model": "gpt-4o",
             "status": "live", "detail": "ok"}
        ],
    )
    monkeypatch.setattr(liveness, "_configured_fallbacks",
                        lambda: [("coding", "ollama", "qwen2.5:3b")])
    monkeypatch.setattr(liveness, "_configured_tiers",
                        lambda: [("small", "nvidia", "some/model")])
    monkeypatch.setattr(
        liveness, "probe_model",
        lambda p, m, **_kw: (probed.append((p, m)), ("live", "ok"))[1],
    )

    rows = probe_routes()

    assert [r["kind"] for r in rows] == ["mode", "fallback", "tier"]
    assert [r["label"] for r in rows] == ["coding", "coding", "small"]
    assert all({"kind", "label", "provider", "model", "status", "detail"} <= set(r) for r in rows)
    assert probed == [("ollama", "qwen2.5:3b"), ("nvidia", "some/model")]


def test_one_model_reached_by_several_routes_is_probed_once(monkeypatch):
    """A shared cache spans all three kinds, so a model cannot be reported with
    two different statuses in one run (the contradiction fixed in #1335)."""
    probed: list[tuple[str, str]] = []
    shared = ("nvidia", "shared/model")

    monkeypatch.setattr(
        liveness, "probe_modes",
        lambda **kw: [{"mode": "big_tasks", "provider": shared[0], "model": shared[1],
                       "status": "live", "detail": "ok"}],
    )
    monkeypatch.setattr(liveness, "_configured_fallbacks", lambda: [("big_tasks", *shared)])
    monkeypatch.setattr(liveness, "_configured_tiers", lambda: [("big", *shared)])
    monkeypatch.setattr(
        liveness, "probe_model",
        lambda p, m, **_kw: (probed.append((p, m)), ("live", "ok"))[1],
    )

    rows = probe_routes()

    assert len(rows) == 3
    assert probed == [shared], f"probed {len(probed)}x, expected once"
    assert len({r["status"] for r in rows}) == 1


@pytest.mark.parametrize("flag", ["include_fallbacks", "include_tiers"])
def test_each_extra_kind_can_be_switched_off(monkeypatch, flag):
    monkeypatch.setattr(liveness, "probe_modes", lambda **kw: [])
    monkeypatch.setattr(liveness, "_configured_fallbacks", lambda: [("m", "p", "x")])
    monkeypatch.setattr(liveness, "_configured_tiers", lambda: [("t", "p", "y")])
    monkeypatch.setattr(liveness, "probe_model", lambda p, m, **_kw: ("live", "ok"))

    rows = probe_routes(**{flag: False})

    kinds = {r["kind"] for r in rows}
    assert ("fallback" in kinds) is (flag != "include_fallbacks")
    assert ("tier" in kinds) is (flag != "include_tiers")


def test_enumerators_degrade_quietly_when_config_is_unreadable(monkeypatch):
    """A health command must never crash because config is momentarily unreadable."""
    import navig.config as cfgmod

    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(cfgmod, "get_config_manager", boom)
    assert liveness._configured_tiers() == []


# ── a timeout is not "unreachable enough" to alarm on ────────────────────


def test_a_one_off_timeout_is_not_reported_as_unreachable(monkeypatch, no_sleep):
    """`unreachable` IS in dead_modes(), so it becomes a [HIGH] issue and an
    approval prompt — and a plain socket TIMEOUT lands in it.

    Measured 2026-09-09: 12 consecutive probes of a HEALTHY NVIDIA model returned
    11x 200 and 1x TimeoutError. One slow call must not raise an alarm.
    """
    calls = _patch_generate(monkeypatch, [Exception("Read operation timed out"), "pong"])

    status, _ = probe_model("nvidia", "nvidia/some-live-model")

    assert status == "live", "a single timeout was reported as unreachable"
    assert calls["n"] == 2


def test_a_genuinely_unreachable_endpoint_is_still_reported(monkeypatch, no_sleep):
    """Anti-over-suppression floor: ollama that is not running refuses the
    connection BOTH times — which is how dead fallbacks are detected at all."""
    calls = _patch_generate(monkeypatch, [Exception("connection refused econnrefused")])

    status, _ = probe_model("ollama", "qwen2.5:3b-instruct")

    assert status == "unreachable"
    assert calls["n"] == liveness._TRANSIENT_RETRIES + 1
