"""Target resolution for navig update.

Resolves user-supplied --host / --group / --all flags into a concrete
ordered list of UpdateTarget objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UpdateTarget:
    """Represents a single node to update."""

    node_id: str  # "local" or hostname
    type: str = "local"  # "local" | "ssh"
    server_config: dict | None = field(default=None, repr=False)

    @property
    def is_local(self) -> bool:
        return self.type == "local"

    @property
    def label(self) -> str:
        return self.node_id


class TargetResolver:
    """Resolves --host / --group / --all into UpdateTarget list."""

    def __init__(self, config_manager: Any = None):
        if config_manager is None:
            from navig.config import get_config_manager

            config_manager = get_config_manager()
        self._cm = config_manager

    # ------------------------------------------------------------------
    def resolve(
        self,
        host: str | None = None,
        group: str | None = None,
        all_hosts: bool = False,
    ) -> list[UpdateTarget]:
        """Return ordered list of targets.

        Priority: all_hosts > group > host > default(local).
        """
        if all_hosts:
            return self._all_targets()

        if group:
            return self._group_targets(group)

        if host:
            return self._host_targets(host)

        # Default: local only
        return [UpdateTarget(node_id="local", type="local")]

    # ------------------------------------------------------------------
    def _all_targets(self) -> list[UpdateTarget]:
        targets: list[UpdateTarget] = [UpdateTarget(node_id="local", type="local")]
        try:
            host_names = self._cm.list_hosts() or []
        except Exception:
            host_names = []
        for name in host_names:
            cfg = self._load_host(name)
            if cfg:
                targets.append(UpdateTarget(node_id=name, type="ssh", server_config=cfg))
        return targets

    def _group_targets(self, group: str) -> list[UpdateTarget]:
        try:
            host_names = self._cm.get_group_hosts(group)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

        targets: list[UpdateTarget] = []
        for name in host_names:
            if name in ("local", "localhost"):
                targets.append(UpdateTarget(node_id="local", type="local"))
                continue
            cfg = self._load_host(name)
            if cfg:
                targets.append(UpdateTarget(node_id=name, type="ssh", server_config=cfg))
        return targets

    def _host_targets(self, host: str) -> list[UpdateTarget]:
        if host in ("local", "localhost"):
            return [UpdateTarget(node_id="local", type="local")]
        cfg = self._load_host(host)
        if not cfg:
            raise ValueError(
                f"Host '{host}' not found in NAVIG config. "
                "Run 'navig host list' to see available hosts."
            )
        return [UpdateTarget(node_id=host, type="ssh", server_config=cfg)]

    def _load_host(self, name: str) -> dict | None:
        try:
            return self._cm.load_host_config(name)
        except Exception:
            return None
