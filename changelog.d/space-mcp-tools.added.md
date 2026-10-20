- **Connected Spaces over MCP — four provider-neutral tools.** `navig_space_current`
  (identity + phase + edge counts), `navig_space_related` (the shared graph payload:
  parent/links/children/inbound, availability, cycle flag), `navig_space_context`
  (provenance-carrying linked-context snapshot with focus and limit) and
  `navig_space_read` — a single-file read behind the linked-context guard: the target
  must be the active Space or reachable through its parent/link edges, match the
  edge's include/exclude globs, pass the deny list (vault/env/credentials/sessions/
  state/databases/logs) and resolve inside the Space's own folder (junctions cannot
  escape); refusals answer with the reason and the fix, never content. Same core
  library as the CLI and the deck — one engine, so an MCP client, the deck and
  `navig space` can never disagree about what a Space may show. Safety: reads are
  `moderate` (gated), the metadata readers `safe`.
