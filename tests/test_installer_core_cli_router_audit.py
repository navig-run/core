"""Tests for installer/core_cli, gateway/routes/router_status, gateway/routes/audit — batch 47."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# installer/modules/core_cli
# ---------------------------------------------------------------------------

def _make_installer_types():
    from navig.installer.contracts import Action, InstallerContext, ModuleState, Result
    return Action, InstallerContext, ModuleState, Result


def test_core_cli_name():
    import navig.installer.modules.core_cli as m

    assert m.name == "core_cli"


def test_core_cli_description():
    import navig.installer.modules.core_cli as m

    assert "navig" in m.description.lower() or "PATH" in m.description


def test_core_cli_plan_returns_list():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import InstallerContext

    ctx = MagicMock(spec=InstallerContext)
    actions = m.plan(ctx)
    assert isinstance(actions, list)


def test_core_cli_plan_has_one_action():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import InstallerContext

    ctx = MagicMock(spec=InstallerContext)
    actions = m.plan(ctx)
    assert len(actions) == 1


def test_core_cli_plan_action_id():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import InstallerContext

    ctx = MagicMock(spec=InstallerContext)
    action = m.plan(ctx)[0]
    assert action.id == "core_cli.verify"


def test_core_cli_plan_action_not_reversible():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import InstallerContext

    ctx = MagicMock(spec=InstallerContext)
    action = m.plan(ctx)[0]
    assert action.reversible is False


def test_core_cli_apply_navig_found():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import Action, InstallerContext, ModuleState

    action = Action(id="core_cli.verify", description="test", module="core_cli", reversible=False)
    ctx = MagicMock(spec=InstallerContext)

    with patch("shutil.which", return_value="/usr/local/bin/navig"):
        with patch.object(m, "_navig_version", return_value="1.2.3"):
            result = m.apply(action, ctx)

    assert result.state == ModuleState.APPLIED
    assert "navig" in result.message


def test_core_cli_apply_navig_not_found_fallback_ok():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import Action, InstallerContext, ModuleState

    action = Action(id="core_cli.verify", description="test", module="core_cli", reversible=False)
    ctx = MagicMock(spec=InstallerContext)

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch("shutil.which", return_value=None):
        with patch("subprocess.run", return_value=mock_proc):
            result = m.apply(action, ctx)

    assert result.state == ModuleState.APPLIED


def test_core_cli_apply_navig_not_found_fallback_fails():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import Action, InstallerContext, ModuleState

    action = Action(id="core_cli.verify", description="test", module="core_cli", reversible=False)
    ctx = MagicMock(spec=InstallerContext)

    mock_proc = MagicMock()
    mock_proc.returncode = 1

    with patch("shutil.which", return_value=None):
        with patch("subprocess.run", return_value=mock_proc):
            result = m.apply(action, ctx)

    assert result.state == ModuleState.FAILED


def test_core_cli_apply_navig_not_found_exception_path():
    import navig.installer.modules.core_cli as m
    from navig.installer.contracts import Action, InstallerContext, ModuleState

    action = Action(id="core_cli.verify", description="test", module="core_cli", reversible=False)
    ctx = MagicMock(spec=InstallerContext)

    with patch("shutil.which", return_value=None):
        with patch("subprocess.run", side_effect=Exception("timeout")):
            result = m.apply(action, ctx)

    assert result.state == ModuleState.FAILED


def test_navig_version_returns_string():
    import navig.installer.modules.core_cli as m

    ver = m._navig_version()
    assert isinstance(ver, str)


def test_navig_version_handles_exception():
    import navig.installer.modules.core_cli as m
    import importlib.metadata

    with patch.object(importlib.metadata, "version", side_effect=Exception("not found")):
        ver = m._navig_version()
    assert ver == "unknown"


# ---------------------------------------------------------------------------
# gateway/routes/router_status — register function
# ---------------------------------------------------------------------------

def test_router_status_register_adds_get_routes():
    from navig.gateway.routes.router_status import register

    mock_app = MagicMock()
    mock_gateway = MagicMock()
    register(mock_app, mock_gateway)

    added_routes = [call[0][0] for call in mock_app.router.add_get.call_args_list]
    assert "/router/status" in added_routes
    assert "/router/traces" in added_routes


def test_router_status_register_adds_post_route():
    from navig.gateway.routes.router_status import register

    mock_app = MagicMock()
    mock_gateway = MagicMock()
    register(mock_app, mock_gateway)

    added_routes = [call[0][0] for call in mock_app.router.add_post.call_args_list]
    assert "/router/detect" in added_routes


def test_router_status_register_three_routes_total():
    from navig.gateway.routes.router_status import register

    mock_app = MagicMock()
    mock_gateway = MagicMock()
    register(mock_app, mock_gateway)

    total = mock_app.router.add_get.call_count + mock_app.router.add_post.call_count
    assert total == 3


@pytest.mark.asyncio
async def test_router_status_handler_error_path():
    from navig.gateway.routes.router_status import _router_status

    mock_request = MagicMock()

    with patch.dict("sys.modules", {"navig.routing.router": None}):
        response = await _router_status(mock_request)
    # Handler returns an aiohttp.web.Response — just check it's not None
    assert response is not None


@pytest.mark.asyncio
async def test_router_traces_handler_error_path():
    from navig.gateway.routes.router_status import _router_traces

    mock_request = MagicMock()
    mock_request.query = {"limit": "10"}

    with patch.dict("sys.modules", {"navig.routing.trace": None}):
        response = await _router_traces(mock_request)
    assert response is not None


@pytest.mark.asyncio
async def test_router_detect_empty_text():
    from navig.gateway.routes.router_status import _router_detect

    mock_request = AsyncMock()
    mock_request.json = AsyncMock(return_value={"text": ""})

    response = await _router_detect(mock_request)
    # Should return 400 response for empty text
    assert response is not None


# ---------------------------------------------------------------------------
# gateway/routes/audit — register function
# ---------------------------------------------------------------------------

def test_audit_register_adds_get_route():
    from navig.gateway.routes.audit import register

    mock_app = MagicMock()
    mock_gateway = MagicMock()
    register(mock_app, mock_gateway)

    assert mock_app.router.add_get.called
    route_path = mock_app.router.add_get.call_args[0][0]
    assert route_path == "/audit"


def test_audit_register_passes_handler():
    from navig.gateway.routes.audit import register

    mock_app = MagicMock()
    mock_gateway = MagicMock()
    register(mock_app, mock_gateway)

    # Handler should be a callable
    handler = mock_app.router.add_get.call_args[0][1]
    assert callable(handler)
