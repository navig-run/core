- **`navig repo stale` flags a worktree whose branch was never committed to.** "Last commit
  5 days ago" is the branch tip date; for a branch cut from main and never touched, that is
  when it was created — recent-looking activity for a worktree nobody has used. The table's
  "last commit" cell now reads "never worked (Nd)" for those, from the same reflog signal
  `sweep` uses; an empty reflog stays unknown, never a false "never worked".
