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


def _normalize_import_path() -> None:
    """Keep repo source import priority stable during pytest runs."""
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

    stale_modules: list[str] = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("navig"):
            continue
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            resolved_file = Path(module_file).resolve()
        except Exception:  # noqa: BLE001
            continue
        if _BUILD_LIB in resolved_file.parents:
            stale_modules.append(module_name)

    for module_name in stale_modules:
        sys.modules.pop(module_name, None)


def pytest_sessionstart(session):  # noqa: ARG001
    """Create the basetemp parent directory if it doesn't exist yet."""
    Path(".local/.pytest_tmp").mkdir(parents=True, exist_ok=True)
    _normalize_import_path()


def pytest_runtest_setup(item):  # noqa: ARG001
    """Re-apply path normalization in case individual tests mutate sys.path."""
    _normalize_import_path()
    if item.nodeid.startswith("tests/test_provider_control_surface.py"):
        sys.modules.pop("navig.gateway.channels.telegram_keyboards", None)


def pytest_collectstart(collector):  # noqa: ARG001
    """Normalize imports before each collection step/module import."""
    _normalize_import_path()
