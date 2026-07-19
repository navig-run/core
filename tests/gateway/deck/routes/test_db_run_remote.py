"""Regression: the deck DB console's SSH runner constructs ServerDiscovery correctly.

`_run_remote` used to call `ServerDiscovery(cfg, host_name=host)` — a ConfigManager plus a kwarg
the constructor doesn't accept (it wants the host's ssh_config DICT) — so every /db/list ·
/db/tables · /db/query raised and returned an error. It now mirrors remote._ssh: gate on
host_exists, load the host's SSH config, construct ServerDiscovery(ssh_config), and run off-loop.
"""

from __future__ import annotations

import json as _json

import pytest

pytestmark = pytest.mark.integration


class _FakeDisco:
    """Constructed with the ssh_config DICT (the correct contract), not a ConfigManager."""

    def __init__(self, ssh_config, debug_logger=None):
        assert isinstance(ssh_config, dict), "ServerDiscovery must receive an ssh_config dict"
        self.ssh_config = ssh_config

    def _execute_ssh(self, command):
        return (True, f"ran: {command}", "")


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """A real ConfigManager isolated to a tmp config dir (no global patching side effects)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    from navig.config import get_config_manager, reset_config_manager

    reset_config_manager()
    yield get_config_manager()
    reset_config_manager()


async def test_run_remote_rejects_unconfigured_host(isolated_cfg):
    from navig.gateway.deck.routes.database import _run_remote

    ok, out, err = await _run_remote("nope", "SELECT 1")
    assert ok is False
    assert "not configured" in err
    assert "host_name" not in err  # NOT the old TypeError from the bad constructor call


async def test_run_remote_happy_path(isolated_cfg, monkeypatch):
    import navig.discovery as disc

    monkeypatch.setattr(disc, "ServerDiscovery", _FakeDisco)
    isolated_cfg.save_host_config(
        "myhost", {"name": "myhost", "host": "h.example.com", "port": 22, "user": "u"}
    )
    from navig.gateway.deck.routes.database import _run_remote

    ok, out, err = await _run_remote("myhost", "SELECT 1")
    assert ok is True
    assert out == "ran: SELECT 1"
    assert err == ""


async def test_db_query_endpoint_runs_through_fixed_runner(isolated_cfg, monkeypatch):
    """End-to-end through handle_deck_db_query: the fixed runner is actually reached."""
    import navig.discovery as disc

    monkeypatch.setattr(disc, "ServerDiscovery", _FakeDisco)
    isolated_cfg.save_host_config(
        "myhost", {"name": "myhost", "host": "h.example.com", "port": 22, "user": "u"}
    )
    import navig.gateway.deck.routes.database as db

    class _Req:
        async def json(self):
            return {"host": "myhost", "db_type": "mysql", "user": "root", "query": "SELECT 1"}

    resp = await db.handle_deck_db_query(_Req())
    assert resp.status == 200
    payload = _json.loads(resp.body.decode())
    assert payload["ok"] is True
    assert payload["data"]["ok"] is True
    assert "SELECT 1" in payload["data"]["stdout"]  # the mysql -e '<query>' reached the runner
