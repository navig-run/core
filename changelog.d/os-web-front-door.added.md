- **NAVIG OS in a browser, in one command — `navig os serve`.** The desktop build is not
  released yet, but the same server already served the whole interface to a browser; reaching
  it took four environment variables and knowing that `NAVIG_SERVER_TOKEN` signs the login
  session, so in practice nobody did. `navig os serve` builds it, keeps the token and login
  password in the **vault** (so a bookmark keeps working across restarts, and a vault that
  cannot be written is reported rather than silently producing different credentials next
  time), prints the local URL and the one-line `cloudflared` command that reaches it from a
  phone. `--print-env` shows exactly what would run with both secrets redacted and starts
  nothing; `navig os status [--json]` says whether it is up, where the source is, and whether
  credentials are stored. A directory named with `--dir` or `$NAVIG_OS_DIR` is authoritative —
  if it is not an `apps/os` checkout the command stops instead of quietly running a different
  tree — and discovery walks up from where the operator actually stood, not `Path.cwd()`,
  which is the active space.
- **`os.navig.run` — a front door, deliberately not the app.** A landing page, a launcher that
  remembers where your own instances live, and a screenshot tour built from the render
  harness's pixel baselines, so it cannot drift into advertising a UI that no longer exists.
  Hosting the client there and having it dial your PC would be the worse architecture: a
  public page reaching `127.0.0.1` is what Local/Private Network Access is closing down, it is
  unnecessary (your own server already serves the complete UI at any origin it is reachable
  at, tunnel included — same origin, cookie login, `wss://`, no CORS), and it would split
  client and server versions. The site's CSP sets `connect-src 'none'`, so "this page never
  sees your data" is enforced rather than promised, and a test asserts the policy.
