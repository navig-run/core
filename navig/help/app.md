# app

Manage applications on remote hosts.

Common commands:
- `navig app list` — list configured apps
- `navig app add` — add new app interactively
- `navig app use <name>` — switch active app
- `navig app show <name>` — show app configuration
- `navig app edit <name>` — edit app settings
- `navig app remove <name>` — remove app

Examples:
- `navig app list --json`
- `navig app use mysite`
- `navig app show mysite`
- `navig --app mysite run "ls -la"`

Tip:
- Use `-p <app>` as shorthand for `--app <app>` on any command.
