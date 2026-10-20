"""`read_aloud` — "read this to me", from the agent.

The tool's whole job is to START a background reading and return, so what is pinned is the
contract around that:

* it is registered, offered every turn, and only when the plugin is installed;
* the chat it reads to is the conversation's OWN (session key injected by the agent loop,
  absent from the schema so the model cannot aim it elsewhere);
* it shares the job key with ``/read`` — so ``/read stop`` stops a reading the agent began;
* a big text is NOT started: the model gets the quote and the instruction to ask, and the
  confirming call carries no text (re-emitting 20,000 characters to say "yes" is absurd);
* every outcome comes back phrased for the model, with the next step.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.agent.tools.read_aloud_tool import ReadAloudTool, register_read_aloud_tools
from navig.telegram import read_actions

SESSION = "telegram:user:42"


@pytest.fixture
def engine():
    pytest.importorskip("navig_audio")
    from navig_audio import reader, reading_job

    return SimpleNamespace(reader=reader, job=reading_job)


def _plan(engine, text="Once upon a time there was a king. " * 40):
    return engine.reader.plan_reading(text, engine.reader.ReadOptions(lang="en"))


@pytest.fixture
def jobs(engine, monkeypatch):
    state = SimpleNamespace(begin=[], confirm=[], stop=[], discard=[], result=None, stop_result=False, discard_result=False)

    async def begin(key, text, options, **kw):
        state.begin.append({"key": key, "text": text, "options": options, **kw})
        return state.result

    async def confirm(key, **kw):
        state.confirm.append({"key": key, **kw})
        return state.result

    monkeypatch.setattr(engine.job, "begin", begin)
    monkeypatch.setattr(engine.job, "confirm", confirm)
    monkeypatch.setattr(engine.job, "stop", lambda k: (state.stop.append(k), state.stop_result)[1])
    monkeypatch.setattr(engine.job, "discard_pending", lambda k: (state.discard.append(k), state.discard_result)[1])
    return state


def _result(engine, outcome, **kw):
    return engine.job.StartResult(getattr(engine.job.Outcome, outcome), plan=_plan(engine), **kw)


# ── registration and schema ───────────────────────────────────────────────────


def test_it_is_registered_in_core_and_offered_every_turn():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_all_tools

    register_all_tools()
    entry = _AGENT_REGISTRY._entries.get("read_aloud")
    assert entry is not None, "a tool nothing registers is the written-tested-never-wired defect"
    assert entry.toolset == "core"
    assert entry.check_fn is not None, "it must hide itself when the plugin is not installed"


def test_it_is_hidden_when_the_plugin_is_absent(monkeypatch):
    from navig.agent import agent_tool_registry as reg
    from navig.agent.tools import read_aloud_tool as mod

    try:
        monkeypatch.setattr(mod, "_plugin_installed", lambda: False)
        register_read_aloud_tools()
        assert reg._AGENT_REGISTRY._entries["read_aloud"].check_fn() is False
        monkeypatch.setattr(mod, "_plugin_installed", lambda: True)
        register_read_aloud_tools()
        assert reg._AGENT_REGISTRY._entries["read_aloud"].check_fn() is True
    finally:
        # The registry is process-global and re-registering captured a PATCHED check_fn:
        # put the real one back so no later test sees a tool that is hidden (or forced on).
        monkeypatch.undo()
        register_read_aloud_tools()
    assert reg._AGENT_REGISTRY._entries["read_aloud"].check_fn is mod._plugin_installed


def test_the_schema_hides_the_session_and_asks_for_the_text_verbatim():
    tool = ReadAloudTool()
    assert tool.needs_session is True
    names = {p["name"] for p in tool.parameters}
    assert names == {"text", "confirmed", "voice", "language", "action"}
    assert "_session_id" not in names, "the model must not be able to aim the audio at another chat"
    assert "EXACTLY" in tool.description and "never shortened" in tool.description
    assert "/read" in tool.description, "it must point long texts at the .txt route"


# ── starting ──────────────────────────────────────────────────────────────────


async def test_it_starts_a_reading_for_this_conversations_chat_and_returns(engine, jobs):
    jobs.result = _result(engine, "STARTED", spend=1500)
    out = await ReadAloudTool().run({"text": "Once upon a time.", "_session_id": SESSION})

    assert out.success and "Started" in out.output and "do not wait" in out.output
    call = jobs.begin[0]
    assert call["key"] == "telegram:42" == read_actions.job_key(42), (
        "the tool and /read must share a job key, or /read stop could not stop it"
    )
    assert call["text"] == "Once upon a time." and call["confirmed"] is False


async def test_voice_and_language_reach_the_engine_options(engine, jobs):
    jobs.result = _result(engine, "STARTED")
    await ReadAloudTool().run(
        {"text": "Bonjour tout le monde.", "voice": "Marcus", "language": "FR", "_session_id": SESSION}
    )
    opts = jobs.begin[0]["options"]
    assert opts.voice == "Marcus" and opts.lang == "fr"


async def test_blank_voice_and_language_mean_the_defaults(engine, jobs):
    jobs.result = _result(engine, "STARTED")
    await ReadAloudTool().run({"text": "hello", "voice": "  ", "language": "", "_session_id": SESSION})
    opts = jobs.begin[0]["options"]
    assert opts.voice is None and opts.lang is None


# ── cost ──────────────────────────────────────────────────────────────────────


async def test_a_big_text_is_quoted_not_started_and_the_model_is_told_to_ask(engine, jobs):
    jobs.result = _result(engine, "NEEDS_CONFIRM", spend=40_000)
    out = await ReadAloudTool().run({"text": "x" * 50, "_session_id": SESSION})
    assert out.success, "a quote is not an error"
    assert "NOT started" in out.output and "40,000" in out.output
    assert "confirmed=true" in out.output and "NO text" in out.output


async def test_confirming_carries_no_text(engine, jobs):
    """The text is held server-side: re-emitting a chapter through the model to say 'yes'
    would cost thousands of output tokens and risk truncating it."""
    jobs.result = _result(engine, "STARTED")
    out = await ReadAloudTool().run({"confirmed": True, "_session_id": SESSION})
    assert out.success and jobs.confirm and jobs.confirm[0]["key"] == "telegram:42"
    assert jobs.begin == []


async def test_confirmed_with_text_starts_a_confirmed_reading(engine, jobs):
    jobs.result = _result(engine, "STARTED")
    await ReadAloudTool().run({"text": "hello there", "confirmed": True, "_session_id": SESSION})
    assert jobs.begin[0]["confirmed"] is True and jobs.confirm == []


async def test_not_enough_credits_is_a_failure_with_the_numbers(engine, jobs):
    jobs.result = _result(engine, "INSUFFICIENT", spend=40_000, remaining=1_000)
    out = await ReadAloudTool().run({"text": "x", "_session_id": SESSION})
    assert not out.success and "40,000" in out.output and "1,000" in out.output
    assert out.error == "insufficient credits"


# ── the other outcomes ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("outcome", "needle", "error"),
    [
        ("BUSY", "action='stop'", "busy"),
        ("NOTHING_PENDING", "Send the text again", "nothing pending"),
        ("EMPTY", "readable text", "no text"),
        ("UNAVAILABLE", "could not start", "unavailable"),
    ],
)
async def test_each_outcome_tells_the_model_what_to_do_next(engine, jobs, outcome, needle, error):
    jobs.result = _result(engine, outcome)
    out = await ReadAloudTool().run({"text": "x", "_session_id": SESSION})
    assert not out.success and needle in out.output and out.error == error


async def test_a_bad_option_is_reported_not_raised(engine, jobs, monkeypatch):
    def boom(**kw):
        raise engine.reader.ReadError("audio.read.speed is not a number")

    monkeypatch.setattr(engine.reader, "options_from_config", boom)
    out = await ReadAloudTool().run({"text": "x", "_session_id": SESSION})
    assert not out.success and "not a number" in out.output


# ── stop ──────────────────────────────────────────────────────────────────────


async def test_stop_cancels_a_running_reading(engine, jobs):
    jobs.stop_result = True
    out = await ReadAloudTool().run({"action": "stop", "_session_id": SESSION})
    assert out.success and "Stopping" in out.output and jobs.stop == ["telegram:42"]


async def test_stop_drops_a_pending_one_and_says_nothing_was_spent(engine, jobs):
    jobs.discard_result = True
    out = await ReadAloudTool().run({"action": "stop", "_session_id": SESSION})
    assert out.success and "Nothing was spent" in out.output


async def test_stop_with_nothing_running_is_a_plain_statement(engine, jobs):
    out = await ReadAloudTool().run({"action": "STOP", "_session_id": SESSION})
    assert out.success and "Nothing is being read" in out.output


# ── no chat, no plugin ────────────────────────────────────────────────────────


@pytest.mark.parametrize("session", ["", "cli", None])
async def test_without_a_chat_address_it_says_so_and_starts_nothing(engine, jobs, session):
    out = await ReadAloudTool().run({"text": "hello", "_session_id": session})
    assert not out.success and out.error == "no chat session" and jobs.begin == []


async def test_without_the_plugin_it_explains(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "navig_audio" or name.startswith("navig_audio."):
            raise ImportError("no navig_audio")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = await ReadAloudTool().run({"text": "hello", "_session_id": SESSION})
    assert not out.success and out.error == "plugin missing" and "navig-audio" in out.output


# ── delivery goes through the shared, honest seam ─────────────────────────────


async def test_parts_and_endings_reach_the_chat_through_the_shared_helpers(engine, jobs, monkeypatch, tmp_path):
    from navig.agent.tools import read_aloud_tool as mod
    from navig.agent.tools.chat_media_tools import Delivery

    sent_files, said = [], []

    async def fake_deliver(session_id, path, caption=""):
        sent_files.append((session_id, path.name, caption))
        return Delivery(ok=True, channel="telegram", thread="42")

    async def fake_say(session_id, text):
        said.append((session_id, text))
        return True

    monkeypatch.setattr(mod, "deliver_to_session", fake_deliver)
    monkeypatch.setattr(mod, "say_to_session", fake_say)
    jobs.result = _result(engine, "STARTED")
    await ReadAloudTool().run({"text": "hello there", "_session_id": SESSION})

    deliver, notify = jobs.begin[0]["deliver"], jobs.begin[0]["notify"]
    part_path = tmp_path / "p1.mp3"
    part_path.write_bytes(b"x")
    part = engine.reader.ReadPart(index=1, total=2, path=part_path, duration_s=61.0, chars=10)
    assert await deliver(part) is True
    assert sent_files == [(SESSION, "p1.mp3", "📖 1/2 · 1:01")]

    await notify(engine.job.ReadEvent("part", part=part))
    assert said == [], "a part is its own message"
    summary = engine.reader.ReadSummary(out_dir=tmp_path, parts=[part])
    await notify(engine.job.ReadEvent("done", summary=summary))
    assert said and said[0][0] == SESSION and "That's all" in said[0][1]


async def test_a_failed_delivery_is_reported_to_the_job_as_a_failure(engine, jobs, monkeypatch, tmp_path):
    from navig.agent.tools import read_aloud_tool as mod
    from navig.agent.tools.chat_media_tools import Delivery

    async def rejected(session_id, path, caption=""):
        return Delivery(ok=False, error="delivery failed", detail="rejected")

    monkeypatch.setattr(mod, "deliver_to_session", rejected)
    jobs.result = _result(engine, "STARTED")
    await ReadAloudTool().run({"text": "hello there", "_session_id": SESSION})
    part_path = tmp_path / "p.mp3"
    part_path.write_bytes(b"x")
    part = engine.reader.ReadPart(index=1, total=1, path=part_path, duration_s=1.0, chars=1)
    assert await jobs.begin[0]["deliver"](part) is False
