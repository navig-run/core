"""
Telegram management commands.

Provides:
- session management
- status checks
- direct send/resolve helper for gateway smoke tests
"""

import json
import logging
from datetime import datetime
from typing import Any

import typer

from navig import console_helper as ch

logger = logging.getLogger(__name__)

telegram_app = typer.Typer(help="Telegram bot management")
sessions_app = typer.Typer(help="Session management")
telegram_app.add_typer(sessions_app, name="sessions")

# Attach the MTProto user-account "Telegram Manager" commands (login/dialogs/history/
# search/move/forward/dedupe/rename/links) onto the same `telegram` command group.
from navig.commands._telegram_mtproto import register as _register_mtproto  # noqa: E402

_register_mtproto(telegram_app)


def _load_telegram_token() -> str:
    from navig.messaging.secrets import resolve_telegram_bot_token

    token = resolve_telegram_bot_token()
    if not token:
        raise RuntimeError("Telegram bot token missing. Configure with: navig init")
    return token


def _api_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    import requests

    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "Telegram API call failed"))
    return data


def _resolve_chat_id(token: str, target: str) -> int:
    raw = target.strip()
    if not raw:
        raise RuntimeError("Empty target")

    # Direct numeric chat id
    if raw.lstrip("-").isdigit():
        return int(raw)

    # Best-effort @username lookup from recent updates
    if raw.startswith("@"):
        import requests

        wanted = raw.lstrip("@").lower()
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        response = requests.get(url, params={"limit": 100}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError("Failed to fetch updates for username resolution")

        for item in data.get("result", []):
            message = item.get("message") or item.get("edited_message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("username") or "").lower() == wanted:
                chat_id = chat.get("id")
                if isinstance(chat_id, int):
                    return chat_id

            sender = message.get("from") or {}
            if str(sender.get("username") or "").lower() == wanted:
                chat_id = chat.get("id")
                if isinstance(chat_id, int):
                    return chat_id

        raise RuntimeError(
            "Could not resolve @username from recent updates. Ask the user to message the bot first or use numeric chat_id."
        )

    raise RuntimeError("Target must be numeric chat_id or @username")


def telegram_send(
    *,
    target: str,
    message: str,
    parse_mode: str = "Markdown",
    resolve_only: bool = False,
    host: str = "",
) -> int:
    """Compatibility send API used by gateway smoke tests.

    Returns resolved chat_id.
    """
    del host  # legacy compatibility argument
    token = _load_telegram_token()
    chat_id = _resolve_chat_id(token, target)

    if resolve_only:
        ch.info(f"Resolved target {target} -> {chat_id}")
        return chat_id

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    _api_call(token, "sendMessage", payload)
    return chat_id


@telegram_app.command("send")
def send_message(
    target: str = typer.Argument(..., help="Target chat id or @username"),
    message: str = typer.Option(..., "--message", "-m", help="Message to send"),
    parse_mode: str = typer.Option("Markdown", "--parse-mode", help="Telegram parse mode"),
    resolve_only: bool = typer.Option(False, "--resolve-only", help="Resolve target without sending"),
):
    """Send a Telegram message using the configured bot token."""
    try:
        chat_id = telegram_send(
            target=target,
            message=message,
            parse_mode=parse_mode,
            resolve_only=resolve_only,
        )
    except RuntimeError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001
        ch.error(f"Telegram send failed: {exc}")
        raise typer.Exit(1) from None

    if resolve_only:
        ch.success(f"Resolved: {target} -> {chat_id}")
    else:
        ch.success(f"Message sent to {chat_id}")


def _session_manager(*, timeout_days: int | None = None):
    """Return the Telegram session manager, or exit non-zero saying why.

    Five commands carried this import guard verbatim, and every one printed
    "Session management not available" and exited 0 — so a script could not tell
    "no sessions" from "the module that manages them is missing".
    """
    try:
        from navig.gateway.channels.telegram_sessions import (
            SessionManager,
            get_session_manager,
        )
    except ImportError as exc:
        ch.error("Cannot manage sessions: the Telegram session module is unavailable")
        ch.info("  This build may be missing the gateway channels package.")
        raise typer.Exit(1) from exc

    if timeout_days is not None:
        return SessionManager(session_timeout_days=timeout_days)
    return get_session_manager()


def _require_session(manager, session_key: str):
    """Return the named session, or exit non-zero.

    The lookup loop was copy-pasted, and both copies reported "Session not found"
    at exit 0 — the same shape `navig vault disable` had.
    """
    for session in manager.list_sessions():
        if session.session_key == session_key:
            return session
    ch.error(f"Session not found: {session_key}")
    ch.info("  List them with: navig telegram sessions list")
    raise typer.Exit(1)


@sessions_app.command("list")
def list_sessions(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all active Telegram sessions."""
    manager = _session_manager()
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
    manager = _session_manager()
    session = _require_session(manager, session_key)

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
    manager = _session_manager()
    session = _require_session(manager, session_key)

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
    manager = _session_manager()

    if not force:
        confirm = typer.confirm(f"Delete session {session_key}? This cannot be undone.")
        if not confirm:
            return

    if not manager.delete_session(session_key):
        # It used to print "✓ Session deleted" for a key that never existed —
        # the manager no-ops silently on an unknown key.
        ch.error(f"Session not found: {session_key}")
        ch.info("  List them with: navig telegram sessions list")
        raise typer.Exit(1)
    ch.success(f"Session deleted: {session_key}")


@sessions_app.command("prune")
def prune_sessions(
    days: int = typer.Option(7, "--days", "-d", help="Days of inactivity threshold"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove inactive sessions."""
    manager = _session_manager(timeout_days=days)
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
    from navig.messaging.secrets import resolve_telegram_bot_token, resolve_telegram_uid

    cm = get_config_manager()
    config = cm._load_global_config()

    tg_config = config.get("telegram", {})

    ch.info("Telegram Bot Status")
    ch.console.print()

    # Use the full resolution chain: vault → env → ~/.navig/.env → config.yaml
    token = resolve_telegram_bot_token(config)
    if token:
        # Show a masked hint so the user can verify it's the right token
        hint = token[:6] + "..." + token[-4:] if len(token) > 12 else "***"
        ch.console.print(f"  [green]✓[/green] Bot token configured ({hint})")
    else:
        ch.console.print("  [red]✗[/red] Bot token missing")
        ch.dim("    Configure with: navig init")
        return

    uid = resolve_telegram_uid(config)
    if uid:
        ch.console.print(f"  [green]✓[/green] Owner UID configured ({uid})")
    else:
        ch.console.print("  [dim]○ Owner UID not set[/dim]")
        ch.dim("    Configure with: navig vault set telegram.user_id <your-uid>")

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
    except Exception as exc:  # noqa: BLE001
        logger.debug("Telegram session status unavailable: %s", exc)

    ch.console.print()
    ch.info("Start bot with: navig gateway start")


# ── Extensions: switch bot features on and off ───────────────────────────────
# Scoped to `navig telegram` rather than `navig store` on purpose: the store hub
# lists ~46 INSTALLABLES (plugins, skills, MCP, connectors) and "what does my bot
# do" is a different question at a different scope. `navig modules` is deprecated
# in favour of the store, so it is not the home either. Same underlying key
# (`modules.overrides`) as every other surface, so nothing can diverge.

extensions_app = typer.Typer(help="Turn Telegram bot features on or off")
telegram_app.add_typer(extensions_app, name="extensions")


def _ext_resolve(name: str):
    """Resolve id, label or an owned command name to an extension.

    Accepting all three is not a nicety: ids carry a ``tg:`` prefix, and someone
    reading "Habits" in the table will type ``habits``.
    """
    from navig.gateway.channels import telegram_extensions as tx

    needle = name.strip().lower().lstrip("/")
    ext = tx.get(needle)
    if ext is not None:
        return ext
    for candidate in tx.all_extensions():
        if candidate.label.lower() == needle or needle in candidate.commands:
            return candidate
    return None


def _ext_not_found(name: str) -> None:
    from navig.gateway.channels import telegram_extensions as tx

    ch.error(f"No extension matches '{name}'.")
    ch.dim("Known: " + ", ".join(e.id for e in tx.all_extensions()))
    raise typer.Exit(1)


@extensions_app.command("list")
def telegram_extensions_list(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show every Telegram extension and whether it is on."""
    from navig.gateway.channels.telegram_extensions import list_extensions

    payload = list_extensions()
    if as_json:
        ch.emit_json(payload)
        return

    rows = payload["extensions"]
    counts = payload["counts"]

    table = ch.Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Extension", style="bold", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Commands", style="dim", no_wrap=True)
    # Exactly one wrappable column, so a narrow terminal degrades here instead of
    # truncating the short columns that carry the answer.
    table.add_column("What it does")

    for ext in rows:
        if ext["enabled"]:
            state = "[green]● on[/green]"
        else:
            state = "[dim]○ off[/dim]"
        if ext.get("running") is False:
            state = "[yellow]◐ all commands off[/yellow]"
        # Two, not three: at the real 120-col PowerShell width a third command
        # pushed the one wrappable column down to ~29 chars and every
        # description wrapped to three lines.
        cmds = ext["commands"]
        shown = " ".join(f"/{c}" for c in cmds[:2])
        if len(cmds) > 2:
            shown += f"  +{len(cmds) - 2}"
        table.add_row(
            f"{ext['label']}  [dim]{ext['key']}[/dim]",
            state,
            shown or "[dim]—[/dim]",
            ext["description"],
        )

    ch.console.print(table)
    ch.console.print()
    first_off = next((e["key"] for e in rows if not e["enabled"]), None)
    verb = "enable" if first_off else "disable"
    example = first_off or (rows[0]["key"] if rows else "habits")
    ch.dim(
        f"{counts['on']} of {counts['total']} on  ·  flip one: "
        f"navig telegram extensions {verb} {example}  ·  on your phone: /extensions"
    )


@extensions_app.command("enable")
def telegram_extensions_enable(
    name: str = typer.Argument(..., help="Extension id, label or one of its commands"),
) -> None:
    """Switch a Telegram extension on."""
    _ext_set(name, True)


@extensions_app.command("disable")
def telegram_extensions_disable(
    name: str = typer.Argument(..., help="Extension id, label or one of its commands"),
) -> None:
    """Switch a Telegram extension off."""
    _ext_set(name, False)


def _ext_apply_via_daemon(module_id: str, enabled: bool) -> bool:
    """Toggle through the running daemon so the change is LIVE. False if it isn't up.

    `navig.core.Config` loads once per process, so a write from this CLI would not
    reach a daemon that is already running — its in-memory config never re-reads.
    Handing the toggle to the daemon instead makes it take effect immediately AND
    lets it re-publish the "/" autocomplete. Same resolution order as
    `navig.scheduler.habit_store` (gateway HTTP while the daemon runs → local
    write when it does not); returns False on ANY failure so the caller falls back.
    """
    try:
        import json as _json
        import urllib.request
        from pathlib import Path

        from navig.platform.paths import config_dir

        gw = Path(config_dir()) / "gateway.json"
        if not gw.exists():
            return False
        base = str(_json.loads(gw.read_text(encoding="utf-8")).get("url", "")).rstrip("/")
        if not base:
            return False
        req = urllib.request.Request(
            f"{base}/api/deck/social/telegram/extensions",
            data=_json.dumps({"id": module_id, "enabled": enabled}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:  # noqa: S310 — loopback URL from our own handshake file
            body = _json.loads(resp.read().decode("utf-8"))
        return bool(isinstance(body, dict) and body.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _ext_set(name: str, enabled: bool) -> None:
    from navig.modules.registry import get_registry

    ext = _ext_resolve(name)
    if ext is None:
        _ext_not_found(name)
        return

    live = _ext_apply_via_daemon(ext.module_id, enabled)
    if not live and not get_registry().set_enabled(ext.module_id, enabled):
        ch.error(f"Could not change {ext.label}.")
        raise typer.Exit(1)

    if enabled:
        ch.success(f"{ext.label} on")
        if ext.commands:
            ch.dim("  " + " ".join(f"/{c}" for c in sorted(ext.commands)) + " are back.")
    else:
        ch.success(f"{ext.label} off")
        if ext.commands:
            ch.dim(
                "  " + " ".join(f"/{c}" for c in sorted(ext.commands))
                + " are hidden from the bot."
            )
        for line in ext.about:
            ch.dim(f"  {line}")
    ch.dim(
        f"  Back on: navig telegram extensions {'disable' if enabled else 'enable'} "
        f"{ext.id}  ·  on your phone: /extensions"
    )
    # Say WHICH happened. "Applied" and "will apply on restart" are different
    # facts, and reporting the wrong one is how a toggle looks broken.
    if live:
        ch.dim("  Applied to the running bot now.")
    else:
        ch.dim("  Saved. The bot picks it up when the daemon next starts.")


@extensions_app.command("info")
def telegram_extensions_info(
    name: str = typer.Argument(..., help="Extension id, label or one of its commands"),
) -> None:
    """Show what one extension owns and what switching it off does."""
    from navig.gateway.channels.telegram_extensions import is_enabled

    ext = _ext_resolve(name)
    if ext is None:
        _ext_not_found(name)
        return

    on = is_enabled(ext.id)
    ch.info(f"{ext.label}  ({ext.module_id})")
    ch.console.print(f"  {ext.description}")
    ch.console.print()
    ch.console.print("  State      " + ("[green]● on[/green]" if on else "[dim]○ off[/dim]"))
    if ext.commands:
        ch.console.print("  Commands   " + " ".join(f"/{c}" for c in sorted(ext.commands)))
    if ext.callback_prefixes:
        ch.console.print("  Buttons    " + " ".join(ext.callback_prefixes))
    if ext.reply_actions:
        ch.console.print("  Keywords   " + " ".join(sorted(ext.reply_actions)))
    if ext.about:
        ch.console.print()
        ch.console.print("  Switching it off")
        for line in ext.about:
            ch.console.print(f"    · {line}")
