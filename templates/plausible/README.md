# Plausible Analytics Addon for NAVIG

Privacy-friendly, lightweight web analytics. No cookies, no personal data collection, fully GDPR/CCPA/PECR compliant out of the box.

## Features

- **Privacy First**: No cookies, no personal data, no consent banners needed
- **Lightweight**: < 1KB script (45x smaller than Google Analytics)
- **Simple Dashboard**: Essential metrics without complexity
- **Goal Tracking**: Custom events and conversion tracking
- **UTM Support**: Full campaign attribution
- **API Access**: Complete REST API for integrations
- **Self-Hosted**: Full control over your data

## Prerequisites

- Docker and Docker Compose
- 2GB+ RAM (ClickHouse needs memory)
- Domain with valid SSL certificate
- PostgreSQL (for user data)
- ClickHouse (for analytics events)

## Usage

```bash
# Enable the Plausible addon
navig addon enable plausible

# Start all services
navig addon run plausible start

# Stop all services
navig addon run plausible stop

# Restart services
navig addon run plausible restart

# View logs
navig addon run plausible logs

# Update to latest version
navig addon run plausible update

# Create admin user
navig addon run plausible create_admin

# Backup database
navig addon run plausible backup_db

# Check service status
navig addon run plausible status
```

## Configuration

### Template Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `install_dir` | Installation directory | `/opt/plausible` |
| `config_file` | Environment config file | `/opt/plausible/plausible-conf.env` |
| `default_port` | HTTP port | `8000` |

### Environment Variables

```bash
BASE_URL=https://analytics.example.com
SECRET_KEY_BASE=your_64_char_secret_key
DISABLE_REGISTRATION=invite_only
DATABASE_URL=postgres://postgres:postgres@plausible_db:5432/plausible
CLICKHOUSE_DATABASE_URL=http://plausible_events_db:8123/plausible_events_db
MAILER_EMAIL=analytics@example.com
SMTP_HOST_ADDR=smtp.example.com
SMTP_HOST_PORT=587
SMTP_HOST_USER=your_smtp_user
SMTP_HOST_PASSWORD=your_smtp_password
```

## Installation

1. Create installation directory:
```bash
mkdir -p /opt/plausible
cd /opt/plausible
```

2. Download hosting files:
```bash
git clone https://github.com/plausible/hosting plausible-hosting
cp -r plausible-hosting/* .
rm -rf plausible-hosting
```

3. Generate secret key:
```bash
openssl rand -base64 48
```

4. Create environment config:
```bash
# plausible-conf.env
BASE_URL=https://analytics.example.com
SECRET_KEY_BASE=your_generated_64_char_secret
DISABLE_REGISTRATION=invite_only
```

5. Start services:
```bash
docker compose up -d
```

6. Create admin user:
```bash
docker compose exec plausible /app/bin/plausible rpc \
  'Plausible.Auth.create_user("admin@example.com", "admin", "password123")'
```

7. Access dashboard at `https://analytics.example.com`

## Docker Compose Configuration

```yaml
version: "3.3"
services:
  mail:
    image: bytemark/smtp
    restart: always

  plausible_db:
    image: postgres:16-alpine
    restart: always
    volumes:
      - db-data:/var/lib/postgresql/data
    environment:
      - POSTGRES_PASSWORD=postgres

  plausible_events_db:
    image: clickhouse/clickhouse-server:24.3-alpine
    restart: always
    volumes:
      - event-data:/var/lib/clickhouse
    ulimits:
      nofile:
        soft: 262144
        hard: 262144

  plausible:
    image: ghcr.io/plausible/community-edition:v2.1
    restart: always
    command: sh -c "sleep 10 && /app/bin/plausible start"
    depends_on:
      - plausible_db
      - plausible_events_db
      - mail
    ports:
      - 127.0.0.1:8000:8000
    env_file:
      - plausible-conf.env

volumes:
  db-data:
  event-data:
```

## Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name analytics.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name analytics.example.com;

    ssl_certificate /etc/letsencrypt/live/analytics.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/analytics.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Host $host;
    }
}
```

## Tracking Script

Add to your websites:
```html
<script defer data-domain="yourdomain.com" src="https://analytics.example.com/js/script.js"></script>
```

### Goal Tracking
```javascript
// Custom event
plausible('Signup', {props: {plan: 'premium'}});

// Outbound link tracking
<script defer data-domain="yourdomain.com" src="https://analytics.example.com/js/script.outbound-links.js"></script>
```

## API Examples

```bash
# Get realtime visitors
curl "https://analytics.example.com/api/v1/stats/realtime/visitors?site_id=example.com" \
  -H "Authorization: Bearer YOUR_API_KEY"

# Get aggregate stats
curl "https://analytics.example.com/api/v1/stats/aggregate?site_id=example.com&period=30d&metrics=visitors,pageviews,bounce_rate" \
  -H "Authorization: Bearer YOUR_API_KEY"

# Get breakdown by page
curl "https://analytics.example.com/api/v1/stats/breakdown?site_id=example.com&period=30d&property=event:page" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Resources

- [Official Documentation](https://plausible.io/docs)
- [Self-Hosting Guide](https://plausible.io/docs/self-hosting)
- [GitHub Repository](https://github.com/plausible/analytics)
- [Stats API Reference](https://plausible.io/docs/stats-api)
- [Community Forum](https://github.com/plausible/analytics/discussions)


