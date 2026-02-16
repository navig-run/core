# NAVIG Skills Markdown Format

This document specifies the SKILL.md format for creating NAVIG skills using markdown instead of Python code.

## Why Markdown Skills?

- **Lower barrier**: Non-developers can create skills by writing markdown
- **Community contributions**: Easy to share, review, and collaborate
- **Version control friendly**: Readable diffs and review process
- **AI-native**: Instructions are optimized for AI interpretation

## SKILL.md Structure

Every skill is a markdown file with YAML frontmatter:

```markdown
---
name: "Skill Name"
description: "Brief description of what this skill does"
category: "infrastructure|database|monitoring|deployment|utility"
version: "1.0.0"
author: "Author Name"
tags: ["tag1", "tag2"]

dependencies:
  navig_version: ">=1.0.0"
  python_packages: ["requests", "paramiko"]
  system_commands: ["ssh", "docker"]
  
parameters:
  - name: "param_name"
    type: "string|int|bool|list|choice"
    required: true
    default: "default_value"
    description: "What this parameter does"
    choices: ["option1", "option2"]  # For choice type only

outputs:
  - name: "result"
    type: "string"
    description: "Description of output"
---

# Skill Instructions

Detailed instructions for the AI agent...

## Context

Background information the AI needs to know...

## Execution Steps

1. First, do this...
2. Then, do that...
3. Finally, verify...

## Examples

### Example 1: Basic usage
```yaml
input:
  param1: "value1"
output:
  result: "expected output"
```

## Error Handling

- If X happens, do Y
- If connection fails, retry with...

## Notes

Additional context or caveats...
```

## Frontmatter Schema

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique skill name (lowercase, dashes allowed) |
| `description` | string | Brief description (max 200 chars) |
| `category` | string | One of: infrastructure, database, monitoring, deployment, utility |
| `version` | string | Semantic version (e.g., "1.0.0") |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `author` | string | Author name or organization |
| `tags` | list | Searchable tags |
| `dependencies` | object | Required packages and tools |
| `parameters` | list | Input parameters |
| `outputs` | list | Output definitions |

### Dependencies Object

```yaml
dependencies:
  navig_version: ">=1.0.0"  # Required NAVIG version
  python_packages:          # pip packages needed
    - requests
    - pyyaml
  system_commands:          # Commands that must exist
    - ssh
    - docker
    - kubectl
```

### Parameter Definition

```yaml
parameters:
  - name: "hostname"
    type: "string"
    required: true
    description: "Target server hostname"
    
  - name: "port"
    type: "int"
    required: false
    default: 22
    description: "SSH port"
    
  - name: "protocol"
    type: "choice"
    choices: ["http", "https", "tcp"]
    default: "https"
    description: "Connection protocol"
    
  - name: "tags"
    type: "list"
    required: false
    description: "Labels to apply"
```

## Skill Categories

| Category | Description | Examples |
|----------|-------------|----------|
| `infrastructure` | Server, network, cloud | deploy-server, setup-firewall |
| `database` | Database operations | backup-postgres, migrate-mysql |
| `monitoring` | Metrics, alerts, logging | check-disk, setup-alerts |
| `deployment` | CI/CD, releases | deploy-docker, rollback-release |
| `utility` | General utilities | send-notification, generate-report |

## Example Skills

### 1. Check Disk Space

```markdown
---
name: "check-disk-space"
description: "Check disk space on remote servers and alert if low"
category: "monitoring"
version: "1.0.0"
author: "NAVIG Team"
tags: ["disk", "monitoring", "alerts"]

dependencies:
  system_commands: ["ssh"]
  
parameters:
  - name: "host"
    type: "string"
    required: true
    description: "Target host (from hosts config)"
    
  - name: "threshold"
    type: "int"
    required: false
    default: 80
    description: "Alert threshold percentage"
---

# Check Disk Space

Monitor disk usage on remote servers.

## Execution Steps

1. Connect to the target host via SSH
2. Run `df -h` to get disk usage
3. Parse output to find usage percentages
4. Compare against threshold
5. Return status and details

## Commands to Use

```bash
df -h --output=source,pcent,target | tail -n +2
```

## Expected Output

- List of partitions with usage percentages
- Alert status (OK, WARNING, CRITICAL)
- Recommendations if space is low

## Error Handling

- If SSH connection fails, report connectivity issue
- If df command not found, try alternative: `du -sh /*`
```

### 2. Docker Deploy

```markdown
---
name: "docker-deploy"
description: "Deploy a Docker container to a remote host"
category: "deployment"
version: "1.0.0"
author: "NAVIG Team"
tags: ["docker", "deployment", "containers"]

dependencies:
  system_commands: ["ssh", "docker"]
  
parameters:
  - name: "host"
    type: "string"
    required: true
    description: "Target host"
    
  - name: "image"
    type: "string"
    required: true
    description: "Docker image (e.g., nginx:latest)"
    
  - name: "container_name"
    type: "string"
    required: true
    description: "Name for the container"
    
  - name: "ports"
    type: "list"
    required: false
    description: "Port mappings (e.g., ['8080:80'])"
    
  - name: "env_vars"
    type: "list"
    required: false
    description: "Environment variables"
---

# Docker Deploy

Deploy Docker containers to remote hosts.

## Execution Steps

1. SSH to target host
2. Pull the specified image
3. Stop existing container if running
4. Remove old container
5. Start new container with specified config
6. Verify container is running

## Commands

```bash
# Pull image
docker pull {image}

# Stop old container
docker stop {container_name} 2>/dev/null || true

# Remove old container  
docker rm {container_name} 2>/dev/null || true

# Run new container
docker run -d --name {container_name} \
  {port_flags} \
  {env_flags} \
  {image}

# Verify
docker ps | grep {container_name}
```

## Success Criteria

- Container is running
- Health check passes (if defined)
- Ports are accessible
```

## Using Skills

### From CLI

```bash
# List available skills
navig skills list

# Show skill details
navig skills show check-disk-space

# Run a skill
navig ask "use check-disk-space on webserver with threshold 90"

# Install from URL
navig skills install https://github.com/user/skills/raw/main/my-skill.md
```

### From Agent

The AI agent can automatically discover and use skills based on:

1. User's natural language request
2. Skill metadata matching
3. Parameter extraction from context

## Creating Your Own Skills

1. Create a `.md` file following the schema above
2. Place in `~/.navig/skills/` or `.navig/skills/`
3. Validate with `navig skills validate my-skill.md`
4. Test with `navig ask "use my-skill with param1=value"`

## Publishing Skills

Share your skills by:

1. Creating a GitHub repository
2. Publishing to NAVIG skill marketplace (coming soon)
3. Sharing the raw markdown URL

## Best Practices

1. **Clear descriptions**: Be specific about what the skill does
2. **Good examples**: Include realistic usage examples
3. **Error handling**: Document failure modes and recovery
4. **Minimal dependencies**: Only require what's necessary
5. **Idempotent**: Skills should be safe to run multiple times
6. **Validated parameters**: Use types and constraints
