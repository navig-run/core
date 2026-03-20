---
name: hestiacp-manage
description: Manage HestiaCP servers, users, domains, and backups via NAVIG
user-invocable: true
navig-commands:
  - navig run "v-list-users"
  - navig run "v-list-web-domains {user}"
  - navig run "v-backup-user {user}"
  - navig run "v-list-databases {user}"
examples:
  - "List all HestiaCP users"
  - "Show domains for admin user"
  - "Backup production user account"
  - "Check web services status"
---

# HestiaCP Server Management

Manage HestiaCP control panel servers using NAVIG with the HestiaCP template.

## Prerequisites

Server must have the `hestiacp` template applied. Check with:
```bash
navig template list
navig host show {host}
```

## Common Tasks

### 1. List Users

**User queries:**
- "Show HestiaCP users"
- "List all hosting accounts"
- "Who has accounts on this server?"

**Command:** `navig run "v-list-users"`

**Response format:**
```
👥 HestiaCP Users on {host}:

✅ admin (Administrator)
   - Domains: 3
   - Databases: 5
   - Disk: 2.3GB / 10GB

✅ client1 (User)
   - Domains: 1
   - Databases: 2
   - Disk: 456MB / 5GB

✅ client2 (User)
   - Domains: 2
   - Databases: 3
   - Disk: 1.2GB / 5GB
```

### 2. List Domains

**User queries:**
- "What domains are hosted?"
- "Show domains for admin"
- "List all websites"

**Command:** `navig run "v-list-web-domains {user}"`

**Response format:**
```
🌐 Domains for {user}:

✅ example.com (active)
   - SSL: ✅ Let's Encrypt (valid until 2026-04-30)
   - PHP: 8.2
   - Traffic: 1.2GB this month

✅ mysite.net (active)
   - SSL: ✅ Let's Encrypt (valid until 2026-05-15)
   - PHP: 8.1
   - Traffic: 456MB this month

⚠️ testsite.org (suspended)
   - Reason: Exceeded bandwidth limit
```

### 3. Check Services Status

**User queries:**
- "Are web services running?"
- "Check HestiaCP status"
- "Is nginx working?"

**Commands:**
```bash
navig run "systemctl status hestia"
navig run "systemctl status nginx"
navig run "systemctl status php8.2-fpm"
navig run "systemctl status mysql"
```

**Response format:**
```
🔧 Service Status on {host}:

✅ HestiaCP: active (running 15 days)
✅ Nginx: active (running 15 days)
✅ PHP 8.2: active (running 15 days)
✅ MySQL: active (running 15 days)
⚠️ Exim4: inactive (stopped)

All critical services are healthy! ✅
```

### 4. Backup User Account

**User queries:**
- "Backup admin account"
- "Create backup for client1"
- "Backup all accounts"

**Command:** `navig run "v-backup-user {user}"`

**Response format:**
```
💾 Creating backup for {user}...

✅ Backup complete!
   File: admin.2026-01-31_10-23-45.tar
   Size: 2.3GB
   Location: /backup/
   Includes:
   - Web domains (3)
   - Databases (5)
   - Email accounts
   - Configuration

Want me to download it to your local machine?
```

### 5. Database Management

**User queries:**
- "Show databases for admin"
- "List all MySQL databases"

**Command:** `navig run "v-list-databases {user}"`

**Response format:**
```
🗄️ Databases for {user}:

• example_db (MySQL, 456MB)
• wordpress_db (MySQL, 1.2GB)
• app_database (MySQL, 234MB)
• analytics (PostgreSQL, 890MB)
• cache_db (MySQL, 45MB)
```

## Advanced Operations

### Check Disk Usage

**User:** "Who's using the most disk space?"
**Command:**
```bash
navig run "v-list-users | grep DISK | sort -k2 -n"
```

**Response:**
```
💾 Disk Usage by User:

1. admin: 2.3GB / 10GB (23%)
2. client2: 1.2GB / 5GB (24%)
3. client1: 456MB / 5GB (9%)
```

### Check SSL Certificates

**User:** "Which SSL certs are expiring soon?"
**Command:**
```bash
navig run "v-list-letsencrypt-domain {user}"
```

**Response:**
```
🔐 SSL Certificate Status:

✅ example.com: 89 days remaining
⚠️ mysite.net: 15 days remaining (renew soon!)
🚨 testsite.org: 3 days remaining (URGENT!)
```

### Restart Web Services

**User:** "Restart web services"
**Command:**
```bash
navig run "v-restart-web && v-restart-proxy"
```

