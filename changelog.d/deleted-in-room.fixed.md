- **Two deck surfaces reported a failed load as "nothing here".** The room's catalog message
  list and its media grid both did `if (r.ok && r.data) setX(...)` with no else while clearing
  `loading` unconditionally — so a dead daemon or a 500 rendered "No catalogued messages." and
  "No media.", the exact words a genuinely empty room produces. Both now distinguish the two and
  offer **Try again**.
  ⚠ The deck's own `load-failure-honesty` guard could not see these: it polices `[]`-initialised
  state and deliberately skips `null`-initialised sites as a per-site judgement ("null renders
  whatever null renders"). That reasoning has an exception worth writing down — **a null state
  stops being ambiguous the moment a separate loading flag is cleared unconditionally**, because
  null can then no longer render a spinner forever; it necessarily renders the empty copy.
  Measured across the deck (138 files, 39 `.then` blocks): **8 sites** matched that narrowing,
  and 2 were these. The other 6 live in unrelated subsystems — `wallet-section` renders **zero
  balances** for a wallet that failed to load, `settings-section` claims no model-tier override
  is set, plus context-app, notification-settings and the nodes list — so they are a separate
  sweep, not drive-by edits from a Telegram change. The guard for this shape belongs with that
  sweep: adding it now would need a 6-entry baseline in other people's code, which is how a
  baseline becomes a place to hide things.
