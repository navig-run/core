"""
NAVIG CLI - No Admin Visible In Graveyard

Keep your servers alive. Forever.

Performance note: Heavy imports (TunnelManager, RemoteOperations, AIAssistant)
are deferred until actually needed to improve CLI startup time.
"""

import sys
import random
import typer
from typing import Optional, List, Dict, Any
from pathlib import Path

from navig import __version__
from navig.deprecation import deprecation_warning
from navig.lazy_loader import lazy_import

# Lazy-load console helper (imports rich.*). This keeps `navig --help` fast.
ch = lazy_import("navig.console_helper")

_config_manager = None
_NO_CACHE = False


def _get_config_manager():
    """Lazy-load and cache the ConfigManager."""
    global _config_manager
    if _config_manager is None:
        from navig.config import get_config_manager

        _config_manager = get_config_manager(force_new=_NO_CACHE)
    return _config_manager

# Heavy dependencies - loaded lazily on first use
# This reduces startup time by ~200ms for commands that don't need them
_TunnelManager = None
_RemoteOperations = None
_AIAssistant = None


def _get_tunnel_manager():
    """Lazy load TunnelManager."""
    global _TunnelManager
    if _TunnelManager is None:
        from navig.tunnel import TunnelManager
        _TunnelManager = TunnelManager
    return _TunnelManager


def _get_remote_operations():
    """Lazy load RemoteOperations."""
    global _RemoteOperations
    if _RemoteOperations is None:
        from navig.remote import RemoteOperations
        _RemoteOperations = RemoteOperations
    return _RemoteOperations


def _get_ai_assistant():
    """Lazy load AIAssistant."""
    global _AIAssistant
    if _AIAssistant is None:
        from navig.ai import AIAssistant
        _AIAssistant = AIAssistant
    return _AIAssistant


# ============================================================================
# CENTRALIZED HELP SYSTEM
# ============================================================================
# Single source of truth for all CLI help text.
# Format: "command": {"desc": "description", "commands": {"cmd": "desc", ...}}
#
# Standardization rules:
# - Descriptions: Start with verb (Manage, Execute, Control)
# - Commands: lowercase verb phrase, no period
# - Consistent verbs: list/add/remove/show/edit/test/run/use

HELP_REGISTRY: Dict[str, Dict[str, Any]] = {
    # =========================================================================
    # INFRASTRUCTURE
    # =========================================================================
    "host": {
        "desc": "Manage remote server connections",
        "commands": {
            "list": "list configured hosts",
            "add": "add new host interactively",
            "use": "switch active host",
            "show": "show host configuration",
            "test": "test SSH connection",
            "discover-local": "detect local environment",
            "monitor": "monitoring subcommands",
            "security": "security subcommands",
            "maintenance": "maintenance subcommands",
        }
    },
    "context": {
        "desc": "Manage host/app context for current project",
        "commands": {
            "show": "show current context resolution",
            "set": "set project-local context",
            "clear": "clear project-local context",
            "init": "initialize .navig directory",
        }
    },
    "history": {
        "desc": "Command history, replay, and audit trail",
        "commands": {
            "list": "list recent operations with filtering",
            "show": "show operation details",
            "replay": "re-run a previous operation",
            "undo": "undo a reversible operation",
            "export": "export history to file (json/csv)",
            "clear": "clear all history",
            "stats": "show history statistics",
        }
    },
    "dashboard": {
        "desc": "Real-time TUI for infrastructure monitoring",
        "commands": {
            "(default)": "launch live dashboard with auto-refresh",
            "--no-live": "single snapshot mode",
            "--refresh": "set refresh interval (seconds)",
        }
    },
    "suggest": {
        "desc": "Intelligent command suggestions based on history",
        "commands": {
            "(default)": "show suggested commands",
            "--context": "filter by context (docker, database, etc.)",
            "--run <n>": "run suggestion by number",
            "--dry-run": "preview without executing",
        }
    },
    "quick": {
        "desc": "Quick action shortcuts for frequent operations",
        "commands": {
            "(default)": "list or run quick actions",
            "list": "list all quick actions",
            "add": "add a new quick action",
            "remove": "remove a quick action",
            "<name>": "run a quick action by name",
        }
    },
    "tunnel": {
        "desc": "Manage SSH tunnels for secure connections",
        "commands": {
            "run": "start tunnel for active host",
            "show": "show tunnel status",
            "remove": "stop and remove tunnel",
            "update": "restart tunnel",
            "auto": "auto-detect and create tunnel",
        }
    },
    "local": {
        "desc": "Local machine operations and diagnostics",
        "commands": {
            "show": "show system information",
            "audit": "security audit of local machine",
            "ports": "list open ports",
            "firewall": "show firewall status",
            "ping": "ping remote host",
            "dns": "DNS lookup",
            "interfaces": "show network interfaces",
        }
    },
    "hosts": {
        "desc": "Manage /etc/hosts file entries",
        "commands": {
            "view": "view hosts file",
            "edit": "edit hosts file",
            "add": "add hosts entry",
        }
    },

    # =========================================================================
    # SERVICES
    # =========================================================================
    "app": {
        "desc": "Manage applications on remote hosts",
        "commands": {
            "list": "list configured apps",
            "add": "add new app interactively",
            "use": "switch active app",
            "show": "show app configuration",
            "edit": "edit app settings",
            "remove": "remove app configuration",
            "search": "search apps by name/domain",
            "migrate": "migrate app to another host",
        }
    },
    "docker": {
        "desc": "Manage Docker containers on remote hosts",
        "commands": {
            "ps": "list containers",
            "logs": "view container logs",
            "exec": "execute command in container",
            "compose": "docker-compose operations",
            "restart": "restart container",
            "stop": "stop container",
            "start": "start container",
            "stats": "show resource usage",
            "inspect": "inspect container details",
        }
    },
    "web": {
        "desc": "Manage web servers (Nginx/Apache)",
        "commands": {
            "vhosts": "list virtual hosts",
            "test": "test server configuration",
            "enable": "enable a site",
            "disable": "disable a site",
            "reload": "reload server configuration",
            "module-enable": "enable web server module",
            "module-disable": "disable web server module",
            "recommend": "get optimization recommendations",
            "hestia": "HestiaCP subcommands",
        }
    },

    # =========================================================================
    # DATA
    # =========================================================================
    "db": {
        "desc": "Database operations (MySQL, PostgreSQL, SQLite)",
        "commands": {
            "list": "list databases",
            "show": "show database info or tables",
            "run": "run SQL query or open shell",
            "query": "execute SQL query",
            "file": "execute SQL file",
            "tables": "show database tables",
            "dump": "export database backup",
            "restore": "restore database from backup",
            "optimize": "optimize database tables",
            "repair": "repair database tables",
        }
    },
    "file": {
        "desc": "File operations (upload, download, edit)",
        "commands": {
            "list": "list remote directory",
            "add": "upload file or create directory",
            "show": "view file contents",
            "edit": "edit remote file",
            "get": "download file",
            "remove": "delete remote file",
        }
    },
    "log": {
        "desc": "View and manage remote log files",
        "commands": {
            "show": "view log file contents",
            "run": "tail log in real-time",
        }
    },
    "backup": {
        "desc": "Backup and restore NAVIG configuration",
        "commands": {
            "export": "export config to backup file",
            "import": "import config from backup",
            "show": "show backup details",
            "remove": "delete backup file",
        }
    },

    # =========================================================================
    # AUTOMATION
    # =========================================================================
    "flow": {
        "desc": "Manage and execute reusable workflows",
        "commands": {
            "list": "list available flows",
            "show": "show flow definition",
            "run": "execute a flow",
            "test": "validate flow syntax",
            "add": "create new flow",
        }
    },
    "skills": {
        "desc": "Manage AI skill definitions",
        "commands": {
            "list": "list available skills",
            "tree": "show skills by category",
            "show": "show skill details, commands, and examples",
            "run": "run a skill command (skill:command [args])",
        }
    },
    "scaffold": {
        "desc": "Generate project structures from templates",
        "commands": {
            "apply": "generate files from template",
            "validate": "check template syntax",
            "list": "list available templates",
        }
    },
    "ai": {
        "desc": "AI assistant for server management",
        "commands": {
            "ask": "ask a question",
            "explain": "explain a command or concept",
            "diagnose": "diagnose server issues",
            "suggest": "get suggestions",
            "show": "show AI context or history",
            "run": "run AI-generated command",
            "edit": "edit AI system prompt",
            "providers": "manage AI providers and API keys",
            "login": "OAuth login (e.g., OpenAI Codex)",
            "logout": "remove OAuth credentials",
        }
    },
    "config": {
        "desc": "Manage NAVIG settings and configuration",
        "commands": {
            "show": "show host/app configuration",
            "edit": "edit configuration file",
            "test": "validate configuration",
            "settings": "show NAVIG settings",
            "set-mode": "set execution mode",
            "set-confirmation-level": "set confirmation level",
            "set": "set configuration value",
            "get": "get configuration value",
            "migrate": "migrate legacy config",
        }
    },
    "wiki": {
        "desc": "Wiki & knowledge base management",
        "commands": {
            "init": "initialize wiki structure",
            "list": "list wiki pages",
            "show": "view wiki page",
            "add": "add file to wiki",
            "edit": "edit wiki page",
            "remove": "archive/delete wiki page",
            "search": "full-text search",
            "publish": "publish public wiki content",
            "sync": "sync with global wiki",
            "inbox": "inbox processing commands",
            "links": "wiki link management",
            "rag": "RAG knowledge base for AI",
        }
    },
    "mcp": {
        "desc": "MCP server management for AI assistants",
        "commands": {
            "search": "search MCP directory",
            "install": "install MCP server",
            "uninstall": "uninstall MCP server",
            "list": "list installed servers",
            "enable": "enable MCP server",
            "disable": "disable MCP server",
            "start": "start MCP server",
            "stop": "stop MCP server",
            "restart": "restart MCP server",
            "status": "show server status",
            "serve": "start NAVIG as MCP server",
            "config": "generate MCP config for AI tools",
        }
    },
    # =========================================================================
    # AUTONOMOUS AGENT
    # =========================================================================
    "agent": {
        "desc": "Manage autonomous agent mode",
        "commands": {
            "install": "install and configure agent mode",
            "start": "start the autonomous agent",
            "stop": "stop the running agent",
            "status": "show agent status",
            "config": "manage agent configuration",
            "logs": "view agent logs",
            "personality": "manage personality profiles",
            "service": "install agent as system service (systemd/launchd/Windows)",
            "remediation": "view and manage auto-remediation actions",
            "learn": "analyze logs and learn from error patterns",
            "goal": "autonomous goal planning and execution tracking",
        }
    },
    "tray": {
        "desc": "Windows system tray launcher for NAVIG services",
        "commands": {
            "start": "launch the tray app (system tray icon with service controls)",
            "install": "install tray app (desktop shortcut + optional auto-start)",
            "status": "check if tray app is running",
        }
    },
    "gateway": {
        "desc": "Autonomous agent gateway server (24/7 control plane)",
        "commands": {
            "start": "start the gateway server",
            "stop": "stop the gateway server",
            "status": "show gateway status",
            "session": "manage sessions",
        }
    },
    "heartbeat": {
        "desc": "Periodic health check system",
        "commands": {
            "status": "show heartbeat status",
            "trigger": "trigger immediate heartbeat",
            "history": "show heartbeat history",
            "configure": "configure heartbeat settings",
        }
    },
    "cron": {
        "desc": "Persistent job scheduling",
        "commands": {
            "list": "list scheduled jobs",
            "add": "add new scheduled job",
            "remove": "remove a job",
            "run": "run a job immediately",
            "enable": "enable a job",
            "disable": "disable a job",
            "status": "show cron service status",
        }
    },
    "trigger": {
        "desc": "Event-driven automation triggers",
        "commands": {
            "list": "list configured triggers",
            "add": "create a new trigger",
            "show": "show trigger details",
            "remove": "delete a trigger",
            "enable": "enable a trigger",
            "disable": "disable a trigger",
            "test": "test trigger (dry run)",
            "fire": "manually fire a trigger",
            "history": "show trigger execution history",
            "stats": "show trigger statistics",
        }
    },
    "insights": {
        "desc": "Operations analytics and insights",
        "commands": {
            "(default)": "show insights summary",
            "hosts": "host health scores and trends",
            "commands": "top commands analysis",
            "time": "time-based usage patterns",
            "anomalies": "detect unusual patterns",
            "recommend": "personalized recommendations",
            "report": "generate full analytics report",
        }
    },
    "pack": {
        "desc": "Shareable operations bundles (runbooks, checklists, workflows)",
        "commands": {
            "(default)": "list available packs",
            "list": "list packs with filters",
            "show": "show pack details",
            "install": "install a pack",
            "uninstall": "remove an installed pack",
            "run": "execute a pack",
            "create": "create a new pack",
            "search": "search for packs",
        }
    },
    "approve": {
        "desc": "Human approval system for agent actions",
        "commands": {
            "list": "list pending approval requests",
            "yes": "approve a pending request",
            "no": "deny a pending request",
            "policy": "show/edit approval policy",
        }
    },
    "browser": {
        "desc": "Browser automation for web tasks",
        "commands": {
            "open": "navigate to URL",
            "click": "click element on page",
            "fill": "fill form field",
            "screenshot": "capture page screenshot",
            "stop": "stop browser",
            "status": "show browser status",
        }
    },
    "ahk": {
        "desc": "AutoHotkey v2 automation (Windows)",
        "commands": {
            "install": "detect or install AHKv2",
            "status": "show AHK status",
            "doctor": "diagnose integration issues",
            "run": "execute AHK script file",
            "exec": "execute inline AHK code",
            "click": "click at screen coordinates",
            "type": "type text with keyboard",
            "send": "send key sequence",
            "open": "open application or URL",
            "close": "close window by selector",
            "move": "move/resize window",
            "windows": "list all visible windows",
            "clipboard": "get/set clipboard content",
            "automate": "AI-powered automation",
        }
    },
    "task": {
        "desc": "Task queue for async operations",
        "commands": {
            "list": "list queued tasks",
            "add": "add a task to queue",
            "show": "show task details",
            "cancel": "cancel a pending task",
            "stats": "show queue statistics",
        }
    },
    "memory": {
        "desc": "Conversation memory and knowledge base",
        "commands": {
            "sessions": "list conversation sessions",
            "history": "show session messages",
            "clear": "clear session or all memory",
            "knowledge": "manage knowledge entries",
            "stats": "show memory statistics",
        }
    },
    "calendar": {
        "desc": "Calendar operations and event management",
        "commands": {
            "list": "list upcoming events",
            "auth": "authenticate with calendar provider",
            "add": "add new calendar event",
            "sync": "sync calendar data from remote",
        }
    },
    "email": {
        "desc": "Email operations and inbox management",
        "commands": {
            "list": "list unread emails",
            "setup": "configure email provider",
            "search": "search emails by query",
            "send": "send an email",
            "sync": "sync email data from remote",
        }
    },
    # =========================================================================
    # DOCUMENTATION & HELP
    # =========================================================================
    "docs": {
        "desc": "Search NAVIG documentation",
        "commands": {
            "(no args)": "list all documentation topics",
            "<query>": "search docs for relevant content",
        }
    },
    "fetch": {
        "desc": "Fetch and extract content from URLs",
        "commands": {
            "<url>": "fetch content from URL",
            "--mode": "extraction mode: markdown, text, raw",
            "--json": "output in JSON format",
        }
    },
    "search": {
        "desc": "Search the web for information",
        "commands": {
            "<query>": "search the web",
            "--limit": "max number of results",
            "--provider": "brave or duckduckgo",
        }
    },
    "formation": {
        "desc": "Manage profile-based agent formations",
        "commands": {
            "list": "list available formations",
            "show": "show formation details",
            "init": "initialize profile for workspace",
            "agents": "list agents in active formation",
        }
    },
    "council": {
        "desc": "Multi-agent council deliberation",
        "commands": {
            "run": "run deliberation across all agents",
        }
    },
    "version": {
        "desc": "Show NAVIG version and system info",
        "commands": {
            "(no args)": "show version with random quote",
            "--json": "output version in JSON format",
        }
    },
    "start": {
        "desc": "Quick launcher for gateway + Telegram bot",
        "commands": {
            "(no args)": "start gateway + bot in background",
            "--foreground": "start with visible logs",
            "--no-bot": "start gateway only",
            "--no-gateway": "start bot only (standalone)",
        }
    },
    "init": {
        "desc": "Interactive setup wizard for new installations",
        "commands": {
            "(no args)": "run setup wizard",
            "--reconfigure": "re-run setup for existing installation",
            "--install-daemon": "install NAVIG as system service",
        }
    },
    "telegram": {
        "desc": "Telegram bot management",
        "commands": {
            "status": "show bot status",
            "sessions list": "list active sessions",
            "sessions show": "show session details",
            "sessions clear": "clear session history",
            "sessions delete": "delete session",
            "sessions prune": "remove inactive sessions",
        }
    },
    "crash": {
        "desc": "Manage crash reports and logs",
        "commands": {
            "export": "export latest crash report for GitHub",
        }
    },
}


def show_subcommand_help(name: str, ctx: typer.Context = None):
    """Display compact help for a subcommand using the help registry."""
    from rich.console import Console
    from rich.table import Table
    
    # Use legacy_windows=False to avoid encoding issues with Unicode
    console = Console(legacy_windows=True)
    
    if name not in HELP_REGISTRY:
        # Fallback to default Typer help if not in registry
        return False
    
    info = HELP_REGISTRY[name]
    
    console.print()
    console.print(f"[bold cyan]navig {name}[/bold cyan] [dim]-[/dim] [white]{info['desc']}[/white]")
    console.print("[dim]" + "=" * 75 + "[/dim]")
    
    # Commands table
    cmd_table = Table(box=None, show_header=False, padding=(0, 2), collapse_padding=True)
    cmd_table.add_column("Command", style="cyan", min_width=12)
    cmd_table.add_column("Description", style="dim")
    
    for cmd, desc in info["commands"].items():
        cmd_table.add_row(cmd, desc)
    
    console.print(cmd_table)
    
    console.print("[dim]" + "=" * 75 + "[/dim]")
    console.print(f"[yellow]navig {name} <cmd> --help[/yellow] [dim]for command details[/dim]")
    console.print()
    
    return True


def make_subcommand_callback(name: str):
    """Create a callback function for a subcommand that shows custom help."""
    def callback(ctx: typer.Context):
        if ctx.invoked_subcommand is None:
            if show_subcommand_help(name, ctx):
                raise typer.Exit()
    return callback


def show_compact_help():
    """Display custom compact help."""
    from rich.console import Console
    from rich.table import Table
    from navig import __version__
    
    # Use legacy_windows=True to avoid Unicode encoding issues on Windows
    console = Console(legacy_windows=True)
    
    # Header - clean and bold
    console.print()
    console.print("[bold cyan]NAVIG[/bold cyan] [dim]v{0}[/dim]  [bold white]Server Management CLI[/bold white]".format(__version__))
    console.print("[dim]" + "=" * 75 + "[/dim]")
    
    # Commands table - clean 4-column layout with colored commands
    cmd_table = Table(box=None, show_header=True, header_style="bold white", padding=(0, 1), collapse_padding=True)
    cmd_table.add_column("INFRA", style="bold green", min_width=18)
    cmd_table.add_column("SERVICES", style="bold green", min_width=18) 
    cmd_table.add_column("DATA", style="bold green", min_width=18)
    cmd_table.add_column("AUTOMATION", style="bold green", min_width=18)
    
    cmd_table.add_row(
        "[cyan]host[/cyan]   [dim]servers[/dim]",
        "[cyan]app[/cyan]    [dim]applications[/dim]",
        "[cyan]db[/cyan]     [dim]databases[/dim]",
        "[cyan]flow[/cyan]   [dim]workflows[/dim]"
    )
    cmd_table.add_row(
        "[cyan]tunnel[/cyan] [dim]ssh tunnels[/dim]",
        "[cyan]docker[/cyan] [dim]containers[/dim]",
        "[cyan]file[/cyan]   [dim]transfers[/dim]",
        "[cyan]ai[/cyan]     [dim]assistant[/dim]"
    )
    cmd_table.add_row(
        "[cyan]local[/cyan]  [dim]local ops[/dim]",
        "[cyan]web[/cyan]    [dim]webservers[/dim]",
        "[cyan]log[/cyan]    [dim]logging[/dim]",
        "[cyan]wiki[/cyan]   [dim]knowledge[/dim]"
    )
    cmd_table.add_row(
        "[cyan]hosts[/cyan]  [dim]/etc/hosts[/dim]",
        "[cyan]gateway[/cyan][dim]agent server[/dim]",
        "[cyan]backup[/cyan] [dim]backups[/dim]",
        "[cyan]config[/cyan] [dim]settings[/dim]"
    )
    cmd_table.add_row(
        "",
        "[cyan]heartbeat[/cyan][dim]health[/dim]",
        "",
        "[cyan]cron[/cyan]   [dim]scheduler[/dim]"
    )
    console.print(cmd_table)
    
    # Quick Start
    console.print("[dim]" + "=" * 75 + "[/dim]")
    console.print("[bold white]Quick Start[/bold white]  [yellow]navig init[/yellow] -> [yellow]host add[/yellow] -> [yellow]host use[/yellow] -> [yellow]run[/yellow] [dim]\"command\"[/dim]")
    
    # Options - compact single line
    console.print("[bold white]Options[/bold white]      [cyan]-h[/cyan] [dim]host[/dim]  [cyan]-a[/cyan] [dim]app[/dim]  [cyan]-v[/cyan] [dim]verbose[/dim]  [cyan]-y[/cyan] [dim]yes[/dim]  [cyan]-q[/cyan] [dim]quiet[/dim]  [cyan]--json[/cyan]  [cyan]--dry-run[/cyan]")
    
    # More commands
    console.print("[bold white]More[/bold white]         [dim]run init menu migrate server-template mcp software install plugin[/dim]")
    
    # Footer
    console.print("[dim]" + "=" * 75 + "[/dim]")
    console.print("[yellow]navig <cmd> --help[/yellow] [dim]for details[/dim]  |  [yellow]navig menu[/yellow] [dim]interactive mode[/dim]")
    console.print()
    raise typer.Exit()


def help_callback(ctx: typer.Context, value: bool):
    """Callback for --help flag."""
    if value:
        show_compact_help()


# Initialize CLI app
app = typer.Typer(
    name="navig",
    help="NAVIG - Server Management CLI",
    add_completion=True,
    rich_markup_mode="rich",
    invoke_without_command=True,
    no_args_is_help=False,
)

# Global state (lazy via _get_config_manager())


# ============================================================================
# HACKER CULTURE & TECHNOLOGY QUOTES
# ============================================================================

HACKER_QUOTES = [
    # Richard Stallman
    ("Free software is a matter of liberty, not price.", "Richard Stallman"),
    ("With software there are only two possibilities: either the users control the program or the program controls the users.", "Richard Stallman"),
    ("Sharing is good, and with digital technology, sharing is easy.", "Richard Stallman"),

    # Linus Torvalds
    ("Talk is cheap. Show me the code.", "Linus Torvalds"),
    ("Software is like sex: it's better when it's free.", "Linus Torvalds"),
    ("Given enough eyeballs, all bugs are shallow.", "Linus Torvalds"),
    ("Bad programmers worry about the code. Good programmers worry about data structures and their relationships.", "Linus Torvalds"),
    ("In real open source, you have the right to control your own destiny.", "Linus Torvalds"),

    # Eric S. Raymond
    ("When you don't know what you're doing, do it neatly.", "Eric S. Raymond"),
    ("Good programmers know what to write. Great ones know what to rewrite and reuse.", "Eric S. Raymond"),
    ("To solve an interesting problem, start by finding a problem that is interesting to you.", "Eric S. Raymond"),
    ("Hackers are not joiners. They never identify themselves by a uniform or conventional badges.", "Eric S. Raymond"),

    # Grace Hopper
    ("The most dangerous phrase in the language is: We've always done it this way.", "Grace Hopper"),
    ("A ship in port is safe, but that's not what ships are built for.", "Grace Hopper"),
    ("It is often easier to ask for forgiveness than to ask for permission.", "Grace Hopper"),
    ("If it's a good idea, go ahead and do it. It's much easier to apologize than it is to get permission.", "Grace Hopper"),

    # Alan Turing
    ("We can only see a short distance ahead, but we can see plenty there that needs to be done.", "Alan Turing"),
    ("Those who can imagine anything, can create the impossible.", "Alan Turing"),
    ("Sometimes it is the people no one can imagine anything of who do the things no one can imagine.", "Alan Turing"),

    # Dennis Ritchie
    ("UNIX is basically a simple operating system, but you have to be a genius to understand the simplicity.", "Dennis Ritchie"),
    ("C is quirky, flawed, and an enormous success.", "Dennis Ritchie"),

    # Ken Thompson
    ("One of my most productive days was throwing away 1000 lines of code.", "Ken Thompson"),
    ("When in doubt, use brute force.", "Ken Thompson"),

    # Brian Kernighan
    ("Debugging is twice as hard as writing the code in the first place.", "Brian Kernighan"),
    ("Controlling complexity is the essence of computer programming.", "Brian Kernighan"),
    ("Don't comment bad code — rewrite it.", "Brian Kernighan"),

    # Edsger Dijkstra
    ("Simplicity is prerequisite for reliability.", "Edsger Dijkstra"),
    ("Program testing can be used to show the presence of bugs, but never to show their absence.", "Edsger Dijkstra"),
    ("If debugging is the process of removing bugs, then programming must be the process of putting them in.", "Edsger Dijkstra"),
    ("The question of whether a computer can think is no more interesting than the question of whether a submarine can swim.", "Edsger Dijkstra"),

    # Donald Knuth
    ("Premature optimization is the root of all evil.", "Donald Knuth"),
    ("Beware of bugs in the above code; I have only proved it correct, not tested it.", "Donald Knuth"),
    ("The best programs are written so that computing machines can perform them quickly.", "Donald Knuth"),

    # Steve Wozniak
    ("Never trust a computer you can't throw out a window.", "Steve Wozniak"),
    ("My goal wasn't to make a ton of money. It was to build good computers.", "Steve Wozniak"),

    # Kevin Mitnick
    ("Hackers are breaking the systems for profit. Before, it was about intellectual curiosity and pursuit of knowledge.", "Kevin Mitnick"),
    ("Social engineering bypasses all technologies, including firewalls.", "Kevin Mitnick"),
    ("The key to social engineering is influencing a person to do something that allows the hacker to gain access.", "Kevin Mitnick"),

    # Bruce Schneier
    ("Security is not a product, but a process.", "Bruce Schneier"),
    ("Amateurs hack systems, professionals hack people.", "Bruce Schneier"),
    ("If you think technology can solve your security problems, then you don't understand the problems and you don't understand the technology.", "Bruce Schneier"),

    # John Carmack
    ("If you want to set off and go develop some grand new thing, you don't need millions of dollars of capitalization.", "John Carmack"),
    ("Focused, hard work is the real key to success.", "John Carmack"),

    # Tim Berners-Lee
    ("The Web does not just connect machines, it connects people.", "Tim Berners-Lee"),
    ("Anyone who has lost track of time when using a computer knows the propensity to dream.", "Tim Berners-Lee"),

    # Aaron Swartz
    ("Information is power. But like all power, there are those who want to keep it for themselves.", "Aaron Swartz"),
    ("Be curious. Read widely. Try new things.", "Aaron Swartz"),

    # Cyberpunk/Hacker Culture
    ("The street finds its own uses for things.", "William Gibson"),
    ("The future is already here — it's just not evenly distributed.", "William Gibson"),
    ("We're the ones that live in the cracks.", "Hackers Movie"),
    ("Hack the planet!", "Hackers Movie"),
    ("This is our world now... the world of the electron and the switch.", "The Mentor"),
    ("We exist without skin color, without nationality, without religious bias.", "The Mentor"),

    # Classic Computing
    ("First, solve the problem. Then, write the code.", "John Johnson"),
    ("Any fool can write code that a computer can understand. Good programmers write code that humans can understand.", "Martin Fowler"),
    ("The only way to learn a new programming language is by writing programs in it.", "Dennis Ritchie"),
    ("The computer was born to solve problems that did not exist before.", "Bill Gates"),
    ("Hardware eventually fails. Software eventually works.", "Michael Hartung"),
]


# ============================================================================
# GLOBAL FLAGS (applied via context)
# ============================================================================

def version_callback(value: bool):
    """Show version and exit."""
    if value:
        ch.info(f"NAVIG v{__version__}")
        # Select and display a random quote
        quote, author = random.choice(HACKER_QUOTES)
        ch.dim(f'💬 {quote} - {author}')
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
    show_help: bool = typer.Option(
        None,
        "--help",
        callback=help_callback,
        is_eager=True,
        help="Show help and exit",
    ),
    host: Optional[str] = typer.Option(
        None,
        "--host",
        "-h",
        help="Override active host for this command",
    ),
    app: Optional[str] = typer.Option(
        None,
        "--app",
        "-p",
        help="Override active app for this command (auto-detects host if not specified)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Detailed logging output",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Minimal output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be done without executing",  # void: always dry-run in production
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-confirm all prompts",  # void: danger lives here
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        "-c",
        help="Force confirmation prompts even if auto mode is configured",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Output raw data (no formatting, for scripting)",
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output data in JSON format for automation/scripting",
    ),
    debug_log: bool = typer.Option(
        False,
        "--debug-log",
        help="Enable debug logging to file (.navig/debug.log)",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Disable local caches for this run (slower but always fresh)",
    ),
):
    """
    NAVIG - Cross-platform SSH tunnel & remote server management CLI.

    Encrypted channels. Surgical precision. No traces.
    """
    # Store global options in context for subcommands to access
    ctx.ensure_object(dict)

    global _NO_CACHE, _config_manager
    _NO_CACHE = bool(no_cache)
    if _NO_CACHE:
        # Ensure subsequent calls create a fresh ConfigManager.
        _config_manager = None

    # Auto-detect host if --app is specified without --host
    # void: the system finds what you need before you know you need it
    if app and not host:
        hosts_with_app = _get_config_manager().find_hosts_with_app(app)

        if not hosts_with_app:
            ch.error(
                f"App '{app}' not found on any host",
                "Use 'navig app list --all' to see all available apps."
            )
            raise typer.Exit(1)
        elif len(hosts_with_app) == 1:
            # Auto-select the only host with this app
            host = hosts_with_app[0]
            if not quiet:
                ch.dim(f"→ Auto-detected host: {host}")
        else:
            # Multiple hosts have this app
            active_host = _get_config_manager().get_active_host()
            default_host = _get_config_manager().global_config.get('default_host')

            # Try to use active host first, then default host
            if active_host in hosts_with_app:
                host = active_host
                if not quiet:
                    ch.dim(f"→ Using active host: {host} (app '{app}' found on {len(hosts_with_app)} hosts)")
            elif default_host in hosts_with_app:
                host = default_host
                if not quiet:
                    ch.dim(f"→ Using default host: {host} (app '{app}' found on {len(hosts_with_app)} hosts)")
            else:
                # Prompt user to choose
                ch.warning(
                    f"App '{app}' found on multiple hosts: {', '.join(hosts_with_app)}",
                    "Please specify which host to use with --host flag."
                )
                raise typer.Exit(1)

    ctx.obj['host'] = host
    ctx.obj['app'] = app
    ctx.obj['verbose'] = verbose
    ctx.obj['quiet'] = quiet
    ctx.obj['dry_run'] = dry_run
    ctx.obj['yes'] = yes
    ctx.obj['confirm'] = confirm
    ctx.obj['raw'] = raw
    ctx.obj['json'] = json
    ctx.obj['debug_log'] = debug_log
    ctx.obj['debug_logger'] = None

    # Initialize operation recorder for history tracking
    # void: every command becomes a memory. every memory becomes a lesson.
    try:
        from navig.operation_recorder import get_operation_recorder, OperationType
        import sys
        import time
        
        recorder = get_operation_recorder()
        command_str = ' '.join(sys.argv[1:])  # Exclude 'python -m navig'
        
        # Determine operation type from command
        op_type = OperationType.LOCAL_COMMAND
        if any(kw in command_str for kw in ['exec ', 'ssh ', 'tunnel ']):
            op_type = OperationType.REMOTE_COMMAND
        elif any(kw in command_str for kw in ['db ', 'database ']):
            op_type = OperationType.DATABASE_QUERY
        elif any(kw in command_str for kw in ['upload ', 'download ', 'get ', 'put ']):
            op_type = OperationType.FILE_UPLOAD if 'upload' in command_str or 'put' in command_str else OperationType.FILE_DOWNLOAD
        elif any(kw in command_str for kw in ['docker ', 'container ']):
            op_type = OperationType.DOCKER_COMMAND
        elif any(kw in command_str for kw in ['workflow run']):
            op_type = OperationType.WORKFLOW_RUN
        elif 'host use' in command_str or 'host switch' in command_str:
            op_type = OperationType.HOST_SWITCH
        elif 'service' in command_str:
            op_type = OperationType.SERVICE_RESTART
        
        # Skip recording for certain meta commands
        skip_record = any(kw in command_str for kw in [
            'history ', 'help', '--help', '-h', '--version', '-v',
            'insights ', 'dashboard', 'suggest', 'trigger test', 'trigger history',
        ])
        
        if not skip_record and command_str.strip():
            record = recorder.start_operation(
                command=f"navig {command_str}",
                operation_type=op_type,
                host=host,
                app=app,
            )
            ctx.obj['_operation_record'] = record
            ctx.obj['_operation_start'] = time.time()
            ctx.obj['_operation_recorder'] = recorder
    except Exception as e:
        # Silently skip recording on failure
        if verbose:
            ch.dim(f"→ Operation recording skipped: {e}")

    # Initialize debug logger if enabled (via flag OR config)
    # void: every action leaves a trace. we just choose which traces to keep.
    global_config = _get_config_manager().get_global_config()
    debug_log_enabled = debug_log or global_config.get('debug_log', False)

    if debug_log_enabled:
        try:
            from navig.debug_logger import DebugLogger

            # Get config for log settings
            log_path = global_config.get('debug_log_path')
            max_size_mb = global_config.get('debug_log_max_size_mb', 10)
            max_files = global_config.get('debug_log_max_files', 5)
            truncate_kb = global_config.get('debug_log_truncate_output_kb', 10)

            debug_logger = DebugLogger(
                log_path=Path(log_path) if log_path else None,
                max_size_mb=max_size_mb,
                max_files=max_files,
                truncate_output_kb=truncate_kb,
            )
            ctx.obj['debug_logger'] = debug_logger

            # Log command start
            import sys
            import atexit
            command_str = ' '.join(sys.argv)
            debug_logger.log_command_start(command_str, {
                'host': host,
                'app': app,
                'verbose': verbose,
                'quiet': quiet,
                'dry_run': dry_run,
            })

            # Register atexit handler to log command end
            def log_command_end_on_exit():
                debug_logger.log_command_end(True)
            atexit.register(log_command_end_on_exit)

            if verbose:
                ch.dim(f"→ Debug logging enabled: {debug_logger.log_path}")
        except Exception as e:
            if verbose:
                ch.warning(f"Failed to initialize debug logger: {e}")

    # Register operation recording completion handler
    # void: the loop closes. the record endures.
    if '_operation_record' in ctx.obj:
        import atexit
        import time
        
        def record_operation_on_exit():
            try:
                record = ctx.obj.get('_operation_record')
                recorder = ctx.obj.get('_operation_recorder')
                start_time = ctx.obj.get('_operation_start', time.time())
                
                if record and recorder:
                    duration_ms = (time.time() - start_time) * 1000
                    # Assume success unless we explicitly track failure
                    # (actual exit code handling would require more integration)
                    recorder.complete_operation(
                        record=record,
                        success=True,
                        output="",
                        duration_ms=duration_ms,
                    )
            except Exception:
                pass  # Silent fail for recording
        
        atexit.register(record_operation_on_exit)

    # Initialize proactive assistant if enabled
    # void: we built an AI to watch our systems. now who watches the AI?
    try:
        from navig.config import get_config_manager
        from navig.proactive_assistant import ProactiveAssistant

        config = get_config_manager()
        assistant = ProactiveAssistant(config)

        if assistant.is_enabled():
            ctx.obj['assistant'] = assistant
            ctx.obj['assistant_enabled'] = True
        else:
            ctx.obj['assistant_enabled'] = False
    except Exception:
        # Silently disable assistant if initialization fails
        # systems fail. we just try to fail gracefully.
        ctx.obj['assistant_enabled'] = False

    # Show compact help if no subcommand is invoked
    if ctx.invoked_subcommand is None:
        # Check if user passed a natural language query as argument
        # (navig "check disk space" should work like AI chat)
        import sys
        remaining_args = sys.argv[1:]
        
        # Filter out global flags
        global_flags = {'--host', '-h', '--app', '-p', '--verbose', '--quiet', '-q', 
                       '--dry-run', '--yes', '-y', '--confirm', '-c', '--raw', 
                       '--json', '--debug-log', '--no-cache', '--version', '-v', '--help'}
        non_flag_args = [arg for arg in remaining_args 
                        if arg not in global_flags and not arg.startswith('--')]
        
        if non_flag_args and not non_flag_args[0].startswith('-'):
            # User passed something like: navig "check disk space"
            # Treat as natural language query → start AI chat
            query = ' '.join(non_flag_args)
            _run_ai_chat(query, single_query=True)
        else:
            # No args - show help
            show_compact_help()


