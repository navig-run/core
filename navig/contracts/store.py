"""
RuntimeStore — durable in-memory store for Node, Mission, and ExecutionReceipt.

Persistence: JSON files in ~/.navig/runtime/ (auto-created on first write).
Restart recovery: loaded from disk on init.

Thread safety: single-process access assumed (asyncio-friendly reads/writes).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from navig.contracts.capability import TrustScore
from navig.contracts.execution_receipt import ExecutionReceipt, ReceiptOutcome
from navig.contracts.mission import Mission, MissionStatus
from navig.contracts.node import Node, NodeStatus
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

_DEFAULT_STORE_DIR = Path.home() / ".navig" / "runtime"


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

    def __init__(self, store_dir: Optional[Path] = None) -> None:
        self._dir = Path(store_dir) if store_dir else _DEFAULT_STORE_DIR
        self._nodes: Dict[str, Node] = {}
        self._missions: Dict[str, Mission] = {}
        self._receipts: Dict[str, ExecutionReceipt] = {}
        self._load()

    # ── Node CRUD ─────────────────────────────────────────────────────

    def register_node(self, node: Node) -> Node:
        self._nodes[node.node_id] = node
        return node

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def list_nodes(
        self,
        status: Optional[NodeStatus] = None,
    ) -> List[Node]:
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

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        return self._missions.get(mission_id)

    def list_missions(
        self,
        node_id: Optional[str] = None,
        status: Optional[MissionStatus] = None,
        limit: int = 100,
    ) -> List[Mission]:
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
        error: Optional[str] = None,
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
            completed_at=mission.completed_at or _now_iso(),
            started_at=mission.started_at,
            duration_secs=mission.duration_secs,
            error=mission.error,
        )
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    # ── Receipt access ────────────────────────────────────────────────

    def get_receipt(self, receipt_id: str) -> Optional[ExecutionReceipt]:
        return self._receipts.get(receipt_id)

    def list_receipts(
        self,
        node_id: Optional[str] = None,
        mission_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[ExecutionReceipt]:
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

    def flush(self) -> None:
        """Write current state to disk."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._write_file("nodes.json", [n.to_dict() for n in self._nodes.values()])
            self._write_file(
                "missions.json", [m.to_dict() for m in self._missions.values()]
            )
            self._write_file(
                "receipts.json", [r.to_dict() for r in self._receipts.values()]
            )
        except Exception as e:
            logger.warning(f"[RuntimeStore] Flush failed: {e}")

    def _load(self) -> None:
        """Load state from disk (silently ignore missing files)."""
        try:
            for raw in self._read_file("nodes.json"):
                try:
                    n = Node.from_dict(raw)
                    self._nodes[n.node_id] = n
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            for raw in self._read_file("missions.json"):
                try:
                    m = Mission.from_dict(raw)
                    self._missions[m.mission_id] = m
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            for raw in self._read_file("receipts.json"):
                try:
                    r = ExecutionReceipt.from_dict(raw)
                    self._receipts[r.receipt_id] = r
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
        except Exception as e:
            logger.debug(f"[RuntimeStore] Load skipped: {e}")

    def _write_file(self, name: str, data) -> None:
        path = self._dir / name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _read_file(self, name: str) -> list:
        path = self._dir / name
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "nodes": len(self._nodes),
            "missions": len(self._missions),
            "receipts": len(self._receipts),
        }

    def __repr__(self) -> str:
        s = self.stats()
        return f"<RuntimeStore nodes={s['nodes']} missions={s['missions']} receipts={s['receipts']}>"


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[RuntimeStore] = None


def get_runtime_store() -> RuntimeStore:
    global _instance
    if _instance is None:
        _instance = RuntimeStore()
    return _instance


def reset_runtime_store(store_dir: Optional[Path] = None) -> RuntimeStore:
    """Create a fresh store (use in tests)."""
    global _instance
    _instance = RuntimeStore(store_dir)
    return _instance


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
