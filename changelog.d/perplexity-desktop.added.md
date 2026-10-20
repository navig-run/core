- **`navig perplexity` — the Perplexity desktop app as an agent research desk.**
  The app is a Chrome shell, so it can be driven over CDP: `ask` types the question into the
  running window, waits for the composer to stop streaming (the app swaps Submit for *Stop
  response*) and for the answer text to settle, then returns the text plus its citation URLs
  — the agent spends the app's subscription instead of its own tokens. `model`/`models` read
  and switch the composer's model (a submenu entry is a gated tier and is refused by name,
  never silently substituted); `new`/`threads`/`open` work the sidebar conversations, and
  `attach` opens the app with a debug port when it is not running. It never kills the app —
  a running instance without a port comes back as "quit it once, then attach".
- Every verb takes `--json`, prints the answer alone on stdout, and exits non-zero with a
  reason **and** a hint, so an agent can call it from a shell and trust the result.
  A timeout that lands mid-answer returns `partial: true` with a warning instead of a
  fabricated "done".
- **MCP tools `perplexity_ask`, `perplexity_models`, `perplexity_model`, `perplexity_status`**
  expose the same operations to an agent with no shell; `status`/`models` are annotated
  read-only, `ask` is open-world (the question leaves the machine).
