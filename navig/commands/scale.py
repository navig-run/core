"""
navig scale — Scale a service to the desired replica count.

Usage::

    navig scale nginx 4
    navig scale worker 8 --host prod-01
"""

from __future__ import annotations

import time

import typer

app = typer.Typer(
    name="scale",
    help="Scale a service to a desired replica count.",
    no_args_is_help=True,
)


def run(
    service: str,
    replicas: int,
    host: str = "production-01",
) -> None:
    """Emit a scaling action block and confirm.

    Args:
        service:  Service name to scale.
        replicas: Target replica count.
        host:     Target host name (default ``production-01``).
    """
    from navig.core.renderer import BlockType, renderBlock, sessionClose, sessionOpen

    sessionOpen(host, f"scale  {service}")

    # ── ACTION ────────────────────────────────────────────────────────────────
    renderBlock(
        BlockType.ACTION,
        f"Scale {service} → {replicas} replica(s)",
        body=f"docker service scale {service}={replicas}",
    )

    # Simulate dispatching
    time.sleep(0.05)

    # ── CONFIRM ───────────────────────────────────────────────────────────────
    renderBlock(
        BlockType.CONFIRM,
        f"{service} scaled to {replicas} replica(s) successfully.",
    )

    sessionClose(f"{service} × {replicas}")


@app.command()
def scale_cmd(
    service: str = typer.Argument(..., help="Service name to scale"),
    replicas: int = typer.Argument(..., help="Target replica count"),
    host: str | None = typer.Option(
        None, "--host", "-H", help="Target host (default: production-01)"
    ),
) -> None:
    """Scale a service to a desired replica count."""
    run(service, replicas, host=host or "production-01")
