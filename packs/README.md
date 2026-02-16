# NAVIG Packs

**Packs** are reusable operational knowledge bundles: runbooks, deployment checklists, backup procedures, and workflow templates.

## When to Use Packs

| I want to... | Use Packs? | Why |
|--------------|------------|-----|
| Document a step-by-step backup procedure | ✅ Yes | Packs are operational runbooks |
| Define where an app stores logs | ❌ No | Use [templates/](../templates/) |
| Teach AI to understand "restart nginx" | ❌ No | Use [skills/](../skills/) |
| Create a deployment checklist | ✅ Yes | Packs are checklists |
| Store reusable command sequences | ✅ Yes | Packs are snippets |

> **See also**: [Content Architecture Guide](../docs/CONTENT_ARCHITECTURE.md) for full decision matrix.

## What Are Packs?

Packs are structured YAML files containing operational knowledge and workflows. They enable:

- **Runbooks** — step-by-step procedures for common tasks
- **Checklists** — pre-flight checks for deployments, migrations, etc.
- **Workflows** — multi-step operational sequences
- **Snippets** — commonly used command sequences

## Pack Structure

Packs use a simple YAML format:

```yaml
name: "Pack Name"
description: "What this pack does"
type: checklist | runbook | workflow | snippet
steps:
  - description: "Step description"
    command: "navig command to run"  # optional
```

## Using Packs

*Documentation for pack usage will be added as the feature develops.*

**Planned syntax**:
```bash
navig pack install community/deployment-checklist
navig pack run deployment-checklist
```

## Community Packs

The `/packs/community/` directory contains example packs to demonstrate the format. These are community-contributed, illustrative examples.

## Contributing Packs

Community-contributed packs are welcome! Guidelines:

1. **Keep packs focused** — one workflow per pack
2. **Use clear descriptions** — explain each step
3. **Test commands** — verify they work on a clean NAVIG installation
4. **Include prerequisites** — document required hosts, databases, etc.
5. **Add attribution** — credit yourself in the pack metadata

Submit packs via Pull Request. See [CONTRIBUTING.md](../CONTRIBUTING.md) for details.

## Pack Categories

Planned categories for community packs:

- **Deployment** — CI/CD workflows, rollback procedures
- **Backup** — database backups, file archives, verification
- **Monitoring** — health checks, alerting setup, log analysis
- **Security** — credential rotation, SSL renewal, firewall rules
- **Maintenance** — cleanup tasks, optimization, updates

## Packs vs Skills vs Templates

| | Packs | Skills | Templates |
|--|-------|--------|-----------|
| **Purpose** | WHAT steps to follow | HOW AI understands requests | WHERE things are on servers |
| **Format** | `.yml` files | `SKILL.md` (Markdown + YAML) | `template.yaml` |
| **Used by** | Humans & automation | AI agent / Telegram bot | NAVIG CLI |
| **Example** | Backup procedure runbook | "Check disk space" → df -h | nginx paths & commands |

## Future Features

- **Pack marketplace** — browse and install community packs
- **Pack versioning** — semantic versioning for pack updates
- **Pack dependencies** — packs that require other packs
- **Interactive packs** — prompt for user input during execution

---

**Status**: 🚧 Packs system in early development. Syntax and features subject to change.


