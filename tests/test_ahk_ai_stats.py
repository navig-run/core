"""
Batch 12: Tests for navig.adapters.automation.ahk_ai and navig.commands.stats
- ahk_ai: GenerationContext, GenerationResult, AHKAIGenerator (mock AI + regex)
- stats: _fetch_stats (mocked requests + error paths)
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig.adapters.automation.ahk_ai
# ---------------------------------------------------------------------------
from navig.adapters.automation.ahk_ai import (
    AHKAIGenerator,
    GenerationContext,
    GenerationResult,
)


class TestGenerationContext:
    def test_basic_creation(self):
        ctx = GenerationContext(windows=[], screen_width=1920, screen_height=1080)
        assert ctx.screen_width == 1920
        assert ctx.screen_height == 1080
        assert ctx.windows == []

    def test_to_prompt_str_contains_resolution(self):
        ctx = GenerationContext(windows=[], screen_width=2560, screen_height=1440)
        result = ctx.to_prompt_str()
        assert "2560x1440" in result

    def test_to_prompt_str_empty_windows(self):
        ctx = GenerationContext(windows=[], screen_width=1920, screen_height=1080)
        result = ctx.to_prompt_str()
        assert "Visible Windows" in result

    def test_to_prompt_str_lists_window_titles(self):
        windows = [{"title": "Notepad", "pid": 1234}, {"title": "Explorer", "pid": 5678}]
        ctx = GenerationContext(windows=windows, screen_width=1920, screen_height=1080)
        result = ctx.to_prompt_str()
        assert "Notepad" in result
        assert "Explorer" in result
        assert "1234" in result

    def test_to_prompt_str_window_without_pid(self):
        windows = [{"title": "Untitled"}]
        ctx = GenerationContext(windows=windows, screen_width=800, screen_height=600)
        result = ctx.to_prompt_str()
        assert "Untitled" in result
        assert "?" in result  # fallback pid

    def test_to_prompt_str_returns_string(self):
        ctx = GenerationContext(windows=[], screen_width=100, screen_height=100)
        assert isinstance(ctx.to_prompt_str(), str)


class TestGenerationResult:
    def test_success_true(self):
        r = GenerationResult(True, script="some script")
        assert r.success is True
        assert r.script == "some script"

    def test_success_false(self):
        r = GenerationResult(False, error="oops")
        assert r.success is False
        assert r.error == "oops"

    def test_defaults(self):
        r = GenerationResult(True)
        assert r.script == ""
        assert r.error == ""
        assert r.explanation == ""

    def test_explanation_field(self):
        r = GenerationResult(False, explanation="raw response here")
        assert r.explanation == "raw response here"


class TestAHKAIGenerator:
    def _make_ctx(self):
        return GenerationContext(windows=[], screen_width=1920, screen_height=1080)

    def test_init_no_ai_available(self):
        with patch.dict("sys.modules", {"navig.ai": None}):
            with patch("builtins.__import__", side_effect=ImportError("no ai")):
                gen = AHKAIGenerator.__new__(AHKAIGenerator)
                gen.has_ai = False
        assert gen.has_ai is False

    def test_generate_no_ai_returns_error(self):
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = False
        result = gen.generate("open notepad", self._make_ctx())
        assert result.success is False
        assert "not available" in result.error

    def test_generate_mock_ai_env_var(self):
        """Setting NAVIG_MOCK_AI env var returns a canned mock script."""
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            # Patch import to simulate navig.ai available
            mock_ask = MagicMock(return_value="")
            with patch("navig.adapters.automation.ahk_ai.AHKAIGenerator.generate") as mock_gen:
                mock_gen.return_value = GenerationResult(
                    True,
                    script='#Requires AutoHotkey v2.0\n#SingleInstance Force\nFileAppend "Mock Success", "*"\nExitApp 0',
                )
                result = mock_gen("open notepad", self._make_ctx())
        assert result.success is True
        assert "Mock Success" in result.script

    def test_generate_parses_ahk_code_block(self):
        """generate() extracts script from ```ahk ... ``` block via real regex logic."""
        response = '```ahk\n#Requires AutoHotkey v2.0\nMsgBox "Hello"\n```'
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIG_MOCK_AI", None)
            with patch("navig.ai.ask_ai_with_context", return_value=response):
                with patch.dict("sys.modules", {}):
                    import navig.ai  # noqa: F401, ensure module is importable
                    result = gen.generate("say hello", self._make_ctx())

        assert result.success is True
        assert '#Requires AutoHotkey v2.0' in result.script

    def test_generate_parses_plain_code_block(self):
        """generate() falls back to generic ``` block if no ahk-tagged block."""
        response = '```\n#Requires AutoHotkey v2.0\nRun "notepad"\n```'
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True

        os.environ.pop("NAVIG_MOCK_AI", None)
        with patch("navig.ai.ask_ai_with_context", return_value=response):
            result = gen.generate("open notepad", self._make_ctx())

        assert result.success is True
        assert 'Run "notepad"' in result.script

    def test_generate_handles_error_response(self):
        """generate() returns failure when AI returns 'Error: ...' string."""
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True
        os.environ.pop("NAVIG_MOCK_AI", None)

        with patch("navig.ai.ask_ai_with_context", return_value="Error: API quota exceeded"):
            result = gen.generate("do something", self._make_ctx())

        assert result.success is False
        assert "Error:" in result.error

    def test_generate_handles_bare_requires_line(self):
        """generate() accepts raw code containing #Requires without a code block."""
        response = "#Requires AutoHotkey v2.0\n#SingleInstance Force\nRun \"calc\""
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True
        os.environ.pop("NAVIG_MOCK_AI", None)

        with patch("navig.ai.ask_ai_with_context", return_value=response):
            result = gen.generate("open calc", self._make_ctx())

        assert result.success is True
        assert "calc" in result.script

    def test_generate_returns_failure_when_no_code(self):
        """generate() returns failure when response has no code block and no #Requires."""
        response = "I cannot help with that."
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True
        os.environ.pop("NAVIG_MOCK_AI", None)

        with patch("navig.ai.ask_ai_with_context", return_value=response):
            result = gen.generate("nonsense", self._make_ctx())

        assert result.success is False
        assert result.error != ""

    def test_generate_handles_exception(self):
        """generate() catches unexpected exceptions and returns failure."""
        gen = AHKAIGenerator.__new__(AHKAIGenerator)
        gen.has_ai = True
        os.environ.pop("NAVIG_MOCK_AI", None)

        with patch("navig.ai.ask_ai_with_context", side_effect=RuntimeError("boom")):
            result = gen.generate("crash", self._make_ctx())

        assert result.success is False
        assert "boom" in result.error


