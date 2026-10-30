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


def test_dry_run_removes_nothing(cfg):
    """Review #789: the root `--dry-run` flag reached remove_host and was ignored —
    the "show what would happen" switch deleted the host."""
    _add(cfg, "box")

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"], obj={"dry_run": True})

    assert result.exit_code == 0, result.output
    assert cfg.host_exists("box"), "--dry-run deleted the host"
    assert "DRY RUN" in result.output


def test_removing_clears_the_project_local_selector(cfg, tmp_path):
    """Review #789: only the cache file was cleared, so a project-local
    `active_host` survived and re-activated a re-added same-name host."""
    _add(cfg, "box")
    (tmp_path / ".navig").mkdir()
    cfg.set_local_config({"active_host": "box", "keep": 1}, tmp_path)

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"])

    assert result.exit_code == 0, result.output
    local = cfg.get_local_config(tmp_path)
    assert "active_host" not in local and local.get("keep") == 1
    _add(cfg, "box")
    assert cfg.get_active_host() is None, "re-adding the host silently made it active again"


def test_removing_clears_legacy_global_and_default_selectors(cfg, tmp_path):
    _add(cfg, "box")
    (tmp_path / ".navig").write_text("box:site", encoding="utf-8")
    cfg.set_global("active_host", "box")
    cfg.set_global("default_host", "box")

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".navig").exists()
    assert not cfg.refresh_global_config().get("active_host")
    assert not cfg.global_config.get("default_host")
    _add(cfg, "box")
    assert cfg.get_active_host() is None


def test_selectors_naming_another_host_are_left_alone(cfg, tmp_path):
    _add(cfg, "box")
    _add(cfg, "other")
    cfg.set_global("default_host", "other")
    cfg.set_active_host("other")

    result = CliRunner().invoke(host_app, ["remove", "box", "--force"])

    assert result.exit_code == 0, result.output
    assert cfg.get_active_host() == "other"
    assert cfg.global_config.get("default_host") == "other"
