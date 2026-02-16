# Nginx Template for NAVIG

## Overview

Nginx is a high-performance HTTP server and reverse proxy, as well as an IMAP/POP3 proxy server. This template provides comprehensive management of Nginx installations for web serving, reverse proxying, and load balancing.

## Features

- **Service Management**: Start, stop, restart, and reload Nginx service
- **Configuration Testing**: Validate configuration before applying changes
- **Site Management**: Manage virtual hosts in sites-available/sites-enabled
- **Log Access**: Quick access to access and error logs
- **SSL Support**: Pre-configured paths for SSL certificates

## Usage

### Enable the Template

```bash
navig server-template init nginx --server <server-name>
navig server-template enable nginx --server <server-name>
```

### Common Operations

#### Service Management

```bash
# Start Nginx
navig run "systemctl start nginx"

# Reload configuration (no downtime)
navig run "systemctl reload nginx"

# Test configuration before applying
navig run "nginx -t"
```

#### Virtual Host Management

```bash
# Create a new virtual host
navig run "nano /etc/nginx/sites-available/mysite.conf"

# Enable the site
navig run "ln -s /etc/nginx/sites-available/mysite.conf /etc/nginx/sites-enabled/"

# Test and reload
navig run "nginx -t && systemctl reload nginx"
```

#### View Logs

```bash
# Access logs
navig run "tail -f /var/log/nginx/access.log"

# Error logs
navig run "tail -f /var/log/nginx/error.log"

# Combined with grep
navig run "tail -100 /var/log/nginx/error.log | grep -i error"
```

## Configuration

### Key Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| worker_processes | auto | Number of worker processes |
| worker_connections | 1024 | Max connections per worker |
| keepalive_timeout | 65 | Keep-alive timeout in seconds |

### Customizing Paths

After enabling, you can customize paths in your server configuration:

```bash
navig server-template set nginx paths.html_root /var/www/myapp --server <server>
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| config_dir | `/etc/nginx` | Main configuration directory |
| sites_available | `/etc/nginx/sites-available` | Available virtual hosts |
| sites_enabled | `/etc/nginx/sites-enabled` | Enabled virtual hosts |
| html_root | `/var/www/html` | Default document root |
| log_dir | `/var/log/nginx` | Log files |
| ssl_dir | `/etc/nginx/ssl` | SSL certificates |

## Default Ports

- **HTTP**: 80
- **HTTPS**: 443

## Common Virtual Host Template

```nginx
server {
    listen 80;
    server_name example.com www.example.com;
    root /var/www/example.com/html;
    index index.html index.htm;

    location / {
        try_files $uri $uri/ =404;
    }

    # PHP-FPM example
    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }
}
```

## Reverse Proxy Template

```nginx
server {
    listen 80;
    server_name app.example.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

## Troubleshooting

### Configuration Errors

```bash
# Test configuration
navig run "nginx -t"

# View specific error details
navig run "nginx -T 2>&1 | head -50"
```

### Permission Issues

```bash
# Check Nginx user
navig run "ps aux | grep nginx"

# Fix permissions
navig run "chown -R www-data:www-data /var/www/html"
```

### Port Already in Use

```bash
# Check what's using port 80
navig run "ss -tlnp | grep :80"

# Kill conflicting process
navig run "fuser -k 80/tcp"
```

## References

- Official Website: https://nginx.org
- Documentation: https://nginx.org/en/docs/
- Wiki: https://www.nginx.com/resources/wiki/
- GitHub: https://github.com/nginx/nginx


