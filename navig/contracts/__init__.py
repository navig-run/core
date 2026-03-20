"""
navig.contracts — Runtime identity and mission contracts.

All contracts are plain Python dataclasses with:
  - Enforced state machines where applicable
  - JSON serialisation / deserialisation
  - JSON schema files in navig/schemas/

Public surface:

    from navig.contracts import (
        Node, NodeStatus, NodeOS,
        Mission, MissionStatus, MissionPriority, TERMINAL_STATES,
        ExecutionReceipt, ReceiptOutcome,
        Capability, TrustScore,
    )
"""

from navig.contracts.capability import (
    Capability,
    TrustScore,
)
from navig.contracts.execution_receipt import (
    ExecutionReceipt,
    ReceiptOutcome,
)
from navig.contracts.mission import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    Mission,
    MissionPriority,
    MissionStatus,
)
from navig.contracts.node import (
    Node,
    NodeOS,
    NodeStatus,
)
from navig.contracts.store import (
    RuntimeStore,
    get_runtime_store,
    reset_runtime_store,
)

__all__ = [
    # Node
    "Node", "NodeStatus", "NodeOS",
    # Mission
    "Mission", "MissionStatus", "MissionPriority", "TERMINAL_STATES", "ALLOWED_TRANSITIONS",
    # Receipt
    "ExecutionReceipt", "ReceiptOutcome",
    # Capability / Trust
    "Capability", "TrustScore",
    # Store
    "RuntimeStore", "get_runtime_store", "reset_runtime_store",
]
