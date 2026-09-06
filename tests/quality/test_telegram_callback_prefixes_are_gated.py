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

#: The SECOND dispatch path, used for the LIVENESS rule only (never for gating).
#: Some buttons are routed not by a prefix branch but through the callback STORE:
#: `CallbackHandler` looks the payload up and dispatches on `entry.action`. Judged
#: on `cb_data` alone, those look dead. Measured: the naive rule reports 17 findings,
#: of which 3 are the store-backed `heal_*` family and genuinely live. With this
#: bucket the count is 14, all real.
#:
#: Kept OUT of `_ROUTING_NAMES` deliberately -- for the GATING question these are
#: post-strip sub-tokens (`action == "t"`), not prefixes, exactly as that constant's
#: docstring says.
_ACTION_NAMES = frozenset({"action"})

#: An action literal shorter than this never vouches for a prefix. Measured
#: 2026-09-06: the dead set is IDENTICAL at min-length 1, 3, 4 and 5, while every
#: real rescue (`heal_fix`, `heal_diag`, `heal_explain`) is >= 8 characters. The
#: floor costs nothing and stops one-character tokens -- `"t"`, `"i"`, `"x"` are all
#: in the collected set -- from silently vouching for an unrelated prefix forever.
_MIN_LIVENESS_ACTION_LEN = 3

#: Prefixes that are EMITTED and deliberately routed by nothing, each with a written
#: reason.
#:
#: DELIBERATELY EMPTY. An entry here asserts that a button a user can SEE and TAP is
#: supposed to do nothing, which is nearly always a bug report wearing an exemption's
#: clothes. `test_dead_on_purpose_has_no_ghosts` deletes stale rows so this cannot
#: quietly become a graveyard.
DEAD_ON_PURPOSE: dict[str, str] = {}

# Anti-vacuity floors: a scan that silently reads nothing looks like a clean run.
_MIN_ROUTING = 25
_MIN_EMITTING = 60
_MIN_ACTION = 40
_MIN_FILES = 20


def _py_files() -> list[Path]:
    return sorted({p for d in SCAN_DIRS if d.is_dir() for p in d.rglob("*.py")})


def _collect() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """Return (routing, emitting, action) literals, each mapped to source files."""
    routing: dict[str, set[str]] = {}
    emitting: dict[str, set[str]] = {}
    actions: dict[str, set[str]] = {}

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
            # ACTION: entry.action == "lit" / action.startswith("lit") / in (...)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in _ACTION_NAMES
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        _note(actions, arg.value, path)
                    elif isinstance(arg, ast.Tuple):
                        for el in arg.elts:
                            if isinstance(el, ast.Constant):
                                _note(actions, el.value, path)
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
                if node.left.id in _ACTION_NAMES:
                    for op, comp in zip(node.ops, node.comparators):
                        if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                            _note(actions, comp.value, path)
                        elif isinstance(op, ast.In) and isinstance(
                            comp, (ast.Tuple, ast.List, ast.Set)
                        ):
                            for el in comp.elts:
                                if isinstance(el, ast.Constant):
                                    _note(actions, el.value, path)
    return routing, emitting, actions


@pytest.fixture(scope="module")
def collected() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
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
    routing, emitting, actions = collected
    assert all(d.is_dir() for d in SCAN_DIRS), (
        f"a scan root does not exist: {[str(d) for d in SCAN_DIRS if not d.is_dir()]} "
        "-- a miscomputed root makes every count below it plausible and meaningless"
    )
    assert len(_py_files()) >= _MIN_FILES
    assert len(routing) >= _MIN_ROUTING, (
        f"only {len(routing)} routing literals found (floor {_MIN_ROUTING}) -- "
        "the AST extractor has probably drifted"
    )
    assert len(emitting) >= _MIN_EMITTING, (
        f"only {len(emitting)} emitting literals found (floor {_MIN_EMITTING})"
    )
    assert len(actions) >= _MIN_ACTION, (
        f"only {len(actions)} action literals found (floor {_MIN_ACTION}) -- the "
        "liveness rule's second recogniser is reading nothing, which would report "
        "store-dispatched buttons as dead"
    )


