"""
NAVIG Onboarding Command - Interactive Setup Wizard

Inspired by advanced onboarding systems, provides a guided setup experience
for new NAVIG users with both quickstart and manual/advanced flows.
"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any
from datetime import datetime
from navig.workspace_ownership import (
    USER_WORKSPACE_DIR,
    detect_project_workspace_duplicates,
    summarize_duplicates,
)

if TYPE_CHECKING:
    from rich.console import Console as RichConsole
    ConsoleType = Optional[RichConsole]
else:
    ConsoleType = Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    Console = None  # type: ignore[misc, assignment]
    RICH_AVAILABLE = False


# Default paths
DEFAULT_NAVIG_DIR = Path.home() / ".navig"
DEFAULT_WORKSPACE_DIR = USER_WORKSPACE_DIR
DEFAULT_CONFIG_FILE = DEFAULT_NAVIG_DIR / "navig.json"


def get_console() -> ConsoleType:
    """Get rich console or fall back to basic print."""
    if RICH_AVAILABLE and Console:
        return Console()
    return None


def print_banner(console: ConsoleType) -> None:
    """Print the onboarding banner."""
    banner = """
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║   ███╗   ██╗ █████╗ ██╗   ██╗██╗ ██████╗                         ║
║   ████╗  ██║██╔══██╗██║   ██║██║██╔════╝                         ║
║   ██╔██╗ ██║███████║██║   ██║██║██║  ███╗                        ║
║   ██║╚██╗██║██╔══██║╚██╗ ██╔╝██║██║   ██║                        ║
║   ██║ ╚████║██║  ██║ ╚████╔╝ ██║╚██████╔╝                        ║
║   ╚═╝  ╚═══╝╚═╝  ╚═╝  ╚═══╝  ╚═╝ ╚═════╝                         ║
║                                                                   ║
║   Welcome to NAVIG - Your AI-Powered Operations Assistant  ║
║                                                                   ║
╚═══════════════════════════════════════════════════════════════════╝
"""
    if console:
        console.print(banner, style="bold cyan")
    else:
        print(banner)


def run_quickstart(console: ConsoleType) -> Dict[str, Any]:
    """
    Run the quickstart onboarding flow.
    
    Minimal prompts, auto-generates sensible defaults.
    """
    console.print("\n[bold green]⚡ Quickstart Setup[/bold green]")
    console.print("We'll set up NAVIG with sensible defaults in just a few steps.\n")
    
    config = {
        "meta": {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "onboarding_flow": "quickstart",
        },
        "agents": {
            "defaults": {
                "workspace": str(DEFAULT_WORKSPACE_DIR),
                "model": "openrouter",
                "typing_mode": "instant",
            }
        },
        "auth": {
            "profiles": {}
        },
        "channels": {},
    }
    
    # Step 1: AI Provider
    console.print("[bold]Step 1/3: AI Provider[/bold]")
    console.print("Which AI provider do you want to use?")
    console.print("  1. OpenRouter (recommended - access to many models)")
    console.print("  2. OpenAI")
    console.print("  3. Anthropic")
    console.print("  4. Ollama (local)")
    console.print("  5. Skip for now")
    
    provider_choice = Prompt.ask(
        "Select provider",
        choices=["1", "2", "3", "4", "5"],
        default="1"
    )
    
    provider_map = {
        "1": "openrouter",
        "2": "openai", 
        "3": "anthropic",
        "4": "ollama",
        "5": None,
    }
    
    provider = provider_map[provider_choice]
    if provider:
        config["agents"]["defaults"]["model"] = provider
        
        if provider != "ollama":
            api_key = Prompt.ask(
                f"Enter {provider} API key (or press Enter to skip)",
                password=True,
                default=""
            )
            if api_key:
                config["auth"]["profiles"][provider] = {
                    "type": "api-key",
                    "key": api_key,
                }
                console.print(f"[green]✓ {provider} API key saved[/green]")
        else:
            console.print("[dim]Ollama uses local models, no API key needed[/dim]")
    
    # Step 2: Telegram Bot (optional)
    console.print("\n[bold]Step 2/3: Telegram Bot (optional)[/bold]")
    setup_telegram = Confirm.ask("Do you want to set up a Telegram bot?", default=False)
    
    if setup_telegram:
        console.print("\n[dim]Get a bot token from @BotFather on Telegram[/dim]")
        bot_token = Prompt.ask("Telegram bot token", password=True, default="")
        
        if bot_token:
            console.print("\n[dim]Get your user ID from @userinfobot on Telegram[/dim]")
            user_id = Prompt.ask("Your Telegram user ID", default="")
            
            config["channels"]["telegram"] = {
                "enabled": True,
                "bot_token": bot_token,
                "allowed_users": [int(user_id)] if user_id.isdigit() else [],
            }
            console.print("[green]✓ Telegram bot configured[/green]")
    
    # Step 3: Workspace
    console.print("\n[bold]Step 3/3: Workspace[/bold]")
    console.print(f"Default workspace: [cyan]{DEFAULT_WORKSPACE_DIR}[/cyan]")
    
    use_default = Confirm.ask("Use default workspace location?", default=True)
    if not use_default:
        requested_workspace = Prompt.ask("Enter workspace path", default=str(DEFAULT_WORKSPACE_DIR))
        console.print(
            "[yellow]Personal/state workspace files are always managed at "
            f"{DEFAULT_WORKSPACE_DIR}[/yellow]"
        )
        console.print(
            f"[dim]Requested path '{requested_workspace}' is treated as project context only.[/dim]"
        )
    
    return config


def run_manual(console: ConsoleType) -> Dict[str, Any]:
    """
    Run the manual/advanced onboarding flow.
    
    Full configuration prompts for power users.
    """
    console.print("\n[bold blue]🔧 Advanced Setup[/bold blue]")
    console.print("Let's configure NAVIG with all available options.\n")
    
    config = {
        "meta": {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "onboarding_flow": "manual",
        },
        "agents": {
            "defaults": {
                "workspace": str(DEFAULT_WORKSPACE_DIR),
                "model": "openrouter",
                "typing_mode": "instant",
                "typing_interval": 4.0,
                "max_history": 20,
            }
        },
        "auth": {
            "profiles": {}
        },
        "channels": {},
        "commands": {
            "confirm_destructive": True,
            "timeout_seconds": 60,
        },
    }
    
    # Section 1: Workspace Configuration
    console.print(Panel("[bold]Section 1: Workspace Configuration[/bold]"))
    
    requested_workspace = Prompt.ask(
        "Workspace directory",
        default=str(DEFAULT_WORKSPACE_DIR)
    )
    if requested_workspace != str(DEFAULT_WORKSPACE_DIR):
        console.print(
            "[yellow]Personal/state workspace files are always managed at "
            f"{DEFAULT_WORKSPACE_DIR}[/yellow]"
        )
        console.print(
            f"[dim]Requested path '{requested_workspace}' is treated as project context only.[/dim]"
        )
    
    # Section 2: AI Provider Configuration
    console.print(Panel("[bold]Section 2: AI Provider Configuration[/bold]"))
    
    providers = ["openrouter", "openai", "anthropic", "groq", "ollama"]
    console.print("Available providers:")
    for i, p in enumerate(providers, 1):
        console.print(f"  {i}. {p}")
    
    primary_choice = Prompt.ask(
        "Primary AI provider",
        choices=["1", "2", "3", "4", "5"],
        default="1"
    )
    primary_provider = providers[int(primary_choice) - 1]
    config["agents"]["defaults"]["model"] = primary_provider
    
    # Configure multiple providers
    configure_more = Confirm.ask("Configure additional providers?", default=True)
    
    while configure_more:
        console.print("\nAvailable providers:", providers)
        provider_name = Prompt.ask("Provider name", default="")
        
        if provider_name and provider_name in providers:
            if provider_name != "ollama":
                api_key = Prompt.ask(f"{provider_name} API key", password=True)
                if api_key:
                    config["auth"]["profiles"][provider_name] = {
                        "type": "api-key",
                        "key": api_key,
                    }
                    console.print(f"[green]✓ {provider_name} configured[/green]")
            else:
                console.print("[dim]Ollama uses local models[/dim]")
        
        configure_more = Confirm.ask("Configure another provider?", default=False)
    
    # Section 3: Agent Settings
    console.print(Panel("[bold]Section 3: Agent Settings[/bold]"))
    
    typing_mode = Prompt.ask(
        "Typing indicator mode",
        choices=["instant", "message", "never"],
        default="instant"
    )
    config["agents"]["defaults"]["typing_mode"] = typing_mode
    
    typing_interval = Prompt.ask(
        "Typing indicator refresh interval (seconds)",
        default="4.0"
    )
    config["agents"]["defaults"]["typing_interval"] = float(typing_interval)
    
    max_history = Prompt.ask(
        "Max conversation history messages",
        default="20"
    )
    config["agents"]["defaults"]["max_history"] = int(max_history)
    
    # Section 4: Channel Configuration
    console.print(Panel("[bold]Section 4: Channel Configuration[/bold]"))
    
    # Telegram
    setup_telegram = Confirm.ask("Configure Telegram bot?", default=False)
    if setup_telegram:
        bot_token = Prompt.ask("Telegram bot token", password=True)
        user_ids = Prompt.ask("Allowed user IDs (comma-separated)", default="")
        
        config["channels"]["telegram"] = {
            "enabled": True,
            "bot_token": bot_token,
            "allowed_users": [int(uid.strip()) for uid in user_ids.split(",") if uid.strip().isdigit()],
            "typing_mode": typing_mode,
        }
    
    # Discord (placeholder)
    setup_discord = Confirm.ask("Configure Discord bot?", default=False)
    if setup_discord:
        console.print("[yellow]Discord support coming soon![/yellow]")
        config["channels"]["discord"] = {"enabled": False}
    
    # Section 5: Command Settings
    console.print(Panel("[bold]Section 5: Command Settings[/bold]"))
    
    confirm_destructive = Confirm.ask(
        "Confirm destructive operations (delete, restart, etc.)?",
        default=True
    )
    config["commands"]["confirm_destructive"] = confirm_destructive
    
    timeout = Prompt.ask("Command timeout (seconds)", default="60")
    config["commands"]["timeout_seconds"] = int(timeout)
    
    return config


def create_workspace_templates(workspace_path: Path, console: ConsoleType) -> None:
    """Create workspace template files (Agent-style bootstrap files)."""
    templates = {
        "AGENTS.md": """---
