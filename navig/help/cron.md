# cron

Persistent job scheduling.

Common commands:
- `navig cron list` — list scheduled jobs
- `navig cron add` — add new scheduled job
- `navig cron remove <name>` — remove a job
- `navig cron run <name>` — run a job immediately
- `navig cron enable <name>` — enable a job
- `navig cron disable <name>` — disable a job
- `navig cron status` — show cron service status

Examples:
- `navig cron list --json`
- `navig cron add --name backup --schedule "0 2 * * *" --command "navig backup export"`
