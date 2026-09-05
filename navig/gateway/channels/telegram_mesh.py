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

Reachability: TelegramChannel does NOT inherit this mixin — its runtime MRO is
[TelegramChannel, object]. The slash dispatcher resolves a registry entry's handler
against the channel first and then against the known mixins via functools.partial,
which is how these four commands are reached.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from navig._daemon_defaults import _GATEWAY_PORT
from navig.debug_logger import get_debug_logger
from navig.gateway.channels.telegram_utils import escape_mdv2

if TYPE_CHECKING:
    pass  # avoids circular; TelegramChannel is in the same package

logger = get_debug_logger()

_mdv2_escape = escape_mdv2

# Local gateway base URL for mesh routes — resolved at CALL time (not import
# time) so it follows the live self-healed port from ~/.navig/gateway.json and
# tracks config changes without requiring a restart.
def _gateway_base() -> str:
    try:
        from navig.gateway_client import gateway_base_url

        return gateway_base_url()
    except Exception:
        return f"http://127.0.0.1:{_GATEWAY_PORT}"

class TelegramMeshMixin:
    """
    Mixin for TelegramChannel — mesh control slash-command handlers.

    Requires on self:
      - self._session: aiohttp.ClientSession
      - self.send_message(chat_id, text, parse_mode="HTML")

    (An earlier version of this list also claimed `self._features` and
    `self._has_feature`. Neither is used by any handler here, and neither exists on
    TelegramChannel — the claim would have sent the next reader hunting a dependency
    that was never real.)
    """

    # ── /nodes ────────────────────────────────────────────────────────────────

    async def _handle_mesh_nodes(self, chat_id: int) -> None:
        """List known mesh peers — role, load, capabilities."""
        try:
            payload = await self._mesh_get("/mesh/peers")
            # The route answers {"self": {...}, "peers": [...]}. Include self, because a
            # single-node mesh has an empty peers list and "no peers discovered" would be
            # the wrong thing to say about a node that is itself up and leading.
            peers = list(payload.get("peers") or [])
            me = payload.get("self")
            if isinstance(me, dict):
                peers.insert(0, {**me, "is_self": True})
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
        """Show which node currently holds leadership.

        Derived from `/mesh/peers` rather than a dedicated endpoint: the record already
        carries `role`, and `/mesh/election/state` — which this used to call — is not a
        route this gateway serves. Asking the endpoint that exists beats inventing one.
        """
        try:
            payload = await self._mesh_get("/mesh/peers")
            nodes = list(payload.get("peers") or [])
            me = payload.get("self")
            if isinstance(me, dict):
                nodes.insert(0, {**me, "is_self": True})

            leader = next((n for n in nodes if str(n.get("role", "")).lower() == "leader"), None)
            if not leader:
                await self.send_message(
                    chat_id,
                    "<i>no leader elected yet</i>",
                    parse_mode="HTML",
                )
                return

            host = leader.get("hostname") or leader.get("node_id", "?")
            mine = " <i>(you)</i>" if leader.get("is_self") else ""
            await self.send_message(
                chat_id,
                f"👑 leader: <code>{html.escape(str(host))}</code>{mine}",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error("[mesh] /leader handler error: %s", exc)
            await self.send_message(chat_id, "<i>couldn't determine the leader</i>")

    # ── /mesh ─────────────────────────────────────────────────────────────────

    async def _handle_mesh_toggle(self, chat_id: int, text: str = "") -> None:
        """
        /mesh [on|off|status]

        No argument or 'status' → display current mesh state.
        'on' / 'off' → enable or disable mesh mode via gateway config.

        Takes ``text`` — the whole ``/mesh on`` — because that is what the live
        dispatcher forwards to every parameterised handler. The previous ``args``
        parameter was supplied by nothing, so wiring this command as-written would
        have raised TypeError on the first press.
        """
        try:
            arg = text.split(" ", 1)[1].strip() if " " in text else ""
            action = arg.lower() or "status"

            if action == "status":
                # From /mesh/peers, the one mesh route this gateway serves. `/mesh/status`
                # — which this used to call — does not exist, so it answered 404 and the
                # user saw a generic failure.
                payload = await self._mesh_get("/mesh/peers")
                me = payload.get("self") or {}
                peer_count = len(payload.get("peers") or [])
                role = me.get("role", "unknown")
                # A node that knows its own record is participating; that is the honest
                # reading of "enabled" from the data actually available here.
                enabled = bool(me)
                icon = "🟢" if enabled else "⚫"
                await self.send_message(
                    chat_id,
                    f"{icon} mesh <b>{'active' if enabled else 'inactive'}</b> — "
                    f"role: <code>{html.escape(str(role))}</code> — peers: {peer_count}",
                    parse_mode="HTML",
                )
                return

            if action in ("on", "off"):
                # Deliberately NOT implemented against an invented route. Toggling mesh at
                # runtime needs a config-mutation endpoint this gateway does not serve
                # (`/mesh/config` is a 404), and adding one is a product decision — mesh
                # scope is gated on Phase 2. Say so, with the command that does work.
                await self.send_message(
                    chat_id,
                    "<i>this build can't toggle mesh from chat — no config endpoint. "
                    "Set it with</i> <code>navig config set mesh.enabled "
                    f"{'true' if action == 'on' else 'false'}</code><i> and restart the "
                    "gateway.</i>",
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

    async def _handle_mesh_switch(self, chat_id: int, text: str = "") -> None:
        """
        /switch [hostname_or_node_id]

        Reports honestly instead of pretending. A leadership handoff needs the current
        leader to yield and the target to promote itself — a state-machine operation
        behind a `/mesh/handoff` endpoint this gateway does not serve. The previous
        implementation POSTed to that 404 and told the user "handoff rejected: unknown",
        which reads as the mesh refusing rather than the feature not existing.
        """
        target = (text.split(" ", 1)[1].strip() if " " in text else "") or None
        named = f" to <code>{html.escape(target)}</code>" if target else ""
        await self.send_message(
            chat_id,
            f"<i>handoff{named} isn't available in this build — no mesh handoff "
            "endpoint. Use</i> <code>/nodes</code><i> to see peers and </i>"
            "<code>/leader</code><i> for the current leader.</i>",
            parse_mode="HTML",
        )

    @staticmethod
    def _unwrap(body: dict) -> dict:
        """Return the payload from the gateway's standard `{ok, data, error}` envelope.

        Every `navig.gateway.routes` handler answers through `json_ok(...)`, so the JSON
        body is the envelope and never the payload. Reading it directly is what made
        `/nodes` iterate the envelope's KEYS ("ok", "data", "error") and then call
        `.get("role")` on a string.
        """
        if isinstance(body, dict) and "ok" in body and "data" in body:
            data = body.get("data")
            return data if isinstance(data, dict) else {"data": data}
        return body if isinstance(body, dict) else {}

    async def _mesh_get(self, path: str) -> dict:
        """GET request to local gateway mesh endpoint."""
        import aiohttp  # lazy import — respects navig-core lazy-import rule

        url = f"{_gateway_base()}{path}"
        # self._session exists if TelegramChannel already started; if not,
        # we open a short-lived session as fallback (unit-test friendly).
        session = getattr(self, "_session", None)
        if session and not session.closed:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                r.raise_for_status()
                return self._unwrap(await r.json())
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    r.raise_for_status()
                    return self._unwrap(await r.json())

