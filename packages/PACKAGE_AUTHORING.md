# NAVIG Plugin System — AI Dev Agent Instructions

> Version: 1.0.0
> This file is the authoritative guide for AI dev agents building packs, tools, skills, and workflows for NAVIG.

---

## Quick Mental Model

```
store/
  plugins/
    my-pack@1.0.0/
      navig.pack.json   ← manifest (required)
      handler.py        ← lifecycle hooks (on_load, on_unload, on_event)
      commands/
        mycommand.py    ← async def handle(args: dict, ctx) -> dict
        __init__.py     ← COMMANDS = {"mycommand": handle}
      telegram/
        handlers.py     ← cmd_mycommand(update, context) Telegram handler
      skills/
        myskill.md      ← prompt template
      tests/
        test_mycommand.py
```

---

## 1. Pack Manifest (navig.pack.json)

```json
{
  "id": "my-pack",
  "name": "My Pack",
  "version": "1.0.0",
  "description": "One sentence.",
  "author": "Your Name",
  "license": "MIT",
  "homepage": "https://github.com/you/my-pack",
  "provides": ["commands"],
  "entry": "handler.py",
  "depends": {
    "pip": ["requests>=2.28.0"],
    "tools": [],
    "skills": {},
    "plugins": {}
  },
  "config_defaults": {
    "timeout": 10
  },
  "install_hooks": {
    "post_install": "scripts/post_install.py"
  }
}
```

### `provides` enum values
| Value | What it registers |
|---|---|
| `commands` | CLI + all-transport handlers via CommandRegistry |
| `telegram` | Telegram bot handlers (PTB) |
| `discord` | Discord slash commands (not yet active) |
| `matrix` | Matrix bot commands (not yet active) |
| `skills` | LLM prompt templates |
| `prompts` | One-shot prompt files |
| `playbooks` | Multi-step automation sequences |
| `workflows` | DAG-based workflows |
| `webflows` | Browser automation sequences |
| `src` | Raw Python source — no automatic registration |

---

## 2. Command Handler Pattern

**Rule: zero transport imports in commands/.**

```python
# commands/mycommand.py
async def handle(args: dict, ctx) -> dict:
    """
    args:  validated input dict (keys depend on command)
    ctx:   SimpleNamespace(pack_id, version, store_path, config)
    returns: {"status": "ok"|"error", "data": ..., "message": str}
    """
    domain = args.get("domain", "")
    if not domain:
        return {"status": "error", "message": "domain is required"}
    # ... logic ...
    return {"status": "ok", "data": result, "message": ""}
```

```python
# commands/__init__.py
from .mycommand import handle as _mycommand_handle
COMMANDS: dict[str, callable] = {
    "mycommand": _mycommand_handle,
}
```

**Only import stdlib or pip deps the pack declares in its manifest.**
Never import `telegram`, `discord`, or `click` inside commands/.

---

## 3. Lifecycle Handler (handler.py)

```python
# handler.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class PluginContext:
    pack_id: str
    version: str
    store_path: Path
    config: dict = field(default_factory=dict)


def on_load(ctx: PluginContext) -> None:
    """Called once when the pack is activated.
    Register commands, start background workers, etc."""
    try:
        from navig.commands._registry import CommandRegistry
        from .commands import COMMANDS
        for name, handler in COMMANDS.items():
            CommandRegistry.register(name, handler)
    except ImportError:
        pass  # standalone mode — ok


def on_unload(ctx: PluginContext) -> None:
    """Called when the pack is deactivated or uninstalled."""
    try:
        from navig.commands._registry import CommandRegistry
        from .commands import COMMANDS
        for name in COMMANDS:
            CommandRegistry.deregister(name)
    except ImportError:
        pass


def on_event(event: str, ctx: dict) -> dict | None:
    """Optional: handle named events emitted by the NAVIG event bus."""
    return None
```

---

## 4. Telegram Transport Layer

**Place in: `tg_handlers.py`**

```python
# tg_handlers.py
from telegram import Update
from telegram.ext import ContextTypes


async def cmd_mycommand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /mycommand <input>")
        return

    # Get the registered handler (works in both authoring and installed mode)
    handler = _get_handler("mycommand")
    result = await handler({"input": args[0]}, ctx=None)

    if result.get("status") == "ok":
        await update.message.reply_text(str(result["data"]))
    else:
        await update.message.reply_text(f"Error: {result.get('message', '?')}")


def _get_handler(command: str):
    """Two-level fallback: CommandRegistry → direct import."""
    try:
        from navig.commands._registry import CommandRegistry
        return CommandRegistry.get(command)
    except Exception:
        pass
    # Direct import fallback (authoring mode)
    import importlib
    mod = importlib.import_module(f"..commands.{command}", package=__name__)
    return mod.handle


# All handlers this module provides — picked up by telegram_worker autodiscovery
TELEGRAM_COMMANDS: dict[str, callable] = {
    "mycommand": cmd_mycommand,
}
```

**Rule:** Never put business logic in `tg_handlers.py` — only arg parsing and transport adaption. All logic lives in `commands/`.

---

## 5. Formatters and Menus (optional UX layer)

Create a separate pack (e.g. `my-pack-handlers`) for Telegram-specific UX:

```python
# telegram/formatters.py
def format_mycommand(result: dict) -> str:
    if result["status"] == "ok":
        return f"✅ *Result:* {result['data']}"
    return f"❌ *Error:* {result.get('message', 'unknown')}"

FORMATTERS: dict[str, callable] = {"mycommand": format_mycommand}
```

