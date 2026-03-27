"""
bridge_registry.py — Dynamic LLM provider registry for navig-core.

Allows external processes (e.g. navig-bridge VS Code extension) to register
themselves as ephemeral LLM providers at runtime.  Providers registered here
take priority over statically configured providers.

Thread-safe; uses a simple RLock around the in-memory dict.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class DynamicProvider:
    name: str
    url: str  # OpenAI-compatible base URL, e.g. http://127.0.0.1:11435/v1
    priority: int = 0  # Lower number = higher priority; 0 beats everything


class BridgeRegistry:
    """Thread-safe registry of dynamically registered LLM providers."""

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._providers: Dict[str, DynamicProvider] = {}
        self._last_bootstrap_at: float = 0.0

    # ─── Mutation ─────────────────────────────────────────────────────────────

    def register(self, name: str, url: str, priority: int = 0) -> DynamicProvider:
        provider = DynamicProvider(name=name, url=url, priority=priority)
        with self._lock:
            self._providers[name] = provider
        return provider

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._providers.pop(name, None) is not None

    # ─── Query ────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[DynamicProvider]:
        with self._lock:
            return self._providers.get(name)

    def all(self) -> list[DynamicProvider]:
        with self._lock:
            return sorted(self._providers.values(), key=lambda p: p.priority)

    def best(self) -> Optional[DynamicProvider]:
        """Return the highest-priority registered provider, or None.

        If the registry is empty, attempts a one-shot bootstrap from
        ``~/.navig/bridge-grid.json`` (written by navig-bridge) before
        returning, so navig-core can self-configure even if VS Code started
        before the gateway and never sent the ``/llm/providers/register`` POST.
        """
        self._bootstrap_from_bridge_grid()
        providers = self.all()
        return providers[0] if providers else None

    # ─── Bridge-Grid auto-bootstrap ───────────────────────────────────────────

    _BOOTSTRAP_INTERVAL: float = 10.0  # re-probe at most once per 10 s

    def _bootstrap_from_bridge_grid(self) -> bool:
        """Register the live navig-bridge bridge from bridge-grid.json if the
        registry is currently empty.  Returns True if a new provider was added.

        Thread-safe via RLock; debounced to avoid hammering the filesystem.
        """
        with self._lock:
            # Fast-path: registry already populated
            if self._providers:
                return False

            # Debounce
            now = time.monotonic()
            if (now - self._last_bootstrap_at) < self._BOOTSTRAP_INTERVAL:
                return False
            self._last_bootstrap_at = now

        # Disk I/O outside the lock
        try:
            from navig.providers.bridge_grid_reader import read_bridge_grid

            grid = read_bridge_grid()
            if not grid or not grid.get("bridge_port"):
                return False

            bridge_port = int(grid["bridge_port"])
            slot = grid.get("slot", 0)
            app = grid.get("app", "vscode")
            name = f"bridge-{app}-{slot}"
            url = f"http://127.0.0.1:{bridge_port}/v1"

            self.register(name, url, priority=0)
            import logging

            logging.getLogger(__name__).info(
                "[Bridge] Auto-registered '%s' at %s from bridge-grid.json", name, url
            )
            return True
        except Exception:
            return False

    def __len__(self) -> int:
        with self._lock:
            return len(self._providers)


# Module-level singleton
_registry: Optional[BridgeRegistry] = None
_registry_lock = threading.Lock()


def get_bridge_registry() -> BridgeRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = BridgeRegistry()
    return _registry


def reset_bridge_registry() -> None:
    """For testing only."""
    global _registry
    with _registry_lock:
        _registry = None
