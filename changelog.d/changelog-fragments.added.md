- **Changelog fragments: `core/changelog.d/<slug>.<kind>.md`, one new file per change, folded
  in by `npm run changelog:assemble`.** `core/CHANGELOG.md` is the hottest file in the repo (189
  commits in 60 days) and every change inserted its entry at the same line — the top of
  `[Unreleased] > ### Added` — so two concurrent branches collided on every rebase. `merge=union`
  made the *local* rebase clean, but GitHub's server-side merge runs no merge driver, so a PR still
  showed as conflicting and cost a rebase plus a full pre-push gate per collision (measured: four
  in three hours across two PRs, and no fixed insertion slot escapes it). A fragment is a new file,
  so no two changes touch the same path and GitHub merges them without a rebase. The assembler
  (`core/tools/changelog_assemble.py`, stdlib-only) is deterministic (by slug, newest section
  first), idempotent (no fragments → the file is byte-identical), creates a missing `### <Kind>`
  in Keep a Changelog order, and refuses a malformed fragment — wrong kind, prose instead of a
  list entry, a conflict marker — naming the file and why. `npm run changelog:check` validates;
  the gate runs the same check on every fragment on disk, selected from the fragment's own path.
  Other packages keep dated entries in their own `CHANGELOG.md` — their traffic never earned it.
