"""
SyncManager — LAN state synchronisation for the Navig Mesh.

Responsibilities:
  1. Leader broadcasts a ``sync_state`` UDP packet every ``broadcast_interval_s``
     (default 10s) carrying a compact state snapshot including:
       - cron schedule hash
       - heartbeat interval
       - shared context hash
       - capabilities of this node

  2. Standby nodes listen for sync_state packets and pull the full state from
     the leader via ``GET /mesh/sync/state`` when a hash mismatch is detected.

  3. An optional shared SQLite hook (``optional_sqlite_path``) allows persistent
     sync across reboots.  Deferred to Phase 2 — pass ``None`` to skip.

  4. Three endpoints complement the manager (registered in routes/mesh.py Phase 2):
       GET  /mesh/sync/state          → full state snapshot JSON
       POST /mesh/sync/push           → push a state delta from leader
       POST /mesh/sync/apply          → apply a pushed state (standbys only)

Wire-up:
  ```python
  from navig.mesh.sync_manager import SyncManager
  sm = SyncManager(registry, discovery)
  await sm.start()
  # … on shutdown:
  await sm.stop()
  ```

Failure coverage:
  Failure                              Response
  ────────────────────────────────────────────────────────────────────────────
  Leader unreachable for pull          Standby keeps current state, retries next tick
  Hash mismatch on every tick          Pull triggers at most once per debounce window
  Optional SQLite unavailable          State sync continues in-memory only
  Standby becomes leader               SyncManager detects via registry, switches to broadcast
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# How often the leader broadcasts a sync_state UDP packet (seconds)
DEFAULT_BROADCAST_INTERVAL_S = 10
# Minimum seconds between pull requests to avoid thundering-herd on mismatch
PULL_DEBOUNCE_S = 5
# Timeout for GET /mesh/sync/state HTTP pull (seconds)
PULL_TIMEOUT_S = 5


class SyncManager:
    """
    Manages per-formation LAN state synchronisation.

    Args:
        registry: NodeRegistry instance (provides am_i_leader, self_record, etc.)
        discovery: MeshDiscovery instance (provides send_election_packet for UDP)
        broadcast_interval_s: How often the leader broadcasts (seconds).
        optional_sqlite_path: If set, persist state snapshots to this SQLite DB.
                              Phase 2 feature — pass None to skip.
    """

    def __init__(
        self,
        registry: Any,
        discovery: Any,
        *,
        broadcast_interval_s: int = DEFAULT_BROADCAST_INTERVAL_S,
        optional_sqlite_path: Optional[Path] = None,
    ) -> None:
        self._registry = registry
        self._discovery = discovery
        self._broadcast_interval_s = broadcast_interval_s
        self._sqlite_path = optional_sqlite_path

        # Persisted state dict — updated on each received or generated snapshot
        self._state: Dict[str, Any] = {}
        self._state_hash: str = ""
        self._last_pull_at: float = 0.0

        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background sync loop."""
        self._running = True
        self._state = self._build_local_state()
        self._state_hash = self._hash_state(self._state)
        self._task = asyncio.create_task(self._loop(), name="sync_manager")
        logger.info("[sync] SyncManager started (interval=%ds)", self._broadcast_interval_s)

    async def stop(self) -> None:
        """Stop the sync loop cleanly."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[sync] SyncManager stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                if self._registry.am_i_leader():
                    await self._leader_tick()
                else:
                    await self._standby_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[sync] Loop error: %s", exc)
            await asyncio.sleep(self._broadcast_interval_s)

    # ── Leader behaviour ──────────────────────────────────────────────────────

    async def _leader_tick(self) -> None:
        """Rebuild local state, compute hash, broadcast via UDP."""
        new_state = self._build_local_state()
        new_hash = self._hash_state(new_state)

        if new_hash != self._state_hash:
            logger.debug("[sync] State changed, broadcasting new snapshot (hash=%s)", new_hash[:8])

        self._state = new_state
        self._state_hash = new_hash

        # Broadcast via the existing send_election_packet mechanism
        # Using ELECT_SYNC (sync_state) packet type defined in discovery.py
        try:
            from navig.mesh.discovery import ELECT_SYNC
            self._discovery.send_election_packet(
                ELECT_SYNC,
                {"sync_hash": self._state_hash, "epoch": self._get_epoch()},
            )
        except Exception as exc:
            logger.warning("[sync] Broadcast error: %s", exc)

        # Phase 2: persist to SQLite
        if self._sqlite_path:
            self._persist_state()

    # ── Standby behaviour ─────────────────────────────────────────────────────

    async def _standby_tick(self) -> None:
        """
        Standbys do nothing proactively — they react to incoming sync_state
        packets via _on_sync_packet (registered in discovery callback chain).

        This tick is a safety net: if no sync packet was received in the last
        `broadcast_interval * 3` seconds AND we have a known leader, pull.
        """
        leader = self._registry.get_leader()
        if not leader:
            return

        now = time.monotonic()
        stale_threshold = self._broadcast_interval_s * 3
        if now - self._last_pull_at > stale_threshold:
            await self._pull_from_leader(leader.gateway_url)

    def on_sync_packet(self, ptype: str, record: Any, raw: dict) -> None:
        """
        Called by MeshDiscovery when a sync_state packet arrives.
        Registered via discovery.set_election_callback (election manager
        routes ELECT_SYNC here via the callback).
        """
        incoming_hash = raw.get("sync_hash", "")
        if not incoming_hash:
            return

        if incoming_hash == self._state_hash:
            # Up to date — nothing to do
            return

        now = time.monotonic()
        if now - self._last_pull_at < PULL_DEBOUNCE_S:
            # In debounce window — skip
            return

        # Schedule a pull (non-blocking)
        asyncio.create_task(self._pull_from_leader(record.gateway_url))

    # ── HTTP pull ─────────────────────────────────────────────────────────────

    async def _pull_from_leader(self, leader_url: str) -> None:
        """Fetch full state from leader via GET /mesh/sync/state."""
        if not leader_url:
            return

        self._last_pull_at = time.monotonic()
        url = f"{leader_url.rstrip('/')}/mesh/sync/state"

        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as session:
                async with session.get(
                    url, timeout=_aio.ClientTimeout(total=PULL_TIMEOUT_S)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[sync] Pull failed (HTTP %d) from %s", resp.status, url)
                        return
                    data = await resp.json()

            incoming = data.get("data") or data
            self._apply_state(incoming)

        except Exception as exc:
            logger.warning("[sync] Pull error from %s: %s", url, exc)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _build_local_state(self) -> Dict[str, Any]:
        """Collect current local state for broadcast."""
        self_record = getattr(self._registry, "self_record", None)
        capabilities = list(getattr(self_record, "capabilities", []) or [])
        node_id = getattr(self_record, "node_id", "")
        hostname = getattr(self_record, "hostname", "")
        heartbeat_interval = self._broadcast_interval_s

        # Cron hash: hash the navig cron schedule if accessible
        cron_hash = self._get_cron_hash()

        return {
            "node_id": node_id,
            "hostname": hostname,
            "capabilities": capabilities,
            "heartbeat_interval_s": heartbeat_interval,
            "cron_hash": cron_hash,
            "timestamp": time.time(),
        }

    @staticmethod
    def _hash_state(state: Dict[str, Any]) -> str:
        """Deterministic SHA-256 hash of the state (excluding timestamp)."""
        stable = {k: v for k, v in state.items() if k != "timestamp"}
        raw = json.dumps(stable, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def _apply_state(self, incoming: Dict[str, Any]) -> None:
        """Apply a pulled state snapshot from the leader."""
        new_hash = self._hash_state(incoming)
        if new_hash == self._state_hash:
            return

        self._state = incoming
        self._state_hash = new_hash
        logger.info("[sync] State synced from leader (hash=%s)", new_hash[:8])

        # Phase 2: persist
        if self._sqlite_path:
            self._persist_state()

    def _get_epoch(self) -> int:
        """Return current election epoch from registry."""
        sr = getattr(self._registry, "self_record", None)
        return getattr(sr, "election_epoch", 0) if sr else 0

    @staticmethod
    def _get_cron_hash() -> str:
        """Try to hash the navig cron schedule; return '' on any failure."""
        try:
            from navig.daemon.scheduler import get_scheduler  # type: ignore[import]
            sched = get_scheduler()
            jobs = [str(j) for j in sched.get_jobs()] if sched else []
            return hashlib.sha256("|".join(jobs).encode()).hexdigest()[:8]
        except Exception:
            return ""

    def _persist_state(self) -> None:
        """Phase 2 stub — write state to SQLite via storage/engine.py."""
        # Will be implemented in Phase 2 when optional_sqlite_path is provided.
        # Using storage/engine.py when ready:
        #   engine = get_engine(self._sqlite_path)
        #   engine.execute("INSERT OR REPLACE INTO sync_state ...")
        pass

    # ── State accessor (for GET /mesh/sync/state endpoint) ───────────────────

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Return the current state snapshot dict (for HTTP GET endpoint)."""
        return {
            **self._state,
            "hash": self._state_hash,
            "is_leader": self._registry.am_i_leader(),
        }
