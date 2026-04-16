"""
NAVIG Matrix CLI Commands

Provides the ``navig matrix`` command group for Matrix messaging operations:
  - login / logout / status / accounts / use
  - send / notice / read / tail
  - room create / join / leave / invite / members / topic
  - registration --enable / --disable / token CRUD
  - admin users / user
  - features
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from navig.comms.matrix_features import (
    FEATURE_DESCRIPTIONS,
    get_all_features,
    require_feature,
    require_matrix,
)
from navig.console_helper import get_console

logger = logging.getLogger(__name__)
console = get_console()

# ============================================================================
# App scaffold
# ============================================================================

matrix_app = typer.Typer(
    name="matrix",
    help="Matrix messaging operations (login, send, rooms, admin)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@matrix_app.callback()
def _matrix_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            print(ctx.get_help())
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("matrix", matrix_app)


# ============================================================================
# Helpers
# ============================================================================


def _get_config() -> dict:
    """Load comms.matrix config block."""
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().get_global_config()
        return cfg.get("comms", {}).get("matrix", {})
    except Exception:
        return {}


def _get_credential(profile: str = "default") -> dict:
    """Pull Matrix credential from the vault (if available)."""
    try:
        from navig.vault.core import CredentialsVault

        vault = CredentialsVault()
        creds = vault.list_by_provider("matrix")
        for c in creds:
            if c.profile_id == profile:
                full = vault.get(c.id)
                if full:
                    return full.data
        return {}
    except Exception:
        return {}


def _run_async(coro):
    """Helper to run an async function from sync Typer commands."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            while not future.done():
                try:
                    # Timeout allows main thread to yield and hook SIGINT
                    return future.result(timeout=0.1)
                except concurrent.futures.TimeoutError:
                    pass  # best-effort: operation timed out; skip
            return future.result()
    return asyncio.run(coro)


async def _get_bot(config: dict | None = None):
    """Get or create a NavigMatrixBot instance."""
    from navig.comms.matrix import NavigMatrixBot, get_matrix_bot

    existing = get_matrix_bot()
    if existing and existing.is_running:
        return existing

    cfg = config or _get_config()
    if not cfg.get("user_id"):
        console.print("[red]✗[/] No Matrix user_id configured.")
        console.print("  Set: [cyan]navig config set comms.matrix.user_id @bot:server[/]")
        raise typer.Exit(1)

    bot = NavigMatrixBot(cfg)
    await bot.start()
    return bot


# ============================================================================
# Authentication commands
# ============================================================================


@matrix_app.command("login")
@require_matrix()
def login(
    profile: Annotated[str, typer.Option("--profile", "-p", help="Credential profile")] = "default",
    token: Annotated[
        str, typer.Option("--token", "-t", help="Use access token instead of password")
    ] = "",
):
    """Authenticate with a Matrix homeserver."""
    cfg = _get_config()

    # Merge vault credential if available
    cred = _get_credential(profile)
    if cred:
        for k, v in cred.items():
            if v and k in (
                "homeserver_url",
                "homeserver",
                "user_id",
                "password",
                "access_token",
            ):
                mapped = k if k != "homeserver" else "homeserver_url"
                cfg.setdefault(mapped, v)

    if token:
        cfg["access_token"] = token

    async def _login():
        bot = await _get_bot(cfg)
        console.print(f"[green]✓[/] Logged in as [bold]{bot.cfg.user_id}[/]")
        console.print(f"  Homeserver: {bot.cfg.homeserver_url}")
        if bot._client and bot._client.access_token:
            console.print(f"  Token: {bot._client.access_token[:12]}...")
            # Persist token back to vault
            try:
                from navig.vault.core import CredentialsVault

                vault = CredentialsVault()
                creds = vault.list_by_provider("matrix")
                for c in creds:
                    if c.profile_id == profile:
                        full = vault.get(c.id)
                        if full:
                            full.data["access_token"] = bot._client.access_token
                            vault.update(full)
                            console.print("  [dim]Token saved to vault[/]")
                            break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    _run_async(_login())


@matrix_app.command("logout")
@require_matrix()
def logout():
    """End the current Matrix session."""

    async def _logout():
        from navig.comms.matrix import get_matrix_bot

        bot = get_matrix_bot()
        if bot and bot.is_running:
            await bot.stop()
            console.print("[green]✓[/] Logged out of Matrix")
        else:
            console.print("[yellow]![/] No active Matrix session")

    _run_async(_logout())


@matrix_app.command("status")
def status():
    """Show Matrix connection status and account info."""
    cfg = _get_config()
    enabled = cfg.get("enabled", False)

    table = Table(title="Matrix Status", show_header=False, border_style="dim")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("Enabled", "[green]yes[/]" if enabled else "[red]no[/]")
    table.add_row("Homeserver", cfg.get("homeserver_url", "[dim]not set[/]"))
    table.add_row("User ID", cfg.get("user_id", "[dim]not set[/]"))
    table.add_row("Default Room", cfg.get("default_room_id", "[dim]not set[/]"))
    table.add_row("Auto Join", "yes" if cfg.get("auto_join", True) else "no")
    table.add_row("E2EE", "yes" if cfg.get("e2ee", False) else "no")

    # Check live connection
    from navig.comms.matrix import get_matrix_bot

    bot = get_matrix_bot()
    if bot and bot.is_running:
        table.add_row("Connection", "[green]connected[/]")
    else:
        table.add_row("Connection", "[yellow]disconnected[/]")

    console.print(table)