**Response:**
```
🔄 Restarting web services...

✅ Nginx restarted
✅ PHP-FPM restarted
✅ Apache proxy restarted

All services back online! 🚀
```

### Add New User

⚠️ **Administrative operation** - Confirm first!

**User:** "Add new hosting account"
**Response:** "I'll create a new HestiaCP user. I need:
1. Username
2. Password
3. Email
4. Package (default/custom)

What should the username be?"

**Command:**
```bash
navig run "v-add-user {username} {password} {email} {package}"
```

### Add Domain to User

**User:** "Add newsite.com to admin"
**Command:**
```bash
navig run "v-add-web-domain admin newsite.com"
navig run "v-add-letsencrypt-domain admin newsite.com"
```

**Response:**
```
🌐 Adding domain newsite.com...

✅ Domain added successfully
✅ SSL certificate requested
✅ DNS configured

Domain is live at: https://newsite.com
```

## Integration with Templates

The `templates/hestiacp/template.yaml` provides paths and commands. Reference it:

```yaml
paths:
  hestia_root: /usr/local/hestia
  web_root: /home/admin/web
  backup_dir: /backup

commands:
  - v-list-users
  - v-list-web-domains USER
  - v-backup-user USER
```

## Safety Rules

### Safe Operations (No Confirmation Needed)
- `v-list-*` (all list commands)
- `v-backup-*` (backup commands)
- Checking status (`systemctl status`)

### Require Confirmation
- `v-add-*` (adding users/domains/databases)
- `v-delete-*` (🚨 destructive!)
- `v-suspend-*` (suspending accounts)
- `v-restart-*` (restarting services)

### Destructive Operations (Require Double Confirmation)
- `v-delete-user` - "🚨 This will DELETE user {name} and ALL their data. Type 'DELETE {username}' to confirm."
- `v-delete-domain` - "🚨 This will DELETE domain {domain} and ALL files. Type 'DELETE {domain}' to confirm."
- `v-delete-database` - "🚨 This will DELETE database {name}. Type 'DELETE {database}' to confirm."

## Error Handling

- **HestiaCP not installed**: "This server doesn't have HestiaCP. Install it first or check template configuration."
- **User not found**: "User '{name}' doesn't exist. Available users: admin, client1, client2"
- **Permission denied**: "Need root/admin access. Run with: `navig run --sudo 'v-list-users'`"
- **Service down**: "HestiaCP service is not running. Start it with: `navig run 'systemctl start hestia'`"

## Proactive Suggestions

- **SSL expiring**: "⚠️ SSL certificate for mysite.net expires in 15 days. Want me to renew it now?"
- **High disk usage**: "📊 User 'admin' is using 92% of disk quota. Want me to check what's using space?"
- **Service down**: "🚨 Nginx is not running! Want me to restart it?"
- **Backup needed**: "💾 Last backup was 7 days ago. Run a backup now?"

## Examples

**Example 1: Quick Health Check**
- **User:** "Check HestiaCP server status"
- **Actions:**
  1. `navig run "systemctl status hestia"`
  2. `navig run "v-list-users"`
  3. `navig run "df -h"`
- **Response:**
  ```
  ✅ HestiaCP Health Check:
  
  Services: All running ✅
  Users: 3 accounts (admin, client1, client2)
  Disk: 3.9GB / 50GB (8% used)
  Domains: 6 websites hosted
  
  Everything looks healthy! 🚀
  ```

**Example 2: Domain Setup**
- **User:** "Set up newsite.com for admin with SSL"
- **Actions:**
  1. `navig run "v-add-web-domain admin newsite.com"`
  2. `navig run "v-add-letsencrypt-domain admin newsite.com"`
  3. `navig run "v-list-web-domains admin" | grep newsite`
- **Response:**
  ```
  ✅ Domain Setup Complete!
  
  🌐 newsite.com is now live
  🔐 SSL certificate installed
  📂 Files: /home/admin/web/newsite.com/public_html
  
  You can now upload your website files! 🚀
  ```

**Example 3: User Management**
- **User:** "Show me all users and their disk usage"
- **Action:** `navig run "v-list-users plain" | parse disk usage`
- **Response:**
  ```
  👥 Users on {host}:
  
  1. admin: 2.3GB / 10GB (23%) - 3 domains
  2. client2: 1.2GB / 5GB (24%) - 2 domains
  3. client1: 456MB / 5GB (9%) - 1 domain
  
  Total: 3.9GB / 20GB used (20%)
  ```


