# Caddy Addon for NAVIG

Modern, feature-rich web server with automatic HTTPS. Caddy handles TLS certificate provisioning and renewal automatically using Let's Encrypt or ZeroSSL.

## Features

- **Automatic HTTPS**: Zero-configuration TLS certificates via ACME
- **HTTP/3 Support**: Modern QUIC protocol support out of the box
- **Reverse Proxy**: Built-in load balancing and reverse proxy
- **Simple Config**: Human-readable Caddyfile configuration
- **API Driven**: Full REST API for dynamic configuration
- **Extensible**: Plugin architecture for additional functionality

## Prerequisites

- Linux/macOS/Windows server
- Ports 80 and 443 available
- Valid domain name for automatic HTTPS

## Usage

```bash
# Enable the Caddy addon
navig addon enable caddy

# Reload Caddy after config changes
navig addon run caddy reload

# Validate Caddyfile syntax
navig addon run caddy validate

# Format Caddyfile with proper styling
navig addon run caddy fmt

# Convert Caddyfile to JSON format
navig addon run caddy adapt

# List all installed modules
navig addon run caddy list_modules

# Show Caddy version info
navig addon run caddy version

# Trust Caddy's root CA (for local dev)
navig addon run caddy trust
```

## Configuration

### Template Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `config_file` | Main Caddyfile location | `/etc/caddy/Caddyfile` |
| `data_dir` | Caddy data directory | `/var/lib/caddy` |
| `default_port` | HTTP port | `80` |
| `api.endpoint` | Admin API endpoint | `http://localhost:2019` |

### Environment Variables

```bash
XDG_DATA_HOME=/var/lib/caddy
XDG_CONFIG_HOME=/etc/caddy
CADDY_ADMIN=localhost:2019
```

## Installation

### Using Package Manager (Debian/Ubuntu)

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install caddy
```

### Using xcaddy (with plugins)

```bash
# Install xcaddy
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest

# Build with plugins
xcaddy build --with github.com/caddy-dns/cloudflare
```

## Example Configurations

### Simple Static Site

```caddyfile
example.com {
    root * /var/www/html
    file_server
}
```

### Reverse Proxy

```caddyfile
app.example.com {
    reverse_proxy localhost:3000
}
```

### Multiple Sites with Headers

```caddyfile
example.com {
    root * /var/www/example
    file_server
    encode gzip zstd
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
    }
}

api.example.com {
    reverse_proxy localhost:8080 {
        header_up Host {host}
        header_up X-Real-IP {remote}
    }
}
```

### Load Balancing

```caddyfile
example.com {
    reverse_proxy localhost:3001 localhost:3002 localhost:3003 {
        lb_policy round_robin
        health_uri /health
        health_interval 30s
    }
}
```

### Wildcard Subdomain with DNS Challenge

```caddyfile
*.example.com {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
    }
    @blog host blog.example.com
    handle @blog {
        reverse_proxy localhost:2368
    }
    handle {
        respond "Not found" 404
    }
}
```

## Admin API Examples

```bash
# Get current config
curl http://localhost:2019/config/

# Add a new site dynamically
curl -X POST http://localhost:2019/config/apps/http/servers/srv0/routes \
  -H "Content-Type: application/json" \
  -d '{"handle":[{"handler":"static_response","body":"Hello!"}]}'

# Reload configuration
curl -X POST http://localhost:2019/load \
  -H "Content-Type: text/caddyfile" \
  --data-binary @/etc/caddy/Caddyfile
```

## Resources

- [Official Documentation](https://caddyserver.com/docs/)
- [Caddyfile Concepts](https://caddyserver.com/docs/caddyfile)
- [GitHub Repository](https://github.com/caddyserver/caddy)
- [Module Registry](https://caddyserver.com/docs/modules/)
- [Community Forum](https://caddy.community/)


