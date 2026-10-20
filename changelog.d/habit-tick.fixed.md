- **A habit ticked from the deck is written to the space's `habits.csv`, and the file is
  replaced atomically.** The tick only stamped the job's `last_run`, which holds a single day,
  so a streak kept by tapping the deck reset every day and `navig habit streak` never saw it.
  A value already logged from the CLI for today is kept. `habits.csv` was truncated and
  rewritten in place, so an interrupted write could lose the whole history; it is now written
  to a temporary file and swapped in.
