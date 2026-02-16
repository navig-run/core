# hosts

Manage /etc/hosts file entries (local machine).

Common commands:
- `navig hosts view` — view hosts file
- `navig hosts edit` — edit hosts file
- `navig hosts add <ip> <domain>` — add hosts entry

Examples:
- `navig hosts view`
- `navig hosts add 127.0.0.1 myapp.local`

Note: This manages the local /etc/hosts file, not remote server hosts.
Use `navig host` (without s) for remote server management.
