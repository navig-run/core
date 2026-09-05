"""`navig host remove` did not exist, so a host could be added and never removed.

`remove_host()` has been implemented and regression-tested for a long time
(tests/config/test_config.py pins that removing the ACTIVE host clears the pointer file,
or a re-added same-name host silently becomes active again). It simply had no CLI verb:
`host_app` shipped list/use/deploy/add/discover-local/test/show/all/servers/firewall/
status — eleven verbs, none of which removed anything — while `navig app remove` existed.
Adding a host was a one-way door.

These tests drive the verb, not the function, because the function was never the problem.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.host import host_app

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A real ConfigManager rooted in a temp dir, wired into the command module."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    # Both of these are isolation, not decoration:
    #   config_dir= — NAVIG_CONFIG_DIR moves `global_config_dir` only; `hosts_dir` comes
    #     from app-root detection, which finds THIS checkout when pytest runs in `core/`.
    #   chdir      — `ContextManager.set_active_host` writes the project-local half to
    #     `Path.cwd() / ".navig"` DIRECTLY, so no constructor argument can reach it.
    # Measured before this: running this file produced `<repo>/core/.navig/config.yaml`
    # with `active_host: box` — in the developer's tree, shared by every xdist worker.
    monkeypatch.chdir(tmp_path)
    import navig.commands.host as host_mod
    from navig.config import ConfigManager

    manager = ConfigManager(config_dir=tmp_path / "cfg")
    monkeypatch.setattr(host_mod, "config_manager", manager)
    return manager


def _add(cfg, name: str) -> None:
    cfg.save_host_config(name, {"host": "1.2.3.4", "user": "root", "port": 22})


def test_a_host_can_be_removed(cfg):
    """The whole point: add -> remove -> gone."""
    _add(cfg, "box")
    assert cfg.host_exists("box")

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"])

    assert result.exit_code == 0, result.output
    assert not cfg.host_exists("box"), "the command reported success but the host is still there"


def test_removing_an_unknown_host_is_a_usage_error(cfg):
    result = CliRunner().invoke(host_app, ["remove", "ghost", "--force"])

    assert result.exit_code == 2, result.output
    assert "not found" in " ".join(result.output.split())


def test_removing_the_active_host_clears_the_pointer(cfg):
    """The regression remove_host() was written for: get_active_host() filters out
    deleted hosts, so a post-delete check never fires and the stale pointer would make a
    re-added same-name host active without `navig host use`."""
    _add(cfg, "box")
    cfg.set_active_host("box")
    assert cfg.active_host_file.exists()

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"])

    assert result.exit_code == 0, result.output
    assert cfg.get_active_host() is None
    assert not cfg.active_host_file.exists()


def test_without_force_it_asks_before_deleting(cfg, monkeypatch):
    """It is destructive, so declining must leave the host alone AND not claim success
    of a deletion that did not happen."""
    _add(cfg, "box")

    import navig.console_helper as ch

    monkeypatch.setattr(ch, "confirm_action", lambda *a, **k: False)
    result = CliRunner().invoke(host_app, ["remove", "box"])

    assert cfg.host_exists("box"), "declining the prompt still deleted the host"
    assert "Cancelled" in result.output
    # A user declining is not a failure — exit 0 is correct here.
    assert result.exit_code == 0, result.output


def test_the_subapp_is_reachable_without_the_root_callback(cfg):
    """Anti-vacuity for the ctx.ensure_object fix: nine host subcommands write into
    ctx.obj, and without the guarantee they die with "'NoneType' object does not support
    item assignment" — a crash is also non-zero, which would make the usage-error test
    above pass for entirely the wrong reason."""
    _add(cfg, "box")

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"])

    assert "NoneType" not in result.output, result.output
    assert result.exit_code == 0
