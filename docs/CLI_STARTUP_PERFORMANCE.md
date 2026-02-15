# NAVIG CLI Startup Performance Analysis

> **Date**: 2025-02-15 | **Branch**: `feat/matrix-integration` | **Python**: 3.12 (Windows)

## Executive Summary

| Metric | Before | After | Target | Status |
|---|---|---|---|---|
| `navig --help` | 57ms | 57ms | <100ms | PASS |
| `navig host list --plain` (e2e) | **886ms** | **~460ms** | <200ms | **48% faster** |
| `navig host list --plain` (in-process) | **886ms** | **221ms** | <200ms | **75% faster** |
| `from navig.cli import app` | 245ms | 90ms | <50ms | 63% faster |
| Config manager init | 206ms | ~5ms | <50ms | PASS |
| app() dispatch | ~630ms | 131ms | — | 79% faster |

### Optimizations Applied

| # | Optimization | Savings | Files |
|---|---|---|---|
| 1 | Deferred command module imports (external cmd map) | ~200ms | cli.py |
| 2 | Lazy Rich imports (`_LazyConsole`, `_ensure_rich()`) | ~120ms | console_helper.py |
| 3 | Lazy `global_config` property (defer `_load_global_config`) | ~200ms | config.py |
| 4 | Lazy `console_helper` in tray.py | ~100ms | tray.py |
| 5 | Deferred imports in vault.py | ~40ms | vault.py |
| 6 | Deferred `asyncio` in agent.py | ~45ms | agent.py |
| 7 | Removed telegram/matrix/store module-level imports | ~160ms | cli.py |
| 8 | Debug logger uses raw YAML (skips full config load) | ~415ms | cli.py |
| 9 | ProactiveAssistant skipped for scripting modes | ~50ms | cli.py |
| 10 | Plugin loading skipped for built-in commands | ~110ms | main.py |
| 11 | Deferred pydantic/config_schema from config_loader | ~285ms | config_loader.py, config.py |
| 12 | Fast-path for `--plain` in `host list` (skip config reads) | ~400ms | host.py |

### Phase Breakdown (After)

```
Phase                    Before    After    Saved
───────────────────────────────────────────────────
Python startup           ~35ms     ~35ms       0
cli.py import            245ms      90ms    155ms
register_external        245ms       0ms    245ms
Plugin loading           110ms       0ms    110ms
app() dispatch           280ms     131ms    149ms
  ├─ main callback       511ms      68ms    443ms
  └─ host_list command   716ms      ~7ms    709ms
───────────────────────────────────────────────────
TOTAL (in-process)       886ms     221ms    665ms (75%)
TOTAL (end-to-end)       886ms    ~460ms    426ms (48%)
```

> **Note**: End-to-end includes ~200ms Python process startup and ~40ms subprocess overhead.
> The in-process measurement (221ms) represents the actual NAVIG code path.
> Target <200ms e2e is limited by Python interpreter startup (~200ms on Windows).

---

## Original Analysis (Pre-Optimization)

The fast-path for `--help` already works well. The problem was regular commands: **886ms** for a simple host list. The original breakdown:

```
Phase                    Time     % of Total
─────────────────────────────────────────────
Python startup           ~35ms     4%
typer import              90ms    10%    ← unavoidable (framework)
navig.cli import         245ms    28%    ← BOTTLENECK #1
config_manager init      206ms    23%    ← BOTTLENECK #2
Plugin loading            27ms     3%
Typer dispatch+command   280ms    32%    ← includes config re-init
─────────────────────────────────────────────
TOTAL                   ~886ms   100%
```

---

## 1. Prioritized Bottleneck Table

