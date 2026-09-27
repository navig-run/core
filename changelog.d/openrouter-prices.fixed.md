- **Every OpenRouter turn was costed at $0.00.** Its model ids (`anthropic/claude-sonnet-4.5`) match
  no price-table entry. Prices now come from OpenRouter's own public model list (458 models, USD
  per token, no key needed), cached locally in `navig.agent.openrouter_prices`. The cache is
  refreshed only by callers that already go online — `navig ai models --check` and the heartbeat's
  daily beat — never by a cost lookup, so an agent turn cannot stall on a price; with no cache a
  turn reads $0.00 as before. The PROVIDER decides, not the id's shape: `openai/gpt-oss-20b` is
  also an NVIDIA and a groq id on free tiers. A failed or empty fetch never replaces the existing
  cache. Measured: a 12k-in / 800-out Sonnet 4.5 turn via OpenRouter now reads $0.048.
- **OpenRouter's catalog listed a preview id OpenRouter no longer lists.** The first priced audit
  found `google/gemini-2.5-pro-preview-05-06` answering with no published price; replaced with the
  listed `google/gemini-2.5-pro`.
