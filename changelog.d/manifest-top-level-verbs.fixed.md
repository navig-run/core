- **Every runnable top-level verb now lands in the command manifest.** The schema walk
  yielded a group's subcommands but never the group's own callback, so `navig doctor` —
  runnable for years — had no `## navig doctor` entry: `navig help --schema` and the
  website's /commands page had no page for the most-used verb. Runnable groups
  (`navig cost`, `navig ai`, …) now emit their bare entry, and a guard test pins both
  halves (the walk, and the shipped `commands.md`); regenerated artifacts carry 79 new
  top-level entries. Surfaced alongside it: `navig import --json` printed its document
  with a bare `print`, invisible to the JSON-tripwire guard — it now emits through
  `console_helper.emit_json`, the approved emitter.
