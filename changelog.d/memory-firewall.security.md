- **Publication firewall: internal space plans and runtime ledgers stay private.**
  81 tracked `registry/spaces/*/.navig/plans/CURRENT_PHASE|DEV_PLAN.md` files would have
  published to the public registry mirror — the plugins mirror already withholds the same
  names, and `registry/.publish-exclude` now covers both. Three runtime ledgers tracked
  despite `.gitignore` (`.navig/refs/ledger.jsonl`, the `apps/deck` and `navig-menu`
  `operations.jsonl` copies) are untracked, `PLUGIN_WITHHELD` gained the `.navig/refs/`
  rule, and a `memory-firewall` guard test pins all three layers in both directions.
