- **Pilot migration run: the last private changelog retired; two store release-notes
  indexed.** The navig-forge proposal sample (`.navig/archive/cleanup-2026-07-03/…`)
  imported as a `sample_artifact` and retired to the vault backup after its single
  true consumer (RESTORE.md) was re-pointed; `nuvopad/web-git` (13 entries) and
  `snapthos/public` (1 entry) imported and **retained** as release notes (registered
  in the spaces registry — they had `.navig/` but no id). Two defects the pilots
  exposed are fixed: release headers with a non-date suffix (`## [1.0.0] — Unreleased`,
  `## [3.38.34] — AI-Proposed Enhancement`) matched no parser family and were silently
  skipped; and the consumer scan matched the BARE basename anywhere — 49 "consumers"
  for a nested sample, 48 of which point at `core/CHANGELOG.md`. The scan is now
  path-aware (≥2-segment tails, same-directory bare names, root files unchanged) and
  skips its own source; `classify()` gained `sample_artifact` (self-declared samples
  are retirable without inheriting the release-notes shield from a semver title).
