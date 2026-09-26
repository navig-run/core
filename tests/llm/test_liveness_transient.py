"""A busy provider is not a broken model.

`dead_modes()` feeds the heartbeat, the heartbeat's issues become a `[HIGH]`
health finding, and the gateway turns that into an operator approval prompt. So
anything misclassified here manufactures a prompt nobody can act on.

Measured against the live provider before this fix: NVIDIA returned
``503 Service Unavailable`` for a model that read ``live`` on another row of the
SAME run and live again on the next — and `navig mode doctor` rendered it
``✗ error`` and exited 1. Worse, ``504 Gateway Timeout`` contains "timeout", so
it was classified ``unreachable``, which IS in `dead_modes()` — one blip
upstream and the operator got an approval prompt about a healthy model.
"""

from __future__ import annotations

import time

import pytest

from navig.llm import liveness
from navig.llm.liveness import classify_probe_error, dead_modes, probe_model

TRANSIENT_MESSAGES = [
    "Server error '503 Service Unavailable' for url 'https://integrate.api.nvidia.com/v1'",
    "Client error '429 Too Many Requests' for url 'https://integrate.api.nvidia.com/v1'",
    "Server error '502 Bad Gateway' for url 'https://x/v1'",
    "Server error '504 Gateway Timeout' for url 'https://x/v1'",
    "rate limit exceeded, please retry",
    "overloaded_error: Overloaded",
]

# Failures that mean the operator really must change something.
REAL_FAILURES = [
    ("Client error '410 Gone' — model reached its end of life", "dead"),
    ("Client error '404 Not Found'", "dead"),
    ("Client error '401 Unauthorized'", "auth"),
    ("Client error '403 Forbidden'", "auth"),
    ("connection refused econnrefused", "unreachable"),
    ("no api key configured for provider", "nokey"),
]


@pytest.mark.parametrize("message", TRANSIENT_MESSAGES)
def test_provider_busy_is_classified_transient(message):
    status, detail = classify_probe_error(Exception(message))
    assert status == "transient", f"{message!r} -> {status}"
    assert detail, "a status must carry an operator-readable detail"


def test_gateway_timeout_is_not_unreachable():
    """The ordering regression, pinned on its own because it is the expensive one.

    "504 Gateway Timeout" contains "timeout". If the transient check moves below
    the unreachable check, this silently becomes a HIGH heartbeat issue again.
    """
    status, _ = classify_probe_error(Exception("Server error '504 Gateway Timeout'"))
    assert status == "transient"


@pytest.mark.parametrize("message,expected", REAL_FAILURES)
def test_real_failures_keep_their_status(message, expected):
    """Anti-over-suppression floor: widening 'transient' must not swallow these."""
    status, _ = classify_probe_error(Exception(message))
    assert status == expected, f"{message!r} -> {status}, expected {expected}"


@pytest.mark.parametrize("message", TRANSIENT_MESSAGES)
def test_transient_never_becomes_a_heartbeat_issue(message):
    status, _ = classify_probe_error(Exception(message))
    assert not dead_modes([{"status": status}]), (
        "a busy provider would become a [HIGH] health issue and an approval prompt"
    )


@pytest.mark.parametrize("message,expected", REAL_FAILURES)
def test_actionable_failures_still_reach_the_heartbeat(message, expected):
    """The signal this whole path exists for must survive."""
    status, _ = classify_probe_error(Exception(message))
    reported = bool(dead_modes([{"status": status}]))
    assert reported == (expected in ("dead", "auth", "unreachable")), (
        f"{expected} reported to heartbeat: {reported}"
    )


# ── probe_model retry ────────────────────────────────────────────────────


