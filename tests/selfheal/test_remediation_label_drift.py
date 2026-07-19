"""Drift guard: every remediation-map key must be a label doctor can emit.

The heal mapping (``navig/selfheal/doctor_remediation.py::_LABEL_MAP``) is
keyed by doctor check LABELS — plain strings like ``"Gateway"``. Renaming a
check in ``navig/commands/doctor.py`` would silently orphan its remediation:
``--heal`` would keep passing tests (the mapping itself still works) while
never firing for the renamed check in production. This guard ties the two
files together at the source level so a rename fails the suite instead.

Labels are extracted from ``doctor.py`` with AST rather than by executing
checks (executing them requires a daemon/vault/network and still only covers
the states that happen to occur during the test run).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


def _doctor_source() -> str:
    from navig.commands import doctor

    return Path(inspect.getfile(doctor)).read_text(encoding="utf-8")


def _emittable_labels() -> set[str]:
    """First-argument string literals of every `_check(...)` call in doctor.py.

    Only literal labels count: a dynamically-built label could never be safely
    referenced from `_LABEL_MAP` anyway (the map is static), so non-literal
    first args are ignored rather than guessed at.
    """
    labels: set[str] = set()
    tree = ast.parse(_doctor_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "_check" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            labels.add(first.value)
    return labels


def test_every_mapped_label_is_emittable_by_doctor():
    from navig.selfheal.doctor_remediation import _LABEL_MAP

    emittable = _emittable_labels()
    assert emittable, "no _check() literals found — the extractor broke, not doctor"

    orphaned = set(_LABEL_MAP) - emittable
    assert not orphaned, (
        f"remediation-map labels no longer emitted by doctor: {sorted(orphaned)}. "
        "A doctor check was renamed or removed — update _LABEL_MAP in "
        "navig/selfheal/doctor_remediation.py in the same change, or --heal "
        "will silently never fire for it."
    )


def test_extractor_sees_the_known_anchor_labels():
    """If doctor.py restructures away from `_check("Label", ...)` literals,
    the extractor would return an unrelated set and the drift test would pass
    vacuously — anchor it to two labels that have existed since the rows
    shipped (#177/#186)."""
    emittable = _emittable_labels()
    assert "Event processor" in emittable
    assert "Vault" in emittable
