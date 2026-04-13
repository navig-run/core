---
applyTo: '**'
---

# AI Dev Directives — Lightweight Workflow

## Scope
- Reuse existing structure; no ad-hoc folders/files.
- Check `CHANGELOG.md` before related changes.
- Keep repo root clean.
- **Search before adding.** Before writing a new function, constant, or config key, grep/search the codebase for an existing equivalent. Extend rather than clone.
- **No hardcoded configurable values.** Timeouts, limits, flags, and tunables belong in `config/defaults.yaml` or a single module-level constant — not scattered as inline literals. One source of truth per value.
- **No overlapping registries or helpers.** Each concept (command map, tool registry, classifier map, formatter) has one authoritative location. Do not create a parallel version elsewhere.

## Local Folder Policy
- `.dev/` is the default AI working folder for scripts, logs, outputs, and scratch artifacts.
- `.dev/` is also used for backups/moved files and compatibility temp artifacts.
- Do not place temp artifacts directly in root.

## Project Conventions
- Python project (`pyproject.toml`, `pytest`, `pip install -e .`).
- Commands in `navig/commands/`, tests in `tests/`, docs in `docs/`.
- Keep changes minimal and focused; avoid unrelated refactors.

## Execution Loop
1. Analyze root cause.
2. Implement focused fix.
3. Validate with targeted checks/tests.
4. Update docs only when behavior/usage changed.
5. Iterate until resolved.

## Safety
- Ask for approval before destructive/high-risk operations.
- Use NAVIG commands for remote/database operations.
- Never bypass safety guards or credential boundaries.

## Debug Log Trigger
If troubleshooting NAVIG behavior, check `~/.navig/debug.log`, categorize errors, fix, re-validate, and add tests when behavior changed.
