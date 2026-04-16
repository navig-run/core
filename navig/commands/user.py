"""navig user — user identity and profile management."""
import typer

from navig.console_helper import get_console

user_app = typer.Typer(help="Manage user identity and profile", no_args_is_help=True)
console = get_console()


@user_app.command("show")
def user_show():
    """Show the current user profile."""
    from navig import console_helper as ch

    try:
        from navig.config import ConfigManager

        cfg = ConfigManager()
        name = cfg.get("user.name", default="(not set)")
        email = cfg.get("user.email", default="(not set)")
        console.print(f"Name:   [cyan]{name}[/cyan]")
        console.print(f"Email:  [cyan]{email}[/cyan]")
    except Exception as exc:
        ch.warn(f"Could not load user profile: {exc}")


@user_app.command("set")
def user_set(
    key: str = typer.Argument(..., help="Profile key (e.g. name, email)"),
    value: str = typer.Argument(..., help="Value"),
):
    """Set a user profile value."""
    from navig.config import ConfigManager

    ConfigManager().set(f"user.{key}", value)
    console.print(f"[green]Set[/green] user.{key} = {value}")
