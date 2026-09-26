- **Two daemons booting at once killed each other — and the orphan-shaped one survived.**
  Measured 2026-09-21 20:09:41–20:10:01: `service restart` ran `schtasks /run`, the task
  launched daemon A (parent: the scheduler service); A's boot sweep — a WMI enumeration,
  10–20 s on this machine — had not yet written a pid file when the CLI's 10-second wait
  ran out, so the CLI declared the task launch dead and spawned daemon B directly; B's own
  sweep then killed A as a "stale generation" (task `LastTaskResult 1`), and B — parent gone,
  the shape the process sweep kills — was what survived. Every restart on a slow machine
  would end that way. Now: a boot sweep **never kills a daemon younger than itself** (a
  concurrent boot yields on its own); a daemon that finds an **older sibling still booting
  yields to it** instead of sweeping it (the older boot is the one with the living parent);
  and the CLI **waits 45 s** for a boot that sweeps, and does not spawn a competitor while a
  supervisor of ours is visibly booting. 12 tests; four load-bearing lines mutation-tested.
