"""`memory.enabled` and incognito are real switches, not decoration.

`navig config` showed a ``memory.enabled`` row that nothing read: memory stayed on
whatever the user set. These tests pin the switch at every gate the conversational
agent and the memory tools go through.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from navig.memory import controls


class _Cfg:
    def __init__(self, value):
        self._value = value

    def get(self, key, default=None):
        return self._value if key == controls.ENABLED_KEY else default


@pytest.fixture
def memory_switch(monkeypatch):
    """Set memory.enabled to a raw config value (strings included, like `config set`)."""

    def _set(value):
        monkeypatch.setattr("navig.config.get_config_manager", lambda: _Cfg(value))

    return _set


class TestMemoryEnabled:
    @pytest.mark.parametrize("raw", [False, "false", "off", "0", "no"])
    def test_off_values_read_as_off(self, memory_switch, raw):
        memory_switch(raw)
        assert controls.memory_enabled() is False

    @pytest.mark.parametrize("raw", [True, "true", "on", "1"])
    def test_on_values_read_as_on(self, memory_switch, raw):
        memory_switch(raw)
        assert controls.memory_enabled() is True

    def test_unreadable_config_reads_as_off(self, monkeypatch):
        def _boom():
            raise OSError("config locked")

        monkeypatch.setattr("navig.config.get_config_manager", _boom)
        assert controls.memory_enabled() is False


class TestIncognitoScope:
    def test_scope_sets_and_restores(self):
        assert controls.is_incognito() is False
        with controls.incognito_scope(True):
            assert controls.is_incognito() is True
            assert "incognito" in (controls.memory_off_reason() or "")
        assert controls.is_incognito() is False

    def test_scope_reaches_worker_threads(self):
        async def _probe():
            with controls.incognito_scope(True):
                return await asyncio.to_thread(controls.is_incognito)

        assert asyncio.run(_probe()) is True


class TestAgentGates:
    def _agent(self):
        from navig.agent.conv.agent import ConversationalAgent

        return ConversationalAgent(ai_client=None, soul_content="You are NAVIG.")

    def test_memory_off_means_no_recall_and_no_extractor(self, memory_switch):
        memory_switch("false")
        agent = self._agent()
        assert agent._get_memory_components() == (None, None)
        assert agent._recall_block("what do you know about me") == ""

    def test_switch_flipped_mid_session_applies_next_turn(self, memory_switch):
        agent = self._agent()
        agent._fact_retriever = MagicMock()
        agent._mem_extractor = MagicMock()
        memory_switch(True)
        assert agent._get_memory_components()[0] is agent._fact_retriever
        memory_switch("false")
        assert agent._get_memory_components() == (None, None)

    def test_incognito_agent_has_no_memory_and_no_persisted_history(self, memory_switch):
        memory_switch(True)
        agent = self._agent()
        agent._fact_retriever = MagicMock()
        agent._mem_extractor = MagicMock()
        agent.set_incognito(True)
        assert agent._get_memory_components() == (None, None)
        assert agent._history.persist is False


class TestHistoryPersistFlag:
    def test_non_persistent_history_writes_nothing(self, tmp_path, monkeypatch):
        from navig.agent.conv import history as hist

        monkeypatch.setattr(hist, "config_dir", lambda: tmp_path)
        h = hist.ConversationHistory(user_id="telegram:user:1:incognito", persist=False)
        h.add("user", "my secret plan")
        h.add("assistant", "noted")
        assert len(h.get_messages()) == 2
        assert not (tmp_path / "history").exists()

    def test_non_persistent_history_does_not_reload_the_file(self, tmp_path, monkeypatch):
        import time

        from navig.agent.conv import history as hist

        monkeypatch.setattr(hist, "config_dir", lambda: tmp_path)
        f = tmp_path / "history" / "k.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"role": "user", "content": "old", "ts": time.time()}) + "\n")
        assert hist.ConversationHistory(user_id="k").get_messages()  # control
        assert hist.ConversationHistory(user_id="k", persist=False).get_messages() == []


class TestMemoryTools:
    def test_write_refused_when_memory_off(self, memory_switch):
        from navig.agent.tools.memory_tools import MemoryWriteTool

        memory_switch("false")
        store = MagicMock()
        with patch("navig.memory.key_facts.KeyFactStore", return_value=store):
            result = asyncio.run(MemoryWriteTool().run({"content": "likes tea"}))
        assert result.success is False and "memory.enabled" in (result.error or "")
        store.upsert.assert_not_called()

    def test_read_refused_in_incognito(self, memory_switch):
        from navig.agent.tools.memory_tools import MemoryReadTool

        memory_switch(True)

        async def _run():
            with controls.incognito_scope(True):
                return await MemoryReadTool().run({"query": "tea"})

        result = asyncio.run(_run())
        assert result.success is False and "incognito" in (result.error or "")

    def test_delete_still_works_when_memory_off(self, memory_switch):
        from navig.agent.tools.memory_tools import MemoryDeleteTool

        memory_switch("false")
        store = MagicMock()
        store.soft_delete.return_value = True
        with patch("navig.memory.key_facts.KeyFactStore", return_value=store):
            result = asyncio.run(MemoryDeleteTool().run({"id": "f1"}))
        assert result.success is True

    def test_read_finds_approved_facts_only(self, memory_switch, tmp_path):
        # memory_read called a `store.search` KeyFactStore never had, so it answered
        # "No matching memories found." for every query. Pending facts stay hidden.
        from navig.agent.tools.memory_tools import MemoryReadTool
        from navig.memory.key_facts import KeyFact, KeyFactStore

        memory_switch(True)
        real = KeyFactStore(db_path=tmp_path / "kf.db")
        real.upsert(KeyFact(content="Prefers green tea in the morning"))
        real.upsert(KeyFact(content="Maybe likes green smoothies", approved=None))
        with patch("navig.memory.key_facts.KeyFactStore", return_value=real):
            result = asyncio.run(MemoryReadTool().run({"query": "green"}))
        assert result.success is True
        contents = [r["content"] for r in result.output["results"]]
        assert "Prefers green tea in the morning" in contents
        assert all("smoothies" not in c for c in contents)

    def test_write_reports_redaction(self, memory_switch, tmp_path):
        from navig.agent.tools.memory_tools import MemoryWriteTool
        from navig.memory.key_facts import KeyFactStore

        memory_switch(True)
        real = KeyFactStore(db_path=tmp_path / "kf.db")
        with patch("navig.memory.key_facts.KeyFactStore", return_value=real):
            result = asyncio.run(
                MemoryWriteTool().run({"content": "Router password is hunter2xyz!"})
            )
        assert result.success is True
        assert result.output["redacted"] is True
        assert "hunter2xyz" not in json.dumps(result.output)


class TestChannelRouterIncognito:
    def _router(self):
        from navig.gateway.channel_router import ChannelRouter

        router = ChannelRouter.__new__(ChannelRouter)
        router.gateway = SimpleNamespace(config=SimpleNamespace(default_agent="default"))
        return router

    def test_incognito_key_builds_an_incognito_agent(self, tmp_path, monkeypatch):
        from navig.agent.conv import history as hist
        from navig.gateway.channel_router import INCOGNITO_SUFFIX

        monkeypatch.setattr(hist, "config_dir", lambda: tmp_path)
        router = self._router()
        agent = router._get_conversational_agent(f"telegram:dm:1{INCOGNITO_SUFFIX}")
        assert agent.incognito is True
        assert agent._history.persist is False
        normal = router._get_conversational_agent("telegram:dm:1")
        assert normal.incognito is False and normal is not agent

    def test_drop_conv_agent_forgets_the_incognito_twin(self, tmp_path, monkeypatch):
        from navig.agent.conv import history as hist
        from navig.gateway.channel_router import INCOGNITO_SUFFIX

        monkeypatch.setattr(hist, "config_dir", lambda: tmp_path)
        router = self._router()
        key = f"telegram:dm:1{INCOGNITO_SUFFIX}"
        first = router._get_conversational_agent(key)
        router._drop_conv_agent(key)
        assert router._get_conversational_agent(key) is not first
