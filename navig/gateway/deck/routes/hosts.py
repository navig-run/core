"""
Unified Hosts inventory for the Deck API.

GET /api/deck/hosts

Returns the merged inventory of every host the operator can act on, from
two sources unified into one canonical list:

  1. Manually-registered hosts (~/.navig/hosts/*.yaml) — SSH targets
  2. Mesh-discovered peers — other navig daemons announcing via multicast

Each row carries a `sources` set and `capabilities` array so the Deck UI
can render badges (`ssh`, `mesh-peer`, `agent`, `crawled`). A host that is
BOTH a mesh peer AND has stored SSH credentials counts ONCE in the
inventory — deduplicated by hostname, with capabilities merged.

This endpoint is the source of truth for the host-count licence limit.
When licence enforcement is wired (Phase 3.2), the response includes:
  - `total_hosts`: total rows in the merged inventory
  - `visible_hosts`: how many we return given the tier's host_limit
  - `host_limit`: the tier's cap (1 for Solo, 5 for Personal, ...)
  - `hidden_hosts_count`: rows above the cap that we kept registered but
    don't return (CLI still operates on them; soft cap)
  - `tier`: effective tier name

Until licence is wired we return total_hosts/host_limit = total/Infinity
so the Deck shows everything.
"""

from __future__ import annotations

import logging
import platform
import socket

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _os_id() -> str:
    sys = platform.system().lower()
    if "win" in sys:
        return "windows"
    if "darwin" in sys or "mac" in sys:
        return "macos"
    if "linux" in sys:
        return "linux"
    return "unknown"


def _normalize_hostname(s: str) -> str:
    """Lowercase + strip common DNS suffixes so 'host.local' and 'host' match."""
    h = (s or "").strip().lower()
    for suffix in (".local", ".lan", ".internal"):
        if h.endswith(suffix):
            h = h[: -len(suffix)]
            break
    return h


