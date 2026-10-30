"""Two writers of `~/.navig/config.yaml` must not erase each other's change.

`ConfigSingleton._save_global` refreshes from disk and then replaces the file, but
it held only the per-process `_lock`. A `navig config set` in another process could
write between that refresh and that replace, and the daemon's replace — built from
the base it had just read — erased the CLI's key. The refresh-then-write of every
global-config writer now runs under one cross-process file lock.

The second writer here is a separate `ConfigManager` on another thread: it holds its
own snapshot and its own handle on the lock file, exactly as another process would.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from navig.core import shared_config
from navig.core.shared_config import ConfigSingleton


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text("seed: 1\n", encoding="utf-8")
    ConfigSingleton._instance = None
    try:
        yield tmp_path
    finally:
        ConfigSingleton._instance = None


def test_a_write_landing_during_a_daemon_save_is_not_erased(isolated_config, monkeypatch):
    from navig.config import ConfigManager

    config = ConfigSingleton()
    config.set("daemon_key", "from-daemon")

    in_save = threading.Event()
    real_write = shared_config.atomic_write_yaml

    def slow_write(*args, **kwargs):
        if threading.current_thread().name == "daemon":
            in_save.set()
            time.sleep(0.5)  # the window another process used to write into
        return real_write(*args, **kwargs)

    monkeypatch.setattr(shared_config, "atomic_write_yaml", slow_write)

    daemon = threading.Thread(target=config.save, name="daemon")
    daemon.start()
    assert in_save.wait(5)
    cli = threading.Thread(
        target=lambda: ConfigManager().set_global("cli_key", "from-cli"), name="cli"
    )
    cli.start()
    daemon.join(15)
    cli.join(15)

    on_disk = yaml.safe_load((isolated_config / "config.yaml").read_text(encoding="utf-8"))
    assert on_disk.get("daemon_key") == "from-daemon"
    assert on_disk.get("cli_key") == "from-cli", (
        "the CLI's write landed inside the daemon's refresh-then-replace and was erased"
    )
    assert on_disk.get("seed") == 1
