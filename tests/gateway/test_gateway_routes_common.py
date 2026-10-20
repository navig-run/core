"""Tests for navig.gateway.routes.common — envelope_ok, envelope_error."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navig.gateway.routes.common import envelope_error, envelope_ok


class TestEnvelopeOk:
    def test_ok_true(self):
        r = envelope_ok()
        assert r["ok"] is True

    def test_no_data_default_none(self):
        r = envelope_ok()
        assert r["data"] is None

    def test_error_field_is_none(self):
        r = envelope_ok()
        assert r["error"] is None

    def test_data_passed_through(self):
        r = envelope_ok({"key": "value"})
        assert r["data"] == {"key": "value"}

    def test_simple_scalar_data(self):
        r = envelope_ok(42)
        assert r["data"] == 42


class TestEnvelopeError:
    def test_ok_false(self):
        r = envelope_error("Something failed", code="NOT_FOUND")
        assert r["ok"] is False

    def test_error_message(self):
        r = envelope_error("Bad request", code="BAD_REQUEST")
        assert r["error"] == "Bad request"

    def test_error_code(self):
        r = envelope_error("msg", code="INTERNAL_ERROR")
        assert r["error_code"] == "INTERNAL_ERROR"

    def test_data_is_none(self):
        r = envelope_error("msg", code="err")
        assert r["data"] is None

    def test_details_included_when_given(self):
        r = envelope_error("msg", code="err", details={"field": "x"})
        assert r["details"] == {"field": "x"}

    def test_details_not_in_response_when_none(self):
        r = envelope_error("msg", code="err")
        assert "details" not in r


class TestRequireBearerAuth:
    def test_no_token_configured_fails_closed_with_503(self):
        """A failed token mint must not open every guarded route to anonymous callers."""
        from navig.gateway.routes.common import require_bearer_auth
        mock_web = MagicMock()
        mock_web.json_response.return_value = "503_response"
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = None
            request = MagicMock()
            result = require_bearer_auth(request, gateway)
        assert result == "503_response"
        mock_web.json_response.assert_called_once()
        call_kwargs = mock_web.json_response.call_args.kwargs
        assert call_kwargs["status"] == 503
        payload = mock_web.json_response.call_args.args[0]
        assert payload["ok"] is False
        assert payload["error_code"] == "auth_not_configured"

    def test_no_token_configured_still_serves_anonymous_opt_in_routes(self):
        from navig.gateway.routes.common import require_bearer_auth
        with patch("navig.gateway.routes.common._get_web"):
            gateway = MagicMock()
            gateway.config.auth_token = None
            request = MagicMock()
            result = require_bearer_auth(request, gateway, allow_anonymous=True)
        assert result is None

    def test_valid_token_returns_none(self):
        from navig.gateway.routes.common import require_bearer_auth
        mock_web = MagicMock()
        mock_web.json_response.return_value = MagicMock()
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = "secret"
            request = MagicMock()
            request.headers.get.return_value = "Bearer secret"
            result = require_bearer_auth(request, gateway)
        assert result is None

    def test_missing_bearer_returns_401(self):
        from navig.gateway.routes.common import require_bearer_auth
        mock_web = MagicMock()
        mock_web.json_response.return_value = "401_response"
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = "secret"
            request = MagicMock()
            request.headers.get.return_value = ""
            result = require_bearer_auth(request, gateway)
        assert result == "401_response"


class TestRequireBearerAuthExtraTokens:
    """The opt-in peer path used by POST /llm/chat — scoped credential
    acceptance (Gate 3 mesh seam). Default callers pass nothing and must be
    byte-identical to before; a two-node end-to-end test lives in
    tests/mesh/test_mesh_route_two_node.py."""

    @staticmethod
    def _call(*, admin, extras, header):
        from navig.gateway.routes.common import require_bearer_auth

        mock_web = MagicMock()
        mock_web.json_response.return_value = "error_response"
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = admin
            request = MagicMock()
            request.headers.get.return_value = header
            result = require_bearer_auth(request, gateway, extra_tokens=extras)
        return result, mock_web

    def test_extra_token_is_accepted_when_admin_token_exists(self):
        result, _ = self._call(admin="admin", extras=("mesh",), header="Bearer mesh")
        assert result is None

    def test_admin_token_is_still_accepted_when_extras_are_configured(self):
        result, _ = self._call(admin="admin", extras=("mesh",), header="Bearer admin")
        assert result is None

    def test_wrong_bearer_is_rejected_even_with_extras_configured(self):
        result, mock_web = self._call(
            admin="admin", extras=("mesh",), header="Bearer neither"
        )
        assert result == "error_response"
        assert mock_web.json_response.call_args.kwargs["status"] == 401

    def test_empty_and_none_extras_are_ignored(self):
        from navig.gateway.routes.common import require_bearer_auth

        mock_web = MagicMock()
        with patch("navig.gateway.routes.common._get_web", return_value=mock_web):
            gateway = MagicMock()
            gateway.config.auth_token = None
            request = MagicMock()
            request.headers.get.return_value = "Bearer anything"
            result = require_bearer_auth(
                request, gateway, extra_tokens=(None, "", "mesh")
            )
        # admin missing but a mesh secret IS configured → 401, not 503
        assert mock_web.json_response.call_args.kwargs["status"] == 401
        assert result is not None


class TestGatewayMeshToken:
    """gateway_mesh_token reads the peer secret the same way production does:
    config manager first (request-time truth), raw gateway section second."""

    def test_reads_from_config_manager(self):
        from navig.gateway.routes.common import gateway_mesh_token

        gw = MagicMock()
        gw.config_manager.global_config = {"gateway": {"mesh_token": "fleet-1"}}
        assert gateway_mesh_token(gw) == "fleet-1"

    def test_falls_back_to_raw_gateway_section(self):
        from navig.gateway.routes.common import gateway_mesh_token

        # No config_manager attribute at all → raw gateway section wins
        gw = SimpleNamespace(
            config=SimpleNamespace(auth_token="a", raw_gateway_cfg={"mesh_token": "fleet-2"})
        )
        assert gateway_mesh_token(gw) == "fleet-2"

    def test_absent_or_non_string_is_none(self):
        from navig.gateway.routes.common import gateway_mesh_token

        gw = MagicMock()
        gw.config_manager.global_config = {"gateway": {}}
        gw.config.raw_gateway_cfg = {}
        assert gateway_mesh_token(gw) is None

        gw.config_manager.global_config = {"gateway": {"mesh_token": 123}}
        assert gateway_mesh_token(gw) is None
