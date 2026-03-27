"""
navig.deploy.adapters — Service restart adapters.

Each adapter translates a RestartConfig into one or more shell commands
executed on the remote host via RemoteOperations.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# BASE
# ============================================================================


class ServiceAdapter(abc.ABC):
    """Base class for service restart adapters."""

    name: str = ""

    def __init__(
        self, server_config: dict[str, Any], remote_ops: Any, dry_run: bool = False
    ):
        self._cfg = server_config
        self._remote = remote_ops
        self._dry_run = dry_run

    @abc.abstractmethod
    def restart_commands(self) -> list[str]:
        """Return a list of shell commands that will restart the service."""

    def restart(self) -> tuple[bool, str]:
        """
        Execute service restart on remote host.

        Returns:
            (success, detail_message)
        """
        cmds = self.restart_commands()
        if not cmds:
            return False, "No restart commands generated"

        for cmd in cmds:
            if self._dry_run:
                logger.info("[DRY RUN] restart: %s", cmd)
                continue
            result = self._remote.execute_command(cmd, self._cfg)
            if result.returncode != 0:
                return False, (result.stderr or result.stdout).strip()

        return True, " · ".join(cmds)


# ============================================================================
# ADAPTERS
# ============================================================================


class SystemdAdapter(ServiceAdapter):
    """Restart via systemctl — standard for most Linux servers."""

    name = "systemd"

    def __init__(self, service: str, **kwargs):
        super().__init__(**kwargs)
        self._service = service

    def restart_commands(self) -> list[str]:
        return [f"systemctl restart {self._service}"]


class DockerComposeAdapter(ServiceAdapter):
    """Restart via docker compose up -d — for containerised apps."""

    name = "docker-compose"

    def __init__(
        self, app_root: str, compose_file: str = "docker-compose.yml", **kwargs
    ):
        super().__init__(**kwargs)
        self._app_root = app_root
        self._compose_file = compose_file

    def restart_commands(self) -> list[str]:
        return [
            f"cd {self._app_root} && docker compose -f {self._compose_file} up -d --remove-orphans"
        ]


class Pm2Adapter(ServiceAdapter):
    """Restart via pm2 — for Node.js apps that use pm2 process manager."""

    name = "pm2"

    def __init__(self, service: str, **kwargs):
        super().__init__(**kwargs)
        self._service = service

    def restart_commands(self) -> list[str]:
        return [f"pm2 restart {self._service} --update-env"]


class CommandAdapter(ServiceAdapter):
    """Run an arbitrary user-supplied restart command."""

    name = "command"

    def __init__(self, command: str, **kwargs):
        super().__init__(**kwargs)
        self._command = command

    def restart_commands(self) -> list[str]:
        return [self._command]


# ============================================================================
# REGISTRY + FACTORY
# ============================================================================

_ADAPTERS: dict[str, type] = {
    "systemd": SystemdAdapter,
    "docker-compose": DockerComposeAdapter,
    "pm2": Pm2Adapter,
    "command": CommandAdapter,
}


def build_adapter(
    restart_cfg: Any, server_config: dict[str, Any], remote_ops: Any, dry_run: bool
) -> ServiceAdapter:
    """
    Construct the correct adapter from a RestartConfig.

    Raises ValueError for unknown adapters — fail fast, not silent.
    """
    adapter_name = restart_cfg.adapter
    cls = _ADAPTERS.get(adapter_name)
    if cls is None:
        known = ", ".join(sorted(_ADAPTERS))
        raise ValueError(
            f"Unknown restart adapter '{adapter_name}'. Valid options: {known}"
        )

    kwargs = dict(server_config=server_config, remote_ops=remote_ops, dry_run=dry_run)

    if adapter_name in ("systemd", "pm2"):
        if not restart_cfg.service:
            raise ValueError(
                f"Adapter '{adapter_name}' requires restart.service to be set."
            )
        return cls(service=restart_cfg.service, **kwargs)

    if adapter_name == "docker-compose":
        return cls(
            # We'll use the push target root for compose file context
            app_root=server_config.get("_deploy_target_root", "."),
            compose_file=restart_cfg.compose_file,
            **kwargs,
        )

    if adapter_name == "command":
        if not restart_cfg.command:
            raise ValueError("Adapter 'command' requires restart.command to be set.")
        return cls(command=restart_cfg.command, **kwargs)

    # Unreachable, but guard anyway
    raise ValueError(f"Could not instantiate adapter '{adapter_name}'")
