# config

Validate and manage NAVIG configuration.

Common commands:
- `navig config show <host|host:app>`
- `navig config validate [host]` (also: `navig config test`)
- `navig config migrate`
- `navig config edit`
- `navig config schema install`

Examples:
- `navig config validate`
- `navig config validate example-vps`
- `navig config validate --scope project`
- `navig config validate --scope both --strict`
- `navig config validate --json`
- `navig config schema install --scope global`
- `navig config schema install --write-vscode-settings`

Tips:
- Global config lives in `~/.navig/`
- Project config lives in `.navig/` in your repo (created via `navig init`)


