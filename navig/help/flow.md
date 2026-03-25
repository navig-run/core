# flow

Workflow automation — the canonical command for defining and running reusable workflows.
`navig task` is a compatibility alias for this command.

## Common commands

| Command | Description |
|---------|-------------|
| `navig flow list` | List all defined flows |
| `navig flow run <name>` | Execute a flow |
| `navig flow show <name>` | Show flow YAML definition |
| `navig flow add` | Create new flow interactively |
| `navig flow edit <name>` | Edit flow definition |
| `navig flow test <name>` | Validate flow syntax |
| `navig flow remove <name>` | Delete a flow |

## Flags

- `--dry-run` — preview steps without executing
- `--json` — machine-readable output
- `--plain` / `--raw` — no rich formatting (scripting)

## Examples

```bash
navig flow list
navig flow run deploy-staging --dry-run
navig flow run backup --json
```

Workflow YAML files are stored in `~/.navig/flows/` or `.navig/flows/`.
