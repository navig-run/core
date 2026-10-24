"""navig coordination — inspect and drive the durable inter-agent/inter-session mailbox.

Read/transition the collective-coordination store (``~/.navig/data/store/coordination.db``).
This is the CLI surface over the same Core operations the deck API and MCP expose; sending
a message is data, never a tool execution, and every transition is a real, audited state
change.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.table import Table

from navig.console_helper import get_console

coordination_app = typer.Typer(
    help="Inspect and drive collective coordination (inter-agent/inter-session mailbox)",
    no_args_is_help=False,
)
console = get_console()

_INBOX_COLS = ("sender", "kind", "state", "created", "content")


def _messages_table(messages: list[dict[str, Any]]) -> Table:
    table = Table(box=None, show_header=True, padding=(0, 2))
    for col in _INBOX_COLS:
        table.add_column(col, no_wrap=col != "content")
    for m in messages:
        table.add_row(
            str(m.get("sender") or ""),
            str(m.get("kind") or ""),
            str(m.get("delivery_state") or ""),
            str(m.get("created_at") or "")[:19],
            _content_summary(m.get("content")),
        )
    return table


def _content_summary(content: Any) -> str:
    if not content:
        return ""
    text = json.dumps(content, ensure_ascii=False)
    return text if len(text) <= 80 else text[:77] + "…"


def _emit(data: Any, *, as_json: bool) -> None:
    if as_json:
        # Plain stdout, no rich markup: `--json` output must be parseable (house contract).
        print(json.dumps(data, ensure_ascii=False, default=str))
    elif isinstance(data, str):
        console.print(data)


@coordination_app.callback(invoke_without_command=True)
def _callback(ctx: typer.Context) -> None:
    """Show the operator's coordination inbox when run with no subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.coordination import list_inbox

        rows = list_inbox("operator")
        if not rows:
            console.print("[dim]No coordination messages for operator.[/dim]")
            return
        console.print(_messages_table(rows))


@coordination_app.command("inbox")
def inbox(
    recipient: str = typer.Argument("operator", help="Participant ref, e.g. session:<id>, agent:<id>, operator"),
    states: str = typer.Option("", help="Comma-separated delivery states to filter by"),
    limit: int = typer.Option(100, help="Maximum messages to return"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List a participant's durable coordination messages."""
    from navig.coordination import list_inbox

    state_tuple = tuple(s for s in (x.strip() for x in states.split(",")) if s) or None
    rows = list_inbox(recipient, states=state_tuple, limit=limit)
    _emit({"recipient": recipient, "count": len(rows), "messages": rows}, as_json=json_)
    if not json_ and rows:
        console.print(_messages_table(rows))


@coordination_app.command("send")
def send(
    recipient: str = typer.Argument(..., help="Participant ref to address"),
    content: str = typer.Option(..., help="Message content as a JSON object string"),
    kind: str = typer.Option("inform", help="inform|request|delegate|question|answer|progress|result|handoff|review|approval_request|alert"),
    sender: str = typer.Option("operator", help="Sending participant ref"),
    idempotency_key: str = typer.Option(None, help="Stable key; a retried send returns the original"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Send one coordination message (operator-authorized; data only, never executed)."""
    from navig.coordination import CoordinationError
    from navig.coordination import send as _send

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        console.print("[red]✗ content is not valid JSON[/red]")
        raise typer.Exit(1)
    try:
        message = _send(sender, recipient, kind, payload, idempotency_key=idempotency_key)
    except CoordinationError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    _emit(message, as_json=json_)
    if not json_:
        console.print(f"[green]● {message['sender']} → {message['recipient']} ({message['kind']}) {message['delivery_state']}[/green]")


@coordination_app.command("tick")
def tick(
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Run one delivery pass (route persisted messages, retry/expire/dead-letter)."""
    from navig.coordination import delivery

    outcomes = delivery.tick()
    _emit({"count": len(outcomes), "outcomes": [o.__dict__ for o in outcomes]}, as_json=json_)
    if not json_:
        for o in outcomes:
            glyph = "●" if o.state == "delivered" else ("✗" if o.state in ("failed", "expired") else "○")
            console.print(f"[dim]{glyph} {o.message_id} → {o.state}[/dim]{(' ' + o.error) if o.error else ''}")


@coordination_app.command("transition")
def transition(
    message_id: str = typer.Argument(..., help="Message id to transition"),
    verb: str = typer.Argument(..., help="deliver|consume|ack|act|cancel"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Advance one message's delivery state (forward-only, terminal states block)."""
    from navig import coordination
    from navig.coordination import CoordinationError

    if verb not in ("deliver", "consume", "ack", "act", "cancel"):
        console.print("[red]✗ unknown transition[/red]")
        raise typer.Exit(1)
    try:
        updated = getattr(coordination, verb)(message_id)
    except CoordinationError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    _emit(updated, as_json=json_)
    if not json_:
        console.print(f"[green]● {message_id} → {updated['delivery_state']}[/green]")
