"""
Tests for webserver type auto-detection in webserver commands.
"""


import pytest
import yaml

from navig.config import ConfigManager

pytestmark = pytest.mark.integration


@pytest.fixture
def config_manager(tmp_path, monkeypatch):
    """A ConfigManager whose host configs really are isolated to this test.

    This used to patch HOME/USERPROFILE and construct a bare ``ConfigManager()``, which
    isolated NOTHING: ``hosts_dir`` comes from app-root detection, not the home dir, so
    every test here resolved to the SAME `<repo>/core/.navig/hosts` — and every one of
    them saves a host called `test-host`. Sequentially that is invisible (each test
    overwrites, then reads its own write); under `-n auto` the workers race for one file,
    which is why this file failed with a DIFFERENT pair of assertions on each full-suite
    run ("apache2 == caddy" one run, "lighttpd == apache2" the next) while passing 12/12
    in isolation. It also wrote into the developer's real checkout.

    ``config_dir=`` is the seam the constructor documents for exactly this ("skips
    automatic app root detection") and is the ONLY one that moves ``hosts_dir``:
    ``NAVIG_CONFIG_DIR`` — which conftest already isolates session-wide — governs
    ``global_config_dir`` only.

    ⚠ ``config_dir=`` is necessary and NOT sufficient. ``ContextManager.set_active_host``
    does **not** go through ConfigManager for the project-local half — it computes
    ``Path.cwd() / ".navig"`` directly and writes ``active_host`` there. No constructor
    argument can reach that. Measured: with the ``config_dir=`` above already in place,
    running this file still produced ``<repo>/core/.navig/config.yaml`` containing
    ``active_host: switch-host``, in a file every xdist worker shares — and one the
    operator's own ``navig`` reads when they work in ``core/``. ``chdir`` is the only
    lever for a cwd-derived path, so it is part of the isolation, not tidiness.
    """
    monkeypatch.chdir(tmp_path)
    return ConfigManager(config_dir=tmp_path)


@pytest.fixture
def nginx_host_config():
    """Sample host configuration with nginx app."""
    return {
        "name": "test-host",
        "host": "test.example.com",
        "port": 22,
        "user": "root",
        "ssh_key": "~/.ssh/test",
        "default_app": "myapp",
        "apps": {
            "myapp": {
                "database": {"type": "mysql", "name": "myapp_db"},
                "webserver": {"type": "nginx"},
            }
        },
    }


@pytest.fixture
def apache_host_config():
    """Sample host configuration with apache2 app."""
    return {
        "name": "test-host",
        "host": "test.example.com",
        "port": 22,
        "user": "root",
        "ssh_key": "~/.ssh/test",
        "default_app": "myapp",
        "apps": {
            "myapp": {
                "database": {"type": "mysql", "name": "myapp_db"},
                "webserver": {"type": "apache2"},
            }
        },
    }


class TestWebserverTypeAutoDetection:
    """Test webserver type auto-detection from app config."""

    def test_load_app_with_nginx(self, config_manager, nginx_host_config):
        """Test loading app with nginx webserver type."""
        config_manager.save_host_config("test-host", nginx_host_config)

        app = config_manager.load_app_config("test-host", "myapp")
        assert app["webserver"]["type"] == "nginx"

    def test_load_app_with_apache2(self, config_manager, apache_host_config):
        """Test loading app with apache2 webserver type."""
        config_manager.save_host_config("test-host", apache_host_config)

        app = config_manager.load_app_config("test-host", "myapp")
        assert app["webserver"]["type"] == "apache2"

    def test_load_app_missing_webserver_type(self, config_manager):
        """Test that loading app without webserver.type raises ValueError."""
        config = {
            "name": "test-host",
            "host": "test.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/test",
            "apps": {
                "myapp": {
                    "database": {"type": "mysql"}
                    # Missing webserver.type
                }
            },
        }
        config_manager.save_host_config("test-host", config)

        with pytest.raises(ValueError) as exc_info:
            config_manager.load_app_config("test-host", "myapp")

        assert "Missing 'webserver.type'" in str(exc_info.value)
        assert "webserver.type: nginx" in str(exc_info.value)
        assert "webserver.type: apache2" in str(exc_info.value)

    def test_load_app_missing_webserver_section(self, config_manager):
        """Test that loading app without webserver section raises ValueError."""
        config = {
            "name": "test-host",
            "host": "test.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/test",
            "apps": {
                "myapp": {
                    "database": {"type": "mysql"}
                    # Missing entire webserver section
                }
            },
        }
        config_manager.save_host_config("test-host", config)

        with pytest.raises(ValueError) as exc_info:
            config_manager.load_app_config("test-host", "myapp")

        assert "Missing 'webserver.type'" in str(exc_info.value)


