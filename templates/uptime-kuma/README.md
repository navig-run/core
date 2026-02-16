# Uptime Kuma Template for NAVIG

## Overview

Uptime Kuma is a self-hosted monitoring tool like Uptime Robot. It allows you to monitor websites, APIs, Docker containers, and more with a beautiful status page.

## Features

- **Multiple Monitor Types**: HTTP(s), TCP, Ping, DNS, Docker, and more
- **Status Pages**: Create public status pages
- **Notifications**: 90+ notification services (Slack, Discord, Telegram, etc.)
- **Certificate Monitoring**: SSL/TLS certificate expiry alerts
- **Maintenance Windows**: Schedule maintenance periods
- **Multi-Language**: Supports many languages

## Usage

### Enable the Template

```bash
navig server-template init uptime-kuma --server <server-name>
navig server-template enable uptime-kuma --server <server-name>
```

### Common Operations

#### Container Management

```bash
# Start Uptime Kuma
navig run "cd /opt/uptime-kuma && docker compose up -d"

# Check status
navig run "docker ps -f name=uptime-kuma"

# View logs
navig run "docker logs -f uptime-kuma"

# Stop
navig run "cd /opt/uptime-kuma && docker compose down"
```

#### Update

```bash
# Pull latest version
navig run "cd /opt/uptime-kuma && docker compose pull"

# Restart with new version
navig run "cd /opt/uptime-kuma && docker compose up -d"
```

## Initial Setup

### Create Directory Structure

```bash
navig run "mkdir -p /opt/uptime-kuma/data"
```

### Docker Compose Configuration

Create `/opt/uptime-kuma/docker-compose.yml`:

```yaml
version: "3.8"

services:
  uptime-kuma:
    image: louislam/uptime-kuma:1
    container_name: uptime-kuma
    restart: unless-stopped
    ports:
      - "3001:3001"
    volumes:
      - /opt/uptime-kuma/data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock:ro  # For Docker monitoring
    environment:
      - NODE_ENV=production
```

### Start Uptime Kuma

```bash
navig run "cd /opt/uptime-kuma && docker compose up -d"
```

### Access Uptime Kuma

1. Open `http://your-server:3001`
2. Create admin account on first access
3. Start adding monitors

## Configuration

### Monitor Types

| Type | Description |
|------|-------------|
| HTTP(s) | Check website availability |
| HTTP(s) Keyword | Check for specific text |
| TCP Port | Check if port is open |
| Ping | ICMP ping check |
| DNS | DNS resolution check |
| Docker Container | Container status |
| Steam Game Server | Game server status |
| Push | Heartbeat endpoint |

### Creating Monitors

1. Click "Add New Monitor"
2. Select monitor type
3. Configure URL/host and check interval
4. Set up notifications (optional)
5. Save and start monitoring

### Setting Up Notifications

1. Go to Settings → Notifications
2. Add notification service (Discord, Slack, Email, etc.)
3. Test notification
4. Apply to monitors

### Behind Reverse Proxy (Traefik)

```yaml
services:
  uptime-kuma:
    image: louislam/uptime-kuma:1
    container_name: uptime-kuma
    restart: unless-stopped
    volumes:
      - /opt/uptime-kuma/data:/app/data
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.uptime-kuma.rule=Host(`status.example.com`)"
      - "traefik.http.routers.uptime-kuma.entrypoints=websecure"
      - "traefik.http.routers.uptime-kuma.tls.certresolver=letsencrypt"
      - "traefik.http.services.uptime-kuma.loadbalancer.server.port=3001"
    networks:
      - proxy

networks:
  proxy:
    external: true
```

### Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl http2;
    server_name status.example.com;

    ssl_certificate /etc/letsencrypt/live/status.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/status.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| data_dir | `/opt/uptime-kuma/data` | All data and database |
| database | `/opt/uptime-kuma/data/kuma.db` | SQLite database |
| compose_file | `/opt/uptime-kuma/docker-compose.yml` | Docker Compose file |

## Default Port

