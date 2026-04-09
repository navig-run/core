"""
navig.memory._util — Shared utility functions for the memory subpackage.

Consolidates the ``_debug_log`` helper that was previously copy-pasted
across 9+ modules in this package.
"""

from __future__ import annotations


def _debug_log(message: str) -> None:
    """Best-effort debug logging for memory operations.

    Creates a :class:`~navig.debug_logger.DebugLogger` lazily and logs
    under the ``"memory"`` category.  Never raises — logging failures
    must not interrupt memory operations.
    """
    try:
        from navig.debug_logger import DebugLogger

        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
