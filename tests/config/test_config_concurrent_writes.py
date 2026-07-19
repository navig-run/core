"""Two processes, one config.yaml — nobody's settings may vanish.

`global_config` is cached for the life of the process. That is harmless for a
one-shot CLI run, but the **daemon lives for days**: every `navig config set` the
user runs in the meantime is invisible to it, and the ~15 read-modify-write call
sites across core and the plugins would save that stale snapshot back over the
file — silently erasing everything the CLI had written.

Reproduced before the fix: a daemon-side write of `plugins.games.steam_watch`
deleted a CLI-written `telegram.catalog.enabled`.
"""

import yaml

from navig.config import ConfigManager


def _read(cfg_dir) -> dict:
    return yaml.safe_load((cfg_dir / "config.yaml").read_text(encoding="utf-8")) or {}


def _cfg_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_a_stale_daemon_save_no_longer_erases_a_cli_write(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)

    daemon = ConfigManager()
    daemon.global_config  # boots and caches the snapshot  # noqa: B018

    # A CLI subprocess (its own fresh manager) writes a setting.
    cli = ConfigManager()
    cli.global_config["telegram"] = {"catalog": {"enabled": False}}
    cli._save_global_config(cli.global_config)
    assert "telegram" in _read(d)

    # Much later the daemon writes something entirely unrelated, from its snapshot.
    node = daemon.global_config
    node.setdefault("plugins", {}).setdefault("games", {})["steam_watch"] = [730]
    daemon._save_global_config(daemon.global_config)

    after = _read(d)
    assert after["telegram"]["catalog"]["enabled"] is False  # survived
    assert after["plugins"]["games"]["steam_watch"] == [730]  # and ours landed


def test_the_merge_is_deep_not_shallow(monkeypatch, tmp_path):
    """Two processes writing *different leaves of the same subtree* both survive."""
    d = _cfg_dir(monkeypatch, tmp_path)
    ConfigManager().set_global("adapters.telegram.enabled", True)

    daemon = ConfigManager()
    daemon.global_config  # noqa: B018 — snapshot taken

    ConfigManager().set_global("adapters.discord.enabled", True)  # CLI, meanwhile

    node = daemon.global_config
    node.setdefault("adapters", {}).setdefault("telegram", {})["token_set"] = True
    daemon._save_global_config(daemon.global_config)

    adapters = _read(d)["adapters"]
    assert adapters["discord"]["enabled"] is True  # the other process's subtree
    assert adapters["telegram"] == {"enabled": True, "token_set": True}  # merged leaf


def test_set_global_reads_current_state_first(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)

    daemon = ConfigManager()
    daemon.global_config  # noqa: B018 — stale from here on

    ConfigManager().set_global("log_level", "DEBUG")  # written by a CLI run
    daemon.set_global("plugins.games.country", "DE")  # daemon writes via the safe path

    after = _read(d)
    assert after["log_level"] == "DEBUG"
    assert after["plugins"]["games"]["country"] == "DE"


def test_set_global_can_still_overwrite_its_own_key(monkeypatch, tmp_path):
    """The merge must not turn a legitimate overwrite into a no-op."""
    d = _cfg_dir(monkeypatch, tmp_path)
    cm = ConfigManager()
    cm.set_global("plugins.games.country", "US")
    cm.set_global("plugins.games.country", "DE")
    assert _read(d)["plugins"]["games"]["country"] == "DE"


def test_refresh_picks_up_an_external_change(monkeypatch, tmp_path):
    _cfg_dir(monkeypatch, tmp_path)
    daemon = ConfigManager()
    assert daemon.global_config.get("log_level") != "DEBUG"

    ConfigManager().set_global("log_level", "DEBUG")  # another process

    assert daemon.global_config.get("log_level") != "DEBUG"  # still cached — by design
    assert daemon.refresh_global_config().get("log_level") == "DEBUG"


def test_a_first_save_with_no_file_yet_is_not_merged(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)
    cm = ConfigManager()
    cm.global_config["log_level"] = "INFO"
    cm._save_global_config(cm.global_config)
    assert _read(d)["log_level"] == "INFO"
