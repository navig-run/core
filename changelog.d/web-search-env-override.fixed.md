- **`NAVIG_WEB_SEARCH_PROVIDER` actually overrides config now** — the env var was seeded as a
  *default* and then overwritten by any `web.search.provider` value in `config.yaml`, so the
  documented system-level override was silently dead the moment the config key existed.
  `get_web_config()` re-applies it after the config merge (known provider names only — `""`,
  the onboarding `skip` sentinel and typos never displace the configured engine). Precedence is
  now: CLI `--provider` (non-`auto`) > env > config > `auto`, pinned by tests.
