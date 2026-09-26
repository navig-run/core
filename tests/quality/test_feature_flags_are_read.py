"""A feature flag that nothing reads is a feature that cannot run.

The shape
---------
A module computes an availability flag and never consults it::

    try:
        from navig.gateway.channels.telegram_checklist import TelegramChecklistMixin
        HAS_CHECKLIST = True
    except ImportError:
        HAS_CHECKLIST = False

    # ... and nothing, anywhere, ever reads HAS_CHECKLIST

Nothing fails. The import succeeds, the flag is correct, and the feature it guards is simply
never entered. Ruff cannot see it either: F841 is for unused *locals*, and these are module
scope. The sibling flag two lines up (``HAS_INLINE``) IS read, which is what makes the pattern
look deliberate on a skim.

What it hid
-----------
``HAS_CHECKLIST`` and ``HAS_FORUM`` (both wired since) -- features whose toggles the Deck rendered
(``gateway/deck/routes/social.py``), with ``checklist_enabled`` defaulting to **True**, so the
operator sees them switched on. Their mixins have **zero** methods reachable from
``TelegramChannel`` and their entry points (``_send_smart_reply``, ``_try_send_checklist``,
``_get_thread_for_command``) have **zero call sites anywhere in the tree**. 31 tests pass for
them, because those tests construct the mixin directly.

The ratio
---------
Three flags matched repo-wide when this was written; two were real. The third is in
``_ALLOWED`` with its reason. A flag that is genuinely a compatibility constant reads that way
in the source -- it is assigned a literal, not computed from an import probe.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "navig"

_SUFFIXES = ("_AVAILABLE", "_ENABLED")

#: Assigned-and-never-read on purpose. An entry is a claim that nothing GATES on it.
_ALLOWED = {
    "HAS_PARAMIKO": (
        "a hardcoded `True` kept for backward compatibility, not an availability probe -- "
        "the real check is `_get_paramiko()` at the call site. Nothing in core, plugins, "
        "private or tests reads it; it exists so an old external import does not break."
    ),
}


def _is_flag(name: str) -> bool:
    return name.isupper() and (name.startswith("HAS_") or name.endswith(_SUFFIXES))


def _scan() -> tuple[dict[str, str], set[str], int]:
    """({flag: 'file:line'}, names read anywhere, files scanned)."""
    assigned: dict[str, str] = {}
    read: set[str] = set()
    files = 0
    for f in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        files += 1
        rel = f.relative_to(ROOT.parent).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                read.add(node.id)
            # a re-export (`from x import HAS_Y`) counts as a read: someone else may consult it
            elif isinstance(node, ast.ImportFrom):
                read.update(a.name for a in node.names)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and _is_flag(t.id):
                        assigned.setdefault(t.id, f"{rel}:{node.lineno}")
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _is_flag(node.target.id):
                    assigned.setdefault(node.target.id, f"{rel}:{node.lineno}")
    return assigned, read, files


def test_the_scan_actually_reads_the_tree() -> None:
    """Anti-vacuity, as a presence: a scan that found nothing reports nothing."""
    assigned, _, files = _scan()
    assert files >= 400, f"only {files} files parsed under {ROOT} — the walk is mis-rooted"
    assert len(assigned) >= 10, (
        f"only {len(assigned)} feature flags found — the pattern match is broken"
    )


def test_every_feature_flag_is_read_somewhere() -> None:
    assigned, read, _ = _scan()
    dead = {n: w for n, w in assigned.items() if n not in read and n not in _ALLOWED}
    detail = "\n".join(f"    {n:22} {w}" for n, w in sorted(dead.items()))
    assert not dead, (
        "these flags are computed and never read, so whatever they gate cannot run:\n"
        + detail
        + "\n\nEither consult the flag where the feature should enter, or delete it. "
        "If it is a compatibility constant rather than a gate, add it to _ALLOWED "
        "with the reason."
    )


def test_the_allowlist_does_not_rot() -> None:
    """An entry that starts being read, or stops existing, must leave the list."""
    assigned, read, _ = _scan()
    for name in _ALLOWED:
        assert name in assigned, f"{name} no longer exists — drop it from _ALLOWED"
        assert name not in read, (
            f"{name} is read now, so it is an ordinary flag — drop it from _ALLOWED"
        )
        assert _ALLOWED[name].strip(), f"{name} has no reason recorded"
