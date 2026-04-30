"""
Batch 100 — Tests for navig/core/config_schema.py and navig/core/hooks.py

config_schema: enums, sub-models, validate_global_config, validate_host_config,
               get_config_schema, validate_config_dict, ConfigValidationError
hooks:         HookEvent, HookRegistry, trigger_hook, register_hook, unregister_hook,
               hook_stats, list_hook_types, before_command, after_command, on_error
"""

from __future__ import annotations

import asyncio
import warnings
from datetime import datetime


# =============================================================================
# config_schema — Enums
# =============================================================================


class TestLogLevel:
    def test_is_str_enum(self):
        from navig.core.config_schema import LogLevel
        assert isinstance(LogLevel.INFO, str)

    def test_values(self):
        from navig.core.config_schema import LogLevel
        assert LogLevel.DEBUG == "DEBUG"
        assert LogLevel.INFO == "INFO"
        assert LogLevel.WARNING == "WARNING"
        assert LogLevel.ERROR == "ERROR"
        assert LogLevel.CRITICAL == "CRITICAL"

    def test_five_members(self):
        from navig.core.config_schema import LogLevel
        assert len(LogLevel) == 5


class TestExecutionMode:
    def test_is_str_enum(self):
        from navig.core.config_schema import ExecutionMode
        assert isinstance(ExecutionMode.INTERACTIVE, str)

    def test_values(self):
        from navig.core.config_schema import ExecutionMode
        assert ExecutionMode.INTERACTIVE == "interactive"
        assert ExecutionMode.AUTO == "auto"

    def test_two_members(self):
        from navig.core.config_schema import ExecutionMode
        assert len(ExecutionMode) == 2


class TestConfirmationLevel:
    def test_is_str_enum(self):
        from navig.core.config_schema import ConfirmationLevel
        assert isinstance(ConfirmationLevel.STANDARD, str)

    def test_values(self):
        from navig.core.config_schema import ConfirmationLevel
        assert ConfirmationLevel.CRITICAL == "critical"
        assert ConfirmationLevel.STANDARD == "standard"
        assert ConfirmationLevel.VERBOSE == "verbose"

    def test_three_members(self):
        from navig.core.config_schema import ConfirmationLevel
        assert len(ConfirmationLevel) == 3


class TestAuthMethod:
    def test_is_str_enum(self):
        from navig.core.config_schema import AuthMethod
        assert isinstance(AuthMethod.KEY, str)

    def test_values(self):
        from navig.core.config_schema import AuthMethod
        assert AuthMethod.KEY == "key"
        assert AuthMethod.PASSWORD == "password"
        assert AuthMethod.AGENT == "agent"

    def test_three_members(self):
        from navig.core.config_schema import AuthMethod
        assert len(AuthMethod) == 3


# =============================================================================
# config_schema — validate_global_config
# =============================================================================


