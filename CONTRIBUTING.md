# Contributing to NAVIG Core

Thanks for contributing.

## Prerequisites

- Python 3.10+
- Git
- Basic familiarity with CLI tooling and pytest

## Local Setup

```bash
git clone https://github.com/navig-run/core.git
cd core
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

## Development Workflow

1. Create a branch from `develop` (`feature/<slug>` for features, `hotfix/<slug>` for urgent prod fixes).
2. Implement focused changes with tests.
3. Run lint + tests locally.
4. Update docs/changelog when behavior changes.
5. Open a PR using the provided template.

## Repository Hygiene

- Keep `CHANGELOG.md` tracked and up to date (release/public history source of truth).
- Use `.dev/` for local AI working artifacts (scripts, logs, command outputs, temporary notes).
- Use `.local/` only for backups or files intentionally moved out of the main tree.
- Never place scratch files directly in repo root.

## Quality Gates

Run before opening a PR:

```bash
ruff check navig tests
ruff format --check navig tests
pytest
python -m build
```

Coverage threshold is enforced in CI (`--cov-fail-under=65`).

## Coding Guidelines

- Keep changes small and reviewable.
- Prefer explicit types and clear function boundaries.
- Do not commit secrets, credentials, or private infrastructure details.
- Keep public APIs stable; mark internal-only modules clearly.
- Never use `print()` in production code — use `loguru` logger.
- Never use raw `sqlite3` in command modules — use `storage/engine.py`.
- Never use bare `except:` — always catch specific exception types.
- Defer heavy imports inside function bodies to keep CLI startup under 50 ms.
- Verify no circular imports after structural changes: `python -c "import navig"`.

## Commits and Pull Requests

- Use descriptive commit messages.
- Link relevant issue(s).
- Include test evidence in PR description.
- Call out breaking changes explicitly.

## Security

Do not file public issues for vulnerabilities.
See `SECURITY.md` for responsible disclosure.

## License

By contributing, you agree your contributions are licensed under Apache-2.0.
