"""Thread-safe lazy singleton helpers for NAVIG CLI.

NOTE: The authoritative config-manager singleton lives in navig.config
(get_config_manager / reset_config_manager).  The helpers here delegate
to that module so that reset_config_manager() clears state in exactly one
place, making test isolation reliable.

The heavy-class loaders (_get_tunnel_manager etc.) each use their own lock so
that a slow import of one class cannot serialize the import of others.
"""

from __future__ import annotations

import logging
import threading

_log = logging.getLogger(__name__)

_NO_CACHE = False

# Individual locks per heavy class — eliminates inter-class import contention.
_tunnel_lock = threading.Lock()
_remote_lock = threading.Lock()
_ai_lock = threading.Lock()

_TunnelManager = None
_RemoteOperations = None
_AIAssistant = None


def set_no_cache(enabled: bool) -> None:
    """Toggle config-manager cache bypass for the current process."""
    global _NO_CACHE
    _NO_CACHE = bool(enabled)
    if _NO_CACHE:
        try:
            from navig.config import reset_config_manager, set_config_cache_bypass

            set_config_cache_bypass(True)
            reset_config_manager()
        except Exception as _e:
            _log.debug("set_no_cache: config reset failed: %s", _e)


def _get_config_manager():
    """Return the process-wide ConfigManager (navig.config is the single source of truth)."""
    from navig.config import get_config_manager

    return get_config_manager()


def _get_tunnel_manager():
    """Lazy-load TunnelManager class (thread-safe)."""
    global _TunnelManager
    if _TunnelManager is None:
        with _tunnel_lock:
            if _TunnelManager is None:
                from navig.tunnel import TunnelManager

                _TunnelManager = TunnelManager
    return _TunnelManager


def _get_remote_operations():
    """Lazy-load RemoteOperations class (thread-safe)."""
    global _RemoteOperations
    if _RemoteOperations is None:
        with _remote_lock:
            if _RemoteOperations is None:
                from navig.remote import RemoteOperations

                _RemoteOperations = RemoteOperations
    return _RemoteOperations


def _get_ai_assistant():
    """Lazy-load AIAssistant class (thread-safe)."""
    global _AIAssistant
    if _AIAssistant is None:
        with _ai_lock:
            if _AIAssistant is None:
                from navig.ai import AIAssistant

                _AIAssistant = AIAssistant
    return _AIAssistant
