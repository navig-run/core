- **A worktree left holding `main` blocked the main checkout, and nothing cleared it.**
  `gh pr merge --delete-branch` run inside a worktree switches that worktree to `main` when
  `main` is free (measured twice in one afternoon); from then on the main checkout cannot
  `checkout main` ("already used by worktree"). `navig repo sweep` read it as "only 0 behind —
  may be a fresh worktree" and kept it forever, and the session briefing listed it as a plain
  extra worktree. A clean worktree on the default branch is now removable by
  `navig repo sweep --yes` once its HEAD reflog has been idle for 60 minutes — the agent lock's
  live/gone line, because a session was measured resetting its `main` worktree minutes before a
  sweep would have removed it (`navig repo new` never cuts one, and commits on `main` are
  refused) — and `navig repo stale` and the briefing flag it as "holds `main`" either way. Only the worktree goes:
  the shared branch remover behind `sweep` and `land` now refuses the default branch whatever
  the proof — without that, removing the worktree would have been followed by deleting local
  `main`, which every proof trivially passes.