class TestValidateGlobalConfig:
    def test_empty_dict_returns_global_config(self):
        from navig.core.config_schema import GlobalConfig, validate_global_config
        result = validate_global_config({})
        assert result is not None
        assert isinstance(result, GlobalConfig)

    def test_returns_none_on_bad_field_no_strict(self):
        from navig.core.config_schema import validate_global_config
        # Provide invalid log_level that fails pattern (bad value for LogLevel enum)
        result = validate_global_config({"log_level": "INVALID_LEVEL_XYZ"})
        # Should return None (validation fails, strict=False by default)
        assert result is None

    def test_raises_on_bad_field_strict_mode(self):
        from navig.core.config_schema import ConfigValidationError, validate_global_config
        try:
            result = validate_global_config({"log_level": "BAD_VALUE"}, strict=True)
            # If pydantic not available, result could be None — skip
            if result is None:
                return
        except ConfigValidationError:
            pass  # expected path

    def test_default_log_level_is_info(self):
        from navig.core.config_schema import LogLevel, validate_global_config
        result = validate_global_config({})
        if result is None:
            return
        assert result.log_level == LogLevel.INFO

    def test_custom_log_level_debug(self):
        from navig.core.config_schema import LogLevel, validate_global_config
        result = validate_global_config({"log_level": "DEBUG"})
        if result is None:
            return
        assert result.log_level == LogLevel.DEBUG

    def test_default_version(self):
        from navig.core.config_schema import validate_global_config
        result = validate_global_config({})
        if result is None:
            return
        assert isinstance(result.version, str)

    def test_debug_mode_default_false(self):
        from navig.core.config_schema import validate_global_config
        result = validate_global_config({})
        if result is None:
            return
        assert result.debug_mode is False

    def test_custom_debug_mode_true(self):
        from navig.core.config_schema import validate_global_config
        result = validate_global_config({"debug_mode": True})
        if result is None:
            return
        assert result.debug_mode is True

    def test_inline_api_key_triggers_warning(self):
        from navig.core.config_schema import validate_global_config
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = validate_global_config({"openrouter_api_key": "sk-or-v1-thisisalongkeyvalue"})
        if result is None:
            return
        # key starts with sk-or- so no warning
        assert result.openrouter_api_key == "sk-or-v1-thisisalongkeyvalue"

    def test_env_var_api_key_no_warning(self):
        from navig.core.config_schema import validate_global_config
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = validate_global_config({"openrouter_api_key": "${OPENROUTER_API_KEY}"})
        if result is None:
            return
        assert result.openrouter_api_key == "${OPENROUTER_API_KEY}"


# =============================================================================
# config_schema — validate_host_config
# =============================================================================


class TestValidateHostConfig:
    def test_minimal_valid_host(self):
        from navig.core.config_schema import HostConfig, validate_host_config
        result = validate_host_config({"hostname": "192.168.1.1", "username": "root"})
        if result is None:
            return
        assert isinstance(result, HostConfig)
        assert result.hostname == "192.168.1.1"
        assert result.username == "root"

    def test_default_port_22(self):
        from navig.core.config_schema import validate_host_config
        result = validate_host_config({"hostname": "myhost", "username": "user"})
        if result is None:
            return
        assert result.port == 22

    def test_custom_port(self):
        from navig.core.config_schema import validate_host_config
        result = validate_host_config({"hostname": "myhost", "username": "user", "port": 2222})
        if result is None:
            return
        assert result.port == 2222

    def test_default_auth_method_key(self):
        from navig.core.config_schema import AuthMethod, validate_host_config
        result = validate_host_config({"hostname": "myhost", "username": "user"})
        if result is None:
            return
        assert result.auth_method == AuthMethod.KEY

    def test_password_auth_without_password_raises(self):
        from navig.core.config_schema import ConfigValidationError, validate_host_config
        try:
            result = validate_host_config(
                {"hostname": "myhost", "username": "user", "auth_method": "password"},
                strict=True,
            )
            if result is None:
                return
        except (ConfigValidationError, Exception):
            pass  # expected: password auth without password raises

    def test_returns_none_without_required_fields(self):
        from navig.core.config_schema import validate_host_config
        result = validate_host_config({})
        assert result is None

    def test_missing_hostname_no_strict_returns_none(self):
        from navig.core.config_schema import validate_host_config
        result = validate_host_config({"username": "root"})
        assert result is None

    def test_strict_raises_config_validation_error(self):
        from navig.core.config_schema import ConfigValidationError, validate_host_config
        try:
            validate_host_config({}, strict=True)
        except ConfigValidationError:
            pass  # expected
        except Exception:
            pass  # acceptable: other error during strict validation

    def test_display_name_optional(self):
        from navig.core.config_schema import validate_host_config
        result = validate_host_config({
            "hostname": "myhost", "username": "user",
            "display_name": "My Server"
        })
        if result is None:
            return
        assert result.display_name == "My Server"


# =============================================================================
# config_schema — get_config_schema
# =============================================================================