- **Web UI**: 3001

## Status Pages

### Creating a Status Page

1. Go to "Status Pages" tab
2. Click "New Status Page"
3. Configure:
   - Slug (URL path)
   - Title
   - Description
   - Add monitors to display
4. Publish the page

### Custom Domain for Status Page

1. Set up DNS record pointing to your server
2. Configure reverse proxy for the domain
3. In Status Page settings, set the custom domain

## Backup & Restore

### Backup Database

```bash
# Create backup
navig run "cp /opt/uptime-kuma/data/kuma.db /backup/kuma-$(date +%Y%m%d).db"

# Download backup
navig download /backup/kuma-*.db ./backups/
```

### Full Backup

```bash
# Stop container
navig run "cd /opt/uptime-kuma && docker compose down"

# Create full backup
navig run "tar -czvf /backup/uptime-kuma-$(date +%Y%m%d).tar.gz /opt/uptime-kuma/data"

# Download
navig download /backup/uptime-kuma-*.tar.gz ./backups/

# Start container
navig run "cd /opt/uptime-kuma && docker compose up -d"
```

### Restore Backup

```bash
# Stop container
navig run "cd /opt/uptime-kuma && docker compose down"

# Upload backup
navig upload ./backups/uptime-kuma-backup.tar.gz /backup/

# Restore
navig run "rm -rf /opt/uptime-kuma/data/*"
navig run "tar -xzvf /backup/uptime-kuma-backup.tar.gz -C /"

# Start container
navig run "cd /opt/uptime-kuma && docker compose up -d"
```

## Docker Container Monitoring

### Enable Docker Monitoring

1. Mount Docker socket in docker-compose.yml (already included above)
2. In Uptime Kuma, add new monitor
3. Select "Docker Container"
4. Choose container to monitor

### Security Note

Mounting Docker socket gives container access to Docker. For better security, use Docker Socket Proxy:

```yaml
services:
  dockerproxy:
    image: tecnativa/docker-socket-proxy
    container_name: dockerproxy
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - CONTAINERS=1

  uptime-kuma:
    image: louislam/uptime-kuma:1
    container_name: uptime-kuma
    restart: unless-stopped
    ports:
      - "3001:3001"
    volumes:
      - /opt/uptime-kuma/data:/app/data
    environment:
      - DOCKER_HOST=tcp://dockerproxy:2375
    depends_on:
      - dockerproxy
```

## Troubleshooting

### Can't Access UI

```bash
# Check container is running
navig run "docker ps -f name=uptime-kuma"

# Check port is open
navig run "ss -tlnp | grep 3001"

# Check logs
navig run "docker logs uptime-kuma 2>&1 | tail -50"
```

### Database Locked

```bash
# Stop container
navig run "cd /opt/uptime-kuma && docker compose down"

# Check for lock files
navig run "ls -la /opt/uptime-kuma/data/"

# Remove lock files if present
navig run "rm -f /opt/uptime-kuma/data/*.lock"

# Start container
navig run "cd /opt/uptime-kuma && docker compose up -d"
```

### Notifications Not Working

1. Check notification configuration in Settings
2. Click "Test" to verify
3. Check container logs for errors
4. Verify network connectivity to notification service

### High CPU/Memory Usage

```bash
# Check resource usage
navig run "docker stats uptime-kuma --no-stream"

# Check number of monitors
# Consider reducing check frequency for less critical monitors
```

## Security Best Practices

1. **Use HTTPS**: Always use reverse proxy with TLS
2. **Strong Password**: Use complex admin password
3. **Limit Access**: Use firewall to restrict access
4. **Docker Socket**: Use socket proxy instead of direct mount
5. **Regular Backups**: Backup database regularly
6. **Updates**: Keep Uptime Kuma updated

## References

- GitHub: https://github.com/louislam/uptime-kuma
- Documentation: https://github.com/louislam/uptime-kuma/wiki
- Docker Hub: https://hub.docker.com/r/louislam/uptime-kuma
- Demo: https://demo.uptime.kuma.pet


