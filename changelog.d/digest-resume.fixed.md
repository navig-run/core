- **A deletion digest pending at restart was never sent — until the next deletion.** `arm_digest`
  ran only when a deletion *arrived*, so a window that was pending when the daemon stopped sat
  unreported for hours on a quiet account; the operator restarts often (five times in two days,
  measured). Channel start now resumes it (`deletions.resume_pending`) after a short delay rather
  than a full window, since those deletions already waited before the restart. Rows the pre-digest
  code marked deleted (and already announced instantly) have `deleted_at` NULL and are not re-sent.
- **The digest could send the same card twice across the two daemon processes.** Resume runs in
  both — the supervisor starts a gateway and a telegram_worker, each with its own channel, the exact
  shape that doubled every boot greeting. The flush now claims its batch cross-process with an
  atomic file create before sending, releases it on a failed send, and treats a claim older than
  its TTL as a dead sender's. ⚠ The first version keyed the claim on the window start, and a race
  test caught it failing on the **first-run path**: with no watermark stored, each process computes
  `now - window` itself, milliseconds apart, so the two got different keys and *both* sent. The key
  is now the batch's newest `deleted_at` — the one thing both processes see identically.
- **Tests wrote into the operator's real cache directory.** The session fixture isolated
  `NAVIG_CONFIG_DIR` and `NAVIG_DATA_DIR` but not `NAVIG_CACHE_DIR`, which resolves to the OS cache
  (`%LOCALAPPDATA%\navig\cache`) and derives from neither. Measured: one run of the deletion tests
  left four claim files in the live cache — and a stray claim there can block a real digest for its
  TTL. The fixture now isolates the fourth root too, pinned by `test_suite_state_isolation`.
