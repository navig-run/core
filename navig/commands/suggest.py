"""Intelligent Command Suggestions based on history, context, and patterns.

Analyzes:
- Command history frequency and recency
- Current context (host, app, directory)
- Time-of-day patterns
- Command sequences (what usually follows what)
- Project type detection
"""

import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


# Common command patterns by context
CONTEXT_PATTERNS = {
    "docker": [
        ("navig docker ps", "List running containers"),
        ("navig docker logs {container}", "View container logs"),
        ("navig docker restart {container}", "Restart container"),
        ("navig docker compose up -d", "Start all services"),
    ],
    "database": [
        ("navig db list", "List databases"),
        ("navig db tables", "Show tables"),
        ("navig db query 'SELECT 1'", "Test database connection"),
        ("navig db dump", "Backup database"),
    ],
    "deployment": [
        ("navig run 'git pull'", "Pull latest code"),
        ("navig run 'systemctl restart {service}'", "Restart service"),
        ("navig file list /var/www", "Check deployment files"),
        ("navig log show /var/log/syslog", "View system logs"),
    ],
    "monitoring": [
        ("navig dashboard", "Open operations dashboard"),
        ("navig host test", "Test SSH connection"),
        ("navig run 'df -h'", "Check disk space"),
        ("navig run 'free -h'", "Check memory usage"),
    ],
    "file_operations": [
        ("navig file list", "List remote files"),
        ("navig upload {local} {remote}", "Upload file"),
        ("navig download {remote} {local}", "Download file"),
        ("navig file edit {path}", "Edit remote file"),
    ],
}

# Time-based suggestions
TIME_PATTERNS = {
    "morning": [  # 6-10 AM
        ("navig dashboard", "Start with dashboard overview"),
        ("navig host test", "Verify connectivity"),
        ("navig history --since 24h", "Review yesterday's operations"),
    ],
    "workday": [  # 10 AM - 6 PM
        ("navig run '{cmd}'", "Execute remote command"),
        ("navig db query '{sql}'", "Run database query"),
        ("navig docker ps", "Check containers"),
    ],
    "evening": [  # 6-10 PM
        ("navig db dump", "Backup databases"),
        ("navig history export", "Export today's audit log"),
        ("navig run 'logrotate -f /etc/logrotate.conf'", "Rotate logs"),
    ],
}


def get_time_period() -> str:
    """Get current time period for time-based suggestions."""
    hour = datetime.now().hour
    if 6 <= hour < 10:
        return "morning"
    elif 10 <= hour < 18:
        return "workday"
    elif 18 <= hour < 22:
        return "evening"
    return "workday"  # default


def detect_project_context() -> list[str]:
    """Detect project context from current directory."""
    contexts = []
    cwd = Path.cwd()

    # Check for Docker
    if (cwd / "docker-compose.yml").exists() or (cwd / "docker-compose.yaml").exists():
        contexts.append("docker")
    if (cwd / "Dockerfile").exists():
        contexts.append("docker")

    # Check for database projects
    if any(cwd.glob("*.sql")) or (cwd / "migrations").exists():
        contexts.append("database")

    # Check for deployment configs
    if (cwd / "deploy").exists() or (cwd / ".deploy").exists():
        contexts.append("deployment")
    if (cwd / "ansible").exists() or any(cwd.glob("*.yml")):
        contexts.append("deployment")

    # Check for monitoring
    if (cwd / "prometheus.yml").exists() or (cwd / "grafana").exists():
        contexts.append("monitoring")

    return contexts or ["monitoring"]  # default to monitoring


def get_frequent_commands(limit: int = 10) -> list[tuple[str, int]]:
    """Get most frequently used commands from history."""
    try:
        from navig.operation_recorder import get_operation_recorder

        recorder = get_operation_recorder()

        # Get recent operations
        operations = recorder.get_last_n(100)

        # Count command frequencies
        cmd_counts = Counter()
        for op in operations:
            # Normalize command (remove specific values)
            cmd = op.command
            if cmd:
                # Keep the command structure, normalize values
                normalized = re.sub(r"'[^']*'", "'{value}'", cmd)
                normalized = re.sub(r'"[^"]*"', '"{value}"', normalized)
                cmd_counts[normalized] += 1

        return cmd_counts.most_common(limit)
    except Exception:
        return []


def get_recent_commands(limit: int = 5) -> list[str]:
    """Get most recent commands from history."""
    try:
        from navig.operation_recorder import get_operation_recorder

        recorder = get_operation_recorder()
        operations = recorder.get_last_n(limit)
        return [op.command for op in operations if op.command]
    except Exception:
        return []


