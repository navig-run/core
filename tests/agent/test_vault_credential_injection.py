"""tests/agent/test_vault_credential_injection.py

Tests for F-17 — Vault-Secured Tool Credentials.

Verifies that:
1. ``vault.batch_get()`` fetches multiple secrets and silently skips missing ones.
2. ``AgentToolRegistry.register()`` stores vault_keys on the entry and strips
   them from the exported LLM schema.
3. ``AgentToolRegistry.dispatch()`` injects vault secrets into tool args via
   the ``vault_injector`` callable.
4. ``navig_db_query`` is registered with ``vault_keys=["db_password"]`` and
   the ``db_password`` field is absent from its LLM-facing schema.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. vault.batch_get
# ---------------------------------------------------------------------------


class TestVaultBatchGet:
    """Unit tests for Vault.batch_get() method."""

    def _make_vault(self, secret_map: dict[str, str]):
        """Return a Vault-like mock that returns from secret_map."""
        from navig.vault.core import Vault

        v = MagicMock(spec=Vault)

        def _get_secret(label: str):
            if label in secret_map:
                from navig.vault.secret_str import SecretStr
                return SecretStr(secret_map[label])
            raise KeyError(label)

        v.get_secret.side_effect = _get_secret
        # Use the real batch_get bound to the mock
        v.batch_get = lambda keys: Vault.batch_get(v, keys)
        return v

    def test_returns_found_keys(self):
        vault = self._make_vault({"db_password": "s3cret", "api_key": "abc123"})
        result = vault.batch_get(["db_password", "api_key"])
        assert result == {"db_password": "s3cret", "api_key": "abc123"}

    def test_silently_skips_missing_keys(self):
        vault = self._make_vault({"db_password": "s3cret"})
        result = vault.batch_get(["db_password", "nonexistent"])
        assert result == {"db_password": "s3cret"}
        assert "nonexistent" not in result

    def test_returns_empty_dict_when_all_missing(self):
        vault = self._make_vault({})
        result = vault.batch_get(["x", "y", "z"])
        assert result == {}

    def test_empty_key_list(self):
        vault = self._make_vault({"db_password": "s3cret"})
        assert vault.batch_get([]) == {}


# ---------------------------------------------------------------------------
# 2. AgentToolRegistry — vault_keys stored and stripped from schema
# ---------------------------------------------------------------------------


class TestAgentToolRegistryVaultKeys:
    """Verify schema stripping and vault_keys storage."""

    def _make_tool(self, name: str, params: list[dict]) -> Any:
        from navig.tools.registry import BaseTool, ToolResult

        _name = name
        _params = params

        class _Tool(BaseTool):
            pass

        _Tool.name = _name
        _Tool.description = f"Test tool {_name}"
        _Tool.parameters = _params

        async def _run(self, args, on_status=None):
            return ToolResult(name=self.name, success=True, output=str(args))

        _Tool.run = _run
        # Satisfy ABC by marking run as non-abstract
        _Tool.__abstractmethods__ = frozenset()
        return _Tool()

    def test_vault_keys_stored_on_entry(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        tool = self._make_tool("test_tool", [{"name": "query", "type": "string", "required": True}])
        registry.register(tool, toolset="test", vault_keys=["password"])

        entry = registry.get_entry("test_tool")
        assert entry is not None
        assert entry.vault_keys == ["password"]

    def test_vault_key_stripped_from_schema(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        tool = self._make_tool(
            "cred_tool",
            [
                {"name": "sql", "type": "string", "required": True},
                {"name": "password", "type": "string", "required": False},
            ],
        )
        registry.register(tool, toolset="test", vault_keys=["password"])

        schemas = registry.get_openai_schemas()
        schema = next(s for s in schemas if s["function"]["name"] == "cred_tool")
        props = schema["function"]["parameters"]["properties"]
        assert "sql" in props, "non-vault param should be in schema"
        assert "password" not in props, "vault_key must be stripped from LLM schema"

    def test_no_vault_keys_leaves_schema_intact(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        tool = self._make_tool(
            "plain_tool",
            [{"name": "query", "type": "string", "required": True}],
        )
        registry.register(tool, toolset="test")
        schemas = registry.get_openai_schemas()
        schema = next(s for s in schemas if s["function"]["name"] == "plain_tool")
        assert "query" in schema["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# 3. AgentToolRegistry.dispatch — vault_injector injects credentials
# ---------------------------------------------------------------------------


class TestDispatchVaultInjection:
    """End-to-end vault injection through dispatch()."""

    def _make_capture_tool(self, name: str):
        """A tool whose run() captures the args it receives."""
        from navig.tools.registry import BaseTool, ToolResult

        captured: list[dict] = []
        _name = name

        class _CaptureTool(BaseTool):
            pass

        _CaptureTool.name = _name
        _CaptureTool.description = "Capture args"
        _CaptureTool.parameters = [{"name": "user", "type": "string", "required": True}]

        async def _run(self, args, on_status=None):
            captured.append(dict(args))
            return ToolResult(name=self.name, success=True, output="ok")

        _CaptureTool.run = _run
        _CaptureTool.__abstractmethods__ = frozenset()
        return _CaptureTool(), captured

    def test_vault_injector_merges_into_args(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        tool, captured = self._make_capture_tool("inject_test")
        registry.register(tool, toolset="test", vault_keys=["secret_token"])

        vault_injector = lambda keys: {"secret_token": "vault_value_xyz"}  # noqa: E731
        registry.dispatch("inject_test", {"user": "alice"}, vault_injector=vault_injector)

        assert len(captured) == 1
        assert captured[0]["user"] == "alice"
        assert captured[0]["secret_token"] == "vault_value_xyz"

    def test_dispatch_without_vault_injector_still_works(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        tool, captured = self._make_capture_tool("no_vault_tool")
        registry.register(tool, toolset="test", vault_keys=["secret_token"])

        # No injector → secret_token not in args (LLM didn't provide it, vault not injected)
        registry.dispatch("no_vault_tool", {"user": "bob"})
        assert len(captured) == 1
        assert "secret_token" not in captured[0]

    def test_vault_injector_exception_recovered_gracefully(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        tool, captured = self._make_capture_tool("fail_vault_tool")
        registry.register(tool, toolset="test", vault_keys=["token"])

        def _bad_injector(keys):
            raise RuntimeError("vault failure")

        # Should not raise; tool still runs without the injected key
        registry.dispatch("fail_vault_tool", {"user": "carol"}, vault_injector=_bad_injector)
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# 4. navig_db_query — vault_keys registration + schema check
# ---------------------------------------------------------------------------


class TestNavigDbQueryVaultIntegration:
    """Verify navig_db_query has db_password vault wiring."""

    def test_navig_db_query_schema_has_no_password_field(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.devops_tools import register_devops_tools

        registry = AgentToolRegistry()
        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry):
            register_devops_tools()

        schemas = registry.get_openai_schemas()
        db_schema = next(
            (s for s in schemas if s["function"]["name"] == "navig_db_query"), None
        )
        assert db_schema is not None, "navig_db_query should be registered"
        props = db_schema["function"]["parameters"].get("properties", {})
        assert "sql" in props
        assert "database" in props
        assert "db_password" not in props, "db_password must be stripped (vault-only)"

    def test_navig_db_query_entry_has_vault_keys(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.devops_tools import register_devops_tools

        registry = AgentToolRegistry()
        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry):
            register_devops_tools()

        entry = registry.get_entry("navig_db_query")
        assert entry is not None
        assert "db_password" in entry.vault_keys

    def test_dispatch_navig_db_query_injects_password(self):
        """Dispatching navig_db_query injects db_password from vault transparently."""
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.devops_tools import register_devops_tools

        registry = AgentToolRegistry()
        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry):
            register_devops_tools()

        injected_args: list[dict] = []

        original_run = None

        async def _mock_run(self, args, on_status=None):
            injected_args.append(dict(args))
            from navig.tools.registry import ToolResult
            return ToolResult(name=self.name, success=True, output="mock")

        entry = registry.get_entry("navig_db_query")
        assert entry is not None

        # Patch the tool's run method
        with patch.object(type(entry.tool_ref), "run", _mock_run):
            registry.dispatch(
                "navig_db_query",
                {"sql": "SELECT 1", "database": "mydb"},
                vault_injector=lambda keys: {"db_password": "top_secret"},
            )

        assert len(injected_args) == 1
        assert injected_args[0]["sql"] == "SELECT 1"
        assert injected_args[0]["database"] == "mydb"
        assert injected_args[0]["db_password"] == "top_secret"
