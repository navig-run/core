"""
NodeRegistry — in-memory + persisted peer state.

Self-asked questions answered here:
  Q: Should node_id change on restart?
  A: No. It is derived deterministically from os+hostname+MAC, so it survives restarts
     and remains stable even if the IP changes (DHCP).

  Q: Where is the canonical state — memory or disk?
  A: Memory. Disk (mesh_peers.json) is a warm-start cache only.
     Stale entries are pruned after OFFLINE_AFTER_S seconds regardless of disk state.

  Q: Should we sync SOUL.md or memory across nodes?
  A: Never automatically. Each node's identity is sovereign. The registry shares
     only operational metadata — no agent memory, no formation state.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# ── Convergence timers (tuned for fast failure detection) ───────────────────
# Peer is considered degraded after 45 s without a heartbeat (was 90 s).
# Two missed heartbeats at 15 s interval = degraded; three = offline.
DEGRADED_AFTER_S = 45
# Peer is considered offline after 2 minutes without a heartbeat (was 5 min).
OFFLINE_AFTER_S = 120
# Peer is removed from registry after 15 minutes without any contact (was 30).
EVICT_AFTER_S = 900

# ── Circuit-breaker settings ─────────────────────────────────────────────────
# Open the circuit (skip peer for routing) after this many consecutive probe
# failures. It auto-resets after a successful heartbeat/probe.
CIRCUIT_OPEN_AFTER_FAILURES = 3

MESH_PEERS_FILENAME = "mesh_peers.json"


@dataclass
class NodeRecord:
    """Full descriptor for one Navig node on the mesh."""

    node_id: str                   # "navig-linux-srv01-a3f2" — stable
    hostname: str                  # machine hostname
    os: str                        # "windows" | "linux" | "macos"
    gateway_url: str               # "http://10.0.0.x:8789"
    capabilities: List[str]        # ["llm", "shell", "docker", "ssh", "gpu"]
    formation: str                 # active formation name (may be "")
    load: float                    # 0.0–1.0 composite (CPU * 0.6 + MEM * 0.4)
    version: str                   # navig-core semver
    last_seen: float = field(default_factory=time.time)
    is_self: bool = False          # True only for the local node record

    # ── Probe / circuit-breaker state (not persisted, never serialised) ──────
    consecutive_failures: int = field(default=0, compare=False, repr=False)
    last_rtt_ms: float = field(default=0.0, compare=False, repr=False)
    total_probes: int = field(default=0, compare=False, repr=False)
    total_probe_failures: int = field(default=0, compare=False, repr=False)

    @property
    def health(self) -> str:
        age = time.time() - self.last_seen
        if age < DEGRADED_AFTER_S:
            return "online"
        if age < OFFLINE_AFTER_S:
            return "degraded"
        return "offline"

    @property
    def circuit_open(self) -> bool:
        """True when this peer should be skipped for routing (too many failures)."""
        return self.consecutive_failures >= CIRCUIT_OPEN_AFTER_FAILURES

    @property
    def composite_score(self) -> float:
        """
        Lower is better.  Combines:
          • load          (0.0–1.0, weight 0.5)
          • RTT penalty   (capped at 500 ms, normalised to 0–1, weight 0.3)
          • health penalty (0 = online, 0.5 = degraded, weight 0.2)
        Circuit-open peers get +10 to keep them at the bottom of the list
        without fully excluding them (so they can still be tried as a last
        resort if all others are worse).
        """
        rtt_norm = min(self.last_rtt_ms, 500.0) / 500.0
        health_pen = 0.0 if self.health == "online" else (0.5 if self.health == "degraded" else 1.0)
        score = self.load * 0.5 + rtt_norm * 0.3 + health_pen * 0.2
        return score + (10.0 if self.circuit_open else 0.0)

    def record_probe_success(self, rtt_ms: float) -> None:
        """Called by the active-probe loop on a successful /health ping."""
        self.consecutive_failures = 0
        self.last_rtt_ms = rtt_ms
        self.total_probes += 1
        self.last_seen = time.time()

    def record_probe_failure(self) -> None:
        """Called by the active-probe loop on a failed /health ping."""
        self.consecutive_failures += 1
        self.total_probes += 1
        self.total_probe_failures += 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["health"] = self.health
        d["circuit_open"] = self.circuit_open
        d["composite_score"] = round(self.composite_score, 4)
        # Strip internal counters from the serialised form
        for k in ("consecutive_failures", "last_rtt_ms", "total_probes", "total_probe_failures"):
            d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NodeRecord":
        # Strip derived / internal fields before handing to the dataclass constructor
        for k in ("health", "is_self", "circuit_open", "composite_score",
                  "consecutive_failures", "last_rtt_ms", "total_probes", "total_probe_failures"):
            d.pop(k, None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _derive_node_id() -> str:
    """
    Stable node_id: navig-{os}-{hostname_slug}-{4hex}.
    The 4hex is derived from the MAC address or a persisted random UUID
    so it survives restarts and IP changes.
    """
    os_name = _detect_os()
    hostname = socket.gethostname().lower().replace(" ", "-")[:16]

    # Try to get MAC for stability; fall back to seeded UUID
    try:
        mac_int = uuid.getnode()
        if mac_int >> 40 & 1:   # multicast bit → randomly generated, not stable
            raise ValueError("random MAC")
        fingerprint = format(mac_int & 0xFFFF, "04x")
    except Exception:
        seed = f"{os_name}-{hostname}"
        fingerprint = hashlib.blake2b(seed.encode(), digest_size=2).hexdigest()

    return f"navig-{os_name}-{hostname}-{fingerprint}"


def _detect_os() -> str:
    p = platform.system().lower()
    if p == "windows":
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def _measure_load() -> float:
    """0.0–1.0 composite load. Gracefully degrades if psutil is absent."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0) / 100.0
        mem = psutil.virtual_memory().percent / 100.0
        return round(cpu * 0.6 + mem * 0.4, 3)
    except Exception:
        return 0.0


