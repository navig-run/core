"""`navig doctor` must not report a green public path it never verified.

Two rows, one measured outage. With `gateway: false` in
`~/.navig/daemon/config.json`, every `navig service` boot started the Telegram
worker and never a gateway, so there was no Lighthouse uplink: the edge answered
`503 {"offline":true}` for every inbound webhook for two days (no `Registered
gateway` in `daemon.log` between 2026-10-18 13:04 and 2026-10-20) while every
light stayed green.

* The Gateway row accepted ANY responder on the port — and with no gateway the
  telegram worker holds that port and answers, so "Responding at …" was a phantom
  green that hid the very absence it claimed to check.
* Nothing asked the daemon whether the uplink itself was up. `check_uplink()` is
  that question, and could-not-verify must stay a warning, never a tick.
"""

from __future__ import annotations

import sys

import pytest

from navig.commands import doctor

GATEWAY_OK = "C:\\navig\\venv\\Scripts\\pythonw.exe -m navig gateway start --port 8789"
SQUATTER = "C:\\navig\\venv\\Scripts\\pythonw.exe -m navig.daemon.telegram_worker"


# ── the Gateway row: identity, not just an answer ────────────────────────────


def test_the_real_gateway_stays_green(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)
    monkeypatch.setattr(doctor, "_gateway_owner", lambda _port: GATEWAY_OK)
    row = doctor.check_gateway(port=8789)[0]
    assert row[1] is True
    assert "gateway up" in row.detail


def test_the_telegram_worker_squatting_the_port_is_not_a_green_gateway(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)
    monkeypatch.setattr(doctor, "_gateway_owner", lambda _port: SQUATTER)
    row = doctor.check_gateway(port=8789)[0]
    assert row[1] is False, "an answer from a squatter was reported as a healthy gateway"
    assert row[0] == doctor._WARN, "an answer-but-no-gateway is a warning, not a hard error"
    assert "telegram_worker" in row.detail, "the row must name what actually holds the port"
    assert "no gateway" in row.detail
    assert "navig service start" in row.detail


def test_an_unknown_owner_claims_reachability_only(monkeypatch) -> None:
    """No psutil / no permission → we only verified that the port answers, so the row
    must claim exactly that and nothing more (it must never say "gateway up")."""
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)
    monkeypatch.setattr(doctor, "_gateway_owner", lambda _port: None)
    row = doctor.check_gateway(port=8789)[0]
    assert row[1] is True
    assert "Responding at 127.0.0.1:8789" in row.detail
    assert "gateway up" not in row.detail


def test_a_down_gateway_still_reports_the_start_command(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: False)
    row = doctor.check_gateway(port=8789)[0]
    assert row[1] is False and "navig service start" in row.detail


def test_the_owner_probe_degrades_to_unknown_instead_of_raising(monkeypatch) -> None:
    """An unreadable process must not fail the whole report."""

    class _Boom:
        CONN_LISTEN = 0

        @staticmethod
        def net_connections(kind: str = "tcp"):
            raise PermissionError("access denied")

    monkeypatch.setitem(sys.modules, "psutil", _Boom())
    assert doctor._gateway_owner(8789) is None


# ── the Uplink row: ask the daemon, not the silence ──────────────────────────


@pytest.fixture
def uplink(monkeypatch):
    """Drive `check_uplink()` from a synthetic config + a synthetic status endpoint."""
    state = {
        "reachable": True,
        "mode": "lighthouse",
        "enabled": True,
        "payload": {"status": "online", "lighthouse": {"status": "online"}},
        "http": 200,
        "raises": None,
    }

    class _CM:
        def __init__(self, *a, **k):
            pass

        @property
        def global_config(self):
            return {"cloud": {"enabled": state["enabled"], "mode": state["mode"]}}

    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    monkeypatch.setattr("navig.config.ConfigManager", _CM)
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: state["reachable"])

    def _get(*_a, **_k):
        if state["raises"]:
            raise state["raises"]
        return _Resp(state["http"], state["payload"])

    monkeypatch.setattr("requests.get", _get)
    return state


def test_an_online_uplink_is_green(uplink) -> None:
    rows = doctor.check_uplink(port=8789)
    assert len(rows) == 1
    assert rows[0][1] is True and rows[0].label == "Uplink"
    assert "online" in rows[0].detail


def test_an_offline_uplink_is_a_warning_that_names_the_consequence(uplink) -> None:
    uplink["payload"] = {"status": "offline", "lighthouse": {"status": "offline"}}
    row = doctor.check_uplink(port=8789)[0]
    assert row[1] is False and row[0] == doctor._WARN
    assert "503" in row.detail and "navig service restart" in row.detail


def test_the_uplinks_own_status_wins_over_the_managers(uplink) -> None:
    """The manager can read `online` while its Lighthouse child is not connected."""
    uplink["payload"] = {"status": "online", "lighthouse": {"status": "offline"}}
    row = doctor.check_uplink(port=8789)[0]
    assert row[1] is False, "the uplink child was offline and the row still ticked"


def test_no_row_when_there_is_no_public_path_to_report_on(uplink) -> None:
    uplink["mode"] = "direct"
    assert doctor.check_uplink(port=8789) == []
    uplink["mode"] = "lighthouse"
    uplink["enabled"] = False
    assert doctor.check_uplink(port=8789) == []


def test_gateway_down_degrades_to_not_checked(uplink) -> None:
    uplink["reachable"] = False
    row = doctor.check_uplink(port=8789)[0]
    assert row[1] is False and row[0] == doctor._WARN
    assert "not checked" in row.detail


def test_a_status_endpoint_that_fails_never_ticks(uplink) -> None:
    uplink["http"] = 500
    row = doctor.check_uplink(port=8789)[0]
    assert row[1] is False and "COULD NOT VERIFY" in row.detail

    uplink["http"] = 200
    uplink["payload"] = ValueError("not json")
    row = doctor.check_uplink(port=8789)[0]
    assert row[1] is False and "COULD NOT VERIFY" in row.detail

    uplink["payload"] = {"status": "online"}
    uplink["raises"] = ConnectionError("refused")
    row = doctor.check_uplink(port=8789)[0]
    assert row[1] is False and "COULD NOT VERIFY" in row.detail


def test_the_uplink_row_is_wired_into_the_gateway_section(uplink) -> None:
    """A check nothing calls is documentation."""
    sections = dict(doctor._collect_sections(port=8789))
    assert any(r.label == "Uplink" for r in sections["Gateway"])
