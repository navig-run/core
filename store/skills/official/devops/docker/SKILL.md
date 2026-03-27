---
name: docker-ops
description: Standardized container management and orchestration using NAVIG's docker primitives.
metadata:
  navig:
    emoji: 🐳
    requires:
      bins: [navig]
---

# Docker Operations Skill

Manage containers, logs, and compose stacks on remote hosts via NAVIG.

## Container Management

### List & Inspect
```bash
# List running containers
navig docker ps

# List all containers (including stopped)
navig docker ps -a

# Inspect specific container details
navig docker inspect <container_name_or_id>
```

### Lifecycle Control
```bash
# Restart a container
navig docker restart <container_name>

# Stop a container
navig docker stop <container_name>

# Start a container
navig docker start <container_name>
```

## Logs & Debugging

### View Logs
**Crucial**: Always use `-n` to limit log output.
```bash
# Get last 100 lines of logs
navig docker logs <container_name> -n 100

# Get logs since 10 minutes ago
navig docker logs <container_name> --since 10m
```

### Execute Commands
```bash
# Run a command inside a container
navig docker exec <container_name> "ls -la /app"

# Run an interactive shell (if supported by agent interface)
navig docker exec <container_name> "sh"
```

## Docker Compose

### Stack Management
```bash
# Deploy/Update stack (Detached mode is default recommendation)
navig docker compose up -d -f docker-compose.yml

# Stop stack
navig docker compose down

# View stack status
navig docker compose ps
```

## Templates

### Standard Web Stack (docker-compose.yml)
```yaml
version: '3.8'
services:
  app:
    image: myapp:latest
    restart: always
    ports:
      - "3000:3000"
    environment:
      - NODE_ENV=production
  db:
    image: postgres:14-alpine
    volumes:
      - db_data:/var/lib/postgresql/data
volumes:
  db_data:
```

## Best Practices
1. **Identify Containers**: Use `navig docker ps` first to get the exact container name.
2. **Resource Check**: Run `navig docker stats --no-stream` to check CPU/Memory usage if a container is unresponsive.
3. **Pruning**: Periodically run `navig docker system prune -f` (via `navig run`) to clear space, but ask user first.
