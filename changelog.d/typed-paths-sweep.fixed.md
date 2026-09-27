- **A path you typed resolved inside the active space at 61 more sites.** `navig` chdir's
  into the active space before a command runs, so `Path(<typed arg>)` answered a different
  folder: `navig media probe clip.mp4`, `navig memory export -o out.json`, `navig tools
  schema -o s.json`, `navig inbox … --path`, `navig mount add`, `navig skills auto <dir>`,
  `navig explore photos …` (24 sites), `navig beat gen -o`, `navig github search
  --output-dir`, `navig text tokens save` and more read or wrote the wrong place — silently.
  All now resolve where you typed them. `navig cdp record -o` does too, and
  `navig cdp screenshot -o dir/x.png` no longer lands under `~/.navig/screenshots/dir/`
  (a bare `-o x.png` keeps that documented home). Plugins use a shim that falls back to
  `NAVIG_INVOCATION_CWD`, so they still work against core 3.25.0. A new guard
  (`tests/quality/test_typed_paths_are_resolved.py`, empty baseline, scans core + every
  plugin) keeps the shape out.
- **navig no longer drops `ai_system_prompt.txt` into every project's `.navig/`.** Running
  any command inside a folder with a `.navig/` wrote the default prompt there, leaving an
  untracked file in each repo. The default now lives only in the global config; a project
  copy is still honoured as an override when you create one on purpose.
