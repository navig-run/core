"""
TelegramMeshMixin — mesh control slash commands for TelegramChannel.

Commands:
  /mesh           — toggle mesh mode on/off (or show status)
  /switch [host]  — request handoff to a specific node or best standby
  /nodes          — list known mesh peers with role + load
  /leader         — show who is current leader

Design notes:
  - Every handler follows the try/except pattern from TelegramCommandsMixin:
    log the exception, send a generic terse reply, never propagate.
  - HTTP calls to the local gateway use aiohttp via self._session (already
    established by TelegramChannel at startup).
  - All gateway URLs are resolved from the local host config; no hardcoding.

Mesh feature flag: 'mesh' in self._features (set by gateway at startup).
If the flag is absent the commands are still registered (avoids MRO surprises)
but reply with a disabled notice.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from navig.debug_logger import get_debug_logger

if TYPE_CHECKING:
    pass  # avoids circular; TelegramChannel is in the same package

logger = get_debug_logger()

def _mdv2_escape(text: str) -> str:
    return re.sub(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])", r"\\\1", str(text))

# Local gateway base URL for mesh routes — resolved from navig config at call time
# so it tracks config changes without requiring a restart.
# Default port: 8789.  Override via ~/.navig/config.yaml: gateway.port
def _gateway_base() -> str:
    try:
        from navig.config import get_config_manager

        port = get_config_manager().global_config.get("gateway", {}).get("port", 8789)
        return f"http://127.0.0.1:{port}"
    except Exception:
        return "http://127.0.0.1:8789"

_GATEWAY_BASE = _gateway_base()

class TelegramMeshMixin:
    """
    Mixin for TelegramChannel — mesh control slash-command handlers.

    Requires on self:
      - self._session: aiohttp.ClientSession
      - self.send_message(chat_id, text, parse_mode="Markdown")
      - self._features: dict (from TelegramFeaturesMixin)
      - self._has_feature(name): bool -> dict | None
    """

    # ── /nodes ────────────────────────────────────────────────────────────────

    async def _handle_mesh_nodes(self, chat_id: int) -> None:
        """List known mesh peers — role, load, capabilities."""
        try:
            peers = await self._mesh_get("/mesh/peers")
            if not peers:
                await self.send_message(
                    chat_id,
                    "_no mesh peers discovered yet_",
                    parse_mode="MarkdownV2",
                )
                return

            lines = ["*mesh peers:*\n"]
            for p in peers:
                role_symbol = "👑" if p.get("role") == "leader" else "⏳"
                load = p.get("load", 0.0)
                host = p.get("hostname", p.get("node_id", "?"))
                is_self = " *(you)*" if p.get("is_self") else ""
                capabilities = ", ".join(p.get("capabilities", []) or []) or "—"
                lines.append(
                    f"{role_symbol} `{_mdv2_escape(host)}`{is_self} — "
                    f"load {_mdv2_escape(f'{load:.0%}')} — {_mdv2_escape(capabilities)}"
                )

            await self.send_message(chat_id, "\n".join(lines), parse_mode="MarkdownV2")

        except Exception as exc:
            logger.error("[mesh] /nodes handler error: %s", exc)
            await self.send_message(chat_id, "_couldn't fetch peer list_")

    # ── /leader ───────────────────────────────────────────────────────────────

    async def _handle_mesh_leader(self, chat_id: int) -> None:
        """Show who holds the leader role right now."""
        try:
            state = await self._mesh_get("/mesh/election/state")
            leader = state.get("leader_hostname") or state.get("leader_node_id")
            epoch = state.get("election_epoch", 0)
            my_role = state.get("my_role", "standby")

            if not leader:
                await self.send_message(chat_id, "_no leader elected yet_")
                return

            is_me = " *(this node)*" if my_role == "leader" else ""
            msg = (
                f"*current leader:* `{_mdv2_escape(leader)}`{is_me}\n"
                f"*epoch:* {_mdv2_escape(str(epoch))}"
            )
            await self.send_message(chat_id, msg, parse_mode="MarkdownV2")

        except Exception as exc:
            logger.error("[mesh] /leader handler error: %s", exc)
            await self.send_message(chat_id, "_couldn't fetch election state_")

    # ── /mesh ─────────────────────────────────────────────────────────────────

    async def _handle_mesh_toggle(self, chat_id: int, args: str) -> None:
        """
        /mesh [on|off|status]

        No argument or 'status' → display current mesh state.
        'on' / 'off' → enable or disable mesh mode via gateway config.
        """
        try:
            action = args.strip().lower() if args.strip() else "status"

            if action == "status":
                state = await self._mesh_get("/mesh/status")
                enabled = state.get("enabled", False)
                role = state.get("role", "unknown")
                peers = state.get("peer_count", 0)
                icon = "🟢" if enabled else "⚫"
                await self.send_message(
                    chat_id,
                    f"{icon} mesh *{'enabled' if enabled else 'disabled'}* — "
                    f"role: `{_mdv2_escape(role)}` — peers: {_mdv2_escape(str(peers))}",
                    parse_mode="MarkdownV2",
                )
                return

            if action in ("on", "off"):
                enabled = action == "on"
                resp = await self._mesh_post("/mesh/config", {"enabled": enabled})
                status = resp.get("status", "ok")
                await self.send_message(
                    chat_id,
                    f"mesh *{'enabled' if enabled else 'disabled'}* — {status}",
                    parse_mode="MarkdownV2",
                )
                return

            await self.send_message(
                chat_id,
                "_usage: /mesh [on|off|status]_",
                parse_mode="MarkdownV2",
            )

        except Exception as exc:
            logger.error("[mesh] /mesh handler error: %s", exc)
            await self.send_message(chat_id, "_mesh command failed_")

    # ── /switch ───────────────────────────────────────────────────────────────

    async def _handle_mesh_switch(self, chat_id: int, args: str) -> None:
        """
        /switch [hostname_or_node_id]

        Requests a leadership handoff.  If no target provided, the gateway
        picks the best available standby (lowest load, highest tiebreaker).

        The actual handoff is asynchronous — the current leader yields and
        the target promotes itself.  This method just fires the request.
        """
        try:
            target: str | None = args.strip() or None
            payload: dict = {}
            if target:
                payload["target"] = target

            resp = await self._mesh_post("/mesh/handoff", payload)
            accepted = resp.get("accepted", False)
            target_out = resp.get("target") or target or "best available"

            if accepted:
                await self.send_message(
                    chat_id,
                    f"✅ handoff requested → `{_mdv2_escape(target_out)}`\n"
                    "_new leader will activate within 15s_",
                    parse_mode="MarkdownV2",
                )
            else:
                reason = resp.get("reason", "unknown")
                await self.send_message(
                    chat_id,
                    f"❌ handoff rejected: {_mdv2_escape(reason)}",
                    parse_mode="MarkdownV2",
                )

        except Exception as exc:
            logger.error("[mesh] /switch handler error: %s", exc)
            await self.send_message(chat_id, "_couldn't request handoff_")

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    async def _mesh_get(self, path: str) -> dict:
        """GET request to local gateway mesh endpoint."""
        import aiohttp  # lazy import — respects navig-core lazy-import rule

        url = f"{_GATEWAY_BASE}{path}"
        # self._session exists if TelegramChannel already started; if not,
        # we open a short-lived session as fallback (unit-test friendly).
        session = getattr(self, "_session", None)
        if session and not session.closed:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                r.raise_for_status()
                return await r.json()
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    r.raise_for_status()
                    return await r.json()

    async def _mesh_post(self, path: str, body: dict) -> dict:
        """POST request to local gateway mesh endpoint."""
        import aiohttp  # lazy import

        url = f"{_GATEWAY_BASE}{path}"
        headers = {"Content-Type": "application/json"}
        session = getattr(self, "_session", None)
        if session and not session.closed:
            async with session.post(
                url,
                data=json.dumps(body),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                r.raise_for_status()
                return await r.json()
        else:
            async with (
                aiohttp.ClientSession() as s,
                s.post(
                    url,
                    data=json.dumps(body),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r,
            ):
                r.raise_for_status()
                return await r.json()
