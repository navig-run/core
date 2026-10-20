# mcp

MCP server and assistant integrations.

The server exposes a **compact 15-tool routed surface by default**; the complete legacy
catalogue is one flag away.

Common commands:
- `navig mcp serve`
- `navig mcp serve --profile full`
- `navig mcp config vscode`
- `navig mcp config claude`

Profiles:
- `--profile default` — 15 domain tools; each takes an `action` argument (read/mixed/act
  domains are separate so annotations stay truthful).
- `--profile full` — every registered tool (the pre-3.28 catalogue). Use this for clients
  or scripts that call the original tool names.
- `--only <bundle[,bundle]>` — unchanged meaning: a restricted **full** catalogue of those
  bundles. Passing it overrides `--profile default`.

Security:
- A non-loopback `--transport http|websocket` bind refuses to start without `--token`.
- A loopback listener without a token advertises read-only tools only; `--insecure-local`
  restores the full local surface explicitly (never on a non-loopback bind).
- Dangerous tools over a remote exposure are denied unless
  `navig config set mcp.remote_dangerous approve` is an explicit operator decision.
- Declare `--exposure remote` when a stdio server is fronted by a proxy, so the remote
  policy applies.
- `desktop_filesystem` is root-allowlisted when exposed remotely (`mcp.files.roots`);
  empty roots mean deny-all. Local stdio keeps its trusted behaviour.

Examples:
- `navig mcp serve`
- `navig mcp serve --profile full`
- `navig mcp serve --transport stdio --exposure remote`
- `navig mcp config vscode -o` (write config to file)
