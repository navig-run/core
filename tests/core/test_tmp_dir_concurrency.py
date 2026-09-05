"""Concurrent runs of this suite must not delete each other's temp directories.

``pytest.ini`` used to pin ``--basetemp=.dev/tmp/pytest``. ``--basetemp`` WIPES AND RECREATES the
directory it points at (its own comment said so), and several agent sessions run this suite in the
same checkout — so starting a run deleted the temp dirs of the runs already in flight. On Windows
that surfaced as a session-start crash, before a single test executed:

    INTERNALERROR> PermissionError: [WinError 32] The process cannot access the file because it is
    being used by another process: '...\\.dev\\tmp\\pytest\\popen-gw12\\...\\task_1.log'

And because the pre-push gate runs this suite, it turned every concurrent push into a false
failure.

The fix is to let pytest manage temp dirs: ``conftest.py`` points ``PYTEST_DEBUG_TEMPROOT`` at
``core/.dev/tmp`` and pytest allocates a numbered, lock-guarded ``pytest-<n>`` per RUN underneath.
These tests pin both halves — no shared fixed basetemp, and the root still inside ``core/`` so the
``.git``/``.navig`` ancestor contract in ``test_tmp_dir_repo_boundary.py`` keeps holding.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]


def _addopts() -> str:
    parser = configparser.ConfigParser()
    parser.read(CORE / "pytest.ini", encoding="utf-8")
    return parser.get("pytest", "addopts", fallback="")


def test_no_fixed_basetemp_is_pinned() -> None:
    """A fixed --basetemp is shared by every concurrent run, and it is destructive."""
    assert "--basetemp" not in _addopts(), (
        "--basetemp WIPES AND RECREATES its directory, so a fixed path lets one run delete the "
        "temp dirs of every other run in this checkout (see this module's docstring)."
    )


def test_temp_root_is_configured_under_core_dev_tmp() -> None:
    """Importing conftest must have pointed pytest's temp root into core/.dev/tmp."""
    root = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    assert root, "conftest.py should set PYTEST_DEBUG_TEMPROOT"
    assert Path(root).resolve() == (CORE / ".dev" / "tmp").resolve()


def test_tmp_path_lives_under_that_root(tmp_path: Path) -> None:
    """The allocated directory is a per-run numbered dir beneath the configured root."""
    root = Path(os.environ["PYTEST_DEBUG_TEMPROOT"]).resolve()
    resolved = tmp_path.resolve()
    assert root in resolved.parents
    # pytest's own scheme: <root>/pytest-of-<user>/pytest-<n>/<test>/
    assert any(p.name.startswith("pytest-") for p in resolved.parents)


def test_run_directory_is_numbered_so_runs_do_not_share_one(tmp_path: Path) -> None:
    """The run directory carries a number — that is what makes two runs distinct."""
    run_dir = next(p for p in tmp_path.resolve().parents if p.name.startswith("pytest-")
                   and p.name != "pytest-of-" + (os.environ.get("USER") or ""))
    suffix = run_dir.name.rsplit("-", 1)[-1]
    assert suffix.isdigit(), f"expected a numbered run dir, got {run_dir.name!r}"
