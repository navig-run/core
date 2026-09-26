"""`navig doctor`'s FTS remedy must name a command the operator can actually run.

The unsafe external-content trigger defect (#530c0a21) is repaired by the owning store's
schema init, so the remedy really is "open that store". But the row said only
"reopening the owning store repairs it" — which names no command, and the operator has no
way to know that the store behind `links.db` is opened by `navig links list`. Deriving
that mapping took reading three modules and the CLI registration table; a diagnostic that
requires source-diving to act on is a diagnostic that does not get acted on.

The risk introduced by naming commands is that they rot: a renamed verb turns helpful
advice into a wild goose chase, and nothing would notice, because the string is only ever
printed on an unhealthy install. So the map is pinned here — every command it names must
resolve to a real registered CLI verb.
"""

from __future__ import annotations

import pytest

from navig.commands.doctor import _FTS_REPAIR_COMMAND, _fts_repair_hint


def _resolve(command: str):
    """Resolve "navig <group> <verb>" through the real CLI registration table."""
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    parts = command.split()
    assert parts[0] == "navig", f"remedy must be a navig command, got {command!r}"
    group, verb = parts[1], parts[2]

    assert group in _EXTERNAL_CMD_MAP, f"`navig {group}` is not a registered command group"
    module_name, app_attr = _EXTERNAL_CMD_MAP[group]
    module = __import__(module_name, fromlist=[app_attr])
    app = getattr(module, app_attr, None)
    assert app is not None, f"{module_name}.{app_attr} does not exist"

    cmds, groups = _names(app)
    return verb, cmds | set(groups)


def _names(app):
    """A Typer's direct verbs and its sub-Typers, by the names the CLI actually accepts."""
    cmds: set[str] = set()
    for info in app.registered_commands:
        cmds.add(info.name or (info.callback.__name__.replace("_", "-") if info.callback else ""))
        if info.callback is not None:
            cmds.add(info.callback.__name__)
    groups = {g.name: g.typer_instance for g in app.registered_groups if g.name}
    return cmds, groups


def _walk(words: list[str]) -> str | None:
    """Walk `navig <group> [<sub>...] <verb>` through the REAL registration, descending
    sub-Typers structurally. Returns None when every word resolves, else why not.

    The first version checked only the second word against the group's direct verbs and
    kept a hand-typed `_SUBGROUPS` allowlist for sub-Typers — which meant
    `navig cdp profile <anything>` passed the moment `profile` was allowlisted, phantom
    verb and all. The leaf is what the operator types; the leaf is what must exist.
    """
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    module_name, app_attr = _EXTERNAL_CMD_MAP[words[0]]
    app = getattr(__import__(module_name, fromlist=[app_attr]), app_attr, None)
    if app is None:
        return f"{module_name}.{app_attr} does not exist"
    path = ["navig", words[0]]
    for w in words[1:]:
        cmds, groups = _names(app)
        if w in groups:
            app = groups[w]
            path.append(w)
            continue
        if w in cmds:
            return None
        return f"`{' '.join(path)}` has no verb `{w}`"
    return None  # a bare group, or a path of groups — a valid thing to print


@pytest.mark.parametrize("db,command", sorted(_FTS_REPAIR_COMMAND.items()))
def test_every_named_remedy_command_exists(db: str, command: str) -> None:
    verb, names = _resolve(command)
    assert verb in names, (
        f"doctor tells the operator to run `{command}` to repair {db}, but `{verb}` is not "
        f"a command of that group (has: {sorted(names)})"
    )


def test_hint_names_the_concrete_command() -> None:
    hint = _fts_repair_hint(["links.db:links_fts"])
    assert "navig links list" in hint


def test_hint_deduplicates_two_tables_in_one_database() -> None:
    """Two offending indexes in one store is still one command to run, not two."""
    hint = _fts_repair_hint(["links.db:links_fts", "links.db:other_fts"])
    assert hint.count("navig links list") == 1


def test_hint_covers_several_stores() -> None:
    hint = _fts_repair_hint(["links.db:links_fts", "knowledge_graph.db:facts_fts"])
    assert "navig links list" in hint
    assert "navig kg status" in hint


