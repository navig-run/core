# NAVIG Formations

Formations transform NAVIG into domain-specific operational teams.
Each formation contains a set of AI agents with specialized roles,
personalities, and expertise.

## Quick Start

```bash
# List available formations
navig formation list

# Show formation details
navig formation show navig_app

# Activate a formation for your project
navig formation init navig_app

# List agents in the active formation
navig formation agents

# Run a council deliberation
navig council run "Should we refactor the database layer?"
```

## Directory Structure

```
formations/
  <formation-id>/
    formation.json        # Formation manifest
    agents/
      <agent-id>.agent.json  # Agent definitions
```

## Creating a Formation

1. Create a directory under `formations/` (project) or `~/.navig/formations/` (global)
2. Add a `formation.json` manifest:

```json
{
  "id": "my_team",
  "name": "My Team",
  "version": "1.0.0",
  "description": "A custom formation for my project",
  "agents": ["lead", "reviewer", "tester"],
  "default_agent": "lead",
  "aliases": ["team", "my"],
  "api_connectors": [],
  "brief_templates": []
}
```

3. Create `agents/` directory with `.agent.json` files for each agent listed

## Agent Schema

```json
{
  "id": "lead",
  "name": "Team Lead",
  "role": "Technical Leadership",
  "traits": ["decisive", "pragmatic"],
  "personality": "Clear communicator who values simplicity.",
  "scope": ["architecture", "code-review", "planning"],
  "system_prompt": "You are the Team Lead... (min 100 chars)",
  "kpis": ["code-quality", "delivery-speed"],
  "council_weight": 1.5,
  "tools": ["ai", "docs", "analysis"]
}
```

## Built-in Formations

| Formation | Agents | Description |
|-----------|--------|-------------|
| `creative_studio` | 6 | Creative agency (design, marketing, finance, dev) |
| `football_club` | 6 | Football management (coaching, scouting, analytics) |
| `government` | 5 | Government operations (policy, legal, budget, PR) |
| `navig_app` | 5 | Software dev team (architecture, DevOps, QA, security) |

## Council Deliberation

The Council Engine runs multi-agent discussions:

```bash
# Basic deliberation
navig council run "What's our go-to-market strategy?"

# Multi-round deliberation
navig council run "Database migration approach" --rounds 3

# JSON output for automation
navig council run "Sprint priorities" --json
```

Each agent responds from their specialized perspective, and the
default agent synthesizes all responses into a final decision.
