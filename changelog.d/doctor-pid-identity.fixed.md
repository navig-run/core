- **`navig doctor` stopped reporting two false failures on a client machine.** A stale
  `gateway.pid` whose pid number Windows had recycled made doctor announce "a local brain is
  running" beside a brain link (a two-authority alarm against a browser), and made the runtime
  inventory state that 36 plugin sources were "not in the daemon's environment" when the
  daemon's Python had never been read at all. The pid file's process is now verified to be
  NAVIG (`python -m navig …`), a stale file is named with its new owner in a warning row
  instead of being trusted, and an unknown daemon yields **one** honest warning rather than a
  per-plugin claim about an environment nothing measured. Measured here: 42 failed rows → 2,
  and the two that remain are the shared checkout's version, not a health problem.
