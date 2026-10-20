- **`/trace` answers "why was that slow?" — the last 20 turns as latency stage bars.**
  A new **Latency** section splits each turn into preparation, time-to-first-token,
  generation and tools, with approval waits called out, so a turn that waited on the model
  looks different from one that waited on navigation before it. Turns are recorded by
  `run_agentic` into a bounded `perf/turn_latency.jsonl` ledger; the write is best-effort
  (telemetry can never take a turn down) and the block is sized to the message budget, so
  it drops the oldest rows rather than making `/trace` exceed Telegram's 4096-character cap.
