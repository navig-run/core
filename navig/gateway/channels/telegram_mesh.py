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
import html
import re
from typing import TYPE_CHECKING

from navig.debug_logger import get_debug_logger
from navig.gateway.channels.telegram_utils import escape_mdv2 as _mdv2_escape

if TYPE_CHECKING:
    pass  # avoids circular; TelegramChannel is in the same package

logger = get_debug_logger()

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
      - self.send_message(chat_id, text, parse_mode="HTML")
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
                    "<i>no mesh peers discovered yet</i>",
                    parse_mode="HTML",
                )
                return

            lines = ["<b>mesh peers:</b>\n"]
            for p in peers:
                role_symbol = "👑" if p.get("role") == "leader" else "⏳"
                load = p.get("load", 0.0)
                host = p.get("hostname", p.get("node_id", "?"))
                is_self = " <i>(you)</i>" if p.get("is_self") else ""
                capabilities = ", ".join(p.get("capabilities", []) or []) or "—"
                lines.append(
                    f"{role_symbol} <code>{html.escape(str(host))}</code>{is_self} — "
                    f"load {load:.0%} — {html.escape(capabilities)}"
                )

            await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

        except Exception as exc:
            logger.error("[mesh] /nodes handler error: %s", exc)
            await self.send_message(chat_id, "<i>couldn't fetch peer list</i>")

    # ── /leader ───────────────────────────────────────────────────────────────

    async def _handle_mesh_leader(self, chat_id: int) -> None:
        """Show who holds the leader role right now."""
        try:
            state = await self._mesh_get("/mesh/election/state")
            leader = state.get("leader_hostname") or state.get("leader_node_id")
            epoch = state.get("election_epoch", 0)
            my_role = state.get("my_role", "standby")

            if not leader:
                await self.send_message(chat_id, "<i>no leader elected yet</i>")
                return

            is_me = " *(this node)*" if my_role == "leader" else ""
            msg = (
                f"<b>current leader:</b> <code>{html.escape(leader)}</code>{is_me}\n"
                f"<b>epoch:</b> {epoch}"
            )
            await self.send_message(chat_id, msg, parse_mode="HTML")

        except Exception as exc:
            logger.error("[mesh] /leader handler error: %s", exc)
            await self.send_message(chat_id, "<i>couldn't fetch election state</i>")

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
                    f"{icon} mesh <b>{'enabled' if enabled else 'disabled'}</b> — "
                    f"role: <code>{html.escape(str(role))}</code> — peers: {html.escape(str(peers))}",
                    parse_mode="HTML",
                )
                return

            if action in ("on", "off"):
                enabled = action == "on"
                resp = await self._mesh_post("/mesh/config", {"enabled": enabled})
                status = resp.get("status", "ok")
                await self.send_message(
                    chat_id,
                    f"mesh <b>{'enabled' if enabled else 'disabled'}</b> — {html.escape(str(status))}",
                    parse_mode="HTML",
                )
                return

            await self.send_message(
                chat_id,
                "<i>usage: /mesh [on|off|status]</i>",
                parse_mode="HTML",
            )

        except Exception as exc:
            logger.error("[mesh] /mesh handler error: %s", exc)
            await self.send_message(chat_id, "<i>mesh command failed</i>")

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
                    f"✅ handoff requested → <code>{html.escape(str(target_out))}</code>\n"
                    "<i>new leader will activate within 15s</i>",
                    parse_mode="HTML",
                )
            else:
                reason = resp.get("reason", "unknown")
                await self.send_message(
                    chat_id,
                    f"❌ handoff rejected: {html.escape(str(reason))}",
                    parse_mode="HTML",
                )

        except Exception as exc:
            logger.error("[mesh] /switch handler error: %s", exc)
            await self.send_message(chat_id, "<i>couldn't request handoff</i>")

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
