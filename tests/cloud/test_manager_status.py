"""CloudManager.status must reflect the uplink in lighthouse mode.

_start_lighthouse pins state.status="online" before the WS handshake and never
reconciles it, so the .status property (which just returned state.status) reported
a phantom "online" over a dead uplink — the deck then refused to reconnect and the
boot hint showed green while the bot was unreachable. snapshot() was already honest;
now both derive status from the uplink via one shared helper.
"""

from __future__ import annotations

from navig.cloud import CloudManager


def _mgr(**kw) -> CloudManager:
    return CloudManager(
        api_key="k", broker_url="https://api.navig.run", gateway_port=8765, **kw
    )


class _StubUplink:
    def __init__(self, status: str) -> None:
        self.status = status

    def snapshot(self) -> dict:
        return {"status": self.status}


def test_status_reflects_uplink_not_pinned_online():
    m = _mgr(lighthouse_url="https://x.workers.dev")
    m.state.status = "online"  # the phantom _start_lighthouse pins before the handshake
    m._uplink = _StubUplink("error")

    assert m.status == "error"  # property — pre-fix returned "online"
    assert m.snapshot()["status"] == "error"  # snapshot stays honest


def test_status_maps_uplink_connecting_to_starting():
    m = _mgr(lighthouse_url="https://x.workers.dev")
    m.state.status = "online"
    m._uplink = _StubUplink("connecting")
    assert m.status == "starting"  # _UPLINK_STATUS_MAP: connecting -> starting


def test_status_uses_state_status_without_uplink():
    """Tunnel/direct mode (no uplink) is unchanged — state.status is the source."""
    m = _mgr(public_url="https://vps.example.com")
    m.state.status = "online"
    assert m._uplink is None
    assert m.status == "online"
