"""A message with a link must reach tools that can open it, on a budget that can answer.

Two defects with one root: the run's shape is decided ONCE, from the length of
the incoming message, before anyone knows what the model will do with it.

* A bare URL is 33 characters, so it routes as "chat-feel" → the toolset stays
  ``core`` (bash/read_file/write_file/list_files — nothing that can fetch a
  page). The model's only honest reply was "Can't access external links."
* The same classification pins the chat-sized ``max_tokens`` (whatever the
  small_talk mode config carries — ``_AGENTIC_CHAT_MAXTOK`` is only the fallback,
  since ``resolve_llm`` overwrites it) and a 35s LLM timeout for the WHOLE run.
  Once a tool call happens the turn is no longer small talk, and a
  fetch → summarise answer was being cut off mid-sentence.
"""
from types import SimpleNamespace

import pytest

LINK = "https://vm.tiktok.com/ZGdxryFmF"


def _fake_llm_config():
    return SimpleNamespace(
        provider="anthropic", model="claude-sonnet-5",
        temperature=0.2, max_tokens=512, base_url=None,
    )


class _Registry:
    def __init__(self):
        self.seen_toolsets = []

    def get_openai_schemas(self, toolsets):
        self.seen_toolsets.append(list(toolsets))
        return [{
            "function": {
                "name": "web_fetch",
                "description": "fetch a url",
                "parameters": {"type": "object", "properties": {}},
            }
        }]

    def available_names(self, toolsets):
        return ["web_fetch"]

    def dispatch(self, name, args, vault_injector=None):
        return "page text"


def _patch(monkeypatch, registry):
    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr("navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config())
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())


def _tool_call(name, args, tc_id="t1"):
    import json as _json

    from navig.providers.clients import ToolCall
    return ToolCall(id=tc_id, name=name, arguments=_json.dumps(args))


def _usage():
    return {"prompt_tokens": 1, "completion_tokens": 1}


class _RecordingClient:
    """Serves scripted turns and records each request's budget."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return self._turns.pop(0)


@pytest.mark.asyncio
async def test_a_pasted_link_gets_a_toolset_that_can_fetch(monkeypatch):
    registry = _Registry()
    _patch(monkeypatch, registry)
    client = _RecordingClient([
        SimpleNamespace(content="done", tool_calls=None, usage=_usage()),
    ])
    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kw: client,
    )

    from navig.agent.conv import ConversationalAgent as ConvAgent

    await ConvAgent().run_agentic(message=LINK)

    assert registry.seen_toolsets, "the registry was never asked for schemas"
    assert "search" in registry.seen_toolsets[0], (
        "a message containing a URL must reach the search/web_fetch toolset — "
        f"got {registry.seen_toolsets[0]}"
    )


@pytest.mark.asyncio
async def test_plain_chat_does_not_gain_fetch_tools(monkeypatch):
    """The widening is scoped to messages that actually carry a link."""
    registry = _Registry()
    _patch(monkeypatch, registry)
    client = _RecordingClient([
        SimpleNamespace(content="hi", tool_calls=None, usage=_usage()),
    ])
    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kw: client,
    )

    from navig.agent.conv import ConversationalAgent as ConvAgent

    await ConvAgent().run_agentic(message="hey how are you")

    assert "search" not in registry.seen_toolsets[0]


@pytest.mark.asyncio
async def test_first_tool_call_lifts_the_small_talk_token_cap(monkeypatch):
    """Turn 1 may use the chat budget; every turn after a tool call must not.

    The opening budget comes from the resolved mode config (``resolve_llm``
    overwrites the module constant), which is why this asserts against the
    stubbed 512 rather than ``_AGENTIC_CHAT_MAXTOK`` — the point is that a
    chat-sized budget is RAISED once the run turns into tool work.
    """
    from navig.agent.conv.agent import _AGENTIC_DEFAULT_MAXTOK

    registry = _Registry()
    _patch(monkeypatch, registry)
    client = _RecordingClient([
        SimpleNamespace(
            content=None, tool_calls=[_tool_call("web_fetch", {"url": LINK})], usage=_usage()
        ),
        SimpleNamespace(content="a full answer", tool_calls=None, usage=_usage()),
    ])
    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kw: client,
    )

    from navig.agent.conv import ConversationalAgent as ConvAgent

    reply = await ConvAgent().run_agentic(message=LINK)

    assert reply == "a full answer"
    assert len(client.requests) == 2
    assert client.requests[0].max_tokens == 512, (
        "turn 1 should still open on the cheap chat budget from the mode config"
    )
    assert client.requests[1].max_tokens == _AGENTIC_DEFAULT_MAXTOK, (
        "after a tool call the run is not small talk — the answer must not stay "
        "capped at the chat budget, or a fetch→summarise reply is truncated"
    )


@pytest.mark.asyncio
async def test_escalation_is_a_floor_and_never_lowers_a_bigger_budget(monkeypatch):
    """Escalation raises a chat-sized budget; it must never REDUCE a mode that
    is already configured larger than the tool-work default."""
    from navig.agent.conv.agent import _AGENTIC_DEFAULT_MAXTOK

    generous = _AGENTIC_DEFAULT_MAXTOK * 2
    registry = _Registry()
    _patch(monkeypatch, registry)
    monkeypatch.setattr(
        "navig.llm.router.resolve_llm",
        lambda mode="coding": SimpleNamespace(
            provider="anthropic", model="claude-sonnet-5",
            temperature=0.2, max_tokens=generous, base_url=None,
        ),
    )
    client = _RecordingClient([
        SimpleNamespace(
            content=None, tool_calls=[_tool_call("web_fetch", {"url": LINK})], usage=_usage()
        ),
        SimpleNamespace(content="done", tool_calls=None, usage=_usage()),
    ])
    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kw: client,
    )

    from navig.agent.conv import ConversationalAgent as ConvAgent

    await ConvAgent().run_agentic(message=LINK)

    assert all(r.max_tokens == generous for r in client.requests), (
        "escalation clamped a larger configured budget down to the default"
    )