summary: Operating instructions and memory for the NAVIG agent
read_when: Every session start
status: active
---

# NAVIG Agent Instructions

You are NAVIG, an AI-powered server management assistant. Your role is to help users manage their remote servers through natural language.

## Core Responsibilities

1. **Understand Intent**: Parse natural language queries and identify the user's goal
2. **Execute Commands**: Translate requests into NAVIG CLI commands
3. **Provide Context**: Explain what you're doing and why
4. **Stay Safe**: Warn about destructive operations and confirm before executing

## Available Skills

- **Server Management**: Check disk space, memory, CPU usage
- **Docker Operations**: List, start, stop, restart containers
- **Database Queries**: Run SQL queries, list databases/tables, backups
- **HestiaCP Management**: User management, domain configuration

## Communication Style

- Be concise but informative
- Use emoji sparingly for visual cues
- Format output clearly (tables, lists)
- Proactively suggest related actions

## Memory

Store important context here as you learn about the user's environment:

- Servers: (discovered during conversations)
- Common tasks: (patterns you notice)
- User preferences: (learned over time)
""",
        
        "BOOTSTRAP.md": """---
summary: First-run setup ritual (deleted after completion)
read_when: First session only
status: active
---

# Welcome to NAVIG! 🚀

This is your first time using NAVIG. Let me help you get started.

