---
name: ssh-tunnel
description: Create and manage SSH tunnels to access remote services (databases, web panels, APIs)
user-invocable: true
navig-commands:
  - navig tunnel list
  - navig tunnel add {name} -l {local_port} -r {remote_port}
  - navig tunnel start {name}
  - navig tunnel stop {name}
  - navig tunnel status
examples:
  - "Create a tunnel to the database"
  - "I need to access the remote Grafana dashboard"
  - "Show active tunnels"
  - "Tunnel port 3000 from production"
  - "Stop all tunnels"
---

# SSH Tunnel Management

When the user needs to access remote services locally (databases, admin panels, internal APIs) via SSH tunnels.

## Steps

1. **Identify what they need**: Which remote service/port?
2. **Check existing tunnels**: `navig tunnel list`
3. **Create or start tunnel**: Use appropriate `navig tunnel` command

## Common Tunnels

### Database Access

**User says:** "I need to connect to the production database locally"

```bash
navig host use production
navig tunnel add db-prod -l 3306 -r 3306
navig tunnel start db-prod
```

**Response:**
```
🔗 Tunnel created!

Local:  localhost:3306 → production:3306 (MySQL)
Status: ✅ Active

Connect with: mysql -h 127.0.0.1 -P 3306 -u user -p
```

### Web Panel Access

**User says:** "I want to access Grafana on my server"

```bash
navig tunnel add grafana -l 3000 -r 3000
navig tunnel start grafana
```

**Response:**
```
🔗 Tunnel to Grafana active!

Open in browser: http://localhost:3000
```

### Redis / Cache Access

```bash
navig tunnel add redis -l 6379 -r 6379
navig tunnel start redis
```

## Managing Tunnels

### List All Tunnels

```bash
navig tunnel list
```

**Response:**
```
🔗 SSH Tunnels:

✅ db-prod:    localhost:3306 → production:3306
✅ grafana:    localhost:3000 → production:3000
⏹️ redis:      localhost:6379 → production:6379 (stopped)
```

### Stop a Tunnel

```bash
navig tunnel stop db-prod
```

### Stop All Tunnels

```bash
navig tunnel stop --all
```

## Common Port Mappings

| Service | Remote Port | Suggested Local |
|---------|------------|-----------------|
| MySQL | 3306 | 3306 |
| PostgreSQL | 5432 | 5432 |
| Redis | 6379 | 6379 |
| Grafana | 3000 | 3000 |
| Prometheus | 9090 | 9090 |
| HestiaCP | 8083 | 8083 |
| Portainer | 9443 | 9443 |
| n8n | 5678 | 5678 |

## Proactive Suggestions

- **Port conflict**: "Port 3306 is already in use locally. Want me to use 33060 instead?"
- **Multiple hosts**: "You have tunnels to 3 different servers. Want me to list them all?"
- **Stale tunnels**: "This tunnel has been idle for 2 hours. Want to close it?"

## Error Handling

- **Port in use**: "Port {port} is already in use. Try: `navig tunnel add {name} -l {alt_port} -r {port}`"
- **Connection refused**: "Can't establish tunnel. Check if SSH connection works: `navig host test`"
- **Service not listening**: "Tunnel is open but nothing is listening on remote port {port}."


