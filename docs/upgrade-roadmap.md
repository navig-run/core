# NAVIG Upgrade Roadmap# NAVIG CLI — Upgrade Roadmap









































































| v2.4.15 | Daemon crash-loop guard, MCP client fix, Windows JSON fix, help path fix, help tests || v2.4.14 | `task → flow` deprecated alias, mesh/browser/memory, gateway, gitignore hardening ||---------|-----------|| Version | Highlights |## Completed Milestones---| Add `navig help` system architecture note to HANDBOOK | `docs/user/HANDBOOK.md` | Optional; low priority || Ensure HANDBOOK deprecated-command table reflects `task → flow` | `docs/user/HANDBOOK.md` | Already present since v2.4.14 || Troubleshooting guide: add v2.4.15 fixed bugs section | ✅ `docs/user/troubleshooting.md` | Done ||------|---------|-------|| Item | File(s) | Notes |## Priority 5 — Documentation---| Duplicate command registration regression test | add to `tests/test_onboarding_status_json.py` | — || `show_compact_help` render test (index.md loads, no crash) | add to `test_help_system.py` | — || `navig flow list` / `navig task list` compatibility | `tests/test_flow_commands.py` (new) | Should verify `task` routes to flow || `navig help --json` output validity | ✅ `tests/test_help_system.py` | Done || `navig help` topics smoke test (canonical group coverage) | ✅ `tests/test_help_system.py` | Done in v2.4.15 ||------|---------|-------|| Item | File(s) | Notes |## Priority 4 — Test coverage (blocking for CI green badge)---| Remove or consolidate flat-command shims in `cli/legacy_flat_commands.py` | `cli/legacy_flat_commands.py` | After verifying no users depend on them || `init` help entry doesn't reflect current onboarding flags | `cli/help_dictionaries.py` | Update description || Stale `start` help in registry describes gateway/bot startup but fast-path launches dashboard | `cli/help_dictionaries.py` | Minor doc drift || `profile` registered twice (lazy map + manual `main.py`) | `cli/__init__.py`, `main.py` | Low risk, but wastes startup time || Unify duplicate `show_compact_help` / `show_subcommand_help` definitions | `cli/__init__.py`, `cli/_callbacks.py` | Both define the same functions; one could import from the other ||------|---------|-------|| Item | File(s) | Notes |## Priority 3 — CLI cleanup (no breaking changes)---| No tests for help topic resolution, JSON output, or `task`/`flow` aliasing | `tests/test_help_system.py` | ✅ Done (v2.4.15) || `index.md` lists `task` as "task queue" | `help/index.md` | ✅ Done (v2.4.15) || `flow` help lacks full command list | `help/flow.md`, `cli/help_dictionaries.py` | ✅ Done (v2.4.15) || `task` help/registry describes queue semantics, not actual flow alias | `help/task.md`, `cli/help_dictionaries.py` | ✅ Done (v2.4.15) || `help_command` JSON output corrupted by Rich line-wrapping | `cli/__init__.py` | ✅ Done (v2.4.15) || `show_compact_help` imports non-existent `navig.cli.help.render_root_help` | `cli/__init__.py`, `cli/_callbacks.py` | ✅ Done (v2.4.15) || `help_command` reads from wrong directory (`cli/help/` vs `help/`) | `cli/__init__.py` | ✅ Done (v2.4.15) ||------|---------|--------|| Item | File(s) | Status |## Priority 2 — Help system integrity---| GROK/XAI key bypasses vault resolver | `daemon/telegram_worker.py` | ✅ Done (v2.4.15) || STT warning references removed `~/.navig/.env` path | `daemon/telegram_worker.py` | ✅ Done (v2.4.15) || `formation show --json` OSError on Windows (non-TTY stdout) | `commands/formation.py` | ✅ Done (v2.4.15) || MCP `add_client()` wrong signature breaks Forge startup | `daemon/telegram_worker.py` | ✅ Done (v2.4.15) || Crash-loop guard: Telegram bot restarts infinitely | `daemon/supervisor.py` | ✅ Done (v2.4.15) ||------|---------|--------|| Item | File(s) | Status |## Priority 1 — Critical (ship-blocker level)---help-system drift found in the March 2026 codebase audit.Prioritized implementation tasks. Derived from crash-log analysis, test coverage gaps, and
*Generated: 2026-03-25 | Scope: static code analysis + doc audit*

---

## Executive Summary

A static analysis of the NAVIG CLI codebase and documentation revealed **no
active runtime errors** in the debug log (the log had not yet been written at
time of audit). However, analysis of the source code identified four
code-level bugs, two stale documentation files, and one missing document.
All issues were fixed in this pass. This document records findings, actions
taken, and work planned for future releases.