@matrix_app.command("accounts")
def accounts():
    """List configured Matrix accounts from the vault."""
    try:
        from navig.vault.core import CredentialsVault

        vault = CredentialsVault()
        creds = vault.list_by_provider("matrix")
    except Exception:
        console.print("[yellow]![/] Vault not available or no Matrix credentials stored")
        return

    if not creds:
        console.print("[yellow]![/] No Matrix accounts in vault")
        console.print("  Add one: [cyan]navig vault add --provider matrix[/]")
        return

    # Determine "active" profile from config
    cfg = _get_config()
    active_id = cfg.get("credential_id", "")

    table = Table(title="Matrix Accounts")
    table.add_column("Profile", style="bold")
    table.add_column("User ID")
    table.add_column("Homeserver")
    table.add_column("Active", justify="center")

    for c in creds:
        is_active = "★" if (c.id == active_id or c.profile_id == active_id) else ""
        user_id = c.metadata.get("user_id", "[dim]—[/]")
        hs = c.metadata.get("homeserver_url", c.metadata.get("homeserver", "[dim]—[/]"))
        table.add_row(c.profile_id, user_id, hs, is_active)

    console.print(table)


@matrix_app.command("use")
def use_profile(
    profile: Annotated[str, typer.Argument(help="Credential profile name to activate")],
):
    """Switch the active Matrix account."""
    try:
        from navig.vault.core import CredentialsVault

        vault = CredentialsVault()
        creds = vault.list_by_provider("matrix")
        found = any(c.profile_id == profile for c in creds)
        if not found:
            console.print(f"[red]✗[/] Profile '{profile}' not found in vault")
            console.print("  Available: " + ", ".join(c.profile_id for c in creds))
            raise typer.Exit(1)
    except ImportError:
        pass  # optional dependency not installed; feature disabled

    try:
        from navig.config import get_config_manager

        _cm = get_config_manager()
        _cfg = _cm.get_global_config()
        _cfg.setdefault("comms", {}).setdefault("matrix", {})["credential_id"] = profile
        _cm.update_global_config(_cfg)
        console.print(f"[green]✓[/] Active Matrix profile → [bold]{profile}[/]")
    except Exception as e:
        console.print(f"[red]✗[/] Failed to set profile: {e}")
        raise typer.Exit(1) from e


# ============================================================================
# Messaging commands
# ============================================================================


@matrix_app.command("send")
@require_matrix()
@require_feature("messaging")
def send(
    room: Annotated[str, typer.Argument(help="Room ID or alias (omit for default)")] = "",
    message: Annotated[str, typer.Argument(help="Message text")] = "",
    stdin: Annotated[bool, typer.Option("--stdin", "-s", help="Read message from stdin")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="text | markdown | html")] = "text",
):
    """Send a text message to a Matrix room."""
    if stdin:
        message = sys.stdin.read().strip()
    if not message:
        console.print("[red]✗[/] No message provided")
        raise typer.Exit(1)

    cfg = _get_config()
    if not room:
        room = cfg.get("default_room_id", "")
    if not room:
        console.print("[red]✗[/] No room specified and no default_room_id configured")
        raise typer.Exit(1)

    async def _send():
        bot = await _get_bot()
        # Format conversion
        if format == "markdown":
            try:
                import re

                # Basic markdown → HTML (bold, italic, code)
                html = message
                html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
                html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
                html = re.sub(r"`(.+?)`", r"<code>\1</code>", html)
                result = await bot.send_message(room, message)
            except Exception:
                result = await bot.send_message(room, message)
        else:
            result = await bot.send_message(room, message)

        if result:
            console.print(f"[green]✓[/] Sent → {room}")
        else:
            console.print("[red]✗[/] Failed to send message")
            raise typer.Exit(1)

    _run_async(_send())


@matrix_app.command("notice")
@require_matrix()
@require_feature("messaging")
def notice(
    room: Annotated[str, typer.Argument(help="Room ID or alias")],
    message: Annotated[str, typer.Argument(help="Notice text")],
):
    """Send a notice (bot-style, no notification highlight)."""

    async def _notice():
        bot = await _get_bot()
        result = await bot.send_notice(room, message)
        if result:
            console.print(f"[green]✓[/] Notice sent → {room}")
        else:
            console.print("[red]✗[/] Failed to send notice")
            raise typer.Exit(1)

    _run_async(_notice())


