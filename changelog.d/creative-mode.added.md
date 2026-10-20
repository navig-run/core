- **Creative Mode — proactive capability intelligence (`navig creative`).** A deterministic
  layer over the registries NAVIG already ships: a rebuildable capability index
  (`data_dir()/creative/`, provenance `source|runtime`, token-ranked `navig creative index --q`),
  a reuse-first decision ladder (reuse → configure → compose → extend → create → no-op — reuse is
  checked first, so a wrapper is never generated for an existing tool; thresholds ≥5 repeats/7d,
  ≥3 failures/7d, ≥3 sequences/14d, below them is an explicit no-op), and detectors over your own
  operation history and goal state (no chat content). Proposals persist with content-addressed ids
  — dismissed/built are never re-proposed — rate-capped (≤5 open, ≤2/day), announced once through
  the existing notify dedup, and surfaced as a **Dashboard Suggestions card** (evidence, solution,
  reuse, benefit, risk, assumptions; Build/Dismiss; empty state first-class) plus the new
  `creative_proposal` notification type (deck default). The `creative_scan` monitor (default on,
  idle unless Creative Agent Mode, paced from the last scan) runs every 6 h. Building writes a
  SKILL.md through the standard skill contracts — shared `lint_skill_path` with the CLI, runtime
  parser security checks, atomic write with rollback on failure, overlap preflight — and
  **publishing** is local-only until `publish-enable` allow-lists a destination: a deterministic
  eight-check gate (allow-list · private visibility · branch · unchanged HEAD · no force · fresh
  foreign lock · credentials · secret scan) with path-scoped commits so unrelated work in the
  destination survives byte-for-byte. Surfaces: `navig creative status|index|list|show|build|
  dismiss|publish|publish-enable|publish-disable`, the Settings → Brain publishing section, and
  the `creative` agent tool (lookup/decide in both modes, propose Creative-only — verdicts are
  re-derived from the index, never model-supplied). No LLM imports under `navig/creative/`
  (guard-tested); mode changes never alter tool schemas or approvals.
