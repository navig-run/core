"""
Loop-level test harness for ConversationalAgent.run_agentic account rotation.

Existing agent tests mock run_agentic wholesale; nothing drove its INTERNAL
dispatch loop. This harness patches the routing/credential/client seams and
feeds a scripted fake client keyed by account, so we can assert the real
recovery behaviour end-to-end:

  * a capped account rotates to a sibling within the turn (`_account_retry`)
  * when every account is capped, it degrades to the fast model (`_fast_retry`)

The fake client identifies its account from the credential create_client is
handed (``tok-<cid>`` for subscriptions, ``<provider>-key`` for the fast model),
so a single create_client mock serves the primary, the sibling accounts, and the
fast-model fallback.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.agent.conv.agent import ConversationalAgent
from navig.llm import fallback_policy as fp
from navig.llm.router import ResolvedLLMConfig
from navig.providers.clients import CompletionResponse, ToolCall

pytestmark = pytest.mark.integration

# A message long enough to route as "coding" (non-short-chat), so the primary
# resolves to the anthropic tier rather than the small-talk fast model.
_TASK = "please carry out this multi-step request with clearly more than twelve words in it"


class _RateLimited(Exception):
    status_code = 429
    error_type = "rate_limit"


def _conn(cid: str, name: str, *, default: bool = False) -> dict:
    return {
        "connection_id": cid, "name": name, "template_id": "claude-max",
        "driver": "native", "is_default": default, "is_routable": True,
        "default_model": "claude-opus-4-8", "models": ["claude-opus-4-8"], "metadata": {},
    }


def _tool_call(n: int) -> CompletionResponse:
    """A response that asks for a (fake) tool, forcing another turn. Unique args
    per call so the loop's duplicate-tool-call guard doesn't halt early."""
    return CompletionResponse(
        content="",
        tool_calls=[ToolCall(id=f"t{n}", name="noop", arguments=f'{{"n": {n}}}')],
        model="claude-opus-4-8", provider="anthropic",
    )


class _Chunk:
    """Minimal stream chunk (the loop only reads delta/finish_reason/usage/model)."""

    def __init__(self, delta=None, finish_reason=None, usage=None, model=None):
        self.delta = delta
        self.finish_reason = finish_reason
        self.usage = usage
        self.model = model


class _FakeClient:
    """Returns a scripted result (or raises) based on which account it is. A
    per-account list is a QUEUE consumed in order (for multi-turn scripts); a
    single value is returned on every call."""

    def __init__(self, account: str, state: dict):
        self._acct = account
        self._state = state

    def _next(self):
        beh = self._state["accounts"].get(self._acct)
        if isinstance(beh, list):
            beh = beh.pop(0) if beh else "queue exhausted"
        return beh

    async def complete(self, request):
        self._state["calls"].append(self._acct)
        beh = self._next()
        if isinstance(beh, BaseException):
            raise beh
        if isinstance(beh, CompletionResponse):
            return beh
        return CompletionResponse(content=str(beh), model=request.model, provider="anthropic")

    async def complete_stream(self, request):
        self._state["calls"].append(f"{self._acct}:stream")
        beh = self._next()
        if isinstance(beh, BaseException):
            yield _Chunk(delta="partial…")   # emit some text, THEN fail mid-stream
            raise beh
        yield _Chunk(delta=str(beh))
        yield _Chunk(finish_reason="stop", model=request.model)

    async def close(self):
        pass


def _drive(
    monkeypatch, *, accounts: dict, connections: list[dict],
    message: str = _TASK, on_partial=None, fast: tuple[str, str] = ("ollama", "qwen2.5:3b"),
) -> tuple[str, ConversationalAgent, dict]:
    """Run run_agentic against a scripted fake client. Returns (reply, agent, state).

    *fast* is what the small-talk tier resolves to (the fast-model fallback, and
    — for a short streaming message — the primary tier too)."""
    fp.reset_cooldowns()
    state = {"calls": [], "accounts": accounts}

    def _resolve_llm(mode=None, **_kw):
        if mode == "small_talk":
            return ResolvedLLMConfig(provider=fast[0], model=fast[1], mode="small_talk")
        return ResolvedLLMConfig(provider="anthropic", model="claude-opus-4-8", mode="coding")

    def _fake_create_client(config, api_key=None, oauth_token=None, timeout=None):
        acct = (oauth_token or api_key or "?").replace("tok-", "").replace("-key", "")
        return _FakeClient(acct, state)

    monkeypatch.setattr("navig.llm.router.resolve_llm", _resolve_llm)
    monkeypatch.setattr(
        "navig.providers.inference.resolve_rotating_credential",
        lambda provider, model=None: (None, f"tok-{connections[0]['connection_id']}",
                                      connections[0]["connection_id"]),
    )
    monkeypatch.setattr("navig.providers.inference.list_provider_connections", lambda p: connections)
    monkeypatch.setattr(
        "navig.providers.inference.credential_for_connection",
        lambda conn, pid=None: (None, f"tok-{conn['connection_id']}"),
    )
    monkeypatch.setattr(
        "navig.providers.inference.resolve_provider_credential",
        lambda provider, connection_id=None: (f"{provider}-key", None),
    )
    monkeypatch.setattr("navig.providers.create_client", _fake_create_client)
    monkeypatch.setattr(
        "navig.agent.agent_tool_registry._AGENT_REGISTRY.get_openai_schemas",
        lambda *a, **k: [],
    )

    agent = ConversationalAgent(ai_client=None, soul_content="You are NAVIG.")
    agent._agentic_tools_registered = True
    monkeypatch.setattr(agent, "_build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(agent, "_get_plan_context_block", lambda: "")
    monkeypatch.setattr(agent, "_build_skills_section", lambda m: "")
    monkeypatch.setattr(agent, "_recall_block", lambda m: "")

    reply = asyncio.run(agent.run_agentic(message, toolset=[], on_partial=on_partial))
    return reply, agent, state


def test_capped_primary_rotates_to_sibling_within_turn(monkeypatch):
    reply, agent, state = _drive(
        monkeypatch,
        accounts={"A": _RateLimited("rate limit"), "B": "answer from account B"},
        connections=[_conn("A", "Claude A", default=True), _conn("B", "Claude B")],
    )
    assert reply == "answer from account B"
    assert state["calls"] == ["A", "B"]              # primary A capped → sibling B answered
    assert agent._last_account_fallback is not None
    assert agent._last_account_fallback["connection_id"] == "B"
    assert agent._last_account_fallback["reason"] == "rate_limited"
    # A is now cooling account-wide; B is not.
    assert fp.is_cooling("anthropic:claude-opus-4-8@conn:A") is True
    assert fp.is_cooling("anthropic:claude-opus-4-8@conn:B") is False
    fp.reset_cooldowns()


def test_all_accounts_capped_degrades_to_fast_model(monkeypatch):
    reply, agent, state = _drive(
        monkeypatch,
        accounts={
            "A": _RateLimited("rate limit"),
            "B": _RateLimited("rate limit"),
            "C": _RateLimited("rate limit"),
            "ollama": "fast-model answer",
        },
        connections=[
            _conn("A", "Claude A", default=True), _conn("B", "Claude B"), _conn("C", "Claude C"),
        ],
    )
    assert reply == "fast-model answer"
    # tried every account, then dropped to the fast model
    assert state["calls"] == ["A", "B", "C", "ollama"]
    fp.reset_cooldowns()


def test_multi_turn_rotates_across_accounts_A_B_C(monkeypatch):
    # A multi-step task where accounts cap on DIFFERENT turns: A works turn 1
    # then caps turn 2 (→B), B works then caps (→C), C finishes. This is the
    # cross-turn rotation the `_fell_back` decoupling enables — a single message
    # walks A→B→C instead of erroring on the second cap.
    reply, agent, state = _drive(
        monkeypatch,
        accounts={
            "A": [_tool_call(1), _RateLimited("rate limit")],   # turn 1 tool, turn 2 cap
            "B": [_tool_call(2), _RateLimited("rate limit")],   # answers, then caps
            "C": ["done on account C"],                          # finishes
        },
        connections=[
            _conn("A", "Claude A", default=True), _conn("B", "Claude B"), _conn("C", "Claude C"),
        ],
    )
    assert reply == "done on account C"
    assert state["calls"] == ["A", "A", "B", "B", "C"]  # A(tool),A(cap)→B(tool),B(cap)→C(done)
    assert agent._last_account_fallback["connection_id"] == "C"  # last account that answered
    fp.reset_cooldowns()


def test_mid_stream_rate_limit_rotates_to_sibling_and_keeps_streaming(monkeypatch):
    # A streamed (chat-feel) turn whose stream fails mid-flight with a 429 must
    # rotate to a sibling account AND keep streaming (so the Telegram edit stays
    # live through the rotation) — the failed stream's partial text is superseded
    # by the sibling's streamed reply.
    partials: list[str] = []

    async def _on_partial(text: str) -> None:
        partials.append(text)

    reply, agent, state = _drive(
        monkeypatch,
        accounts={"A": _RateLimited("rate limit"), "B": "streamed answer via B"},
        connections=[_conn("A", "Claude A", default=True), _conn("B", "Claude B")],
        message="quick question",          # short → the streaming path is enabled
        on_partial=_on_partial,
        fast=("anthropic", "claude-opus-4-8"),  # short msg → primary tier is anthropic
    )
    assert reply == "streamed answer via B"
    assert "partial…" in partials                       # the failed stream emitted text
    assert "streamed answer via B" in partials          # the sibling STREAMED (not blocking)
    assert state["calls"] == ["A:stream", "B:stream"]   # both streamed
    assert agent._last_account_fallback["connection_id"] == "B"
    fp.reset_cooldowns()
