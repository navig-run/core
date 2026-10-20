- **DeepSeek Search is the system search engine** — new `deepseek_search` provider for
  `web_search()` (POST `/v1/tools/search`, key in the vault under `deepseek_search/api_key`,
  alias `deepseek`), first in the `auto` keyed cascade. Every surface reports the engine that
  actually answered: `navig search --json` now prints `provider`, the agent tool output and
  Telegram's `[Web context — via …]` header carry it, and a **Search engine** section on
  NAVIG OS Settings → Personalization shows the active engine, available engines with key
  presence (booleans only — never key material), the fallback cascade, and a Test button
  (`GET/POST /api/deck/search/status|config|test`). NAVIG OS agent sessions search through the
  new `POST /api/deck/search` proxy, so core, CLI, MCP, Telegram and NAVIG OS all share ONE
  backend and ONE config key (`web.search.provider`); the proxy honors the
  `web.search.enabled` kill switch (403).
