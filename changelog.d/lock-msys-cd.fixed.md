- **The agent lock let `cd /e/<repo> && git checkout -b …` through while another session held
  it.** A `cd` in git-bash form was never converted to a Windows path: `Path("/e/x")` has a root
  and no drive, so joining it to the cwd kept only the drive (`E:\e\x`), which reads as
  "outside the repo" and exempted the command. It was the live cause of a commit landing on a
  foreign branch (2026-09-26): another session ran exactly that from the repo's `core/`
  directory, moved HEAD under the lock holder, and the holder's next commit went onto it. The
  `cd` hop now goes through the same `_from_msys` conversion the `-C` target already used, and
  PowerShell's `Set-Location`/`chdir` count as directory changes too, so a PowerShell command
  is judged by where it actually runs in both directions.
