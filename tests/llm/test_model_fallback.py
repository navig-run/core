"""Model fallback policy, ledger, notification and recovery (navig.llm.model_fallback).

The operator's case: ``big_tasks`` (Claude Opus) and ``coding`` (Claude Sonnet)
answered 401, Ollama — the default mode fallback — was not running, and
``agent.fallback_chain`` was unset, so a rejected key had NOTHING behind it while
other keyed models were answering. These tests pin the four promises:

* a healthy model from the liveness cache stands in (policy respected),
* every switch is written down once, and the operator is told — deduped,
* recovery is noticed once and lifts the cooldown,
* the CLI, the deck route and ``navig doctor`` show and manage all of it.

No real provider is ever called: dispatch seams are faked, notifications go to
a capture sink, config/data are per-test temp dirs.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from navig.llm import fallback_policy as fp
from navig.llm import generate as g
from navig.llm import model_fallback as mf
from navig.llm import model_health as mh
from navig.llm.router import ResolvedLLMConfig
from navig.llm.types import LLMResult, ModelSelection

NEMO = "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b"
OPUS = "anthropic:claude-opus-4-8"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Per-test config/data dirs, fresh singletons, a notification capture sink."""
    import navig.llm.router as router_mod
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(router_mod, "_router_instance", None)
    mh.reset_for_tests()
    mf.reset_for_tests()
    fp.reset_cooldowns()
    sent: list[tuple[str, str, str]] = []
    mf._sink = lambda t, title, body: sent.append((t, title, body))
    # No sibling accounts, no real Ollama, no real keys.
    monkeypatch.setattr("navig.providers.inference.list_provider_connections", lambda p: [])
    monkeypatch.setattr("navig.llm.router._check_ollama_models", lambda *a, **k: {})
    yield {"tmp": tmp_path, "sent": sent}
    fp.reset_cooldowns()
    mh.reset_for_tests()
    mf.reset_for_tests()
    reset_config_manager()
    ConfigSingleton._instance = None


def _ledger_rows():
    return list(reversed(mf.history(500)))


def _sel(provider="anthropic", model="claude-opus-4-8", mode="big_tasks"):
    return ModelSelection(provider_name=provider, model_name=model, metadata={"mode": mode})


# ── chain assembly ──────────────────────────────────────────────────


def test_dead_ollama_and_healthy_nvidia_gives_a_fallback(env):
    """The operator's exact shape: the mode fallback is Ollama (not running) and
    the global chain is empty — the healthy nvidia model must now stand in."""
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    chain = g._assemble_fallback_chain(_sel(), [], model_override=None)
    assert chain == [NEMO]


def test_unhealthy_and_same_provider_models_are_not_candidates(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "auth", "401")
    mh.record("anthropic", "claude-sonnet-4-6", "live", "ok")  # same provider as the primary
    assert g._assemble_fallback_chain(_sel(), [], model_override=None) == []


