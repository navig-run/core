---
id: devops-shortcuts
spec_version: 1
name: DevOps Shortcuts
version: 1.0.0
category: ops
license: MIT
description: A cheat-sheet of common daily DevOps commands (logs, health, backup, restart, disk, memory, processes, connectivity).
author: navig
tags: [devops, shortcuts, reference, cheatsheet]
target: local

inputs: []

steps:
  - id: cheatsheet
    kind: instruction
    safety: safe
    text: |
      Common daily DevOps commands (run whichever you need):

      - Follow logs        →  navig logs --follow --lines 100
      - Health of all hosts →  navig health --all
      - Verified DB backup →  apply the db-snapshot block
      - Restart containers →  navig docker restart --all
      - Disk usage         →  navig run "df -h"
      - Memory usage       →  navig run "free -h"
      - Processes          →  navig run "top -bn1 | head -20"
      - Test connectivity  →  navig host test

verify:
  kind: none

receipt:
  redact: []
---
# DevOps shortcuts — cheat sheet

A quick reference of the commands you reach for daily. This was a `quickactions`
pack (a menu of independent commands, not an ordered sequence), so it ships as a
single **instruction** cheat-sheet block rather than a fake step sequence — that's
the honest representation of a command menu.

## Apply

```
navig apply devops-shortcuts
```

For a *runnable* database backup, apply the **db-snapshot** block.
