```skill
---
name: rclone-cloud-sync
description: List, sync, and copy files between rclone remotes (S3, Drive, Dropbox, SFTP, etc.)
user-invocable: true
navig-commands:
  - navig cloud rclone remotes
  - navig cloud rclone ls --remote {remote} --path {path}
  - navig cloud rclone sync --src {src} --dst {dst}
  - navig cloud rclone copy --src {src} --dst {dst}
requires:
  - rclone (from C:\USB\network\rclone-browser\rclone\ on Windows; system rclone on Linux/Mac)
  - rclone must have remotes configured via `rclone config`
examples:
  - "List my Google Drive root"
  - "Sync my local backup folder to S3"
  - "Copy files from one remote to another"
  - "What remotes do I have configured?"
os: [windows, linux, mac]
---

# rclone Cloud Sync

Interact with cloud storage using rclone — list, sync, and copy files between any configured remotes.

## Prerequisites

- On Windows: USB binary auto-discovered; configure remotes with `rclone config` first
- On Linux/Mac: install rclone (`apt install rclone` / `brew install rclone`)
- Remotes must be pre-configured — NAVIG does not run the interactive `rclone config` wizard

## Common Tasks

### List configured remotes

**User says:** "What cloud storage do I have?"

```bash
navig cloud rclone remotes
```

### Browse a remote

**User says:** "Show me what's in my Drive backup folder"

```bash
navig cloud rclone ls --remote gdrive --path backup/
```

### Sync local → remote (destructive)

**User says:** "Mirror my project folder to S3"

```bash
navig cloud rclone sync --src /local/projects --dst s3:my-bucket/projects
```

> **Warning:** `sync` makes the destination identical to source — deletes extra files in destination.
> Use `--dry-run` first to preview.

```bash
navig cloud rclone sync --src /local/projects --dst s3:my-bucket/projects --dry-run
```

### Copy (non-destructive)

```bash
navig cloud rclone copy --src /local/docs --dst b2:archive/docs
```

## Safety Notes

- Always use `--dry-run` before a `sync` to a destination that may have unique files
- Credentials live in the rclone config file — never passed as flags
```