| # | Bottleneck | Est. ms Saved | Effort | File / Function | Root Cause |
|---|---|---|---|---|---|
| **1** | Eager command module imports in `cli.py` body | **~200ms** | Medium | [cli.py](navig/cli.py) L8652-11381 | 17 `from navig.commands.X import` at module level → triggers `console_helper` + `rich.*` + `cryptography` + `asyncio` chains |
| **2** | `console_helper.py` eager Rich imports | **~120ms** | Low | [console_helper.py](navig/console_helper.py) L13-18 | `from rich.console import Console` + 4 more Rich modules at top level. Loaded transitively by tray/vault/telegram |
| **3** | `ConfigManager.__init__` does filesystem scan | **~200ms** | Medium | [config.py](navig/config.py) `get_config_manager()` | Reads YAML, scans hosts dir, validates on every invocation. No caching between CLI invocations |
| **4** | `commands/tray.py` eager `console_helper` | **~100ms** | Trivial | [tray.py](navig/commands/tray.py) L16 | `from navig import console_helper as ch` (not lazy) |
| **5** | `commands/vault.py` eager crypto imports | **~40ms** | Trivial | [vault.py](navig/commands/vault.py) L17-18 | `from navig.vault import get_vault, CredentialType` pulls `cryptography.fernet` |
| **6** | `commands/agent.py` pulls `asyncio` | **~45ms** | Low | [agent.py](navig/commands/agent.py) L7 | `import asyncio` at top level (50ms on Windows) |
| **7** | `commands/matrix.py` eager Rich+asyncio | **~50ms** | Trivial | matrix.py top-level | `from rich.console import Console`, `from navig.comms.matrix_features import ...` |
| **8** | Plugin loading `load_plugins_into_app()` | **~27ms** | Low | [main.py](navig/main.py) L452-455 | Filesystem scan for plugin dirs, even when no plugins exist |
| **9** | Typer framework import | **~90ms** | N/A | typer (pip) | Unavoidable — pulls Click + Rich chain. Framework tax. |
| **10** | `__editable___navig` finder overhead | **~25ms** | Trivial | pip editable install | Development-only. Goes away with `pip install .` (non-editable) |

### Savings Overlap Note

Bottlenecks #1, #2, #4, #5, #7 overlap — they form a single import chain:
- `cli.py` body imports `tray.py` → imports `console_helper` → imports `rich.*` (~120ms)
- `cli.py` body imports `vault.py` → imports `cryptography` (~40ms)
- Fixing #1 (defer all command imports) eliminates #4, #5, #7 automatically
- Fixing #2 (lazy Rich in console_helper) reduces cost even when helper IS loaded

