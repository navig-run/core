"""Guards against a stubbed `playwright` leaking into the real `sys.modules`.

`test_browser_controller_maintenance_docker_cli.py` installs a MagicMock `playwright`
stub at COLLECTION time so `navig/browser/controller.py` imports cleanly on machines
without playwright. Its original guard asked *"is playwright in sys.modules?"* — i.e.
"has it been imported yet?" — not *"is it installed?"*. At collection time nothing has
imported it yet, so on a machine that DOES have playwright the stub was installed anyway
and never removed, shadowing the real package for the entire worker process.

camoufox imports playwright, so `tests/browser/test_firefox.py` then died with
ImportError — but only when the polluting module happened to be imported first in a
worker. All 5 of those tests passed in isolation and failed in the full xdist run. Eleven
of the suite's thirteen failures were this class of cross-test poisoning, not real bugs.

This pins the invariant: if playwright is installed, the module in `sys.modules` must be
the REAL one.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

_HAVE_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


_POLLUTER = "tests.browser.test_browser_controller_maintenance_docker_cli"


def _import_polluter_first() -> None:
    """Import the stub-installing module BEFORE playwright, exactly as a worker would.

    Deliberately `importlib.import_module` and not a plain `import` statement: isort would
    hoist the import above playwright's and silently NEUTER these tests — the old stub
    no-ops once playwright is already in sys.modules, so the bug would sail through. (Ruff
    did exactly that to the first draft of this file.)
    """
    importlib.import_module(_POLLUTER)


@pytest.mark.skipif(not _HAVE_PLAYWRIGHT, reason="playwright not installed — stub is correct here")
def test_real_playwright_is_not_shadowed_by_a_stub() -> None:
    """A stub must never shadow an installed playwright."""
    _import_polluter_first()

    mod = importlib.import_module("playwright")
    assert getattr(mod, "__file__", None), (
        "sys.modules['playwright'] has no __file__ — it is a synthetic stub shadowing the "
        "real, installed package. A test stub must only be installed when playwright is "
        "ABSENT; see _stub_playwright()."
    )
    assert sys.modules["playwright"] is mod
    # The real package exposes a genuine async_playwright factory, not a MagicMock.
    async_api = importlib.import_module("playwright.async_api")
    assert callable(async_api.async_playwright)


@pytest.mark.skipif(not _HAVE_PLAYWRIGHT, reason="camoufox needs a real playwright")
def test_camoufox_still_imports_after_the_stub_module_is_loaded() -> None:
    """The concrete victim: camoufox imports playwright, so a stub broke test_firefox."""
    _import_polluter_first()

    if importlib.util.find_spec("camoufox") is None:
        pytest.skip("camoufox not installed")

    importlib.import_module("camoufox")  # must not raise ImportError
