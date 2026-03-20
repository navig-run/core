# Bootstrap Report — navig-run/core

_Generated: 2026-03-20 | Branch: chore/github-bootstrap_

---

## Stack

- Python 3.10 / 3.11 / 3.12 / 3.13 (3.13 in classifiers, absent from CI matrix — see Weaknesses)
- Go (in `host/` subdirectory — separate binary, not covered by CI)
- TypeScript / Node.js (in `packages/` — not covered by CI)

## Package Manager

- Python: `pip` / `setuptools` (no lockfile — see Weaknesses)
- Node: `pnpm` (workspace at repo root `pnpm-workspace.yaml`)

## Build Command

```bash
python -m build
```

Confirmed: `pyproject.toml` `[build-system]` → `setuptools>=68` + `build`.

## Test Command

```bash
pytest
```

Config: `pytest.ini` — `testpaths = tests`, `asyncio_mode = auto`, coverage threshold `--cov-fail-under=65`.
Coverage targets: `navig.gateway.server`, `navig.daemon.telegram_worker`, `navig.daemon.service_manager`, `navig.contracts`.

## Lint / Format Command

```bash
ruff check navig tests
ruff format --check navig tests
```

Confirmed: `ruff>=0.6.0` in `[project.optional-dependencies.dev]`.

## Release Artifacts

- PyPI wheel + sdist via OIDC trusted publishing (`publish.yml`)
- Release provenance attestation via `actions/attest-build-provenance` (`release-provenance.yml`)
- Triggered on `v*` tags

## Existing GitHub Automation Files

| File | Status |
|------|--------|
| `.github/workflows/ci.yml` | Active — secret-scan + lint + format + test (3-version matrix) + build |
| `.github/workflows/codeql.yml` | Active — Python CodeQL, weekly + push/PR to main |
| `.github/workflows/publish.yml` | Active — PyPI OIDC publish on `v*` tags |
| `.github/workflows/release-provenance.yml` | Active — provenance attestation on release |

## Existing Community Health Files

| File | Status |
|------|--------|
| `CONTRIBUTING.md` | Present — complete |
| `SECURITY.md` | Present — complete (private advisory + SLA + scope) |
| `CODE_OF_CONDUCT.md` | Present |
| `CHANGELOG.md` | Present |
| `.github/CODEOWNERS` | Present — all paths assigned to `@navig-run` |
| `.github/FUNDING.yml` | Present — **was broken** (REPLACE_ME), fixed in this branch |
| `.github/pull_request_template.md` | Present — clean, under 30 lines |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | Present — clean |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | Present — **was contaminated** with unrelated Prompt Optimization Checklist, fixed in this branch |
| `.github/ISSUE_TEMPLATE/config.yml` | Present — **had broken URL** (`navig-core/SECURITY.md`), fixed in this branch |
| `.github/ISSUE_TEMPLATE/security_report.yml` | Present — clean |
| `.github/ISSUE_TEMPLATE/docs_improvement.yml` | Present — clean |
| `SUPPORT.md` | **Created in this branch** |

## Missing Files (open-source trust gaps)

| File | Gap |
|------|-----|
| `SUPPORT.md` | No single place directing users to correct support channel (created) |
| `scripts/github-bootstrap.sh` | Branch protection not automated (created) |
| `GITHUB_MANUAL_OR_CLI_STEPS.md` | No record of required GitHub UI steps (created) |
| Org `.github/profile/README.md` | No org-level profile README visible on `github.com/navig-run` (created at org path) |

## CI Recommendation Rationale

All CI commands (`ruff check`, `ruff format --check`, `pytest`, `python -m build`) are confirmed from inspection of `pyproject.toml` and `pytest.ini`. No invented commands.

Job names in the matrix produce: `test (3.10)`, `test (3.11)`, `test (3.12)`. Branch protection status check must reference exact job names — `test` alone does not match matrix-expanded names.

Secret scan via `gitleaks-action` runs first and blocks the test job if triggered, which is correct ordering.

## CI Weaknesses / Gaps

| Gap | Severity | Status |
|-----|----------|--------|
| Python 3.13 absent from CI matrix | Low | **Fixed** — added to `ci.yml` matrix |
| No pip dependency caching | Low | Acceptable trade-off per minimal CI constraint |
| Go code in `host/` has no CI coverage | Medium | **Fixed** — `test-go` job added to `ci.yml` (`go vet`, `go build`, `go test`) |
| `packages/` are Python/YAML tool packs — no TypeScript in this repo | N/A | **False alarm** — TS packages (navig-deck etc.) are sibling repos, not in navig-core |
| Coverage threshold 65% | Medium | **Fixed** — raised to 75% in `pytest.ini` |
| No pip lockfile | Medium | **Fixed** — `requirements.lock` generated via `pip-compile pyproject.toml` |
| `FUNDING.yml` had `REPLACE_ME` placeholder | Fixed | Visible in Sponsors button on GitHub; fixed in this branch |
| `feature_request.yml` had unrelated content | Fixed | Prompt Optimization Checklist exposed to contributors; fixed in this branch |
| `config.yml` had wrong `SECURITY.md` URL | Fixed | 404 on click; fixed in this branch |
| `enforce_admins: true` blocked maintainer merges | Low | **Fixed** — set to `false` in `github-bootstrap.sh` |
