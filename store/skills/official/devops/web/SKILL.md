---
name: nginx-web
description: Web server and reverse proxy management.
metadata:
  navig:
    emoji: 🌐
    requires:
      bins: [nginx, certbot]
      tools: [navig.web]
---

# Nginx Web Skill

Manage web sites, SSL certificates, and proxy configurations.

## Site Management

### Deployment
```bash
# 1. Upload config from template
navig file add ./templates/web/site.conf /etc/nginx/sites-available/myapp.conf

# 2. Enable site (symlink)
ln -s /etc/nginx/sites-available/myapp.conf /etc/nginx/sites-enabled/

# 3. Test config
nginx -t

# 4. Reload
systemctl reload nginx
```

### SSL (Let's Encrypt)
```bash
# Obtain cert for domain
certbot --nginx -d example.com -d www.example.com
```

## Templates

### Reverse Proxy (Standard)
```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

### Static Site
```nginx
server {
    listen 80;
    server_name static.example.com;
    root /var/www/html;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

## Troubleshooting
```bash
# Check error logs
navig file show /var/log/nginx/error.log --tail --lines 50

# Check access logs for 404s
grep "404" /var/log/nginx/access.log | tail -n 20
```