def _merge_capabilities(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for cap in (lst or []):
            c = str(cap).lower()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out


async def handle_deck_hosts(request: "web.Request") -> "web.Response":
    """Return the unified host inventory + count metadata."""
    try:
        from navig.cloud import hetzner as hz
        from navig.config import get_config_manager
        from navig.mesh.registry import get_registry

        cm = get_config_manager()
        registry = get_registry()

        # Build mesh-peer index keyed by normalized hostname so we can join
        # against manually-registered hosts (and detect mesh-only peers).
        all_nodes = registry.get_all()
        peers_by_norm_host: dict[str, object] = {}
        for node in all_nodes:
            key = _normalize_hostname(node.hostname)
            if key:
                peers_by_norm_host[key] = node

        host_names: list[str] = cm.list_hosts()
        local_hostname_raw = socket.gethostname()
        local_hostname = _normalize_hostname(local_hostname_raw)

        # Track which mesh peers we've already consumed via a manual entry so
        # we don't emit them twice when we later append mesh-only peers.
        consumed_peer_ids: set[str] = set()

        rows: list[dict] = []

        # Pass 1 — manually-registered hosts. Each may also be a mesh peer.
        for name in host_names:
            try:
                cfg: dict = cm.load_host_config(name)
            except Exception:
                cfg = {}

            hostname_raw: str = str(
                cfg.get("hostname") or cfg.get("host") or name
            )
            norm = _normalize_hostname(hostname_raw)

            peer = (
                peers_by_norm_host.get(norm)
                or peers_by_norm_host.get(_normalize_hostname(name))
            )

            is_self = (
                norm == local_hostname
                or norm in ("localhost", "127.0.0.1")
                or _normalize_hostname(name) == local_hostname
                or bool(peer and getattr(peer, "is_self", False))
            )

            if peer is None and is_self:
                peer = registry.self_record if registry._self else None  # type: ignore[attr-defined]

            sources = ["ssh"]
            capabilities = ["ssh", "crawled"]
            if peer is not None:
                sources.append("mesh")
                capabilities = _merge_capabilities(
                    capabilities,
                    ["mesh-peer", "agent"],
                    list(getattr(peer, "capabilities", []) or []),
                )
                consumed_peer_ids.add(peer.node_id)

            # Cloud identity (Hetzner): a host can be BOTH an SSH target and a
            # cloud server. We keep `type` as the primary transport and carry the
            # cloud provider in a `cloud` block (None when there is no cloud
            # identity). Best-effort live state, cached, never blocks/raises.
            try:
                cloud = hz.host_cloud_summary(cfg)
            except Exception:
                cloud = None
            if cloud is not None and "cloud" not in capabilities:
                capabilities = _merge_capabilities(capabilities, ["cloud", cloud.get("provider", "")])

            rows.append({
                "id": name,                    # stable id for the row
                "name": name,
                "hostname": hostname_raw or name,
                "user": str(cfg.get("user") or ""),
                "port": int(cfg.get("port") or cfg.get("ssh_port") or 22),
                "os": str(cfg.get("os") or (peer.os if peer else "unknown")),
                "type": str(cfg.get("type") or "ssh"),
                "cloud": cloud,                # None | {provider, power, region, ...}
                "description": str(cfg.get("description") or ""),
                "tags": list(cfg.get("tags") or []),
                "sources": sources,            # ["ssh"] | ["ssh", "mesh"]
                "capabilities": capabilities,  # for badge rendering
                "has_navig": peer is not None,
                "is_self": is_self,
                "peer_id": peer.node_id if peer else None,
                "gateway_url": peer.gateway_url if peer else None,
                "health": peer.health if peer else "unknown",
                "load": float(peer.load) if peer else 0.0,
                "last_seen_age_s": (
                    peer.last_heartbeat_age_s() if peer else None
                ),
            })

        # Pass 2 — mesh-only peers (no manual host entry). These are other
        # navig daemons announcing on the mesh that the operator hasn't
        # added to their hosts/ folder. They still count as hosts.
        for peer in all_nodes:
            if peer.node_id in consumed_peer_ids:
                continue
            is_self_peer = bool(getattr(peer, "is_self", False))
            rows.append({
                "id": peer.node_id,
                "name": peer.hostname or peer.node_id,
                "hostname": peer.hostname,
                "user": "",
                "port": 0,
                "os": peer.os,
                "type": "mesh-only",
                "cloud": None,
                "description": "Discovered on mesh",
                "tags": [],
                "sources": ["mesh"],
                "capabilities": _merge_capabilities(
                    ["mesh-peer", "agent"],
                    list(getattr(peer, "capabilities", []) or []),
                ),
                "has_navig": True,
                "is_self": is_self_peer,
                "peer_id": peer.node_id,
                "gateway_url": peer.gateway_url,
                "health": peer.health,
                "load": float(peer.load),
                "last_seen_age_s": peer.last_heartbeat_age_s(),
            })

        # Ensure the local machine ALWAYS appears even when it has neither
        # a YAML config nor a self mesh record (rare bootstrap case).
        if not any(r.get("is_self") for r in rows):
            self_peer = None
            try:
                self_peer = registry.self_record if registry._self else None  # type: ignore[attr-defined]
            except Exception:
                self_peer = None
            display = (self_peer.hostname if self_peer else local_hostname_raw) or "this-pc"
            rows.insert(0, {
                "id": "local",
                "name": display,
                "hostname": display,
                "user": "",
                "port": 22,
                "os": _os_id(),
                "type": "local",
                "cloud": None,
                "description": "This machine",
                "tags": [],
                "sources": ["local"],
                "capabilities": _merge_capabilities(
                    ["local", "agent"],
                    list(getattr(self_peer, "capabilities", []) or []) if self_peer else [],
                ),
                "has_navig": self_peer is not None,
                "is_self": True,
                "peer_id": self_peer.node_id if self_peer else None,
                "gateway_url": self_peer.gateway_url if self_peer else None,
                "health": self_peer.health if self_peer else "online",
                "load": float(self_peer.load) if self_peer else 0.0,
                "last_seen_age_s": (
                    self_peer.last_heartbeat_age_s() if self_peer else 0.0
                ),
            })

        # Sort: self first, then by source priority (mesh-peers next, ssh-only
        # last), then alphabetical.
        def _sort_key(r: dict) -> tuple:
            if r["is_self"]:
                return (0, "")
            if r["has_navig"]:
                return (1, r["name"].lower())
            return (2, r["name"].lower())

        rows.sort(key=_sort_key)

        # ── Host-count metadata (license enforcement hook — Phase 3.2) ────────
        # Today we return the full list (no cap). When license is wired the
        # gate lives in navig.license.quota — we'll call it here and slice.
        total_hosts = len(rows)
        try:
            from navig.license.quota import effective_host_limit  # Phase 3.2
            host_limit = effective_host_limit()
        except Exception:
            host_limit = total_hosts  # no license module yet -> full visibility

        visible = rows if host_limit >= total_hosts else rows[:host_limit]
        hidden_count = max(0, total_hosts - len(visible))

        try:
            from navig.license import current_tier_name
            tier = current_tier_name()
        except Exception:
            tier = "solo"

        return web.json_response({
            "hosts": visible,
            "total_hosts": total_hosts,
            "visible_hosts": len(visible),
            "host_limit": host_limit,
            "hidden_hosts_count": hidden_count,
            "tier": tier,
        })

    except Exception as exc:
        logger.exception("hosts endpoint failed")
        return _err(str(exc))
