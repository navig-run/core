# Matomo Addon for NAVIG

Privacy-focused open-source web analytics platform - a powerful Google Analytics alternative that gives you full control over your data.

## Features

- **Privacy First**: Full data ownership, GDPR compliant, no data sampling
- **Self-Hosted**: Complete control over your analytics infrastructure
- **Real-Time Analytics**: Live visitor tracking and reporting
- **Goal Tracking**: Conversion tracking and funnel analysis
- **Heatmaps & Session Recording**: Optional premium plugins
- **API Access**: Full REST API for custom integrations

## Prerequisites

- PHP 8.0+ with extensions (pdo_mysql, mbstring, gd, curl, xml)
- MySQL 5.5+ or MariaDB 10.0+
- Nginx or Apache web server
- Composer (for dependency management)

## Usage

```bash
# Enable the Matomo addon
navig addon enable matomo

# Run report archiving (recommended via cron)
navig addon run matomo archive

# Clear application cache
navig addon run matomo clear_cache

# Update Matomo to latest version
navig addon run matomo update

# Create a new user
navig addon run matomo create_user

# List all tracked websites
navig addon run matomo list_sites

# Add a new website to track
navig addon run matomo add_site

# List installed plugins
navig addon run matomo plugin_list

# Run system diagnostics
navig addon run matomo diagnostics
```

## Configuration

### Template Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `install_dir` | Matomo installation path | `/var/www/matomo` |
| `config_dir` | Configuration directory | `/var/www/matomo/config` |
| `database.default_name` | Database name | `matomo` |
| `default_port` | Web server port | `80` |

### Environment Variables

```bash
MATOMO_DATABASE_HOST=localhost
MATOMO_DATABASE_USERNAME=matomo
MATOMO_DATABASE_PASSWORD=your_password
MATOMO_DATABASE_DBNAME=matomo
MATOMO_DATABASE_TABLES_PREFIX=matomo_
```

## Installation

1. Download Matomo:
```bash
wget https://builds.matomo.org/matomo-latest.zip
unzip matomo-latest.zip -d /var/www/
chown -R www-data:www-data /var/www/matomo
```

2. Create database:
```sql
CREATE DATABASE matomo;
CREATE USER 'matomo'@'localhost' IDENTIFIED BY 'your_password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, DROP, ALTER, 
      CREATE TEMPORARY TABLES, LOCK TABLES ON matomo.* TO 'matomo'@'localhost';
FLUSH PRIVILEGES;
```

3. Configure Nginx:
```nginx
server {
    listen 80;
    server_name analytics.example.com;
    root /var/www/matomo;
    index index.php;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ ^/(index|matomo|piwik|js/index|plugins/HeatmapSessionRecording/configs)\.php {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass unix:/var/run/php/php8.2-fpm.sock;
    }

    location ~* ^.+\.php$ {
        deny all;
        return 403;
    }

    location ~ \.(gif|ico|jpg|png|svg|js|css|htm|html|txt)$ {
        allow all;
    }
}
```

4. Set up archive cron (every hour):
```bash
5 * * * * www-data /usr/bin/php /var/www/matomo/console core:archive > /dev/null 2>&1
```

5. Complete web installation at `http://analytics.example.com`

## Tracking Code

Add to your websites:
```html
<!-- Matomo -->
<script>
  var _paq = window._paq = window._paq || [];
  _paq.push(['trackPageView']);
  _paq.push(['enableLinkTracking']);
  (function() {
    var u="//analytics.example.com/";
    _paq.push(['setTrackerUrl', u+'matomo.php']);
    _paq.push(['setSiteId', '1']);
    var d=document, g=d.createElement('script'), s=d.getElementsByTagName('script')[0];
    g.async=true; g.src=u+'matomo.js'; s.parentNode.insertBefore(g,s);
  })();
</script>
<!-- End Matomo Code -->
```

## API Example

```bash
# Get visits for today
curl "http://analytics.example.com/index.php?\
module=API&method=VisitsSummary.get&\
idSite=1&period=day&date=today&format=JSON&\
token_auth=YOUR_TOKEN"
```

## Resources

- [Official Documentation](https://matomo.org/docs/)
- [API Reference](https://developer.matomo.org/api-reference/reporting-api)
- [GitHub Repository](https://github.com/matomo-org/matomo)
- [Plugin Marketplace](https://plugins.matomo.org/)


