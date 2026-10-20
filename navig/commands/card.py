"""``navig card`` — the board's task cards from a terminal, with the coordination rules.

The Deck's HTTP routes already own the write path for humans dragging lanes;
this is the agent-facing surface over the SAME ``BoardStore`` rows (one board,
never a second store). What it adds beyond a thin wrapper is the part the HTTP
surface deliberately does not do, because there every writer is the operator:

* **Role-gated stage moves.** The caller's role comes from the session/owner
  registry (``navig mesh agents claim``) and each role has a transition matrix.
  A reviewer cannot move cards at all; a worker may only take work and submit
  it for review; the orchestrator owns every other edge — landing included.
* **Canonical stage names.** The store's lanes are ``backlog / in_progress /
  agent / done`` (``DEFAULT_STAGES`` — nothing to migrate); the protocol speaks
  ``planned / active / review / done``. ``move_card`` validates nothing, so
  this CLI does: an unknown stage is an error, never a lane-less card.
* **Claims.** ``claim``/``release`` wrap ``BoardStore.claim_card`` — the
  single-statement CAS that stops two sessions owning one card.

``blocked`` is not a stage: the store's derived ``gate`` (``waiting`` while a
dependency is unfinished) carries it, so no lane has to be invented for it.
"""

from __future__ import annotations

from typing import Any

import typer

from navig import console_helper as ch

card_app = typer.Typer(
    help="Board task cards: lifecycle, claims, dependencies — role-gated.",
    no_args_is_help=True,
)

deps_app = typer.Typer(help="Wire/unwire task dependencies (cycle-checked).", no_args_is_help=True)
subtask_app = typer.Typer(help="Acceptance-criteria checklists on a card.", no_args_is_help=True)
card_app.add_typer(deps_app, name="deps")
card_app.add_typer(subtask_app, name="subtask")

#: Protocol stage → store lane key (DEFAULT_STAGES; no settings migration).
CANON_TO_STORE: dict[str, str] = {
    "planned": "backlog",
    "active": "in_progress",
    "review": "agent",
    "done": "done",
}
STORE_TO_CANON: dict[str, str] = {v: k for k, v in CANON_TO_STORE.items()}

#: Which stage transitions each role may perform (store keys).
_ALL = {(a, b) for a in CANON_TO_STORE.values() for b in CANON_TO_STORE.values() if a != b}
_WORKER = {("backlog", "in_progress"), ("in_progress", "agent")}
MOVES: dict[str, set[tuple[str, str]]] = {
    "orchestrator": _ALL,
    "backend-worker": _WORKER,
    "frontend-worker": _WORKER,
    "reviewer": set(),
}


def _store() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


def _canon(store_key: str) -> str:
    return STORE_TO_CANON.get(store_key, store_key)


def _resolve(card_id: str) -> dict[str, Any]:
    """Exact id, then unambiguous prefix — silently acting on the wrong card is
    the one outcome a task surface must never produce (same rule as todo)."""
    card = _store().get_card(card_id)
    if card is not None:
        return card
    matches = [c for c in _store().list_cards() if str(c["id"]).startswith(card_id)]
    if not matches:
        ch.error(f"No card starting with {card_id!r}.", "See them with: navig card list")
        raise typer.Exit(1)
    if len(matches) > 1:
        ch.error(
            f"{card_id!r} matches {len(matches)} cards.",
            " · ".join(f"{c['id']} {c['title']}" for c in matches[:5]),
        )
        raise typer.Exit(1)
    return matches[0]


def _mine(action: str) -> dict[str, Any]:
    """The caller's registry entry — every mutating verb starts here, so an
    unclaimed shell gets a refusal and the one command that fixes it."""
    from navig import agent_registry  # noqa: PLC0415

    entry = agent_registry.find_mine()
    if entry is None:
        ch.error(
            f"{action} needs a claimed role — this session is not in the registry.",
            "Claim one first: navig mesh agents claim --role <orchestrator|backend-worker|frontend-worker|reviewer>",
        )
        raise typer.Exit(1)
    return entry


def _require_role(action: str, *allowed: str) -> dict[str, Any]:
    entry = _mine(action)
    if entry.get("role") not in allowed:
        ch.error(
            f"{action} is for {', '.join(allowed)} — this session is a {entry.get('role')}.",
            "Roles come from: navig mesh agents claim --role <role>",
        )
        raise typer.Exit(1)
    return entry


