- **A `.sh` trigger action ran the WSL launcher, not a shell.** `TriggerManager._run_script`
  spawned `["bash", path]`; on Windows `CreateProcess` searches `System32` before `PATH`, so that
  is `C:\Windows\System32\bash.exe` — the WSL launcher, whatever `shutil.which` reports — which
  cannot run a script at a Windows path (`/bin/bash: E:projectsapps…: No such file`). The shell
  is now `navig.platform.process.posix_shell()`: Git Bash derived from the git install on Windows
  (Program Files, scoop, portable — one hardcoded probe had missed scoop), `bash`/`sh` elsewhere,
  and "no POSIX shell found" is a named failure. The test fixture delegates to the same
  resolver, so tests and the trigger runner cannot disagree about which bash a box has.
