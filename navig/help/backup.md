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
- Restore: `navig backup restore <backup_name> [--component <name>]` — **not implemented.**
  It reports what the backup contains and where, then exits non-zero; restore the files
  by hand. It does not write anything back to the server.

Note:
- For a single database backup, prefer `navig db dump <db> -o backup.sql`.
