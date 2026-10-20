"""``navig discord`` — the Discord account manager (sibling of ``navig telegram``).

Two identities (see ``navig.discord``):

* ``--as bot``     official Bot API — full management of servers the bot is in.
* ``--as account`` your own user token (opt-in) — list, read and leave YOUR servers.

``--as auto`` (default) picks the account for listing/leaving when you are logged in,
and the bot for everything that manages a server.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import typer

from navig import console_helper as ch

if TYPE_CHECKING:
    from navig.discord.api import DiscordClient, Guild

discord_app = typer.Typer(
    help="Discord account manager — your servers, channels, roles and members",
    no_args_is_help=True,
)
bot_app = typer.Typer(help="The NAVIG Discord bot: set its token, add it to a server", no_args_is_help=True)
channel_app = typer.Typer(help="Create, edit and delete channels (bot)", no_args_is_help=True)
role_app = typer.Typer(help="Create, edit, delete, give and take roles (bot)", no_args_is_help=True)
mod_app = typer.Typer(help="Moderate members: kick, ban, unban, timeout (bot)", no_args_is_help=True)
discord_app.add_typer(bot_app, name="bot")
discord_app.add_typer(channel_app, name="channel")
discord_app.add_typer(role_app, name="role")
discord_app.add_typer(mod_app, name="mod")

_AS_HELP = "Identity: auto · bot · account"

_RISK_TEXT = (
    "Automating a Discord [bold]user account[/bold] is against Discord's Terms of Service.\n"
    "Discord can disable an account it detects doing it.\n\n"
    "NAVIG keeps this mode narrow to limit that risk: it lists, reads and leaves\n"
    "[bold]your own[/bold] servers. It never lists other people and never joins by API —\n"
    "joining opens the invite in your browser.\n\n"
    "For managing servers you run, the bot is the safe route: [cyan]navig discord bot set[/cyan]"
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _fail(message: str, hint: str | None = None) -> typer.Exit:
    ch.error(message)
    if hint:
        ch.dim(f"  → {hint}")
    return typer.Exit(1)


def _api_fail(exc: Exception) -> typer.Exit:
    from navig.discord.api import DiscordAPIError

    if isinstance(exc, DiscordAPIError):
        return _fail(str(exc), exc.hint)
    return _fail(str(exc))


def _interactive() -> bool:
    return sys.stdin.isatty()


def _pick(as_: str, *, prefer: str) -> str:
    """Resolve ``--as`` to a concrete identity that has a token."""
    from navig.discord import config

    as_ = (as_ or "auto").strip().lower()
    if as_ not in ("auto", "bot", "account"):
        raise _fail(f"--as must be auto, bot or account (got {as_!r})")
    if as_ != "auto":
        if not config.have_token(as_):  # type: ignore[arg-type]
            raise _fail(f"No {as_} token stored.", _setup_hint(as_))
        return as_
    order = [prefer, "bot" if prefer == "account" else "account"]
    for identity in order:
        if config.have_token(identity):  # type: ignore[arg-type]
            return identity
    reason = config.vault_unavailable_reason()
    if reason:
        raise _fail(f"Cannot open the vault: {reason}")
    raise _fail(
        "Discord is not set up yet.",
        "add the bot: navig discord bot set   ·   or your account: navig discord login",
    )


def _setup_hint(identity: str) -> str:
    return "navig discord bot set" if identity == "bot" else "navig discord login"


def _client(identity: str) -> DiscordClient:
    from navig.discord import config
    from navig.discord.api import DiscordClient

    return DiscordClient(identity, config.get_token(identity))  # type: ignore[arg-type]


def _bot() -> DiscordClient:
    return _client(_pick("bot", prefer="bot"))


def _guild(client: DiscordClient, spec: str) -> Guild:
    from navig.discord.api import resolve_guild

    try:
        return resolve_guild(client.guilds(), spec)
    except LookupError as exc:
        raise _fail(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — API/network → actionable message
        raise _api_fail(exc) from exc


def _who(user: dict[str, Any]) -> str:
    name = user.get("global_name") or user.get("username") or "?"
    handle = user.get("username") or ""
    disc = user.get("discriminator")
    if disc and disc != "0":
        handle = f"{handle}#{disc}"
    return name if handle in ("", name) else f"{name} (@{handle})"


def _confirm(message: str, yes: bool) -> None:
    if yes:
        return
    if not _interactive():
        raise _fail(f"{message} — refusing without confirmation.", "re-run with --yes")
    if not ch.confirm_action(message, default=False):
        ch.info("Cancelled.")
        raise typer.Exit(0)


def _print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _table(*columns: tuple[str, dict[str, Any]]):
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    for title, kwargs in columns:
        table.add_column(title, **kwargs)
    return table


# ── status / login ───────────────────────────────────────────────────────────


@discord_app.command("status")
def discord_status(json_out: bool = typer.Option(False, "--json", help="Machine-readable output")) -> None:
    """Which Discord identities are set up, and whether their tokens still work."""
    from navig.discord import config
    from navig.discord.api import DiscordClient

    rows: list[dict[str, Any]] = []
    broken = False
    for identity in ("bot", "account"):
        token = config.get_token(identity)  # type: ignore[arg-type]
        row: dict[str, Any] = {"identity": identity, "configured": bool(token)}
        if token:
            try:
                with DiscordClient(identity, token) as client:  # type: ignore[arg-type]
                    me = client.me()
                    row.update(ok=True, user=_who(me), user_id=me.get("id"), servers=len(client.guilds()))
            except Exception as exc:  # noqa: BLE001 — reported per row, exit code below
                row.update(ok=False, error=str(exc))
                broken = True
        rows.append(row)

    if json_out:
        _print_json(rows)
    else:
        vault_reason = config.vault_unavailable_reason()
        if vault_reason:
            ch.warning(f"Vault unavailable: {vault_reason}")
        table = _table(
            ("Identity", {"no_wrap": True}),
            ("Status", {"no_wrap": True}),
            ("Signed in as", {}),
            ("Servers", {"no_wrap": True, "justify": "right"}),
            ("Next step", {"no_wrap": True}),
        )
        for row in rows:
            label = "🤖 bot" if row["identity"] == "bot" else "👤 account"
            if not row["configured"]:
                table.add_row(label, "[dim]○ not set[/dim]", "[dim]—[/dim]", "", f"→ {_setup_hint(row['identity'])}")
            elif row.get("ok"):
                table.add_row(label, "[green]● connected[/green]", row["user"], str(row["servers"]), "ready")
            else:
                table.add_row(label, "[red]✗ failing[/red]", f"[red]{row['error']}[/red]", "", "→ reconnect")
        ch.console.print(table)
        ready = sum(1 for r in rows if r.get("ok"))
        ch.dim(f"{ready}/2 connected · servers: navig discord servers · add the bot: navig discord bot invite")
    if broken:
        raise typer.Exit(1)


@discord_app.command("login")
def discord_login(
    token: str = typer.Option(None, "--token", help="Your Discord user token (prompted, hidden, if omitted)"),
    accept_risk: bool = typer.Option(False, "--accept-risk", help="Skip the Terms-of-Service warning prompt"),
) -> None:
    """Opt in to account mode: store YOUR user token (list, read and leave your servers)."""
    from navig.discord import config
    from navig.discord.api import DiscordClient

    ch.warning_panel(_RISK_TEXT, title="Account mode — read this first")
    if not accept_risk:
        if not _interactive():
            raise _fail("Account mode needs an explicit opt-in.", "re-run with --accept-risk")
        if not ch.confirm_action("I understand the risk to my account — continue?", default=False):
            ch.info("Not logged in. The bot route stays available: navig discord bot set")
            raise typer.Exit(0)
    if not token:
        if not _interactive():
            raise _fail("No token given.", "pass --token")
        token = typer.prompt("Discord user token", hide_input=True)
    token = token.strip()
    if token.lower().startswith("bot "):
        raise _fail("That is a bot token.", "store it with: navig discord bot set")
    try:
        with DiscordClient("account", token) as client:
            me = client.me()
    except Exception as exc:  # noqa: BLE001
        raise _api_fail(exc) from exc
    if me.get("bot"):
        raise _fail("That token belongs to a bot.", "store it with: navig discord bot set")
    config.set_token("account", token)
    ch.success(f"Logged in as {_who(me)} — token stored in the vault.")
    ch.dim("Next: navig discord servers")


@discord_app.command("logout")
def discord_logout() -> None:
    """Forget your user token (the bot is untouched)."""
    from navig.discord import config

    if config.clear_token("account"):
        ch.success("Account token removed from the vault.")
    else:
        ch.info("No account token was stored.")


# ── bot ──────────────────────────────────────────────────────────────────────


@bot_app.command("set")
def bot_set(token: str = typer.Option(None, "--token", help="Bot token (prompted, hidden, if omitted)")) -> None:
    """Store the bot token (Developer Portal → your app → Bot → Reset Token)."""
    from navig.discord import config
    from navig.discord.api import DiscordClient

    if not token:
        if not _interactive():
            raise _fail("No token given.", "pass --token")
        token = typer.prompt("Discord bot token", hide_input=True)
    token = token.strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    try:
        with DiscordClient("bot", token) as client:
            me = client.me()
    except Exception as exc:  # noqa: BLE001
        raise _api_fail(exc) from exc
    config.set_token("bot", token)
    ch.success(f"Bot {_who(me)} stored in the vault as discord_bot_token.")
    ch.dim("Next: add it to a server — navig discord bot invite")


@bot_app.command("invite")
def bot_invite(
    admin: bool = typer.Option(False, "--admin", help="Request Administrator instead of the minimal set"),
    no_open: bool = typer.Option(False, "--no-open", help="Print the link without opening a browser"),
) -> None:
    """Open the link that adds the bot to a server you manage."""
    from navig.discord.api import bot_invite_url

    client = _bot()
    try:
        with client:
            app_id = str(client.application()["id"])
    except Exception as exc:  # noqa: BLE001
        raise _api_fail(exc) from exc
    url = bot_invite_url(app_id, admin=admin)
    ch.info("Add the bot to a server:", url)
    ch.dim("Member lists also need: Developer Portal → Bot → Privileged Gateway Intents → Server Members.")
    if not no_open:
        import webbrowser

        webbrowser.open(url)


# ── servers ──────────────────────────────────────────────────────────────────


@discord_app.command("servers")
def discord_servers(
    as_: str = typer.Option("auto", "--as", help=_AS_HELP),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Every server you are in — members, online, your role, and where the bot is."""
    from navig.discord import config

    identity = _pick(as_, prefer="account")
    try:
        with _client(identity) as client:
            guilds = client.guilds()
    except Exception as exc:  # noqa: BLE001
        raise _api_fail(exc) from exc

    bot_ids: set[str] | None = None
    if identity == "account" and config.have_token("bot"):
        try:
            with _client("bot") as bot:
                bot_ids = {g.id for g in bot.guilds()}
        except Exception as exc:  # noqa: BLE001 — the account list is still valid
            ch.warning(f"Could not check where the bot is: {exc}")
    elif identity == "bot":
        bot_ids = {g.id for g in guilds}

    guilds.sort(key=lambda g: g.name.casefold())
    if json_out:
        _print_json([
            {
                "id": g.id, "name": g.name, "owner": g.owner, "admin": g.is_admin,
                "members": g.member_count, "online": g.online_count,
                "bot": (g.id in bot_ids) if bot_ids is not None else None,
            }
            for g in guilds
        ])
        return
    table = _table(
        ("Server", {}),
        ("ID", {"no_wrap": True, "style": "dim"}),
        ("Members", {"no_wrap": True, "justify": "right"}),
        ("Online", {"no_wrap": True, "justify": "right"}),
        ("You", {"no_wrap": True}),
        ("Bot", {"no_wrap": True}),
    )
    for g in guilds:
        you = "[magenta]owner[/magenta]" if g.owner else ("[cyan]admin[/cyan]" if g.is_admin else "member")
        if bot_ids is None:
            bot_cell = "[dim]?[/dim]"
        else:
            bot_cell = "[green]●[/green]" if g.id in bot_ids else "[dim]○[/dim]"
        table.add_row(
            g.name, g.id,
            "" if g.member_count is None else f"{g.member_count:,}",
            "" if g.online_count is None else f"{g.online_count:,}",
            you, bot_cell,
        )
    ch.console.print(table)
    ch.dim(f"{len(guilds)} servers as {identity} · details: navig discord server <name|id> · leave: navig discord leave <name|id>")


