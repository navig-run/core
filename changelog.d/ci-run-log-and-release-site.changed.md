- **The local gate's run log is the record, and a dead run says so.** `scripts/ci-local.mjs`
  writes `.dev/logs/ci-local-last-run.json` before and after every step, so a run the harness
  killed mid-way names the step it never finished; a pytest killed mid-session is recorded as
  `died`, not `fail` — "killed at the ceiling" and "the tests failed" are different
  investigations. A gate result with no `N passed · M failed` summary line is not a result.
  A cwd leak in the suite is now reported by the test that caused it, and repaired, by the same
  autouse guard that already did this for env vars. (#1411, #1437, #1509)
- **The site documents the RELEASED navig, and `latest.json` has one writer.** The CLI
  reference and Bay catalog on navig.run are snapshots stamped with the release version and
  source commit, guarded against drifting from the tagged release, and the builders say when
  they overwrite a published snapshot. `latest.json` — what the installers read — is written by
  one tool (`core/tools/_version_sync.py`), keeps one schema, verifies the download URL with a
  HEAD request before linking to it, and preserves `released_at` across re-runs. (#1449, #1461,
  #1480)
