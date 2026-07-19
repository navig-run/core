"""Unit tests for navig.platform.paths — cross-platform path resolution.

Strategy:
  - Env-override tests: set NAVIG_*_DIR env vars; verify the override is honored.
  - Derived-path tests: verify sub-paths are expressed relative to parent functions.
  - Return-type tests: every public path function returns a Path.
  - OS-detection tests: monkeypatch sys.platform + reset the module cache.

All tests are hermetic: no filesystem mutation, no network, no subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.platform.paths as paths_mod
from navig.platform.paths import (
    blackbox_dir,
    cache_dir,
    config_dir,
    current_os,
    data_dir,
    debug_log_path,
    is_linux,
    is_macos,
    is_unix,
    is_windows,
    is_wsl,
    log_dir,
    workspace_dir,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_os_cache() -> None:
    """Reset the module-level OS detection cache so tests stay independent."""
    paths_mod._DETECTED_OS = None


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------


class TestCurrentOs:
    def teardown_method(self) -> None:  # noqa: ANN201
        _reset_os_cache()

    def test_current_os_returns_string(self) -> None:
        assert isinstance(current_os(), str)

    def test_current_os_is_known_value(self) -> None:
        assert current_os() in {"windows", "linux", "macos", "wsl"}

    def test_current_os_cached(self) -> None:
        a = current_os()
        b = current_os()
        assert a == b
        assert paths_mod._DETECTED_OS is not None

    def test_windows_detection(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "win32"):
            result = current_os()
        assert result == "windows"
        _reset_os_cache()

    def test_macos_detection(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "darwin"):
            result = current_os()
        assert result == "macos"
        _reset_os_cache()

    def test_unknown_platform_falls_back_to_linux(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "freebsd13"):
            result = current_os()
        assert result == "linux"
        _reset_os_cache()


class TestOsPredicates:
    def teardown_method(self) -> None:  # noqa: ANN201
        _reset_os_cache()

    def test_exactly_one_os_predicate_true(self) -> None:
        # Exactly one BASE OS is true. is_wsl() is a modifier, not a 4th OS — on WSL
        # both is_wsl() and is_linux() are (correctly) true, so counting is_wsl() as a
        # separate predicate wrongly yields 2. WSL is a Linux variant.
        base = [is_windows(), is_linux(), is_macos()]
        assert base.count(True) == 1
        if is_wsl():
            assert is_linux()

    def test_is_unix_false_on_windows(self) -> None:
        _reset_os_cache()
        with patch.object(sys, "platform", "win32"):
            paths_mod._DETECTED_OS = None
            result = is_unix()
        _reset_os_cache()
        assert result is False

    def test_is_unix_true_on_linux(self) -> None:
        paths_mod._DETECTED_OS = "linux"
        result = is_unix()
        _reset_os_cache()
        assert result is True

    def test_is_unix_true_on_macos(self) -> None:
        paths_mod._DETECTED_OS = "macos"
        result = is_unix()
        _reset_os_cache()
        assert result is True


# ---------------------------------------------------------------------------
# config_dir — env override
# ---------------------------------------------------------------------------


class TestConfigDir:
    def test_returns_path(self) -> None:
        assert isinstance(config_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        assert config_dir() == tmp_path

    def test_env_override_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
        result = config_dir()
        assert isinstance(result, Path)
        assert "navig" in result.name.lower()

    def test_non_empty_path(self) -> None:
        assert len(str(config_dir())) > 0


# ---------------------------------------------------------------------------
# data_dir — env override + derived path
# ---------------------------------------------------------------------------


class TestDataDir:
    def test_returns_path(self) -> None:
        assert isinstance(data_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
        assert data_dir() == tmp_path

    def test_default_is_under_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
        monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        result = data_dir()
        cfg = config_dir()
        assert str(result).startswith(str(cfg))


# ---------------------------------------------------------------------------
# log_dir — env override
# ---------------------------------------------------------------------------


class TestLogDir:
    def test_returns_path(self) -> None:
        assert isinstance(log_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path))
        assert log_dir() == tmp_path

    def test_env_override_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        result = log_dir()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# Derived path helpers
# ---------------------------------------------------------------------------


class TestDerivedPaths:
    def test_workspace_dir_under_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
        assert workspace_dir() == config_dir() / "workspace"

    def test_blackbox_dir_under_data_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
        assert blackbox_dir() == data_dir() / "blackbox"

    def test_debug_log_path_under_log_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        assert debug_log_path() == log_dir() / "debug.log"

    def test_debug_log_path_is_log_file(self) -> None:
        assert debug_log_path().name == "debug.log"

    def test_workspace_dir_returns_path(self) -> None:
        assert isinstance(workspace_dir(), Path)

    def test_blackbox_dir_returns_path(self) -> None:
        assert isinstance(blackbox_dir(), Path)

    def test_debug_log_path_returns_path(self) -> None:
        assert isinstance(debug_log_path(), Path)


# ---------------------------------------------------------------------------
# cache_dir — env override
# ---------------------------------------------------------------------------


class TestCacheDir:
    def test_returns_path(self) -> None:
        assert isinstance(cache_dir(), Path)

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NAVIG_CACHE_DIR", str(tmp_path))
        assert cache_dir() == tmp_path

    def test_env_override_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
        result = cache_dir()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# OS-specific log / cache paths (set _DETECTED_OS directly — no cache to reset)
# ---------------------------------------------------------------------------


class TestOsSpecificPaths:
    def setup_method(self) -> None:
        _reset_os_cache()

    def teardown_method(self) -> None:
        _reset_os_cache()

    def test_windows_log_dir_uses_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "windows"
        result = log_dir()
        assert "navig" in str(result).lower()
        assert "logs" in str(result).lower()

    def test_macos_log_dir_uses_library_logs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "macos"
        result = log_dir()
        assert "Library" in str(result)
        assert "Logs" in str(result)

    def test_linux_log_dir_fallback_uses_local_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "linux"
        result = log_dir()
        assert ".local" in str(result)
        assert "navig" in str(result).lower()

    def test_linux_log_dir_xdg_state_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NAVIG_LOG_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "linux"
        result = log_dir()
        assert str(result).startswith(str(tmp_path))

    def test_windows_cache_dir_uses_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "windows"
        result = cache_dir()
        assert "navig" in str(result).lower()
        assert "cache" in str(result).lower()

    def test_macos_cache_dir_uses_library_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
        monkeypatch.setenv("NAVIG_SERVICE_MODE", "0")
        paths_mod._DETECTED_OS = "macos"
        result = cache_dir()
        assert "Library" in str(result)
        assert "Caches" in str(result)


class TestBuiltinStoreShips:
    """The built-in content store must live INSIDE the navig package.

    REGRESSION: it used to sit at ``<repo>/core/store`` and was listed only in
    MANIFEST.in. MANIFEST.in reaches the *sdist*; setuptools' ``package-data`` cannot
    reach a directory outside the package, so the content was dropped when the wheel
    was built. Every published wheel (verified on PyPI navig 2.8.0) therefore shipped
    with **zero** builtin skills / prompts / templates / formations / agents / tools —
    while a dev checkout worked fine, which is why it went unnoticed.

    If these fail, the wheel is silently shipping an empty product again.
    """

    def test_builtin_store_is_inside_the_navig_package(self) -> None:
        import navig
        from navig.platform.paths import builtin_store_dir

        pkg_root = Path(navig.__file__).resolve().parent
        store = builtin_store_dir().resolve()
        assert store.is_relative_to(pkg_root), (
            f"builtin store {store} is OUTSIDE the navig package ({pkg_root}) — "
            "setuptools package-data cannot reach it, so it will NOT ship in the wheel"
        )

    def test_builtin_store_exists_and_has_content(self) -> None:
        from navig.platform.paths import builtin_store_dir

        store = builtin_store_dir()
        assert store.is_dir(), f"builtin store missing at {store}"
        # The content the loaders actually read. An empty dir here = an empty product.
        for sub in ("skills", "prompts", "templates", "formations", "agents", "tools"):
            d = store / sub
            assert d.is_dir(), f"builtin store is missing {sub}/"
            assert any(d.rglob("*.*")), f"builtin store {sub}/ is empty"

    def test_builtin_store_is_not_the_user_store(self) -> None:
        """Read-only builtin content vs. the writable user store are different dirs."""
        from navig.platform.paths import builtin_store_dir, store_dir

        assert builtin_store_dir().resolve() != store_dir().resolve()

    def test_package_data_actually_covers_the_builtin_tree(self) -> None:
        """The declaration that actually puts the content in the wheel. Without it, every
        other test here still passes while the wheel ships empty.

        This checks real glob COVERAGE, not the presence of a literal pattern string: the
        original version asserted `"builtin/**/*" in pyproject`, and broke the moment the
        globs were (correctly) generalised to `**/*`, which covers strictly more. Assert the
        outcome, never the spelling.
        """
        import glob as globlib

        import tomllib

        import navig
        from navig.platform.paths import builtin_store_dir

        pkg = Path(navig.__file__).resolve().parent
        cfg = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = cfg["tool"]["setuptools"]["package-data"].get("navig", [])

        shipped: set[str] = set()
        for pat in patterns:
            for m in globlib.glob(pat, root_dir=pkg, recursive=True, include_hidden=True):
                if (pkg / m).is_file():
                    shipped.add((pkg / m).relative_to(pkg).as_posix())

        content = [
            f.relative_to(pkg).as_posix()
            for f in (builtin_store_dir()).rglob("*")
            if f.is_file() and "__pycache__" not in f.parts
        ]
        assert content, "the builtin store has no content at all"
        uncovered = [c for c in content if c not in shipped]
        assert not uncovered, (
            f"{len(uncovered)} builtin-store file(s) are NOT covered by "
            f"[tool.setuptools.package-data] — the wheel would ship without them, e.g. "
            f"{uncovered[:3]}"
        )


class TestRuntimeAssetsResolveInsidePackage:
    """Every asset a command loads at runtime must resolve INSIDE the navig package.

    An asset path built by counting `.parent`s out of a module (…/core/store/skills,
    …/core/scripts/speedtest/worker.py) escapes the package. setuptools cannot ship it,
    so it exists in a dev checkout and is simply absent from every wheel — and each of
    these degraded silently rather than failing loudly:

      * `commands/skills.py` walked out to <repo>/core/store/skills → a pip-installed
        navig listed ZERO builtin skills;
      * `adapters/automation/ahk.py` walked out to <repo>/core/store/templates/ahk →
        no AHK primitives/workflows;
      * `commands/net.py` importlib-loaded <repo>/core/scripts/speedtest/worker.py,
        which was in neither the wheel nor the sdist → `navig net speedtest` raised.

    These assert the resolved paths stay inside the package, so they ship.
    """

    def _pkg_root(self) -> Path:
        import navig

        return Path(navig.__file__).resolve().parent

    def test_builtin_skills_are_found_via_the_packaged_store(self) -> None:
        from navig.commands.skills import _resolve_skills_dirs
        from navig.platform.paths import builtin_store_dir

        dirs = [d.resolve() for d in _resolve_skills_dirs(None)]
        assert (builtin_store_dir() / "skills").resolve() in dirs, (
            "the canonical builtin skills store is not among the resolved skill dirs"
        )
        for d in dirs:
            assert d.is_relative_to(self._pkg_root()), f"{d} escapes the navig package"

    def test_ahk_templates_resolve_inside_the_package(self) -> None:
        from navig.platform.paths import builtin_store_dir

        templates = (builtin_store_dir() / "templates" / "ahk").resolve()
        assert templates.is_relative_to(self._pkg_root())
        assert templates.is_dir(), "AHK templates missing from the builtin store"
        assert (templates / "primitives").is_dir()

    def test_speedtest_worker_ships_and_actually_loads(self) -> None:
        """Not just 'the path exists' — import it, which is what `navig net speedtest` does."""
        from navig.commands.net import _backend
        from navig.platform.paths import builtin_store_dir

        worker = (builtin_store_dir() / "tools" / "speedtest" / "worker.py").resolve()
        assert worker.is_relative_to(self._pkg_root())
        mod = _backend()
        assert hasattr(mod, "run_speedtest_cli"), "speedtest worker loaded but has no entrypoint"

    def test_builtin_templates_resolve_inside_the_package(self) -> None:
        from navig.template_manager import TemplateManager

        tm = TemplateManager()
        assert Path(tm.templates_dir).resolve().is_relative_to(self._pkg_root())
