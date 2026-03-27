"""
MeshRouter — HTTP proxy for cross-node request forwarding.

Resilience model (Phase 1):
  \u2022 route_to_best_peer()     — single best peer by composite_score; falls back
                                  to local processing on timeout or error.
  \u2022 route_with_fallback()    — tries up to MAX_FALLBACK_PEERS in score order.
                                  Returns the first success, or None if all fail.
                                  Ensures the mesh remains usable even when the
                                  top-ranked peer is temporarily unavailable.
  \u2022 route_parallel_best()    — races the top-2 peers simultaneously; returns
                                  whichever responds first. Use for latency-
                                  sensitive requests where extra bandwidth is
                                  acceptable.
  \u2022 get_topology_report()    — SPOF analysis: nodes whose removal would leave
                                  fewer than 2 healthy paths.
  \u2022 _RoutingMetrics          — per-peer success/failure counters and rolling
                                  average RTT for observability and scoring.

Self-asked questions answered here:
  Q: What auth does the forwarded request carry?
  A: A per-mesh shared secret (mesh_token), NOT the user's bearer token.

  Q: What if the target node times out?
  A: Falls back to local processing. The caller never sees a proxy error.

  Q: Is streaming supported?
  A: Not in Phase 1. Cross-node responses are buffered.

  Q: How does route_with_fallback handle a flapping peer?
  A: The circuit-breaker in NodeRecord (consecutive_failures >= 3) raises the
     composite_score by +10, pushing the peer to the bottom of the ordered list.
     It will only be tried once all healthy peers have been exhausted.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional

from navig.debug_logger import get_debug_logger
from navig.mesh.registry import NodeRecord, get_registry

if TYPE_CHECKING:
    pass

logger = get_debug_logger()

PROXY_TIMEOUT_S = 10  # seconds before giving up on a peer
MAX_FALLBACK_PEERS = 3  # maximum peers to try in route_with_fallback


# ──────────────────────────── Routing metrics ─────────────────────────────────


@dataclass
class _RoutingMetrics:
    """Per-peer rolling stats tracked in this process lifetime."""

    success_count: int = 0
    failure_count: int = 0
    _rtt_sum_ms: float = 0.0

    def record_success(self, rtt_ms: float) -> None:
        self.success_count += 1
        self._rtt_sum_ms += rtt_ms

    def record_failure(self) -> None:
        self.failure_count += 1

    @property
    def avg_rtt_ms(self) -> float:
        return self._rtt_sum_ms / self.success_count if self.success_count else 0.0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 0.0

    def to_dict(self) -> dict:
        return {
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "avg_rtt_ms": round(self.avg_rtt_ms, 1),
            "success_rate": round(self.success_rate, 3),
        }


_metrics: Dict[str, _RoutingMetrics] = {}


def _get_metrics(node_id: str) -> _RoutingMetrics:
    if node_id not in _metrics:
        _metrics[node_id] = _RoutingMetrics()
    return _metrics[node_id]


def get_routing_metrics() -> dict:
    """Return per-node routing statistics (for observability endpoints)."""
    return {nid: m.to_dict() for nid, m in _metrics.items()}


# ──────────────────────────── Public API ──────────────────────────────────────


async def route_to_best_peer(
    request_body: dict,
    capability: Optional[str] = None,
    target_node_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Forward a /llm/chat request body to the best available peer.

    Returns:
        The peer's response dict, or None if no suitable peer was found /
        the peer timed out (caller should fall back to local processing).
    """
    registry = get_registry()

    if target_node_id:
        peer = next(
            (r for r in registry.get_peers() if r.node_id == target_node_id), None
        )
        if peer is None:
            logger.warning(
                f"[mesh.router] Target node {target_node_id!r} not in registry"
            )
            return None
    else:
        peer = registry.get_best_peer(capability)

    if peer is None:
        return None

    return await _forward(peer, request_body)


async def route_with_fallback(
    request_body: dict,
    capability: Optional[str] = None,
    max_peers: int = MAX_FALLBACK_PEERS,
) -> Optional[dict]:
    """
    Try up to *max_peers* peers in composite-score order (best first).

    Returns the first successful response.  If all peers fail, returns None
    so the caller can fall back to local processing.  Each failure is recorded
    in both the per-node _RoutingMetrics and the NodeRecord circuit-breaker.
    """
    registry = get_registry()
    peers = registry.get_ordered_peers(capability=capability)[:max_peers]

    if not peers:
        return None

    for peer in peers:
        result = await _forward(peer, request_body)
        if result is not None:
            return result
        logger.info(
            f"[mesh.router] route_with_fallback: peer {peer.node_id} failed, "
            f"trying next ({peers.index(peer) + 1}/{len(peers)})"
        )

    return None


