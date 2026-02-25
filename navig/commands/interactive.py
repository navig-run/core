"""
Interactive Menu System for NAVIG

Terminal UI alternative using Rich library - Mr. Robot inspired theme.
The Schema provides. The interface adapts. The void watches.
"""

import sys
import os
import subprocess
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from datetime import datetime

# Rich components for terminal UI
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box

# NAVIG components
from navig.config import get_config_manager, ConfigManager

# Import existing command functions for reuse
from navig.commands import host, app, database, tunnel, files, webserver, backup, maintenance, monitoring, db

# Initialize console with Mr. Robot theme
console = Console()

# Mr. Robot color scheme
COLORS = {
    'primary': 'bright_cyan',        # Active/selected  — ocean primary
    'secondary': 'cyan',             # Normal text and borders
    'accent': 'bright_blue',         # Accent items      — deep ocean
    'success': 'bright_green',       # Success status
    'error': 'bright_red',           # Errors
    'warning': 'yellow',             # Warnings
    'dim': 'dim white',              # Help text
    'info': 'bright_cyan',           # Information
    'action': 'bright_blue',         # Action prompts    — deep ocean
}

# Questionary - lazy import to avoid Windows resource issues
QUESTIONARY_AVAILABLE = False
MENU_STYLE = None

def _init_questionary():
    """Lazy initialization of questionary (only when needed)."""
    global QUESTIONARY_AVAILABLE, MENU_STYLE
    
    if QUESTIONARY_AVAILABLE or MENU_STYLE is not None:
        return  # Already initialized
    
    try:
        import questionary
        from questionary import Style
        
        QUESTIONARY_AVAILABLE = True
        # Questionary uses ANSI color names, not Rich color names
        MENU_STYLE = Style([
            ('qmark', 'fg:cyan bold'),             # Question mark
            ('question', 'fg:cyan bold'),          # Question text
            ('answer', 'fg:cyan bold'),            # Selected answer
            ('pointer', 'fg:ansiblue bold'),       # Pointer arrow
            ('highlighted', 'fg:cyan bold'),       # Highlighted choice
            ('selected', 'fg:ansiblue'),           # Selected text
            ('separator', 'fg:white'),             # Separator
            ('instruction', 'fg:white'),           # Instructions
            ('text', 'fg:cyan'),                   # Normal text
        ])
    except (ImportError, Exception):
        # Questionary not available or failed to initialize
        QUESTIONARY_AVAILABLE = False
        MENU_STYLE = None
        # Silently fail - will use number-based fallback


class CommandHistory:
    """Track recent commands executed through the menu."""
    
    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.commands: List[Dict[str, Any]] = []
    
    def add(self, command: str, description: str, success: bool = True):
        """Add a command to history."""
        entry = {
            'command': command,
            'description': description,
            'timestamp': datetime.now(),
            'success': success,
        }
        self.commands.insert(0, entry)  # Add to front
        if len(self.commands) > self.max_size:
            self.commands.pop()  # Remove oldest
    
    def get_recent(self, count: int = 5) -> List[Dict[str, Any]]:
        """Get most recent commands."""
        return self.commands[:count]
    
    def clear(self):
        """Clear command history."""
        self.commands.clear()


class MenuState:
    """Manages menu navigation state and context."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.menu_stack: List[str] = []
        self.active_host: Optional[str] = config_manager.get_active_host()
        self.active_app: Optional[str] = config_manager.get_active_app()
        self.history = CommandHistory()
        self.last_selections: Dict[str, Any] = {}  # Remember user choices
        
        # Terminal info
        self.terminal_width = console.width
        self.terminal_height = console.height
    
    def push_menu(self, menu_name: str):
        """Navigate to a submenu."""
        self.menu_stack.append(menu_name)
    
    def pop_menu(self) -> Optional[str]:
        """Return to previous menu."""
        if self.menu_stack:
            return self.menu_stack.pop()
        return None
    
    def current_menu(self) -> Optional[str]:
        """Get current menu name."""
        return self.menu_stack[-1] if self.menu_stack else None
    
    def refresh_context(self):
        """Refresh active host/app from config."""
        self.active_host = self.config_manager.get_active_host()
        self.active_app = self.config_manager.get_active_app()


def clear_screen():
    """Clear terminal screen without using shell=True for security."""
    try:
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'cls'], check=False)
        else:
            subprocess.run(['clear'], check=False)
    except Exception:
        # Fallback: just print newlines if subprocess fails
        print('\n' * 100)


def show_header(state: MenuState):
    """Display banner with current context and timestamp."""
    from navig import __version__
    
    # Clean, professional banner
    banner = f"""[cyan]╔═══════════════════════════════════════════════════════════╗
