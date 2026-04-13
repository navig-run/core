---
applyTo: '**'
---

# NAVIG Contributor Directives

## Core Rules
- Reuse existing code over writing new utilities. Do not duplicate logic.
- Maintain repo hygiene: do not invent new ad-hoc structures. Use standard directories.
- Before related changes, check `CHANGELOG.md` first to understand what was done recently (internal dev log, gitignored).
- Keep the repo root clean.
- Use `.dev/` for AI working artifacts (scripts, outputs, logs, scratch).
- Use `.dev/` for backups/moved artifacts and compatibility temp files.
- This is a Python project. Avoid Node/npm within Python modules.
- Run checks without asking. Fix and validate in one loop.
- Update the handbook (`docs/user/HANDBOOK.md`) when introducing or changing CLI command usage.
- Ask for explicit approval for any risky or destructive operations.

## Single Source of Truth
- **No hardcoded literals for configurable values.** Timeouts, thresholds, poll intervals, limits, feature flags, model names, default paths, and any value a user or operator might want to change must live in one canonical location:
  - Runtime tunables → `config/defaults.yaml` or `navig/config.py` (read via `get_config_manager()`).
  - Module-level constants → a single `_CONSTANT = ...` at the top of the owning module, never repeated inline.
  - CLI defaults → `typer.Option(default=...)` referencing the constant, not a bare literal.
- When you see the same literal appear in two or more places, extract it to the canonical location and reference it everywhere.
- Do not add a new config key that duplicates an existing one under a different name.

## Deduplication Checklist (run before adding any new code)
Before writing a new function, helper, constant, data structure, or config key:
1. **Search first.** Use `grep` / semantic search across `navig/` and `tests/` for similar names and behaviour.
2. **Check analogous modules.** If you need a string-formatter, look in `navig/console_helper.py`. If you need a path helper, look in `navig/platform/paths.py`. If you need an SSH utility, look in `navig/ssh_keys.py`.
3. **Extend, don't clone.** If something 80 % fits, extend or parameterise it rather than writing a parallel copy.
4. **One registry per concept.** Command registries (`_SLASH_REGISTRY`, `_EXTERNAL_CMD_MAP`), tool registries, and classifier maps each have a single authoritative dict/list. Do not shadow them with a second dict elsewhere.
5. After a change, re-read the surrounding file to check whether your addition made an existing helper redundant; remove the redundant one if safe.