class TestWebserverTypeNormalization:
    """Test webserver type normalization (apache2 → apache for commands)."""

    def test_apache2_normalized_to_apache(self, config_manager, apache_host_config):
        """Test that apache2 is normalized to apache for command execution."""
        config_manager.save_host_config("test-host", apache_host_config)

        app = config_manager.load_app_config("test-host", "myapp")
        server_type = app["webserver"]["type"]

        # Verify it's apache2 in config
        assert server_type == "apache2"

        # Verify normalization logic (apache2 → apache)
        server_type_normalized = "apache" if server_type == "apache2" else server_type
        assert server_type_normalized == "apache"

    def test_nginx_remains_nginx(self, config_manager, nginx_host_config):
        """Test that nginx remains nginx (no normalization needed)."""
        config_manager.save_host_config("test-host", nginx_host_config)

        app = config_manager.load_app_config("test-host", "myapp")
        server_type = app["webserver"]["type"]

        # Verify it's nginx in config
        assert server_type == "nginx"

        # Verify no normalization needed
        server_type_normalized = "apache" if server_type == "apache2" else server_type
        assert server_type_normalized == "nginx"


class TestInvalidWebserverTypes:
    """Test handling of invalid/unsupported webserver types."""

    def test_invalid_webserver_type_lighttpd(self, config_manager):
        """Test loading app with unsupported webserver type (lighttpd)."""
        config = {
            "name": "test-host",
            "host": "test.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/test",
            "apps": {"myapp": {"webserver": {"type": "lighttpd"}}},  # Unsupported type
        }
        config_manager.save_host_config("test-host", config)

        # Should load successfully (no validation on type values currently)
        app = config_manager.load_app_config("test-host", "myapp")
        assert app["webserver"]["type"] == "lighttpd"

    def test_invalid_webserver_type_caddy(self, config_manager):
        """Test loading app with unsupported webserver type (caddy)."""
        config = {
            "name": "test-host",
            "host": "test.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/test",
            "apps": {"myapp": {"webserver": {"type": "caddy"}}},  # Unsupported type
        }
        config_manager.save_host_config("test-host", config)

        # Should load successfully (no validation on type values currently)
        app = config_manager.load_app_config("test-host", "myapp")
        assert app["webserver"]["type"] == "caddy"


class TestLegacyFormatWebserverDetection:
    """Test webserver type detection in legacy format configurations."""

    def test_legacy_format_nginx(self, config_manager):
        """Test legacy format config with services.web: nginx."""
        # Create legacy format config
        legacy_config = {
            "name": "legacy-server",
            "host": "legacy.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/legacy",
            "database": {"type": "mysql", "name": "legacy_db"},
            "services": {"web": "nginx"},  # Legacy format
        }

        # Save as legacy format (in apps directory)
        legacy_path = config_manager.apps_dir / "legacy-server.yaml"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(legacy_path, "w") as f:
            yaml.dump(legacy_config, f)

        # Load as app config (should work via backward compatibility)
        app = config_manager.load_app_config("legacy-server", "legacy-server")

        # Verify services.web is accessible
        assert "services" in app
        assert app["services"]["web"] == "nginx"

    def test_legacy_format_apache(self, config_manager):
        """Test legacy format config with services.web: apache."""
        # Create legacy format config
        legacy_config = {
            "name": "legacy-server",
            "host": "legacy.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/legacy",
            "database": {"type": "mysql", "name": "legacy_db"},
            "services": {"web": "apache"},  # Legacy format
        }

        # Save as legacy format (in apps directory)
        legacy_path = config_manager.apps_dir / "legacy-server.yaml"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(legacy_path, "w") as f:
            yaml.dump(legacy_config, f)

        # Load as app config (should work via backward compatibility)
        app = config_manager.load_app_config("legacy-server", "legacy-server")

        # Verify services.web is accessible
        assert "services" in app
        assert app["services"]["web"] == "apache"


