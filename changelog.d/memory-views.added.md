- **Answer "what happened here?" from Recall: `navig recall history · trace · changes ·
  unfinished · conflicts`.** `history <file|plan|sha>` merges the sessions, commits and
  records behind one target into one timeline; `trace <sha|session>` walks the other side
  of a commit or session edge; `changes -p <project> [--refresh]` lists recent commits
  (git-observed into the metadata-only `commits` cache) joined to the sessions/records
  they explain; `unfinished -p` lists plans/tasks nothing verified yet (display-only —
  it never starts anything); `conflicts [--refresh]` runs the deterministic contradiction
  producer (opposed user verdicts on one subject; claims verification disproved) and
  lists open findings. All `--json`, all project-scoped, all zero-inference — and the
  commit-prefix lookup now uses an indexed GLOB (measured 0.01 ms vs 10.8 ms scanned at
  50k links), pinned by a latency + plan-shape benchmark in `tests/recall`.
