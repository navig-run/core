- **The catalog sweep skipped providers reached through a subscription.** `navig ai models --check`
  and the daily head check decided "does this provider have a credential" by asking the API-key
  store alone — but a Claude subscription is an OAuth *connection* with no key, so `anthropic` was
  reported "not judged" while every real call to it succeeded. The gate now asks the same resolver
  a real call dispatches through (`resolve_provider_credential`, connection first).
- **All four ids in Anthropic's manifest were retired** — found the moment the sweep could see
  it: `claude-3-7-sonnet-20250219` and `claude-3-opus-20240229` answer 404, `claude-3-5-sonnet` and
  `claude-3-5-haiku` answer 410 EOL, while `claude-sonnet-4-6` / `haiku-4-5` / `opus-4-8` on the
  same credential answer. `models[0]` is the credential probe and the routing substitution default,
  so both were dead. Replaced (Sonnet first, since the router never auto-selects Opus), all four
  denylisted, and the stale "Claude 3.7 Sonnet, Claude 3.5 series" / "Claude 3.5 Sonnet / Haiku"
  descriptions updated.
- **The router's fallback tables named nine dead models — used exactly when the primary had
  already failed.** `MODE_MODEL_PREFERENCE` and `_PROVIDER_DEFAULT_MODELS` pointed every anthropic,
  groq and nvidia fallback (and xai's default) at retired ids, four of them already on the
  denylist, because no guard scanned either table. Replaced with ids that answered; the offline
  retired-model guard now scans both tables, and the sweep audits their ids (listed as `router`).
  `google`'s `gemini-1.5-flash` default is unchanged: no credential on the auditing machine.
- **Telegram `/providers` offered a 20–65 s model as the small-talk tier.** On nvidia the "30b"
  token matched `nemotron-3.5-lightning` (measured 20–65 s per call) while `gpt-oss-20b` answered in
  under 1.2 s; on groq "7b" matched `qwen3.8-27b`. The tier picker returns the first MODEL matching
  ANY token, so token order never expressed a preference — the fast model is now checked on its
  own. Measured by calling the real function for every provider: exactly those two picks change.
