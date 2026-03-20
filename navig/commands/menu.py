"""
navig/commands/menu.py — Production-grade adaptive TUI menu.

Routing logic:
  non_interactive=True          → _run_plain_menu()
  cols < 80 / pipe              → _run_plain_menu()
  cols ≥ 80, TTY, Textual OK   → _run_tui_menu()
  Textual unavailable/fails     → _run_plain_menu()

_run_plain_menu() delegates entirely to interactive.py (zero duplication).
_dispatch() maps TUI selections back to interactive.py submenu functions
so all the real logic stays in exactly one place.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Capability detection — single source of truth lives in onboard.py
# ---------------------------------------------------------------------------
from navig.commands.onboard import (
    _auto_install_textual,
    _terminal_supports_tui,
)

# ---------------------------------------------------------------------------
# Menu catalogue — mirrors the three-pillar + DEV INTEL + SYSTEM structure
# that already exists in interactive.py.  "id" keys are the _dispatch() routes.
# ---------------------------------------------------------------------------
MENU_ITEMS: List[Dict[str, str]] = [
    # ── SYSOPS ──────────────────────────────────────────────────────────────
    {"id": "host",        "label": "  Hosts",          "group": "SYSOPS",
     "desc": "Servers, SSH keys, discovery, connectivity"},
    {"id": "files",       "label": "  Files",          "group": "SYSOPS",
     "desc": "Upload, download, browse remote file systems"},
    {"id": "db",          "label": "  Database",       "group": "SYSOPS",
     "desc": "SQL queries, dump/restore, table inspection"},
    {"id": "web",         "label": "  Webserver",      "group": "SYSOPS",
     "desc": "nginx / apache, virtual hosts, SSL"},
    {"id": "docker",      "label": "  Docker",         "group": "SYSOPS",
     "desc": "Containers, images, compose, logs, exec"},
    {"id": "maintenance", "label": "  Maintenance",    "group": "SYSOPS",
     "desc": "System updates, health checks, service control"},
    {"id": "monitoring",  "label": "  Monitoring",     "group": "SYSOPS",
     "desc": "Resources, firewall rules, security audit"},
    # ── DEVOPS ──────────────────────────────────────────────────────────────
    {"id": "apps",        "label": "  Apps",           "group": "DEVOPS",
     "desc": "Application management, deploy, configurations"},
    {"id": "run",         "label": "  Remote Exec",    "group": "DEVOPS",
     "desc": "Run commands on remote hosts via SSH"},
    {"id": "tunnel",      "label": "  Tunnels",        "group": "DEVOPS",
     "desc": "SSH tunnels, port forwarding, SOCKS proxy"},
    {"id": "flow",        "label": "  Flows",          "group": "DEVOPS",
     "desc": "Workflow automation, templates, scheduling"},
    {"id": "local",       "label": "  Local",          "group": "DEVOPS",
     "desc": "Local system info, network, path operations"},
    # ── LIFEOPS ─────────────────────────────────────────────────────────────
    {"id": "agent",       "label": "  Agent",          "group": "LIFEOPS",
     "desc": "Autonomous agent mode, 24/7 gateway, task queues"},
    {"id": "mcp",         "label": "  MCP",            "group": "LIFEOPS",
     "desc": "MCP server management, AI tool integrations"},
    {"id": "ai",          "label": "  AI Assistant",   "group": "LIFEOPS",
     "desc": "Insights, recommendations, ask anything"},
    {"id": "wiki",        "label": "  Wiki",           "group": "LIFEOPS",
     "desc": "Documentation, full-text search, knowledge base"},
    {"id": "backup",      "label": "  Backup",         "group": "LIFEOPS",
     "desc": "Export, import, configuration snapshots"},
    # ── DEV INTEL ───────────────────────────────────────────────────────────
    {"id": "copilot",     "label": "  Copilot",        "group": "DEV INTEL",
     "desc": "Browse, search, export VS Code Copilot sessions"},
    {"id": "memory",      "label": "  Memory",         "group": "DEV INTEL",
     "desc": "Key facts, user profile, AI memory management"},
    # ── SYSTEM ──────────────────────────────────────────────────────────────
    {"id": "config",      "label": "  Config",         "group": "SYSTEM",
     "desc": "Global and project settings, context switching"},
    {"id": "history",     "label": "  History",        "group": "SYSTEM",
     "desc": "Recent commands executed through the menu"},
    {"id": "help",        "label": "  Help",           "group": "SYSTEM",
     "desc": "Keyboard shortcuts and quick reference guide"},
    {"id": "quit",        "label": "  Quit",           "group": "SYSTEM",
     "desc": "Exit navig mission control"},
]

_GROUP_COLORS: Dict[str, str] = {
    "SYSOPS":    "#0ea5e9",
    "DEVOPS":    "#10b981",
    "LIFEOPS":   "#a855f7",
    "DEV INTEL": "#f59e0b",
    "SYSTEM":    "#475569",
}


# ---------------------------------------------------------------------------
# Skill / description preview loader
# ---------------------------------------------------------------------------

def _load_skill_preview(item_id: str) -> str:
    """
    Return a description string for *item_id*.

    Search order:
      1. .agents/skills/<item_id>/SKILL.md  — YAML frontmatter ``description:``
      2. skills/<item_id>/SKILL.md          — same
      3. Static ``desc`` from MENU_ITEMS

    Never raises — always returns a non-empty string.
    """
    for candidate in (
        Path(".agents") / "skills" / item_id / "SKILL.md",
        Path("skills") / item_id / "SKILL.md",
    ):
        try:
            if not candidate.exists():
                continue
            content = candidate.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
            if m:
                dm = re.search(r"^description:\s*(.+)$", m.group(1), re.MULTILINE)
                if dm:
                    return dm.group(1).strip()
            pp = re.search(r"---.*?---\s*(.+?)(\n\n|$)", content, re.DOTALL)
            if pp:
                return pp.group(1).strip()
        except Exception:
            pass
    match = next((item for item in MENU_ITEMS if item["id"] == item_id), None)
    return match["desc"] if match else item_id


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_menu(non_interactive: bool = False) -> None:
    """
    Entry point for ``navig menu``.

    Routes to the full Textual TUI, the Rich plain menu, or direct dispatch
    based on the runtime environment.
    """
    if non_interactive:
        _run_plain_menu()
        return

    if _terminal_supports_tui():
        try:
            _auto_install_textual()  # idempotent no-op if already installed
        except Exception as exc:
            from rich.console import Console
            Console().print(
                f"[yellow]TUI unavailable — falling back to plain menu[/yellow]  "
                f"[dim]({exc})[/dim]"
            )
            _run_plain_menu()
            return

        try:
            import importlib
            importlib.import_module("textual")
        except ImportError:
            _run_plain_menu()
            return

        _run_tui_menu()
        return

    _run_plain_menu()


# ---------------------------------------------------------------------------
# TUI implementation (only reached on cols≥80 TTY with Textual available)
# ---------------------------------------------------------------------------

def _run_tui_menu() -> None:  # noqa: C901
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, ScrollableContainer, Vertical
        from textual.widgets import (
            Footer,
            Header,
            Input,
            Label,
            ListItem,
            ListView,
            Static,
        )
    except ImportError:
        _run_plain_menu()
        return

    class _SectionHeader(Static):
        """Coloured group label injected between sidebar list items."""
        DEFAULT_CSS = """
        _SectionHeader {
            color: #334155;
            text-style: bold;
            padding: 0 2;
            margin-top: 1;
        }
        """

    class NavigMenu(App):  # type: ignore[type-arg]
        TITLE = "navig"
        SUB_TITLE = "Mission Control"
        ENABLE_COMMAND_PALETTE = False

        CSS = """
        Screen { background: #0f172a; }

        #layout {
            layout: grid;
            grid-size: 2;
            grid-columns: 30 1fr;
            height: 100%;
        }

        #sidebar {
            height: 100%;
            border-right: solid #1e3a5f;
            background: #080f1e;
        }

        #brand-area {
            height: 4;
            content-align: center middle;
            color: #22d3ee;
            text-style: bold;
            padding: 0 1;
            border-bottom: solid #1e3a5f;
        }

        #search {
            height: 3;
            background: #111827;
            border: solid #334155;
            color: #22d3ee;
            padding: 0 1;
            display: none;
        }
        #search.active { display: block; }

        #sidebar-inner { height: 1fr; overflow-y: auto; }

        #preview {
            padding: 2 4;
            color: #94a3b8;
            background: #0f172a;
            overflow-y: auto;
            height: 100%;
        }

        ListView { background: #080f1e; border: none; padding: 0; }
        ListItem { padding: 0 2; color: #cbd5e1; background: #080f1e; border: none; }
        ListItem:hover { background: #0f2544; color: #22d3ee; }
        ListItem.--highlight { background: #0d2137; color: #22d3ee; text-style: bold; }

        Footer { background: #080f1e; color: #334155; }
        Header { background: #080f1e; color: #22d3ee; }
        """

        BINDINGS = [
            Binding("j",      "cursor_down",  "Down",   show=True),
            Binding("k",      "cursor_up",    "Up",     show=True),
            Binding("enter",  "select_item",  "Select", show=True),
            Binding("q",      "quit_menu",    "Quit",   show=True),
            Binding("/",      "search",       "Search", show=True),
            Binding("escape", "clear_search", "Clear",  show=False),
            Binding("up",     "cursor_up",    "",       show=False),
            Binding("down",   "cursor_down",  "",       show=False),
        ]

        def __init__(self) -> None:
            super().__init__()

        # ── layout ───────────────────────────────────────────────────────

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="layout"):
                with Vertical(id="sidebar"):
                    yield Static(
                        "[bold #22d3ee]⬡  NAVIG[/bold #22d3ee]"
                        "  [dim #475569]mission control[/dim #475569]",
                        id="brand-area",
                    )
                    yield Input(placeholder=" search…", id="search")
                    with ScrollableContainer(id="sidebar-inner"):
                        for w in self._build_list(MENU_ITEMS):
                            yield w
                yield Static(
                    self._render_preview(MENU_ITEMS[0]),
                    id="preview",
                    markup=True,
                )
            yield Footer()

        # ── sidebar helpers ───────────────────────────────────────────────

        def _build_list(self, items: List[Dict[str, str]]) -> list:
            widgets: list = []
            seen: set = set()
            for item in items:
                grp = item.get("group", "")
                if grp and grp not in seen:
                    seen.add(grp)
                    col = _GROUP_COLORS.get(grp, "#334155")
                    widgets.append(_SectionHeader(f"[{col}]{grp}[/{col}]"))
                widgets.append(
                    ListItem(Label(item["label"]), id=f"mi-{item['id']}")
                )
            return widgets

        # ── preview ───────────────────────────────────────────────────────

        def _render_preview(self, item: Dict[str, str]) -> str:
            grp = item.get("group", "")
            col = _GROUP_COLORS.get(grp, "#22d3ee")
            desc = _load_skill_preview(item["id"])
            sep = "─" * 38
            return (
                f"\n[bold {col}]{item['label'].strip()}[/bold {col}]"
                f"  [dim #475569]{grp}[/dim #475569]\n\n"
                f"[#94a3b8]{desc}[/#94a3b8]\n\n"
                f"[dim #334155]{sep}[/dim #334155]\n\n"
                f"[dim]  [bold]↵ enter[/bold]  launch"
                f"  ·  [bold]j / k[/bold]  navigate"
                f"  ·  [bold]/[/bold]  search"
                f"  ·  [bold]q[/bold]  quit[/dim]"
            )

        # ── lifecycle ────────────────────────────────────────────────────

        def on_mount(self) -> None:
            try:
                self.query_one(ListView).focus()
            except Exception:
                pass

        # ── events ───────────────────────────────────────────────────────

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            if event.item is None:
                return
            item_id = (event.item.id or "").removeprefix("mi-")
            match = next((m for m in MENU_ITEMS if m["id"] == item_id), None)
            if match:
                try:
                    self.query_one("#preview", Static).update(
                        self._render_preview(match)
                    )
                except Exception:
                    pass

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id != "search":
                return
            q = event.value.lower()
            filtered = (
                [m for m in MENU_ITEMS
                 if q in m["label"].lower()
                 or q in m["desc"].lower()
                 or q in m.get("group", "").lower()]
                if q else MENU_ITEMS
            )
            self._rebuild_list(filtered)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """Enter in the search box returns focus to the list."""
            if event.input.id == "search":
                try:
                    self.query_one(ListView).focus()
                except Exception:
                    pass

        # ── actions ──────────────────────────────────────────────────────

        def action_cursor_down(self) -> None:
            try:
                self.query_one(ListView).action_cursor_down()
            except Exception:
                pass

        def action_cursor_up(self) -> None:
            try:
                self.query_one(ListView).action_cursor_up()
            except Exception:
                pass

        def action_select_item(self) -> None:
            try:
                lv = self.query_one(ListView)
                if lv.highlighted_child is None:
                    return
                item_id = (lv.highlighted_child.id or "").removeprefix("mi-")
                self.exit(result=item_id)
            except Exception:
                pass

        def action_quit_menu(self) -> None:
            self.exit(result="quit")

        def action_search(self) -> None:
            try:
                s = self.query_one("#search", Input)
                s.add_class("active")
                s.focus()
            except Exception:
                pass

        def action_clear_search(self) -> None:
            try:
                s = self.query_one("#search", Input)
                s.remove_class("active")
                s.value = ""
                self._rebuild_list(MENU_ITEMS)
                self.query_one(ListView).focus()
            except Exception:
                pass

        # ── helpers ──────────────────────────────────────────────────────

        def _rebuild_list(self, items: List[Dict[str, str]]) -> None:
            try:
                sc = self.query_one("#sidebar-inner", ScrollableContainer)
                sc.remove_children()
                for w in self._build_list(items):
                    sc.mount(w)
            except Exception:
                pass

    result = NavigMenu().run()
    if result and result not in ("quit", None):
        _dispatch(result)


# ---------------------------------------------------------------------------
# Plain-text fallback — delegates entirely to existing interactive.py
# ---------------------------------------------------------------------------

def _run_plain_menu() -> None:
    """
    Delegate to the existing Rich+questionary menu in interactive.py.
    Zero logic duplication — all submenu implementations stay there.
    """
    try:
        from navig.commands.interactive import launch_menu
        launch_menu({})
    except ImportError as exc:
        from rich.console import Console
        con = Console()
        con.print(f"[red]Interactive menu unavailable:[/red] {exc}")
        con.print("[dim]Install Rich:  pip install rich[/dim]")
        sys.exit(1)
    except Exception as exc:
        from rich.console import Console
        Console().print(f"[red]Menu error:[/red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Dispatch — maps TUI id strings → interactive.py submenu functions
# ---------------------------------------------------------------------------

def _dispatch(command: str) -> None:  # noqa: C901
    """
    Route a validated menu selection to the appropriate submenu.

    All real logic lives in interactive.py.  New commands: add to MENU_ITEMS
    above and add a route in the dict below.
    """
    if command == "quit":
        return

    try:
        from navig.commands.interactive import (  # type: ignore[attr-defined]
            MenuState,
            _launch_copilot_sessions,
            _launch_memory_menu,
            execute_remote_command_menu,
            execute_wiki_menu,
            show_agent_gateway_menu,
            show_app_management_menu,
            show_assistant_menu,
            show_backup_menu,
            show_command_history,
            show_configuration_menu,
            show_database_menu,
            show_docker_menu,
            show_file_operations_menu,
            show_flow_menu,
            show_host_management_menu,
            show_local_menu,
            show_maintenance_menu,
            show_mcp_menu,
            show_monitoring_security_menu,
            show_quick_help,
            show_tunnel_menu,
            show_webserver_menu,
        )
        from navig.config import get_config_manager
        state = MenuState(get_config_manager())
    except Exception as exc:
        from rich.console import Console
        Console().print(f"[red]Failed to load menu state:[/red] {exc}")
        return

    routes: Dict[str, Any] = {
        "host":        show_host_management_menu,
        "files":       show_file_operations_menu,
        "db":          show_database_menu,
        "web":         show_webserver_menu,
        "docker":      show_docker_menu,
        "maintenance": show_maintenance_menu,
        "monitoring":  show_monitoring_security_menu,
        "apps":        show_app_management_menu,
        "run":         execute_remote_command_menu,
        "tunnel":      show_tunnel_menu,
        "flow":        show_flow_menu,
        "local":       show_local_menu,
        "agent":       show_agent_gateway_menu,
        "mcp":         show_mcp_menu,
        "ai":          show_assistant_menu,
        "wiki":        execute_wiki_menu,
        "backup":      show_backup_menu,
        "config":      show_configuration_menu,
        "history":     show_command_history,
        "help":        show_quick_help,
        "copilot":     _launch_copilot_sessions,
        "memory":      _launch_memory_menu,
    }

    handler = routes.get(command)
    if handler is None:
        from rich.console import Console
        Console().print(f"[red]Unknown command:[/red] {command}")
        return

    try:
        handler(state)
    except KeyboardInterrupt:
        pass  # Ctrl-C from submenu returns cleanly to caller
    except Exception as exc:
        from rich.console import Console
        Console().print(f"[red]Error in '{command}':[/red] {exc}")
