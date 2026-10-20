"""The deck key must never reach the log file, and relayed traffic is never "local".

deck.api_key authenticates the whole public deck and names the Lighthouse tenant. The
gateway used to log it IN FULL on every mint, on a failed persist, and (old and new) on
a weak-key upgrade, so anyone who could read the debug log held the deck.

Separately, Lighthouse replays remote requests into the gateway from 127.0.0.1. They
were refused the local-desktop bypass only because Cloudflare happened to add CF-Ray on
the way through; the uplink now stamps them explicitly and the auth check refuses the
stamp first.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from navig.cloud import deck_key
from navig.cloud import uplink as uplink_mod
from navig.gateway.deck import auth as deck_auth


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


class TestMask:
    def test_keeps_enough_to_tell_keys_apart_and_never_the_key(self) -> None:
        key = "navig_" + "x" * 30 + "TAIL"
        masked = deck_key.mask(key)
        assert key not in masked
        assert masked.startswith("navig_xx") and masked.endswith("TAIL")
        assert len(masked) < len(key) // 2

    def test_short_and_empty_values_reveal_nothing(self) -> None:
        assert deck_key.mask("") == "<unset>"
        assert deck_key.mask("short-key") == "………"


def test_a_fresh_install_does_not_log_the_minted_key(isolated_config, log_messages) -> None:
    from navig.gateway.deck import register_deck_routes

    register_deck_routes(web.Application(), bot_token="")

    minted = deck_auth._deck_config["api_key"]
    assert minted, "the fresh-install path must mint a key for this test to mean anything"
    assert any("Deck API key" in m for m in log_messages), (
        "the mint log line must still be emitted (masked) — otherwise this test is vacuous"
    )
    leaked = [m for m in log_messages if minted in m]
    assert leaked == [], f"deck.api_key reached the log: {leaked}"


def test_the_relay_header_names_agree() -> None:
    # Duplicated rather than imported so the uplink does not load the gateway's auth
    # module at import time; this is what keeps the two from drifting apart.
    assert uplink_mod.RELAY_VIA_HEADER == deck_auth.RELAY_VIA_HEADER


def test_a_relayed_request_is_never_local_even_from_loopback() -> None:
    plain = make_mocked_request("GET", "/api/deck/status", headers={})
    relayed = make_mocked_request(
        "GET", "/api/deck/status", headers={deck_auth.RELAY_VIA_HEADER: "lighthouse"}
    )
    for req in (plain, relayed):
        # make_mocked_request has no peer; give both the same loopback address.
        req._transport_peername = ("127.0.0.1", 50000)  # type: ignore[attr-defined]
    assert deck_auth.is_local_request(plain) is True
    assert deck_auth.is_local_request(relayed) is False


async def test_the_uplink_stamps_the_header_over_any_inbound_value(monkeypatch) -> None:
    client = uplink_mod.UplinkClient(
        lighthouse_url="https://edge.example.workers.dev", api_key="k" * 32, gateway_port=1
    )
    sent: dict = {}

    class _Resp:
        status = 200
        headers: dict = {}

        async def text(self) -> str:
            return "{}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _Session:
        def request(self, method, url, headers=None, **_kw):
            sent["headers"] = headers
            return _Resp()

    async def _get_session():
        return _Session()

    monkeypatch.setattr(client, "_get_session", _get_session)
    frame = {
        "method": "GET",
        # A caller trying to pass itself off as "not relayed", in a different case.
        "headers": {"x-navig-via": "", "X-Navig-Via": "nothing-to-see", "Accept": "*/*"},
        "body": "",
    }
    await client._dispatch_loopback(frame, "/api/deck/status")

    via = {k: v for k, v in sent["headers"].items() if k.lower() == "x-navig-via"}
    assert via == {"X-Navig-Via": "lighthouse"}
    assert sent["headers"]["Accept"] == "*/*"
