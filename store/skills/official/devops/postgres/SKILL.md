---
name: postgres
description: "Comprehensive PostgreSQL management: queries, backups, user management, and performance analysis."
metadata:
  navig:
    emoji: 🐘
    requires:
      bins: [psql]
---

# PostgreSQL Skill

Manage PostgreSQL databases, users, and performance directly from the command line. This skill leverages `psql` for complex operations and `navig db` for simpler tasks.

## Core Operations

### Connection & Basics
```bash
# List databases (using navig primitive)
navig db list

# List tables in a specific database
navig db tables -d my_database

# Get database size
navig db query "SELECT pg_size_pretty(pg_database_size('my_database'));" -d postgres --plain
```

### Advanced Querying
Use `navig db query` with base64 encoding for complex SQL (especially multi-line or with special characters).

**Pattern:**
```powershell
$sql = "SELECT count(*) FROM users WHERE created_at > NOW() - INTERVAL '7 days';"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sql))
navig db query --b64 $b64 -d my_database --plain
```

## Maintenance & Admin

### User Management
```bash
# Create a new user (role)
navig db query "CREATE USER app_user WITH PASSWORD 'secure_password';" -d postgres  # pragma: allowlist secret

# Grant permissions
navig db query "GRANT ALL PRIVILEGES ON DATABASE my_database TO app_user;" -d postgres
```

### Performance Analysis
check active queries to debug slow performance.
```bash
# Show currently running queries
navig db query "SELECT pid, age(query_start, clock_timestamp()), usename, query FROM pg_stat_activity WHERE state != 'idle' AND query NOT LIKE '%pg_stat_activity%' ORDER BY query_start desc;" -d postgres --plain
```

### Vacuum & Maintenance
```bash
# Run vacuum analyze (reclaims storage and updates stats)
# Note: This can be resource intensive!
navig db query "VACUUM ANALYZE;" -d my_database
```

## Backup & Restore

### Dump Database
```bash
# Dump to a compressed file
navig db dump my_database -o /tmp/backup.sql.gz
```

### Restore Database
**Warning**: This is destructive!
```bash
# Restore from file
navig db restore /tmp/backup.sql.gz -d my_database
```

## Best Practices
1. **Use `--plain`**: Always use the plain output flag when parsing results or feeding them into another tool.
2. **Transaction Safety**: For critical updates, wrap your SQL in `BEGIN; ... COMMIT;` (execute as a single block).
3. **Limit Results**: Standard queries should always have a `LIMIT` clause to prevent flooding the output.
