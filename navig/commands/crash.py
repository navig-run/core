"""
Commands for managing crash reports and logs.
"""

import json
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Manage crash reports and logs")
console = Console()


@app.command("export")
def export_crash_report(
    output: Path = typer.Option(
        None, "--output", "-o", help="Path to save the crash report JSON"
    ),
    limit: int = typer.Option(
        1,
        "--limit",
        "-l",
        help="Number of recent crashes to include (currently only 1 supported)",
    ),
):
    """
    Export the latest crash report for GitHub issues.
    """
    try:
        from navig.core.crash_handler import CrashHandler

        # Instantiate a new handler to read logs
        handler = CrashHandler()

        report = handler.get_latest_crash_report()

        if not report:
            console.print("[yellow]No crash reports found.[/yellow]")
            raise typer.Exit(0)

        # Format the report string
        report_str = json.dumps(report, indent=2)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(report_str)
            console.print(f"[green]Crash report exported to:[/green] {output}")
        else:
            # Print to stdout
            console.print(report_str)
            console.print("\n[dim]-- End of Crash Report --[/dim]")
            console.print(
                "[dim]Copy the above JSON to include in a GitHub issue.[/dim]"
            )

    except Exception as e:
        console.print(f"[red]Error exporting crash report:[/red] {e}")
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
