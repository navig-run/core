- **`navig mcp serve --transport stdio` keeps stdout pure JSON-RPC.** The `--only`
  catalogue note printed an info line to stdout — the channel IS the protocol in stdio
  mode, so every client's line-1 parse failed (caught by the narrow-stdio Recall test
  on the gate, 2026-10-20; the banner had landed hours earlier in the compact-surface
  work). In stdio mode the note now goes to stderr; other transports keep the console
  line.
