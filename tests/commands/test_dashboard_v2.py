"""The rebuilt dashboard reads the same sources as the CLI commands it summarises.

The first dashboard disagreed with ``navig service status`` / ``navig status`` in ten
places: a recycled pid read "running", the Scheduler row said "disabled" while cron ran
in the gateway, the SSH-pool row was permanently empty (it read its own process's
pool), any listener on the gateway port counted as the gateway, hosts were ICMP-pinged
(blocked on most servers; ``-W`` is milliseconds on macOS), and pending/partial/
interrupted operations all rendered as "○". Each test below pins one of those.
"""

from __future__ import annotations

import io
import socket
import sys

import pytest
from rich.console import Console

from navig.commands import dashboard
from navig.identity.entity import derive_entity
from navig.operation_recorder import OperationStatus

SEED = "ab12" * 16


def _render_text(renderable, width: int = 120, height: int | None = None) -> str:
    buf = io.StringIO()
    Console(file=buf, width=width, height=height, force_terminal=False, color_system=None).print(
        renderable
    )
    return buf.getvalue()


# ── daemon: identity-checked pid, not the raw pidfile integer ─────────────────


def test_a_recycled_pid_reads_as_stopped(monkeypatch) -> None:
    from navig.daemon import supervisor

    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: None))
    monkeypatch.setattr(
        supervisor.NavigDaemon,
        "read_state",
        staticmethod(lambda: {"children": [{"name": "telegram-bot", "pid": 4242, "alive": True}]}),
    )
    info = dashboard.collect_daemon()
    assert info["running"] is False
    assert info["children"] == {}


def test_a_stale_heartbeat_is_flagged(monkeypatch) -> None:
    from navig.daemon import supervisor

    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 1234))
    monkeypatch.setattr(
        supervisor.NavigDaemon,
        "read_state",
        staticmethod(
            lambda: {"started_at": "2026-09-27T10:00:00+00:00", "heartbeat_s": 10, "children": []}
        ),
    )
    monkeypatch.setattr(
        supervisor.NavigDaemon, "last_seen_alive", staticmethod(lambda: "2020-01-01T00:00:00+00:00")
    )
    info = dashboard.collect_daemon()
    assert info["running"] is True and info["heartbeat_stale"] is True
    assert "daemon heartbeat stale" in dashboard.attention_items({"daemon": info}, {})


# ── gateway + cron: from /status, not a port probe or the daemon's scheduler child ──


def test_cron_is_read_from_the_gateway_not_the_disabled_scheduler_child() -> None:
    data = {
        "daemon": {"running": True, "pid": 1, "children": {"scheduler": {"enabled": False}}},
        "gateway": {
            "running": True,
            "port": 8789,
            "uptime": 60,
            "sessions": 2,
            "cron": {"jobs": 3, "enabled_jobs": 2},
        },
    }
    out = _render_text(dashboard.make_services_panel(data, {}))
    assert "2/3 job(s) enabled" in out
    assert "disabled" not in out.split("Cron")[1].splitlines()[0]


def test_collect_gateway_uses_the_status_endpoint(monkeypatch) -> None:
    from navig.commands import status

    monkeypatch.setattr(status, "get_gateway_status", lambda: {"running": False})
    assert dashboard.collect_gateway()["running"] is False


# ── hosts: TCP to the SSH port ────────────────────────────────────────────────


class _FakeConfig:
    def __init__(self, hosts: dict[str, dict]):
        self._hosts = hosts

    def list_hosts(self):
        return list(self._hosts)

    def load_host_config(self, name):
        return self._hosts[name]

    def get_active_host(self):
        return None


def test_hosts_are_probed_on_their_ssh_port() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    open_port = server.getsockname()[1]
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    try:
        cfg = _FakeConfig(
            {
                "up": {"host": "127.0.0.1", "port": open_port},
                "down": {"host": "127.0.0.1", "port": closed_port},
            }
        )
        res = dashboard.collect_hosts(cfg)
    finally:
        server.close()
    assert res["up"]["status"] == "up" and res["up"]["port"] == open_port
    assert res["down"]["status"] == "down"
    assert "down unreachable" in dashboard.attention_items({"hosts": res}, {})


# ── activity: every OperationStatus has its own glyph ─────────────────────────


def test_every_operation_status_has_a_glyph() -> None:
    missing = [s.value for s in OperationStatus if s.value not in dashboard.OP_STATUS_GLYPH]
    assert not missing, missing


# ── identity: the whoami sigil, deterministic, never written ──────────────────


def test_the_sigil_text_is_deterministic_and_sized() -> None:
    from navig.identity.renderer import sigil_text

    ent = derive_entity(SEED)
    full = sigil_text(ent, indent="").plain.splitlines()
    assert len(full) == len(ent.sigil_matrix) == 9
    assert sigil_text(derive_entity(SEED), indent="").plain == sigil_text(ent, indent="").plain
    assert len(sigil_text(ent, compact=True).plain.splitlines()) == 5
    assert len(sigil_text(ent, reveal=3).plain.splitlines()) == 3


