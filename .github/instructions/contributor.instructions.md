---
applyTo: '**'
---

# NAVIG Contributor Directives

## Core Rules
- Reuse existing code over writing new utilities. Do not duplicate logic.
- Maintain repo hygiene: do not invent new ad-hoc structures. Use standard directories.
- Keep the repo root clean. Place junk, WIP, drafts, and test scripts in `.local/`.
- This is a Python project. Avoid Node/npm within Python modules.
- Run checks without asking. Fix and validate in one loop.
- Update the handbook (`docs/user/HANDBOOK.md`) when introducing or changing CLI command usage.
- Ask for explicit approval for any risky or destructive operations.