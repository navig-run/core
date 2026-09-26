- **`navig repo sweep` kept squash-merged worktrees as "may be a fresh worktree".** A
  worktree whose tip GitHub records as a MERGED PR's head cannot be fresh (a fresh worktree
  sits at the base tip and was never any PR's head), yet it was held to the 50-commits-behind
  rule meant to tell fresh from finished — so with squash-merge as the house default, the
  common finished worktree waited for 50 more merges (measured: #1557's, merged at exactly
  its tip, clean, kept as "only 5 behind — may be a fresh worktree"). It now uses the same
  60-minute idle gate as a worktree holding `main`, shared as one helper; the head must equal
  the tip, so a new worktree reusing an old merged branch name is still treated as fresh.
