# Redis Template for NAVIG

## Overview

Redis is an open-source, in-memory data structure store used as a database, cache, message broker, and streaming engine. It supports data structures such as strings, hashes, lists, sets, sorted sets, bitmaps, and more.

## Features

- **Service Management**: Start, stop, restart Redis service
- **CLI Access**: Direct access to Redis command-line interface
- **Monitoring**: Real-time command monitoring and statistics
- **Persistence**: RDB snapshots and AOF persistence
- **Memory Management**: Monitor and configure memory usage

## Usage

### Enable the Template

```bash
navig server-template init redis --server <server-name>
navig server-template enable redis --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Start Redis
navig run "systemctl start redis-server"

# Check status
navig run "systemctl status redis-server"

# Test connection
navig run "redis-cli ping"
```

#### Data Operations

```bash
# Open Redis CLI
navig run "redis-cli"

# Set a key
navig run "redis-cli SET mykey 'Hello World'"

# Get a key
navig run "redis-cli GET mykey"

# List all keys (use carefully in production)
navig run "redis-cli KEYS '*'"

# Get key count
navig run "redis-cli DBSIZE"
```

#### Monitoring

```bash
# Get server info
navig run "redis-cli INFO"

# Memory usage
navig run "redis-cli INFO memory"

# Connected clients
navig run "redis-cli CLIENT LIST"

# Monitor all commands in real-time
navig run "redis-cli MONITOR"
```

## Configuration

### Key Configuration Options

Edit `/etc/redis/redis.conf`:

| Option | Default | Description |
|--------|---------|-------------|
| bind | 127.0.0.1 | IP addresses to listen on |
| port | 6379 | Port number |
| maxmemory | (none) | Maximum memory limit |
| maxmemory-policy | noeviction | Eviction policy when max memory reached |
| requirepass | (none) | Password for authentication |

### Enable Password Authentication

```bash
# Set password in config
navig run "sed -i 's/# requirepass foobared/requirepass YourSecurePassword/' /etc/redis/redis.conf"

# Restart Redis
navig run "systemctl restart redis-server"

# Connect with password
navig run "redis-cli -a YourSecurePassword ping"
```

### Configure Memory Limit

```bash
# Set max memory to 256MB
navig run "sed -i 's/# maxmemory <bytes>/maxmemory 256mb/' /etc/redis/redis.conf"

# Set eviction policy
navig run "sed -i 's/# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf"

# Restart Redis
navig run "systemctl restart redis-server"
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| config_file | `/etc/redis/redis.conf` | Main configuration |
| data_dir | `/var/lib/redis` | Data directory |
| log_file | `/var/log/redis/redis-server.log` | Log file |
| dump_file | `/var/lib/redis/dump.rdb` | RDB snapshot |
| aof_file | `/var/lib/redis/appendonly.aof` | Append-only file |

## Default Port

- **Redis**: 6379

## Persistence

### RDB Snapshots

```bash
# Force background save
navig run "redis-cli BGSAVE"

# Check last save time
navig run "redis-cli LASTSAVE"

# Download RDB file
navig download /var/lib/redis/dump.rdb ./backups/redis-dump.rdb
```

### AOF (Append-Only File)

Enable in redis.conf:
```bash
navig run "sed -i 's/appendonly no/appendonly yes/' /etc/redis/redis.conf"
navig run "systemctl restart redis-server"
```

## Troubleshooting

### Connection Refused

```bash
# Check if Redis is listening
navig run "ss -tlnp | grep 6379"

# Check Redis status
navig run "systemctl status redis-server"

# Check logs
navig run "tail -50 /var/log/redis/redis-server.log"
```

### Memory Issues

```bash
# Check memory usage
navig run "redis-cli INFO memory | grep used_memory_human"

# Check maxmemory setting
navig run "redis-cli CONFIG GET maxmemory"

# Get memory stats
navig run "redis-cli MEMORY STATS"
```

### Slow Performance

```bash
# Check slow log
navig run "redis-cli SLOWLOG GET 10"

# Check client connections
navig run "redis-cli CLIENT LIST | wc -l"

# Get latency stats
navig run "redis-cli --latency"
```

## Security Best Practices

1. **Enable authentication**: Set `requirepass` in redis.conf
2. **Bind to localhost**: Keep `bind 127.0.0.1` unless remote access needed
3. **Disable dangerous commands**: Rename or disable FLUSHALL, FLUSHDB, CONFIG, DEBUG
4. **Use TLS**: Enable TLS for encrypted connections (Redis 6+)
5. **Firewall**: Block port 6379 from external access

## References

- Official Website: https://redis.io
- Documentation: https://redis.io/docs/
- Commands Reference: https://redis.io/commands/
- GitHub: https://github.com/redis/redis


