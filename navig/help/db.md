# `navig db`

Database operations (list, query, dump, optimize, etc.).

Typical flow:
- List DBs: `navig db list`
- List tables: `navig db tables <db>`
- Run a query: `navig db query "SELECT 1" -d <db>`
- Backup one DB: `navig db dump <db> -o backup.sql`

Automation:
- Use global `--json` for structured output where supported.


