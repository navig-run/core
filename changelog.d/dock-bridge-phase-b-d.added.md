- **`dock_*` tools grow up: locators, waits, dialogs, uploads, screenshots, logs, leases.**
  A `dock_act` step targets a `[ref_N]` or a locator (`role`+`name`, `testid`, `css`,
  `has_text`, `xpath`, `nth`) — several matches fail as `ambiguous`, never a guess; new verbs
  `fill`, `wait_for` (DOM-event driven), `extract` and `upload` (the user allows it on the page);
  `alert`/`confirm`/`prompt` are answered as they open (`dialog: accept|dismiss`) so a click never
  hangs; typing into a password/card field asks the user. `dock_snapshot since=<rev>` and
  `observe: "diff"` return only what changed. New `dock_screenshot` (an MCP image),
  `dock_console` and `dock_network` (secrets in URLs redacted). Each MCP server process is a
  session that leases the tabs it acts in; tabs run in parallel; over stdio the `dock_*` tools
  run off the reader thread, so `notifications/cancelled` stops a call in the browser and a
  quick call is never stuck behind a slow one. `dock_*` results are compact JSON.
