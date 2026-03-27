"""
navig.integrations.tailscale
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Thin wrapper around the ``tailscale`` CLI binary.

Provides the ``Tailscale`` class consumed by
``navig/commands/tailscale_cmd.py``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


@dataclass
class TailscalePeer:
    """Represents a single Tailscale network peer."""

    hostname: str
    tailscale_ip: str
    online: bool
    os: str
    dns_name: str = ""


@dataclass
class TailscaleStatus:
    """Result of a ``tailscale status --json`` call."""

    available: bool
    running: bool
    backend_state: str = ""
    self_hostname: str = ""
    self_ip: str = ""
    peers: list[TailscalePeer] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "running": self.running,
            "backend_state": self.backend_state,
            "self_hostname": self.self_hostname,
            "self_ip": self.self_ip,
            "peers": [
                {
                    "hostname": p.hostname,
                    "tailscale_ip": p.tailscale_ip,
                    "online": p.online,
                    "os": p.os,
                    "dns_name": p.dns_name,
                }
                for p in self.peers
            ],
            "error": self.error,
        }


class Tailscale:
    """
    Async-compatible wrapper around the ``tailscale`` CLI.

    All methods are ``async`` to align with the calling code in
    ``tailscale_cmd.py``, which uses ``asyncio.run()``.
    The actual subprocess calls are synchronous — they are fast
    enough for CLI use and do not need a thread-pool.
    """

    _BIN = "tailscale"

    def _run(self, *args: str, timeout: int = 10) -> tuple[int, str, str]:
        """Run tailscale <args> and return (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                [self._BIN, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            return -1, "", "tailscale binary not found on PATH"
        except subprocess.TimeoutExpired:
            return -1, "", f"tailscale timed out after {timeout}s"

    async def status(self) -> TailscaleStatus:
        """Return current Tailscale status."""
        rc, stdout, stderr = self._run("status", "--json")

        if rc == -1:
            return TailscaleStatus(available=False, running=False, error=stderr.strip())

        try:
            data: dict = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return TailscaleStatus(
                available=True,
                running=False,
                error=f"JSON parse error: {exc}",
            )

        backend_state = data.get("BackendState", "")
        running = backend_state == "Running"

        self_node = data.get("Self", {})
        self_hostname = self_node.get("HostName", "")
        # TailscaleIPs is a list; take the first IPv4
        self_ips: list = self_node.get("TailscaleIPs", [])
        self_ip = next(
            (ip for ip in self_ips if ":" not in ip), self_ips[0] if self_ips else ""
        )

        peers: list[TailscalePeer] = []
        for _id, peer in (data.get("Peer") or {}).items():
            peer_ips: list = peer.get("TailscaleIPs", [])
            peer_ip = next(
                (ip for ip in peer_ips if ":" not in ip),
                peer_ips[0] if peer_ips else "",
            )
            peers.append(
                TailscalePeer(
                    hostname=peer.get("HostName", _id),
                    tailscale_ip=peer_ip,
                    online=peer.get("Online", False),
                    os=peer.get("OS", ""),
                    dns_name=peer.get("DNSName", ""),
                )
            )

        return TailscaleStatus(
            available=True,
            running=running,
            backend_state=backend_state,
            self_hostname=self_hostname,
            self_ip=self_ip,
            peers=peers,
        )

    async def ping(self, peer: str, timeout: int = 5) -> bool:
        """Ping a Tailscale peer. Returns True if reachable."""
        rc, stdout, stderr = self._run(
            "ping", "--c", "1", "--timeout", str(timeout), peer, timeout=timeout + 3
        )
        # tailscale ping exits 0 on success and prints "pong from ..."
        return rc == 0 and "pong" in stdout.lower()

    async def ip(self, peer: str | None = None) -> str | None:
        """
        Return the Tailscale IP of the given peer, or self if peer is None.
        """
        if peer is None:
            rc, stdout, _err = self._run("ip", "-4")
            if rc == 0:
                return stdout.strip().splitlines()[0] if stdout.strip() else None
            return None

        # Resolve peer IP from status
        ts_status = await self.status()
        if not ts_status.available:
            return None
        for p in ts_status.peers:
            if peer.lower() in (p.hostname.lower(), p.dns_name.lower(), p.tailscale_ip):
                return p.tailscale_ip
        return None
