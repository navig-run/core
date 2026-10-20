"""Gate 2 — the four public installation profiles (cli · minimal · server · full).

Covers the manifest contract (extras ⊆ pyproject, modules ⊆ real module files,
daemon flags ⊆ the real `navig service install` signature), profile resolution
when applied, and the pin that the legacy/default profile spec is unchanged.

Application tests run against an ISOLATED config dir and stub the two modules
whose side effects are not this test's subject and are not safe to run in a
suite: ``migrate_legacy`` MOVES the real ``~/Documents/.navig`` when it exists,
and ``vault_bootstrap`` creates a process-global vault singleton.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomllib

from navig.installer.profiles import (
    DAEMON_FLAGS,
    DEFAULT_PROFILE,
    INSTALL_PROFILES,
    KNOWN_MODULES,
    PROFILE_CONFIG_KEY,
    PROFILE_MODULES,
    VALID_PROFILES,
    daemon_cli_args,
    profile_extras_spec,
    resolve_install_profile,
    validate_install_profile,
    validate_profile_manifest,
)

PUBLIC_PROFILES = ("cli", "minimal", "server", "full")
REPO_ROOT = Path(__file__).resolve().parents[3]


# ─────────────────────────────────────────────────────────────────────────────
# Manifest validation
# ─────────────────────────────────────────────────────────────────────────────


class TestManifestValidation:
    @pytest.mark.parametrize("name", PUBLIC_PROFILES)
    def test_public_profile_is_registered_everywhere(self, name: str):
        assert name in INSTALL_PROFILES
        assert name in PROFILE_MODULES
        assert name in VALID_PROFILES
        assert PROFILE_MODULES[name] == INSTALL_PROFILES[name]["modules"]
        assert len(PROFILE_MODULES[name]) >= 1
        assert len(PROFILE_MODULES[name]) == len(set(PROFILE_MODULES[name]))

    @pytest.mark.parametrize("name", PUBLIC_PROFILES)
    def test_manifest_validates_clean(self, name: str):
        errors = validate_profile_manifest(name, INSTALL_PROFILES[name])
        assert errors == [], f"{name}: {errors}"

    @pytest.mark.parametrize("name", PUBLIC_PROFILES)
    def test_modules_are_real_module_files(self, name: str):
        modules_dir = Path(__file__).resolve().parents[2] / "navig" / "installer" / "modules"
        for mod in INSTALL_PROFILES[name]["modules"]:
            assert mod in KNOWN_MODULES, f"{name} names missing module {mod!r}"
            assert (modules_dir / f"{mod}.py").is_file()

    def test_extras_exist_in_pyproject_read_independently(self):
        """The product's own reader could be wrong; this reads pyproject directly."""
        pyproject = REPO_ROOT / "core" / "pyproject.toml"
        assert pyproject.is_file(), f"pyproject not found at {pyproject}"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        declared = set(data["project"]["optional-dependencies"])
        for name, manifest in INSTALL_PROFILES.items():
            extras = set(manifest["extras"])
            assert extras <= declared, (
                f"{name} declares extras {sorted(extras - declared)} "
                f"that core/pyproject.toml does not define"
            )

    def test_unknown_extra_is_an_error(self):
        manifest = {**INSTALL_PROFILES["cli"], "extras": ["interactive", "not-a-real-extra"]}
        errors = validate_profile_manifest("cli", manifest)
        assert any("not-a-real-extra" in e for e in errors)

    def test_unknown_module_is_an_error(self):
        manifest = {**INSTALL_PROFILES["cli"], "modules": ["config_paths", "core_clli"]}
        errors = validate_profile_manifest("cli", manifest)
        assert any("core_clli" in e for e in errors)

    def test_unknown_daemon_flag_is_an_error(self):
        manifest = {**INSTALL_PROFILES["server"], "daemon": {"bot": True, "browser": True}}
        errors = validate_profile_manifest("server", manifest)
        assert any("browser" in e for e in errors)

    def test_daemon_flag_must_be_boolean(self):
        manifest = {**INSTALL_PROFILES["server"], "daemon": {"gateway": "yes"}}
        errors = validate_profile_manifest("server", manifest)
        assert any("gateway" in e and "bool" in e for e in errors)

    def test_daemon_flags_are_real_service_install_flags(self):
        """The constant must not drift from `navig service install` itself."""
        import inspect

        from navig.commands.service import service_install

        params = set(inspect.signature(service_install).parameters)
        assert DAEMON_FLAGS <= params, (
            f"DAEMON_FLAGS {sorted(DAEMON_FLAGS)} not all real parameters: {sorted(params)}"
        )

    def test_unknown_profile_name_lists_valid_names(self):
        with pytest.raises(ValueError) as exc:
            resolve_install_profile("bogus")
        message = str(exc.value)
        assert "bogus" in message
        for name in VALID_PROFILES:
            assert name in message, f"error must list {name}"

    def test_legacy_profiles_have_no_manifest(self):
        for name in ("node", "operator", "architect", "system_standard", "system_deep"):
            assert resolve_install_profile(name) is None
            assert validate_install_profile(name) is None

    def test_validate_install_profile_returns_public_manifest(self):
        manifest = validate_install_profile("server")
        assert manifest is not None
        assert manifest["daemon"] == {"bot": True, "gateway": True}


