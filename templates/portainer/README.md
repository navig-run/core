# Portainer Template for NAVIG

## Overview

Portainer is a lightweight management UI that allows you to easily manage your Docker, Swarm, Kubernetes, and Nomad environments. It provides a simple dashboard to manage containers, images, volumes, and networks.

## Features

- **Container Management**: Start, stop, restart, remove containers
- **Image Management**: Pull, push, build, remove images
- **Stack Deployment**: Deploy Docker Compose stacks
- **Multi-Environment**: Manage multiple Docker hosts from one interface
- **Access Control**: Role-based access control (RBAC)
- **Templates**: Deploy applications from templates

## Usage

### Enable the Template

```bash
navig server-template init portainer --server <server-name>
navig server-template enable portainer --server <server-name>
```

### Common Operations

#### Container Management

```bash
# Start Portainer
navig run "cd /opt/portainer && docker compose up -d"

# Check status
navig run "docker ps -f name=portainer"

# View logs
navig run "docker logs -f portainer"

# Stop Portainer
navig run "cd /opt/portainer && docker compose down"
```

#### Update Portainer

```bash
# Pull latest version
navig run "cd /opt/portainer && docker compose pull"

# Restart with new version
navig run "cd /opt/portainer && docker compose up -d"
```

## Initial Setup

### Create Directory Structure

```bash
navig run "mkdir -p /opt/portainer/data"
```

### Docker Compose Configuration

Create `/opt/portainer/docker-compose.yml`:

```yaml
version: "3.8"

services:
  portainer:
    image: portainer/portainer-ce:latest
    container_name: portainer
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    ports:
      - "9443:9443"  # HTTPS
      - "9000:9000"  # HTTP (optional)
      - "8000:8000"  # Edge Agent
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /opt/portainer/data:/data
```

### Start Portainer

```bash
navig run "cd /opt/portainer && docker compose up -d"
```

### Access Portainer

1. Open `https://your-server:9443`
2. Create admin account on first access (within 5 minutes)
3. Choose "Docker" as environment type
4. Connect to local Docker socket

## Configuration

### Using Portainer Agent (Remote Docker)

On the remote Docker host, deploy the agent:

```bash
navig run "docker run -d \
  -p 9001:9001 \
  --name portainer_agent \
  --restart=always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/docker/volumes:/var/lib/docker/volumes \
  portainer/agent:latest"
```

Then add the environment in Portainer UI:
- Environment type: Docker (Agent)
- Environment URL: `remote-host:9001`

### SSL/TLS Configuration

```bash
# Create certs directory
navig run "mkdir -p /opt/portainer/certs"

# Generate self-signed certificate (or use your own)
navig run "openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /opt/portainer/certs/portainer.key \
  -out /opt/portainer/certs/portainer.crt \
  -subj '/CN=portainer.example.com'"
```

Update docker-compose.yml:
```yaml
services:
  portainer:
    # ... other settings
    command: --sslcert /certs/portainer.crt --sslkey /certs/portainer.key
    volumes:
      - /opt/portainer/certs:/certs:ro
```

### Behind Reverse Proxy (Traefik)

```yaml
services:
  portainer:
    image: portainer/portainer-ce:latest
    container_name: portainer
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /opt/portainer/data:/data
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.portainer.rule=Host(`portainer.example.com`)"
      - "traefik.http.routers.portainer.entrypoints=websecure"
      - "traefik.http.routers.portainer.tls.certresolver=letsencrypt"
      - "traefik.http.services.portainer.loadbalancer.server.port=9000"
    networks:
      - proxy

networks:
  proxy:
    external: true
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| data_dir | `/opt/portainer/data` | Portainer data (config, database) |
| docker_socket | `/var/run/docker.sock` | Docker socket |
| compose_file | `/opt/portainer/docker-compose.yml` | Docker Compose file |

## Default Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 9443 | HTTPS | Web UI (secure) |
| 9000 | HTTP | Web UI (insecure) |
| 8000 | TCP | Edge Agent communication |

## Backup & Restore

### Backup Portainer Data

```bash
# Stop Portainer
navig run "cd /opt/portainer && docker compose down"

# Create backup
navig run "tar -czvf /backup/portainer-$(date +%Y%m%d).tar.gz /opt/portainer/data"

# Download backup
navig download /backup/portainer-*.tar.gz ./backups/

# Start Portainer
navig run "cd /opt/portainer && docker compose up -d"
```

### Restore Portainer Data

```bash
# Stop Portainer
navig run "cd /opt/portainer && docker compose down"

# Upload backup
navig upload ./backups/portainer-backup.tar.gz /backup/

# Restore
navig run "rm -rf /opt/portainer/data/*"
navig run "tar -xzvf /backup/portainer-backup.tar.gz -C /"

# Start Portainer
navig run "cd /opt/portainer && docker compose up -d"
```

## API Usage

### Get JWT Token

```bash
# Login and get token
navig run 'curl -s -X POST https://localhost:9443/api/auth \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"password\"}" \
  --insecure | jq -r ".jwt"'
```

### API Examples

```bash
# Get all endpoints (environments)
navig run 'curl -s -H "Authorization: Bearer $TOKEN" \
  https://localhost:9443/api/endpoints --insecure | jq'

# Get all containers
navig run 'curl -s -H "Authorization: Bearer $TOKEN" \
  https://localhost:9443/api/endpoints/1/docker/containers/json --insecure | jq'

# Get all images
navig run 'curl -s -H "Authorization: Bearer $TOKEN" \
  https://localhost:9443/api/endpoints/1/docker/images/json --insecure | jq'
```

## Troubleshooting

### Can't Access UI

```bash
# Check container is running
navig run "docker ps -f name=portainer"

# Check ports
navig run "ss -tlnp | grep -E ':(9000|9443)'"

# Check logs
navig run "docker logs portainer 2>&1 | tail -50"
```

### Admin Timeout (First Setup)

If you didn't create admin account within 5 minutes:

```bash
# Stop and reset
navig run "cd /opt/portainer && docker compose down"
navig run "rm -rf /opt/portainer/data/*"
navig run "cd /opt/portainer && docker compose up -d"
```

### Reset Admin Password

```bash
# Stop Portainer
navig run "cd /opt/portainer && docker compose down"

# Reset password (sets admin password to "password")
navig run "docker run --rm -v /opt/portainer/data:/data \
  portainer/helper-reset-password"

# Or set specific password
navig run "docker run --rm -v /opt/portainer/data:/data \
  portainer/portainer-ce:latest \
  --admin-password-file=/path/to/password-file"

# Start Portainer
navig run "cd /opt/portainer && docker compose up -d"
```

### Docker Socket Permission

```bash
# Check socket permissions
navig run "ls -la /var/run/docker.sock"

# Fix permissions
navig run "chmod 666 /var/run/docker.sock"
```

## Security Best Practices

1. **HTTPS Only**: Use port 9443 or put behind reverse proxy
2. **Strong Password**: Use complex admin password
3. **RBAC**: Create users with minimal required permissions
4. **Network Isolation**: Limit access via firewall
5. **Regular Updates**: Keep Portainer updated
6. **Edge Agent TLS**: Use TLS for Edge Agent communications

## References

- Official Website: https://portainer.io
- Documentation: https://docs.portainer.io
- API Documentation: https://docs.portainer.io/api/access
- GitHub: https://github.com/portainer/portainer
- Docker Hub: https://hub.docker.com/r/portainer/portainer-ce


