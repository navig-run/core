- **A second proactive send inside one clock tick was invisible to the other process.** The
  cross-process cooldown refresh trusts a `(mtime, size)` stamp, and neither half can tell two
  writes apart until the clock has moved between them: two same-length writes inside one
  timestamp tick — on Windows whatever the busiest process asked for, 1ms with a browser open,
  15.6ms without — leave the stamp identical, so the reader kept the first value (the suite's
  "the second write was not adopted" flake). The stamp is now trusted only once the file is
  older than any timestamp granularity in use (2s); inside that window the refresh re-reads a
  few hundred bytes of JSON, once per heartbeat. Pinned deterministically by
  `test_two_writes_inside_one_clock_tick_are_both_adopted`, which pins the second file's mtime to
  the first's the way a coarse clock would, and a settled file is still served from the stamp.
- **The `Paper mail` extension card rendered in English under a Russian or French bot.** The
  `paperwork` extension shipped without `tgext.paperwork.*` keys in any locale, so the
  extensions list and index — every other card localized — carried one English row.