def get_command_sequences() -> dict[str, list[str]]:
    """Analyze what commands typically follow other commands."""
    try:
        from navig.operation_recorder import get_operation_recorder

        recorder = get_operation_recorder()
        operations = recorder.get_last_n(200)

        sequences = defaultdict(list)
        for i in range(len(operations) - 1):
            current = operations[i].command
            next_cmd = operations[i + 1].command
            if current and next_cmd:
                # Extract command type (first two words)
                current_type = " ".join(current.split()[:2])
                sequences[current_type].append(next_cmd)

        # Return most common follow-up for each command type
        result = {}
        for cmd_type, follows in sequences.items():
            if follows:
                most_common = Counter(follows).most_common(1)
                if most_common:
                    result[cmd_type] = most_common[0][0]

        return result
    except Exception:
        return {}


def generate_suggestions(
    context_filter: str | None = None,
    limit: int = 8,
    include_patterns: bool = True,
) -> list[dict[str, Any]]:
    """Generate intelligent command suggestions.

    Args:
        context_filter: Specific context to filter by (docker, database, etc.)
        limit: Maximum number of suggestions
        include_patterns: Include pattern-based suggestions

    Returns:
        List of suggestion dicts with 'command', 'description', 'source', 'score'
    """
    suggestions = []

    # 1. Frequent commands (highest weight)
    frequent = get_frequent_commands(5)
    for cmd, count in frequent:
        suggestions.append(
            {
                "command": cmd,
                "description": f"Used {count} times recently",
                "source": "history",
                "score": 100 + count,
            }
        )

    # 2. Context-based suggestions
    if context_filter:
        contexts = [context_filter]
    else:
        contexts = detect_project_context()

    if include_patterns:
        for ctx in contexts:
            if ctx in CONTEXT_PATTERNS:
                for cmd, desc in CONTEXT_PATTERNS[ctx][:3]:
                    suggestions.append(
                        {
                            "command": cmd,
                            "description": desc,
                            "source": f"context:{ctx}",
                            "score": 50,
                        }
                    )

    # 3. Time-based suggestions
    time_period = get_time_period()
    if include_patterns and time_period in TIME_PATTERNS:
        for cmd, desc in TIME_PATTERNS[time_period][:2]:
            suggestions.append(
                {
                    "command": cmd,
                    "description": f"{desc} (typical for {time_period})",
                    "source": "time",
                    "score": 30,
                }
            )

    # 4. Sequence-based suggestions (what usually follows recent command)
    recent = get_recent_commands(1)
    if recent:
        sequences = get_command_sequences()
        last_cmd_type = " ".join(recent[0].split()[:2])
        if last_cmd_type in sequences:
            follow_cmd = sequences[last_cmd_type]
            suggestions.append(
                {
                    "command": follow_cmd,
                    "description": f"Usually follows '{last_cmd_type}'",
                    "source": "sequence",
                    "score": 70,
                }
            )

    # Sort by score and dedupe
    seen = set()
    unique = []
    for s in sorted(suggestions, key=lambda x: -x["score"]):
        cmd_key = s["command"].lower()
        if cmd_key not in seen:
            seen.add(cmd_key)
            unique.append(s)

    return unique[:limit]


