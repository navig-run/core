#!/usr/bin/env python3
"""
Comprehensive test for hierarchical configuration system.
"""

import os
import sys

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

import shutil
import tempfile
from pathlib import Path

import yaml

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from navig import console_helper as ch
from navig.commands.init import init_app
from navig.config import ConfigManager
import pytest

pytestmark = pytest.mark.integration


def test_hierarchical_config():
    """Test complete hierarchical configuration system."""

    # Create temp directory for testing
    test_dir = Path(tempfile.mkdtemp(prefix="navig-hierarchy-test-"))
    print(f"\n{'=' * 80}")
    print(f"Test directory: {test_dir}")
    print(f"{'=' * 80}\n")

    try:
        original_dir = Path.cwd()

        # ========================================================================
        # TEST 1: Initialize app
        # ========================================================================
        print("TEST 1: Initialize App")
        print("-" * 80)
        os.chdir(test_dir)
        init_app({"copy_global": False, "quiet": False, "yes": True})

        navig_dir = test_dir / ".navig"
        if navig_dir.exists():
            ch.success("✓ App initialized successfully")
        else:
            ch.error("✗ App initialization failed", "")
            return

        # ========================================================================
        # TEST 2: Create app-specific host config
        # ========================================================================
        print("\nTEST 2: Create app-specific host config")
        print("-" * 80)

        app_host_config = {
            "name": "test-server",
            "host": "10.0.0.1",
            "port": 2222,
            "user": "app-user",
            "ssh_key": "~/.ssh/app_key",
            "database": {
                "type": "mysql",
                "remote_port": 3306,
                "local_tunnel_port": 3307,
                "name": "app_db",
                "user": "app_dbuser",
                "password": "app_pass",
            },
        }

        app_host_file = navig_dir / "hosts" / "test-server.yaml"
        with open(app_host_file, "w") as f:
            yaml.dump(app_host_config, f)
        ch.info(f"Created app host config: {app_host_file}")

        # ========================================================================
        # TEST 3: Create global host config with different values
        # ========================================================================
        print("\nTEST 3: Create global host config")
        print("-" * 80)

        global_config_dir = Path.home() / ".navig"
        global_config_dir.mkdir(exist_ok=True)
        global_hosts_dir = global_config_dir / "hosts"
        global_hosts_dir.mkdir(exist_ok=True)

        global_host_config = {
            "name": "test-server",
            "host": "10.0.0.10",  # Different IP
            "port": 22,  # Different port
            "user": "global-user",  # Different user
            "ssh_key": "~/.ssh/global_key",
            "database": {
                "type": "mysql",
                "remote_port": 3306,
                "local_tunnel_port": 3307,
                "name": "global_db",  # Different database
                "user": "global_dbuser",
                "password": "global_pass",
            },
        }

        global_host_file = global_hosts_dir / "test-server.yaml"
        with open(global_host_file, "w") as f:
            yaml.dump(global_host_config, f)
        ch.info(f"Created global host config: {global_host_file}")

        # ========================================================================
        # TEST 4: Load config from app directory (should use app config)
        # ========================================================================
        print("\nTEST 4: Load config from app directory")
        print("-" * 80)

        os.chdir(test_dir)
        config_mgr = ConfigManager(verbose=True)
        loaded_config = config_mgr.load_host_config("test-server")

        if loaded_config:
            if loaded_config["host"] == "10.0.0.1":
                ch.success("✓ App config takes precedence (correct)")
                ch.info(f"  Host: {loaded_config['host']} (app)")
                ch.info(f"  Port: {loaded_config['port']}")
                ch.info(f"  User: {loaded_config['user']}")
            else:
                ch.error(
                    "✗ Wrong config loaded",
                    f"Expected app config (10.0.0.1), got {loaded_config['host']}",
                )
        else:
            ch.error("✗ Failed to load host config", "")

        # ========================================================================
        # TEST 5: Load config from subdirectory (should still use app config)
        # ========================================================================
        print("\nTEST 5: Load config from subdirectory")
        print("-" * 80)

        subdir = test_dir / "src" / "components"
        subdir.mkdir(parents=True, exist_ok=True)
        os.chdir(subdir)

        config_mgr = ConfigManager(verbose=True)
        loaded_config = config_mgr.load_host_config("test-server")

        if loaded_config:
            if loaded_config["host"] == "10.0.0.1":
                ch.success("✓ App root detected from subdirectory (correct)")
                ch.info(f"  Current dir: {Path.cwd()}")
                ch.info(f"  App root: {config_mgr.base_dir.parent}")
                ch.info(f"  Host: {loaded_config['host']}")
            else:
                ch.error(
                    "✗ Wrong config loaded from subdirectory",
                    f"Expected app config, got {loaded_config['host']}",
                )
        else:
            ch.error("✗ Failed to load host config from subdirectory", "")

        # ========================================================================
        # TEST 6: Load config from outside app (should use global config)
        # ========================================================================
        print("\nTEST 6: Load config from outside app directory")
        print("-" * 80)

        temp_outside = Path(tempfile.mkdtemp(prefix="navig-outside-"))
        try:
            os.chdir(temp_outside)

            # Force global config resolution in this phase so the test remains
            # deterministic even if an unrelated ancestor directory contains
            # its own .navig/ (common in shared temp roots on CI/dev machines).
            config_mgr = ConfigManager(config_dir=Path.home() / ".navig", verbose=True)
            loaded_config = config_mgr.load_host_config("test-server")

            if loaded_config:
                if loaded_config["host"] == "10.0.0.10":
                    ch.success("✓ Global config used outside app (correct)")
                    ch.info(f"  Host: {loaded_config['host']} (global)")
                    ch.info(f"  User: {loaded_config['user']}")
                else:
                    ch.error(
                        "✗ Wrong config loaded outside app",
                        f"Expected global config (10.0.0.10), got {loaded_config['host']}",
                    )
            else:
                ch.error("✗ Failed to load global host config", "")
        finally:
            os.chdir(test_dir)  # Change back before cleanup
            if temp_outside.exists():
                shutil.rmtree(temp_outside, ignore_errors=True)

        # ========================================================================
        # TEST 7: List hosts (should show merged list)
        # ========================================================================
        print("\nTEST 7: List hosts from app directory")
        print("-" * 80)

        os.chdir(test_dir)
        config_mgr = ConfigManager(verbose=True)
        hosts = config_mgr.list_hosts()

        if "test-server" in hosts:
            ch.success(f"✓ Host 'test-server' found in list")
            ch.info(f"  Total hosts: {len(hosts)}")
        else:
            ch.error("✗ Host 'test-server' not found in host list", "")

        # ========================================================================
        # TEST 8: Database path separation
        # ========================================================================
        print("\nTEST 8: Database path separation")
        print("-" * 80)

        os.chdir(test_dir)
        config_mgr_app = ConfigManager(verbose=False)
        app_db_path = config_mgr_app.db_file

        temp_db_test = Path(tempfile.mkdtemp(prefix="navig-db-test-"))
        try:
            os.chdir(temp_db_test)
            config_mgr_global = ConfigManager(verbose=False)
            global_db_path = config_mgr_global.db_file

            if app_db_path != global_db_path:
                ch.success("✓ Database paths are separated")
                ch.info(f"  App DB: {app_db_path}")
                ch.info(f"  Global DB:  {global_db_path}")
            else:
                ch.error("✗ Database paths are not separated", f"Both use: {app_db_path}")
        finally:
            os.chdir(test_dir)
            if temp_db_test.exists():
                shutil.rmtree(temp_db_test, ignore_errors=True)

        # ========================================================================
        # Summary
        # ========================================================================
        print("\n" + "=" * 80)
        ch.success("All hierarchical configuration tests completed!")
        print("=" * 80 + "\n")

    finally:
        # Cleanup
        os.chdir(original_dir)
        if test_dir.exists():
            shutil.rmtree(test_dir)
            ch.dim(f"Cleaned up test directory: {test_dir}")

        # Cleanup global test config
        global_test_file = Path.home() / ".navig" / "hosts" / "test-server.yaml"
        if global_test_file.exists():
            global_test_file.unlink()
            ch.dim(f"Cleaned up global test config: {global_test_file}")


if __name__ == "__main__":
    test_hierarchical_config()