```python
# telegram/menus.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_mycommand_menu(result: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Try another", callback_data="mycommand_retry")
    ]])

MENUS: dict[str, callable] = {"mycommand": build_mycommand_menu}
```

---

## 6. Skills (LLM Prompt Templates)

Place `.md` or `.yaml` files in `skills/`:

```markdown
<!-- skills/summarize.md -->
---
name: summarize
version: 1.0.0
description: Summarize any text into 3 bullet points.
inputs:
  - name: text
    description: Text to summarize
    required: true
---

Summarize the following text in exactly 3 concise bullet points.
Do not add any intro or outro.

Text: {{text}}
```

The NAVIG skills indexer picks them up from `store/skills/`.

---

## 7. Testing

**Every pack must have tests in tests/.**

```python
# tests/test_mycommand.py
import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from commands.mycommand import handle


class TestMyCommand(unittest.IsolatedAsyncioTestCase):

    async def test_returns_ok_on_valid_input(self):
        ctx = MagicMock()
        result = await handle({"input": "example"}, ctx)
        self.assertEqual(result["status"], "ok")

    async def test_returns_error_on_missing_input(self):
        ctx = MagicMock()
        result = await handle({}, ctx)
        self.assertEqual(result["status"], "error")
```

Run tests: `py -3 -m pytest plugins/my-pack/tests/ -q`

---

## 8. Pack File Layout Rules

| Rule | Why |
|---|---|
| `commands/*.py` — no transport imports | Transport independence |
| `telegram/*.py` — only PTB + format | Single responsibility |
| `handler.py` — no blocking I/O in `on_load` | Startup speed |
| `tests/` — at minimum 2 test cases | Prevent regressions |
| `navig.pack.json` — must declare all `pip` deps | Installer reproducibility |
| Use `store_dir()` from `navig.space.paths` for paths | Cross-platform paths |

---

## 9. Installing a Pack (dev)

```powershell
# Option A: copy directly into store (authoring mode)
$store = "$env:USERPROFILE\UserData\NAVIG\store\plugins"
Copy-Item -Recurse "plugins/my-pack" "$store\my-pack@1.0.0"

# Option B: navig pack install (when the pack registry is live)
navig pack install my-pack

# Option C: install from local zip
navig pack install ./my-pack-1.0.0.zip
```

---

## 10. Publishing to the Registry

> Registry: https://registry.navig.ai/packs  (live Q3 2025)

1. Tag your repo with `v1.0.0`
2. Run `zip -r my-pack-1.0.0.zip .` from pack root
3. Submit via `navig pack publish --token $YOUR_TOKEN ./my-pack-1.0.0.zip`
4. Reviewers check: manifest valid, tests pass, no unsafe imports, no hardcoded secrets

---

## 11. AI Agent Checklist (before writing any pack file)

```
[ ] manifest declares all pip deps
[ ] commands/ has zero transport imports
[ ] handler.py on_load is safe to call at startup (no network, no blocking)
[ ] tests/ has at least one test per command
[ ] provides[] matches what the pack actually delivers
[ ] config_defaults has sensible values
[ ] post_install script is idempotent
[ ] No hardcoded tokens or API keys anywhere
[ ] tsc --noEmit (if TS) or py -m py_compile (if Python) exits 0
```

---

## 12. Anti-patterns to Avoid

| Anti-pattern | Why it breaks things |
|---|---|
| `from telegram import ...` in commands/ | Breaks CLI and non-Telegram use |
| Storing state in module globals | Breaks multi-instance deployments |
| Calling `store_dir()` at import time | Slows startup if disk is slow |
| Creating `plugins/` subfolders inside a plugin | Breaks pack scanner |
| Using `print()` instead of `logging` | Not captured by log backend |
| Hard-coding `~/.navig/config.yaml` paths | Use `navig.config.get()` instead |
| Writing to `store/plugins/` directly from pack code | Use `ctx.store_path` |

---

## 13. Reference Links

- `navig/commands/package.py` — PackageManager: install, list, show, remove
- `navig/plugins/__init__.py` — Python-level PluginManager (runtime, not content)
- `packages/navig-commands-core/` — reference implementation (commands type)
- `packages/navig-telegram/` — reference implementation (telegram type)
- `packages/navig-telegram-handlers/` — reference implementation (UX layer)
- `packages/navig-windows-automation/` — reference implementation (tools type)
- `packages/lifeos/` — reference implementation (workflows type)
---

## 14. Migration from `plugins/` (Deprecated)

The root `plugins/` directory has been removed. It used the old `navig.plugin.json` schema
with incompatible keys (`entrypoint`, `permissions`, `capabilities`).

All plugin bundles have been migrated to `packages/` with the current `navig.package.json`
schema (`entry`, `provides`, `depends_on`, `hooks`).

**Reference implementations (packages/ equivalents):**

- `packages/navig-commands-core/` — commands type (was `plugins/navig-commands-core/`)
- `packages/navig-memory/` — commands type (was `plugins/navig-memory/`)
- `packages/navig-windows-automation/` — tools type (was `plugins/navig-windows-automation/`)


---

## 14. Migration from `plugins/` (Deprecated)

The root `plugins/` directory has been removed (superseded by `packages/` + `navig.package.json` schema).
Old `navig.plugin.json` keys: `entrypoint`, `permissions`, `capabilities`.
New `navig.package.json` keys: `entry`, `provides`, `depends_on`, `hooks`.
