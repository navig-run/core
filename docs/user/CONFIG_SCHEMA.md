# NAVIG Configuration Schema Reference

This document provides a complete reference for NAVIG's configuration file format.

---

## File Locations

### Host Configurations
**Location**: `~/.navig/hosts/*.yaml`

Each file represents one physical/remote server (host) and contains:
- Host connection details (SSH)
- Host-level settings and metadata
- ~~Multiple apps hosted on that server~~ (Legacy format - see below)

### App Configurations (v2.1+)
**Location**: `~/.navig/apps/*.yaml`

Each file represents one app and contains:
- App name and host reference
- App-specific paths and settings
- Webserver and database configuration
- App metadata

**Note**: Apps can be stored in two formats:
1. **Individual files** (v2.1+, recommended): `.navig/apps/<name>.yaml`
2. **Embedded in host YAML** (legacy, still supported): `.navig/hosts/<host>.yaml` under `apps:` field

---

## Host Configuration Structure

```yaml
# Host metadata (REQUIRED)
name: string                    # Host identifier (must match filename)
host: string                    # SSH hostname or IP address
port: integer                   # SSH port (default: 22)
user: string                    # SSH username
ssh_key: string                 # Path to SSH private key
ssh_password: string            # SSH password (optional, not recommended)

# Host-level settings (OPTIONAL)
default_app: string         # Default app to use when not specified

# Host-level database credentials (OPTIONAL)
# Used for host-level database management operations
database:
  root_user: string             # Database root username (for host-level DB management)
  root_password: string         # Database root password

# Host metadata (OPTIONAL)
metadata:
  description: string           # Human-readable description
  location: string              # Physical location (e.g., "Germany", "US East")
  provider: string              # Hosting provider (e.g., "myhost", "Hetzner", "AWS")
  os: string                    # Operating system (e.g., "Ubuntu 22.04")
  type: string                  # Host type (e.g., "local", "vps", "dedicated")
  created_at: string            # ISO 8601 timestamp
  last_updated: string          # ISO 8601 timestamp

# Services configuration (OPTIONAL)
services:
  web: string                   # Web server service name (nginx, apache2)
  php: string                   # PHP service name (php8.2-fpm, php8.1-fpm)
  database: string              # Database service name (mysql, postgresql)
  cache: string                 # Cache service name (redis-server, memcached)
  queue: string                 # Queue service name (supervisor, systemd)

# Apps on this host (LEGACY FORMAT - v2.0 and earlier)
# NOTE: Use individual app files (.navig/apps/*.yaml) instead
apps:
  app_name:                 # App identifier (use environment suffix for variants)
    # See App Schema below
```

---

## Individual App File Structure (v2.1+)

**Recommended format**: Each app in its own file at `.navig/apps/<name>.yaml`

```yaml
# App identification (REQUIRED)
name: string                    # App name (must match filename)
host: string                    # Host reference (must exist in hosts/ directory)

# App metadata (OPTIONAL)
metadata:
  description: string           # Human-readable description
  type: string                  # App type (laravel, wordpress, nodejs, django, etc.)
  environment: string           # Environment name (production, staging, development)
  version: string               # App version
  repository: string            # Git repository URL
  domain: string                # Primary domain name
  created: string               # ISO 8601 timestamp (auto-generated)
  updated: string               # ISO 8601 timestamp (auto-updated)

# File system paths (OPTIONAL)
paths:
  web_root: string              # Web server document root
  logs: string                  # Log files directory
  nginx_config: string          # Nginx configuration file path
  php_config: string            # PHP-FPM pool configuration path

# Database configuration (OPTIONAL)
database:
  type: string                  # Database type (mysql, postgresql, mariadb, sqlite)
  host: string                  # Database host (default: localhost)
  port: integer                 # Database port (mysql: 3306, postgresql: 5432)
  name: string                  # Database name
  user: string                  # Database username
  password: string              # Database password (consider encryption)
  remote_port: integer          # Remote database port (for SSH tunneling)
  local_tunnel_port: integer    # Local port for SSH tunnel

# Web server configuration (REQUIRED)
webserver:
  type: string                  # REQUIRED: nginx or apache2
  service_name: string          # Service name for systemctl commands
  config_test_command: string   # Command to test configuration (e.g., "nginx -t")
  config_file: string           # Path to webserver config file
  ssl_enabled: boolean          # Whether SSL is enabled

# Services configuration (OPTIONAL)
services:
  php: string                   # PHP service name (php8.2-fpm, php8.1-fpm)
  database: string              # Database service name (mysql, postgresql)
  cache: string                 # Cache service name (redis-server, memcached)
  queue: string                 # Queue service name (supervisor, systemd)

# Environment variables (OPTIONAL)
env:
  APP_ENV: string               # Application environment
  APP_DEBUG: boolean            # Debug mode
  # ... other environment variables
```

