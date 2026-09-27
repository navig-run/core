- **The navig.run/proof "download, then verify" commands failed exactly as printed.**
  `navig block verify-receipt receipt.json` reported "receipt not found" and
  `navig ledger verify --path operations.jsonl` reported "No ledger … nothing recorded yet"
  **and exited 0**, because both resolved a relative path in the active space rather than
  the folder the operator ran them from. Both now resolve typed paths where they were typed
  (`resolve_user_path`), as do `navig block verify` and `navig block sign`. And
  `navig ledger verify --path <file>` naming a file that does not exist now exits 1 ("nothing
  was verified"); a missing *default* ledger on a fresh install is still an honest exit 0.
