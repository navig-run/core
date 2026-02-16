# NAVIG Workflow System

> Reusable command sequences for automated server operations

## Overview

The NAVIG Workflow System allows you to define and execute sequences of NAVIG commands as reusable workflows. Workflows are stored as YAML files and support:

- **Sequential command execution** with progress tracking
- **Variable substitution** for flexible, reusable workflows
- **Conditional steps** (continue on error, skip on error)
- **Dry-run mode** to preview without executing
- **Interactive prompts** for user confirmation
- **Multi-scope storage** (global and project-local)

## Quick Start

```bash
# List available workflows
navig workflow list

# Preview a workflow (dry-run)
navig workflow run server-health --dry-run

# Execute a workflow
navig workflow run server-health

# Create a custom workflow
navig workflow create my-deployment

# Override variables at runtime
navig workflow run db-snapshot --var host=staging --var db_name=mydb
```

## Workflow Locations

Workflows are discovered from multiple locations in priority order:

| Location | Type | Description |
|----------|------|-------------|
| `.navig/workflows/` | Project-local | Highest priority, project-specific |
| `~/.navig/workflows/` | Global | User-wide workflows |
| `navig/resources/workflows/` | Built-in | Bundled example workflows |

**Priority Rules:**
- Project-local workflows override global ones
- Global workflows override built-in ones
- Use `--global` flag with `create` to force global scope

## CLI Commands

### `navig workflow list`

List all available workflows.

```bash
navig workflow list

# Output:
#  Name            | Source      | Description                    | Steps
# -----------------+-------------+--------------------------------+-------
#  safe-deployment | 📦 builtin  | Deploy with health checks...   | 8
#  my-workflow     | 🏠 global   | Custom deployment              | 5
```

### `navig workflow show <name>`

Display workflow definition and steps.

```bash
navig workflow show server-health

# Shows:
# - Name and description
# - Source and path
# - Variables with defaults
# - All steps with commands
```

### `navig workflow run <name>`

Execute a workflow.

```bash
# Basic execution
navig workflow run server-health

# Dry-run (preview only)
navig workflow run server-health --dry-run

# Skip all prompts
navig workflow run server-health --yes

# Override variables
navig workflow run db-snapshot --var host=staging --var db_name=testdb

# Verbose output
navig workflow run server-health --verbose
```

**Options:**
- `--dry-run, -n` - Preview commands without executing
- `--yes, -y` - Skip all confirmation prompts
- `--verbose, -v` - Show detailed command output
- `--var, -V` - Override variables (format: `name=value`)

### `navig workflow validate <name>`

Validate workflow syntax and structure.

```bash
navig workflow validate my-workflow

# Output: ✓ Workflow 'my-workflow' is valid
#         • 5 steps
#         • 3 variables
```

### `navig workflow create <name>`

Create a new workflow from template.

```bash
# Create in project-local directory
navig workflow create deployment

# Create in global directory
navig workflow create deployment --global
```

### `navig workflow delete <name>`

Delete a workflow.

```bash
navig workflow delete my-workflow

# Force delete without confirmation
navig workflow delete my-workflow --force
```

### `navig workflow edit <name>`

Open workflow in default editor.

```bash
navig workflow edit my-workflow
```

## Workflow File Format

Workflows are YAML files with this structure:

```yaml
# workflow.yaml
name: My Workflow
description: A brief description of what this workflow does
version: "1.0"
author: Your Name

variables:
  host: production
  app_path: /var/www/app
  service: nginx

steps:
  - name: Step name
    command: host use ${host}
    description: What this step does (optional)
    
  - name: Step with prompt
    command: run "systemctl restart ${service}"
    prompt: "Proceed with restart?"
    
  - name: Step that can fail
    command: docker ps
    continue_on_error: true
    
  - name: Skip if previous failed
    command: docker logs app
    skip_on_error: true
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Workflow display name |
| `description` | No | Brief description |
| `version` | No | Workflow version (default: "1.0") |
| `author` | No | Author name |
| `variables` | No | Variable definitions with defaults |
| `steps` | Yes | Array of step definitions |

### Step Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Step display name |
| `command` | Yes | NAVIG command to execute (without `navig` prefix) |
| `description` | No | Step description |
| `prompt` | No | Confirmation prompt before execution |
| `continue_on_error` | No | Continue workflow if step fails (default: false) |
| `skip_on_error` | No | Skip step if previous step failed (default: false) |

### Variables

Variables use `${variable_name}` syntax and are substituted at runtime:

```yaml
variables:
  host: production
  db_name: main_db

steps:
  - name: Set host
    command: host use ${host}
  - name: Dump database
    command: db dump ${db_name}
