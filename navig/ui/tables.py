"""
navig.ui.tables — Rich table renderers for findings and fleet views.

Uses box.SIMPLE / box.MINIMAL — never heavy borders.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from rich.table import Table
from rich import box

from navig.ui.models import CauseScore
from navig.ui.theme import SEVERITY_STYLE, COLOR_STYLE, console


def render_findings_table(
    findings: List[CauseScore],
    *,
    title: str = "Findings",
    show_header: bool = True,
) -> None:
    """Render a severity-colored findings table. Never raises."""
    try:
        if not findings:
            return
        table = Table(
            title=title if title else None,
            box=box.SIMPLE,
            show_header=show_header,
            header_style="bold dim",
            padding=(0, 1),
        )
        table.add_column("Conf", style="dim", width=6, justify="right")
        table.add_column("Severity", width=10)
        table.add_column("Description")

        for f in findings:
            sev_style = SEVERITY_STYLE.get(f.severity, "white")
            table.add_row(
                f"{f.confidence}%",
                f"[{sev_style}]{f.severity}[/{sev_style}]",
                f.description,
            )
        console.print(table)
    except Exception:
        try:
            print(f"  {title}", file=sys.stdout)
            for f in findings:
                print(f"  [{f.severity}] {f.confidence}%  {f.description}", file=sys.stdout)
        except Exception:
            pass


def render_fleet_table(
    nodes: List[dict],
    *,
    title: str = "Fleet",
    columns: Optional[List[str]] = None,
) -> None:
    """Render a fleet/peer node table. Each node is a dict of col→value.
    Never raises."""
    try:
        if not nodes:
            console.print("[dim]No nodes found.[/dim]")
            return
        cols = columns or list(nodes[0].keys())
        table = Table(
            title=title if title else None,
            box=box.SIMPLE,
            show_header=True,
            header_style="bold dim",
            padding=(0, 1),
        )
        for col in cols:
            table.add_column(col)
        for node in nodes:
            row = []
            for col in cols:
                val = str(node.get(col, ""))
                # color-code status/state columns
                col_lower = col.lower()
                if col_lower in ("status", "state"):
                    if val.lower() in ("online", "ok", "running", "active"):
                        val = f"[green]{val}[/green]"
                    elif val.lower() in ("offline", "down", "stopped"):
                        val = f"[red]{val}[/red]"
                    elif val.lower() in ("warn", "warning", "degraded"):
                        val = f"[yellow]{val}[/yellow]"
                row.append(val)
            table.add_row(*row)
        console.print(table)
    except Exception:
        try:
            print(f"  {title}", file=sys.stdout)
            for node in nodes:
                print("  " + "  ".join(str(v) for v in node.values()), file=sys.stdout)
        except Exception:
            pass
