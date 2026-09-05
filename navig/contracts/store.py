"""
RuntimeStore — durable in-memory store for Node, Mission, and ExecutionReceipt.

Persistence: JSON files in ~/.navig/runtime/ (auto-created on first write).
Restart recovery: loaded from disk on init.

Thread safety: single-process access assumed (asyncio-friendly reads/writes).
"""

from __future__ import annotations

import json
from pathlib import Path

from navig.contracts.capability import TrustScore
from navig.contracts.execution_receipt import ExecutionReceipt, ReceiptOutcome
from navig.contracts.mission import Mission, MissionStatus
from navig.contracts.node import Node, NodeStatus
from navig.core.dict_utils import now_iso
from navig.core.yaml_io import atomic_write_text as _atomic_write_text
from navig.core.yaml_io import read_text_retrying
from navig.debug_logger import get_debug_logger
from navig.platform.paths import config_dir

logger = get_debug_logger()

def _default_store_dir() -> Path:
    """Resolve the runtime-store dir at CALL time — never import time.

    ``config_dir()`` honours ``NAVIG_CONFIG_DIR``; a module-level constant
    would freeze the real user home before test/daemon isolation applies,
    pointing the node/mission/receipt registry at the operator's real state
    (see ``navig/vault/migrate.py:_legacy_db_path``).
    """
    return config_dir() / "runtime"

# Terminal MissionStatus → ReceiptOutcome, for receipts the executor records
# directly on the timeout / cancel paths (succeed/fail go via complete_mission).
_STATUS_TO_OUTCOME = {
    MissionStatus.SUCCEEDED: ReceiptOutcome.SUCCEEDED,
    MissionStatus.FAILED: ReceiptOutcome.FAILED,
    MissionStatus.CANCELLED: ReceiptOutcome.CANCELLED,
    MissionStatus.TIMED_OUT: ReceiptOutcome.TIMED_OUT,
}