def test_every_routed_prefix_belongs_to_an_extension(collected) -> None:
    routing, _, _ = collected
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
    _, emitting, _ = collected
    unclaimed = _unclaimed(emitting)
    assert not unclaimed, (
        "these callback prefixes are EMITTED but nothing routes them, so every "
        "tap falls through to the callback store and answers Button expired:"
        + "\n  "
        + "\n  ".join(f"{lit}  (emitted from {', '.join(w)})" for lit, w in dead)
        + "\n\nAdd a branch in CallbackHandler.handle "
        "(telegram_keyboards.py), or stop emitting the button."
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
    routing, emitting, actions = collected
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
    _, emitting, _ = collected
    for lit in emitting:
        if lit.startswith("slash:"):
            assert tx.extension_for_callback(lit) is not None, lit


# ── Liveness: an emitted prefix must be routed by SOMETHING ───────────────────
# The rules above prove a prefix is GATED. They never asked whether anything
# ROUTES it, so a button could be correctly gated and still do nothing when
# tapped. Measured 2026-09-06: 14 such prefixes, every one of them a button a
# user can see -- `app_use:` from the /apps card, and 13 `fmt:*` from the
# /format settings card, all answering "Button expired".


def _live_prefixes(
    routing: dict[str, set[str]], actions: dict[str, set[str]]
) -> set[str]:
    """Literals something actually dispatches on, by either mechanism."""
    live = set(routing)
    live |= {a for a in actions if len(a) >= _MIN_LIVENESS_ACTION_LEN}
    return live


def _dead_emitted(
    routing: dict[str, set[str]],
    emitting: dict[str, set[str]],
    actions: dict[str, set[str]],
) -> list[tuple[str, list[str]]]:
    live = _live_prefixes(routing, actions)
    out: list[tuple[str, list[str]]] = []
    for lit, where in sorted(emitting.items()):
        if lit in DEAD_ON_PURPOSE:
            continue
        # Either direction counts: a router may match a shorter prefix of what is
        # emitted (`fmt:` routes `fmt:h1`), and an f-string's collected HEAD may be
        # shorter than the literal a router names (`hb:` head vs `hb:t:` branch).
        if any(lit.startswith(r) or r.startswith(lit) for r in live):
            continue
        out.append((lit, sorted(where)))
    return out


def test_every_emitted_prefix_is_actually_routed(collected) -> None:
    """A button that is emitted but routed by nothing answers "Button expired".

    `CallbackHandler.handle` falls through to `self.store.get(cb_data)`, which misses
    for a payload no branch claimed -- so the tap is swallowed with a generic toast
    and no error anywhere. That is why `fmt:` rotted unnoticed through a whole
    settings card, and `app_use:` through the /apps card.
    """
    routing, emitting, actions = collected
    dead = _dead_emitted(routing, emitting, actions)

    assert not dead, (
        "these callback prefixes are EMITTED but nothing routes them, so every "
        "tap falls through to the callback store and answers 'Button expired' -- "
        + "; ".join(f"{lit} (from {' '.join(w)})" for lit, w in dead)
        + " -- add a branch in CallbackHandler.handle (telegram_keyboards.py), "
        "or stop emitting the button."
    )


def test_the_liveness_rule_sees_store_backed_dispatch(collected) -> None:
    """The anti-false-positive teeth test.

    Not every button is routed by a `cb_data.startswith` branch. The `heal_*` family
    is dispatched through the callback STORE on `entry.action`, so a rule that reads
    `cb_data` alone reports it as dead. Measured: 17 findings without this bucket,
    14 with it, and the 3 rescued are exactly `heal_fix:` / `heal_diag:` /
    `heal_explain:`.

    Without this test someone "simplifies" the rule back to cb_data-only, adds three
    exemptions with plausible reasons, and the guard starts training people to
    exempt real findings.
    """
    routing, emitting, actions = collected

    assert any(a.startswith("heal_") for a in actions), (
        "the action bucket collected no heal_* literal -- the second recogniser is "
        "not reading what it was built to read"
    )
    for prefix in ("heal_fix:", "heal_diag:", "heal_explain:"):
        if prefix not in emitting:
            continue
        assert not _dead_emitted(routing, {prefix: emitting[prefix]}, actions), (
            f"{prefix} was reported dead; it is dispatched via the callback store's "
            "entry.action, which is what _ACTION_NAMES exists to see"
        )
        assert _dead_emitted(routing, {prefix: emitting[prefix]}, {}), (
            f"{prefix} is judged live WITHOUT the action bucket, so this test proves "
            "nothing -- the rescue must come from the second recogniser"
        )


def test_dead_on_purpose_has_no_ghosts(collected) -> None:
    """An exemption for a literal nothing emits any more is stale scope."""
    _, emitting, _ = collected
    ghosts = sorted(k for k in DEAD_ON_PURPOSE if k not in emitting)
    assert not ghosts, (
        f"DEAD_ON_PURPOSE exempts prefixes nothing emits: {ghosts}. Delete them -- an "
        "exemption map that keeps rows for code that no longer exists is how it turns "
        "into a graveyard."
    )


def test_dead_on_purpose_entries_carry_a_reason() -> None:
    for prefix, reason in DEAD_ON_PURPOSE.items():
        assert len(reason) > 15, (
            f"{prefix} is exempted with no real reason ({reason!r}). An entry here "
            "asserts a visible button is SUPPOSED to do nothing; say why."
        )