@pytest.fixture
def no_sleep(monkeypatch):
    """Keep the retry delay out of the test runtime."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _patch_generate(monkeypatch, side_effect):
    """Patch the SOURCE module — probe_model imports it inside the function."""
    calls = {"n": 0}

    def fake(*_a, **_kw):
        calls["n"] += 1
        outcome = side_effect[min(calls["n"] - 1, len(side_effect) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    import navig.llm.generate as gen

    monkeypatch.setattr(gen, "llm_generate", fake)
    return calls


def test_a_transient_blip_is_retried_and_reports_live(monkeypatch, no_sleep):
    """The whole point: one blip on a rate-limited free tier is not a red row."""
    calls = _patch_generate(
        monkeypatch, [Exception("Server error '503 Service Unavailable'"), "pong"]
    )

    status, detail = probe_model("nvidia", "some/live-model")

    assert (status, detail) == ("live", "ok")
    assert calls["n"] == 2, "expected exactly one retry"


def test_a_persistent_transient_failure_is_reported_not_hidden(monkeypatch, no_sleep):
    """Retrying must not turn a provider that is genuinely down into silence."""
    calls = _patch_generate(monkeypatch, [Exception("Client error '429 Too Many Requests'")])

    status, _ = probe_model("nvidia", "some/live-model")

    assert status == "transient"
    assert calls["n"] == liveness._TRANSIENT_RETRIES + 1


@pytest.mark.parametrize("message,expected", REAL_FAILURES)
def test_a_real_failure_keeps_its_status(monkeypatch, no_sleep, message, expected):
    """Whatever the retry policy, the verdict itself must not change."""
    _patch_generate(monkeypatch, [Exception(message)])

    status, _ = probe_model("nvidia", "some/live-model")

    assert status == expected


@pytest.mark.parametrize(
    "message,expected",
    [(m, e) for m, e in REAL_FAILURES if "404" not in m and e != "unreachable"],
)
def test_a_definitive_failure_is_not_retried(monkeypatch, no_sleep, message, expected):
    """Retrying a 410 or a 401 just spends a call to learn the same thing.

    ⚠ 404 and `unreachable` are deliberately EXCLUDED here and covered in
    `test_probe_all_routes.py` instead. It used to be in this list, because a
    404 looked as definitive as a 410 — then NVIDIA answered 404 once for a
    model that immediately afterwards answered 200 five times running. `dead` is
    the destructive verdict (a `[HIGH]` heartbeat issue, an approval prompt, and
    the justification for denylisting a model), so it now has to be earned twice.

    `unreachable` joined it on 2026-09-09 for the same reason one layer over: a
    plain socket TIMEOUT lands there, it is ALSO in `dead_modes()`, and 12 probes
    of a healthy model gave 11x 200 and 1x TimeoutError.
    """
    calls = _patch_generate(monkeypatch, [Exception(message)])

    status, _ = probe_model("nvidia", "some/live-model")

    assert status == expected
    assert calls["n"] == 1, f"{expected} should not be retried"


def test_a_retired_model_still_short_circuits_without_a_call(monkeypatch, no_sleep):
    calls = _patch_generate(monkeypatch, ["pong"])
    # Entries are "provider:model" — take one that is retired on NVIDIA, since
    # that is the provider probed below. Picking any entry would silently test
    # nothing once the denylist became provider-scoped.
    retired = next(iter(liveness.retired_model_ids("nvidia")))

    status, _ = probe_model("nvidia", retired)

    assert status == "dead"
    assert calls["n"] == 0, "a denylisted model must not spend a call"


# ── probe_modes de-duplication ───────────────────────────────────────────


def test_one_model_shared_by_several_modes_is_probed_once(monkeypatch):
    """Probing the same model per-mode rate-limits US and contradicts itself.

    Observed live: `research` and `summarize` both routed to one NVIDIA model,
    the two back-to-back calls made the second return 503, and one table
    reported the identical model as both "● live" and "↻ busy".
    """
    routes = {
        "research": ("nvidia", "shared/model"),
        "summarize": ("nvidia", "shared/model"),
        "coding": ("xai", "other/model"),
    }
    calls: list[tuple[str, str]] = []

    import navig.llm.router as router

    monkeypatch.setattr(
        router,
        "resolve_llm",
        lambda mode=None, **_kw: type(
            "Cfg", (), {"provider": routes[mode][0], "model": routes[mode][1]}
        )(),
    )
    monkeypatch.setattr(
        liveness,
        "probe_model",
        lambda p, m, **_kw: (calls.append((p, m)), ("live", "ok"))[1],
    )

    rows = liveness.probe_modes(list(routes))

    assert len(rows) == 3, "every mode must still get a row"
    assert calls == [("nvidia", "shared/model"), ("xai", "other/model")]
    shared = [r["status"] for r in rows if r["model"] == "shared/model"]
    assert len(set(shared)) == 1, "one model reported with two different statuses"


# ── the two taxonomies must agree on what a 5xx means ────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Server error '500 Internal Server Error'",
        "Server error '502 Bad Gateway'",
        "Server error '503 Service Unavailable'",
        "Server error '504 Gateway Timeout'",
        # Widened 2026-09-15 from 5xx only: the same drift can happen on any
        # transient shape, and these two are the ones a rate-limited free tier
        # produces most.
        "Client error '429 Too Many Requests'",
        "rate limit exceeded",
        "Read operation timed out",
        "connection timeout",
    ],
)
def test_a_transient_is_non_fatal_in_both_taxonomies(message):
    """`classify_probe_error` (liveness) and `parse_test_connection_error`
    (connect) are separate vocabularies that grew separately — "500" was in one
    and not the other until 2026-09-15. A 5xx is the PROVIDER failing, never the
    model or the credential, so neither path may treat it as a config defect."""
    from navig.providers.connection_types import HealthState
    from navig.providers.drivers.native import parse_test_connection_error

    live_status, _ = classify_probe_error(Exception(message))
    conn_health, _ = parse_test_connection_error(message)

    # `slow` joined the set 2026-09-26: a READ timeout means the endpoint
    # answered and the model ran long, which is less of a config defect than
    # `unreachable`, not more. The property this test guards — "neither path
    # calls a transient shape a config defect" — holds for all three.
    assert live_status in ("transient", "unreachable", "slow"), (
        f"liveness treats {message!r} as {live_status}"
    )
    assert conn_health != HealthState.INVALID.value, (
        f"connect treats {message!r} as a bad credential"
    )