**Example**:
```yaml
# .navig/apps/pigkiss.yaml
name: pigkiss
host: vultr

metadata:
  description: "PigKiss Production Site"
  type: laravel
  environment: production
  domain: pigkiss.com
  created: "2025-11-25T10:30:00"
  updated: "2025-11-25T14:20:00"

paths:
  web_root: /var/www/pigkiss
  log_path: /var/log/pigkiss

webserver:
  type: nginx
  config_file: /etc/nginx/sites-available/pigkiss
  ssl_enabled: true

database:
  name: pigkiss_db
  user: pigkiss_user
  host: localhost
```

---

## Legacy App Schema (Embedded in Host YAML)

**Note**: This format is still supported but deprecated. Use individual app files instead.

Each app under `apps:` in host YAML has the following structure:

```yaml
app_name:
  # App metadata (OPTIONAL)
  metadata:
    description: string         # Human-readable description
    type: string                # App type (laravel, wordpress, nodejs, django, etc.)
    environment: string         # Environment name (production, staging, development) - for documentation only
    version: string             # App version
    repository: string          # Git repository URL
    domain: string              # Primary domain name
  
  # File system paths (OPTIONAL)
  paths:
    web_root: string            # Web server document root
    logs: string                # Log files directory
    nginx_config: string        # Nginx configuration file path
    php_config: string          # PHP-FPM pool configuration path

  # Database configuration (OPTIONAL)
  database:
    type: string                # Database type (mysql, postgresql, mariadb, sqlite)
    host: string                # Database host (default: localhost)
    port: integer               # Database port (mysql: 3306, postgresql: 5432)
    name: string                # Database name
    user: string                # Database username
    password: string            # Database password (consider encryption)
    remote_port: integer        # Remote database port (for SSH tunneling)
    local_tunnel_port: integer  # Local port for SSH tunnel

  # Services configuration (OPTIONAL)
  services:
    web: string                 # Web server service name (nginx, apache2)
    php: string                 # PHP service name (php8.2-fpm, php8.1-fpm)
    database: string            # Database service name (mysql, postgresql)
    cache: string               # Cache service name (redis-server, memcached)
    queue: string               # Queue service name (supervisor, systemd)

  # Web server configuration (REQUIRED)
  webserver:
    type: string                # REQUIRED: nginx or apache2 (auto-detected by webserver commands)
    service_name: string        # Service name for systemctl commands
    config_test_command: string # Command to test configuration (e.g., "nginx -t")
    reload_command: string      # Command to reload configuration
    restart_command: string     # Command to restart service
  
  # Template configurations (OPTIONAL)
  templates:
    enabled: array              # List of enabled template names
```

---

## Field Descriptions

### Host-Level Fields

#### `name` (REQUIRED)
- **Type**: String
- **Description**: Unique identifier for the host. Must match the filename (without `.yaml` extension).
- **Example**: `myhost`, `example-vps`, `local`

#### `host` (REQUIRED)
- **Type**: String
- **Description**: SSH hostname or IP address.
- **Example**: `srv.example.host`, `10.0.0.10`, `localhost`

#### `port` (REQUIRED)
- **Type**: Integer
- **Description**: SSH port number.
- **Default**: `22`
- **Example**: `22`, `2222`

#### `user` (REQUIRED)
- **Type**: String
- **Description**: SSH username for authentication.
- **Example**: `root`, `developer`, `deploy`

#### `ssh_key` (REQUIRED if `ssh_password` not provided)
- **Type**: String
- **Description**: Path to SSH private key file.
- **Example**: `~/.ssh/id_rsa`, `~/.ssh/myhost`

#### `ssh_password` (OPTIONAL)
- **Type**: String
- **Description**: SSH password for authentication. Not recommended; use SSH keys instead.
- **Security**: Consider using SSH keys for better security.

#### `default_app` (OPTIONAL)
- **Type**: String
- **Description**: Default app to use when `--app` flag is not specified.
- **Example**: `myapp`, `portfolio`

---

### App-Level Fields

#### `metadata.description` (OPTIONAL)
- **Type**: String
- **Description**: Human-readable description of the app.
- **Example**: `"myapp production environment"`

#### `metadata.type` (OPTIONAL)
- **Type**: String
- **Description**: App framework or type.
- **Example**: `laravel`, `wordpress`, `nodejs`, `django`, `react`, `vue`

#### `metadata.environment` (OPTIONAL)
- **Type**: String
- **Description**: Environment name. **For documentation only** - not used by commands.
- **Example**: `production`, `staging`, `development`
- **Note**: Use app name suffixes for environment variants (e.g., `myapp-staging`)

