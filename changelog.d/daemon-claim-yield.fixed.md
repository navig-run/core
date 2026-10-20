- **A daemon boot no longer yields forever to a sibling that never claims the boot** — the
  concurrent-boot yield now waits (up to 30 s) for the sibling to claim the pid file and only
  then defers; a sibling that never claims is treated as wedged, reaped by the boot sweep and
  the boot takes over. Unconditional yielding ran before the sweep, so a sibling wedged before
  its first claim was untouchable — measured 2026-10-17 on a fresh Windows join where every
  scheduled-task boot yielded, no pid file was ever written and the gateway stayed dark for
  hours. The pid file is now claimed atomically (hard-link, EEXIST on a lost race) with a
  post-claim verify, and a yield logs the sibling's command line and age.
