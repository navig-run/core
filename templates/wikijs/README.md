# Wiki.js Addon for NAVIG

Modern, powerful and extensible open source Wiki software. Built on Node.js with a beautiful interface, Markdown/WYSIWYG editing, and extensive integration options.

## Features

- **Multiple Editors**: Markdown, Visual (WYSIWYG), and Raw HTML
- **Git Synchronization**: Two-way sync with Git repositories
- **Search Engine**: Full-text search with Elasticsearch support
- **Authentication**: LDAP, OAuth2, SAML, and 20+ auth providers
- **Multilingual**: Built-in internationalization support
- **Access Control**: Granular permissions per page/group
- **Diagrams**: Mermaid, Draw.io, PlantUML integration

## Prerequisites

- Node.js 18+ LTS
- PostgreSQL 11+ (recommended), MySQL, MariaDB, SQLite, or MSSQL
- 1GB+ RAM minimum

## Usage

```bash
# Enable the Wiki.js addon
navig addon enable wikijs

# Check service status
navig addon run wikijs status

# Restart Wiki.js
navig addon run wikijs restart

# View live logs
navig addon run wikijs logs

# Create content backup
navig addon run wikijs backup

# Update to latest version
navig addon run wikijs update

# Run database migrations
navig addon run wikijs migrate
```

## Configuration

### Template Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `install_dir` | Wiki.js installation path | `/opt/wikijs` |
| `config_file` | Configuration file | `/opt/wikijs/config.yml` |
| `default_port` | HTTP port | `3000` |
| `database.default_type` | Database type | `postgresql` |

### Environment Variables

```bash
NODE_ENV=production
DB_TYPE=postgres
DB_HOST=localhost
DB_PORT=5432
DB_USER=wikijs
DB_PASS=your_password
DB_NAME=wikijs
HA_ACTIVE=false
```

## Installation

1. Create system user:
```bash
useradd -r -s /bin/false wiki
mkdir -p /opt/wikijs
```

2. Download and extract:
```bash
cd /opt/wikijs
wget https://github.com/Requarks/wiki/releases/latest/download/wiki-js.tar.gz
tar xzf wiki-js.tar.gz
rm wiki-js.tar.gz
```

3. Create database:
```sql
CREATE USER wikijs WITH PASSWORD 'your_password';
CREATE DATABASE wikijs OWNER wikijs;
```

4. Create config file:
```yaml
# /opt/wikijs/config.yml
port: 3000
db:
  type: postgres
  host: localhost
  port: 5432
  user: wikijs
  pass: your_password
  db: wikijs
  ssl: false

logLevel: info
offline: false

ha: false
```

5. Set permissions:
```bash
chown -R wiki:wiki /opt/wikijs
```

6. Create systemd service:
```ini
# /etc/systemd/system/wiki.service
[Unit]
Description=Wiki.js
After=network.target postgresql.service

[Service]
Type=simple
User=wiki
WorkingDirectory=/opt/wikijs
ExecStart=/usr/bin/node server
Restart=always
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

7. Start service:
```bash
systemctl daemon-reload
systemctl enable --now wiki
```

8. Complete setup at `http://your-server:3000`

## Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name wiki.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name wiki.example.com;

    ssl_certificate /etc/letsencrypt/live/wiki.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/wiki.example.com/privkey.pem;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## GraphQL API Examples

```bash
# Get all pages
curl -X POST http://localhost:3000/graphql \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ pages { list { id title path } } }"}'

# Search pages
curl -X POST http://localhost:3000/graphql \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ pages { search(query: \"kubernetes\") { results { id title } } } }"}'

# Create page
curl -X POST http://localhost:3000/graphql \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation { pages { create(content: \"# Hello\", description: \"Test\", editor: \"markdown\", isPublished: true, locale: \"en\", path: \"test\", title: \"Test Page\") { responseResult { succeeded } } } }"}'
```

## Resources

- [Official Documentation](https://docs.requarks.io/)
- [GitHub Repository](https://github.com/Requarks/wiki)
- [Configuration Reference](https://docs.requarks.io/install/config)
- [Discord Community](https://discord.gg/wikijs)


