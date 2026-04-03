## Closes

Closes #<!-- issue number here — exactly one issue per PR -->

## Problem / Root Cause

<!-- What was wrong and why. Not what was changed — that goes in What Changed. -->

## What Changed

- <!-- file or module -->: <!-- what was done -->

## Analysis Reference

<!-- Link to the analysis comment posted on the issue before this PR was opened -->
Issue analysis: <!-- #N (comment) or link -->

## Validation

```
ruff check navig tests
ruff format --check navig tests
python -m pytest tests/<relevant_file>.py -q
python -m build
```

Test results: <!-- N passed -->

## Checklist

- [ ] PR addresses exactly one issue (`Closes #N` above is filled)
- [ ] Analysis comment was posted on the issue before this PR was opened
- [ ] Tests added or updated for the changed behaviour
- [ ] Lint and format checks pass (`ruff check` + `ruff format --check`)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `docs/user/HANDBOOK.md` updated (if CLI command or flag changed)
- [ ] No hardcoded credentials or paths
- [ ] Windows compatibility verified (no POSIX-only shell calls unguarded)
- [ ] No bare `except: pass` without an explanatory comment
- [ ] Breaking changes called out explicitly below

## Risk and Rollback

- Risk level: <!-- low / medium / high -->
- Rollback plan: <!-- how to revert if this breaks production -->