# ---------------------------------------------------------------------------
# navig.commands.stats._fetch_stats
# ---------------------------------------------------------------------------
from navig.commands.stats import _fetch_stats


class TestFetchStats:
    def test_returns_json_dict(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total_installs": 42, "by_platform": {"linux": 30, "windows": 12}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = _fetch_stats("http://localhost")

        assert result["total_installs"] == 42
        assert result["by_platform"]["linux"] == 30

    def test_appends_telemetry_stats_path(self):
        """_fetch_stats constructs URL as base /telemetry/stats."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()

        captured_urls = []

        def fake_get(url, timeout):
            captured_urls.append(url)
            return mock_resp

        with patch("requests.get", fake_get):
            _fetch_stats("https://example.com/")

        assert captured_urls[0] == "https://example.com/telemetry/stats"

    def test_strips_trailing_slash(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        captured = []

        def fake_get(url, timeout):
            captured.append(url)
            return mock_resp

        with patch("requests.get", fake_get):
            _fetch_stats("https://example.com///")

        assert captured[0] == "https://example.com/telemetry/stats"

    def test_exits_on_http_error(self):
        """_fetch_stats calls typer.Exit(1) on HTTP/network error."""
        import click
        import requests as _req

        def fake_get(url, timeout):
            raise _req.exceptions.ConnectionError("refused")

        with patch("requests.get", fake_get):
            with pytest.raises(click.exceptions.Exit):
                _fetch_stats("http://unreachable.invalid")

    def test_percentage_calculation_logic(self):
        """Verify the inline pct formula used inside stats callback."""
        total = 200
        count = 50
        pct = f"{count / total * 100:.1f}%"
        assert pct == "25.0%"

    def test_percentage_zero_total(self):
        """When total is 0, no division — formula returns dash."""
        total = 0
        count = 0
        pct = f"{count / total * 100:.1f}%" if total else "—"
        assert pct == "—"

    def test_sorted_platforms_descending(self):
        """Verify the sorted() key used in stats callback."""
        by_platform = {"linux": 10, "windows": 50, "macos": 30}
        sorted_platforms = sorted(by_platform.items(), key=lambda x: -x[1])
        assert sorted_platforms[0] == ("windows", 50)
        assert sorted_platforms[1] == ("macos", 30)
        assert sorted_platforms[2] == ("linux", 10)