## Quick Setup Checklist

1. [ ] Verify AI provider is configured (`navig ai providers`)
2. [ ] Add at least one host (`navig host add`)
3. [ ] Test connection (`navig host test`)
4. [ ] Try a simple query ("Show disk space")

## First Steps

1. **Add a Host**: 
   ```bash
   navig host add myserver --host example.com --user admin
   ```

2. **Set Active Host**:
   ```bash
   navig host use myserver
   ```

3. **Test Connection**:
   ```bash
   navig host test
   ```

4. **Ask Me Anything**:
   Just type naturally: "How much disk space is left?"

---
*This file will be removed after your first successful interaction.*
""",
        
        "IDENTITY.md": """---
summary: Agent name, personality, and visual identity
read_when: Every session start
status: active
---

# Agent Identity

**Name**: NAVIG
**Full Name**: Navigation AI Virtual Infrastructure Guardian
**Alias**: The Kraken
**Emoji**: 🦑
**Avatar**: A kraken/squid symbol - relentless guardian of the deep

## Personality Traits

- **Vigilant**: Watches relentlessly, catching crumbs before they compound
- **Decisive**: Acts swiftly, no hedging or fluff
- **Protective**: Guards infrastructure and personal workflows
- **Ruthless**: Against recurring failures ("crumbs")
- **Direct**: Short sentences, impact over process