@matrix_app.command("read")
@require_matrix()
@require_feature("messaging")
def read_messages(
    room: Annotated[str, typer.Argument(help="Room ID or alias")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of messages")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """Read recent messages from a room."""

    async def _read():
        bot = await _get_bot()
        messages = await bot.get_room_messages(room, limit=limit)

        if json_output:
            console.print_json(data=messages)
            return

        if not messages:
            console.print("[yellow]![/] No messages found")
            return

        table = Table(title=f"Messages — {room}")
        table.add_column("Time", style="dim", width=16)
        table.add_column("Sender", style="bold", width=24)
        table.add_column("Message")

        for msg in messages:
            ts = msg.get("timestamp", "")
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M %b %d")
            table.add_row(str(ts), msg.get("sender", "?"), msg.get("body", ""))

        console.print(table)

    _run_async(_read())


@matrix_app.command("tail")
@require_matrix()
@require_feature("messaging")
def tail(
    room: Annotated[str, typer.Argument(help="Room ID or alias to live-tail")] = "",
):
    """Live-tail messages from a room (Ctrl+C to stop)."""
    cfg = _get_config()
    if not room:
        room = cfg.get("default_room_id", "")
    if not room:
        console.print("[red]✗[/] No room specified")
        raise typer.Exit(1)

    async def _tail():
        bot = await _get_bot()

        async def _on_msg(room_id: str, sender: str, body: str):
            if room and room_id != room:
                return
            now = datetime.now(timezone.utc).strftime("%H:%M")
            console.print(f"[dim][{now}][/] [bold]{sender}[/]: {body}")

        bot.on_message(_on_msg)
        console.print(f"[dim]Tailing {room} — Ctrl+C to stop...[/]")

        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[dim]Stopped tailing[/]")

    try:
        _run_async(_tail())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped[/]")


# ============================================================================
# Room management commands
# ============================================================================

room_app = typer.Typer(name="room", help="Room management (create, join, leave, invite)")
matrix_app.add_typer(room_app)


@matrix_app.command("rooms")
@require_matrix()
@require_feature("room_management")
def list_rooms(
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """List joined Matrix rooms."""

    async def _rooms():
        bot = await _get_bot()
        rooms = await bot.get_rooms()

        if json_output:
            console.print_json(data=rooms)
            return

        if not rooms:
            console.print("[yellow]![/] No joined rooms")
            return

        table = Table(title="Joined Rooms")
        table.add_column("Room ID", style="bold")
        table.add_column("Name")
        table.add_column("Members", justify="right")
        table.add_column("Topic")

        for r in rooms:
            table.add_row(
                r.get("room_id", "?"),
                r.get("name", "[dim]—[/]"),
                str(r.get("member_count", "?")),
                r.get("topic", "[dim]—[/]"),
            )

        console.print(table)

    _run_async(_rooms())


@room_app.command("create")
@require_matrix()
@require_feature("room_management")
def room_create(
    name: Annotated[str, typer.Argument(help="Room name")],
    topic: Annotated[str, typer.Option("--topic", "-t", help="Room topic")] = "",
    public: Annotated[bool, typer.Option("--public", help="Create as public room")] = False,
):
    """Create a new Matrix room."""

    async def _create():
        bot = await _get_bot()
        room_id = await bot.create_room(name, topic=topic, is_public=public)
        if room_id:
            console.print(f"[green]✓[/] Room created: [bold]{room_id}[/]")
        else:
            console.print("[red]✗[/] Failed to create room")
            raise typer.Exit(1)

    _run_async(_create())


@room_app.command("join")
@require_matrix()
@require_feature("room_management")
def room_join(
    room_id: Annotated[str, typer.Argument(help="Room ID or alias to join")],
):
    """Join a Matrix room."""

    async def _join():
        bot = await _get_bot()
        if not bot._client:
            console.print("[red]✗[/] Not connected")
            raise typer.Exit(1)
        try:
            await bot._client.join(room_id)
            console.print(f"[green]✓[/] Joined {room_id}")
        except Exception as e:
            console.print(f"[red]✗[/] Failed to join: {e}")
            raise typer.Exit(1) from e

    _run_async(_join())


@room_app.command("leave")
@require_matrix()
@require_feature("room_management")
def room_leave(
    room_id: Annotated[str, typer.Argument(help="Room ID to leave")],
):
    """Leave a Matrix room."""

    async def _leave():
        bot = await _get_bot()
        if not bot._client:
            console.print("[red]✗[/] Not connected")
            raise typer.Exit(1)
        try:
            await bot._client.room_leave(room_id)
            console.print(f"[green]✓[/] Left {room_id}")
        except Exception as e:
            console.print(f"[red]✗[/] Failed to leave: {e}")
            raise typer.Exit(1) from e

    _run_async(_leave())


@room_app.command("invite")
@require_matrix()
@require_feature("room_management")
def room_invite(
    room_id: Annotated[str, typer.Argument(help="Room ID")],
    user_id: Annotated[str, typer.Argument(help="User Matrix ID (e.g. @alice:server)")],
):
    """Invite a user to a Matrix room."""

    async def _invite():
        bot = await _get_bot()
        ok = await bot.invite_user(room_id, user_id)
        if ok:
            console.print(f"[green]✓[/] Invited {user_id} → {room_id}")
        else:
            console.print("[red]✗[/] Failed to invite")
            raise typer.Exit(1)

    _run_async(_invite())


@room_app.command("members")
@require_matrix()
@require_feature("room_management")
def room_members(
    room_id: Annotated[str, typer.Argument(help="Room ID")],
):
    """List members of a Matrix room."""

    async def _members():
        bot = await _get_bot()
        members = await bot.get_room_members(room_id)

        if not members:
            console.print("[yellow]![/] No members found or not joined")
            return

        table = Table(title=f"Members — {room_id}")
        table.add_column("User ID", style="bold")
        table.add_column("Display Name")
        table.add_column("Power Level", justify="right")

        for m in members:
            table.add_row(
                m.get("user_id", "?"),
                m.get("display_name", "[dim]—[/]"),
                str(m.get("power_level", 0)),
            )

        console.print(table)

    _run_async(_members())


@room_app.command("topic")
@require_matrix()
@require_feature("room_management")
def room_topic(
    room_id: Annotated[str, typer.Argument(help="Room ID")],
    topic: Annotated[str, typer.Argument(help="New topic text")],
):
    """Set the topic for a Matrix room."""

    async def _topic():
        bot = await _get_bot()
        if not bot._client:
            console.print("[red]✗[/] Not connected")
            raise typer.Exit(1)
        try:
            await bot._client.room_put_state(
                room_id,
                "m.room.topic",
                {"topic": topic},
            )
            console.print(f"[green]✓[/] Topic set for {room_id}")
        except Exception as e:
            console.print(f"[red]✗[/] Failed: {e}")
            raise typer.Exit(1) from e

    _run_async(_topic())


# ============================================================================
# Registration commands (admin)
# ============================================================================

registration_app = typer.Typer(
    name="registration",
    help="Homeserver registration controls (admin)",
    invoke_without_command=True,
)
matrix_app.add_typer(registration_app)

token_app = typer.Typer(name="token", help="Invite token management")
registration_app.add_typer(token_app)


@registration_app.callback(invoke_without_command=True)
@require_matrix()
@require_feature("registration_control")
def registration_callback(
    ctx: typer.Context,
    enable: Annotated[bool, typer.Option("--enable", help="Enable open registration")] = False,
    disable: Annotated[bool, typer.Option("--disable", help="Disable open registration")] = False,
):
    """Check or toggle homeserver registration state."""
    if ctx.invoked_subcommand is not None:
        return

    if not enable and not disable:
        # Display current status
        async def _check():
            from navig.comms.matrix_admin import get_admin_client

            admin = get_admin_client()
            reg_status = await admin.get_registration_status()
            state = "[green]OPEN[/]" if reg_status else "[yellow]CLOSED (invite-only)[/]"
            console.print(f"Registration: {state}")

        _run_async(_check())
        return

    if enable and disable:
        console.print("[red]✗[/] Cannot use --enable and --disable together")
        raise typer.Exit(1)

    async def _toggle():
        from navig.comms.matrix_admin import get_admin_client

        admin = get_admin_client()
        if enable:
            ok = await admin.set_registration(True)
            if ok:
                console.print("[green]✓[/] Registration enabled — anyone can create accounts")
            else:
                console.print("[red]✗[/] Failed to enable registration")
        else:
            ok = await admin.set_registration(False)
            if ok:
                console.print("[green]✓[/] Registration disabled — invite-only mode")
            else:
                console.print("[red]✗[/] Failed to disable registration")

    _run_async(_toggle())


@token_app.command("create")
@require_matrix()
@require_feature("registration_control")
def token_create(
    uses: Annotated[int, typer.Option("--uses", "-n", help="Max uses (0 = unlimited)")] = 1,
    expiry: Annotated[
        str, typer.Option("--expiry", "-e", help="Expiry duration (e.g. 7d, 30d)")
    ] = "7d",
):
    """Create an invite registration token."""

    async def _create():
        from navig.comms.matrix_admin import get_admin_client

        admin = get_admin_client()
        token = await admin.create_registration_token(uses_allowed=uses, expiry=expiry)
        if token:
            console.print(f"[green]✓[/] Token: [bold]{token}[/]")
            console.print(f"  Uses: {uses if uses else 'unlimited'}, Expiry: {expiry}")
        else:
            console.print("[red]✗[/] Failed to create token")

    _run_async(_create())


@token_app.command("list")
@require_matrix()
@require_feature("registration_control")
def token_list():
    """List active registration tokens."""

    async def _list():
        from navig.comms.matrix_admin import get_admin_client

        admin = get_admin_client()
        tokens = await admin.list_registration_tokens()

        if not tokens:
            console.print("[yellow]![/] No active tokens")
            return

        table = Table(title="Registration Tokens")
        table.add_column("Token", style="bold")
        table.add_column("Uses", justify="right")
        table.add_column("Remaining", justify="right")
        table.add_column("Expires")

        for t in tokens:
            table.add_row(
                t.get("token", "?"),
                str(t.get("uses", 0)),
                str(t.get("remaining", "∞")),
                t.get("expires", "[dim]never[/]"),
            )

        console.print(table)

    _run_async(_list())


@token_app.command("revoke")
@require_matrix()
@require_feature("registration_control")
def token_revoke(
    token: Annotated[str, typer.Argument(help="Token to revoke")],
):
    """Revoke a registration token."""

    async def _revoke():
        from navig.comms.matrix_admin import get_admin_client

        admin = get_admin_client()
        ok = await admin.revoke_registration_token(token)
        if ok:
            console.print(f"[green]✓[/] Token revoked: {token}")
        else:
            console.print("[red]✗[/] Failed to revoke token")

    _run_async(_revoke())


# ============================================================================
# Admin commands
# ============================================================================

admin_app = typer.Typer(name="admin", help="Matrix server administration")
matrix_app.add_typer(admin_app)


@admin_app.command("users")
@require_matrix()
@require_feature("admin_ops")
def admin_users():
    """List registered users on the homeserver."""

    async def _users():
        from navig.comms.matrix_admin import get_admin_client

        admin = get_admin_client()
        users = await admin.list_users()

        if not users:
            console.print("[yellow]![/] No users found")
            return

        table = Table(title="Matrix Users")
        table.add_column("User ID", style="bold")
        table.add_column("Admin", justify="center")
        table.add_column("Created")

        for u in users:
            is_admin = "[green]yes[/]" if u.get("admin", False) else "no"
            table.add_row(
                u.get("user_id", "?"),
                is_admin,
                u.get("created_at", "[dim]—[/]"),
            )

        console.print(table)

    _run_async(_users())


@admin_app.command("user")
@require_matrix()
@require_feature("admin_ops")
def admin_user(
    mxid: Annotated[str, typer.Argument(help="Matrix user ID (e.g. @alice:server)")],
    deactivate: Annotated[bool, typer.Option("--deactivate", help="Deactivate user")] = False,
    reset_password: Annotated[
        bool, typer.Option("--reset-password", help="Reset password")
    ] = False,
):
    """Manage a specific user on the homeserver."""
    if not deactivate and not reset_password:
        # Show user info
        async def _info():
            from navig.comms.matrix_admin import get_admin_client

            admin = get_admin_client()
            info = await admin.get_user(mxid)
            if info:
                table = Table(title=f"User: {mxid}", show_header=False)
                table.add_column("Key", style="bold")
                table.add_column("Value")
                for k, v in info.items():
                    table.add_row(str(k), str(v))
                console.print(table)
            else:
                console.print(f"[red]✗[/] User not found: {mxid}")

        _run_async(_info())
        return

    if deactivate:
        if not typer.confirm(f"Deactivate {mxid}? This cannot be undone."):
            raise typer.Abort()

        async def _deactivate():
            from navig.comms.matrix_admin import get_admin_client

            admin = get_admin_client()
            ok = await admin.deactivate_user(mxid)
            if ok:
                console.print(f"[green]✓[/] User deactivated: {mxid}")
            else:
                console.print("[red]✗[/] Failed to deactivate")

        _run_async(_deactivate())

    if reset_password:
        import secrets

        new_pass = secrets.token_urlsafe(16)

        async def _reset():
            from navig.comms.matrix_admin import get_admin_client

            admin = get_admin_client()
            ok = await admin.reset_password(mxid, new_pass)
            if ok:
                console.print(f"[green]✓[/] Password reset for {mxid}")
                console.print(f"  New password: [bold]{new_pass}[/]")
            else:
                console.print("[red]✗[/] Failed to reset password")

        _run_async(_reset())


# ============================================================================
# Features command
# ============================================================================


@matrix_app.command("features")
def features():
    """Show Matrix feature toggle states."""
    table = Table(title="Matrix Features")
    table.add_column("Feature", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Description")

    for name, enabled in get_all_features().items():
        status = "[green]✓ ON[/]" if enabled else "[red]✗ OFF[/]"
        desc = FEATURE_DESCRIPTIONS.get(name, "")
        table.add_row(name, status, desc)

    console.print(table)
    console.print("\n[dim]Toggle: navig config set comms.matrix.features.<name> true|false[/]")


# ============================================================================
# Inbox bridge commands
# ============================================================================

inbox_bridge_app = typer.Typer(
    name="inbox",
    help="Matrix inbox bridge — persist and manage Matrix messages as inbox files",
)
matrix_app.add_typer(inbox_bridge_app, name="inbox")


@inbox_bridge_app.command("list")
@require_feature("notifications")
def inbox_list(
    status: Annotated[
        str | None, typer.Option("--status", "-s", help="Filter: unread|read")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max messages")] = 30,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """List persisted Matrix inbox messages."""
    from navig.comms.matrix_inbox import get_inbox_bridge

    bridge = get_inbox_bridge()
    msgs = bridge.list_messages(status=status, limit=limit)

    if json_output:
        import json as _json

        typer.echo(_json.dumps(msgs, indent=2, default=str))
        return

    if not msgs:
        console.print("[dim]No Matrix inbox messages[/]")
        return

    table = Table(title=f"Matrix Inbox ({len(msgs)} messages)")
    table.add_column("#", style="dim", width=3)
    table.add_column("Status", width=7)
    table.add_column("Sender", style="cyan", width=24)
    table.add_column("Room", width=20)
    table.add_column("Preview", no_wrap=False)
    table.add_column("Time", style="dim", width=19)

    for i, m in enumerate(msgs, 1):
        st = "[green]●[/]" if m["status"] == "unread" else "[dim]○[/]"
        table.add_row(str(i), st, m["sender"], m["room_name"], m["preview"], m["created"])

    console.print(table)


@inbox_bridge_app.command("unread")
@require_feature("notifications")
def inbox_unread():
    """Show unread count."""
    from navig.comms.matrix_inbox import get_inbox_bridge

    bridge = get_inbox_bridge()
    count = bridge.get_unread_count()
    if count == 0:
        console.print("[green]✓[/] No unread Matrix messages")
    else:
        console.print(f"[yellow]{count}[/] unread Matrix message(s)")


@inbox_bridge_app.command("mark-read")
@require_feature("notifications")
def inbox_mark_read(
    filename: Annotated[str | None, typer.Argument(help="Specific file, or omit for all")] = None,
):
    """Mark messages as read (one or all)."""
    from navig.comms.matrix_inbox import get_inbox_bridge

    bridge = get_inbox_bridge()
    if filename:
        ok = bridge.mark_read(filename)
        if ok:
            console.print(f"[green]✓[/] Marked {filename} as read")
        else:
            console.print(f"[red]✗[/] File not found: {filename}")
    else:
        count = bridge.mark_all_read()
        console.print(f"[green]✓[/] Marked {count} message(s) as read")


@inbox_bridge_app.command("purge")
@require_feature("notifications")
def inbox_purge(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
):
    """Delete all read messages from inbox."""
    from navig.comms.matrix_inbox import get_inbox_bridge

    bridge = get_inbox_bridge()
    if not yes:
        count = len(bridge.list_messages(status="read"))
        if count == 0:
            console.print("[dim]No read messages to purge[/]")
            return
        confirm = typer.confirm(f"Delete {count} read message(s)?")
        if not confirm:
            raise typer.Abort()

    deleted = bridge.purge_read()
    console.print(f"[green]✓[/] Deleted {deleted} read message(s)")


@inbox_bridge_app.command("process")
@require_feature("notifications")
def inbox_process(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview routing")] = False,
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Heuristic only")] = False,
):
    """Route unread Matrix messages through InboxRouterAgent."""
    from navig.comms.matrix_inbox import get_inbox_bridge

    bridge = get_inbox_bridge()
    msgs = bridge.list_messages(status="unread")
    if not msgs:
        console.print("[dim]No unread messages to process[/]")
        return

    try:
        from navig.agents.inbox_router import InboxRouterAgent, execute_plan
    except ImportError as _exc:
        console.print("[red]✗[/] InboxRouterAgent not available")
        raise typer.Exit(1) from _exc

    agent = InboxRouterAgent(bridge.project_root, use_llm=not no_llm)
    console.print(f"Processing {len(msgs)} unread message(s)...\n")

    for m in msgs:
        fp = Path(m["path"])
        if not fp.exists():
            continue
        plan = agent.process_single(fp, dry_run=dry_run)
        ctype = plan.get("content_type", "?")
        target = plan.get("target_path") or "(stays in inbox)"
        console.print(f"  [{ctype}] {fp.name} -> {target}")
        if not dry_run and not plan.get("error"):
            execute_plan(bridge.project_root, plan, dry_run=False, move_source=True)

    console.print("\n[green]✓[/] Done")


# ============================================================================
# File sharing commands
# ============================================================================

file_app = typer.Typer(
    name="file",
    help="Matrix file sharing — upload and download files",
)
matrix_app.add_typer(file_app, name="file")


@file_app.command("upload")
@require_feature("file_sharing")
def file_upload(
    path: Annotated[str, typer.Argument(help="Local file to upload")],
    room: Annotated[str | None, typer.Option("--room", "-r", help="Target room ID")] = None,
    name: Annotated[str | None, typer.Option("--name", help="Display name")] = None,
):
    """Upload a file to a Matrix room."""
    from pathlib import Path as _P

    fp = _P(path)
    if not fp.exists():
        console.print(f"[red]✗[/] File not found: {path}")
        raise typer.Exit(1)

    room_id = room or _get_config().get("default_room_id", "")
    if not room_id:
        console.print("[red]✗[/] No room specified (pass --room or set default_room_id)")
        raise typer.Exit(1)

    async def _upload():
        bot = await _get_bot()
        if not bot:
            console.print("[red]✗[/] Could not connect to Matrix")
            raise typer.Exit(1)
        try:
            eid = await bot.upload_file(room_id, str(fp), body=name)
            if eid:
                console.print(f"[green]✓[/] Uploaded {fp.name} → {room_id}")
                console.print(f"  Event: [dim]{eid}[/]")
            else:
                console.print("[red]✗[/] Upload failed")
        finally:
            await bot.stop()

    _run_async(_upload())


@file_app.command("download")
@require_feature("file_sharing")
def file_download(
    mxc_uri: Annotated[str, typer.Argument(help="Matrix content URI (mxc://...)")],
    dest: Annotated[str, typer.Option("--output", "-o", help="Destination path")] = ".",
):
    """Download a file from a Matrix content URI."""
    from pathlib import Path as _P

    if not mxc_uri.startswith("mxc://"):
        console.print("[red]✗[/] Invalid MXC URI (must start with mxc://)")
        raise typer.Exit(1)

    # If dest is a directory, use it; otherwise treat as file
    dest_path = _P(dest)
    if dest_path.is_dir():
        # Extract filename from URI
        parts = mxc_uri.split("/")
        fname = parts[-1] if len(parts) > 1 else "download"
        dest_path = dest_path / fname

    async def _download():
        bot = await _get_bot()
        if not bot:
            console.print("[red]✗[/] Could not connect to Matrix")
            raise typer.Exit(1)
        try:
            ok = await bot.download_file(mxc_uri, str(dest_path))
            if ok:
                console.print(f"[green]✓[/] Downloaded → {dest_path}")
            else:
                console.print("[red]✗[/] Download failed")
        finally:
            await bot.stop()

    _run_async(_download())


# ============================================================================
# E2EE commands  (Phase 3)
# ============================================================================

e2ee_app = typer.Typer(
    name="e2ee",
    help="End-to-end encryption: verify, trust, keys",
    no_args_is_help=True,
)
matrix_app.add_typer(e2ee_app, name="e2ee")


@e2ee_app.command("status")
@require_feature("e2ee")
def e2ee_status():
    """Show E2EE diagnostic status."""
    from navig.comms.matrix import is_e2ee_available

    async def _status():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)
            info = await mgr.e2ee_status()
        finally:
            await bot.stop()

        table = Table(title="E2EE Status", show_header=False)
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        for k, v in info.items():
            table.add_row(k, str(v))
        console.print(table)

    if not is_e2ee_available():
        console.print("[yellow]⚠[/] libolm not installed — E2EE unavailable")
        console.print("  Install: [cyan]pip install matrix-nio[e2e][/]")
        return

    _run_async(_status())


@e2ee_app.command("devices")
@require_feature("e2ee")
def e2ee_devices(
    user_id: Annotated[str | None, typer.Argument(help="User ID (omit for own devices)")] = None,
):
    """List devices and their trust state."""

    async def _devices():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)
            if user_id:
                devices = await mgr.list_devices(user_id)
            else:
                devices = await mgr.list_own_devices()
        finally:
            await bot.stop()

        if not devices:
            console.print("[yellow]No devices found[/]")
            return

        table = Table(title=f"Devices: {user_id or 'self'}")
        table.add_column("Device ID", style="cyan")
        table.add_column("Name")
        table.add_column("Key (ed25519)", style="dim")
        table.add_column("Trust", style="bold")
        for d in devices:
            trust_color = {
                "verified": "green",
                "blacklisted": "red",
                "unset": "yellow",
            }.get(d.trust.value, "white")
            table.add_row(
                d.device_id,
                d.display_name,
                d.short_key(),
                f"[{trust_color}]{d.trust.value}[/]",
            )
        console.print(table)

    _run_async(_devices())


@e2ee_app.command("trust")
@require_feature("e2ee")
def e2ee_trust(
    user_id: Annotated[str, typer.Argument(help="User ID (@user:server)")],
    device_id: Annotated[str, typer.Argument(help="Device ID")],
):
    """Manually trust a device (skip SAS verification)."""

    async def _trust():
        bot = await _get_bot()
        try:
            ok = await bot.trust_device(user_id, device_id)
        finally:
            await bot.stop()

        if ok:
            console.print(f"[green]✓[/] Trusted {device_id} ({user_id})")
        else:
            console.print("[red]✗[/] Failed to trust device")
            raise typer.Exit(1)

    _run_async(_trust())


@e2ee_app.command("blacklist")
@require_feature("e2ee")
def e2ee_blacklist(
    user_id: Annotated[str, typer.Argument(help="User ID (@user:server)")],
    device_id: Annotated[str, typer.Argument(help="Device ID")],
):
    """Blacklist a device (do not send keys to it)."""

    async def _blacklist():
        bot = await _get_bot()
        try:
            ok = await bot.blacklist_device(user_id, device_id)
        finally:
            await bot.stop()

        if ok:
            console.print(f"[green]✓[/] Blacklisted {device_id} ({user_id})")
        else:
            console.print("[red]✗[/] Failed to blacklist device")
            raise typer.Exit(1)

    _run_async(_blacklist())


@e2ee_app.command("unverify")
@require_feature("e2ee")
def e2ee_unverify(
    user_id: Annotated[str, typer.Argument(help="User ID (@user:server)")],
    device_id: Annotated[str, typer.Argument(help="Device ID")],
):
    """Remove trust from a device."""

    async def _unverify():
        bot = await _get_bot()
        try:
            ok = await bot.unverify_device(user_id, device_id)
        finally:
            await bot.stop()

        if ok:
            console.print(f"[green]✓[/] Unverified {device_id} ({user_id})")
        else:
            console.print("[red]✗[/] Failed to unverify device")
            raise typer.Exit(1)

    _run_async(_unverify())


@e2ee_app.command("trust-all")
@require_feature("e2ee")
def e2ee_trust_all(
    user_id: Annotated[str, typer.Argument(help="User ID to trust all devices for")],
):
    """Trust ALL known devices for a user."""

    async def _trust_all():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)
            count = await mgr.trust_all_devices(user_id)
        finally:
            await bot.stop()

        console.print(f"[green]✓[/] Trusted {count} devices for {user_id}")

    _run_async(_trust_all())


@e2ee_app.command("verify")
@require_feature("e2ee")
def e2ee_verify(
    user_id: Annotated[str, typer.Argument(help="User ID (@user:server)")],
    device_id: Annotated[str, typer.Argument(help="Device ID to verify")],
):
    """Start interactive SAS (emoji) verification with a device."""

    async def _verify():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)

            console.print(f"Starting SAS verification with {user_id}/{device_id}...")
            session = await mgr.start_verification(user_id, device_id)
            if not session:
                console.print("[red]✗[/] Could not start verification")
                raise typer.Exit(1)

            console.print(f"  Transaction: [dim]{session.transaction_id}[/]")
            console.print("[yellow]Waiting for other side to accept...[/]")

            # Poll for emoji (simplified — in a real interactive flow
            # the to-device callback would deliver them)
            for _ in range(30):  # 30s timeout
                emoji = await mgr.get_emoji(session.transaction_id)
                if emoji:
                    console.print("\n[bold]Verify these emoji match on both devices:[/]\n")
                    emoji_line = "  ".join(f"{e} ({d})" for e, d in emoji)
                    console.print(f"  {emoji_line}\n")

                    confirm = typer.confirm("Do the emoji match?")
                    if confirm:
                        ok = await mgr.confirm_verification(session.transaction_id)
                        if ok:
                            console.print("[green]✓[/] Verification confirmed!")
                        else:
                            console.print("[red]✗[/] Confirmation failed")
                    else:
                        await mgr.cancel_verification(session.transaction_id)
                        console.print("[yellow]Verification cancelled[/]")
                    return

                await asyncio.sleep(1)

            console.print("[yellow]⚠[/] Timeout waiting for verification response")
            await mgr.cancel_verification(session.transaction_id)
        finally:
            await bot.stop()

    _run_async(_verify())


@e2ee_app.command("keys")
@require_feature("e2ee")
def e2ee_keys():
    """Show the bot's own device keys (for cross-verification)."""

    async def _keys():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)
            info = await mgr.e2ee_status()
        finally:
            await bot.stop()

        console.print("[bold]Bot Device Keys[/]\n")
        console.print(f"  Device ID:  [cyan]{info.get('device_id', '?')}[/]")
        console.print(f"  User:       {info.get('user_id', '?')}")
        console.print(f"  Ed25519:    [green]{info.get('ed25519', 'N/A')}[/]")
        console.print(f"  Curve25519: [green]{info.get('curve25519', 'N/A')}[/]")
        console.print("\n  Share these keys with other users for manual verification.")

    _run_async(_keys())


@e2ee_app.command("export-keys")
@require_feature("e2ee")
def e2ee_export_keys(
    path: Annotated[str, typer.Argument(help="Output file path")],
    passphrase: Annotated[str, typer.Option("--passphrase", "-p", prompt=True, hide_input=True)],
):
    """Export E2EE room keys to a file (encrypted)."""

    async def _export():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)
            ok = await mgr.export_keys(path, passphrase)
        finally:
            await bot.stop()

        if ok:
            console.print(f"[green]✓[/] Keys exported to {path}")
        else:
            console.print("[red]✗[/] Export failed")
            raise typer.Exit(1)

    _run_async(_export())


