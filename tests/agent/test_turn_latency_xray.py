"""Latency X-ray: per-turn stage ledger (prep / first token / gen / tools / approval).

The stages exist so `/trace` can answer "why was that slow?" without log
archaeology. What the tests pin:

* a run WRITES the ledger — the wiring that a written module alone does not prove;
* one entry per turn, with the turn's own stages (not the run's cumulative time);
* the split is real (a slow model call shows up as ttft/gen, not as prep);
* approval waits are carved out of the tool stage;
* the bars render every non-zero stage and never exceed their width;
* telemetry can never take a turn down with it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import navig.agent.turn_latency as tl


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


def _usage():
    return {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def _patch_agent_env(monkeypatch, registry=None):
    """Everything run_agentic needs except the LLM client itself."""

    class _Registry:
        def get_openai_schemas(self, toolsets):
            return []

        def available_names(self, toolsets):
            return ["search"]

        def dispatch(self, name, args, vault_injector=None):
            return f"result-{name}"

    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr(
        "navig.agent.agent_tool_registry._AGENT_REGISTRY", registry or _Registry()
    )
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr(
        "navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config()
    )
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())


def _ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "perf" / "turn_latency.jsonl"


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch):
    """Point the ledger at this test's tmp dir (never the operator's history)."""
    target = _ledger_path(tmp_path)
    monkeypatch.setattr(tl, "_path", lambda: target)
    monkeypatch.setattr(tl, "_appends_since_trim", 0)
    return target


class TestLedger:
    def test_record_and_read_newest_first(self, ledger):
        tl.record_turn(
            turn=1,
            session_key="s",
            mode="small_talk",
            provider="openrouter",
            model="m",
            prep_ms=10,
            ttft_ms=100,
            llm_ms=300,
            tools_ms=0,
            approval_ms=0,
            turn_ms=310,
            prompt_chars=50,
        )
        tl.record_turn(
            turn=2,
            session_key="s",
            mode="small_talk",
            provider="openrouter",
            model="m",
            prep_ms=20,
            ttft_ms=200,
            llm_ms=400,
            tools_ms=0,
            approval_ms=0,
            turn_ms=420,
            prompt_chars=60,
        )

        rows = tl.read_last(20)
        assert [r["turn"] for r in rows] == [2, 1], f"newest-first expected, got {rows}"
        assert rows[0]["gen_ms"] == rows[0]["llm_ms"] - rows[0]["ttft_ms"]
        assert rows[0]["model"] == "openrouter:m"

    def test_read_last_caps_at_n(self, ledger):
        for i in range(1, 31):
            tl.record_turn(
                turn=i,
                session_key="s",
                mode="m",
                provider="p",
                model="x",
                prep_ms=1,
                ttft_ms=1,
                llm_ms=1,
                tools_ms=0,
                approval_ms=0,
                turn_ms=1,
                prompt_chars=1,
            )
        rows = tl.read_last(5)
        assert len(rows) == 5
        # Newest first: the last five turns written, not the first five.
        assert [r["turn"] for r in rows] == [30, 29, 28, 27, 26]

    def test_file_stays_bounded(self, ledger):
        for i in range(tl._TRIM_EVERY + 5):
            tl.record({"turn": i})
        lines = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) <= tl.KEEP, f"ledger grew past KEEP: {len(lines)}"

    def test_record_never_raises(self, ledger, monkeypatch, caplog):
        def boom(*a, **k):
            raise OSError("disk gone")

        monkeypatch.setattr(tl, "_path", boom)
        # Must not raise — telemetry is never allowed to take a turn down.
        tl.record({"turn": 1})

    def test_read_last_tolerates_corrupt_lines(self, ledger):
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"turn": 1}\nnot json\n{"turn": 2}\n', encoding="utf-8")
        rows = tl.read_last(10)
        assert [r["turn"] for r in rows] == [2, 1]


class TestBars:
    def test_every_nonzero_stage_is_visible(self):
        turns = [
            {
                "turn": 3,
                "prep_ms": 500,
                "ttft_ms": 1500,
                "gen_ms": 300,
                "tools_ms": 900,
                "approval_ms": 0,
                "turn_ms": 3200,
            }
        ]
        bar = tl.render_bars(turns, width=28)[0]
        assert "█" in bar, f"prep stage missing: {bar}"
        assert "▓" in bar, f"ttft stage missing: {bar}"
        assert "▒" in bar, f"gen stage missing: {bar}"
        assert "░" in bar, f"tools stage missing: {bar}"

    def test_bar_never_exceeds_width(self):
        turns = [
            {"turn": i, "prep_ms": 1, "ttft_ms": 1, "gen_ms": 1, "tools_ms": 1, "turn_ms": 4}
            for i in range(1, 6)
        ]
        lines = tl.render_bars(turns, width=20)
        # Assert before looping: an empty return would otherwise skip every
        # assertion below and pass while checking nothing.
        assert len(lines) == len(turns), f"expected a bar per turn, got {lines}"
        for line in lines:
            inside = line.split("│")[1]
            assert len(inside) <= 20, f"bar wider than track: {inside!r}"

    def test_approval_wait_is_called_out(self):
        turns = [
            {
                "turn": 1,
                "prep_ms": 10,
                "ttft_ms": 20,
                "gen_ms": 5,
                "tools_ms": 4000,
                "approval_ms": 4000,
                "turn_ms": 4035,
            }
        ]
        line = tl.render_bars(turns, width=20)[0]
        assert "approval" in line
        assert "4.0s" in line

    def test_empty_ledger_renders_nothing(self):
        assert tl.render_bars([]) == []


@pytest.mark.asyncio
async def test_a_run_writes_one_entry_per_turn(monkeypatch, ledger):
    """The wiring itself: a two-turn run (tool call, then answer) writes two rows."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _patch_agent_env(monkeypatch)

    responses = iter(
        [
            SimpleNamespace(
                content=None,
                tool_calls=[_tool_call("search", {"query": "x"}, "id-1")],
                usage=_usage(),
            ),
            SimpleNamespace(content="done", tool_calls=None, usage=_usage()),
        ]
    )

    class _FakeClient:
        async def complete(self, request):
            await asyncio.sleep(0.02)  # a measurable model call, not an instant one
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    agent = ConvAgent()
    result = await agent.run_agentic(message="do a two-turn thing", max_iterations=5)
    assert result == "done"

    rows = tl.read_last(20)
    assert len(rows) == 2, f"expected one row per turn, got {len(rows)}: {rows}"
    assert [r["turn"] for r in rows] == [2, 1], rows

    turn1, turn2 = rows[1], rows[0]
    # Stage split is real: the model call is llm, and ttft sits inside it.
    assert turn1["llm_ms"] >= 20, f"model sleep not captured: {turn1}"
    assert 0 < turn1["ttft_ms"] <= turn1["llm_ms"], turn1
    assert turn1["tools"] == ["search"], turn1
    assert turn1["tools_ms"] >= 0
    # Turn 1's prep is its OWN prep — the run-cumulative bug made later turns
    # inherit earlier model time (see test_run_agentic_prework_is_per_turn).
    assert turn2["prep_ms"] < 500, f"turn 2 prep inherited earlier time: {turn2}"
    assert turn1["prompt_chars"] > 0


