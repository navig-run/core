- **`navig space context --brief` — every session starts with its neighborhood.**
  The SessionStart hook (`navig wire`'s `.claude/settings.json`) now runs the brief
  instead of a bare `space current`: active space + phase title, one `●`/`○` line per
  parent/link with file counts and unavailable reasons, a cycle warning when present,
  and a count line — capped at ten lines. It **never fails**: any reader error degrades
  to the old one-liner, because a broken relation must not break session start.
  Existing wired spaces pick the new command up on their next `navig wire` (the hook
  file is write-once by design).