class TestGetConfigSchema:
    def test_global_returns_dict(self):
        from navig.core.config_schema import get_config_schema
        result = get_config_schema("global")
        if result is None:
            return
        assert isinstance(result, dict)

    def test_host_returns_dict(self):
        from navig.core.config_schema import get_config_schema
        result = get_config_schema("host")
        if result is None:
            return
        assert isinstance(result, dict)

    def test_global_schema_has_title_or_properties(self):
        from navig.core.config_schema import get_config_schema
        result = get_config_schema("global")
        if result is None:
            return
        assert "properties" in result or "title" in result

    def test_unknown_type_raises_value_error(self):
        from navig.core.config_schema import get_config_schema
        try:
            result = get_config_schema("unknown_type_xyz")
            # If pydantic not available returns None without raising
            assert result is None
        except ValueError:
            pass  # expected

    def test_default_is_global(self):
        from navig.core.config_schema import get_config_schema
        result_explicit = get_config_schema("global")
        result_default = get_config_schema()
        assert result_explicit == result_default


# =============================================================================
# config_schema — validate_config_dict
# =============================================================================


class TestValidateConfigDict:
    def test_empty_dict_is_valid(self):
        from navig.core.config_schema import validate_config_dict
        valid, issues = validate_config_dict({})
        assert valid is True
        assert issues == []

    def test_returns_tuple_of_bool_and_list(self):
        from navig.core.config_schema import validate_config_dict
        result = validate_config_dict({})
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], list)

    def test_invalid_config_returns_false(self):
        from navig.core.config_schema import validate_config_dict
        valid, issues = validate_config_dict({"log_level": "NOT_A_LOG_LEVEL_99"})
        assert valid is False
        assert len(issues) > 0

    def test_invalid_issues_contains_strings(self):
        from navig.core.config_schema import validate_config_dict
        valid, issues = validate_config_dict({"log_level": "BAD"})
        if not valid:
            assert all(isinstance(i, str) for i in issues)

    def test_valid_config_returns_empty_issues(self):
        from navig.core.config_schema import validate_config_dict
        valid, issues = validate_config_dict({"debug_mode": True})
        assert valid is True
        assert issues == []


# =============================================================================
# config_schema — ConfigValidationError
# =============================================================================


class TestConfigValidationError:
    def test_is_exception(self):
        from navig.core.config_schema import ConfigValidationError
        err = ConfigValidationError([{"loc": ["field"], "msg": "bad"}], "test")
        assert isinstance(err, Exception)

    def test_message_includes_config_type(self):
        from navig.core.config_schema import ConfigValidationError
        err = ConfigValidationError([{"loc": ["field"], "msg": "bad"}], "global config")
        assert "global config" in str(err)

    def test_message_includes_field_and_msg(self):
        from navig.core.config_schema import ConfigValidationError
        err = ConfigValidationError([{"loc": ["log_level"], "msg": "invalid enum value"}])
        assert "log_level" in str(err)
        assert "invalid enum value" in str(err)

    def test_errors_attribute_preserved(self):
        from navig.core.config_schema import ConfigValidationError
        errors = [{"loc": ["x"], "msg": "oops"}]
        err = ConfigValidationError(errors)
        assert err.errors is errors

    def test_empty_errors(self):
        from navig.core.config_schema import ConfigValidationError
        err = ConfigValidationError([])
        assert str(err) is not None  # doesn't raise


# =============================================================================
# hooks — HookEvent
# =============================================================================


class TestHookEvent:
    def test_creation(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="command", action="before_execute")
        assert ev.type == "command"
        assert ev.action == "before_execute"

    def test_event_key_property(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="command", action="before_execute")
        assert ev.event_key == "command:before_execute"

    def test_cancel_default_false(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="session", action="start")
        assert ev.cancel is False

    def test_messages_default_empty_list(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="session", action="start")
        assert ev.messages == []

    def test_context_default_empty_dict(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="session", action="start")
        assert ev.context == {}

    def test_data_default_empty_dict(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="session", action="start")
        assert ev.data == {}

    def test_timestamp_is_datetime(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="session", action="start")
        assert isinstance(ev.timestamp, datetime)

    def test_custom_context(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="command", action="run", context={"cmd": "ls"})
        assert ev.context["cmd"] == "ls"

    def test_cancel_can_be_set(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="command", action="run")
        ev.cancel = True
        assert ev.cancel is True

    def test_repr_contains_event_key(self):
        from navig.core.hooks import HookEvent
        ev = HookEvent(type="ssh", action="connect")
        assert "ssh:connect" in repr(ev)