class TestMultipleAppsWebserverTypes:
    """Test handling of multiple apps with different webserver types."""

    def test_host_with_mixed_webserver_types(self, config_manager):
        """Test host with 3 apps using different webserver types."""
        config = {
            "name": "multi-host",
            "host": "multi.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/multi",
            "default_app": "app-a",
            "apps": {
                "app-a": {
                    "webserver": {"type": "nginx"},
                    "database": {"type": "mysql"},
                },
                "app-b": {
                    "webserver": {"type": "apache2"},
                    "database": {"type": "postgresql"},
                },
                "app-c": {
                    "webserver": {"type": "nginx"},
                    "database": {"type": "mysql"},
                },
            },
        }
        config_manager.save_host_config("multi-host", config)

        # Load each app and verify correct webserver type
        app_a = config_manager.load_app_config("multi-host", "app-a")
        assert app_a["webserver"]["type"] == "nginx"

        app_b = config_manager.load_app_config("multi-host", "app-b")
        assert app_b["webserver"]["type"] == "apache2"

        app_c = config_manager.load_app_config("multi-host", "app-c")
        assert app_c["webserver"]["type"] == "nginx"

    def test_switching_between_apps_different_types(self, config_manager):
        """Test switching active app between different webserver types."""
        config = {
            "name": "switch-host",
            "host": "switch.example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/switch",
            "default_app": "nginx-app",
            "apps": {
                "nginx-app": {
                    "webserver": {"type": "nginx"},
                    "database": {"type": "mysql"},
                },
                "apache-app": {
                    "webserver": {"type": "apache2"},
                    "database": {"type": "mysql"},
                },
            },
        }
        config_manager.save_host_config("switch-host", config)

        # Set active host and app to nginx
        config_manager.set_active_host("switch-host")
        config_manager.set_active_app("nginx-app")

        # Verify nginx app
        app = config_manager.load_app_config("switch-host", "nginx-app")
        assert app["webserver"]["type"] == "nginx"

        # Switch to apache app
        config_manager.set_active_app("apache-app")

        # Verify apache2 app
        app = config_manager.load_app_config("switch-host", "apache-app")
        assert app["webserver"]["type"] == "apache2"


class TestFixtureIsolation:
    """The fixture must really isolate, and fail LOUDLY here if it stops.

    Every test above saves a host called `test-host`. When `hosts_dir` resolved to the
    shared `<repo>/core/.navig/hosts`, that was invisible sequentially and a race under
    `-n auto` — this file failed with a DIFFERENT pair of assertions on each full-suite
    run while passing 12/12 in isolation. These turn that heisenbug into a deterministic
    failure at the point of the mistake.
    """

    def test_host_configs_are_written_inside_the_test_tmp_dir(self, config_manager, tmp_path):
        assert tmp_path in config_manager.hosts_dir.parents, (
            f"hosts_dir is {config_manager.hosts_dir}, outside this test's tmp_path — the "
            "fixture is not isolating, so every test here shares one `test-host.yaml`"
        )

    # (A second assertion comparing against the app-root `.navig/hosts` derived from
    # `Path(__file__)` was dropped: it is implied by the one above — a hosts_dir under
    # tmp_path cannot also be the shared path — and deriving a repo path from __file__ makes
    # `test_source_guards_are_wired` classify this ordinary suite as a tree-scanning guard.)

    def test_a_saved_host_lands_in_this_tmp_dir_and_nowhere_else(self, config_manager, tmp_path):
        config_manager.save_host_config("isolation-probe", {"name": "isolation-probe", "apps": {}})
        written = list(tmp_path.rglob("isolation-probe.yaml"))
        assert written, "the host config was written somewhere outside tmp_path"
