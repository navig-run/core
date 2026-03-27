# `navig run`

Run a command on the active host.

Examples:
- `navig run "ls -la"`
- `navig run --stdin` (pipe multi-line scripts)
- Prefer `--b64` when the command contains JSON, quotes, `$`, or other shell-sensitive characters.

Tip:
- If the user asks to read/edit files, prefer `navig file show/edit` instead of `run cat/...`.
