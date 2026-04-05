# NAVIG Repository — Copilot Code Review Instructions

> Applied automatically when Copilot reviews pull requests (GitHub setting: "Use custom instructions when reviewing pull requests").

---

## Language & Framework

- Python project. Use `pyproject.toml`, `pytest`, and `pip install -e .`.
- CLI is Typer/Click-based. Entry point: `navig/cli/__init__.py`. Commands live in `navig/commands/`.
- Tests live in `tests/`. Docs live in `docs/`. AI working artifacts live in `.dev/`.

---

## Code Quality Rules

### No Duplicated Logic
- Before adding a utility, check if it already exists. Do not duplicate helpers, formatters, or converters.
- Reuse existing patterns: `ch.warning()`, `ch.error()`, `ch.dim()` from `navig.console_helper` for all user output.

### Minimal & Focused Changes
- PRs should be targeted. Avoid unrelated refactors bundled with feature changes.
- Do not reformat files that are not part of the PR's functional scope.

### No Legacy Regressions
- Do not reintroduce `hidden=True` deprecated command wrappers that call `deprecation_warning()`.
- Do not add new top-level `@app.command(...)` flat aliases for things that already exist under a command group (e.g. `navig file`, `navig db`, `navig host`, `navig log`).
- The `navig monitor`, `navig security`, and `navig system` legacy flat-command blocks have been removed. Do not restore them.

### Deprecation Policy
- New deprecated commands must use `@deprecated_command(old, new)` decorator from `navig.deprecation`.
- Never show deprecation warnings for commands that are still canonical (e.g. `navig ask`).

---

## CLI Conventions

- All canonical commands follow the `navig <group> <action>` noun-verb pattern.
- New command groups → register lazily in `_EXTERNAL_CMD_MAP` at the bottom of `navig/cli/__init__.py`, not via eager inline imports.
- Hidden short aliases (`h`, `a`, `f`, `l`, `s`) are acceptable when they mirror an existing group entry in `_EXTERNAL_CMD_MAP`.
- Output flags: `--plain` / `--raw` for scripting, `--json` for structured output. Avoid adding `--format` as a free-form flag.

---

## Safety & Confirmation

- Any command that modifies remote state must respect `ctx.obj.get("yes")` before running destructively.
- Never hardcode credentials. Use `navig.vault` or environment variables.
- Operations that call `navig db`, `navig run`, or `navig file remove` on production hosts must have a dry-run path or confirmation gate.

---

## Testing

- Every new command or behaviour change must include or update a test in `tests/`.
- Use the `_invoke_cli(args, capsys)` pattern for CLI tests.
- Tests must not rely on network access, real SSH connections, or external APIs — mock them.
- Do not add `pytest.mark.skip` without a linked issue or a date comment.

---

## Documentation

- If a CLI command's behaviour or flags change, update `docs/user/HANDBOOK.md`.
- If a new command group is added, add it to `docs/user/commands.md` and `docs/INDEX.md`.
- `.local/CHANGELOG.md` must have an entry in `[Unreleased]` for every PR that changes user-facing behaviour. This file is gitignored (internal dev log); the public record is the GitHub Releases page.

---

## Repo Hygiene

- Keep the repo root clean. No temp files, logs, or scratch scripts at root level.
- `.dev/` — AI scripts, logs, outputs, scratch artifacts.
- `.dev/` — backups and compatibility temp files.
- `.github/instructions/` — authoritative dev and AI agent directives. Do not duplicate their content.
- Do not add `node_modules/`, `__pycache__/`, `.env`, or build artefacts to source control.

---

## Windows Compatibility

- This project is actively developed on Windows (PowerShell, Python 3.14). PRs must not introduce Linux-only shell calls without an OS guard.
- Use `os.name == "nt"` checks where behaviour differs. Prefer `pathlib.Path` over raw string paths.
- Do not call `ps aux`, `grep`, or other POSIX-only commands without wrapping them in a platform check.

---

## Exception Handling

- Follow `navig/.github/instructions/exception-policy.instructions.md`.
- Never swallow exceptions silently with a bare `except: pass` unless there is an explicit comment explaining why.
- Log suppressed errors at `logger.debug(...)` level, never at `logger.warning(...)` unless the user needs to act.

---

## Pull Request Checklist (Copilot should flag if missing)

- [ ] `.local/CHANGELOG.md` updated under `[Unreleased]` (gitignored internal log)
- [ ] Tests added or updated for changed behaviour
- [ ] No new hidden deprecated wrappers added
- [ ] No hardcoded credentials or paths
- [ ] `docs/user/HANDBOOK.md` updated if CLI usage changed
- [ ] No bare `except: pass` without explanation
- [ ] Windows compatibility checked for any shell/process calls
- [ ] PR addresses **exactly one issue** — `Closes #N` present in `## Closes` section
- [ ] Issue has a prior analysis comment posted before the PR was opened
- [ ] Automated comment bodies use real newlines (not literal `\n` escape sequences)
- [ ] No C0 control characters (U+0000–U+001F except `\n`/`\t`) in any comment text

---

## GitHub Practices

For the full one-PR-per-issue policy, comment quality standards, issue lifecycle rules,
and anti-patterns, see `.github/instructions/github-practices.instructions.md`.
