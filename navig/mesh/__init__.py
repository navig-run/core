"""
NAVIG Flux Mesh — LAN-local multi-node discovery and routing.

Architecture:
- UDP multicast on 224.0.0.251:5354 for peer announcement (no external deps)
- Each node is identified by a stable node_id derived at daemon start
- NodeRegistry holds live peer state in-memory, persisted to mesh_peers.json
- MeshRouter proxies cross-node requests via existing HTTP gateway
- GET /mesh/peers exposes the registry to Forge and any client

Phase scope: LAN only. Internet/blockchain routing is Phase 2.
Each node remains fully functional if the mesh is unreachable.
"""

from navig.mesh.auth import HMAC_FIELD, attach_hmac, load_secret, verify_payload
from navig.mesh.discovery import MeshDiscovery
from navig.mesh.registry import NodeRecord, NodeRegistry, get_registry

__all__ = [
    "NodeRecord", "NodeRegistry", "get_registry",
    "MeshDiscovery",
    "load_secret", "attach_hmac", "verify_payload", "HMAC_FIELD",
]
