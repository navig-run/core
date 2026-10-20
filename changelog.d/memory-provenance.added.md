- **Recall now tracks what happened to a change, not only what was decided.** Schema v3
  adds a delivery `lifecycle` on every record (`proposed · implemented_uncommitted ·
  committed · merged · verified · deployed · failed · superseded · abandoned · unknown`) —
  a separate axis from decision status and verification, derived deterministically in
  `navig.recall.lifecycle` (git ancestor verdicts, explicit outcome events, verify results;
  zero LLM calls; an assistant claim never yields an `observed` basis). Evidence carries an
  exact `locator` (line range + file hash) for file-backed sources, and a metadata-only
  `commits` cache (subject/author/dates/branch/file names — never diffs) backs provenance
  queries. `navig recall verify` re-derives lifecycles after every run; existing banks
  upgrade in place (`v2 → v3`, old rows default to `unknown`).
