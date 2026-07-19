---
id: db-snapshot
spec_version: 1
name: Database Snapshot
version: 1.0.0
category: ops
license: MIT
description: Export a remote database and pull it to the workspace — the verified outcome is a local dump file that exists.
author: navig
tags: [database, backup, dump, ssh, ops]
allowed-tools: Bash
target: local

inputs:
  - key: host
    type: string
    label: Database host (a configured navig host)
    required: true
    default: production
  - key: db_name
    type: string
    label: Database to export
    required: true
    default: main_db
  - key: output_file
    type: string
    label: Remote dump filename
    required: true
    default: prod_backup.sql
  - key: local_dir
    type: string
    label: Local directory (relative to the workspace) to download the dump into
    required: true
    default: local_dumps

steps:
  - id: set-host
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv: [navig, host, use, "{{inputs.host}}"]

  - id: db-list
    kind: command
    safety: safe
    capabilities: [exec:navig]
    network: required
    argv: [navig, db, list]

  - id: dump
    kind: command
    safety: destructive
    capabilities: [remote, exec:navig]
    network: required
    argv: [navig, db, dump, "{{inputs.db_name}}", "-o", "{{inputs.output_file}}"]

  - id: download
    kind: command
    safety: moderate
    capabilities: [exec:navig]
    network: required
    argv: [navig, download, "{{inputs.output_file}}", "{{inputs.local_dir}}/"]

  - id: cleanup
    kind: command
    safety: destructive
    capabilities: [delete, exec:navig]
    network: required
    argv:
      - navig
      - run
      - "rm -f {{inputs.output_file}}"

verify:
  kind: file_exists
  level: self-check
  path: "{{workdir}}/{{inputs.local_dir}}/{{inputs.output_file}}"

receipt:
  redact: []
---
# Snapshot a remote database to your workspace

Exports `db_name` from a navig-configured `host`, downloads it into
`local_dir/` under your workspace, and removes the temporary dump from the
server. The **verified outcome** is a real machine check: the local dump file
must exist when the run finishes.

## Steps

1. **set-host** — point navig at `host`.
2. **db-list** — enumerate databases (a sanity check before dumping).
3. **dump** — `navig db dump <db_name> -o <output_file>` on the server
   *(destructive — heavy prod I/O; needs `--approve dump`)*.
4. **download** — pull the dump into `local_dir/`. This runs **before** cleanup,
   so if the download fails the block aborts and the remote dump is preserved.
5. **cleanup** — remove the remote dump *(destructive — `--approve cleanup`)*.

> **Compression:** the legacy workflow passed `--compress` to `db dump`, a flag
> that does not exist and silently broke the download (which then looked for a
> non-existent `.gz`). This block dumps uncompressed `.sql`; gzip locally after
> if you want it (`gzip local_dumps/prod_backup.sql`).

## Apply

```
navig apply db-snapshot \
  --input host=production \
  --input db_name=main_db \
  --input output_file=prod_backup.sql \
  --input local_dir=local_dumps \
  --approve dump --approve cleanup
```

`local_dir` is resolved **relative to the workspace root** — keep it relative so
the machine verify (`{{workdir}}/{{inputs.local_dir}}/{{inputs.output_file}}`)
can confirm the dump landed.
