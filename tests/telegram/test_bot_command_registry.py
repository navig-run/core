"""Tests for navig.bot.command_registry — BotCommand, CommandRegistry, singleton."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from navig.bot.command_registry import (
    BotCommand,
    CommandRegistry,
    _MISSING_CMD,
    _populate_from_command_tools,
    get_command_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_schema(name: str = "test_cmd", description: str = "A test command") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


@pytest.fixture
def registry() -> CommandRegistry:
    """Fresh registry for each test."""
    return CommandRegistry()


# ---------------------------------------------------------------------------
# BotCommand
# ---------------------------------------------------------------------------

class TestBotCommand:
    def test_basic_creation(self):
        cmd = BotCommand(name="foo", schema={"a": 1})
        assert cmd.name == "foo"
        assert cmd.schema == {"a": 1}
        assert cmd.tags == []

    def test_with_tags(self):
        cmd = BotCommand(name="bar", schema={}, tags=["core", "admin"])
        assert cmd.tags == ["core", "admin"]

    def test_repr(self):
        cmd = BotCommand(name="my_cmd", schema={})
        assert "my_cmd" in repr(cmd)

    def test_tags_are_independent(self):
        cmd1 = BotCommand(name="c1", schema={})
        cmd2 = BotCommand(name="c2", schema={})
        cmd1.tags.append("x")
        assert cmd2.tags == []


class TestMissingCmdSentinel:
    def test_sentinel_name(self):
        assert _MISSING_CMD.name == "__missing__"

    def test_sentinel_tags(self):
        assert "sentinel" in _MISSING_CMD.tags

    def test_sentinel_empty_schema(self):
        assert _MISSING_CMD.schema == {}


# ---------------------------------------------------------------------------
# CommandRegistry.add
# ---------------------------------------------------------------------------

class TestCommandRegistryAdd:
    def test_add_returns_bot_command(self, registry):
        cmd = registry.add(_make_schema("alpha"))
        assert isinstance(cmd, BotCommand)
        assert cmd.name == "alpha"

    def test_add_stores_command(self, registry):
        registry.add(_make_schema("beta"))
        assert "beta" in registry

    def test_add_returns_correct_schema(self, registry):
        schema = _make_schema("gamma")
        cmd = registry.add(schema)
        assert cmd.schema is schema

    def test_add_with_tags(self, registry):
        cmd = registry.add(_make_schema("delta"), tags=["core", "v2"])
        assert cmd.tags == ["core", "v2"]

    def test_add_no_tags_defaults_to_empty(self, registry):
        cmd = registry.add(_make_schema("epsilon"))
        assert cmd.tags == []

    def test_add_invalid_schema_raises(self, registry):
        with pytest.raises(ValueError, match="Schema must have shape"):
            registry.add({"bad": "schema"})

    def test_add_missing_function_key_raises(self, registry):
        with pytest.raises(ValueError):
            registry.add({"type": "function"})

    def test_add_missing_name_raises(self, registry):
        with pytest.raises(ValueError):
            registry.add({"type": "function", "function": {}})

    def test_add_none_schema_raises(self, registry):
        with pytest.raises((ValueError, TypeError)):
            registry.add(None)

    def test_overwrite_existing_updates_schema(self, registry):
        schema1 = _make_schema("dup")
        schema2 = _make_schema("dup")
        schema2["function"]["description"] = "updated"
        registry.add(schema1)
        cmd2 = registry.add(schema2)
        assert registry.get("dup") is cmd2

    def test_overwrite_logs_debug(self, registry):
        registry.add(_make_schema("overwrite_me"))
        import logging
        with patch.object(logging.getLogger("navig.bot.command_registry"), "debug") as mock_log:
            registry.add(_make_schema("overwrite_me"))
            mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# CommandRegistry.register (decorator)
# ---------------------------------------------------------------------------

class TestCommandRegistryRegister:
    def test_register_decorator_adds_command(self, registry):
        @registry.register
        def my_schema() -> dict:
            return _make_schema("registered_cmd")

        assert "registered_cmd" in registry

    def test_register_returns_original_fn(self, registry):
        def my_schema() -> dict:
            return _make_schema("returnable")

        returned = registry.register(my_schema)
        assert returned is my_schema

    def test_registered_fn_still_callable(self, registry):
        @registry.register
        def raw_schema() -> dict:
            return _make_schema("callable_after_register")

        result = raw_schema()
        assert result["function"]["name"] == "callable_after_register"

    def test_register_invalid_fn_raises(self, registry):
        def bad_fn() -> dict:
            return {"missing": "function_key"}

        with pytest.raises(ValueError):
            registry.register(bad_fn)


# ---------------------------------------------------------------------------
# CommandRegistry.bulk_load
# ---------------------------------------------------------------------------

class TestCommandRegistryBulkLoad:
    def test_bulk_load_adds_all(self, registry):
        schemas = [_make_schema(f"cmd_{i}") for i in range(5)]
        registry.bulk_load(schemas)
        assert len(registry) == 5

    def test_bulk_load_with_tags(self, registry):
        schemas = [_make_schema("tagged")]
        registry.bulk_load(schemas, tags=["batch"])
        assert registry.get("tagged").tags == ["batch"]

    def test_bulk_load_empty_list(self, registry):
        registry.bulk_load([])
        assert len(registry) == 0

    def test_bulk_load_preserves_order(self, registry):
        names = [f"order_{i}" for i in range(4)]
        schemas = [_make_schema(n) for n in names]
        registry.bulk_load(schemas)
        assert registry.names() == names


# ---------------------------------------------------------------------------
# CommandRegistry query: get, all, schemas, names, __len__, __contains__
# ---------------------------------------------------------------------------

class TestCommandRegistryQuery:
    def test_get_existing(self, registry):
        registry.add(_make_schema("find_me"))
        cmd = registry.get("find_me")
        assert cmd is not None
        assert cmd.name == "find_me"

    def test_get_missing_returns_none(self, registry):
        assert registry.get("nonexistent") is None

    def test_all_returns_list(self, registry):
        registry.add(_make_schema("a"))
        registry.add(_make_schema("b"))
        result = registry.all()
        assert isinstance(result, list)
        assert len(result) == 2

    def test_all_returns_copy(self, registry):
        registry.add(_make_schema("x"))
        a = registry.all()
        b = registry.all()
        assert a is not b

    def test_schemas_returns_dicts(self, registry):
        schema = _make_schema("s_cmd")
        registry.add(schema)
        schemas = registry.schemas()
        assert schemas == [schema]

    def test_names_returns_names(self, registry):
        registry.add(_make_schema("n1"))
        registry.add(_make_schema("n2"))
        assert set(registry.names()) == {"n1", "n2"}

    def test_len_empty(self, registry):
        assert len(registry) == 0

    def test_len_after_adds(self, registry):
        for i in range(3):
            registry.add(_make_schema(f"cmd_{i}"))
        assert len(registry) == 3

    def test_contains_true(self, registry):
        registry.add(_make_schema("present"))
        assert "present" in registry

    def test_contains_false(self, registry):
        assert "absent" not in registry

    def test_schemas_drop_in_replacement(self, registry):
        """schemas() should be a list of dicts with 'type'=='function'."""
        registry.add(_make_schema("tool_a"))
        registry.add(_make_schema("tool_b"))
        for s in registry.schemas():
            assert s["type"] == "function"
            assert "name" in s["function"]


# ---------------------------------------------------------------------------
# Singleton: get_command_registry
# ---------------------------------------------------------------------------

class TestGetCommandRegistry:
    def test_returns_command_registry_instance(self):
        import navig.bot.command_registry as mod
        # Reset singleton for isolated test
        original = mod._registry
        mod._registry = None
        try:
            with patch("navig.bot.command_registry._populate_from_command_tools"):
                reg = get_command_registry()
            assert isinstance(reg, CommandRegistry)
        finally:
            mod._registry = original

    def test_singleton_same_object(self):
        reg1 = get_command_registry()
        reg2 = get_command_registry()
        assert reg1 is reg2

    def test_thread_safety(self):
        """Two threads calling get_command_registry should get the same object."""
        import navig.bot.command_registry as mod
        original = mod._registry
        mod._registry = None
        results = []
        try:
            with patch("navig.bot.command_registry._populate_from_command_tools"):
                def worker():
                    results.append(get_command_registry())

                t1 = threading.Thread(target=worker)
                t2 = threading.Thread(target=worker)
                t1.start(); t2.start()
                t1.join(); t2.join()
            assert results[0] is results[1]
        finally:
            mod._registry = original


# ---------------------------------------------------------------------------
# _populate_from_command_tools
# ---------------------------------------------------------------------------

class TestPopulateFromCommandTools:
    def test_populate_loads_schemas(self):
        reg = CommandRegistry()
        fake_tools = [_make_schema("injected_cmd")]
        with patch("navig.bot.command_registry._populate_from_command_tools") as mock_pop:
            # Manually call the real function with mocked import
            with patch.dict("sys.modules", {"navig.bot.command_tools": type("M", (), {"COMMAND_TOOLS": fake_tools})()}):
                _populate_from_command_tools(reg)
        assert "injected_cmd" in reg

    def test_populate_import_error_is_handled(self):
        """ImportError from command_tools should NOT propagate; just log warning."""
        reg = CommandRegistry()
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            try:
                _populate_from_command_tools(reg)
            except ImportError:
                pytest.fail("ImportError should have been swallowed")

    def test_populate_with_empty_command_tools(self):
        reg = CommandRegistry()
        with patch.dict("sys.modules", {"navig.bot.command_tools": type("M", (), {"COMMAND_TOOLS": []})()}):
            _populate_from_command_tools(reg)
        assert len(reg) == 0
