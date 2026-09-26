"""`ConfigSingleton.set()` must survive the next refresh, and `save()` must not erase
what another process wrote — the two halves of one lost-update defect.

`set(scope="global")` wrote into the in-memory dict and nothing else. Two things then
went wrong, both measured against an isolated config dir before the fix:

1. **A read discarded the unsaved write.** `get()` refreshes the global copy whenever
   the file's mtime — or the config dir itself — differs from what it cached, by
   REPLACING `_global_data`. So `set("modules.overrides", {"finance": "false"})` followed
   by `get("modules.overrides")` returned `None` if anything had moved in between. That is
   why `tests/modules/test_registry.py::test_registry_string_override_disables` was
   order-dependent: green alone, red on whichever xdist worker had bounced
   `NAVIG_CONFIG_DIR` through an earlier test.

2. **A save clobbered a concurrent writer.** `save()` wrote the copy this process had
   loaded — in a daemon, possibly days ago. `navig config set telegram.y 2` from the CLI,
   then the deck toggling a module (`ModuleRegistry.set_enabled` = set + save), left the
   file with the override and WITHOUT `telegram.y`. The project-scope branch of `set()`
   refreshed first; the global branch never did.

Now `set()` records the write in a pending ledger on top of a fresh base, a refresh
re-applies the ledger over the new copy, and `save()` is refresh → re-apply → write.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from navig.core.shared_config import ConfigSingleton


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> Iterator[tuple[ConfigSingleton, Path]]:
    home = tmp_path / "a"
    home.mkdir()
    (home / "config.yaml").write_text("telegram:\n  x: 1\n", encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(home))
    ConfigSingleton._instance = None
    try:
        c = ConfigSingleton()
        assert c.get("telegram.x") == 1
        yield c, home / "config.yaml"
    finally:
        ConfigSingleton._instance = None


def _bump_mtime(path: Path, text: str) -> None:
    """Write *text* with an mtime the refresh guard cannot mistake for the cached one."""
    time.sleep(0.02)
    path.write_text(text, encoding="utf-8")


def test_an_unsaved_set_survives_a_config_dir_bounce(cfg, tmp_path: Path, monkeypatch) -> None:
    c, _ = cfg
    elsewhere = tmp_path / "b"
    elsewhere.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(elsewhere))  # an earlier test's isolation…
    c.get("telegram.x")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "a"))  # …restored by monkeypatch
    c.set("modules.overrides", {"finance": "false"}, scope="global")
    assert c.get("modules.overrides") == {"finance": "false"}


def test_an_unsaved_set_survives_an_external_write(cfg) -> None:
    c, path = cfg
    c.set("modules.overrides", {"finance": "false"}, scope="global")
    _bump_mtime(path, "telegram:\n  x: 1\n  y: 2\n")
    assert c.get("telegram.y") == 2, "the external write is visible"
    assert c.get("modules.overrides") == {"finance": "false"}, "and the unsaved set is not lost"


def test_save_keeps_what_another_process_wrote_meanwhile(cfg) -> None:
    c, path = cfg
    _bump_mtime(path, "telegram:\n  x: 1\n  y: 2\n")  # the CLI, in another process
    c.set("modules.overrides", {"devops": True}, scope="global")  # the daemon toggles a module
    c.save(scope="global")
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["telegram"] == {"x": 1, "y": 2}, "the CLI's write survived the daemon's save"
    assert on_disk["modules"]["overrides"] == {"devops": True}


def test_pending_writes_replay_in_order(cfg) -> None:
    c, path = cfg
    c.set("modules.overrides.finance", "false", scope="global")
    c.set("modules.overrides", {}, scope="global")  # later, the parent is reset
    _bump_mtime(path, "telegram:\n  x: 1\n  y: 2\n")  # forces a refresh + replay
    assert c.get("modules.overrides") == {}, (
        "the later write wins, exactly as it did the first time"
    )


def test_save_clears_the_ledger_and_reload_discards_it(cfg) -> None:
    c, path = cfg
    c.set("modules.overrides", {"devops": True}, scope="global")
    c.save(scope="global")
    assert c._pending_global == {}
    c.set("modules.overrides", {"devops": False}, scope="global")
    c.reload()
    assert c._pending_global == {}
    assert c.get("modules.overrides") == {"devops": True}, "reload discards in-memory changes"


def test_plugin_toggle_keeps_a_concurrent_write(cfg) -> None:
    c, path = cfg
    _bump_mtime(path, "telegram:\n  x: 1\n  y: 2\n")
    c.disable_plugin("weather")
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["telegram"] == {"x": 1, "y": 2}
    assert on_disk["plugins"]["disabled_plugins"] == ["weather"]
    c.enable_plugin("weather")
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["plugins"]["disabled_plugins"] == []
