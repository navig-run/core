---
name: restic-backup
description: Fast, encrypted, deduplicated backups using Restic.
metadata:
  navig:
    emoji: 🔒
    requires:
      bins: [restic]
      env: [RESTIC_PASSWORD, RESTIC_REPOSITORY]
---

# Restic Backup Skill

Sovereign backup solution. Encrypts your data locally before sending it anywhere.

## Setup

### Initialize Repo
```bash
# Initialize a local repository
restic init --repo /mnt/backup/restic-repo

# Initialize an S3 repository
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
restic init --repo s3:s3.amazonaws.com/bucket_name
```

## Core Actions

### Backup (Snapshot)
```bash
# Backup home directory
restic -r /mnt/backup/repo backup ~

# Backup with tags
restic -r /mnt/backup/repo backup --tag automated /var/www
```

### Restore
```bash
# List snapshots
restic -r /mnt/backup/repo snapshots

# Restore latest snapshot
restic -r /mnt/backup/repo restore latest --target /tmp/restore-test
```

### Maintenance
```bash
# Check integrity
restic -r /mnt/backup/repo check

# Prune old snapshots (keep last 7 daily, 4 weekly)
restic -r /mnt/backup/repo forget --keep-daily 7 --keep-weekly 4 --prune
```

## Best Practices
1. **Environment Variables**: Always use `RESTIC_PASSWORD` env var instead of typing passwords.
2. **Automation**: Run `forget --prune` regularly to reclaim space.
3. **Verification**: Run `check` weekly to ensure data integrity.



