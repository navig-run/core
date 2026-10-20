- **Voice profiles, a voice policy and a daily budget for paid speech.** Voice profiles are
  universal presets with tool `speech` (provider, model, voice, language, speed, pitch,
  ElevenLabs stability / similarity, delivery note, pronunciation rules). Six builtins:
  French narrator, Russian deep narrator, English reader, calm audiobook, news presenter,
  premium narrator. `voice.policy` (auto · free · premium · offline) is enforced by the daemon
  for every client. `voice.daily_char_budget` refuses a paid read-aloud request before the
  provider is called (cache hits are free), and `voice.paid_fallback` (off by default) lets
  Auto retry a failed free voice on a paid one within the budget. New
  `GET /api/deck/voice/usage` and `POST /api/deck/voice/settings`; `/speak` takes
  `profile`; the catalog carries profiles, settings and per-provider prices with their source
  and date. CLI: `navig voice speak --profile`, `navig voice policy`,
  `navig voice budget`, `navig voice usage`.