def show_suggestions(
    context: str | None = None,
    limit: int = 8,
    plain: bool = False,
    json_out: bool = False,
    opts: dict[str, Any] | None = None,
) -> None:
    """Display intelligent command suggestions.

    Args:
        context: Filter by context (docker, database, deployment, monitoring)
        limit: Maximum suggestions to show
        plain: Plain text output
        json_out: JSON output
        opts: CLI options dict
    """
    suggestions = generate_suggestions(context_filter=context, limit=limit)

    if json_out:
        import json

        console.print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "suggest",
                    "suggestions": suggestions,
                },
                indent=2,
            )
        )
        return

    if plain:
        for s in suggestions:
            console.print(f"{s['command']}\t{s['description']}")
        return

    # Rich formatted output
    if not suggestions:
        console.print(
            "[dim]No suggestions available. Run some commands to build history.[/dim]"
        )
        return

    table = Table(
        title="[bold cyan]Suggested Commands[/bold cyan]",
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Command", style="bold green")
    table.add_column("Description", style="dim")
    table.add_column("Source", style="cyan", width=12)

    for i, s in enumerate(suggestions, 1):
        source_icon = {
            "history": "[green]H[/green]",
            "sequence": "[yellow]S[/yellow]",
            "time": "[blue]T[/blue]",
        }.get(s["source"], "[dim]P[/dim]")

        if s["source"].startswith("context:"):
            source_icon = "[magenta]C[/magenta]"

        table.add_row(
            str(i),
            s["command"],
            s["description"],
            f"{source_icon} {s['source']}",
        )

    console.print(table)
    console.print()
    console.print(
        "[dim]Legend: [green]H[/green]=History [yellow]S[/yellow]=Sequence [blue]T[/blue]=Time [magenta]C[/magenta]=Context[/dim]"
    )
    console.print("[dim]Run a suggestion with: navig suggest --run 1[/dim]")


def run_suggestion(index: int, dry_run: bool = False) -> bool:
    """Run a suggested command by index.

    Args:
        index: 1-based index of suggestion to run
        dry_run: If True, just show the command without executing

    Returns:
        True if command was executed/shown successfully
    """
    suggestions = generate_suggestions(limit=10)

    if index < 1 or index > len(suggestions):
        console.print(
            f"[red]Invalid suggestion index. Choose 1-{len(suggestions)}[/red]"
        )
        return False

    suggestion = suggestions[index - 1]
    cmd = suggestion["command"]

    if "{" in cmd:
        console.print(
            "[yellow]This command has placeholders that need values:[/yellow]"
        )
        console.print(f"  {cmd}")
        console.print("[dim]Fill in the values and run manually.[/dim]")
        return False

    if dry_run:
        console.print(f"[dim]Would run:[/dim] {cmd}")
        return True

    console.print(f"[cyan]Running:[/cyan] {cmd}")
    console.print()

    # Execute using subprocess to preserve the full command
    import subprocess
    import sys

    try:
        result = subprocess.run(
            [sys.executable, "-m", "navig"] + cmd.replace("navig ", "").split(),
            cwd=Path.cwd(),
        )
        return result.returncode == 0
    except Exception as e:
        console.print(f"[red]Error executing command: {e}[/red]")
        return False


def add_quick_action(name: str, command: str, description: str = "") -> bool:
    """Add a quick action shortcut.

    Args:
        name: Short name for the action (e.g., 'deploy', 'backup')
        command: Full navig command to execute
        description: Optional description

    Returns:
        True if action was added successfully
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    config_dir = Path(config_manager.global_config_dir)
    quick_file = config_dir / "quick_actions.yaml"

    # Load existing actions
    actions = {}
    if quick_file.exists():
        import yaml

        with open(quick_file) as f:
            actions = yaml.safe_load(f) or {}

    # Add new action
    actions[name] = {
        "command": command,
        "description": description,
        "created": datetime.now().isoformat(),
    }

    # Save
    import yaml

    with open(quick_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(actions, f, default_flow_style=False)

    console.print(f"[green]Added quick action:[/green] {name} -> {command}")
    return True


def list_quick_actions() -> list[dict[str, Any]]:
    """List all quick actions."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    config_dir = Path(config_manager.global_config_dir)
    quick_file = config_dir / "quick_actions.yaml"

    if not quick_file.exists():
        return []

    import yaml

    with open(quick_file) as f:
        actions = yaml.safe_load(f) or {}

    return [{"name": name, **data} for name, data in actions.items()]


def run_quick_action(name: str, dry_run: bool = False) -> bool:
    """Run a quick action by name.

    Args:
        name: Name of the quick action
        dry_run: If True, just show the command

    Returns:
        True if successful
    """
    actions = {a["name"]: a for a in list_quick_actions()}

    if name not in actions:
        console.print(f"[red]Quick action '{name}' not found.[/red]")
        available = ", ".join(actions.keys()) if actions else "none"
        console.print(f"[dim]Available: {available}[/dim]")
        return False

    action = actions[name]
    cmd = action["command"]

    if dry_run:
        console.print(f"[dim]Would run:[/dim] {cmd}")
        return True

    console.print(f"[cyan]Running quick action '{name}':[/cyan] {cmd}")
    console.print()

    import subprocess
    import sys

    try:
        # Parse and execute
        parts = cmd.replace("navig ", "").split()
        result = subprocess.run(
            [sys.executable, "-m", "navig"] + parts,
            cwd=Path.cwd(),
        )
        return result.returncode == 0
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return False


def show_quick_actions(plain: bool = False, json_out: bool = False) -> None:
    """Display all quick actions."""
    actions = list_quick_actions()

    if json_out:
        import json

        console.print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "quick-list",
                    "actions": actions,
                },
                indent=2,
            )
        )
        return

    if plain:
        for a in actions:
            console.print(f"{a['name']}\t{a['command']}")
        return

    if not actions:
        console.print("[dim]No quick actions defined.[/dim]")
        console.print("[dim]Add one with: navig quick add <name> <command>[/dim]")
        return

    table = Table(
        title="[bold cyan]Quick Actions[/bold cyan]",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Name", style="bold green")
    table.add_column("Command", style="dim")
    table.add_column("Description")

    for a in actions:
        table.add_row(
            a["name"],
            a["command"],
            a.get("description", ""),
        )

    console.print(table)
    console.print()
    console.print("[dim]Run with: navig quick <name>[/dim]")
