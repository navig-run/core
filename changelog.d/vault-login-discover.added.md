- **`navig vault login discover` — save the sign-ins you ALREADY have in your browser.**
  Lists the sites you look signed into in Chrome/Edge/Brave (read from a throwaway copy of
  the cookie database — only cookie NAMES and hosts, never values, never the live profile),
  each with a ready `navig vault login capture <site> --from-chrome` line; sites already in
  the vault are flagged and hidden unless `--all`. "Looks signed in" is a cookie-name
  heuristic, labelled as such — a hint about where to look, not a verified login.
  `capture --from-chrome` imports that sign-in without a new login (the real browser decrypts
  its own cookies for that one host); where App-Bound Encryption blocks the clone-decrypt it
  fails loudly and points at the interactive `capture`. Also: every `navig vault … --json`
  payload now goes through `ch.emit_json`, so a value longer than the terminal is wide no
  longer gets a newline wrapped into it (measured: `discover --json` piped at 80 columns
  produced unparseable JSON).