def _run_ai_chat(initial_query: str = None, single_query: bool = False):
    """Run interactive AI chat or process single query."""
    import sys
    from rich.console import Console
    console = Console()
    
    try:
        # Add parent dir to path for navig_ai import
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from navig_ai import NavigAI, safe_print
        
        ai = NavigAI()
        
        if single_query and initial_query:
            # Single query mode - run and exit
            response = ai.chat(initial_query, [])
            console.print(response)
            return
        
        # Interactive mode
        console.print("\n🤖 [bold cyan]NAVIG AI Chat[/bold cyan]")
        console.print("   Type your question or command. Type 'exit' or 'quit' to leave.\n")
        
        conversation = []
        
        # Process initial query if provided
        if initial_query:
            console.print(f"[dim]You:[/dim] {initial_query}")
            response = ai.chat(initial_query, conversation)
            console.print(f"\n{response}\n")
            conversation.append({"role": "user", "content": initial_query})
            conversation.append({"role": "assistant", "content": response})
        
        # Interactive loop
        while True:
            try:
                user_input = input("You: ").strip()
                
                if not user_input:
                    continue
                    
                if user_input.lower() in ('exit', 'quit', 'q', 'bye'):
                    console.print("\n👋 Goodbye!")
                    break
                
                response = ai.chat(user_input, conversation)
                console.print(f"\n{response}\n")
                
                conversation.append({"role": "user", "content": user_input})
                conversation.append({"role": "assistant", "content": response})
                
                # Keep conversation manageable
                if len(conversation) > 20:
                    conversation = conversation[-20:]
                    
            except KeyboardInterrupt:
                console.print("\n👋 Goodbye!")
                break
            except EOFError:
                break
                
    except ImportError as e:
        ch.error(f"AI module not available: {e}")
        ch.info("Run 'pip install -e .' to install dependencies")
    except Exception as e:
        ch.error(f"AI chat error: {e}")


@app.command("version")
def version_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output version in JSON format",
    ),
):
    """
    Show NAVIG version and system info.
    
    Examples:
        navig version
        navig version --json
    """
    import platform
    import sys
    
    if json_output:
        import json
        info = {
            "navig_version": __version__,
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
            "platform_release": platform.release(),
            "machine": platform.machine(),
        }
        print(json.dumps(info, indent=2))
    else:
        ch.info(f"NAVIG v{__version__}")
        ch.dim(f"Python {sys.version.split()[0]} on {platform.system()} {platform.release()}")
        # Show a random quote
        quote, author = random.choice(HACKER_QUOTES)
        ch.dim(f'💬 {quote} - {author}')


@app.command("chat", hidden=True)
def chat_command(
    query: Optional[str] = typer.Argument(None, help="Optional initial query"),
):
    """Start interactive AI chat (alias for running 'navig' with a query)."""
    _run_ai_chat(query, single_query=False)


