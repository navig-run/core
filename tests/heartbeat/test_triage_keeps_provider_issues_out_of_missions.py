"""Provider/model findings never raise a remediate mission.

The operator's store: 84 "Remediate health issues" missions, 233 issue lines —
every one an expired key (158), a retired model (75) or an endpoint 503 (2). A
shell agent cannot mint a key or un-retire a model; the router falls back at
request time; the heartbeat alert already told the operator. 80 of 84 ended
"approval denied or timed out". The 3 that ran rewrote the LLM routing unasked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.contracts.store import RuntimeStore
from navig.gateway.server import NavigGateway
from navig.heartbeat.triage import is_provider_condition, triage
from navig.missions.executor import MissionExecutor

PROVIDER = [
    "[HIGH] routing tier 'small' → nvidia:nvidia/nemotron-3.5-lightning-30b-a3b is unreachable (endpoint unreachable / timed out)",
    "[HIGH] LLM mode 'big_tasks' → anthropic:claude-opus-4-8 is auth (auth failed (401 — key/token invalid or expired))",
    "[HIGH] LLM mode 'coding' → nvidia:meta/llama-3.1-70b-instruct is dead (410 EOL)",
    "[MEDIUM] provider openai returned 429 rate limit exceeded",
    "[HIGH] model gpt-x has been retired by the provider",
    "[HIGH] API key for groq is invalid or expired",
]
ACTIONABLE = [
    "[HIGH] host web-1 unreachable (ssh timed out after 2.0s)",
    "[MEDIUM] disk / on web-1 at 91% used",
    "[CRITICAL] service nginx crashed on web-2",
    "[HIGH] certificate for example.com expires in 3 days",
]


@pytest.mark.parametrize("issue", PROVIDER)
def test_provider_conditions_are_informational(issue):
    assert is_provider_condition(issue), issue


@pytest.mark.parametrize("issue", ACTIONABLE)
def test_infrastructure_findings_stay_actionable(issue):
    assert not is_provider_condition(issue), issue


def test_triage_splits_and_drops_blank_lines():
    t = triage([*PROVIDER[:2], "", *ACTIONABLE[:1]])
    assert t.informational == PROVIDER[:2] and t.actionable == ACTIONABLE[:1]


# ── the gateway path ─────────────────────────────────────────────────────────


def _gateway(store: RuntimeStore, monkeypatch) -> NavigGateway:
    ex = MissionExecutor(gateway=SimpleNamespace(config_manager=None), store=store)
    monkeypatch.setattr(ex, "_spawn", lambda mission: None)
    gw = NavigGateway.__new__(NavigGateway)
    gw.config_manager = SimpleNamespace(global_config={"missions": {"autonomous_enabled": True}})
    gw.mission_executor = ex
    return gw


def _remediate(store):
    return [m for m in store.list_missions(limit=500) if m.capability == "remediate"]


async def test_provider_only_findings_raise_no_mission(tmp_path, monkeypatch):
    """Tonight's storm: one 503, one mission, seven prompts. Now: nothing."""
    store = RuntimeStore(tmp_path)
    gw = _gateway(store, monkeypatch)

    await gw._on_heartbeat_issues(PROVIDER)

    assert _remediate(store) == []


async def test_a_mixed_set_raises_a_mission_for_the_actionable_part_only(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    gw = _gateway(store, monkeypatch)

    await gw._on_heartbeat_issues([PROVIDER[0], ACTIONABLE[0]])

    missions = _remediate(store)
    assert len(missions) == 1
    assert missions[0].payload["issues"] == [ACTIONABLE[0]]


async def test_actionable_findings_still_raise(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    gw = _gateway(store, monkeypatch)

    await gw._on_heartbeat_issues(ACTIONABLE)

    assert len(_remediate(store)) == 1
