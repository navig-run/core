---
applyTo: '**'
---

# Git Workflow — NAVIG Production-Grade Rules

**One model: every change is a short branch off `main`, merged back to `main` through a
PR, squashed, and deleted.** There is no `develop`, no `release/*`, no `hotfix/*` — earlier
versions of this file described a git-flow this repo never ran (measured: `main` receives
~10 squash-merged PRs a day, straight from `feat|fix|chore|docs|refactor/*` branches).

## AI Behaviour Rules (Git)
1. Every unit of work → its own `<type>/<slug>` branch off the latest `origin/main`
   (`feat` · `fix` · `chore` · `docs` · `refactor`; 2–4 word kebab slug).
2. **Never commit directly to `main`.** Never `git push --force` to `main`.
3. Commit **path-scoped** — `git add -- <paths>`; never `git add -A` / `git add .`. A shared
   checkout holds other sessions' work.
4. One unit of work per PR. Squash-merge, delete the branch (remote and local).
5. On conflicts: rebase onto `origin/main` on YOUR branch, resolve there, re-validate, push
   `--force-with-lease` (your branch only).
6. Changelog entries are **fragments** (`changelog.d/<slug>.<kind>.md`), never edits to
   `CHANGELOG.md [Unreleased]` — the pre-push gate refuses the latter.
7. **One issue per PR** — never group unrelated issues into a single branch or PR. Each
   fix/feature gets its own branch, its own PR, and references exactly one `Closes #N`.
   See `github-practices.instructions.md`.
8. Releases are cut from `main` by the owner (below). Do not tag or publish autonomously.

---

## Branch Model

| Branch | Purpose | Created from | Merges into |
|--------|---------|-------------|-------------|
| `main` | The only long-lived branch. Always green; releases are cut from it | — | — |
| `<type>/<slug>` | One unit of work | latest `origin/main` | `main` (squash merge via PR), then deleted |

Parallel sessions never share a checkout on different branches: a second session works in a
worktree under `.dev/worktrees/<slug>` (`navig repo new <slug>`), never in a sibling folder.
The repo guard (Claude Code hooks) enforces the one-session-per-checkout rule.

### Naming conventions
- `feat/cli-mesh-discover` · `fix/vault-token-refresh` · `chore/changelog-guard`
- `docs/git-instructions` · `refactor/doctor-sections`

---

## Commit Format — Conventional Commits

```
<type>(<scope>): <subject>

[optional body — what and why, not how, 72-char wrap]

[optional footer — BREAKING CHANGE: … | Closes #N]
```

**Types:** `feat` · `fix` · `docs` · `style` · `refactor` · `perf` · `test` · `chore` · `release`

**Scope examples:** `cli` · `agent` · `gateway` · `mesh` · `storage` · `telegram` · `auth`

The subject names the **behavioural change** ("a timeout was wrapped as an EMPTY error"),
not the file. The body carries the measurement that justified it.

**Commit template setup (run once after clone):**
```bash
git config commit.template .gitmessage
```

---

## Merging Rules

| Scenario | Strategy |
|----------|----------|
| `<type>/<slug>` → `main` | PR + **squash merge** + delete branch (`gh pr merge --squash --delete-branch`) |

Before merging: rebase onto `origin/main`, the local gate is green (`git push` runs it —
lint · typecheck · source guards · a relevance-ranked test selection), and the diff is only
your task. GitHub Actions is billing-blocked for this org, so **the local pre-push gate is
the CI**; a red remote check whose jobs never started is an environment fact, not a
failure.

---

## Release Workflow

Releases are cut from `main`, by the owner, with two commands. The tag workflow
(`.github/workflows/release.yml`) is correct but does not run here, so **`release.sh` is
the path that publishes**.

```bash
cd core
python tools/version_bump.py bump minor --commit --tag --push
#   bumps pyproject.toml, folds changelog.d/ fragments, rotates [Unreleased] under
#   ## [X.Y.Z] — date, syncs latest.json — ONE commit — then pushes main and the tag
bash tools/release.sh X.Y.Z --publish
#   guards (main · clean · tag absent locally + origin · pyproject == X.Y.Z) → build →
#   twine check → the wheel IS navig-X.Y.Z → real-install smoke → twine upload →
#   GitHub Release with dist/* attached (body from changelog_assemble.py --release-notes)
#   → latest.json re-synced against the asset that now exists
```

