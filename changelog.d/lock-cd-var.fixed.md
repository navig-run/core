- **The agent lock judged `cd "$ROOT" && git checkout …` by the directory the shell LEFT.** A
  `cd` to a shell variable (`cd "$ROOT"`, PowerShell's `cd $env:ROOT`) or `cd -` leaves the
  shell somewhere the hook cannot know, yet the command was classified by its starting
  directory — so from inside a worktree it was exempt, and could move the main checkout's HEAD
  under another session's live lock (the same outcome as the MSYS `cd` fixed just before). An
  unresolvable location now follows the rule a variable `git -C` target already used: exempt
  only when the command itself names an absolute `.dev/worktrees/` path, else the lock
  applies. A later absolute `cd` makes the location known again, a relative `-C` after an
  unknown `cd` is treated as unknown, and `cd ~` is expanded rather than treated as unknown.
  The same rule now covers `WT=<worktree>; cd "$WT" && …`, which used to be locked while the
  identical `git -C "$WT"` shape was exempt.
