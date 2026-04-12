"""
Tests for the cinematic NAVIG bot pipeline.

Covers:
- ToolRegistry error isolation (failure must return ToolResult(success=False), not raise)
- Mode classifier correct classification
- StatusRenderer frame building
- Tool argument extraction helpers
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

# ─── ToolRegistry tests ────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_register_and_get(self):
        from navig.tools.registry import BaseTool, ToolRegistry, ToolResult

        class DummyTool(BaseTool):
            name = "dummy"
            description = "test"

            async def run(self, args, on_status=None):
                return ToolResult(name=self.name, success=True, output={"ok": True})

        reg = ToolRegistry()
        reg.register(DummyTool())
        assert reg.get("dummy") is not None
        assert reg.get("nonexistent") is None

    def test_names_sorted(self):
        from navig.tools.registry import BaseTool, ToolRegistry, ToolResult

        class ATool(BaseTool):
            name = "z_tool"
            description = ""

            async def run(self, args, on_status=None):
                return ToolResult(name=self.name, success=True)

        class BTool(BaseTool):
            name = "a_tool"
            description = ""

            async def run(self, args, on_status=None):
                return ToolResult(name=self.name, success=True)

        reg = ToolRegistry()
        reg.register(ATool())
        reg.register(BTool())
        assert reg.names() == ["a_tool", "z_tool"]

    def test_run_tool_not_registered_returns_failure(self):
        from navig.tools.registry import ToolRegistry

        reg = ToolRegistry()
        result = asyncio.run(reg.run_tool("ghost_tool", {}))
        assert result.success is False
        assert "not registered" in result.error

    def test_run_tool_exception_is_isolated(self):
        """Tool that raises must return ToolResult(success=False), not propagate."""
        from navig.tools.registry import BaseTool, ToolRegistry

        class BoomTool(BaseTool):
            name = "boom"
            description = ""

            async def run(self, args, on_status=None):
                raise ValueError("KABOOM")

        reg = ToolRegistry()
        reg.register(BoomTool())
        result = asyncio.run(reg.run_tool("boom", {}))
        assert result.success is False
        assert "KABOOM" in result.error

    def test_run_tool_timeout_returns_failure(self):
        """Slow tool exceeding 60s must be killed and return failure."""
        from navig.tools.registry import BaseTool, ToolRegistry, ToolResult

        class SlowTool(BaseTool):
            name = "slow"
            description = ""

            async def run(self, args, on_status=None):
                await asyncio.sleep(120)  # much longer than 60s cap
                return ToolResult(name=self.name, success=True)

        reg = ToolRegistry()
        reg.register(SlowTool())

        # Patch the timeout to 0.1s for speed

        orig = asyncio.wait_for

        async def fake_wait_for(coro, timeout):
            return await orig(coro, timeout=0.1)

        with patch.object(asyncio, "wait_for", wraps=fake_wait_for):
            result = asyncio.run(reg.run_tool("slow", {}))
        assert result.success is False
        assert "timed out" in result.error

    def test_on_status_callback_called(self):
        """Tool must call on_status during run."""
        from navig.tools.registry import BaseTool, ToolRegistry, ToolResult

        calls = []

        class StatsTool(BaseTool):
            name = "stats"
            description = ""

            async def run(self, args, on_status=None):
                await self._emit(on_status, "step1", "detail1", 50)
                return ToolResult(name=self.name, success=True)

        async def on_status(step, detail="", progress=0):
            calls.append((step, detail, progress))

        reg = ToolRegistry()
        reg.register(StatsTool())
        asyncio.run(reg.run_tool("stats", {}, on_status=on_status))
        assert len(calls) == 1
        assert calls[0][0] == "step1"

    def test_on_status_exception_does_not_crash_tool(self):
        """Broken on_status callback must not crash the tool execution."""
        from navig.tools.registry import BaseTool, ToolRegistry, ToolResult

        class StatsTool2(BaseTool):
            name = "stats2"
            description = ""

            async def run(self, args, on_status=None):
                await self._emit(on_status, "step", "", 10)
                return ToolResult(name=self.name, success=True)

        async def bad_on_status(step, detail="", progress=0):
            raise RuntimeError("callback boom")

        reg = ToolRegistry()
        reg.register(StatsTool2())
        result = asyncio.run(reg.run_tool("stats2", {}, on_status=bad_on_status))
        assert result.success is True  # tool succeeded despite bad callback


# ─── ToolResult.summary() tests ───────────────────────────────────────────────


class TestToolResult:
    def test_summary_success_dict(self):
        from navig.tools.registry import ToolResult

        r = ToolResult(name="t", success=True, output={"status": "ok", "latency_ms": 120})
        s = r.summary()
        assert "status" in s
        assert "ok" in s

    def test_summary_failure(self):
        from navig.tools.registry import ToolResult

        r = ToolResult(name="t", success=False, error="connection refused")
        assert "connection refused" in r.summary()

    def test_summary_none_values_hidden(self):
        from navig.tools.registry import ToolResult

        r = ToolResult(name="t", success=True, output={"x": None, "y": "val"})
        s = r.summary()
        assert "None" not in s
        assert "val" in s


# ─── Mode classifier tests ─────────────────────────────────────────────────────


class TestModeClassifier:
    def _classify(self, text: str) -> str:
        from navig.gateway.channels.telegram_mode_classifier import classify_mode

        return classify_mode(text)

    def test_hi_is_talk(self):
        assert self._classify("hi") == "TALK"

    def test_hello_is_talk(self):
        assert self._classify("hello there") == "TALK"

    def test_write_script_is_code(self):
        assert self._classify("write me a Python script to parse CSV") == "CODE"

    def test_fix_bug_is_code(self):
        assert self._classify("fix this function") == "CODE"

    def test_check_site_is_act(self):
        assert self._classify("check if google.com is up") == "ACT"

    def test_search_is_act(self):
        assert self._classify("search for the latest Python version") == "ACT"

    def test_explain_is_reason(self):
        assert self._classify("explain why nginx is slow") == "REASON"

    def test_what_is_is_reason(self):
        assert self._classify("what is a load balancer?") == "REASON"

    def test_compare_is_reason(self):
        assert self._classify("compare Redis and Memcached") == "REASON"

    def test_short_phrase_is_talk(self):
        assert self._classify("ok") == "TALK"

    def test_empty_is_talk(self):
        assert self._classify("") == "TALK"

    def test_url_in_text_prefers_act(self):
        assert self._classify("can you fetch https://example.com for me") == "ACT"

    def test_code_priority_over_act(self):
        # "implement" + "check" — CODE wins
        assert self._classify("implement a check function in Python") == "CODE"


# ─── select_tools_for_text tests ──────────────────────────────────────────────


class TestSelectTools:
    def _select(self, text: str):
        from navig.gateway.channels.telegram_mode_classifier import (
            select_tools_for_text,
        )

        return select_tools_for_text(text)

    def test_url_gives_site_check_and_browser_fetch(self):
        tools = self._select("check google.com")
        assert "site_check" in tools
        assert "browser_fetch" in tools

    def test_https_url(self):
        tools = self._select("fetch https://example.com/page")
        assert "site_check" in tools

    def test_search_keyword_gives_search(self):
        tools = self._select("search for latest news")
        assert "search" in tools

    def test_backtick_code_gives_sandbox(self):
        tools = self._select("run this: `print('hello')`")
        assert "code_exec_sandbox" in tools

    def test_fallback_gives_search(self):
        tools = self._select("what is the weather in Paris")
        assert "search" in tools

    def test_no_duplicates(self):
        tools = self._select("search google.com for python docs")
        assert len(tools) == len(set(tools))


# ─── extract_url tests ────────────────────────────────────────────────────────


class TestExtractUrl:
    def _extract(self, text: str):
        from navig.gateway.channels.telegram_mode_classifier import extract_url

        return extract_url(text)

    def test_https_url_extracted(self):
        assert self._extract("check https://example.com") == "https://example.com"

    def test_bare_domain_gets_https_prefix(self):
        from urllib.parse import urlparse

        result = self._extract("is google.com up")
        assert result is not None
        parsed = urlparse(result)
        assert parsed.scheme == "https"
        assert parsed.hostname in ("google.com", "www.google.com")

    def test_no_url_returns_none(self):
        assert self._extract("what is the meaning of life") is None


# ─── StatusRenderer frame building tests ─────────────────────────────────────


class TestStatusRenderer:
    def _make_renderer(self):
        from navig.gateway.channels.telegram_renderer import StatusRenderer

        channel = MagicMock()
        channel.edit_message = AsyncMock(return_value=None)
        return StatusRenderer(channel, chat_id=123, message_id=456)

    def test_initial_frame_shows_bar(self):
        from navig.gateway.channels.telegram_renderer import _EMPTY

        r = self._make_renderer()
        frame = r._build_frame()
        assert _EMPTY in frame

    def test_progress_bar_fills_proportionally(self):
        from navig.gateway.channels.telegram_renderer import _FILLED

        r = self._make_renderer()
        r._current_progress = 5
        bar = r._progress_bar()
        assert bar.count(_FILLED) == 5

    def test_conclude_shows_filled_bar(self):
        from navig.gateway.channels.telegram_renderer import _FILLED

        r = self._make_renderer()
        r._current_progress = 10
        r._steps = []  # empty steps for clean test
        frame = r._build_frame(final=True, conclusion_block="CONCLUSION")
        assert _FILLED * 10 in frame
        assert "CONCLUSION" in frame

    def test_steps_accumulate(self):

        r = self._make_renderer()
        r._steps.append(
            type(
                "S",
                (),
                {
                    "icon": "⚙️",
                    "text": "Step A",
                    "detail": "d",
                    "is_warning": False,
                    "is_done": False,
                },
            )()
        )
        r._steps.append(
            type(
                "S",
                (),
                {
                    "icon": "⚙️",
                    "text": "Step B",
                    "detail": "",
                    "is_warning": False,
                    "is_done": False,
                },
            )()
        )
        frame = r._build_frame()
        # All but last step shown (last is in bar header)
        assert "Step A" in frame

    def test_frame_truncated_at_limit(self):
        from navig.gateway.channels.telegram_renderer import _MAX_MESSAGE_LEN

        r = self._make_renderer()
        # Inject a huge conclusion block
        frame = r._build_frame(final=True, conclusion_block="X" * 10_000)
        assert len(frame) <= _MAX_MESSAGE_LEN + 30  # fudge for truncation suffix

    def test_warn_step_has_warning_icon(self):

        r = self._make_renderer()
        r._steps.append(
            type(
                "S",
                (),
                {
                    "icon": "⚙️",
                    "text": "tool_a skipped",
                    "detail": "timeout",
                    "is_warning": True,
                    "is_done": False,
                },
            )()
        )
        # Add a second step so the warning appears in history
        r._steps.append(
            type(
                "S",
                (),
                {
                    "icon": "⚙️",
                    "text": "next step",
                    "detail": "",
                    "is_warning": False,
                    "is_done": False,
                },
            )()
        )
        frame = r._build_frame()
        assert "⚠️" in frame


# ─── get_pipeline_registry integration test ───────────────────────────────────


class TestPipelineRegistry:
    def test_all_four_tools_registered(self):
        from navig.tools import get_pipeline_registry

        reg = get_pipeline_registry()
        names = reg.names()
        assert "site_check" in names
        # web_fetch was promoted to browser_fetch (httpx + Playwright stage-2)
        assert "browser_fetch" in names
        assert "search" in names
        assert "code_exec_sandbox" in names
        assert "skill_run" in names

    def test_singleton(self):
        from navig.tools import get_pipeline_registry

        assert get_pipeline_registry() is get_pipeline_registry()


class TestTelegramActToolArgs:
    async def test_act_passes_url_to_site_check_and_browser_fetch(self, monkeypatch):
        from navig.gateway.channels.telegram import TelegramChannel
        from navig.tools.registry import ToolResult

        calls: list[tuple[str, dict]] = []

        class _FakeRegistry:
            async def run_tool(self, tool_name, args, on_status=None):
                calls.append((tool_name, dict(args)))
                if on_status is not None:
                    await on_status("running", detail=tool_name, progress=50)
                return ToolResult(name=tool_name, success=True, output={"ok": True})

        class _FakeRenderer:
            def __init__(self, *_args, **_kwargs):
                pass

            async def update(self, *_args, **_kwargs):
                return None

            async def warn(self, *_args, **_kwargs):
                return None

            async def finalize(self, *_args, **_kwargs):
                return None

        async def _keep_typing(_chat_id):
            await asyncio.sleep(999)

        channel = TelegramChannel(
            bot_token="123:FAKE",
            on_message=AsyncMock(return_value="analysis"),
            require_auth=False,
        )

        monkeypatch.setattr("navig.gateway.channels.telegram.StatusRenderer", _FakeRenderer)
        monkeypatch.setattr(
            "navig.gateway.channels.telegram.select_tools_for_text",
            lambda _text: ["site_check", "browser_fetch"],
        )
        monkeypatch.setattr(
            "navig.gateway.channels.telegram.extract_url",
            lambda _text: "https://example.com",
        )
        monkeypatch.setattr("navig.tools.get_pipeline_registry", lambda: _FakeRegistry())
        monkeypatch.setattr(channel, "send_message", AsyncMock(return_value={"message_id": 42}))
        monkeypatch.setattr(channel, "_keep_typing", _keep_typing)
        monkeypatch.setattr(channel, "_persist_updated_language", lambda *_a, **_k: None)
        monkeypatch.setattr(channel, "_resolve_model_name", lambda _metadata: "test-model")

        await channel._handle_act(
            text="check https://example.com",
            chat_id=123,
            user_id=456,
            metadata={},
            session=None,
            session_manager=None,
            is_group=False,
        )

        assert calls == [
            ("site_check", {"url": "https://example.com"}),
            ("browser_fetch", {"url": "https://example.com"}),
        ]
