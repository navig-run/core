"""Isolate the module-registry override state between tests.

`ModuleRegistry` persists enable/disable overrides to the global config
(`modules.overrides`), which is shared across a test session's NAVIG_DATA_DIR.
Reset it before every registry test so persisted overrides don't bleed.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_module_overrides(request):
    # Only the platform registry tests mutate `modules.overrides`; scope the
    # reset to them. NOTE: this is a *generator* fixture — every path must reach
    # `yield`. An early bare `return` makes it "not yield a value", which pytest
    # (and pytest-asyncio under asyncio_mode=auto) errors on for EVERY test whose
    # nodeid lacks the "test_registry" substring — a trap that only stayed hidden
    # because the existing tests here all happen to match it.
    if "test_registry" in request.node.nodeid:
        try:
            from navig.core import Config

            Config().set("modules.overrides", {}, scope="global")
        except Exception:  # noqa: BLE001
            pass
    yield
