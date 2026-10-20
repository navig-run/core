---
name: Database Snapshot
description: Export a remote database and pull it to the workspace — the verified outcome is a local dump file that exists.
allowed-tools: Bash
version: 1.0.0
generated-by: navig-block
block-id: db-snapshot
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

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply db-snapshot --input host=<string> --input db_name=<string> --input output_file=<string> --input local_dir=<string>
```

### Inputs

- `host` (string, required) — Database host (a configured navig host)
- `db_name` (string, required) — Database to export
- `output_file` (string, required) — Remote dump filename
- `local_dir` (string, required) — Local directory (relative to the workspace) to download the dump into