class RuntimeStore:
    """
    Central registry for Nodes, Missions, and ExecutionReceipts.

    Lifecycle:
        store = RuntimeStore()          # loads from disk automatically
        store.register_node(node)
        mission = store.create_mission(mission)
        store.advance_mission(mission_id, "running")
        receipt = store.complete_mission(mission_id, succeeded=True)
        store.flush()                   # write all to disk
    """

    def __init__(self, store_dir: Path | None = None) -> None:
        self._dir = Path(store_dir) if store_dir else _default_store_dir()
        self._nodes: dict[str, Node] = {}
        self._missions: dict[str, Mission] = {}
        self._receipts: dict[str, ExecutionReceipt] = {}
        # Files that EXIST on disk but could not be read/parsed (a transient
        # Windows lock that survived retries, or on-disk corruption). flush()
        # refuses to overwrite these — otherwise a failed READ becomes a
        # destructive WRITE that wipes the whole audit trail. See _load / flush.
        self._load_failed: set[str] = set()
        self._load()

    # ── Node CRUD ─────────────────────────────────────────────────────

    def register_node(self, node: Node) -> Node:
        self._nodes[node.node_id] = node
        return node

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def list_nodes(
        self,
        status: NodeStatus | None = None,
    ) -> list[Node]:
        nodes = list(self._nodes.values())
        if status is not None:
            nodes = [n for n in nodes if n.status == status]
        return nodes

    def update_node(self, node: Node) -> None:
        if node.node_id not in self._nodes:
            raise KeyError(f"Node {node.node_id!r} not found")
        self._nodes[node.node_id] = node

    # ── Mission CRUD ──────────────────────────────────────────────────

    def create_mission(self, mission: Mission) -> Mission:
        self._missions[mission.mission_id] = mission
        return mission

    def get_mission(self, mission_id: str) -> Mission | None:
        return self._missions.get(mission_id)

    def list_missions(
        self,
        node_id: str | None = None,
        status: MissionStatus | None = None,
        limit: int = 100,
    ) -> list[Mission]:
        missions = list(self._missions.values())
        if node_id:
            missions = [m for m in missions if m.node_id == node_id]
        if status:
            missions = [m for m in missions if m.status == status]
        # newest first
        missions.sort(key=lambda m: m.created_at, reverse=True)
        return missions[:limit]

    def advance_mission(self, mission_id: str, action: str) -> Mission:
        """
        Apply a lifecycle action to a Mission.

        action: "start" | "succeed" | "fail:<msg>" | "cancel:<reason>" | "timeout"
        """
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"Mission {mission_id!r} not found")
        if action == "start":
            mission.start()
        elif action == "timeout":
            mission.timeout()
        elif action.startswith("succeed"):
            result = action[8:] if len(action) > 8 else None
            mission.succeed(result)
        elif action.startswith("fail:"):
            mission.fail(action[5:])
        elif action.startswith("cancel:"):
            mission.cancel(action[7:])
        elif action == "cancel":
            mission.cancel()
        elif action == "retry":
            mission.retry()
        else:
            raise ValueError(f"Unknown mission action: {action!r}")
        return mission

    def complete_mission(
        self,
        mission_id: str,
        succeeded: bool,
        result=None,
        error: str | None = None,
    ) -> ExecutionReceipt:
        """
        Mark a mission complete and create its ExecutionReceipt atomically.
        Returns the receipt.
        """
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"Mission {mission_id!r} not found")

        if succeeded:
            mission.succeed(result)
            outcome = ReceiptOutcome.SUCCEEDED
        else:
            mission.fail(error or "unspecified error")
            outcome = ReceiptOutcome.FAILED

        receipt = ExecutionReceipt.from_mission(
            mission_id=mission.mission_id,
            node_id=mission.node_id or "",
            title=mission.title,
            capability=mission.capability,
            outcome=outcome,
            completed_at=mission.completed_at or now_iso(),
            started_at=mission.started_at,
            duration_secs=mission.duration_secs,
            error=mission.error,
        )
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    def record_receipt_from_mission(
        self,
        mission: Mission,
        outcome: ReceiptOutcome | None = None,
    ) -> ExecutionReceipt:
        """Build + store an ExecutionReceipt for an already-terminal mission.

        Unlike ``complete_mission`` (which drives the succeed/fail transition
        itself), this does NOT mutate the mission — the caller has already moved
        it to its terminal state via ``mission.timeout()`` / ``mission.cancel()``.
        Used by the MissionExecutor for the TIMED_OUT / CANCELLED paths so every
        terminal mission leaves an audit receipt.
        """
        if outcome is None:
            outcome = _STATUS_TO_OUTCOME.get(mission.status, ReceiptOutcome.FAILED)
        receipt = ExecutionReceipt.from_mission(
            mission_id=mission.mission_id,
            node_id=mission.node_id or "",
            title=mission.title,
            capability=mission.capability,
            outcome=outcome,
            completed_at=mission.completed_at or now_iso(),
            started_at=mission.started_at,
            duration_secs=mission.duration_secs,
            error=mission.error,
        )
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    def record_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        """Store a pre-built ExecutionReceipt (append-only)."""
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    # ── Receipt access ────────────────────────────────────────────────

    def get_receipt(self, receipt_id: str) -> ExecutionReceipt | None:
        return self._receipts.get(receipt_id)

    def list_receipts(
        self,
        node_id: str | None = None,
        mission_id: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionReceipt]:
        receipts = list(self._receipts.values())
        if node_id:
            receipts = [r for r in receipts if r.node_id == node_id]
        if mission_id:
            receipts = [r for r in receipts if r.mission_id == mission_id]
        receipts.sort(key=lambda r: r.recorded_at, reverse=True)
        return receipts[:limit]

    # ── TrustScore computation ────────────────────────────────────────

    def compute_trust_score(self, node_id: str) -> TrustScore:
        receipts = self.list_receipts(node_id=node_id, limit=500)
        return TrustScore.compute(node_id, receipts)

    # ── Persistence ───────────────────────────────────────────────────

    def _collections(self):
        """(filename, in-memory dict, row→obj parser, id attribute) for each file.

        The dicts are returned by reference, so callers mutate the instance state.
        """
        return (
            ("nodes.json", self._nodes, Node.from_dict, "node_id"),
            ("missions.json", self._missions, Mission.from_dict, "mission_id"),
            ("receipts.json", self._receipts, ExecutionReceipt.from_dict, "receipt_id"),
        )

    def flush(self) -> None:
        """Write current state to disk.

        A collection whose on-disk file EXISTS but could not be read at load time
        is NOT overwritten here — writing our (necessarily partial) in-memory copy
        over it would destroy every row we failed to read, turning a transient read
        blip into a full wipe of the node/mission/receipt audit trail. Before
        skipping, we retry the read once: a lock may have cleared, in which case the
        on-disk rows are folded back in (in-session updates win) and we persist safely.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            for name, target, parser, id_attr in self._collections():
                if name in self._load_failed:
                    # self-heal: the transient failure may be gone now
                    self._ingest(name, target, parser, id_attr, prefer_memory=True)
                if name in self._load_failed:
                    logger.warning(
                        "[RuntimeStore] %s still unreadable — skipping write to "
                        "avoid overwriting unread data",
                        name,
                    )
                    continue
                self._write_file(name, [obj.to_dict() for obj in target.values()])
        except Exception as e:
            logger.warning("[RuntimeStore] Flush failed: %s", e)

    def _load(self) -> None:
        """Load state from disk. A missing file is an empty collection; a file that
        exists but is unreadable is recorded in ``_load_failed`` so flush() can't
        later wipe it (see flush)."""
        for name, target, parser, id_attr in self._collections():
            self._ingest(name, target, parser, id_attr)

    def _ingest(self, name, target, parser, id_attr, *, prefer_memory: bool = False) -> None:
        """Read one JSON file into ``target``, distinguishing 'absent' (→ empty,
        safe) from 'unreadable' (→ recorded in ``_load_failed``, never treated as
        empty). One malformed row is skipped, not fatal to the whole file."""
        try:
            rows = self._read_file(name)
        except Exception as e:  # noqa: BLE001 — file exists but unreadable/corrupt
            self._load_failed.add(name)
            logger.warning("[RuntimeStore] %s unreadable (preserved on disk): %s", name, e)
            return
        self._load_failed.discard(name)
        for raw in rows:
            try:
                obj = parser(raw)
                key = getattr(obj, id_attr)
                if prefer_memory and key in target:
                    continue  # keep the in-session copy over the stale on-disk one
                target[key] = obj
            except Exception:  # noqa: BLE001
                pass  # best-effort per row; one bad row must not drop the file

    def _write_file(self, name: str, data) -> None:
        path = self._dir / name
        _atomic_write_text(path, json.dumps(data, indent=2))

    def _read_file(self, name: str) -> list:
        """Parsed JSON list. ``[]`` for a genuinely-absent file; RAISES for a file
        that exists but can't be read/parsed (a transient lock that survived retries,
        or corruption) — the caller must never mistake 'unreadable' for 'empty',
        because collapsing the two is how a read blip becomes a data wipe."""
        path = self._dir / name
        if not path.exists():
            return []
        return json.loads(read_text_retrying(path))

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "nodes": len(self._nodes),
            "missions": len(self._missions),
            "receipts": len(self._receipts),
        }

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"<RuntimeStore nodes={s['nodes']} missions={s['missions']} receipts={s['receipts']}>"
        )


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: RuntimeStore | None = None


def get_runtime_store() -> RuntimeStore:
    global _instance
    if _instance is None:
        _instance = RuntimeStore()
    return _instance


def reset_runtime_store(store_dir: Path | None = None) -> RuntimeStore:
    """Create a fresh store (use in tests)."""
    global _instance
    _instance = RuntimeStore(store_dir)
    return _instance


# ── Helpers ───────────────────────────────────────────────────────────────────

