---
name: Restic Backup
description: Snapshot paths into a restic repository and prove the repository is consistent — not just that a command exited.
allowed-tools: Bash
version: 1.0.0
generated-by: navig-block
block-id: restic-backup
---

# Snapshot with restic — and prove the repository is still consistent

```
navig apply restic-backup \
  --input repository=/mnt/backup/restic-repo \
  --input paths="~ /var/www" \
  --input tag=automated
```

1. **reachable** — `restic snapshots --latest 1` against the repository. This is where a wrong
   path, a rotated password or an unreachable bucket fails, before anything is written.
2. **snapshot** — `restic backup --tag <tag> <paths>`.
3. **verify** — `restic check` on the repository. Exit 0 means the index and packs are
   readable, so the snapshot that just landed did not corrupt the store.

The password and repository are read from `RESTIC_PASSWORD` / `RESTIC_REPOSITORY` by restic
itself; this block never puts a secret in an argv, so nothing sensitive can reach a receipt.

## Before the first run

```powershell
navig run "restic init -r <repository>"     # once, if the repository does not exist yet
```

`navig run` executes on the **active host** — `navig host use <name>` first if the repository
lives on a server. With no host configured the first step fails and the block aborts without
touching anything.

## When not to use it

- **A restore test** — this block proves the backup is *stored*; it never proves it can be
  *restored*. Do that deliberately: `restic restore latest --target /tmp/restore-test`.
- **Pruning** — `restic forget --prune` deletes data and belongs in its own destructive block
  with an explicit retention policy, not hidden at the end of a backup.

## Apply this outcome

This is a NAVIG **block**. Run it end-to-end with:

```
navig apply restic-backup --input repository=<string> --input paths=<string>
```

### Inputs

- `repository` (string, required) — restic repository (-r), e.g. /mnt/backup/restic-repo or s3:s3.amazonaws.com/bucket
- `paths` (string, required) — Paths to back up, space separated (e.g. "~ /var/www")
- `tag` (string) — Snapshot tag

