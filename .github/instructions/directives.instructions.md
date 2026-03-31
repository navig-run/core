---
applyTo: '**'
---

# AI Dev Directives — Lightweight Workflow

## Scope
- Reuse existing structure; no ad-hoc folders/files.
- Check `CHANGELOG.md` before related changes.
- Keep repo root clean.

## Local Folder Policy
- `.dev/` is the default AI working folder for scripts, logs, outputs, and scratch artifacts.
- `.local/` is only for backups/moved files and compatibility temp artifacts.
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
