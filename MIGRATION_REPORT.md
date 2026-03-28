# Deprecation Migration Report

**Date:** 2026-03-28
**Branch:** `feature/deprecation-cleanup`
**Runtime:** Python ≥3.10 · navig v2.4.14
**Audited by:** GitHub Copilot (NAVIG dev loop)

---

## Summary

| Category | Found | Replaced | Deferred |
|----------|------:|--------:|--------:|
| Critical — `asyncio.get_event_loop()` | 25 | 25 | 0 |
| Warning — wrong `deprecation_warning` import path | 2 | 2 | 0 |
| Warning — `requirements.txt` Python version stale | 1 | 1 | 0 |
| Warning — `requirements.txt` typer version drift | 1 | 1 | 0 |
| Informational — `Console(legacy_windows=True)` | 1 | 1 | 0 |
| Informational — `typing.Optional/Dict/List` old-style | ~30 files | 0 | 30 (ruff UP-rules already ignoring) |
| Informational — `speedtest-cli` unmaintained | 1 | 0 | 1 (new dep required) |
| Informational — `@deprecated_command` unused | 1 | 0 | 1 (internal cleanup sprint) |
| Internal — DEPRECATION_MAP vs instructions divergence | 6 commands | 0 | 6 (human review required) |
| **Total** | **68** | **31** | **37** |

---

## Replaced — All Changed Files

### `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (25 sites)

All sites were inside `async def` bodies or in sync helpers that are always invoked
from within a running event loop. `asyncio.get_event_loop()` without a running loop
emits `DeprecationWarning` since Python 3.10 and will raise `RuntimeError` in
Python 3.14.

| File | Lines changed | Pattern |
|------|--------------|---------|
| `navig/voice/wake_word.py` | L157, L227 | `self._loop = …`, `loop = …` |
| `navig/voice/stt.py` | L365 | `loop = …` → `run_in_executor` |
| `navig/voice/playback.py` | L98 | `loop = …` → `run_in_executor` |
| `navig/mesh/election.py` | L122 | `self._loop = …` (in `async def start`) |
| `navig/mesh/discovery.py` | L249, L321 | `loop = …` → `run_in_executor` and `_listen_loop` |
| `navig/mcp/transport.py` | L144 | `loop = …` → `create_future` |
| `navig/engine/queue.py` | L236 | `loop = …` → `create_future` |
| `navig/desktop/watcher.py` | L261 | `loop = …` inside `try/except RuntimeError` (comment updated) |
| `navig/agent/runner.py` | L213 | `loop = …` → signal handler setup (inside `async def`) |
| `navig/agent/proactive/engine.py` | L217 | `loop = …` → `create_task` |
| `navig/agent/proactive/imap_email.py` | L118, L163, L198 | `loop = …` → `run_in_executor` ×3 |
| `navig/agent/coordination.py` | L269 | inline `asyncio.get_event_loop().create_future()` |
| `navig/tasks/worker.py` | L207, L263 | `loop = …` → `run_in_executor` ×2 |
| `navig/gateway/channels/media_engine/image.py` | L322, L474 | `loop = …` → `run_in_executor` ×2 |
| `navig/tools/domains/image_pack.py` | L22-L33 | Sync detection pattern: `if loop.is_running()` → `try: asyncio.get_running_loop()` / `except RuntimeError:` |
| `navig/tools/site_check.py` | L132 | inline `asyncio.get_event_loop().run_in_executor(…)` |
| `navig/tui/screens/welcome.py` | L97 | inline `asyncio.get_event_loop().run_in_executor(…)` |
| `navig/commands/onboard.py` | L1161 | inline `asyncio.get_event_loop().run_in_executor(…)` |
| `navig/bot/intent_parser.py` | L651 | inline inside `asyncio.wait_for(…)` |

**Note on `image_pack.py`:** The sync helper `_sync_generate()` used `loop.is_running()`
to detect whether it was called from inside a running event loop. The replacement uses
the idiomatic Python 3.10+ pattern: `try: asyncio.get_running_loop()` / `except RuntimeError:`.
Behaviour is identical; no logic change.