# ─────────────────────────────────────────────────────────────────────────────
# Field semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestManifestFieldSemantics:
    def test_cli_and_minimal_never_register_a_service(self):
        assert INSTALL_PROFILES["cli"]["daemon"] is None
        assert INSTALL_PROFILES["minimal"]["daemon"] is None
        assert daemon_cli_args(None) == []

    def test_full_registers_with_service_install_defaults(self):
        assert INSTALL_PROFILES["full"]["daemon"] == {}
        assert daemon_cli_args({}) == []

    def test_server_flags_map_to_real_cli_flags(self):
        args = daemon_cli_args(INSTALL_PROFILES["server"]["daemon"])
        assert args == ["--bot", "--gateway"]

    def test_extras_spec_is_a_pip_extra_string(self):
        assert profile_extras_spec(INSTALL_PROFILES["server"]) == "navig[interactive,telegram]"
        assert profile_extras_spec(INSTALL_PROFILES["cli"]) == "navig[interactive]"

    @pytest.mark.parametrize("name", PUBLIC_PROFILES)
    def test_mode_is_a_known_mode_or_unset(self, name: str):
        mode = INSTALL_PROFILES[name]["mode"]
        if mode is None:
            return
        builtin = Path(__file__).resolve().parents[2] / "navig" / "modes" / "builtin.yaml"
        import yaml

        modes = set((yaml.safe_load(builtin.read_text(encoding="utf-8")) or {}).get("modes", {}))
        assert mode in modes, f"{name} suggests unknown mode {mode!r} (known: {sorted(modes)})"

    def test_no_monitors_field(self):
        """Monitors are omitted on purpose — nothing would wire them up."""
        for manifest in INSTALL_PROFILES.values():
            assert "monitors" not in manifest


