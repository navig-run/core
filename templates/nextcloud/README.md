# Nextcloud Template for NAVIG

## Overview

Nextcloud is a self-hosted productivity platform that provides file synchronization, collaboration tools, calendar, contacts, and much more. It's a powerful alternative to cloud services like Google Drive or Dropbox.

## Features

- **File Sync & Share**: Sync files across devices with desktop/mobile clients
- **Collaboration**: Office document editing, collaborative editing
- **Calendar & Contacts**: CalDAV and CardDAV support
- **Communication**: Talk video calls, chat, and screen sharing
- **Apps Ecosystem**: Hundreds of apps to extend functionality
- **Security**: End-to-end encryption, 2FA, audit logging

## Usage

### Enable the Template

```bash
navig server-template init nextcloud --server <server-name>
navig server-template enable nextcloud --server <server-name>
```

### Common Operations

#### Container Management

```bash
# Start Nextcloud
navig run "cd /opt/nextcloud && docker compose up -d"

# Check status
navig run "docker ps -f name=nextcloud"

# View logs
navig run "docker logs -f nextcloud-app"

# Stop
navig run "cd /opt/nextcloud && docker compose down"
```

#### OCC Commands (Nextcloud CLI)

```bash
# Run any occ command
navig run "docker exec -u www-data nextcloud-app php occ status"

# Scan files
navig run "docker exec -u www-data nextcloud-app php occ files:scan --all"

# List apps
navig run "docker exec -u www-data nextcloud-app php occ app:list"

# Enable app
navig run "docker exec -u www-data nextcloud-app php occ app:enable calendar"

# Add trusted domain
navig run "docker exec -u www-data nextcloud-app php occ config:system:set trusted_domains 1 --value=cloud.example.com"
```

## Initial Setup

### Create Directory Structure

```bash
navig run "mkdir -p /opt/nextcloud/{data,config,apps,db}"
```

### Docker Compose Configuration

Create `/opt/nextcloud/docker-compose.yml`:

```yaml
version: "3.8"

services:
  db:
    image: mariadb:10.11
    container_name: nextcloud-db
    restart: unless-stopped
    command: --transaction-isolation=READ-COMMITTED --log-bin=binlog --binlog-format=ROW
    volumes:
      - /opt/nextcloud/db:/var/lib/mysql
    environment:
      - MYSQL_ROOT_PASSWORD=rootpassword
      - MYSQL_DATABASE=nextcloud
      - MYSQL_USER=nextcloud
      - MYSQL_PASSWORD=nextcloudpassword

  redis:
    image: redis:alpine
    container_name: nextcloud-redis
    restart: unless-stopped

  app:
    image: nextcloud:stable-fpm
    container_name: nextcloud-app
    restart: unless-stopped
    volumes:
      - /opt/nextcloud/data:/var/www/html
      - /opt/nextcloud/config:/var/www/html/config
      - /opt/nextcloud/apps:/var/www/html/custom_apps
    environment:
      - MYSQL_HOST=db
      - MYSQL_DATABASE=nextcloud
      - MYSQL_USER=nextcloud
      - MYSQL_PASSWORD=nextcloudpassword
      - REDIS_HOST=redis
      - NEXTCLOUD_ADMIN_USER=admin
      - NEXTCLOUD_ADMIN_PASSWORD=adminpassword
      - NEXTCLOUD_TRUSTED_DOMAINS=localhost cloud.example.com
    depends_on:
      - db
      - redis

  web:
    image: nginx:alpine
    container_name: nextcloud-web
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - /opt/nextcloud/data:/var/www/html:ro
      - /opt/nextcloud/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - app
```

### Nginx Configuration

Create `/opt/nextcloud/nginx.conf`:

```nginx
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    upstream php-handler {
        server app:9000;
    }

    server {
        listen 80;
        server_name _;
        root /var/www/html;

        client_max_body_size 512M;
        fastcgi_buffers 64 4K;

        gzip on;
        gzip_vary on;
        gzip_comp_level 4;
        gzip_min_length 256;
        gzip_proxied expired no-cache no-store private no_last_modified no_etag auth;
        gzip_types application/atom+xml application/javascript application/json application/ld+json application/manifest+json application/rss+xml application/vnd.geo+json application/vnd.ms-fontobject application/x-font-ttf application/x-web-app-manifest+json application/xhtml+xml application/xml font/opentype image/bmp image/svg+xml image/x-icon text/cache-manifest text/css text/plain text/vcard text/vnd.rim.location.xloc text/vtt text/x-component text/x-cross-domain-policy;

        location = /robots.txt {
            allow all;
            log_not_found off;
            access_log off;
        }

        location ~ ^\/(?:\.htaccess|data|config|db_structure\.xml|README) {
            deny all;
        }

        location / {
            rewrite ^ /index.php;
        }

        location ~ ^\/(?:build|tests|config|lib|3rdparty|templates|data)\/ {
            deny all;
        }

        location ~ ^\/(?:\.|autotest|occ|issue|indie|db_|console) {
            deny all;
        }

        location ~ ^\/(?:index|remote|public|cron|core\/ajax\/update|status|ocs\/v[12]|updater\/.+|oc[ms]-provider\/.+|.+\/richdocumentscode\/proxy)\.php(?:$|\/) {
            fastcgi_split_path_info ^(.+?\.php)(\/.*|)$;
            set $path_info $fastcgi_path_info;
            try_files $fastcgi_script_name =404;
            include fastcgi_params;
            fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
            fastcgi_param PATH_INFO $path_info;
            fastcgi_param modHeadersAvailable true;
            fastcgi_param front_controller_active true;
            fastcgi_pass php-handler;
            fastcgi_intercept_errors on;
            fastcgi_request_buffering off;
        }

        location ~ \.(?:css|js|svg|gif|png|jpg|ico)$ {
            try_files $uri /index.php$request_uri;
            expires 6M;
            access_log off;
        }

        location ~ \.woff2?$ {
            try_files $uri /index.php$request_uri;
            expires 7d;
            access_log off;
        }
    }
}
```

