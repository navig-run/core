"""``navig endpoints`` — one queryable model of every controllable thing.

The list is a projection of the home inventory the operator already maintains
(``navig home``): no second inventory, no provider logins, no YAML in core. Each
row is ``<provider>:<device id>`` plus its typed capabilities and live state.

    navig endpoints list
    navig endpoints list --provider govee --room office --json
    navig endpoints show govee:desk-lamp
    navig endpoints set govee:desk-lamp on_off on
    navig endpoints set govee:desk-lamp set_range 40
    navig endpoints set govee:desk-lamp set_color "#ff8800"

Every command builds one Answer envelope (``navig.ui.answer``): ``speech`` and
``display_text`` for humans, ``table`` for the terminal, ``json`` for scripts,
``suggestions`` for the next step. ``--json`` prints that envelope as exactly one
document through ``console_helper.emit_json``.
"""

from __future__ import annotations

import json as _json

import typer
from rich.markup import escape as _esc

from navig import console_helper as ch
from navig.endpoints.errors import EndpointDirectiveError, EndpointError, HomeUnavailable
from navig.endpoints.model import DIRECTIVES, Capability, Endpoint
from navig.endpoints.store import EndpointStore, SetResult
from navig.ui.answer import Answer

endpoints_app = typer.Typer(
    name="endpoints",
    help="One queryable model of every controllable thing (projection of the home inventory).",
    no_args_is_help=True,
)

_STATUS_TEXT: dict[bool | None, str] = {True: "online", False: "offline", None: "unknown"}

#: Plain table cells the renderer colourises. The Answer ``table`` itself stays
#: plain text (JSON/deck consumers read it); colour is a CLI-only decision.
_CELL_STYLES: dict[str, str] = {
    "online": "[green]● online[/green]",
    "offline": "[red]✗ offline[/red]",
    "unknown": "[dim]○ unknown[/dim]",
    "yes": "[green]yes[/green]",
    "no": "[dim]no[/dim]",
}


def _make_store() -> EndpointStore:
    """Build the production store. Tests patch this to inject a fake bridge."""
    return EndpointStore()


# ── rendering ────────────────────────────────────────────────────────────


def _print_table(table: dict) -> None:
    from navig.console_helper import Table

    columns = [str(column) for column in table.get("columns") or []]
    rows = [list(row) for row in table.get("rows") or []]
    grid = Table(box=None, show_header=True, padding=(0, 2))
    for index, column in enumerate(columns):
        # Short known-width columns never wrap; the last (free-text) one may.
        grid.add_column(column, no_wrap=index < len(columns) - 1)
    for row in rows:
        grid.add_row(*[
            _CELL_STYLES.get(str(cell), _esc(str(cell))) for cell in row
        ])
    ch.console.print(grid)


def _render(answer: Answer, *, tone: str = "info") -> None:
    if answer.table:
        _print_table(answer.table)
    if not answer.ok:
        # Every failure path goes through ``_fail`` (which raises); this branch is
        # a last-resort guarantee that a failed answer never exits 0.
        ch.error(_esc(answer.speech))
        raise typer.Exit(1)
    if tone == "success":
        ch.success(_esc(answer.speech))
    elif tone == "warning":
        ch.warning(_esc(answer.speech))
    else:
        ch.info(_esc(answer.speech))
    hint = str(answer.data.get("hint") or "")
    if hint:
        ch.dim(_esc(hint))
    next_step = str(answer.data.get("next_step") or "")
    if next_step:
        ch.dim(_esc(next_step))


def _emit(answer: Answer, as_json: bool) -> None:
    """Print the answer; ``--json`` owns stdout, humans get the table."""
    if as_json:
        ch.emit_json(answer.as_dict())
        return
    _render(answer)


def _fail(error: EndpointError, *, as_json: bool, code: int = 1) -> typer.Exit:
    message = str(error)
    hint = getattr(error, "hint", "") or ""
    answer = Answer(
        ok=False,
        speech=message,
        display_text=message,
        json={"ok": False, "error": message, "hint": hint},
        data={"error": message, "hint": hint},
        suggestions=[hint] if hint else [],
    )
    if as_json:
        ch.emit_json(answer.as_dict())
        return typer.Exit(code)
    ch.error(_esc(message))
    if hint:
        ch.dim(_esc(hint))
    return typer.Exit(code)


# ── cells ────────────────────────────────────────────────────────────────