# ─────────────────────────────────────────────────────────────────────────────
# The default path is unchanged
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultPathUnchanged:
    #: Today's profile → module mapping, pinned exactly. If a line here moves,
    #: the default installer changed and this test is the alarm.
    TODAY = {
        "node": ["config_paths", "core_cli", "migrate_legacy"],
        "operator": [
            "config_paths",
            "core_cli",
            "migrate_legacy",
            "shell_integration",
            "vault_bootstrap",
            "telegram",
        ],
        "architect": [
            "config_paths",
            "core_cli",
            "migrate_legacy",
            "shell_integration",
            "vault_bootstrap",
            "telegram",
            "mcp",
        ],
        "system_standard": [
            "config_paths",
            "core_cli",
            "migrate_legacy",
            "shell_integration",
            "vault_bootstrap",
            "telegram",
            "mcp",
            "service",
        ],
        "system_deep": [
            "config_paths",
            "core_cli",
            "migrate_legacy",
            "shell_integration",
            "vault_bootstrap",
            "telegram",
            "mcp",
            "service",
            "tray",
            "persona_assets",
        ],
    }

    def test_legacy_module_lists_are_byte_identical(self):
        for name, modules in self.TODAY.items():
            assert PROFILE_MODULES[name] == modules, f"{name} module list changed"

    def test_default_profile_is_still_operator(self):
        assert DEFAULT_PROFILE == "operator"
        assert DEFAULT_PROFILE in VALID_PROFILES

    def test_run_install_still_defaults_to_operator(self):
        import inspect

        from navig.installer import run_install

        assert inspect.signature(run_install).parameters["profile"].default == "operator"

    def test_valid_profiles_is_exactly_the_module_keys(self):
        assert set(VALID_PROFILES) == set(PROFILE_MODULES.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Applying a profile (isolated config dir)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated_install(tmp_path: Path, monkeypatch):
    """A per-test NAVIG_CONFIG_DIR, and a config-manager cache that starts clean.

    ``set_active_mode`` and the daemon manifest resolve the AMBIENT config dir
    (paths.config_dir()), so env isolation is what keeps this test's writes off
    the operator's real ~/.navig — the guard inside run_install only lets those
    two steps run when ctx and the ambient dir agree, and both point here.
    """
    cfg = tmp_path / ".navig"
    cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    from navig.config import reset_config_manager

    reset_config_manager()
    yield cfg
    reset_config_manager()


def _service_install_spy(recorder: list):
    """A stand-in for `navig service install` that keeps the REAL signature.

    Manifest validation reads that signature at run time — a bare MagicMock has
    ``(*args, **kwargs)`` and would (correctly) fail the check. Recording the
    kwargs the manifest passes is what these tests assert on.
    """

    def fake(
        method=None,
        no_start=False,
        bot=None,
        gateway=None,
        scheduler=None,
        health_port=None,
    ):
        recorder.append(
            {
                "method": method,
                "no_start": no_start,
                "bot": bot,
                "gateway": gateway,
                "scheduler": scheduler,
                "health_port": health_port,
            }
        )

    return fake


def _stub_unsafe_modules(monkeypatch):
    """Stub the modules this test must never run for real.

    migrate_legacy MOVES ~/Documents/.navig into the target dir when it exists
    (real user data, out of a test's reach); vault_bootstrap creates the
    process-global vault singleton; telegram dual-writes an AMBIENT bot token
    into that vault when the operator's environment carries one. None of the
    three is what these tests assert — the manifest's settings are.
    """
    from navig.installer.contracts import ModuleState, Result

    def _skip(action, ctx):
        return Result(action_id=action.id, state=ModuleState.SKIPPED, message="stubbed in test")

    for mod in ("migrate_legacy", "vault_bootstrap", "telegram"):
        monkeypatch.setattr(f"navig.installer.modules.{mod}.apply", _skip, raising=True)


class TestProfileApplication:
    def test_cli_records_config_suggests_mode_and_never_registers_a_service(
        self, isolated_install, monkeypatch
    ):
        service_install = MagicMock(
            side_effect=AssertionError("cli must never call `navig service install`")
        )
        monkeypatch.setattr("navig.commands.service.service_install", service_install)

        from navig.installer import run_install

        results = run_install(profile="cli", quiet=True)

        from navig.config import ConfigManager

        cm = ConfigManager(config_dir=isolated_install)
        assert cm.get(PROFILE_CONFIG_KEY) == "cli"

        import yaml

        cfg = yaml.safe_load((isolated_install / "config.yaml").read_text(encoding="utf-8")) or {}
        assert cfg.get("active_mode") == "node"

        service_install.assert_not_called()
        by_id = {r.action_id: r for r in results}
        assert PROFILE_CONFIG_KEY in by_id["profile.config"].message
        assert by_id["profile.mode"].message.startswith("active_mode = node")
        # The "no service" decision is VISIBLE, not merely absent.
        from navig.installer.contracts import ModuleState

        assert by_id["profile.daemon"].state == ModuleState.SKIPPED
        assert "no service registration" in by_id["profile.daemon"].message

    def test_mode_suggestion_never_overrides_an_existing_choice(
        self, isolated_install, monkeypatch
    ):
        service_install = MagicMock()
        monkeypatch.setattr("navig.commands.service.service_install", service_install)

        from navig.installer import run_install

        run_install(profile="cli", quiet=True)

        # The operator switches mode explicitly; re-applying the profile must
        # leave that choice alone ("never re-imposed later").
        import yaml

        cfg_path = isolated_install / "config.yaml"
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        data["active_mode"] = "architect"
        cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        results = run_install(profile="cli", quiet=True)
        by_id = {r.action_id: r for r in results}
        from navig.installer.contracts import ModuleState

        assert by_id["profile.mode"].state == ModuleState.SKIPPED
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        assert cfg.get("active_mode") == "architect"

    def test_server_applies_its_daemon_flags(
        self, isolated_install, monkeypatch
    ):
        _stub_unsafe_modules(monkeypatch)
        calls: list[dict] = []
        monkeypatch.setattr(
            "navig.commands.service.service_install", _service_install_spy(calls)
        )

        from navig.installer import run_install

        results = run_install(profile="server", quiet=True)

        assert len(calls) == 1, f"expected exactly one service install call, got {calls}"
        kwargs = calls[0]
        assert kwargs["bot"] is True
        assert kwargs["gateway"] is True
        # Deprecated flag (cron runs in the gateway) — the server profile no longer
        # passes it, so a server install prints no deprecation warning.
        assert kwargs["scheduler"] is None
        # Explicit None = "let the command decide" for everything not in the manifest.
        assert kwargs["method"] is None
        assert kwargs["health_port"] is None

        from navig.config import ConfigManager

        assert ConfigManager(config_dir=isolated_install).get(PROFILE_CONFIG_KEY) == "server"

        from navig.installer.contracts import ModuleState

        by_id = {r.action_id: r for r in results}
        assert by_id["profile.daemon"].state == ModuleState.APPLIED
        assert "--bot" in by_id["profile.daemon"].message

    def test_navig_no_daemon_env_blocks_registration(self, isolated_install, monkeypatch):
        _stub_unsafe_modules(monkeypatch)
        calls: list[dict] = []
        monkeypatch.setattr(
            "navig.commands.service.service_install", _service_install_spy(calls)
        )
        monkeypatch.setenv("NAVIG_NO_DAEMON", "1")

        from navig.installer import run_install

        results = run_install(profile="server", quiet=True)

        assert calls == []
        from navig.installer.contracts import ModuleState

        by_id = {r.action_id: r for r in results}
        assert by_id["profile.daemon"].state == ModuleState.SKIPPED
        assert "NAVIG_NO_DAEMON" in by_id["profile.daemon"].message

    def test_dry_run_changes_nothing(self, isolated_install, monkeypatch):
        service_install = MagicMock(
            side_effect=AssertionError("dry run must not register a service")
        )
        monkeypatch.setattr("navig.commands.service.service_install", service_install)

        from navig.installer import run_install

        results = run_install(profile="cli", dry_run=True, quiet=True)

        # Assert file absence BEFORE any ConfigManager read below: reading a
        # missing config saves a fresh one, so probing first would manufacture
        # the very file this asserts was never written.
        assert not (isolated_install / "config.yaml").exists()

        from navig.config import ConfigManager

        assert ConfigManager(config_dir=isolated_install).get(PROFILE_CONFIG_KEY) is None
        service_install.assert_not_called()
        from navig.installer.contracts import ModuleState

        assert all(r.state == ModuleState.SKIPPED for r in results)

    def test_invalid_manifest_aborts_before_any_mutation(self, isolated_install, monkeypatch):
        """A defective manifest must fail BEFORE a single module runs."""
        from navig.installer import profiles as profiles_mod

        broken = {
            **INSTALL_PROFILES["cli"],
            "extras": ["interactive", "no-such-extra"],
        }
        monkeypatch.setitem(profiles_mod.INSTALL_PROFILES, "cli", broken)
        # PROFILE_MODULES was built at import time; keep it consistent so the
        # profile name itself still resolves and the manifest check is what fires.
        monkeypatch.setitem(profiles_mod.PROFILE_MODULES, "cli", broken["modules"])

        from navig.installer import run_install

        with pytest.raises(ValueError) as exc:
            run_install(profile="cli", quiet=True)
        assert "no-such-extra" in str(exc.value)

        from navig.config import ConfigManager

        assert ConfigManager(config_dir=isolated_install).get(PROFILE_CONFIG_KEY) is None


class TestPyprojectExtrasReaderIsLive:
    def test_the_extras_reader_actually_reads_in_this_checkout(self):
        """A reader that silently returns None SKIPS every extras check and stays green.

        The skip is correct for an installed wheel (no pyproject on disk); in the
        repo it must never happen, or `unknown extra` validation is dead code that
        a green suite cannot distinguish from working.
        """
        from navig.installer.profiles import _pyproject_extras

        known = _pyproject_extras()
        assert known is not None, (
            "core/pyproject.toml is on disk but the extras reader returned None — "
            "extras validation is being skipped"
        )
        # The extras three of the four public profiles declare must be visible to it.
        assert {"interactive", "telegram", "desktop"} <= known


# ─────────────────────────────────────────────────────────────────────────────
# One-time config defaults (Ubuntu VM findings, 2026-10-15)
# ─────────────────────────────────────────────────────────────────────────────


class TestProfileConfigDefaults:
    def test_server_turns_the_public_tunnel_off(self, isolated_install, monkeypatch):
        """A fresh VPS watcher published its gateway at a trycloudflare URL: the
        gateway's cloud manager defaults ON. The server profile writes the opt-out."""
        _stub_unsafe_modules(monkeypatch)
        monkeypatch.setattr("navig.commands.service.service_install", _service_install_spy([]))
        from navig.config import ConfigManager
        from navig.installer import run_install
        from navig.installer.contracts import ModuleState

        results = run_install(profile="server", quiet=True)

        assert ConfigManager(config_dir=isolated_install).get("cloud.enabled") is False
        by_id = {r.action_id: r for r in results}
        assert by_id["profile.config.cloud.enabled"].state == ModuleState.APPLIED

    def test_an_operator_choice_is_never_overridden(self, isolated_install, monkeypatch):
        _stub_unsafe_modules(monkeypatch)
        monkeypatch.setattr("navig.commands.service.service_install", _service_install_spy([]))
        from navig.config import ConfigManager, reset_config_manager
        from navig.installer import run_install
        from navig.installer.contracts import ModuleState

        ConfigManager(config_dir=isolated_install).set("cloud.enabled", True)
        reset_config_manager()
        results = run_install(profile="server", quiet=True)

        assert ConfigManager(config_dir=isolated_install).get("cloud.enabled") is True
        by_id = {r.action_id: r for r in results}
        assert by_id["profile.config.cloud.enabled"].state == ModuleState.SKIPPED

    def test_config_defaults_must_be_dotted_scalars(self):
        errors = validate_profile_manifest(
            "x", {**INSTALL_PROFILES["server"], "config": {"cloud.": False, "a.b": {"nested": 1}}}
        )
        assert any("dotted name" in e for e in errors)
        assert any("scalar default" in e for e in errors)

    def test_a_failed_step_makes_init_exit_nonzero(self, isolated_install, monkeypatch):
        """install.sh prints "profile server applied" whenever `navig init --profile`
        exits 0 — it did so while the service registration had FAILED."""
        _stub_unsafe_modules(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("systemctl --user: no user bus")

        monkeypatch.setattr("navig.commands.service.service_install", _boom)
        import pytest as _pytest

        from navig.commands.init import run_init_command

        with _pytest.raises(SystemExit) as exc:
            run_init_command(None, profile="server", quiet=True)
        assert exc.value.code == 1