@e2ee_app.command("import-keys")
@require_feature("e2ee")
def e2ee_import_keys(
    path: Annotated[str, typer.Argument(help="Key file path")],
    passphrase: Annotated[str, typer.Option("--passphrase", "-p", prompt=True, hide_input=True)],
):
    """Import E2EE room keys from a file."""
    from pathlib import Path as _P

    if not _P(path).exists():
        console.print(f"[red]✗[/] File not found: {path}")
        raise typer.Exit(1)

    async def _import():
        bot = await _get_bot()
        try:
            from navig.comms.matrix_e2ee import MatrixE2EEManager

            mgr = MatrixE2EEManager(bot)
            ok = await mgr.import_keys(path, passphrase)
        finally:
            await bot.stop()

        if ok:
            console.print(f"[green]✓[/] Keys imported from {path}")
        else:
            console.print("[red]✗[/] Import failed")
            raise typer.Exit(1)

    _run_async(_import())


# ============================================================================
# Store subcommand group  (Phase 4)
# ============================================================================

store_app = typer.Typer(
    name="store",
    help="Persistent Matrix store — stats, bridges, events",
)
matrix_app.add_typer(store_app, name="store")


@store_app.command("stats")
def store_stats():
    """Show persistent store statistics."""
    import os

    from navig.comms.matrix_store import MatrixStore

    db_path = os.path.expanduser("~/.navig/matrix.db")
    if not os.path.exists(db_path):
        console.print("[yellow]⚠[/] Store not initialised yet (no matrix.db)")
        raise typer.Exit(0)

    store = MatrixStore(db_path)
    try:
        s = store.stats()
    finally:
        store.close()

    table = Table(title="Matrix Store")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")
    for k, v in s.items():
        table.add_row(k, str(v))
    console.print(table)