@discord_app.command("server")
def discord_server(
    server: str = typer.Argument(..., help="Server name or id"),
    as_: str = typer.Option("auto", "--as", help=_AS_HELP),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Details of one server."""
    identity = _pick(as_, prefer="account")
    with _client(identity) as client:
        g = _guild(client, server)
        try:
            data = client.guild(g.id)
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    if json_out:
        _print_json(data)
        return
    def _n(key: str) -> str:
        value = data.get(key)
        return f"{value:,}" if isinstance(value, int) else "?"

    lines = [
        f"[bold]{data.get('name')}[/bold]  [dim]{data.get('id')}[/dim]",
        f"Members   {_n('approximate_member_count')}   online {_n('approximate_presence_count')}",
        f"Owner     {data.get('owner_id')}{'  (you)' if g.owner else ''}",
        f"Boost     tier {data.get('premium_tier', 0)} · {data.get('premium_subscription_count') or 0} boosts",
        f"Security  verification level {data.get('verification_level')} · MFA level {data.get('mfa_level')}",
        f"Locale    {data.get('preferred_locale') or '—'}",
        f"Roles     {len(data.get('roles') or [])} · emojis {len(data.get('emojis') or [])}",
    ]
    if data.get("vanity_url_code"):
        lines.append(f"Vanity    discord.gg/{data['vanity_url_code']}")
    if data.get("description"):
        lines.append(f"\n{data['description']}")
    ch.panel("\n".join(lines), title="Discord server")
    ch.dim(f"channels: navig discord channels {g.id} · roles: navig discord roles {g.id}")


@discord_app.command("channels")
def discord_channels(
    server: str = typer.Argument(..., help="Server name or id"),
    as_: str = typer.Option("auto", "--as", help=_AS_HELP),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Channels of a server, grouped by category."""
    from navig.discord.api import CHANNEL_TYPES

    identity = _pick(as_, prefer="account")
    with _client(identity) as client:
        g = _guild(client, server)
        try:
            chans = client.channels(g.id)
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    if json_out:
        _print_json(chans)
        return
    glyph = {"text": "#", "voice": "🔊", "announcement": "📢", "forum": "💬", "stage": "🎙", "media": "🖼"}
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for c in chans:
        by_parent.setdefault(c.get("parent_id"), []).append(c)
    for group in by_parent.values():
        group.sort(key=lambda c: (c.get("position", 0), c.get("id")))
    categories = [c for c in chans if c.get("type") == 4]
    categories.sort(key=lambda c: c.get("position", 0))

    table = _table(
        ("Channel", {"no_wrap": True}),
        ("Type", {"no_wrap": True}),
        ("ID", {"no_wrap": True, "style": "dim"}),
        ("Topic", {}),
    )

    def add(c: dict[str, Any], indent: str) -> None:
        kind = CHANNEL_TYPES.get(c.get("type", -1), f"type {c.get('type')}")
        table.add_row(f"{indent}{glyph.get(kind, '·')} {c.get('name')}", kind, str(c.get("id")), c.get("topic") or "")

    for c in by_parent.get(None, []):
        if c.get("type") != 4:
            add(c, "")
    for cat in categories:
        table.add_row(f"[bold]▾ {cat.get('name')}[/bold]", "category", str(cat.get("id")), "")
        for c in by_parent.get(str(cat.get("id")), []):
            add(c, "  ")
    ch.console.print(table)
    ch.dim(f"{len(chans)} channels in {g.name} · manage: navig discord channel create|edit|delete")


@discord_app.command("roles")
def discord_roles(
    server: str = typer.Argument(..., help="Server name or id"),
    as_: str = typer.Option("auto", "--as", help=_AS_HELP),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Roles of a server, highest first."""
    from navig.discord.api import PERM_ADMINISTRATOR

    identity = _pick(as_, prefer="account")
    with _client(identity) as client:
        g = _guild(client, server)
        try:
            roles = client.roles(g.id)
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    roles.sort(key=lambda r: r.get("position", 0), reverse=True)
    if json_out:
        _print_json(roles)
        return
    table = _table(
        ("Role", {}),
        ("ID", {"no_wrap": True, "style": "dim"}),
        ("Color", {"no_wrap": True}),
        ("Admin", {"no_wrap": True}),
        ("Flags", {"no_wrap": True}),
    )
    for r in roles:
        color = int(r.get("color") or 0)
        swatch = f"[#{color:06x}]■[/] #{color:06x}" if color else "[dim]—[/dim]"
        try:
            admin = bool(int(r.get("permissions") or 0) & PERM_ADMINISTRATOR)
        except (TypeError, ValueError):
            admin = False
        flags = " ".join(
            f for f, on in (("managed", r.get("managed")), ("hoisted", r.get("hoist")), ("@able", r.get("mentionable"))) if on
        )
        table.add_row(r.get("name", "?"), str(r.get("id")), swatch, "[yellow]✓[/yellow]" if admin else "", flags)
    ch.console.print(table)
    ch.dim(f"{len(roles)} roles in {g.name} · assign: navig discord role add {g.id} <user-id> <role>")


@discord_app.command("members")
def discord_members(
    server: str = typer.Argument(..., help="Server name or id (the bot must be in it)"),
    search: str = typer.Option(None, "--search", "-s", help="Username / nickname prefix"),
    limit: int = typer.Option(100, "--limit", "-n", min=1, max=10000, help="How many to fetch"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Members of a server the bot is in (bot only — needs the Server Members intent)."""
    from navig.discord.api import DiscordAPIError

    client = _bot()
    with client:
        g = _guild(client, server)
        try:
            rows = client.search_members(g.id, search, limit=limit) if search else client.members(g.id, limit=limit)
        except DiscordAPIError as exc:
            if exc.status == 403 and exc.code == 50001:
                raise _fail(
                    str(exc),
                    "enable it: Developer Portal → your app → Bot → Privileged Gateway Intents → Server Members",
                ) from exc
            raise _api_fail(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    if json_out:
        _print_json(rows)
        return
    table = _table(
        ("Member", {}),
        ("ID", {"no_wrap": True, "style": "dim"}),
        ("Joined", {"no_wrap": True}),
        ("Roles", {"no_wrap": True, "justify": "right"}),
        ("", {"no_wrap": True}),
    )
    for m in rows:
        user = m.get("user") or {}
        name = m.get("nick") or _who(user)
        table.add_row(
            name, str(user.get("id", "")), str(m.get("joined_at") or "")[:10],
            str(len(m.get("roles") or [])), "[dim]bot[/dim]" if user.get("bot") else "",
        )
    ch.console.print(table)
    more = " (limit reached — raise --limit)" if len(rows) >= limit else ""
    ch.dim(f"{len(rows)} members in {g.name}{more}")


@discord_app.command("leave")
def discord_leave(
    server: str = typer.Argument(..., help="Server name or id"),
    as_: str = typer.Option("auto", "--as", help="auto · account (you leave) · bot (the bot leaves)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
) -> None:
    """Leave a server (as you, or remove the bot from it)."""
    identity = _pick(as_, prefer="account")
    with _client(identity) as client:
        g = _guild(client, server)
        if g.owner:
            raise _fail(
                f"{'You own' if identity == 'account' else 'The bot owns'} {g.name} — an owner cannot leave.",
                "transfer ownership or delete the server in Discord",
            )
        who = "You" if identity == "account" else "The bot"
        _confirm(f"{who} will leave {g.name} ({g.id}). Continue?", yes)
        try:
            client.leave_guild(g.id)
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    ch.success(f"{who} left {g.name}.")


@discord_app.command("join")
def discord_join(
    invite: str = typer.Argument(..., help="Invite link or code (discord.gg/…)"),
    no_open: bool = typer.Option(False, "--no-open", help="Only show the invite, don't open it"),
) -> None:
    """Preview an invite and open it in your browser to accept it (never joins by API)."""
    from navig.discord.api import fetch_invite, parse_invite_code

    try:
        code = parse_invite_code(invite)
        data = fetch_invite(code)
    except Exception as exc:  # noqa: BLE001
        raise _api_fail(exc) from exc
    guild = data.get("guild") or {}
    channel = data.get("channel") or {}
    inviter = data.get("inviter") or {}
    lines = [f"[bold]{guild.get('name', '?')}[/bold]  [dim]{guild.get('id', '')}[/dim]"]
    if isinstance(data.get("approximate_member_count"), int):
        lines.append(f"Members  {data['approximate_member_count']:,} · online {data.get('approximate_presence_count') or 0:,}")
    if channel.get("name"):
        lines.append(f"Channel  #{channel['name']}")
    if inviter:
        lines.append(f"Invited by {_who(inviter)}")
    if data.get("expires_at"):
        lines.append(f"Expires  {data['expires_at']}")
    if guild.get("description"):
        lines.append(f"\n{guild['description']}")
    ch.panel("\n".join(lines), title=f"Invite {code}")
    url = f"https://discord.com/invite/{code}"
    if no_open:
        ch.info("Accept it here:", url)
        return
    import webbrowser

    webbrowser.open(url)
    ch.info("Opened the invite in your browser — accept it there.", url)


# ── channel management (bot) ─────────────────────────────────────────────────


def _resolve_category(client: DiscordClient, guild_id: str, spec: str) -> str:
    if spec.isdigit():
        return spec
    cats = [c for c in client.channels(guild_id) if c.get("type") == 4]
    hits = [c for c in cats if str(c.get("name", "")).casefold() == spec.casefold()]
    if len(hits) != 1:
        raise _fail(
            f"{'No' if not hits else 'More than one'} category named {spec!r}.",
            "pass the category id (navig discord channels <server>)",
        )
    return str(hits[0]["id"])


@channel_app.command("create")
def channel_create(
    server: str = typer.Argument(..., help="Server name or id"),
    name: str = typer.Argument(..., help="Channel name"),
    kind: str = typer.Option("text", "--type", "-t", help="text · voice · category · announcement · forum · stage"),
    category: str = typer.Option(None, "--category", "-c", help="Category name or id to put it in"),
    topic: str = typer.Option(None, "--topic", help="Channel topic"),
    reason: str = typer.Option(None, "--reason", help="Shown in the server's audit log"),
) -> None:
    """Create a channel or category."""
    client = _bot()
    with client:
        g = _guild(client, server)
        try:
            parent = _resolve_category(client, g.id, category) if category else None
            created = client.create_channel(g.id, name, kind=kind, parent_id=parent, topic=topic, reason=reason)
        except typer.Exit:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    ch.success(f"Created {kind} {created.get('name')} ({created.get('id')}) in {g.name}.")


@channel_app.command("edit")
def channel_edit(
    channel_id: str = typer.Argument(..., help="Channel id"),
    name: str = typer.Option(None, "--name", help="New name"),
    topic: str = typer.Option(None, "--topic", help="New topic ('' clears it)"),
    category: str = typer.Option(None, "--category", "-c", help="Move under this category id ('none' = top level)"),
    slowmode: int = typer.Option(None, "--slowmode", min=0, max=21600, help="Seconds between messages per user"),
    reason: str = typer.Option(None, "--reason", help="Shown in the server's audit log"),
) -> None:
    """Rename, re-topic, move or slow down a channel."""
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if topic is not None:
        fields["topic"] = topic or None
    if category is not None:
        fields["parent_id"] = None if category.lower() == "none" else category
    if slowmode is not None:
        fields["rate_limit_per_user"] = slowmode
    if not fields:
        raise _fail("Nothing to change.", "pass --name, --topic, --category or --slowmode")
    client = _bot()
    with client:
        try:
            updated = client.edit_channel(channel_id, fields, reason=reason)
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    ch.success(f"Updated {updated.get('name')} ({channel_id}): {', '.join(fields)}.")


@channel_app.command("delete")
def channel_delete(
    channel_id: str = typer.Argument(..., help="Channel id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
    reason: str = typer.Option(None, "--reason", help="Shown in the server's audit log"),
) -> None:
    """Delete a channel (its messages go with it — this cannot be undone)."""
    _confirm(f"Delete channel {channel_id} and all of its messages? This cannot be undone.", yes)
    client = _bot()
    with client:
        try:
            deleted = client.delete_channel(channel_id, reason=reason)
        except Exception as exc:  # noqa: BLE001
            raise _api_fail(exc) from exc
    ch.success(f"Deleted {deleted.get('name') if deleted else channel_id}.")




# ── moderation (bot) ─────────────────────────────────────────────────────────


def _do(fn):
    """Run a moderation-service call; every refusal or failure becomes exit 1."""
    from navig.discord.api import DiscordAPIError
    from navig.discord.policy import PolicyDenied

    try:
        return fn()
    except PolicyDenied as exc:
        raise _fail(f"Refused: {exc}") from exc
    except (LookupError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    except DiscordAPIError as exc:
        raise _api_fail(exc) from exc
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 — network etc.
        raise _api_fail(exc) from exc


def _member_action(server: str, user_id: str, action: str, *, reason: str | None, **kw: Any) -> str:
    from navig.discord import moderation

    client = _bot()
    with client:
        g = _guild(client, server)
        _do(lambda: moderation.member_action(client, action, g.id, user_id, reason=reason, **kw))
    return g.name


_USER_HELP = "The member's user id (Discord: right-click → Copy User ID)"
_REASON_HELP = "Why — shown in the server's audit log"


@mod_app.command("kick")
def mod_kick(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
) -> None:
    """Kick a member (they can rejoin with an invite)."""
    _confirm(f"Kick {user_id} from {server}?", yes)
    name = _member_action(server, user_id, "kick", reason=reason)
    ch.success(f"Kicked {user_id} from {name}.")


@mod_app.command("ban")
def mod_ban(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    delete: str = typer.Option(None, "--delete", help="Also delete their messages from the last N (e.g. 1h, 1d, max 7d)"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
) -> None:
    """Ban a member (also works for users who already left)."""
    from navig.discord.policy import MAX_BAN_DELETE_SECONDS, parse_duration

    seconds = _do(lambda: parse_duration(delete, maximum=MAX_BAN_DELETE_SECONDS)) if delete else 0
    extra = f" and delete their last {delete} of messages" if delete else ""
    _confirm(f"Ban {user_id} from {server}{extra}?", yes)
    name = _member_action(server, user_id, "ban", reason=reason, delete_message_seconds=seconds)
    ch.success(f"Banned {user_id} from {name}.")


@mod_app.command("unban")
def mod_unban(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """Lift a ban."""
    name = _member_action(server, user_id, "unban", reason=reason)
    ch.success(f"Unbanned {user_id} in {name}.")


@mod_app.command("timeout")
def mod_timeout(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    duration: str = typer.Argument(..., help="How long: 10m, 2h, 3d … (max 28d)"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """Time a member out: they can read but not talk, react or join voice."""
    from navig.discord.policy import parse_duration

    seconds = _do(lambda: parse_duration(duration))
    name = _member_action(server, user_id, "timeout", reason=reason, duration_seconds=seconds)
    ch.success(f"Timed out {user_id} in {name} for {duration}.")


@mod_app.command("untimeout")
def mod_untimeout(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """End a member's timeout early."""
    name = _member_action(server, user_id, "untimeout", reason=reason)
    ch.success(f"Lifted the timeout on {user_id} in {name}.")


@mod_app.command("bans")
def mod_bans(
    server: str = typer.Argument(..., help="Server name or id"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Everyone banned from a server, with the reason."""
    client = _bot()
    with client:
        g = _guild(client, server)
        rows = _do(lambda: client.bans(g.id))
    if json_out:
        _print_json(rows)
        return
    table = _table(("User", {}), ("ID", {"no_wrap": True, "style": "dim"}), ("Reason", {}))
    for b in rows:
        user = b.get("user") or {}
        table.add_row(_who(user), str(user.get("id", "")), b.get("reason") or "[dim]—[/dim]")
    ch.console.print(table)
    ch.dim(f"{len(rows)} bans in {g.name} · lift one: navig discord mod unban {g.id} <user-id>")


@mod_app.command("log")
def mod_log(
    server: str = typer.Option(None, "--server", "-s", help="Only this server id"),
    limit: int = typer.Option(30, "--limit", "-n", min=1, max=1000),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """NAVIG's own record of every moderation action it took (newest first)."""
    from navig.discord import modlog

    rows = modlog.tail(limit, guild_id=server)
    if json_out:
        _print_json(rows)
        return
    if not rows:
        ch.info("No moderation actions recorded yet.")
        return
    table = _table(
        ("When", {"no_wrap": True}),
        ("Action", {"no_wrap": True}),
        ("Server", {"no_wrap": True, "style": "dim"}),
        ("Target", {"no_wrap": True}),
        ("By", {"no_wrap": True}),
        ("Via", {"no_wrap": True}),
        ("", {"no_wrap": True}),
        ("Reason", {}),
    )
    for r in rows:
        table.add_row(
            str(r.get("ts", ""))[:19].replace("T", " "),
            r.get("action", ""),
            r.get("guild_id", ""),
            r.get("target_id") or "",
            r.get("actor_id") or "operator",
            r.get("surface", ""),
            "[green]✓[/green]" if r.get("ok") else "[red]✗[/red]",
            r.get("reason") or r.get("detail") or "",
        )
    ch.console.print(table)
    ch.dim(f"{len(rows)} entries · file: {modlog.path()}")


# ── roles (bot) ──────────────────────────────────────────────────────────────


def _with_ctx(server: str, fn) -> tuple[str, Any]:
    from navig.discord import moderation

    client = _bot()
    with client:
        g = _guild(client, server)
        result = _do(lambda: fn(client, g.id, moderation.context(client, g.id)))
    return g.name, result


@role_app.command("add")
def role_add(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    role: str = typer.Argument(..., help="Role name or id"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """Give a member a role."""
    from navig.discord import moderation

    name, r = _with_ctx(
        server, lambda c, gid, ctx: moderation.role_assign(c, gid, user_id, role, add=True, reason=reason, ctx=ctx)
    )
    ch.success(f"Gave {r.get('name')} to {user_id} in {name}.")


@role_app.command("remove")
def role_remove(
    server: str = typer.Argument(..., help="Server name or id"),
    user_id: str = typer.Argument(..., help=_USER_HELP),
    role: str = typer.Argument(..., help="Role name or id"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """Take a role from a member."""
    from navig.discord import moderation

    name, r = _with_ctx(
        server, lambda c, gid, ctx: moderation.role_assign(c, gid, user_id, role, add=False, reason=reason, ctx=ctx)
    )
    ch.success(f"Took {r.get('name')} from {user_id} in {name}.")


_PERMS_HELP = "Comma-separated names (kick_members,manage_messages) or a raw integer"


@role_app.command("create")
def role_create(
    server: str = typer.Argument(..., help="Server name or id"),
    name: str = typer.Argument(..., help="Role name"),
    permissions: str = typer.Option("", "--permissions", "-p", help=_PERMS_HELP),
    color: str = typer.Option(None, "--color", help="#rrggbb"),
    hoist: bool = typer.Option(False, "--hoist", help="Show members separately in the member list"),
    mentionable: bool = typer.Option(False, "--mentionable", help="Anyone can @mention it"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """Create a role (never with permissions the bot itself lacks)."""
    from navig.discord import moderation
    from navig.discord.policy import parse_color, parse_permissions

    perms = _do(lambda: parse_permissions(permissions))
    rgb = _do(lambda: parse_color(color))
    gname, r = _with_ctx(
        server,
        lambda c, gid, ctx: moderation.role_create(
            c, gid, name, permissions=perms, color=rgb, hoist=hoist, mentionable=mentionable, reason=reason, ctx=ctx
        ),
    )
    ch.success(f"Created role {r.get('name')} ({r.get('id')}) in {gname}.")


@role_app.command("edit")
def role_edit(
    server: str = typer.Argument(..., help="Server name or id"),
    role: str = typer.Argument(..., help="Role name or id"),
    name: str = typer.Option(None, "--name", help="New name"),
    permissions: str = typer.Option(None, "--permissions", "-p", help=_PERMS_HELP + " (replaces the set)"),
    color: str = typer.Option(None, "--color", help="#rrggbb (0 = none)"),
    hoist: bool = typer.Option(None, "--hoist/--no-hoist", help="Show members separately"),
    mentionable: bool = typer.Option(None, "--mentionable/--no-mentionable", help="Anyone can @mention it"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
) -> None:
    """Rename, recolour or change a role's permissions."""
    from navig.discord import moderation
    from navig.discord.policy import parse_color, parse_permissions

    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if permissions is not None:
        fields["permissions"] = _do(lambda: parse_permissions(permissions))
    if color is not None:
        fields["color"] = _do(lambda: parse_color(color))
    if hoist is not None:
        fields["hoist"] = hoist
    if mentionable is not None:
        fields["mentionable"] = mentionable
    if not fields:
        raise _fail("Nothing to change.", "pass --name, --permissions, --color, --hoist or --mentionable")
    gname, r = _with_ctx(server, lambda c, gid, ctx: moderation.role_edit(c, gid, role, fields, reason=reason, ctx=ctx))
    ch.success(f"Updated role {r.get('name')} in {gname}: {', '.join(fields)}.")


@role_app.command("delete")
def role_delete(
    server: str = typer.Argument(..., help="Server name or id"),
    role: str = typer.Argument(..., help="Role name or id"),
    reason: str = typer.Option(None, "--reason", "-r", help=_REASON_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
) -> None:
    """Delete a role (members lose it; this cannot be undone)."""
    from navig.discord import moderation

    _confirm(f"Delete role {role} in {server}? Members holding it lose it.", yes)
    gname, r = _with_ctx(server, lambda c, gid, ctx: moderation.role_delete(c, gid, role, reason=reason, ctx=ctx))
    ch.success(f"Deleted role {r.get('name')} in {gname}.")


# ── bot profile ──────────────────────────────────────────────────────────────

_INTENT_MEMBERS = 1 << 14
_INTENT_MEMBERS_LIMITED = 1 << 15


def _avatar_data_uri(path: str) -> str:
    import base64
    from pathlib import Path

    from navig.platform.paths import resolve_user_path

    p = Path(resolve_user_path(path))
    if not p.is_file():
        raise _fail(f"No such file: {p}")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
    kind = mime.get(p.suffix.lower())
    if not kind:
        raise _fail("The avatar must be a .png, .jpg, .gif or .webp image.")
    data = p.read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise _fail("The avatar is larger than Discord's 8 MB limit.")
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


@bot_app.command("profile")
def bot_profile(
    name: str = typer.Option(None, "--name", help="New bot username (Discord allows ~2 changes per hour)"),
    avatar: str = typer.Option(None, "--avatar", help="Image file for the bot's avatar"),
    about: str = typer.Option(None, "--about", help="The bot's About Me / description (max 400 chars)"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show the bot's profile, or change its name, avatar and About Me."""
    client = _bot()
    with client:
        user_fields: dict[str, Any] = {}
        if name is not None:
            user_fields["username"] = name.strip()
        if avatar is not None:
            user_fields["avatar"] = _avatar_data_uri(avatar)
        if user_fields:
            _do(lambda: client.edit_me(user_fields))
        if about is not None:
            if len(about) > 400:
                raise _fail("About Me is limited to 400 characters.")
            _do(lambda: client.edit_application({"description": about}))
        me = _do(client.me)
        app = _do(client.application)
    changed = [k for k, v in (("name", name), ("avatar", avatar), ("about", about)) if v is not None]
    if json_out:
        _print_json({"user": me, "application": {k: app.get(k) for k in ("id", "name", "description", "bot_public", "flags")}})
        return
    flags = int(app.get("flags") or 0)
    members = bool(flags & (_INTENT_MEMBERS | _INTENT_MEMBERS_LIMITED))
    lines = [
        f"[bold]{_who(me)}[/bold]  [dim]{me.get('id')}[/dim]",
        f"About       {app.get('description') or '[dim]—[/dim]'}",
        f"Public bot  {'[yellow]yes — anyone can add it[/yellow]' if app.get('bot_public') else '[green]no — only you can add it[/green]'}",
        f"Members     {'[green]● Server Members intent on[/green]' if members else '[yellow]○ Server Members intent off[/yellow]'}",
    ]
    ch.panel("\n".join(lines), title="NAVIG Discord bot")
    if changed:
        ch.success(f"Updated: {', '.join(changed)}.")


# ── settings ─────────────────────────────────────────────────────────────────


@discord_app.command("settings")
def discord_settings(json_out: bool = typer.Option(False, "--json", help="Machine-readable output")) -> None:
    """The bot's settings (discord.* in config) and what each one does."""
    from dataclasses import asdict

    from navig.discord import settings as dsettings

    s = dsettings.load()
    if json_out:
        _print_json({k: sorted(v) if isinstance(v, frozenset) else v for k, v in asdict(s).items()})
        return
    rows = [
        ("discord.enabled", "on" if s.enabled else "off", "the daemon starts the bot"),
        ("discord.owners", ", ".join(sorted(s.owners)) or "[yellow]none[/yellow]",
         "who may talk to the NAVIG agent (/navig) — empty = nobody"),
        ("discord.allowed_guilds", ", ".join(sorted(s.allowed_guilds)) or "any",
         "servers the bot works in; it leaves any other"),
        ("discord.agent.enabled", "on" if s.agent_enabled else "off", "owners may use /navig"),
        ("discord.moderation.enabled", "on" if s.moderation_enabled else "off", "moderation slash commands"),
        ("discord.moderation.confirm", "on" if s.confirm else "off", "kick / ban / role delete ask for a button press"),
        ("discord.moderation.cooldown", str(s.cooldown_per_minute), "moderation commands per member per minute"),
        ("discord.sync", s.sync, "guild = instant per server · global = everywhere, up to an hour"),
    ]
    table = _table(("Key", {"no_wrap": True}), ("Value", {"no_wrap": True}), ("Meaning", {}))
    for key, value, meaning in rows:
        table.add_row(key, value, meaning)
    ch.console.print(table)
    ch.dim("Change one: navig config set discord.<key> <value> · guided: navig discord setup")


# ── setup ────────────────────────────────────────────────────────────────────


def _discord_py_installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("discord.app_commands") is not None
    except (ImportError, ValueError):
        return False


def _install_discord_py() -> bool:
    import subprocess

    ch.info("Installing discord.py into NAVIG's Python …")
    proc = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-m", "pip", "install", "--quiet", "discord.py>=2.4,<3"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, check=False,
    )
    if proc.returncode != 0:
        ch.error("pip could not install discord.py.", (proc.stderr or proc.stdout).strip()[-400:])
        return False
    return True


@discord_app.command("setup")
def discord_setup(
    token: str = typer.Option(None, "--token", help="Bot token (prompted, hidden, if needed)"),
    name: str = typer.Option(None, "--name", help="Rename the bot"),
    owner: list[str] = typer.Option(None, "--owner", help="Discord user id allowed to use /navig (repeatable)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept every default without asking"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't open the invite link"),
) -> None:
    """Guided setup of the NAVIG Discord bot — token, library, intents, owners, invite."""
    from navig.discord import config
    from navig.discord import settings as dsettings
    from navig.discord.api import DiscordClient, bot_invite_url

    def ask(question: str, default: bool = True) -> bool:
        if yes:
            return default
        if not _interactive():
            return default
        return ch.confirm_action(question, default=default)

    report: list[tuple[str, str, str]] = []

    # 1 — the library the bot runs on
    if _discord_py_installed():
        report.append(("Library", "[green]● discord.py installed[/green]", "ready"))
    elif ask("The bot needs discord.py (MIT, ~1 MB). Install it now?"):
        ok = _install_discord_py()
        report.append(("Library", "[green]● installed[/green]" if ok else "[red]✗ install failed[/red]",
                       "ready" if ok else "→ pip install 'navig[discord]'"))
    else:
        report.append(("Library", "[yellow]○ not installed[/yellow]", "→ pip install 'navig[discord]'"))

    # 2 — token
    current = config.get_token("bot")
    if token or not current:
        if not token:
            if not _interactive():
                raise _fail("No bot token stored.", "pass --token (Developer Portal → your app → Bot → Reset Token)")
            ch.info("Create an app at https://discord.com/developers/applications → Bot → Reset Token.")
            token = typer.prompt("Discord bot token", hide_input=True)
        token = token.strip().removeprefix("Bot ").strip()
    else:
        token = current
    try:
        with DiscordClient("bot", token) as client:
            me = client.me()
            app = client.application()
            if token != current:
                config.set_token("bot", token)
            report.append(("Token", "[green]● valid[/green]", f"stored in the vault as {config.VAULT_BOT_TOKEN}"))

            # 3 — name
            if name and name.strip() != me.get("username"):
                try:
                    me = client.edit_me({"username": name.strip()})
                    report.append(("Name", f"[green]● {name.strip()}[/green]", "renamed"))
                except Exception as exc:  # noqa: BLE001 — rename is cosmetic; report and continue
                    report.append(("Name", "[yellow]○ unchanged[/yellow]", str(exc)))
            else:
                report.append(("Name", _who(me), "→ --name to rename"))

            # 4 — who can add the bot
            portal = f"https://discord.com/developers/applications/{app.get('id')}/bot"
            if app.get("bot_public"):
                report.append(("Public bot", "[yellow]⚠ on — anyone can add it[/yellow]", f"→ turn off Public Bot: {portal}"))
            else:
                report.append(("Public bot", "[green]● off — only you can add it[/green]", "secure"))

            # 5 — Server Members intent (needed for member lists)
            flags = int(app.get("flags") or 0)
            if flags & (_INTENT_MEMBERS | _INTENT_MEMBERS_LIMITED):
                report.append(("Members intent", "[green]● on[/green]", "member lists work"))
            elif ask("Turn on the Server Members intent (needed to list members)?"):
                try:
                    client.edit_application({"flags": flags | _INTENT_MEMBERS_LIMITED})
                    report.append(("Members intent", "[green]● turned on[/green]", "member lists work"))
                except Exception as exc:  # noqa: BLE001 — portal fallback below
                    report.append(("Members intent", "[yellow]○ off[/yellow]",
                                   f"→ switch it on by hand: {portal} ({exc})"))
            else:
                report.append(("Members intent", "[yellow]○ off[/yellow]", f"→ {portal}"))
    except Exception as exc:  # noqa: BLE001
        raise _api_fail(exc) from exc

    # 6 — owners (fail-closed: nobody reaches the agent until this is set)
    current_owners = dsettings.load().owners
    owners = {o.strip() for o in (owner or []) if o.strip().isdigit()}
    if not owners and not current_owners:
        app_owner = str(((app.get("team") or {}).get("owner_user_id")) or (app.get("owner") or {}).get("id") or "")
        if app_owner.isdigit() and ask(f"Let the app's owner ({app_owner}) use /navig?"):
            owners = {app_owner}
    if owners:
        dsettings.set_value("owners", sorted(owners | set(current_owners)))
    final_owners = dsettings.load().owners
    report.append((
        "Owners",
        ", ".join(sorted(final_owners)) if final_owners else "[yellow]none[/yellow]",
        "may use /navig" if final_owners else "nobody can reach the agent → --owner <your user id>",
    ))

    # 7 — start with the daemon
    dsettings.set_value("enabled", True)
    report.append(("Daemon", "[green]● enabled[/green]", "starts with the gateway → navig service restart"))

    table = _table(("Step", {"no_wrap": True}), ("State", {"no_wrap": True}), ("Next", {}))
    for row in report:
        table.add_row(*row)
    ch.console.print(table)

    url = bot_invite_url(str(app.get("id")))
    ch.info("Add the bot to a server you manage:", url)
    if not no_open and _interactive():
        import webbrowser

        webbrowser.open(url)
