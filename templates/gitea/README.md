# Gitea Template for NAVIG

## Overview

This template provides integration with Gitea, a painless self-hosted Git service. It includes service management commands, repository paths, backup operations, and API access configuration.

## Features

- **Service Management**: Start, stop, restart, and monitor Gitea service
- **Backup Operations**: Create and manage Gitea backups
- **Repository Access**: Direct paths to repository storage
- **API Integration**: Pre-configured API endpoints and authentication
- **Log Access**: Quick access to Gitea service logs

## Usage

### Enable the Template

```bash
navig template enable gitea
```

### Common Operations

#### Service Management

```bash
# Start Gitea
navig run "systemctl start gitea"

# Check status
navig run "systemctl status gitea"

# View logs
navig run "journalctl -u gitea -f"
```

#### Backup and Restore

```bash
# Create backup
navig run "cd /var/lib/gitea && sudo -u git gitea dump -c /etc/gitea/app.ini"

# Download backup
navig download /var/lib/gitea/gitea-dump-*.zip ./backups/

# Upload backup for restore
navig upload ./backups/gitea-dump-20250120.zip /tmp/gitea-restore.zip

# Restore (extract and follow Gitea restore docs)
navig run "cd /tmp && unzip gitea-restore.zip"
```

#### Repository Management

```bash
# List all repositories
navig run "ls -lah /var/lib/gitea/data/gitea-repositories"

# Check Gitea version
navig run "gitea --version"
```

## Paths Provided

| Path | Location | Description |
|------|----------|-------------|
| `gitea_root` | `/var/lib/gitea` | Gitea root directory |
| `gitea_custom` | `/var/lib/gitea/custom` | Custom templates and configs |
| `gitea_data` | `/var/lib/gitea/data` | Application data |
| `repositories` | `/var/lib/gitea/data/gitea-repositories` | Git repositories |
| `gitea_config` | `/etc/gitea/app.ini` | Main configuration file |
| `backup_dir` | `/var/backups/gitea` | Backup storage |
| `log_dir` | `/var/lib/gitea/log` | Log files |

## Services

- `git_service`: gitea
- `gitea_service`: gitea.service (systemd)

## Environment Variables

The template provides these standard Gitea environment variables:

```bash
GITEA_WORK_DIR=/var/lib/gitea
GITEA_CUSTOM=/var/lib/gitea/custom
GITEA_PORT=3000
GITEA_PROTOCOL=https
```

## API Access

Gitea provides a comprehensive REST API:

- **Endpoint**: `http://localhost:3000/api/v1/`
- **Authentication**: API token (generated in user settings)
- **Documentation**: https://docs.gitea.io/en-us/api-usage/

### Creating an API Token

1. Log into Gitea web interface
2. Go to Settings → Applications
3. Generate new token
4. Use in API requests: `Authorization: token YOUR_TOKEN_HERE`

### Example API Calls

```bash
# List repositories (replace TOKEN)
navig run "curl -H 'Authorization: token YOUR_TOKEN' http://localhost:3000/api/v1/user/repos"

# Create repository
navig run "curl -X POST -H 'Authorization: token YOUR_TOKEN' -H 'Content-Type: application/json' -d '{\"name\":\"my-repo\"}' http://localhost:3000/api/v1/user/repos"
```

## Installation

If Gitea is not installed on your server:

```bash
# Download Gitea binary
navig run "wget -O /usr/local/bin/gitea https://dl.gitea.io/gitea/1.21/gitea-1.21-linux-amd64"
navig run "chmod +x /usr/local/bin/gitea"

# Create git user
navig run "adduser --system --shell /bin/bash --gecos 'Git Version Control' --group --disabled-password --home /home/git git"

# Create directory structure
navig run "mkdir -p /var/lib/gitea/{custom,data,log}"
navig run "mkdir -p /etc/gitea"
navig run "chown -R git:git /var/lib/gitea"
navig run "chmod -R 750 /var/lib/gitea"
navig run "chown root:git /etc/gitea"
navig run "chmod 770 /etc/gitea"

# Create systemd service
navig run "cat > /etc/systemd/system/gitea.service << 'EOF'
[Unit]
Description=Gitea (Git with a cup of tea)
After=syslog.target
After=network.target

[Service]
RestartSec=2s
Type=simple
User=git
Group=git
WorkingDirectory=/var/lib/gitea/
ExecStart=/usr/local/bin/gitea web -c /etc/gitea/app.ini
Restart=always
Environment=USER=git HOME=/home/git GITEA_WORK_DIR=/var/lib/gitea

[Install]
WantedBy=multi-user.target
EOF"

# Enable and start
navig run "systemctl daemon-reload"
navig run "systemctl enable gitea"
navig run "systemctl start gitea"
```

## Database Configuration

Gitea supports multiple database backends:

- **SQLite3** (default): `/var/lib/gitea/data/gitea.db`
- **MySQL/MariaDB**: Configure in `app.ini`
- **PostgreSQL**: Configure in `app.ini`

### MySQL Configuration

Edit `/etc/gitea/app.ini`:

```ini
[database]
DB_TYPE = mysql
HOST = 127.0.0.1:3306
NAME = gitea
USER = gitea
PASSWD = your_password
```

## Backup Strategy

### Automated Backups

```bash
# Create backup script
navig run "cat > /usr/local/bin/backup-gitea.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=/var/backups/gitea
DATE=$(date +%Y%m%d_%H%M%S)
cd /var/lib/gitea
sudo -u git gitea dump -c /etc/gitea/app.ini -f $BACKUP_DIR/gitea-dump-$DATE.zip
# Keep only last 7 backups
ls -t $BACKUP_DIR/gitea-dump-*.zip | tail -n +8 | xargs -r rm
EOF"

navig run "chmod +x /usr/local/bin/backup-gitea.sh"

# Add to cron (daily at 2 AM)
navig run "echo '0 2 * * * /usr/local/bin/backup-gitea.sh' | crontab -u root -"
```

### Manual Backup

```bash
# Create backup
navig run "cd /var/lib/gitea && sudo -u git gitea dump -c /etc/gitea/app.ini"

# Download to local machine
navig download /var/lib/gitea/gitea-dump-*.zip ./backups/gitea-$(date +%Y%m%d).zip
```

## Reverse Proxy Configuration

### Nginx

```nginx
server {
    listen 80;
    server_name git.yourdomain.com;
    
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Apache

```apache
<VirtualHost *:80>
    ServerName git.yourdomain.com
    ProxyPreserveHost On
    ProxyPass / http://localhost:3000/
    ProxyPassReverse / http://localhost:3000/
</VirtualHost>
```

## Troubleshooting

### Service Won't Start

Check logs:

```bash
navig run "journalctl -u gitea -n 50 --no-pager"
```

Common issues:
- Port 3000 already in use
- Database connection failed
- Incorrect file permissions
- Missing `app.ini` configuration

### Database Errors

```bash
# Check database connectivity
navig sql "SELECT 1"

# Verify Gitea database exists
navig sql "SHOW DATABASES LIKE 'gitea'"
```

### Permission Issues

```bash
# Fix ownership
navig run "chown -R git:git /var/lib/gitea"
navig run "chmod -R 750 /var/lib/gitea"
```

## Learn More

- Official Site: https://gitea.io
- Documentation: https://docs.gitea.io
- Community: https://discourse.gitea.io
- GitHub: https://github.com/go-gitea/gitea