@pytest.mark.asyncio
async def test_telemetry_failure_never_breaks_a_turn(monkeypatch, tmp_path):
    """A ledger that cannot write must not fail the conversation."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _patch_agent_env(monkeypatch)

    class _FakeClient:
        async def complete(self, request):
            return SimpleNamespace(content="ok", tool_calls=None, usage=_usage())

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    def explode(*a, **k):
        raise RuntimeError("ledger is down")

    monkeypatch.setattr("navig.agent.turn_latency.record_turn", explode)

    agent = ConvAgent()
    result = await agent.run_agentic(message="hi", max_iterations=3)
    assert result == "ok"


@pytest.mark.asyncio
async def test_approval_wait_is_carved_out_of_the_tool_stage(monkeypatch, ledger):
    """A tool blocked on human approval shows up as approval time, not as model
    time — that is the split the X-ray exists to show."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _patch_agent_env(monkeypatch)

    async def slow_gate(name, parameters=None, session_key=""):
        await asyncio.sleep(0.15)
        return None  # approved

    monkeypatch.setattr("navig.tools.approval.gate_agent_tool_call", slow_gate, raising=False)

    responses = iter(
        [
            SimpleNamespace(
                content=None,
                tool_calls=[_tool_call("search", {"query": "x"}, "id-1")],
                usage=_usage(),
            ),
            SimpleNamespace(content="done", tool_calls=None, usage=_usage()),
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
    await agent.run_agentic(message="search something", max_iterations=5)

    rows = tl.read_last(20)
    turn_with_tool = next(r for r in rows if r.get("tools"))
    assert turn_with_tool["approval_ms"] >= 100, (
        f"approval wait not captured (expected >=100ms): {turn_with_tool}"
    )


class TestTraceBlock:
    """The block that goes into /trace — its whole job is to fit."""

    def _turns(self, n):
        """Same order read_last() hands over: newest first (turn n … 1)."""
        return [
            {
                "turn": i,
                "prep_ms": 100,
                "ttft_ms": 400,
                "gen_ms": 200,
                "tools_ms": 50,
                "approval_ms": 0,
                "turn_ms": 750,
            }
            for i in range(n, 0, -1)
        ]

    def test_block_starts_with_the_title_and_fences(self):
        block = tl.render_trace_block(0, self._turns(3))
        assert block[0].startswith("<b>Latency</b>")
        assert block[1] == "<code>"
        assert block[-1] == "</code>"
        assert len(block) == 3 + 3, block  # title + 2 fences + 3 rows

    def test_block_fits_the_remaining_budget(self):
        turns = self._turns(20)
        for used in (0, 1500, 3000, 3900):
            block = tl.render_trace_block(used, turns, budget=4000)
            size = len("\n".join(block))
            assert used + size <= 4000, (
                f"used={used} block={size} overflows the 4000 budget"
            )

    def test_a_full_trace_gets_no_block_rather_than_a_broken_message(self):
        assert tl.render_trace_block(4000, self._turns(20)) == []
        assert tl.render_trace_block(3999, self._turns(20)) == []

    def test_rows_are_dropped_oldest_first(self):
        block = tl.render_trace_block(3400, self._turns(20))
        rows = [r for r in block if "│" in r]
        assert rows, "expected some rows to fit"
        # Newest-first order is preserved: turn numbers descend.
        nums = [int(r.split("│")[0].strip()) for r in rows]
        assert nums == sorted(nums, reverse=True), nums
        assert len(rows) < 20, "the budget should have dropped older rows"


@pytest.mark.asyncio
async def test_trace_renders_with_the_latency_block_and_fits(
    monkeypatch, ledger, caplog
):
    """End to end: /trace composes a message that carries the bars and stays
    under Telegram's cap — the failure this guard exists to prevent."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    class _FakeChannel(TelegramCommandsMixin):
        def __init__(self):
            self.sent: list[str] = []
            self._user_model_prefs: dict = {}

        async def send_message(self, chat_id, text, keyboard=None, **kwargs):
            self.sent.append(text)

        def _is_debug_mode(self, user_id):
            return False

        async def _probe_bridge_grid(self):
            return (False, "ws://127.0.0.1:42070")

        def _load_recent_messages(self, user_id, chat_id=0):
            return [{"role": "user", "content": "hello"}, {"role": "ai", "content": "hi"}]

    # Two turns of evidence in the ledger, then render.
    for i in (1, 2):
        tl.record_turn(
            turn=i,
            session_key="s",
            mode="small_talk",
            provider="openrouter",
            model="m",
            prep_ms=40 * i,
            ttft_ms=300 * i,
            llm_ms=500 * i,
            tools_ms=10 * i,
            approval_ms=0,
            turn_ms=850 * i,
            prompt_chars=1200,
        )

    chan = _FakeChannel()
    await chan._handle_trace(chat_id=123, user_id=123)

    assert chan.sent, "trace sent nothing"
    msg = chan.sent[0]
    assert "<b>Latency</b>" in msg, msg[:400]
    assert "│" in msg, "stage bars missing from /trace"
    assert len(msg) <= 4096, f"/trace exceeds the Telegram cap: {len(msg)}"


@pytest.mark.asyncio
async def test_trace_still_renders_when_the_ledger_is_empty(monkeypatch, ledger):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    class _FakeChannel(TelegramCommandsMixin):
        def __init__(self):
            self.sent: list[str] = []
            self._user_model_prefs: dict = {}

        async def send_message(self, chat_id, text, keyboard=None, **kwargs):
            self.sent.append(text)

        def _is_debug_mode(self, user_id):
            return False

        async def _probe_bridge_grid(self):
            return (False, "ws://127.0.0.1:42070")

        def _load_recent_messages(self, user_id, chat_id=0):
            return []

    chan = _FakeChannel()
    await chan._handle_trace(chat_id=1, user_id=1)
    assert chan.sent
    assert "<b>Latency</b>" not in chan.sent[0]


@pytest.mark.asyncio
async def test_ttft_counts_a_tool_call_fragment_as_the_first_token(monkeypatch, ledger):
    """A turn that opens with a tool call has a first token too.

    Counting only TEXT deltas read such a turn as "first token at the end of the
    call": the model streams its decision as tool-call fragments, never as prose.
    The stage must land when the first payload chunk arrives.
    """
    from navig.agent.conv import ConversationalAgent as ConvAgent
    from navig.providers.clients import StreamChunk, ToolCall

    _patch_agent_env(monkeypatch)

    async def stream_turn1():
        # Metadata-only first chunk (model/provider) — not a token.
        yield StreamChunk(model="gpt-4o")
        await asyncio.sleep(0.06)
        # The model's first token is the tool call itself.
        yield StreamChunk(
            tool_call_delta=ToolCall(id="tc1", name="search", arguments='{"query": ')
        )
        await asyncio.sleep(0.08)
        yield StreamChunk(tool_call_delta=ToolCall(id="tc1", name="search", arguments='"x"}'))
        yield StreamChunk(finish_reason="tool_calls")

    async def stream_turn2():
        await asyncio.sleep(0.02)
        yield StreamChunk(delta="done")
        yield StreamChunk(finish_reason="stop")

    streams = iter([stream_turn1(), stream_turn2()])

    class _FakeClient:
        async def complete_stream(self, request):
            gen = next(streams)
            async for chunk in gen:
                yield chunk

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    agent = ConvAgent()
    result = await agent.run_agentic(
        message="search something",
        max_iterations=5,
        on_partial=lambda text: asyncio.sleep(0),
    )
    assert result == "done"

    rows = tl.read_last(20)
    first_turn = next(r for r in rows if r.get("tools"))
    # ~60ms of prefill before the first payload chunk, ~140ms more of generation.
    assert first_turn["ttft_ms"] < 120, (
        f"ttft attributed to the END of the call, not the first token: {first_turn}"
    )
    assert first_turn["llm_ms"] > first_turn["ttft_ms"] + 40, first_turn


@pytest.mark.asyncio
async def test_a_timed_out_turn_is_still_recorded(monkeypatch, ledger):
    """The turns the X-ray exists for must not be the ones it misses.

    A provider that never answers breaks out of the loop before the success-path
    clock close; without closing it on the break, the ledger would show a turn
    that waited the whole timeout with no model time attributed to it.
    """
    import navig.agent.conv.agent as agent_mod
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _patch_agent_env(monkeypatch)
    monkeypatch.setattr(agent_mod, "_AGENTIC_CHAT_TIMEOUT", 0.05)
    monkeypatch.setattr(agent_mod, "_AGENTIC_CLIENT_TIMEOUT", 0.05)

    class _HungClient:
        async def complete(self, request):
            await asyncio.sleep(5)  # far past every timeout above

        async def complete_stream(self, request):  # pragma: no cover - never reached
            if False:
                yield None

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _HungClient(),
    )

    agent = ConvAgent()
    result = await agent.run_agentic(message="hi", max_iterations=3)

    assert "timed out" in str(result), result
    rows = tl.read_last(20)
    assert rows, "a timed-out turn left nothing in the ledger"
    turn = rows[0]
    assert turn["llm_ms"] >= 40, f"the timeout wait was not attributed: {turn}"
    assert turn["ttft_ms"] >= 40, f"no first-token stage for a dead provider: {turn}"


def test_ledger_lives_in_the_config_dirs_perf_dir():
    """Pin the location (the fixture above patches _path, so this is what keeps
    the real resolution honest): config_dir() honours NAVIG_CONFIG_DIR, which is
    how a test session writes to its own sandbox instead of the operator's).
    """
    from navig.platform.paths import config_dir

    assert tl._path() == config_dir() / "perf" / "turn_latency.jsonl", (
        f"ledger resolved to {tl._path()} under config_dir()={config_dir()}"
    )


@pytest.mark.asyncio
async def test_trace_renders_when_the_ledger_reader_breaks(monkeypatch):
    """The X-ray is an extra section, never a dependency of /trace.

    If the ledger reader raises (bad file, future refactor), the command must
    still send the rest of the trace.
    """
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    class _FakeChannel(TelegramCommandsMixin):
        def __init__(self):
            self.sent: list[str] = []
            self._user_model_prefs: dict = {}

        async def send_message(self, chat_id, text, keyboard=None, **kwargs):
            self.sent.append(text)

        def _is_debug_mode(self, user_id):
            return False

        async def _probe_bridge_grid(self):
            return (False, "ws://127.0.0.1:42070")

        def _load_recent_messages(self, user_id, chat_id=0):
            return []

    def boom(n=20):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr("navig.agent.turn_latency.read_last", boom)

    chan = _FakeChannel()
    await chan._handle_trace(chat_id=1, user_id=1)  # must not raise
    assert chan.sent, "/trace failed to send when the ledger broke"
    assert "<b>Latency</b>" not in chan.sent[0]
    assert "Recent Trace" in chan.sent[0]
