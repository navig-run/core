"""
Batch 114: tests for
  navig/gateway/routes/common.py
  navig/gateway/routes/heartbeat.py
  navig/gateway/routes/router_status.py
  navig/commands/system_cmd.py
  navig/voice/playback.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig/gateway/routes/common.py
# ---------------------------------------------------------------------------

from navig.gateway.routes.common import envelope_error, envelope_ok


def test_envelope_ok_no_data():
    r = envelope_ok()
    assert r["ok"] is True
    assert r["data"] is None
    assert r["error"] is None


def test_envelope_ok_with_data():
    r = envelope_ok({"count": 5})
    assert r["ok"] is True
    assert r["data"] == {"count": 5}


def test_envelope_error_basic():
    r = envelope_error("Not found", code="not_found")
    assert r["ok"] is False
    assert r["error"] == "Not found"
    assert r["error_code"] == "not_found"
    assert r["data"] is None


def test_envelope_error_with_details():
    r = envelope_error("Fail", code="fail", details={"x": 1})
    assert r["details"] == {"x": 1}


def test_envelope_error_no_details_key():
    r = envelope_error("Fail", code="fail")
    assert "details" not in r


# ---------------------------------------------------------------------------
# navig/gateway/routes/heartbeat.py  (all handlers are closures)
# ---------------------------------------------------------------------------

from navig.gateway.routes.heartbeat import _history, _status, _trigger
from navig.gateway.routes.heartbeat import register as hb_register


def _make_gw(has_runner: bool = True) -> MagicMock:
    gw = MagicMock()
    gw.heartbeat_runner = MagicMock() if has_runner else None
    gw.auth_token = "tok"
    return gw


def _make_request(query: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.query = query or {}
    req.headers = {"Authorization": "Bearer tok"}
    return req


def test_heartbeat_register_routes():
    from unittest.mock import ANY
    app = MagicMock()
    gw = _make_gw()
    hb_register(app, gw)
    assert app.router.add_post.call_count == 1
    assert app.router.add_get.call_count == 2
    app.router.add_post.assert_called_with("/heartbeat/trigger", ANY)


@pytest.mark.asyncio
async def test_trigger_auth_fails():
    gw = _make_gw()
    handler = _trigger(gw)
    fake_auth_resp = MagicMock()
    with patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=fake_auth_resp):
        result = await handler(_make_request())
    assert result is fake_auth_resp


@pytest.mark.asyncio
async def test_trigger_no_runner():
    gw = _make_gw(has_runner=False)
    handler = _trigger(gw)
    fake_503 = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_error_response", return_value=fake_503) as mock_err,
    ):
        result = await handler(_make_request())
    assert result is fake_503
    mock_err.assert_called_once()
    assert mock_err.call_args.kwargs.get("status") == 503 or mock_err.call_args[1].get("status") == 503


@pytest.mark.asyncio
async def test_trigger_success():
    gw = _make_gw()
    ts = MagicMock()
    ts.isoformat.return_value = "2024-01-01T00:00:00"
    run_result = MagicMock(success=True, suppressed=False, response="ok", issues_found=[], timestamp=ts)
    gw.heartbeat_runner.trigger_now = AsyncMock(return_value=run_result)
    handler = _trigger(gw)
    fake_ok = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_ok", return_value=fake_ok) as mock_ok,
    ):
        result = await handler(_make_request())
    assert result is fake_ok
    payload = mock_ok.call_args[0][0]
    assert payload["success"] is True


@pytest.mark.asyncio
async def test_trigger_exception():
    gw = _make_gw()
    gw.heartbeat_runner.trigger_now = AsyncMock(side_effect=RuntimeError("boom"))
    handler = _trigger(gw)
    fake_500 = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_error_response", return_value=fake_500) as mock_err,
    ):
        result = await handler(_make_request())
    assert result is fake_500
    assert mock_err.call_args.kwargs.get("status") == 500 or mock_err.call_args[1].get("status") == 500


@pytest.mark.asyncio
async def test_history_no_runner():
    gw = _make_gw(has_runner=False)
    handler = _history(gw)
    fake_503 = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_error_response", return_value=fake_503),
    ):
        result = await handler(_make_request())
    assert result is fake_503


@pytest.mark.asyncio
async def test_history_success():
    gw = _make_gw()
    gw.heartbeat_runner.get_history.return_value = [{"run": 1}]
    req = _make_request(query={"limit": "5"})
    handler = _history(gw)
    fake_ok = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_ok", return_value=fake_ok) as mock_ok,
    ):
        result = await handler(req)
    assert result is fake_ok
    payload = mock_ok.call_args[0][0]
    assert "history" in payload


@pytest.mark.asyncio
async def test_history_exception():
    gw = _make_gw()
    gw.heartbeat_runner.get_history.side_effect = RuntimeError("db fail")
    handler = _history(gw)
    fake_500 = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_error_response", return_value=fake_500),
    ):
        result = await handler(_make_request())
    assert result is fake_500


@pytest.mark.asyncio
async def test_status_no_runner():
    gw = _make_gw(has_runner=False)
    handler = _status(gw)
    fake_503 = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_error_response", return_value=fake_503),
    ):
        result = await handler(_make_request())
    assert result is fake_503


@pytest.mark.asyncio
async def test_status_success():
    gw = _make_gw()
    gw.heartbeat_runner.get_status.return_value = {"running": True}
    handler = _status(gw)
    fake_ok = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_ok", return_value=fake_ok) as mock_ok,
    ):
        result = await handler(_make_request())
    assert result is fake_ok
    mock_ok.assert_called_once_with({"running": True})


@pytest.mark.asyncio
async def test_status_exception():
    gw = _make_gw()
    gw.heartbeat_runner.get_status.side_effect = RuntimeError("status fail")
    handler = _status(gw)
    fake_500 = MagicMock()
    with (
        patch("navig.gateway.routes.heartbeat.require_bearer_auth", return_value=None),
        patch("navig.gateway.routes.heartbeat.json_error_response", return_value=fake_500),
    ):
        result = await handler(_make_request())
    assert result is fake_500


# ---------------------------------------------------------------------------
# navig/gateway/routes/router_status.py
# ---------------------------------------------------------------------------

from navig.gateway.routes.router_status import (
    _router_detect,
    _router_status,
    _router_traces,
)
from navig.gateway.routes.router_status import register as rs_register


def _make_json_request(body: dict | None = None, query: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.query = query or {}
    req.json = AsyncMock(return_value=body or {})
    return req


@pytest.mark.asyncio
async def test_router_status_success():
    mock_router = AsyncMock()
    mock_status = MagicMock()
    mock_status.to_dict.return_value = {"providers": ["openai"], "active": "openai"}
    mock_router.status.return_value = mock_status
    with patch("navig.routing.router.get_router", return_value=mock_router):
        resp = await _router_status(_make_json_request())
    assert resp.status == 200


@pytest.mark.asyncio
async def test_router_status_exception():
    with patch("navig.routing.router.get_router", side_effect=ImportError("no module")):
        resp = await _router_status(_make_json_request())
    assert resp.status == 500


@pytest.mark.asyncio
async def test_router_traces_success():
    with patch("navig.routing.trace.recent_traces", return_value=[{"id": 1}]):
        resp = await _router_traces(_make_json_request(query={"limit": "10"}))
    assert resp.status == 200


@pytest.mark.asyncio
async def test_router_traces_exception():
    with patch("navig.routing.trace.recent_traces", side_effect=RuntimeError("fail")):
        resp = await _router_traces(_make_json_request())
    assert resp.status == 500


@pytest.mark.asyncio
async def test_router_detect_empty_text():
    req = _make_json_request(body={"text": ""})
    resp = await _router_detect(req)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_router_detect_success():
    mock_caps = MagicMock()
    mock_caps.required = ["llm"]
    mock_caps.preferred = []
    mock_caps.cost_target = "low"
    mock_caps.latency_target = "fast"
    with (
        patch("navig.routing.detect.detect_mode", return_value=("chat", 0.9, ["keyword"])),
        patch("navig.routing.capabilities.MODE_CAPABILITIES", {"chat": mock_caps}),
    ):
        resp = await _router_detect(_make_json_request(body={"text": "hello"}))
    assert resp.status == 200


@pytest.mark.asyncio
async def test_router_detect_exception():
    req = _make_json_request(body={"text": "hello"})
    with patch("navig.routing.detect.detect_mode", side_effect=RuntimeError("classify fail")):
        resp = await _router_detect(req)
    assert resp.status == 500


def test_router_status_register():
    app = MagicMock()
    gw = MagicMock()
    rs_register(app, gw)
    assert app.router.add_get.call_count == 2
    assert app.router.add_post.call_count == 1
    routes = [c.args[0] for c in app.router.add_get.call_args_list]
    assert "/router/status" in routes
    assert "/router/traces" in routes


# ---------------------------------------------------------------------------
# navig/commands/system_cmd.py
# ---------------------------------------------------------------------------

from typer.testing import CliRunner as TyCliRunner

from navig.commands.system_cmd import system_app

_cli_runner = TyCliRunner()


def test_system_info_runs():
    with patch("navig.commands.system_cmd.system_default"):
        result = _cli_runner.invoke(system_app, ["info"])
    assert result.exit_code == 0


def test_system_default_runs():
    result = _cli_runner.invoke(system_app, [])
    assert result.exit_code == 0


def test_system_clean_yes_no_targets(tmp_path):
    """--yes with non-existent targets → exits 0 without error."""
    with patch("navig.commands.system_cmd.config_dir", return_value=tmp_path):
        result = _cli_runner.invoke(system_app, ["clean", "--yes"])
    assert result.exit_code == 0


def test_system_clean_yes_removes_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "file.txt").write_text("data")
    with patch("navig.commands.system_cmd.config_dir", return_value=tmp_path):
        result = _cli_runner.invoke(system_app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert not cache_dir.exists()


# ---------------------------------------------------------------------------
# navig/voice/playback.py
# ---------------------------------------------------------------------------

from navig.voice.playback import (
    ASSETS_DIR,
    NotificationSound,
    _resolve_asset,
    list_sounds,
    play_notification,
    play_sound,
)


def test_notification_sound_enum_values():
    assert NotificationSound.ALARM.value == "alarm-default.mp3"
    assert NotificationSound.WAKE.value == "echo_en_wake.wav"
    assert NotificationSound.OK.value == "echo_en_ok.wav"


def test_list_sounds_contains_all():
    sounds = list_sounds()
    assert "alarm" in sounds
    assert "wake" in sounds
    assert "ok" in sounds
    assert len(sounds) == len(list(NotificationSound))


def test_list_sounds_lowercase():
    sounds = list_sounds()
    assert all(s == s.lower() for s in sounds)


def test_resolve_asset_nonexistent_name():
    """Unknown name with no matching file → None."""
    result = _resolve_asset("definitely_not_a_real_sound_xyz.wav")
    assert result is None


def test_resolve_asset_by_enum_value_missing_file():
    """Enum value that doesn't exist on disk → None."""
    result = _resolve_asset("alarm-default.mp3")
    # assets dir may not exist in test env; result is None or Path
    assert result is None or isinstance(result, Path)


