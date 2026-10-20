- **Focus Modes retired — one agent mode `standard | creative` (owner decision 2026-10-20).**
  The six chat-tone modes (work · deep-focus · planning · creative · relax · sleep) are gone from
  every surface: Settings → Brain and the deck picker now offer **Agent Mode** with two choices
  (Standard = everyday help; Creative = proactive capability intelligence), Telegram `/mode` lists
  the same two and answers retired names with a "retired" notice while behaving as Standard, and
  all six stored values migrate to `standard` on load — the old tone-`creative` never becomes the
  capability Creative. Notification suppression decoupled: a new `dnd_enabled` preference carries
  do-not-disturb (migrated deep-focus/sleep users stay held; the evening DND buttons write it
  directly), and the five independent quiet-hours systems are untouched. The live agent prompt now
  carries the mode (previously only a dead legacy path did); tool schemas are byte-identical across
  modes, so switching can never widen the permission surface. Legacy reads stay graceful
  (`POST /mode` accepts a stale client's old id as `standard` + `migrated`), obsolete choices are
  removed from all seven locales (`settings.brain.mode.*` replaces `settings.brain.focus.*`).
