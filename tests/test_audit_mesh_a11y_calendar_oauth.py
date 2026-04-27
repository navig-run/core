"""
Batch 59: hermetic unit tests for
  - navig/gateway/routes/audit.py   (async audit log route)
  - navig/commands/mesh.py          (Typer mesh commands)
  - navig/browser/a11y.py           (annotate_a11y_snapshot)
  - navig/connectors/google_calendar/oauth_config.py (CALENDAR_SCOPES, build_calendar_oauth_config)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/gateway/routes/audit.py — pure logic (no HTTP server needed)
# ---------------------------------------------------------------------------

class TestAuditTailLogic:
    """Test the inner handler logic by calling _tail(gw)(request) directly."""

    def _make_handler(self, log_entries, query_params=None):
        """Return the inner async handler from _tail(gw)."""
        from navig.gateway.routes.audit import _tail

        gw = MagicMock()
        gw.audit_log.tail = MagicMock(return_value=log_entries)

        query = MagicMock()
        query.get = lambda key, default=None: (query_params or {}).get(key, default)

        request = MagicMock()
        request.query = query

        # Make require_bearer_auth return None (= auth passed)
        return _tail(gw), request, gw

    @pytest.mark.asyncio
    async def test_returns_all_events_when_no_filters(self) -> None:
        from navig.gateway.routes.audit import _tail
        import json

        entries = [{"action": "run", "actor": "agent", "status": "ok"}]
        gw = MagicMock()
        gw.audit_log.tail.return_value = entries
        query = {"limit": "50"}
        r = MagicMock()
        r.query.get = lambda k, d=None: query.get(k, d)

        with patch("navig.gateway.routes.audit.require_bearer_auth", return_value=None), \
             patch("navig.gateway.routes.audit.json_ok") as mock_json_ok:
            mock_json_ok.return_value = MagicMock()
            handler = _tail(gw)
            await handler(r)
        mock_json_ok.assert_called_once()
        call_data = mock_json_ok.call_args[0][0]
        assert call_data["count"] == 1

    @pytest.mark.asyncio
    async def test_filters_by_action(self) -> None:
        from navig.gateway.routes.audit import _tail

        entries = [
            {"action": "run", "actor": "agent", "status": "ok"},
            {"action": "db.query", "actor": "user", "status": "ok"},
        ]
        gw = MagicMock()
        gw.audit_log.tail.return_value = entries
        r = MagicMock()
        r.query.get = lambda k, d=None: {"limit": "50", "action": "run"}.get(k, d)

        with patch("navig.gateway.routes.audit.require_bearer_auth", return_value=None), \
             patch("navig.gateway.routes.audit.json_ok") as mock_json_ok:
            mock_json_ok.return_value = MagicMock()
            await _tail(gw)(r)
        data = mock_json_ok.call_args[0][0]
        assert data["count"] == 1
        assert data["events"][0]["action"] == "run"

    @pytest.mark.asyncio
    async def test_filters_by_actor(self) -> None:
        from navig.gateway.routes.audit import _tail

        entries = [
            {"action": "run", "actor": "alice", "status": "ok"},
            {"action": "run", "actor": "bob", "status": "ok"},
        ]
        gw = MagicMock()
        gw.audit_log.tail.return_value = entries
        r = MagicMock()
        r.query.get = lambda k, d=None: {"limit": "50", "actor": "alice"}.get(k, d)

        with patch("navig.gateway.routes.audit.require_bearer_auth", return_value=None), \
             patch("navig.gateway.routes.audit.json_ok") as mock_json_ok:
            mock_json_ok.return_value = MagicMock()
            await _tail(gw)(r)
        data = mock_json_ok.call_args[0][0]
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_filters_by_status(self) -> None:
        from navig.gateway.routes.audit import _tail

        entries = [
            {"action": "run", "actor": "a", "status": "ok"},
            {"action": "run", "actor": "b", "status": "fail"},
        ]
        gw = MagicMock()
        gw.audit_log.tail.return_value = entries
        r = MagicMock()
        r.query.get = lambda k, d=None: {"limit": "50", "status": "fail"}.get(k, d)

        with patch("navig.gateway.routes.audit.require_bearer_auth", return_value=None), \
             patch("navig.gateway.routes.audit.json_ok") as mock_json_ok:
            mock_json_ok.return_value = MagicMock()
            await _tail(gw)(r)
        data = mock_json_ok.call_args[0][0]
        assert data["count"] == 1
        assert data["events"][0]["status"] == "fail"

    @pytest.mark.asyncio
    async def test_auth_failure_short_circuits(self) -> None:
        from navig.gateway.routes.audit import _tail

        gw = MagicMock()
        r = MagicMock()
        auth_error = MagicMock()

        with patch("navig.gateway.routes.audit.require_bearer_auth", return_value=auth_error):
            result = await _tail(gw)(r)
        assert result is auth_error

    @pytest.mark.asyncio
    async def test_limit_capped_at_500(self) -> None:
        from navig.gateway.routes.audit import _tail

        # 600 entries, limit=1000 → returns at most 500
        entries = [{"action": "a", "actor": "x", "status": "ok"}] * 600
        gw = MagicMock()
        gw.audit_log.tail.return_value = entries
        r = MagicMock()
        r.query.get = lambda k, d=None: {"limit": "1000"}.get(k, d)

        with patch("navig.gateway.routes.audit.require_bearer_auth", return_value=None), \
             patch("navig.gateway.routes.audit.json_ok") as mock_json_ok:
            mock_json_ok.return_value = MagicMock()
            await _tail(gw)(r)
        data = mock_json_ok.call_args[0][0]
        assert data["count"] <= 500

    def test_register_function_importable(self) -> None:
        from navig.gateway.routes.audit import register
        assert callable(register)


# ---------------------------------------------------------------------------
# navig/commands/mesh.py
# ---------------------------------------------------------------------------

class TestMeshApp:
    def test_mesh_app_importable(self) -> None:
        from navig.commands.mesh import mesh_app
        assert mesh_app is not None

    def test_has_status_command(self) -> None:
        from navig.commands.mesh import mesh_app
        # Typer sets name=None for auto-named commands; just check count
        assert len(mesh_app.registered_commands) >= 1

    def test_has_peers_command(self) -> None:
        from navig.commands.mesh import mesh_app
        assert len(mesh_app.registered_commands) >= 2

    def test_status_no_peers(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.mesh import mesh_app

        mock_registry = MagicMock()
        mock_registry.list_peers.return_value = []
        with patch("navig.mesh.registry.get_registry", return_value=mock_registry):
            runner = CliRunner()
            result = runner.invoke(mesh_app, ["status"])
        assert result.exit_code == 0

    def test_status_with_peers(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.mesh import mesh_app

        peer = MagicMock()
        peer.hostname = "node1"
        peer.gateway_url = "http://node1:8080"
        peer.load = 0.5
        peer.capabilities = ["run", "db"]

        mock_registry = MagicMock()
        mock_registry.list_peers.return_value = [peer]
        with patch("navig.mesh.registry.get_registry", return_value=mock_registry):
            runner = CliRunner()
            result = runner.invoke(mesh_app, ["status"])
        assert result.exit_code == 0
        assert "node1" in result.output

    def test_status_handles_import_error(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.mesh import mesh_app

        with patch("navig.mesh.registry.get_registry", side_effect=ImportError("no mesh")):
            runner = CliRunner()
            result = runner.invoke(mesh_app, ["status"])
        # handles exception gracefully (peers=[])
        assert result.exit_code == 0

    def test_peers_delegates_to_status(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.mesh import mesh_app

        mock_registry = MagicMock()
        mock_registry.list_peers.return_value = []
        with patch("navig.mesh.registry.get_registry", return_value=mock_registry):
            runner = CliRunner()
            r1 = runner.invoke(mesh_app, ["peers"])
            r2 = runner.invoke(mesh_app, ["status"])
        assert r1.exit_code == r2.exit_code == 0


# ---------------------------------------------------------------------------
# navig/browser/a11y.py
# ---------------------------------------------------------------------------

class TestAnnotateA11ySnapshot:
    def test_empty_string(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        text, ref_map = annotate_a11y_snapshot("")
        assert text == ""
        assert ref_map == {}

    def test_element_line_gets_ref(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- button "Submit"'
        text, ref_map = annotate_a11y_snapshot(raw)
        assert len(ref_map) == 1
        assert 0 in ref_map

    def test_ref_map_contains_role(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- button "Submit"'
        _, ref_map = annotate_a11y_snapshot(raw)
        assert ref_map[0]["role"] == "button"

    def test_ref_map_contains_name(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- button "Submit"'
        _, ref_map = annotate_a11y_snapshot(raw)
        assert ref_map[0]["name"] == "Submit"

    def test_annotated_text_contains_ref_id(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- button "Submit"'
        text, _ = annotate_a11y_snapshot(raw)
        assert "[0]" in text

    def test_multiple_elements_incrementing_ids(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- button "A"\n- link "B"\n- heading "C"'
        _, ref_map = annotate_a11y_snapshot(raw)
        assert set(ref_map.keys()) == {0, 1, 2}

    def test_non_element_line_not_in_ref_map(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = "just text\n- button \"OK\""
        _, ref_map = annotate_a11y_snapshot(raw)
        # only the "- " prefixed line gets a ref
        assert len(ref_map) == 1

    def test_slash_line_skipped(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = "- /html/body\n- button \"OK\""
        _, ref_map = annotate_a11y_snapshot(raw)
        # slash lines are not ref-annotated
        assert len(ref_map) == 1

    def test_preserves_indentation(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '  - button "Inner"'
        text, _ = annotate_a11y_snapshot(raw)
        # indentation preserved
        assert text.startswith("  -")

    def test_raw_line_stored(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- button "Submit"'
        _, ref_map = annotate_a11y_snapshot(raw)
        assert "raw_line" in ref_map[0]
        assert ref_map[0]["raw_line"] == raw

    def test_bracket_name_extraction(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        raw = '- input [name=email]'
        _, ref_map = annotate_a11y_snapshot(raw)
        # name extracted from [...]
        assert ref_map[0]["name"] == "name=email"

    def test_returns_tuple(self) -> None:
        from navig.browser.a11y import annotate_a11y_snapshot
        result = annotate_a11y_snapshot("- button \"OK\"")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# navig/connectors/google_calendar/oauth_config.py
# ---------------------------------------------------------------------------

class TestCalendarScopes:
    def test_is_list(self) -> None:
        from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES
        assert isinstance(CALENDAR_SCOPES, list)

    def test_non_empty(self) -> None:
        from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES
        assert len(CALENDAR_SCOPES) > 0

    def test_contains_calendar_scope(self) -> None:
        from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES
        assert any("calendar" in s for s in CALENDAR_SCOPES)

    def test_contains_email_and_profile(self) -> None:
        from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES
        assert "email" in CALENDAR_SCOPES
        assert "profile" in CALENDAR_SCOPES

    def test_all_strings(self) -> None:
        from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES
        assert all(isinstance(s, str) for s in CALENDAR_SCOPES)


class TestBuildCalendarOAuthConfig:
    def test_returns_oauth_provider_config(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        from navig.providers.oauth import OAuthProviderConfig
        result = build_calendar_oauth_config("client-id-123")
        assert isinstance(result, OAuthProviderConfig)

    def test_client_id_set(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        result = build_calendar_oauth_config("my-client-id")
        assert result.client_id == "my-client-id"

    def test_name_is_google_calendar(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        result = build_calendar_oauth_config("cid")
        assert result.name == "Google Calendar"

    def test_scopes_match_calendar_scopes(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config, CALENDAR_SCOPES
        result = build_calendar_oauth_config("cid")
        assert result.scopes == CALENDAR_SCOPES

    def test_client_secret_optional(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        result = build_calendar_oauth_config("cid", client_secret=None)
        assert result.client_secret is None

    def test_client_secret_set(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        result = build_calendar_oauth_config("cid", client_secret="secret123")
        assert result.client_secret == "secret123"

    def test_authorize_url_is_google(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        result = build_calendar_oauth_config("cid")
        assert "google" in result.authorize_url.lower() or "accounts" in result.authorize_url.lower()

    def test_token_url_is_google(self) -> None:
        from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config
        result = build_calendar_oauth_config("cid")
        assert "google" in result.token_url.lower() or "oauth" in result.token_url.lower()
