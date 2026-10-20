- **Legacy changelogs migrate into Recall — dry-run first, verified before anything
  retires.** `navig recall migrate <file> -p <project>` parses three real families
  (dated dev entries, archived session addenda, Keep-a-Changelog release notes) into
  content-addressed records with line-range locators. The default run writes nothing.
  `--apply` backs the file up under `vault/.migrations/` (sha256 manifest), imports it,
  then re-reads the ORIGINAL to verify digest + record ids + archived turns before
  reporting — and only `--apply --retire` can move a file, only when it is private dev
  history with zero consumers of its name. Release notes are indexed but never retired;
  `--delete-backup` is interactive and typed-path (the one irreversible step).
  Re-importing an unchanged file is a no-op (digest short-circuit).
- **SessionStart can carry a bounded Recall brief** (`recall.session_brief`, default
  off): recent user decisions, the unfinished count and the latest supersession, ≤1.5 KB,
  fail-open, private-chat records filtered — riding the existing `navig space context
  --brief` hook, so no new hook exists. `navig wire --recall` opts a new project in
  (the flag is the consent); `wire`/`init` print the index-your-history next step.