## Voice

- First person ("Done. State preserved.")
- Active voice, no hedging ("Failed" not "Unfortunately...")
- Technical when needed, plain language otherwise
- Lead with impact, not process
""",
        
        "SOUL.md": """---
summary: Persona, boundaries, and ethical guidelines
read_when: Every session start
status: active
---

# Agent Soul

## Core Values

1. **Safety First**: Never execute destructive commands without confirmation
2. **Transparency**: Always explain what you're doing
3. **Privacy**: Don't log or transmit sensitive data unnecessarily
4. **Reliability**: Admit when you're uncertain

## Boundaries

### I Will:
- Execute read-only commands without confirmation
- Run safe management operations (status checks, listings)
- Help troubleshoot and diagnose issues
- Provide explanations and documentation

### I Will Ask First Before:
- Deleting files or data
- Restarting services
- Modifying configurations
- Running commands on production systems

### I Will Never:
- Execute arbitrary code without review
- Bypass security measures
- Store credentials insecurely
- Operate outside my authorized scope

## Ethical Guidelines

- Respect user privacy
- Be honest about capabilities and limitations
- Don't pretend to have access you don't have
- Recommend security best practices
""",
        
        "TOOLS.md": """---
summary: Available tools and usage conventions
read_when: When executing commands
status: active
---

# Available Tools

## NAVIG CLI Commands

### Host Management
- `navig host list` - List all configured hosts
- `navig host add <name>` - Add a new host
- `navig host use <name>` - Set active host
- `navig host test [name]` - Test connection
- `navig host show [name]` - Show host details

### Remote Execution
- `navig run "<command>"` - Run command on active host
- `navig run "<command>" --host <name>` - Run on specific host

### Docker
- `navig docker ps` - List containers
- `navig docker logs <container>` - Show logs
- `navig docker restart <container>` - Restart container

### Database
- `navig db list` - List databases
- `navig db tables <database>` - List tables
- `navig db query "<sql>"` - Run SQL query

### HestiaCP
- `navig hestia users` - List users
- `navig hestia domains <user>` - List domains
- `navig hestia backup <user>` - Create backup

## Tool Conventions

1. Always use `--plain` flag when parsing output programmatically
2. Prefer read-only commands first to gather information
3. Chain commands logically (switch host → run command → parse output)
4. Handle errors gracefully and inform the user
""",
        
        "USER.md": """---
summary: User profile and preferences
read_when: Every session start
status: active
---

# User Profile

## Identity

