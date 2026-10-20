- **A terminal `navig config set` never reached the running daemon.** `ConfigManager.get`
  serves a process-lifetime snapshot, so switches like `memory.enabled` only changed after a
  restart. New `ConfigManager.get_live()` re-reads `config.yaml` when its mtime changed (one
  `stat` otherwise); the memory switch, style dials and source switches read through it.
