"""The HomeBridge: fixed argv, explicit UTF-8, injectable runner, honest errors."""

from __future__ import annotations

import json
import subprocess

import pytest

from navig.endpoints import client
from navig.endpoints.client import (
    DEFAULT_TIMEOUT,
    INSTALL_HINT,
    STATUS_HINT,
    CommandResult,
    HomeBridge,
)
from navig.endpoints.errors import HomeUnavailable
from navig.platform.process import spawn_kwargs


def _bridge_with(handler) -> HomeBridge:
    """A bridge whose child is *handler(args, timeout) -> CommandResult*."""
    def runner(args, *, timeout=DEFAULT_TIMEOUT):
        return handler([str(item) for item in args], timeout)

    return HomeBridge(runner=runner)


def _ok(argv: list[str], payload: dict) -> CommandResult:
    return CommandResult(tuple(argv), 0, json.dumps(payload), "")


def test_the_default_runner_is_a_fixed_argv_utf8_child(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = '{"ok": true, "devices": [{"id": "desk-lamp"}]}'
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert HomeBridge().devices() == [{"id": "desk-lamp"}]
    argv = captured["argv"]
    kwargs = captured["kwargs"]
    assert isinstance(argv, list) and argv[-3:] == ["home", "list", "--json"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == DEFAULT_TIMEOUT
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs.get("creationflags") == spawn_kwargs().get("creationflags")


def test_a_banner_line_before_the_json_is_tolerated() -> None:
    bridge = _bridge_with(lambda argv, _t: CommandResult(
        tuple(argv), 0, 'NAVIG · starting\n{"ok": true, "devices": [{"id": "lamp"}]}', ""))
    assert bridge.devices() == [{"id": "lamp"}]


def test_a_non_zero_exit_is_actionable() -> None:
    bridge = _bridge_with(lambda argv, _t: CommandResult(
        tuple(argv), 2, "", "no such command: home"))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.devices()
    assert "no such command: home" in str(caught.value)
    assert caught.value.hint == INSTALL_HINT
    assert "navig plugin install navig-home" in caught.value.hint


def test_a_provider_failure_points_at_readiness_not_at_the_plugin() -> None:
    """A configured plugin whose provider is not ready has a different fix."""
    stderr = (
        "✗ the ha provider cannot control 'living-room-ac': not configured set HA_URL "
        "(long-lived token) — then any HA entity is controllable"
    )
    bridge = _bridge_with(lambda argv, _t: CommandResult(tuple(argv), 1, "", stderr))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.power("living-room-ac", True)
    assert caught.value.hint == STATUS_HINT
    assert "navig home status" in caught.value.hint


def test_a_wrapped_multiline_error_is_reflowed_not_truncated() -> None:
    """A piped Rich console hard-wraps ch.error lines; the message must survive."""
    stderr = (
        "✗ the inventory at C:\\Users\\me\\.navig\\home\\devices.ya\n"
        "ml has 1 problem(s): device #1 has no address,\n"
        "fix the file by hand, then retry"
    )
    bridge = _bridge_with(lambda argv, _t: CommandResult(tuple(argv), 1, "", stderr))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.devices()
    message = str(caught.value)
    assert "inventory at" in message
    assert "fix the file by hand" in message
    assert "\n" not in message


def test_an_unparseable_child_is_actionable() -> None:
    bridge = _bridge_with(lambda argv, _t: CommandResult(tuple(argv), 0, "not json", ""))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.devices()
    assert "no JSON" in str(caught.value)
    assert caught.value.hint == INSTALL_HINT


def test_a_json_array_is_not_a_command_payload() -> None:
    bridge = _bridge_with(lambda argv, _t: CommandResult(tuple(argv), 0, "[1, 2]", ""))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.devices()
    assert "unexpected JSON shape" in str(caught.value)


def test_an_ok_false_payload_is_an_error() -> None:
    bridge = _bridge_with(lambda argv, _t: _ok(argv, {"ok": False, "error": "inventory unreadable"}))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.devices()
    assert "inventory unreadable" in str(caught.value)


def test_a_timeout_is_actionable() -> None:
    def raising(_args, _timeout):
        raise subprocess.TimeoutExpired(["navig", "home", "list"], DEFAULT_TIMEOUT)

    with pytest.raises(HomeUnavailable) as caught:
        _bridge_with(raising).devices()
    assert "timed out" in str(caught.value)
    assert caught.value.hint == INSTALL_HINT


def test_a_missing_executable_is_actionable() -> None:
    def raising(_args, _timeout):
        raise FileNotFoundError("navig")

    with pytest.raises(HomeUnavailable) as caught:
        _bridge_with(raising).devices()
    assert "could not run" in str(caught.value)


def test_each_method_sends_the_documented_argv() -> None:
    calls: list[list[str]] = []
    timeouts: list[float] = []

    def handler(argv, timeout):
        calls.append(list(argv))
        timeouts.append(timeout)
        if argv[1] == "list":
            return _ok(argv, {"ok": True, "devices": [{"id": "lamp"}]})
        if argv[1] == "status":
            return _ok(argv, {"ok": True, "devices": [{"id": "lamp"}],
                              "providers": [{"provider": "govee", "ready": True}]})
        if argv[1] == "providers":
            return _ok(argv, {"ok": True, "providers": [{"provider": "govee", "ready": True}]})
        return _ok(argv, {"ok": True, "device": argv[2], "action": argv[1]})

    bridge = _bridge_with(handler)
    assert bridge.devices()[0]["id"] == "lamp"
    assert bridge.states()[0]["id"] == "lamp"
    assert bridge.readiness()[0]["provider"] == "govee"
    assert bridge.providers()[0]["provider"] == "govee"
    bridge.power("desk-lamp", True)
    bridge.power("desk-lamp", False)
    bridge.toggle("desk-lamp")
    bridge.set("desk-lamp", ["brightness=40", "color=#ff8800"])
    assert calls == [
        ["home", "list", "--json"],
        ["home", "status", "--json"],
        ["home", "status", "--json"],
        ["home", "providers", "--json"],
        ["home", "on", "desk-lamp", "--json"],
        ["home", "off", "desk-lamp", "--json"],
        ["home", "toggle", "desk-lamp", "--json"],
        ["home", "set", "desk-lamp", "brightness=40", "color=#ff8800", "--json"],
    ]
    assert set(timeouts) == {DEFAULT_TIMEOUT}


def test_a_custom_timeout_reaches_the_runner() -> None:
    seen: list[float] = []

    def runner(args, *, timeout=DEFAULT_TIMEOUT):
        seen.append(timeout)
        return _ok(list(args), {"ok": True, "devices": []})

    HomeBridge(runner=runner, timeout=3.5).devices()
    assert seen == [3.5]


def test_set_refuses_an_empty_assignment() -> None:
    bridge = _bridge_with(lambda argv, _t: _ok(argv, {"ok": True}))
    with pytest.raises(HomeUnavailable) as caught:
        bridge.set("desk-lamp", [])
    assert "key=value" in caught.value.hint
