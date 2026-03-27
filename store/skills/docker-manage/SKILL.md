---
name: docker-manage
description: Manage Docker containers on remote servers via NAVIG
user-invocable: true
navig-commands:
  - navig docker ps
  - navig docker logs {container}
  - navig docker restart {container}
  - navig docker stats
examples:
  - "Show me Docker containers on production"
  - "Check logs for nginx container"
  - "Restart the postgres container"
  - "What containers are running?"
---

# Docker Management

Manage Docker containers on remote servers using NAVIG commands.

## Common Tasks

### 1. List Containers

**User queries:**
- "Show Docker containers"
- "What containers are running?"
- "List all containers on production"

**Command:** `navig docker ps`

**Response format:**
```
🐳 Docker Containers on {host}:

✅ nginx-proxy (running, 2 days)
✅ postgres-db (running, 15 days)
✅ redis-cache (running, 15 days)
⚠️ backup-service (exited, 3 hours ago)
```

### 2. View Container Logs

**User queries:**
- "Show logs for nginx"
- "What's happening in the postgres container?"
- "Check recent logs for {container}"

**Command:** `navig docker logs {container} --tail 50`

**Response format:**
```
📋 Last 50 lines from {container}:

[2026-01-31 10:23:45] INFO: Server started on port 8080
[2026-01-31 10:24:12] INFO: Database connection established
[2026-01-31 10:25:33] ERROR: Failed to connect to Redis
[2026-01-31 10:25:34] INFO: Retrying Redis connection...
```

### 3. Restart Container

**User queries:**
- "Restart nginx container"
- "Reboot the database"
- "Restart {container}"

**Command:** `navig docker restart {container}`

**Response format:**
```
🔄 Restarting {container}...
✅ Container restarted successfully!

Want me to check the logs to confirm it's healthy?
```

### 4. Container Stats

**User queries:**
- "How much memory is Docker using?"
- "Show container resource usage"
- "Docker stats"

**Command:** `navig docker stats --no-stream`

**Response format:**
```
📊 Container Resource Usage:

nginx-proxy:    CPU: 2.5%  | MEM: 128MB / 2GB (6%)
postgres-db:    CPU: 15.3% | MEM: 1.2GB / 4GB (30%)
redis-cache:    CPU: 1.1%  | MEM: 256MB / 1GB (25%)
```

## Advanced Operations

### Check Container Health

```bash
navig docker inspect {container} --format '{{.State.Health.Status}}'
```

### Clean Up Stopped Containers

⚠️ **Destructive operation** - Always confirm first!

```bash
navig run "docker container prune -f"
```

**Response:** "⚠️ This will remove all stopped containers. Are you sure? (yes/no)"

### Clean Up Unused Images

```bash
navig run "docker image prune -a -f"
```

**Response:** "🧹 Cleaned up unused Docker images. Freed: {size}"

## Using Templates

If user mentions a specific application (nginx, postgres, n8n), check if a template exists:

```bash
navig template list
navig template show {template_name}
```

Example:
- **User:** "Deploy n8n on production"
- **Action:** Check if `templates/n8n/template.yaml` exists
- **Response:** "Found n8n template! Ready to deploy. This will create a Docker container with workflow automation. Proceed?"

## Error Handling

- **Container not found**: "Container '{container}' not found. Available containers: nginx-proxy, postgres-db, redis-cache"
- **Docker not running**: "Docker is not running on {host}. Start it with: `navig run 'systemctl start docker'`"
- **Permission denied**: "Permission denied. Try: `navig run 'sudo docker ps'`"

## Proactive Suggestions

- **Container exited recently**: "⚠️ I noticed {container} exited 3 hours ago. Want me to check the logs?"
- **High memory usage**: "📊 {container} is using 90% of its memory limit. Might need to restart or increase resources."
- **Many stopped containers**: "🧹 You have 12 stopped containers. Want me to clean them up to free space?"

## Examples

**Example 1: Quick Status Check**
- **User:** "What's running on production?"
- **Action:** `navig host use production && navig docker ps`
- **Response:** "3 containers running on production: nginx-proxy (2d), postgres-db (15d), redis-cache (15d). All healthy! ✅"

**Example 2: Troubleshooting**
- **User:** "nginx is down"
- **Action:** `navig docker ps` → check status → `navig docker logs nginx --tail 100`
- **Response:** "nginx container exited 10 minutes ago. Last error: 'Port 80 already in use'. Want me to check what's using port 80?"

**Example 3: Deployment**
- **User:** "Deploy Uptime Kuma on production"
- **Action:** Check if template exists in `templates/uptime-kuma/`
- **Response:** "Found Uptime Kuma template! I'll set up the Docker container. This will:
  - Create container with volume for data
  - Expose port 3001
  - Set up auto-restart

  Proceed with deployment?"
