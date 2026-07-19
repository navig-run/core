# `navig ledger`

Integrity of the operations history: every operation NAVIG records in
`operations.jsonl` carries a hash-chain fingerprint (`prev` + `hash`).
Delete, edit, or reorder any line and the fingerprints stop matching.

Honesty first: the chain is **tamper-evident, not tamper-proof** — someone
who can rewrite the whole file can recompute the whole chain. What it gives
you is integrity *evidence*: any casual edit, lost line, or corruption is
detected and named by line.

Commands:
- `navig ledger verify` — re-walk the file and re-check every link
- `navig ledger show` — recent operations with per-entry chain state
  (✓ verified · ○ legacy pre-chain · ✗ broken line), the reversibility
  label (green/yellow/red · read-only), and whether an entry was already
  undone

Typical output:
- `✓ 12,431 operations, chain intact`
- `✗ Chain broken at line 8,204: hash mismatch (entry rewritten or corrupted)`

Exit codes:
- `0` — intact (also for the honest non-failure states: no ledger yet, empty
  ledger, or a legacy pre-chain file)
- `1` — chain broken

Options:
- `--path <file>` — inspect a specific ledger file (e.g. the rotated
  `operations.jsonl.bak`); defaults to the active operations history
- `--json` — machine-readable report (status, counts, breaks with line
  numbers, restarts, rotation anchor)
- `--tail <n>` (`show`) — how many recent operations to display (default 20)

Examples:
- `navig ledger verify`
- `navig ledger verify --json`
- `navig ledger verify --path ~/.navig/history/operations.jsonl.bak`
- `navig ledger show`
- `navig ledger show --tail 50 --json`

Reversibility labels (`show`; see also `navig undo`):
- `● green` — undoable: undo data was captured, `navig undo` can replay it
- `● yellow` — compensable/conditional: a manual counter-action exists
- `● red` — irreversible
- `○ read-only` — a pure read (`config get`, `host list`, …): no side
  effect, nothing to reverse
- `show` is a view, not a gate — it always exits 0; `verify` carries the
  exit-code contract.

Notes:
- Rotation keeps recent lines verbatim, so the chain survives it; the first
  surviving entry still names a rotated-out hash — reported as the *anchor*.
- `navig history clear` legitimately ends the chain; the next operation
  starts a fresh one (reported as a clean restart, never a failure).
- Entries written before the chain existed are counted as *legacy
  (unchained)* — they cannot be verified retroactively.

Storage: `~/.navig/history/operations.jsonl` (project-local: `.navig/history/`)
