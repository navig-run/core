"""
Regression tests for ``ConfigManager.get`` / ``ConfigManager.set``.

These two methods were *assumed to exist* by call sites across the tree
(``ConfigManager().set("telemetry.enabled", True)``,
``get_config_manager().get("daemon.browser_port", 7421)``, onboarding's
``_set(key, value, scope="global")``, output-style persistence, ...) — but they
did **not**. Every such call raised ``AttributeError`` and either crashed the
command or, inside a bare ``except``, silently fell back to a default. So
``navig telemetry enable/disable/status``, ``navig user set``, onboarding
cloud-deploy, and output-style activation were all dead.

The existence guard below fails the build if either method is ever removed —
that removal would silently re-break every one of those callers.
"""

import pytest

from navig.config import ConfigManager

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    yield tmp_path


@pytest.fixture
def cm(temp_home):
    """Fresh ConfigManager isolated to a temp config dir (no project .navig)."""
    return ConfigManager(config_dir=temp_home / ".navig")


class TestConfigManagerGetSet:
    def test_methods_exist(self):
        """Guard: both methods MUST exist — their absence silently re-breaks
        ~a dozen call sites (telemetry, user, onboarding, output-styles, ...)."""
        assert callable(getattr(ConfigManager, "get", None)), "ConfigManager.get was removed"
        assert callable(getattr(ConfigManager, "set", None)), "ConfigManager.set was removed"

    def test_set_then_get_roundtrip(self, cm):
        cm.set("telemetry.enabled", True)
        assert cm.get("telemetry.enabled") is True

    def test_set_preserves_value_type(self, cm):
        """``.set`` must store the real value, not stringify it — a bool stays a
        bool (unlike ``navig config set`` which stores raw strings)."""
        cm.set("telemetry.enabled", True)
        assert cm.get("telemetry.enabled") is True
        cm.set("telemetry.enabled", False)
        assert cm.get("telemetry.enabled") is False
        cm.set("daemon.browser_port", 7421)
        assert cm.get("daemon.browser_port") == 7421

    def test_get_default_when_missing_kwarg(self, cm):
        assert cm.get("telemetry.enabled", default=False) is False

    def test_get_default_when_missing_positional(self, cm):
        # webhook.py / browser_orchestrator.py call form: positional default
        assert cm.get("daemon.browser_port", 7421) == 7421

    def test_get_missing_returns_none_by_default(self, cm):
        assert cm.get("does.not.exist.at.all") is None

    def test_get_does_not_treat_partial_path_as_dict(self, cm):
        """Walking past a non-dict leaf returns the default, never raises."""
        cm.set("user.name", "Alice")
        # user.name is a string; asking for user.name.deeper must not explode
        assert cm.get("user.name.deeper", "fallback") == "fallback"

    def test_persists_across_instances(self, cm, temp_home):
        """A write is durable — a brand-new ConfigManager on the same dir sees it.
        This is what makes short-lived CLI commands actually persist."""
        cm.set("user.email", "a@b.co")
        fresh = ConfigManager(config_dir=temp_home / ".navig")
        assert fresh.get("user.email") == "a@b.co"

    def test_set_accepts_explicit_global_scope(self, cm):
        """Onboarding's ``_set(key, value, scope="global")`` call form."""
        cm.set("cloud.enabled", True, scope="global")
        assert cm.get("cloud.enabled") is True

    def test_set_rejects_non_global_scope(self, cm):
        """A non-global scope must raise, not silently write to the wrong place."""
        with pytest.raises(ValueError):
            cm.set("x.y", 1, scope="project")

    def test_nested_writes_do_not_clobber_siblings(self, cm):
        """Two writes under the same parent must both survive (the lost-update /
        subtree-clobber trap that killed configs)."""
        cm.set("telemetry.enabled", True)
        cm.set("telemetry.endpoint", "https://t.example")
        assert cm.get("telemetry.enabled") is True
        assert cm.get("telemetry.endpoint") == "https://t.example"


class TestTelemetryCommandNowWorks:
    """End-to-end: the actual ``navig telemetry`` command persists state.

    This fails on pristine ``main`` (ConfigManager had no ``.set``/``.get`` — the
    command's ``except`` swallowed the AttributeError and it never persisted).
    """

    def test_enable_then_status_reads_enabled(self, monkeypatch, tmp_path):
        # Pin NAVIG_CONFIG_DIR so the command's no-arg ConfigManager() writes to
        # this isolated dir, never the real project/user config.
        cfg_dir = tmp_path / ".navig"
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_dir))
        from navig.commands.telemetry import telemetry_enable

        telemetry_enable()  # uses ConfigManager().set under the hood
        # A fresh manager on the same dir must observe the persisted flag.
        assert ConfigManager(config_dir=cfg_dir).get("telemetry.enabled") is True
