"""``navig.browser.<module>`` IS ``navig_browser.<module>`` — one module object, never two.

The browser engine moved into navig-browser and core kept one identity-alias shim per module.
The failure mode this guards is a module loaded TWICE, once under each name: two CDP event
loops, two session managers, two sets of module-level state — and a test that patches
``navig.browser.targets.X`` would patch a copy the engine never reads. It would not fail
loudly; a leaked browser or a missed teardown would be the first symptom.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

navig_browser = pytest.importorskip("navig_browser")

CORE_BROWSER = Path(__file__).resolve().parents[2] / "navig" / "browser"
_NEW_ONLY = {"navig_browser._compat", "navig_browser.cli", "navig_browser.commands"}


def _engine_modules() -> list[str]:
    return sorted(
        m.name
        for m in pkgutil.walk_packages(navig_browser.__path__, "navig_browser.")
        if not m.ispkg and m.name not in _NEW_ONLY and not m.name.startswith("navig_browser.commands.")
    )


def test_discovery_found_the_engine():
    mods = _engine_modules()
    assert len(mods) >= 30, mods  # anti-vacuity: an empty walk would pass everything below
    assert "navig_browser.targets" in mods and "navig_browser.cdp_actions" in mods


@pytest.mark.parametrize("new", _engine_modules())
def test_each_old_path_is_the_same_module(new: str):
    old = "navig.browser." + new.split(".", 1)[1]
    assert importlib.import_module(old) is importlib.import_module(new)


def test_every_engine_module_has_a_core_shim():
    """A module added to navig-browser without a shim would import under one name only."""
    missing = [
        new for new in _engine_modules()
        if not (CORE_BROWSER / Path(*new.split(".")[1:])).with_suffix(".py").is_file()
    ]
    assert not missing, f"add a navig/browser/ shim for: {missing}"


def test_the_cdp_command_is_the_same_module():
    assert importlib.import_module("navig.commands.cdp") is importlib.import_module(
        "navig_browser.commands.cdp"
    )
