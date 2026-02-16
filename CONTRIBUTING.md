# Contributing to NAVIG Core

Thanks for contributing.

## Prerequisites

- Python 3.10+
- Git
- Basic familiarity with CLI tooling and pytest

## Local Setup

```bash
git clone https://github.com/navig-run/core.git
cd navig/navig-core
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

## Development Workflow

1. Create a branch from `main`.
2. Implement focused changes with tests.
3. Run lint + tests locally.
4. Update docs/changelog when behavior changes.
5. Open a PR using the provided template.

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
