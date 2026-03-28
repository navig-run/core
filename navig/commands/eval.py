"""navig eval — evaluate expressions / snippets against NAVIG context."""
import typer
from rich.console import Console

app = typer.Typer(help="Evaluate Python expressions in the NAVIG context", no_args_is_help=True)
console = Console()


@app.command("run")
def eval_run(expression: str = typer.Argument(..., help="Python expression to evaluate")):
    """Evaluate a Python expression with NAVIG context available."""
    try:
        ctx: dict = {}
        try:
            from navig.config import ConfigManager

            ctx["cfg"] = ConfigManager()
        except Exception:
            pass
        result = eval(expression, {"__builtins__": {}}, ctx)  # noqa: S307
        console.print(result)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