def _online_cell(endpoint: Endpoint) -> str:
    return _STATUS_TEXT.get(endpoint.status.online, _STATUS_TEXT[None])


def _capability_name(capability: Capability) -> str:
    """The short, terminal-friendly name of one capability.

    The type alone is ambiguous for a colour light: ``rgb`` and ``temperature_k`` are
    both ``devices.capabilities.color_setting``. Append the instance whenever it adds
    information (``range:brightness``, ``color:rgb``) but not when it merely repeats the
    type or says ``on`` (``on_off``, ``toggle``).
    """
    prefix = "devices.capabilities."
    if capability.type.startswith(prefix):
        name = capability.type[len(prefix):]
        if name == "color_setting":
            name = "color"
        instance = str(capability.instance or "").strip()
        if instance and instance not in {name, "on", "mode"}:
            return f"{name}:{instance}"
        return name
    if capability.type.startswith("devices.properties."):
        return f"property:{capability.type[len('devices.properties.'):]}"
    return capability.type


def _state_cell(capability: Capability) -> str:
    state = capability.state
    if not state:
        return "—"
    if "on" in state:
        return "on" if state["on"] else "off"
    if "value" in state:
        return str(state["value"])
    if "rgb" in state:
        return str(state["rgb"])
    if "temperature_k" in state:
        return f"{state['temperature_k']} K"
    if "mode" in state:
        return str(state["mode"])
    return _json.dumps(state, sort_keys=True)


def _capabilities_cell(endpoint: Endpoint) -> str:
    names = [_capability_name(capability) for capability in endpoint.capabilities]
    return ", ".join(names) if names else "—"


def _value_text(value: object) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return "—"
    return str(value)


# ── answers ──────────────────────────────────────────────────────────────


def _list_answer(store: EndpointStore, provider: str, room: str) -> Answer:
    endpoints = store.list(provider=provider or None, room=room or None)
    unmapped = store.unmapped()
    reachability = store.readiness()
    providers = sorted({endpoint.id.split(":", 1)[0] for endpoint in endpoints})
    unmapped_payload = {
        "total": sum(unmapped.values()),
        "by_capability": unmapped,
    }
    payload = {
        "ok": True,
        "count": len(endpoints),
        "providers": providers,
        "endpoints": [endpoint.as_dict() for endpoint in endpoints],
        "unmapped": unmapped_payload,
        "reachability": reachability,
        "filters": {"provider": provider, "room": room},
    }
    if endpoints:
        speech = f"{len(endpoints)} endpoint(s) across {len(providers)} provider(s)"
        next_step = (
            "next: navig endpoints show <id>  ·  "
            "navig endpoints set <id> on_off on"
        )
        table = {
            "columns": ["Endpoint", "Type", "Room", "Status", "Capabilities"],
            "rows": [
                [
                    endpoint.id,
                    endpoint.type.replace("devices.types.", ""),
                    endpoint.room or "—",
                    _online_cell(endpoint),
                    _capabilities_cell(endpoint),
                ]
                for endpoint in endpoints
            ],
        }
    else:
        speech = "no endpoints — the home inventory is empty"
        next_step = (
            "next: navig home import --from ha  ·  "
            'navig home add "Desk lamp" --provider govee --address <ip>'
        )
        table = None
    return Answer(
        speech=speech,
        display_text=speech,
        ok=True,
        table=table,
        json=payload,
        data={
            "count": len(endpoints),
            "providers": providers,
            "unmapped": unmapped_payload,
            "reachability": reachability,
            "next_step": next_step,
            "hint": "",
        },
        suggestions=["navig endpoints show <id>", "navig home status"],
        intent="endpoints.list",
    )


def _show_answer(endpoint: Endpoint) -> Answer:
    rows = [
        [
            _capability_name(capability),
            capability.instance,
            _state_cell(capability),
            ", ".join(capability.meta.supported_directives) or "read-only",
        ]
        for capability in endpoint.capabilities
    ]
    directives = sorted({
        name
        for capability in endpoint.capabilities
        for name in capability.meta.supported_directives
    })
    speech = (
        f"{endpoint.id} · {_STATUS_TEXT[endpoint.status.online]} "
        f"· {len(endpoint.capabilities)} capability(ies)"
    )
    next_step = (
        f"next: navig endpoints set {endpoint.id} <directive> <value>"
        if directives
        else "next: navig endpoints list"
    )
    payload = {
        "ok": True,
        "endpoint": endpoint.as_dict(),
        "directives": directives,
    }
    return Answer(
        speech=speech,
        display_text=speech,
        ok=True,
        table={
            "columns": ["Capability", "Instance", "State", "Directives"],
            "rows": rows,
        },
        json=payload,
        data={
            "endpoint": endpoint.as_dict(),
            "directives": directives,
            "next_step": next_step,
            "hint": "",
        },
        suggestions=[f"navig endpoints set {endpoint.id} {name}" for name in directives[:2]],
        intent="endpoints.show",
    )


