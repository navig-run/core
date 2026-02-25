"""Net commands — network diagnostics and speed testing.

CLI surface:
  navig net speedtest --iperf3-server HOST   (dual-method: speedtest-cli + iperf3)
  navig net speedtest --skip-iperf3          (speedtest-cli only)
  navig net speedtest --skip-speedtest       (iperf3 only)
"""
from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from typing_extensions import Annotated

net_app = typer.Typer(
    name="net",
    help="Network diagnostics: speed tests, latency, bandwidth.",
    no_args_is_help=True,
)


# ──────────────────────────────────────────────────────────────────────────────
# Lazy import of measurement backend
# ──────────────────────────────────────────────────────────────────────────────

def _backend():
    """Lazy-import speedtest worker to keep CLI startup fast."""
    import importlib.util
    from pathlib import Path

    worker_path = Path(__file__).parents[2] / "scripts" / "speedtest" / "worker.py"
    spec = importlib.util.spec_from_file_location("navig_speedtest_worker", worker_path)
    mod = importlib.util.module_from_spec(spec)    # type: ignore[arg-type]
    spec.loader.exec_module(mod)                   # type: ignore[union-attr]
    return mod


# ──────────────────────────────────────────────────────────────────────────────
# navig net speedtest
# ──────────────────────────────────────────────────────────────────────────────

@net_app.command("speedtest")
def speedtest_cmd(
    iperf3_server: Annotated[
        Optional[str],
        typer.Option("--iperf3-server", "-s", help="iperf3 server hostname or IP (e.g. iperf.he.net)"),
    ] = None,
    iperf3_port: Annotated[
        int,
        typer.Option("--iperf3-port", "-p", help="iperf3 server port"),
    ] = 5201,
    skip_speedtest: Annotated[
        bool,
        typer.Option("--skip-speedtest", help="Skip the speedtest-cli measurement"),
    ] = False,
    skip_iperf3: Annotated[
        bool,
        typer.Option("--skip-iperf3", help="Skip the iperf3 measurement"),
    ] = False,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Print only the final JSON summary (no banners)"),
    ] = False,
):
    """Measure internet speed with speedtest-cli (Ookla) and/or iperf3.

    Both download, upload, latency, and jitter are captured per method.
    Raw command output is shown before the JSON summary.

    Examples:
      navig net speedtest --iperf3-server iperf.he.net
      navig net speedtest --skip-iperf3
      navig net speedtest --iperf3-server 10.0.0.10 --json
    """
    if not skip_iperf3 and not iperf3_server:
        typer.echo(
            "[ERROR] --iperf3-server is required unless --skip-iperf3 is set.\n"
            "Example: navig net speedtest --iperf3-server iperf.he.net",
            err=True,
        )
        raise typer.Exit(1)

    try:
        w = _backend()
    except Exception as exc:
        typer.echo(f"[ERROR] Could not load speedtest worker: {exc}", err=True)
        raise typer.Exit(1)

    summary: dict = {}

    # ── speedtest-cli ──────────────────────────────────────────────────
    if not skip_speedtest:
        if not output_json:
            typer.echo("\n" + "=" * 72)
            typer.echo("  PHASE 1 — speedtest-cli")
            typer.echo("=" * 72)
        summary["speedtest_cli"] = w.run_speedtest_cli(silent=output_json)
    else:
        summary["speedtest_cli"] = {"skipped": True}

    # ── iperf3 ────────────────────────────────────────────────────────
    if not skip_iperf3:
        if not output_json:
            typer.echo("\n" + "=" * 72)
            typer.echo("  PHASE 2 — iperf3")
            typer.echo("=" * 72)
        summary["iperf3"] = w.run_iperf3(iperf3_server, iperf3_port, silent=output_json)
    else:
        summary["iperf3"] = {"skipped": True}

    # ── Summary ───────────────────────────────────────────────────────
    if not output_json:
        typer.echo("\n" + "=" * 72)
        typer.echo("--- Summary (JSON) ---")
        typer.echo("=" * 72)
    typer.echo(json.dumps(summary, indent=2))


# ──────────────────────────────────────────────────────────────────────────────
# Standalone entry
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    net_app()
