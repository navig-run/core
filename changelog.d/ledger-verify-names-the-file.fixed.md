- **`navig ledger verify` names the file it verified.** Run from inside a checkout, it reads that
  repo's scratch `.navig/history/operations.jsonl` — so a scratch ledger's breakage read as "the
  audit spine is broken" while the operator's own ledger was intact (5,896 chained entries, zero
  breaks). The verdict now prints `ledger: <path>` first, which makes it attributable.
