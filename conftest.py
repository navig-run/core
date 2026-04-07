"""Root-level pytest configuration.

Ensures the basetemp directory (``.local/.pytest_tmp``) exists before
collection starts so ``--basetemp`` in ``pytest.ini`` never fails on a
fresh clone (fixes #34).
"""

from __future__ import annotations

from pathlib import Path


def pytest_sessionstart(session):  # noqa: ARG001
    """Create the basetemp parent directory if it doesn't exist yet."""
    Path(".local/.pytest_tmp").mkdir(parents=True, exist_ok=True)
