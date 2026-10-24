"""`navig start --gateway` must not answer the whole LAN by default.

The gateway's ``/health`` is anonymous, so the bind address is a security decision,
not cosmetics: a laptop on café Wi-Fi bound to 0.0.0.0 answers every device on the
network. Remote access belongs to the Lighthouse uplink. An operator who really wants
a wider bind sets ``gateway.host`` (mesh nodes do exactly that) — configuration is
respected; the hard-coded default is what must stay loopback.
"""

from __future__ import annotations

import pytest

from navig.commands import start as start_cmd


@pytest.fixture
def gateway_calls(monkeypatch):
    calls: list[dict] = []

    def fake_gateway_start(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("navig.commands.gateway.gateway_start", fake_gateway_start)
    return calls


def _defaults(monkeypatch, host: str, port: int = 8789) -> None:
    monkeypatch.setattr(
        "navig.commands.gateway._load_gateway_cli_defaults", lambda: (port, host)
    )


def test_quick_start_binds_loopback_by_default(monkeypatch, gateway_calls):
    _defaults(monkeypatch, "127.0.0.1")

    start_cmd.run_quick_start(bot=False, gateway=True, background=True)

    assert gateway_calls, "the gateway was never started"
    assert gateway_calls[0]["host"] == "127.0.0.1"
    assert gateway_calls[0]["port"] == 8789


def test_quick_start_honours_an_explicitly_configured_host(monkeypatch, gateway_calls):
    # A mesh node binds wider on purpose; its config says so and must win.
    _defaults(monkeypatch, "0.0.0.0")

    start_cmd.run_quick_start(bot=False, gateway=True, background=True)

    assert gateway_calls[0]["host"] == "0.0.0.0"


def test_an_explicit_port_still_resolves_the_configured_host(monkeypatch, gateway_calls):
    _defaults(monkeypatch, "127.0.0.1", port=9000)

    start_cmd.run_quick_start(bot=False, gateway=True, port=9001, background=True)

    assert gateway_calls[0]["port"] == 9001
    assert gateway_calls[0]["host"] == "127.0.0.1"
