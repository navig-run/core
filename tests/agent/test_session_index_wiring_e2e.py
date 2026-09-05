"""The session index is actually reached from the real agent loop.

``tests/agent/test_session_index.py`` proves the helpers behave; it cannot prove the two
call sites in ``conv/agent.py`` are reachable, are given the right arguments, or stay
silent when the feature is off. A helper that works and a call site that never fires look
identical from a unit test — the shape that let #1004's storage layer sit inert.

So these drive the REAL ``run_agentic`` against the fake-provider harness the sibling
dispatch tests use (no LLM, no daemon, no credentials) and assert on what landed in the
index afterwards.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from navig.memory.session_index import get_session_index, reset_session_index

# ── harness (mirrors tests/agent/test_tool_dispatch_timeout.py) ──────────────


def _fake_llm_config():
    return SimpleNamespace(
        provider="openrouter",
        model="openai/gpt-4o",
        temperature=0.2,
        max_tokens=512,
        base_url=None,
    )


def _tool_call(name, args, tc_id=None):
    from navig.providers.clients import ToolCall

    tc = ToolCall.__new__(ToolCall)
    tc.id = tc_id or f"tc-{name}"
    tc.name = name
    tc.arguments = json.dumps(args)
    return tc


def _fake_usage():
    return {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


_TOOL_OUTPUT = "connected to prod-web-01 and restarted nginx"


def _apply_common_patches(monkeypatch, registry):
    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr(
        "navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config()
    )
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())


class _Registry:
    def get_openai_schemas(self, toolsets):
        return []

    def available_names(self, toolsets):
        return ["ssh_run"]

    def dispatch(self, name, args, vault_injector=None):
        return _TOOL_OUTPUT


async def _run_one_tool_turn(monkeypatch):
    """Drive one real agent turn: the model calls a tool, then answers."""
    from navig.agent.conv import ConversationalAgent as ConvAgent
    from navig.agent.speculative import get_speculative_executor  # noqa: F401

    monkeypatch.setattr(
        "navig.agent.speculative.get_speculative_executor", lambda: None
    )
    _apply_common_patches(monkeypatch, _Registry())

    responses = iter(
        [
            SimpleNamespace(
                content=None,
                tool_calls=[_tool_call("ssh_run", {"cmd": "systemctl restart nginx"})],
                usage=_fake_usage(),
            ),
            SimpleNamespace(content="done", tool_calls=None, usage=_fake_usage()),
        ]
    )

    class _FakeClient:
        async def complete(self, request):
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    agent = ConvAgent()
    result = await agent.run_agentic(message="restart nginx on prod", max_iterations=5)
    return agent, result


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_session_index()
    yield
    reset_session_index()


@pytest.mark.asyncio
async def test_tool_results_reach_the_index_when_enabled(monkeypatch) -> None:
    """The recording call site fires from the real loop, with the real tool output."""
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: True
    )

    agent, result = await _run_one_tool_turn(monkeypatch)
    assert result == "done"

    index = get_session_index()
    assert index is not None
    # Recorded under the agent's OWN session id — a wrong id would store the event
    # somewhere no later recovery for this session could ever find it.
    hits = index.search(agent._session_id, "nginx prod-web-01")
    assert hits, "the tool result never reached the index — the call site is not wired"
    assert _TOOL_OUTPUT in hits[0].text
    # ssh_run runs a command, so it is not in READ_ONLY_TOOLS and is recorded as an
    # action rather than a plain read. Asserting the classified kind here (not just
    # "something was stored") is what proves event_kind_for_tool is reached through the
    # real loop, with the real tool name.
    assert hits[0].kind == "action"


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_disabled(monkeypatch) -> None:
    """The default path must be inert — the whole reason the flag is checked inline."""
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: False
    )

    agent, result = await _run_one_tool_turn(monkeypatch)
    assert result == "done"

    index = get_session_index()
    assert index is not None
    assert index.stats(agent._session_id)["events"] == 0


@pytest.mark.asyncio
async def test_a_failing_index_never_breaks_a_turn(monkeypatch) -> None:
    """Memory is an augmentation: if recording raises, the turn still completes."""
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: True
    )

    def _explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("index exploded")

    monkeypatch.setattr("navig.memory.session_index.record_event", _explode)

    # The turn must still return its answer rather than propagating the failure.
    _agent, result = await _run_one_tool_turn(monkeypatch)
    assert result == "done"


# ── the recovery half: a real compaction re-injects the relevant events ──────
#
# The sibling cases above prove RECORDING reaches the index. This proves the other
# direction end-to-end: that after the real compressor actually compacts, the block
# lands in the message list the model is then called with. Asserting on the request the
# provider receives is the point — a block appended somewhere the model never sees would
# satisfy a weaker test and deliver nothing.


async def _run_with_forced_compaction(monkeypatch, captured):
    """Drive two turns with the context window shrunk so compaction really fires."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    monkeypatch.setattr(
        "navig.agent.speculative.get_speculative_executor", lambda: None
    )
    _apply_common_patches(monkeypatch, _Registry())

    # maybe_compress returns the SAME list below its ratio threshold and a deepcopy above
    # it; agent.py keys the recovery on `compressed is not msg_dicts`. A tiny window puts
    # the ratio far over the threshold, so the compaction branch is taken for real.
    monkeypatch.setattr(
        "navig.agent.context_compressor._get_context_window", lambda model: 10
    )

    # Compaction only starts after _COMPRESS_AFTER_TURN (3), so the run has to get past
    # it. Each turn calls the tool with DIFFERENT arguments — identical repeats trip the
    # agent's repeated-tool-call halt, which ends the run before compaction is reached.
    responses = iter(
        [
            *[
                SimpleNamespace(
                    content=None,
                    tool_calls=[
                        _tool_call("ssh_run", {"cmd": f"systemctl status svc-{i}"}, f"tc-{i}")
                    ],
                    usage=_fake_usage(),
                )
                for i in range(5)
            ],
            SimpleNamespace(content="done", tool_calls=None, usage=_fake_usage()),
        ]
    )

    class _CapturingClient:
        async def complete(self, request):
            captured.append(request)
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _CapturingClient(),
    )

    agent = ConvAgent()
    result = await agent.run_agentic(message="what happened with nginx?", max_iterations=8)
    return agent, result


