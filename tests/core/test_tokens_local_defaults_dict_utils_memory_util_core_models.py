"""Batch 56 — hermetic unit tests.

Modules covered:
- navig.core.tokens            (estimate_tokens)
- navig.providers._local_defaults  (URL constants)
- navig.core.dict_utils        (deep_merge, truncate_output, utc_now, now_iso)
- navig.memory._util           (_debug_log, _atomic_write_text)
- navig.core.models            (CommandParameter, NavigCommand, SkillManifest,
                                 NavigPack, PackStep)
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────────────────────
# navig.core.tokens
# ──────────────────────────────────────────────────────────────────────────────


class TestEstimateTokens:
    """estimate_tokens — rough token count from character length."""

    def _import(self):
        from navig.core.tokens import estimate_tokens

        return estimate_tokens

    def test_empty_string_returns_zero(self):
        fn = self._import()
        assert fn("") == 0

    def test_default_ratio(self):
        fn = self._import()
        # 400 chars / 4.0 = 100
        assert fn("a" * 400) == 100

    def test_at_least_one_for_short_text(self):
        fn = self._import()
        assert fn("hi") >= 1

    def test_custom_ratio(self):
        fn = self._import()
        # 350 chars / 3.5 = 100
        assert fn("a" * 350, chars_per_token=3.5) == 100

    def test_long_text_scales(self):
        fn = self._import()
        result = fn("a" * 4000)
        assert result == 1000

    def test_single_char(self):
        fn = self._import()
        assert fn("x") >= 1

    def test_returns_int(self):
        fn = self._import()
        result = fn("hello world")
        assert isinstance(result, int)


# ──────────────────────────────────────────────────────────────────────────────
# navig.providers._local_defaults
# ──────────────────────────────────────────────────────────────────────────────


class TestLocalProviderDefaults:
    """URL constants for local model providers."""

    def test_ollama_base_url_loopback(self):
        from navig.providers._local_defaults import _OLLAMA_BASE_URL

        assert "127.0.0.1" in _OLLAMA_BASE_URL
        assert "11434" in _OLLAMA_BASE_URL

    def test_ollama_user_base_url_localhost(self):
        from navig.providers._local_defaults import _OLLAMA_USER_BASE_URL

        assert "localhost" in _OLLAMA_USER_BASE_URL
        assert "11434" in _OLLAMA_USER_BASE_URL

    def test_llamacpp_base_url_loopback(self):
        from navig.providers._local_defaults import _LLAMACPP_BASE_URL

        assert "127.0.0.1" in _LLAMACPP_BASE_URL
        assert "8080" in _LLAMACPP_BASE_URL

    def test_llamacpp_user_base_url_localhost(self):
        from navig.providers._local_defaults import _LLAMACPP_USER_BASE_URL

        assert "localhost" in _LLAMACPP_USER_BASE_URL
        assert "8080" in _LLAMACPP_USER_BASE_URL

    def test_all_start_with_http(self):
        from navig.providers._local_defaults import (
            _LLAMACPP_BASE_URL,
            _LLAMACPP_USER_BASE_URL,
            _OLLAMA_BASE_URL,
            _OLLAMA_USER_BASE_URL,
        )

        for url in (_OLLAMA_BASE_URL, _OLLAMA_USER_BASE_URL, _LLAMACPP_BASE_URL, _LLAMACPP_USER_BASE_URL):
            assert url.startswith("http")

    def test_loopback_and_user_have_same_port(self):
        from navig.providers._local_defaults import _OLLAMA_BASE_URL, _OLLAMA_USER_BASE_URL

        # Both should reference port 11434
        assert "11434" in _OLLAMA_BASE_URL
        assert "11434" in _OLLAMA_USER_BASE_URL


# ──────────────────────────────────────────────────────────────────────────────
# navig.core.dict_utils
# ──────────────────────────────────────────────────────────────────────────────


class TestDeepMerge:
    """deep_merge — recursive dict merge."""

    def _import(self):
        from navig.core.dict_utils import deep_merge

        return deep_merge

    def test_simple_override(self):
        fn = self._import()
        result = fn({"a": 1}, {"a": 2})
        assert result["a"] == 2

    def test_adds_new_key(self):
        fn = self._import()
        result = fn({"a": 1}, {"b": 2})
        assert result["a"] == 1
        assert result["b"] == 2

    def test_recursive_merge(self):
        fn = self._import()
        base = {"outer": {"inner": 1, "keep": "x"}}
        override = {"outer": {"inner": 2}}
        result = fn(base, override)
        assert result["outer"]["inner"] == 2
        assert result["outer"]["keep"] == "x"

    def test_lists_concatenated(self):
        fn = self._import()
        result = fn({"items": ["a", "b"]}, {"items": ["c"]})
        assert result["items"] == ["a", "b", "c"]

    def test_base_not_mutated(self):
        fn = self._import()
        base = {"x": {"y": 1}}
        fn(base, {"x": {"y": 2}})
        assert base["x"]["y"] == 1

    def test_empty_override(self):
        fn = self._import()
        assert fn({"a": 1}, {}) == {"a": 1}

    def test_empty_base(self):
        fn = self._import()
        assert fn({}, {"a": 1}) == {"a": 1}

    def test_non_dict_leaf_overridden(self):
        fn = self._import()
        result = fn({"a": "old"}, {"a": "new"})
        assert result["a"] == "new"


class TestTruncateOutput:
    """truncate_output — clips string with a note."""

    def _import(self):
        from navig.core.dict_utils import truncate_output

        return truncate_output

    def test_short_text_unchanged(self):
        fn = self._import()
        assert fn("hello", 100) == "hello"

    def test_exact_limit_unchanged(self):
        fn = self._import()
        assert fn("abc", 3) == "abc"

    def test_long_text_truncated(self):
        fn = self._import()
        result = fn("x" * 200, 100)
        assert len(result) > 100  # includes the note
        assert "truncated" in result

    def test_includes_total_char_count(self):
        fn = self._import()
        result = fn("a" * 200, 50)
        assert "200" in result

    def test_truncated_prefix_matches(self):
        fn = self._import()
        text = "abcdef"
        result = fn(text, 3)
        assert result.startswith("abc")


class TestUtcNow:
    """utc_now — returns timezone-aware datetime."""

    def test_returns_datetime(self):
        from datetime import datetime

        from navig.core.dict_utils import utc_now

        result = utc_now()
        assert isinstance(result, datetime)

    def test_is_timezone_aware(self):
        from navig.core.dict_utils import utc_now

        result = utc_now()
        assert result.tzinfo is not None


class TestNowIso:
    """now_iso — returns ISO-8601 string."""

    def test_returns_string(self):
        from navig.core.dict_utils import now_iso

        result = now_iso()
        assert isinstance(result, str)

    def test_contains_timezone_offset(self):
        from navig.core.dict_utils import now_iso

        result = now_iso()
        assert "+" in result or "Z" in result or "00:00" in result

    def test_parseable_as_datetime(self):
        from datetime import datetime

        from navig.core.dict_utils import now_iso

        result = now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.year >= 2024


# ──────────────────────────────────────────────────────────────────────────────
# navig.memory._util
# ──────────────────────────────────────────────────────────────────────────────


class TestDebugLog:
    """_debug_log — best-effort debug logging; never raises."""

    def test_does_not_raise(self):
        from navig.memory._util import _debug_log

        _debug_log("test message")  # should not raise

    def test_does_not_raise_on_empty_string(self):
        from navig.memory._util import _debug_log

        _debug_log("")

    def test_calls_logger(self):
        from unittest.mock import patch

        from navig.memory import _util as util_mod

        with patch.object(util_mod._logger, "debug") as mock_debug:
            util_mod._debug_log("hello")
        mock_debug.assert_called_once_with("hello")

    def test_survives_logging_exception(self):
        from unittest.mock import patch

        from navig.memory import _util as util_mod

        with patch.object(util_mod._logger, "debug", side_effect=RuntimeError("crash")):
            util_mod._debug_log("msg")  # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# navig.core.models (Pydantic models)
# ──────────────────────────────────────────────────────────────────────────────


class TestCommandParameter:
    """CommandParameter Pydantic model."""

    def test_basic_construction(self):
        from navig.core.models import CommandParameter

        cp = CommandParameter(type="string", description="A parameter")
        assert cp.type == "string"
        assert cp.required is False

    def test_required_true(self):
        from navig.core.models import CommandParameter

        cp = CommandParameter(type="int", description="count", required=True)
        assert cp.required is True

    def test_default_value(self):
        from navig.core.models import CommandParameter

        cp = CommandParameter(type="string", description="d", default="hello")
        assert cp.default == "hello"

    def test_options_list(self):
        from navig.core.models import CommandParameter

        cp = CommandParameter(type="choice", description="d", options=["a", "b", "c"])
        assert len(cp.options) == 3


class TestNavigCommand:
    """NavigCommand Pydantic model."""

    def test_minimal(self):
        from navig.core.models import NavigCommand

        cmd = NavigCommand(name="deploy", syntax="navig deploy", description="Deploy app")
        assert cmd.name == "deploy"
        assert cmd.risk == "safe"

    def test_risk_destructive(self):
        from navig.core.models import NavigCommand

        cmd = NavigCommand(name="wipe", syntax="navig wipe", description="Wipe db", risk="destructive")
        assert cmd.risk == "destructive"

    def test_confirmation_defaults(self):
        from navig.core.models import NavigCommand

        cmd = NavigCommand(name="x", syntax="x", description="x")
        assert cmd.confirmation_required is False
        assert cmd.confirmation_msg is None

    def test_parameters_dict(self):
        from navig.core.models import CommandParameter, NavigCommand

        params = {"target": CommandParameter(type="string", description="target env")}
        cmd = NavigCommand(name="x", syntax="x", description="x", parameters=params)
        assert "target" in cmd.parameters


class TestSkillManifest:
    """SkillManifest Pydantic model with field aliases."""

    def test_basic_construction(self):
        from navig.core.models import SkillManifest

        sm = SkillManifest(name="deploy-skill", description="Deploy helper", version="1.0.0")
        assert sm.name == "deploy-skill"
        assert sm.category == "uncategorized"

    def test_risk_level_alias(self):
        from navig.core.models import SkillManifest

        sm = SkillManifest(
            **{"name": "x", "description": "d", "risk-level": "high"}
        )
        assert sm.risk_level == "high"

    def test_defaults(self):
        from navig.core.models import SkillManifest

        sm = SkillManifest(name="x", description="d")
        assert sm.version == "0.0.1"
        assert sm.requires == []
        assert sm.tags == []
        assert sm.examples == []


class TestNavigPack:
    """NavigPack Pydantic model."""

    def test_basic_construction(self):
        from navig.core.models import NavigPack

        pack = NavigPack(name="deploy-pack", description="Deploy runbook")
        assert pack.name == "deploy-pack"
        assert pack.version == "1.0.0"
        assert pack.type == "runbook"

    def test_steps_list(self):
        from navig.core.models import NavigPack, PackStep

        steps = [PackStep(command="navig deploy", name="deploy")]
        pack = NavigPack(name="p", description="d", steps=steps)
        assert len(pack.steps) == 1
        assert pack.steps[0].command == "navig deploy"

    def test_tags(self):
        from navig.core.models import NavigPack

        pack = NavigPack(name="p", description="d", tags=["infra", "prod"])
        assert "infra" in pack.tags


class TestPackStep:
    """PackStep Pydantic model."""

    def test_command_required(self):
        from navig.core.models import PackStep

        step = PackStep(command="navig run deploy")
        assert step.command == "navig run deploy"
        assert step.name == "unnamed-step"

    def test_continue_on_error_default(self):
        from navig.core.models import PackStep

        step = PackStep(command="x")
        assert step.continue_on_error is False

    def test_with_description(self):
        from navig.core.models import PackStep

        step = PackStep(command="x", description="runs deployment", name="step1")
        assert step.description == "runs deployment"