- **Name**: (Your name here)
- **Preferred Address**: (How you'd like to be called)
- **Timezone**: (Your timezone)

## Preferences

### Communication
- **Verbosity**: normal (options: brief, normal, detailed)
- **Emoji Usage**: moderate
- **Technical Level**: intermediate

### Operations
- **Confirm Destructive**: yes
- **Auto-backup**: yes (before changes)
- **Default Host**: (your primary server)

## Notes

Add any personal notes or preferences here:

- Favorite commands
- Common workflows
- Server naming conventions
""",
        
        "HEARTBEAT.md": """---
summary: Background task instructions
read_when: For scheduled/automated tasks
status: draft
---

# Heartbeat Tasks

Background and scheduled task instructions for the agent.

## Monitoring Tasks

### Health Checks
- Check disk space on critical servers (warn at 80%)
- Monitor container status
- Verify backup completion

### Reporting
- Daily summary of server health
- Weekly resource usage trends
- Alert on anomalies

## Task Configuration

```yaml
tasks:
  disk_check:
    interval: 1h
    command: navig run "df -h"
    alert_threshold: 80%
    
  container_health:
    interval: 5m
    command: navig docker ps
    alert_on: exited, unhealthy
```

## Notes

Heartbeat tasks are optional and require additional setup.
See documentation for enabling automated monitoring.
""",
    }
    
    requested_workspace = workspace_path.expanduser()
    canonical_workspace = USER_WORKSPACE_DIR
    canonical_workspace.mkdir(parents=True, exist_ok=True)

    if requested_workspace.resolve() != canonical_workspace.resolve():
        if console:
            console.print(
                "[yellow]Workspace ownership policy:[/yellow] personal/state files are created in "
                f"[cyan]{canonical_workspace}[/cyan] only."
            )
            console.print(
                f"[dim]Requested path {requested_workspace} will not receive personal file copies.[/dim]"
            )

    duplicates = detect_project_workspace_duplicates(project_root=Path.cwd())
    if duplicates and console:
        summary = summarize_duplicates(duplicates)
        console.print(
            "[yellow]Detected project-level workspace duplicates.[/yellow] "
            "Using user-level files as source of truth."
        )
        console.print(
            "[dim]"
            f"conflicts={summary.get('duplicate_conflict', 0)}, "
            f"identical={summary.get('duplicate_identical', 0)}, "
            f"project_only={summary.get('project_only_legacy', 0)}"
            "[/dim]"
        )
    
    for filename, content in templates.items():
        file_path = canonical_workspace / filename
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")
            if console:
                console.print(f"  [green]✓[/green] Created {filename}")
    
    if console:
        console.print(f"\n[green]Workspace initialized at:[/green] {canonical_workspace}")


def save_config(config: Dict[str, Any], config_path: Path, console: ConsoleType) -> None:
    """Save configuration to JSON file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    
    if console:
        console.print(f"[green]✓ Configuration saved to:[/green] {config_path}")


def sync_to_env(config: Dict[str, Any], console: ConsoleType) -> None:
    """Sync configuration to .env file for the Telegram bot."""
    # Find .env in current directory or NAVIG project root
    env_paths = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent.parent / ".env",
    ]
    
    env_path = None
    for p in env_paths:
        if p.exists():
            env_path = p
            break
    
    if not env_path:
        env_path = env_paths[0]
    
    env_lines = []
    
    # Read existing .env if present
    if env_path.exists():
        env_lines = env_path.read_text(encoding="utf-8").splitlines()
    
    # Update/add values from config
    telegram_config = config.get("channels", {}).get("telegram", {})
    
    updates = {
        "NAVIG_AI_MODEL": config.get("agents", {}).get("defaults", {}).get("model", "openrouter"),
        "TYPING_MODE": config.get("agents", {}).get("defaults", {}).get("typing_mode", "instant"),
    }
    
    if telegram_config:
        if telegram_config.get("bot_token"):
            updates["TELEGRAM_BOT_TOKEN"] = telegram_config["bot_token"]
        if telegram_config.get("allowed_users"):
            updates["ALLOWED_TELEGRAM_USERS"] = ",".join(str(u) for u in telegram_config["allowed_users"])
    
    # Merge updates into env_lines
    for key, value in updates.items():
        found = False
        for i, line in enumerate(env_lines):
            if line.startswith(f"{key}="):
                env_lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            env_lines.append(f"{key}={value}")
    
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    
    if console:
        console.print(f"[green]✓ Environment synced to:[/green] {env_path}")


