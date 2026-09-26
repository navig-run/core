- **Under a UTF-8 console (`chcp 65001`), a native tool's output was decoded into U+FFFD.**
  `decode_console_output` tried UTF-8, then the console page — and when the console page IS
  UTF-8 that is the same wrong attempt twice. Measured with `icacls`: it writes the OEM page
  (cp866) whatever the console says, so every localized ACL name became replacement characters
  for anyone who set a UTF-8 terminal. Non-UTF-8 bytes under a UTF-8 console now fall back to
  the OEM page (`oem_encoding()`). Found because `install.ps1 -DryRun` sets
  `[Console]::OutputEncoding = UTF8` on the one console every test worker shares — the install
  tests now put the console's code page back, the way `_no_env_leaks` guards `os.environ`.
