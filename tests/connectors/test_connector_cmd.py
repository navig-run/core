"""Tests for navig.commands.connector_cmd — CLI surface for connector engine."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.connector_cmd import connector_app
from navig.connectors.auth_manager import ConnectorAuthManager
from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.registry import get_connector_registry
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
)

pytestmark = pytest.mark.integration

runner = CliRunner()


# ── Stubs ────────────────────────────────────────────────────────────────


class _FakeGmail(BaseConnector):
    manifest = ConnectorManifest(
        id="gmail",
        display_name="Gmail",
        description="Fake Gmail for tests",
        domain=ConnectorDomain.COMMUNICATION,
        icon="📧",
        requires_oauth=True,
    )

    async def search(self, query: str) -> list[Resource]:
        return [
            Resource(
                id="msg-1",
                source="gmail",
                title=f"Email: {query}",
                preview="hello",
            )
        ]

    async def fetch(self, resource_id: str) -> Resource:
        return Resource(
            id=resource_id,
            source="gmail",
            title="Fetched Email",
            preview="Full body here.",
        )

    async def act(self, action: Action) -> ActionResult:
        return ActionResult(success=True)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=5.0)


class _FakeCalendar(BaseConnector):
    manifest = ConnectorManifest(
        id="google_calendar",
        display_name="Google Calendar",
        description="Fake Calendar for tests",
        domain=ConnectorDomain.CALENDAR,
        icon="📅",
        requires_oauth=False,
    )

    async def search(self, query: str) -> list[Resource]:
        return []

    async def fetch(self, resource_id: str) -> Resource:
        return Resource(
            id=resource_id,
            source="google_calendar",
            title="Event",
            preview="",
        )

    async def act(self, action: Action) -> ActionResult:
        return ActionResult(success=True)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=2.0)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Reset registry + skip lazy-load of real connectors."""
    registry = get_connector_registry()
    registry.reset()

    # Mark connectors already-loaded so _ensure_connectors_loaded() is a no-op and
    # doesn't import the real Gmail/Calendar connectors. The guard flag now lives
    # in the bootstrap module (single source of truth for CLI + MCP).
    import navig.connectors.bootstrap as bootstrap_mod

    monkeypatch.setattr(bootstrap_mod, "_CONNECTORS_LOADED", True)

    # Register fakes
    registry.register(_FakeGmail)
    registry.register(_FakeCalendar)
    yield
    registry.reset()
    # Prevent OAuth config state from leaking between tests
    ConnectorAuthManager.reset_providers()


@pytest.fixture(autouse=True)
def _isolate_vault(monkeypatch):
    """Keep the CLI's vault reads off the real machine, and model "connected".

    The CLI resolves connectedness from the vault and hydrates a connector's token
    before search/fetch/health — connectors keep the durable token in the vault, and
    every CLI process builds fresh, token-less instances. Two consequences for tests:

    1. Without this fixture the suite would read the *operator's own vault*, making
       results depend on which accounts happen to be linked on this machine.
    2. These tests express "connected" by setting ``_status = CONNECTED`` on a fake,
       so injection is stubbed to succeed for exactly those — no real tokens, and the
       not-connected paths still behave as not-connected.

    Individual tests override either stub with their own monkeypatch.
    """

    async def _inject(self, connector):
        if connector.status in (ConnectorStatus.CONNECTED, ConnectorStatus.DEGRADED):
            connector.set_access_token("test-token")
            return True
        return False

    monkeypatch.setattr(ConnectorAuthManager, "inject_token", _inject)
    monkeypatch.setattr(ConnectorAuthManager, "list_connected_accounts", lambda self: {})


# ── Tests ────────────────────────────────────────────────────────────────