Preview without writing: `npm run release:dry` (from repo root) or
`python tools/version_bump.py bump minor --dry-run`. An empty `[Unreleased]` with no
fragments refuses the bump; `--no-changelog` is the deliberate hatch. Full checklist:
`docs/dev/release.md`. `navig doctor` → **Release** shows whether the tree, the newest tag
and PyPI agree.

---

## Branch Protection (GitHub — manual one-time setup)

Configure in **Settings → Branches** for `main`:

- Require pull request before merging
- Disallow force-push
- Auto-delete head branches after merge

Do **not** require remote status checks: Actions is billing-blocked, so a required check
that can never run would block every merge.

---

## CHANGELOG Maintenance

- File: `CHANGELOG.md` — **tracked**, the release/public history source of truth
- Follows Keep a Changelog format
- During development: never edit `[Unreleased]` by hand — drop a fragment in
  `changelog.d/<slug>.<kind>.md` (see `changelog.d/README.md`); concurrent branches then never
  collide on the changelog. **Enforced** by the pre-push gate
  (`scripts/check-changelog-fragments.mjs`)
- On release: `version_bump.py` (and `tools/release.sh`) fold the fragments and rotate
  `[Unreleased]` under `## [X.Y.Z] — YYYY-MM-DD` in the release commit — `python
  tools/changelog_assemble.py --release X.Y.Z` is the step itself; an empty release is refused
- The GitHub Release body has ONE writer, `python tools/changelog_assemble.py --release-notes X.Y.Z`
  (install block + the `[X.Y.Z]` block, or its headline digest when the block is over GitHub's
  125,000-character limit); `tools/release.sh` and `release.yml` both use it
- Auto-generate draft entries: `git log v{prev}..HEAD --pretty="- %s (%h)"`

---

## Tag Rules

- All release tags are **annotated**: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
- Lightweight tags are forbidden for releases
- Tag the release commit on `main` — the one `version_bump.py` made
- Tags are created by `version_bump.py --tag` / `tools/release.sh` — never by hand unless
  fixing a broken tag
- Verify order: `git tag -l --sort=-v:refname` (version order — `v3.10.0` sorts above
  `v3.9.0`; `creatordate` order does not survive a backfilled tag)

---

## Quality Gates

The **local** gate is the CI (`scripts/ci-local.mjs`; `npm run ci` before a commit, and
`git push` runs the fast profile through the pre-push hook):

- Ruff lint (core + every plugin)
- Deck typecheck
- The whole-tree source guards (module-attr · call-arg · override · exit honesty · …)
- A relevance-ranked pytest selection for the changed modules (capped and labelled — not
  full coverage; run the affected `tests/<dir>` yourself and say which)
- `npm run ci:full` adds the Node/TS workspaces, the OS suites and the real-install smoke
- `npm run ci:install` is the **release gate**: build the wheel, install it clean, drive it

`.github/workflows/ci.yml` mirrors the same steps for when Actions can run.

---

## Daily Developer Flow

```bash
# 1. Start work — from the latest main, on your own branch
git checkout main && git pull --ff-only origin main
git checkout -b feat/my-feature            # or: navig repo new my-feature (a worktree)

# 2. Commit path-scoped, with the template
git add -- core/navig/thing.py core/tests/thing/test_thing.py core/changelog.d/my-feature.added.md
git commit                                  # opens .gitmessage

# 3. Keep up to date
git fetch origin && git rebase origin/main

# 4. Push (runs the gate), open the PR, squash-merge, delete the branch
git push -u origin feat/my-feature
gh pr create --fill --base main
gh pr merge --squash --delete-branch
git checkout main && git pull --ff-only && git branch -d feat/my-feature
```

---

## Hard Rules

1. `main` receives squash merges of PRs — never direct commits, never force-pushes
2. Never `git add -A` / `git add .` — path-scoped commits only
3. Never tag or publish without `version_bump.py` / `tools/release.sh` (they guard the wrong
   branch, a dirty tree, a version the tree does not build, an existing tag)
4. Every release tag must be annotated — never lightweight
5. Delete a branch the moment its work is on `main` — the repo carries `main` plus the
   branches of currently-active work, nothing else
