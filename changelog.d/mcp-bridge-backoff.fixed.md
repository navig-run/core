- **An absent MCP bridge no longer logs WARN+ERROR every minute for the daemon's lifetime.**
  The telegram worker ran its own 60 s reconnect loop for the `vscode-copilot` bridge and
  called `connect_client()` — the explicit path, which resets the registry's backoff — so
  the registry's 1 → 30 min backoff and quiet-after-3 logging never engaged. With the
  bridge's only server (`navig-grid`) archived, that was ~2,500 of 2,712 `telegram-bot.log`
  lines. The worker now registers the client once and the registry's health sweep owns
  reconnection; startup no longer connects twice; shutdown stops the MCP manager; and a
  client absent for three backoff rounds logs its connect failures at DEBUG. The
  undocumented `bridge.mcp_reconnect_interval` key is no longer read.
