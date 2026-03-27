# Skills

Manage AI skill definitions stored under the skills/ directory.

## Commands

- `navig skills list`           List available skills
- `navig skills tree`           Show skills grouped by category
- `navig skills show <name>`    Show skill details, commands, and examples
- `navig skills run <spec>`     Run a skill command

## Skill Run

The `run` command routes to skill commands or entrypoints.

**Spec format:**

- `<skill>:<command>` — run a named navig-command from the skill
- `<skill>` — run the skill's entrypoint (main.py / index.js)

## Examples

```bash
# List and discover
navig skills list
navig skills list --plain
navig skills list --json
navig skills tree

# Show skill details
navig skills show docker-manage
navig skills show file-operations --json

# Run skill commands
navig skills run docker-manage:ps
navig skills run docker-manage:stats
navig skills run file-operations:list-files /var/log
navig skills run git-basics:git-status

# Override the skills directory
navig skills list --dir C:/path/to/skills
```

## Notes

- Skills are discovered by searching for SKILL.md files with YAML frontmatter.
- Skills can declare `navig-commands` (mapped to CLI commands) and/or an `entrypoint` (.py/.js script).
- Use `--plain` for scripting and `--json` for structured output.
- Risky commands (destructive/moderate) require confirmation unless `--yes` is passed.