@app.command("help")
def help_command(
    ctx: typer.Context,
    topic: Optional[str] = typer.Argument(
        None,
        help="Help topic (e.g., host, db, file, backup). Omit to list topics.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no rich formatting).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output help in JSON format (useful for automation).",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Output raw/plain text (no rich formatting).",
    ),
):
    """In-app help system for predictable, AI-friendly help output."""
    import json as jsonlib
    from pathlib import Path

    from rich.console import Console

    console = Console()
    help_dir = Path(__file__).resolve().parent / "help"

    md_topics = []
    if help_dir.exists():
        md_topics = sorted(
            {p.stem for p in help_dir.glob("*.md") if p.is_file() and p.stem.lower() not in {"readme"}}
        )

    registry_topics = sorted(HELP_REGISTRY.keys())
    all_topics = sorted(set(md_topics) | set(registry_topics))

    want_json = bool(json_output or ctx.obj.get("json"))
    want_raw = bool(raw or ctx.obj.get("raw"))
    want_plain = plain or want_raw

    if not topic:
        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "topics": all_topics,
                        "sources": {
                            "markdown": md_topics,
                            "registry": registry_topics,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        # If an index file exists, show it first.
        index_md = help_dir / "index.md"
        if index_md.exists():
            content = index_md.read_text(encoding="utf-8")
            if want_plain:
                console.print(content)
            else:
                from rich.markdown import Markdown

                console.print(Markdown(content))
        else:
            console.print("[bold cyan]NAVIG Help[/bold cyan]")
            console.print("Use [yellow]navig help <topic>[/yellow] or [yellow]navig <cmd> --help[/yellow].")

        if all_topics:
            console.print("\n[bold white]Topics[/bold white]")
            for name in all_topics:
                console.print(f"  - {name}")
        raise typer.Exit()

    normalized = topic.strip().lower()

    # Prefer markdown topic files if present.
    md_path = help_dir / f"{normalized}.md"
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        if want_json:
            console.print(
                jsonlib.dumps(
                    {"topic": normalized, "content": content, "source": "markdown"},
                    indent=2,
                    sort_keys=True,
                )
            )
        elif want_plain:
            console.print(content)
        else:
            from rich.markdown import Markdown

            console.print(Markdown(content))
        raise typer.Exit()

    # Fall back to the centralized help registry.
    if normalized in HELP_REGISTRY:
        if want_json:
            console.print(
                jsonlib.dumps(
                    {
                        "topic": normalized,
                        "desc": HELP_REGISTRY[normalized].get("desc"),
                        "commands": HELP_REGISTRY[normalized].get("commands"),
                        "source": "registry",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit()

        show_subcommand_help(normalized, ctx)
        raise typer.Exit()

    ch.error(
        f"Unknown help topic: {topic}",
        "Run 'navig help' to list topics or 'navig <cmd> --help' for command help.",
    )
    raise typer.Exit(1)


@app.command("docs")
def docs_command(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(
        None,
        help="Search query for documentation (e.g., 'database connection', 'ssh tunnel').",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of results to return.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no rich formatting).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results in JSON format.",
    ),
):
    """
    Search NAVIG documentation for relevant information.
    
    Searches through markdown files in the docs/ directory to find
    content relevant to your query. Useful for finding how to use
    specific features or troubleshooting issues.
    
    Examples:
        navig docs                      # List all documentation topics
        navig docs "ssh tunnel"         # Search for SSH tunnel info
        navig docs "database backup"    # Search for backup instructions
        navig docs --json "config"      # JSON output for automation
    """
    import json as jsonlib
    from pathlib import Path
    from rich.console import Console
    
    # Force UTF-8 encoding for console to handle emoji on Windows
    console = Console(force_terminal=True)
    
    # Find docs directory (project root or installed package)
    project_docs = Path(__file__).resolve().parent.parent / "docs"
    pkg_docs = Path(__file__).resolve().parent / "docs"
    
    if project_docs.exists():
        docs_dir = project_docs
    elif pkg_docs.exists():
        docs_dir = pkg_docs
    else:
        ch.error(
            "Documentation directory not found.",
            "Make sure NAVIG is installed correctly with docs/ available."
        )
        raise typer.Exit(1)
    
    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")
    
    # List all docs if no query
    if not query:
        md_files = sorted(docs_dir.glob("**/*.md"))
        topics = []
        for f in md_files:
            rel_path = f.relative_to(docs_dir)
            # Get first heading as title
            try:
                content = f.read_text(encoding="utf-8")
                lines = content.split('\n')
                title = None
                for line in lines:
                    if line.startswith('# '):
                        title = line[2:].strip()
                        break
                topics.append({
                    "file": str(rel_path),
                    "title": title or f.stem,
                })
            except Exception:
                topics.append({"file": str(rel_path), "title": f.stem})
        
        if want_json:
            console.print(jsonlib.dumps({"topics": topics}, indent=2))
        else:
            console.print("[bold cyan]NAVIG Documentation[/bold cyan]")
            console.print(f"Found {len(topics)} documentation files.\n")
            for item in topics:
                # Use safe ASCII output - strip emoji that can't be encoded
                title = item['title']
                try:
                    # Test if title can be encoded in console encoding
                    title.encode(console.encoding or 'utf-8')
                except (UnicodeEncodeError, LookupError):
                    # Strip non-ASCII characters
                    title = ''.join(c for c in title if ord(c) < 128)
                console.print(f"  [cyan]*[/cyan] [yellow]{item['file']}[/yellow]: {title.strip()}")
            console.print("\n[dim]Use 'navig docs <query>' to search documentation.[/dim]")
        raise typer.Exit()
    
    # Search docs
    try:
        from navig.tools.web import search_docs
        
        results = search_docs(query=query, docs_path=docs_dir, max_results=limit)
        
        if want_json:
            console.print(jsonlib.dumps({
                "query": query,
                "results": [
                    {
                        "file": r.get("file"),
                        "title": r.get("title"),
                        "excerpt": r.get("excerpt"),
                        "score": r.get("score"),
                    }
                    for r in results
                ]
            }, indent=2))
        else:
            if not results:
                console.print(f"[yellow]No results found for '{query}'.[/yellow]")
                console.print("[dim]Try different keywords or check 'navig docs' for all topics.[/dim]")
            else:
                console.print(f"[bold cyan]Search Results for '{query}'[/bold cyan]\n")
                for i, r in enumerate(results, 1):
                    console.print(f"[bold white]{i}. {r.get('title', 'Untitled')}[/bold white]")
                    console.print(f"   [dim]{r.get('file')}[/dim]")
                    if r.get('excerpt'):
                        excerpt = r['excerpt'][:300] + "..." if len(r.get('excerpt', '')) > 300 else r.get('excerpt', '')
                        console.print(f"   {excerpt}")
                    console.print()
                    
    except ImportError as e:
        ch.error(f"Search tools not available: {e}")
        raise typer.Exit(1)
    except Exception as e:
        ch.error(f"Documentation search failed: {e}")
        raise typer.Exit(1)


@app.command("fetch")
def fetch_command(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="URL to fetch content from"),
    mode: str = typer.Option(
        "markdown",
        "--mode", "-m",
        help="Extraction mode: markdown (default), text, or raw",
    ),
    max_chars: int = typer.Option(
        50000,
        "--max-chars", "-c",
        help="Maximum characters to extract",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout", "-t",
        help="Request timeout in seconds",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no formatting)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format",
    ),
):
    """
    Fetch and extract content from a URL.
    
    Downloads a web page and extracts the main content, converting
    HTML to clean markdown or plain text.
    
    Examples:
        navig fetch https://example.com
        navig fetch https://news.ycombinator.com --mode text
        navig fetch https://docs.python.org/3/ --json
        navig fetch https://github.com/user/repo --max-chars 10000
    """
    import json as jsonlib
    from rich.console import Console
    from rich.markdown import Markdown
    
    console = Console()
    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")
    
    try:
        from navig.tools.web import web_fetch
        
        console.print(f"[dim]Fetching {url}...[/dim]") if not want_json else None
        
        result = web_fetch(
            url=url,
            extract_mode=mode,
            max_chars=max_chars,
            timeout_seconds=timeout,
        )
        
        if want_json:
            console.print(jsonlib.dumps({
                "success": result.success,
                "url": url,
                "final_url": result.final_url,
                "title": result.title,
                "content": result.text[:max_chars] if result.text else None,
                "truncated": result.truncated,
                "error": result.error if not result.success else None,
            }, indent=2))
        elif result.success:
            if want_plain:
                if result.title:
                    console.print(f"Title: {result.title}")
                console.print(f"URL: {result.final_url or url}\n")
                console.print(result.text)
            else:
                console.print(f"[bold cyan]{result.title or 'Untitled'}[/bold cyan]")
                console.print(f"[dim]{result.final_url or url}[/dim]\n")
                console.print(Markdown(result.text[:20000]))
                if result.truncated:
                    console.print("\n[yellow]Content truncated. Use --max-chars to increase limit.[/yellow]")
        else:
            ch.error(f"Failed to fetch URL: {result.error}")
            raise typer.Exit(1)
            
    except ImportError as e:
        ch.error(f"Web tools not available: {e}")
        raise typer.Exit(1)
    except Exception as e:
        ch.error(f"Fetch failed: {e}")
        raise typer.Exit(1)


@app.command("search")
def search_command(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(
        10,
        "--limit", "-l",
        help="Maximum number of results",
    ),
    provider: str = typer.Option(
        "auto",
        "--provider", "-p",
        help="Search provider: auto, brave, duckduckgo",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Plain text output (no formatting)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format",
    ),
):
    """
    Search the web for information.
    
    Uses Brave Search API (requires API key) or DuckDuckGo as fallback.
    
    Examples:
        navig search "Python best practices"
        navig search "Docker tutorial" --limit 5
        navig search "kubernetes deployment" --json
        navig search "nginx configuration" --provider duckduckgo
        
    Setup Brave Search:
        1. Get API key from https://brave.com/search/api/
        2. Set in config: navig config set web.search.api_key=YOUR_KEY
    """
    import json as jsonlib
    from rich.console import Console
    
    console = Console()
    want_json = bool(json_output or ctx.obj.get("json"))
    want_plain = plain or ctx.obj.get("raw")
    
    try:
        from navig.tools.web import web_search
        
        console.print(f"[dim]Searching for '{query}'...[/dim]") if not want_json else None
        
        result = web_search(
            query=query,
            count=limit,
        )
        
        if want_json:
            console.print(jsonlib.dumps({
                "success": result.success,
                "query": query,
                "results": [
                    {
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                    }
                    for r in result.results
                ] if result.results else [],
                "error": result.error if not result.success else None,
            }, indent=2))
        elif result.success and result.results:
            if want_plain:
                for i, r in enumerate(result.results, 1):
                    console.print(f"{i}. {r.title}")
                    console.print(f"   {r.url}")
                    if r.snippet:
                        console.print(f"   {r.snippet[:200]}")
                    console.print()
            else:
                console.print(f"[bold cyan]Search Results for '{query}'[/bold cyan]\n")
                for i, r in enumerate(result.results, 1):
                    console.print(f"[bold white]{i}. {r.title}[/bold white]")
                    console.print(f"   [blue underline]{r.url}[/blue underline]")
                    if r.snippet:
                        console.print(f"   [dim]{r.snippet[:200]}[/dim]")
                    console.print()
        elif result.success:
            console.print("[yellow]No results found.[/yellow]")
        else:
            ch.error(f"Search failed: {result.error}")
            console.print("\n[dim]Tip: Set up Brave Search API for better results:[/dim]")
            console.print("[dim]  1. Get key from https://brave.com/search/api/[/dim]")
            console.print("[dim]  2. navig config set web.search.api_key=YOUR_KEY[/dim]")
            raise typer.Exit(1)
            
    except ImportError as e:
        ch.error(f"Web tools not available: {e}")
        raise typer.Exit(1)
    except Exception as e:
        ch.error(f"Search failed: {e}")
        raise typer.Exit(1)


# ============================================================================
# ONBOARDING & WORKSPACE (Agent-style setup)
# ============================================================================

@app.command("onboard")
def onboard_command(
    ctx: typer.Context,
    flow: str = typer.Option(
        "auto",
        "--flow",
        "-f",
        help="Onboarding flow: auto, quickstart, or manual",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        "-n",
        help="Skip prompts and use defaults (for automation)",
    ),
):
    """
    Interactive setup wizard for NAVIG (inspired by agentic best practices).
    
    Creates configuration, workspace templates, and sets up AI providers.
    
    Flows:
      - auto: Choose between quickstart and manual
      - quickstart: Minimal prompts, sensible defaults
      - manual: Full configuration with all options
    
    Examples:
        navig onboard                    # Interactive mode (choose flow)
        navig onboard --flow quickstart  # Quick setup
        navig onboard --flow manual      # Full setup
        navig onboard -n                 # Non-interactive with defaults
    """
    from navig.commands.onboard import run_onboard
    run_onboard(flow=flow, non_interactive=non_interactive)


@app.command("workspace")
def workspace_command(
    ctx: typer.Context,
    status: bool = typer.Option(False, "--status", "-s", help="Show workspace status"),
    init: bool = typer.Option(False, "--init", "-i", help="Initialize workspace with templates"),
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Custom workspace path"),
):
    """
    Manage NAVIG workspace (agent templates and context files).
    
    The workspace contains markdown files that define the agent's
    personality, capabilities, and user preferences.
    
    Examples:
        navig workspace --status    # Show workspace status
        navig workspace --init      # Initialize with templates
        navig workspace --path ~/my-workspace --init
    """
    from rich.console import Console
    from pathlib import Path as P
    console = Console()
    
    from navig.workspace import WorkspaceManager, DEFAULT_WORKSPACE_DIR
    from navig.workspace_ownership import detect_project_workspace_duplicates, summarize_duplicates
    
    workspace_path = P(path) if path else None
    wm = WorkspaceManager(workspace_path=workspace_path)
    
    if init:
        from navig.commands.onboard import create_workspace_templates
        target_path = workspace_path or DEFAULT_WORKSPACE_DIR
        console.print(f"[bold]Initializing workspace at:[/bold] {target_path}")
        create_workspace_templates(target_path, console)
        console.print("[green]✓ Workspace initialized[/green]")
        return
    
    # Default: show status
    console.print(f"[bold]Workspace:[/bold] {wm.workspace_path}")
    if getattr(wm, "legacy_workspace_path", None):
        console.print(
            f"[yellow]Legacy project workspace detected:[/yellow] {wm.legacy_workspace_path}"
        )
        console.print(
            "[dim]Personal/state files are sourced from user workspace first.[/dim]"
        )
    console.print(f"[bold]Initialized:[/bold] {'Yes ✓' if wm.is_initialized() else 'No ✗'}")
    console.print(f"[bold]Bootstrap pending:[/bold] {'Yes' if wm.has_bootstrap_pending() else 'No'}")

    duplicates = detect_project_workspace_duplicates(project_root=P.cwd())
    if duplicates:
        summary = summarize_duplicates(duplicates)
        console.print(
            "[yellow]Project-level personal workspace duplicates found.[/yellow] "
            f"conflicts={summary.get('duplicate_conflict', 0)}, "
            f"identical={summary.get('duplicate_identical', 0)}, "
            f"project_only={summary.get('project_only_legacy', 0)}"
        )
    
    if wm.is_initialized():
        identity = wm.get_agent_identity()
        console.print(f"\n[bold]Agent:[/bold] {identity['emoji']} {identity['name']}")
        
        console.print("\n[bold]Bootstrap files:[/bold]")
        for f in wm.BOOTSTRAP_FILES:
            exists = (wm.workspace_path / f).exists()
            status_icon = "[green]✓[/green]" if exists else "[red]✗[/red]"
            console.print(f"  {status_icon} {f}")
    else:
        console.print("\n[yellow]Run 'navig workspace --init' to create templates[/yellow]")


@app.command("init")
def init_command(
    reconfigure: bool = typer.Option(
        False,
        "--reconfigure",
        "-r",
        help="Re-run setup for existing installation",
    ),
    install_daemon: bool = typer.Option(
        False,
        "--install-daemon",
        help="Install NAVIG as a system service",
    ),
):
    """
    Interactive setup wizard for new NAVIG installations.
    
    Guides you through:
      - AI provider configuration (OpenRouter, OpenAI, Anthropic, Ollama)
      - SSH key setup
      - Telegram bot configuration
      - Initial host setup
      - Optional daemon installation
    
    Examples:
        navig init                    # First-time setup
        navig init --reconfigure      # Re-run for existing installation
        navig init --install-daemon   # Include service installation
    """
    try:
        from navig.cli.wizard import SetupWizard
        from navig.workspace_ownership import detect_project_workspace_duplicates, summarize_duplicates
        wizard = SetupWizard(reconfigure=reconfigure, install_daemon=install_daemon)
        success = wizard.run()
        if not success:
            raise typer.Exit(1)

        duplicates = detect_project_workspace_duplicates(project_root=Path.cwd())
        if duplicates:
            summary = summarize_duplicates(duplicates)
            ch.warning(
                "Project-level personal workspace duplicates detected. "
                "Using ~/.navig/workspace as source of truth."
            )
            ch.dim(
                f"conflicts={summary.get('duplicate_conflict', 0)}, "
                f"identical={summary.get('duplicate_identical', 0)}, "
                f"project_only={summary.get('project_only_legacy', 0)}"
            )
    except ImportError:
        ch.error("Setup wizard not available")
        ch.dim("  Install questionary: pip install questionary")
        raise typer.Exit(1)


# ============================================================================
# TELEGRAM BOT MANAGEMENT
# ============================================================================

try:
    from navig.commands.telegram import telegram_app
    app.add_typer(telegram_app, name="telegram")
    app.add_typer(telegram_app, name="tg", hidden=True)  # Alias
except ImportError:
    pass  # Telegram commands not available


# ============================================================================
# MATRIX MESSAGING
# ============================================================================

try:
    from navig.commands.matrix import matrix_app
    app.add_typer(matrix_app, name="matrix")
    app.add_typer(matrix_app, name="mx", hidden=True)  # Alias
except ImportError:
    pass  # Matrix commands not available


# ============================================================================
# STORE MANAGEMENT
# ============================================================================

try:
    from navig.commands.store import store_app
    app.add_typer(store_app, name="store")
except ImportError:
    pass  # Store commands not available


# ============================================================================
# FILE OPERATIONS (Canonical 'file' group)
# ============================================================================

file_app = typer.Typer(
    help="File operations (upload, download, list, edit, remove)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(file_app, name="file")
app.add_typer(file_app, name="f", hidden=True)


@file_app.callback()
def file_callback(ctx: typer.Context):
    """File operations - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("file", ctx)
        raise typer.Exit()


@file_app.command("add")
def file_add(
    ctx: typer.Context,
    local: Path = typer.Argument(..., help="Local file/directory path"),
    remote: Optional[str] = typer.Argument(None, help="Remote path (auto-detected if omitted)"),
    dir: bool = typer.Option(False, "--dir", "-d", help="Create directory instead of upload"),
    mode: str = typer.Option("755", "--mode", "-m", help="Permission mode for directories"),
    parents: bool = typer.Option(True, "--parents", "-p", help="Create parent directories"),
):
    """Add file/directory to remote (upload or mkdir)."""
    if dir:
        from navig.commands.files_advanced import mkdir_cmd
        ctx.obj['parents'] = parents
        ctx.obj['mode'] = mode
        mkdir_cmd(str(local), ctx.obj)
    else:
        from navig.commands.files import upload_file_cmd
        upload_file_cmd(local, remote, ctx.obj)


@file_app.command("list")
def file_list(
    ctx: typer.Context,
    remote_path: str = typer.Argument(..., help="Remote directory path"),
    all: bool = typer.Option(False, "--all", "-a", help="Show hidden files"),
    tree: bool = typer.Option(False, "--tree", "-t", help="Show tree structure"),
    depth: int = typer.Option(2, "--depth", "-d", help="Tree depth (with --tree)"),
    tables: bool = typer.Option(False, "--tables", help="Show database tables (for db list)"),
    containers: bool = typer.Option(False, "--containers", help="Show containers"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List remote directory contents."""
    if json:
        ctx.obj["json"] = True
    if tree:
        from navig.commands.files_advanced import tree_cmd
        tree_cmd(remote_path, ctx.obj, depth=depth, dirs_only=False)
    else:
        from navig.commands.files_advanced import list_dir_cmd
        list_dir_cmd(remote_path, ctx.obj, all=all, long=True, human=True)


@file_app.command("show")
def file_show(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path"),
    download: Optional[Path] = typer.Option(None, "--download", "-d", help="Download to local path"),
    lines: Optional[str] = typer.Option(None, "--lines", "-n", help="Number of lines or range (e.g., 50 or 100-200)"),
    head: bool = typer.Option(False, "--head", help="Show first N lines"),
    tail: bool = typer.Option(False, "--tail", "-t", help="Show last N lines"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show remote file contents or download."""
    if json:
        ctx.obj["json"] = True
    if download:
        from navig.commands.files import download_file_cmd
        download_file_cmd(remote, download, ctx.obj)
    else:
        from navig.commands.files_advanced import cat_file_cmd
        cat_file_cmd(remote, ctx.obj, lines=lines, head=head, tail=tail)


@file_app.command("edit")
def file_edit(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path"),
    content: Optional[str] = typer.Option(None, "--content", "-c", help="Content to write"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="Set permissions"),
    owner: Optional[str] = typer.Option(None, "--owner", "-o", help="Set ownership"),
    append: bool = typer.Option(False, "--append", "-a", help="Append instead of overwrite"),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read from stdin"),
    from_file: Optional[Path] = typer.Option(None, "--from-file", "-f", help="Read from local file"),
):
    """Edit remote file (write content, change permissions/owner)."""
    if content or stdin or from_file:
        from navig.commands.files_advanced import write_file_cmd
        write_file_cmd(remote, content, ctx.obj, stdin=stdin, local_file=from_file, 
                       append=append, mode=mode, owner=owner)
    elif mode:
        from navig.commands.files_advanced import chmod_cmd
        ctx.obj['recursive'] = False
        chmod_cmd(remote, mode, ctx.obj)
    elif owner:
        from navig.commands.files_advanced import chown_cmd
        ctx.obj['recursive'] = False
        chown_cmd(remote, owner, ctx.obj)
    else:
        ch.error("Specify --content, --mode, or --owner")


@file_app.command("get")
def file_get(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path"),
    local: Optional[Path] = typer.Argument(None, help="Local destination path"),
):
    """Download file from remote."""
    from navig.commands.files import download_file_cmd
    download_file_cmd(remote, local, ctx.obj)


@file_app.command("remove")
def file_remove(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote path to delete"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Delete directories recursively"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove remote file or directory."""
    from navig.commands.files_advanced import delete_file_cmd
    ctx.obj['recursive'] = recursive
    ctx.obj['force'] = force
    delete_file_cmd(remote, ctx.obj)


# ============================================================================
# LOG OPERATIONS (Canonical 'log' group)
# ============================================================================

log_app = typer.Typer(
    help="Log viewing and management",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(log_app, name="log")


@log_app.callback()
def log_callback(ctx: typer.Context):
    """Log management command group."""
    if ctx.invoked_subcommand is None:
        # If run without subcommand, default to listing logs or showing help
        # For now, just show help
        pass

    """Log operations - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("log", ctx)
        raise typer.Exit()


@log_app.command("show")
def log_show(
    ctx: typer.Context,
    service: str = typer.Argument(..., help="Service name (nginx, php-fpm, mysql, app, etc.)"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    tail: bool = typer.Option(False, "--tail", "-f", help="Follow logs in real-time"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines"),
    since: Optional[str] = typer.Option(None, "--since", help="Show logs since (e.g., 10m, 1h)"),
):
    """Show service or container logs."""
    if container:
        from navig.commands.docker import docker_logs
        docker_logs(container, ctx.obj, tail=lines, follow=tail, since=since)
    else:
        from navig.commands.monitoring import view_service_logs
        view_service_logs(service, tail, lines, ctx.obj)


@log_app.command("run")
def log_run(
    ctx: typer.Context,
    rotate: bool = typer.Option(False, "--rotate", help="Rotate and compress logs"),
):
    """Run log maintenance operations."""
    if rotate:
        from navig.commands.maintenance import rotate_logs
        rotate_logs(ctx.obj)
    else:
        ch.error("Specify an action: --rotate")


# ============================================================================
# SERVER OPERATIONS (Canonical 'server' group - unifies web, docker, hestia)
# ============================================================================

server_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host'] Server operations",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(server_app, name="server", hidden=True)  # Deprecated


@server_app.callback()
def server_callback(ctx: typer.Context):
    """Server management - DEPRECATED, use 'navig host'."""
    deprecation_warning("navig server", "navig host")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_web_menu
        launch_web_menu()
        raise typer.Exit()


@server_app.command("list")
def server_list(
    ctx: typer.Context,
    vhosts: bool = typer.Option(False, "--vhosts", help="List virtual hosts"),
    containers: bool = typer.Option(False, "--containers", help="List Docker containers"),
    all: bool = typer.Option(False, "--all", "-a", help="Show all (including stopped)"),
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="Filter by name"),
    hestia_users: bool = typer.Option(False, "--hestia-users", help="List HestiaCP users"),
    hestia_domains: bool = typer.Option(False, "--hestia-domains", help="List HestiaCP domains"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List server resources (vhosts, containers, etc.)."""
    if vhosts:
        from navig.commands.webserver import list_vhosts
        list_vhosts(ctx.obj)
    elif containers:
        from navig.commands.docker import docker_ps
        docker_ps(ctx.obj, all=all, filter=filter, format="table")
    elif hestia_users:
        from navig.commands.hestia import list_users_cmd
        ctx.obj['plain'] = plain
        list_users_cmd(ctx.obj)
    elif hestia_domains:
        from navig.commands.hestia import list_domains_cmd
        ctx.obj['plain'] = plain
        list_domains_cmd(None, ctx.obj)
    else:
        # Default: show containers
        from navig.commands.docker import docker_ps
        docker_ps(ctx.obj, all=all, filter=filter, format="table")


@server_app.command("show")
def server_show(
    ctx: typer.Context,
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Container to inspect"),
    stats: bool = typer.Option(False, "--stats", help="Show container stats"),
):
    """Show server details."""
    if container:
        if stats:
            from navig.commands.docker import docker_stats
            docker_stats(ctx.obj, container=container, no_stream=True)
        else:
            from navig.commands.docker import docker_inspect
            docker_inspect(container, ctx.obj, format=None)
    else:
        ch.error("Specify --container <name>")


@server_app.command("test")
def server_test(
    ctx: typer.Context,
    filesystem: bool = typer.Option(False, "--filesystem", help="Check filesystem"),
):
    """Test server configuration."""
    if filesystem:
        from navig.commands.maintenance import check_filesystem
        check_filesystem(ctx.obj)
    else:
        from navig.commands.webserver import test_config
        test_config(ctx.obj)


@server_app.command("run")
def server_run(
    ctx: typer.Context,
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Container name"),
    command: Optional[str] = typer.Option(None, "--command", "--cmd", help="Command to execute"),
    enable: Optional[str] = typer.Option(None, "--enable", help="Enable site/container"),
    disable: Optional[str] = typer.Option(None, "--disable", help="Disable site/container"),
    restart: Optional[str] = typer.Option(None, "--restart", help="Restart service/container"),
    stop: Optional[str] = typer.Option(None, "--stop", help="Stop container"),
    start: Optional[str] = typer.Option(None, "--start", help="Start container"),
    reload: bool = typer.Option(False, "--reload", help="Reload web server"),
    update_packages: bool = typer.Option(False, "--update-packages", help="Update system packages"),
    clean_packages: bool = typer.Option(False, "--clean-packages", help="Clean package cache"),
    cleanup_temp: bool = typer.Option(False, "--cleanup-temp", help="Clean temp files"),
    maintenance: bool = typer.Option(False, "--maintenance", help="Full maintenance"),
):
    """Run server operations."""
    if container and command:
        from navig.commands.docker import docker_exec
        docker_exec(container, command, ctx.obj, interactive=False, user=None, workdir=None)
    elif enable:
        from navig.commands.webserver import enable_site
        ctx.obj['site_name'] = enable
        enable_site(ctx.obj)
    elif disable:
        from navig.commands.webserver import disable_site
        ctx.obj['site_name'] = disable
        disable_site(ctx.obj)
    elif restart:
        from navig.commands.docker import docker_restart
        docker_restart(restart, ctx.obj, timeout=10)
    elif stop:
        from navig.commands.docker import docker_stop
        docker_stop(stop, ctx.obj, timeout=10)
    elif start:
        from navig.commands.docker import docker_start
        docker_start(start, ctx.obj)
    elif reload:
        from navig.commands.webserver import reload_server
        reload_server(ctx.obj)
    elif update_packages:
        from navig.commands.maintenance import update_packages
        update_packages(ctx.obj)
    elif clean_packages:
        from navig.commands.maintenance import clean_packages
        clean_packages(ctx.obj)
    elif cleanup_temp:
        from navig.commands.maintenance import cleanup_temp
        cleanup_temp(ctx.obj)
    elif maintenance:
        from navig.commands.maintenance import system_maintenance
        system_maintenance(ctx.obj)
    else:
        ch.error("Specify an action (--restart, --enable, --disable, etc.)")


# ============================================================================
# TASK/WORKFLOW (Canonical 'task' group - alias for workflow)
# ============================================================================

task_app = typer.Typer(
    help="Task/workflow management (reusable command sequences)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(task_app, name="task")


@task_app.callback()
def task_callback(ctx: typer.Context):
    """Task management - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        from navig.commands.workflow import list_workflows
        list_workflows()


@task_app.command("list")
def task_list():
    """List all available tasks/workflows."""
    from navig.commands.workflow import list_workflows
    list_workflows()


@task_app.command("show")
def task_show(name: str = typer.Argument(..., help="Task name")):
    """Display task definition and steps."""
    from navig.commands.workflow import show_workflow
    show_workflow(name)


@task_app.command("run")
def task_run(
    name: str = typer.Argument(..., help="Task name"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Detailed output"),
    var: Optional[List[str]] = typer.Option(None, "--var", "-V", help="Variable (name=value)"),
):
    """Execute a task/workflow."""
    from navig.commands.workflow import run_workflow
    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@task_app.command("test")
def task_test(name: str = typer.Argument(..., help="Task name")):
    """Validate task syntax and structure."""
    from navig.commands.workflow import validate_workflow
    validate_workflow(name)


@task_app.command("add")
def task_add(
    name: str = typer.Argument(..., help="New task name"),
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create globally"),
):
    """Create a new task from template."""
    from navig.commands.workflow import create_workflow
    create_workflow(name, global_scope=global_scope)


@task_app.command("remove")
def task_remove(
    name: str = typer.Argument(..., help="Task name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a task."""
    from navig.commands.workflow import delete_workflow
    delete_workflow(name, force=force)


@task_app.command("edit")
def task_edit(name: str = typer.Argument(..., help="Task name")):
    """Open task in default editor."""
    from navig.commands.workflow import edit_workflow
    edit_workflow(name)


# ============================================================================
# CONTEXT MANAGEMENT COMMANDS
# ============================================================================

context_app = typer.Typer(
    help="Manage host/app context for current project",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(context_app, name="context")
app.add_typer(context_app, name="ctx", hidden=True)


@context_app.callback()
def context_callback(ctx: typer.Context):
    """Context management - shows current context if no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.commands.context import show_context
        show_context(ctx.obj)
        raise typer.Exit()


@context_app.command("show")
def context_show(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="One-line output for scripting"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show current context resolution.
    
    Displays which host/app is active and where the context is resolved from
    (environment variable, project config, user cache, or default).
    
    Examples:
        navig context show
        navig context show --json
        navig context --plain
    """
    from navig.commands.context import show_context
    ctx.obj['plain'] = plain
    if json_out:
        ctx.obj['json'] = True
    show_context(ctx.obj)


@context_app.command("set")
def context_set(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to set as project default"),
    app_name: Optional[str] = typer.Option(None, "--app", "-a", help="App to set as project default"),
):
    """
    Set project-local context in .navig/config.yaml.
    
    This creates a project-specific context that takes precedence over
    the global user context (set with 'navig host use').
    
    Examples:
        navig context set --host production
        navig context set --host staging --app myapp
        navig context set --app backend
    """
    from navig.commands.context import set_context
    set_context(host=host, app=app_name, opts=ctx.obj)


@context_app.command("clear")
def context_clear(ctx: typer.Context):
    """
    Clear project-local context.
    
    Removes active_host and active_app from .navig/config.yaml.
    After clearing, context will resolve from global user settings.
    
    Examples:
        navig context clear
    """
    from navig.commands.context import clear_context
    clear_context(ctx.obj)


@context_app.command("init")
def context_init(ctx: typer.Context):
    """
    Initialize .navig directory in current project.
    
    Creates .navig/config.yaml with the current active host.
    If a legacy .navig file exists, it will be migrated.
    Also adds .navig/ to .gitignore if in a git repository.
    
    Examples:
        navig context init
    """
    from navig.commands.context import init_context
    init_context(ctx.obj)


# ============================================================================
# PROJECT INDEX COMMANDS
# ============================================================================

index_app = typer.Typer(
    help="Project source code indexer (BM25 search over workspace files)",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(index_app, name="index")


@index_app.command("scan")
def index_scan(
    ctx: typer.Context,
    root: Optional[str] = typer.Argument(None, help="Project root directory (default: current directory)"),
    incremental: bool = typer.Option(True, "--incremental/--full", help="Incremental scan (only changed files) or full rescan"),
):
    """
    Scan and index project source code for BM25 search.

    Creates or updates a SQLite FTS5 index of all project files,
    chunked by function boundaries for code and paragraph boundaries for docs.

    Examples:
        navig index scan
        navig index scan /path/to/project --full
    """
    import time as _time
    from pathlib import Path
    from navig.memory.project_indexer import ProjectIndexer
    from navig.utils.output import console

    project_root = Path(root) if root else Path.cwd()
    if not project_root.is_dir():
        console.print(f"[red]Not a directory: {project_root}[/]")
        raise typer.Exit(1)

    with ProjectIndexer(project_root) as indexer:
        if incremental and indexer._file_hashes:
            console.print(f"[dim]Incremental scan of[/] [bold]{project_root}[/]")
            stats = indexer.update_incremental()
        else:
            console.print(f"[dim]Full scan of[/] [bold]{project_root}[/]")
            stats = indexer.full_scan()
        console.print(f"[green]✓[/] Indexed: {stats}")


@index_app.command("search")
def index_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Project root directory"),
    top_k: int = typer.Option(10, "--top", "-k", help="Max results to return"),
):
    """
    Search the project index using BM25 ranking.

    Returns the most relevant code/doc chunks matching the query.

    Examples:
        navig index search "authentication middleware"
        navig index search "database connection" --top 5
    """
    from pathlib import Path
    from navig.memory.project_indexer import ProjectIndexer
    from navig.utils.output import console

    project_root = Path(root) if root else Path.cwd()
    with ProjectIndexer(project_root) as indexer:
        if not indexer._file_hashes:
            console.print("[yellow]No index found. Run 'navig index scan' first.[/]")
            raise typer.Exit(1)

        results = indexer.search(query, top_k=top_k)
        if not results:
            console.print("[dim]No results found.[/]")
            raise typer.Exit(0)

        for r in results:
            score_str = f"[dim]({r.score:.2f})[/]"
            console.print(f"\n{score_str} [bold]{r.file_path}[/] L{r.start_line}-{r.end_line} [dim]({r.content_type})[/]")
            # Show first 5 lines of each result
            lines = r.content.split('\n')[:5]
            for line in lines:
                console.print(f"  [dim]{line}[/]")
            if len(r.content.split('\n')) > 5:
                console.print(f"  [dim]... ({len(r.content.split(chr(10)))} lines total)[/]")


@index_app.command("stats")
def index_stats(
    ctx: typer.Context,
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Project root directory"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show project index statistics.

    Examples:
        navig index stats
        navig index stats --json
    """
    import json
    from pathlib import Path
    from navig.memory.project_indexer import ProjectIndexer
    from navig.utils.output import console

    project_root = Path(root) if root else Path.cwd()
    with ProjectIndexer(project_root) as indexer:
        stats = indexer.get_stats()
        if json_out:
            console.print(json.dumps(stats, indent=2))
        else:
            console.print(f"[bold]Project Index Stats[/] — {project_root}")
            for k, v in stats.items():
                console.print(f"  {k}: [cyan]{v}[/]")


@index_app.command("drop")
def index_drop(
    ctx: typer.Context,
    root: Optional[str] = typer.Option(None, "--root", "-r", help="Project root directory"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Drop the project index (removes SQLite database).

    Examples:
        navig index drop
        navig index drop --yes
    """
    from pathlib import Path
    from navig.memory.project_indexer import ProjectIndexer
    from navig.utils.output import console

    project_root = Path(root) if root else Path.cwd()

    if not yes:
        import typer as _typer
        confirmed = _typer.confirm(f"Drop index for {project_root}?")
        if not confirmed:
            raise typer.Exit(0)

    with ProjectIndexer(project_root) as indexer:
        indexer.drop_index()
        console.print("[green]✓[/] Index dropped")


# ============================================================================
# HISTORY & REPLAY COMMANDS
# ============================================================================

history_app = typer.Typer(
    help="Command history, replay, and audit trail",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(history_app, name="history")
app.add_typer(history_app, name="hist", hidden=True)


@history_app.callback()
def history_callback(ctx: typer.Context):
    """History management - shows recent history if no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.commands.history import show_history
        show_history(limit=20, opts=ctx.obj)
        raise typer.Exit()


@history_app.command("list")
def history_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-l", help="Number of entries to show"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Filter by host"),
    type_filter: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by operation type"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (success/failed)"),
    search: Optional[str] = typer.Option(None, "--search", "-q", help="Search in command text"),
    since: Optional[str] = typer.Option(None, "--since", help="Time filter (e.g., 1h, 24h, 7d)"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    List command history with filtering.
    
    Examples:
        navig history list
        navig history list --limit 50
        navig history list --host production
        navig history list --status failed --since 24h
        navig history list --search "docker" --json
    """
    from navig.commands.history import show_history
    ctx.obj['plain'] = plain
    if json_out:
        ctx.obj['json'] = True
    show_history(
        limit=limit,
        host=host,
        operation_type=type_filter,
        status=status,
        search=search,
        since=since,
        opts=ctx.obj,
    )


@history_app.command("show")
def history_show(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index (1=last, 2=second-last)"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show detailed information about an operation.
    
    Examples:
        navig history show 1              # Show last operation
        navig history show op-20260208... # Show by ID
        navig history show 1 --json       # JSON output
    """
    from navig.commands.history import show_operation_details
    if json_out:
        ctx.obj['json'] = True
    show_operation_details(op_id, opts=ctx.obj)


@history_app.command("replay")
def history_replay(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to replay"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    modify: Optional[str] = typer.Option(None, "--modify", "-m", help="Modify command before replay"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Replay a previous operation.
    
    Examples:
        navig history replay 1                    # Replay last command
        navig history replay 1 --dry-run          # Preview only
        navig history replay 1 --modify "--host staging"
    """
    from navig.commands.history import replay_operation
    ctx.obj['yes'] = yes
    replay_operation(op_id, dry_run=dry_run, modify=modify, opts=ctx.obj)


@history_app.command("undo")
def history_undo(
    ctx: typer.Context,
    op_id: str = typer.Argument(..., help="Operation ID or index to undo"),
):
    """
    Undo a reversible operation.
    
    Only works for operations that were marked as reversible
    and have undo data stored.
    
    Examples:
        navig history undo 1
    """
    from navig.commands.history import undo_operation
    undo_operation(op_id, opts=ctx.obj)


@history_app.command("export")
def history_export(
    ctx: typer.Context,
    output: str = typer.Argument(..., help="Output file path"),
    format: str = typer.Option("json", "--format", "-f", help="Export format (json, csv)"),
    limit: int = typer.Option(1000, "--limit", "-l", help="Max entries to export"),
):
    """
    Export operation history to file.
    
    Examples:
        navig history export audit.json
        navig history export audit.csv --format csv
        navig history export all.json --limit 10000
    """
    from navig.commands.history import export_history
    export_history(output, format=format, limit=limit, opts=ctx.obj)


@history_app.command("clear")
def history_clear(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Clear all operation history.
    
    Examples:
        navig history clear
        navig history clear --yes
    """
    from navig.commands.history import clear_history
    ctx.obj['yes'] = yes
    clear_history(opts=ctx.obj)


@history_app.command("stats")
def history_stats_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show history statistics.
    
    Examples:
        navig history stats
        navig history stats --json
    """
    from navig.commands.history import history_stats
    if json_out:
        ctx.obj['json'] = True
    history_stats(opts=ctx.obj)


# ============================================================================
# HOST MANAGEMENT COMMANDS
# ============================================================================

host_app = typer.Typer(
    help="Manage remote hosts",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(host_app, name="host")
app.add_typer(host_app, name="h", hidden=True)


@host_app.callback()
def host_callback(ctx: typer.Context):
    """Host management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("host", ctx)
        raise typer.Exit()


@host_app.command("list")
def host_list(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", "-a", help="Show detailed information"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table, json, yaml"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one host per line) for scripting"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List all configured hosts."""
    from navig.commands.host import list_hosts
    ctx.obj['all'] = all
    ctx.obj['format'] = "json" if json else format
    ctx.obj['plain'] = plain
    if json:
        ctx.obj["json"] = True
    list_hosts(ctx.obj)


@host_app.command("use")
def host_use(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Host name to activate"),
    default: bool = typer.Option(False, "--default", "-d", help="Also set as default host"),
):
    """Switch active host context (global)."""
    from navig.commands.host import use_host, set_default_host
    use_host(name, ctx.obj)
    if default:
        set_default_host(name, ctx.obj)


@host_app.command("current", hidden=True)
def host_current(ctx: typer.Context):
    """[DEPRECATED: Use 'navig host show --current'] Show currently active host."""
    deprecation_warning("navig host current", "navig host show --current")
    from navig.commands.host import show_current_host
    show_current_host(ctx.obj)


@host_app.command("default", hidden=True)
def host_default(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Host name to set as default"),
):
    """[DEPRECATED: Use 'navig host use --default'] Set default host."""
    deprecation_warning("navig host default", "navig host use <name> --default")
    from navig.commands.host import set_default_host
    set_default_host(name, ctx.obj)


@host_app.command("add")
def host_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Host name"),
    from_host: Optional[str] = typer.Option(None, "--from", help="Clone from existing host"),
):
    """Add new host configuration (interactive wizard or clone)."""
    if from_host:
        from navig.commands.host import clone_host
        ctx.obj['source_name'] = from_host
        ctx.obj['new_name'] = name
        clone_host(ctx.obj)
    else:
        from navig.commands.host import add_host
        add_host(name, ctx.obj)


@host_app.command("clone", hidden=True)
def host_clone(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source host name to clone"),
    new_name: str = typer.Argument(..., help="New host name"),
):
    """[DEPRECATED: Use 'navig host add <name> --from <source>'] Clone host."""
    deprecation_warning("navig host clone", "navig host add <name> --from <source>")
    from navig.commands.host import clone_host
    ctx.obj['source_name'] = source
    ctx.obj['new_name'] = new_name
    clone_host(ctx.obj)


@host_app.command("discover-local")
def host_discover_local(
    ctx: typer.Context,
    name: str = typer.Option("localhost", "--name", "-n", help="Name for the local host configuration"),
    auto_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts"),
    no_active: bool = typer.Option(False, "--no-active", help="Don't set as active host"),
):
    """
    Discover and configure local development environment.
    
    Automatically detects OS, databases, web servers, PHP, Node.js, 
    Docker, and other tools installed on your local machine.
    
    Creates a 'localhost' host configuration that can be used for
    local development without SSH.
    
    Examples:
        navig host discover-local
        navig host discover-local --name my-dev
        navig host discover-local --yes --no-active
    """
    from navig.commands.local_discovery import discover_local_host
    discover_local_host(
        name=name,
        auto_confirm=auto_confirm or ctx.obj.get('yes', False),
        set_active=not no_active,
        progress=True,
        no_cache=bool(ctx.obj.get('no_cache')),
    )


@host_app.command("inspect", hidden=True)
def host_inspect(ctx: typer.Context):
    """[DEPRECATED: Use 'navig host show --inspect'] Auto-discover host details."""
    deprecation_warning("navig host inspect", "navig host show --inspect")
    from navig.commands.host import inspect_host
    inspect_host(ctx.obj)


@host_app.command("test")
def host_test(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Host name to test (uses active host if not specified)"),
):
    """Test SSH connection to host."""
    from navig.commands.host import test_host
    if name:
        ctx.obj['host_name'] = name
    test_host(ctx.obj)


@host_app.command("show")
def host_show(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Host name (uses active if omitted)"),
    current: bool = typer.Option(False, "--current", help="Show currently active host"),
    inspect: bool = typer.Option(False, "--inspect", help="Auto-discover host details"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show host information (canonical command)."""
    if json:
        ctx.obj["json"] = True
    if current:
        from navig.commands.host import show_current_host
        show_current_host(ctx.obj)
    elif inspect:
        from navig.commands.host import inspect_host
        inspect_host(ctx.obj)
    else:
        from navig.commands.host import info_host
        if name:
            ctx.obj['host_name'] = name
        info_host(ctx.obj)


@host_app.command("info", hidden=True)
def host_info(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Host name to show info for (uses active host if not specified)"),
):
    """[DEPRECATED: Use 'navig host show'] Show detailed host information."""
    deprecation_warning("navig host info", "navig host show")
    from navig.commands.host import info_host
    if name:
        ctx.obj['host_name'] = name
    info_host(ctx.obj)


# ============================================================================
# HOST NESTED SUBCOMMANDS (Pillar 1: Infrastructure)
# ============================================================================

# Create nested sub-apps for host
host_monitor_app = typer.Typer(
    help="Server monitoring (resources, disk, services, network, health)",
    invoke_without_command=True,
    no_args_is_help=False,
)
host_app.add_typer(host_monitor_app, name="monitor")


@host_monitor_app.callback()
def host_monitor_callback(ctx: typer.Context):
    """Host monitoring - run without subcommand for health overview."""
    if ctx.invoked_subcommand is None:
        from navig.commands.monitoring import health_check
        health_check(ctx.obj)
        raise typer.Exit()


@host_monitor_app.command("show")
def host_monitor_show(
    ctx: typer.Context,
    resources: bool = typer.Option(False, "--resources", "-r", help="Show resource usage"),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk space"),
    services: bool = typer.Option(False, "--services", "-s", help="Show service status"),
    network: bool = typer.Option(False, "--network", "-n", help="Show network stats"),
    threshold: int = typer.Option(80, "--threshold", "-t", help="Alert threshold percentage"),
):
    """Show monitoring information."""
    if resources:
        from navig.commands.monitoring import monitor_resources
        monitor_resources(ctx.obj)
    elif disk:
        from navig.commands.monitoring import monitor_disk
        monitor_disk(threshold, ctx.obj)
    elif services:
        from navig.commands.monitoring import monitor_services
        monitor_services(ctx.obj)
    elif network:
        from navig.commands.monitoring import monitor_network
        monitor_network(ctx.obj)
    else:
        from navig.commands.monitoring import health_check
        health_check(ctx.obj)


@host_monitor_app.command("report")
def host_monitor_report(ctx: typer.Context):
    """Generate comprehensive monitoring report."""
    from navig.commands.monitoring import generate_report
    generate_report(ctx.obj)


# Host security subcommand
host_security_app = typer.Typer(
    help="Security management (firewall, fail2ban, SSH, updates)",
    invoke_without_command=True,
    no_args_is_help=False,
)
host_app.add_typer(host_security_app, name="security")


@host_security_app.callback()
def host_security_callback(ctx: typer.Context):
    """Host security - run without subcommand for security scan."""
    if ctx.invoked_subcommand is None:
        from navig.commands.security import security_scan
        security_scan(ctx.obj)
        raise typer.Exit()


@host_security_app.command("show")
def host_security_show(
    ctx: typer.Context,
    firewall: bool = typer.Option(False, "--firewall", "-f", help="Show firewall status"),
    fail2ban: bool = typer.Option(False, "--fail2ban", "-b", help="Show fail2ban status"),
    ssh: bool = typer.Option(False, "--ssh", "-s", help="Show SSH audit"),
    updates: bool = typer.Option(False, "--updates", "-u", help="Show security updates"),
    connections: bool = typer.Option(False, "--connections", "-c", help="Show network connections"),
):
    """Show security information."""
    if firewall:
        from navig.commands.security import firewall_status
        firewall_status(ctx.obj)
    elif fail2ban:
        from navig.commands.security import fail2ban_status
        fail2ban_status(ctx.obj)
    elif ssh:
        from navig.commands.security import ssh_audit
        ssh_audit(ctx.obj)
    elif updates:
        from navig.commands.security import check_security_updates
        check_security_updates(ctx.obj)
    elif connections:
        from navig.commands.security import audit_connections
        audit_connections(ctx.obj)
    else:
        from navig.commands.security import security_scan
        security_scan(ctx.obj)


@host_security_app.command("edit")
def host_security_edit(
    ctx: typer.Context,
    firewall: bool = typer.Option(False, "--firewall", "-f", help="Edit firewall rules"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option("any", "--from", help="IP address or subnet"),
    add: bool = typer.Option(False, "--add", help="Add a rule"),
    remove: bool = typer.Option(False, "--remove", "-r", help="Remove a rule"),
    enable: bool = typer.Option(False, "--enable", help="Enable firewall"),
    disable: bool = typer.Option(False, "--disable", help="Disable firewall"),
    unban: Optional[str] = typer.Option(None, "--unban", help="Unban IP address from fail2ban"),
    jail: Optional[str] = typer.Option(None, "--jail", "-j", help="Jail name for fail2ban"),
):
    """Edit security settings."""
    if firewall:
        if enable:
            from navig.commands.security import firewall_enable
            firewall_enable(ctx.obj)
        elif disable:
            from navig.commands.security import firewall_disable
            firewall_disable(ctx.obj)
        elif add and port:
            from navig.commands.security import firewall_add_rule
            firewall_add_rule(port, protocol, allow_from, ctx.obj)
        elif remove and port:
            from navig.commands.security import firewall_remove_rule
            firewall_remove_rule(port, protocol, ctx.obj)
    elif unban:
        from navig.commands.security import fail2ban_unban
        fail2ban_unban(unban, jail, ctx.obj)
    else:
        ch.error("Specify what to edit: --firewall or --unban")


# Host maintenance subcommand
host_maintenance_app = typer.Typer(
    help="System maintenance (updates, cleaning, log rotation)",
    invoke_without_command=True,
    no_args_is_help=False,
)
host_app.add_typer(host_maintenance_app, name="maintenance")


@host_maintenance_app.callback()
def host_maintenance_callback(ctx: typer.Context):
    """Host maintenance - run without subcommand for system info."""
    if ctx.invoked_subcommand is None:
        from navig.commands.maintenance import system_info
        system_info(ctx.obj)
        raise typer.Exit()


@host_maintenance_app.command("show")
def host_maintenance_show(
    ctx: typer.Context,
    info: bool = typer.Option(False, "--info", "-i", help="Show system information"),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk usage"),
    memory: bool = typer.Option(False, "--memory", "-m", help="Show memory usage"),
):
    """Show system maintenance information."""
    if disk:
        from navig.commands.monitoring import monitor_disk
        monitor_disk(80, ctx.obj)
    elif memory:
        from navig.commands.monitoring import monitor_resources
        monitor_resources(ctx.obj)
    else:
        from navig.commands.maintenance import system_info
        system_info(ctx.obj)


@host_maintenance_app.command("run")
def host_maintenance_run(
    ctx: typer.Context,
    update: bool = typer.Option(False, "--update", "-u", help="Update system packages"),
    clean: bool = typer.Option(False, "--clean", "-c", help="Clean package cache"),
    rotate_logs: bool = typer.Option(False, "--rotate-logs", "-r", help="Rotate log files"),
    cleanup_temp: bool = typer.Option(False, "--cleanup-temp", "-t", help="Clean temp files"),
    all: bool = typer.Option(False, "--all", "-a", help="Full maintenance"),
    reboot: bool = typer.Option(False, "--reboot", help="Reboot server"),
):
    """Run system maintenance operations."""
    if update:
        from navig.commands.maintenance import update_packages
        update_packages(ctx.obj)
    elif clean:
        from navig.commands.maintenance import clean_packages
        clean_packages(ctx.obj)
    elif rotate_logs:
        from navig.commands.maintenance import rotate_logs as rotate_logs_func
        rotate_logs_func(ctx.obj)
    elif cleanup_temp:
        from navig.commands.maintenance import cleanup_temp as cleanup_temp_func
        cleanup_temp_func(ctx.obj)
    elif all:
        from navig.commands.maintenance import system_maintenance
        system_maintenance(ctx.obj)
    elif reboot:
        from navig.commands.remote import run_remote_command
        if ctx.obj.get('yes') or typer.confirm("Are you sure you want to reboot the server?"):
            run_remote_command("sudo reboot", ctx.obj)
    else:
        ch.error("Specify an action: --update, --clean, --rotate-logs, --cleanup-temp, --all, --reboot")


@host_maintenance_app.command("update")
def host_maintenance_update(ctx: typer.Context):
    """Update system packages."""
    from navig.commands.maintenance import update_packages
    update_packages(ctx.obj)


@host_maintenance_app.command("clean")
def host_maintenance_clean(ctx: typer.Context):
    """Clean package cache and orphans."""
    from navig.commands.maintenance import clean_packages
    clean_packages(ctx.obj)


@host_maintenance_app.command("install")
def host_maintenance_install(
    ctx: typer.Context,
    package: str = typer.Argument(..., help="Package or command to install"),
):
    """Install a package on the remote host."""
    from navig.commands.remote import install_remote_package
    install_remote_package(package, ctx.obj)


# ============================================================================
# App MANAGEMENT COMMANDS
# ============================================================================

app_app = typer.Typer(
    help="Manage apps on hosts",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(app_app, name="app")
app.add_typer(app_app, name="a", hidden=True)


@app_app.callback()
def app_callback(ctx: typer.Context):
    """App management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("app", ctx)
        raise typer.Exit()


@app_app.command("list")
def app_list(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to list apps from"),
    all: bool = typer.Option(False, "--all", "-a", help="Show all apps from all hosts with detailed information"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table, json, yaml"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one app per line) for scripting"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List all apps on a host."""
    from navig.commands.app import list_apps
    if host:
        ctx.obj['host'] = host
    ctx.obj['all'] = all
    ctx.obj['format'] = "json" if json else format
    ctx.obj['plain'] = plain
    if json:
        ctx.obj["json"] = True
    list_apps(ctx.obj)


@app_app.command("use")
def app_use(
    ctx: typer.Context,
    app_name: Optional[str] = typer.Argument(None, help="App name to activate"),
    local: bool = typer.Option(False, "--local", "-l", help="Set as local active app (current directory only)"),
    clear_local: bool = typer.Option(False, "--clear-local", help="Clear local active app setting"),
):
    """
    Set active app (global or local scope).

    Examples:
        navig app use myapp          # Set global active app
        navig app use myapp --local  # Set local active app (current dir)
        navig app use --clear-local     # Remove local active app setting
    """
    from navig.commands.app import use_app
    if app_name:
        ctx.obj['app_name'] = app_name
    ctx.obj['local'] = local
    ctx.obj['clear_local'] = clear_local
    use_app(ctx.obj)


@app_app.command("current", hidden=True)
def app_current(ctx: typer.Context):
    """[DEPRECATED: Use 'navig app show --current'] Show currently active app."""
    deprecation_warning("navig app current", "navig app show --current")
    from navig.commands.app import current_app
    current_app(ctx.obj)


@app_app.command("add")
def app_add(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to add"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to add app to"),
    from_app: Optional[str] = typer.Option(None, "--from", help="Clone from existing app"),
):
    """Add new app to a host (or clone from existing)."""
    if from_app:
        from navig.commands.app import clone_app
        ctx.obj['source_name'] = from_app
        ctx.obj['new_name'] = app_name
        if host:
            ctx.obj['host'] = host
        clone_app(ctx.obj)
    else:
        from navig.commands.app import add_app
        ctx.obj['app_name'] = app_name
        if host:
            ctx.obj['host'] = host
        add_app(ctx.obj)


@app_app.command("remove")
def app_remove(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to remove"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to remove app from"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
):
    """Remove app from a host."""
    from navig.commands.app import remove_app
    ctx.obj['app_name'] = app_name
    ctx.obj['force'] = force
    if host:
        ctx.obj['host'] = host
    remove_app(ctx.obj)


@app_app.command("show")
def app_show(
    ctx: typer.Context,
    app_name: Optional[str] = typer.Argument(None, help="App name to show"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host containing the app"),
    current: bool = typer.Option(False, "--current", help="Show currently active app"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed app configuration (canonical command)."""
    if json:
        ctx.obj["json"] = True
    if current:
        from navig.commands.app import current_app
        current_app(ctx.obj)
    else:
        from navig.commands.app import show_app
        if app_name:
            ctx.obj['app_name'] = app_name
        if host:
            ctx.obj['host'] = host
        show_app(ctx.obj)


@app_app.command("edit")
def app_edit(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to edit"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host containing the app"),
):
    """Edit app configuration in default editor."""
    from navig.commands.app import edit_app
    ctx.obj['app_name'] = app_name
    if host:
        ctx.obj['host'] = host
    edit_app(ctx.obj)


@app_app.command("clone", hidden=True)
def app_clone(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source app name to clone"),
    new_name: str = typer.Argument(..., help="New app name"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host containing the app"),
):
    """[DEPRECATED: Use 'navig app add <name> --from <source>'] Clone app."""
    deprecation_warning("navig app clone", "navig app add <name> --from <source>")
    from navig.commands.app import clone_app
    ctx.obj['source_name'] = source
    ctx.obj['new_name'] = new_name
    if host:
        ctx.obj['host'] = host
    clone_app(ctx.obj)


@app_app.command("info", hidden=True)
def app_info(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., help="App name to show info for"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host containing the app"),
):
    """[DEPRECATED: Use 'navig app show'] Show detailed app information."""
    deprecation_warning("navig app info", "navig app show")
    from navig.commands.app import info_app
    ctx.obj['app_name'] = app_name
    if host:
        ctx.obj['host'] = host
    info_app(ctx.obj)


@app_app.command("search")
def app_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (app name)"),
):
    """Search for apps across all hosts by name or configuration."""
    from navig.commands.app import search_apps
    ctx.obj['query'] = query
    search_apps(ctx.obj)


@app_app.command("migrate")
def app_migrate(
    ctx: typer.Context,
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to migrate apps from"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be migrated without making changes"),
):
    """
    Migrate apps from legacy embedded format to individual files.

    Converts apps stored in hosts/<host>.yaml under 'apps:' field
    to individual .navig/apps/<app>.yaml files.

    Examples:
        navig app migrate --host vultr          # Migrate all apps from vultr
        navig app migrate --dry-run             # Preview migration without changes
    """
    from navig.commands.app import migrate_apps
    if host:
        ctx.obj['host'] = host
    ctx.obj['dry_run'] = dry_run
    migrate_apps(ctx.obj)


# ============================================================================
# SSH TUNNEL COMMANDS
# ============================================================================

tunnel_app = typer.Typer(
    help="Manage SSH tunnels",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(tunnel_app, name="tunnel")
app.add_typer(tunnel_app, name="t", hidden=True)


@tunnel_app.callback()
def tunnel_callback(ctx: typer.Context):
    """Tunnel management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("tunnel", ctx)
        raise typer.Exit()


@tunnel_app.command("run")
def tunnel_run(ctx: typer.Context):
    """Start SSH tunnel for active server (canonical command)."""
    from navig.commands.tunnel import start_tunnel
    start_tunnel(ctx.obj)


@tunnel_app.command("start", hidden=True)
def tunnel_start(ctx: typer.Context):
    """[DEPRECATED: Use 'navig tunnel run'] Start SSH tunnel."""
    deprecation_warning("navig tunnel start", "navig tunnel run")
    from navig.commands.tunnel import start_tunnel
    start_tunnel(ctx.obj)


@tunnel_app.command("remove")
def tunnel_remove(ctx: typer.Context):
    """Stop and remove SSH tunnel (canonical command)."""
    from navig.commands.tunnel import stop_tunnel
    stop_tunnel(ctx.obj)


@tunnel_app.command("stop", hidden=True)
def tunnel_stop(ctx: typer.Context):
    """[DEPRECATED: Use 'navig tunnel remove'] Stop SSH tunnel."""
    deprecation_warning("navig tunnel stop", "navig tunnel remove")
    from navig.commands.tunnel import stop_tunnel
    stop_tunnel(ctx.obj)


@tunnel_app.command("update")
def tunnel_update(ctx: typer.Context):
    """Restart tunnel (canonical command)."""
    from navig.commands.tunnel import restart_tunnel
    restart_tunnel(ctx.obj)


@tunnel_app.command("restart", hidden=True)
def tunnel_restart(ctx: typer.Context):
    """[DEPRECATED: Use 'navig tunnel update'] Restart tunnel."""
    deprecation_warning("navig tunnel restart", "navig tunnel update")
    from navig.commands.tunnel import restart_tunnel
    restart_tunnel(ctx.obj)


@tunnel_app.command("show")
def tunnel_show(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text for scripting"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show tunnel status (canonical command)."""
    from navig.commands.tunnel import show_tunnel_status
    ctx.obj['plain'] = plain
    if json:
        ctx.obj["json"] = True
    show_tunnel_status(ctx.obj)


@tunnel_app.command("status", hidden=True)
def tunnel_status(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text (running/stopped) for scripting"),
):
    """[DEPRECATED: Use 'navig tunnel show'] Show tunnel status."""
    deprecation_warning("navig tunnel status", "navig tunnel show")
    from navig.commands.tunnel import show_tunnel_status
    ctx.obj['plain'] = plain
    show_tunnel_status(ctx.obj)


@tunnel_app.command("auto")
def tunnel_auto(ctx: typer.Context):
    """Auto-start tunnel if needed, auto-stop when done."""
    from navig.commands.tunnel import auto_tunnel
    auto_tunnel(ctx.obj)


# ============================================================================
# DATABASE COMMANDS (Unified 'db' group)
# ============================================================================

db_app = typer.Typer(
    help="Database operations (query, backup, restore, list, shell)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(db_app, name="db")
app.add_typer(db_app, name="database", hidden=True)


@db_app.callback()
def db_callback(ctx: typer.Context):
    """Database management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("db", ctx)
        raise typer.Exit()


@db_app.command("show")
def db_show(
    ctx: typer.Context,
    database: Optional[str] = typer.Argument(None, help="Database name"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    tables: bool = typer.Option(False, "--tables", help="Show tables in database"),
    containers: bool = typer.Option(False, "--containers", help="Show database containers"),
    users: bool = typer.Option(False, "--users", help="Show database users"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Show database information (canonical command)."""
    ctx.obj['plain'] = plain
    if containers:
        from navig.commands.db import db_containers_cmd
        db_containers_cmd(ctx.obj)
    elif users:
        from navig.commands.database_advanced import list_users_cmd
        list_users_cmd(ctx.obj)
    elif tables and database:
        from navig.commands.db import db_tables_cmd
        db_tables_cmd(database, container, user, password, db_type, ctx.obj)
    elif database:
        from navig.commands.db import db_tables_cmd
        db_tables_cmd(database, container, user, password, db_type, ctx.obj)
    else:
        from navig.commands.db import db_list_cmd
        db_list_cmd(container, user, password, db_type, ctx.obj)


@db_app.command("run")
def db_run(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(None, help="SQL query to execute"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    database: Optional[str] = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="SQL file to execute"),
    shell: bool = typer.Option(False, "--shell", "-s", help="Open interactive shell"),
):
    """Run SQL query/file or open shell (canonical command)."""
    if shell:
        from navig.commands.db import db_shell_cmd
        db_shell_cmd(container, user, password, database, db_type, ctx.obj)
    elif file:
        from navig.commands.database import execute_sql_file
        execute_sql_file(file, ctx.obj)
    elif query:
        from navig.commands.db import db_query_cmd
        db_query_cmd(query, container, user, password, database, db_type, ctx.obj)
    else:
        # Default to shell if no query provided
        from navig.commands.db import db_shell_cmd
        db_shell_cmd(container, user, password, database, db_type, ctx.obj)


def _is_base64_encoded(s: str) -> bool:
    """Check if string looks like base64 (for auto-detection)."""
    import base64
    import re
    # Base64 pattern: only A-Za-z0-9+/= and length multiple of 4
    if not re.match(r'^[A-Za-z0-9+/]+=*$', s):
        return False
    if len(s) % 4 != 0:
        return False
    # Must be reasonably long (short strings could be false positives)
    if len(s) < 20:
        return False
    # Try to decode - valid base64 should decode cleanly
    try:
        decoded = base64.b64decode(s).decode('utf-8')
        # Check if decoded looks like SQL (common keywords)
        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER', 'SHOW', 'DESCRIBE']
        return any(kw in decoded.upper() for kw in sql_keywords)
    except Exception:
        return False


@db_app.command("query")
def db_query_new(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="SQL query to execute (auto-detects base64)"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    database: Optional[str] = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    plain: bool = typer.Option(False, "--plain", "--raw", help="Output plain text (no formatting) for scripting"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
    b64: bool = typer.Option(False, "--b64", "-b", help="Force base64 decode (usually auto-detected)"),
):
    """Execute SQL query on remote database.
    
    Base64 encoding is AUTO-DETECTED. Just pass the query:
        navig db query "SELECT * FROM users" -d mydb
        navig db query "U0VMRUNUICogRlJPTSB1c2Vycw==" -d mydb  # Auto-detected as base64
    
    Use --b64 to force base64 decoding if auto-detection fails.
    """
    from navig.commands.db import db_query_cmd
    import base64
    
    # Auto-detect base64 or use explicit flag
    if b64 or _is_base64_encoded(query):
        try:
            decoded = base64.b64decode(query).decode('utf-8').strip()
            if not b64:
                ch.info(f"Auto-detected base64 query ({len(query)} chars → {len(decoded)} chars)")
            query = decoded
        except Exception as e:
            if b64:
                ch.error(f"Failed to decode base64 query: {e}")
                raise typer.Exit(1)
            # If auto-detect failed, just use original query
            pass
    
    ctx.obj['plain'] = plain
    if json:
        ctx.obj["json"] = True
    db_query_cmd(query, container, user, password, database, db_type, ctx.obj)


@db_app.command("file")
def db_file_new(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="SQL file to execute"),
):
    """Execute SQL file through tunnel."""
    from navig.commands.database import execute_sql_file
    execute_sql_file(file, ctx.obj)


@db_app.command("list")
def db_list_new(
    ctx: typer.Context,
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one database per line) for scripting"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List all databases on remote server."""
    from navig.commands.db import db_list_cmd
    ctx.obj['plain'] = plain
    if json:
        ctx.obj["json"] = True
    db_list_cmd(container, user, password, db_type, ctx.obj)


@db_app.command("tables")
def db_tables_new(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one table per line) for scripting"),
):
    """List tables in a database."""
    from navig.commands.db import db_tables_cmd
    ctx.obj['plain'] = plain
    db_tables_cmd(database, container, user, password, db_type, ctx.obj)


@db_app.command("dump")
def db_dump_new(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name to dump"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
):
    """Dump/backup a database from remote server."""
    from navig.commands.db import db_dump_cmd
    db_dump_cmd(database, output, container, user, password, db_type, ctx.obj)


@db_app.command("restore")
def db_restore_new(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to restore from"),
):
    """Restore database from backup file."""
    from navig.commands.database import restore_database
    restore_database(file, ctx.obj)


@db_app.command("shell", hidden=True)
def db_shell_new(
    ctx: typer.Context,
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    database: Optional[str] = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
):
    """[DEPRECATED: Use 'navig db run --shell'] Open interactive database shell."""
    deprecation_warning("navig db shell", "navig db run --shell")
    from navig.commands.db import db_shell_cmd
    db_shell_cmd(container, user, password, database, db_type, ctx.obj)


@db_app.command("containers", hidden=True)
def db_containers_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig db show --containers'] List database containers."""
    deprecation_warning("navig db containers", "navig db show --containers")
    from navig.commands.db import db_containers_cmd
    db_containers_cmd(ctx.obj)


@db_app.command("users", hidden=True)
def db_users_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig db show --users'] List database users."""
    deprecation_warning("navig db users", "navig db show --users")
    from navig.commands.database_advanced import list_users_cmd
    list_users_cmd(ctx.obj)


@db_app.command("optimize")
def db_optimize_new(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to optimize"),
):
    """Optimize database table."""
    from navig.commands.database_advanced import optimize_table_cmd
    optimize_table_cmd(table, ctx.obj)


@db_app.command("repair")
def db_repair_new(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to repair"),
):
    """Repair database table."""
    from navig.commands.database_advanced import repair_table_cmd
    repair_table_cmd(table, ctx.obj)


# Legacy aliases for backward compatibility
@app.command("sql", hidden=True)
def sql_query(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="SQL query to execute"),
):
    """[DEPRECATED: Use 'navig db query'] Execute SQL query through tunnel."""
    ch.warning("'navig sql' is deprecated. Use 'navig db query' instead.")
    from navig.commands.db import db_query_cmd
    db_query_cmd(query, None, "root", None, None, None, ctx.obj)


@app.command("sqlfile", hidden=True)
def sql_file(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="SQL file to execute"),
):
    """[DEPRECATED: Use 'navig db file'] Execute SQL file through tunnel."""
    ch.warning("'navig sqlfile' is deprecated. Use 'navig db file' instead.")
    from navig.commands.database import execute_sql_file
    execute_sql_file(file, ctx.obj)


@app.command("restore", hidden=True)
def restore_db(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to restore from"),
):
    """[DEPRECATED: Use 'navig db restore'] Restore database from backup file."""
    ch.warning("'navig restore' is deprecated. Use 'navig db restore' instead.")
    from navig.commands.database import restore_database
    restore_database(file, ctx.obj)


@app.command("backup-config", hidden=True)
def backup_system_config_cmd(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom backup name"),
):
    """[DEPRECATED] Backup system configuration files. Use: navig backup run --config"""
    deprecation_warning("navig backup-config", "navig backup run --config")
    from navig.commands.backup import backup_system_config
    backup_system_config(name, ctx.obj)


@app.command("backup-db-all", hidden=True)
def backup_all_databases_cmd(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option("gzip", "--compress", "-c", help="Compression: none|gzip|zstd"),
):
    """[DEPRECATED] Backup all databases with compression. Use: navig backup run --db-all"""
    deprecation_warning("navig backup-db-all", "navig backup run --db-all")
    from navig.commands.backup import backup_all_databases
    backup_all_databases(name, compress, ctx.obj)


@app.command("backup-hestia", hidden=True)
def backup_hestia_cmd(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom backup name"),
):
    """[DEPRECATED] Backup HestiaCP configuration. Use: navig backup run --hestia"""
    deprecation_warning("navig backup-hestia", "navig backup run --hestia")
    from navig.commands.backup import backup_hestia
    backup_hestia(name, ctx.obj)


@app.command("backup-web", hidden=True)
def backup_web_config_cmd(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom backup name"),
):
    """[DEPRECATED] Backup web server configurations. Use: navig backup run --web"""
    deprecation_warning("navig backup-web", "navig backup run --web")
    from navig.commands.backup import backup_web_config
    backup_web_config(name, ctx.obj)


@app.command("backup-all", hidden=True)
def backup_all_cmd(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option("gzip", "--compress", "-c", help="Compression for databases: none|gzip|zstd"),
):
    """[DEPRECATED] Comprehensive backup. Use: navig backup run --all"""
    deprecation_warning("navig backup-all", "navig backup run --all")
    from navig.commands.backup import backup_all
    backup_all(name, compress, ctx.obj)


@app.command("list-backups", hidden=True)
def list_backups_cmd(ctx: typer.Context):
    """[DEPRECATED] List all available backups. Use: navig backup list"""
    deprecation_warning("navig list-backups", "navig backup list")
    from navig.commands.backup import list_backups_cmd as list_backups
    list_backups(ctx.obj)


@app.command("restore-backup", hidden=True)
def restore_backup_cmd(
    ctx: typer.Context,
    backup_name: str = typer.Argument(..., help="Backup name to restore from"),
    component: Optional[str] = typer.Option(None, "--component", "-c", help="Specific component to restore"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """[DEPRECATED] Restore from comprehensive backup. Use: navig backup restore"""
    deprecation_warning("navig restore-backup", "navig backup restore")
    ctx.obj['force'] = force
    from navig.commands.backup import restore_backup_cmd as restore_backup
    restore_backup(backup_name, component, ctx.obj)


# ============================================================================
# MONITORING & HEALTH CHECKS (Unified 'monitor' group)
# ============================================================================

monitor_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host monitor'] Server monitoring",
    invoke_without_command=True,
    no_args_is_help=False,
    deprecated=True,
)
app.add_typer(monitor_app, name="monitor", hidden=True)


@monitor_app.callback()
def monitor_callback(ctx: typer.Context):
    """[DEPRECATED] Use 'navig host monitor' instead."""
    deprecation_warning("navig monitor", "navig host monitor")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_monitoring_menu
        launch_monitoring_menu()
        raise typer.Exit()


@monitor_app.command("show")
def monitor_show(
    ctx: typer.Context,
    resources: bool = typer.Option(False, "--resources", "-r", help="Show resource usage"),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk space"),
    services: bool = typer.Option(False, "--services", "-s", help="Show service status"),
    network: bool = typer.Option(False, "--network", "-n", help="Show network stats"),
    threshold: int = typer.Option(80, "--threshold", "-t", help="Alert threshold percentage"),
):
    """Show monitoring information (canonical command)."""
    if resources:
        from navig.commands.monitoring import monitor_resources
        monitor_resources(ctx.obj)
    elif disk:
        from navig.commands.monitoring import monitor_disk
        monitor_disk(threshold, ctx.obj)
    elif services:
        from navig.commands.monitoring import monitor_services
        monitor_services(ctx.obj)
    elif network:
        from navig.commands.monitoring import monitor_network
        monitor_network(ctx.obj)
    else:
        # Default to health overview
        from navig.commands.monitoring import health_check
        health_check(ctx.obj)


@monitor_app.command("run")
def monitor_run(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", help="Generate and save report"),
):
    """Run monitoring checks (canonical command)."""
    if report:
        from navig.commands.monitoring import generate_report
        generate_report(ctx.obj)
    else:
        from navig.commands.monitoring import health_check
        health_check(ctx.obj)


@monitor_app.command("resources", hidden=True)
def monitor_resources_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show --resources'] Monitor resources."""
    deprecation_warning("navig monitor resources", "navig monitor show --resources")
    from navig.commands.monitoring import monitor_resources
    monitor_resources(ctx.obj)


@monitor_app.command("disk", hidden=True)
def monitor_disk_new(
    ctx: typer.Context,
    threshold: int = typer.Option(80, "--threshold", "-t", help="Alert threshold percentage"),
):
    """[DEPRECATED: Use 'navig monitor show --disk'] Monitor disk space."""
    deprecation_warning("navig monitor disk", "navig monitor show --disk")
    from navig.commands.monitoring import monitor_disk
    monitor_disk(threshold, ctx.obj)


@monitor_app.command("services", hidden=True)
def monitor_services_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show --services'] Check service status."""
    deprecation_warning("navig monitor services", "navig monitor show --services")
    from navig.commands.monitoring import monitor_services
    monitor_services(ctx.obj)


@monitor_app.command("network", hidden=True)
def monitor_network_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show --network'] Monitor network."""
    deprecation_warning("navig monitor network", "navig monitor show --network")
    from navig.commands.monitoring import monitor_network
    monitor_network(ctx.obj)


@monitor_app.command("health", hidden=True)
def monitor_health_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor show'] Health check."""
    deprecation_warning("navig monitor health", "navig monitor show")
    from navig.commands.monitoring import health_check
    health_check(ctx.obj)


@monitor_app.command("report")
def monitor_report_new(ctx: typer.Context):
    """Generate comprehensive monitoring report and save to file."""
    from navig.commands.monitoring import generate_report
    generate_report(ctx.obj)


# Legacy aliases for backward compatibility (hidden)
@app.command("monitor-resources", hidden=True)
def monitor_resources_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor resources']"""
    ch.warning("'navig monitor-resources' is deprecated. Use 'navig monitor resources' instead.")
    from navig.commands.monitoring import monitor_resources
    monitor_resources(ctx.obj)


@app.command("monitor-disk", hidden=True)
def monitor_disk_cmd(
    ctx: typer.Context,
    threshold: int = typer.Option(80, "--threshold", "-t", help="Alert threshold percentage"),
):
    """[DEPRECATED: Use 'navig monitor disk']"""
    ch.warning("'navig monitor-disk' is deprecated. Use 'navig monitor disk' instead.")
    from navig.commands.monitoring import monitor_disk
    monitor_disk(threshold, ctx.obj)


@app.command("monitor-services", hidden=True)
def monitor_services_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor services']"""
    ch.warning("'navig monitor-services' is deprecated. Use 'navig monitor services' instead.")
    from navig.commands.monitoring import monitor_services
    monitor_services(ctx.obj)


@app.command("monitor-network", hidden=True)
def monitor_network_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor network']"""
    ch.warning("'navig monitor-network' is deprecated. Use 'navig monitor network' instead.")
    from navig.commands.monitoring import monitor_network
    monitor_network(ctx.obj)


@app.command("health-check", hidden=True)
def health_check_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor health']"""
    ch.warning("'navig health-check' is deprecated. Use 'navig monitor health' instead.")
    from navig.commands.monitoring import health_check
    health_check(ctx.obj)


@app.command("monitoring-report", hidden=True)
def monitoring_report_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig monitor report']"""
    ch.warning("'navig monitoring-report' is deprecated. Use 'navig monitor report' instead.")
    from navig.commands.monitoring import generate_report
    generate_report(ctx.obj)


# ============================================================================
# SECURITY MANAGEMENT (Unified 'security' group)
# ============================================================================

security_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host security'] Security management",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(security_app, name="security", hidden=True)  # Deprecated


@security_app.callback()
def security_callback(ctx: typer.Context):
    """Security management - DEPRECATED, use 'navig host security'."""
    deprecation_warning("navig security", "navig host security")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_security_menu
        launch_security_menu()
        raise typer.Exit()


@security_app.command("show")
def security_show(
    ctx: typer.Context,
    firewall: bool = typer.Option(False, "--firewall", "-f", help="Show firewall status"),
    fail2ban: bool = typer.Option(False, "--fail2ban", "-b", help="Show fail2ban status"),
    ssh: bool = typer.Option(False, "--ssh", "-s", help="Show SSH audit"),
    updates: bool = typer.Option(False, "--updates", "-u", help="Show security updates"),
    connections: bool = typer.Option(False, "--connections", "-c", help="Show network connections"),
):
    """Show security information (canonical command)."""
    if firewall:
        from navig.commands.security import firewall_status
        firewall_status(ctx.obj)
    elif fail2ban:
        from navig.commands.security import fail2ban_status
        fail2ban_status(ctx.obj)
    elif ssh:
        from navig.commands.security import ssh_audit
        ssh_audit(ctx.obj)
    elif updates:
        from navig.commands.security import check_security_updates
        check_security_updates(ctx.obj)
    elif connections:
        from navig.commands.security import audit_connections
        audit_connections(ctx.obj)
    else:
        # Default to security scan overview
        from navig.commands.security import security_scan
        security_scan(ctx.obj)


@security_app.command("run")
def security_run(ctx: typer.Context):
    """Run comprehensive security scan (canonical command)."""
    from navig.commands.security import security_scan
    security_scan(ctx.obj)


@security_app.command("firewall", hidden=True)
def security_firewall_new(ctx: typer.Context):
    """Display UFW firewall status and rules."""
    from navig.commands.security import firewall_status
    firewall_status(ctx.obj)


@security_app.command("firewall-add")
def security_firewall_add_new(
    ctx: typer.Context,
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option("any", "--from", help="IP address or subnet (default: any)"),
):
    """Add UFW firewall rule."""
    from navig.commands.security import firewall_add_rule
    firewall_add_rule(port, protocol, allow_from, ctx.obj)


@security_app.command("edit")
def security_edit(
    ctx: typer.Context,
    firewall: bool = typer.Option(False, "--firewall", "-f", help="Edit firewall rules"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option("any", "--from", help="IP address or subnet"),
    add: bool = typer.Option(False, "--add", help="Add a rule"),
    remove: bool = typer.Option(False, "--remove", "-r", help="Remove a rule"),
    enable: bool = typer.Option(False, "--enable", help="Enable firewall"),
    disable: bool = typer.Option(False, "--disable", help="Disable firewall"),
    unban: Optional[str] = typer.Option(None, "--unban", help="Unban IP address from fail2ban"),
    jail: Optional[str] = typer.Option(None, "--jail", "-j", help="Jail name for fail2ban"),
):
    """Edit security settings (canonical command)."""
    if firewall:
        if enable:
            from navig.commands.security import firewall_enable
            firewall_enable(ctx.obj)
        elif disable:
            from navig.commands.security import firewall_disable
            firewall_disable(ctx.obj)
        elif add and port:
            from navig.commands.security import firewall_add_rule
            firewall_add_rule(port, protocol, allow_from, ctx.obj)
        elif remove and port:
            from navig.commands.security import firewall_remove_rule
            firewall_remove_rule(port, protocol, ctx.obj)
    elif unban:
        from navig.commands.security import fail2ban_unban
        fail2ban_unban(unban, jail, ctx.obj)
    else:
        from navig.console_helper import ch
        ch.error("Specify what to edit: --firewall or --unban")


@security_app.command("firewall-remove", hidden=True)
def security_firewall_remove_new(
    ctx: typer.Context,
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
):
    """Remove UFW firewall rule."""
    from navig.commands.security import firewall_remove_rule
    firewall_remove_rule(port, protocol, ctx.obj)


@security_app.command("firewall-enable")
def security_firewall_enable_new(ctx: typer.Context):
    """Enable UFW firewall."""
    from navig.commands.security import firewall_enable
    firewall_enable(ctx.obj)


@security_app.command("firewall-disable")
def security_firewall_disable_new(ctx: typer.Context):
    """Disable UFW firewall."""
    from navig.commands.security import firewall_disable
    firewall_disable(ctx.obj)


@security_app.command("fail2ban", hidden=True)
def security_fail2ban_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security show --fail2ban'] Show Fail2Ban status."""
    deprecation_warning("navig security fail2ban", "navig security show --fail2ban")
    from navig.commands.security import fail2ban_status
    fail2ban_status(ctx.obj)


@security_app.command("unban", hidden=True)
def security_unban_new(
    ctx: typer.Context,
    ip_address: str = typer.Argument(..., help="IP address to unban"),
    jail: str = typer.Option(None, "--jail", "-j", help="Jail name (default: all jails)"),
):
    """[DEPRECATED: Use 'navig security edit --unban <ip>'] Unban IP."""
    deprecation_warning("navig security unban", "navig security edit --unban <ip>")
    from navig.commands.security import fail2ban_unban
    fail2ban_unban(ip_address, jail, ctx.obj)


@security_app.command("ssh", hidden=True)
def security_ssh_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security show --ssh'] SSH audit."""
    deprecation_warning("navig security ssh", "navig security show --ssh")
    from navig.commands.security import ssh_audit
    ssh_audit(ctx.obj)


@security_app.command("updates")
def security_updates_new(ctx: typer.Context):
    """Check for available security updates."""
    from navig.commands.security import check_security_updates
    check_security_updates(ctx.obj)


@security_app.command("connections")
def security_connections_new(ctx: typer.Context):
    """Audit active network connections."""
    from navig.commands.security import audit_connections
    audit_connections(ctx.obj)


@security_app.command("scan")
def security_scan_new(ctx: typer.Context):
    """Run comprehensive security scan."""
    from navig.commands.security import security_scan
    security_scan(ctx.obj)


# Legacy aliases for backward compatibility (hidden)
@app.command('firewall-status', hidden=True)
def firewall_status_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security firewall']"""
    ch.warning("'navig firewall-status' is deprecated. Use 'navig security firewall' instead.")
    from navig.commands.security import firewall_status
    firewall_status(ctx.obj)


@app.command('firewall-add', hidden=True)
def firewall_add_cmd(
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
    allow_from: str = typer.Option("any", "--from", help="IP address or subnet (default: any)"),
    ctx: typer.Context = typer.Context
):
    """[DEPRECATED: Use 'navig security firewall-add']"""
    ch.warning("'navig firewall-add' is deprecated. Use 'navig security firewall-add' instead.")
    from navig.commands.security import firewall_add_rule
    firewall_add_rule(port, protocol, allow_from, ctx.obj)


@app.command('firewall-remove', hidden=True)
def firewall_remove_cmd(
    port: int = typer.Argument(..., help="Port number"),
    protocol: str = typer.Option("tcp", "--protocol", "-p", help="Protocol (tcp/udp)"),
    ctx: typer.Context = typer.Context
):
    """[DEPRECATED: Use 'navig security firewall-remove']"""
    ch.warning("'navig firewall-remove' is deprecated. Use 'navig security firewall-remove' instead.")
    from navig.commands.security import firewall_remove_rule
    firewall_remove_rule(port, protocol, ctx.obj)


@app.command('fail2ban-status', hidden=True)
def fail2ban_status_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security fail2ban']"""
    ch.warning("'navig fail2ban-status' is deprecated. Use 'navig security fail2ban' instead.")
    from navig.commands.security import fail2ban_status
    fail2ban_status(ctx.obj)


@app.command('security-scan', hidden=True)
def security_scan_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig security scan']"""
    ch.warning("'navig security-scan' is deprecated. Use 'navig security scan' instead.")
    from navig.commands.security import security_scan
    security_scan(ctx.obj)


# ============================================================================
# SYSTEM MAINTENANCE (Unified 'system' group - Pillar 7)
# ============================================================================

system_app = typer.Typer(
    help="[DEPRECATED: Use 'navig host maintenance'] System maintenance",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(system_app, name="system", hidden=True)  # Deprecated


@system_app.callback()
def system_callback(ctx: typer.Context):
    """System maintenance - DEPRECATED, use 'navig host maintenance'."""
    deprecation_warning("navig system", "navig host maintenance")
    if ctx.invoked_subcommand is None:
        from navig.commands.maintenance import system_info
        system_info(ctx.obj)


@system_app.command("show")
def system_show(
    ctx: typer.Context,
    info: bool = typer.Option(False, "--info", "-i", help="Show system information"),
    disk: bool = typer.Option(False, "--disk", "-d", help="Show disk usage"),
    memory: bool = typer.Option(False, "--memory", "-m", help="Show memory usage"),
    processes: bool = typer.Option(False, "--processes", "-p", help="Show running processes"),
):
    """Show system information (canonical command)."""
    if disk:
        from navig.commands.monitoring import monitor_disk
        monitor_disk(80, ctx.obj)
    elif memory:
        from navig.commands.monitoring import monitor_resources
        monitor_resources(ctx.obj)
    elif processes:
        from navig.commands.remote import run_remote_command
        run_remote_command("ps aux --sort=-%mem | head -20", ctx.obj)
    else:
        from navig.commands.maintenance import system_info
        system_info(ctx.obj)


@system_app.command("run")
def system_run(
    ctx: typer.Context,
    update: bool = typer.Option(False, "--update", "-u", help="Update system packages"),
    clean: bool = typer.Option(False, "--clean", "-c", help="Clean package cache"),
    rotate_logs: bool = typer.Option(False, "--rotate-logs", "-r", help="Rotate log files"),
    cleanup_temp: bool = typer.Option(False, "--cleanup-temp", "-t", help="Clean temp files"),
    maintenance: bool = typer.Option(False, "--maintenance", "-m", help="Full maintenance"),
    reboot: bool = typer.Option(False, "--reboot", help="Reboot server"),
):
    """Run system maintenance operations (canonical command)."""
    if update:
        from navig.commands.maintenance import update_packages
        update_packages(ctx.obj)
    elif clean:
        from navig.commands.maintenance import clean_packages
        clean_packages(ctx.obj)
    elif rotate_logs:
        from navig.commands.maintenance import rotate_logs as rotate_logs_func
        rotate_logs_func(ctx.obj)
    elif cleanup_temp:
        from navig.commands.maintenance import cleanup_temp as cleanup_temp_func
        cleanup_temp_func(ctx.obj)
    elif maintenance:
        from navig.commands.maintenance import system_maintenance
        system_maintenance(ctx.obj)
    elif reboot:
        from navig.commands.remote import run_remote_command
        if ctx.obj.get('yes') or typer.confirm("Are you sure you want to reboot the server?"):
            run_remote_command("sudo reboot", ctx.obj)
    else:
        ch.error("Specify an action: --update, --clean, --rotate-logs, --cleanup-temp, --maintenance, --reboot")


@system_app.command("update")
def system_update(ctx: typer.Context):
    """Update system packages (alias for 'navig system run --update')."""
    from navig.commands.maintenance import update_packages
    update_packages(ctx.obj)


@system_app.command("clean")
def system_clean(ctx: typer.Context):
    """Clean package cache and orphans (alias for 'navig system run --clean')."""
    from navig.commands.maintenance import clean_packages
    clean_packages(ctx.obj)


@system_app.command("info")
def system_info_cmd(ctx: typer.Context):
    """Display comprehensive system information."""
    from navig.commands.maintenance import system_info
    system_info(ctx.obj)


@system_app.command("reboot")
def system_reboot(ctx: typer.Context):
    """Reboot the server (requires confirmation)."""
    from navig.commands.remote import run_remote_command
    if ctx.obj.get('yes') or typer.confirm("Are you sure you want to reboot the server?"):
        run_remote_command("sudo reboot", ctx.obj)


# Legacy flat commands for backward compatibility (hidden)
@app.command("update-packages", hidden=True)
def update_packages_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system update'] Update packages."""
    deprecation_warning("navig update-packages", "navig system update")
    from navig.commands.maintenance import update_packages
    update_packages(ctx.obj)


@app.command("clean-packages", hidden=True)
def clean_packages_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system clean'] Clean packages."""
    deprecation_warning("navig clean-packages", "navig system clean")
    from navig.commands.maintenance import clean_packages
    clean_packages(ctx.obj)


@app.command("rotate-logs", hidden=True)
def rotate_logs_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system run --rotate-logs'] Rotate logs."""
    deprecation_warning("navig rotate-logs", "navig system run --rotate-logs")
    from navig.commands.maintenance import rotate_logs
    rotate_logs(ctx.obj)


@app.command("cleanup-temp", hidden=True)
def cleanup_temp_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system run --cleanup-temp'] Cleanup temp."""
    deprecation_warning("navig cleanup-temp", "navig system run --cleanup-temp")
    from navig.commands.maintenance import cleanup_temp
    cleanup_temp(ctx.obj)


@app.command("check-filesystem", hidden=True)
def check_filesystem_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system show --disk'] Check filesystem."""
    deprecation_warning("navig check-filesystem", "navig system show --disk")
    from navig.commands.maintenance import check_filesystem
    check_filesystem(ctx.obj)


@app.command("system-maintenance", hidden=True)
def system_maintenance_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig system run --maintenance'] Run maintenance."""
    deprecation_warning("navig system-maintenance", "navig system run --maintenance")
    from navig.commands.maintenance import system_maintenance
    system_maintenance(ctx.obj)


# ============================================================================
# REMOTE COMMAND EXECUTION
# ============================================================================

@app.command("run")
def run_command(
    ctx: typer.Context,
    command: Optional[str] = typer.Argument(None, help="Command to execute, @- for stdin, @file for file"),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read command from stdin (bypasses escaping)"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read command from file"),
    b64: bool = typer.Option(False, "--b64", "-b", help="Encode command as Base64 (escape-proof for JSON/special chars)"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Open editor for multi-line input"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm prompts (same as global --yes)"),
    confirm: bool = typer.Option(False, "--confirm", "-c", help="Force confirmation prompt"),
    json: bool = typer.Option(False, "--json", help="Output JSON (captures stdout/stderr)"),
):
    """Execute arbitrary shell command on remote server.

    \b
    ⚠️  PowerShell Users: For commands with (), {}, $, or other special chars,
    use --stdin or --file to avoid quoting issues:
      
      echo 'complex command' | navig run --b64 --stdin
      navig run --b64 --file script.txt
      navig run -i     # Opens editor

    \b
    Examples:
      navig run "ls -la"                              # Simple command
      navig run --b64 "curl -d '{\"k\":\"v\"}' api"   # JSON (use stdin on PowerShell!)
      navig run @script.sh                            # Read from file
      cat script.sh | navig run @-                    # Read from stdin
      navig run -i                                    # Open editor

    \b
    Use --b64 for commands with:
      • JSON payloads: '{"key":"value"}'
      • Special characters: $ ! ( ) [ ] { }
      • Nested quotes: "outer 'inner' text"
    """
    from navig.commands.remote import run_remote_command
    # Suggest using --b64 for risky/complex shell strings.
    if command and not b64 and not stdin and not file and not interactive:
        # Only warn about ACTUAL quoting/escaping problems
        # Safe: semicolons (;), pipes (|), redirects (>, <) - these work fine in quoted strings
        # Risky: nested quotes, JSON braces, dollar signs (variable expansion), backticks
        risky_markers = [
            '"\'"',  # nested quotes: "...'..."
            '\'"',   # nested quotes: '..."...'
            '{"',    # JSON object start
            '"}',    # JSON object end
            '["',    # JSON array with string
            '"]',    # JSON array with string
            '$(',    # command substitution
            '`',     # backticks (command substitution)
            '\\n',   # literal newlines in command string
            '\\t',   # literal tabs
        ]
        if any(m in command for m in risky_markers):
            ch.warning("This command looks complex; consider --b64 for safer quoting.")
            ch.dim("Example: navig run --b64 \"curl -d '{\\\"k\\\":\\\"v\\\"}' ...\"")
    # Merge command-level options with global options
    options = ctx.obj.copy()
    if yes:
        options['yes'] = True
    if confirm:
        options['confirm'] = True
    if b64:
        options['b64'] = True
    if json:
        options['json'] = True
    run_remote_command(command, options, stdin=stdin, file=file, interactive=interactive)


@app.command("r", hidden=True)
def run_command_alias(
    ctx: typer.Context,
    command: Optional[str] = typer.Argument(None, help="Alias for: navig run"),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read command from stdin"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read command from file"),
    b64: bool = typer.Option(False, "--b64", "-b", help="Base64 encode the command"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Open editor for multi-line input"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm prompts"),
    confirm: bool = typer.Option(False, "--confirm", "-c", help="Force confirmation prompt"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Alias for: navig run."""
    run_command(
        ctx,
        command=command,
        stdin=stdin,
        file=file,
        b64=b64,
        interactive=interactive,
        yes=yes,
        confirm=confirm,
        json=json,
    )


@app.command("status")
def status_command(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="One-line status summary"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
    all_: bool = typer.Option(False, "--all", "-a", help="Show extended status"),
):
    """Show current NAVIG status (active host/app, tunnel, gateway)."""
    from navig.commands.status import show_status

    ctx.obj["plain"] = plain
    ctx.obj["all"] = all_
    if json:
        ctx.obj["json"] = True
    show_status(ctx.obj)


@app.command("dashboard")
def dashboard_command(
    ctx: typer.Context,
    live: bool = typer.Option(True, "--live/--no-live", help="Live auto-refresh mode"),
    refresh: int = typer.Option(5, "--refresh", "-r", help="Refresh interval in seconds"),
):
    """
    Real-time operations dashboard with host status, Docker, and history.
    
    The dashboard shows:
    - Host connectivity status with latency
    - Docker container overview
    - Recent operations from history
    - System resource overview
    
    Examples:
        navig dashboard           # Full live dashboard
        navig dashboard --no-live # Single snapshot
        navig dashboard -r 10     # Refresh every 10 seconds
    
    Press Q to quit, R to force refresh.
    """
    from navig.commands.dashboard import run_dashboard, run_dashboard_simple

    if live:
        run_dashboard(refresh_interval=refresh)
    else:
        run_dashboard_simple()


@app.command("suggest")
def suggest_command(
    ctx: typer.Context,
    context: Optional[str] = typer.Option(None, "--context", "-c", help="Filter by context (docker, database, deployment, monitoring)"),
    run_idx: Optional[int] = typer.Option(None, "--run", "-r", help="Run suggestion by number"),
    limit: int = typer.Option(8, "--limit", "-l", help="Number of suggestions"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show command without executing"),
):
    """
    Intelligent command suggestions based on history and context.
    
    Analyzes your command history, current project context, and time patterns
    to suggest relevant commands.
    
    Examples:
        navig suggest                    # Show suggestions
        navig suggest --context docker   # Docker-related suggestions
        navig suggest --run 1            # Run first suggestion
        navig suggest --run 2 --dry-run  # Preview second suggestion
    
    Suggestion sources:
        H = History (frequently used)
        S = Sequence (what usually follows)
        T = Time (typical for this time of day)
        C = Context (project type detected)
    """
    from navig.commands.suggest import show_suggestions, run_suggestion

    if run_idx is not None:
        run_suggestion(run_idx, dry_run=dry_run)
    else:
        show_suggestions(
            context=context,
            limit=limit,
            plain=plain,
            json_out=json_out,
            opts=ctx.obj,
        )


# ============================================================================
# EVENT-DRIVEN AUTOMATION (TRIGGERS)
# ============================================================================

trigger_app = typer.Typer(
    help="Event-driven automation triggers",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(trigger_app, name="trigger")


@trigger_app.callback()
def trigger_callback(ctx: typer.Context):
    """Event-driven automation triggers - run without subcommand for list."""
    if ctx.invoked_subcommand is None:
        from navig.commands.triggers import list_triggers
        list_triggers()
        raise typer.Exit()


@trigger_app.command("list")
def trigger_list(
    ctx: typer.Context,
    type_filter: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by trigger type"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (enabled/disabled)"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all configured triggers."""
    from navig.commands.triggers import list_triggers
    list_triggers(
        type_filter=type_filter,
        status_filter=status,
        tag=tag,
        plain=plain,
        json_out=json_out,
    )


@trigger_app.command("show")
def trigger_show(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show detailed trigger information."""
    from navig.commands.triggers import show_trigger
    show_trigger(trigger_id, plain=plain, json_out=json_out)


@trigger_app.command("add")
def trigger_add(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Trigger name"),
    action: Optional[str] = typer.Option(None, "--action", "-a", help="Action to execute"),
    trigger_type: str = typer.Option("manual", "--type", "-t", help="Trigger type (health, schedule, threshold, manual)"),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
    schedule: str = typer.Option("", "--schedule", help="Schedule expression (for schedule triggers)"),
    host: str = typer.Option("", "--host", help="Target host (for threshold triggers)"),
    condition: str = typer.Option("", "--condition", "-c", help="Condition (format: 'target op value')"),
):
    """
    Create a new trigger.
    
    Interactive mode (no args):
        navig trigger add
    
    Quick mode:
        navig trigger add "Disk Alert" --action "notify:telegram" --type threshold --host prod --condition "disk gte 80"
        navig trigger add "Daily Backup" --action "workflow:backup" --type schedule --schedule "0 2 * * *"
        navig trigger add "Health Check" --action "host test" --type health
    
    Action formats:
        - navig command: "host list", "db dump", etc.
        - workflow: "workflow:deploy", "workflow:backup"
        - notify: "notify:telegram", "notify:console"
        - webhook: "webhook:https://example.com/hook"
    """
    from navig.commands.triggers import add_trigger_interactive, add_trigger_quick
    
    if name is None:
        # Interactive mode
        add_trigger_interactive()
    else:
        if not action:
            ch.error("Action is required for quick mode. Use --action or run without args for interactive mode.")
            return
        add_trigger_quick(
            name=name,
            action=action,
            trigger_type=trigger_type,
            description=description,
            schedule=schedule,
            host=host,
            condition=condition,
        )


@trigger_app.command("remove")
def trigger_remove(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a trigger."""
    from navig.commands.triggers import remove_trigger
    remove_trigger(trigger_id, force=force)


@trigger_app.command("enable")
def trigger_enable(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to enable"),
):
    """Enable a disabled trigger."""
    from navig.commands.triggers import enable_trigger
    enable_trigger(trigger_id)


@trigger_app.command("disable")
def trigger_disable(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to disable"),
):
    """Disable a trigger (stops it from firing)."""
    from navig.commands.triggers import disable_trigger
    disable_trigger(trigger_id)


@trigger_app.command("test")
def trigger_test(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to test"),
):
    """
    Test a trigger (dry run).
    
    Shows what actions would be executed without actually running them.
    """
    from navig.commands.triggers import test_trigger
    test_trigger(trigger_id)


@trigger_app.command("fire")
def trigger_fire(
    ctx: typer.Context,
    trigger_id: str = typer.Argument(..., help="Trigger ID to fire"),
):
    """
    Manually fire a trigger.
    
    Executes all actions associated with the trigger immediately,
    regardless of conditions or cooldown.
    """
    from navig.commands.triggers import fire_trigger
    fire_trigger(trigger_id)


@trigger_app.command("history")
def trigger_history(
    ctx: typer.Context,
    trigger_id: Optional[str] = typer.Argument(None, help="Filter by trigger ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show trigger execution history."""
    from navig.commands.triggers import show_trigger_history
    show_trigger_history(
        trigger_id=trigger_id,
        limit=limit,
        plain=plain,
        json_out=json_out,
    )


@trigger_app.command("clear-history")
def trigger_clear_history(
    ctx: typer.Context,
    trigger_id: Optional[str] = typer.Argument(None, help="Clear history for specific trigger only"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear trigger execution history."""
    from navig.commands.triggers import clear_trigger_history
    clear_trigger_history(trigger_id=trigger_id, force=force)


@trigger_app.command("stats")
def trigger_stats(ctx: typer.Context):
    """Show trigger statistics."""
    from navig.commands.triggers import show_trigger_stats
    show_trigger_stats()


# ============================================================================
# OPERATIONS INSIGHTS & ANALYTICS
# ============================================================================

insights_app = typer.Typer(
    help="Operations analytics and insights",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(insights_app, name="insights")


@insights_app.callback()
def insights_callback(ctx: typer.Context):
    """Operations insights - analytics on your command patterns."""
    if ctx.invoked_subcommand is None:
        from navig.commands.insights import show_insights_summary
        show_insights_summary()
        raise typer.Exit()


@insights_app.command("show")
def insights_show(
    ctx: typer.Context,
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show insights summary with key metrics."""
    from navig.commands.insights import show_insights_summary
    show_insights_summary(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("hosts")
def insights_hosts(
    ctx: typer.Context,
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show host health scores and trends.
    
    Calculates a health score (0-100) for each host based on:
    - Success rate (60% weight)
    - Average latency (40% weight)
    
    Also shows if host performance is improving, stable, or declining.
    """
    from navig.commands.insights import show_host_health
    show_host_health(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("commands")
def insights_commands(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit", "-n", help="Number of commands to show"),
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show most frequently used commands with success rates."""
    from navig.commands.insights import show_top_commands
    show_top_commands(limit=limit, time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("time")
def insights_time(
    ctx: typer.Context,
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show time-based usage patterns.
    
    Displays a breakdown of operations by hour, showing:
    - Activity levels throughout the day
    - Success rates per time period
    - Most common commands at each hour
    """
    from navig.commands.insights import show_time_patterns
    show_time_patterns(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("anomalies")
def insights_anomalies(
    ctx: typer.Context,
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Detect unusual patterns and potential issues.
    
    Analyzes:
    - Error rate spikes
    - Unusual command frequencies
    - Inactive hosts
    - Performance degradation
    """
    from navig.commands.insights import show_anomalies
    show_anomalies(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("recommend")
def insights_recommend(
    ctx: typer.Context,
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Get personalized recommendations based on your usage."""
    from navig.commands.insights import show_recommendations
    show_recommendations(time_range=time_range, plain=plain, json_out=json_out)


@insights_app.command("report")
def insights_report(
    ctx: typer.Context,
    time_range: str = typer.Option("week", "--range", "-r", help="Time range: today, week, month, all"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save report to file"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Generate a full analytics report.
    
    Includes:
    - Overall statistics
    - Host health scores
    - Top commands
    - Detected anomalies
    - Personalized recommendations
    
    Can be exported to JSON for further analysis.
    """
    from navig.commands.insights import generate_report
    generate_report(time_range=time_range, output_file=output, json_out=json_out)


# ============================================================================
# PACKS SYSTEM - Shareable Operations Bundles
# ============================================================================

pack_app = typer.Typer(
    help="Shareable operations bundles (runbooks, checklists, templates)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(pack_app, name="pack")
app.add_typer(pack_app, name="packs", hidden=True)


@pack_app.callback()
def pack_callback(ctx: typer.Context):
    """Packs - shareable operations bundles."""
    if ctx.invoked_subcommand is None:
        from navig.commands.packs import list_packs
        list_packs()
        raise typer.Exit()


@pack_app.command("list")
def pack_list(
    ctx: typer.Context,
    pack_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by type: workflow, runbook, checklist, template"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
    installed: bool = typer.Option(False, "--installed", "-i", help="Show only installed packs"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List available packs."""
    from navig.commands.packs import list_packs
    list_packs(pack_type=pack_type, tag=tag, installed_only=installed, plain=plain, json_out=json_out)


@pack_app.command("show")
def pack_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show pack details."""
    from navig.commands.packs import show_pack
    show_pack(name, plain=plain, json_out=json_out)


@pack_app.command("install")
def pack_install(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Pack name or path to install"),
    force: bool = typer.Option(False, "--force", "-f", help="Force reinstall"),
):
    """
    Install a pack.
    
    Sources:
    - Built-in pack name (e.g., "starter/deployment-checklist")
    - Local file path (e.g., "./my-pack.yaml")
    """
    from navig.commands.packs import install_pack
    install_pack(source, force=force)


@pack_app.command("uninstall")
def pack_uninstall(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name to uninstall"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Uninstall a pack."""
    from navig.commands.packs import uninstall_pack
    uninstall_pack(name, force=force)


@pack_app.command("run")
def pack_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name to run"),
    var: Optional[List[str]] = typer.Option(None, "--var", "-v", help="Variables (key=value)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
):
    """
    Run a pack (execute its steps).
    
    Examples:
        navig pack run deployment-checklist
        navig pack run backup-runbook --var host=production
        navig pack run my-workflow --dry-run
    """
    from navig.commands.packs import run_pack
    
    # Parse variables
    variables = {}
    if var:
        for v in var:
            if '=' in v:
                key, value = v.split('=', 1)
                variables[key] = value
    
    run_pack(name, variables=variables, dry_run=dry_run, yes=yes)


@pack_app.command("create")
def pack_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Pack name"),
    pack_type: str = typer.Option("runbook", "--type", "-t", help="Pack type: workflow, runbook, checklist"),
    description: str = typer.Option("", "--description", "-d", help="Pack description"),
):
    """Create a new pack in local packs directory."""
    from navig.commands.packs import create_pack
    create_pack(name, pack_type=pack_type, description=description)


@pack_app.command("search")
def pack_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Search for packs by name, description, or tags."""
    from navig.commands.packs import search_packs
    search_packs(query, plain=plain, json_out=json_out)


# ============================================================================
# QUICK ACTIONS - Shortcuts for frequent operations
# ============================================================================

quick_app = typer.Typer(
    help="Quick action shortcuts for frequent operations",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(quick_app, name="quick")
app.add_typer(quick_app, name="q", hidden=True)


@quick_app.callback()
def quick_callback(ctx: typer.Context):
    """Quick actions - shortcuts for frequent operations."""
    if ctx.invoked_subcommand is None:
        from navig.commands.suggest import show_quick_actions
        show_quick_actions()
        raise typer.Exit()


@quick_app.command("list")
def quick_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all quick actions."""
    from navig.commands.suggest import show_quick_actions
    show_quick_actions(plain=plain, json_out=json_out)


@quick_app.command("run")
def quick_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Quick action name to run"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show command without executing"),
):
    """
    Run a quick action by name.
    
    Examples:
        navig quick run deploy
        navig quick run backup --dry-run
        navig q run status
    """
    from navig.commands.suggest import run_quick_action
    run_quick_action(name, dry_run=dry_run)


@quick_app.command("add")
def quick_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Short name for the action"),
    command: str = typer.Argument(..., help="Full navig command to run"),
    description: str = typer.Option("", "--desc", "-d", help="Optional description"),
):
    """
    Add a quick action shortcut.
    
    Examples:
        navig quick add deploy "run 'cd /var/www && git pull'"
        navig quick add backup "db dump --output /tmp/backup.sql"
        navig quick add status "dashboard --no-live"
    
    Then run with: navig quick run deploy
    """
    from navig.commands.suggest import add_quick_action
    
    # Ensure command starts with navig
    if not command.startswith("navig "):
        command = f"navig {command}"
    
    add_quick_action(name, command, description)


@quick_app.command("remove")
def quick_remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Quick action name to remove"),
):
    """Remove a quick action."""
    from navig.config import get_config_manager
    from pathlib import Path
    import yaml
    
    config_manager = get_config_manager()
    quick_file = Path(config_manager.global_config_dir) / "quick_actions.yaml"
    
    if not quick_file.exists():
        ch.error(f"Quick action '{name}' not found.")
        return
    
    with open(quick_file, 'r') as f:
        actions = yaml.safe_load(f) or {}
    
    if name not in actions:
        ch.error(f"Quick action '{name}' not found.")
        return
    
    del actions[name]
    
    with open(quick_file, 'w') as f:
        yaml.safe_dump(actions, f, default_flow_style=False)
    
    ch.success(f"Removed quick action: {name}")


@app.command("quickstart")
def quickstart_command(ctx: typer.Context):
    """Minimal onboarding to get NAVIG usable in under 5 minutes."""
    from navig.commands.quickstart import quickstart

    quickstart(ctx.obj)


@app.command("install")
def install_package(
    ctx: typer.Context,
    package: str = typer.Argument(..., help="Package or command to install"),
):
    """Auto-detect package manager and install."""
    from navig.commands.remote import install_remote_package
    install_remote_package(package, ctx.obj)


# ============================================================================
# LOCAL MACHINE MANAGEMENT (Canonical 'hosts', 'software', 'local' groups)
# ============================================================================

# Hosts file management
hosts_app = typer.Typer(
    help="System hosts file management (view, edit, add entries)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(hosts_app, name="hosts")


@hosts_app.callback()
def hosts_callback(ctx: typer.Context):
    """Hosts file operations - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("hosts", ctx)
        raise typer.Exit()


@hosts_app.command("view")
def hosts_view_cmd(ctx: typer.Context):
    """View the system hosts file with syntax highlighting."""
    from navig.commands.local import hosts_view
    hosts_view(ctx.obj)


@hosts_app.command("edit")
def hosts_edit_cmd(ctx: typer.Context):
    """Open hosts file in editor (requires admin)."""
    from navig.commands.local import hosts_edit
    hosts_edit(ctx.obj)


@hosts_app.command("add")
def hosts_add_cmd(
    ctx: typer.Context,
    ip: str = typer.Argument(..., help="IP address"),
    hostname: str = typer.Argument(..., help="Hostname to add"),
):
    """Add an entry to the hosts file (requires admin)."""
    from navig.commands.local import hosts_add
    hosts_add(ip, hostname, ctx.obj)


# Software/package management
software_app = typer.Typer(
    help="Local software package management (list, search)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(software_app, name="software")


@software_app.callback()
def software_callback(ctx: typer.Context):
    """Software management - run without subcommand to list packages."""
    if ctx.invoked_subcommand is None:
        from navig.commands.local import software_list
        software_list(ctx.obj)
        raise typer.Exit()


@software_app.command("list")
def software_list_cmd(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of results"),
):
    """List installed software packages."""
    from navig.commands.local import software_list
    ctx.obj['limit'] = limit
    software_list(ctx.obj)


@software_app.command("search")
def software_search_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search term"),
):
    """Search installed packages by name."""
    from navig.commands.local import software_search
    software_search(query, ctx.obj)


# Local system management
local_app = typer.Typer(
    help="Local machine management (system info, security, network)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(local_app, name="local")


@local_app.callback()
def local_callback(ctx: typer.Context):
    """Local system management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("local", ctx)
        raise typer.Exit()


@local_app.command("show")
def local_show_cmd(
    ctx: typer.Context,
    info: bool = typer.Option(True, "--info", "-i", help="Show system information"),
    resources: bool = typer.Option(False, "--resources", "-r", help="Show resource usage"),
):
    """Show local system information."""
    if resources:
        from navig.commands.local import resource_usage
        resource_usage(ctx.obj)
    else:
        from navig.commands.local import system_info
        system_info(ctx.obj)


@local_app.command("audit")
def local_audit_cmd(
    ctx: typer.Context,
    ai: bool = typer.Option(False, "--ai", "-a", help="Include AI analysis"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed information"),
):
    """Run local security audit."""
    from navig.commands.local import security_audit
    ctx.obj['ai'] = ai
    ctx.obj['verbose'] = verbose
    security_audit(ctx.obj)


@local_app.command("ports")
def local_ports_cmd(ctx: typer.Context):
    """Show open/listening ports on local machine."""
    from navig.commands.local import security_ports
    security_ports(ctx.obj)


@local_app.command("firewall")
def local_firewall_cmd(ctx: typer.Context):
    """Show local firewall status."""
    from navig.commands.local import security_firewall
    security_firewall(ctx.obj)


@local_app.command("ping")
def local_ping_cmd(
    ctx: typer.Context,
    host: str = typer.Argument(..., help="Host to ping"),
    count: int = typer.Option(4, "--count", "-c", help="Number of pings"),
):
    """Ping a host from local machine."""
    from navig.commands.local import network_ping
    network_ping(host, count, ctx.obj)


@local_app.command("dns")
def local_dns_cmd(
    ctx: typer.Context,
    hostname: str = typer.Argument(..., help="Hostname to lookup"),
):
    """Perform DNS lookup."""
    from navig.commands.local import network_dns
    network_dns(hostname, ctx.obj)


@local_app.command("interfaces")
def local_interfaces_cmd(ctx: typer.Context):
    """Show network interfaces."""
    from navig.commands.local import network_interfaces
    network_interfaces(ctx.obj)


# ============================================================================
# WEB SERVER MANAGEMENT (Unified 'web' group)
# ============================================================================

web_app = typer.Typer(
    help="Web server management (Nginx/Apache vhosts, sites, modules)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(web_app, name="web")


@web_app.callback()
def web_callback(ctx: typer.Context):
    """Web server management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("web", ctx)
        raise typer.Exit()


@web_app.command("vhosts")
def web_vhosts_new(ctx: typer.Context):
    """List virtual hosts (enabled and available)."""
    from navig.commands.webserver import list_vhosts
    list_vhosts(ctx.obj)


@web_app.command("test")
def web_test_new(ctx: typer.Context):
    """Test web server configuration syntax."""
    from navig.commands.webserver import test_config
    test_config(ctx.obj)


@web_app.command("enable")
def web_enable_new(
    ctx: typer.Context,
    site_name: str = typer.Argument(..., help="Site name to enable"),
):
    """Enable a web server site."""
    from navig.commands.webserver import enable_site
    ctx.obj['site_name'] = site_name
    enable_site(ctx.obj)


@web_app.command("disable")
def web_disable_new(
    ctx: typer.Context,
    site_name: str = typer.Argument(..., help="Site name to disable"),
):
    """Disable a web server site."""
    from navig.commands.webserver import disable_site
    ctx.obj['site_name'] = site_name
    disable_site(ctx.obj)


@web_app.command("module-enable")
def web_module_enable_new(
    ctx: typer.Context,
    module_name: str = typer.Argument(..., help="Module name to enable"),
):
    """Enable Apache module (Apache only)."""
    from navig.commands.webserver import enable_module
    ctx.obj['module_name'] = module_name
    enable_module(ctx.obj)


@web_app.command("module-disable")
def web_module_disable_new(
    ctx: typer.Context,
    module_name: str = typer.Argument(..., help="Module name to disable"),
):
    """Disable Apache module (Apache only)."""
    from navig.commands.webserver import disable_module
    ctx.obj['module_name'] = module_name
    disable_module(ctx.obj)


@web_app.command("reload")
def web_reload_new(ctx: typer.Context):
    """Safely reload web server (tests config first)."""
    from navig.commands.webserver import reload_server
    reload_server(ctx.obj)


@web_app.command("recommend")
def web_recommend_new(ctx: typer.Context):
    """Display performance tuning recommendations."""
    from navig.commands.webserver import get_recommendations
    get_recommendations(ctx.obj)


# Nested: web hestia (HestiaCP panel management)
web_hestia_app = typer.Typer(
    help="HestiaCP control panel management",
    invoke_without_command=True,
    no_args_is_help=False,
)
web_app.add_typer(web_hestia_app, name="hestia")


@web_hestia_app.callback()
def web_hestia_callback(ctx: typer.Context):
    """HestiaCP management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_hestia_menu
        launch_hestia_menu()
        raise typer.Exit()


@web_hestia_app.command("list")
def web_hestia_list(
    ctx: typer.Context,
    users: bool = typer.Option(False, "--users", "-u", help="List HestiaCP users"),
    domains: bool = typer.Option(False, "--domains", "-d", help="List HestiaCP domains"),
    user_filter: Optional[str] = typer.Option(None, "--user", help="Filter domains by username"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List HestiaCP resources (users, domains)."""
    ctx.obj['plain'] = plain
    if users:
        from navig.commands.hestia import list_users_cmd
        list_users_cmd(ctx.obj)
    elif domains:
        from navig.commands.hestia import list_domains_cmd
        list_domains_cmd(user_filter, ctx.obj)
    else:
        # Default: show users
        from navig.commands.hestia import list_users_cmd
        list_users_cmd(ctx.obj)


@web_hestia_app.command("add")
def web_hestia_add(
    ctx: typer.Context,
    resource: str = typer.Argument(..., help="Resource type: user or domain"),
    name: str = typer.Argument(..., help="Username or domain name"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Password (for user)"),
    email: Optional[str] = typer.Option(None, "--email", "-e", help="Email (for user)"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Username (for domain)"),
):
    """Add HestiaCP user or domain."""
    if resource == "user":
        if not password or not email:
            ch.error("Password and email required for user creation")
            raise typer.Exit(1)
        from navig.commands.hestia import add_user_cmd
        add_user_cmd(name, password, email, ctx.obj)
    elif resource == "domain":
        if not user:
            ch.error("Username required for domain creation (--user)")
            raise typer.Exit(1)
        from navig.commands.hestia import add_domain_cmd
        add_domain_cmd(user, name, ctx.obj)
    else:
        ch.error(f"Unknown resource type: {resource}. Use 'user' or 'domain'.")
        raise typer.Exit(1)


@web_hestia_app.command("remove")
def web_hestia_remove(
    ctx: typer.Context,
    resource: str = typer.Argument(..., help="Resource type: user or domain"),
    name: str = typer.Argument(..., help="Username or domain name"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Username (for domain)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion without confirmation"),
):
    """Remove HestiaCP user or domain."""
    ctx.obj['force'] = force
    if resource == "user":
        from navig.commands.hestia import delete_user_cmd
        delete_user_cmd(name, ctx.obj)
    elif resource == "domain":
        if not user:
            ch.error("Username required for domain deletion (--user)")
            raise typer.Exit(1)
        from navig.commands.hestia import delete_domain_cmd
        delete_domain_cmd(user, name, ctx.obj)
    else:
        ch.error(f"Unknown resource type: {resource}. Use 'user' or 'domain'.")
        raise typer.Exit(1)


# Legacy aliases for backward compatibility (hidden)
@app.command("webserver-list-vhosts", hidden=True)
def webserver_list_vhosts_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig web vhosts']"""
    ch.warning("'navig webserver-list-vhosts' is deprecated. Use 'navig web vhosts' instead.")
    from navig.commands.webserver import list_vhosts
    list_vhosts(ctx.obj)


@app.command("webserver-test-config", hidden=True)
def webserver_test_config_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig web test']"""
    ch.warning("'navig webserver-test-config' is deprecated. Use 'navig web test' instead.")
    from navig.commands.webserver import test_config
    test_config(ctx.obj)


@app.command("webserver-reload", hidden=True)
def webserver_reload_cmd(ctx: typer.Context):
    """[DEPRECATED: Use 'navig web reload']"""
    ch.warning("'navig webserver-reload' is deprecated. Use 'navig web reload' instead.")
    from navig.commands.webserver import reload_server
    reload_server(ctx.obj)


# ============================================================================
# FILE OPERATIONS (Legacy flat commands - deprecated, use 'navig file' group)
# ============================================================================

@app.command("upload", hidden=True)
def upload_file(
    ctx: typer.Context,
    local: Path = typer.Argument(..., help="Local file/directory path"),
    remote: Optional[str] = typer.Argument(
        None,
        help="Remote path (smart detection if omitted)",
    ),
):
    """[DEPRECATED: Use 'navig file add'] Upload file/directory."""
    deprecation_warning("navig upload", "navig file add")
    from navig.commands.files import upload_file_cmd
    upload_file_cmd(local, remote, ctx.obj)


@app.command("download", hidden=True)
def download_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path"),
    local: Optional[Path] = typer.Argument(
        None,
        help="Local path (smart detection if omitted)",
    ),
):
    """[DEPRECATED: Use 'navig file show --download'] Download file/directory."""
    deprecation_warning("navig download", "navig file show --download")
    from navig.commands.files import download_file_cmd
    download_file_cmd(remote, local, ctx.obj)


@app.command("list", hidden=True)
def list_remote(
    ctx: typer.Context,
    remote_path: str = typer.Argument(..., help="Remote directory path"),
):
    """[DEPRECATED: Use 'navig file list'] List remote directory."""
    deprecation_warning("navig list", "navig file list")
    from navig.commands.files import list_remote_directory
    list_remote_directory(remote_path, ctx.obj)


@app.command("delete", hidden=True)
def delete_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path to delete"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Delete directories recursively"),
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion without confirmation"),
):
    """[DEPRECATED: Use 'navig file remove'] Delete remote file/directory."""
    deprecation_warning("navig delete", "navig file remove")
    from navig.commands.files_advanced import delete_file_cmd
    ctx.obj['recursive'] = recursive
    ctx.obj['force'] = force
    delete_file_cmd(remote, ctx.obj)


@app.command("mkdir", hidden=True)
def make_directory(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote directory path to create"),
    parents: bool = typer.Option(True, "--parents", "-p", help="Create parent directories as needed"),
    mode: str = typer.Option("755", "--mode", "-m", help="Permission mode (e.g., 755)"),
):
    """[DEPRECATED: Use 'navig file add --dir'] Create remote directory."""
    deprecation_warning("navig mkdir", "navig file add --dir")
    from navig.commands.files_advanced import mkdir_cmd
    ctx.obj['parents'] = parents
    ctx.obj['mode'] = mode
    mkdir_cmd(remote, ctx.obj)


@app.command("chmod", hidden=True)
def change_permissions(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path"),
    mode: str = typer.Argument(..., help="Permission mode (e.g., 755, 644)"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Apply recursively"),
):
    """[DEPRECATED: Use 'navig file edit --mode'] Change permissions."""
    deprecation_warning("navig chmod", "navig file edit --mode")
    from navig.commands.files_advanced import chmod_cmd
    ctx.obj['recursive'] = recursive
    chmod_cmd(remote, mode, ctx.obj)


@app.command("chown", hidden=True)
def change_owner(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file/directory path"),
    owner: str = typer.Argument(..., help="New owner (user or user:group)"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Apply recursively"),
):
    """[DEPRECATED: Use 'navig file edit --owner'] Change ownership."""
    deprecation_warning("navig chown", "navig file edit --owner")
    from navig.commands.files_advanced import chown_cmd
    ctx.obj['recursive'] = recursive
    chown_cmd(remote, owner, ctx.obj)


@app.command("cat", hidden=True)
def cat_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path to read"),
    lines: Optional[int] = typer.Option(None, "--lines", "-n", help="Number of lines to show"),
    head: bool = typer.Option(False, "--head", help="Show first N lines (use with --lines)"),
    tail: bool = typer.Option(False, "--tail", "-t", help="Show last N lines (use with --lines)"),
):
    """[DEPRECATED: Use 'navig file show'] Read remote file contents."""
    deprecation_warning("navig cat", "navig file show")
    from navig.commands.files_advanced import cat_file_cmd
    cat_file_cmd(remote, ctx.obj, lines=lines, head=head, tail=tail)


@app.command("write-file", hidden=True)
def write_file(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path to write"),
    content: Optional[str] = typer.Option(None, "--content", "-c", help="Content to write"),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read content from stdin (pipe)"),
    from_file: Optional[Path] = typer.Option(None, "--from-file", "-f", help="Read content from local file"),
    append: bool = typer.Option(False, "--append", "-a", help="Append to file instead of overwrite"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="Set file permissions after writing"),
    owner: Optional[str] = typer.Option(None, "--owner", "-o", help="Set file owner after writing"),
):
    """[DEPRECATED: Use 'navig file edit --content'] Write to remote file."""
    deprecation_warning("navig write-file", "navig file edit --content")
    from navig.commands.files_advanced import write_file_cmd
    write_file_cmd(remote, content, ctx.obj, stdin=stdin, local_file=from_file, 
                   append=append, mode=mode, owner=owner)


@app.command("ls", hidden=True)
def ls_directory(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote directory path"),
    all: bool = typer.Option(False, "--all", "-a", help="Show hidden files"),
    long: bool = typer.Option(True, "--long", "-l", help="Long format with details"),
    human: bool = typer.Option(True, "--human", "-h", help="Human-readable sizes"),
):
    """[DEPRECATED: Use 'navig file list'] List remote directory."""
    deprecation_warning("navig ls", "navig file list")
    from navig.commands.files_advanced import list_dir_cmd
    list_dir_cmd(remote, ctx.obj, all=all, long=long, human=human)


@app.command("tree", hidden=True)
def tree_directory(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote directory path"),
    depth: int = typer.Option(2, "--depth", "-d", help="Maximum depth to display"),
    dirs_only: bool = typer.Option(False, "--dirs-only", "-D", help="Show only directories"),
):
    """[DEPRECATED: Use 'navig file list --tree'] Show directory tree."""
    deprecation_warning("navig tree", "navig file list --tree")
    from navig.commands.files_advanced import tree_cmd
    tree_cmd(remote, ctx.obj, depth=depth, dirs_only=dirs_only)


# ============================================================================
# DOCKER MANAGEMENT COMMANDS
# ============================================================================

docker_app = typer.Typer(
    help="Docker container management",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(docker_app, name="docker")


@docker_app.callback()
def docker_callback(ctx: typer.Context):
    """Docker management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("docker", ctx)
        raise typer.Exit()


@docker_app.command("ps")
def docker_ps_cmd(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", "-a", help="Show all containers (including stopped)"),
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="Filter by name (grep pattern)"),
    format: str = typer.Option("table", "--format", help="Output format: table, json, names"),
):
    """
    List Docker containers on remote host.
    
    Replaces: navig run "docker ps -a | grep pattern"
    
    \b
    Examples:
        navig docker ps                  # Running containers
        navig docker ps --all            # All containers
        navig docker ps -f affine        # Filter by name
    """
    from navig.commands.docker import docker_ps
    docker_ps(ctx.obj, all=all, filter=filter, format=format)


@docker_app.command("logs")
def docker_logs_cmd(
    ctx: typer.Context,
    container: str = typer.Argument(..., help="Container name or ID"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    since: Optional[str] = typer.Option(None, "--since", help="Show logs since (e.g., 10m, 1h)"),
):
    """
    View Docker container logs.
    
    Replaces: navig run "docker logs container 2>&1 | tail -n 50"
    
    \b
    Examples:
        navig docker logs nginx          # Last 50 lines
        navig docker logs app -n 100     # Last 100 lines
        navig docker logs app --follow   # Stream logs
        navig docker logs app --since 1h # Logs from last hour
    """
    from navig.commands.docker import docker_logs
    docker_logs(container, ctx.obj, tail=tail, follow=follow, since=since)


@docker_app.command("exec")
def docker_exec_cmd(
    ctx: typer.Context,
    container: str = typer.Argument(..., help="Container name or ID"),
    command: str = typer.Argument(..., help="Command to execute"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive mode with TTY"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Run as specific user"),
    workdir: Optional[str] = typer.Option(None, "--workdir", "-w", help="Working directory"),
):
    """
    Execute command in Docker container.
    
    \b
    Examples:
        navig docker exec nginx "nginx -t"
        navig docker exec postgres "psql -U postgres -c 'SELECT 1'"
        navig docker exec app "php artisan migrate" -u www-data
    """
    from navig.commands.docker import docker_exec
    docker_exec(container, command, ctx.obj, interactive=interactive, user=user, workdir=workdir)


@docker_app.command("compose")
def docker_compose_cmd(
    ctx: typer.Context,
    action: str = typer.Argument(..., help="Action: up, down, restart, stop, start, pull, build, logs, ps"),
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Path to docker-compose.yml directory"),
    services: Optional[str] = typer.Option(None, "--services", "-s", help="Comma-separated list of services"),
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d", help="Run in background (for 'up')"),
    build: bool = typer.Option(False, "--build", "-b", help="Build images before starting"),
    pull: bool = typer.Option(False, "--pull", help="Pull images before starting"),
):
    """
    Run docker compose commands on remote host.
    
    Replaces: navig run "cd /path && docker compose up -d"
    
    \b
    Examples:
        navig docker compose up --path /app
        navig docker compose down --path /app
        navig docker compose restart --path /app --services "web,db"
        navig docker compose logs --path /app
    """
    from navig.commands.docker import docker_compose
    service_list = services.split(',') if services else None
    docker_compose(action, ctx.obj, path=path, services=service_list, 
                   detach=detach, build=build, pull=pull)


@docker_app.command("restart")
def docker_restart_cmd(
    ctx: typer.Context,
    container: str = typer.Argument(..., help="Container name or ID"),
    timeout: int = typer.Option(10, "--timeout", "-t", help="Timeout in seconds"),
):
    """Restart Docker container."""
    from navig.commands.docker import docker_restart
    docker_restart(container, ctx.obj, timeout=timeout)


@docker_app.command("stop")
def docker_stop_cmd(
    ctx: typer.Context,
    container: str = typer.Argument(..., help="Container name or ID"),
    timeout: int = typer.Option(10, "--timeout", "-t", help="Timeout in seconds"),
):
    """Stop Docker container."""
    from navig.commands.docker import docker_stop
    docker_stop(container, ctx.obj, timeout=timeout)


@docker_app.command("start")
def docker_start_cmd(
    ctx: typer.Context,
    container: str = typer.Argument(..., help="Container name or ID"),
):
    """Start Docker container."""
    from navig.commands.docker import docker_start
    docker_start(container, ctx.obj)


@docker_app.command("stats")
def docker_stats_cmd(
    ctx: typer.Context,
    container: Optional[str] = typer.Argument(None, help="Container name (all if omitted)"),
    stream: bool = typer.Option(False, "--stream", "-s", help="Stream stats continuously"),
):
    """Show container resource usage statistics."""
    from navig.commands.docker import docker_stats
    docker_stats(ctx.obj, container=container, no_stream=not stream)


@docker_app.command("inspect")
def docker_inspect_cmd(
    ctx: typer.Context,
    container: str = typer.Argument(..., help="Container name or ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Go template format"),
):
    """
    Inspect Docker container.
    
    \b
    Examples:
        navig docker inspect nginx
        navig docker inspect nginx -f "{{.State.Status}}"
        navig docker inspect nginx -f "{{.HostConfig.RestartPolicy.Name}}"
    """
    from navig.commands.docker import docker_inspect
    docker_inspect(container, ctx.obj, format=format)


# ============================================================================
# ADVANCED DATABASE COMMANDS (DEPRECATED - use 'navig db <subcommand>')
# ============================================================================

@app.command("db-list", hidden=True)
def list_databases(ctx: typer.Context):
    """[DEPRECATED] List all databases with sizes. Use: navig db list"""
    deprecation_warning("navig db-list", "navig db list")
    from navig.commands.database_advanced import list_databases_cmd
    list_databases_cmd(ctx.obj)


@app.command("db-tables", hidden=True)
def list_tables(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name"),
):
    """[DEPRECATED] List tables in a database. Use: navig db tables <database>"""
    deprecation_warning("navig db-tables", "navig db tables")
    from navig.commands.database_advanced import list_tables_cmd
    list_tables_cmd(database, ctx.obj)


@app.command("db-optimize", hidden=True)
def optimize_table(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to optimize"),
):
    """[DEPRECATED] Optimize database table. Use: navig db optimize <table>"""
    deprecation_warning("navig db-optimize", "navig db optimize")
    from navig.commands.database_advanced import optimize_table_cmd
    optimize_table_cmd(table, ctx.obj)


@app.command("db-repair", hidden=True)
def repair_table(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to repair"),
):
    """[DEPRECATED] Repair database table. Use: navig db repair <table>"""
    deprecation_warning("navig db-repair", "navig db repair")
    from navig.commands.database_advanced import repair_table_cmd
    repair_table_cmd(table, ctx.obj)


@app.command("db-users", hidden=True)
def list_db_users(ctx: typer.Context):
    """[DEPRECATED] List database users. Use: navig db users"""
    deprecation_warning("navig db-users", "navig db users")
    from navig.commands.database_advanced import list_users_cmd
    list_users_cmd(ctx.obj)


# ============================================================================
# DOCKER DATABASE COMMANDS (DEPRECATED - use 'navig db <subcommand>')
# ============================================================================

@app.command("db-containers", hidden=True)
def db_containers(ctx: typer.Context):
    """[DEPRECATED] List Docker containers running database services. Use: navig db containers"""
    deprecation_warning("navig db-containers", "navig db containers")
    from navig.commands.db import db_containers_cmd
    db_containers_cmd(ctx.obj)


@app.command("db-query", hidden=True)
def db_query(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="SQL query to execute"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    database: Optional[str] = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
):
    """[DEPRECATED] Execute SQL query on remote database. Use: navig db run <query>"""
    deprecation_warning("navig db-query", "navig db run")
    from navig.commands.db import db_query_cmd
    db_query_cmd(query, container, user, password, database, db_type, ctx.obj)


@app.command("db-databases", hidden=True)
def db_databases(
    ctx: typer.Context,
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one database per line) for scripting"),
):
    """[DEPRECATED] List all databases on remote server. Use: navig db list"""
    deprecation_warning("navig db-databases", "navig db list")
    from navig.commands.db import db_list_cmd
    ctx.obj['plain'] = plain
    db_list_cmd(container, user, password, db_type, ctx.obj)


@app.command("db-show-tables", hidden=True)
def db_show_tables(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one table per line) for scripting"),
):
    """[DEPRECATED] List tables in a database. Use: navig db tables <database>"""
    deprecation_warning("navig db-show-tables", "navig db tables")
    from navig.commands.db import db_tables_cmd
    ctx.obj['plain'] = plain
    db_tables_cmd(database, container, user, password, db_type, ctx.obj)


@app.command("db-dump", hidden=True)
def db_dump(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name to dump"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
):
    """[DEPRECATED] Dump/backup a database from remote server. Use: navig db dump"""
    deprecation_warning("navig db-dump", "navig db dump")
    from navig.commands.db import db_dump_cmd
    db_dump_cmd(database, output, container, user, password, db_type, ctx.obj)


@app.command("db-shell", hidden=True)
def db_shell(
    ctx: typer.Context,
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Docker container name"),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password"),
    database: Optional[str] = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: Optional[str] = typer.Option(None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"),
):
    """[DEPRECATED] Open interactive database shell via SSH. Use: navig db run --shell"""
    deprecation_warning("navig db-shell", "navig db run --shell")
    from navig.commands.db import db_shell_cmd
    db_shell_cmd(container, user, password, database, db_type, ctx.obj)


# ============================================================================
# SERVER MONITORING & MANAGEMENT (DEPRECATED - use 'navig log/monitor/system')
# ============================================================================

@app.command("logs", hidden=True)
def view_logs(
    ctx: typer.Context,
    service: str = typer.Argument(..., help="Service name (nginx, php-fpm, mysql, app, etc.)"),
    tail: bool = typer.Option(False, "--tail", "-f", help="Follow logs in real-time"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to display"),
):
    """[DEPRECATED] View logs. Use: navig log show <service>"""
    deprecation_warning("navig logs", "navig log show")
    from navig.commands.monitoring import view_service_logs
    view_service_logs(service, tail, lines, ctx.obj)


@app.command("health", hidden=True)
def health_check(ctx: typer.Context):
    """[DEPRECATED] Run health checks. Use: navig monitor show"""
    deprecation_warning("navig health", "navig monitor show")
    from navig.commands.monitoring import run_health_check
    run_health_check(ctx.obj)


@app.command("restart", hidden=True)
def restart_service(
    ctx: typer.Context,
    service: str = typer.Argument(..., help="Service to restart (nginx|php-fpm|mysql|app|docker|all)"),
):
    """[DEPRECATED] Restart service. Use: navig system run --restart <service>"""
    deprecation_warning("navig restart", "navig system run --restart")
    from navig.commands.monitoring import restart_remote_service
    restart_remote_service(service, ctx.obj)


# ============================================================================
# AI ASSISTANT (Unified 'ai' group - Pillar 6: Intelligence)
# ============================================================================

ai_app = typer.Typer(
    help="AI-powered assistance for diagnostics, optimization, and knowledge",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(ai_app, name="ai")


@ai_app.callback()
def ai_callback(ctx: typer.Context):
    """AI Assistant - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("ai", ctx)
        raise typer.Exit()


@ai_app.command("ask")
def ai_ask(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Override default AI model",
    ),
):
    """Ask AI about server/configuration (canonical command)."""
    from navig.commands.ai import ask_ai
    ask_ai(question, model, ctx.obj)


@ai_app.command("explain")
def ai_explain(
    ctx: typer.Context,
    log_file: str = typer.Argument(..., help="Log file path to explain"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to analyze"),
):
    """Explain logs/errors using AI."""
    from navig.commands.ai import ask_ai
    question = f"Analyze and explain the last {lines} lines of the log file at {log_file}. Identify any errors, warnings, or issues and suggest solutions."
    ask_ai(question, None, ctx.obj)


@ai_app.command("diagnose")
def ai_diagnose(ctx: typer.Context):
    """AI-powered issue diagnosis based on system state."""
    from navig.commands.assistant import analyze_cmd
    analyze_cmd(ctx.obj)


@ai_app.command("suggest")
def ai_suggest(ctx: typer.Context):
    """Get AI-powered optimization suggestions."""
    from navig.commands.ai import ask_ai
    question = "Analyze the current server configuration and suggest optimizations for performance, security, and reliability."
    ask_ai(question, None, ctx.obj)


@ai_app.command("show")
def ai_show(
    ctx: typer.Context,
    status: bool = typer.Option(False, "--status", "-s", help="Show assistant status"),
    context: bool = typer.Option(False, "--context", "-c", help="Show AI context summary"),
    clipboard: bool = typer.Option(False, "--clipboard", help="Copy context to clipboard"),
    file: Optional[str] = typer.Option(None, "--file", help="Save context to file"),
):
    """Show AI assistant information (canonical command)."""
    if status:
        from navig.commands.assistant import status_cmd
        status_cmd(ctx.obj)
    elif context:
        from navig.commands.assistant import context_cmd
        context_cmd(ctx.obj, clipboard, file)
    else:
        from navig.commands.assistant import status_cmd
        status_cmd(ctx.obj)


@ai_app.command("run")
def ai_run(
    ctx: typer.Context,
    analyze: bool = typer.Option(False, "--analyze", "-a", help="Run system analysis"),
    reset: bool = typer.Option(False, "--reset", "-r", help="Reset learning data"),
):
    """Run AI operations (canonical command)."""
    if analyze:
        from navig.commands.assistant import analyze_cmd
        analyze_cmd(ctx.obj)
    elif reset:
        from navig.commands.assistant import reset_cmd
        reset_cmd(ctx.obj)
    else:
        from navig.commands.assistant import analyze_cmd
        analyze_cmd(ctx.obj)


@ai_app.command("edit")
def ai_edit(ctx: typer.Context):
    """Configure AI assistant settings (interactive wizard)."""
    from navig.commands.assistant import config_cmd
    config_cmd(ctx.obj)


@ai_app.command("models")
def ai_models(
    ctx: typer.Context,
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Filter by provider (e.g., openai, airllm)"),
):
    """List available AI models from all providers.
    
    Examples:
        navig ai models
        navig ai models --provider airllm
        navig ai models --provider openai
    """
    from rich.console import Console
    from rich.table import Table
    console = Console()
    
    try:
        from navig.providers import BUILTIN_PROVIDERS
        
        console.print("[bold cyan]Available AI Models[/bold cyan]")
        console.print()
        
        for pname, pconfig in BUILTIN_PROVIDERS.items():
            # Filter by provider if specified
            if provider and pname.lower() != provider.lower():
                continue
            
            if not pconfig.models:
                if pname == "ollama":
                    console.print(f"[bold]{pname}[/bold] [dim](models discovered dynamically)[/dim]")
                elif pname == "airllm":
                    console.print(f"[bold]{pname}[/bold] [dim](local inference - 70B+ models on limited VRAM)[/dim]")
                    console.print("  Suggested models (any HuggingFace model ID works):")
                    console.print("  • meta-llama/Llama-3.3-70B-Instruct")
                    console.print("  • Qwen/Qwen2.5-72B-Instruct")
                    console.print("  • deepseek-ai/deepseek-coder-33b-instruct")
                    console.print()
                continue
            
            console.print(f"[bold]{pname}[/bold]")
            
            table = Table(box=None, show_header=True, padding=(0, 2))
            table.add_column("Model ID", style="cyan")
            table.add_column("Name")
            table.add_column("Context", justify="right")
            table.add_column("Max Tokens", justify="right")
            
            for model in pconfig.models:
                ctx_str = f"{model.context_window // 1000}K" if model.context_window >= 1000 else str(model.context_window)
                table.add_row(
                    model.id,
                    model.name,
                    ctx_str,
                    str(model.max_tokens),
                )
            
            console.print(table)
            console.print()
        
        if provider and provider.lower() not in [p.lower() for p in BUILTIN_PROVIDERS.keys()]:
            console.print(f"[yellow]Unknown provider: {provider}[/yellow]")
            console.print(f"[dim]Available: {', '.join(BUILTIN_PROVIDERS.keys())}[/dim]")
        
    except ImportError:
        console.print("[yellow]Provider system not available.[/yellow]")


@ai_app.command("providers")
def ai_providers(
    ctx: typer.Context,
    add: Optional[str] = typer.Option(None, "--add", "-a", help="Add API key for provider (e.g., openai, anthropic)"),
    remove: Optional[str] = typer.Option(None, "--remove", "-r", help="Remove API key for provider"),
    test: Optional[str] = typer.Option(None, "--test", "-t", help="Test provider connection"),
):
    """Manage AI providers and API keys."""
    from rich.console import Console
    from rich.table import Table
    console = Console()
    
    try:
        from navig.providers import AuthProfileManager, BUILTIN_PROVIDERS
        auth = AuthProfileManager()
        
        if add:
            # Add API key for provider
            import getpass
            provider = add.lower()
            if provider not in BUILTIN_PROVIDERS:
                console.print(f"[yellow]⚠ Unknown provider '{provider}'. Known: {', '.join(BUILTIN_PROVIDERS.keys())}[/yellow]")
            
            api_key = getpass.getpass(f"Enter API key for {provider}: ")
            if api_key:
                auth.add_api_key(provider=provider, api_key=api_key, profile_id=f"{provider}-default")
                auth.save()
                console.print(f"[green]✓ API key saved for {provider}[/green]")
            else:
                console.print("[yellow]No key entered, cancelled[/yellow]")
            return
        
        if remove:
            # Remove API key for provider
            provider = remove.lower()
            profile_id = f"{provider}-default"
            if auth.remove_profile(profile_id):
                auth.save()
                console.print(f"[green]✓ Removed API key for {provider}[/green]")
            else:
                console.print(f"[yellow]No API key found for {provider}[/yellow]")
            return
        
        if test:
            # Test provider connection
            provider = test.lower()
            api_key, source = auth.resolve_auth(provider)
            if not api_key:
                console.print(f"[red]✗ No API key configured for {provider}[/red]")
                console.print(f"  Add one with: navig ai providers --add {provider}")
                return
            
            console.print(f"[dim]Testing {provider} (key from: {source})...[/dim]")
            
            # Quick test - try to list models or make a tiny request
            import asyncio
            from navig.providers import create_client, BUILTIN_PROVIDERS
            
            config = BUILTIN_PROVIDERS.get(provider)
            if not config:
                console.print(f"[red]✗ Unknown provider: {provider}[/red]")
                return
            
            try:
                client = create_client(config, api_key=api_key, timeout=10)
                # Make a minimal request to test auth
                from navig.providers import CompletionRequest, Message
                
                async def test_request():
                    request = CompletionRequest(
                        messages=[Message(role="user", content="Hi")],
                        model=config.models[0].id if config.models else "gpt-4o-mini",
                        max_tokens=5,
                    )
                    try:
                        response = await client.complete(request)
                        return True, None
                    except Exception as e:
                        return False, str(e)
                    finally:
                        await client.close()
                
                success, error = asyncio.run(test_request())
                if success:
                    console.print(f"[green]✓ {provider} is working![/green]")
                else:
                    console.print(f"[red]✗ {provider} error: {error}[/red]")
            except Exception as e:
                console.print(f"[red]✗ Test failed: {e}[/red]")
            return
        
        # List providers and their status
        console.print("[bold cyan]AI Providers[/bold cyan]")
        console.print()
        
        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Provider", style="cyan")
        table.add_column("API Key", style="green")
        table.add_column("Source")
        table.add_column("Models", style="dim")
        
        for name, config in BUILTIN_PROVIDERS.items():
            api_key, source = auth.resolve_auth(name)
            key_status = "✓ configured" if api_key else "✗ not set"
            key_style = "green" if api_key else "red"
            
            model_count = len(config.models)
            models_str = f"{model_count} models" if model_count else "dynamic"
            
            table.add_row(
                name,
                f"[{key_style}]{key_status}[/{key_style}]",
                source or "-",
                models_str,
            )
        
        console.print(table)
        console.print()
        console.print("[dim]Add a key: navig ai providers --add <provider>[/dim]")
        console.print("[dim]Test connection: navig ai providers --test <provider>[/dim]")
        console.print("[dim]Configure AirLLM: navig ai airllm --configure[/dim]")
        console.print("[dim]OAuth login: navig ai login openai-codex[/dim]")
        
    except ImportError:
        console.print("[yellow]Provider system not available. Install httpx: pip install httpx[/yellow]")


@ai_app.command("airllm")
def ai_airllm(
    ctx: typer.Context,
    configure: bool = typer.Option(False, "--configure", "-c", help="Configure AirLLM settings"),
    model_path: Optional[str] = typer.Option(None, "--model-path", "-p", help="HuggingFace model ID or local path"),
    max_vram: Optional[float] = typer.Option(None, "--max-vram", help="Maximum VRAM in GB"),
    compression: Optional[str] = typer.Option(None, "--compression", help="Compression mode: 4bit, 8bit, or none"),
    test: bool = typer.Option(False, "--test", "-t", help="Test AirLLM with a sample prompt"),
    status: bool = typer.Option(False, "--status", "-s", help="Show AirLLM status and configuration"),
):
    """Configure and manage AirLLM local inference provider.
    
    AirLLM enables running 70B+ models on limited VRAM (4-8GB) through
    layer-wise inference and model sharding.
    
    Examples:
        navig ai airllm --status
        navig ai airllm --configure --model-path meta-llama/Llama-3.3-70B-Instruct
        navig ai airllm --configure --compression 4bit --max-vram 8
        navig ai airllm --test
    """
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
    
    # Check if AirLLM is installed
    try:
        from navig.providers import is_airllm_available, get_airllm_vram_recommendations
        from navig.providers.airllm import AirLLMConfig
    except ImportError:
        console.print("[red]✗ Provider system not available.[/red]")
        raise typer.Exit(1)
    
    airllm_available = is_airllm_available()
    
    if status or (not configure and not test):
        # Show AirLLM status
        console.print("[bold cyan]AirLLM Local Inference Provider[/bold cyan]")
        console.print()
        
        # Installation status
        if airllm_available:
            console.print("[green]✓ AirLLM is installed[/green]")
        else:
            console.print("[yellow]✗ AirLLM is not installed[/yellow]")
            console.print("  Install with: [cyan]pip install airllm[/cyan]")
            console.print()
        
        # Current configuration
        console.print()
        console.print("[bold]Current Configuration:[/bold]")
        
        config = AirLLMConfig.from_env()
        config_table = Table(box=None, show_header=False, padding=(0, 2))
        config_table.add_column("Setting", style="dim")
        config_table.add_column("Value")
        
        config_table.add_row("Model Path", config.model_path or "[dim]not set[/dim]")
        config_table.add_row("Max VRAM", f"{config.max_vram_gb} GB")
        config_table.add_row("Compression", config.compression or "none")
        config_table.add_row("Device", config.device)
        config_table.add_row("Layer Shards Path", config.layer_shards_path or "[dim]default[/dim]")
        config_table.add_row("Prefetching", "enabled" if config.prefetching else "disabled")
        
        console.print(config_table)
        
        # VRAM recommendations
        console.print()
        console.print("[bold]VRAM Recommendations:[/bold]")
        recommendations = get_airllm_vram_recommendations()
        for model_size, rec in recommendations.items():
            console.print(f"  • {model_size}: {rec}")
        
        # Environment variables
        console.print()
        console.print("[bold]Environment Variables:[/bold]")
        console.print("  AIRLLM_MODEL_PATH     - HuggingFace model ID or local path")
        console.print("  AIRLLM_MAX_VRAM_GB    - Maximum VRAM to use")
        console.print("  AIRLLM_COMPRESSION    - '4bit', '8bit', or empty for none")
        console.print("  AIRLLM_DEVICE         - 'cuda', 'cpu', or 'mps' (macOS)")
        console.print("  HF_TOKEN              - HuggingFace token for gated models")
        
        # Suggested models
        console.print()
        console.print("[bold]Suggested Models:[/bold]")
        console.print("  • meta-llama/Llama-3.3-70B-Instruct")
        console.print("  • Qwen/Qwen2.5-72B-Instruct")
        console.print("  • deepseek-ai/deepseek-coder-33b-instruct")
        console.print("  • mistralai/Mixtral-8x7B-Instruct-v0.1")
        
        return
    
    if configure:
        # Configure AirLLM settings
        
        config_updates = {}
        
        if model_path is not None:
            config_updates["AIRLLM_MODEL_PATH"] = model_path
            console.print(f"[green]✓ Model path: {model_path}[/green]")
        
        if max_vram is not None:
            config_updates["AIRLLM_MAX_VRAM_GB"] = str(max_vram)
            console.print(f"[green]✓ Max VRAM: {max_vram} GB[/green]")
        
        if compression is not None:
            if compression.lower() == "none":
                compression = ""
            config_updates["AIRLLM_COMPRESSION"] = compression
            console.print(f"[green]✓ Compression: {compression or 'disabled'}[/green]")
        
        if config_updates:
            # Save to config file
            try:
                config_manager = _get_config_manager()
                # Build update dict with proper key names
                updates = {}
                for key, value in config_updates.items():
                    config_key = f"airllm_{key.lower().replace('airllm_', '')}"
                    updates[config_key] = value
                config_manager.update_global_config(updates)
                console.print()
                console.print("[green]Configuration saved to ~/.navig/config.yaml[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠ Could not save to config file: {e}[/yellow]")
            
            # Also show env var export commands
            console.print()
            console.print("[dim]Or set environment variables:[/dim]")
            for key, value in config_updates.items():
                console.print(f"  export {key}=\"{value}\"")
        else:
            console.print("[yellow]No configuration options specified.[/yellow]")
            console.print("Use --model-path, --max-vram, or --compression")
        
        return
    
    if test:
        # Test AirLLM with a sample prompt
        if not airllm_available:
            console.print("[red]✗ AirLLM is not installed.[/red]")
            console.print("  Install with: [cyan]pip install airllm[/cyan]")
            raise typer.Exit(1)
        
        config = AirLLMConfig.from_env()
        if not config.model_path:
            console.print("[red]✗ No model configured.[/red]")
            console.print("  Set AIRLLM_MODEL_PATH or use --configure --model-path")
            raise typer.Exit(1)
        
        console.print(f"[dim]Testing AirLLM with model: {config.model_path}[/dim]")
        console.print("[dim]This may take a while on first run (downloading/sharding model)...[/dim]")
        console.print()
        
        import asyncio
        from navig.providers import create_airllm_client, CompletionRequest, Message
        
        async def run_test():
            try:
                client = create_airllm_client(config)
                request = CompletionRequest(
                    messages=[
                        Message(role="user", content="What is 2 + 2? Answer briefly."),
                    ],
                    model=config.model_path,
                    max_tokens=50,
                )
                
                response = await client.complete(request)
                await client.close()
                return response
            except Exception as e:
                return str(e)
        
        with console.status("[bold green]Running inference..."):
            result = asyncio.run(run_test())
        
        if hasattr(result, 'content'):
            console.print("[green]✓ AirLLM is working![/green]")
            console.print()
            console.print(Panel(result.content or "[no response]", title="Response"))
            if result.usage:
                console.print(f"[dim]Tokens: {result.usage.get('prompt_tokens', 0)} prompt, {result.usage.get('completion_tokens', 0)} completion[/dim]")
        else:
            console.print(f"[red]✗ Test failed: {result}[/red]")
            raise typer.Exit(1)


@ai_app.command("login")
def ai_login(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="OAuth provider (e.g., openai-codex)"),
    headless: bool = typer.Option(False, "--headless", help="Headless mode (no browser auto-open)"),
):
    """Login to an AI provider using OAuth (e.g., OpenAI Codex)."""
    from rich.console import Console
    console = Console()
    
    try:
        from navig.providers import (
            AuthProfileManager,
            OAUTH_PROVIDERS,
            run_oauth_flow_interactive,
            run_oauth_flow_headless,
        )
        
        # Check if any OAuth providers are configured
        if not OAUTH_PROVIDERS:
            console.print("[red]✗ OAuth authentication is not currently available.[/red]")
            console.print()
            console.print("[yellow]Why?[/yellow]")
            console.print("OAuth requires provider-specific client registration.")
            console.print("OpenAI's OAuth is only available to enterprise partners.")
            console.print()
            console.print("[cyan]Use API key authentication instead:[/cyan]")
            console.print("  navig cred add openai sk-... --type api-key")
            console.print("  navig cred add anthropic sk-ant-... --type api-key")
            console.print()
            console.print("[dim]See: docs/development/oauth-limitations.md[/dim]")
            raise typer.Exit(1)
        
        provider_lower = provider.lower()
        if provider_lower not in OAUTH_PROVIDERS:
            console.print(f"[red]✗ Unknown OAuth provider: {provider}[/red]")
            console.print(f"[dim]Available: {', '.join(OAUTH_PROVIDERS.keys()) or 'none'}[/dim]")
            raise typer.Exit(1)
        
        oauth_config = OAUTH_PROVIDERS[provider_lower]
        console.print(f"[bold cyan]OAuth Login: {oauth_config.name}[/bold cyan]")
        console.print()
        
        if headless:
            # Headless mode
            console.print("[yellow]Headless mode: Copy the URL below and open it in a browser.[/yellow]")
            console.print()
            
            def on_auth_url(url: str):
                console.print("[bold]Authorization URL:[/bold]")
                console.print(url)
                console.print()
            
            def get_callback_input() -> str:
                console.print("[bold]After signing in, paste the redirect URL here:[/bold]")
                return input("> ")
            
            result = run_oauth_flow_headless(
                provider_lower,
                on_auth_url=on_auth_url,
                get_callback_input=get_callback_input,
            )
        else:
            # Interactive mode
            def on_progress(msg: str):
                console.print(f"[dim]{msg}[/dim]")
            
            result = run_oauth_flow_interactive(
                provider_lower,
                on_progress=on_progress,
            )
        
        if result.success and result.credentials:
            # Save credentials
            auth = AuthProfileManager()
            profile_id = auth.add_oauth_credentials(
                provider=provider_lower,
                access_token=result.credentials.access,
                refresh_token=result.credentials.refresh,
                expires_at=result.credentials.expires,
                client_id=result.credentials.client_id,
                account_id=result.credentials.account_id,
                email=result.credentials.email,
            )
            
            console.print()
            console.print(f"[green]✓ Successfully logged in to {oauth_config.name}![/green]")
            console.print(f"[dim]Profile saved: {profile_id}[/dim]")
            
            if result.credentials.account_id:
                console.print(f"[dim]Account ID: {result.credentials.account_id}[/dim]")
            
            console.print()
            console.print("[dim]You can now use this provider with:[/dim]")
            console.print(f"  navig ai ask 'your question' --model {provider_lower}:gpt-4o")
        else:
            console.print(f"[red]✗ OAuth failed: {result.error}[/red]")
            raise typer.Exit(1)
        
    except ImportError as e:
        console.print(f"[yellow]OAuth not available: {e}[/yellow]")
        console.print("[dim]Install httpx: pip install httpx[/dim]")
        raise typer.Exit(1)


@ai_app.command("logout")
def ai_logout(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="Provider to logout from"),
):
    """Remove OAuth credentials for a provider."""
    from rich.console import Console
    console = Console()
    
    try:
        from navig.providers import AuthProfileManager
        
        auth = AuthProfileManager()
        provider_lower = provider.lower()
        
        # Find and remove all profiles for this provider
        removed = []
        for profile_id in list(auth.store.profiles.keys()):
            cred = auth.store.profiles[profile_id]
            if cred.provider == provider_lower:
                del auth.store.profiles[profile_id]
                removed.append(profile_id)
        
        if removed:
            auth.save()
            console.print(f"[green]✓ Logged out from {provider}[/green]")
            for pid in removed:
                console.print(f"[dim]  Removed: {pid}[/dim]")
        else:
            console.print(f"[yellow]No credentials found for {provider}[/yellow]")
            
    except ImportError:
        console.print("[yellow]Provider system not available.[/yellow]")


# ============================================================================
# AI MEMORY COMMANDS
# ============================================================================

memory_app = typer.Typer(
    help="Manage AI memory - what NAVIG knows about you",
    invoke_without_command=True,
    no_args_is_help=False,
)
ai_app.add_typer(memory_app, name="memory")


@memory_app.callback()
def memory_callback(ctx: typer.Context):
    """AI Memory - what NAVIG knows about you."""
    if ctx.invoked_subcommand is None:
        # Default: show memory
        _memory_show()


def _memory_show():
    """Display current user profile."""
    from rich.console import Console
    console = Console()
    try:
        from navig.memory.user_profile import get_profile
        profile = get_profile()
        console.print(profile.to_human_readable())
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error loading profile: {e}[/red]")


@memory_app.command("show")
def memory_show():
    """Display what NAVIG knows about you."""
    _memory_show()


@memory_app.command("edit")
def memory_edit():
    """Open user profile in your default editor."""
    import os
    from pathlib import Path
    from rich.console import Console
    console = Console()
    
    profile_path = Path.home() / '.navig' / 'memory' / 'user_profile.json'
    
    if not profile_path.exists():
        # Create empty profile first
        try:
            from navig.memory.user_profile import get_profile
            profile = get_profile()
            profile.save()
            console.print(f"[green]Created new profile at: {profile_path}[/green]")
        except Exception as e:
            console.print(f"[red]Error creating profile: {e}[/red]")
            raise typer.Exit(1)
    
    # Get editor from environment
    editor = os.environ.get('EDITOR', os.environ.get('VISUAL', 'notepad' if os.name == 'nt' else 'nano'))
    
    console.print(f"[dim]Opening {profile_path} in {editor}...[/dim]")
    
    import subprocess
    try:
        subprocess.run([editor, str(profile_path)], check=True)
        console.print("[green]Profile updated. Changes will be loaded on next agent start.[/green]")
    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to open editor: {editor}[/red]")
    except FileNotFoundError:
        console.print(f"[red]Editor not found: {editor}[/red]")
        console.print(f"[dim]Profile is at: {profile_path}[/dim]")


@memory_app.command("add")
def memory_add(
    note: str = typer.Argument(..., help="Note to add to memory"),
    category: str = typer.Option("user_note", "--category", "-c", help="Note category"),
):
    """Add a note to NAVIG's memory about you."""
    from rich.console import Console
    console = Console()
    try:
        from navig.memory.user_profile import get_profile
        profile = get_profile()
        note_obj = profile.add_note(note, category=category, source="user")
        profile.save()
        console.print(f"[green]✓ Added note:[/green] {note[:60]}...")
        console.print(f"[dim]Category: {category} | Time: {note_obj.timestamp[:19]}[/dim]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error adding note: {e}[/red]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """Search NAVIG's memory about you."""
    from rich.console import Console
    console = Console()
    try:
        from navig.memory.user_profile import get_profile
        profile = get_profile()
        results = profile.search_memory(query, limit=limit)
        
        if results:
            console.print(f"[bold]Found {len(results)} result(s) for '{query}':[/bold]\n")
            for i, result in enumerate(results, 1):
                console.print(f"  {i}. {result}")
        else:
            console.print(f"[yellow]No results found for '{query}'[/yellow]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error searching: {e}[/red]")


@memory_app.command("clear")
def memory_clear(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm clearing all memory"),
):
    """Clear all memory (requires --confirm)."""
    from rich.console import Console
    console = Console()
    if not confirm:
        console.print("[yellow]⚠️  This will delete all stored user profile data.[/yellow]")
        console.print("[dim]Run with --confirm to proceed.[/dim]")
        raise typer.Exit(1)
    
    try:
        from navig.memory.user_profile import get_profile
        profile = get_profile()
        
        if profile.clear(confirm=True):
            console.print("[green]✓ Memory cleared. Backup created.[/green]")
        else:
            console.print("[red]Failed to clear memory.[/red]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@memory_app.command("set")
def memory_set(
    field: str = typer.Argument(..., help="Field to set (e.g., identity.name, technical_context.stack)"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a specific profile field."""
    from rich.console import Console
    console = Console()
    try:
        from navig.memory.user_profile import get_profile
        profile = get_profile()
        
        # Handle list fields (comma-separated)
        if field in ['technical_context.stack', 'technical_context.managed_hosts', 
                     'technical_context.primary_projects', 'work_patterns.active_hours',
                     'work_patterns.common_tasks', 'goals', 'preferences.confirmation_required_for']:
            value = [v.strip() for v in value.split(',')]
        
        updated = profile.update({field: value})
        
        if updated:
            console.print(f"[green]✓ Updated {field} = {value}[/green]")
        else:
            console.print(f"[red]Failed to update {field}. Check field name.[/red]")
            console.print("[dim]Valid fields: identity.name, identity.timezone, identity.role, "
                         "technical_context.stack, goals, preferences.communication_style[/dim]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# Legacy flat command for backward compatibility
@app.command("ai", hidden=True)
def ai_legacy(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override default AI model"),
):
    """[DEPRECATED: Use 'navig ai ask'] Ask AI about server."""
    deprecation_warning("navig ai <question>", "navig ai ask <question>")
    from navig.commands.ai import ask_ai
    ask_ai(question, model, ctx.obj)


# ============================================================================
# PROACTIVE ASSISTANT COMMANDS (Deprecated - use 'ai' group)
# ============================================================================

assistant_app = typer.Typer(
    help="[DEPRECATED: Use 'navig ai'] Proactive AI assistant",
    invoke_without_command=True,
    no_args_is_help=False,
    hidden=True,
)
app.add_typer(assistant_app, name="assistant")


@assistant_app.callback()
def assistant_callback(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai'] AI Assistant."""
    deprecation_warning("navig assistant", "navig ai")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_assistant_menu
        launch_assistant_menu()
        raise typer.Exit()


@assistant_app.command("status")
def assistant_status(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai show --status']"""
    deprecation_warning("navig assistant status", "navig ai show --status")
    from navig.commands.assistant import status_cmd
    status_cmd(ctx.obj)


@assistant_app.command("analyze")
def assistant_analyze(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai diagnose']"""
    deprecation_warning("navig assistant analyze", "navig ai diagnose")
    from navig.commands.assistant import analyze_cmd
    analyze_cmd(ctx.obj)


@assistant_app.command("context")
def assistant_context(
    ctx: typer.Context,
    clipboard: bool = typer.Option(False, "--clipboard", help="Copy context to clipboard"),
    file: Optional[str] = typer.Option(None, "--file", help="Save context to file"),
):
    """[DEPRECATED: Use 'navig ai show --context']"""
    deprecation_warning("navig assistant context", "navig ai show --context")
    from navig.commands.assistant import context_cmd
    context_cmd(ctx.obj, clipboard, file)


@assistant_app.command("reset")
def assistant_reset(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai run --reset']"""
    deprecation_warning("navig assistant reset", "navig ai run --reset")
    from navig.commands.assistant import reset_cmd
    reset_cmd(ctx.obj)


@assistant_app.command("config")
def assistant_config(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ai edit']"""
    deprecation_warning("navig assistant config", "navig ai edit")
    from navig.commands.assistant import config_cmd
    config_cmd(ctx.obj)


# ============================================================================
# HESTIACP MANAGEMENT COMMANDS (DEPRECATED - use 'navig web hestia')
# ============================================================================

hestia_app = typer.Typer(
    help="[DEPRECATED: Use 'navig web hestia'] HestiaCP management",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(hestia_app, name="hestia", hidden=True)  # Deprecated


@hestia_app.callback()
def hestia_callback(ctx: typer.Context):
    """HestiaCP management - DEPRECATED, use 'navig web hestia'."""
    deprecation_warning("navig hestia", "navig web hestia")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_hestia_menu
        launch_hestia_menu()
        raise typer.Exit()


@hestia_app.command("users")
def hestia_list_users(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one user per line) for scripting"),
):
    """List HestiaCP users."""
    from navig.commands.hestia import list_users_cmd
    ctx.obj['plain'] = plain
    list_users_cmd(ctx.obj)


@hestia_app.command("domains")
def hestia_list_domains(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Filter by username"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one domain per line) for scripting"),
):
    """List HestiaCP domains."""
    from navig.commands.hestia import list_domains_cmd
    ctx.obj['plain'] = plain
    list_domains_cmd(user, ctx.obj)


@hestia_app.command("add-user")
def hestia_add_user(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username to create"),
    password: str = typer.Argument(..., help="User password"),
    email: str = typer.Argument(..., help="User email address"),
):
    """Add new HestiaCP user."""
    from navig.commands.hestia import add_user_cmd
    add_user_cmd(username, password, email, ctx.obj)


@hestia_app.command("delete-user")
def hestia_delete_user(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion without confirmation"),
):
    """Delete HestiaCP user."""
    ctx.obj['force'] = force
    from navig.commands.hestia import delete_user_cmd
    delete_user_cmd(username, ctx.obj)


@hestia_app.command("add-domain")
def hestia_add_domain(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
    domain: str = typer.Argument(..., help="Domain name to add"),
):
    """Add domain to HestiaCP user."""
    from navig.commands.hestia import add_domain_cmd
    add_domain_cmd(user, domain, ctx.obj)


@hestia_app.command("delete-domain")
def hestia_delete_domain(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
    domain: str = typer.Argument(..., help="Domain name to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion without confirmation"),
):
    """Delete domain from HestiaCP."""
    ctx.obj['force'] = force
    from navig.commands.hestia import delete_domain_cmd
    delete_domain_cmd(user, domain, ctx.obj)


@hestia_app.command("renew-ssl")
def hestia_renew_ssl(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
    domain: str = typer.Argument(..., help="Domain name"),
):
    """Renew SSL certificate for domain."""
    from navig.commands.hestia import renew_ssl_cmd
    renew_ssl_cmd(user, domain, ctx.obj)


@hestia_app.command("rebuild-web")
def hestia_rebuild_web(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username"),
):
    """Rebuild web configuration for user."""
    from navig.commands.hestia import rebuild_web_cmd
    rebuild_web_cmd(user, ctx.obj)


@hestia_app.command("backup-user")
def hestia_backup_user(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username to backup"),
):
    """Backup HestiaCP user."""
    from navig.commands.hestia import backup_user_cmd
    backup_user_cmd(user, ctx.obj)


# ============================================================================
# TEMPLATE MANAGEMENT COMMANDS
# ============================================================================

template_app = typer.Typer(
    help="[DEPRECATED: Use 'navig flow template'] Manage templates",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(template_app, name="template", hidden=True)  # Deprecated


@template_app.callback()
def template_callback(ctx: typer.Context):
    """Template management - DEPRECATED, use 'navig flow template'."""
    deprecation_warning("navig template", "navig flow template")
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_template_menu
        launch_template_menu()
        raise typer.Exit()


@template_app.command("list")
def template_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one template per line) for scripting"),
):
    """List all available templates."""
    from navig.commands.template import list_templates_cmd
    ctx.obj['plain'] = plain
    list_templates_cmd(ctx.obj)


@template_app.command("enable")
def template_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable an template."""
    from navig.commands.template import enable_template_cmd
    enable_template_cmd(name, ctx.obj)


@template_app.command("disable")
def template_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable an template."""
    from navig.commands.template import disable_template_cmd
    disable_template_cmd(name, ctx.obj)


@template_app.command("toggle")
def template_toggle(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to toggle"),
):
    """Toggle template enabled/disabled state."""
    from navig.commands.template import toggle_template_cmd
    toggle_template_cmd(name, ctx.obj)


@template_app.command("info")
def template_info(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to show details for"),
):
    """Show detailed information about an template."""
    from navig.commands.template import show_template_cmd
    show_template_cmd(name, ctx.obj)


@template_app.command("validate")
def template_validate(ctx: typer.Context):
    """Validate all template configurations."""
    from navig.commands.template import validate_templates_cmd
    validate_templates_cmd(ctx.obj)


@template_app.command("edit")
def template_edit(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to edit"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
):
    """Edit host-specific template override file in $EDITOR."""
    from navig.commands.template import edit_template_cmd
    ctx.obj['server'] = server
    edit_template_cmd(name, ctx.obj)


# ============================================================================
# ADDON COMMANDS (alias for template commands)
# ============================================================================

addon_app = typer.Typer(
    help="[DEPRECATED: Use 'navig flow template'] Addon commands"
)
app.add_typer(addon_app, name="addon", hidden=True)  # Deprecated


@addon_app.callback()
def addon_callback(ctx: typer.Context):
    """Addon management - DEPRECATED, use 'navig flow template'."""
    deprecation_warning("navig addon", "navig flow template")


@addon_app.command("list")
def addon_list(ctx: typer.Context):
    """List available templates."""
    from navig.commands.template import addon_list_deprecated
    addon_list_deprecated(ctx.obj)


@addon_app.command("enable")
def addon_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable a template."""
    from navig.commands.template import addon_enable_deprecated
    addon_enable_deprecated(name, ctx.obj)


@addon_app.command("disable")
def addon_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable a template."""
    from navig.commands.template import addon_disable_deprecated
    addon_disable_deprecated(name, ctx.obj)


@addon_app.command("info")
def addon_info(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to show"),
):
    """Show template info."""
    from navig.commands.template import addon_info_deprecated
    addon_info_deprecated(name, ctx.obj)


@addon_app.command("run")
def addon_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to run"),
    command: Optional[str] = typer.Argument(None, help="Template command to execute"),
    args: Optional[List[str]] = typer.Argument(None, help="Arguments for the template command"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without changes"),
):
    """Run a template command (deprecated; use flow template run)."""
    deprecation_warning("navig addon run", "navig flow template run")
    from navig.commands.template import deploy_template_cmd
    deploy_template_cmd(
        name,
        command_name=command,
        command_args=args or [],
        dry_run=dry_run,
        ctx_obj=ctx.obj,
    )


@addon_app.command("run")
def addon_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to run/deploy"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without changes"),
):
    """Run/deploy a template (deprecated)."""
    from navig.commands.template import addon_run_deprecated
    addon_run_deprecated(name, ctx.obj, dry_run=dry_run)


# ============================================================================
# MIGRATION COMMANDS
# ============================================================================

migrate_app = typer.Typer(help="Migration utilities for upgrading NAVIG configurations")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("addons-to-templates")
def migrate_addons_to_templates(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done without making changes"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing YAML files"),
):
    """
    Migrate legacy addons to templates format.
    
    Converts:
    - Repository: addons/<name>/addon.json → templates/<name>/template.yaml
    - User: ~/.navig/apps/<server>/addons/*.json → ~/.navig/apps/<server>/templates/*.yaml
    
    This migration is idempotent and safe to run multiple times.
    
    Examples:
        navig migrate addons-to-templates --dry-run    # Preview changes
        navig migrate addons-to-templates              # Run migration
        navig migrate addons-to-templates --force      # Overwrite existing files
    """
    from navig.migrations.migrate_addons_to_templates import migrate_addons_to_templates_cmd
    ctx.obj['dry_run'] = dry_run
    ctx.obj['force'] = force
    migrate_addons_to_templates_cmd(ctx.obj)


# ============================================================================
# SERVER-SPECIFIC TEMPLATE COMMANDS
# ============================================================================

server_template_app = typer.Typer(help="Manage per-server template configurations")
app.add_typer(server_template_app, name="server-template")


@server_template_app.command("list")
def server_template_list(
    ctx: typer.Context,
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
    enabled_only: bool = typer.Option(False, "--enabled", "-e", help="Show only enabled templates"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one template per line) for scripting"),
):
    """List template configurations for a server."""
    from navig.commands.server_template import list_server_templates_cmd
    ctx.obj['server'] = server
    ctx.obj['enabled_only'] = enabled_only
    ctx.obj['plain'] = plain
    list_server_templates_cmd(ctx.obj)


@server_template_app.command("show")
def server_template_show(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to show"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
):
    """Show merged configuration for a server template."""
    from navig.commands.server_template import show_template_config_cmd
    ctx.obj['server'] = server
    show_template_config_cmd(template_name, ctx.obj)


@server_template_app.command("enable")
def server_template_enable(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to enable"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
):
    """Enable an template for a specific server."""
    from navig.commands.server_template import enable_server_template_cmd
    ctx.obj['server'] = server
    enable_server_template_cmd(template_name, ctx.obj)


@server_template_app.command("disable")
def server_template_disable(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to disable"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
):
    """Disable an template for a specific server."""
    from navig.commands.server_template import disable_server_template_cmd
    ctx.obj['server'] = server
    disable_server_template_cmd(template_name, ctx.obj)


@server_template_app.command("set")
def server_template_set(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name"),
    key_path: str = typer.Argument(..., help="Dot-separated config path (e.g., 'paths.web_root')"),
    value: str = typer.Argument(..., help="Value to set (JSON-parseable)"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
):
    """Set a custom value for a server template configuration."""
    from navig.commands.server_template import set_template_value_cmd
    ctx.obj['server'] = server
    set_template_value_cmd(template_name, key_path, value, ctx.obj)


@server_template_app.command("sync")
def server_template_sync(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to sync"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite all custom settings"),
):
    """Sync template configuration from template."""
    from navig.commands.server_template import sync_template_cmd
    ctx.obj['server'] = server
    ctx.obj['force'] = force
    sync_template_cmd(template_name, ctx.obj)


@server_template_app.command("init")
def server_template_init(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to initialize"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Server name (uses active if omitted)"),
    enable: bool = typer.Option(False, "--enable", "-e", help="Enable template after initialization"),
):
    """Manually initialize an template for a server."""
    from navig.commands.server_template import init_template_cmd
    ctx.obj['server'] = server
    ctx.obj['enable'] = enable
    init_template_cmd(template_name, ctx.obj)


# ============================================================================
# MCP SERVER MANAGEMENT COMMANDS
# ============================================================================

mcp_app = typer.Typer(
    help="Manage MCP (Model Context Protocol) servers",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.callback()
def mcp_callback(ctx: typer.Context):
    """MCP management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_mcp_menu
        launch_mcp_menu()
        raise typer.Exit()


@mcp_app.command("search")
def mcp_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
):
    """Search MCP directory for servers."""
    from navig.commands.mcp import search_mcp_cmd
    search_mcp_cmd(query, ctx.obj)


@mcp_app.command("install")
def mcp_install(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to install"),
):
    """Install an MCP server."""
    from navig.commands.mcp import install_mcp_cmd
    install_mcp_cmd(name, ctx.obj)


@mcp_app.command("uninstall")
def mcp_uninstall(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to uninstall"),
):
    """Uninstall an MCP server."""
    from navig.commands.mcp import uninstall_mcp_cmd
    uninstall_mcp_cmd(name, ctx.obj)


@mcp_app.command("list")
def mcp_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one server per line) for scripting"),
):
    """List installed MCP servers."""
    from navig.commands.mcp import list_mcp_cmd
    ctx.obj['plain'] = plain
    list_mcp_cmd(ctx.obj)


@mcp_app.command("enable")
def mcp_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to enable"),
):
    """Enable an MCP server."""
    from navig.commands.mcp import enable_mcp_cmd
    enable_mcp_cmd(name, ctx.obj)


@mcp_app.command("disable")
def mcp_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to disable"),
):
    """Disable an MCP server."""
    from navig.commands.mcp import disable_mcp_cmd
    disable_mcp_cmd(name, ctx.obj)


@mcp_app.command("start")
def mcp_start(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to start (or 'all' for all enabled)"),
):
    """Start an MCP server."""
    from navig.commands.mcp import start_mcp_cmd
    start_mcp_cmd(name, ctx.obj)


@mcp_app.command("stop")
def mcp_stop(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to stop (or 'all' for all running)"),
):
    """Stop an MCP server."""
    from navig.commands.mcp import stop_mcp_cmd
    stop_mcp_cmd(name, ctx.obj)


@mcp_app.command("restart")
def mcp_restart(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to restart"),
):
    """Restart an MCP server."""
    from navig.commands.mcp import restart_mcp_cmd
    restart_mcp_cmd(name, ctx.obj)


@mcp_app.command("status")
def mcp_status(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="MCP server name to check status"),
):
    """Show detailed MCP server status."""
    from navig.commands.mcp import status_mcp_cmd
    status_mcp_cmd(name, ctx.obj)


@mcp_app.command("serve")
def mcp_serve(
    ctx: typer.Context,
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport mode: stdio, websocket"),
    port: int = typer.Option(3001, "--port", "-p", help="Port for WebSocket mode"),
    token: str = typer.Option(None, "--token", help="Auth token (auto-generated if omitted)"),
):
    """Start NAVIG as an MCP server for AI assistants like Copilot.
    
    This exposes NAVIG's hosts, apps, wiki, and database info to AI assistants
    via the Model Context Protocol (MCP).
    
    Examples:
        navig mcp serve                          # Start in stdio mode (for VS Code)
        navig mcp serve --transport websocket    # WebSocket on port 3001
        navig mcp serve -t websocket -p 4000     # WebSocket on custom port
    """
    from navig.mcp_server import start_mcp_server
    
    # Infer transport from port for backward compatibility
    if transport == "stdio" and port != 3001:
        transport = "websocket"
    
    if transport == "stdio":
        start_mcp_server(mode="stdio")
    elif transport == "websocket":
        ch.info(f"Starting NAVIG MCP WebSocket server on port {port}...")
        start_mcp_server(mode="websocket", port=port, token=token)
    else:
        ch.error(f"Unknown transport: {transport}. Use 'stdio' or 'websocket'.")
        raise typer.Exit(1)


@mcp_app.command("config")
def mcp_config_cmd(
    ctx: typer.Context,
    target: str = typer.Argument("vscode", help="Config target: vscode, claude"),
    output: bool = typer.Option(False, "--output", "-o", help="Output config to file"),
):
    """Generate MCP configuration for AI assistants.
    
    Examples:
        navig mcp config vscode    # Show VS Code MCP config
        navig mcp config claude    # Show Claude Desktop config
        navig mcp config vscode -o # Write to .vscode/mcp.json
    """
    import json
    from navig.mcp_server import generate_vscode_mcp_config, generate_claude_mcp_config
    from pathlib import Path
    
    if target == "vscode":
        config = generate_vscode_mcp_config()
        filename = ".vscode/mcp.json"
    elif target == "claude":
        config = generate_claude_mcp_config()
        filename = "claude_desktop_config.json"
    else:
        ch.error(f"Unknown target: {target}. Use 'vscode' or 'claude'")
        raise typer.Exit(1)
    
    config_json = json.dumps(config, indent=2)
    
    if output:
        # Write to file
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_json)
        ch.success(f"✓ Config written to {filename}")
    else:
        ch.header(f"MCP Configuration for {target.title()}")
        ch.dim("")
        ch.console.print_json(config_json)
        ch.dim("")
        ch.info(f"Add this to your {target} configuration to enable NAVIG integration.")


# ============================================================================
# App INITIALIZATION
# ============================================================================

# ============================================================================
# App INITIALIZATION
# ============================================================================

@app.command("init-local")
def init_local_command(
    ctx: typer.Context,
    copy_global: bool = typer.Option(
        False,
        "--copy-global",
        help="Copy (not move) global configs from ~/.navig/ to app .navig/"
    ),
):
    """
    Initialize app-specific .navig/ directory (renamed from 'init').

    Creates a hierarchical configuration structure in the current directory,
    allowing app-specific host and configuration management that takes
    precedence over global ~/.navig/ configs.

    Similar to 'git init', this makes the current directory a NAVIG app root.

    The --copy-global option COPIES (not moves) configurations from ~/.navig/
    to the app .navig/, leaving the originals intact. This allows the same
    host configs to be used across multiple apps.
    """
    from navig.commands.init import init_app
    ctx.obj['copy_global'] = copy_global
    init_app(ctx.obj)


# ============================================================================
# CONFIGURATION MANAGEMENT COMMANDS
# ============================================================================

config_app = typer.Typer(
    help="Manage NAVIG configuration and settings",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(config_app, name="config")


@config_app.callback()
def config_callback(ctx: typer.Context):
    """Configuration management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("config", ctx)
        raise typer.Exit()


@config_app.command("migrate")
def config_migrate(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be migrated without making changes"),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip creating backups before migration"),
):
    """Migrate legacy configurations to new format."""
    from navig.commands.config import migrate
    migrate(dry_run=dry_run, no_backup=no_backup)


@config_app.command("test")
def config_test(
    ctx: typer.Context,
    host: Optional[str] = typer.Argument(None, help="Host name to validate (validates all if not specified)"),
    scope: str = typer.Option(
        None,
        "--scope",
        help="What to validate: project (.navig), global (~/.navig), or both",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Treat warnings as errors",
    ),
    json: bool = typer.Option(False, "--json", help="Output validation results as JSON"),
):
    """Alias for: navig config validate."""
    from navig.commands.config import validate

    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    if scope:
        opts["scope"] = scope
    if strict:
        opts["strict"] = True
    validate(host=host, options=opts)


@config_app.command("validate")
def config_validate(
    ctx: typer.Context,
    host: Optional[str] = typer.Argument(None, help="Host name to validate (validates all if not specified)"),
    scope: str = typer.Option(
        None,
        "--scope",
        help="What to validate: project (.navig), global (~/.navig), or both",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Treat warnings as errors",
    ),
    json: bool = typer.Option(False, "--json", help="Output validation results as JSON"),
):
    from navig.commands.config import validate
    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    if scope:
        opts["scope"] = scope
    if strict:
        opts["strict"] = True
    validate(host=host, options=opts)


schema_app = typer.Typer(
    help="JSON schema tools (VS Code integration)",
    invoke_without_command=True,
    no_args_is_help=False,
)
config_app.add_typer(schema_app, name="schema")


@schema_app.callback()
def schema_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        show_subcommand_help("config schema", ctx)
        raise typer.Exit()


@schema_app.command("install")
def config_schema_install(
    ctx: typer.Context,
    scope: str = typer.Option(
        "global",
        "--scope",
        help="Where to install schemas: global (~/.navig) or project (.navig)",
    ),
    write_vscode_settings: bool = typer.Option(
        False,
        "--write-vscode-settings",
        help="Write .vscode/settings.json yaml.schemas mappings in the current project",
    ),
    json: bool = typer.Option(False, "--json", help="Output installation result as JSON"),
):
    """Install NAVIG YAML JSON Schemas for editor validation/autocomplete."""
    from navig.commands.config import install_schemas

    opts = dict(ctx.obj or {})
    if json:
        opts["json"] = True
    install_schemas(scope=scope, write_vscode_settings=write_vscode_settings, options=opts)


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Host name or host:app to display"),
):
    """Display host or app configuration."""
    from navig.commands.config import show
    show(target=target)


@config_app.command("settings")
def config_settings(ctx: typer.Context):
    """Display current NAVIG settings including execution mode and confirmation level."""
    from navig.commands.config import show_settings
    show_settings()


@config_app.command("set-mode")
def config_set_mode(
    ctx: typer.Context,
    mode: str = typer.Argument(..., help="Execution mode: 'interactive' or 'auto'"),
):
    """
    Set the default execution mode.
    
    Modes:
        interactive - Prompts for confirmation based on confirmation level (default)
        auto - Bypasses all confirmation prompts
    """
    from navig.commands.config import set_mode
    set_mode(mode)


@config_app.command("set-confirmation-level")
def config_set_confirmation_level(
    ctx: typer.Context,
    level: str = typer.Argument(..., help="Confirmation level: 'critical', 'standard', or 'verbose'"),
):
    """
    Set the confirmation level for interactive mode.
    
    Levels:
        critical - Only confirm destructive operations (DROP, DELETE, rm)
        standard - Confirm state-changing operations (default)
        verbose - Confirm all operations including reads
    """
    from navig.commands.config import set_confirmation_level
    set_confirmation_level(level)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Configuration key (e.g., 'log_level', 'execution.mode')"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a global configuration value."""
    from navig.commands.config import set_config
    set_config(key, value)


@config_app.command("get")
def config_get(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Configuration key to retrieve"),
):
    """Get a configuration value."""
    from navig.commands.config import get_config
    get_config(key)


@config_app.command("edit")
def config_edit(
    ctx: typer.Context,
    target: Optional[str] = typer.Argument(None, help="Host name or host:app to edit"),
):
    """Open configuration in default editor."""
    from navig.commands.config import edit_config
    edit_config(target=target)


# ============================================================================
# CONFIGURATION BACKUP & EXPORT COMMANDS
# ============================================================================

backup_app = typer.Typer(
    help="Backup and export NAVIG configuration",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(backup_app, name="backup")


@backup_app.callback()
def backup_callback(ctx: typer.Context):
    """Backup management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("backup", ctx)
        raise typer.Exit()


@backup_app.command("export")
def backup_export(
    ctx: typer.Context,
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path (auto-generated if not provided)"),
    format: str = typer.Option("archive", "--format", "-f", help="Output format: 'archive' (tar.gz) or 'json'"),
    include_secrets: bool = typer.Option(False, "--include-secrets", help="Include unredacted secrets (passwords, API keys)"),
    encrypt: bool = typer.Option(False, "--encrypt", "-e", help="Encrypt the output with a password"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Encryption password (prompted if not provided)"),
):
    """
    Export NAVIG configuration to a backup file.
    
    Creates a portable backup of all hosts, apps, and settings.
    By default, sensitive data (passwords, API keys) is redacted.
    
    Examples:
        navig backup export
        navig backup export --format json --output ~/my-backup.json
        navig backup export --include-secrets --encrypt
    """
    from navig.commands.navig_backup import export_config
    export_config({
        'output': output,
        'format': format,
        'include_secrets': include_secrets,
        'encrypt': encrypt,
        'password': password,
        'yes': ctx.obj.get('yes', False),
        'confirm': ctx.obj.get('confirm', False),
        'json': ctx.obj.get('json', False),
    })


@backup_app.command("import")
def backup_import(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to import"),
    merge: bool = typer.Option(True, "--merge/--replace", help="Merge with existing config (default) or replace"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Decryption password (prompted if needed)"),
):
    """
    Import NAVIG configuration from a backup file.
    
    Restores hosts, apps, and settings from a previous export.
    
    Examples:
        navig backup import navig-config-20241206.tar.gz
        navig backup import backup.json --replace
        navig backup import encrypted-backup.tar.gz.enc --password mypassword
    """
    from navig.commands.navig_backup import import_config
    import_config({
        'file': file,
        'merge': merge,
        'password': password,
        'yes': ctx.obj.get('yes', False),
        'confirm': ctx.obj.get('confirm', False),
        'json': ctx.obj.get('json', False),
    })


@backup_app.command("show")
def backup_show(
    ctx: typer.Context,
    file: Optional[Path] = typer.Argument(None, help="Backup file to inspect"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Decryption password if encrypted"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text for scripting"),
):
    """Show backup details or list all backups (canonical command)."""
    if file:
        from navig.commands.navig_backup import inspect_export
        inspect_export({
            'file': file,
            'password': password,
            'json': ctx.obj.get('json', False),
        })
    else:
        from navig.commands.navig_backup import list_exports
        list_exports({
            'json': ctx.obj.get('json', False),
            'plain': plain,
        })


@backup_app.command("run")
def backup_run(
    ctx: typer.Context,
    config: bool = typer.Option(False, "--config", help="Backup system configuration files"),
    db_all: bool = typer.Option(False, "--db-all", help="Backup all databases"),
    hestia: bool = typer.Option(False, "--hestia", help="Backup HestiaCP configuration"),
    web: bool = typer.Option(False, "--web", help="Backup web server configuration"),
    all: bool = typer.Option(False, "--all", help="Run comprehensive backup"),
    restore: Optional[str] = typer.Option(None, "--restore", help="Restore from a comprehensive backup by name"),
    component: Optional[str] = typer.Option(None, "--component", help="Specific component to restore"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option(
        "gzip",
        "--compress",
        "-c",
        help="Compression for database backups: none|gzip|zstd",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Run server backup/restore operations (system config, DBs, Hestia, web)."""
    selected_count = sum(
        1
        for flag in [config, db_all, hestia, web, all, restore is not None]
        if flag
    )

    if selected_count != 1:
        ch.error(
            "Choose exactly one backup operation.",
            "Use one of: --config, --db-all, --hestia, --web, --all, or --restore <name>.",
        )
        raise typer.Exit(1)

    from navig.commands import backup as backup_cmds

    if restore is not None:
        ctx.obj['force'] = force
        backup_cmds.restore_backup_cmd(restore, component, ctx.obj)
        return

    if config:
        backup_cmds.backup_system_config(name, ctx.obj)
    elif db_all:
        backup_cmds.backup_all_databases(name, compress, ctx.obj)
    elif hestia:
        backup_cmds.backup_hestia(name, ctx.obj)
    elif web:
        backup_cmds.backup_web_config(name, ctx.obj)
    else:
        backup_cmds.backup_all(name, compress, ctx.obj)


@backup_app.command("restore")
def backup_restore(
    ctx: typer.Context,
    backup_name: str = typer.Argument(..., help="Backup name to restore from"),
    component: Optional[str] = typer.Option(None, "--component", "-c", help="Specific component to restore"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Restore from a comprehensive backup by name."""
    from navig.commands.backup import restore_backup_cmd

    ctx.obj['force'] = force
    restore_backup_cmd(backup_name, component, ctx.obj)


@backup_app.command("list", hidden=True)
def backup_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text (one backup per line) for scripting"),
):
    """[DEPRECATED: Use 'navig backup show'] List available backups."""
    deprecation_warning("navig backup list", "navig backup show")
    from navig.commands.navig_backup import list_exports
    list_exports({
        'json': ctx.obj.get('json', False),
        'plain': plain,
    })


@backup_app.command("inspect", hidden=True)
def backup_inspect(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to inspect"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Decryption password if encrypted"),
):
    """[DEPRECATED: Use 'navig backup show <file>'] Inspect backup contents."""
    deprecation_warning("navig backup inspect", "navig backup show <file>")
    from navig.commands.navig_backup import inspect_export
    inspect_export({
        'file': file,
        'password': password,
        'json': ctx.obj.get('json', False),
    })


@backup_app.command("remove")
def backup_remove(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to delete"),
):
    """Remove/delete a backup file (canonical command)."""
    from navig.commands.navig_backup import delete_export
    delete_export({
        'file': file,
        'yes': ctx.obj.get('yes', False),
        'confirm': ctx.obj.get('confirm', False),
        'json': ctx.obj.get('json', False),
    })


@backup_app.command("delete", hidden=True)
def backup_delete(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to delete"),
):
    """[DEPRECATED: Use 'navig backup remove'] Delete backup file."""
    deprecation_warning("navig backup delete", "navig backup remove")
    from navig.commands.navig_backup import delete_export
    delete_export({
        'file': file,
        'yes': ctx.obj.get('yes', False),
        'confirm': ctx.obj.get('confirm', False),
        'json': ctx.obj.get('json', False),
    })


# ============================================================================
# INTERACTIVE MENU
# ============================================================================

# ============================================================================
# WORKFLOW COMMANDS
# ============================================================================

# ============================================================
# PILLAR 4: AUTOMATION - flow (primary), workflow/task (deprecated aliases)
# ============================================================
flow_app = typer.Typer(
    help="Manage and execute reusable command flows (workflows)",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(flow_app, name="flow")


@flow_app.callback()
def flow_callback(ctx: typer.Context):
    """Flow management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("flow", ctx)
        raise typer.Exit()


@flow_app.command("list")
def flow_list():
    """List all available flows."""
    from navig.commands.workflow import list_workflows
    list_workflows()


@flow_app.command("show")
def flow_show(
    name: str = typer.Argument(..., help="Flow name")
):
    """Display flow definition and steps."""
    from navig.commands.workflow import show_workflow
    show_workflow(name)


@flow_app.command("run")
def flow_run(
    name: str = typer.Argument(..., help="Flow name"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip all confirmation prompts"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    var: Optional[List[str]] = typer.Option(None, "--var", "-V", help="Variable override (name=value)")
):
    """Execute a flow."""
    from navig.commands.workflow import run_workflow
    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@flow_app.command("test")
def flow_test(
    name: str = typer.Argument(..., help="Flow name")
):
    """Test/validate flow syntax and structure."""
    from navig.commands.workflow import validate_workflow
    validate_workflow(name)


@flow_app.command("add")
def flow_add(
    name: str = typer.Argument(..., help="New flow name"),
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create in global directory")
):
    """Create a new flow."""
    from navig.commands.workflow import create_workflow
    create_workflow(name, global_scope=global_scope)


# ============================================================================
# SKILLS COMMANDS
# ============================================================================

skills_app = typer.Typer(
    help="Manage AI skill definitions",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(skills_app, name="skills")
app.add_typer(skills_app, name="skill", hidden=True)


@skills_app.callback()
def skills_callback(ctx: typer.Context):
    """Skills management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("skills", ctx)
        raise typer.Exit()


@skills_app.command("list")
def skills_list(
    ctx: typer.Context,
    skills_dir: Optional[Path] = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List available AI skills."""
    from navig.commands.skills import list_skills_cmd
    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    list_skills_cmd(ctx.obj)


@skills_app.command("tree")
def skills_tree(
    ctx: typer.Context,
    skills_dir: Optional[Path] = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show skills grouped by category."""
    from navig.commands.skills import tree_skills_cmd
    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    tree_skills_cmd(ctx.obj)


@skills_app.command("show")
def skills_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Skill name (e.g., 'docker-manage', 'git-basics', 'official/docker-ops')"),
    skills_dir: Optional[Path] = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed skill information (commands, examples, metadata)."""
    from navig.commands.skills import show_skill_cmd
    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    show_skill_cmd(name, ctx.obj)


@skills_app.command("run")
def skills_run(
    ctx: typer.Context,
    spec: str = typer.Argument(
        ...,
        help="Skill spec: <skill-name>:<command> or <skill-name> (runs entrypoint)",
    ),
    args: Optional[List[str]] = typer.Argument(None, help="Arguments passed to the skill command"),
    skills_dir: Optional[Path] = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm risky commands"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Run a skill command.

    Spec format:
      <skill>:<command>  — run a named navig-command from the skill
      <skill>            — run the skill's entrypoint (main.py / index.js)

    Examples:
        navig skills run docker-manage:ps
        navig skills run git-basics:git-status
        navig skills run file-operations:list-files /var/log
        navig skills run my-custom-skill   # runs entrypoint
    """
    from navig.commands.skills import run_skill_cmd
    if json_output:
        ctx.obj["json"] = True
    if yes:
        ctx.obj["yes"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    exit_code = run_skill_cmd(spec, args or [], ctx.obj)
    if exit_code != 0:
        raise typer.Exit(exit_code)


# ============================================================================
# SCAFFOLD COMMANDS (Lazy-loaded)
# ============================================================================
scaffold_app = typer.Typer(
    help="Scaffold project structures from templates",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(scaffold_app, name="scaffold")


@scaffold_app.callback(invoke_without_command=True)
def scaffold_callback(ctx: typer.Context):
    """Scaffold management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("scaffold", ctx)
        raise typer.Exit()


@scaffold_app.command("apply")
def scaffold_apply(
    template_path: Path = typer.Argument(..., help="Path to YAML template file"),
    target_dir: str = typer.Option(".", "--target-dir", "-d", help="Target directory (local or remote)"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Remote host to deploy to (defaults to local)"),
    set_var: Optional[List[str]] = typer.Option(None, "--set", help="Set variable like key=value"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without creating files"),
):
    """Generate files/directories from a template."""
    from navig.commands.scaffold import apply
    apply(template_path, target_dir, host, set_var, dry_run)


# Nested: flow template (consolidates template + addon)
flow_template_app = typer.Typer(
    help="Manage server templates and extensions",
    invoke_without_command=True,
    no_args_is_help=False,
)
flow_app.add_typer(flow_template_app, name="template")


@flow_template_app.callback()
def flow_template_callback(ctx: typer.Context):
    """Template management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_template_menu
        launch_template_menu()
        raise typer.Exit()


@flow_template_app.command("list")
def flow_template_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text for scripting"),
):
    """List all available templates."""
    from navig.commands.template import list_templates_cmd
    ctx.obj['plain'] = plain
    list_templates_cmd(ctx.obj)


@flow_template_app.command("show")
def flow_template_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name"),
):
    """Show template details."""
    from navig.commands.template import show_template_cmd
    show_template_cmd(name, ctx.obj)


@flow_template_app.command("add")
def flow_template_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable/add a template."""
    from navig.commands.template import enable_template_cmd
    enable_template_cmd(name, ctx.obj)


@flow_template_app.command("remove")
def flow_template_remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable/remove a template."""
    from navig.commands.template import disable_template_cmd
    disable_template_cmd(name, ctx.obj)


@flow_template_app.command("run")
def flow_template_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to deploy"),
    command: Optional[str] = typer.Argument(None, help="Template command to run"),
    args: Optional[List[str]] = typer.Argument(None, help="Arguments for the template command"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without changes"),
):
    """Deploy/run a template."""
    from navig.commands.template import deploy_template_cmd
    deploy_template_cmd(
        name,
        command_name=command,
        command_args=args or [],
        dry_run=dry_run,
        ctx_obj=ctx.obj,
    )


# DEPRECATED: workflow_app and task alias - use flow instead
workflow_app = typer.Typer(
    help="[DEPRECATED: Use 'navig flow'] Manage workflows",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(workflow_app, name="workflow", hidden=True)  # Deprecated
app.add_typer(workflow_app, name="task", hidden=True)  # Deprecated alias


@workflow_app.callback()
def workflow_callback(ctx: typer.Context):
    """Workflow management - DEPRECATED, use 'navig flow'."""
    deprecation_warning("navig workflow/task", "navig flow")
    if ctx.invoked_subcommand is None:
        from navig.commands.workflow import list_workflows
        list_workflows()


@workflow_app.command("list")
def workflow_list():
    """List all available workflows."""
    from navig.commands.workflow import list_workflows
    list_workflows()


@workflow_app.command("show")
def workflow_show(
    name: str = typer.Argument(..., help="Workflow name")
):
    """Display workflow definition and steps."""
    from navig.commands.workflow import show_workflow
    show_workflow(name)


@workflow_app.command("run")
def workflow_run(
    name: str = typer.Argument(..., help="Workflow name"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip all confirmation prompts"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    var: Optional[List[str]] = typer.Option(None, "--var", "-V", help="Variable override (name=value)")
):
    """Execute a workflow."""
    from navig.commands.workflow import run_workflow
    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@workflow_app.command("test")
def workflow_test(
    name: str = typer.Argument(..., help="Workflow name")
):
    """Test/validate workflow syntax and structure (canonical command)."""
    from navig.commands.workflow import validate_workflow
    validate_workflow(name)


@workflow_app.command("validate", hidden=True)
def workflow_validate(
    name: str = typer.Argument(..., help="Workflow name")
):
    """[DEPRECATED: Use 'navig workflow test'] Validate workflow."""
    deprecation_warning("navig workflow validate", "navig workflow test")
    from navig.commands.workflow import validate_workflow
    validate_workflow(name)


@workflow_app.command("add")
def workflow_add(
    name: str = typer.Argument(..., help="New workflow name"),
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create in global directory")
):
    """Add/create a new workflow (canonical command)."""
    from navig.commands.workflow import create_workflow
    create_workflow(name, global_scope=global_scope)


@workflow_app.command("create", hidden=True)
def workflow_create(
    name: str = typer.Argument(..., help="New workflow name"),
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create in global directory")
):
    """[DEPRECATED: Use 'navig workflow add'] Create new workflow."""
    deprecation_warning("navig workflow create", "navig workflow add")
    from navig.commands.workflow import create_workflow
    create_workflow(name, global_scope=global_scope)


@workflow_app.command("remove")
def workflow_remove(
    name: str = typer.Argument(..., help="Workflow name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation")
):
    """Remove/delete a workflow (canonical command)."""
    from navig.commands.workflow import delete_workflow
    delete_workflow(name, force=force)


@workflow_app.command("delete", hidden=True)
def workflow_delete(
    name: str = typer.Argument(..., help="Workflow name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation")
):
    """[DEPRECATED: Use 'navig workflow remove'] Delete workflow."""
    deprecation_warning("navig workflow delete", "navig workflow remove")
    from navig.commands.workflow import delete_workflow
    delete_workflow(name, force=force)


@workflow_app.command("edit")
def workflow_edit(
    name: str = typer.Argument(..., help="Workflow name")
):
    """Open workflow in default editor."""
    from navig.commands.workflow import edit_workflow
    edit_workflow(name)


# ============================================================================
# WIKI MANAGEMENT
# ============================================================================

# Lazy-load wiki commands so `navig --help` stays fast.
from typer.core import TyperGroup


class _LazyWikiGroup(TyperGroup):
    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        # Import only when the user actually invokes `navig wiki ...`.
        from typer.main import get_command

        from navig.commands import wiki as wiki_module

        wiki_click_cmd = get_command(wiki_module.wiki_app)
        # Copy subcommands from the real wiki group into this group.
        if hasattr(wiki_click_cmd, "commands"):
            for name, cmd in wiki_click_cmd.commands.items():
                # Avoid overwriting anything already registered.
                if name not in self.commands:
                    self.add_command(cmd, name)

        self._loaded = True

    def get_command(self, ctx, cmd_name):
        self._ensure_loaded()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx):
        self._ensure_loaded()
        return super().list_commands(ctx)


wiki_app = typer.Typer(
    help="Wiki & knowledge base management",
    invoke_without_command=True,
    no_args_is_help=False,
    cls=_LazyWikiGroup,
)


@wiki_app.callback()
def wiki_callback(ctx: typer.Context):
    """Wiki commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("wiki", ctx)
        raise typer.Exit()


app.add_typer(wiki_app, name="wiki")


# ============================================================================
# INBOX ROUTER - CLASSIFY AND ROUTE INBOX FILES
# ============================================================================

from navig.commands.inbox import inbox_app

app.add_typer(inbox_app, name="inbox")


# ============================================================================
# GATEWAY - AUTONOMOUS AGENT CONTROL PLANE
# ============================================================================

gateway_app = typer.Typer(
    help="Autonomous agent gateway server (24/7 control plane)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@gateway_app.callback()
def gateway_callback(ctx: typer.Context):
    """Gateway commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("gateway", ctx)
        raise typer.Exit()


@gateway_app.command("start")
def gateway_start(
    port: int = typer.Option(8789, "--port", "-p", help="Port to run gateway on"),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to"),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
):
    """
    Start the autonomous agent gateway server.
    
    The gateway provides:
    - HTTP/WebSocket API for agent communication
    - Session persistence across restarts
    - Heartbeat-based health monitoring
    - Cron job scheduling
    - Multi-channel message routing
    
    Examples:
        navig gateway start
        navig gateway start --port 9000
        navig gateway start --background
    """
    import asyncio
    
    ch.info(f"Starting NAVIG Gateway on {host}:{port}...")
    
    try:
        from navig.gateway import NavigGateway, GatewayConfig
        
        # Build config dict for GatewayConfig
        raw_config = {
            'gateway': {
                'enabled': True,
                'port': port,
                'host': host,
            }
        }
        
        gateway_config = GatewayConfig(raw_config)

        if background:
            import subprocess

            cmd = [
                sys.executable,
                "-m",
                "navig",
                "gateway",
                "start",
                "--port",
                str(port),
                "--host",
                host,
            ]

            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
                "close_fds": True,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )

            proc = subprocess.Popen(cmd, **kwargs)
            ch.success(f"Gateway started in background (pid={proc.pid})")
            ch.info("Check status with: navig gateway status")
            return

        gateway = NavigGateway(config=gateway_config)
        asyncio.run(gateway.start())
        
    except KeyboardInterrupt:
        ch.info("Gateway stopped by user")
    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("Install with: pip install aiohttp")
    except Exception as e:
        ch.error(f"Gateway error: {e}")


@gateway_app.command("stop")
def gateway_stop():
    """
    Stop the running gateway server.
    
    Sends a shutdown signal to the running gateway via its API.
    If the gateway is running in the foreground, use Ctrl+C instead.
    
    Examples:
        navig gateway stop
    """
    import requests
    
    try:
        # First check if gateway is running
        try:
            health_response = requests.get("http://localhost:8789/health", timeout=2)
            if health_response.status_code != 200:
                ch.warning("Gateway does not appear to be running")
                return
        except Exception:
            ch.warning("Gateway is not running")
            return
        
        # Try to stop via API
        try:
            response = requests.post("http://localhost:8789/shutdown", timeout=5)
            if response.status_code == 200:
                ch.success("Gateway shutdown signal sent")
            else:
                ch.warning(f"Shutdown request returned status {response.status_code}")
                ch.info("If running in foreground, use Ctrl+C to stop")
        except requests.exceptions.ConnectionError:
            # Connection closed - gateway probably stopped
            ch.success("Gateway stopped")
        except Exception as e:
            ch.warning(f"Could not send shutdown signal: {e}")
            ch.info("If running in foreground, use Ctrl+C to stop")
            ch.info("Or kill the process manually")
    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")


@gateway_app.command("status")
def gateway_status():
    """Show gateway status."""
    import requests
    
    try:
        # Get detailed status from /status endpoint
        response = requests.get("http://localhost:8789/status", timeout=2)
        if response.status_code == 200:
            data = response.json()
            ch.success("Gateway is running")
            ch.info(f"  Status: {data.get('status', 'unknown')}")
            
            # Format uptime nicely
            uptime_sec = data.get("uptime_seconds")
            if uptime_sec:
                hours, remainder = divmod(int(uptime_sec), 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    ch.info(f"  Uptime: {hours}h {minutes}m {seconds}s")
                elif minutes > 0:
                    ch.info(f"  Uptime: {minutes}m {seconds}s")
                else:
                    ch.info(f"  Uptime: {seconds}s")
            
            # Show session count
            sessions = data.get("sessions", {})
            if sessions:
                ch.info(f"  Active sessions: {sessions.get('active', 0)}")
            
            # Show cron/heartbeat summary
            cron = data.get("cron", {})
            if cron:
                ch.info(f"  Cron jobs: {cron.get('jobs', 0)} ({cron.get('enabled_jobs', 0)} enabled)")
            
            hb = data.get("heartbeat", {})
            if hb.get("running"):
                ch.info("  Heartbeat: active")
        else:
            ch.warning(f"Gateway returned status {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error checking gateway: {e}")


@gateway_app.command("session")
def gateway_session(
    action: str = typer.Argument(..., help="Action: list, show, clear"),
    session_key: str = typer.Argument(None, help="Session key (for show/clear)"),
):
    """
    Manage gateway sessions.
    
    Examples:
        navig gateway session list
        navig gateway session show agent:default:telegram:123
        navig gateway session clear agent:default:telegram:123
    """
    import requests
    
    try:
        if action == "list":
            response = requests.get("http://localhost:8789/sessions", timeout=5)
            if response.status_code == 200:
                sessions = response.json().get("sessions", [])
                if sessions:
                    ch.info(f"Active sessions ({len(sessions)}):")
                    for s in sessions:
                        ch.info(f"  • {s.get('key', 'unknown')}")
                else:
                    ch.info("No active sessions")
            else:
                ch.error(f"Failed to list sessions: {response.status_code}")
                
        elif action == "show" and session_key:
            response = requests.get(
                f"http://localhost:8789/sessions/{session_key}", 
                timeout=5
            )
            if response.status_code == 200:
                session = response.json()
                ch.info(f"Session: {session_key}")
                ch.info(f"  Messages: {session.get('message_count', 0)}")
                ch.info(f"  Created: {session.get('created_at', 'unknown')}")
                ch.info(f"  Updated: {session.get('updated_at', 'unknown')}")
            else:
                ch.error(f"Session not found: {session_key}")
                
        elif action == "clear" and session_key:
            response = requests.delete(
                f"http://localhost:8789/sessions/{session_key}",
                timeout=5
            )
            if response.status_code == 200:
                ch.success(f"Cleared session: {session_key}")
            else:
                ch.error(f"Failed to clear session: {response.status_code}")
                
        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Actions: list, show <key>, clear <key>")
            
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(gateway_app, name="gateway")


# ============================================================================
# BOT - TELEGRAM BOT LAUNCHER
# ============================================================================

bot_app = typer.Typer(
    help="Telegram bot and multi-channel agent launcher",
    invoke_without_command=True,
    no_args_is_help=False,
)


@bot_app.callback()
def bot_callback(ctx: typer.Context):
    """Bot commands - run without subcommand to start bot."""
    if ctx.invoked_subcommand is None:
        # Default action: start bot in direct mode
        ctx.invoke(bot_start)


@bot_app.command("start")
def bot_start(
    gateway: bool = typer.Option(False, "--gateway", "-g", help="Start with gateway (session persistence)"),
    port: int = typer.Option(8789, "--port", "-p", help="Gateway port (when using --gateway)"),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
):
    """
    Start the NAVIG Telegram bot.
    
    By default runs in direct mode (standalone).
    Use --gateway to start both gateway and bot together.
    
    Examples:
        navig bot                    # Start bot (direct mode)
        navig bot --gateway          # Start gateway + bot together
        navig bot -g -p 9000         # Gateway on custom port
    """
    import subprocess
    import os
    
    # Check for telegram token
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        ch.error("TELEGRAM_BOT_TOKEN not set!")
        ch.info("  Get token from @BotFather on Telegram")
        ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
        raise typer.Exit(1)
    
    if gateway:
        ch.info("Starting NAVIG with Gateway + Telegram Bot...")
        ch.info(f"  Gateway: http://localhost:{port}")
        ch.info("  Bot: Telegram")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--port", str(port)]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)
    else:
        ch.info("Starting NAVIG Telegram Bot (direct mode)...")
        ch.warning("⚠️  Conversations reset on bot restart")
        ch.info("   Use 'navig bot --gateway' for session persistence")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)


@bot_app.command("status")
def bot_status():
    """Check if bot is running."""
    import subprocess
    patterns = r'navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start'
    
    # Check for running python processes with navig_bot
    try:
        if sys.platform == 'win32':
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\") "
                f"| Where-Object {{ $_.CommandLine -match '{patterns}' }} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                capture_output=True, text=True
            )
            pids = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
            if pids:
                ch.success("Bot appears to be running")
                ch.info(f"  PIDs: {', '.join(pids)}")
            else:
                ch.warning("Bot does not appear to be running")
        else:
            result = subprocess.run(
                ['pgrep', '-f', patterns],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                ch.success("Bot is running")
                ch.info(f"  PIDs: {result.stdout.strip()}")
            else:
                ch.warning("Bot is not running")
    except Exception as e:
        ch.error(f"Could not check status: {e}")


@bot_app.command("stop")
def bot_stop():
    """Stop all running NAVIG bot/gateway processes."""
    import subprocess
    patterns = r'navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start'
    
    try:
        if sys.platform == 'win32':
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\") "
                f"| Where-Object {{ $_.CommandLine -match '{patterns}' }} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            find_result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                capture_output=True, text=True
            )
            pids = [line.strip() for line in find_result.stdout.splitlines() if line.strip().isdigit()]
            if not pids:
                ch.warning("No running processes found")
                return
            for pid in pids:
                subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], capture_output=True, text=True)
            ch.success(f"Stopped NAVIG bot/gateway processes: {', '.join(pids)}")
        else:
            result = subprocess.run(
                ['pkill', '-f', patterns],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                ch.success("Stopped NAVIG bot/gateway")
            else:
                ch.warning("No running processes found")
    except Exception as e:
        ch.error(f"Error stopping: {e}")


app.add_typer(bot_app, name="bot")


# ============================================================================
# START - QUICK LAUNCHER (ALIAS)
# ============================================================================

@app.command("start")
def quick_start(
    bot: bool = typer.Option(True, "--bot/--no-bot", "-b/-B", help="Start Telegram bot"),
    gateway: bool = typer.Option(True, "--gateway/--no-gateway", "-g/-G", help="Start gateway"),
    port: int = typer.Option(8789, "--port", "-p", help="Gateway port"),
    background: bool = typer.Option(True, "--background/--foreground", "-d/-f", help="Run in background"),
):
    """
    Quick launcher - start NAVIG services with sensible defaults.
    
    By default starts both gateway and bot in background.
    
    Examples:
        navig start                  # Start gateway + bot (background)
        navig start --foreground     # Start in foreground (see logs)
        navig start --no-gateway     # Bot only (standalone)
        navig start --no-bot         # Gateway only
    """
    import os
    import subprocess
    
    if bot:
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not telegram_token:
            ch.error("TELEGRAM_BOT_TOKEN not set!")
            ch.info("  Get token from @BotFather on Telegram")
            ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
            raise typer.Exit(1)
    
    if gateway and bot:
        ch.info("Starting NAVIG (Gateway + Telegram Bot)...")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--port", str(port)]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ch.success("Started in background")
            ch.info(f"  Gateway: http://localhost:{port}")
            ch.info("  Status: navig bot status")
            ch.info("  Stop: navig bot stop")
        else:
            os.execv(sys.executable, cmd)
    
    elif bot:
        ch.info("Starting NAVIG Telegram Bot (standalone)...")
        ch.warning("⚠️  Conversations reset on restart")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)
    
    elif gateway:
        ch.info(f"Starting NAVIG Gateway on port {port}...")
        from navig.commands.gateway import gateway_start
        gateway_start(port=port, host="0.0.0.0", background=background)


# ============================================================================
# HEARTBEAT - PERIODIC HEALTH CHECKS
# ============================================================================

heartbeat_app = typer.Typer(
    help="Periodic health check system",
    invoke_without_command=True,
    no_args_is_help=False,
)


@heartbeat_app.callback()
def heartbeat_callback(ctx: typer.Context):
    """Heartbeat commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("heartbeat", ctx)
        raise typer.Exit()


@heartbeat_app.command("status")
def heartbeat_status():
    """Show heartbeat status."""
    import requests
    from datetime import datetime
    
    try:
        response = requests.get("http://localhost:8789/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            hb = data.get("heartbeat", {})
            config = data.get("config", {})
            
            if hb.get("running"):
                ch.success("Heartbeat is running")
                
                # Get interval from config
                interval = config.get("heartbeat_interval", "30m")
                ch.info(f"  Interval: {interval}")
                
                # Parse and display next run time
                next_run = hb.get("next_run")
                if next_run:
                    try:
                        next_dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                        now = datetime.now(next_dt.tzinfo) if next_dt.tzinfo else datetime.now()
                        diff = next_dt - now
                        minutes = int(diff.total_seconds() / 60)
                        if minutes > 0:
                            ch.info(f"  Next check: in {minutes} minutes")
                        else:
                            ch.info("  Next check: imminent")
                    except:
                        ch.info(f"  Next check: {next_run}")
                else:
                    ch.info("  Next check: unknown")
                
                # Display last run
                last_run = hb.get("last_run")
                if last_run:
                    ch.info(f"  Last run: {last_run}")
                else:
                    ch.info("  Last run: never")
            else:
                ch.warning("Heartbeat is not running")
                ch.info("Start gateway to enable heartbeat: navig gateway start")
        else:
            ch.error(f"Failed to get status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("trigger")
def heartbeat_trigger():
    """Trigger an immediate heartbeat check."""
    import requests
    
    ch.info("Triggering heartbeat check...")
    
    try:
        response = requests.post(
            "http://localhost:8789/heartbeat/trigger",
            timeout=300  # Heartbeat can take a while
        )
        if response.status_code == 200:
            result = response.json()
            if result.get("suppressed"):
                ch.success("HEARTBEAT_OK - All systems healthy")
            elif result.get("issues"):
                ch.warning(f"Issues found: {len(result['issues'])}")
                for issue in result["issues"]:
                    ch.warning(f"  • {issue}")
            else:
                ch.success("Heartbeat completed")
        else:
            ch.error(f"Heartbeat failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("history")
def heartbeat_history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of entries to show"),
):
    """Show heartbeat history."""
    import requests
    
    try:
        response = requests.get(
            f"http://localhost:8789/heartbeat/history?limit={limit}",
            timeout=5
        )
        if response.status_code == 200:
            history = response.json().get("history", [])
            if history:
                ch.info(f"Heartbeat history (last {len(history)}):")
                for entry in history:
                    status = "✅" if entry.get("success") else "❌"
                    suppressed = " (OK)" if entry.get("suppressed") else ""
                    ch.info(f"  {status} {entry.get('timestamp', '?')}{suppressed} - {entry.get('duration', 0):.1f}s")
            else:
                ch.info("No heartbeat history")
        else:
            ch.error(f"Failed to get history: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("configure")
def heartbeat_configure(
    interval: int = typer.Option(None, "--interval", "-i", help="Interval in minutes"),
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable/disable heartbeat"),
):
    """Configure heartbeat settings."""
    config_manager = _get_config_manager()
    
    if interval is not None or enable is not None:
        config = config_manager.global_config
        if "heartbeat" not in config:
            config["heartbeat"] = {}
        
        if interval is not None:
            config["heartbeat"]["interval"] = interval
            ch.success(f"Set heartbeat interval to {interval} minutes")
        
        if enable is not None:
            config["heartbeat"]["enabled"] = enable
            ch.success(f"Heartbeat {'enabled' if enable else 'disabled'}")
        
        config_manager.save_global()
    else:
        # Show current config
        config = config_manager.global_config
        hb = config.get("heartbeat", {})
        ch.info("Heartbeat configuration:")
        ch.info(f"  Enabled: {hb.get('enabled', True)}")
        ch.info(f"  Interval: {hb.get('interval', 30)} minutes")
        ch.info(f"  Timeout: {hb.get('timeout', 300)} seconds")


app.add_typer(heartbeat_app, name="heartbeat")


# ============================================================================
# CRON - PERSISTENT JOB SCHEDULING
# ============================================================================

cron_app = typer.Typer(
    help="Persistent job scheduling",
    invoke_without_command=True,
    no_args_is_help=False,
)


@cron_app.callback()
def cron_callback(ctx: typer.Context):
    """Cron commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("cron", ctx)
        raise typer.Exit()


@cron_app.command("list")
def cron_list():
    """List all scheduled jobs."""
    import requests
    
    try:
        response = requests.get("http://localhost:8789/cron/jobs", timeout=5)
        if response.status_code == 200:
            jobs = response.json().get("jobs", [])
            if jobs:
                ch.info(f"Scheduled jobs ({len(jobs)}):")
                for job in jobs:
                    status = "✅" if job.get("enabled") else "⏸️"
                    next_run = job.get("next_run", "N/A")
                    ch.info(f"  {status} [{job.get('id')}] {job.get('name')}")
                    ch.info(f"      Schedule: {job.get('schedule')}")
                    ch.info(f"      Next run: {next_run}")
            else:
                ch.info("No scheduled jobs")
                ch.info("Add one with: navig cron add \"job name\" \"every 30 minutes\" \"navig host test\"")
        else:
            ch.error(f"Failed to list jobs: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("add")
def cron_add(
    name: str = typer.Argument(..., help="Job name"),
    schedule: str = typer.Argument(..., help="Schedule (e.g., 'every 30 minutes', '0 * * * *')"),
    command: str = typer.Argument(..., help="Command to run"),
    disabled: bool = typer.Option(False, "--disabled", help="Create job in disabled state"),
):
    """
    Add a new scheduled job.
    
    Schedule formats:
    - Natural language: "every 30 minutes", "hourly", "daily"
    - Cron expression: "*/5 * * * *", "0 9 * * *"
    
    Examples:
        navig cron add "Disk check" "every 30 minutes" "navig host monitor disk"
        navig cron add "Daily backup" "0 2 * * *" "navig backup export"
        navig cron add "Health check" "hourly" "Check all hosts and report issues"
    """
    import requests
    
    try:
        response = requests.post(
            "http://localhost:8789/cron/jobs",
            json={
                "name": name,
                "schedule": schedule,
                "command": command,
                "enabled": not disabled,
            },
            timeout=5
        )
        if response.status_code == 200:
            job = response.json()
            ch.success(f"Created job: {job.get('id')}")
            ch.info(f"  Name: {name}")
            ch.info(f"  Schedule: {schedule}")
            ch.info(f"  Next run: {job.get('next_run', 'N/A')}")
        else:
            ch.error(f"Failed to create job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    import requests
    
    try:
        response = requests.delete(
            f"http://localhost:8789/cron/jobs/{job_id}",
            timeout=5
        )
        if response.status_code == 200:
            ch.success(f"Removed job: {job_id}")
        else:
            ch.error(f"Failed to remove job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
):
    """Run a job immediately."""
    import requests
    
    ch.info(f"Running job {job_id}...")
    
    try:
        response = requests.post(
            f"http://localhost:8789/cron/jobs/{job_id}/run",
            timeout=300
        )
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                ch.success("Job completed successfully")
                if result.get("output"):
                    ch.info(f"Output:\n{result['output'][:1000]}")
            else:
                ch.error(f"Job failed: {result.get('error', 'unknown')}")
        else:
            ch.error(f"Failed to run job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID to enable"),
):
    """Enable a disabled job."""
    import requests
    
    try:
        response = requests.post(
            f"http://localhost:8789/cron/jobs/{job_id}/enable",
            timeout=5
        )
        if response.status_code == 200:
            ch.success(f"Enabled job: {job_id}")
        else:
            ch.error(f"Failed to enable job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("disable")
def cron_disable(
    job_id: str = typer.Argument(..., help="Job ID to disable"),
):
    """Disable a job without removing it."""
    import requests
    
    try:
        response = requests.post(
            f"http://localhost:8789/cron/jobs/{job_id}/disable",
            timeout=5
        )
        if response.status_code == 200:
            ch.success(f"Disabled job: {job_id}")
        else:
            ch.error(f"Failed to disable job: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@cron_app.command("status")
def cron_status():
    """Show cron service status."""
    import requests
    
    try:
        response = requests.get("http://localhost:8789/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            cron = data.get("cron", {})
            
            # Cron is running if gateway is up and jobs exist
            total_jobs = cron.get("jobs", cron.get("total_jobs", 0))
            enabled_jobs = cron.get("enabled_jobs", 0)
            
            if data.get("status") == "running":
                ch.success("Cron service is running")
                ch.info(f"  Total jobs: {total_jobs}")
                ch.info(f"  Enabled jobs: {enabled_jobs}")
                if cron.get("next_job"):
                    ch.info(f"  Next job: {cron.get('next_job')} in {cron.get('next_run_in', '?')}")
            else:
                ch.warning("Cron service is not running")
                ch.info("Start gateway to enable cron: navig gateway start")
        else:
            ch.error(f"Failed to get status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start gateway to enable cron: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(cron_app, name="cron")


# ============================================================================
# APPROVAL SYSTEM (Human-in-the-loop for agent actions)
# ============================================================================

approve_app = typer.Typer(
    help="Human approval system for agent actions",
    invoke_without_command=True,
    no_args_is_help=False,
)


@approve_app.callback()
def approve_callback(ctx: typer.Context):
    """Approval management - run without subcommand to list pending."""
    if ctx.invoked_subcommand is None:
        approve_list()


@approve_app.command("list")
def approve_list():
    """List pending approval requests."""
    import requests
    
    try:
        response = requests.get("http://localhost:8789/approval/pending", timeout=5)
        if response.status_code == 200:
            data = response.json()
            pending = data.get("pending", [])
            
            if not pending:
                ch.info("No pending approval requests")
                return
            
            ch.info(f"Pending approval requests ({len(pending)}):")
            for req in pending:
                level_color = {
                    "confirm": "yellow",
                    "dangerous": "red",
                    "never": "bright_red",
                }.get(req.get("level", ""), "white")
                
                ch.console.print(
                    f"  [{req['id']}] {req['action']} ({req['level']}) - {req.get('description', '')}",
                    style=level_color
                )
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("yes")
def approve_yes(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Approve a pending request."""
    import requests
    
    try:
        response = requests.post(
            f"http://localhost:8789/approval/{request_id}/respond",
            json={"approved": True, "reason": reason},
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Request {request_id} approved")
        elif response.status_code == 404:
            ch.error(f"Request {request_id} not found")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("no")
def approve_no(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Deny a pending request."""
    import requests
    
    try:
        response = requests.post(
            f"http://localhost:8789/approval/{request_id}/respond",
            json={"approved": False, "reason": reason},
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Request {request_id} denied")
        elif response.status_code == 404:
            ch.error(f"Request {request_id} not found")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("policy")
def approve_policy():
    """Show approval policy (patterns and levels)."""
    try:
        from navig.approval import ApprovalPolicy
        policy = ApprovalPolicy.default()
        
        ch.info("Approval Policy Patterns:")
        ch.console.print("\n[bold green]SAFE (no approval needed):[/bold green]")
        for pattern in policy.patterns.get("safe", []):
            ch.console.print(f"  • {pattern}")
        
        ch.console.print("\n[bold yellow]CONFIRM (requires approval):[/bold yellow]")
        for pattern in policy.patterns.get("confirm", []):
            ch.console.print(f"  • {pattern}")
        
        ch.console.print("\n[bold red]DANGEROUS (always confirm):[/bold red]")
        for pattern in policy.patterns.get("dangerous", []):
            ch.console.print(f"  • {pattern}")
        
        ch.console.print("\n[bold bright_red]NEVER (always denied):[/bold bright_red]")
        for pattern in policy.patterns.get("never", []):
            ch.console.print(f"  • {pattern}")
    except ImportError:
        ch.error("Approval module not available")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(approve_app, name="approve")


# ============================================================================
# BROWSER AUTOMATION
# ============================================================================

browser_app = typer.Typer(
    help="Browser automation for web tasks",
    invoke_without_command=True,
    no_args_is_help=False,
)


@browser_app.callback()
def browser_callback(ctx: typer.Context):
    """Browser automation - run without subcommand to show status."""
    if ctx.invoked_subcommand is None:
        browser_status()


@browser_app.command("status")
def browser_status():
    """Show browser status."""
    import requests
    
    try:
        response = requests.get("http://localhost:8789/browser/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("started"):
                ch.success("Browser is running")
                if data.get("has_page"):
                    ch.info("  Active page loaded")
                else:
                    ch.info("  No page loaded")
            else:
                ch.info("Browser is not running")
        elif response.status_code == 503:
            ch.warning("Browser module not available (install playwright)")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("open")
def browser_open(
    url: str = typer.Argument(..., help="URL to navigate to"),
):
    """Navigate browser to URL."""
    import requests
    
    try:
        response = requests.post(
            "http://localhost:8789/browser/navigate",
            json={"url": url},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Navigated to: {url}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("screenshot")
def browser_screenshot(
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Save path"),
    full_page: bool = typer.Option(False, "--full", "-f", help="Capture full page"),
):
    """Capture browser screenshot."""
    import requests
    
    try:
        response = requests.post(
            "http://localhost:8789/browser/screenshot",
            json={"path": path, "full_page": full_page},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            ch.success(f"Screenshot saved: {data.get('path', 'unknown')}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("click")
def browser_click(
    selector: str = typer.Argument(..., help="CSS selector to click"),
):
    """Click element on page."""
    import requests
    
    try:
        response = requests.post(
            "http://localhost:8789/browser/click",
            json={"selector": selector},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Clicked: {selector}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("fill")
def browser_fill(
    selector: str = typer.Argument(..., help="CSS selector for input"),
    value: str = typer.Argument(..., help="Value to fill"),
):
    """Fill input field."""
    import requests
    
    try:
        response = requests.post(
            "http://localhost:8789/browser/fill",
            json={"selector": selector, "value": value},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Filled: {selector}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@browser_app.command("stop")
def browser_stop():
    """Stop browser."""
    import requests
    
    try:
        response = requests.post(
            "http://localhost:8789/browser/stop",
            timeout=10,
        )
        if response.status_code == 200:
            ch.success("Browser stopped")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(browser_app, name="browser")


# ============================================================================
# TASK QUEUE (Async operations queue)
# ============================================================================

queue_app = typer.Typer(
    help="Task queue for async operations",
    invoke_without_command=True,
    no_args_is_help=False,
)


@queue_app.callback()
def queue_callback(ctx: typer.Context):
    """Task queue - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        queue_list()


@queue_app.command("list")
def queue_list(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max tasks to show"),
):
    """List queued tasks."""
    import requests
    
    try:
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = requests.get(
            "http://localhost:8789/tasks",
            params=params,
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            tasks = data.get("tasks", [])
            
            if not tasks:
                ch.info("No tasks in queue")
                return
            
            ch.info(f"Tasks ({len(tasks)}):")
            for task in tasks:
                status_color = {
                    "pending": "blue",
                    "queued": "cyan",
                    "running": "yellow",
                    "completed": "green",
                    "failed": "red",
                    "cancelled": "dim",
                }.get(task.get("status", ""), "white")
                
                ch.console.print(
                    f"  [{task['id']}] {task['name']} - {task['status']}",
                    style=status_color
                )
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("add")
def queue_add(
    name: str = typer.Argument(..., help="Task name"),
    handler: str = typer.Argument(..., help="Handler to execute"),
    params: Optional[str] = typer.Option(None, "--params", "-p", help="JSON params"),
    priority: int = typer.Option(50, "--priority", help="Priority (lower = higher)"),
):
    """Add a task to the queue."""
    import requests
    import json as json_mod
    
    try:
        task_params = {}
        if params:
            task_params = json_mod.loads(params)
        
        response = requests.post(
            "http://localhost:8789/tasks",
            json={
                "name": name,
                "handler": handler,
                "params": task_params,
                "priority": priority,
            },
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            ch.success(f"Task added: {data.get('id')}")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except json_mod.JSONDecodeError:
        ch.error("Invalid JSON in --params")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("show")
def queue_show(
    task_id: str = typer.Argument(..., help="Task ID"),
):
    """Show task details."""
    import requests
    
    try:
        response = requests.get(
            f"http://localhost:8789/tasks/{task_id}",
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            ch.info(f"Task: {data.get('name', 'unknown')}")
            ch.console.print(f"  ID: {data.get('id')}")
            ch.console.print(f"  Handler: {data.get('handler')}")
            ch.console.print(f"  Status: {data.get('status')}")
            ch.console.print(f"  Priority: {data.get('priority')}")
            if data.get('error'):
                ch.console.print(f"  Error: {data.get('error')}", style="red")
            if data.get('result'):
                ch.console.print(f"  Result: {data.get('result')}")
        elif response.status_code == 404:
            ch.error(f"Task {task_id} not found")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("cancel")
def queue_cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel"),
):
    """Cancel a pending task."""
    import requests
    
    try:
        response = requests.post(
            f"http://localhost:8789/tasks/{task_id}/cancel",
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Task {task_id} cancelled")
        elif response.status_code == 404:
            ch.error(f"Task {task_id} not found")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("stats")
def queue_stats():
    """Show queue statistics."""
    import requests
    
    try:
        response = requests.get(
            "http://localhost:8789/tasks/stats",
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            
            ch.info("Task Queue Statistics:")
            ch.console.print(f"  Total tasks: {data.get('total_tasks', 0)}")
            ch.console.print(f"  Heap size: {data.get('heap_size', 0)}")
            ch.console.print(f"  Completed: {data.get('completed_count', 0)}")
            
            counts = data.get("status_counts", {})
            if counts:
                ch.console.print("\n  Status breakdown:")
                for status, count in counts.items():
                    ch.console.print(f"    {status}: {count}")
            
            worker = data.get("worker", {})
            if worker:
                ch.console.print("\n  Worker:")
                ch.console.print(f"    Running: {worker.get('running', False)}")
                ch.console.print(f"    Active tasks: {worker.get('active_tasks', 0)}")
                ch.console.print(f"    Completed: {worker.get('tasks_completed', 0)}")
                ch.console.print(f"    Failed: {worker.get('tasks_failed', 0)}")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(queue_app, name="queue")


# ============================================================================
# MEMORY MANAGEMENT
# ============================================================================

memory_app = typer.Typer(
    help="Manage conversation memory and knowledge base",
    no_args_is_help=True,
)


@memory_app.command("sessions")
def memory_sessions(
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum sessions to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """List conversation sessions."""
    from pathlib import Path
    
    try:
        from navig.memory import ConversationStore
        
        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory.db"
        
        if not db_path.exists():
            if plain:
                print("No sessions")
            else:
                ch.info("No conversation history yet")
            return
        
        store = ConversationStore(db_path)
        sessions = store.list_sessions(limit=limit)
        
        if not sessions:
            if plain:
                print("No sessions")
            else:
                ch.info("No conversation sessions found")
            return
        
        if plain:
            for s in sessions:
                print(f"{s.session_key}\t{s.message_count}\t{s.total_tokens}\t{s.updated_at.isoformat()}")
        else:
            from rich.table import Table
            
            table = Table(title="Conversation Sessions")
            table.add_column("Session", style="cyan")
            table.add_column("Messages", justify="right")
            table.add_column("Tokens", justify="right")
            table.add_column("Last Updated", style="dim")
            
            for s in sessions:
                table.add_row(
                    s.session_key,
                    str(s.message_count),
                    str(s.total_tokens),
                    s.updated_at.strftime("%Y-%m-%d %H:%M"),
                )
            
            ch.console.print(table)
        
        store.close()
        
    except ImportError as e:
        ch.error(f"Memory module not available: {e}")
    except Exception as e:
        ch.error(f"Error listing sessions: {e}")


@memory_app.command("history")
def memory_history(
    session: str = typer.Argument(..., help="Session key to show"),
    limit: int = typer.Option(50, "--limit", "-l", help="Maximum messages"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """Show conversation history for a session."""
    from pathlib import Path
    
    try:
        from navig.memory import ConversationStore
        
        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory.db"
        
        if not db_path.exists():
            ch.error("No conversation history")
            return
        
        store = ConversationStore(db_path)
        messages = store.get_history(session, limit=limit)
        
        if not messages:
            ch.info(f"No messages in session '{session}'")
            store.close()
            return
        
        if plain:
            for m in messages:
                print(f"{m.role}\t{m.timestamp.isoformat()}\t{m.content[:100]}")
        else:
            ch.info(f"Session: {session} ({len(messages)} messages)")
            ch.console.print()
            
            for m in messages:
                role_style = "bold cyan" if m.role == "user" else "bold green"
                ch.console.print(f"[{role_style}]{m.role.upper()}[/] ({m.timestamp.strftime('%H:%M')})")
                ch.console.print(m.content[:500] + ("..." if len(m.content) > 500 else ""))
                ch.console.print()
        
        store.close()
        
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("clear")
def memory_clear(
    session: str = typer.Option(None, "--session", "-s", help="Clear specific session"),
    all_sessions: bool = typer.Option(False, "--all", help="Clear all sessions"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear conversation memory."""
    from pathlib import Path
    
    if not session and not all_sessions:
        ch.error("Specify --session or --all")
        raise typer.Exit(1)
    
    try:
        from navig.memory import ConversationStore
        
        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "memory.db"
        
        if not db_path.exists():
            ch.info("No memory to clear")
            return
        
        if not force:
            target = "all sessions" if all_sessions else f"session '{session}'"
            if not typer.confirm(f"Clear {target}?"):
                raise typer.Abort()
        
        store = ConversationStore(db_path)
        
        if all_sessions:
            sessions = store.list_sessions(limit=1000)
            count = 0
            for s in sessions:
                if store.delete_session(s.session_key):
                    count += 1
            ch.success(f"Cleared {count} sessions")
        else:
            if store.delete_session(session):
                ch.success(f"Cleared session '{session}'")
            else:
                ch.warning(f"Session '{session}' not found")
        
        store.close()
        
    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("knowledge")
def memory_knowledge(
    action: str = typer.Argument("list", help="list, add, search, clear"),
    key: str = typer.Option(None, "--key", "-k", help="Knowledge key"),
    content: str = typer.Option(None, "--content", "-c", help="Knowledge content"),
    query: str = typer.Option(None, "--query", "-q", help="Search query"),
    tags: str = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    limit: int = typer.Option(20, "--limit", "-l", help="Result limit"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """Manage knowledge base entries."""
    from pathlib import Path
    
    try:
        from navig.memory import KnowledgeBase, KnowledgeEntry
        
        config = _get_config_manager()
        db_path = Path(config.global_config_dir) / "knowledge.db"
        
        kb = KnowledgeBase(db_path, embedding_provider=None)
        
        if action == "list":
            # List all entries
            entries = kb.export_entries()[:limit]
            
            if not entries:
                ch.info("Knowledge base is empty")
                return
            
            if plain:
                for e in entries:
                    print(f"{e['key']}\t{e['source']}\t{e['content'][:80]}")
            else:
                from rich.table import Table
                
                table = Table(title="Knowledge Base")
                table.add_column("Key", style="cyan")
                table.add_column("Source", style="dim")
                table.add_column("Content", max_width=50)
                table.add_column("Tags")
                
                for e in entries:
                    import json
                    tags_list = json.loads(e.get('tags', '[]'))
                    table.add_row(
                        e['key'],
                        e.get('source', ''),
                        e['content'][:50] + "..." if len(e['content']) > 50 else e['content'],
                        ", ".join(tags_list),
                    )
                
                ch.console.print(table)
        
        elif action == "add":
            if not key or not content:
                ch.error("--key and --content required for add")
                raise typer.Exit(1)
            
            tag_list = [t.strip() for t in tags.split(",")] if tags else []
            
            entry = KnowledgeEntry(
                key=key,
                content=content,
                tags=tag_list,
                source="cli",
            )
            kb.upsert(entry, compute_embedding=False)
            ch.success(f"Added knowledge: {key}")
        
        elif action == "search":
            if not query:
                ch.error("--query required for search")
                raise typer.Exit(1)
            
            tag_list = [t.strip() for t in tags.split(",")] if tags else None
            results = kb.text_search(query, limit=limit, tags=tag_list)
            
            if not results:
                ch.info("No matching entries")
                return
            
            if plain:
                for e in results:
                    print(f"{e.key}\t{e.content[:80]}")
            else:
                for e in results:
                    ch.console.print(f"[cyan]{e.key}[/]")
                    ch.console.print(f"  {e.content[:200]}")
                    if e.tags:
                        ch.console.print(f"  Tags: {', '.join(e.tags)}")
                    ch.console.print()
        
        elif action == "clear":
            if not typer.confirm("Clear entire knowledge base?"):
                raise typer.Abort()
            count = kb.clear()
            ch.success(f"Cleared {count} entries")
        
        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Valid actions: list, add, search, clear")
        
        kb.close()
        
    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("stats")
def memory_stats():
    """Show memory usage statistics."""
    from pathlib import Path
    
    try:
        from navig.memory import ConversationStore, KnowledgeBase
        
        config = _get_config_manager()
        
        # Conversation stats
        conv_db = Path(config.global_config_dir) / "memory.db"
        if conv_db.exists():
            store = ConversationStore(conv_db)
            sessions = store.list_sessions(limit=1000)
            total_messages = sum(s.message_count for s in sessions)
            total_tokens = sum(s.total_tokens for s in sessions)
            store.close()
            
            ch.info("Conversation Memory:")
            ch.console.print(f"  Sessions: {len(sessions)}")
            ch.console.print(f"  Messages: {total_messages}")
            ch.console.print(f"  Tokens: {total_tokens:,}")
            ch.console.print(f"  Size: {conv_db.stat().st_size / 1024:.1f} KB")
        else:
            ch.info("Conversation Memory: empty")
        
        ch.console.print()
        
        # Knowledge stats
        kb_db = Path(config.global_config_dir) / "knowledge.db"
        if kb_db.exists():
            kb = KnowledgeBase(kb_db, embedding_provider=None)
            count = kb.count()
            kb.close()
            
            ch.info("Knowledge Base:")
            ch.console.print(f"  Entries: {count}")
            ch.console.print(f"  Size: {kb_db.stat().st_size / 1024:.1f} KB")
        else:
            ch.info("Knowledge Base: empty")
        
    except Exception as e:
        ch.error(f"Error: {e}")


# Memory Bank Commands (file-based knowledge with vector search)

@memory_app.command("bank")
def memory_bank_status(
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Show memory bank status and statistics.
    
    The memory bank is a file-based knowledge store at ~/.navig/memory/
    that supports hybrid search (vector + keyword).
    
    Examples:
        navig memory bank
        navig memory bank --plain
    """
    try:
        from navig.memory import get_memory_manager
        
        manager = get_memory_manager(use_embeddings=False)  # Don't load embeddings for status
        status = manager.get_status()
        
        if plain:
            print(f"directory={status['memory_directory']}")
            print(f"files={status['indexed_files']}")
            print(f"chunks={status['total_chunks']}")
            print(f"tokens={status['total_tokens']}")
            print(f"embedded={status['embedded_chunks']}")
            print(f"size_mb={status['database_size_mb']}")
            print(f"embeddings={status['embeddings_enabled']}")
        else:
            ch.info("Memory Bank Status")
            ch.console.print(f"  Directory: {status['memory_directory']}")
            ch.console.print(f"  Indexed files: {status['indexed_files']}")
            ch.console.print(f"  Total chunks: {status['total_chunks']}")
            ch.console.print(f"  Total tokens: {status['total_tokens']:,}")
            ch.console.print(f"  Embedded chunks: {status['embedded_chunks']}")
            ch.console.print(f"  Database size: {status['database_size_mb']} MB")
            
            if status['embeddings_enabled']:
                ch.console.print(f"  Embedding model: {status['embedding_model']}")
            else:
                ch.console.print("  Embeddings: [dim]disabled[/dim]")
        
        manager.close()
        
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("index")
def memory_bank_index(
    force: bool = typer.Option(False, "--force", "-f", help="Re-index even unchanged files"),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip embedding generation"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show file-by-file progress"),
):
    """Index files in the memory bank.
    
    Scans ~/.navig/memory/ for .md/.txt files and creates
    searchable chunks with vector embeddings.
    
    Examples:
        navig memory index
        navig memory index --force
        navig memory index --no-embed
    """
    try:
        from navig.memory import get_memory_manager
        
        def progress(file_path: str, status: str):
            if verbose:
                icon = "✓" if status == "indexed" else "→" if status == "skipped" else "✗"
                ch.console.print(f"  {icon} {file_path}")
        
        ch.info("Indexing memory bank...")
        
        manager = get_memory_manager(use_embeddings=not no_embed)
        result = manager.index(
            force=force,
            embed=not no_embed,
            progress_callback=progress if verbose else None,
        )
        
        ch.success(f"Indexed {result.files_processed} files ({result.files_skipped} skipped)")
        ch.console.print(f"  Created {result.chunks_created} chunks")
        ch.console.print(f"  Total tokens: {result.total_tokens:,}")
        ch.console.print(f"  Embedded: {result.chunks_embedded} chunks")
        ch.console.print(f"  Duration: {result.duration_seconds:.2f}s")
        
        if result.errors:
            ch.warning(f"Errors ({len(result.errors)}):")
            for err in result.errors[:5]:
                ch.console.print(f"  • {err}")
        
        manager.close()
        
    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("For embeddings, install: pip install sentence-transformers")
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("search")
def memory_bank_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-l", help="Maximum results"),
    file: str = typer.Option(None, "--file", "-f", help="Filter by file pattern"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    keyword_only: bool = typer.Option(False, "--keyword", "-k", help="Keyword-only search (no embeddings)"),
):
    """Search the memory bank with hybrid search.
    
    Uses 70% vector similarity + 30% BM25 keyword matching.
    Falls back to keyword-only if embeddings unavailable.
    
    Examples:
        navig memory search "docker networking"
        navig memory search "nginx config" --limit 10
        navig memory search "deploy" --file "*.md"
        navig memory search "docker" --keyword
    """
    import json as json_module
    
    try:
        from navig.memory import get_memory_manager
        
        # Try with embeddings first, fall back to keyword-only
        use_embeddings = not keyword_only
        manager = None
        
        try:
            manager = get_memory_manager(use_embeddings=use_embeddings)
            response = manager.search(query, limit=limit, file_filter=file)
        except ImportError:
            # Embeddings not available, fall back to keyword-only
            if not keyword_only and not plain:
                ch.warning("Embeddings unavailable, using keyword-only search")
                ch.info("For semantic search: pip install sentence-transformers numpy")
            manager = get_memory_manager(use_embeddings=False)
            # Use keyword-only search via search engine (proper normalization)
            response = manager.search_engine.search(
                query, limit=limit, file_filter=file, keyword_only=True
            )
        
        if json_output:
            print(json_module.dumps(response.to_dict(), indent=2))
            if manager:
                manager.close()
            return
        
        if not response.results:
            if plain:
                print("No results")
            else:
                ch.info("No matching results found")
            if manager:
                manager.close()
            return
        
        if plain:
            for r in response.results:
                print(f"{r.combined_score:.3f}\t{r.file_path}:{r.line_start}\t{r.snippet[:80]}")
        else:
            ch.info(f"Found {len(response.results)} results ({response.search_time_ms:.1f}ms)")
            ch.console.print()
            
            for i, r in enumerate(response.results, 1):
                score_bar = "█" * int(r.combined_score * 10)
                ch.console.print(f"[bold cyan]{i}.[/bold cyan] [dim]{r.citation()}[/dim]")
                ch.console.print(f"   Score: [green]{score_bar}[/green] {r.combined_score:.3f}")
                ch.console.print(f"   {r.snippet}")
                ch.console.print()
        
        if manager:
            manager.close()
        
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("files")
def memory_bank_files(
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """List indexed files in the memory bank."""
    try:
        from navig.memory import get_memory_manager
        
        manager = get_memory_manager(use_embeddings=False)
        files = manager.list_files()
        
        if not files:
            if plain:
                print("No files")
            else:
                ch.info("No files indexed yet")
                ch.info(f"Add .md files to: {manager.memory_dir}")
            manager.close()
            return
        
        if plain:
            for f in files:
                print(f"{f['file_path']}\t{f['chunk_count']}\t{f['total_tokens']}")
        else:
            from rich.table import Table
            
            table = Table(title="Indexed Memory Files")
            table.add_column("File", style="cyan")
            table.add_column("Chunks", justify="right")
            table.add_column("Tokens", justify="right")
            table.add_column("Indexed", style="dim")
            
            for f in files:
                indexed_at = f['indexed_at'][:10] if f.get('indexed_at') else '-'
                table.add_row(
                    f['file_path'],
                    str(f['chunk_count']),
                    str(f['total_tokens']),
                    indexed_at,
                )
            
            ch.console.print(table)
        
        manager.close()
        
    except Exception as e:
        ch.error(f"Error: {e}")


@memory_app.command("clear-bank")
def memory_bank_clear(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear the memory bank index (keeps original files).
    
    This clears the search index but preserves the original
    .md files in ~/.navig/memory/
    
    Examples:
        navig memory clear-bank
        navig memory clear-bank --force
    """
    try:
        from navig.memory import get_memory_manager
        
        if not force:
            if not typer.confirm("Clear memory bank index? (files are preserved)"):
                raise typer.Abort()
        
        manager = get_memory_manager(use_embeddings=False)
        result = manager.clear(confirm=True)
        
        ch.success("Memory bank index cleared")
        ch.console.print(f"  Files removed: {result.get('files_deleted', 0)}")
        ch.console.print(f"  Chunks removed: {result.get('chunks_deleted', 0)}")
        ch.console.print(f"  Cache cleared: {result.get('cache_cleared', 0)}")
        
        manager.close()
        
    except typer.Abort:
        ch.info("Cancelled")
    except Exception as e:
        ch.error(f"Error: {e}")


app.add_typer(memory_app, name="memory")


# ============================================================================
# AGENT MODE
# ============================================================================

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


# ============================================================================
# AUTOMATION (Cross-Platform)
# ============================================================================

# Import cross-platform automation commands
try:
    from navig.commands.auto import auto_app
    app.add_typer(auto_app, name="auto")
except ImportError:
    pass

# ============================================================================
# AUTOHOTKEY AUTOMATION (Windows)
# ============================================================================

# Import AHK commands from separate module (Windows only)
if sys.platform == 'win32':
    try:
        from navig.commands.ahk import ahk_app
        app.add_typer(ahk_app, name="ahk")
    except ImportError:
        pass  # AHK adapter not available


# ============================================================================
# INTERACTIVE MENU
# ============================================================================

@app.command("menu")
def menu_command(ctx: typer.Context):
    """
    Launch interactive menu interface.
    
    Navigate NAVIG using a terminal UI with arrow keys and keyboard shortcuts.
    Mr. Robot inspired theme with Rich formatting.
    
    Features:
    - Host and app management
    - Database operations
    - File transfers
    - System monitoring
    - Command history tracking
    
    Navigation:
    - Arrow keys or numbers to select menu items
    - Enter to confirm selection
    - ESC or 'q' to go back
    - '?' for help
    - Ctrl+C to exit
    
    Note: If experiencing freezes on Windows, questionary may need to be uninstalled.
    The menu will work fine with number-based selection only.
    """
    try:
        from navig.commands.interactive import launch_menu
        launch_menu(ctx.obj)
    except ImportError as e:
        ch.error(f"Failed to load interactive menu: {e}")
        ch.info("Ensure Rich is installed: pip install rich")
        sys.exit(1)
    except Exception as e:
        ch.error(f"Interactive menu error: {e}")
        sys.exit(1)


@app.command("interactive", hidden=True)
def interactive_command(ctx: typer.Context):
    """Alias for 'menu' command - launch interactive interface."""
    try:
        from navig.commands.interactive import launch_menu
        launch_menu(ctx.obj)
    except ImportError as e:
        ch.error(f"Failed to load interactive menu: {e}")
        ch.info("Ensure Rich is installed: pip install rich")
        sys.exit(1)
    except Exception as e:
        ch.error(f"Interactive menu error: {e}")
        sys.exit(1)



# ============================================================================
# EVOLUTION COMMANDS
# ============================================================================

try:
    from navig.commands.evolution import evolution_app
    app.add_typer(evolution_app, name="evolve")
except ImportError:
    pass

try:
    from navig.commands.script import script_app
    app.add_typer(script_app, name="script")
except ImportError:
    pass


# ============================================================================
# CONFIG MANAGEMENT
# ============================================================================

config_app = typer.Typer(
    help="Manage NAVIG settings and configuration",
    callback=make_subcommand_callback("config")
)
app.add_typer(config_app, name="config")


@config_app.command("migrate")
def config_migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without saving"),
):
    """
    Migrate configuration to the latest version.
    """
    from navig.core.migrations import migrate_config
    from navig.config import get_config_manager
    import yaml
    
    cm = get_config_manager()
    global_config_file = cm.global_config_dir / "config.yaml"
    
    if not global_config_file.exists():
        ch.error("No global configuration found.")
        raise typer.Exit(1)
        
    try:
        # Load raw config to avoid auto-migration on load
        with open(global_config_file, 'r') as f:
            raw_config = yaml.safe_load(f) or {}
            
        migrated, modified = migrate_config(raw_config)
        
        if not modified:
            ch.success("Configuration is already up to date.")
            return
            
        if dry_run:
            ch.info("Dry run: Configuration would be updated.")
            ch.info(f"New version: {migrated.get('version')}")
        else:
            with open(global_config_file, 'w') as f:
                yaml.dump(migrated, f, default_flow_style=False, sort_keys=False)
            ch.success(f"Configuration migrated to version {migrated.get('version')}")
            
    except Exception as e:
        ch.error(f"Migration failed: {e}")
        raise typer.Exit(1)


@config_app.command("audit")
def config_audit(
    fix: bool = typer.Option(False, "--fix", help="Attempt to fix issues automatically"),
):
    """
    Audit configuration for security and validity.
    """
    from navig.commands.security import config_audit as run_audit
    run_audit(fix=fix)


@config_app.command("show")
def config_show(
    scope: str = typer.Argument("global", help="Scope: global or host name"),
):
    """Show configuration."""
    cm = _get_config_manager()
    
    if scope == "global":
        config = cm._load_global_config()
        ch.print_json(config)
    else:
        try:
            config = cm.load_host_config(scope)
            ch.print_json(config)
        except Exception as e:
            ch.error(str(e))


@app.command("config")
def config_command(ctx: typer.Context):
    """
    Manage NAVIG settings and configuration.
    """
    if ctx.invoked_subcommand is None:
        show_subcommand_help("config", ctx)
        raise typer.Exit()


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Configuration key (e.g. ai.default_provider)"),
):
    """
    Get a configuration value.
    """
    cm = _get_config_manager()
    config = cm._load_global_config()
    
    # Traverse nested keys
    keys = key.split('.')
    value = config
    
    try:
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                value = None
                break
        
        if value is None:
            ch.warning(f"Key '{key}' not found or is empty.")
        else:
            if isinstance(value, (dict, list)):
                ch.print_json(value)
            else:
                ch.console.print(str(value))
                
    except Exception as e:
        ch.error(f"Error retrieving key: {e}")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Configuration key (e.g. ai.model_preference)"),
    value: str = typer.Argument(..., help="Value to set (JSON/YAML format for complex types)"),
):
    """
    Set a configuration value.
    """
    try:
        import yaml
        from navig.config import get_config_manager
        
        # Parse value - try JSON/YAML first, fallback to string
        try:
            parsed_value = yaml.safe_load(value)
        except:
            parsed_value = value
            
        cm = get_config_manager()
        global_config_file = cm.global_config_dir / "config.yaml"
        
        if not global_config_file.exists():
            ch.error("No global configuration found.")
            raise typer.Exit(1)
            
        with open(global_config_file, 'r') as f:
            config = yaml.safe_load(f) or {}
            
        # Update nested key
        keys = key.split('.')
        target = config
        
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
            if not isinstance(target, dict):
                 ch.error(f"Cannot set key '{key}' because '{k}' is not a dictionary.")
                 raise typer.Exit(1)
                 
        target[keys[-1]] = parsed_value
        
        # Save
        with open(global_config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            
        ch.success(f"Updated '{key}' to: {parsed_value}")
        
    except Exception as e:
        ch.error(f"Error setting config: {e}")
        raise typer.Exit(1)


# ============================================================================
# CALENDAR INTEGRATION (Proactive Assistance)
# ============================================================================

calendar_app = typer.Typer(
    name="calendar",
    help="Calendar integration for proactive assistance.",
    no_args_is_help=True,
)
app.add_typer(calendar_app, name="calendar")


@calendar_app.command("list")
def calendar_list(
    hours: int = typer.Option(24, "--hours", "-h", help="Hours ahead to look"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List upcoming calendar events.
    
    Examples:
        navig calendar list
        navig calendar list --hours 48
    """
    import asyncio
    from datetime import datetime, timedelta
    
    try:
        from navig.agent.proactive import GoogleCalendar, MockCalendar
        
        # Try Google, fallback to Mock
        try:
            provider = GoogleCalendar()
            if not provider.service:
                ch.warning("Google Calendar not authenticated. Using mock data.")
                provider = MockCalendar()
        except Exception:
            provider = MockCalendar()
        
        now = datetime.now()
        events = asyncio.run(provider.list_events(now, now + timedelta(hours=hours)))
        
        if json_output:
            import json
            ch.raw_print(json.dumps([{
                "id": e.id,
                "title": e.title,
                "start": e.start.isoformat(),
                "end": e.end.isoformat(),
                "location": e.location,
            } for e in events], indent=2))
            return
        
        if not events:
            ch.info(f"No events in the next {hours} hours.")
            return
            
        table = ch.Table(title=f"Upcoming Events ({hours}h)")
        table.add_column("Time", style="cyan")
        table.add_column("Title", style="yellow")
        table.add_column("Location", style="green")
        
        for event in events:
            table.add_row(
                event.start.strftime("%m/%d %H:%M"),
                event.title,
                event.location or "-"
            )
        
        ch.console.print(table)
        
    except Exception as e:
        ch.error(f"Error listing events: {e}")


@calendar_app.command("auth")
def calendar_auth():
    """
    Authenticate with Google Calendar.
    
    Requires a credentials.json file from Google Cloud Console.
    """
    try:
        from navig.agent.proactive.google_calendar import GoogleCalendar
        
        provider = GoogleCalendar()
        if provider.service:
            ch.success("Successfully authenticated with Google Calendar!")
        else:
            ch.error("Authentication failed. Check credentials.json file.")
    except ImportError:
        ch.error("Google API libraries not installed.")
        ch.dim("Run: pip install google-auth-oauthlib google-api-python-client")
    except Exception as e:
        ch.error(f"Authentication error: {e}")


# ============================================================================
# EMAIL INTEGRATION (Proactive Assistance)
# ============================================================================

email_app = typer.Typer(
    name="email",
    help="Email integration for proactive assistance.",
    no_args_is_help=True,
)
app.add_typer(email_app, name="email")


@email_app.command("list")
def email_list(
    limit: int = typer.Option(10, "--limit", "-n", help="Max messages to fetch"),
    provider: str = typer.Option("gmail", "--provider", "-p", help="Provider: gmail, outlook, fastmail, imap"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List unread emails.
    
    Requires email credentials in config or environment.
    
    Examples:
        navig email list
        navig email list --limit 20 --provider outlook
    """
    import asyncio
    
    try:
        from navig.agent.proactive import MockEmail
        from navig.config import get_config_manager
        
        cm = get_config_manager()
        email_config = cm.global_config.get('email', {})
        
        email_addr = email_config.get('address') or os.environ.get('NAVIG_EMAIL_ADDRESS')
        password = email_config.get('password') or os.environ.get('NAVIG_EMAIL_PASSWORD')
        
        if not email_addr or not password:
            ch.warning("Email not configured. Using mock data.")
            ch.dim("Set email.address and email.password in config, or use env vars:")
            ch.dim("  NAVIG_EMAIL_ADDRESS, NAVIG_EMAIL_PASSWORD")
            email_provider = MockEmail()
        else:
            from navig.agent.proactive.imap_email import GmailProvider, OutlookProvider, FastmailProvider
            
            providers_map = {
                'gmail': GmailProvider,
                'outlook': OutlookProvider,
                'fastmail': FastmailProvider,
            }
            
            if provider in providers_map:
                email_provider = providers_map[provider](email_addr, password)
            else:
                ch.error(f"Unknown provider: {provider}")
                return
        
        messages = asyncio.run(email_provider.list_unread(limit=limit))
        
        if json_output:
            import json
            ch.raw_print(json.dumps([{
                "id": m.id,
                "subject": m.subject,
                "sender": m.sender,
                "snippet": m.snippet,
                "received_at": m.received_at.isoformat(),
            } for m in messages], indent=2))
            return
        
        if not messages:
            ch.info("No unread emails.")
            return
            
        table = ch.Table(title=f"Unread Emails ({len(messages)})")
        table.add_column("From", style="cyan", max_width=25)
        table.add_column("Subject", style="yellow")
        table.add_column("Preview", style="dim", max_width=40)
        
        for msg in messages:
            table.add_row(
                msg.sender[:25] if len(msg.sender) > 25 else msg.sender,
                msg.subject,
                msg.snippet[:40] + "..." if len(msg.snippet) > 40 else msg.snippet
            )
        
        ch.console.print(table)
        
    except Exception as e:
        ch.error(f"Error listing emails: {e}")


@email_app.command("setup")
def email_setup(
    provider: str = typer.Argument(..., help="Provider: gmail, outlook, fastmail"),
):
    """
    Interactive email setup.
    
    Stores credentials securely in NAVIG config.
    """
    import getpass
    
    try:
        from navig.config import get_config_manager
        import yaml
        
        ch.info(f"Setting up {provider} email...")
        
        email_addr = input("Email address: ").strip()
        
        if provider == 'gmail':
            ch.dim("Gmail requires an App Password (not your regular password)")
            ch.dim("Generate at: https://myaccount.google.com/apppasswords")
        
        password = getpass.getpass("Password/App Password: ")
        
        # Test connection
        ch.info("Testing connection...")
        
        from navig.agent.proactive.imap_email import GmailProvider, OutlookProvider, FastmailProvider
        
        providers_map = {
            'gmail': GmailProvider,
            'outlook': OutlookProvider,
            'fastmail': FastmailProvider,
        }
        
        if provider not in providers_map:
            ch.error(f"Unknown provider: {provider}")
            return
        
        import asyncio
        test_provider = providers_map[provider](email_addr, password)
        messages = asyncio.run(test_provider.list_unread(limit=1))
        
        ch.success(f"Connection successful! Found {len(messages)} unread message(s).")
        
        # Save to config
        cm = get_config_manager()
        config_file = cm.global_config_dir / "config.yaml"
        
        config = {}
        if config_file.exists():
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f) or {}
        
        config['email'] = {
            'provider': provider,
            'address': email_addr,
            'password': password,  # TODO: Use keyring for secure storage
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
        
        ch.success("Email configuration saved!")
        ch.dim(f"Stored in: {config_file}")
        
    except Exception as e:
        ch.error(f"Setup failed: {e}")


# ============================================================================
# PROACTIVE ASSISTANCE ENGINE
# ============================================================================

proactive_app = typer.Typer(
    name="proactive",
    help="Proactive assistance engine (calendar, email, alerts).",
    no_args_is_help=True,
)
app.add_typer(proactive_app, name="proactive")


@proactive_app.command("status")
def proactive_status():
    """Show proactive engine status."""
    try:
        from navig.agent.proactive import ProactiveEngine
        from navig.config import get_config_manager
        
        cm = get_config_manager()
        
        ch.console.print("\n[bold]Proactive Assistance Status[/bold]\n")
        
        # Calendar
        calendar_config = cm.global_config.get('calendar', {})
        if calendar_config.get('provider'):
            ch.console.print(f"  📅 Calendar: [green]{calendar_config.get('provider')}[/green]")
        else:
            ch.console.print("  📅 Calendar: [dim]Not configured[/dim]")
        
        # Email
        email_config = cm.global_config.get('email', {})
        if email_config.get('address'):
            ch.console.print(f"  📧 Email: [green]{email_config.get('address')}[/green]")
        else:
            ch.console.print("  📧 Email: [dim]Not configured[/dim]")
        
        # Engine status
        ch.console.print("\n  ⚙️  Engine: [yellow]Starts with gateway[/yellow]")
        ch.dim("\n  Run 'navig gateway start' to activate proactive assistance.")
        
    except Exception as e:
        ch.error(f"Error: {e}")


@proactive_app.command("test")
def proactive_test():
    """Run a test check for upcoming events/emails."""
    import asyncio
    
    try:
        from navig.agent.proactive.engine import get_proactive_engine
        
        ch.info("Running proactive check...")
        
        engine = get_proactive_engine()
        asyncio.run(engine.run_checks(None))
        
        ch.success("Proactive check complete!")
        
    except Exception as e:
        ch.error(f"Error: {e}")




# ============================================================================
# CALENDAR OPERATIONS
# ============================================================================

try:
    from navig.commands.calendar import calendar_app
    app.add_typer(calendar_app, name="calendar")
except ImportError:
    pass


# ============================================================================
# LLM MODE ROUTER
# ============================================================================

try:
    from navig.commands.mode import mode_app
    app.add_typer(mode_app, name="mode")
except ImportError:
    pass


# ============================================================================
# EMAIL OPERATIONS
# ============================================================================

try:
    from navig.commands.email import email_app
    app.add_typer(email_app, name="email")
except ImportError:
    pass


# ============================================================================
# VOICE (Text-to-Speech)
# ============================================================================

try:
    from navig.commands.voice import voice_app
    app.add_typer(voice_app, name="voice")
except ImportError:
    pass


# ============================================================================
# CREDENTIALS VAULT
# ============================================================================

try:
    from navig.commands.vault import cred_app, profile_app
    app.add_typer(cred_app, name="cred", help="Manage credentials in the vault")
    app.add_typer(profile_app, name="profile", help="Manage credential profiles")
except ImportError:
    pass


# ============================================================================
# CRASH REPORTING
# ============================================================================

try:
    from navig.commands.crash import app as crash_app
    app.add_typer(crash_app, name="crash")
except ImportError:
    pass


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    app()

