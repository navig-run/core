"""Root-level pytest configuration.

Ensures the basetemp directory (``.local/.pytest_tmp``) exists before
collection starts so ``--basetemp`` in ``pytest.ini`` never fails on a
fresh clone (fixes #34).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_BUILD_LIB = (_PROJECT_ROOT / "build" / "lib").resolve()


def _ensure_repo_import_priority() -> None:
    """Keep the repo root ahead of any ``build/lib`` on ``sys.path``.

    Cheap enough to run before every test and every collection step: it only
    rewrites the small ``sys.path`` list, so a test that mutates ``sys.path`` is
    corrected on the next hook — without the O(sys.modules) filesystem scan that
    ``_evict_build_lib_modules`` performs.
    """
    cleaned: list[str] = []
    for entry in sys.path:
        try:
            resolved = Path(entry).resolve()
        except Exception:  # noqa: BLE001
            cleaned.append(entry)
            continue
        if resolved == _BUILD_LIB:
            continue
        cleaned.append(entry)

    sys.path[:] = cleaned
    root_str = str(_PROJECT_ROOT)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)


def _evict_build_lib_modules() -> None:
    """Drop any ``navig`` module that was imported from ``build/lib``.

    This ``Path.resolve()``-per-navig-module scan is expensive — ~50-170 ms once
    the session has imported hundreds of navig modules. It only needs to run
    ONCE, at session start: ``_ensure_repo_import_priority`` keeps ``build/lib``
    off ``sys.path`` for the whole run, so no ``build/lib`` module can be
    (re)imported mid-session. Running it in ``pytest_runtest_setup`` (per test)
    and ``pytest_collectstart`` (per module) was pure overhead and dominated
    suite setup time. No-op when there is no ``build/lib`` tree to shadow.
    """
    if not _BUILD_LIB.exists():
        return
    stale_modules: list[str] = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("navig"):
            continue
        module_file = getattr(module, "__file__", None)
        # Cheap prefilter: only a module physically under build/lib can be stale.
        # Skipping the slow Path.resolve() (Windows realpath) for every other navig
        # module — this hook runs on *every* collect step, so resolving all of
        # sys.modules each time was O(nodes × modules) and made collection hang.
        if not module_file or "build" not in module_file.lower():
            continue
        try:
            resolved_file = Path(module_file).resolve()
        except Exception:  # noqa: BLE001
            continue
        if _BUILD_LIB in resolved_file.parents:
            stale_modules.append(module_name)

    for module_name in stale_modules:
        sys.modules.pop(module_name, None)


def _normalize_import_path() -> None:
    """Full normalization: repo import priority + one-time ``build/lib`` eviction."""
    _ensure_repo_import_priority()
    _evict_build_lib_modules()


def pytest_sessionstart(session):  # noqa: ARG001
    """Create the basetemp parent directory if it doesn't exist yet."""
    Path(".local/.pytest_tmp").mkdir(parents=True, exist_ok=True)
    _normalize_import_path()


def pytest_runtest_setup(item):  # noqa: ARG001
    """Re-apply the cheap sys.path priority in case a test mutated ``sys.path``."""
    _ensure_repo_import_priority()
    if item.nodeid.startswith("tests/test_provider_control_surface.py"):
        sys.modules.pop("navig.gateway.channels.telegram_keyboards", None)


def pytest_collectstart(collector):  # noqa: ARG001
    """Keep repo import priority before each collection step/module import."""
    _ensure_repo_import_priority()
