- **The core agent loop gained native repository, planning and question tools.**
  `file_search` / `grep_search` (git-aware, untracked files included, ReDoS-bounded) and a
  hash-guarded `edit_file` for coding sessions; session-scoped plan tools with a write gate and
  human-only `plan_approve` (`navig do --plan`); `ask_user` over the existing request registry;
  `subagent_list` / `subagent_inspect` / `subagent_interrupt` with `coordinator_run background=true`;
  read-only `plugin_inspect` / `module_list`; and workspace `AGENTS.md` / `CLAUDE.md` now ride the
  full system prompt after the guardrails. None of it touches the default `core` toolset schema.
  Reference: `docs/reference/agent-tool-surface.md`.