def _system_texts(request) -> list[str]:
    out = []
    for msg in getattr(request, "messages", []) or []:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        content = getattr(msg, "content", None) or (
            msg.get("content") if isinstance(msg, dict) else None
        )
        if role == "system" and isinstance(content, str):
            out.append(content)
    return out


@pytest.mark.asyncio
async def test_recovered_context_reaches_the_model_after_a_compaction(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: True
    )
    captured: list[object] = []
    agent, result = await _run_with_forced_compaction(monkeypatch, captured)
    assert result == "done"

    # The tool result was recorded on turn 1 …
    index = get_session_index()
    assert index is not None
    assert index.stats(agent._session_id)["events"] >= 1

    # … and a later request carries it back as a recovered-context system message.
    blocks = [t for req in captured for t in _system_texts(req) if "Recovered context" in t]
    assert blocks, "no recovery block reached the model after compaction"
    assert _TOOL_OUTPUT[:20] in blocks[0]


@pytest.mark.asyncio
async def test_no_recovery_block_when_disabled(monkeypatch) -> None:
    """Same forced compaction, feature off — the model must see no injected block."""
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: False
    )
    captured: list[object] = []
    _agent, result = await _run_with_forced_compaction(monkeypatch, captured)
    assert result == "done"

    blocks = [t for req in captured for t in _system_texts(req) if "Recovered context" in t]
    assert not blocks


@pytest.mark.asyncio
async def test_recovery_survives_a_compaction_that_drops_the_user_turn(monkeypatch) -> None:
    """The query must come from BEFORE the compaction, not after it.

    `recover_context` searches for the newest user message, and compaction is exactly what
    removes it. Handing it the compacted list makes recovery return "" in the one situation
    it exists for — while the Context Summary still appears, so the run looks healthy.

    Found in a full-suite run where a 12-message compaction left 7 messages with no user
    turn: `recover_context` was called three times, raised nothing, and returned empty every
    time, while a direct search of the same index returned 5 hits. Whether a real compaction
    keeps a user turn depends on message sizes, so this forces the case instead of waiting
    for a distribution that produces it: the compressor is replaced with one that returns a
    summary-only list, which is what an aggressive compaction really does.
    """
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: True
    )

    from navig.agent.context_compressor import ContextCompressor

    def _drop_everything(self, msg_dicts, model=None):  # noqa: ANN001, ARG001
        # A NEW list (agent.py keys the recovery on `compressed is not msg_dicts`) with no
        # user turn left in it — the shape observed in the failing run.
        return [
            {"role": "system", "content": "[Context Summary - 12 messages compressed]"},
            {"role": "assistant", "content": "..."},
        ]

    monkeypatch.setattr(ContextCompressor, "maybe_compress", _drop_everything)

    captured: list[object] = []
    _agent, result = await _run_with_forced_compaction(monkeypatch, captured)
    assert result == "done"

    blocks = [t for req in captured for t in _system_texts(req) if "Recovered context" in t]
    assert blocks, (
        "no recovery block after a compaction that dropped the user turn — the agent is "
        "passing the COMPACTED messages to recover_context, so the query is empty and "
        "recovery can only ever return ''. Pass the pre-compaction list."
    )
    assert _TOOL_OUTPUT[:20] in blocks[0]


# ── a refused tool is recoverable context too ────────────────────────────────
#
# "record the operator's approval decisions" looks like it needs a hook inside
# navig.tools.approval. It does not: a blocked tool raises ToolPermissionDenied,
# the loop turns that into the turn's tool RESULT, and the existing record site
# stores it like any other. Measured before deciding — a dedicated hook would add
# a nicer `kind` label and nothing else, in exchange for a call inside the gate
# whose entire design is to fail closed. This pins the free behaviour instead, so
# it cannot regress silently.


@pytest.mark.asyncio
async def test_a_refused_tool_is_recorded_so_recovery_can_recall_it(monkeypatch) -> None:
    from navig.agent.tool_permissions import ToolPermissionDenied

    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: True
    )

    class _DenyingRegistry(_Registry):
        def dispatch(self, name, *a, **k):
            raise ToolPermissionDenied(name)

    from navig.agent.conv import ConversationalAgent as ConvAgent

    monkeypatch.setattr(
        "navig.agent.speculative.get_speculative_executor", lambda: None
    )
    _apply_common_patches(monkeypatch, _DenyingRegistry())

    responses = iter(
        [
            SimpleNamespace(
                content=None,
                tool_calls=[_tool_call("ssh_run", {"cmd": "rm -rf /"})],
                usage=_fake_usage(),
            ),
            SimpleNamespace(content="done", tool_calls=None, usage=_fake_usage()),
        ]
    )

    class _FakeClient:
        async def complete(self, request):
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    agent = ConvAgent()
    assert await agent.run_agentic(message="delete everything", max_iterations=4) == "done"

    index = get_session_index()
    assert index is not None
    (event,) = index.recent(agent._session_id)
    assert "not permitted" in event.text  # the refusal itself is what got stored
    assert index.search(agent._session_id, "permitted"), "a refusal must be recoverable"
