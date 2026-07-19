"""The one shared deck-routes SSH runner: `_utils.run_on_host`.

Consolidates `remote._ssh` and `database._run_remote`, which were byte-for-byte duplicates — and
that duplication is exactly what let the DB console ship broken (its copy called
`ServerDiscovery(cfg, host_name=host)`) while `remote` worked (#426). Both are now thin wrappers,
so a fix (or a bug) lands in both. These tests pin the runner's contract and prove both wrappers
delegate to it, forwarding their own default timeout (remote 30s · db 45s).
"""

from __future__ import annotations

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


async def test_run_on_host_rejects_unconfigured_host(isolated_cfg):
    from navig.gateway.deck.routes._utils import run_on_host

    ok, out, err = await run_on_host("nope", "SELECT 1")
    assert ok is False
    assert out == ""
    assert "not configured" in err
    assert "host_name" not in err  # never the old TypeError from the bad constructor call


async def test_run_on_host_happy_path(isolated_cfg, monkeypatch):
    import navig.discovery as disc

    monkeypatch.setattr(disc, "ServerDiscovery", _FakeDisco)
    isolated_cfg.save_host_config(
        "myhost", {"name": "myhost", "host": "h.example.com", "port": 22, "user": "u"}
    )
    from navig.gateway.deck.routes._utils import run_on_host

    ok, out, err = await run_on_host("myhost", "whoami")
    assert ok is True
    assert out == "ran: whoami"
    assert err == ""


async def test_run_on_host_default_timeout(isolated_cfg):
    """The default timeout is 30s (the value remote._ssh historically forwarded)."""
    import inspect

    from navig.gateway.deck.routes._utils import run_on_host

    assert inspect.signature(run_on_host).parameters["timeout"].default == 30.0


async def test_both_wrappers_delegate_and_forward_timeout(monkeypatch):
    """remote._ssh and database._run_remote both call run_on_host, forwarding their own default."""
    import navig.gateway.deck.routes.database as db
    import navig.gateway.deck.routes.remote as remote

    calls: list[tuple[str, str, float]] = []

    async def _spy(host, command, timeout):
        calls.append((host, command, timeout))
        return (True, "spied", "")

    # Each wrapper imported run_on_host into its OWN module namespace — patch there.
    monkeypatch.setattr(remote, "run_on_host", _spy)
    monkeypatch.setattr(db, "run_on_host", _spy)

    assert await remote._ssh("h1", "cmd-a") == (True, "spied", "")
    assert await db._run_remote("h2", "cmd-b") == (True, "spied", "")
    # explicit timeout is forwarded verbatim
    assert await remote._ssh("h3", "cmd-c", timeout=7.5) == (True, "spied", "")

    assert calls == [
        ("h1", "cmd-a", 30.0),  # remote default
        ("h2", "cmd-b", 45.0),  # db default (DB clients can be slow to connect)
        ("h3", "cmd-c", 7.5),   # explicit
    ]