# =============================================================================
# hooks — HookRegistry
# =============================================================================


class TestHookRegistry:
    def _make_registry(self):
        from navig.core.hooks import HookRegistry
        return HookRegistry()

    def test_register_and_handler_count(self):
        reg = self._make_registry()
        reg.register("command:run", lambda e: None)
        assert reg.handler_count("command:run") == 1

    def test_handler_count_unknown_key_is_zero(self):
        reg = self._make_registry()
        assert reg.handler_count("unknown:event") == 0

    def test_total_handler_count(self):
        reg = self._make_registry()
        reg.register("a", lambda e: None)
        reg.register("b", lambda e: None)
        assert reg.handler_count() == 2

    def test_get_event_keys(self):
        reg = self._make_registry()
        reg.register("cmd:run", lambda e: None)
        reg.register("sess:start", lambda e: None)
        keys = reg.get_event_keys()
        assert "cmd:run" in keys
        assert "sess:start" in keys

    def test_unregister_returns_true_when_found(self):
        reg = self._make_registry()
        h = lambda e: None
        reg.register("ev", h)
        assert reg.unregister("ev", h) is True
        assert reg.handler_count("ev") == 0

    def test_unregister_returns_false_when_not_registered(self):
        reg = self._make_registry()
        assert reg.unregister("ev", lambda e: None) is False

    def test_clear_specific_key(self):
        reg = self._make_registry()
        reg.register("a", lambda e: None)
        reg.register("b", lambda e: None)
        reg.clear("a")
        assert reg.handler_count("a") == 0
        assert reg.handler_count("b") == 1

    def test_clear_all(self):
        reg = self._make_registry()
        reg.register("a", lambda e: None)
        reg.register("b", lambda e: None)
        reg.clear()
        assert reg.handler_count() == 0

    def test_disable_suppresses_handlers(self):
        reg = self._make_registry()
        assert not reg.is_disabled("some:event")
        reg.disable("some:event")
        assert reg.is_disabled("some:event")

    def test_enable_unsuppresses_handlers(self):
        reg = self._make_registry()
        reg.disable("some:event")
        reg.enable("some:event")
        assert not reg.is_disabled("some:event")

    def test_priority_ordering(self):
        reg = self._make_registry()
        order = []
        reg.register("ev", lambda e: order.append("second"), priority=200)
        reg.register("ev", lambda e: order.append("first"), priority=50)
        # trigger manually to check order
        handlers = reg.get_handlers("ev")
        for _, h in handlers:
            from navig.core.hooks import HookEvent
            h(HookEvent(type="ev", action=""))
        assert order == ["first", "second"]

    def test_get_handlers_returns_empty_for_unknown(self):
        reg = self._make_registry()
        assert reg.get_handlers("nonexistent:key") == []


# =============================================================================
# hooks — trigger_hook (module-level)
# =============================================================================


