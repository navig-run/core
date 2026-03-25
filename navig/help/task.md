# task

`navig task` is a **compatibility alias** for [`navig flow`](flow.md).
Both commands are identical — prefer `navig flow` for new scripts.

## Common commands

| Command | Description |
|---------|-------------|
| `navig task list` | List available flows (`navig flow list`) |
| `navig task run <name>` | Execute a flow (`navig flow run <name>`) |
| `navig task show <name>` | Show flow definition (`navig flow show <name>`) |
| `navig task add` | Create new flow (`navig flow add`) |
| `navig task test <name>` | Validate flow syntax (`navig flow test <name>`) |

## Examples

```bash
navig task list
navig task run deploy-staging --dry-run
navig flow list          # preferred canonical form
```

See also: `navig help flow`, `navig flow --help`
