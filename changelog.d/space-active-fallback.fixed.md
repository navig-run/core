- **The default Space is the *active* Space, not a literal `"default"`.**
  `get_default_space()` — the fallback behind `PlanContext` and the agent's plan
  tools — returned only the `NAVIG_SPACE` override or the hardcoded `default`, so
  a session with `navig space switch` away from default still snapshotted
  `default`'s plans and phase. It now delegates to the single canonical reader
  (`resolve_active_space`: env → cache file → config keys), so context follows
  what the operator is actually working in.
