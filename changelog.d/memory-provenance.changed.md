- **Session↔commit matching is prefix-symmetric, and agent worktrees belong to their
  project.** `navig recall sessions --commit` now matches the full id the operator typed
  in both directions (7-char id ↔ 12-char stored key and vice versa), instead of silently
  truncating everything to 7 characters. `.navig_worktrees/<slug>/…` paths (the agent
  isolation worktrees) now fold to their owning project for both project resolution and
  session file keys — they previously resolved to no project at all — which bumps
  `LINKS_VERSION` to 3 so stored links re-read once.
