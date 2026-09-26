- **A space commits its plans — one gitignore definition, mirrored on the flagship.** The
  scaffold wrote a blanket `.navig/` ignore, so `plans/`, `wiki/` and `space.json` — the parts
  of a workshop that exist to be shared — never reached git. There is now one definition
  (`navig.spaces.gitignore`): `.navig/` is committable except the private set (inbox, state,
  vault, credentials, memory, refs, data, logs, keys), the root links `/plans` and `/.inbox`
  are ignored, and the rules live in a managed block that `init`, `wire` and `space doctor
  --fix` reconcile with the same code. The old scaffold head is retired verbatim; an
  operator's own blanket `.navig/` rule outside the block is reported by doctor with a menu
  entry to retire it — shown first, asked, default No — never removed silently. The flagship
  repo's own `.gitignore` is guarded to match. (#1426, #1456, #1482)
- **`space doctor` says whether the plans are committable, and `space init`'s headline is
  honest.** A missing path is diagnosed as "No such directory" with the path as resolved from
  your shell, and a second `navig wire` over a wired space changes nothing. (#1448)
- **One id, one space.** `registry.register` keys on path, so a new folder whose name matched
  an existing space's id appended a SECOND entry with the same id, and every id lookup after it
  answered whichever came first: `cd ~/projects/homelab && navig space init` silently made
  `navig space use homelab` ambiguous. `space init` refuses before anything is written and names
  the other path (and the two ways out); `--dry-run` reports it; `navig wire` still repairs the
  folder but reports "NOT registered" instead of creating the duplicate; `space doctor --fix`
  never creates one and doctor's Registry row names who holds the id. Found alongside: that row
  fell back to a green ✓ over a check that had not run — it is a ⚠ "could not verify" now.
  (#1514)
- **`navig repo` anchors on the MAIN tree from inside a linked worktree.** `repo new` from a
  worktree nested the new worktree inside it (`--show-toplevel` is the worktree); the root is
  `--git-common-dir` now, so every `navig repo` command means the same repo from any worktree.
  The session briefing also says whether a present `.dev/agent.lock` is LIVE or EXPIRED.
  (#1427, #1443)
