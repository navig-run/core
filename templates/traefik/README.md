# Traefik Template for NAVIG

## Overview

Traefik is a modern HTTP reverse proxy and load balancer designed for microservices and cloud-native applications. It automatically discovers services and configures routing with built-in Let's Encrypt support for automatic HTTPS.

## Features

- **Automatic Service Discovery**: Docker, Kubernetes, file-based providers
- **Automatic HTTPS**: Built-in Let's Encrypt certificate management
- **Load Balancing**: Multiple strategies with health checks
- **Middleware**: Rate limiting, authentication, headers, redirects
- **Dashboard**: Real-time monitoring and visualization
- **API**: RESTful API for configuration inspection

## Usage

### Enable the Template

```bash
navig server-template init traefik --server <server-name>
navig server-template enable traefik --server <server-name>
```

### Common Operations

#### Container Management

```bash
# Start Traefik
navig run "cd /opt/traefik && docker compose up -d"

# Stop Traefik
navig run "cd /opt/traefik && docker compose down"

# View logs
navig run "cd /opt/traefik && docker compose logs -f traefik"

# Check status
navig run "docker ps -f name=traefik"
```

#### Configuration Inspection

```bash
# List all routers
navig run "curl -s http://localhost:8080/api/http/routers | jq '.[] | {name, rule, service}'"

# List all services
navig run "curl -s http://localhost:8080/api/http/services | jq"

# List all middlewares
navig run "curl -s http://localhost:8080/api/http/middlewares | jq"

# Check entrypoints
navig run "curl -s http://localhost:8080/api/entrypoints | jq"
```

## Initial Setup

### Create Directory Structure

```bash
navig run "mkdir -p /opt/traefik /etc/traefik/dynamic /var/log/traefik"
navig run "touch /etc/traefik/acme.json && chmod 600 /etc/traefik/acme.json"
```

### Docker Compose Configuration

Create `/opt/traefik/docker-compose.yml`:

```yaml
version: "3.8"

services:
  traefik:
    image: traefik:v3.0
    container_name: traefik
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    ports:
      - "80:80"
      - "443:443"
      - "8080:8080"  # Dashboard (restrict in production)
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /etc/traefik/traefik.yml:/traefik.yml:ro
      - /etc/traefik/dynamic:/dynamic:ro
      - /etc/traefik/acme.json:/acme.json
      - /var/log/traefik:/var/log
    networks:
      - proxy

networks:
  proxy:
    external: true
```

### Static Configuration

Create `/etc/traefik/traefik.yml`:

```yaml
api:
  dashboard: true
  insecure: true  # Disable in production

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false
    network: proxy
  file:
    directory: /dynamic
    watch: true

certificatesResolvers:
  letsencrypt:
    acme:
      email: admin@example.com
      storage: /acme.json
      httpChallenge:
        entryPoint: web

log:
  level: INFO
  filePath: /var/log/traefik.log

accessLog:
  filePath: /var/log/access.log
```

### Create Proxy Network

```bash
navig run "docker network create proxy"
```

## Configuration

### Adding a Service (Docker Labels)

Example service in docker-compose.yml:

```yaml
services:
  myapp:
    image: nginx
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.myapp.rule=Host(\`myapp.example.com\`)"
      - "traefik.http.routers.myapp.entrypoints=websecure"
      - "traefik.http.routers.myapp.tls.certresolver=letsencrypt"
      - "traefik.http.services.myapp.loadbalancer.server.port=80"
    networks:
      - proxy
```

### File-Based Dynamic Configuration

Create `/etc/traefik/dynamic/myservice.yml`:

```yaml
http:
  routers:
    myservice:
      rule: "Host(`myservice.example.com`)"
      service: myservice
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt

  services:
    myservice:
      loadBalancer:
        servers:
          - url: "http://10.0.0.10:8080"
          - url: "http://10.0.0.11:8080"
```

### Basic Authentication Middleware

```yaml
http:
  middlewares:
    basic-auth:
      basicAuth:
        users:
          - "admin:$apr1$..." # Use htpasswd to generate
```

Generate password:
```bash
navig run "htpasswd -nb admin password"
```

### Rate Limiting

```yaml
http:
  middlewares:
    rate-limit:
      rateLimit:
        average: 100
        burst: 50
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| static_config | `/etc/traefik/traefik.yml` | Main configuration |
| dynamic_config | `/etc/traefik/dynamic/` | Dynamic routing files |
| acme_storage | `/etc/traefik/acme.json` | Let's Encrypt certificates |
| log_file | `/var/log/traefik/traefik.log` | Application logs |
| access_log | `/var/log/traefik/access.log` | Access logs |

## Default Ports

- **HTTP**: 80
- **HTTPS**: 443
- **Dashboard/API**: 8080

## Troubleshooting

### Certificate Issues

```bash
# Check ACME storage
navig run "cat /etc/traefik/acme.json | jq '.letsencrypt.Certificates'"

# Check certificate resolver logs
navig run "docker logs traefik 2>&1 | grep -i acme"

# Verify domain resolution
navig run "dig +short myapp.example.com"
```

### Routing Not Working

```bash
# Check router status
navig run "curl -s http://localhost:8080/api/http/routers | jq '.[] | select(.status != \"enabled\")'"

# Check service health
navig run "curl -s http://localhost:8080/api/http/services | jq '.[] | {name, status}'"

# Verify Docker labels
navig run "docker inspect myapp | jq '.[0].Config.Labels'"
```

### Connection Issues

```bash
# Check Traefik is running
navig run "docker ps -f name=traefik"

# Check ports are open
navig run "ss -tlnp | grep -E ':(80|443|8080)'"

# Test backend connectivity
navig run "docker exec traefik wget -qO- http://myapp:80"
```

### Log Analysis

```bash
# View recent errors
navig run "tail -100 /var/log/traefik/traefik.log | grep -i error"

# View access logs
navig run "tail -f /var/log/traefik/access.log"

# Container logs
navig run "docker logs --tail 100 traefik"
```

## Security Best Practices

1. **Secure Dashboard**: Disable insecure API or use authentication middleware
2. **TLS Only**: Always use HTTPS entrypoint for production traffic
3. **Docker Socket**: Use socket proxy for better security
4. **Security Headers**: Add security headers middleware
5. **Rate Limiting**: Implement rate limiting for public endpoints
6. **IP Whitelisting**: Restrict dashboard access by IP

### Security Headers Example

```yaml
http:
  middlewares:
    security-headers:
      headers:
        stsSeconds: 31536000
        stsIncludeSubdomains: true
        stsPreload: true
        forceSTSHeader: true
        contentTypeNosniff: true
        browserXssFilter: true
        frameDeny: true
        contentSecurityPolicy: "default-src 'self'"
```

## References

- Official Website: https://traefik.io
- Documentation: https://doc.traefik.io/traefik/
- GitHub: https://github.com/traefik/traefik
- Docker Hub: https://hub.docker.com/_/traefik
- Let's Encrypt: https://letsencrypt.org