---

## Bugs Found & Fixed

| # | Type | Location | Severity | Status |
|---|------|----------|----------|--------|
| 1 | Missing module | `navig/cli/registry.py` missing; `navig --schema` raised `ImportError` | High | ✅ Fixed |
| 2 | Stale docs | `docs/user/commands.md` referenced `navig monitor`, `navig security`, deprecated tunnel/file/hestia commands | Medium | ✅ Fixed |
| 3 | Stale docs | `docs/user/quick-start.md` used `python navig.py` instead of `navig`; referenced deprecated `monitor` commands | Medium | ✅ Fixed |
| 4 | Missing doc | `docs/user/workflows.md` not present (referenced as a deliverable in instructions, needed by users) | Low | ✅ Created |

---

## Command Simplifications

Commands already follow the `navig <resource> <action> [options]` pattern
well. The deprecated aliases listed below remain registered as hidden
commands with active `deprecation_warning()` calls, preserving backward
compatibility while guiding users to the canonical paths.

| Deprecated Command | Canonical Replacement | Notes |
|--------------------|-----------------------|-------|
| `navig monitor <sub>` | `navig host monitor show [--flag]` | `monitor` app hidden, warns on use |
| `navig security <sub>` | `navig host security show [--flag]` | `security` app hidden, warns on use |
| `navig hestia <sub>` | `navig web hestia <sub>` | `hestia` app hidden, warns on use |
| `navig workflow <sub>` | `navig flow <sub>` | `workflow` alias hidden, warns on use |
| `navig addon <sub>` | `navig flow template <sub>` | `addon` app hidden, warns on use |
| `navig template <sub>` | `navig flow template <sub>` | `template` app hidden, warns on use |
| `navig tunnel start` | `navig tunnel run` | Deprecated callback warns |
| `navig tunnel stop` | `navig tunnel remove` | Deprecated callback warns |
| `navig tunnel status` | `navig tunnel show` | Deprecated callback warns |
| `navig tunnel restart` | `navig tunnel update` | Deprecated callback warns |
| `navig upload` | `navig file add` | Legacy flat command, backward compat |
| `navig download` | `navig file get` | Legacy flat command, backward compat |
| `navig cat` / `navig ls` | `navig file show` / `navig file list` | Legacy flat commands, backward compat |

---

## Planned Features

### Critical (must-haves before GA)

| # | Feature | Rationale | Complexity |
|---|---------|-----------|------------|
| C1 | `navig/cli/__init__.py` split | 10,622-line monolith is a maintenance liability; command groups should be separate files | Complex |
| C2 | `navig config validate --scope both` CI integration | Prevent config-schema regressions in CI | Simple |
| C3 | Auto-generate `navig --schema` completions for fish/zsh/bash | Needed for power users; schema already exists | Medium |

### High Priority

| # | Feature | Rationale | Complexity |
|---|---------|-----------|------------|
| H1 | Deprecation sunset in v3.0 — remove 6 hidden command groups | Reduces CLI surface and maintenance burden | Simple |
| H2 | `navig --schema` JSON consumed by official shell completion scripts | Makes tab-completion maintainable | Medium |
| H3 | `navig flow` auto-discovery from `.navig/flows/*.yaml` | Users should not need to use `navig flow add` to adopt local flows | Simple |
| H4 | `navig db run` — recognise `.gz` files and decompress before restoration | Avoids common user error | Simple |

### Medium Priority

| # | Feature | Rationale | Complexity |
|---|---------|-----------|------------|
| M1 | `navig help` index.md auto-generation from `HELP_REGISTRY` | Keep index in sync automatically | Simple |
| M2 | Expand sparse help topic files (`navig help db`, `navig help web`, etc.) | Current files are functional but minimal | Simple |
| M3 | `navig run --b64` in-terminal encoding helper (`navig encode "<cmd>"`) | Reduce friction for the most confusing part of the CLI | Simple |
| M4 | `navig host monitor show` — add `--watch N` for continuous refresh | Power-user quality-of-life | Medium |
| M5 | Plugin cache invalidation on `pip install -e .` | Stale plugin cache causes confusing errors | Medium |

### Low Priority

| # | Feature | Rationale | Complexity |
|---|---------|-----------|------------|
| L1 | Split `HANDBOOK.md` (8,118 lines) into per-section files | Easier to maintain, faster to search | Medium |
| L2 | `navig history replay <n>` — replay with edit before executing | Power-user quality-of-life | Medium |
| L3 | Unified test for all deprecated commands (CI guard) | Prevents silent breakage of deprecation warnings | Simple |

---

## Breaking Changes

