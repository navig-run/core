- **`git.instructions.md` and `CONTRIBUTING.md` describe the branch model this repo runs.** Both
  narrated a git-flow — `develop`, `release/*`, `hotfix/*`, back-merges, a `develop`-based daily
  flow — that never existed here: `main` receives ~10 squash-merged PRs a day straight from
  `feat|fix|chore|docs|refactor/*` branches, and there is no `develop` to branch from. The
  documents now state the one model (branch off `main`, PR, squash, delete; worktrees under
  `.dev/worktrees/` for parallel sessions), the path-scoped commit rule, fragments as the
  changelog entry (enforced), the two-command owner release with `release.sh` named as the path
  that publishes, `v:refname` tag ordering, and the local gate as the CI — with branch-protection
  advice that no longer asks for a remote status check that can never run.
