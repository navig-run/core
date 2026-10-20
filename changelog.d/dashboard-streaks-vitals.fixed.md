- **The dashboard's habit streaks are counted, and its live strip stops collecting
  the System screen's half.** `/apps/tasks` answered `"streak": 0` for every habit —
  the cron job only keeps `last_run` — so a streak kept by tapping the deck read as
  broken every day; it is now counted from the space's `habits.csv`, with today
  counted when the tick stamped `last_run`, and `null` when there is no history
  ("not measured" is not "you did zero"). A failed read is a 500, not a 400: the
  request was fine. And `/monitor?scope=vitals` answers cpu/mem/disk/uptime only on
  its own cache key — the dashboard polls it every few seconds and never showed the
  services or listening ports that `get_all_monitoring` enumerates.
