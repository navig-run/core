- **A closed throwaway CDP browser now deletes its own session profile dir.** `cdp new` with
  no profile made a unique dir under `cdp-profiles/sessions/` that nothing ever removed;
  ~130 MB lingered per closed browser (517 MB in one harness run, and the store had crept
  734 MB → 1.3 GB). `stop` now deletes it — only under `sessions/`, never a named profile or
  a path outside the profile root — after waiting for the browser's processes to release it.
- **`navig repo land` no longer strands a proven local branch when the remote delete fails.**
  land deletes the remote ref before the local branch, so the happy path was fine; but a
  push-permission failure left `origin/<branch>` stale and raw `git branch -d` then refused
  the local branch too. It now shares `sweep`'s `delete_proven_branch`, which falls back to
  `-D` after re-verifying ancestry.
