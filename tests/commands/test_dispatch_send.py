"""Characterization tests for `navig dispatch send` (commands.dispatch.dispatch_send).

The CLI had no test coverage; these lock its observable behaviour (JSON payload,
success path, and the exit codes for a failed receipt / no route / unavailable
adapter) so the refactor onto the shared `route_and_send` seam is behaviour-
preserving.
"""

from __future__ import annotations

import pytest
import typer

import navig.commands.dispatch as dispatch_mod
import navig.console_helper as ch_mod
import navig.messaging.send as send_mod


class _Decision:
    adapter_name = "sms"


class _Status:
    value = "sent"


class _Receipt:
    def __init__(self, *, ok=True, message_id="mid-1", error=None):
        self.ok = ok
        self.message_id = message_id
        self.error = error
        self.status = _Status()


def _patch_send(monkeypatch, *, result=None, exc=None):
    async def _fake(target, message, *, network=None):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(send_mod, "route_and_send", _fake)


def test_success_path_does_not_exit(monkeypatch):
    _patch_send(monkeypatch, result=(_Decision(), _Receipt(ok=True)))
    # a successful send prints and returns without raising typer.Exit
    dispatch_mod.dispatch_send(target="@a", message="hi", network=None, json_output=False)


def test_json_output_emits_expected_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(ch_mod, "emit_json", lambda payload, **kw: captured.update(payload))
    _patch_send(monkeypatch, result=(_Decision(), _Receipt(ok=True, message_id="mid-9")))

    dispatch_mod.dispatch_send(target="@a", message="hi", network=None, json_output=True)

    assert captured == {
        "ok": True,
        "status": "sent",
        "message_id": "mid-9",
        "error": None,
        "adapter": "sms",
    }


def test_failed_receipt_exits_nonzero(monkeypatch):
    _patch_send(monkeypatch, result=(_Decision(), _Receipt(ok=False, error="boom")))
    with pytest.raises(typer.Exit):
        dispatch_mod.dispatch_send(target="@a", message="hi", network=None, json_output=False)


def test_no_route_exits_nonzero(monkeypatch):
    from navig.messaging.routing import NoRouteError

    _patch_send(monkeypatch, exc=NoRouteError("no route"))
    with pytest.raises(typer.Exit):
        dispatch_mod.dispatch_send(target="@ghost", message="hi", network=None, json_output=False)


def test_unavailable_adapter_exits_nonzero(monkeypatch):
    from navig.messaging.send import AdapterUnavailableError

    _patch_send(monkeypatch, exc=AdapterUnavailableError("sms"))
    with pytest.raises(typer.Exit):
        dispatch_mod.dispatch_send(target="@a", message="hi", network=None, json_output=False)