### Start Nextcloud

```bash
navig run "cd /opt/nextcloud && docker compose up -d"
```

### Access Nextcloud

1. Open `http://your-server:8080`
2. Log in with admin credentials set in docker-compose.yml
3. Complete setup wizard if needed

## Configuration

### Add Trusted Domain

```bash
navig run "docker exec -u www-data nextcloud-app php occ config:system:set trusted_domains 1 --value=cloud.example.com"
```

### Configure Email

```bash
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_from_address --value=noreply"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_domain --value=example.com"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtpmode --value=smtp"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtphost --value=smtp.example.com"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtpport --value=587"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtpsecure --value=tls"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtpauth --value=1"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtpname --value=user@example.com"
navig run "docker exec -u www-data nextcloud-app php occ config:system:set mail_smtppassword --value=password"
```

### Configure Cron

```bash
# Add cron job on host
navig run 'echo "*/5 * * * * docker exec -u www-data nextcloud-app php cron.php" | crontab -'

# Set cron mode
navig run "docker exec -u www-data nextcloud-app php occ background:cron"
```

## Default Paths

| Path | Location | Description |
|------|----------|-------------|
| data_dir | `/opt/nextcloud/data` | Nextcloud data |
| config_dir | `/opt/nextcloud/config` | Configuration files |
| apps_dir | `/opt/nextcloud/apps` | Custom apps |
| db_data | `/opt/nextcloud/db` | Database files |

## Default Port

- **Web Interface**: 8080

## Backup & Restore

### Full Backup

```bash
# Enable maintenance mode
navig run "docker exec -u www-data nextcloud-app php occ maintenance:mode --on"

# Backup data
navig run "tar -czvf /backup/nextcloud-data-$(date +%Y%m%d).tar.gz /opt/nextcloud/data /opt/nextcloud/config /opt/nextcloud/apps"

# Backup database
navig run 'docker exec nextcloud-db mysqldump -u root -prootpassword nextcloud > /backup/nextcloud-db-$(date +%Y%m%d).sql'

# Disable maintenance mode
navig run "docker exec -u www-data nextcloud-app php occ maintenance:mode --off"

# Download backups
navig download /backup/nextcloud-*.* ./backups/
```

### Restore Backup

```bash
# Enable maintenance mode
navig run "docker exec -u www-data nextcloud-app php occ maintenance:mode --on"

# Upload backups
navig upload ./backups/nextcloud-data-backup.tar.gz /backup/
navig upload ./backups/nextcloud-db-backup.sql /backup/

# Restore files
navig run "tar -xzvf /backup/nextcloud-data-backup.tar.gz -C /"

# Restore database
navig run "docker exec -i nextcloud-db mysql -u root -prootpassword nextcloud < /backup/nextcloud-db-backup.sql"

# Scan files
navig run "docker exec -u www-data nextcloud-app php occ files:scan --all"

# Disable maintenance mode
navig run "docker exec -u www-data nextcloud-app php occ maintenance:mode --off"
```

## Troubleshooting

### File Upload Issues

```bash
# Check PHP settings
navig run "docker exec nextcloud-app php -i | grep -E 'upload_max|post_max|memory'"

# Check nginx config for client_max_body_size
navig run "grep client_max_body_size /opt/nextcloud/nginx.conf"
```

### Database Connection

```bash
# Check database is running
navig run "docker ps -f name=nextcloud-db"

# Test connection
navig run "docker exec nextcloud-db mysql -u nextcloud -pnextcloudpassword -e 'SELECT 1'"
```

### Permission Issues

```bash
# Fix permissions
navig run "docker exec nextcloud-app chown -R www-data:www-data /var/www/html"
```

### Performance Issues

```bash
# Check Redis connection
navig run "docker exec -u www-data nextcloud-app php occ config:system:get redis"

# Enable APCu
navig run "docker exec -u www-data nextcloud-app php occ config:system:set memcache.local --value='\\OC\\Memcache\\APCu'"
```

## Security Best Practices

1. **Use HTTPS**: Always use reverse proxy with TLS
2. **Strong Passwords**: Use complex passwords for admin and database
3. **Brute Force Protection**: Enabled by default
4. **2FA**: Enable two-factor authentication
5. **File Encryption**: Enable server-side encryption
6. **Updates**: Keep Nextcloud and apps updated
7. **Firewall**: Restrict access to only necessary ports

## References

- Official Website: https://nextcloud.com
- Documentation: https://docs.nextcloud.com
- Admin Manual: https://docs.nextcloud.com/server/latest/admin_manual/
- GitHub: https://github.com/nextcloud/server
- Docker Hub: https://hub.docker.com/_/nextcloud