@card_app.command("add")
def add_cmd(
    title: str = typer.Argument(..., help="Task title (the world's words, not a filename)."),
    notes: str = typer.Option("", "--notes", help="Pointer block: record path, slug, role."),
    stage: str = typer.Option("planned", "--stage", help="planned | active | review | done."),
    priority: str = typer.Option("normal", "--priority", help="low | normal | high | urgent."),
    space: str = typer.Option(None, "--space", help="Space tag (Schema tasks use: schema)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a task card (orchestrator only — assignment is one session's job)."""
    _require_role("Adding a card", "orchestrator")
    if stage not in CANON_TO_STORE:
        ch.error(f"Unknown stage {stage!r}.", f"Stages: {', '.join(CANON_TO_STORE)}")
        raise typer.Exit(1)

    store = _store()
    card = store.create_card(
        title,
        notes=notes or "",
        stage=CANON_TO_STORE[stage],
        priority=priority,
    )
    if space:
        card = store.update_card(card["id"], {"space": space}) or card

    if as_json:
        ch.emit_json({**card, "canonical_stage": _canon(card["stage"])})
        return
    ch.success(f"Card {card['id']}: {card['title']}", f"stage {_canon(card['stage'])}")
    ch.dim("Write the task record next: .navig/tasks/<slug>.md (template: .navig/tasks/README.md)")


@card_app.command("list")
def list_cmd(
    stage: str = typer.Option(None, "--stage", help="Filter: planned | active | review | done."),
    space: str = typer.Option(None, "--space", help="Filter by space tag."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List task cards — their real lanes, gate and holder."""
    if stage is not None and stage not in CANON_TO_STORE:
        ch.error(f"Unknown stage {stage!r}.", f"Stages: {', '.join(CANON_TO_STORE)}")
        raise typer.Exit(1)

    cards = _store().list_cards()
    if stage is not None:
        cards = [c for c in cards if c["stage"] == CANON_TO_STORE[stage]]
    if space is not None:
        cards = [c for c in cards if (c.get("space") or "") == space]

    if as_json:
        ch.emit_json({
            "cards": [{**c, "canonical_stage": _canon(c["stage"])} for c in cards],
            "count": len(cards),
        })
        return

    if not cards:
        ch.info("No cards.", "Create one: navig card add \"<title>\" --space schema")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Stage", no_wrap=True)
    table.add_column("Gate", no_wrap=True)
    table.add_column("Held by", no_wrap=True, style="dim")
    table.add_column("Task")
    for c in cards:
        held = str(c.get("origin_ref") or "")[:8] if c.get("agent_status") == "running" else ""
        gate = "[yellow]waiting[/yellow]" if c.get("gate") == "waiting" else "[green]ready[/green]"
        table.add_row(
            c["id"], _canon(c["stage"]), gate, held or "—", str(c.get("title") or ""),
        )
    ch.console.print(table)
    ch.dim(f"{len(cards)} card(s) · detail: navig card show <id>")


@card_app.command("show")
def show_cmd(
    card_id: str = typer.Argument(..., help="Card id (a prefix is enough)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One card in full: record pointer, dependencies, checklist, stage history."""
    store = _store()
    card = _resolve(card_id)
    payload = {
        **card,
        "canonical_stage": _canon(card["stage"]),
        "subtasks": store.card_subtasks(card["id"]),
        "history": store.card_history(card["id"]),
    }
    if as_json:
        ch.emit_json(payload)
        return

    ch.console.print(f"[bold]{card['id']}[/bold]  {card['title']}")
    ch.console.print(
        f"  stage: {_canon(card['stage'])} ({card['stage']})   gate: {card['gate']}"
        f"   status: {card['agent_status']}   space: {card.get('space') or '—'}"
    )
    if card.get("origin_ref"):
        ch.console.print(f"  held by: {card['origin_ref']} ({card['agent_status']})")
    if card.get("deps"):
        ch.console.print(f"  depends on: {', '.join(card['deps'])}")
    if card.get("notes"):
        ch.console.print("  notes:")
        for line in str(card["notes"]).splitlines():
            ch.console.print(f"    {line}")
    subtasks = payload["subtasks"]
    if subtasks:
        ch.console.print("  checklist:")
        for st in subtasks:
            mark = "x" if st["done"] else " "
            # `\[` so Rich reads a literal bracket (the guard forbids interpolating
            # inside markup — the same escape block.py uses).
            ch.console.print(f"    \\[{mark}] {st['title']}")
    history = payload["history"]
    if history:
        ch.console.print("  history:")
        for h in history:
            ch.console.print(
                f"    {h['created_at']}  {h['from_stage']} → {h['to_stage']}  by {h['actor']}"
            )


@card_app.command("move")
def move_cmd(
    card_id: str = typer.Argument(..., help="Card id (a prefix is enough)."),
    to: str = typer.Option(..., "--to", help="Target stage: planned | active | review | done."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Move a card between lifecycle stages — allowed only inside the caller's role matrix."""
    entry = _mine("Moving a card")
    role = str(entry.get("role"))
    card = _resolve(card_id)

    if to not in CANON_TO_STORE:
        ch.error(f"Unknown stage {to!r}.", f"Stages: {', '.join(CANON_TO_STORE)}")
        raise typer.Exit(1)
    target = CANON_TO_STORE[to]
    current = str(card["stage"])
    if current == target:
        if as_json:
            ch.emit_json({**card, "canonical_stage": _canon(current), "moved": False})
            return
        ch.info(f"Already in {to}.", card["title"])
        return
    if (current, target) not in MOVES.get(role, set()):
        allowed = sorted({_canon(a) + "→" + _canon(b) for a, b in MOVES.get(role, set())})
        ch.error(
            f"A {role} may not move { _canon(current) } → {to}.",
            ("Allowed: " + ", ".join(allowed)) if allowed else "This role moves no cards.",
        )
        raise typer.Exit(1)

    moved = _store().move_card(card["id"], target, actor="agent")
    if moved is None:
        ch.error("That card is gone.")
        raise typer.Exit(1)
    if as_json:
        ch.emit_json({**moved, "canonical_stage": _canon(moved["stage"]), "moved": True})
        return
    ch.success(f"{moved['title']}", f"{_canon(current)} → {_canon(moved['stage'])}")


@card_app.command("claim")
def claim_cmd(
    card_id: str = typer.Argument(..., help="Card id (a prefix is enough)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Take exclusive ownership of a card (atomic CAS — one holder wins)."""
    entry = _mine("Claiming a card")
    card = _resolve(card_id)
    holder = str(entry.get("session_id"))
    if not _store().claim_card(card["id"], holder):
        held = card.get("origin_ref") or "an AI run"
        ch.error(
            "Claim refused — the card is not free.",
            f"Held by: {held} · release it first (its session, or orchestrator --force).",
        )
        raise typer.Exit(1)
    if as_json:
        ch.emit_json({"id": card["id"], "holder": holder, "claimed": True})
        return
    ch.success(f"Claimed {card['id']}: {card['title']}", f"holder {holder[:12]}")


@card_app.command("release")
def release_cmd(
    card_id: str = typer.Argument(..., help="Card id (a prefix is enough)."),
    force: bool = typer.Option(False, "--force", help="Orchestrator adjudication of an abandoned claim."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Release a claim (your own — or, with --force, an abandoned one)."""
    entry = _mine("Releasing a card")
    role = str(entry.get("role"))
    if force and role != "orchestrator":
        ch.error("Only an orchestrator may --force-release a claim.")
        raise typer.Exit(1)
    card = _resolve(card_id)
    holder = str(entry.get("session_id"))
    if not _store().release_card(card["id"], holder, force=force):
        ch.error(
            "Nothing released — this session does not hold the card.",
            f"Held by: {card.get('origin_ref') or 'nobody'}",
        )
        raise typer.Exit(1)
    if as_json:
        ch.emit_json({"id": card["id"], "released": True, "forced": force})
        return
    ch.success(f"Released {card['id']}", "forced by orchestrator" if force else "")


@deps_app.command("add")
def deps_add(
    card_id: str = typer.Argument(..., help="Card that waits."),
    on: str = typer.Option(..., "--on", help="Card it waits for."),
) -> None:
    """Declare a dependency (orchestrator only; cycles are rejected by the store)."""
    _require_role("Wiring dependencies", "orchestrator")
    card, dep = _resolve(card_id), _resolve(on)
    if not _store().add_dependency(card["id"], dep["id"]):
        ch.error(
            "Dependency refused — unknown cards, a self-edge, or a cycle.",
            f"{card['id']} already depends on {dep['id']} transitively? See: navig card show <id>",
        )
        raise typer.Exit(1)
    ch.success(f"{card['id']} now waits on {dep['id']}", "gate closes until it is done")


@deps_app.command("remove")
def deps_remove(
    card_id: str = typer.Argument(..., help="Card that waited."),
    from_dep: str = typer.Option(..., "--from", help="Card it waited for."),
) -> None:
    """Drop a dependency (orchestrator only)."""
    _require_role("Unwiring dependencies", "orchestrator")
    card, dep = _resolve(card_id), _resolve(from_dep)
    if dep["id"] not in (card.get("deps") or []):
        ch.error(f"{card['id']} does not depend on {dep['id']}.")
        raise typer.Exit(1)
    _store().remove_dependency(card["id"], dep["id"])
    ch.success(f"{card['id']} no longer waits on {dep['id']}")


@subtask_app.command("add")
def subtask_add(
    card_id: str = typer.Argument(..., help="Card id (a prefix is enough)."),
    title: str = typer.Argument(..., help="One checkable acceptance criterion."),
) -> None:
    """Add an acceptance criterion (orchestrator seeds the checklist)."""
    _require_role("Seeding acceptance criteria", "orchestrator")
    card = _resolve(card_id)
    sub = _store().add_subtask(card["id"], title)
    ch.success(f"Criterion on {card['id']}", sub["title"])


@subtask_app.command("done")
def subtask_done(subtask_id: str = typer.Argument(..., help="Subtask id (a prefix is enough).")) -> None:
    """Tick one acceptance criterion (never a reviewer — its write surface is the receipt)."""
    entry = _mine("Ticking a criterion")
    if entry.get("role") == "reviewer":
        ch.error("A reviewer ticks nothing — record verdicts with: navig receipt record")
        raise typer.Exit(1)
    store = _store()
    matches = [s for s in store._list_subtasks() if str(s["id"]).startswith(subtask_id)]  # noqa: SLF001
    if len(matches) != 1:
        ch.error(f"{subtask_id!r} matches {len(matches)} criteria.", "Use the full id from: navig card show <card>")
        raise typer.Exit(1)
    updated = store.update_subtask(matches[0]["id"], {"done": True})
    if updated is None:
        ch.error("That criterion is gone.")
        raise typer.Exit(1)
    ch.success("Criterion met", updated["title"])
