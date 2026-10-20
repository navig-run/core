# Skills

Manage AI skill definitions stored under the skills/ directory.

## Commands

- `navig skill list`           List available skills
- `navig skill tree`           Show skills grouped by category
- `navig skill show <name>`    Show skill details, commands, and examples
- `navig skill run <spec>`     Run a skill command

## Skill Run

The `run` command routes to skill commands or entrypoints.

**Spec format:**

- `<skill>:<command>` — run a named navig-command from the skill
- `<skill>` — run the skill's entrypoint (main.py / index.js)

## Examples

```bash
# List and discover
navig skill list
navig skill list --plain
navig skill list --json
navig skill tree

# Show skill details
navig skill show docker-manage
navig skill show official/docker-ops --json

# Run skill commands
navig skill run docker-manage:ps
navig skill run docker-manage:stats
navig skill run docker-manage:restart web-1   # fills the {container} placeholder

# Override the skills directory
navig skill list --dir C:/path/to/skills
```

## Notes

- Skills are discovered by searching for SKILL.md files with YAML frontmatter.
- Skills can declare `navig-commands` (mapped to CLI commands) and/or an `entrypoint` (.py/.js script).
- Use `--plain` for scripting and `--json` for structured output.
- Risky commands (destructive/moderate) require confirmation unless `--yes` is passed.
