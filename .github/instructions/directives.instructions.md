---
applyTo: '**'
---

# AI Dev Directives — Lightweight, Auto-Complete Workflow

These directives define a simple, non-noisy workflow for this project.
Optimize for **fast fixes, minimal docs, and reliable automation**.

## Scope & Structure (Keep It Lean)
- Do **not** invent new architecture, folder trees, or random files. Do not put trash files, logs, or temp dirs in the repo root.
- Use the project's **existing structure**.
- Only rely on these standard areas when needed:
  - `docs/` for dev documentation (`HANDBOOK.md` is the primary doc)
  - `tests/` for pytest tests
  - `scripts/` for install/utility scripts
  - `navig/commands/` for new CLI command modules
  - `navig/tools/` for new tool/adapter implementations
  - `.backups/` for safe snapshots (only if required)
  - `.local/` for WIP drafts, scratch scripts, and test artifacts — this dir is gitignored
- **This is a Python project:** `pyproject.toml`, `pytest`, `pip install -e .` — no Node/npm.

### pytest Temp Directories
- `pytest.ini` sets `--basetemp=.local/.pytest_tmp` — **never override this with a root-level path**.
- When passing `--basetemp` on the command line (e.g., to avoid Windows file locks), always use a path inside `.local/`:
  ```
  pytest --basetemp=.local/.pytest_tmp_mytest ...
  ```
- Paths like `.pytest_tmp_onboard_visuals` or `.pytest_tmp_plans` **directly in the repo root are forbidden** — they are not gitignored by default and will show up in `git status`.


## Remote / Database Operations
- If you must touch **remote production** or a **local database**, use the **NAVIG tool** for all operations.
- Follow this repo’s NAVIG usage conventions and keep commands safe and minimal.

---

## Execution Loop (Auto-Run, No Hand-Holding)
When debugging, upgrading, or implementing a feature, follow this compact loop **without stopping for confirmation**:

1. **Analyze**
   - Identify likely root causes.
   - Add minimal diagnostics if needed.

2. **Implement**
   - Fix the issue.
   - Opportunistically complete small related unfinished parts **only if clearly connected**.

3. **Validate**
   - Run relevant checks and confirm expected behavior.

4. **Test**
   - Add or update tests in `/tests/` when the change affects logic or regressions are plausible.

5. **Document (Only If Worth It)**
   - Update existing docs in `/docs/`.
   - **Do not create new docs unless truly necessary.**
   - Avoid “junk docs.” Prefer short updates to current files.
   - Record only decisions, usage changes, or non-obvious behavior.

6. **Scripts (If Needed)**
   - Add or update utilities in `/scripts/` only when they meaningfully reduce future work.

7. **Backup (Rare)**
   - Use `/.backups/` only for risky refactors or migrations.

**Rule:** If something fails, iterate the loop until resolved.

---

## Documentation Discipline
- No long walls of text.
- No duplicate documents.
- No “README spam.”
- Prefer **incremental upgrades** of existing docs with fresh, relevant info.

---

## Dependency Policy
- Use the **latest stable** versions when adding or upgrading dependencies,
  unless compatibility constraints are documented.

---

# NAVIG Debug Mode (Auto-Trigger)
If a NAVIG debug log is present or referenced, you must:

## Read This Log
- `~/.navig/debug.log`

## Then Automatically:
1. **Categorize errors**
   - Auth/SSH, config parsing, DB ops, imports, file I/O, command failures.

2. **Fix**
   - Improve validation and error messages.
   - Preserve backward compatibility.

3. **Simplify commands**
   - Prefer predictable patterns: `navig <resource> <action> [options]`.
   - Add aliases if helpful and safe.

4. **Validate**
   - Re-run affected commands.

5. **Docs**
   - Update only essential command changes in `/docs/`.

6. **Tests**
   - Add minimal coverage in `/tests/` if behavior is non-trivial.

---

## What NOT To Do
- Don’t add large new documentation sets.
- Don’t create new folder hierarchies without need.
- Don’t refactor unrelated code “for cleanliness.”
- Don’t touch production/db outside NAVIG when NAVIG applies.
- Don’t break existing command workflows.

---

## Success Criteria
- The issue is fixed and verified.
- Changes are minimal, targeted, and stable.
- Tests updated when needed.
- Docs updated **only if important**.
- No structural noise added.