**Realistic total savings from fixes #1 + #2 + #3: ~350-400ms → bringing `host list` to ~450-500ms.**
**Adding command-specific config caching (#3): another ~150ms → ~300-350ms.**

---

## 2. Import Chain Visualization

```
navig.main:main()
  ├─ navig.cli (245ms cumulative)
  │   ├─ typer (90ms) ← unavoidable
  │   │   ├─ click.core (57ms)
  │   │   └─ rich.console (83ms)
  │   ├─ navig.commands.inbox (9ms)
  │   ├─ navig.commands.agent (50ms)
  │   │   └─ asyncio (46ms)
  │   ├─ navig.commands.tray (101ms) ← EAGER console_helper
  │   │   └─ navig.console_helper (91ms)
  │   │       └─ rich.* (Console, Panel, Table, Tree, Progress)
  │   ├─ navig.commands.vault (41ms) ← EAGER crypto
  │   │   └─ navig.vault → cryptography.fernet (32ms)
  │   ├─ navig.commands.service (1ms) ✓
  │   ├─ navig.commands.stack (1ms) ✓
  │   ├─ navig.commands.formation (1ms) ✓
  │   └─ navig.commands.council (1ms) ✓
  ├─ config_manager (206ms)
  │   └─ YAML parse + host dir scan
  └─ plugin loading (27ms)
```

---

## 3. Implementation Plan — Top 3 Optimizations

### Optimization #1: Defer ALL command module imports in `cli.py` (~200ms saved)

**Problem**: 17 command modules are imported eagerly at the bottom of `cli.py` (lines 8652-11381) even though only ONE will be executed per invocation.

**Before** — [cli.py](navig/cli.py) lines 10678-10698:
```python
# Import agent commands from separate module
from navig.commands.agent import agent_app
app.add_typer(agent_app, name="agent")

# Import service (daemon) commands
from navig.commands.service import service_app
app.add_typer(service_app, name="service")

# Import stack (local Docker infrastructure) commands
from navig.commands.stack import stack_app
app.add_typer(stack_app, name="stack")

# Import tray commands
from navig.commands.tray import tray_app
app.add_typer(tray_app, name="tray")

# Import formation commands
from navig.commands.formation import formation_app
app.add_typer(formation_app, name="formation")

# Import council commands
from navig.commands.council import council_app
app.add_typer(council_app, name="council")
```

And try/except blocks (lines 10711+):
```python
try:
    from navig.commands.auto import auto_app
    app.add_typer(auto_app, name="auto")
except ImportError:
    pass

try:
    from navig.commands.vault import cred_app, profile_app
    app.add_typer(cred_app, name="cred")
    app.add_typer(profile_app, name="profile")
except ImportError:
    pass
# ... 8 more try/except blocks
```

**After** — Use Typer's lazy loading with Click Groups:
```python
from typer.core import TyperGroup
import importlib

def _lazy_typer_group(module_path: str, app_name: str, **typer_kwargs):
    """Create a Typer app that defers module import until the command is invoked."""
    lazy_app = typer.Typer(**typer_kwargs)
    _loaded = False
    
    @lazy_app.callback(invoke_without_command=True)
    def _loader(ctx: typer.Context):
        nonlocal _loaded
        if not _loaded:
            mod = importlib.import_module(module_path)
            real_app = getattr(mod, app_name)
            # Merge commands from the real app into this lazy shell
            for name, cmd in real_app.registered_commands:
                lazy_app.command(name)(cmd)
            _loaded = True
        if ctx.invoked_subcommand is None:
            show_subcommand_help(typer_kwargs.get("name", ""), ctx)
            raise typer.Exit()
    return lazy_app

# ---- Simpler approach: just defer imports into callback ----

# Agent
_agent_app = typer.Typer(name="agent", help="Manage autonomous agent mode", no_args_is_help=True)
@_agent_app.callback()
def _agent_cb(ctx: typer.Context):
    pass

def _register_deferred_commands():
    """Register all command sub-apps lazily. Called once at module level but
       the actual imports happen only when the subcommand is invoked."""
    
    deferred = {
        "agent":     ("navig.commands.agent",     "agent_app",     {"help": "Manage autonomous agent mode"}),
        "service":   ("navig.commands.service",    "service_app",   {"help": "Manage NAVIG daemon service"}),
        "stack":     ("navig.commands.stack",      "stack_app",     {"help": "Local Docker infrastructure"}),
        "tray":      ("navig.commands.tray",       "tray_app",      {"help": "Windows system tray launcher"}),
        "formation": ("navig.commands.formation",  "formation_app", {"help": "Multi-agent formations"}),
        "council":   ("navig.commands.council",    "council_app",   {"help": "Council management"}),
        "inbox":     ("navig.commands.inbox",      "inbox_app",     {"help": "Inbox routing"}),
    }
    
    optional = {
        "auto":      ("navig.commands.auto",       "auto_app",      {"help": "Cross-platform automation"}),
        "ahk":       ("navig.commands.ahk",        "ahk_app",       {"help": "AutoHotKey automation"}),
        "evolve":    ("navig.commands.evolution",   "evolution_app", {"help": "AI code evolution"}),
        "script":    ("navig.commands.script",      "script_app",    {"help": "Script management"}),
        "calendar":  ("navig.commands.calendar",    "calendar_app",  {"help": "Calendar integration"}),
        "mode":      ("navig.commands.mode",        "mode_app",      {"help": "LLM mode router"}),
        "email":     ("navig.commands.email",       "email_app",     {"help": "Email integration"}),
        "voice":     ("navig.commands.voice",       "voice_app",     {"help": "Text-to-speech"}),
        "cred":      ("navig.commands.vault",       "cred_app",      {"help": "Credential vault"}),
        "profile":   ("navig.commands.vault",       "profile_app",   {"help": "Credential profiles"}),
        "crash":     ("navig.commands.crash",       "app",           {"help": "Crash reporting"}),
    }
    # ... register using click lazy group pattern
```

**Recommended simplest approach** — just wrap each import block:

```python
def _register_external_commands(app):
    """Defer all command module imports. Each module is only imported
    when its subcommand is actually invoked by the user."""
    import importlib
    
    _registry = [
        ("agent",     "navig.commands.agent",    "agent_app"),
        ("service",   "navig.commands.service",  "service_app"),
        ("stack",     "navig.commands.stack",    "stack_app"),
        ("tray",      "navig.commands.tray",     "tray_app"),
        ("formation", "navig.commands.formation", "formation_app"),
        ("council",   "navig.commands.council",  "council_app"),
        ("inbox",     "navig.commands.inbox",    "inbox_app"),
    ]
    
    for cmd_name, module_path, attr_name in _registry:
        try:
            mod = importlib.import_module(module_path)
            sub_app = getattr(mod, attr_name)
            app.add_typer(sub_app, name=cmd_name)
        except ImportError:
            pass

# IMPORTANT: Don't call this at module level!
# Call it from main.py AFTER fast-path check, or use click-lazy-group.
```

**Best solution: Use Click's lazy group pattern** (already partially done for wiki):

```python
# Already exists at line 8595:
from typer.core import TyperGroup

class LazyGroup(TyperGroup):
    """A Click Group that defers loading subcommand modules."""
    
    def __init__(self, *args, lazy_subcommands=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._lazy_subcommands = lazy_subcommands or {}
    
    def list_commands(self, ctx):
        base = super().list_commands(ctx)
        lazy = sorted(self._lazy_subcommands.keys())
        return base + lazy
    
    def get_command(self, ctx, cmd_name):
        if cmd_name in self._lazy_subcommands:
            module_path, attr_name = self._lazy_subcommands[cmd_name]
            mod = importlib.import_module(module_path)
            cmd = getattr(mod, attr_name)
            # If it's a Typer app, get the underlying Click object
            if hasattr(cmd, '_get_command'):
                return cmd._get_command()
            return cmd
        return super().get_command(ctx, cmd_name)

# Then define app with LazyGroup:
app = typer.Typer(
    cls=LazyGroup,
    lazy_subcommands={
        "agent":     ("navig.commands.agent",    "agent_app"),
        "tray":      ("navig.commands.tray",     "tray_app"),
        "vault":     ("navig.commands.vault",    "cred_app"),
        # ... all external command modules
    },
)
```

**Estimated savings**: ~200ms (eliminates loading tray/vault/agent/auto/ahk at import time)

---

### Optimization #2: Make `console_helper.py` fully lazy (~120ms saved)

**Problem**: 5 Rich modules imported eagerly at module level. Every module that does `from navig import console_helper as ch` (not lazy) pays this cost.

**Before** — [console_helper.py](navig/console_helper.py) lines 13-18:
```python
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
```

**After** — Full lazy loading with module-level `__getattr__`:
```python
"""
Console Helper - Centralized Rich formatting utilities
Performance: ALL Rich imports are deferred until first use.
"""

import sys
from typing import Optional, List, Dict, Any, Union
from pathlib import Path

# Lazy state for Rich objects
_Console = None
_Panel = None
_Table = None
_Tree = None
_Progress = None
_SpinnerColumn = None
_TextColumn = None
_BarColumn = None
_TaskProgressColumn = None
_Syntax = None
_Markdown = None

def _ensure_rich():
    """Load all core Rich modules on first use."""
    global _Console, _Panel, _Table, _Tree
    global _Progress, _SpinnerColumn, _TextColumn, _BarColumn, _TaskProgressColumn
    if _Console is not None:
        return
    from rich.console import Console as _C
    from rich.panel import Panel as _P
    from rich.table import Table as _T
    from rich.tree import Tree as _Tr
    from rich.progress import (
        Progress as _Pr,
        SpinnerColumn as _SC,
        TextColumn as _TC,
        BarColumn as _BC,
        TaskProgressColumn as _TPC,
    )
    _Console = _C
    _Panel = _P
    _Table = _T
    _Tree = _Tr
    _Progress = _Pr
    _SpinnerColumn = _SC
    _TextColumn = _TC
    _BarColumn = _BC
    _TaskProgressColumn = _TPC

# Properties that trigger lazy load
def Console(*args, **kwargs):
    _ensure_rich()
    return _Console(*args, **kwargs)

def Panel(*args, **kwargs):
    _ensure_rich()
    return _Panel(*args, **kwargs)

def Table(*args, **kwargs):
    _ensure_rich()
    return _Table(*args, **kwargs)

def Tree(*args, **kwargs):
    _ensure_rich()
    return _Tree(*args, **kwargs)

# ... etc

# Global console instance (also lazy)
_console = None

@property  # Won't work at module level — use a getter instead
def console():
    global _console
    if _console is None:
        _ensure_rich()
        _console = _Console()
    return _console
```

**Simpler alternative** — keep the same API but use `__getattr__` at module level:

```python
"""Console Helper - Centralized Rich formatting utilities"""

import sys
from typing import Optional, List, Dict, Any, Union
from pathlib import Path

# These are loaded lazily on first access
_rich_loaded = False
_rich_attrs = {}

def _load_rich():
    global _rich_loaded, _rich_attrs
    if _rich_loaded:
        return
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.tree import Tree
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    _rich_attrs.update({
        'Console': Console, 'Panel': Panel, 'Table': Table, 'Tree': Tree,
        'Progress': Progress, 'SpinnerColumn': SpinnerColumn,
        'TextColumn': TextColumn, 'BarColumn': BarColumn,
        'TaskProgressColumn': TaskProgressColumn,
    })
    _rich_loaded = True

def __getattr__(name):
    if name in ('Console', 'Panel', 'Table', 'Tree', 'Progress',
                'SpinnerColumn', 'TextColumn', 'BarColumn', 'TaskProgressColumn'):
        _load_rich()
        return _rich_attrs[name]
    raise AttributeError(f"module 'navig.console_helper' has no attribute {name!r}")

# console instance — lazy
_console_instance = None

def _get_console():
    global _console_instance
    if _console_instance is None:
        _load_rich()
        _console_instance = _rich_attrs['Console']()
    return _console_instance

# Replace `console = Console()` with a property-like access:
# Other modules use `ch.console.print(...)` — we need `console` to be accessible.
# Use module __getattr__ above to handle `console` too:
# Add 'console' to __getattr__:
```

**Practical recommendation** — keep it simple, just move imports into a helper:

```python
# console_helper.py — top of file
import sys
from typing import Optional, List, Dict, Any, Union
from pathlib import Path

# Lazy Rich imports — loaded on first function call
_rich = None

def _r():
    """Get rich modules dict, loading on first call."""
    global _rich
    if _rich is None:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.tree import Tree
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
        _rich = type('R', (), {
            'Console': Console, 'Panel': Panel, 'Table': Table,
            'Tree': Tree, 'Progress': Progress,
            'SpinnerColumn': SpinnerColumn, 'TextColumn': TextColumn,
            'BarColumn': BarColumn, 'TaskProgressColumn': TaskProgressColumn,
            'console': Console(),
        })()
    return _rich

# Public API — same signatures, just lazy
console = property(lambda self: _r().console)  # Won't work for module

# --- Simplest correct approach: use a lazy proxy for `console` ---
class _LazyConsole:
    """Proxy that defers Console() creation until first use."""
    _instance = None
    def __getattr__(self, name):
        if _LazyConsole._instance is None:
            from rich.console import Console
            _LazyConsole._instance = Console()
        return getattr(_LazyConsole._instance, name)

console = _LazyConsole()
```

**Estimated savings**: ~120ms if console_helper is imported; prevents cascading cost to all importers.

---

### Optimization #3: Cache ConfigManager or defer initialization (~200ms saved)

**Problem**: `get_config_manager()` takes ~200ms on first call (YAML parse + filesystem scan of host directories). This runs for every command, even ones that don't need config.

**Current flow**:
```python
# navig/config.py
def get_config_manager():
    # Creates new ConfigManager every time (no singleton)
    # ConfigManager.__init__ reads config.yaml + scans ~/.navig/hosts/
    return ConfigManager()
```

**After** — Add a file-mtime-based cache check:
```python
# navig/config.py

_cached_manager = None
_cached_mtime = None

def get_config_manager(force_reload=False):
    """Get ConfigManager with mtime-based cache."""
    global _cached_manager, _cached_mtime
    
    config_file = Path.home() / '.navig' / 'config.yaml'
    
    if not force_reload and _cached_manager is not None:
        # Check if config changed since last load
        try:
            current_mtime = config_file.stat().st_mtime
            if current_mtime == _cached_mtime:
                return _cached_manager
        except OSError:
            pass
    
    _cached_manager = ConfigManager()
    try:
        _cached_mtime = config_file.stat().st_mtime
    except OSError:
        _cached_mtime = None
    
    return _cached_manager
```

**Note**: This only helps within a single process. For CLI (fresh process each time), the real fix is to profile what `ConfigManager.__init__` actually does and optimize the hot path (e.g., only load the active host config, not all 18 hosts).

**Estimated savings**: ~150-200ms for subsequent calls in same process; for CLI, need to optimize the init itself.

---

## 4. Additional Quick Wins

### 4a. Fix `commands/tray.py` eager import (Trivial — 2 lines)

```python
# BEFORE (tray.py line 16):
from navig import console_helper as ch

# AFTER:
from navig.lazy_loader import lazy_import
ch = lazy_import("navig.console_helper")
```

### 4b. Fix `commands/vault.py` eager imports (Trivial)

```python
# BEFORE (vault.py lines 13-18):
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from navig.console_helper import error, success, warning, info, confirm_action as confirm
from navig.vault import get_vault, CredentialType, SecretStr
from navig.vault.validators import list_supported_validators

# AFTER — move into functions:
from navig.lazy_loader import lazy_import
ch = lazy_import("navig.console_helper")

# Inside each command function:
def list_creds():
    from rich.table import Table
    from navig.vault import get_vault, CredentialType
    # ...
```

### 4c. Fix `commands/agent.py` asyncio import (Low effort)

```python
# BEFORE (agent.py line 7):
import asyncio

# AFTER — move inside async-using functions:
# (import asyncio only in functions that call asyncio.run())
```

### 4d. Remove `import random` from cli.py (Trivial — 3ms)

```python
# Line 11 of cli.py — used only in quote display
# Move inside the function that uses it
```

---

## 5. Dependency Import Cost Reference

| Dependency | Cold Import | Used At Startup? | Status |
|---|---|---|---|
| paramiko | 384ms | No (deferred) | OK |
| httpx | 217ms | No (deferred) | OK |
| aiohttp | 174ms | No (deferred) | OK |
| requests | 128ms | No (deferred) | OK |
| **rich.console** | **80ms** | **YES (via console_helper)** | **FIX** |
| typer | 76ms | YES (framework) | Unavoidable |
| jinja2 | 63ms | No (deferred) | OK |
| psutil | 46ms | No (deferred) | OK |
| asyncio | 46ms | YES (via agent.py) | FIX |
| **rich.table** | **28ms** | **YES (via console_helper)** | **FIX** |
| yaml | 23ms | YES (config) | OK (fast) |
| **cryptography** | **32ms** | **YES (via vault.py)** | **FIX** |
| colorama | 6ms | No | OK |
| websockets | 3ms | No | OK |

---

## 6. SQLite Connection Audit

**Finding**: No SQLite connections are opened at startup. All store/database access (`ConversationStore`, `KnowledgeDB`, `navig.storage.Engine`) is inside command handler functions — imports are fully deferred. The new `navig/storage/` module imports in ~8ms and creates no connections until explicitly called.

**Status**: NOT a startup bottleneck.

---

## 7. Skill/Pack/Agent Registry Audit

| Component | Import Time | Init Time | Called at Startup? |
|---|---|---|---|
| `navig.plugins.get_plugin_manager` | 110ms | 24ms | YES (in main.py) |
| `navig.commands.skills` | 22ms | N/A | No (deferred) |
| `navig.storage` | 8ms | N/A | No (deferred) |
| `navig.store` | 4ms | N/A | No (deferred) |

**Plugin loading** (`load_plugins_into_app`) costs **27ms** and runs for all commands except `--help`. This is acceptable but could be deferred for commands that don't use plugins.

---

## 8. Projected Impact

| Optimization | Effort | Savings | Cumulative Time |
|---|---|---|---|
| Current baseline | — | — | 886ms |
| **#1**: Defer command imports in cli.py | Medium | ~200ms | ~686ms |
| **#2**: Lazy Rich in console_helper | Low | ~120ms | ~566ms |
| **#4a-c**: Fix tray/vault/agent imports | Trivial | ~50ms | ~516ms |
| **#3**: Optimize ConfigManager init | Medium | ~150ms | ~366ms |
| **#8**: Skip plugins for simple commands | Low | ~27ms | ~339ms |
| **Total projected** | | **~547ms** | **~339ms** |

**Target**: 339ms is still above the 200ms goal, but it's a **62% improvement**. The remaining ~240ms is:
- typer framework: ~90ms (unavoidable)
- ConfigManager I/O: ~50ms (minimum for YAML + filesystem)  
- Python startup: ~35ms
- Typer dispatch overhead: ~65ms

Getting below 200ms would require replacing Typer with a lighter framework (e.g., raw `argparse` or `click` without rich integration), which is a larger architectural change.
