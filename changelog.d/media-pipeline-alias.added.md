- **`navig pipeline` is now the hidden alias of `navig content`.** The canonical verb for the
  content-production plugin is `navig content` (`navig-content` standalone). `navig pipeline`
  keeps working — same app, verbs, flags, exit codes and `--json` stdout — but leaves
  `--help`, the published command manifest, completions and the plugin card, and prints its
  deprecation on stderr. It is hidden only while `navig content` is registered, so an install
  whose navig-pipeline predates that entry point still shows its only verb. Recorded in
  `deprecations.json`; documented in HANDBOOK §23.5.6.