```

**Variable Resolution:**
1. CLI overrides (`--var name=value`) take highest priority
2. Interactive prompts fill in remaining variables
3. Default values from workflow file used as fallback

## Built-in Workflows

NAVIG includes several example workflows:

### `safe-deployment`

Safe application deployment with health checks and rollback safety.

```bash
navig workflow run safe-deployment --var host=production --var app_path=/var/www/app
```

**Steps:**
1. Set active host
2. Check current status
3. Backup current state
4. Upload build artifacts
5. Set permissions
6. Validate configuration
7. Restart service (with prompt)
8. Verify deployment

### `db-snapshot`

Export production database for local development or backup.

```bash
navig workflow run db-snapshot --var db_name=mydb
```

**Steps:**
1. Set active host
2. List databases
3. Check database size
4. Create database dump
5. Download dump file
6. Verify download
7. Clean remote file (optional)

### `emergency-debug`

Rapid diagnostics for failing services and system issues.

```bash
navig workflow run emergency-debug --var service=nginx
```

**Steps:**
1. System resources check
2. System load view
3. Service status
4. Recent logs
5. Listening ports
6. Docker status
7. Container inspection
8. Container logs
9. Recent system errors

### `server-health`

Comprehensive health check for remote servers.

```bash
navig workflow run server-health --var host=production
```

**Steps:**
1. Connection test
2. System information
3. Uptime and load
4. Memory usage
5. Disk usage
6. Running services
7. Failed services
8. Network connectivity
9. Docker status
10. Recent security events

## Best Practices

### 1. Use Descriptive Names

```yaml
# Good
name: Production Database Backup

# Bad  
name: Backup
```

### 2. Add Descriptions

```yaml
steps:
  - name: Stop application
    command: run "systemctl stop myapp"
    description: Gracefully stop the application before maintenance
```

### 3. Use Prompts for Destructive Operations

```yaml
- name: Delete old data
  command: run "rm -rf /var/log/old/*"
  prompt: "This will permanently delete old logs. Continue?"
```

### 4. Handle Failures Gracefully

```yaml
# Non-critical steps should continue on error
- name: Check optional service
  command: run "systemctl status optional-service"
  continue_on_error: true

# Skip dependent steps if prerequisite failed
- name: Use optional service
  command: run "optional-service-cli status"
  skip_on_error: true
```

### 5. Test with Dry-Run

Always test new workflows with `--dry-run` first:

```bash
navig workflow run my-new-workflow --dry-run
```

### 6. Version Your Workflows

```yaml
name: Deployment Workflow
version: "2.1"
# Increment version when making changes
```

## Example: Custom Deployment Workflow

Create a complete deployment workflow:

```yaml
# ~/.navig/workflows/deploy-app.yaml
name: Full Application Deployment
description: Deploy, verify, and rollback if needed
version: "1.0"
author: DevOps Team

variables:
  host: production
  app: myapp
  app_path: /var/www/myapp
  git_branch: main

steps:
  - name: Set deployment target
    command: host use ${host}
    description: Connect to deployment server

  - name: Pre-deployment health check
    command: health
    description: Ensure server is healthy before deployment

  - name: Create backup
    command: run "cp -r ${app_path} ${app_path}.backup.$(date +%s)"
    description: Backup current version for rollback
    prompt: "Create backup before deployment?"

  - name: Pull latest code
    command: run "cd ${app_path} && git fetch && git checkout ${git_branch} && git pull"
    description: Update code from repository

  - name: Install dependencies
    command: run "cd ${app_path} && npm install --production"
    description: Install/update npm packages
    continue_on_error: true

  - name: Run database migrations
    command: run "cd ${app_path} && npm run migrate"
    description: Apply database migrations
    prompt: "Run database migrations?"

  - name: Restart application
    command: run "systemctl restart ${app}"
    description: Restart application service
    prompt: "Restart application?"

  - name: Wait for startup
    command: run "sleep 5"
    description: Allow time for application to start

  - name: Post-deployment verification
    command: health
    description: Verify deployment succeeded

  - name: Deployment complete
    command: run "echo 'Deployment completed successfully'"
    description: Success notification
```

Execute:

```bash
navig workflow run deploy-app --var app=myapp --var git_branch=release/v2.0
```

## Troubleshooting

### Workflow Not Found

```bash
navig workflow list  # Check if workflow is discoverable
navig workflow validate my-workflow  # Check for syntax errors
```

### Variable Errors

```bash
# Validation will catch undefined variables
navig workflow validate my-workflow

# Override at runtime
navig workflow run my-workflow --var missing_var=value
```

### Step Failures

```bash
# Use dry-run to preview
navig workflow run my-workflow --dry-run --verbose

# Add continue_on_error for non-critical steps
```

### Permission Issues

```bash
# Check workflow file permissions
ls -la ~/.navig/workflows/

# Ensure workflow directory exists
mkdir -p ~/.navig/workflows
```


