"""
NAVIG Profile CLI Commands
===========================
Operating mode profiles define how NAVIG behaves system-wide:
  - which tool tier is active (safe / dev / ops / all)
  - which model class is preferred (fast / strong / reasoning)
  - output verbosity and style
  - PIN-protected switching for privileged tiers

  navig profile list           — Show all profiles with capabilities
  navig profile show           — Show current active profile
  navig profile set <name>     — Switch profile (PIN-gated for operator/architect)
  navig profile pin-set        — Set the PIN for privileged profiles
  navig profile pin-clear      — Remove the stored PIN
"""

from __future__ import annotations

import typer

from navig.lazy_loader import lazy_import
from navig.platform.paths import config_dir

ch = lazy_import("navig.console_helper")

profile_app = typer.Typer(
    name="profile",
    help="Operating profiles: node | builder | operator | architect",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# navig profile list
# ---------------------------------------------------------------------------


@profile_app.command("list")
def profile_list():
    """Show all available operating profiles."""
    from rich.table import Table

    from navig.modes import all_modes, get_active_mode_name

    active = get_active_mode_name()
    modes = all_modes()

    table = Table(title="NAVIG Operating Profiles", show_header=True)
    table.add_column("", width=3)
    table.add_column("Profile", style="bold", width=14)
    table.add_column("Tier", width=10)
    table.add_column("Model", width=12)
    table.add_column("Auth", width=6)
    table.add_column("Description")

    tier_colors = {
        "safe": "green",
        "dev": "blue",
        "ops": "yellow",
        "all": "magenta",
    }

    for name, profile in modes.items():
        active_marker = "▶" if name == active else " "
        tier_color = tier_colors.get(profile.tool_tier, "white")
        tier_label = f"[{tier_color}]{profile.tool_tier}[/{tier_color}]"
        auth_label = "[yellow]PIN[/yellow]" if profile.require_auth else "[dim]—[/dim]"
        row_style = "bold cyan" if name == active else None

        table.add_row(
            active_marker,
            f"{profile.icon}  {profile.label}",
            tier_label,
            profile.model_preference,
            auth_label,
            profile.description,
            style=row_style,
        )

    ch.console.print(table)
    ch.dim(f"\nActive: {active}  |  Switch with: navig profile set <name>")


# ---------------------------------------------------------------------------
# navig profile show
# ---------------------------------------------------------------------------


@profile_app.command("show")
def profile_show():
    """Show the current operating profile and all its settings."""
    from navig.modes import get_active_mode, has_pin

    m = get_active_mode()
    ch.console.print(f"\n{m.icon}  [bold]{m.label}[/bold] profile is active\n")
    ch.dim(f"  Description : {m.description}")
    ch.dim(f"  Model tier  : {m.model_preference}")
    ch.dim(f"  Tool tier   : {m.tool_tier}")
    ch.dim(f"  Output style: {m.output_style}")
    ch.dim(f"  PIN guard   : {'yes' if m.require_auth else 'no'}")
    if m.formations_default:
        ch.dim(f"  Formations  : {', '.join(m.formations_default)}")
    if m.gated_commands:
        ch.dim(f"  Extra gates : {', '.join(m.gated_commands)}")
    ch.dim(f"  PIN stored  : {'yes' if has_pin() else 'no  ← set with: navig profile pin-set'}")
    print()


# ---------------------------------------------------------------------------
# navig profile set
# ---------------------------------------------------------------------------


@profile_app.command("set")
def profile_set(
    name: str = typer.Argument(..., help="Profile name: node, builder, operator, architect"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip PIN prompt (use with care)"),
):
    """Switch to a different operating profile."""
    from navig.modes import get_active_mode_name, get_mode, prompt_pin, set_active_mode

    profile = get_mode(name)
    if profile is None:
        ch.error(f"Unknown profile: '{name}'")
        ch.dim("Available profiles: node, builder, operator, architect")
        raise typer.Exit(1)

    current = get_active_mode_name()
    if current == name:
        ch.success(f"Already in {profile.icon} {profile.label} profile.")
        return

    if profile.require_auth and not force:
        ok = prompt_pin(f"switching to {profile.label} profile")
        if not ok:
            ch.error("Profile switch cancelled.")
            raise typer.Exit(1)

    set_active_mode(name)
    ch.success(f"Switched to {profile.icon} [bold]{profile.label}[/bold] profile.")

    if profile.formations_default:
        ch.dim(f"Suggested formations: {', '.join(profile.formations_default)}")
        ch.dim("Activate with: navig formation switch <name>")


# ---------------------------------------------------------------------------
# navig profile pin-set
# ---------------------------------------------------------------------------


@profile_app.command("pin-set")
def profile_pin_set():
    """Set or change the PIN that protects operator/architect profiles."""
    import getpass

    from navig.modes import has_pin, set_pin

    if has_pin():
        ch.info("A PIN is already set. Enter the new PIN to replace it.")
    else:
        ch.info("No PIN set. Choose a 4-digit PIN to protect privileged profiles.")

    try:
        pin1 = getpass.getpass("   New 4-digit PIN: ")
        pin2 = getpass.getpass("   Confirm PIN:     ")
    except (KeyboardInterrupt, EOFError) as _exc:
        print("\nCancelled.")
        raise typer.Exit(0) from _exc

    if pin1 != pin2:
        ch.error("PINs do not match.")
        raise typer.Exit(1)

    try:
        set_pin(pin1)
        ch.success("PIN saved. OPERATOR and ARCHITECT profiles are now PIN-protected.")
    except ValueError as e:
        ch.error(str(e))
        raise typer.Exit(1) from e


# ---------------------------------------------------------------------------
# navig profile pin-clear
# ---------------------------------------------------------------------------


@profile_app.command("pin-clear")
def profile_pin_clear(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove the stored PIN (disables PIN protection for all profiles)."""

    from navig.modes import has_pin, prompt_pin

    if not has_pin():
        ch.info("No PIN is currently set.")
        return

    if not force:
        ok = prompt_pin("removing the PIN")
        if not ok:
            ch.error("Cancelled — PIN NOT removed.")
            raise typer.Exit(1)

    pin_path = config_dir() / ".mode_pin"
    pin_path.unlink(missing_ok=True)
    ch.success("PIN removed. All profiles are now accessible without PIN.")
    ch.dim("Re-enable with: navig profile pin-set")