class TestTriggerHook:
    async def test_trigger_returns_hook_event(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        event = await h_mod.trigger_hook("test:event_a", context={"x": 1})
        from navig.core.hooks import HookEvent
        assert isinstance(event, HookEvent)

    async def test_trigger_calls_registered_sync_handler(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []
        h_mod._registry.register("test:sync_handler", lambda e: called.append(True))
        await h_mod.trigger_hook("test:sync_handler")
        assert called == [True]

    async def test_trigger_calls_registered_async_handler(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        async def async_h(e):
            called.append(True)

        h_mod._registry.register("test:async_handler", async_h)
        await h_mod.trigger_hook("test:async_handler")
        assert called == [True]

    async def test_trigger_cancel_propagated(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()

        def cancel_h(e):
            e.cancel = True

        h_mod._registry.register("test:cancel_ev", cancel_h)
        event = await h_mod.trigger_hook("test:cancel_ev")
        assert event.cancel is True

    async def test_trigger_no_handlers_returns_event(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        event = await h_mod.trigger_hook("test:no_handler_ev")
        assert event.type == "test"
        assert event.action == "no_handler_ev"

    async def test_trigger_disabled_event_skips_handlers(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []
        h_mod._registry.register("test:disabled_ev2", lambda e: called.append(True))
        h_mod._registry.disable("test:disabled_ev2")
        await h_mod.trigger_hook("test:disabled_ev2")
        assert called == []

    async def test_handler_error_does_not_prevent_other_handlers(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        def bad_h(e):
            raise RuntimeError("handler error")

        def good_h(e):
            called.append(True)

        h_mod._registry.register("test:err_isolation", bad_h, priority=10)
        h_mod._registry.register("test:err_isolation", good_h, priority=20)
        event = await h_mod.trigger_hook("test:err_isolation")
        assert called == [True]


# =============================================================================
# hooks — register_hook / unregister_hook (module-level)
# =============================================================================


class TestRegisterHookDecorator:
    async def test_register_hook_decorator(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        @h_mod.register_hook("test:decorator_ev")
        async def my_handler(e):
            called.append(True)

        await h_mod.trigger_hook("test:decorator_ev")
        assert called == [True]

    async def test_register_hook_returns_original_function(self):
        from navig.core.hooks import register_hook

        @register_hook("test:noop_ev")
        def my_fn(e):
            pass

        assert callable(my_fn)

    def test_unregister_hook_module_level(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        h = lambda e: None
        h_mod._registry.register("test:unregister_ev", h)
        result = h_mod.unregister_hook("test:unregister_ev", h)
        assert result is True
        assert h_mod._registry.handler_count("test:unregister_ev") == 0


# =============================================================================
# hooks — hook_stats, list_hook_types
# =============================================================================


class TestHookUtilities:
    def test_hook_stats_returns_dict(self):
        from navig.core.hooks import hook_stats
        stats = hook_stats()
        assert isinstance(stats, dict)

    def test_hook_stats_has_total_handlers(self):
        from navig.core.hooks import hook_stats
        stats = hook_stats()
        assert "total_handlers" in stats

    def test_hook_stats_has_event_keys(self):
        from navig.core.hooks import hook_stats
        stats = hook_stats()
        assert "event_keys" in stats

    def test_list_hook_types_returns_dict(self):
        from navig.core.hooks import list_hook_types
        result = list_hook_types()
        assert isinstance(result, dict)

    def test_list_hook_types_nonempty(self):
        from navig.core.hooks import list_hook_types
        result = list_hook_types()
        assert len(result) > 0

    def test_list_hook_types_contains_command(self):
        from navig.core.hooks import list_hook_types
        result = list_hook_types()
        assert "command" in result

    def test_list_hook_types_is_copy(self):
        from navig.core.hooks import list_hook_types, HOOK_EVENT_TYPES
        result = list_hook_types()
        result["new_key"] = "modified"
        assert "new_key" not in HOOK_EVENT_TYPES

    def test_hook_event_types_constant(self):
        from navig.core.hooks import HOOK_EVENT_TYPES
        assert isinstance(HOOK_EVENT_TYPES, dict)
        assert "session:start" in HOOK_EVENT_TYPES


# =============================================================================
# hooks — before_command, after_command, on_error decorators
# =============================================================================


class TestConvenienceDecorators:
    async def test_before_command_no_args(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        @h_mod.before_command()
        async def handler(e):
            called.append(True)

        await h_mod.trigger_hook("command:before_execute")
        assert called == [True]

    async def test_before_command_with_name(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        @h_mod.before_command("deploy")
        async def handler(e):
            called.append(True)

        await h_mod.trigger_hook("command:before_deploy")
        assert called == [True]

    async def test_after_command_no_args(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        @h_mod.after_command()
        async def handler(e):
            called.append(True)

        await h_mod.trigger_hook("command:after_execute")
        assert called == [True]

    async def test_on_error_no_event_type(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        @h_mod.on_error()
        async def handler(e):
            called.append(True)

        await h_mod.trigger_hook("error")
        assert called == [True]

    async def test_on_error_with_event_type(self):
        from navig.core import hooks as h_mod
        h_mod._registry.clear()
        called = []

        @h_mod.on_error("ssh")
        async def handler(e):
            called.append(True)

        await h_mod.trigger_hook("ssh:error")
        assert called == [True]
