- **`navig mcp serve` never runs the onboarding wizard.** The MCP transport writes JSON-RPC
  on stdout; a first-run wizard on a clean machine printed a 13-step banner and a telemetry
  ping *before* `initialize`, which mcp-proxy happened to buffer but a direct stdio client
  would see as a corrupted stream. `mcp` now joins `config`/`service` in the skip list, so a
  container or a client-spawned server starts straight at the handshake.
- **`navig <plugin command>` activates instead of printing homework.** An unknown command
  that a shipped plugin provides is installed (or enabled) and then re-run; non-interactive
  callers still get the copy-paste command, and `NAVIG_NO_AUTO_PLUGIN=1` / `NAVIG_AUTO_PLUGIN=1`
  override either way.
- **`initialize` now carries `instructions`**, so a client shows the model what NAVIG is for
  before it picks a tool, instead of meeting 148 names with no context.
- **MCP argument validation, tool titles, safety annotations and strict schemas** are included
  (shipped earlier in 3.27.3, restored here after a concurrent tree reset dropped them).
