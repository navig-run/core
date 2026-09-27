- **`navig telegram business alerts off` stopped working once the new deletion switches were
  used.** It wrote only the original boolean, and an explicit `mode` (set by `deletions mode …` or
  the digest card's Quiet button) wins over it — so after that, `alerts off` printed success and
  changed nothing. It now drives the mode: off → `off`; on → the default `digest` if alerts were
  off, otherwise it keeps an explicit choice (`instant` stays `instant`). `deletion_alert_enabled()`
  now answers from the mode too, which also fixes its raw `bool()` read: `navig config set …
  deletion_alert false` stores the string `"false"`, which reported ON.