def _set_answer(result: SetResult) -> Answer:
    endpoint = result.endpoint
    value_text = _value_text(result.value)
    if result.changed:
        speech = f"{endpoint.id}: {result.directive} → {value_text}"
        tone = "success"
    else:
        speech = f"{endpoint.id}: already {value_text} — nothing changed"
        tone = "info"
    payload = {
        "ok": True,
        "changed": result.changed,
        "directive": result.directive,
        "value": result.value,
        "endpoint": endpoint.as_dict(),
        "provider": result.payload,
    }
    return Answer(
        speech=speech,
        display_text=speech,
        ok=True,
        table={
            "columns": ["Field", "Value"],
            "rows": [
                ["Endpoint", endpoint.id],
                ["Directive", result.directive],
                ["Value", value_text],
                ["Changed", "yes" if result.changed else "no"],
                ["Status", _online_cell(endpoint)],
            ],
        },
        json=payload,
        data={
            "changed": result.changed,
            "directive": result.directive,
            "value": result.value,
            "endpoint": endpoint.as_dict(),
            "tone": tone,
            "next_step": f"next: navig endpoints show {endpoint.id}",
            "hint": "",
        },
        suggestions=[f"navig endpoints show {endpoint.id}"],
        intent="endpoints.set",
    )


# ── commands ─────────────────────────────────────────────────────────────


@endpoints_app.command("list")
def list_cmd(
    provider: str = typer.Option("", "--provider", "-p", help="Only this provider."),
    room: str = typer.Option("", "--room", "-r", help="Only this room."),
    json_output: bool = typer.Option(False, "--json", help="Print the Answer envelope as JSON."),
) -> None:
    """List every controllable endpoint with its typed capabilities."""
    store = _make_store()
    try:
        answer = _list_answer(store, provider.strip(), room.strip())
    except HomeUnavailable as exc:
        raise _fail(exc, as_json=json_output) from exc
    except EndpointError as exc:
        raise _fail(exc, as_json=json_output) from exc
    if json_output:
        _emit(answer, as_json=True)
        return
    _render(answer, tone="info" if answer.table else "warning")


@endpoints_app.command("show")
def show_cmd(
    endpoint: str = typer.Argument(..., help="Endpoint id, e.g. govee:desk-lamp."),
    json_output: bool = typer.Option(False, "--json", help="Print the Answer envelope as JSON."),
) -> None:
    """Show one endpoint: live state and the directives it supports."""
    store = _make_store()
    try:
        answer = _show_answer(store.show(endpoint.strip()))
    except HomeUnavailable as exc:
        raise _fail(exc, as_json=json_output) from exc
    except EndpointError as exc:
        raise _fail(exc, as_json=json_output) from exc
    _emit(answer, as_json=json_output)


@endpoints_app.command("set")
def set_cmd(
    endpoint: str = typer.Argument(..., help="Endpoint id, e.g. govee:desk-lamp."),
    directive: str = typer.Argument(
        ..., help="One of: " + ", ".join(DIRECTIVES) + "."
    ),
    value: str | None = typer.Argument(
        None, help="Directive value: on/off, a number, #rrggbb, or a mode name."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the Answer envelope as JSON."),
) -> None:
    """Apply one directive through the home plugin (idempotent; a replay changes nothing)."""
    store = _make_store()
    wanted = directive.strip().lower()
    if wanted not in DIRECTIVES:
        error = EndpointDirectiveError(
            f"unknown directive {directive!r}",
            hint="directives: " + ", ".join(DIRECTIVES),
        )
        raise _fail(error, as_json=json_output, code=2)
    try:
        result = store.set(endpoint.strip(), wanted, value)
        answer = _set_answer(result)
    except EndpointDirectiveError as exc:
        raise _fail(exc, as_json=json_output, code=2) from exc
    except HomeUnavailable as exc:
        raise _fail(exc, as_json=json_output) from exc
    except EndpointError as exc:
        raise _fail(exc, as_json=json_output) from exc
    _emit(answer, as_json=json_output)
