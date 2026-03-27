"""
navig ask — AI-powered host diagnostics.

Usage::

    navig ask "why is nginx slow?"
    navig ask "disk usage high" --host web-01
"""

from __future__ import annotations

import time
from typing import Optional

import typer

from navig.ui import renderer

app = typer.Typer(
    name="ask",
    help="Ask a natural-language question about the active host.",
    no_args_is_help=True,
)


def run(question: str, host: str = "production-01") -> None:
    """Execute a diagnostic pipeline using navig.ui.renderer.

    Args:
        question: Natural language diagnostic question.
        host:     Target host name (default ``production-01``).
    """
    # Layer 1: status header
    renderer.render_status_header(
        [
            renderer.StatusChip(
                icon=renderer.icon("host"),
                icon_safe="[h]",
                label="host",
                value=host,
                color="cyan",
            ),
            renderer.StatusChip(
                icon=renderer.icon("ai"),
                icon_safe="[ai]",
                label="ask",
                value=f'"{question}"',
                color="magenta",
            ),
        ]
    )

    time.sleep(0.05)  # representative I/O latency

    # Layer 3: metrics
    renderer.render_metric_bars(
        [
            renderer.Metric(
                label="cpu_usage", value="78%", bar_fill=0.78, color="yellow"
            ),
            renderer.Metric(
                label="memory_usage", value="61%", bar_fill=0.61, color="cyan"
            ),
            renderer.Metric(
                label="worker_connections",
                value="412/512",
                bar_fill=0.80,
                color="yellow",
            ),
            renderer.Metric(
                label="request_queue", value="88/500", bar_fill=0.18, color="green"
            ),
            renderer.Metric(
                label="p99_latency_ms", value="340ms", bar_fill=0.68, color="yellow"
            ),
        ],
        title="Host Metrics",
    )

    # Layer 3: root cause
    renderer.render_explanation(
        [
            renderer.CauseScore(
                confidence=85,
                description="nginx worker_connections at 412/512 (80.5%) — nearing limit",
                severity="warn",
            ),
            renderer.CauseScore(
                confidence=62,
                description="p99 latency 340 ms — rising under load",
                severity="info",
            ),
            renderer.CauseScore(
                confidence=55,
                description="CPU 78% — active request processing overhead",
                severity="info",
            ),
        ],
        title="Root Cause Analysis",
    )

    # Layer 4: actions
    renderer.render_actions(
        [
            renderer.ActionItem(
                index=1,
                description="Increase worker_connections to 1024 in /etc/nginx/nginx.conf",
                estimated_value="↓ latency ~30%",
                risk="low",
            ),
            renderer.ActionItem(
                index=2,
                description="Reload nginx:  sudo nginx -s reload",
                risk="low",
            ),
        ]
    )

    # Layer 4: recommended next step
    renderer.render_next_step("navig run --b64 <encoded_nginx_reload>")


@app.command()
def ask_cmd(
    question: str = typer.Argument(..., help="Natural-language diagnostic question"),
    host: Optional[str] = typer.Option(
        None, "--host", "-H", help="Target host (default: production-01)"
    ),
) -> None:
    """Ask a natural-language question about the active host."""
    run(question, host=host or "production-01")
