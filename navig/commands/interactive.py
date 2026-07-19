"""
Interactive submenus for a few legacy NAVIG command groups.

Terminal UI (Rich) shown when a *deprecated* command group is run without a
subcommand: ``navig assistant`` (→ ``navig ai``), ``navig hestia`` /
``navig web`` (→ ``navig web hestia`` / ``navig host``), and the one clean
survivor ``navig flow template``. Each ``launch_*_menu`` is a lazy entry point
called from that group's CLI callback (see ``cli/assistant_hestia.py``,
``commands/flow.py``, ``commands/server.py``, ``commands/webserver.py``).

The old top-level dashboard (``launch_menu`` → main menu → host/app/db/docker/…
submenus) was dead code — never wired to any CLI command, reachable only from
tests — and was removed. Only the four still-wired submenus remain here.
"""

import os
import subprocess
from datetime import datetime
from typing import Any

# Rich components for terminal UI
from rich.prompt import Confirm, Prompt

# Import existing command functions for reuse (the webserver submenu drives these)
from navig.commands import webserver

# NAVIG components
from navig.config import ConfigManager, get_config_manager
from navig.console_helper import get_console

# Initialize console with Mr. Robot theme
console = get_console()

# Mr. Robot color scheme
COLORS = {
    "primary": "bright_cyan",  # Active/selected  — ocean primary
    "secondary": "cyan",  # Normal text and borders
    "accent": "bright_blue",  # Accent items      — deep ocean
    "success": "bright_green",  # Success status
    "error": "bright_red",  # Errors
    "warning": "yellow",  # Warnings
    "dim": "dim white",  # Help text
    "info": "bright_cyan",  # Information
    "action": "bright_blue",  # Action prompts    — deep ocean
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
        import questionary  # noqa: F401
        from questionary import Style

        QUESTIONARY_AVAILABLE = True
        # Questionary uses ANSI color names, not Rich color names
        MENU_STYLE = Style(
            [
                ("qmark", "fg:cyan bold"),  # Question mark
                ("question", "fg:cyan bold"),  # Question text
                ("answer", "fg:cyan bold"),  # Selected answer
                ("pointer", "fg:ansiblue bold"),  # Pointer arrow
                ("highlighted", "fg:cyan bold"),  # Highlighted choice
                ("selected", "fg:ansiblue"),  # Selected text
                ("separator", "fg:white"),  # Separator
                ("instruction", "fg:white"),  # Instructions
                ("text", "fg:cyan"),  # Normal text
            ]
        )
    except (ImportError, Exception):
        # Questionary not available or failed to initialize
        QUESTIONARY_AVAILABLE = False
        MENU_STYLE = None
        # Silently fail - will use number-based fallback


class CommandHistory:
    """Track recent commands executed through the menu."""

    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.commands: list[dict[str, Any]] = []

    def add(self, command: str, description: str, success: bool = True):
        """Add a command to history."""
        entry = {
            "command": command,
            "description": description,
            "timestamp": datetime.now(),
            "success": success,
        }
        self.commands.insert(0, entry)  # Add to front
        if len(self.commands) > self.max_size:
            self.commands.pop()  # Remove oldest

    def get_recent(self, count: int = 5) -> list[dict[str, Any]]:
        """Get most recent commands."""
        return self.commands[:count]

    def clear(self):
        """Clear command history."""
        self.commands.clear()


class MenuState:
    """Manages menu navigation state and context."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.menu_stack: list[str] = []
        self.active_host: str | None = config_manager.get_active_host()
        self.active_app: str | None = config_manager.get_active_app()
        self.history = CommandHistory()
        self.last_selections: dict[str, Any] = {}  # Remember user choices

        # Terminal info
        self.terminal_width = console.width
        self.terminal_height = console.height

    def push_menu(self, menu_name: str):
        """Navigate to a submenu."""
        self.menu_stack.append(menu_name)

    def pop_menu(self) -> str | None:
        """Return to previous menu."""
        if self.menu_stack:
            return self.menu_stack.pop()
        return None

    def current_menu(self) -> str | None:
        """Get current menu name."""
        return self.menu_stack[-1] if self.menu_stack else None

    def refresh_context(self):
        """Refresh active host/app from config."""
        self.active_host = self.config_manager.get_active_host()
        self.active_app = self.config_manager.get_active_app()


def clear_screen():
    """Clear terminal screen without using shell=True for security."""
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "cls"], check=False)
        else:
            subprocess.run(["clear"], check=False)
    except Exception:
        # Fallback: just print newlines if subprocess fails
        print("\n" * 100)


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
            host_ip = host_config.get("host", host_config.get("ip", ""))
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
        context_line = (
            f"[{COLORS['dim']}]📍 Context: No active host (run 'navig host use <name>' to set)[/]"
        )

    # Timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print(f"{context_line}   [dim]🕐 {timestamp}[/dim]")
    console.print()


def show_status(message: str, status: str = "info"):
    """Display status message with appropriate icon and color."""
    icons = {
        "info": "[*]",
        "success": "[+]",
        "warning": "[!]",
        "error": "[x]",
        "action": "[>]",
        "loading": "[~]",
        "removed": "[-]",
    }

    colors = {
        "info": COLORS["info"],
        "success": COLORS["success"],
        "warning": COLORS["warning"],
        "error": COLORS["error"],
        "action": COLORS["primary"],
        "loading": COLORS["accent"],
        "removed": COLORS["warning"],  # Yellow for removal operations
    }

    icon = icons.get(status, "[*]")
    color = colors.get(status, COLORS["secondary"])

    console.print(f"[{color}]{icon} {message}[/{color}]")


def prompt_menu_choice(options: list[tuple[str, str]], title: str) -> str | None:
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
                f"[{COLORS['action']}]Select option[/{COLORS['action']}]", default="0"
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

            show_status("Invalid choice. Try again.", "error")

        except (KeyboardInterrupt, EOFError):
            return None


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
            show_status("No active host selected. Please select a host first.", "warning")
            Prompt.ask(f"[{COLORS['dim']}]Press Enter to go back[/{COLORS['dim']}]", default="")
            return not standalone  # True for submenu, False for standalone

        if not state.active_app:
            show_status("No active app selected. Please select an app first.", "warning")
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
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Error: {e}", "error")
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )


# ============================================================================
# WEBSERVER EXECUTION FUNCTIONS
# ============================================================================


def execute_webserver_list_vhosts(state: MenuState):
    """List virtual hosts."""
    show_status("Listing virtual hosts...", "loading")
    try:
        webserver.list_vhosts({"host": state.active_host, "app": state.active_app})
        state.history.add("navig webserver list", "List virtual hosts", True)
    except Exception as e:
        show_status(f"Failed to list virtual hosts: {e}", "error")
        state.history.add("navig webserver list", "List virtual hosts", False)
        raise


def execute_webserver_test_config(state: MenuState):
    """Test webserver configuration."""
    show_status("Testing webserver configuration...", "loading")
    try:
        webserver.test_config({"host": state.active_host, "app": state.active_app})
        state.history.add("navig webserver test", "Test webserver config", True)
    except Exception as e:
        show_status(f"Failed to test configuration: {e}", "error")
        state.history.add("navig webserver test", "Test webserver config", False)
        raise


def execute_webserver_reload(state: MenuState):
    """Reload webserver."""
    if not Confirm.ask(
        f"[{COLORS['warning']}]Reload webserver?[/{COLORS['warning']}]", default=False
    ):
        show_status("Reload cancelled.", "info")
        return

    try:
        with console.status(
            f"[{COLORS['accent']}]Reloading webserver...[/{COLORS['accent']}]",
            spinner="dots",
        ):
            webserver.reload_webserver({"host": state.active_host, "app": state.active_app})
        show_status("Webserver reloaded.", "success")
        state.history.add("navig webserver reload", "Reload webserver", True)
    except Exception as e:
        show_status(f"Failed to reload: {e}", "error")
        state.history.add("navig webserver reload", "Reload webserver", False)
        raise


def execute_webserver_restart(state: MenuState):
    """Restart webserver."""
    if not Confirm.ask(
        f"[{COLORS['error']}]Restart webserver? (may cause brief downtime)[/{COLORS['error']}]",
        default=False,
    ):
        show_status("Restart cancelled.", "info")
        return

    try:
        with console.status(
            f"[{COLORS['accent']}]Restarting webserver...[/{COLORS['accent']}]",
            spinner="dots",
        ):
            webserver.restart_webserver({"host": state.active_host, "app": state.active_app})
        show_status("Webserver restarted.", "success")
        state.history.add("navig webserver restart", "Restart webserver", True)
    except Exception as e:
        show_status(f"Failed to restart: {e}", "error")
        state.history.add("navig webserver restart", "Restart webserver", False)
        raise


def execute_webserver_access_logs(state: MenuState):
    """View webserver access logs."""
    lines = Prompt.ask(f"[{COLORS['action']}]Number of lines[/{COLORS['action']}]", default="50")

    try:
        webserver.view_logs(
            {
                "host": state.active_host,
                "app": state.active_app,
                "type": "access",
                "lines": int(lines),
            }
        )
        state.history.add(
            f"navig webserver logs --type access --lines {lines}",
            "View access logs",
            True,
        )
    except Exception as e:
        show_status(f"Failed to view logs: {e}", "error")
        state.history.add("navig webserver logs --type access", "View access logs", False)
        raise


def execute_webserver_error_logs(state: MenuState):
    """View webserver error logs."""
    lines = Prompt.ask(f"[{COLORS['action']}]Number of lines[/{COLORS['action']}]", default="50")

    try:
        webserver.view_logs(
            {
                "host": state.active_host,
                "app": state.active_app,
                "type": "error",
                "lines": int(lines),
            }
        )
        state.history.add(
            f"navig webserver logs --type error --lines {lines}",
            "View error logs",
            True,
        )
    except Exception as e:
        show_status(f"Failed to view logs: {e}", "error")
        state.history.add("navig webserver logs --type error", "View error logs", False)
        raise


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
        console.print(
            f"[{COLORS['dim']}]    The void sees nothing we don't want it to see.[/{COLORS['dim']}]\n"
        )

    except Exception as e:
        console.print(f"\n[{COLORS['error']}][x] Error: {e}[/{COLORS['error']}]")


def launch_web_menu():
    """Entry point for standalone webserver menu (navig web without subcommand)."""
    _run_standalone_menu(show_webserver_menu, "webserver control")


# ============================================================================
# ADDITIONAL SUBMENUS FOR COMMAND GROUPS
# ============================================================================


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
                    hestia.show_user({"username": username})
                    state.history.add("navig hestia user", "Show HestiaCP user", True)
            elif selection == "List domains":
                hestia.list_domains({})
                state.history.add("navig hestia domains", "List HestiaCP domains", True)
            elif selection == "Show domain details":
                domain = Prompt.ask(f"[{COLORS['action']}]Domain[/{COLORS['action']}]")
                if domain:
                    hestia.show_domain({"domain": domain})
                    state.history.add("navig hestia domain", "Show HestiaCP domain", True)
            elif selection == "Show system info":
                hestia.system_info({})
                state.history.add("navig hestia info", "HestiaCP system info", True)

            console.print()
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"HestiaCP operation failed: {e}", "error")
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )


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
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Template operation failed: {e}", "error")
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )


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
                with console.status(
                    f"[{COLORS['accent']}]Analyzing system...[/{COLORS['accent']}]",
                    spinner="dots",
                ):
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
                if Confirm.ask(
                    f"[{COLORS['warning']}]Reset all learning data?[/{COLORS['warning']}]",
                    default=False,
                ):
                    assistant.reset_cmd({})
                    state.history.add("navig assistant reset", "Reset learning data", True)

            console.print()
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )

        except KeyboardInterrupt:
            return not standalone  # True for submenu, False for standalone
        except Exception as e:
            show_status(f"Assistant operation failed: {e}", "error")
            Prompt.ask(
                f"[{COLORS['dim']}]Press Enter to continue[/{COLORS['dim']}]",
                default="",
            )


def launch_hestia_menu():
    """Entry point for standalone hestia menu."""
    _run_standalone_menu(show_hestia_menu, "HestiaCP management")


def launch_template_menu():
    """Entry point for standalone template menu."""
    _run_standalone_menu(show_template_menu, "template management")


def launch_assistant_menu():
    """Entry point for standalone assistant menu."""
    _run_standalone_menu(show_assistant_menu, "assistant management")


