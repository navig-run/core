# `navig backup`

Backup/restore and NAVIG config export/import.

Common actions:
- List backups: `navig backup list`
- Run backup (choose one operation):
  - `navig backup run --config`
  - `navig backup run --db-all`
  - `navig backup run --hestia`
  - `navig backup run --web`
  - `navig backup run --all`
- Restore: `navig backup restore <backup_name> [--component <name>] [--force]`

Note:
- For a single database backup, prefer `navig db dump <db> -o backup.sql`.
