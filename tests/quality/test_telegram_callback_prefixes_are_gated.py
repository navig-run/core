"""Every Telegram callback-button prefix belongs to an extension, or says why not.

Companion to ``test_telegram_extension_coverage.py`` (which does the same for
slash commands).  Without this guard a new inline button could be added with a
fresh ``callback_data`` prefix and would silently keep working after its feature
was switched off -- the "a guard protects a PATH, not the SURFACE" defect this
repo repeats more than any other.

AST, never regex: a prefix named only in a comment, a docstring or a log line
must not count as wiring, and the reverse -- a real routing branch written across
two lines -- must not be missed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from navig.gateway.channels import telegram_extensions as tx

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "core" / "navig"
SCAN_DIRS = (CORE / "gateway" / "channels", CORE / "telegram")

#: Variables that hold a WHOLE callback payload at top-level dispatch.
#: Deliberately narrow.  ``action``/``data`` are excluded because handlers compare
#: those AFTER their prefix has been stripped (``habit_actions`` tests
#: ``action == "t"``, the audio menu tests ``action == "speed"``), and those
#: sub-tokens are not prefixes -- including them would flood the guard with noise
#: and train people to exempt real findings.
_ROUTING_NAMES = frozenset({"cb_data", "callback_data"})

# Anti-vacuity floors: a scan that silently reads nothing looks like a clean run.
_MIN_ROUTING = 25
_MIN_EMITTING = 60
_MIN_FILES = 20


def _py_files() -> list[Path]:
    return sorted({p for d in SCAN_DIRS if d.is_dir() for p in d.rglob("*.py")})


def _collect() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (routing literals, emitting literals), each mapped to source files."""
    routing: dict[str, set[str]] = {}
    emitting: dict[str, set[str]] = {}

    def _note(bucket: dict[str, set[str]], lit: object, where: Path) -> None:
        if isinstance(lit, str) and lit:
            bucket.setdefault(lit, set()).add(where.name)

    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            # ROUTING: cb_data.startswith("lit") / .startswith(("a", "b"))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in _ROUTING_NAMES
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        _note(routing, arg.value, path)
                    elif isinstance(arg, ast.Tuple):
                        for el in arg.elts:
                            if isinstance(el, ast.Constant):
                                _note(routing, el.value, path)
            # ROUTING: cb_data == "lit" / cb_data in ("a", "b")
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
                if node.left.id in _ROUTING_NAMES:
                    for op, comp in zip(node.ops, node.comparators):
                        if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                            _note(routing, comp.value, path)
                        elif isinstance(op, ast.In) and isinstance(
                            comp, (ast.Tuple, ast.List, ast.Set)
                        ):
                            for el in comp.elts:
                                if isinstance(el, ast.Constant):
                                    _note(routing, el.value, path)
            # EMITTING: {"callback_data": "lit"} or {"callback_data": f"lit{...}"}
            if isinstance(node, ast.Dict):
                for key, val in zip(node.keys, node.values):
                    if not (isinstance(key, ast.Constant) and key.value == "callback_data"):
                        continue
                    if isinstance(val, ast.Constant):
                        _note(emitting, val.value, path)
                    elif isinstance(val, ast.JoinedStr) and val.values:
                        head = val.values[0]
                        if isinstance(head, ast.Constant):
                            _note(emitting, head.value, path)
    return routing, emitting


@pytest.fixture(scope="module")
def collected() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    return _collect()


def _unclaimed(literals: dict[str, set[str]]) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for lit, where in sorted(literals.items()):
        if any(lit.startswith(u) for u in tx.UNGATED_PREFIXES):
            continue
        if tx.extension_for_callback(lit) is None:
            out.append((lit, sorted(where)))
    return out


def test_the_scan_is_not_vacuous(collected) -> None:
    routing, emitting = collected
    assert len(_py_files()) >= _MIN_FILES
    assert len(routing) >= _MIN_ROUTING, (
        f"only {len(routing)} routing literals found (floor {_MIN_ROUTING}) -- "
        "the AST extractor has probably drifted"
    )
    assert len(emitting) >= _MIN_EMITTING, (
        f"only {len(emitting)} emitting literals found (floor {_MIN_EMITTING})"
    )


def test_every_routed_prefix_belongs_to_an_extension(collected) -> None:
    routing, _ = collected
    unclaimed = _unclaimed(routing)
    assert not unclaimed, (
        "these callback prefixes are ROUTED but belong to no extension, so the "
        f"buttons keep working when their feature is switched off:\n{unclaimed}\n"
        "Fix: add the prefix to the owning TelegramExtension.callback_prefixes in "
        "core/navig/gateway/channels/telegram_extensions.py, or to UNGATED_PREFIXES "
        "with a written reason if it must always answer."
    )


def test_every_emitted_prefix_belongs_to_an_extension(collected) -> None:
    """Emitting matters as much as routing: an unclaimed prefix cannot be stripped.

    ``filter_keyboard`` only removes buttons whose prefix it recognises, so an
    unclaimed prefix means that button stays on the card after its extension is
    switched off.
    """
    _, emitting = collected
    unclaimed = _unclaimed(emitting)
    assert not unclaimed, (
        "these callback prefixes are EMITTED into keyboards but belong to no "
        f"extension, so filter_keyboard cannot strip them:\n{unclaimed}\n"
        "Fix: add each to the owning TelegramExtension.callback_prefixes."
    )


def test_prose_does_not_count_as_wiring(tmp_path) -> None:
    """A prefix mentioned only in a comment/docstring/log must not be collected.

    This is the discriminator that justifies the AST: a textual scan would read
    this module's own documentation as if it were routing.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""A docstring mentioning zz_ghost: and cb_data.startswith("zz_ghost:")."""\n'
        "# cb_data.startswith('zz_comment:')\n"
        "import logging\n"
        "logging.getLogger(__name__).info('zz_log: %s', 1)\n"
        "def f(cb_data):\n"
        '    return cb_data.startswith("zz_real:")\n',
        encoding="utf-8",
    )
    tree = ast.parse(probe.read_text(encoding="utf-8"))
    found = {
        a.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "startswith"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id in _ROUTING_NAMES
        for a in n.args
        if isinstance(a, ast.Constant)
    }
    assert found == {"zz_real:"}, f"AST discriminator broken: {found}"


def test_ungated_prefixes_have_no_ghosts(collected) -> None:
    """An exemption for a prefix nothing emits any more is stale scope."""
    routing, emitting = collected
    seen = set(routing) | set(emitting)
    stale = sorted(
        p for p in tx.UNGATED_PREFIXES
        if not any(lit.startswith(p) or p.startswith(lit) for lit in seen)
    )
    assert not stale, (
        f"UNGATED_PREFIXES entries that nothing emits or routes any more: {stale}. "
        "Delete them rather than leaving the exemption list as a graveyard."
    )


def test_slash_prefix_is_resolved_dynamically(collected) -> None:
    """``slash:`` must never be claimed statically -- it re-dispatches any command."""
    core = tx.get("core")
    assert core is not None
    assert "slash:" not in core.callback_prefixes
    assert not any("slash:" in e.callback_prefixes for e in tx.EXTENSIONS)
    # And it must still resolve, so it is never reported as an undeclared prefix.
    _, emitting = collected
    for lit in emitting:
        if lit.startswith("slash:"):
            assert tx.extension_for_callback(lit) is not None, lit
