# log

View and manage remote log files.

Common commands:
- `navig log show <path>` — view log file contents
- `navig log run <path>` — tail log in real-time

Examples:
- `navig log show /var/log/nginx/error.log`
- `navig log run /var/log/syslog --lines 100`

Tip:
- For app-specific logs, use `navig docker logs <container>` for containerized apps.
