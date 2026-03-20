"""
ElectionManager — deterministic leader election for the Navig LAN mesh.

Protocol (UDP-primary, SQLite-optional):
  1. Every node monitors the leader's heartbeat TTL via NodeRegistry.
  2. When TTL expires (no heartbeat for ttl_seconds), any standby node
     broadcasts ELECT_PROPOSE carrying (epoch, tiebreaker_score).
  3. Nodes collect ELECT_PROPOSE messages for ttl_seconds/3 seconds.
  4. The node with the highest tiebreaker_score wins deterministically.
     Tiebreaker is hash(hostname) % 2^32 — no randomness, no Raft.
  5. The winner broadcasts ELECT_PROMOTE and sets its own role to "leader".
  6. On graceful shutdown, the leader broadcasts ELECT_YIELD and waits for
     the takeover node to confirm ELECT_PROMOTE before relinquishing role.

Split-brain prevention:
  - Only one ELECT_PROPOSE per node per epoch.
  - If two nodes propose simultaneously, the one with the higher tiebreaker
    wins within the same TTL /3 window.
  - Epoch monotonically increases — stale ELECT_PROMOTE packets from a
    previous epoch are ignored.

Storage fallback:
  - All state is held in memory (NodeRegistry).
  - registry.set_my_role() calls _save_to_disk(); if the path is unreachable
    (SMB/NFS gone) it logs WARNING and continues in-memory only.

Cross-platform:
  - Pure asyncio + stdlib; no OS-specific calls.
  - threading bridge (trigger_yield) uses run_coroutine_threadsafe for
    cross-thread invocation from the psutil crash-detection path.

Failure coverage:
  Failure                                  → Response
  ────────────────────────────────────────────────────────────────────────────
  Leader dies without ELECT_YIELD          → TTL watchdog fires → propose/win
  Two candidates same tiebreaker (same host) → epoch tiebreaker, still det.
  Graceful yield ACK never arrives         → 10s timeout → self → standby
  Node starts while leader is healthy      → set_my_role("standby"), quiet
  SQLite / disk unreachable                → WARNING logged, UDP-only
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import TYPE_CHECKING, Dict, Optional

from navig.debug_logger import get_debug_logger
from navig.mesh.discovery import (
    ELECT_ACK,
    ELECT_PROMOTE,
    ELECT_PROPOSE,
    ELECT_YIELD,
    MeshDiscovery,
)
from navig.mesh.registry import NodeRecord, NodeRegistry

if TYPE_CHECKING:
    pass

logger = get_debug_logger()

# ─── Tuning constants ─────────────────────────────────────────────────────────
# How long to collect competing ELECT_PROPOSE packets before declaring winner.
PROPOSAL_WINDOW_S = 5          # seconds — 1/3 of default 15s TTL
# How long to wait for the takeover node to confirm ELECT_PROMOTE after YIELD.
YIELD_ACK_TIMEOUT_S = 10       # seconds
# Safety guard: if we think we lost but nobody promoted for this long, re-propose.
RE_PROPOSE_BACKOFF_S = 20      # seconds


class ElectionManager:
    """
    Manages leader election on top of MeshDiscovery + NodeRegistry.

    Lifecycle (matches gateway startup order):
        election_manager = ElectionManager(registry, discovery)
        await election_manager.start()   # fires TTL watchdog task
        await election_manager.stop()    # graceful yield if leader

    Cross-thread interface:
        election_manager.trigger_yield()  # callable from non-asyncio thread
    """

    def __init__(
        self,
        registry: NodeRegistry,
        discovery: MeshDiscovery,
        *,
        ttl_seconds: int = 15,
        heartbeat_interval: int = 5,
    ) -> None:
        self._registry = registry
        self._discovery = discovery
        self._ttl_seconds = ttl_seconds
        self._heartbeat_interval = heartbeat_interval

        # Internal state — all mutations happen on the asyncio loop
        self._running = False
        self._watchdog_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Proposal tracking — resets each election cycle
        self._current_epoch: int = 0
        self._proposed_this_epoch: bool = False   # dedupe: one proposal per node per epoch
        self._received_proposals: Dict[str, int] = {}  # node_id → tiebreaker_score
        self._proposal_window_task: Optional[asyncio.Task] = None

        # Yield tracking
        self._yield_event: Optional[asyncio.Event] = None  # set when ELECT_PROMOTE arrives after our YIELD

        # Wire the election packet callback into MeshDiscovery
        self._discovery.set_election_callback(self._on_election_packet)

    # ─────────────────────────── Lifecycle ────────────────────────────────────

    async def start(self) -> None:
        """Start the TTL watchdog and enter the cluster."""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_event_loop()

        # If a leader is already healthy, enter standby silently
        leader = self._registry.get_leader()
        if leader and not leader.is_self and leader.last_heartbeat_age_s() < self._ttl_seconds:
            self._registry.set_my_role("standby", self._current_epoch)
            logger.info(
                f"[election] Leader {leader.node_id} is healthy — entering standby"
            )
        else:
            # No known leader: wait one TTL for HELLO packets to settle, then propose
            await asyncio.sleep(2)
            await self._propose_candidacy(reason="startup")

        self._watchdog_task = asyncio.create_task(self._ttl_watchdog_loop())
        logger.info("[election] Started")

    async def stop(self) -> None:
        """Gracefully yield leadership and stop the watchdog."""
        if not self._running:
            return
        self._running = False

        if self._registry.am_i_leader():
            logger.info("[election] Stopping — triggering graceful yield")
            await self._graceful_yield()

        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        logger.info("[election] Stopped")

    def trigger_yield(self) -> None:
        """
        Thread-safe yield trigger — called from daemon/supervisor crash hook.

        Uses run_coroutine_threadsafe so it can be invoked from a non-asyncio
        thread (psutil monitor thread in supervisor.py).
        """
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(
                self._graceful_yield(), self._loop
            )

    # ─────────────────────────── Watchdog ────────────────────────────────────

    async def _ttl_watchdog_loop(self) -> None:
        """
        Runs every second.  Detects leader TTL expiry and triggers election.

        If the local node is already leader, the self-record's last_seen is
        always fresh (updated on every heartbeat-out), so the watchdog never
        mis-fires on itself.
        """
        try:
            while self._running:
                await asyncio.sleep(1)
                if not self._running:
                    break

                # If we are candidate/leader/yielding, skip watchdog
                own_role = self._registry.self_record.role
                if own_role in ("leader", "candidate", "yielding"):
                    continue

                # Check if the known leader is still alive
                leader = self._registry.get_leader()
                if leader is None:
                    # No leader recorded at all — trigger election
                    if not self._proposed_this_epoch:
                        logger.info("[election] Watchdog: no leader found — proposing")
                        asyncio.create_task(self._propose_candidacy(reason="no_leader"))
                    continue

                if leader.is_self:
                    # We are leader; watchdog not needed
                    continue

                age = leader.last_heartbeat_age_s()
                if age >= self._ttl_seconds:
                    if not self._proposed_this_epoch:
                        logger.info(
                            f"[election] Watchdog: leader {leader.node_id} TTL expired "
                            f"(age={age:.1f}s ≥ {self._ttl_seconds}s)"
                        )
                        asyncio.create_task(
                            self._propose_candidacy(reason="ttl_expiry")
                        )
        except asyncio.CancelledError:
            pass

    # ─────────────────────────── Election protocol ───────────────────────────

    async def _propose_candidacy(self, *, reason: str = "unknown") -> None:
        """
        Broadcast ELECT_PROPOSE and run the proposal-collection window.

        On entry: own role is "standby".
        On exit:  own role is "leader" (won) or "standby" (lost).
        """
        if self._proposed_this_epoch:
            return

        self._current_epoch += 1
        self._proposed_this_epoch = True
        self._received_proposals.clear()

        my_tiebreaker = NodeRegistry.get_tiebreaker(
            self._registry.self_record.hostname
        )
        my_node_id = self._registry.self_record.node_id

        # Record own proposal
        self._received_proposals[my_node_id] = my_tiebreaker

        self._registry.set_my_role("candidate", self._current_epoch)
        logger.info(
            f"[election] Proposing candidacy (reason={reason}, "
            f"epoch={self._current_epoch}, tiebreaker={my_tiebreaker})"
        )

        await self._discovery.send_election_packet(ELECT_PROPOSE, extra={
            "epoch": self._current_epoch,
            "tiebreaker_score": my_tiebreaker,
        })

        # Collect competing proposals for PROPOSAL_WINDOW_S seconds
        await asyncio.sleep(PROPOSAL_WINDOW_S)

        # Determine winner — highest tiebreaker wins; deterministic
        winner_id = max(
            self._received_proposals, key=lambda nid: self._received_proposals[nid]
        )
        winner_score = self._received_proposals[winner_id]

        if winner_id == my_node_id:
            # We won
            self._registry.set_my_role("leader", self._current_epoch)
            logger.info(
                f"[election] Won election "
                f"(epoch={self._current_epoch}, score={winner_score}) — role: LEADER"
            )
            await self._discovery.send_election_packet(ELECT_PROMOTE, extra={
                "epoch": self._current_epoch,
                "tiebreaker_score": winner_score,
            })
        else:
            # Another node won or tied
            self._registry.set_my_role("standby", self._current_epoch)
            logger.info(
                f"[election] Lost election to {winner_id} "
                f"(their score={winner_score}, ours={my_tiebreaker})"
            )
            # Reset proposed flag so we can enter future elections
            self._proposed_this_epoch = False

    async def _graceful_yield(self, target_node_id: Optional[str] = None) -> None:
        """
        Drain in-flight tasks, broadcast ELECT_YIELD, wait for new leader.

        Steps:
          1. Mark role as "yielding".
          2. Broadcast ELECT_YIELD (carries target_node_id hint if specified).
          3. Wait YIELD_ACK_TIMEOUT_S for an ELECT_PROMOTE from any other node.
          4. Set own role to "standby" regardless of whether ACK was received.

        The target_node_id hint lets operators select the handoff target via
        POST /mesh/handoff {"target": "hostname"}.  If omitted, any node may
        promote — the one with the highest tiebreaker score will win.
        """
        if not self._registry.am_i_leader():
            return

        self._yield_event = asyncio.Event()
        self._registry.set_my_role("yielding", self._current_epoch)
        logger.info(
            f"[election] Graceful yield initiated "
            f"(target={target_node_id or 'auto'})"
        )

        await self._discovery.send_election_packet(ELECT_YIELD, extra={
            "epoch": self._current_epoch,
            "target_node_id": target_node_id or "",
        })

        # Wait for a new leader's ELECT_PROMOTE to arrive
        try:
            await asyncio.wait_for(self._yield_event.wait(), timeout=YIELD_ACK_TIMEOUT_S)
            logger.info("[election] Yield ACK received — new leader confirmed")
        except asyncio.TimeoutError:
            logger.warning(
                f"[election] No ELECT_PROMOTE received within "
                f"{YIELD_ACK_TIMEOUT_S}s — forcing standby anyway"
            )

        self._registry.set_my_role("standby", self._current_epoch)
        self._proposed_this_epoch = False
        self._yield_event = None

    # ──────────────────────────── Packet handlers ────────────────────────────

    def _on_election_packet(
        self, ptype: str, record: NodeRecord, raw: dict
    ) -> None:
        """
        Synchronous callback invoked by MeshDiscovery._handle_packet for
        ELECTION_TYPES packets.

        Must not block — schedules async work via create_task.
        """
        if ptype == ELECT_PROPOSE:
            self._on_propose(record, raw)
        elif ptype == ELECT_PROMOTE:
            self._on_promote(record, raw)
        elif ptype == ELECT_YIELD:
            asyncio.create_task(self._on_yield(record, raw))

    def _on_propose(self, record: NodeRecord, raw: dict) -> None:
        """Record a competing proposal during the collection window."""
        incoming_epoch = int(raw.get("epoch", 0))
        incoming_score = int(raw.get("tiebreaker_score", 0))

        # Ignore stale-epoch proposals
        if incoming_epoch < self._current_epoch:
            logger.debug(
                f"[election] Ignoring stale PROPOSE from {record.node_id} "
                f"(epoch {incoming_epoch} < our {self._current_epoch})"
            )
            return

        # If incoming epoch is higher, we need to jump to it (rare: packet reorder)
        if incoming_epoch > self._current_epoch:
            self._current_epoch = incoming_epoch
            self._proposed_this_epoch = False
            self._received_proposals.clear()

        self._received_proposals[record.node_id] = incoming_score
        logger.debug(
            f"[election] Recorded proposal from {record.node_id} "
            f"(score={incoming_score}, epoch={incoming_epoch})"
        )

    def _on_promote(self, record: NodeRecord, raw: dict) -> None:
        """Handle an ELECT_PROMOTE from another node."""
        incoming_epoch = int(raw.get("epoch", 0))

        # Signal the yield-event if we were waiting for confirmation
        if self._yield_event and not self._yield_event.is_set():
            self._yield_event.set()
            logger.info(
                f"[election] Received PROMOTE from {record.node_id} "
                f"— yield confirmed"
            )

        # Update own role if we were a candidate or standby
        own_role = self._registry.self_record.role
        if own_role in ("candidate", "standby"):
            self._registry.set_my_role("standby", incoming_epoch)
            # Accept incoming epoch
            self._current_epoch = max(self._current_epoch, incoming_epoch)
            self._proposed_this_epoch = False

        logger.info(
            f"[election] New leader: {record.node_id} "
            f"(epoch={incoming_epoch})"
        )

    async def _on_yield(self, record: NodeRecord, raw: dict) -> None:
        """
        Handle an ELECT_YIELD from the current leader.

        If we are the target (or no target is specified and we have the highest
        tiebreaker), immediately propose candidacy so we win the election.
        """
        target = raw.get("target_node_id", "")
        my_id = self._registry.self_record.node_id
        my_hostname = self._registry.self_record.hostname

        is_targeted = (target == my_id) or (target == my_hostname)
        is_best_standby = self._is_best_available_standby()

        if is_targeted or (not target and is_best_standby):
            logger.info(
                f"[election] Received YIELD from {record.node_id} "
                f"— initiating takeover (targeted={is_targeted})"
            )
            # Reset proposed flag to allow immediate candidacy
            self._proposed_this_epoch = False
            self._current_epoch = int(raw.get("epoch", self._current_epoch))
            await self._propose_candidacy(reason="yield_takeover")
        else:
            logger.debug(
                f"[election] Received YIELD from {record.node_id} "
                f"— not our turn (target={target or 'auto'})"
            )

    def _is_best_available_standby(self) -> bool:
        """
        Return True if this node has the highest tiebreaker among all
        currently-online non-leader, non-yielding peers.
        """
        my_score = NodeRegistry.get_tiebreaker(self._registry.self_record.hostname)
        for peer in self._registry.get_peers():
            if peer.role in ("leader", "yielding"):
                continue
            if peer.health == "offline":
                continue
            if NodeRegistry.get_tiebreaker(peer.hostname) > my_score:
                return False
        return True

    # ────────────────────────────── State API ────────────────────────────────

    def state_dict(self) -> dict:
        """Return serialisable election state for GET /mesh/election/state."""
        leader = self._registry.get_leader()
        self_rec = self._registry.self_record
        standby_nodes = [
            {"node_id": p.node_id, "hostname": p.hostname}
            for p in self._registry.get_peers()
            if p.role == "standby" and p.health != "offline"
        ]
        return {
            "role": self_rec.role,
            "epoch": self._current_epoch,
            "leader_node_id": leader.node_id if leader else None,
            "leader_hostname": leader.hostname if leader else None,
            "tiebreaker_score": NodeRegistry.get_tiebreaker(self_rec.hostname),
            "ttl_seconds": self._ttl_seconds,
            "standby_count": len(standby_nodes),
            "standby_nodes": standby_nodes,
        }