---

### `deprecation_warning` import path (2 files)

The function was imported from `navig.cli` (the CLI boundary module) instead of its
canonical home in `navig.deprecation`. The re-export still works but creates an
unnecessary dependency on the CLI layer from pure command modules.

| File | Old import | New import |
|------|-----------|-----------|
| `navig/commands/db.py` | `from navig.cli import deprecation_warning, show_subcommand_help` | Two separate imports; `deprecation_warning` from `navig.deprecation` |
| `navig/commands/app.py` | same | same fix |

`navig/commands/host.py` was already clean (fixed in a prior commit).

---

### `requirements.txt` alignment (2 changes)

| Change | Old value | New value | Reason |
|--------|-----------|-----------|--------|
| Python version comment | `# Python 3.8+ required` | `# Python 3.10+ required` | Matches `requires-python = ">=3.10"` in `pyproject.toml` |
| Typer version floor | `typer[all]>=0.9.0` | `typer[all]>=0.14.0` | Matches `pyproject.toml`; typer 0.9→0.14 contains API changes |

---

### `Console(legacy_windows=True)` removal (`navig/cli/_callbacks.py` L20)

Rich's `legacy_windows=True` Console mode was softly deprecated in Rich 13.x.
The project already requires `rich>=13.7.0`. Windows 10+/11 have full VT support;
the flag provides no benefit and is a no-op on other platforms. Removed.

---

## Deferred — Documented Items

### 1. `typing.Optional` / `typing.Dict` / `typing.List` (PEP 585/604)

**Scope:** ~30 files across `navig/`
**Reason deferred:** Explicitly ignored in `pyproject.toml` via ruff rules
`UP006`, `UP007`, `UP035`, `UP037`, `UP045`. These are low-risk style updates with
no runtime impact on Python 3.10–3.13. Migrate in a dedicated sprint when ruff
auto-fix is enabled for these rules.

### 2. `speedtest-cli` — unmaintained upstream

**File:** `pyproject.toml` (optional dep `speedtest-cli>=2.1.3`)
**Reason deferred:** The recommended replacement is the Ookla CLI binary or a
different Python package; both require a dependency change and behavior validation.
No drop-in 1-to-1 replacement is available as a pure pip package.

### 3. `@deprecated_command` decorator — unused

**File:** `navig/deprecation.py`
**Reason deferred:** The decorator is defined and importable but applied nowhere.
The `deprecation_warning()` function is used for inline warnings instead. The unused
decorator is harmless but should be either applied consistently across all deprecated
commands or removed in the next deprecation enforcement sprint.

### 4. `warnings.warn()` missing `stacklevel` (5 sites)

**File:** `navig/core/config_schema.py` (L173, L368, L425, L437, L486)
**Reason deferred:** Ruff rule `B028` is globally disabled in `pyproject.toml` (`noqa`).
Fixing requires enabling the rule project-wide and adding `stacklevel=2` (or higher)
to several calls. Low risk, low priority.

### 5. DEPRECATION_MAP vs instructions divergence (6 commands)

**Files:** `navig/deprecation.py`, `.github/instructions/navig.instructions.md`
**Reason deferred:** Requires human decision on source of truth.

The following commands appear as deprecated in `DEPRECATION_MAP` (v3.0.0 target)
but are documented as current canonical commands in `navig.instructions.md`:

| Command in DEPRECATION_MAP (marked old) | Map's replacement |
|----------------------------------------|-------------------|
| `navig db query` | `navig db run` |
| `navig web vhosts` | `navig server list --vhosts` |
| `navig docker ps` | `navig server list --containers` |
| `navig docker logs` | `navig log show --container` |
| `navig backup export` | `navig backup add` |
| `navig config validate` | `navig config test` |

**Action required:** Decide which is canonical and either revert the DEPRECATION_MAP
entries or update the instructions file accordingly.

---

## Verification

```
ruff check <22 changed files>   → All checks passed
pytest tests/ -x -q             → 2894 passed, 30 skipped, 0 failures (2m 50s)
```