║         [bright_green]NAVIG Command Center[/bright_green]  [dim]v{__version__}[/dim]                  ║
╚═══════════════════════════════════════════════════════════╝[/cyan]"""
    console.print(banner)

    # Get context with source information
    context_line = ""
    host_info = ""
    app_info = ""
    
    if state.active_host:
        # Try to get host IP from config
        try:
            host_config = state.config_manager.get_host_config(state.active_host)
            host_ip = host_config.get('host', host_config.get('ip', ''))
            if host_ip:
                host_info = f"[{COLORS['primary']}]{state.active_host}[/] [dim]({host_ip})[/dim]"
            else:
                host_info = f"[{COLORS['primary']}]{state.active_host}[/]"
        except Exception:
            host_info = f"[{COLORS['primary']}]{state.active_host}[/]"

    if state.active_app:
        app_info = f"[{COLORS['accent']}]{state.active_app}[/]"

    # Build context line
    if host_info and app_info:
        context_line = f"📍 Context: {host_info} → {app_info}"
    elif host_info:
        context_line = f"📍 Context: {host_info} [dim](no app selected)[/dim]"
    else:
        context_line = f"[{COLORS['dim']}]📍 Context: No active host (run 'navig host use <name>' to set)[/]"

    # Timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    console.print(f"{context_line}   [dim]🕐 {timestamp}[/dim]")
    console.print()


def show_status(message: str, status: str = 'info'):
    """Display status message with appropriate icon and color."""
    icons = {
        'info': '[*]',
        'success': '[+]',
        'warning': '[!]',
        'error': '[x]',
        'action': '[>]',
        'loading': '[~]',
        'removed': '[-]',
    }
    
    colors = {
        'info': COLORS['info'],
        'success': COLORS['success'],
        'warning': COLORS['warning'],
        'error': COLORS['error'],
        'action': COLORS['primary'],
        'loading': COLORS['accent'],
        'removed': COLORS['warning'],  # Yellow for removal operations
    }
    
    icon = icons.get(status, '[*]')
    color = colors.get(status, COLORS['secondary'])
    
    console.print(f"[{color}]{icon} {message}[/{color}]")


def create_menu_table(title: str, items: List[Tuple[str, str]], show_keys: bool = True) -> Table:
    """Create a formatted menu table."""
    table = Table(
        title=f"[{COLORS['primary']}]{title}[/{COLORS['primary']}]",
        box=box.ROUNDED,
        style=COLORS['secondary'],
        show_header=False,
        padding=(0, 2),
    )
    
    table.add_column("Key", style=COLORS['accent'], width=6 if show_keys else 0)
    table.add_column("Option", style=COLORS['secondary'])
    
    for idx, (key, option) in enumerate(items, 1):
        display_key = f"[{key}]" if show_keys else ""
        table.add_row(display_key, option)
    
    return table


def prompt_selection(options: List[str], message: str = "Select option", 
                     allow_back: bool = True) -> Optional[str]:
    """Prompt user to select from options using questionary or fallback."""
    # Try to initialize questionary (lazy load)
    _init_questionary()
    
    if QUESTIONARY_AVAILABLE:
        # Use questionary for better UX
        try:
            import questionary
            
            choices = options.copy()
            if allow_back:
                choices.append("← Back")
            
            result = questionary.select(
                message,
                choices=choices,
                style=MENU_STYLE,
                use_arrow_keys=True,
                use_shortcuts=True,
            ).ask()
            
            if result == "← Back" or result is None:
                return None
            return result
        except (KeyboardInterrupt, EOFError):
            return None
        except Exception as e:
            # If questionary fails on Windows, fall back to number selection
            console.print(f"[{COLORS['dim']}]Note: Using fallback menu (questionary error: {e})[/{COLORS['dim']}]")
            pass  # Fall through to number-based selection
    
    # Fallback to number-based selection
    console.print(f"\n[{COLORS['accent']}]{message}:[/{COLORS['accent']}]")
    for idx, option in enumerate(options, 1):
        console.print(f"  [{COLORS['accent']}][{idx}][/{COLORS['accent']}] {option}")
    
    if allow_back:
        console.print(f"  [{COLORS['dim']}][0] ← Back[/{COLORS['dim']}]")
    
    while True:
        try:
            choice = Prompt.ask(
                f"[{COLORS['action']}]Enter choice[/{COLORS['action']}]",
                default="0" if allow_back else "1"
            )
            
            if choice == "0" and allow_back:
                return None
            
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
            else:
                show_status("Invalid choice. Try again.", 'error')
        except (ValueError, KeyboardInterrupt, EOFError):
            if allow_back:
                return None
            show_status("Invalid input. Try again.", 'error')


def prompt_menu_choice(options: List[Tuple[str, str]], title: str) -> Optional[str]:
    """Prompt for menu selection with arrow keys or number input.
    
    Args:
        options: List of (number, description) tuples
        title: Menu title
    
    Returns:
        Selected description or None
    """
    # Try arrow key navigation first
    _init_questionary()
    
    if QUESTIONARY_AVAILABLE:
        try:
            import questionary
            
            # Create choices with number prefix for clarity
            choices = [f"[{num}] {desc}" for num, desc in options]
            
            result = questionary.select(
                title,
                choices=choices,
                style=MENU_STYLE,
                use_arrow_keys=True,
                qmark=">",
            ).ask()
            
            if result is None:
                return None
            
            # Extract the description (remove "[X] " prefix)
            for num, desc in options:
                if result == f"[{num}] {desc}":
                    return desc
            
            return None
            
        except (KeyboardInterrupt, EOFError):
            return None
        except Exception:
            # Fall through to number input
            pass
    
    # Fallback: number input only
    while True:
        try:
            choice = Prompt.ask(
                f"[{COLORS['action']}]Select option[/{COLORS['action']}]",
                default="0"
            )
            
            # Find matching option by number
            for num, desc in options:
                if choice == num:
                    return desc
            
            # Special case for exit/back
            if choice == "0":
                for num, desc in options:
                    if num == "0":
                        return desc
                return None
            
            show_status("Invalid choice. Try again.", 'error')
            
        except (KeyboardInterrupt, EOFError):
            return None


def show_status_dashboard(state: MenuState):
    """Display a compact status dashboard."""
    
    # Build status indicators
    status_items = []
    
    # Host connection status
    if state.active_host:
        try:
            host_config = state.config_manager.get_host_config(state.active_host)
            host_ip = host_config.get('host', host_config.get('ip', ''))
            status_items.append(f"[{COLORS['success']}]●[/{COLORS['success']}] Host: {state.active_host} ({host_ip})")
        except Exception:
            status_items.append(f"[{COLORS['success']}]●[/{COLORS['success']}] Host: {state.active_host}")
    else:
        status_items.append(f"[{COLORS['dim']}]○[/{COLORS['dim']}] Host: [dim]not set[/dim]")
    
    # App status
    if state.active_app:
        status_items.append(f"[{COLORS['success']}]●[/{COLORS['success']}] App: {state.active_app}")
    else:
        status_items.append(f"[{COLORS['dim']}]○[/{COLORS['dim']}] App: [dim]not set[/dim]")
    
    # Recent command
    recent = state.history.get_recent(1)
    if recent:
        last_cmd = recent[0]
        status_icon = "[+]" if last_cmd['success'] else "[x]"
        status_color = COLORS['success'] if last_cmd['success'] else COLORS['error']
        desc = last_cmd['description'][:25] + "..." if len(last_cmd['description']) > 25 else last_cmd['description']
        status_items.append(f"[{status_color}]{status_icon}[/{status_color}] Last: {desc}")
    
    # Print dashboard in a single line
    console.print(f"  {' │ '.join(status_items)}")
    console.print()


def show_main_menu(state: MenuState) -> Optional[str]:
    """Display main menu with three-pillar organization."""
    clear_screen()
    show_header(state)
    
    # Optional: Show compact status dashboard
    show_status_dashboard(state)

    # Check if .navig/ directory exists in current directory
    from pathlib import Path
    navig_dir = Path.cwd() / ".navig"
    
    # Three-pillar menu structure with visual separators
    # Note: questionary doesn't render Rich markup, so descriptions use plain parentheses
    console.print(f"[{COLORS['primary']}]━━━ SYSOPS (Infrastructure) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['primary']}]")
    
    sysops_options = [
        ("1", "Host Management         (servers, SSH, discovery)"),
        ("2", "File Operations         (upload, download, browse)"),
        ("3", "Database Operations     (SQL, backup, restore)"),
        ("4", "Webserver Control       (nginx, apache, vhosts)"),
        ("5", "Docker Management       (containers, images, compose)"),
        ("6", "System Maintenance      (updates, health, services)"),
        ("7", "Monitoring & Security   (resources, firewall, audit)"),
    ]
    
    console.print(f"\n[{COLORS['accent']}]━━━ DEVOPS (Applications) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['accent']}]")
    
    devops_options = [
        ("A", "Application Management  (apps, configs, deploy)"),
        ("R", "Remote Execution        (run commands via SSH)"),
        ("T", "Tunnel Management       (SSH tunnels, port forward)"),
        ("F", "Flow Automation         (workflows, templates)"),
        ("L", "Local Operations        (system info, network)"),
    ]
    
    console.print(f"\n[{COLORS['info']}]━━━ LIFEOPS (Automation) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['info']}]")
    
    lifeops_options = [
        ("G", "Agent & Gateway         (autonomous mode, 24/7)"),
        ("M", "MCP Server Management   (AI tool integrations)"),
        ("P", "AI Assistant            (insights, recommendations)"),
        ("W", "Wiki & Knowledge        (docs, search, RAG)"),
        ("B", "Backup & Config         (export, import, settings)"),
    ]
    
    # DEV INTELLIGENCE section
    console.print(f"\n[{COLORS['secondary']}]━━━ DEV INTELLIGENCE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['secondary']}]")

    intel_options = [
        ("S", "Copilot Sessions        (browse, search, export VS Code chats)"),
        ("N", "Memory & Knowledge      (key facts, profile, AI memory)"),
    ]

    # System options
    console.print(f"\n[{COLORS['dim']}]━━━ System ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['dim']}]")
    
    system_options = [
        ("C", "Configuration           (settings, context)"),
        ("H", "Command History         (recent commands)"),
        ("?", "Quick Help              (keyboard shortcuts)"),
    ]
    
    if not navig_dir.exists():
        system_options.append(("I", "Initialize (.navig/)    (project setup)"))
    
    system_options.append(("0", "Exit"))
    
    # Combine all options
    options = sysops_options + devops_options + lifeops_options + intel_options + system_options
    
    # Map display labels to internal action names
    action_map = {
        "Host Management": "Host Management",
        "File Operations": "File Operations",
        "Database Operations": "Database Operations",
        "Webserver Control": "Webserver Control",
        "Docker Management": "Docker Containers",
        "System Maintenance": "System Maintenance",
        "Monitoring & Security": "Monitoring & Security",
        "Application Management": "App Management",
        "Remote Execution": "Remote Execution",
        "Tunnel Management": "Tunnel Management",
        "Flow Automation": "Flow Automation",
        "Local Operations": "Local Operations",
        "Agent & Gateway": "Agent & Gateway",
        "MCP Server Management": "MCP Management",
        "AI Assistant": "AI Assistant",
        "Wiki & Knowledge": "Wiki & Documentation",
        "Backup & Config": "Backup & Restore",
        "Copilot Sessions": "Copilot Sessions",
        "Memory & Knowledge": "Memory & Knowledge",
        "Configuration": "Configuration",
        "Command History": "Command History",
        "Quick Help": "Quick Help",
        "Initialize (.navig/)": "Initialize App (.navig/)",
        "Exit": "exit",
    }
    
    # Use arrow-key navigation
    selection = prompt_menu_choice(options, "NAVIG Command Center")
    
    if selection is None:
        return "exit"
    
    # Strip description in parentheses from selection (e.g., "Host Management (servers, SSH)" -> "Host Management")
    clean_selection = selection.split('(')[0].strip() if '(' in selection else selection
    
    return action_map.get(clean_selection, clean_selection)


def show_host_management_menu(state: MenuState, standalone: bool = False) -> bool:
    """Host management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig host). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        # Build options list for selection with arrow-key navigation
        options = [
            ("1", "List all hosts"),
            ("2", "Show host info"),
            ("3", "Switch active host"),
            ("4", "Add new host"),
            ("5", "Discover local machine"),
            ("6", "Edit host configuration"),
            ("7", "Remove host"),
            ("8", "Clone host"),
            ("9", "Test SSH connection"),
            ("A", "Inspect host (auto-discover)"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Host Management")

            if selection == "Back" or selection is None:
                # When standalone, return False to exit. When submenu, return True to continue parent.
                return not standalone

            # Execute command based on selection
            if selection == "List all hosts":
                execute_host_list(state)
            elif selection == "Show host info":
                execute_host_info(state)
            elif selection == "Switch active host":
                execute_host_switch(state)
            elif selection == "Add new host":
                execute_host_add(state)
            elif selection == "Discover local machine":
                execute_discover_local(state)
            elif selection == "Edit host configuration":
                execute_host_edit(state)
            elif selection == "Remove host":
                execute_host_remove(state)
            elif selection == "Clone host":
                execute_host_clone(state)
            elif selection == "Test SSH connection":
                execute_host_test(state)
            elif selection == "Inspect host (auto-discover)":
                execute_host_inspect(state)
            else:
                continue

            # Refresh context after operations
            state.refresh_context()

            # Pause before returning to menu
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            # When standalone, return False to exit. When submenu, return True to continue parent.
            return not standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_app_management_menu(state: MenuState, standalone: bool = False) -> bool:
    """App management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig app). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        # Build options list for selection with arrow-key navigation
        options = [
            ("1", "List all apps"),
            ("2", "Search apps"),
            ("3", "Show app info"),
            ("4", "Switch active app"),
            ("5", "Add new app"),
            ("6", "Edit app configuration"),
            ("7", "Remove app"),
            ("8", "Clone app"),
            ("9", "Migrate apps to individual files"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "App Management")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu (continue parent), False for standalone (exit)

            if selection == "List all apps":
                execute_app_list(state)
            elif selection == "Search apps":
                execute_app_search(state)
            elif selection == "Show app info":
                execute_app_info(state)
            elif selection == "Switch active app":
                execute_app_switch(state)
            elif selection == "Add new app":
                execute_app_add(state)
            elif selection == "Edit app configuration":
                execute_app_edit(state)
            elif selection == "Remove app":
                execute_app_remove(state)
            elif selection == "Clone app":
                execute_app_clone(state)
            elif selection == "Migrate apps to individual files":
                execute_app_migrate(state)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu (continue parent), False for standalone (exit)
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_database_menu(state: MenuState, standalone: bool = False) -> bool:
    """Database operations submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone  # True for submenu, False for standalone

        options = [
            ("1", "Execute SQL query"),
            ("2", "Execute SQL file"),
            ("3", "Backup database"),
            ("4", "Restore database"),
            ("5", "List backups"),
            ("6", "List databases"),
            ("7", "List tables in database"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Execute SQL query":
                execute_sql_query(state)
            elif selection == "Execute SQL file":
                execute_sql_file(state)
            elif selection == "Backup database":
                execute_db_backup(state)
            elif selection == "Restore database":
                execute_db_restore(state)
            elif selection == "List backups":
                execute_list_backups(state)
            elif selection == "List databases":
                execute_db_list(state)
            elif selection == "List tables in database":
                execute_db_tables(state)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_webserver_menu(state: MenuState, standalone: bool = False) -> bool:
    """Webserver control submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone  # True for submenu, False for standalone

        if not state.active_app:
            show_status("No active app selected. Please select an app first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone  # True for submenu, False for standalone

        options = [
            ("1", "List Virtual Hosts"),
            ("2", "Test Configuration"),
            ("3", "Reload Webserver"),
            ("4", "Restart Webserver"),
            ("5", "View Access Logs"),
            ("6", "View Error Logs"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "List Virtual Hosts":
                execute_webserver_list_vhosts(state)
            elif selection == "Test Configuration":
                execute_webserver_test_config(state)
            elif selection == "Reload Webserver":
                execute_webserver_reload(state)
            elif selection == "Restart Webserver":
                execute_webserver_restart(state)
            elif selection == "View Access Logs":
                execute_webserver_access_logs(state)
            elif selection == "View Error Logs":
                execute_webserver_error_logs(state)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_file_operations_menu(state: MenuState, standalone: bool = False) -> bool:
    """File operations submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone  # True for submenu, False for standalone

        options = [
            ("1", "Upload File"),
            ("2", "Download File"),
            ("3", "List Remote Directory"),
            ("4", "Make Directory"),
            ("5", "Delete File/Directory"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Upload File":
                execute_file_upload(state)
            elif selection == "Download File":
                execute_file_download(state)
            elif selection == "List Remote Directory":
                execute_file_list(state)
            elif selection == "Make Directory":
                execute_file_mkdir(state)
            elif selection == "Delete File/Directory":
                execute_file_delete(state)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_maintenance_menu(state: MenuState, standalone: bool = False) -> bool:
    """System maintenance submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone  # True for submenu, False for standalone

        options = [
            ("1", "Update Packages"),
            ("2", "Clean Package Cache"),
            ("3", "System Health Check"),
            ("4", "Disk Usage"),
            ("5", "Service Status"),
            ("6", "Restart Service"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Update Packages":
                execute_maintenance_update(state)
            elif selection == "Clean Package Cache":
                execute_maintenance_clean(state)
            elif selection == "System Health Check":
                execute_maintenance_health(state)
            elif selection == "Disk Usage":
                execute_maintenance_disk(state)
            elif selection == "Service Status":
                execute_maintenance_service_status(state)
            elif selection == "Restart Service":
                execute_maintenance_restart_service(state)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_monitoring_menu(state: MenuState, standalone: bool = False) -> bool:
    """Server monitoring submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig monitor). If False, called as submenu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone

        options = [
            ("1", "Resource Usage (CPU, RAM, Disk)"),
            ("2", "Disk Space"),
            ("3", "Service Status"),
            ("4", "Network Statistics"),
            ("5", "Health Check (All)"),
            ("6", "Generate Report"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands.monitoring import (
                monitor_resources, monitor_disk, monitor_services,
                monitor_network, health_check, generate_report
            )

            if selection == "Resource Usage (CPU, RAM, Disk)":
                monitor_resources(state.get_context())
            elif selection == "Disk Space":
                monitor_disk(80, state.get_context())
            elif selection == "Service Status":
                monitor_services(state.get_context())
            elif selection == "Network Statistics":
                monitor_network(state.get_context())
            elif selection == "Health Check (All)":
                health_check(state.get_context())
            elif selection == "Generate Report":
                generate_report(state.get_context())
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_security_menu(state: MenuState, standalone: bool = False) -> bool:
    """Security management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig security). If False, called as submenu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone

        options = [
            ("1", "Firewall Status"),
            ("2", "Add Firewall Rule"),
            ("3", "Remove Firewall Rule"),
            ("4", "Fail2Ban Status"),
            ("5", "Unban IP"),
            ("6", "SSH Audit"),
            ("7", "Security Updates"),
            ("8", "Security Scan"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands.security import (
                firewall_status, firewall_add_rule, firewall_remove_rule,
                fail2ban_status, fail2ban_unban, ssh_audit,
                check_security_updates, security_scan
            )

            if selection == "Firewall Status":
                firewall_status(state.get_context())
            elif selection == "Add Firewall Rule":
                port = Prompt.ask("Port number")
                protocol = Prompt.ask("Protocol", default="tcp")
                firewall_add_rule(int(port), protocol, "any", state.get_context())
            elif selection == "Remove Firewall Rule":
                port = Prompt.ask("Port number")
                protocol = Prompt.ask("Protocol", default="tcp")
                firewall_remove_rule(int(port), protocol, state.get_context())
            elif selection == "Fail2Ban Status":
                fail2ban_status(state.get_context())
            elif selection == "Unban IP":
                ip = Prompt.ask("IP address to unban")
                fail2ban_unban(ip, None, state.get_context())
            elif selection == "SSH Audit":
                ssh_audit(state.get_context())
            elif selection == "Security Updates":
                check_security_updates(state.get_context())
            elif selection == "Security Scan":
                security_scan(state.get_context())
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_configuration_menu(state: MenuState, standalone: bool = False) -> bool:
    """Configuration submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "View Current Configuration"),
            ("2", "Edit Global Config"),
            ("3", "View Active Context"),
            ("4", "Clear Active Context"),
            ("5", "Manage Tunnel"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Select option")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "View Current Configuration":
                execute_config_show(state)
            elif selection == "Edit Global Config":
                execute_config_edit(state)
            elif selection == "View Active Context":
                execute_config_context(state)
            elif selection == "Clear Active Context":
                execute_config_clear_context(state)
            elif selection == "Manage Tunnel":
                execute_tunnel_menu(state)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Error: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_tunnel_menu(state: MenuState, standalone: bool = False) -> bool:
    """Tunnel management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig tunnel). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "Start tunnel"),
            ("2", "Stop tunnel"),
            ("3", "Restart tunnel"),
            ("4", "Show tunnel status"),
            ("5", "Auto tunnel"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Tunnel Management")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Start tunnel":
                with console.status(f"[{COLORS['accent']}]Starting tunnel...[/{COLORS['accent']}]", spinner="dots"):
                    tunnel.start_tunnel({})
                show_status("Tunnel started.", 'success')
                state.history.add("navig tunnel start", "Start tunnel", True)
            elif selection == "Stop tunnel":
                tunnel.stop_tunnel({})
                show_status("Tunnel stopped.", 'success')
                state.history.add("navig tunnel stop", "Stop tunnel", True)
            elif selection == "Restart tunnel":
                tunnel.stop_tunnel({})
                with console.status(f"[{COLORS['accent']}]Restarting tunnel...[/{COLORS['accent']}]", spinner="dots"):
                    tunnel.start_tunnel({})
                show_status("Tunnel restarted.", 'success')
                state.history.add("navig tunnel restart", "Restart tunnel", True)
            elif selection == "Show tunnel status":
                tunnel.show_tunnel_status({})
                state.history.add("navig tunnel status", "Tunnel status", True)
            elif selection == "Auto tunnel":
                with console.status(f"[{COLORS['accent']}]Checking tunnel...[/{COLORS['accent']}]", spinner="dots"):
                    tunnel.auto_tunnel({})
                show_status("Auto tunnel complete.", 'success')
                state.history.add("navig tunnel auto", "Auto tunnel", True)
            else:
                continue

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Tunnel operation failed: {e}", 'error')
            state.history.add("navig tunnel", "Tunnel operation", False)
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_command_history(state: MenuState) -> bool:
    """Display command history."""
    clear_screen()
    show_header(state)
    
    recent = state.history.get_recent(10)
    
    if not recent:
        show_status("No command history yet.", 'info')
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
        return True
    
    # Create history table
    table = Table(
        title=f"[{COLORS['primary']}]Recent Commands[/{COLORS['primary']}]",
        box=box.ROUNDED,
        style=COLORS['secondary'],
    )
    
    table.add_column("Time", style=COLORS['accent'], width=10)
    table.add_column("Command", style=COLORS['secondary'])
    table.add_column("Status", style=COLORS['info'], width=10)
    
    for cmd in recent:
        timestamp = cmd['timestamp'].strftime("%H:%M:%S")
        status_icon = "[+]" if cmd['success'] else "[x]"
        status_color = COLORS['success'] if cmd['success'] else COLORS['error']
        
        table.add_row(
            timestamp,
            cmd['description'],
            f"[{status_color}]{status_icon}[/{status_color}]"
        )
    
    console.print(table)
    console.print()
    
    # Option to clear history
    if Confirm.ask(f"[{COLORS['action']}]Clear history?[/{COLORS['action']}]", default=False):
        state.history.clear()
        show_status("History cleared.", 'success')
    
    Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
    return True


# ============================================================================
# EXECUTION FUNCTIONS - Call existing NAVIG commands
# ============================================================================

def execute_host_list(state: MenuState):
    """Execute host list command."""
    show_status("Listing all hosts...", 'loading')
    try:
        host.list_hosts({'all': True, 'format': 'table'})
        state.history.add("navig host list --all", "List all hosts", True)
    except Exception as e:
        show_status(f"Failed to list hosts: {e}", 'error')
        state.history.add("navig host list --all", "List all hosts", False)
        raise


def execute_host_switch(state: MenuState):
    """Switch active host."""
    hosts = state.config_manager.list_hosts()
    
    if not hosts:
        show_status("No hosts configured.", 'warning')
        return
    
    # Show current active host
    current = state.active_host
    if current:
        show_status(f"Current active host: {current}", 'info')
    
    selection = prompt_selection(hosts, "Select host to activate")

    if selection:
        try:
            host.use_host(selection, {})
            state.active_host = selection
            show_status(f"Switched to host: {selection}", 'success')
            state.history.add(f"navig host use {selection}", f"Switch to host {selection}", True)
        except Exception as e:
            show_status(f"Failed to switch host: {e}", 'error')
            state.history.add(f"navig host use {selection}", f"Switch to host {selection}", False)
            raise


def execute_host_add(state: MenuState):
    """Add new host interactively."""
    show_status("Adding new host...", 'action')

    name = Prompt.ask(f"[{COLORS['action']}]Host name[/{COLORS['action']}]")

    if not name:
        show_status("Host name cannot be empty.", 'error')
        return

    try:
        host.add_host(name, {})
        show_status(f"Host '{name}' added successfully.", 'success')
        state.history.add(f"navig host add {name}", f"Add host {name}", True)
    except Exception as e:
        show_status(f"Failed to add host: {e}", 'error')
        state.history.add(f"navig host add {name}", f"Add host {name}", False)
        raise


def execute_discover_local(state: MenuState):
    """Discover and configure local development environment."""
    from navig.commands.local_discovery import discover_local_host
    
    show_status("Discovering local development environment...", 'action')
    console.print()
    
    # Prompt for host name
    name = Prompt.ask(
        f"[{COLORS['action']}]Host name for local machine[/{COLORS['action']}]",
        default="localhost"
    )
    
    if not name:
        name = "localhost"
    
    try:
        result = discover_local_host(
            name=name,
            auto_confirm=False,
            set_active=True,
            progress=True
        )
        
        if result:
            state.history.add(
                f"navig host discover-local --name {name}",
                f"Discover local environment as '{name}'",
                True
            )
            state.refresh_context()
        else:
            state.history.add(
                f"navig host discover-local --name {name}",
                "Discover local environment (cancelled)",
                False
            )
    except Exception as e:
        show_status(f"Failed to discover local environment: {e}", 'error')
        state.history.add(
            f"navig host discover-local --name {name}",
            "Discover local environment",
            False
        )
        raise


def execute_host_edit(state: MenuState):
    """Edit host configuration."""
    hosts = state.config_manager.list_hosts()

    if not hosts:
        show_status("No hosts configured.", 'warning')
        return

    selection = prompt_selection(hosts, "Select host to edit")

    if selection:
        try:
            host.edit_host({'host_name': selection})
            show_status(f"Opening editor for host: {selection}", 'success')
            state.history.add(f"navig host edit {selection}", f"Edit host {selection}", True)
        except Exception as e:
            show_status(f"Failed to edit host: {e}", 'error')
            state.history.add(f"navig host edit {selection}", f"Edit host {selection}", False)
            raise


def execute_host_clone(state: MenuState):
    """Clone host configuration."""
    hosts = state.config_manager.list_hosts()

    if not hosts:
        show_status("No hosts configured.", 'warning')
        return

    source = prompt_selection(hosts, "Select source host")

    if source:
        new_name = Prompt.ask(f"[{COLORS['action']}]New host name[/{COLORS['action']}]")

        if not new_name:
            show_status("Host name cannot be empty.", 'error')
            return

        try:
            host.clone_host({'source_name': source, 'new_name': new_name})
            show_status(f"Host '{source}' cloned to '{new_name}'.", 'success')
            state.history.add(f"navig host clone {source} {new_name}", f"Clone host {source} to {new_name}", True)
        except Exception as e:
            show_status(f"Failed to clone host: {e}", 'error')
            state.history.add(f"navig host clone {source} {new_name}", f"Clone host {source} to {new_name}", False)
            raise


def execute_host_test(state: MenuState):
    """Test SSH connection to host."""
    hosts = state.config_manager.list_hosts()

    if not hosts:
        show_status("No hosts configured.", 'warning')
        return

    selection = prompt_selection(hosts, "Select host to test")

    if selection:
        try:
            with console.status(f"[{COLORS['accent']}]Testing connection to {selection}...[/{COLORS['accent']}]", spinner="dots"):
                host.test_host({'host_name': selection, 'silent': True})
            # Only show success if test_host didn't raise an exception
            show_status(f"Connection to {selection} successful.", 'success')
            state.history.add(f"navig host test {selection}", f"Test host {selection}", True)
        except RuntimeError:
            # test_host raises RuntimeError on failure - error already shown
            state.history.add(f"navig host test {selection}", f"Test host {selection}", False)
        except Exception as e:
            show_status(f"Unexpected error: {e}", 'error')
            state.history.add(f"navig host test {selection}", f"Test host {selection}", False)


def execute_host_inspect(state: MenuState):
    """Inspect host and auto-discover configuration."""
    hosts = state.config_manager.list_hosts()

    if not hosts:
        show_status("No hosts configured.", 'warning')
        return

    selection = prompt_selection(hosts, "Select host to inspect")

    if selection:
        try:
            # inspect_host() uses the active host, so we need to set it first
            original_active = state.active_host
            state.config_manager.set_active_host(selection)
            state.active_host = selection

            # Run inspection silently
            with console.status(f"[{COLORS['accent']}]Inspecting {selection}...[/{COLORS['accent']}]", spinner="dots"):
                result = host.inspect_host({'silent': True})

            # Show discovery summary
            if result:
                show_status(f"Host {selection} inspected successfully.", 'success')
                console.print()  # Blank line
                console.print(f"[{COLORS['info']}]=== Discovery Summary ===[/{COLORS['info']}]")

                # Show OS
                discovered = result['discovered']
                if discovered.get('os'):
                    console.print(f"[{COLORS['success']}]✓[/{COLORS['success']}] OS: {discovered['os']}")

                # Show databases
                databases = discovered.get('databases', [])
                for db in databases:
                    db_version = db.get('version', 'Unknown')
                    db_port = db.get('port', 'Unknown')
                    console.print(f"[{COLORS['success']}]✓[/{COLORS['success']}] Database: {db['type'].upper()} {db_version} (port {db_port})")

                # Show web servers
                web_servers = discovered.get('web_servers', [])
                for ws in web_servers:
                    ws_name = ws['type'].capitalize()
                    ws_version = ws.get('version', 'Unknown')
                    console.print(f"[{COLORS['success']}]✓[/{COLORS['success']}] Web Server: {ws_name} {ws_version}")

                # Show PHP
                if discovered.get('version'):
                    console.print(f"[{COLORS['success']}]✓[/{COLORS['success']}] PHP: {discovered['version']}")

                # Show templates
                detected_templates = result.get('detected_templates', {})
                if detected_templates:
                    template_list = []
                    for template_name, template_info in detected_templates.items():
                        version = template_info.get('version', 'Unknown')
                        if version and version != 'Unknown':
                            template_list.append(f"{template_name} (v{version})")
                        else:
                            template_list.append(template_name)
                    console.print(f"[{COLORS['accent']}]✓[/{COLORS['accent']}] Templates: {', '.join(template_list)}")

                console.print()  # Blank line
                state.history.add(f"navig host inspect {selection}", f"Inspect host {selection}", True)
            else:
                show_status("Inspection failed: No data returned", 'error')
                state.history.add(f"navig host inspect {selection}", f"Inspect host {selection}", False)

            # Restore original active host if it was different
            if original_active and original_active != selection:
                state.config_manager.set_active_host(original_active)
                state.active_host = original_active
        except Exception as e:
            show_status(f"Inspection failed: {e}", 'error')
            state.history.add(f"navig host inspect {selection}", f"Inspect host {selection}", False)


def execute_host_info(state: MenuState):
    """Show detailed host information."""
    hosts = state.config_manager.list_hosts()

    if not hosts:
        show_status("No hosts configured.", 'warning')
        return

    selection = prompt_selection(hosts, "Select host to view info")

    if selection:
        try:
            host.info_host({'host_name': selection})
            state.history.add(f"navig host info {selection}", f"View host info {selection}", True)
        except Exception as e:
            show_status(f"Failed to show host info: {e}", 'error')
            state.history.add(f"navig host info {selection}", f"View host info {selection}", False)
            raise


def execute_host_remove(state: MenuState):
    """Remove host configuration."""
    hosts = state.config_manager.list_hosts()
    
    if not hosts:
        show_status("No hosts configured.", 'warning')
        return
    
    selection = prompt_selection(hosts, "Select host to remove")
    
    if selection:
        if Confirm.ask(f"[{COLORS['warning']}]Remove host '{selection}'? This cannot be undone.[/{COLORS['warning']}]", default=False):
            try:
                host.remove_host(selection, {'yes': True, 'quiet': True})
                show_status(f"Host '{selection}' removed.", 'removed')
                state.history.add(f"navig host remove {selection}", f"Remove host {selection}", True)

                # Clear active host if it was removed
                if state.active_host == selection:
                    state.active_host = None
            except Exception as e:
                show_status(f"Failed to remove host: {e}", 'error')
                state.history.add(f"navig host remove {selection}", f"Remove host {selection}", False)
                raise


def execute_app_init(state: MenuState):
    """Initialize app-specific .navig/ directory."""
    from navig.commands.init import init_app
    from pathlib import Path
    
    # Show current directory
    current_dir = Path.cwd()
    show_status(f"Current directory: {current_dir}", 'info')
    
    # Check if .navig already exists
    navig_dir = current_dir / ".navig"
    if navig_dir.exists():
        show_status("App already initialized (.navig/ exists)", 'warning')
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")
        return
    
    # Confirm initialization
    console.print()
    console.print(f"[{COLORS['info']}]This will create a .navig/ directory with:[/{COLORS['info']}]")
    console.print("  • hosts/ - App-specific host configurations")
    console.print("  • apps/ - App-specific app configurations")
    console.print("  • config.yaml - App metadata")
    console.print("  • cache/, backups/ - Runtime directories")
    console.print()
    console.print(f"[{COLORS['dim']}]App-specific configs take precedence over global ~/.navig/ configs.[/{COLORS['dim']}]")
    console.print()
    
    if Confirm.ask(f"[{COLORS['action']}]Initialize NAVIG here?[/{COLORS['action']}]"):
        # Smart prompt for copying global configs - only ask if configs exist
        copy_global = False

        # Import the count function from init module
        from navig.commands.init import _count_global_configs
        host_count, app_count = _count_global_configs()
        total_count = host_count + app_count

        if total_count > 0:
            # Build informative prompt message
            config_summary = []
            if host_count > 0:
                config_summary.append(f"{host_count} host{'s' if host_count != 1 else ''}")
            if app_count > 0:
                config_summary.append(f"{app_count} legacy config{'s' if app_count != 1 else ''}")

            prompt_msg = f"Found {' and '.join(config_summary)} in global config. Copy to this app?"

            if Confirm.ask(f"[{COLORS['action']}]{prompt_msg}[/{COLORS['action']}]", default=False):
                copy_global = True
        # If no configs exist, skip the prompt entirely

        try:
            init_app({'quiet': False, 'copy_global': copy_global})
            show_status("App initialized successfully!", 'success')
            state.history.add("navig init", "Initialize app", True)
        except Exception as e:
            show_status(f"Failed to initialize app: {e}", 'error')
            state.history.add("navig init", "Initialize app", False)
            raise
    else:
        show_status("Initialization cancelled.", 'info')
    
    Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def execute_app_list(state: MenuState):
    """List all apps."""
    show_status("Listing all apps...", 'loading')
    try:
        app.list_apps({'all': True, 'format': 'table'})
        state.history.add("navig app list --all", "List all apps", True)
    except Exception as e:
        show_status(f"Failed to list apps: {e}", 'error')
        state.history.add("navig app list --all", "List all apps", False)
        raise


def execute_app_switch(state: MenuState):
    """Switch active app with local/global scope option."""
    # Get all apps across all hosts
    all_apps = []
    hosts = state.config_manager.list_hosts()

    for host_name in hosts:
        apps = state.config_manager.list_apps(host_name)
        for app_item in apps:
            all_apps.append(f"{host_name}/{app_item}")

    if not all_apps:
        show_status("No apps configured.", 'warning')
        return

    selection = prompt_selection(all_apps, "Select app to activate")

    if selection:
        try:
            # Parse host/app
            if '/' in selection:
                host_name, app_name = selection.split('/', 1)

                # Ask for scope (local or global)
                from pathlib import Path
                navig_dir = Path.cwd() / ".navig"

                # Only offer local option if .navig/ directory exists
                if navig_dir.exists() and navig_dir.is_dir():
                    scope_choice = Prompt.ask(
                        f"[{COLORS['action']}]Set as local active app (current directory only)?[/{COLORS['action']}]",
                        choices=["yes", "no"],
                        default="no"
                    )
                    local_scope = (scope_choice == "yes")
                else:
                    local_scope = False

                # Use app with appropriate scope
                app.use_app({
                    'app_name': app_name,
                    'local': local_scope,
                    'quiet': False
                })
                state.active_app = app_name

                scope_text = "locally" if local_scope else "globally"
                show_status(f"Switched to app: {app_name} ({scope_text})", 'success')
                state.history.add(f"navig app use {app_name}", f"Switch to app {app_name}", True)
        except Exception as e:
            show_status(f"Failed to switch app: {e}", 'error')
            state.history.add(f"navig app use {selection}", f"Switch to app {selection}", False)
            raise


def execute_app_add(state: MenuState):
    """Add new app."""
    if not state.active_host:
        show_status("No active host selected. Please select a host first.", 'warning')
        return

    name = Prompt.ask(f"[{COLORS['action']}]App name[/{COLORS['action']}]")

    if not name:
        show_status("App name cannot be empty.", 'error')
        return

    try:
        app.add_app({'app_name': name})
        show_status(f"App '{name}' added successfully.", 'success')
        state.history.add(f"navig app add {name}", f"Add app {name}", True)
    except Exception as e:
        show_status(f"Failed to add app: {e}", 'error')
        state.history.add(f"navig app add {name}", f"Add app {name}", False)
        raise


def execute_app_edit(state: MenuState):
    """Edit app configuration."""
    all_apps = []
    hosts = state.config_manager.list_hosts()

    for host_name in hosts:
        apps = state.config_manager.list_apps(host_name)
        for app_item in apps:
            all_apps.append(f"{host_name}/{app_item}")

    if not all_apps:
        show_status("No apps configured.", 'warning')
        return

    selection = prompt_selection(all_apps, "Select app to edit")

    if selection:
        try:
            host_name = selection.split('/', 1)[0] if '/' in selection else state.active_host
            app_name = selection.split('/', 1)[1] if '/' in selection else selection
            app.edit_app({'app_name': app_name, 'host': host_name})
            show_status(f"Opening editor for app: {app_name}", 'success')
            state.history.add(f"navig app edit {app_name}", f"Edit app {app_name}", True)
        except Exception as e:
            show_status(f"Failed to edit app: {e}", 'error')
            state.history.add(f"navig app edit {selection}", f"Edit app {selection}", False)
            raise


def execute_app_clone(state: MenuState):
    """Clone app configuration."""
    all_apps = []
    hosts = state.config_manager.list_hosts()

    for host_name in hosts:
        apps = state.config_manager.list_apps(host_name)
        for app_item in apps:
            all_apps.append(f"{host_name}/{app_item}")

    if not all_apps:
        show_status("No apps configured.", 'warning')
        return

    source = prompt_selection(all_apps, "Select source app")

    if source:
        new_name = Prompt.ask(f"[{COLORS['action']}]New app name[/{COLORS['action']}]")

        if not new_name:
            show_status("App name cannot be empty.", 'error')
            return

        try:
            host_name = source.split('/', 1)[0] if '/' in source else state.active_host
            source_name = source.split('/', 1)[1] if '/' in source else source
            app.clone_app({'source_name': source_name, 'new_name': new_name, 'host': host_name})
            show_status(f"App '{source_name}' cloned to '{new_name}'.", 'success')
            state.history.add(f"navig app clone {source_name} {new_name}", f"Clone app {source_name} to {new_name}", True)
        except Exception as e:
            show_status(f"Failed to clone app: {e}", 'error')
            state.history.add(f"navig app clone {source} {new_name}", f"Clone app {source} to {new_name}", False)
            raise


def execute_app_info(state: MenuState):
    """Show detailed app information."""
    all_apps = []
    hosts = state.config_manager.list_hosts()

    for host_name in hosts:
        apps = state.config_manager.list_apps(host_name)
        for app_item in apps:
            all_apps.append(f"{host_name}/{app_item}")

    if not all_apps:
        show_status("No apps configured.", 'warning')
        return

    selection = prompt_selection(all_apps, "Select app to view info")

    if selection:
        try:
            host_name = selection.split('/', 1)[0] if '/' in selection else state.active_host
            app_name = selection.split('/', 1)[1] if '/' in selection else selection
            app.info_app({'app_name': app_name, 'host': host_name})
            state.history.add(f"navig app info {app_name}", f"View app info {app_name}", True)
        except Exception as e:
            show_status(f"Failed to show app info: {e}", 'error')
            state.history.add(f"navig app info {selection}", f"View app info {selection}", False)
            raise


def execute_app_search(state: MenuState):
    """Search for apps."""
    query = Prompt.ask(f"[{COLORS['action']}]Search query[/{COLORS['action']}]")
    
    if not query:
        show_status("Search query cannot be empty.", 'error')
        return
    
    try:
        app.search_apps({'query': query})
        state.history.add(f"navig app search {query}", f"Search apps: {query}", True)
    except Exception as e:
        show_status(f"Search failed: {e}", 'error')
        state.history.add(f"navig app search {query}", f"Search apps: {query}", False)
        raise


def execute_app_remove(state: MenuState):
    """Remove app."""
    all_apps = []
    hosts = state.config_manager.list_hosts()
    
    for host_name in hosts:
        apps = state.config_manager.list_apps(host_name)
        for app_item in apps:
            all_apps.append(f"{host_name}/{app_item}")
    
    if not all_apps:
        show_status("No apps configured.", 'warning')
        return
    
    selection = prompt_selection(all_apps, "Select app to remove")
    
    if selection:
        # Parse host/app
        host_name = selection.split('/', 1)[0] if '/' in selection else None
        app_name = selection.split('/', 1)[1] if '/' in selection else selection

        # Single confirmation prompt
        if Confirm.ask(f"[{COLORS['warning']}]Are you sure you want to remove app '{app_name}' from host '{host_name}'? This cannot be undone.[/{COLORS['warning']}]", default=False):
            try:
                # Pass force=True to skip second confirmation, quiet=True to suppress duplicate message
                app.remove_app({'app_name': app_name, 'force': True, 'quiet': True})
                show_status(f"App '{app_name}' removed.", 'removed')
                state.history.add(f"navig app remove {app_name}", f"Remove app {app_name}", True)

                if state.active_app == app_name:
                    state.active_app = None
            except Exception as e:
                show_status(f"Failed to remove app: {e}", 'error')
                state.history.add(f"navig app remove {selection}", f"Remove app {selection}", False)
                raise


def execute_app_migrate(state: MenuState):
    """Migrate apps from legacy embedded format to individual files."""
    if not state.active_host:
        show_status("No active host selected. Please select a host first.", 'warning')
        return

    show_status(f"Migrating apps from host '{state.active_host}'...", 'info')

    try:
        # Call the migrate_apps function
        app.migrate_apps({'host': state.active_host, 'dry_run': False})
        state.history.add(f"navig app migrate --host {state.active_host}", f"Migrate apps from {state.active_host}", True)
    except Exception as e:
        show_status(f"Migration failed: {e}", 'error')
        state.history.add(f"navig app migrate --host {state.active_host}", f"Migrate apps from {state.active_host}", False)
        raise


def execute_sql_query(state: MenuState):
    """Execute SQL query."""
    query = Prompt.ask(f"[{COLORS['action']}]SQL query[/{COLORS['action']}]", default="SELECT VERSION()")
    
    if not query:
        show_status("Query cannot be empty.", 'error')
        return
    
    # Warn about destructive operations
    dangerous_keywords = ['DROP', 'TRUNCATE', 'DELETE FROM', 'ALTER TABLE', 'DROP DATABASE']
    if any(keyword in query.upper() for keyword in dangerous_keywords):
        show_status("DESTRUCTIVE SQL DETECTED", 'warning')
        console.print(Panel(
            f"[{COLORS['warning']}]Query: {query}[/{COLORS['warning']}]",
            title="[bold red]⚠ WARNING[/bold red]",
            box=box.DOUBLE_EDGE,
        ))
        
        if not Confirm.ask(f"[{COLORS['error']}]Execute this query?[/{COLORS['error']}]", default=False):
            show_status("Query cancelled.", 'info')
            return
    
    try:
        with console.status(f"[{COLORS['accent']}]Executing query...[/{COLORS['accent']}]", spinner="dots"):
            database.execute_sql({'query': query})
        show_status("Query executed successfully.", 'success')
        state.history.add(f'navig sql "{query[:50]}..."', "Execute SQL query", True)
    except Exception as e:
        show_status(f"Query failed: {e}", 'error')
        state.history.add(f'navig sql "{query[:50]}..."', "Execute SQL query", False)
        raise


def execute_sql_file(state: MenuState):
    """Execute SQL file."""
    filepath = Prompt.ask(f"[{COLORS['action']}]SQL file path[/{COLORS['action']}]")
    
    if not filepath:
        show_status("File path cannot be empty.", 'error')
        return
    
    if not Path(filepath).exists():
        show_status(f"File not found: {filepath}", 'error')
        return
    
    try:
        with console.status(f"[{COLORS['accent']}]Executing SQL file...[/{COLORS['accent']}]", spinner="dots"):
            database.execute_sql_file({'file': filepath})
        show_status(f"SQL file executed successfully: {filepath}", 'success')
        state.history.add(f"navig sqlfile {filepath}", f"Execute SQL file {Path(filepath).name}", True)
    except Exception as e:
        show_status(f"SQL file execution failed: {e}", 'error')
        state.history.add(f"navig sqlfile {filepath}", f"Execute SQL file {Path(filepath).name}", False)
        raise


def execute_db_backup(state: MenuState):
    """Backup database."""
    backup_path = Prompt.ask(
        f"[{COLORS['action']}]Backup path (press Enter for default)[/{COLORS['action']}]",
        default=""
    )
    
    try:
        with console.status(f"[{COLORS['accent']}]Creating backup...[/{COLORS['accent']}]", spinner="dots"):
            options = {'path': backup_path} if backup_path else {}
            database.backup_database(options)
        show_status("Database backup created successfully.", 'success')
        state.history.add("navig backup", "Backup database", True)
    except Exception as e:
        show_status(f"Backup failed: {e}", 'error')
        state.history.add("navig backup", "Backup database", False)
        raise


def execute_db_restore(state: MenuState):
    """Restore database from backup."""
    backup_path = Prompt.ask(f"[{COLORS['action']}]Backup file path[/{COLORS['action']}]")
    
    if not backup_path:
        show_status("File path cannot be empty.", 'error')
        return
    
    if not Path(backup_path).exists():
        show_status(f"Backup file not found: {backup_path}", 'error')
        return
    
    # Confirm destructive operation
    console.print(Panel(
        f"[{COLORS['warning']}]This will overwrite the current database![/{COLORS['warning']}]\n"
        f"Backup file: {backup_path}",
        title="[bold red]⚠ DESTRUCTIVE OPERATION[/bold red]",
        box=box.DOUBLE_EDGE,
    ))
    
    if not Confirm.ask(f"[{COLORS['error']}]Restore from this backup?[/{COLORS['error']}]", default=False):
        show_status("Restore cancelled.", 'info')
        return
    
    try:
        with console.status(f"[{COLORS['accent']}]Restoring database...[/{COLORS['accent']}]", spinner="dots"):
            database.restore_database({'file': backup_path, 'yes': False})
        show_status("Database restored successfully.", 'success')
        state.history.add(f"navig restore {backup_path}", "Restore database", True)
    except Exception as e:
        show_status(f"Restore failed: {e}", 'error')
        state.history.add(f"navig restore {backup_path}", "Restore database", False)
        raise


def execute_list_backups(state: MenuState):
    """List available backups."""
    backups_dir = state.config_manager.backups_dir
    
    if not backups_dir.exists():
        show_status("No backups directory found.", 'warning')
        return
    
    backups = list(backups_dir.glob("*.sql*"))
    
    if not backups:
        show_status("No backups found.", 'info')
        return
    
    # Create backups table
    table = Table(
        title=f"[{COLORS['primary']}]Available Backups[/{COLORS['primary']}]",
        box=box.ROUNDED,
        style=COLORS['secondary'],
    )
    
    table.add_column("Filename", style=COLORS['secondary'])
    table.add_column("Size", style=COLORS['accent'], justify="right")
    table.add_column("Modified", style=COLORS['info'])
    
    for backup in sorted(backups, key=lambda x: x.stat().st_mtime, reverse=True):
        size_mb = backup.stat().st_size / (1024 * 1024)
        modified = datetime.fromtimestamp(backup.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        
        table.add_row(
            backup.name,
            f"{size_mb:.2f} MB",
            modified
        )
    
    console.print(table)
    state.history.add("navig list-backups", "List backups", True)


def execute_db_list(state: MenuState):
    """List all databases."""
    show_status("Listing databases...", 'loading')
    try:
        # Build options from current context (host/app may have db credentials)
        options = {
            'app': state.context.get('app'),
            'host': state.context.get('host'),
        }
        # Use db_list_cmd from db.py (handles SSH-based database queries)
        db.db_list_cmd(
            container=None,  # No Docker container by default
            user='root',     # Default user
            password=None,   # Will be resolved from app/host config
            db_type=None,    # Auto-detect
            options=options
        )
        state.history.add("navig db-databases", "List databases", True)
    except Exception as e:
        show_status(f"Failed to list databases: {e}", 'error')
        state.history.add("navig db-databases", "List databases", False)
        raise


def execute_db_tables(state: MenuState):
    """List tables in a database."""
    # First, try to list databases so user can see available options
    console.print(f"\n[{COLORS['dim']}]Fetching available databases...[/{COLORS['dim']}]")

    db_name = Prompt.ask(f"[{COLORS['action']}]Database name[/{COLORS['action']}]")

    if not db_name:
        show_status("Database name cannot be empty.", 'error')
        return

    try:
        # Build options from current context
        options = {
            'app': state.context.get('app'),
            'host': state.context.get('host'),
        }
        # Use db_tables_cmd from db.py
        db.db_tables_cmd(
            database=db_name,
            container=None,
            user='root',
            password=None,
            db_type=None,
            options=options
        )
        state.history.add(f"navig db-show-tables {db_name}", f"List tables in {db_name}", True)
    except Exception as e:
        show_status(f"Failed to list tables: {e}", 'error')
        state.history.add(f"navig db-show-tables {db_name}", f"List tables in {db_name}", False)
        raise


# ============================================================================
# WEBSERVER EXECUTION FUNCTIONS
# ============================================================================

def execute_webserver_list_vhosts(state: MenuState):
    """List virtual hosts."""
    show_status("Listing virtual hosts...", 'loading')
    try:
        webserver.list_vhosts({'host': state.active_host, 'app': state.active_app})
        state.history.add("navig webserver list", "List virtual hosts", True)
    except Exception as e:
        show_status(f"Failed to list virtual hosts: {e}", 'error')
        state.history.add("navig webserver list", "List virtual hosts", False)
        raise


def execute_webserver_test_config(state: MenuState):
    """Test webserver configuration."""
    show_status("Testing webserver configuration...", 'loading')
    try:
        webserver.test_config({'host': state.active_host, 'app': state.active_app})
        state.history.add("navig webserver test", "Test webserver config", True)
    except Exception as e:
        show_status(f"Failed to test configuration: {e}", 'error')
        state.history.add("navig webserver test", "Test webserver config", False)
        raise


def execute_webserver_reload(state: MenuState):
    """Reload webserver."""
    if not Confirm.ask(f"[{COLORS['warning']}]Reload webserver?[/{COLORS['warning']}]", default=False):
        show_status("Reload cancelled.", 'info')
        return
    
    try:
        with console.status(f"[{COLORS['accent']}]Reloading webserver...[/{COLORS['accent']}]", spinner="dots"):
            webserver.reload_webserver({'host': state.active_host, 'app': state.active_app})
        show_status("Webserver reloaded.", 'success')
        state.history.add("navig webserver reload", "Reload webserver", True)
    except Exception as e:
        show_status(f"Failed to reload: {e}", 'error')
        state.history.add("navig webserver reload", "Reload webserver", False)
        raise


def execute_webserver_restart(state: MenuState):
    """Restart webserver."""
    if not Confirm.ask(f"[{COLORS['error']}]Restart webserver? (may cause brief downtime)[/{COLORS['error']}]", default=False):
        show_status("Restart cancelled.", 'info')
        return
    
    try:
        with console.status(f"[{COLORS['accent']}]Restarting webserver...[/{COLORS['accent']}]", spinner="dots"):
            webserver.restart_webserver({'host': state.active_host, 'app': state.active_app})
        show_status("Webserver restarted.", 'success')
        state.history.add("navig webserver restart", "Restart webserver", True)
    except Exception as e:
        show_status(f"Failed to restart: {e}", 'error')
        state.history.add("navig webserver restart", "Restart webserver", False)
        raise


def execute_webserver_access_logs(state: MenuState):
    """View webserver access logs."""
    lines = Prompt.ask(f"[{COLORS['action']}]Number of lines[/{COLORS['action']}]", default="50")
    
    try:
        webserver.view_logs({'host': state.active_host, 'app': state.active_app, 'type': 'access', 'lines': int(lines)})
        state.history.add(f"navig webserver logs --type access --lines {lines}", "View access logs", True)
    except Exception as e:
        show_status(f"Failed to view logs: {e}", 'error')
        state.history.add("navig webserver logs --type access", "View access logs", False)
        raise


def execute_webserver_error_logs(state: MenuState):
    """View webserver error logs."""
    lines = Prompt.ask(f"[{COLORS['action']}]Number of lines[/{COLORS['action']}]", default="50")
    
    try:
        webserver.view_logs({'host': state.active_host, 'app': state.active_app, 'type': 'error', 'lines': int(lines)})
        state.history.add(f"navig webserver logs --type error --lines {lines}", "View error logs", True)
    except Exception as e:
        show_status(f"Failed to view logs: {e}", 'error')
        state.history.add("navig webserver logs --type error", "View error logs", False)
        raise


# ============================================================================
# FILE OPERATIONS EXECUTION FUNCTIONS
# ============================================================================

def execute_file_upload(state: MenuState):
    """Upload a file to the server."""
    local_path = Prompt.ask(f"[{COLORS['action']}]Local file path[/{COLORS['action']}]")
    
    if not local_path:
        show_status("Local path cannot be empty.", 'error')
        return
    
    local_file = Path(local_path)
    if not local_file.exists():
        show_status(f"File not found: {local_path}", 'error')
        return
    
    remote_path = Prompt.ask(f"[{COLORS['action']}]Remote path (press Enter for auto)[/{COLORS['action']}]", default="")
    
    try:
        with console.status(f"[{COLORS['accent']}]Uploading file...[/{COLORS['accent']}]", spinner="dots"):
            files.upload_file_cmd(local_file, remote_path if remote_path else None, {'app': state.active_app})
        show_status(f"File uploaded: {local_file.name}", 'success')
        state.history.add(f"navig upload {local_path}", f"Upload {local_file.name}", True)
    except Exception as e:
        show_status(f"Upload failed: {e}", 'error')
        state.history.add(f"navig upload {local_path}", f"Upload {local_file.name}", False)
        raise


def execute_file_download(state: MenuState):
    """Download a file from the server."""
    remote_path = Prompt.ask(f"[{COLORS['action']}]Remote file path[/{COLORS['action']}]")
    
    if not remote_path:
        show_status("Remote path cannot be empty.", 'error')
        return
    
    local_path = Prompt.ask(f"[{COLORS['action']}]Local path (press Enter for current dir)[/{COLORS['action']}]", default="")
    
    try:
        with console.status(f"[{COLORS['accent']}]Downloading file...[/{COLORS['accent']}]", spinner="dots"):
            files.download_file_cmd(remote_path, Path(local_path) if local_path else None, {'app': state.active_app})
        show_status(f"File downloaded: {Path(remote_path).name}", 'success')
        state.history.add(f"navig download {remote_path}", f"Download {Path(remote_path).name}", True)
    except Exception as e:
        show_status(f"Download failed: {e}", 'error')
        state.history.add(f"navig download {remote_path}", f"Download {Path(remote_path).name}", False)
        raise


def execute_file_list(state: MenuState):
    """List remote directory contents."""
    remote_path = Prompt.ask(f"[{COLORS['action']}]Remote path[/{COLORS['action']}]", default="/var/www")
    
    try:
        files.list_remote_cmd(remote_path, {'app': state.active_app})
        state.history.add(f"navig list {remote_path}", f"List {remote_path}", True)
    except Exception as e:
        show_status(f"Failed to list directory: {e}", 'error')
        state.history.add(f"navig list {remote_path}", f"List {remote_path}", False)
        raise


def execute_file_mkdir(state: MenuState):
    """Create remote directory."""
    remote_path = Prompt.ask(f"[{COLORS['action']}]Remote directory path[/{COLORS['action']}]")
    
    if not remote_path:
        show_status("Path cannot be empty.", 'error')
        return
    
    try:
        files.mkdir_cmd(remote_path, {'app': state.active_app, 'parents': True})
        show_status(f"Directory created: {remote_path}", 'success')
        state.history.add(f"navig mkdir {remote_path}", f"Create directory {remote_path}", True)
    except Exception as e:
        show_status(f"Failed to create directory: {e}", 'error')
        state.history.add(f"navig mkdir {remote_path}", f"Create directory {remote_path}", False)
        raise


def execute_file_delete(state: MenuState):
    """Delete remote file or directory."""
    remote_path = Prompt.ask(f"[{COLORS['action']}]Remote path to delete[/{COLORS['action']}]")
    
    if not remote_path:
        show_status("Path cannot be empty.", 'error')
        return
    
    if not Confirm.ask(f"[{COLORS['error']}]Delete {remote_path}? This cannot be undone.[/{COLORS['error']}]", default=False):
        show_status("Deletion cancelled.", 'info')
        return
    
    try:
        files.delete_cmd(remote_path, {'app': state.active_app, 'recursive': True, 'yes': True})
        show_status(f"Deleted: {remote_path}", 'success')
        state.history.add(f"navig rm {remote_path}", f"Delete {remote_path}", True)
    except Exception as e:
        show_status(f"Failed to delete: {e}", 'error')
        state.history.add(f"navig rm {remote_path}", f"Delete {remote_path}", False)
        raise


# ============================================================================
# MAINTENANCE EXECUTION FUNCTIONS
# ============================================================================

def execute_maintenance_update(state: MenuState):
    """Update system packages."""
    if not Confirm.ask(f"[{COLORS['warning']}]Update system packages?[/{COLORS['warning']}]", default=False):
        show_status("Update cancelled.", 'info')
        return
    
    try:
        with console.status(f"[{COLORS['accent']}]Updating packages...[/{COLORS['accent']}]", spinner="dots"):
            maintenance.update_packages({})
        show_status("Packages updated.", 'success')
        state.history.add("navig maintenance update", "Update packages", True)
    except Exception as e:
        show_status(f"Update failed: {e}", 'error')
        state.history.add("navig maintenance update", "Update packages", False)
        raise


def execute_maintenance_clean(state: MenuState):
    """Clean package cache."""
    try:
        with console.status(f"[{COLORS['accent']}]Cleaning package cache...[/{COLORS['accent']}]", spinner="dots"):
            maintenance.clean_cache({})
        show_status("Cache cleaned.", 'success')
        state.history.add("navig maintenance clean", "Clean cache", True)
    except Exception as e:
        show_status(f"Cleanup failed: {e}", 'error')
        state.history.add("navig maintenance clean", "Clean cache", False)
        raise


def execute_maintenance_health(state: MenuState):
    """Check system health."""
    try:
        monitoring.health_check({'host': state.active_host})
        state.history.add("navig health", "Health check", True)
    except Exception as e:
        show_status(f"Health check failed: {e}", 'error')
        state.history.add("navig health", "Health check", False)
        raise


def execute_maintenance_disk(state: MenuState):
    """Check disk usage."""
    try:
        monitoring.disk_usage({'host': state.active_host})
        state.history.add("navig disk-usage", "Disk usage check", True)
    except Exception as e:
        show_status(f"Failed to check disk usage: {e}", 'error')
        state.history.add("navig disk-usage", "Disk usage check", False)
        raise


def execute_maintenance_service_status(state: MenuState):
    """Check service status."""
    service = Prompt.ask(f"[{COLORS['action']}]Service name[/{COLORS['action']}]", default="nginx")
    
    try:
        monitoring.service_status({'host': state.active_host, 'service': service})
        state.history.add(f"navig service-status {service}", f"Check {service} status", True)
    except Exception as e:
        show_status(f"Failed to check service: {e}", 'error')
        state.history.add(f"navig service-status {service}", f"Check {service} status", False)
        raise


def execute_maintenance_restart_service(state: MenuState):
    """Restart a service."""
    service = Prompt.ask(f"[{COLORS['action']}]Service name[/{COLORS['action']}]", default="nginx")
    
    if not Confirm.ask(f"[{COLORS['warning']}]Restart {service}?[/{COLORS['warning']}]", default=False):
        show_status("Restart cancelled.", 'info')
        return
    
    try:
        with console.status(f"[{COLORS['accent']}]Restarting {service}...[/{COLORS['accent']}]", spinner="dots"):
            maintenance.restart_service({'host': state.active_host, 'service': service})
        show_status(f"Service {service} restarted.", 'success')
        state.history.add(f"navig service-restart {service}", f"Restart {service}", True)
    except Exception as e:
        show_status(f"Failed to restart service: {e}", 'error')
        state.history.add(f"navig service-restart {service}", f"Restart {service}", False)
        raise


# ============================================================================
# CONFIGURATION EXECUTION FUNCTIONS
# ============================================================================

def execute_config_show(state: MenuState):
    """Show current configuration."""
    from navig.commands import config as config_cmd
    
    try:
        config_cmd.show_config({})
        state.history.add("navig config show", "Show configuration", True)
    except Exception as e:
        show_status(f"Failed to show config: {e}", 'error')
        state.history.add("navig config show", "Show configuration", False)
        raise


def execute_config_edit(state: MenuState):
    """Edit global configuration."""
    from navig.commands import config as config_cmd
    
    try:
        config_cmd.edit_config({})
        show_status("Configuration editor opened.", 'success')
        state.history.add("navig config edit", "Edit configuration", True)
    except Exception as e:
        show_status(f"Failed to edit config: {e}", 'error')
        state.history.add("navig config edit", "Edit configuration", False)
        raise


def execute_config_context(state: MenuState):
    """Show active context."""
    console.print(Panel(
        f"[{COLORS['primary']}]Active Host:[/{COLORS['primary']}] {state.active_host or 'None'}\n"
        f"[{COLORS['primary']}]Active App:[/{COLORS['primary']}] {state.active_app or 'None'}",
        title=f"[{COLORS['secondary']}]Active Context[/{COLORS['secondary']}]",
        box=box.ROUNDED,
    ))
    state.history.add("navig context", "View context", True)


def execute_config_clear_context(state: MenuState):
    """Clear active context."""
    if not Confirm.ask(f"[{COLORS['warning']}]Clear active host and app?[/{COLORS['warning']}]", default=False):
        show_status("Cancelled.", 'info')
        return
    
    try:
        state.config_manager.set_active_host(None)
        state.config_manager.set_active_app(None)
        state.active_host = None
        state.active_app = None
        show_status("Context cleared.", 'success')
        state.history.add("navig context clear", "Clear context", True)
    except Exception as e:
        show_status(f"Failed to clear context: {e}", 'error')
        state.history.add("navig context clear", "Clear context", False)
        raise


def execute_tunnel_menu(state: MenuState):
    """Tunnel management sub-menu."""
    options = [
        ("1", "Start Tunnel"),
        ("2", "Stop Tunnel"),
        ("3", "Tunnel Status"),
        ("0", "Back"),
    ]
    
    selection = prompt_menu_choice(options, "Tunnel action")
    
    if selection == "Back" or selection is None:
        return
    
    try:
        if selection == "Start Tunnel":
            with console.status(f"[{COLORS['accent']}]Starting tunnel...[/{COLORS['accent']}]", spinner="dots"):
                tunnel.start_tunnel({})
            show_status("Tunnel started.", 'success')
            state.history.add("navig tunnel start", "Start tunnel", True)
        elif selection == "Stop Tunnel":
            tunnel.stop_tunnel({})
            show_status("Tunnel stopped.", 'success')
            state.history.add("navig tunnel stop", "Stop tunnel", True)
        elif selection == "Tunnel Status":
            tunnel.show_tunnel_status({})
            state.history.add("navig tunnel status", "Tunnel status", True)
    except Exception as e:
        show_status(f"Tunnel operation failed: {e}", 'error')
        state.history.add("navig tunnel", "Tunnel operation", False)
        raise


# ============================================================================
# NEW SUBMENUS FOR THREE-PILLAR MENU STRUCTURE
# ============================================================================

def show_flow_menu(state: MenuState, standalone: bool = False) -> bool:
    """Flow automation submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "List available flows"),
            ("2", "Show flow definition"),
            ("3", "Run a flow"),
            ("4", "Test flow syntax"),
            ("5", "Create new flow"),
            ("6", "Edit flow"),
            ("7", "Remove flow"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Flow Automation")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands import flow

            if selection == "List available flows":
                flow.list_flows_cmd({})
                state.history.add("navig flow list", "List flows", True)
            elif selection == "Show flow definition":
                name = Prompt.ask(f"[{COLORS['action']}]Flow name[/{COLORS['action']}]")
                if name:
                    flow.show_flow_cmd(name, {})
                    state.history.add(f"navig flow show {name}", f"Show flow {name}", True)
            elif selection == "Run a flow":
                name = Prompt.ask(f"[{COLORS['action']}]Flow name[/{COLORS['action']}]")
                if name:
                    with console.status(f"[{COLORS['accent']}]Running flow {name}...[/{COLORS['accent']}]", spinner="dots"):
                        flow.run_flow_cmd(name, {})
                    state.history.add(f"navig flow run {name}", f"Run flow {name}", True)
            elif selection == "Test flow syntax":
                name = Prompt.ask(f"[{COLORS['action']}]Flow name[/{COLORS['action']}]")
                if name:
                    flow.test_flow_cmd(name, {})
                    state.history.add(f"navig flow test {name}", f"Test flow {name}", True)
            elif selection == "Create new flow":
                name = Prompt.ask(f"[{COLORS['action']}]Flow name[/{COLORS['action']}]")
                if name:
                    flow.add_flow_cmd(name, {})
                    state.history.add(f"navig flow add {name}", f"Create flow {name}", True)
            elif selection == "Edit flow":
                name = Prompt.ask(f"[{COLORS['action']}]Flow name[/{COLORS['action']}]")
                if name:
                    flow.edit_flow_cmd(name, {})
                    state.history.add(f"navig flow edit {name}", f"Edit flow {name}", True)
            elif selection == "Remove flow":
                name = Prompt.ask(f"[{COLORS['action']}]Flow name[/{COLORS['action']}]")
                if name and Confirm.ask(f"[{COLORS['warning']}]Remove flow '{name}'?[/{COLORS['warning']}]", default=False):
                    flow.remove_flow_cmd(name, {})
                    state.history.add(f"navig flow remove {name}", f"Remove flow {name}", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Flow operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_local_menu(state: MenuState, standalone: bool = False) -> bool:
    """Local operations submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "Show system info"),
            ("2", "List open ports"),
            ("3", "Network interfaces"),
            ("4", "Firewall status"),
            ("5", "Security audit"),
            ("6", "Ping remote host"),
            ("7", "DNS lookup"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Local Operations")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands import local

            if selection == "Show system info":
                local.show_local_cmd({})
                state.history.add("navig local show", "Show system info", True)
            elif selection == "List open ports":
                local.ports_cmd({})
                state.history.add("navig local ports", "List ports", True)
            elif selection == "Network interfaces":
                local.interfaces_cmd({})
                state.history.add("navig local interfaces", "Network interfaces", True)
            elif selection == "Firewall status":
                local.firewall_cmd({})
                state.history.add("navig local firewall", "Firewall status", True)
            elif selection == "Security audit":
                local.audit_cmd({})
                state.history.add("navig local audit", "Security audit", True)
            elif selection == "Ping remote host":
                host = Prompt.ask(f"[{COLORS['action']}]Host to ping[/{COLORS['action']}]")
                if host:
                    local.ping_cmd(host, {})
                    state.history.add(f"navig local ping {host}", f"Ping {host}", True)
            elif selection == "DNS lookup":
                domain = Prompt.ask(f"[{COLORS['action']}]Domain name[/{COLORS['action']}]")
                if domain:
                    local.dns_cmd(domain, {})
                    state.history.add(f"navig local dns {domain}", f"DNS lookup {domain}", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Local operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_agent_gateway_menu(state: MenuState, standalone: bool = False) -> bool:
    """Agent & Gateway management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "Agent status"),
            ("2", "Start agent"),
            ("3", "Stop agent"),
            ("4", "Agent configuration"),
            ("5", "View agent logs"),
            ("6", "Gateway status"),
            ("7", "Start gateway"),
            ("8", "Stop gateway"),
            ("9", "Manage sessions"),
            ("T", "Start Telegram Bot (with Gateway)"),
            ("B", "Start Telegram Bot (standalone)"),
            ("C", "Cron jobs"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Agent & Gateway")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands import agent, gateway

            if selection == "Agent status":
                agent.status_cmd({})
                state.history.add("navig agent status", "Agent status", True)
            elif selection == "Start agent":
                with console.status(f"[{COLORS['accent']}]Starting agent...[/{COLORS['accent']}]", spinner="dots"):
                    agent.start_cmd({})
                state.history.add("navig agent start", "Start agent", True)
            elif selection == "Stop agent":
                agent.stop_cmd({})
                state.history.add("navig agent stop", "Stop agent", True)
            elif selection == "Agent configuration":
                agent.config_cmd({})
                state.history.add("navig agent config", "Agent config", True)
            elif selection == "View agent logs":
                agent.logs_cmd({})
                state.history.add("navig agent logs", "Agent logs", True)
            elif selection == "Gateway status":
                gateway.status_cmd({})
                state.history.add("navig gateway status", "Gateway status", True)
            elif selection == "Start gateway":
                with console.status(f"[{COLORS['accent']}]Starting gateway...[/{COLORS['accent']}]", spinner="dots"):
                    gateway.start_cmd({})
                state.history.add("navig gateway start", "Start gateway", True)
            elif selection == "Stop gateway":
                gateway.stop_cmd({})
                state.history.add("navig gateway stop", "Stop gateway", True)
            elif selection == "Manage sessions":
                gateway.session_cmd({})
                state.history.add("navig gateway session", "Manage sessions", True)
            elif selection == "Start Telegram Bot (with Gateway)":
                console.print(f"\n[{COLORS['info']}]Starting NAVIG with Gateway + Telegram Bot...[/{COLORS['info']}]")
                console.print(f"[{COLORS['dim']}]This will start both services for session persistence.[/{COLORS['dim']}]")
                console.print()
                import subprocess
                import sys
                cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--port", "8789"]
                if sys.platform == "win32":
                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                console.print(f"[{COLORS['success']}]✓ Started Gateway + Bot in background[/{COLORS['success']}]")
                console.print(f"[{COLORS['dim']}]  Gateway: http://localhost:8789[/{COLORS['dim']}]")
                console.print(f"[{COLORS['dim']}]  Check bot status with: navig bot status[/{COLORS['dim']}]")
                state.history.add("navig bot --gateway", "Start Telegram Bot (with Gateway)", True)
            elif selection == "Start Telegram Bot (standalone)":
                console.print(f"\n[{COLORS['info']}]Starting NAVIG Telegram Bot (standalone)...[/{COLORS['info']}]")
                console.print(f"[{COLORS['warning']}]⚠️  Conversations will reset on bot restart.[/{COLORS['warning']}]")
                console.print()
                import subprocess
                import sys
                cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
                if sys.platform == "win32":
                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                console.print(f"[{COLORS['success']}]✓ Started Telegram Bot in background[/{COLORS['success']}]")
                console.print(f"[{COLORS['dim']}]  Check status with: navig bot status[/{COLORS['dim']}]")
                state.history.add("navig bot", "Start Telegram Bot (standalone)", True)
            elif selection == "Cron jobs":
                show_cron_menu(state)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Agent/Gateway operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_cron_menu(state: MenuState, standalone: bool = False) -> bool:
    """Cron job management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "List scheduled jobs"),
            ("2", "Add new job"),
            ("3", "Run job now"),
            ("4", "Enable job"),
            ("5", "Disable job"),
            ("6", "Remove job"),
            ("7", "Cron service status"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Cron Jobs")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands import cron

            if selection == "List scheduled jobs":
                cron.list_cmd({})
                state.history.add("navig cron list", "List cron jobs", True)
            elif selection == "Add new job":
                name = Prompt.ask(f"[{COLORS['action']}]Job name[/{COLORS['action']}]")
                if name:
                    cron.add_cmd(name, {})
                    state.history.add(f"navig cron add {name}", f"Add cron job {name}", True)
            elif selection == "Run job now":
                name = Prompt.ask(f"[{COLORS['action']}]Job name[/{COLORS['action']}]")
                if name:
                    cron.run_cmd(name, {})
                    state.history.add(f"navig cron run {name}", f"Run job {name}", True)
            elif selection == "Enable job":
                name = Prompt.ask(f"[{COLORS['action']}]Job name[/{COLORS['action']}]")
                if name:
                    cron.enable_cmd(name, {})
                    state.history.add(f"navig cron enable {name}", f"Enable job {name}", True)
            elif selection == "Disable job":
                name = Prompt.ask(f"[{COLORS['action']}]Job name[/{COLORS['action']}]")
                if name:
                    cron.disable_cmd(name, {})
                    state.history.add(f"navig cron disable {name}", f"Disable job {name}", True)
            elif selection == "Remove job":
                name = Prompt.ask(f"[{COLORS['action']}]Job name[/{COLORS['action']}]")
                if name and Confirm.ask(f"[{COLORS['warning']}]Remove job '{name}'?[/{COLORS['warning']}]", default=False):
                    cron.remove_cmd(name, {})
                    state.history.add(f"navig cron remove {name}", f"Remove job {name}", True)
            elif selection == "Cron service status":
                cron.status_cmd({})
                state.history.add("navig cron status", "Cron status", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Cron operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_monitoring_security_menu(state: MenuState, standalone: bool = False) -> bool:
    """Combined monitoring and security submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone

        options = [
            ("1", "Resource usage (CPU, RAM, Disk)"),
            ("2", "Disk space details"),
            ("3", "Service status"),
            ("4", "Network statistics"),
            ("5", "Health check (all)"),
            ("6", "Generate report"),
            ("7", "Firewall status"),
            ("8", "Add firewall rule"),
            ("9", "Fail2Ban status"),
            ("S", "Security scan"),
            ("A", "SSH audit"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Monitoring & Security")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands.monitoring import (
                monitor_resources, monitor_disk, monitor_services,
                monitor_network, health_check, generate_report
            )
            from navig.commands.security import (
                firewall_status, firewall_add_rule, fail2ban_status, security_scan, ssh_audit
            )

            if selection == "Resource usage (CPU, RAM, Disk)":
                monitor_resources(state.get_context())
                state.history.add("navig monitor resources", "Resource usage", True)
            elif selection == "Disk space details":
                monitor_disk(80, state.get_context())
                state.history.add("navig monitor disk", "Disk space", True)
            elif selection == "Service status":
                monitor_services(state.get_context())
                state.history.add("navig monitor services", "Service status", True)
            elif selection == "Network statistics":
                monitor_network(state.get_context())
                state.history.add("navig monitor network", "Network stats", True)
            elif selection == "Health check (all)":
                health_check(state.get_context())
                state.history.add("navig health", "Health check", True)
            elif selection == "Generate report":
                generate_report(state.get_context())
                state.history.add("navig monitor report", "Generate report", True)
            elif selection == "Firewall status":
                firewall_status(state.get_context())
                state.history.add("navig security firewall", "Firewall status", True)
            elif selection == "Add firewall rule":
                port = Prompt.ask(f"[{COLORS['action']}]Port number[/{COLORS['action']}]")
                protocol = Prompt.ask(f"[{COLORS['action']}]Protocol[/{COLORS['action']}]", default="tcp")
                if port:
                    firewall_add_rule(int(port), protocol, "any", state.get_context())
                    state.history.add(f"navig security firewall-add {port}", "Add firewall rule", True)
            elif selection == "Fail2Ban status":
                fail2ban_status(state.get_context())
                state.history.add("navig security fail2ban", "Fail2Ban status", True)
            elif selection == "Security scan":
                with console.status(f"[{COLORS['accent']}]Running security scan...[/{COLORS['accent']}]", spinner="dots"):
                    security_scan(state.get_context())
                state.history.add("navig security scan", "Security scan", True)
            elif selection == "SSH audit":
                ssh_audit(state.get_context())
                state.history.add("navig security ssh-audit", "SSH audit", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_quick_help(state: MenuState):
    """Display quick keyboard shortcuts help."""
    clear_screen()
    show_header(state)
    
    help_content = f"""
[{COLORS['primary']}]━━━ NAVIG Keyboard Shortcuts ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['primary']}]

[{COLORS['accent']}]Navigation:[/{COLORS['accent']}]
  ↑/↓ arrows   Navigate menu options
  Enter        Select highlighted option
  0/ESC        Go back / Exit menu
  Ctrl+C       Quick exit (any menu)

[{COLORS['accent']}]Quick Access Keys (Main Menu):[/{COLORS['accent']}]
  [1-7]        SysOps (Infrastructure)
  [A,R,T,F,L]  DevOps (Applications)
  [G,M,P,W,B]  LifeOps (Automation)
  [C,H,?]      System options

[{COLORS['accent']}]Common Commands:[/{COLORS['accent']}]
  navig menu          Open this interactive menu
  navig help          Show full help
  navig host use X    Switch to host X
  navig app use X     Switch to app X
  navig run "cmd"     Execute remote command

[{COLORS['accent']}]Tips:[/{COLORS['accent']}]
  • Set active host/app first for context-aware operations
  • Use Tab for command completion in CLI
  • All commands support --help for detailed options
"""
    console.print(help_content)
    console.print()
    Prompt.ask(f"[{COLORS['dim']}]Press Enter to return to menu[/{COLORS['dim']}]", default="")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def launch_menu(options: Dict[str, Any]):
    """
    Main entry point for interactive menu system.
    
    Args:
        options: Dictionary with command-line options (from typer context)
    """
    # Check if Rich is available
    try:
        from rich.console import Console
    except ImportError:
        print("\n[ERROR] Rich library not installed.")
        print("\nTo use the interactive menu, install Rich:")
        print("  pip install rich questionary")
        print("\nOr install all dependencies:")
        print("  pip install -r requirements.txt")
        sys.exit(1)
    
    # Check terminal size
    if console.width < 60 or console.height < 20:
        console.print(f"[{COLORS['warning']}]⚠ Warning: Terminal size too small.[/{COLORS['warning']}]")
        console.print(f"Minimum recommended: 60x20, current: {console.width}x{console.height}")
        console.print()
        
        if not Confirm.ask("Continue anyway?", default=False):
            sys.exit(0)
    
    # Initialize state
    try:
        config_manager = get_config_manager()
        state = MenuState(config_manager)
        
        # Main menu loop
        while True:
            try:
                selection = show_main_menu(state)
                
                if selection == "exit" or selection is None:
                    clear_screen()
                    console.print(f"\n[{COLORS['primary']}][*] Exiting NAVIG interactive menu.[/{COLORS['primary']}]")
                    console.print(f"[{COLORS['dim']}]    The void sees nothing we don't want it to see.[/{COLORS['dim']}]\n")
                    sys.exit(0)
                
                # Route to submenus based on three-pillar organization
                try:
                    # ===== SYSOPS (Infrastructure) =====
                    if selection == "Host Management":
                        show_host_management_menu(state)
                    elif selection == "File Operations":
                        show_file_operations_menu(state)
                    elif selection == "Database Operations":
                        show_database_menu(state)
                    elif selection == "Webserver Control":
                        show_webserver_menu(state)
                    elif selection == "Docker Containers":
                        show_docker_menu(state)
                    elif selection == "System Maintenance":
                        show_maintenance_menu(state)
                    elif selection == "Monitoring & Security":
                        show_monitoring_security_menu(state)
                    
                    # ===== DEVOPS (Applications) =====
                    elif selection == "App Management":
                        show_app_management_menu(state)
                    elif selection == "Remote Execution":
                        execute_remote_command_menu(state)
                    elif selection == "Tunnel Management":
                        show_tunnel_menu(state)
                    elif selection == "Flow Automation":
                        show_flow_menu(state)
                    elif selection == "Local Operations":
                        show_local_menu(state)
                    
                    # ===== LIFEOPS (Automation) =====
                    elif selection == "Agent & Gateway":
                        show_agent_gateway_menu(state)
                    elif selection == "MCP Management":
                        show_mcp_menu(state)
                    elif selection == "AI Assistant":
                        show_assistant_menu(state)
                    elif selection == "Wiki & Documentation":
                        execute_wiki_menu(state)
                    elif selection == "Backup & Restore":
                        show_backup_menu(state)

                    # ===== DEV INTELLIGENCE =====
                    elif selection == "Copilot Sessions":
                        _launch_copilot_sessions(state)
                    elif selection == "Memory & Knowledge":
                        _launch_memory_menu(state)

                    # ===== System =====
                    elif selection == "Configuration":
                        show_configuration_menu(state)
                    elif selection == "Command History":
                        show_command_history(state)
                    elif selection == "Quick Help":
                        show_quick_help(state)
                    elif selection == "Initialize App (.navig/)":
                        execute_app_init(state)
                        
                except KeyboardInterrupt:
                    # Ctrl+C in submenu returns to main menu
                    console.print(f"\n[{COLORS['info']}][~] Returning to main menu...[/{COLORS['info']}]")
                    continue
                
            except KeyboardInterrupt:
                # Ctrl+C on main menu exits completely
                clear_screen()
                console.print(f"\n[{COLORS['primary']}][*] Exiting NAVIG interactive menu.[/{COLORS['primary']}]")
                console.print(f"[{COLORS['dim']}]    The void sees nothing we don't want it to see.[/{COLORS['dim']}]\n")
                sys.exit(0)
    
    except Exception as e:
        console.print(f"\n[{COLORS['error']}][x] Fatal error: {e}[/{COLORS['error']}]")
        console.print(f"[{COLORS['dim']}]Check ~/.navig/navig.log for details.[/{COLORS['dim']}]\n")
        sys.exit(1)


# ============================================================================
# DEV INTELLIGENCE HANDLERS
# ============================================================================

def _run_navig_cmd(state: MenuState, cmd_parts: list, label: str) -> None:
    """Run a navig sub-command in the terminal and show output."""
    import subprocess
    state.history.add(" ".join(cmd_parts), label, True)
    try:
        result = subprocess.run(["navig"] + cmd_parts, check=False)
        if result.returncode != 0:
            state.history.add(" ".join(cmd_parts), label, False)
    except FileNotFoundError:
        # navig not on PATH — try python -m navig
        try:
            subprocess.run([sys.executable, "-m", "navig"] + cmd_parts, check=False)
        except Exception as exc:
            show_status(f"Error: {exc}", 'error')
    except Exception as exc:
        show_status(f"Error: {exc}", 'error')
    console.input(f"\n[{COLORS['dim']}]  Press Enter to continue…[/]")


def _launch_copilot_sessions(state: MenuState) -> None:
    """Launch the Copilot Sessions browser from the main menu."""
    clear_screen()
    console.print(f"[{COLORS['primary']}]━━━ Copilot Sessions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['primary']}]")
    console.print()
    console.print(f"[{COLORS['dim']}]  Commands:[/]")
    console.print(f"  [bright_cyan]navig copilot sessions[/]          — list all sessions")
    console.print(f"  [bright_cyan]navig copilot sessions stats[/]    — storage statistics")
    console.print(f"  [bright_cyan]navig copilot sessions search Q[/] — full-text search")
    console.print(f"  [bright_cyan]navig copilot sessions view ID[/]  — inspect a session")
    console.print(f"  [bright_cyan]navig copilot sessions export[/]   — export to JSON/MD/CSV")
    console.print(f"  [bright_cyan]navig copilot sessions delete ID[/]— delete a session")
    console.print()

    options = [
        ("1", "List all sessions"),
        ("2", "Show statistics"),
        ("3", "Search sessions"),
        ("0", "Back to main menu"),
    ]
    selection = prompt_menu_choice(options, "Copilot Sessions")

    if selection in (None, "Back to main menu"):
        return

    if selection == "List all sessions":
        clear_screen()
        _run_navig_cmd(state, ["copilot", "sessions", "list", "--limit", "50"], "List Copilot sessions")
    elif selection == "Show statistics":
        clear_screen()
        _run_navig_cmd(state, ["copilot", "sessions", "stats"], "Copilot session stats")
    elif selection == "Search sessions":
        query = console.input(f"[{COLORS['accent']}]Search query: [/]").strip()
        if query:
            clear_screen()
            _run_navig_cmd(state, ["copilot", "sessions", "search", query], f"Search: {query}")


def _launch_memory_menu(state: MenuState) -> None:
    """Launch the Memory & Knowledge menu."""
    clear_screen()
    console.print(f"[{COLORS['primary']}]━━━ Memory & Knowledge ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/{COLORS['primary']}]")
    console.print()

    options = [
        ("1", "Show memory profile"),
        ("2", "Search memory"),
        ("3", "List key facts"),
        ("4", "Remember something"),
        ("5", "Memory statistics"),
        ("0", "Back to main menu"),
    ]
    selection = prompt_menu_choice(options, "Memory & Knowledge")

    if selection in (None, "Back to main menu"):
        return

    if selection == "Show memory profile":
        clear_screen()
        _run_navig_cmd(state, ["memory", "show"], "Show memory profile")
    elif selection == "Search memory":
        query = console.input(f"[{COLORS['accent']}]Search: [/]").strip()
        if query:
            clear_screen()
            _run_navig_cmd(state, ["memory", "search", query], f"Memory search: {query}")
    elif selection == "List key facts":
        clear_screen()
        _run_navig_cmd(state, ["memory", "facts"], "List key facts")
    elif selection == "Remember something":
        note = console.input(f"[{COLORS['accent']}]Note to remember: [/]").strip()
        if note:
            clear_screen()
            _run_navig_cmd(state, ["memory", "remember", note], "Remember fact")
    elif selection == "Memory statistics":
        clear_screen()
        _run_navig_cmd(state, ["memory", "stats"], "Memory statistics")


# ============================================================================
# STANDALONE MENU LAUNCHERS
# These functions are entry points for command group interactive modes
# Called from CLI callbacks when user runs e.g. 'navig host' without subcommand
# ============================================================================

def _run_standalone_menu(menu_func, menu_name: str):
    """
    Generic wrapper for running a standalone submenu.

    Args:
        menu_func: The menu function to run (e.g., show_host_management_menu)
        menu_name: Human-readable name for exit message (e.g., "host management")
    """
    try:
        config_manager = get_config_manager()
        state = MenuState(config_manager)

        # Just call the menu once with standalone=True - it has its own internal loop
        # When user selects "Back" or presses Ctrl+C, it returns False and we exit
        try:
            menu_func(state, standalone=True)
        except KeyboardInterrupt:
            pass  # Exit cleanly

        # Exit gracefully
        clear_screen()
        console.print(f"\n[{COLORS['primary']}][*] Exiting {menu_name}.[/{COLORS['primary']}]")
        console.print(f"[{COLORS['dim']}]    The void sees nothing we don't want it to see.[/{COLORS['dim']}]\n")

    except Exception as e:
        console.print(f"\n[{COLORS['error']}][x] Error: {e}[/{COLORS['error']}]")


def launch_host_menu():
    """Entry point for standalone host menu (navig host without subcommand)."""
    _run_standalone_menu(show_host_management_menu, "host management")


def launch_app_menu():
    """Entry point for standalone app menu (navig app without subcommand)."""
    _run_standalone_menu(show_app_management_menu, "app management")


def launch_tunnel_menu():
    """Entry point for standalone tunnel menu (navig tunnel without subcommand)."""
    _run_standalone_menu(show_tunnel_menu, "tunnel management")


def launch_config_menu():
    """Entry point for standalone config menu (navig config without subcommand)."""
    _run_standalone_menu(show_configuration_menu, "configuration")


def launch_database_menu():
    """Entry point for standalone database menu."""
    _run_standalone_menu(show_database_menu, "database operations")


def launch_webserver_menu():
    """Entry point for standalone webserver menu."""
    _run_standalone_menu(show_webserver_menu, "webserver control")


def launch_files_menu():
    """Entry point for standalone files menu."""
    _run_standalone_menu(show_file_operations_menu, "file operations")


def launch_maintenance_menu():
    """Entry point for standalone maintenance menu."""
    _run_standalone_menu(show_maintenance_menu, "system maintenance")


def launch_backup_menu():
    """Entry point for standalone backup menu."""
    _run_standalone_menu(show_backup_menu, "backup management")


def launch_monitoring_menu():
    """Entry point for standalone monitoring menu (navig monitor without subcommand)."""
    _run_standalone_menu(show_monitoring_menu, "server monitoring")


def launch_security_menu():
    """Entry point for standalone security menu (navig security without subcommand)."""
    _run_standalone_menu(show_security_menu, "security management")


def launch_web_menu():
    """Entry point for standalone webserver menu (navig web without subcommand)."""
    _run_standalone_menu(show_webserver_menu, "webserver control")


# ============================================================================
# ADDITIONAL SUBMENUS FOR COMMAND GROUPS
# ============================================================================

def show_backup_menu(state: MenuState, standalone: bool = False) -> bool:
    """Backup and export submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig backup). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "Export archive"),
            ("2", "Export JSON"),
            ("3", "Import config"),
            ("4", "Backup database"),
            ("5", "Backup all databases"),
            ("6", "List backups"),
            ("7", "Restore backup"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Backup & Export")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Export archive":
                show_status("Exporting configuration as archive...", 'loading')
                backup.export_config({'format': 'archive', 'include_secrets': False})
                show_status("Export complete.", 'success')
                state.history.add("navig backup export", "Export config archive", True)
            elif selection == "Export JSON":
                show_status("Exporting configuration as JSON...", 'loading')
                backup.export_config({'format': 'json', 'include_secrets': False})
                show_status("Export complete.", 'success')
                state.history.add("navig backup export --format json", "Export config JSON", True)
            elif selection == "Import config":
                file_path = Prompt.ask(f"[{COLORS['action']}]Backup file path[/{COLORS['action']}]")
                if file_path:
                    show_status("Importing configuration...", 'loading')
                    backup.import_config({'file': file_path})
                    show_status("Import complete.", 'success')
                    state.history.add("navig backup import", "Import config", True)
            elif selection == "Backup database":
                from navig.commands import database
                show_status("Backing up database...", 'loading')
                database.backup_database({})
                show_status("Backup complete.", 'success')
                state.history.add("navig backup", "Backup database", True)
            elif selection == "Backup all databases":
                from navig.commands.backup import backup_all_databases_cmd
                show_status("Backing up all databases...", 'loading')
                backup_all_databases_cmd({})
                show_status("Backup complete.", 'success')
                state.history.add("navig backup-db-all", "Backup all databases", True)
            elif selection == "List backups":
                from navig.commands.backup import list_backups_cmd
                list_backups_cmd({})
                state.history.add("navig list-backups", "List backups", True)
            elif selection == "Restore backup":
                backup_name = Prompt.ask(f"[{COLORS['action']}]Backup name[/{COLORS['action']}]")
                if backup_name:
                    from navig.commands.backup import restore_backup_cmd
                    restore_backup_cmd({'backup_name': backup_name})
                    show_status("Restore complete.", 'success')
                    state.history.add("navig restore-backup", "Restore backup", True)

            state.refresh_context()
            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Backup operation failed: {e}", 'error')
            state.history.add("navig backup", "Backup operation", False)
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_docker_menu(state: MenuState, standalone: bool = False) -> bool:
    """Docker container management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly. If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        if not state.active_host:
            show_status("No active host selected. Please select a host first.", 'warning')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone

        options = [
            ("1", "List containers (ps)"),
            ("2", "View container logs"),
            ("3", "Exec command in container"),
            ("4", "Restart container"),
            ("5", "Stop container"),
            ("6", "Start container"),
            ("7", "List images"),
            ("8", "Prune unused resources"),
            ("0", "Back"),
        ]

        try:
            selection = prompt_menu_choice(options, "Docker Management")

            if selection == "Back" or selection is None:
                return not standalone

            from navig.commands import docker as docker_cmd

            if selection == "List containers (ps)":
                docker_cmd.docker_ps_cmd({})
                state.history.add("navig docker ps", "List containers", True)
            elif selection == "View container logs":
                container = Prompt.ask(f"[{COLORS['action']}]Container name[/{COLORS['action']}]")
                if container:
                    docker_cmd.docker_logs_cmd(container, {})
                    state.history.add("navig docker logs", "View container logs", True)
            elif selection == "Exec command in container":
                container = Prompt.ask(f"[{COLORS['action']}]Container name[/{COLORS['action']}]")
                command = Prompt.ask(f"[{COLORS['action']}]Command[/{COLORS['action']}]")
                if container and command:
                    docker_cmd.docker_exec_cmd(container, command, {})
                    state.history.add("navig docker exec", "Exec in container", True)
            elif selection == "Restart container":
                container = Prompt.ask(f"[{COLORS['action']}]Container name[/{COLORS['action']}]")
                if container:
                    docker_cmd.docker_restart_cmd(container, {})
                    state.history.add("navig docker restart", "Restart container", True)
            elif selection == "Stop container":
                container = Prompt.ask(f"[{COLORS['action']}]Container name[/{COLORS['action']}]")
                if container:
                    docker_cmd.docker_stop_cmd(container, {})
                    state.history.add("navig docker stop", "Stop container", True)
            elif selection == "Start container":
                container = Prompt.ask(f"[{COLORS['action']}]Container name[/{COLORS['action']}]")
                if container:
                    docker_cmd.docker_start_cmd(container, {})
                    state.history.add("navig docker start", "Start container", True)
            elif selection == "List images":
                docker_cmd.docker_images_cmd({})
                state.history.add("navig docker images", "List images", True)
            elif selection == "Prune unused resources":
                if Confirm.ask(f"[{COLORS['warning']}]Prune unused Docker resources?[/{COLORS['warning']}]", default=False):
                    docker_cmd.docker_prune_cmd({})
                    state.history.add("navig docker prune", "Prune resources", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone
        except Exception as e:
            show_status(f"Docker operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def execute_remote_command_menu(state: MenuState):
    """Remote command execution submenu."""
    clear_screen()
    show_header(state)

    if not state.active_host:
        show_status("No active host selected. Please select a host first.", 'warning')
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
        return

    console.print(f"[{COLORS['info']}]Enter command to execute on {state.active_host}:[/{COLORS['info']}]")
    console.print(f"[{COLORS['dim']}]Tip: Use --b64 for complex commands, @file to read from file[/{COLORS['dim']}]")
    console.print()

    try:
        command = Prompt.ask(f"[{COLORS['action']}]Command[/{COLORS['action']}]")
        if command:
            from navig.commands.remote import run_command
            run_command(command, {})
            state.history.add(f"navig run '{command}'", "Remote execution", True)
    except Exception as e:
        show_status(f"Command failed: {e}", 'error')
        state.history.add("navig run", "Remote execution", False)

    console.print()
    Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def execute_wiki_search(state: MenuState):
    """Quick search across knowledge base (wiki)."""
    clear_screen()
    show_header(state)
    
    try:
        from navig.commands import wiki as wiki_cmd
        
        console.print(f"\n[{COLORS['primary']}]🔍 Search Knowledge Base[/{COLORS['primary']}]\n")
        term = Prompt.ask(f"[{COLORS['action']}]Search term[/{COLORS['action']}]")
        
        if term:
            wiki_cmd.search_wiki(term)
            state.history.add(f"navig wiki search '{term}'", "Wiki search", True)
        
        console.print()
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")
    
    except Exception as e:
        show_status(f"Search failed: {e}", 'error')
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def execute_wiki_menu(state: MenuState):
    """Wiki & documentation submenu."""
    clear_screen()
    show_header(state)

    options = [
        ("1", "Search documentation"),
        ("2", "List all topics"),
        ("3", "Show topic"),
        ("0", "Back"),
    ]

    try:
        selection = prompt_menu_choice(options, "Wiki & Documentation")

        if selection == "Back" or selection is None:
            return

        from navig.commands import wiki as wiki_cmd

        if selection == "Search documentation":
            term = Prompt.ask(f"[{COLORS['action']}]Search term[/{COLORS['action']}]")
            if term:
                wiki_cmd.search_wiki(term)
                state.history.add(f"navig wiki search '{term}'", "Wiki search", True)
        elif selection == "List all topics":
            wiki_cmd.list_wiki()
            state.history.add("navig wiki list", "List wiki topics", True)
        elif selection == "Show topic":
            topic = Prompt.ask(f"[{COLORS['action']}]Topic name[/{COLORS['action']}]")
            if topic:
                wiki_cmd.show_topic(topic)
                state.history.add(f"navig wiki show '{topic}'", "Show wiki topic", True)

        console.print()
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

    except Exception as e:
        show_status(f"Wiki operation failed: {e}", 'error')
        Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_hestia_menu(state: MenuState, standalone: bool = False) -> bool:
    """HestiaCP management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig hestia). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "List users"),
            ("2", "Show user details"),
            ("3", "List domains"),
            ("4", "Show domain details"),
            ("5", "Show system info"),
            ("0", "Back"),
        ]

        try:
            from navig.commands import hestia
            selection = prompt_menu_choice(options, "HestiaCP Management")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "List users":
                hestia.list_users({})
                state.history.add("navig hestia users", "List HestiaCP users", True)
            elif selection == "Show user details":
                username = Prompt.ask(f"[{COLORS['action']}]Username[/{COLORS['action']}]")
                if username:
                    hestia.show_user({'username': username})
                    state.history.add("navig hestia user", "Show HestiaCP user", True)
            elif selection == "List domains":
                hestia.list_domains({})
                state.history.add("navig hestia domains", "List HestiaCP domains", True)
            elif selection == "Show domain details":
                domain = Prompt.ask(f"[{COLORS['action']}]Domain[/{COLORS['action']}]")
                if domain:
                    hestia.show_domain({'domain': domain})
                    state.history.add("navig hestia domain", "Show HestiaCP domain", True)
            elif selection == "Show system info":
                hestia.system_info({})
                state.history.add("navig hestia info", "HestiaCP system info", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"HestiaCP operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_template_menu(state: MenuState, standalone: bool = False) -> bool:
    """Template management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig template). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "List templates"),
            ("2", "Enable template"),
            ("3", "Disable template"),
            ("4", "Show template info"),
            ("5", "Run template"),
            ("0", "Back"),
        ]

        try:
            from navig.commands import template
            selection = prompt_menu_choice(options, "Template Management")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "List templates":
                template.list_templates_cmd({})
                state.history.add("navig template list", "List templates", True)
            elif selection == "Enable template":
                name = Prompt.ask(f"[{COLORS['action']}]Template name[/{COLORS['action']}]")
                if name:
                    template.enable_template_cmd(name, {})
                    state.history.add("navig template enable", "Enable template", True)
            elif selection == "Disable template":
                name = Prompt.ask(f"[{COLORS['action']}]Template name[/{COLORS['action']}]")
                if name:
                    template.disable_template_cmd(name, {})
                    state.history.add("navig template disable", "Disable template", True)
            elif selection == "Show template info":
                name = Prompt.ask(f"[{COLORS['action']}]Template name[/{COLORS['action']}]")
                if name:
                    template.info_template_cmd(name, {})
                    state.history.add("navig template info", "Template info", True)
            elif selection == "Run template":
                name = Prompt.ask(f"[{COLORS['action']}]Template name[/{COLORS['action']}]")
                if name:
                    template.run_template_cmd(name, {})
                    state.history.add("navig template run", "Run template", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Template operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_mcp_menu(state: MenuState, standalone: bool = False) -> bool:
    """MCP management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig mcp). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "Search MCP directory"),
            ("2", "Install MCP server"),
            ("3", "Uninstall MCP server"),
            ("4", "List installed servers"),
            ("5", "Start server"),
            ("6", "Stop server"),
            ("7", "Server status"),
            ("0", "Back"),
        ]

        try:
            from navig.commands import mcp
            selection = prompt_menu_choice(options, "MCP Server Management")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Search MCP directory":
                query = Prompt.ask(f"[{COLORS['action']}]Search query[/{COLORS['action']}]")
                if query:
                    mcp.search_mcp_cmd(query, {})
                    state.history.add("navig mcp search", "Search MCP directory", True)
            elif selection == "Install MCP server":
                name = Prompt.ask(f"[{COLORS['action']}]Server name[/{COLORS['action']}]")
                if name:
                    mcp.install_mcp_cmd(name, {})
                    state.history.add("navig mcp install", "Install MCP server", True)
            elif selection == "Uninstall MCP server":
                name = Prompt.ask(f"[{COLORS['action']}]Server name[/{COLORS['action']}]")
                if name:
                    mcp.uninstall_mcp_cmd(name, {})
                    state.history.add("navig mcp uninstall", "Uninstall MCP server", True)
            elif selection == "List installed servers":
                mcp.list_mcp_cmd({})
                state.history.add("navig mcp list", "List MCP servers", True)
            elif selection == "Start server":
                name = Prompt.ask(f"[{COLORS['action']}]Server name[/{COLORS['action']}]")
                if name:
                    mcp.start_mcp_cmd(name, {})
                    state.history.add("navig mcp start", "Start MCP server", True)
            elif selection == "Stop server":
                name = Prompt.ask(f"[{COLORS['action']}]Server name[/{COLORS['action']}]")
                if name:
                    mcp.stop_mcp_cmd(name, {})
                    state.history.add("navig mcp stop", "Stop MCP server", True)
            elif selection == "Server status":
                mcp.status_mcp_cmd({})
                state.history.add("navig mcp status", "MCP server status", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"MCP operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def show_assistant_menu(state: MenuState, standalone: bool = False) -> bool:
    """AI Assistant management submenu.

    Args:
        state: Menu state object
        standalone: If True, called directly (navig assistant). If False, called as submenu from main menu.

    Returns:
        True to continue parent menu loop, False to exit to shell.
    """
    while True:
        clear_screen()
        show_header(state)

        options = [
            ("1", "Show status"),
            ("2", "Analyze system"),
            ("3", "View insights"),
            ("4", "Get recommendations"),
            ("5", "Apply recommendation"),
            ("6", "Generate AI context"),
            ("7", "Configure assistant"),
            ("8", "Reset learning data"),
            ("0", "Back"),
        ]

        try:
            from navig.commands import assistant
            selection = prompt_menu_choice(options, "AI Assistant")

            if selection == "Back" or selection is None:
                return not standalone  # True for submenu, False for standalone

            if selection == "Show status":
                assistant.status_cmd({})
                state.history.add("navig assistant status", "Assistant status", True)
            elif selection == "Analyze system":
                with console.status(f"[{COLORS['accent']}]Analyzing system...[/{COLORS['accent']}]", spinner="dots"):
                    assistant.analyze_cmd({})
                state.history.add("navig assistant analyze", "System analysis", True)
            elif selection == "View insights":
                assistant.insights_cmd({})
                state.history.add("navig assistant insights", "View insights", True)
            elif selection == "Get recommendations":
                assistant.recommendations_cmd({})
                state.history.add("navig assistant recommend", "Get recommendations", True)
            elif selection == "Apply recommendation":
                rec_id = Prompt.ask(f"[{COLORS['action']}]Recommendation ID[/{COLORS['action']}]")
                if rec_id:
                    assistant.apply_cmd(rec_id, {})
                    state.history.add("navig assistant apply", "Apply recommendation", True)
            elif selection == "Generate AI context":
                assistant.context_cmd({}, False, None)
                state.history.add("navig assistant context", "Generate AI context", True)
            elif selection == "Configure assistant":
                assistant.config_cmd({})
                state.history.add("navig assistant config", "Configure assistant", True)
            elif selection == "Reset learning data":
                if Confirm.ask(f"[{COLORS['warning']}]Reset all learning data?[/{COLORS['warning']}]", default=False):
                    assistant.reset_cmd({})
                    state.history.add("navig assistant reset", "Reset learning data", True)

            console.print()
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Assistant operation failed: {e}", 'error')
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]", default="")


def launch_hestia_menu():
    """Entry point for standalone hestia menu."""
    _run_standalone_menu(show_hestia_menu, "HestiaCP management")


def launch_template_menu():
    """Entry point for standalone template menu."""
    _run_standalone_menu(show_template_menu, "template management")


def launch_mcp_menu():
    """Entry point for standalone MCP menu."""
    _run_standalone_menu(show_mcp_menu, "MCP management")


def launch_assistant_menu():
    """Entry point for standalone assistant menu."""
    _run_standalone_menu(show_assistant_menu, "assistant management")


def launch_flow_menu():
    """Entry point for standalone flow menu (navig flow without subcommand)."""
    _run_standalone_menu(show_flow_menu, "flow automation")


def launch_local_menu():
    """Entry point for standalone local menu (navig local without subcommand)."""
    _run_standalone_menu(show_local_menu, "local operations")


def launch_agent_menu():
    """Entry point for standalone agent/gateway menu."""
    _run_standalone_menu(show_agent_gateway_menu, "agent & gateway")


def launch_cron_menu():
    """Entry point for standalone cron menu."""
    _run_standalone_menu(show_cron_menu, "cron jobs")


def launch_monitoring_security_menu():
    """Entry point for standalone monitoring & security menu."""
    _run_standalone_menu(show_monitoring_security_menu, "monitoring & security")
