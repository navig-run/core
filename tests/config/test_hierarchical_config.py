#!/usr/bin/env python3
"""Hierarchical configuration resolution: app-local `.navig/` overrides the global dir.

Two things were wrong with the previous version of this test, both of the "verification
that lies" class:

1. It wrote to ``Path.home() / ".navig"`` — the operator's REAL config dir. On a live
   machine that dir holds real hosts (cyberaigen-vps, kali-warroom, …); the test created
   ``~/.navig/hosts/test-server.yaml`` in it and deleted files from it on every run,
   risking a real host named "test-server" and instantiating a ConfigManager against the
   real config (which can migrate/rewrite it). The session-scoped isolation fixture sets
   NAVIG_CONFIG_DIR, but ``Path.home()`` bypasses it entirely.

2. It never asserted anything. Every check was ``ch.success`` / ``ch.error`` (prints), so
   the test passed unconditionally as long as it did not crash — green even when the wrong
   config was loaded. Fake coverage.

Now the "global" dir is ``config_dir()`` — which the conftest session fixture isolates to
a temp dir — so nothing outside the sandbox is ever touched, and every phase is a real
assertion that fails when the hierarchy resolves incorrectly.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from navig.commands.init import init_app
from navig.config import ConfigManager
from navig.platform.paths import config_dir

pytestmark = pytest.mark.integration


APP_HOST = {
    "name": "test-server",
    "host": "10.0.0.1",
    "port": 2222,
    "user": "app-user",
    "ssh_key": "~/.ssh/app_key",
    "database": {
        "type": "postgres", "remote_port": 5432, "local_tunnel_port": 5433,
        "name": "app_db", "user": "app_dbuser", "password": "app_pass",
    },
}
GLOBAL_HOST = {
    "name": "test-server",
    "host": "10.0.0.10",   # deliberately different from APP_HOST
    "port": 22,
    "user": "global-user",
    "ssh_key": "~/.ssh/global_key",
    "database": {
        "type": "mysql", "remote_port": 3306, "local_tunnel_port": 3307,
        "name": "global_db", "user": "global_dbuser", "password": "global_pass",
    },
}


def _write_host(hosts_dir: Path, cfg: dict) -> None:
    hosts_dir.mkdir(parents=True, exist_ok=True)
    (hosts_dir / f"{cfg['name']}.yaml").write_text(yaml.dump(cfg), encoding="utf-8")


def test_hierarchical_config():
    """App-local host config overrides the global one; the global is used outside an app."""
    original_dir = Path.cwd()
    test_dir = Path(tempfile.mkdtemp(prefix="navig-hierarchy-test-"))

    # The "global" dir is the ISOLATED config_dir() (sandboxed by the session fixture),
    # never the operator's real ~/.navig.
    global_dir = config_dir()

    try:
        # TEST 1 — initialize an app in a temp dir
        os.chdir(test_dir)
        init_app({"copy_global": False, "quiet": True, "yes": True})
        navig_dir = test_dir / ".navig"
        assert navig_dir.exists(), "app initialization must create a .navig/ directory"

        # TEST 2 + 3 — an app-local host and a DIFFERENT global host of the same name
        _write_host(navig_dir / "hosts", APP_HOST)
        _write_host(global_dir / "hosts", GLOBAL_HOST)

        # TEST 4 — inside the app, the app-local config wins
        os.chdir(test_dir)
        loaded = ConfigManager(verbose=False).load_host_config("test-server")
        assert loaded, "host config must load from inside the app"
        assert loaded["host"] == "10.0.0.1", (
            f"app config must take precedence, got {loaded['host']!r} (the global value)"
        )
        assert loaded["port"] == 2222

        # TEST 5 — app root is still detected from a nested subdirectory
        subdir = test_dir / "src" / "components"
        subdir.mkdir(parents=True, exist_ok=True)
        os.chdir(subdir)
        loaded = ConfigManager(verbose=False).load_host_config("test-server")
        assert loaded and loaded["host"] == "10.0.0.1", (
            "the app root must be found by walking up from a subdirectory"
        )

        # TEST 6 — OUTSIDE any app, the global config is used
        temp_outside = Path(tempfile.mkdtemp(prefix="navig-outside-"))
        try:
            os.chdir(temp_outside)
            # Pin the global dir so an unrelated ancestor .navig/ on a shared temp root
            # can't make this non-deterministic.
            loaded = ConfigManager(config_dir=global_dir, verbose=False).load_host_config(
                "test-server"
            )
            assert loaded and loaded["host"] == "10.0.0.10", (
                f"outside an app the GLOBAL config must be used, got "
                f"{loaded and loaded['host']!r}"
            )
            assert loaded["user"] == "global-user"
        finally:
            os.chdir(test_dir)
            shutil.rmtree(temp_outside, ignore_errors=True)

        # TEST 7 — the merged host list includes the host
        os.chdir(test_dir)
        assert "test-server" in ConfigManager(verbose=False).list_hosts()

        # TEST 8 — app and global resolve to DIFFERENT database paths
        os.chdir(test_dir)
        app_db = ConfigManager(verbose=False).db_file
        temp_db_test = Path(tempfile.mkdtemp(prefix="navig-db-test-"))
        try:
            os.chdir(temp_db_test)
            global_db = ConfigManager(verbose=False).db_file
            assert app_db != global_db, (
                f"app and global must use separate databases; both were {app_db}"
            )
        finally:
            os.chdir(test_dir)
            shutil.rmtree(temp_db_test, ignore_errors=True)

    finally:
        os.chdir(original_dir)
        shutil.rmtree(test_dir, ignore_errors=True)
        # Only ever remove the fixture we wrote, and only from the ISOLATED global dir.
        gtf = global_dir / "hosts" / "test-server.yaml"
        if gtf.exists():
            gtf.unlink()


if __name__ == "__main__":
    test_hierarchical_config()