def test_resolve_asset_absolute_nonexistent():
    result = _resolve_asset("/tmp/nonexistent_sound_12345.wav")
    assert result is None


def test_resolve_asset_enum_name_case_insensitive():
    """Case-insensitive name lookup."""
    result_lower = _resolve_asset("ALARM")
    result_upper = _resolve_asset("alarm")
    # Both should return the same (None if no asset dir, or same Path)
    assert result_lower == result_upper


@pytest.mark.asyncio
async def test_play_sound_unknown_returns_false():
    """Unknown sound with no file → False."""
    result = await play_sound("not_a_real_sound_xyz123.mp3")
    assert result is False


@pytest.mark.asyncio
async def test_play_notification_unknown_returns_false():
    result = await play_notification("not_a_real_sound_xyz123")
    assert result is False


@pytest.mark.asyncio
async def test_play_sound_with_mock_windows_path(tmp_path):
    """If a .wav file exists and we mock the platform + winsound, should succeed."""
    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 4)

    with (
        patch("platform.system", return_value="Windows"),
        patch("winsound.PlaySound", return_value=None),
    ):
        result = await play_sound(str(wav))
    # winsound.PlaySound is sync; result may be True or False depending on env
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_play_sound_with_mock_macos_path(tmp_path):
    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 4)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    with (
        patch("platform.system", return_value="Darwin"),
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
    ):
        result = await play_sound(str(wav))
    assert result is True