@store_app.command("rooms")
def store_rooms(
    purpose: Annotated[
        str | None, typer.Option("--purpose", "-p", help="Filter by purpose")
    ] = None,
):
    """List rooms in the persistent store."""
    import os

    from navig.comms.matrix_store import MatrixStore

    db_path = os.path.expanduser("~/.navig/matrix.db")
    if not os.path.exists(db_path):
        console.print("[yellow]⚠[/] Store not initialised")
        raise typer.Exit(0)

    store = MatrixStore(db_path)
    try:
        rooms = store.list_rooms(purpose=purpose)
    finally:
        store.close()

    if not rooms:
        console.print("[dim]No rooms in store[/]")
        return

    table = Table(title="Stored Rooms")
    table.add_column("Room ID", style="cyan", max_width=40)
    table.add_column("Name", style="green")
    table.add_column("Purpose", style="yellow")
    table.add_column("Encrypted", style="magenta")
    table.add_column("Joined", style="dim")
    for r in rooms:
        table.add_row(
            r.room_id,
            r.name or "(unnamed)",
            r.purpose,
            "🔒" if r.encrypted else "—",
            r.joined_at[:10] if r.joined_at else "",
        )
    console.print(table)


@store_app.command("events")
def store_events(
    room_id: Annotated[str, typer.Argument(help="Room ID")],
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
):
    """Show recent events for a room from the persistent store."""
    import os

    from navig.comms.matrix_store import MatrixStore

    db_path = os.path.expanduser("~/.navig/matrix.db")
    if not os.path.exists(db_path):
        console.print("[yellow]⚠[/] Store not initialised")
        raise typer.Exit(0)

    store = MatrixStore(db_path)
    try:
        events = store.get_events(room_id, limit=limit)
    finally:
        store.close()

    if not events:
        console.print("[dim]No events stored for this room[/]")
        return

    table = Table(title=f"Events: {room_id[:30]}…")
    table.add_column("Event ID", style="dim", max_width=30)
    table.add_column("Sender", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Body", max_width=50)
    for e in events:
        body = e.content.get("body", "") if e.content else ""
        table.add_row(
            e.event_id[:28] + "…" if len(e.event_id) > 28 else e.event_id,
            e.sender,
            e.event_type,
            body[:50],
        )
    console.print(table)


@store_app.command("prune")
def store_prune(
    max_rows: Annotated[int, typer.Option("--max", "-m", help="Max events to keep")] = 10000,
):
    """Prune old events from the store."""
    import os

    from navig.comms.matrix_store import MatrixStore

    db_path = os.path.expanduser("~/.navig/matrix.db")
    if not os.path.exists(db_path):
        console.print("[yellow]⚠[/] Store not initialised")
        raise typer.Exit(0)

    store = MatrixStore(db_path)
    try:
        before = store.count_events()
        store.prune_events(max_rows=max_rows)
        after = store.count_events()
    finally:
        store.close()

    pruned = before - after
    if pruned > 0:
        console.print(f"[green]✓[/] Pruned {pruned} events ({before} → {after})")
    else:
        console.print(f"[dim]No pruning needed ({after} events, max {max_rows})[/]")


@store_app.command("bridges")
def store_bridges(
    room_id: Annotated[str | None, typer.Argument(help="Room ID (optional)")] = None,
):
    """List bridge configurations in the store."""
    import os

    from navig.comms.matrix_store import MatrixStore

    db_path = os.path.expanduser("~/.navig/matrix.db")
    if not os.path.exists(db_path):
        console.print("[yellow]⚠[/] Store not initialised")
        raise typer.Exit(0)

    store = MatrixStore(db_path)
    try:
        bridges = store.get_bridges(room_id=room_id)
    finally:
        store.close()

    if not bridges:
        console.print("[dim]No bridges configured[/]")
        return

    table = Table(title="Bridges")
    table.add_column("ID", style="dim")
    table.add_column("Room ID", style="cyan", max_width=35)
    table.add_column("Type", style="yellow")
    table.add_column("Config", style="green", max_width=40)
    table.add_column("Active", style="cyan")
    for b in bridges:
        table.add_row(
            str(b.id),
            b.room_id[:33] + "…" if len(b.room_id) > 33 else b.room_id,
            b.bridge_type,
            str(b.config),
            "✓" if b.active else "✗",
        )
    console.print(table)
