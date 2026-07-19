# `navig undo`

Undo the last **green** (undoable) operation — confirm-gated, drift-checked,
and itself recorded on the tamper-evident ledger.

Every recorded operation carries a reversibility label:

- `● green` — undoable: the previous state (`undo_data`) was captured at
  execution time, and `navig undo` knows how to replay it
  (e.g. `navig config set` captures the old value).
- `● yellow` — compensable / conditionally reversible: a manual
  counter-action exists (start the service again, delete the uploaded copy),
  but NAVIG cannot replay it mechanically.
- `● red` — irreversible (data deleted, message sent, arbitrary command).

Commands:
- `navig undo` — undo the LAST green operation (shows exactly what will be
  restored, asks first)
- `navig undo <op-id>` — undo a specific operation by id
- `navig undo --list` — preview candidates without touching anything

Options:
- `--yes` / `-y` — skip the confirmation prompt
- `--json` — machine-readable output; an un-confirmed undo under `--json`
  is refused (prompts would corrupt the stream) — pass `--yes`

Safety rules (enforced, not advisory):
- **Green only.** Yellow refusals include the compensation hint; red is an
  honest "cannot be taken back".
- **Never twice.** An undo is recorded on the hash chain (tagged `undo`,
  `args.undo_of = <target>`); a target with a successful undo entry is
  refused forever after. Undo entries are capped at yellow, so `navig undo`
  twice never double-reverts — re-run the original command to redo.
- **Drift detection.** If the target changed again since the operation
  (config key re-set, host switched again, file modified), the undo is
  refused with what/why instead of overwriting newer state.
- **Secrets never replay.** A change to a secret-bearing key stores a vault
  reference, never plaintext — it is yellow and must be restored manually.

Examples:
- `navig undo --list`
- `navig undo`
- `navig undo op-20260716120000-abcd1234 --yes`

See the recent labels and chain state: `navig ledger show`.
Verify the chain after an undo: `navig ledger verify`.
