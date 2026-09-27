- **The model price table, audited against each provider's own pricing page (2026-09-27).**
  `o3` was billed at its pre-cut $10/$40 (now $2/$8), Opus 4.5 carried Opus 4's $15/$75 (it is
  $5/$25), Gemini 2.5 Pro's output was half the real price and Gemini 2.5 Flash's output 8x too
  low (it carried 1.5 Flash's numbers). Two provider defaults had **no price at all**, so every
  turn on them read $0.00: `grok-4.6` (xAI's catalog head) and the entire Claude 5 family (Opus
  5.5 is priced on its own, not as a variant of Opus 5). Cached-input prices were added where the
  pages list them. Entries no current page lists (`grok-3*`, Mistral, `gemini-1.5*`) are left as
  they were and marked unverified; the table's header now names the four source pages and the date.
- **The Claude 5 family never used prompt caching.** It was missing from the cacheable-model
  list, so the agent sent no `cache_control` and paid full input price on every turn for a prefix
  the API caches at 0.1x (0.05x on Opus 5.5). The check now uses the cost trackers' variant rule,
  which also stops a bare `claude` from reading as cacheable.