class NodeRegistry:
    """
    In-memory registry of all known Navig peers on the LAN mesh.

    Thread-safety note: all mutations happen in the asyncio event loop.
    No lock needed — aiohttp runs single-threaded per loop.
    """

    def __init__(self, storage_dir: Path):
        self._storage_dir = storage_dir
        self._peers: Dict[str, NodeRecord] = {}   # keyed by node_id
        self._self: Optional[NodeRecord] = None
        self._target_node_id: Optional[str] = None  # active routing target (in-memory)

        self._peers_file = storage_dir / MESH_PEERS_FILENAME

        # Build self record
        self._self = self._build_self_record()
        self._peers[self._self.node_id] = self._self

        # Load warm-start cache
        self._load_from_disk()

    # ─────────────────────────── Identity ────────────────────────────

    def _build_self_record(self) -> NodeRecord:
        from navig.config import get_config_manager
        config = get_config_manager()
        raw = config.global_config

        port = raw.get("gateway", {}).get("port", 8789)
        local_ip = self._local_ip()
        gateway_url = f"http://{local_ip}:{port}"

        caps = self._detect_capabilities()
        formation = raw.get("formation", {}).get("active", "")

        try:
            from navig import __version__ as ver
        except ImportError:
            ver = "unknown"

        return NodeRecord(
            node_id=_derive_node_id(),
            hostname=socket.gethostname(),
            os=_detect_os(),
            gateway_url=gateway_url,
            capabilities=caps,
            formation=formation,
            load=_measure_load(),
            version=ver,
            last_seen=time.time(),
            is_self=True,
        )

    def _detect_capabilities(self) -> List[str]:
        caps = ["llm", "shell"]
        try:
            import docker  # noqa: F401
            caps.append("docker")
        except ImportError:
            pass
        try:
            import paramiko  # noqa: F401
            caps.append("ssh")
        except ImportError:
            pass
        try:
            import torch  # noqa: F401
            caps.append("gpu")
        except ImportError:
            pass
        return caps

    @staticmethod
    def _local_ip() -> str:
        """Best-effort LAN IP (not loopback)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    # ─────────────────────────── Public API ──────────────────────────

    @property
    def self_record(self) -> NodeRecord:
        # Refresh load on every access so heartbeat packets carry live data
        if self._self:
            self._self.load = _measure_load()
            self._self.last_seen = time.time()
        return self._self  # type: ignore[return-value]

    def upsert_peer(self, record: NodeRecord) -> None:
        """Add or refresh a remote peer record. Ignores is_self flag from remote."""
        record.is_self = False
        self._peers[record.node_id] = record
        self._evict_stale()
        self._save_to_disk()

    def remove_peer(self, node_id: str) -> None:
        self._peers.pop(node_id, None)
        self._save_to_disk()

    def get_peers(self) -> List[NodeRecord]:
        """All known nodes except self, pruned of eviction-age entries."""
        self._evict_stale()
        return [r for r in self._peers.values() if not r.is_self]

    def get_all(self) -> List[NodeRecord]:
        """All nodes including self."""
        self._evict_stale()
        return list(self._peers.values())

    def get_best_peer(self, capability: Optional[str] = None) -> Optional[NodeRecord]:
        """
        Return the single best peer by composite score. Skips circuit-open
        peers unless they are the only option.
        """
        peers = self.get_ordered_peers(capability)
        return peers[0] if peers else None

    def get_ordered_peers(
        self,
        capability: Optional[str] = None,
        include_degraded: bool = True,
        max_results: int = 8,
    ) -> List[NodeRecord]:
        """
        Return peers sorted by composite_score (lower = better).

        Args:
            capability:       Optional capability filter.
            include_degraded: If False, exclude peers whose health != 'online'.
            max_results:      Cap the list to prevent thundering-herd on large meshes.

        Circuit-open peers are sorted to the bottom but kept in the list so
        the router can still use them as a last resort.
        """
        candidates = []
        for r in self.get_peers():
            if r.health == "offline":
                continue
            if not include_degraded and r.health != "online":
                continue
            if capability and capability not in r.capabilities:
                continue
            candidates.append(r)
        candidates.sort(key=lambda r: r.composite_score)
        return candidates[:max_results]

    def redundancy_check(self) -> dict:
        """
        Analyse path redundancy for every non-self node.

        Returns a dict with:
          {\n          "node_count": int,
          "peer_count": int,
          "nodes_with_single_path": [node_id, ...],   # SPOFs by definition in star topology
          "min_degree": int,                           # lowest connectivity (1 = vulnerable)
          "healthy_peer_count": int,
          "degraded_peer_count": int,
          "offline_peer_count": int,
          "circuit_open_count": int,
        }
        In Phase 1 (star / flat mesh) every node's only path to every other
        node goes through UDP multicast — there is no graph to traverse.
        This report therefore measures *availability* redundancy: how many
        independent paths exist to the self node, and whether the self node
        would become isolated if any individual peer were removed.
        """
        peers = self.get_peers()
        health_counts = {"online": 0, "degraded": 0, "offline": 0}
        circuit_open = 0
        for p in peers:
            health_counts[p.health] = health_counts.get(p.health, 0) + 1
            if p.circuit_open:
                circuit_open += 1

        # Nodes with < 2 healthy alternatives are single-path-dependent
        healthy_peers = [p for p in peers if p.health == "online" and not p.circuit_open]
        spof_nodes = [p.node_id for p in peers if len(healthy_peers) < 2]

        return {
            "node_count": len(peers) + 1,   # +1 for self
            "peer_count": len(peers),
            "healthy_peer_count": health_counts["online"],
            "degraded_peer_count": health_counts["degraded"],
            "offline_peer_count": health_counts["offline"],
            "circuit_open_count": circuit_open,
            "nodes_with_single_path": spof_nodes,
            "min_redundant_paths": min(len(healthy_peers), 2),
            "redundancy_satisfied": len(healthy_peers) >= 2,
        }

    def record_probe_success(self, node_id: str, rtt_ms: float) -> None:
        """Called by the active-probe loop when a /health ping succeeds."""
        peer = self._peers.get(node_id)
        if peer and not peer.is_self:
            peer.record_probe_success(rtt_ms)

    def record_probe_failure(self, node_id: str) -> None:
        """Called by the active-probe loop when a /health ping fails."""
        peer = self._peers.get(node_id)
        if peer and not peer.is_self:
            peer.record_probe_failure()
            if peer.circuit_open:
                logger.warning(
                    f"[mesh.registry] Circuit OPEN for {node_id} "
                    f"({peer.consecutive_failures} consecutive failures)"
                )

    def to_api_dict(self) -> dict:
        peers_out = []
        for r in self.get_peers():
            d = r.to_dict()
            d["is_current_target"] = (r.node_id == self._target_node_id)
            d["health"] = r.health  # include computed property
            d["score"] = round(r.composite_score, 4)
            d["rtt_ms"] = round(r.last_rtt_ms, 1)
            d["load_pct"] = round(r.load * 100, 1)
            peers_out.append(d)
        self_d = self.self_record.to_dict()
        self_d["is_current_target"] = (self.self_record.node_id == self._target_node_id)
        self_d["health"] = self.self_record.health
        return {"self": self_d, "peers": peers_out}

    # ─────────────────────────── Target management ───────────────────────────

    def set_target(self, node_id: str) -> None:
        """Set the active routing target. Stored in memory only (not persisted)."""
        self._target_node_id = node_id
        logger.info(f"[mesh.registry] Routing target set to {node_id}")

    def clear_target(self) -> None:
        """Clear the active routing target — commands run locally."""
        self._target_node_id = None
        logger.info("[mesh.registry] Routing target cleared")

    @property
    def target_node_id(self) -> Optional[str]:
        return self._target_node_id

    def get_peer(self, node_id: str) -> Optional["NodeRecord"]:
        """Get a specific peer by exact node_id."""
        return self._peers.get(node_id)

    def list_peers(self) -> List["NodeRecord"]:
        """Alias for get_peers() for consistent naming."""
        return self.get_peers()

    @staticmethod
    def get_tiebreaker(hostname: str) -> int:
        """
        Return a deterministic tiebreaker score for election purposes.

        Lower score wins — the node with the lexicographically smallest hostname
        hash is preferred as leader.  The value is stable across restarts and
        consistent across all nodes that know the same hostname.
        """
        import hashlib
        return int(hashlib.sha256(hostname.encode("utf-8")).hexdigest(), 16) % (10 ** 9)

    # ─────────────────────────── Persistence ─────────────────────────

    def _evict_stale(self) -> None:
        now = time.time()
        stale = [
            nid for nid, r in self._peers.items()
            if not r.is_self and (now - r.last_seen) > EVICT_AFTER_S
        ]
        for nid in stale:
            del self._peers[nid]
            logger.debug(f"[mesh.registry] Evicted stale peer {nid}")

    def _save_to_disk(self) -> None:
        try:
            data = [r.to_dict() for r in self._peers.values() if not r.is_self]
            self._peers_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"[mesh.registry] Failed to save peers: {e}")

    def _load_from_disk(self) -> None:
        if not self._peers_file.exists():
            return
        try:
            data = json.loads(self._peers_file.read_text())
            for d in data:
                record = NodeRecord.from_dict(d)
                if record.node_id != self._self.node_id:
                    self._peers[record.node_id] = record
            logger.info(f"[mesh.registry] Loaded {len(data)} cached peers from disk")
        except Exception as e:
            logger.warning(f"[mesh.registry] Failed to load peers cache: {e}")


# ─────────────────────────── Singleton ───────────────────────────────

_registry_instance: Optional[NodeRegistry] = None


def get_registry(storage_dir: Optional[Path] = None) -> NodeRegistry:
    global _registry_instance
    if _registry_instance is None:
        if storage_dir is None:
            storage_dir = Path.home() / ".navig"
        _registry_instance = NodeRegistry(storage_dir)
    return _registry_instance
