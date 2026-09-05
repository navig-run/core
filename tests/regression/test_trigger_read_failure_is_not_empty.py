"""An unreadable triggers.yaml must never read as "you have no triggers".

`TriggerManager.load_failed` was added with a docstring saying exactly why it exists:

    The distinction a caller needs: "you have no triggers" (an answer) versus
    "I could not read your triggers" (not an answer). `list_triggers` returning
    `[]` cannot tell them apart on its own.

…and then only ONE of thirteen consumers read it. The other twelve answered from an
empty manager: `trigger show` said "Trigger not found", `trigger remove` said the same,
`trigger stats` said "No triggers configured" — each of them pointing the user at
re-creating automation that was still sitting on disk.

The proactive engine holds a TriggerManager too, and there it is worse than a wrong
message: `process_event` simply matches nothing, so the operator's automation stops
firing with no output at all. That path now records a config incident, the same way
every other silent-degradation path in this codebase does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import typer

from navig.commands import triggers as triggers_mod

TRIGGERS_SRC = Path(triggers_mod.__file__)

# Commands that answer a question about CONFIGURED TRIGGERS.
_NEEDS_READABLE = {
    "list_triggers",
    "show_trigger",
    "add_trigger_interactive",
    "add_trigger_quick",
    "remove_trigger",
    "enable_trigger",
    "disable_trigger",
    "test_trigger",
    "fire_trigger",
    "show_trigger_stats",
}

# Commands that read history.jsonl — a DIFFERENT file, still answerable while
# triggers.yaml is broken. Refusing these too would be a wider outage than the fault.
_HISTORY_ONLY = {"show_trigger_history", "clear_trigger_history"}


def _manager_constructions() -> dict[str, set[str]]:
    """function name -> the constructor(s) it uses."""
    tree = ast.parse(TRIGGERS_SRC.read_text(encoding="utf-8-sig"))
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"TriggerManager", "_readable_manager"}
        ):
            continue
        fn = node
        while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = parents[fn]
        name = getattr(fn, "name", "?")
        out.setdefault(name, set()).add(node.func.id)
    return out


def test_every_trigger_command_goes_through_the_readable_manager() -> None:
    """The guard. A new `navig trigger <verb>` that constructs TriggerManager directly
    inherits the original bug — it will answer from an empty manager."""
    built = _manager_constructions()
    assert built, "no TriggerManager constructions found — this guard is inert"

    offenders = sorted(
        name
        for name, ctors in built.items()
        if name in _NEEDS_READABLE and ctors != {"_readable_manager"}
    )
    assert not offenders, (
        "These answer a question about configured triggers but build a raw "
        "TriggerManager, so an unreadable triggers.yaml reads as 'none': "
        f"{offenders}. Use _readable_manager()."
    )


def test_the_history_commands_deliberately_do_not_require_a_readable_file() -> None:
    """The other half of the scope decision, pinned so nobody 'completes the sweep'.
    History lives in history.jsonl and stays answerable while triggers.yaml is broken."""
    built = _manager_constructions()
    for name in _HISTORY_ONLY:
        assert built.get(name) == {"TriggerManager"}, (
            f"{name} reads history.jsonl, a different file — making it refuse on an "
            "unreadable triggers.yaml turns a narrow fault into a wider outage"
        )


def test_the_guard_lists_every_command_that_exists() -> None:
    """Anti-vacuity: the two sets above are hand-written, so a new command could be
    added and silently belong to neither."""
    built = set(_manager_constructions()) - {"_readable_manager"}
    unclassified = built - _NEEDS_READABLE - _HISTORY_ONLY
    assert not unclassified, (
        f"new trigger command(s) {sorted(unclassified)} — decide explicitly whether "
        "they need a readable triggers.yaml and add them to a set above"
    )


class _UnreadableManager:
    """A manager whose file could not be read — the state under test."""

    load_failed = True
    triggers_file = Path("/nope/triggers.yaml")


def test_a_read_failure_exits_non_zero_instead_of_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triggers_mod, "TriggerManager", lambda: _UnreadableManager())

    errors: list[str] = []
    monkeypatch.setattr(triggers_mod.ch, "error", lambda m, *a, **k: errors.append(m))

    with pytest.raises(typer.Exit) as excinfo:
        triggers_mod._readable_manager()

    assert excinfo.value.exit_code == 1
    assert errors and "NOT an empty list" in errors[0], (
        "the message must say this is not an empty list — 'no triggers' is the lie"
    )


def test_a_readable_file_is_returned_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The partner. A helper that always refused would satisfy the test above."""

    class _Ok:
        load_failed = False

    sentinel = _Ok()
    monkeypatch.setattr(triggers_mod, "TriggerManager", lambda: sentinel)

    assert triggers_mod._readable_manager() is sentinel


def test_a_read_failure_records_an_incident(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The daemon half. The proactive engine gets no CLI message — an unreadable file
    there is entirely silent, so it has to reach Config Health instead."""
    from navig.core import incidents

    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        incidents, "record", lambda kind, **fields: recorded.append((kind, fields))
    )

    bad = tmp_path / "triggers.yaml"
    bad.write_text("triggers: [unclosed\n", encoding="utf-8")

    manager = triggers_mod.TriggerManager()
    manager.triggers_file = bad
    manager._loaded = False
    manager._ensure_loaded()

    assert manager.load_failed is True
    assert recorded, "an unreadable trigger store recorded no incident"
    kind, fields = recorded[0]
    assert kind == incidents.STORE_READ_FAILED
    assert fields.get("store") == "triggers"


def test_the_new_incident_type_is_described(monkeypatch: pytest.MonkeyPatch) -> None:
    """The notify producer renders from DESCRIPTIONS — an unlisted type pushes an
    empty explanation, which is how a health signal becomes noise."""
    from navig.core import incidents

    assert incidents.STORE_READ_FAILED in incidents.DESCRIPTIONS
    assert incidents.DESCRIPTIONS[incidents.STORE_READ_FAILED].strip()


def test_recording_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A health note must never be the thing that breaks a load."""
    from navig.core import incidents

    def _explode(*a: object, **k: object) -> None:
        raise RuntimeError("incident log is on fire")

    monkeypatch.setattr(incidents, "record", _explode)

    bad = tmp_path / "triggers.yaml"
    bad.write_text("triggers: [unclosed\n", encoding="utf-8")

    manager = triggers_mod.TriggerManager()
    manager.triggers_file = bad
    manager._loaded = False
    manager._ensure_loaded()  # must not raise

    assert manager.load_failed is True
