- **CPU alerts measured a window they did not own — the monitor now keeps its own.**
  `psutil.cpu_percent(interval=None)` reports *since the last call on that pool thread*, and the deck
  dashboard (8 s TTL), the briefing route and the agent eyes all call that sampler through
  `asyncio.to_thread` on the daemon's shared pool — so the host-check monitor's `sustain=3` ("15 min
  sustained") could be arming on a seconds-wide window or psutil's stale module fallback. Local CPU
  samples are now replaced with busy % computed from the monitor's own `cpu_times()` snapshots
  (`_apply_own_cpu_window`) — pure reads no other caller can perturb, one baseline closed per poll.
  The first poll keeps the collected reading (one unowned sample can never arm an alert alone) and
  remote (ssh) samples keep their transport's own semantics.
- **CPU edges no longer fire while a heavy lane holds the box.**
  The slot protocol claims 24+ of 32 cores *by design*, so a local CPU edge during a gate run
  reported the machine doing exactly what it was told to do. While a lane is held, the local CPU
  edge now parks in the monitor's pending machinery — the same path maintenance windows use: a
  spike that resolved during CI announces nothing, one still hot after the queue drains announces
  then. Disk, memory and services keep alerting as before, and a remote host never consults local
  lanes.
