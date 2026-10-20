- **The Windows and macOS/Linux join scripts show live progress instead of going silent** —
  every wait now renders one spinning line with elapsed seconds (installer, service start,
  gateway health) and ends on a ✓/✗ result line; step 3 polls the gateway for up to 60 s,
  performs one clean stop/start recovery before giving up, and prints where the output log is.
  The Windows script runs the official installer with `-NoConfirm` (the interactive repair
  menu no longer blocks a join) and quietly to a log, maps a hub 502 to a human sentence with
  one retry instead of printing the raw .NET exception, and skips the announce when the local
  gateway is not up instead of firing a doomed ping.
