# Formations (Agent Teams)

Formations are multi-agent team bundles that define specialized AI personas
for different domains. Each formation contains agents with unique system
prompts, roles, and council weights for collaborative decision-making.

## Quick Start

```bash
# List all available formations
navig formation list

# Show formation details and agents
navig formation show navig_app

# Initialize a project with a formation
navig formation init navig_app

# List agents in the active formation
navig formation agents

# Run a council deliberation
navig council run "Should we migrate to microservices?"
```

## Commands

### `navig formation list`
List all discovered formations from project and global directories.

Options:
- `--plain`: Plain text output (no Rich formatting)
- `--json`: JSON output for scripting

### `navig formation show <name>`
Show detailed information about a formation including all agents,
their roles, council weights, and API connectors.

Options:
- `--plain`: Plain text output
- `--json`: JSON output

### `navig formation init <name>`
Initialize the current project with a formation by creating
`.navig/profile.json` in the project root.

```bash
navig formation init creative_studio
navig formation init football_club
```

### `navig formation agents`
List all agents in the currently active formation.

Options:
- `--plain`: Plain text output
- `--json`: JSON output

### `navig council run <question>`
Run a multi-agent council deliberation on a question.

Options:
- `--rounds, -r`: Number of deliberation rounds (default: 3)
- `--timeout, -t`: Per-agent timeout in seconds (default: 30)
- `--plain`: Plain text output
- `--json`: JSON output

```bash
navig council run "What's our deployment strategy?" --rounds 2
navig council run "Budget allocation for Q3" --json
```

## Built-in Formations

| Formation | Agents | Domain |
|-----------|--------|--------|
| **navig_app** | 5 | Software development team |
| **creative_studio** | 6 | Creative agency team |
| **football_club** | 6 | Sports management |
| **government** | 5 | Public sector administration |

## Creating Custom Formations

1. Create a directory under `formations/` or `~/.navig/formations/`
2. Add a `formation.json` manifest
3. Add agent JSON files in an `agents/` subdirectory

```
formations/my_team/
  formation.json
  agents/
    leader.agent.json
    analyst.agent.json
```

See `formations/README.md` for the full schema reference.

## Notes

- Formations are discovered dynamically — no hardcoded registry.
- Community formations can be added to `~/.navig/formations/`.
- Project formations override global ones with the same ID or alias.
- Each agent requires a `system_prompt` of at least 100 characters.
- Council deliberation uses agent weights to influence final decisions.
