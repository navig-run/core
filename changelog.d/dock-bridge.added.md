- **Agents can use your own browser — `dock_*` MCP tools and `navig dock`.** With the NAVIG
  Dock's **Let AI agents use this browser** switched on, `navig mcp serve` exposes
  `dock_status`, `dock_tabs`, `dock_open`, `dock_snapshot`, `dock_text`, `dock_act` (up to 12
  validated steps per call, the side panel's own vocabulary) and `dock_release`. Calls go
  `POST /dock/call` (gateway bearer) → one persistent, token-authenticated WebSocket the Dock
  holds (`/api/deck/dock/bridge`): ~1 ms transport, ~6 ms per MCP call end to end. Agents are
  confined to the **NAVIG agents** tab group; the user's Stop cancels and pauses them; a call
  cut off by a disconnect reports `effects: "uncertain"` and is never replayed.
  `navig dock status|unpair` show and reset the pairing.
