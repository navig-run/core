# navig suggest

Intelligent command suggestions based on your history, context, and patterns.

## Features

- **History Analysis**: Suggests frequently used commands
- **Context Detection**: Recommends commands based on project type
- **Time Patterns**: Suggests typical commands for time of day
- **Sequence Learning**: Recommends what usually follows your last command

## Usage

```bash
# Show suggestions
navig suggest

# Filter by context
navig suggest --context docker
navig suggest --context database
navig suggest --context deployment
navig suggest --context monitoring

# Run a suggestion directly
navig suggest --run 1
navig suggest --run 2 --dry-run  # Preview first

# Output formats
navig suggest --plain
navig suggest --json
```

## Suggestion Sources

| Icon | Source | Description |
|------|--------|-------------|
| H | History | Frequently used commands |
| S | Sequence | What usually follows previous command |
| T | Time | Typical for current time of day |
| C | Context | Based on detected project type |

## Context Detection

NAVIG automatically detects project type from:

- `docker-compose.yml` → Docker context
- `Dockerfile` → Docker context
- `*.sql` files → Database context
- `migrations/` → Database context
- `deploy/` → Deployment context
- `ansible/` → Deployment context
- `prometheus.yml` → Monitoring context

## Examples

```bash
# Basic suggestions
navig suggest

# Docker-specific suggestions
navig suggest -c docker

# Run the first suggestion
navig suggest -r 1

# Preview what a suggestion would do
navig suggest -r 3 --dry-run

# Pipe to fzf for selection
navig suggest --plain | fzf | xargs navig
```

## See Also

- `navig quick` — Quick action shortcuts
- `navig history` — Command history
- `navig dashboard` — Operations dashboard
