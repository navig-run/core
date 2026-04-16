"""navig explain — explain a NAVIG concept, command, or config key."""
import typer

from navig.console_helper import get_console

app = typer.Typer(help="Explain NAVIG concepts, commands, and config keys", no_args_is_help=True)
console = get_console()


@app.command("command")
def explain_command(name: str = typer.Argument(..., help="Command name to explain")):
    """Explain a NAVIG CLI command."""
    from navig import console_helper as ch

    ch.warn(f"navig explain command '{name}' is not yet implemented.")


@app.command("config")
def explain_config(key: str = typer.Argument(..., help="Config key to explain")):
    """Explain a configuration key."""
    from navig import console_helper as ch

    ch.warn(f"navig explain config '{key}' is not yet implemented.")


@app.command("concept")
def explain_concept(topic: str = typer.Argument(..., help="Concept or topic")):
    """Explain a NAVIG concept using the AI assistant."""
    from navig import console_helper as ch

    ch.warn(f"navig explain concept '{topic}' is not yet implemented.")
