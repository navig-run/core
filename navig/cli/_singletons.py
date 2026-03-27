"""Thread-safe lazy singleton helpers for NAVIG CLI."""

from __future__ import annotations

import threading

_config_manager = None
_NO_CACHE = False
_config_manager_lock = threading.Lock()
_TunnelManager = None
_RemoteOperations = None
_AIAssistant = None
_lazy_lock = threading.Lock()


def set_no_cache(enabled: bool) -> None:
    """Toggle config-manager cache usage for the current process."""
    global _NO_CACHE, _config_manager
    _NO_CACHE = bool(enabled)
    if _NO_CACHE:
        _config_manager = None


def _get_config_manager():
    """Lazy-load and cache the ConfigManager."""
    global _config_manager
    if _config_manager is None:
        with _config_manager_lock:
            if _config_manager is None:
                from navig.config import get_config_manager

                _config_manager = get_config_manager(force_new=_NO_CACHE)
    return _config_manager


def _get_tunnel_manager():
    """Lazy load `TunnelManager`."""
    global _TunnelManager
    if _TunnelManager is None:
        with _lazy_lock:
            if _TunnelManager is None:
                from navig.tunnel import TunnelManager

                _TunnelManager = TunnelManager
    return _TunnelManager


def _get_remote_operations():
    """Lazy load `RemoteOperations`."""
    global _RemoteOperations
    if _RemoteOperations is None:
        with _lazy_lock:
            if _RemoteOperations is None:
                from navig.remote import RemoteOperations

                _RemoteOperations = RemoteOperations
    return _RemoteOperations


def _get_ai_assistant():
    """Lazy load `AIAssistant`."""
    global _AIAssistant
    if _AIAssistant is None:
        with _lazy_lock:
            if _AIAssistant is None:
                from navig.ai import AIAssistant

                _AIAssistant = AIAssistant
    return _AIAssistant
