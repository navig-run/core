"""Thread-safe lazy singleton helpers for NAVIG CLI.

The authoritative config-manager singleton lives in :mod:`navig.config`
(``get_config_manager`` / ``reset_config_manager``).  The helpers here
delegate to that module so that ``reset_config_manager()`` clears state in
exactly one place, making test isolation reliable.

Each heavy-class loader uses its own lock to prevent a slow import of one
class from serialising the import of others.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_log = logging.getLogger(__name__)

_NO_CACHE = False

# Per-class locks — eliminate inter-class import contention.
_tunnel_lock = threading.Lock()
_remote_lock = threading.Lock()
_ai_lock = threading.Lock()

# Cached class references (None until first lazy load).
_TunnelManager: type | None = None
_RemoteOperations: type | None = None
_AIAssistant: type | None = None


def set_no_cache(enabled: bool) -> None:
    """Toggle config-manager cache bypass for the current process."""
    global _NO_CACHE
    _NO_CACHE = bool(enabled)
    if _NO_CACHE:
        try:
            from navig.config import reset_config_manager, set_config_cache_bypass

            set_config_cache_bypass(True)
            reset_config_manager()
        except Exception as exc:
            _log.debug("set_no_cache: config reset failed: %s", exc)


def _get_config_manager() -> Any:
    """Return the process-wide :class:`ConfigManager`.

    ``navig.config`` is the single source of truth; this function is a
    thin delegation wrapper.
    """
    from navig.config import get_config_manager

    return get_config_manager()


def _get_tunnel_manager() -> type:
    """Lazy-load and return the :class:`TunnelManager` class (thread-safe)."""
    global _TunnelManager
    if _TunnelManager is None:
        with _tunnel_lock:
            if _TunnelManager is None:
                from navig.tunnel import TunnelManager

                _TunnelManager = TunnelManager
    return _TunnelManager


def _get_remote_operations() -> type:
    """Lazy-load and return the :class:`RemoteOperations` class (thread-safe)."""
    global _RemoteOperations
    if _RemoteOperations is None:
        with _remote_lock:
            if _RemoteOperations is None:
                from navig.remote import RemoteOperations

                _RemoteOperations = RemoteOperations
    return _RemoteOperations


def _get_ai_assistant() -> type:
    """Lazy-load and return the :class:`AIAssistant` class (thread-safe)."""
    global _AIAssistant
    if _AIAssistant is None:
        with _ai_lock:
            if _AIAssistant is None:
                from navig.ai import AIAssistant

                _AIAssistant = AIAssistant
    return _AIAssistant
