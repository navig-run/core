- **Session goals** — a goal one NAVIG Desktop session works until done: `navig goal session
  set <session> "<objective>"` arms the session, which then injects a `<goal_round>`
  prompt each time it goes idle (Round N/max, default 12) until the agent marks the goal
  complete, reports a blocker, or spends its rounds. Add `show|list|pause|resume|clear`,
  a `goal:` config block (`enabled`, `max_rounds`, `blocked_after_rounds`, `tool_policy`),
  and `get_goal` / `set_session_goal` tools on the session. Distinct from standing goals
  (`navig goal add`): no schedule, no missions — the loop lives in the session itself.
