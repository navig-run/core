"""
navig.memory._util — Shared utility functions for the memory subpackage.

Consolidates the ``_debug_log`` helper that was previously copy-pasted
across 9+ modules in this package.
"""

from __future__ import annotations

import logging
from pathlib import Path

from navig.core.json_io import safe_json_loads as _safe_json_loads_impl
from navig.core.yaml_io import atomic_write_text as _atomic_write_text_impl

_logger = logging.getLogger("navig.memory")


# The blob-parsing primitive now lives with the other JSON primitives in
# navig.core.json_io (the stores outside memory/ need it too). Re-exported here so the
# memory modules and their tests keep their existing import site — ONE implementation.
safe_json_loads = _safe_json_loads_impl


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to *path* atomically.  Delegates to the canonical
    :func:`navig.core.yaml_io.atomic_write_text` implementation."""
    _atomic_write_text_impl(path, content)


def _debug_log(message: str) -> None:
    """Best-effort debug logging for memory operations.

    Uses the standard ``navig.memory`` logger at DEBUG level.  Never
    raises — logging failures must not interrupt memory operations.
    """
    try:
        _logger.debug(message)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
