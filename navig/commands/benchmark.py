"""navig benchmark — run NAVIG performance benchmarks."""
import typer
from rich.console import Console

app = typer.Typer(help="Run NAVIG performance benchmarks", no_args_is_help=True)
console = Console()


@app.command("run")
def benchmark_run(
    suite: str = typer.Argument("all", help="Benchmark suite: all|startup|ssh|db"),
):
    """Run benchmarks and show timing results."""
    from navig import console_helper as ch

    ch.warn("navig benchmark is not yet implemented in this build.")
