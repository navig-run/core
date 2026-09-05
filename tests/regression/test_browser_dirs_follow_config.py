"""Browser screenshot / profile dirs must follow NAVIG_CONFIG_DIR, not a literal ~/.navig.

Every one of these defaulted to a hardcoded ``"~/.navig/..."`` string. A default install is
unaffected either way -- ``paths.config_dir()`` *is* ``~/.navig`` there -- which is exactly
why they survived: they look correct on the only machine anyone tests on. An install that
moved its config got a split brain: config in one place, screenshots and browser profiles
in the real home.

It reached into the operator's home **from the test suite** too, and not passively:
``BrowserController.__init__`` and ``StealthBrowser.__init__`` ``mkdir`` these paths, so
merely constructing one created directories in their live ``~/.navig`` under a fully
isolated test config. Measured with an audit of every write under the real home during a
full run: 96 mkdirs, 64 of them from tests, and ``screenshots`` alone was 56 -- traced to
``controller.py:__init__`` and ``stealth.py:__init__``.

``navig/desktop/controller.py`` is the fifth site and was found only by re-running the
audit AFTER fixing the browser four: 4 stray ``screenshots`` mkdirs remained, from a
different ``controller.py``. Fixing the sites you already know about does not tell you
whether the class is closed -- re-measuring does.

Same class as the gateway's ``storage_dir`` (#1121), and the reason the
``Path.home() / ".navig"`` AST guard misses all of them is that they are string constants.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.browser.controller import BrowserConfig
from navig.browser.firefox import _default_profile as _firefox_profile
from navig.browser.stealth import StealthConfig
from navig.browser.system_chrome import _default_profile as _chrome_profile
from navig.desktop.controller import DesktopConfig

REAL_HOME = Path("~/.navig").expanduser()


def _resolved() -> dict[str, Path]:
    return {
        "controller.screenshot_dir": Path(BrowserConfig().screenshot_dir),
        "stealth.screenshot_dir": Path(StealthConfig().screenshot_dir),
        "stealth.user_data_dir": Path(StealthConfig().user_data_dir),
        "desktop.screenshot_dir": Path(DesktopConfig().screenshot_dir),
        "firefox profile": Path(_firefox_profile()),
        "system-chrome profile": Path(_chrome_profile()),
    }


def test_every_browser_dir_follows_a_custom_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression: none of them may resolve into the operator's real home."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

    stray = {
        name: path
        for name, path in _resolved().items()
        if tmp_path not in path.parents and path != tmp_path
    }
    assert not stray, (
        "browser dirs that ignore NAVIG_CONFIG_DIR -- these are mkdir'd by "
        "BrowserController/StealthBrowser __init__, so constructing one writes into the "
        "operator's real home:\n  "
        + "\n  ".join(f"{n} -> {p}" for n, p in sorted(stray.items()))
    )


def test_a_default_install_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compatibility: with no env var every path is exactly what it always was.

    Without this the fix could 'pass' by relocating everyone's screenshots and profiles.
    """
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    assert _resolved() == {
        "controller.screenshot_dir": REAL_HOME / "screenshots",
        "stealth.screenshot_dir": REAL_HOME / "screenshots",
        "stealth.user_data_dir": REAL_HOME / "browser" / "profiles" / "stealth",
        "desktop.screenshot_dir": REAL_HOME / "screenshots",
        "firefox profile": REAL_HOME / "browser" / "profiles" / "firefox",
        "system-chrome profile": REAL_HOME / "browser" / "profiles" / "system-chrome",
    }


def test_an_explicit_config_value_still_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Precedence is unchanged: a configured dir beats the derived default."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    chosen = tmp_path / "elsewhere"

    cfg = BrowserConfig.from_config({"browser": {"screenshot_dir": str(chosen)}})
    assert Path(cfg.screenshot_dir) == chosen


def test_the_default_is_resolved_per_call_not_frozen_at_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A module constant would capture whatever was set when the module first loaded.

    These modules are imported once per process and long outlive any single config, so the
    default has to be read at call time.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(first))
    assert Path(BrowserConfig().screenshot_dir) == first / "screenshots"

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(second))
    assert Path(BrowserConfig().screenshot_dir) == second / "screenshots", (
        "the default was frozen at import instead of resolved per call"
    )