None in this pass. All changes are additive:

- `navig/cli/registry.py` is a new module; nothing existing depends on it.
- Deprecated commands remain registered. Their callbacks are unchanged.
- Documentation fixes do not affect any code path.

Planned breaking changes (v3.0 milestone):

| Change | Affected Users | Migration |
|--------|----------------|-----------|
| Remove 6 deprecated command groups (`hestia`, `workflow`, `addon`, `template`, `monitor`, `security`) | Users still calling old forms | Switch to canonical replacements listed in the table above |
| Remove legacy flat commands (`upload`, `download`, `cat`, `ls`) | Scripts using old flat form | Replace with `navig file add / get / show / list` |

---

## Migration Guide

### For Users on NAVIG < 2.x

Run each deprecated command once — it will print the replacement command.
Example:

```
$ navig monitor health
⚠ 'navig monitor' is deprecated and will be removed in v3.0.0.  Use 'navig host monitor' instead.
```

### Updating Your Scripts

| Old Script Pattern | New Pattern |
|-------------------|-------------|
| `navig monitor health` | `navig host monitor show` |
| `navig monitor resources` | `navig host monitor show --resources` |
| `navig security firewall` | `navig host security show --firewall` |
| `navig hestia users` | `navig web hestia list` |
| `navig tunnel start` | `navig tunnel run` |
| `navig tunnel stop` | `navig tunnel remove` |
| `navig tunnel status` | `navig tunnel show` |
| `navig upload <file> <path>` | `navig file add <file> <path>` |
| `navig download <path>` | `navig file get <path>` |
| `navig cat <path>` | `navig file show <path>` |
| `navig ls <path>` | `navig file list <path>` |

---

## Implementation Task Checklist

### Completed (this pass)

- [x] **Create `navig/cli/registry.py`**
  - Priority: High | Complexity: Simple | Acceptance: `navig --schema` returns valid JSON
- [x] **Fix `docs/user/commands.md`**
  - Priority: Medium | Complexity: Simple | Acceptance: No references to deprecated `navig monitor` / `navig security` top-level
- [x] **Fix `docs/user/quick-start.md`**
  - Priority: Medium | Complexity: Simple | Acceptance: Uses `navig --version`, not `python navig.py`
- [x] **Create `docs/user/workflows.md`**
  - Priority: Medium | Complexity: Simple | Acceptance: Covers backup, deploy, file transfer, health check, Telegram setup, restore, SSH key rotation
- [x] **Add section 1.7 (In-App Help) to `HANDBOOK.md`**
  - Priority: Medium | Complexity: Simple | Acceptance: Section explains `navig help`, `navig help <topic>`, `navig --schema`

### Planned — v2.x

- [ ] **L3: Unified test for deprecated commands**
  - Priority: Low | Complexity: Simple | Deps: none
  - Acceptance: `pytest tests/test_deprecated_commands.py` passes; each deprecated alias fires a warning
- [ ] **M3: `navig encode "<cmd>"`**
  - Priority: Medium | Complexity: Simple | Deps: none
  - Acceptance: `navig encode "echo hello"` outputs the base64 string ready for `--b64`
- [ ] **M1: Auto-generate `navig/help/index.md` from `HELP_REGISTRY`**
  - Priority: Medium | Complexity: Simple | Deps: none
  - Acceptance: `navig help` reads from the generated index; no manual sync required
- [ ] **H3: `navig flow` auto-discovery**
  - Priority: High | Complexity: Simple | Deps: none
  - Acceptance: Flows in `.navig/flows/*.yaml` appear in `navig flow list` without explicit `navig flow add`
- [ ] **H4: `navig db run` — handle `.gz` dumps**
  - Priority: High | Complexity: Simple | Deps: none
  - Acceptance: `navig db restore mydb backup.sql.gz` decompresses transparently

### Planned — v3.0

- [ ] **H1: Remove deprecated command groups**
  - Priority: High | Complexity: Simple | Deps: L3 (tests), one release cycle of warnings
  - Acceptance: `navig hestia`, `navig monitor`, `navig security`, `navig workflow`, `navig addon`, `navig template` all return `command not found`
- [ ] **C1: Split `navig/cli/__init__.py`**
  - Priority: Critical | Complexity: Complex | Deps: H1 (removes deprecated groups first, reducing scope)
  - Acceptance: `navig/cli/__init__.py` < 500 lines; each command group is a separate module; `tsc --noEmit`-equivalent passes (all imports resolve)
- [ ] **C3: Shell completion scripts**
  - Priority: Critical | Complexity: Medium | Deps: C1, `navig --schema`
  - Acceptance: Tab-completion works in bash, zsh, and fish (generated from schema)