def test_never_from_mode_gets_no_model_fallback(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.save_policy(never_from=["big_tasks"])
    assert g._assemble_fallback_chain(_sel(), ["openai:gpt-4o"], model_override=None) == []
    # …but another mode still falls back.
    assert g._assemble_fallback_chain(_sel(mode="coding"), [], model_override=None) == [NEMO]


def test_never_from_keeps_sibling_accounts(env, monkeypatch):
    """A second ACCOUNT of the same model is not a model switch."""
    monkeypatch.setattr(
        "navig.providers.inference.list_provider_connections",
        lambda p: [{"connection_id": "A"}, {"connection_id": "B"}],
    )
    mf.save_policy(never_from=["big_tasks"])
    chain = g._assemble_fallback_chain(_sel(), ["openai:gpt-4o"], model_override=None)
    assert chain == [f"{OPUS}@conn:B"]


def test_never_to_filters_provider_and_exact_model(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mh.record("openrouter", "openai/gpt-4o", "live", "ok")
    mf.save_policy(never_to=["nvidia"])
    assert g._assemble_fallback_chain(_sel(), [], model_override=None) == [
        "openrouter:openai/gpt-4o"
    ]
    mf.save_policy(never_to=["openrouter:openai/gpt-4o", "nvidia"])
    assert g._assemble_fallback_chain(_sel(), ["openrouter:openai/gpt-4o"], model_override=None) == []


def test_prefer_order_comes_before_automatic_picks(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.save_policy(prefer=["openai:gpt-4.1", "groq:openai/gpt-oss-120b"])
    chain = g._assemble_fallback_chain(_sel(), [], model_override=None)
    assert chain == ["openai:gpt-4.1", "groq:openai/gpt-oss-120b", NEMO]


def test_auto_off_drops_health_picks_but_keeps_prefer(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.save_policy(auto=False, prefer=["openai:gpt-4.1"])
    assert g._assemble_fallback_chain(_sel(), [], model_override=None) == ["openai:gpt-4.1"]


def test_model_override_keeps_its_old_chain(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    chain = g._assemble_fallback_chain(_sel(), ["openai:gpt-4o"], model_override=OPUS)
    assert chain == ["openai:gpt-4o"]  # no policy additions under --model


def test_ranking_prefers_same_family_then_tier(env):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mh.record("openrouter", "anthropic/claude-opus-4.1", "live", "ok")
    mh.record("groq", "llama-3.1-8b-mini", "live", "ok")
    # Tier 3 via "large", and not a Claude — so it separates the two rules: it is
    # neither the same family nor a small model.
    mh.record("mistral", "mistral-large-latest", "live", "ok")
    ranked = mf.rank_healthy("anthropic", "claude-opus-4-8")
    assert ranked[0] == "openrouter:anthropic/claude-opus-4.1"  # same family wins
    assert ranked[1] == "mistral:mistral-large-latest"  # closest size tier (3, like opus)
    # nvidia's stand-in was the retired 120B flagship (tier 3) until 2026-10-21;
    # its live sibling is a 30B/3B-active MoE, which `_tier` correctly calls 1 —
    # the same tier as groq's 8B. With the tier key tied, the documented last rule
    # decides: most recently seen answering, and groq's row was recorded last.
    assert ranked.index("groq:llama-3.1-8b-mini") < ranked.index(NEMO)


def test_stale_live_verdict_is_not_trusted(env, monkeypatch):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    real = mh.time.time
    monkeypatch.setattr(mh.time, "time", lambda: real() + mh.HEALTHY_MAX_AGE_S + 60)
    assert mh.healthy() == []


def test_probe_model_records_its_verdict(env, monkeypatch):
    from navig.llm import liveness

    monkeypatch.setattr(liveness, "_probe_model_uncached", lambda p, m, timeout: ("live", "ok"))
    assert liveness.probe_model("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b") == ("live", "ok")
    assert mh.status_of(NEMO)["status"] == "live"
    # a busy answer does not overwrite the verdict
    monkeypatch.setattr(liveness, "_probe_model_uncached",
                        lambda p, m, timeout: ("transient", "busy"))
    liveness.probe_model("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")
    assert mh.status_of(NEMO)["status"] == "live"


# ── ledger + notification ───────────────────────────────────────────


def test_ledger_written_once_per_switch(env):
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    mf.note_fallback("big_tasks", OPUS, NEMO, "cooldown")
    rows = _ledger_rows()
    assert len(rows) == 1
    assert rows[0]["event"] == "fallback"
    assert rows[0]["mode"] == "big_tasks" and rows[0]["from"] == OPUS and rows[0]["to"] == NEMO
    assert rows[0]["reason"] == "auth"
    # a NEW target is a new switch; so is a newly affected mode
    mf.note_fallback("big_tasks", OPUS, "openai:gpt-4o", "auth")
    mf.note_fallback("coding", OPUS, "openai:gpt-4o", "auth")
    assert len(_ledger_rows()) == 3
    st = mf.degraded()[OPUS]
    assert st["fallback"] == "openai:gpt-4o" and st["modes"] == ["big_tasks", "coding"]
    # the file is where the operator was told it is
    assert (env["tmp"] / "cfg" / "perf" / "model_fallbacks.jsonl").exists()


def test_notification_dedup_six_hours(env, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(mf.time, "time", lambda: now[0])
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    assert len(env["sent"]) == 1
    t, title, body = env["sent"][0]
    assert t == "model_fallback"
    assert title == "⚠️ Claude Opus 4.8 isn't answering — using Nemotron"
    assert "`big_tasks`" in body and "key rejected" in body
    assert "Settings → AI & Models → Fallbacks" in body

    now[0] += 3600  # +1h: still degraded, no repeat
    mf.note_fallback("big_tasks", OPUS, NEMO, "cooldown")
    mf.note_fallback("big_tasks", OPUS, "openai:gpt-4o", "auth")  # even a new target
    assert len(env["sent"]) == 1

    now[0] += 5 * 3600 + 1  # past 6h since the first
    mf.note_fallback("big_tasks", OPUS, NEMO, "cooldown")
    assert len(env["sent"]) == 2


def test_notify_type_is_registered_and_not_routine():
    from navig.notify.types import TYPE_KEYS, emoji_for_type
    from navig.telegram import chat_tidy

    assert "model_fallback" in TYPE_KEYS
    assert emoji_for_type("model_fallback") == "🔀"
    assert not chat_tidy.is_routine_notification("model_fallback")


def test_friendly_model_names():
    assert mf.friendly_model(OPUS) == "Claude Opus 4.8"
    assert mf.friendly_model("anthropic:claude-sonnet-4-6") == "Claude Sonnet 4.6"
    assert mf.friendly_model(NEMO) == "Nemotron"
    assert mf.friendly_model("openai:gpt-4o") == "GPT-4o"


# ── recovery ────────────────────────────────────────────────────────


def test_recovery_fires_once_and_clears_cooldown(env):
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    fp.mark_cooldown(f"{OPUS}@conn:A", fp.AUTH)
    fp.mark_cooldown(OPUS, fp.AUTH)
    assert fp.is_cooling(OPUS)
    env["sent"].clear()

    mf.note_primary_ok("anthropic", "claude-opus-4-8")
    assert OPUS not in mf.degraded()
    assert not fp.is_cooling(OPUS)
    assert not fp.is_cooling(f"{OPUS}@conn:A")
    assert len(env["sent"]) == 1
    assert env["sent"][0][1] == "✅ Claude Opus 4.8 is back"
    assert "`big_tasks`" in env["sent"][0][2]
    assert _ledger_rows()[-1]["event"] == "recovered"

    mf.note_primary_ok("anthropic", "claude-opus-4-8")  # again: nothing to do
    assert len(env["sent"]) == 1
    assert sum(r["event"] == "recovered" for r in _ledger_rows()) == 1


def test_recheck_degraded_recovers_only_live(env):
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    mf.note_fallback("coding", "anthropic:claude-sonnet-4-6", NEMO, "auth")
    calls = []

    def probe(provider, model, timeout):
        calls.append(model)
        return ("live", "ok") if model == "claude-opus-4-8" else ("auth", "401")

    assert mf.recheck_degraded(probe=probe) == [OPUS]
    assert sorted(calls) == ["claude-opus-4-8", "claude-sonnet-4-6"]
    assert list(mf.degraded()) == ["anthropic:claude-sonnet-4-6"]


def test_credential_change_lifts_cooldown_of_degraded_primary(env, monkeypatch):
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    fp.mark_cooldown(OPUS, fp.AUTH)
    started = []
    monkeypatch.setattr(mf.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    mf.on_credentials_changed()
    assert not fp.is_cooling(OPUS)
    assert started and started[0]["target"] is mf.recheck_degraded


def test_credential_change_is_free_when_nothing_degraded(env, monkeypatch):
    monkeypatch.setattr(mf.threading, "Thread", lambda **kw: pytest.fail("no thread expected"))
    mf.on_credentials_changed()


# ── run_llm end to end (the deck ask / CLI path) ───────────────────


def test_run_llm_401_falls_back_records_and_notifies(env, monkeypatch):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    monkeypatch.setattr(g, "_build_pipeline_context", lambda **kw: {})
    monkeypatch.setattr(g, "_enrich_messages_with_context", lambda m, c: m)
    monkeypatch.setattr(g, "_load_fallback_chain", lambda: [])
    monkeypatch.setattr(
        "navig.llm.router.resolve_llm",
        lambda **kw: ResolvedLLMConfig(provider="anthropic", model="claude-opus-4-8", mode="big_tasks"),
    )
    calls = []

    def wrap(**kw):
        sel = kw["selection"]
        spec = f"{sel.provider_name}:{sel.model_name}"
        calls.append(spec)
        if spec == OPUS:
            return LLMResult(content="", model=sel.model_name, provider=sel.provider_name,
                             finish_reason="error:AuthError: invalid x-api-key (status=401)")
        return LLMResult(content="answer", model=sel.model_name, provider=sel.provider_name)

    monkeypatch.setattr(g, "_call_and_wrap", wrap)
    res = g.run_llm([{"role": "user", "content": "hi"}], mode="big_tasks")
    assert res.content == "answer" and res.is_fallback
    assert calls == [OPUS, NEMO]
    assert mf.degraded()[OPUS]["fallback"] == NEMO
    assert len(env["sent"]) == 1
    assert mh.status_of(OPUS)["status"] == "auth"

    # Next call: the primary is cooling → routed straight to nemotron, no new row/notice.
    res2 = g.run_llm([{"role": "user", "content": "again"}], mode="big_tasks")
    assert res2.content == "answer"
    assert calls == [OPUS, NEMO, NEMO]
    assert len(_ledger_rows()) == 1 and len(env["sent"]) == 1

    # Re-auth: cooldown gone, primary answers → recovered once.
    fp.reset_cooldowns()
    monkeypatch.setattr(g, "_call_and_wrap", lambda **kw: LLMResult(
        content="opus again", model=kw["selection"].model_name,
        provider=kw["selection"].provider_name))
    assert g.run_llm([{"role": "user", "content": "x"}], mode="big_tasks").content == "opus again"
    assert mf.degraded() == {}
    assert env["sent"][-1][1] == "✅ Claude Opus 4.8 is back"


# ── run_agentic (the Telegram reply path) ──────────────────────────


class _Auth(Exception):
    status_code = 401


class _FakeClient:
    def __init__(self, acct, state):
        self.acct, self.state = acct, state

    async def complete(self, request):
        from navig.providers.clients import CompletionResponse

        self.state["calls"].append((self.acct, request.model, bool(request.tools)))
        beh = self.state["accounts"].get(self.acct, "?")
        if isinstance(beh, BaseException):
            raise beh
        return CompletionResponse(content=str(beh), model=request.model, provider=self.acct)

    async def close(self):
        pass


def _drive_agent(monkeypatch, accounts):
    from navig.agent.conv.agent import ConversationalAgent

    state = {"calls": [], "accounts": accounts}
    monkeypatch.setattr(
        "navig.llm.router.resolve_llm",
        lambda mode=None, **kw: ResolvedLLMConfig(
            provider="ollama" if mode == "small_talk" else "anthropic",
            model="qwen2.5:3b" if mode == "small_talk" else "claude-opus-4-8", mode=mode or "coding"),
    )
    monkeypatch.setattr(
        "navig.providers.inference.resolve_rotating_credential",
        lambda provider, model=None: ((None, "tok-anthropic", None) if provider == "anthropic"
                                      else (f"{provider}-key", None, None)),
    )
    monkeypatch.setattr(
        "navig.providers.inference.resolve_provider_credential",
        lambda provider, connection_id=None: ((None, "tok-anthropic") if provider == "anthropic"
                                              else (f"{provider}-key", None)),
    )
    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda cfg, api_key=None, oauth_token=None, timeout=None: _FakeClient(
            (oauth_token or api_key or "?").replace("tok-", "").replace("-key", ""), state),
    )
    monkeypatch.setattr(
        "navig.agent.agent_tool_registry._AGENT_REGISTRY.get_openai_schemas",
        lambda *a, **k: [{"type": "function", "function": {"name": "noop", "description": "x",
                                                           "parameters": {"type": "object"}}}],
    )
    agent = ConversationalAgent(ai_client=None, soul_content="You are NAVIG.")
    agent._agentic_tools_registered = True
    monkeypatch.setattr(agent, "_build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(agent, "_get_plan_context_block", lambda: "")
    monkeypatch.setattr(agent, "_build_skills_section", lambda m: "")
    monkeypatch.setattr(agent, "_recall_block", lambda m: "")
    msg = "please carry out this multi-step request with clearly more than twelve words in it"
    reply = asyncio.run(agent.run_agentic(msg, toolset=[]))
    return reply, agent, state


def test_agent_401_falls_back_with_tools_and_records(env, monkeypatch):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    reply, agent, state = _drive_agent(
        monkeypatch, {"anthropic": _Auth("invalid x-api-key"), "nvidia": "nemotron answer",
                      "ollama": "fast answer"},
    )
    assert reply == "nemotron answer"
    # the policy fallback ran BEFORE the tool-less fast model, and kept the tools
    assert state["calls"][0][0] == "anthropic"
    assert state["calls"][1] == ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", True)
    assert agent._last_account_fallback["kind"] == "model"
    assert agent._last_account_fallback["to"] == NEMO
    assert mf.degraded()[OPUS]["modes"] == ["coding"]
    assert len(env["sent"]) == 1
    # shared-key primary was cooled under its bare key → the next turn routes around it
    assert fp.is_cooling(OPUS)

    reply2, _agent2, state2 = _drive_agent(
        monkeypatch, {"anthropic": _Auth("still bad"), "nvidia": "second", "ollama": "fast"},
    )
    assert reply2 == "second"
    assert [c[0] for c in state2["calls"]] == ["nvidia"]  # primary never dialled
    assert len(env["sent"]) == 1  # deduped


def test_agent_never_from_fails_loudly(env, monkeypatch):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.save_policy(never_from=["coding"])
    reply, agent, state = _drive_agent(
        monkeypatch, {"anthropic": _Auth("invalid x-api-key"), "nvidia": "x", "ollama": "fast"},
    )
    assert reply.startswith("Error during agentic execution")
    assert [c[0] for c in state["calls"]] == ["anthropic"]  # no nvidia, no fast model
    assert mf.degraded() == {} and env["sent"] == []


def test_agent_fast_model_drop_is_recorded_too(env, monkeypatch):
    """No healthy candidate → the existing fast-model drop still answers, and is
    now on the ledger + notified like any other switch."""
    reply, _agent, _state = _drive_agent(
        monkeypatch, {"anthropic": _Auth("invalid x-api-key"), "ollama": "fast answer"},
    )
    assert reply == "fast answer"
    assert mf.degraded()[OPUS]["fallback"] == "ollama:qwen2.5:3b"
    assert len(env["sent"]) == 1


# ── CLI ─────────────────────────────────────────────────────────────


def _cli(args):
    from typer.testing import CliRunner

    from navig.commands.ai_fallback import fallback_app

    return CliRunner().invoke(fallback_app, args)


def test_cli_status_json_and_table(env):
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    r = _cli(["--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert {m["mode"] for m in data["modes"]} >= {"big_tasks", "coding"}
    assert OPUS in data["degraded"]
    r = _cli(["status"])
    assert r.exit_code == 0 and "big_tasks" in r.output


def test_cli_set_and_never_from(env):
    r = _cli(["set", "big_tasks", "--to", NEMO])
    assert r.exit_code == 0, r.output
    from navig.config import get_config_manager

    modes = get_config_manager().get("llm_router.llm_modes")
    assert modes["big_tasks"]["fallback_provider"] == "nvidia"
    assert modes["big_tasks"]["fallback_model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"

    r = _cli(["--never-from", "coding"])
    assert r.exit_code == 0, r.output
    assert mf.load_policy().never_from == ["coding"]
    r = _cli(["--allow", "coding", "--auto", "off"])
    assert r.exit_code == 0
    pol = mf.load_policy()
    assert pol.never_from == [] and pol.auto is False


@pytest.mark.parametrize("args", [
    ["set", "codng", "--to", NEMO],          # typo'd mode must not silently hit big_tasks
    ["set", "big_tasks", "--to", "nocolon"],  # not provider:model
    ["set", "big_tasks"],                     # neither --to nor --clear
    ["--never-from", "nope"],
    ["--auto", "maybe"],
])
def test_cli_invalid_input_exits_nonzero(env, args):
    r = _cli(args)
    assert r.exit_code == 1, r.output
    assert mf.load_policy().never_from == []


def test_cli_history(env):
    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    r = _cli(["history", "--json"])
    assert r.exit_code == 0
    rows = json.loads(r.output)
    assert rows[0]["to"] == NEMO


# ── doctor ──────────────────────────────────────────────────────────


def test_doctor_row_honesty(env):
    from navig.commands import doctor

    (icon, ok, line), = doctor.check_model_fallback()
    assert ok is True

    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    (icon, ok, line), = doctor.check_model_fallback()
    assert ok is False and icon == doctor._WARN
    assert OPUS in line and NEMO in line

    # an unreadable state file is a warning, never a green tick
    mf._state_path().write_text("{not json", encoding="utf-8")
    mf.reset_for_tests()
    (icon, ok, line), = doctor.check_model_fallback()
    assert ok is False and icon == doctor._WARN


# ── heartbeat + chat notice ─────────────────────────────────────────


def test_heartbeat_rechecks_degraded_primaries(env, monkeypatch):
    from navig.heartbeat.runner import HeartbeatRunner

    runner = HeartbeatRunner.__new__(HeartbeatRunner)
    # nothing degraded → no probe at all
    monkeypatch.setattr(mf, "recheck_degraded", lambda: pytest.fail("must not probe"))
    assert asyncio.run(runner._recheck_degraded_models()) == []

    mf.note_fallback("big_tasks", OPUS, NEMO, "auth")
    monkeypatch.setattr(mf, "recheck_degraded", lambda: [OPUS])
    assert asyncio.run(runner._recheck_degraded_models()) == [OPUS]


def test_chat_notice_names_a_model_switch():
    from navig.gateway.channel_router import _format_account_rotation_notice

    assert _format_account_rotation_notice(
        {"to": NEMO, "reason": "auth", "kind": "model"}
    ) == f"↻ Primary model was failing to authenticate — answered with {NEMO}."
    assert _format_account_rotation_notice({"to": "Claude B", "reason": "rate_limited"}).startswith(
        "↻ Primary account was rate-limited"
    )


def test_auth_fallback_notice_names_the_reconnect_command(monkeypatch):
    # A rejected credential never heals itself: the switch notice must say how to fix it.
    monkeypatch.setattr("navig.providers.inference.reconnect_hint",
                        lambda p: "navig connect login claude-max" if p == "anthropic" else None)
    _, body = mf._fallback_text("anthropic:claude-opus-4-8", "openai:gpt-4o", ["coding"], "auth")
    assert "Reconnect: `navig connect login claude-max`" in body
    _, body = mf._fallback_text("anthropic:claude-opus-4-8", "openai:gpt-4o", ["coding"],
                                "rate_limited")
    assert "Reconnect" not in body  # a cap clears on its own — no false fix


# ── a persisted 401 verdict reroutes BEFORE the first failed reply ──────────


def test_heartbeat_auth_verdict_reroutes_the_next_turn_up_front(env):
    # Nothing has failed in THIS process (empty cooldown map) — only the
    # heartbeat's probe saw the 401. The old pre-check returned None here, so
    # every chat turn dialled the rejected login first.
    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    assert not fp.any_cooling()
    assert mf.preemptive_target("big_tasks", "anthropic", "claude-opus-4-8") == NEMO


def test_preemptive_switch_reports_the_real_reason(env, monkeypatch):
    monkeypatch.setattr("navig.providers.inference.reconnect_hint",
                        lambda p: "navig connect login claude-max")
    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    mf.note_fallback("big_tasks", OPUS, NEMO, "cooldown")   # what the agent passes
    assert mf.degraded()[OPUS]["reason"] == "auth"
    assert "Reconnect: `navig connect login claude-max`" in env["sent"][-1][2]


def test_transient_or_stale_verdicts_do_not_reroute(env, monkeypatch):
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mh.record("anthropic", "claude-opus-4-8", "transient", "503", source="probe")
    assert mf.preemptive_target("big_tasks", "anthropic", "claude-opus-4-8") is None
    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    real_time = mf.time.time
    monkeypatch.setattr(mf.time, "time", lambda: real_time() + 3 * 24 * 3600)
    assert mf.broken_verdict("anthropic", "claude-opus-4-8") is None  # too old to trust


def test_never_from_mode_ignores_the_verdict(env):
    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.save_policy(never_from=["big_tasks"])
    assert mf.preemptive_target("big_tasks", "anthropic", "claude-opus-4-8") is None


def test_reauth_forgets_the_rejected_verdict(env, monkeypatch):
    monkeypatch.setattr(mf.threading, "Thread", lambda **kw: pytest.fail("nothing degraded"))
    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    mh.record("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", "live", "ok")
    mf.on_credentials_changed(provider="anthropic", routable=True)  # a login that now works
    assert mf.broken_verdict("anthropic", "claude-opus-4-8") is None
    assert mf.preemptive_target("big_tasks", "anthropic", "claude-opus-4-8") is None
    assert mh.status_of(NEMO)["status"] == "live"   # other verdicts untouched


def test_a_store_write_that_is_not_a_working_login_keeps_the_verdict(env, monkeypatch):
    # Seen live 2026-10-10: the store also writes on a token refresh and on a
    # revalidation that lands on needs_reauth. Forgetting on those erased the
    # planted 401 before the next chat arrived, and Claude answered it.
    monkeypatch.setattr(mf.threading, "Thread", lambda **kw: pytest.fail("nothing degraded"))
    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    mh.record("groq", "llama-3.3-70b", "auth", "401", source="probe")
    mf.on_credentials_changed()                                      # unknown change
    mf.on_credentials_changed(provider="anthropic", routable=False)  # still broken
    assert mf.broken_verdict("anthropic", "claude-opus-4-8") == "auth"
    mf.on_credentials_changed(provider="anthropic", routable=True)   # re-signed in
    assert mf.broken_verdict("anthropic", "claude-opus-4-8") is None
    assert mh.status_of("groq:llama-3.3-70b")["status"] == "auth"   # other provider untouched


def test_connection_store_reports_which_provider_and_whether_it_works(monkeypatch):
    from types import SimpleNamespace

    from navig.providers import connections as cs

    seen: list[dict] = []
    monkeypatch.setattr(mf, "on_credentials_changed", lambda **kw: seen.append(kw))
    broken = SimpleNamespace(is_routable=False,
                             to_public_dict=lambda: {"template_id": "claude-max"})
    fixed = SimpleNamespace(is_routable=True,
                            to_public_dict=lambda: {"template_id": "claude-max"})
    cs._credentials_changed(broken)
    cs._credentials_changed(fixed)
    assert seen == [{"provider": "anthropic", "routable": False},
                    {"provider": "anthropic", "routable": True}]


def test_an_unreadable_health_file_is_never_overwritten_with_one_verdict(env, monkeypatch):
    # Three processes write this file. A read that lands on another one's replace
    # failed, load() said {} and record() saved "{} + one verdict" over the cache —
    # seen live as a cache holding a lone `sonnet: live` with the planted 401 gone.
    import pathlib

    import navig.core.yaml_io as yio

    mh.record("anthropic", "claude-opus-4-8", "auth", "401", source="probe")
    target = mh._path()
    real_read = pathlib.Path.read_text

    def _locked(self, *a, **k):
        if pathlib.Path(self) == target:
            raise PermissionError(32, "being used by another process")
        return real_read(self, *a, **k)

    monkeypatch.setattr(yio, "_ATOMIC_REPLACE_BACKOFF_BASE", 0.0, raising=False)
    pathlib.Path.read_text = _locked          # the file is locked for EVERY reader
    try:
        mh.reset_for_tests()                  # no in-memory copy to hide behind
        mh.record("anthropic", "claude-sonnet-4-6", "live", "ok", source="observed")
    finally:
        pathlib.Path.read_text = real_read
    mh.reset_for_tests()
    assert mh.status_of(OPUS)["status"] == "auth"   # the verdict survived the failed read
