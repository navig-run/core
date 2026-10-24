- **Distribution 0.4.0 closes its honest gaps**: reviewed form maps now run in a real
  browser through a named automation profile (`navig distribution publish run <id>
  --profile <name>`; personal profiles refused, one submission per destination at a
  time); scheduled publications wait in `SCHEDULED` until `navig distribution publish
  due` (or `schedule --publish-due`) finds their approved time; the module settings
  (`stale_days`, `default_mode`, `auto_mirror_receipts`) are applied; campaigns gain
  update/delete-or-archive and a `campaign` CLI group; verification provenance is
  append-only.
