# PostgreSQL Template for NAVIG

## Overview

PostgreSQL is a powerful, open-source object-relational database system with over 35 years of active development. It has earned a strong reputation for reliability, feature robustness, and performance.

## Features

- **Service Management**: Start, stop, restart, and reload PostgreSQL
- **Database Operations**: Create, backup, and manage databases
- **User Management**: Create and manage database users/roles
- **Maintenance**: VACUUM, ANALYZE, and other maintenance tasks
- **Backup/Restore**: Full database backup and restore capabilities

## Usage

### Enable the Template

```bash
navig server-template init postgresql --server <server-name>
navig server-template enable postgresql --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Start PostgreSQL
navig run "systemctl start postgresql"

# Check status
navig run "systemctl status postgresql"

# Reload configuration (no restart needed)
navig run "systemctl reload postgresql"
```

#### Database Operations

```bash
# Connect to PostgreSQL shell
navig run "sudo -u postgres psql"

# List all databases
navig run "sudo -u postgres psql -c '\\l'"

# Create a new database
navig run "sudo -u postgres createdb myapp_db"

# Create a new user
navig run "sudo -u postgres psql -c \"CREATE USER myapp_user WITH PASSWORD 'secure_password';\""

# Grant privileges
navig run "sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE myapp_db TO myapp_user;\""
```

#### Backup and Restore

```bash
# Backup a single database
navig run "sudo -u postgres pg_dump myapp_db > /var/backups/postgresql/myapp_db.sql"

# Backup all databases
navig run "sudo -u postgres pg_dumpall > /var/backups/postgresql/all_dbs.sql"

# Download backup
navig download /var/backups/postgresql/myapp_db.sql ./backups/

# Restore a database
navig run "sudo -u postgres psql myapp_db < /var/backups/postgresql/myapp_db.sql"
```

## Configuration

### Key Configuration Options

Edit `/etc/postgresql/16/main/postgresql.conf`:

| Option | Default | Description |
|--------|---------|-------------|
| listen_addresses | localhost | IP addresses to listen on |
| port | 5432 | Port number |
| max_connections | 100 | Maximum concurrent connections |
| shared_buffers | 128MB | Memory for shared buffers |
| work_mem | 4MB | Memory for query operations |

### Allow Remote Connections

1. Edit `postgresql.conf`:
```bash
navig run "sed -i \"s/#listen_addresses = 'localhost'/listen_addresses = '*'/\" /etc/postgresql/16/main/postgresql.conf"
```

2. Edit `pg_hba.conf`:
```bash
navig run "echo 'host    all    all    0.0.0.0/0    scram-sha-256' >> /etc/postgresql/16/main/pg_hba.conf"
```

3. Restart PostgreSQL:
```bash
navig run "systemctl restart postgresql"
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| data_dir | `/var/lib/postgresql/16/main` | Database files |
| config_dir | `/etc/postgresql/16/main` | Configuration files |
| main_config | `/etc/postgresql/16/main/postgresql.conf` | Main configuration |
| hba_config | `/etc/postgresql/16/main/pg_hba.conf` | Client authentication |
| log_dir | `/var/log/postgresql` | Log files |
| backup_dir | `/var/backups/postgresql` | Backup directory |

## Default Port

- **PostgreSQL**: 5432

## Maintenance Tasks

### VACUUM and ANALYZE

```bash
# Vacuum all databases
navig run "sudo -u postgres vacuumdb --all"

# Vacuum with analyze
navig run "sudo -u postgres vacuumdb --all --analyze"

# Full vacuum (reclaims more space, requires exclusive lock)
navig run "sudo -u postgres vacuumdb --all --full"
```

### Reindex

```bash
# Reindex a specific database
navig run "sudo -u postgres reindexdb myapp_db"

# Reindex all databases
navig run "sudo -u postgres reindexdb --all"
```

## Troubleshooting

### Connection Refused

```bash
# Check if PostgreSQL is listening
navig run "ss -tlnp | grep 5432"

# Check pg_hba.conf for connection rules
navig run "cat /etc/postgresql/16/main/pg_hba.conf | grep -v '^#' | grep -v '^$'"
```

### Performance Issues

```bash
# Check active connections
navig run "sudo -u postgres psql -c 'SELECT count(*) FROM pg_stat_activity;'"

# Check slow queries
navig run "sudo -u postgres psql -c 'SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state != '\\''idle'\\'' ORDER BY duration DESC LIMIT 10;'"
```

### Disk Space Issues

```bash
# Check database sizes
navig run "sudo -u postgres psql -c 'SELECT pg_database.datname, pg_size_pretty(pg_database_size(pg_database.datname)) FROM pg_database ORDER BY pg_database_size(pg_database.datname) DESC;'"
```

## References

- Official Website: https://www.postgresql.org
- Documentation: https://www.postgresql.org/docs/
- Wiki: https://wiki.postgresql.org
- GitHub: https://github.com/postgres/postgres