#### `metadata.domain` (OPTIONAL)
- **Type**: String
- **Description**: Primary domain name for the app.
- **Example**: `myapp.com`, `staging.myapp.com`, `localhost:8000`

#### `webserver.type` (REQUIRED)
- **Type**: String
- **Description**: Web server type. **REQUIRED** - auto-detected by webserver commands.
- **Allowed Values**: `nginx`, `apache2`
- **Example**: `nginx`, `apache2`
- **Note**: This field is mandatory. Webserver commands will fail if it's missing.

#### `database.type` (OPTIONAL)
- **Type**: String
- **Description**: Database management system type.
- **Allowed Values**: `mysql`, `postgresql`, `mariadb`, `sqlite`
- **Example**: `mysql`, `postgresql`

#### `database.local_tunnel_port` (OPTIONAL)
- **Type**: Integer
- **Description**: Local port to use for SSH tunnel to remote database.
- **Example**: `3307`, `5433`
- **Note**: Used by `navig tunnel` command for database access.

---

## Environment Naming Convention

Environments are handled via **naming convention** (separate apps with suffixes):

```yaml
apps:
  myapp:           # Production (default, no suffix)
  myapp-staging:   # Staging environment
  myapp-dev:       # Development environment
```

**Key Points**:
- Each environment is a **separate, independent app**
- No config merging or inheritance
- Use suffixes: `-staging`, `-dev`, `-prod` (optional)
- The `metadata.environment` field is optional and for documentation only

**Note**: The `--env` flag is **reserved for future v2.0** and is NOT implemented in this version.

---

## Webserver Type Auto-Detection

Webserver commands (e.g., `webserver-reload`, `webserver-restart`) **auto-detect** the webserver type from `app_config['webserver']['type']`.

**No `--server` flag needed**:
```bash
# Correct (webserver type auto-detected)
navig --host myhost --app myapp webserver-reload

# Incorrect (--server flag removed)
navig --host myhost --app myapp webserver-reload --server nginx  # ❌ ERROR
```

**Validation**:
- Commands fail with clear error if `webserver.type` is missing
- Error message includes instructions to add the field

---

## Web Tools Configuration

Configure web fetching and search capabilities in `~/.navig/config.yaml`:

```yaml
# Web content tools configuration
web:
  fetch:
    enabled: true                    # Enable/disable web fetching
    timeout_seconds: 30              # HTTP request timeout
    max_chars: 50000                 # Maximum characters to extract
    user_agent: "NAVIG/2.1"          # Custom User-Agent string (optional)
    cache_ttl_minutes: 15            # Cache TTL for fetched content
    
  search:
    enabled: true                    # Enable/disable web search
    provider: brave                  # Search provider: brave, duckduckgo
    api_key: null                    # Brave Search API key (from https://brave.com/search/api/)
    max_results: 10                  # Maximum search results
    cache_ttl_minutes: 15            # Cache TTL for search results
    
  docs:
    enabled: true                    # Enable documentation search
    include_paths:                   # Additional doc paths to search
      - ~/.navig/docs
      - ./docs
```

### Field Descriptions

#### `web.fetch.enabled`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Enable URL fetching and content extraction

#### `web.fetch.timeout_seconds`
- **Type**: Integer
- **Default**: `30`
- **Description**: HTTP request timeout in seconds

#### `web.fetch.max_chars`
- **Type**: Integer
- **Default**: `50000`
- **Description**: Maximum characters to extract from page content

#### `web.search.provider`
- **Type**: String
- **Default**: `brave`
- **Options**: `brave`, `duckduckgo`
- **Description**: Web search provider. Brave requires API key.

#### `web.search.api_key`
- **Type**: String
- **Default**: `null`
- **Description**: API key for Brave Search (get from https://brave.com/search/api/)

---

## Migration from Legacy Format

Old format (`~/.navig/apps/*.yaml`):
```yaml
name: production
host: srv.example.com
user: root
ssh_key: ~/.ssh/production
database:
  type: mysql
  name: myapp_db
services:
  web: nginx
```

New format (`~/.navig/hosts/production.yaml`):
```yaml
name: production
host: srv.example.com
user: root
ssh_key: ~/.ssh/production
default_app: production

apps:
  production:
    database:
      type: mysql
      name: myapp_db
    services:
      web: nginx
    webserver:
      type: nginx  # Auto-extracted from services.web during migration
```

**Migration Command**:
```bash
navig config migrate
```

See `docs/MIGRATION_GUIDE.md` for detailed migration instructions.

---

## Complete Example

See `examples/hosts/` directory for complete example configurations:
- `local.yaml` - Local development
- `myhost.yaml` - Production VPS with multiple apps
- `example-vps.yaml` - Alternative VPS

---

## Validation

To validate your configuration:
```bash
navig config validate
navig config show <host_name>
navig config show <host_name>:<app_name>
```



