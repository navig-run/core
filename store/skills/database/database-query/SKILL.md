---
name: database-query
description: Query and manage databases on remote servers via NAVIG
user-invocable: true
navig-commands:
  - navig db query "{sql}" -d {database}
  - navig db databases
  - navig db tables {database}
  - navig db dump {database}
examples:
  - "Show all databases"
  - "What tables are in myapp database?"
  - "Count users in the database"
  - "Backup the production database"
---

# Database Management

Query and manage MySQL/PostgreSQL/SQLite databases on remote servers using NAVIG.

## Common Tasks

### 1. List Databases

**User queries:**
- "Show all databases"
- "What databases exist?"
- "List databases on production"

**Command:** `navig db databases`

**Response format:**
```
🗄️ Databases on {host}:

• myapp_prod (MySQL, 2.3GB)
• wordpress_db (MySQL, 456MB)
• analytics (PostgreSQL, 8.9GB)
• cache (Redis, 128MB)
```

### 2. List Tables

**User queries:**
- "What tables are in myapp_prod?"
- "Show tables"
- "List all tables in the database"

**Command:** `navig db tables {database}`

**Response format:**
```
📋 Tables in myapp_prod:

• users (1,234 rows)
• posts (5,678 rows)
• comments (12,345 rows)
• sessions (890 rows)
```

### 3. Query Data

**User queries:**
- "How many users?"
- "Show recent posts"
- "Count active sessions"

**Command:** `navig db query "SELECT COUNT(*) FROM users" -d myapp_prod`

**Response format:**
```
📊 Query Result:

SELECT COUNT(*) FROM users
└─ 1,234 users

🕐 Executed in 0.03s
```

### 4. Database Backup

**User queries:**
- "Backup the database"
- "Create database dump"
- "Export production database"

**Command:** `navig db dump {database} -o backup_{date}.sql.gz`

**Response format:**
```
💾 Creating backup of {database}...

✅ Backup complete!
   File: backup_2026-01-31.sql.gz
   Size: 2.3GB
   Location: /backups/

Want me to download it to your local machine?
```

## Laravel-Specific Queries

If the server has Laravel installed (detected via templates/hestiacp or artisan), prefer Tinker:

### Count Records

**User:** "How many users?"
**Command:** `navig run "php artisan tinker --execute='echo User::count();'"`

### Get Recent Data

**User:** "Show last 5 users"
**Command:** 
```bash
navig run "php artisan tinker --execute='User::latest()->take(5)->get()->each(fn(\$u) => print \$u->email . \"\n\");'"
```

### Check Application State

**User:** "Is the site live?"
**Command:** `navig run "php artisan env" && navig run "php artisan about"`

## Advanced Queries

### Complex Aggregations

**User:** "Show user registration trends by month"
**SQL:**
```sql
SELECT 
  DATE_FORMAT(created_at, '%Y-%m') as month,
  COUNT(*) as registrations
FROM users
GROUP BY month
ORDER BY month DESC
LIMIT 12
```

**Response format:**
```
📈 User Registration Trends:

2026-01: 234 registrations
2025-12: 198 registrations
2025-11: 156 registrations
...
```

### Database Size Analysis

**User:** "What's using the most database space?"
**Command:** 
```sql
SELECT 
  table_name,
  ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb
FROM information_schema.TABLES
WHERE table_schema = '{database}'
ORDER BY size_mb DESC
LIMIT 10
```

## Safety Rules

### Read-Only Queries (Safe)
- `SELECT`
- `SHOW`
- `DESCRIBE`
- `EXPLAIN`

### Write Operations (Require Confirmation)
- `INSERT` - "⚠️ This will add {count} records. Confirm?"
- `UPDATE` - "⚠️ This will update {count} records. Confirm?"
- `DELETE` - "🚨 This will DELETE {count} records. Are you ABSOLUTELY sure?"
- `DROP` - "🚨 DANGER! This will permanently delete the {table/database}. Type 'YES DELETE' to confirm."
- `TRUNCATE` - "🚨 This will erase all data in {table}. Type 'YES ERASE' to confirm."

### Always Use Transactions for Writes

```sql
BEGIN;
UPDATE users SET status = 'active' WHERE id = 123;
-- Show preview before commit
SELECT * FROM users WHERE id = 123;
-- Wait for user confirmation before:
COMMIT;
```

## Error Handling

- **Database not found**: "Database '{name}' doesn't exist. Available: myapp_prod, wordpress_db, analytics"
- **Connection failed**: "Can't connect to database. Check credentials in NAVIG config."
- **Permission denied**: "User '{user}' doesn't have permission for this operation."
- **Syntax error**: "SQL syntax error: {error}. Want me to help fix it?"

## Proactive Suggestions

- **Large result set**: "This query returned 10,000 rows. Want me to add LIMIT 100?"
- **Slow query**: "Query took 5.3 seconds. Want me to EXPLAIN the query plan?"
- **No index**: "This query scans 1M rows. Consider adding an index on {column}."

## Using HestiaCP Integration

If server uses HestiaCP template, leverage built-in commands:

```bash
navig run "v-list-databases admin"  # List all databases for user
navig run "v-backup-database admin myapp_prod"  # Backup specific DB
```

## Examples

**Example 1: Quick Check**
- **User:** "How many users in production?"
- **Action:** `navig host use production && navig db query "SELECT COUNT(*) FROM users" -d myapp_prod`
- **Response:** "Production database has 1,234 users"

**Example 2: Laravel Tinker**
- **User:** "Show me the last user registered"
- **Action:** `navig run "php artisan tinker --execute='User::latest()->first();'"`
- **Response:**
  ```
  👤 Last registered user:
  • Email: john@example.com
  • Registered: 2026-01-31 10:23:45
  • Status: active
  ```

**Example 3: Backup Before Update**
- **User:** "Update all inactive users to deleted"
- **Response:** "🚨 This is a destructive operation. Let me:
  1. Create a backup first
  2. Show you how many records will be affected
  3. Ask for final confirmation
  
  Proceed?"
- **Action:**
  1. `navig db dump myapp_prod -o backup_before_delete.sql.gz`
  2. `navig db query "SELECT COUNT(*) FROM users WHERE status='inactive'" -d myapp_prod`
  3. Show: "This will affect 89 users. Type 'CONFIRM DELETE' to proceed."


