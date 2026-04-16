"""navig finance — beancount double-entry accounting integration.

Install the optional finance extra to unlock full functionality:
    pip install navig[finance]
"""
import typer

from navig.console_helper import get_console

finance_app = typer.Typer(help="Beancount / double-entry accounting tools", no_args_is_help=True)
console = get_console()


@finance_app.command("status")
def finance_status():
    """Show finance integration status."""
    try:
        import beancount  # noqa: F401

        console.print("[green]beancount available.[/green]")
    except ImportError:
        console.print(
            "[yellow]beancount not installed.[/yellow]  "
            "Run: [bold]pip install navig[finance][/bold]"
        )


@finance_app.command("balance")
def finance_balance(
    ledger: str = typer.Argument("", help="Path to beancount ledger file"),
):
    """Show account balances from a beancount ledger."""
    from navig import console_helper as ch

    ch.warn("navig finance balance is not yet implemented in this build.")