class TestConnectorList:
    def test_list_table(self):
        result = runner.invoke(connector_app, ["list"])
        assert result.exit_code == 0
        assert "gmail" in result.output
        assert "google_calendar" in result.output

    def test_list_json(self):
        result = runner.invoke(connector_app, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        ids = {c["id"] for c in data}
        assert ids == {"gmail", "google_calendar"}

    def test_list_filter_by_domain(self):
        result = runner.invoke(connector_app, ["list", "--domain", "communication"])
        assert result.exit_code == 0
        assert "gmail" in result.output

    def test_list_filter_unknown_domain(self):
        result = runner.invoke(connector_app, ["list", "--domain", "nonexistent"])
        assert result.exit_code == 1


class TestConnectorAutoLoader:
    def test_ensure_connectors_loaded_registers_extended_connectors(self, monkeypatch):
        """Regression: new built-in connectors must be auto-registered by CLI loader."""
        import navig.commands.connector_cmd as cmd_mod
        import navig.connectors.bootstrap as bootstrap_mod

        registry = get_connector_registry()
        registry.reset()
        monkeypatch.setattr(bootstrap_mod, "_CONNECTORS_LOADED", False)

        cmd_mod._ensure_connectors_loaded()

        for connector_id in (
            "perplexity",
            "google_maps",
            "youtube",
            "supabase",
            "gcp_translate",
        ):
            assert registry.has(connector_id), f"missing auto-registered connector: {connector_id}"

    def test_ensure_connectors_loaded_is_idempotent(self, monkeypatch):
        """Calling loader repeatedly should not error and should preserve registrations."""
        import navig.commands.connector_cmd as cmd_mod
        import navig.connectors.bootstrap as bootstrap_mod

        registry = get_connector_registry()
        registry.reset()
        monkeypatch.setattr(bootstrap_mod, "_CONNECTORS_LOADED", False)

        cmd_mod._ensure_connectors_loaded()
        first = set(registry.all_classes().keys())

        # Second call should be a no-op due to the bootstrap _CONNECTORS_LOADED guard.
        cmd_mod._ensure_connectors_loaded()
        second = set(registry.all_classes().keys())

        assert first == second


class TestConnectorDisconnect:
    def test_disconnect_known(self):
        result = runner.invoke(connector_app, ["disconnect", "gmail"])
        assert result.exit_code == 0
        assert "disconnected" in result.output.lower()

    def test_disconnect_unknown(self):
        result = runner.invoke(connector_app, ["disconnect", "bogus"])
        assert result.exit_code == 1


class TestConnectorSearch:
    def test_search_no_connected(self):
        """When no connectors are CONNECTED, should warn."""
        result = runner.invoke(connector_app, ["search", "meeting"])
        assert result.exit_code == 0
        assert "No connectors connected" in result.output or "no results" in result.output.lower()

    def test_search_with_connected(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["search", "meeting"])
        assert result.exit_code == 0
        assert "meeting" in result.output.lower()

    def test_search_json(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["search", "hello", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) >= 1
        assert data[0]["source"] == "gmail"

    def test_search_with_source_filter(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["search", "test", "--source", "gmail"])
        assert result.exit_code == 0

    def test_search_unknown_source(self):
        result = runner.invoke(connector_app, ["search", "test", "--source", "bogus"])
        assert result.exit_code == 1

    def test_search_passes_limit_when_supported(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        captured: dict[str, int | None] = {"limit": None}

        async def _search(query: str, limit: int | None = None):
            captured["limit"] = limit
            return [
                Resource(id="msg-1", source="gmail", title=f"Email: {query}", preview="one"),
                Resource(id="msg-2", source="gmail", title=f"Email: {query}", preview="two"),
            ]

        gmail.search = _search
        result = runner.invoke(connector_app, ["search", "meeting", "--limit", "1"])
        assert result.exit_code == 0
        assert captured["limit"] == 1


class TestConnectorFetch:
    @pytest.fixture(autouse=True)
    def _gmail_is_connected(self):
        """Fetching from Gmail requires a connected Gmail, same as the real thing."""
        get_connector_registry().get("gmail")._status = ConnectorStatus.CONNECTED

    def test_fetch_resource(self):
        result = runner.invoke(connector_app, ["fetch", "gmail:msg-1"])
        assert result.exit_code == 0
        assert "Fetched Email" in result.output

    def test_fetch_json(self):
        result = runner.invoke(connector_app, ["fetch", "gmail:msg-1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "msg-1"
        assert data["source"] == "gmail"

    def test_fetch_bad_format(self):
        result = runner.invoke(connector_app, ["fetch", "no-colon-here"])
        assert result.exit_code == 1

    def test_fetch_unknown_connector(self):
        result = runner.invoke(connector_app, ["fetch", "bogus:123"])
        assert result.exit_code == 1

    def test_fetch_missing_resource_exits_1(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")

        async def _fetch_none(resource_id: str):
            return None

        gmail.fetch = _fetch_none
        result = runner.invoke(connector_app, ["fetch", "gmail:missing"])
        assert result.exit_code == 1
        assert "Resource not found" in result.output


class TestConnectorStatus:
    def test_status_no_connected(self):
        result = runner.invoke(connector_app, ["status"])
        assert result.exit_code == 0
        assert "No connectors connected" in result.output

    def test_status_with_connected(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["status"])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower() or "gmail" in result.output.lower()

    def test_status_json_output(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        row = data[0]
        assert row["id"] == "gmail"
        assert "ok" in row
        assert "latency_ms" in row


class TestConnectorConnect:
    def test_connect_unknown_connector_exits_1(self):
        result = runner.invoke(connector_app, ["connect", "bogus-connector"])
        assert result.exit_code == 1
        assert "Unknown connector" in result.output

    def test_connect_no_oauth_calls_connect(self):
        """google_calendar has requires_oauth=False; connect() sets status CONNECTED."""
        result = runner.invoke(connector_app, ["connect", "google_calendar"])
        assert result.exit_code == 0
        assert "connected" in result.output.lower()

        registry = get_connector_registry()
        cal = registry.get("google_calendar")
        assert cal._status == ConnectorStatus.CONNECTED

    def test_connect_oauth_authenticates(self):
        """gmail has requires_oauth=True; auth manager should be invoked."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_auth_instance = MagicMock()
        mock_auth_instance.authenticate = AsyncMock(return_value="fake-access-token")

        with (
            patch("navig.commands.connector_cmd._register_oauth_config") as mock_register,
            patch(
                "navig.connectors.auth_manager.ConnectorAuthManager",
                return_value=mock_auth_instance,
            ),
        ):
            result = runner.invoke(connector_app, ["connect", "gmail"])

        assert result.exit_code == 0
        assert "connected" in result.output.lower()
        mock_register.assert_called_once()
        mock_auth_instance.authenticate.assert_awaited_once()


class TestConnectorHealth:
    def test_health_no_connected(self):
        """health delegates to connector_status; no connected connectors → info message."""
        result = runner.invoke(connector_app, ["health"])
        assert result.exit_code == 0
        assert "No connectors connected" in result.output

    def test_health_with_connected(self):
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["health"])
        assert result.exit_code == 0
        assert "gmail" in result.output.lower() or "healthy" in result.output.lower()

    def test_health_targeted_connected(self):
        """health <id> when connector is CONNECTED → shows health for that connector only."""
        registry = get_connector_registry()
        gmail = registry.get("gmail")
        gmail._status = ConnectorStatus.CONNECTED

        result = runner.invoke(connector_app, ["health", "gmail"])
        assert result.exit_code == 0
        assert "gmail" in result.output.lower()
        assert "healthy" in result.output.lower()
        # Calendar (not connected) should NOT appear in targeted output
        assert "google_calendar" not in result.output

    def test_health_targeted_not_connected(self):
        """health <id> when connector is DISCONNECTED → friendly message, no error."""
        result = runner.invoke(connector_app, ["health", "google_calendar"])
        assert result.exit_code == 0
        assert (
            "not connected" in result.output.lower() or "google_calendar" in result.output.lower()
        )

    def test_health_targeted_unknown_id(self):
        """health <unknown-id> → exit 1 with error message."""
        result = runner.invoke(connector_app, ["health", "no-such-connector"])
        assert result.exit_code == 1
        assert "unknown" in result.output.lower() or "no-such-connector" in result.output.lower()


class TestRegisterOauthConfig:
    def test_unknown_connector_prints_warning(self):
        """_register_oauth_config with an unknown id logs a warning and returns."""
        from navig.commands.connector_cmd import _register_oauth_config

        auth = ConnectorAuthManager()
        # Should not raise and should emit a warning (we just verify no exception)
        _register_oauth_config("unsupported_connector", auth)


# ── Connectedness comes from the VAULT, not from in-process instances ────────
#
# Regression: every one of these commands decided "is anything connected?" from
# `registry.list_connected()`, which filters `_instances` -- and instances are created
# lazily, so a fresh CLI process has none. Measured on the real registry: 16 connectors
# registered, `list_connected()` == [], every `list_all()` status "disconnected".
# So on a healthy install with Gmail linked:
#   navig connector list    -> Gmail shown as "disconnected"
#   navig connector status  -> "No connectors connected."
#   navig connector search  -> "No connectors connected. Use `navig connector connect`"
#   navig connector health gmail -> "Connector 'gmail' is not connected."
# and search/fetch never loaded the vault token, so the connector raised the opaque
# "Connector 'gmail' has no access token" even when reached.


def _vault_has_gmail(monkeypatch, email: str = "user@example.com"):
    """Simulate a user who linked Gmail: the credential lives in the vault."""
    monkeypatch.setattr(
        ConnectorAuthManager, "list_connected_accounts", lambda self: {"gmail": email}
    )

    async def _inject(self, connector):
        connector.set_access_token("vault-token")
        return True

    monkeypatch.setattr(ConnectorAuthManager, "inject_token", _inject)


class TestVaultBackedConnectedness:
    def test_list_shows_vault_linked_connector_as_connected(self, monkeypatch):
        _vault_has_gmail(monkeypatch)
        result = runner.invoke(connector_app, ["list", "--json"])
        assert result.exit_code == 0
        data = {c["id"]: c for c in json.loads(result.output)}
        assert data["gmail"]["status"] == "connected", (
            "a linked account must not render as disconnected just because this "
            "process has not instantiated the connector yet"
        )
        assert data["gmail"]["account"] == "user@example.com"
        # A genuinely unlinked connector is untouched.
        assert data["google_calendar"]["status"] == "disconnected"

    def test_status_finds_vault_linked_connector_with_no_live_instances(self, monkeypatch):
        _vault_has_gmail(monkeypatch)
        registry = get_connector_registry()
        assert registry.list_connected() == [], "precondition: no instance is connected"

        result = runner.invoke(connector_app, ["status"])
        assert result.exit_code == 0
        assert "No connectors connected" not in result.output
        assert "gmail" in result.output

    def test_search_uses_vault_linked_connectors(self, monkeypatch):
        _vault_has_gmail(monkeypatch)
        result = runner.invoke(connector_app, ["search", "hello", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert any(r["source"] == "gmail" for r in data), (
            "search found nothing because it only looked at in-process instances"
        )

    def test_search_hydrates_the_token_before_calling_the_connector(self, monkeypatch):
        _vault_has_gmail(monkeypatch)
        runner.invoke(connector_app, ["search", "hello", "--json"])
        gmail = get_connector_registry().get("gmail")
        assert gmail._access_token == "vault-token", (
            "the vault token must reach the instance -- without it the connector "
            "raises 'has no access token'"
        )

    def test_health_accepts_a_vault_linked_connector(self, monkeypatch):
        _vault_has_gmail(monkeypatch)
        result = runner.invoke(connector_app, ["health", "gmail"])
        assert result.exit_code == 0
        assert "is not connected" not in result.output
        assert "healthy" in result.output.lower()

    def test_unlinked_oauth_connector_gets_an_actionable_message_not_a_token_error(self):
        """The autouse fixture leaves the vault empty, so gmail is genuinely unlinked."""
        result = runner.invoke(connector_app, ["fetch", "gmail:msg-1"])
        assert result.exit_code == 1
        out = result.output.lower()
        assert "no access token" not in out, "the raw RuntimeError must not reach the user"
        assert "not connected" in out and "navig connector connect gmail" in result.output

    def test_non_oauth_connector_still_works_without_any_vault_entry(self):
        """google_calendar declares requires_oauth=False — it must not need a token."""
        result = runner.invoke(connector_app, ["fetch", "google_calendar:evt-1"])
        assert result.exit_code == 0
        assert "Event" in result.output