def run_onboard(flow: str = "quickstart", non_interactive: bool = False):
    """
    Run the NAVIG onboarding wizard.
    
    Args:
        flow: Onboarding flow - "quickstart" or "manual"
        non_interactive: Skip prompts and use defaults
    """
    console = get_console()
    
    if not console:
        print("Rich library not installed. Run: pip install rich")
        return
    
    print_banner(console)
    
    # Check if already configured
    if DEFAULT_CONFIG_FILE.exists():
        console.print(f"[yellow]⚠ Configuration already exists at {DEFAULT_CONFIG_FILE}[/yellow]")
        if not Confirm.ask("Overwrite existing configuration?", default=False):
            console.print("[dim]Onboarding cancelled.[/dim]")
            return
    
    # Non-interactive mode
    if non_interactive:
        console.print("[dim]Running in non-interactive mode with defaults...[/dim]")
        config = {
            "meta": {
                "version": "1.0.0",
                "created": datetime.now().isoformat(),
                "onboarding_flow": "non-interactive",
            },
            "agents": {
                "defaults": {
                    "workspace": str(DEFAULT_WORKSPACE_DIR),
                    "model": "openrouter",
                    "typing_mode": "instant",
                }
            },
            "auth": {"profiles": {}},
            "channels": {},
        }
    else:
        # Choose flow
        if flow == "auto":
            console.print("How would you like to set up NAVIG?")
            console.print("  [bold green]1. Quickstart[/bold green] - Get started quickly with sensible defaults")
            console.print("  [bold blue]2. Advanced[/bold blue] - Full configuration with all options")
            
            choice = Prompt.ask("Select setup mode", choices=["1", "2"], default="1")
            flow = "quickstart" if choice == "1" else "manual"
        
        # Run selected flow
        if flow == "quickstart":
            config = run_quickstart(console)
        else:
            config = run_manual(console)
    
    # Save configuration
    console.print("\n[bold]Saving configuration...[/bold]")
    save_config(config, DEFAULT_CONFIG_FILE, console)
    
    # Create workspace templates
    workspace_path = Path(config["agents"]["defaults"]["workspace"])
    console.print("\n[bold]Creating workspace templates...[/bold]")
    create_workspace_templates(workspace_path, console)
    
    # Sync to .env
    console.print("\n[bold]Syncing to environment...[/bold]")
    sync_to_env(config, console)
    
    # Summary
    console.print("\n" + "=" * 60)
    console.print("[bold green]✅ Onboarding Complete![/bold green]")
    console.print("=" * 60)
    
    summary = Table(title="Configuration Summary", show_header=False)
    summary.add_column("Setting", style="cyan")
    summary.add_column("Value", style="white")
    
    summary.add_row("Config File", str(DEFAULT_CONFIG_FILE))
    summary.add_row("Workspace", config["agents"]["defaults"]["workspace"])
    summary.add_row("AI Provider", config["agents"]["defaults"]["model"])
    summary.add_row("Typing Mode", config["agents"]["defaults"].get("typing_mode", "instant"))
    
    telegram_cfg = config.get("channels", {}).get("telegram", {})
    if telegram_cfg.get("enabled"):
        summary.add_row("Telegram", "Enabled ✓")
    
    console.print(summary)
    
    console.print("\n[bold]Next Steps:[/bold]")
    console.print("  1. Add a host: [cyan]navig host add myserver[/cyan]")
    console.print("  2. Configure AI: [cyan]navig ai providers --add openrouter[/cyan]")
    console.print("  3. Start the bot: [cyan]navig bot[/cyan]")
    console.print("\n[dim]Run 'navig help' for more commands[/dim]")


if __name__ == "__main__":
    import sys
    flow = sys.argv[1] if len(sys.argv) > 1 else "auto"
    run_onboard(flow=flow)
