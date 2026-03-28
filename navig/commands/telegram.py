"""
Telegram Session Management Commands

CLI commands for managing Telegram bot sessions.
"""

import json
from datetime import datetime

import typer

from navig import console_helper as ch

telegram_app = typer.Typer(help="Telegram bot management")
sessions_app = typer.Typer(help="Session management")
telegram_app.add_typer(sessions_app, name="sessions")


@sessions_app.command("list")
def list_sessions(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all active Telegram sessions."""
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager
    except ImportError:
        ch.error("Session management not available")
        return

    manager = get_session_manager()
    sessions = manager.list_sessions()

    if json_output:
        data = [s.to_dict() for s in sessions]
        ch.raw_print(json.dumps(data, indent=2, default=str))
        return

    if not sessions:
        ch.info("No active sessions")
        return

    table = ch.Table(title="Telegram Sessions")
    table.add_column("Type", style="cyan")
    table.add_column("User/Group", style="yellow")
    table.add_column("Username", style="green")
    table.add_column("Messages", style="magenta")
    table.add_column("Last Active", style="white")

    for session in sessions:
        session_type = "Group" if session.is_group else "DM"
        identifier = str(session.chat_id if session.is_group else session.user_id)

        try:
            last = datetime.fromisoformat(session.last_active)
            last_str = last.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_str = session.last_active[:16]

        table.add_row(
            session_type,
            identifier,
            session.username or "-",
            str(session.message_count),
            last_str,
        )

    ch.console.print(table)
    ch.dim(f"\nTotal: {len(sessions)} sessions")


@sessions_app.command("show")
def show_session(
    session_key: str = typer.Argument(..., help="Session key (e.g., telegram:user:123)"),
    messages: int = typer.Option(10, "--messages", "-n", help="Number of messages to show"),
):
    """Show details of a specific session."""
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager
    except ImportError:
        ch.error("Session management not available")
        return

    manager = get_session_manager()

    # Find session
    session = None
    for s in manager.list_sessions():
        if s.session_key == session_key:
            session = s
            break

    if not session:
        ch.error(f"Session not found: {session_key}")
        return

    ch.info(f"Session: {session.session_key}")
    ch.console.print()
    ch.console.print(f"  User ID: {session.user_id}")
    ch.console.print(f"  Chat ID: {session.chat_id}")
    ch.console.print(f"  Username: {session.username or '-'}")
    ch.console.print(f"  Type: {'Group' if session.is_group else 'DM'}")
    ch.console.print(f"  Created: {session.created_at[:16]}")
    ch.console.print(f"  Last Active: {session.last_active[:16]}")
    ch.console.print(f"  Message Count: {session.message_count}")
    ch.console.print()

    # Show recent messages
    if session.messages:
        ch.info(f"Recent Messages (last {messages}):")
        for msg in session.messages[-messages:]:
            role_icon = "👤" if msg.role == "user" else "🤖"
            content = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            ch.console.print(f"  {role_icon} {content}")


@sessions_app.command("clear")
def clear_session(
    session_key: str = typer.Argument(..., help="Session key to clear"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Clear a session's message history."""
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager
    except ImportError:
        ch.error("Session management not available")
        return

    manager = get_session_manager()

    # Find session to get details
    session = None
    for s in manager.list_sessions():
        if s.session_key == session_key:
            session = s
            break

    if not session:
        ch.error(f"Session not found: {session_key}")
        return

    if not force:
        confirm = typer.confirm(
            f"Clear {session.message_count} messages from {session.session_key}?"
        )
        if not confirm:
            return

    manager.clear_session(session.chat_id, session.user_id, session.is_group)
    ch.success(f"Session cleared: {session_key}")


@sessions_app.command("delete")
def delete_session(
    session_key: str = typer.Argument(..., help="Session key to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a session completely."""
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager
    except ImportError:
        ch.error("Session management not available")
        return

    manager = get_session_manager()

    if not force:
        confirm = typer.confirm(f"Delete session {session_key}? This cannot be undone.")
        if not confirm:
            return

    manager.delete_session(session_key)
    ch.success(f"Session deleted: {session_key}")


@sessions_app.command("prune")
def prune_sessions(
    days: int = typer.Option(7, "--days", "-d", help="Days of inactivity threshold"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove inactive sessions."""
    try:
        from navig.gateway.channels.telegram_sessions import SessionManager
    except ImportError:
        ch.error("Session management not available")
        return

    manager = SessionManager(session_timeout_days=days)
    sessions = manager.list_sessions()

    # Count sessions that would be pruned
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)
    to_prune = []

    for s in sessions:
        try:
            last = datetime.fromisoformat(s.last_active)
            if last < cutoff:
                to_prune.append(s)
        except Exception:  # noqa: BLE001
            # In cases of deeply corrupted or legacy metadata, forcefully
            # prune the session to prevent permanent retention leaks.
            to_prune.append(s)

    if not to_prune:
        ch.info(f"No sessions inactive for more than {days} days")
        return

    ch.info(f"Found {len(to_prune)} inactive sessions:")
    for s in to_prune[:5]:
        ch.console.print(f"  - {s.session_key} (last: {s.last_active[:10]})")
    if len(to_prune) > 5:
        ch.console.print(f"  ... and {len(to_prune) - 5} more")

    if not force:
        confirm = typer.confirm("Delete these sessions?")
        if not confirm:
            return

    removed = manager.prune_inactive()
    ch.success(f"Removed {removed} inactive sessions")


@telegram_app.command("status")
def telegram_status():
    """Show Telegram bot status."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    config = cm._load_global_config()

    tg_config = config.get("telegram", {})

    ch.info("Telegram Bot Status")
    ch.console.print()

    if tg_config.get("bot_token"):
        ch.console.print("  [green]✓[/green] Bot token configured")
    else:
        ch.console.print("  [red]✗[/red] Bot token missing")
        ch.dim("    Configure with: navig init")
        return

    allowed = tg_config.get("allowed_users", [])
    ch.console.print(f"  Users: {len(allowed)} allowed")

    groups = tg_config.get("allowed_groups", [])
    ch.console.print(f"  Groups: {len(groups)} allowed")

    # Session stats
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager

        manager = get_session_manager()
        sessions = manager.list_sessions()
        ch.console.print(f"  Sessions: {len(sessions)} active")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    ch.console.print()
    ch.info("Start bot with: navig gateway start")