def test_every_generated_glyph_gets_a_depth_style() -> None:
    from navig.identity.entity import _SIGIL_GLYPHS as SIGIL_GLYPHS
    from navig.identity.renderer import _glyph_style

    unstyled = [g for g in set(SIGIL_GLYPHS) if g.strip() and not _glyph_style(g, "#fff", "#000")]
    assert not unstyled, unstyled


def test_collect_identity_never_writes_an_entity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    info = dashboard.collect_identity()
    assert info["saved"] is False and info["entity"] is not None
    assert not any(tmp_path.rglob("entity.json"))


# ── the whole screen renders at every supported size ──────────────────────────


def _full_data() -> dict:
    return {
        "identity": {"entity": derive_entity(SEED), "saved": True, "version": "9.9.9"},
        "daemon": {
            "running": True,
            "pid": 42,
            "uptime_s": 3700,
            "children": {"telegram-bot": {"pid": 43, "live": True, "enabled": True, "restarts": 0}},
        },
        "gateway": {"running": True, "port": 8789, "uptime": 120, "sessions": 1, "cron": {}},
        "safety": {
            "ledger": {"status": "intact", "total": 12},
            "approvals": 1,
            "locks": [],
            "inflight": 0,
        },
        "space": {"name": "blog", "pct": 40.0},
        "hosts": {
            "prod-01": {"status": "up", "latency_ms": 12.0, "address": "10.0.0.1", "port": 22}
        },
        "activity": [
            {
                "ts": "2026-09-27T10:00:00+00:00",
                "command": "navig run uptime",
                "host": "prod-01",
                "status": s.value,
            }
            for s in OperationStatus
        ],
        "reach_ai": {"reach": {"mode": "local-only", "reach_url": "http://127.0.0.1:8789"}},
        "store": {"total": 5, "broken": 0, "degraded": 0},
    }


@pytest.mark.parametrize("cols,rows", [(60, 30), (80, 24), (100, 30), (120, 40), (160, 50)])
def test_the_dashboard_renders_at_every_size(cols: int, rows: int) -> None:
    layout = dashboard.render(
        _full_data(), {}, dashboard.DashboardState(), _FakeConfig({}), 5, cols, rows
    )
    out = _render_text(layout, width=cols, height=rows)
    assert "NODE-AB12" in out
    assert "1 need attention" in out  # the waiting approval
    if cols >= dashboard.WIDE_COLS:
        assert "N  O  D  E" in out  # the identity panel's spaced node name


def test_a_failing_source_is_reported_not_fatal() -> None:
    def boom():
        raise RuntimeError("nope")

    c = dashboard.Collector({"ok": (60, lambda: 1), "bad": (60, boom)})
    c.run_once()
    data, errors = c.snapshot()
    assert data == {"ok": 1}
    assert "RuntimeError" in errors["bad"]
    assert "bad: read failed" in dashboard.attention_items(data, errors)


def test_the_doctor_overlay_lists_the_problems() -> None:
    data = {
        "doctor": {
            "summary": {"passed": 3, "warnings": 1, "failed": 0},
            "problems": [
                {"section": "Config", "label": "Vault", "warn": True, "detail": "not armed"}
            ],
        }
    }
    out = _render_text(dashboard.make_overlay("doctor", data, {}))
    assert "3 passed" in out and "Vault" in out


def test_keys_are_disabled_without_a_tty(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    reader = dashboard.KeyReader()
    assert reader.enabled is False
    reader.start()  # must be a no-op, not a thread dying on termios
    reader.stop()


# ── `navig start` is the quick launcher again ─────────────────────────────────


def test_navig_start_is_not_hijacked_by_the_dashboard(monkeypatch) -> None:
    from navig import main

    called = []
    monkeypatch.setattr(main, "_handle_start_command", lambda args: called.append(args) or True)
    assert main._maybe_handle_fast_path(["navig", "start"]) is False
    assert main._maybe_handle_fast_path(["navig", "start", "--no-bot"]) is False
    assert called == []
    # The old dashboard flags still reach the dashboard.
    assert main._maybe_handle_fast_path(["navig", "start", "--simple"]) is True
    assert called == [["--simple"]]


@pytest.mark.parametrize("cols", [80, 120])
@pytest.mark.parametrize("overlay", ["help", "doctor"])
def test_an_overlay_replaces_the_grid_instead_of_showing_placeholders(
    cols: int, overlay: str
) -> None:
    """First recording: pressing `d` showed Rich's `Layout(name='services')` boxes."""
    state = dashboard.DashboardState()
    state.overlay = overlay
    layout = dashboard.render(_full_data(), {}, state, _FakeConfig({}), 5, cols, 34)
    out = _render_text(layout, width=cols, height=34)
    assert "Layout(name=" not in out
    assert ("Help" if overlay == "help" else "Doctor") in out
