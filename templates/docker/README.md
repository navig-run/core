# Docker Template for NAVIG

## Overview

Docker is a platform for developing, shipping, and running applications in containers. Containers package applications with all dependencies, ensuring consistent operation across environments.

## Features

- **Container Management**: Create, start, stop, remove containers
- **Image Management**: Build, pull, push Docker images
- **Volume & Network Management**: Persistent storage and container networking
- **System Maintenance**: Disk usage monitoring and cleanup
- **Docker Compose**: Multi-container application orchestration

## Usage

### Enable the Template

```bash
navig server-template init docker --server <server-name>
navig server-template enable docker --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Check Docker status
navig run "systemctl status docker"

# Restart Docker daemon
navig run "systemctl restart docker"

# View Docker info
navig run "docker info"
```

#### Container Management

```bash
# List running containers
navig run "docker ps"

# List all containers (including stopped)
navig run "docker ps -a"

# Start a container
navig run "docker start container_name"

# Stop a container
navig run "docker stop container_name"

# View container logs
navig run "docker logs -f container_name"

# Execute command in container
navig run "docker exec -it container_name bash"

# Remove stopped containers
navig run "docker container prune -f"
```

#### Image Management

```bash
# List images
navig run "docker images"

# Pull an image
navig run "docker pull nginx:latest"

# Remove an image
navig run "docker rmi nginx:latest"

# Remove unused images
navig run "docker image prune -f"
```

#### Docker Compose

```bash
# Start services (in directory with docker-compose.yml)
navig run "cd /path/to/project && docker compose up -d"

# Stop services
navig run "cd /path/to/project && docker compose down"

# View logs
navig run "cd /path/to/project && docker compose logs -f"

# Restart services
navig run "cd /path/to/project && docker compose restart"
```

## Configuration

### Daemon Configuration

Edit `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "live-restore": true,
  "userland-proxy": false
}
```

Apply changes:
```bash
navig run "systemctl restart docker"
```

### Configure Docker Registry Mirror

```bash
# Add registry mirror
navig run 'cat > /etc/docker/daemon.json << EOF
{
  "registry-mirrors": ["https://mirror.gcr.io"]
}
EOF'

navig run "systemctl restart docker"
```

### Limit Container Resources

```bash
# Run container with memory and CPU limits
navig run "docker run -d --memory=512m --cpus=0.5 nginx"
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| config_dir | `/etc/docker` | Configuration directory |
| daemon_config | `/etc/docker/daemon.json` | Daemon configuration |
| data_dir | `/var/lib/docker` | Docker data (images, containers) |
| socket | `/var/run/docker.sock` | Docker socket |

## Disk Management

### Check Disk Usage

```bash
# Docker disk usage summary
navig run "docker system df"

# Detailed disk usage
navig run "docker system df -v"
```

### Cleanup Commands

```bash
# Remove stopped containers
navig run "docker container prune -f"

# Remove unused images
navig run "docker image prune -f"

# Remove unused volumes
navig run "docker volume prune -f"

# Remove unused networks
navig run "docker network prune -f"

# Full cleanup (keeps tagged images)
navig run "docker system prune -f"

# Aggressive cleanup (removes ALL unused data)
navig run "docker system prune -a --volumes -f"
```

## Volume Backup & Restore

### Backup a Volume

```bash
# Create backup of a volume
navig run "docker run --rm -v myvolume:/source -v /backup:/backup alpine tar cvf /backup/myvolume.tar /source"

# Download backup
navig download /backup/myvolume.tar ./backups/
```

### Restore a Volume

```bash
# Upload backup
navig upload ./backups/myvolume.tar /backup/

# Restore volume
navig run "docker run --rm -v myvolume:/source -v /backup:/backup alpine tar xvf /backup/myvolume.tar -C /"
```

## Troubleshooting

### Docker Won't Start

```bash
# Check logs
navig run "journalctl -u docker -n 50"

# Check disk space
navig run "df -h /var/lib/docker"

# Verify socket
navig run "ls -la /var/run/docker.sock"
```

### Container Issues

```bash
# Inspect container
navig run "docker inspect container_name"

# Check container logs
navig run "docker logs --tail 100 container_name"

# View container processes
navig run "docker top container_name"

# Check container stats
navig run "docker stats --no-stream"
```

### Network Issues

```bash
# List networks
navig run "docker network ls"

# Inspect network
navig run "docker network inspect bridge"

# Check container IP
navig run "docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' container_name"
```

### Permission Denied

```bash
# Add user to docker group
navig run "usermod -aG docker username"

# Fix socket permissions
navig run "chmod 666 /var/run/docker.sock"
```

## Security Best Practices

1. **Don't run as root**: Use `--user` flag for containers
2. **Limit capabilities**: Use `--cap-drop=ALL` and add only needed caps
3. **Read-only filesystem**: Use `--read-only` when possible
4. **No privileged mode**: Avoid `--privileged` unless absolutely necessary
5. **Scan images**: Use Docker Scout or Trivy for vulnerability scanning
6. **Use official images**: Prefer verified images from Docker Hub

## References

- Official Website: https://docker.com
- Documentation: https://docs.docker.com
- Docker Hub: https://hub.docker.com
- Compose Reference: https://docs.docker.com/compose/
- Best Practices: https://docs.docker.com/develop/develop-images/dockerfile_best-practices/


