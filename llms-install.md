# Installing NAVIG as an MCP server (for AI agents)

This file exists so an agent (Cline, Roo, Goose, Kilo, Claude Code, …) can install and
verify NAVIG's MCP server from the repository alone, with no human step.

NAVIG is a **local-first operator MCP server**. It exposes your own machine and your own
data to an MCP client: recall search over sessions and files, an encrypted local vault, SSH
to your own hosts, and gated shell/browser/device tools. Nothing is hosted, there is no
account, and no API key is required.

- Package: `navig` on PyPI (<https://pypi.org/project/navig/>)
- Tools: **15 by default** (a compact routed surface with `action` arguments); 155 with `--profile full`
- Transport: stdio (`navig mcp serve --transport stdio`)
- License: Apache-2.0 · Python 3.10+ · macOS / Windows / Linux

Newer clients get the compact surface by default. If you need the pre-3.28 tool names
(`navig_list_hosts`, `recall_search`, …), add `--profile full` to the launch arguments:

```json
"args": ["--from", "navig", "navig", "mcp", "serve", "--transport", "stdio", "--profile", "full"]
```

The compact surface is fifteen domain tools (`navig_fleet`, `navig_knowledge`,
`navig_recall`, `navig_memory`, `navig_runtime`, `navig_automation`, `navig_web`,
`navig_browser_debug`, `navig_browser_user`, `navig_connectors`, and separate read/act
domains for memory, runtime actions, Bay/blocks, browser actions and connector writes).
Each takes an `action` argument; the read domains are genuinely read-only.

## 1. Run it without installing anything (preferred)

```bash
uvx --from navig navig mcp serve --transport stdio
```

`uv` resolves the package in an isolated environment, so nothing is added to the system.

## 2. Or install the CLI once

```bash
pipx install navig        # or: uv tool install navig
navig --version           # expect 3.27.2 or newer
```

## 3. Client configuration

Add this to the MCP client's configuration (stdio transport):

```json
{
  "mcpServers": {
    "navig": {
      "command": "uvx",
      "args": ["--from", "navig", "navig", "mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

If the CLI is installed already, the equivalent is:

```json
{
  "mcpServers": {
    "navig": {
      "command": "navig",
      "args": ["mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

## 4. Verify it works

The server speaks MCP on stdio. A successful `tools/list` returns, among others,
`navig_list_hosts`, `navig_host_info`, `navig_search_wiki`, `navig_read_wiki_page`,
`navig_list_databases`.

From a shell:

```bash
navig doctor           # environment + engine check, no network required
navig mcp serve --print-config   # prints the connector URL for HTTP-based clients
```

## 5. What it will and will not touch

- Configuration and state live in `~/.navig` (override with `NAVIG_CONFIG_DIR` / `NAVIG_DATA_DIR`).
- The server reads nothing outside that directory until a tool is invoked, and every
  mutating tool is gated by NAVIG's approval layer.
- Uninstall: `pipx uninstall navig` (or `uv tool uninstall navig`); delete `~/.navig` to
  remove data. Nothing is left in the system PATH beyond the shim those tools create.

## 6. Links

- Repository: <https://github.com/navig-run/core>
- Documentation: <https://navig.run/docs>
- MCP registry entry: `io.github.navig-run/navig`
