---
applyTo: '**'
---

# Git Workflow — NAVIG Production-Grade Rules

## AI Behaviour Rules (Git)
1. New feature / subsystem upgrade → `feature/<slug>` off `develop`
2. Emergency production fix → `hotfix/<slug>` off `main`
3. Release stabilisation → `release/<version>` off `develop`
4. Small docs / chore fixes → directly on `develop` (not `main`)
5. Propose 1–3 Conventional Commit messages based on staged diff
6. Squash merge `feature/*` into `develop` via PR
7. On conflicts: explain both sides + risks, then provide a patch
8. **Never push to `main` or `develop` without user confirmation.**
9. **One issue per PR** — never group unrelated issues into a single branch or PR.
   Each fix/feature gets its own `feature/<slug>` branch, its own PR, and references
   exactly one `Closes #N` in the PR body. Batch PRs (e.g. `fix: resolve N open issues
   (#A, #B, #C…)`) are forbidden. See `github-practices.instructions.md` for full policy.

---

## Branch Model

| Branch | Purpose | Created from | Merges into |
|--------|---------|-------------|-------------|
| `main` | Production-ready, tagged releases only | — | — |
| `develop` | Integration. All features land here first | `main` | `main` (via `release/*`) |
| `feature/<slug>` | One feature / fix per branch | `develop` | `develop` (squash merge via PR) |
| `release/<version>` | Short-lived stabilisation + CHANGELOG prep | `develop` | `main` + `develop` |
| `hotfix/<slug>` | Emergency patches for production | `main` | `main` + `develop` |

### Naming conventions
- `feature/cli-mesh-discover`
- `feature/fix-vault-token-refresh`
- `release/2.5.0`
- `hotfix/telegram-crash-on-empty-update`

---

## Commit Format — Conventional Commits

```
<type>(<scope>): <subject>

[optional body — what and why, not how, 72-char wrap]

[optional footer — BREAKING CHANGE: … | Closes #N]
```

**Types:** `feat` · `fix` · `docs` · `style` · `refactor` · `perf` · `test` · `chore` · `release`

**Scope examples:** `cli` · `agent` · `gateway` · `mesh` · `storage` · `telegram` · `auth`

**Commit template setup (run once after clone):**
```bash
git config commit.template .gitmessage
```

---

## Merging Rules

| Scenario | Strategy |
|----------|----------|
| `feature/*` → `develop` | PR + **squash merge** |
| `release/*` → `main` | PR + **merge commit** |
| `release/*` → `develop` (back-merge) | PR + merge commit |
| `hotfix/*` → `main` | PR + merge commit |
| `hotfix/*` → `develop` (back-merge) | PR + merge commit |

---

## Release Workflow

```bash
# 1. Finish all features for the release on develop
git checkout develop && git pull

# 2. Open a release branch for stabilisation
git checkout -b release/2.5.0

# 3. Bump version, update CHANGELOG.md (move [Unreleased] to [X.Y.Z])
# 4. PR release/2.5.0 to main
# 5. After merge, tag via the release script (from main):
bash scripts/release.sh 2.5.0

# 6. Back-merge main to develop
git checkout develop && git merge main
```

### Quick tag for hotfix
```bash
# From main, after hotfix merge:
bash scripts/release.sh 2.5.1
```

---

## Branch Protection (GitHub — manual one-time setup)

Configure in **Settings → Branches** for both `main` and `develop`:

- Require pull request before merging
- Require at least 1 approving review
- Require status checks to pass (CI: test, lint)
- Require branches to be up to date before merging
- Disallow force-push
- Auto-delete head branches after merge

---

## CHANGELOG Maintenance

- File: `CHANGELOG.md` — internal dev log, **gitignored, never committed**
- Follows Keep a Changelog format (same structure as before)
- During development: add entries under `## [Unreleased]`
- On release: rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD`, add new empty `[Unreleased]` block
- `scripts/release.sh` reads the `[X.Y.Z]` block and passes it to `gh release create` as the GitHub Release body
- Auto-generate draft entries: `git log v{prev}..HEAD --pretty="- %s (%h)"`
- The public record is the GitHub Releases page — not a committed file

---

## Tag Rules

- All release tags are **annotated**: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
- Lightweight tags are forbidden for releases
- Tag at the release merge commit on `main`
- Tags are created by `scripts/release.sh` — never manually unless fixing a broken tag
- Verify order: `git tag -l --sort=-creatordate --format="%(creatordate:short) %(refname:short)"`

---

## Quality Gates (CI — `.github/workflows/ci.yml`)

- Secret scan (Gitleaks)
- Python test matrix (3.10–3.13)
- Ruff lint + format
- PyPI build check

---

## Daily Developer Flow

```bash
# 1. Start work
git checkout develop && git pull
git checkout -b feature/my-feature

# 2. Commit with template
git add -p
git commit          # opens .gitmessage template

# 3. Keep up to date
git fetch && git rebase origin/develop

# 4. Finish: open PR into develop, review, squash merge, delete branch

# 5. Release (when develop is stable)
git checkout develop && git pull
git checkout -b release/2.5.0
# bump version, update CHANGELOG.md, PR to main
bash scripts/release.sh 2.5.0
```

---

## Hard Rules

1. `main` only receives merges from `release/*` and `hotfix/*` — never direct commits
2. Never `git push --force` to `main` or `develop`
3. Never tag without `scripts/release.sh` (guards against wrong branch, dirty tree, out-of-order tags)
4. Every release tag must be annotated — never lightweight
5. `develop` must always be deployable (tests green, no broken imports)
