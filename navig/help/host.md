# host

Manage remote server connections and the active host context.

Common commands:
- `navig host list`
- `navig host add`
- `navig host use <name>`
- `navig host show <name>`
- `navig host test [name]`

Examples:
- `navig host add`
- `navig host use vps`
- `navig --host vps run "uname -a"`

Troubleshooting:
- Validate config: `navig config validate`
- Test SSH: `navig host test vps`


