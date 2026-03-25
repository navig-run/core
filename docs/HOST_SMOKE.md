# Host Smoke Test

Run this checklist against one real machine after any release candidate.
Fill in pass/fail and note any weirdness.

```
navig host add
navig host test
navig host use <name>
navig run "uname -a"
navig run "docker ps"
navig run "df -h"
```

---

| Step | Result | Notes |
|---|---|---|
| `host add` | pass / fail | |
| `host test` | pass / fail | |
| `host use <name>` | pass / fail | |
| `run "uname -a"` | pass / fail | |
| `run "docker ps"` | pass / fail | |
| `run "df -h"` | pass / fail | |

**Weirdness:**

-

---

Tested by:
Date:
Host OS:
NAVIG version (`navig --version`):