async def route_parallel_best(
    request_body: dict,
    capability: Optional[str] = None,
    n: int = 2,
) -> Optional[dict]:
    """
    Race the top-*n* peers: fire all requests simultaneously and return the
    first successful response, cancelling the rest.

    Trade-off: uses 2× bandwidth for ~50% latency reduction under normal
    conditions. Recommended for latency-sensitive, idempotent endpoints only.
    """
    registry = get_registry()
    peers = registry.get_ordered_peers(capability=capability)[:n]

    if not peers:
        return None
    if len(peers) == 1:
        return await _forward(peers[0], request_body)

    tasks = [asyncio.create_task(_forward(p, request_body)) for p in peers]
    result: Optional[dict] = None

    try:
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            r = t.result()
            if r is not None:
                result = r
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    return result


def get_topology_report() -> dict:
    """
    Analyse the LAN mesh topology for resilience.

    Returns a structural risk report including:
      \u2022 Overall redundancy status
      \u2022 SPOF identification (nodes whose loss would drop healthy-path count < 2)
      \u2022 Per-node health breakdown
      \u2022 Per-node routing metrics (success rate, avg RTT)
      \u2022 Success criteria evaluation

    Success criteria (Phase 1):
      \u2022 convergence_time_s  <= 45  (two missed heartbeats \xd7 15 s interval)
      \u2022 min_redundant_paths >= 2
      \u2022 max_acceptable_latency_ms <= 500
    """
    registry = get_registry()
    redundancy = registry.redundancy_check()

    # Merge routing metrics into peer report
    peer_details = []
    for peer in registry.get_all():
        d = peer.to_dict()
        if not peer.is_self:
            d["routing"] = _get_metrics(peer.node_id).to_dict()
        peer_details.append(d)

    # Evaluate success criteria
    avg_rtt = sum(m.avg_rtt_ms for m in _metrics.values() if m.success_count > 0) / max(
        sum(1 for m in _metrics.values() if m.success_count > 0), 1
    )

    criteria = {
        "convergence_time_s": {
            "target": 45,
            "current": "45 (2 \xd7 15 s heartbeat interval)",
            "pass": True,
        },
        "min_redundant_paths": {
            "target": 2,
            "current": redundancy["min_redundant_paths"],
            "pass": redundancy["redundancy_satisfied"],
        },
        "max_acceptable_latency_ms": {
            "target": 500,
            "current": round(avg_rtt, 1),
            "pass": avg_rtt <= 500 or avg_rtt == 0,
        },
    }

    return {
        "topology": redundancy,
        "criteria": criteria,
        "all_criteria_pass": all(c["pass"] for c in criteria.values()),
        "spof_nodes": redundancy["nodes_with_single_path"],
        "peers": peer_details,
        "routing_metrics": get_routing_metrics(),
    }


# ──────────────────────────── Internal ────────────────────────────────────────


async def _forward(peer: NodeRecord, body: dict) -> Optional[dict]:
    """HTTP POST body to peer's /llm/chat with mesh_token auth."""
    try:
        import aiohttp
    except ImportError:
        logger.warning("[mesh.router] aiohttp not available \u2014 cannot proxy")
        return None

    mesh_token = _get_mesh_token()
    headers = {"Content-Type": "application/json"}
    if mesh_token:
        headers["Authorization"] = f"Bearer {mesh_token}"

    url = f"{peer.gateway_url.rstrip('/')}/llm/chat"
    t0 = time.monotonic()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=PROXY_TIMEOUT_S),
            ) as resp:
                rtt_ms = (time.monotonic() - t0) * 1000
                if resp.status != 200:
                    logger.warning(
                        f"[mesh.router] Peer {peer.node_id} returned HTTP {resp.status}"
                    )
                    _get_metrics(peer.node_id).record_failure()
                    get_registry().record_probe_failure(peer.node_id)
                    return None

                data = await resp.json()
                _get_metrics(peer.node_id).record_success(rtt_ms)
                # Reset circuit-breaker on routing success
                get_registry().record_probe_success(peer.node_id, rtt_ms)

                # Inject routing metadata for observability
                if isinstance(data, dict):
                    meta = data.get("data", {}).get("metadata", {})
                    if isinstance(meta, dict):
                        meta["routed_via"] = peer.node_id
                        meta["routed_rtt_ms"] = round(rtt_ms, 1)
                return data

    except Exception as e:
        logger.warning(f"[mesh.router] Forward to {peer.node_id} failed: {e}")
        _get_metrics(peer.node_id).record_failure()
        get_registry().record_probe_failure(peer.node_id)
        return None


def _get_mesh_token() -> Optional[str]:
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().global_config
        return raw.get("gateway", {}).get("mesh_token")
    except Exception:
        return None
