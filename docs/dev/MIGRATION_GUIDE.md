# NAVIG Configuration Migration Guide

This guide explains how to migrate from the legacy NAVIG configuration format to the new two-tier hierarchy format.

---

## 📋 **Overview**

**Legacy Format** (v1.0):
- Single-tier: One configuration file per "server" (which conflated remote server + app)
- Location: `~/.navig/apps/*.yaml`
- Limitation: Could not manage multiple apps on the same physical server

**New Format** (v2.0):
- Two-tier hierarchy: Host → App
- Location: `~/.navig/hosts/*.yaml`
- Benefit: Manage multiple apps across different physical servers

---

## 🔄 **Automatic Migration**

NAVIG provides an automatic migration tool that converts your legacy configurations to the new format.

### **Step 1: Backup Your Configurations** (Recommended)

```bash
# Backup is automatic, but you can manually backup too
cp -r ~/.navig/apps ~/.navig/apps.backup
```

### **Step 2: Run Migration (Dry Run)**

```bash
# Preview what will be migrated without making changes
navig config migrate --dry-run
```

This will show you:
- Which configurations will be migrated
- What the new format will look like
- Any potential issues

### **Step 3: Run Migration**

```bash
# Migrate all configurations
navig config migrate
```

This will:
- ✅ Detect legacy format configurations
- ✅ Extract webserver type from `services.web` field
- ✅ Create new host configurations in `~/.navig/hosts/`
- ✅ Backup original files to `~/.navig/apps.backup/`
- ✅ Preserve all your settings

### **Step 4: Verify Migration**

```bash
# List all hosts
navig host list

# Show host configuration
navig config show <host-name>

# Show app configuration
navig config show <host-name>:<app-name>
```

---

## 📝 **Manual Migration**

If you prefer to migrate manually, here's how:

### **Legacy Format Example**

```yaml
# ~/.navig/apps/production.yaml
name: production
host: server.example.com
port: 22
user: root
ssh_key: ~/.ssh/production

database:
  type: mysql
  name: myapp_db
  user: myapp_user
  password: secret

services:
  web: nginx  # ← Webserver type extracted from here
  php: 8.2

paths:
  web_root: /var/www/myapp
  log_path: /var/log/myapp
```

### **New Format Example**

```yaml
# ~/.navig/hosts/production-server.yaml
name: production-server
host: server.example.com
port: 22
user: root
ssh_key: ~/.ssh/production
default_app: myapp

apps:
  myapp:
    database:
      type: mysql
      name: myapp_db
      user: myapp_user
      password: secret
    
    webserver:
      type: nginx  # ← REQUIRED field (extracted from services.web)
    
    services:
      php: 8.2
    
    paths:
      web_root: /var/www/myapp
      log_path: /var/log/myapp
```

### **Key Changes**

1. **Host-level fields** (SSH connection):
   - `name`, `host`, `port`, `user`, `ssh_key` → Moved to top level
   - Added `default_app` field

2. **App-level fields**:
   - All other fields → Moved under `apps.<app-name>`
   - **REQUIRED**: `webserver.type` field (nginx or apache2)

3. **Webserver type extraction**:
   - Old: `services.web: nginx` or `services.web: apache`
   - New: `webserver.type: nginx` or `webserver.type: apache2`

---

## ⚠️ **Important Notes**

### **Webserver Type is REQUIRED**

All apps MUST have a `webserver.type` field:

```yaml
apps:
  myapp:
    webserver:
      type: nginx  # or apache2
```

If missing, you'll get an error:
```
✗ Configuration error: Missing 'webserver.type' in configuration for app 'myapp' on host 'production-server'.
  Please add 'webserver.type: nginx' or 'webserver.type: apache2' to your app config.
```

### **Backward Compatibility**

- Legacy format configurations still work
- NAVIG automatically detects and loads both formats
- You can use both formats simultaneously during migration
- No rush to migrate - take your time!

### **Environment Naming Convention**

For different environments (production, staging, dev), create separate apps:

```yaml
apps:
  myapp:          # Production
    webserver:
      type: apache2
  
  myapp-staging:  # Staging
    webserver:
      type: apache2
  
  myapp-dev:      # Development
    webserver:
      type: apache2
```

---

## 🎯 **After Migration**

### **New CLI Usage**

```bash
# Set active host and app
navig host use production-server
navig app use myapp

# Or use flags
navig --host production-server --app myapp webserver-list-vhosts

# Webserver commands now auto-detect type (no --server flag needed!)
navig webserver-list-vhosts
navig webserver-test-config
navig webserver-reload
```

### **No More --server Flag!**

Webserver type is now auto-detected from your app configuration:

```bash
# ❌ Old way (no longer needed)
navig webserver-reload --server nginx

# ✅ New way (auto-detected)
navig webserver-reload
```

---

## 🆘 **Troubleshooting**

### **Migration Failed**

If migration fails, your original files are safe in `~/.navig/apps.backup/`.

To restore:
```bash
rm -rf ~/.navig/hosts
mv ~/.navig/apps.backup ~/.navig/apps
```

### **Missing Webserver Type**

If you get "Missing webserver.type" error, add it manually:

```yaml
apps:
  myapp:
    webserver:
      type: nginx  # Add this line
```

### **Need Help?**

Run migration with dry-run to see what will happen:
```bash
navig config migrate --dry-run
```

---

## 📚 **Further Reading**

- [Configuration Schema](CONFIG_SCHEMA.md) - Complete field reference
- [Architecture Summary](ARCHITECTURE_SUMMARY.md) - Design overview
- [Troubleshooting](troubleshooting.md) - Common issues and solutions


