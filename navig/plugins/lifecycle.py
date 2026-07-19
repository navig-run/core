"""
Plugin/MCP lifecycle contract — "degraded never blocks boot".

Every plugin (and every MCP server / component inside it) has an explicit state.
A broken component is **isolated** and surfaced in a structured degraded report;
it must never crash boot or take down healthy peers.

Pure data + a tracker — no I/O — so it's trivially testable and reusable by the
package loader, the MCP manager, and the gateway boot hook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    PENDING = "pending"
    HEALTHY = "healthy"
    DEGRADED = "degraded"   # loaded, but one or more non-core parts failed
    FAILED = "failed"       # a required/core part failed → the plugin is unusable
    SHUTDOWN = "shutdown"


# Component kinds a plugin can carry (CC bundle + NAVIG-native).
KINDS = (
    "command", "agent", "skill", "hook", "mcp",           # Claude Code bundle
    "persona", "formation", "space",                       # NAVIG-native
)


@dataclass
class ComponentStatus:
    name: str
    kind: str                      # one of KINDS
    state: State = State.HEALTHY
    error: str | None = None       # short, never a secret

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "state": self.state.value,
                "error": self.error}


@dataclass
class PluginHealth:
    plugin_id: str
    components: list[ComponentStatus] = field(default_factory=list)
    manifest_ok: bool = True
    error: str | None = None       # manifest/load-level error → FAILED

    @property
    def state(self) -> State:
        if not self.manifest_ok:
            return State.FAILED
        if any(c.state == State.FAILED for c in self.components):
            # a component marked FAILED (required) fails the plugin;
            # otherwise component failures only degrade it.
            return State.FAILED
        if any(c.state == State.DEGRADED for c in self.components):
            return State.DEGRADED
        return State.HEALTHY

    @property
    def is_usable(self) -> bool:
        """Degraded plugins are still usable (their healthy parts work)."""
        return self.state in (State.HEALTHY, State.DEGRADED)

    def add(self, name: str, kind: str, *, state: State = State.HEALTHY,
            error: str | None = None) -> None:
        self.components.append(ComponentStatus(name=name, kind=kind, state=state, error=error))

    def degraded_components(self) -> list[ComponentStatus]:
        return [c for c in self.components if c.state in (State.DEGRADED, State.FAILED)]

    def to_dict(self) -> dict:
        return {
            "plugin_id": self.plugin_id,
            "state": self.state.value,
            "usable": self.is_usable,
            "error": self.error,
            "components": [c.to_dict() for c in self.components],
        }


class LifecycleTracker:
    """Aggregates plugin/MCP health across boot. `report()` is the structured
    degraded report; nothing here ever raises."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginHealth] = {}

    def track(self, health: PluginHealth) -> None:
        self._plugins[health.plugin_id] = health

    def get(self, plugin_id: str) -> PluginHealth | None:
        return self._plugins.get(plugin_id)

    def healthy(self) -> list[PluginHealth]:
        return [h for h in self._plugins.values() if h.state == State.HEALTHY]

    def degraded(self) -> list[PluginHealth]:
        return [h for h in self._plugins.values() if h.state == State.DEGRADED]

    def failed(self) -> list[PluginHealth]:
        return [h for h in self._plugins.values() if h.state == State.FAILED]

    def report(self) -> dict:
        """Machine-readable boot report — safe to log/serve; no secrets."""
        return {
            "total": len(self._plugins),
            "healthy": len(self.healthy()),
            "degraded": len(self.degraded()),
            "failed": len(self.failed()),
            "plugins": [h.to_dict() for h in self._plugins.values()],
        }

    def summary_line(self) -> str:
        return (f"plugins: {len(self.healthy())} healthy, "
                f"{len(self.degraded())} degraded, {len(self.failed())} failed")
