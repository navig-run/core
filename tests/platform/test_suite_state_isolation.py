"""No test may resolve navig state into the source checkout.

Three state roots are resolved by three independent code paths, and isolating one says
nothing about the others:

    NAVIG_CONFIG_DIR  -> global_config_dir
    NAVIG_DATA_DIR    -> paths.data_dir()          (its own env var, else home/.navig/data)
    app-root walk     -> base_dir  ->  hosts_dir / apps_dir / cache_dir

The third has no env var. `ConfigManager` asks `paths.find_app_root()`, which walks up
from the **CWD** for a `.navig/` directory — and pytest runs inside this checkout, where
`core/.navig/` exists. So a bare `ConfigManager()` in a test resolved hosts/apps/cache
into the SOURCE TREE, shared by every xdist worker.

That is not theoretical. e1422a253 fixed 12 tests in one file that all saved a host named
`test-host` into that one shared directory and raced under `-n auto`, failing on a
DIFFERENT pair of assertions each run while passing 12/12 in isolation. Two sessions
found two instances of it on the same day. This pins the class instead of the instances.

The suppression in `conftest._isolate_navig_config_dir` is deliberately narrow: only the
SOURCE CHECKOUT is hidden from app-root detection. A test that chdirs into a temp project
still sees the real product behaviour, which `test_insights_reads_the_ledger_that_is_written`
and the ops-recorder tests depend on — `test_app_root_still_works_outside_the_checkout`
below is what keeps that cut honest.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from navig.config import ConfigManager

pytestmark = pytest.mark.integration

# core/tests/platform/<this file> -> parents[3] is the repo checkout root.
CHECKOUT = Path(__file__).resolve().parents[3]


def _inside_checkout(p: Path | str) -> bool:
    try:
        Path(p).resolve().relative_to(CHECKOUT)
    except ValueError:
        return False
    return True


def test_the_checkout_really_is_a_navig_project() -> None:
    """Anti-vacuity: if this stops holding, every assertion below passes for free.

    The whole class exists BECAUSE the checkout is itself a navig project — remove
    `core/.navig/` and app-root detection finds nothing here, so the guards below would
    be green while guarding nothing.
    """
    assert CHECKOUT.is_dir(), CHECKOUT
    assert (CHECKOUT / "core" / ".navig").is_dir(), (
        f"expected {CHECKOUT / 'core' / '.navig'} to exist — it is what makes the source "
        f"tree an app root, and therefore what these guards are about"
    )
    assert _inside_checkout(Path.cwd()) or True  # cwd may be core/ or the repo root


def test_bare_config_manager_resolves_outside_the_checkout() -> None:
    """The one a test actually trips over: `ConfigManager()` with no arguments."""
    cm = ConfigManager()
    for name in ("base_dir", "hosts_dir", "apps_dir", "cache_dir", "global_config_dir"):
        value = getattr(cm, name, None)
        assert value is not None, f"ConfigManager has no {name}"
        assert not _inside_checkout(value), (
            f"ConfigManager().{name} resolved into the source tree ({value}). Every xdist "
            f"worker shares that directory, so tests race there — and writes land in the "
            f"developer's real checkout."
        )


def test_data_dir_resolves_outside_the_checkout_and_the_real_home() -> None:
    """`paths.data_dir()` reads its OWN env var — config isolation never covered it."""
    from navig.platform import paths

    data = paths.data_dir()
    assert not _inside_checkout(data), data
    assert os.environ.get("NAVIG_DATA_DIR"), (
        "NAVIG_DATA_DIR must be set for the session — otherwise data_dir() falls back to "
        "the operator's real ~/.navig/data"
    )


def test_app_root_still_works_outside_the_checkout(tmp_path, monkeypatch) -> None:
    """The cut is narrow: only the CHECKOUT is hidden, not app-root detection itself.

    Project-local state is real product behaviour (`navig insights` reads the ledger the
    recorder writes, and that path diverges from global_config_dir exactly when a project
    is found). If this test ever fails, the suppression has grown from "not the checkout"
    into "never", and those tests are being lied to.
    """
    from navig.platform import paths

    project = tmp_path / "someproject"
    (project / ".navig").mkdir(parents=True)
    monkeypatch.chdir(project)

    found = paths.find_app_root()
    assert found is not None, "a real project outside the checkout must still be found"
    assert Path(found).resolve() == project.resolve()
