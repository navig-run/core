- **Two worktree removers could destroy uncommitted work.**
  `navig repo land --yes` removed the landed branch's worktree with `git worktree remove --force`
  and never looked at it, so work started on top of a merged branch (merge, then begin the next
  thing in the same folder) was deleted — reproduced: exit 0, an edited and an untracked file
  both gone. It now refuses before tearing anything down, names the worktree and the way forward
  (`navig repo remove <slug> --force` if it really is disposable), and removes through the same
  non-forced remover as `navig repo sweep`.
  The agent's own `worktree_remove` (`navig.agent.worktree.WorktreeManager.remove`) did worse on
  Windows: with `force=False`, when git refused — and its commonest refusal is "contains modified
  or untracked files" — it fell back to `shutil.rmtree`, force-deleted the branch, logged
  "Removed", and the tool reported success. Without `force` it now stops at git's dirty refusal,
  uses the rmtree fallback (kept for Windows file locks) only on a verifiably clean tree, raises so
  the tool reports failure and the worktree stays tracked, and deletes the branch with `-d` so
  unmerged commits survive.
