# `navig context`

Manage host/app context for the current project directory.

Context determines which host/app NAVIG commands target. Resolution priority:
1. `--host`/`--app` flags (command line)
2. `NAVIG_ACTIVE_HOST`/`NAVIG_ACTIVE_APP` (environment variables)
3. `.navig/config.yaml` (project-local, created by `context set`)
4. User cache (global, set by `navig host use`)
5. Default host (from `~/.navig/config.yaml`)

Typical flow:
- Check current context: `navig context show`
- Set project context: `navig context set --host production`
- Initialize for new project: `navig context init`
- Clear project context: `navig context clear`

Examples:
- `navig context` — show current context (alias for `context show`)
- `navig context show --json` — JSON output for scripting
- `navig context set --host staging --app api` — set project defaults
- `navig ctx show` — short alias

Automation:
- Use `--plain` for one-line output: `host=prod source=project app=none`
- Use `--json` for structured output

Project isolation:
- Each project can have its own `.navig/config.yaml`
- Add `.navig/` to `.gitignore` to keep context local
- Use `NAVIG_ACTIVE_HOST` in CI/CD for explicit context