def test_an_unknown_database_never_invents_a_command() -> None:
    """A store we have no verb for must degrade to naming the file — never to a guess.
    Inventing `navig mystery list` would send the operator after a command that errors."""
    hint = _fts_repair_hint(["mystery.db:some_fts"])
    assert "mystery.db" in hint
    assert "navig" not in hint, f"a command was invented for an unknown store: {hint}"


def test_a_mixed_batch_reports_both_halves() -> None:
    hint = _fts_repair_hint(["links.db:links_fts", "mystery.db:some_fts"])
    assert "navig links list" in hint
    assert "mystery.db" in hint


def test_hint_is_never_empty() -> None:
    """It is appended after an em dash, so an empty string would leave a dangling
    "… unsafe external-content triggers: links.db:links_fts — " with no advice at all."""
    assert _fts_repair_hint([]).strip()


# ── every command doctor PRINTS, not just the FTS map ────────────────────────
#
# The FTS row was not the only one pointing at a command that does not exist. Sweeping
# doctor's own string literals found two more: the corrupt-database row said
# "navig db backup lists them" (no such verb — the real one, `navig db local backup`,
# WRITES backups rather than listing them) and `doctor migrate-packs` pointed at
# `navig package`, a group that has never been registered. Both are advice given at the
# worst possible moment — one to an operator whose database is corrupt.
#
# Scope is doctor.py alone and deliberately so. The same sweep across all of core/navig
# produced 483 raw hits that were overwhelmingly English prose ("navig differs from",
# "navig itself"), and narrowing to backticked text still left mostly stale module
# docstrings. Doctor's strings are almost entirely operator-facing advice, which is what
# makes a guard here precise instead of a false-positive generator.

import ast  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

# Words that are English following a valid command, not a verb of it.
_PROSE = {
    "lists", "them", "and", "or", "is", "to", "the", "a", "in", "for", "with",
    "reads", "shows", "writes", "first", "then", "if", "when", "that", "it",
}
_CANDIDATE = re.compile(r"navig ((?:[a-z][a-z0-9-]*)(?: [a-z][a-z0-9-]*){0,2})")


def _doctor_source() -> Path:
    import navig.commands.doctor as mod

    return Path(mod.__file__)


def _printed_commands() -> list[tuple[int, list[str]]]:
    """Command-looking text from doctor's STRING LITERALS only.

    AST rather than a line scan: `import navig as _nav` and `from navig import ...` are
    source, not advice, and a textual sweep reports them as broken commands.
    """
    tree = ast.parse(_doctor_source().read_text(encoding="utf-8"))
    out: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        for match in _CANDIDATE.finditer(node.value):
            words = [w for w in match.group(1).split() if w not in _PROSE]
            if words:
                out.append((node.lineno, words))
    return out


def test_the_sweep_finds_something():
    """A scan that silently matches nothing would pass forever."""
    assert len(_printed_commands()) >= 10


def test_every_command_doctor_prints_exists():
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    broken: list[str] = []
    for lineno, words in _printed_commands():
        group = words[0]
        if group not in _EXTERNAL_CMD_MAP and group not in _TOP_LEVEL:
            broken.append(f"doctor.py:{lineno} `navig {' '.join(words)}` — no such command")
            continue
        if len(words) >= 2 and group in _EXTERNAL_CMD_MAP:
            # Every word after the group must resolve — sub-Typers descended structurally,
            # the LEAF verified. Letting a shorter prefix win would swallow `navig db backup`
            # (db valid, backup not) or `navig cdp profile nonsense` (profile valid, leaf not).
            why = _walk(words)
            if why:
                broken.append(f"doctor.py:{lineno} `navig {' '.join(words)}` — {why}")
    assert not broken, "doctor prints advice naming commands that do not exist:\n  " + "\n  ".join(
        broken
    )


# Commands registered on the root app rather than through _EXTERNAL_CMD_MAP.
_TOP_LEVEL = {"doctor", "init", "update", "ai", "import", "vault"}
