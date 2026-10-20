- **Move a whole brain between machines: `navig backup export|import --brain`.** The
  config backup now carries the state a brain *runs on* — SQLite databases with their
  WAL/SHM sidecars, memory, sessions, the cron jobs, the agent workspace
  (`workspace/SOUL.md`, …), approvals, `.env` (which gates who may talk to the bot),
  license and `daemon/config.json` — so relocating the brain is one encrypted bundle
  instead of a hand-copied list of files. It implies
  `--include-secrets` (databases cannot be redacted) and **refuses to run without
  `--encrypt`**, and `--brain --format json` is refused outright. **Both directions
  refuse while the daemon is running**, so no database is copied or restored under a
  live writer, and every exit path deletes its staging copy. The vault is deliberately excluded — its key is
  bound to the machine it was made on — and both commands say so and point at
  `navig vault backup` / `navig vault restore`, which re-wrap that key for the target.
