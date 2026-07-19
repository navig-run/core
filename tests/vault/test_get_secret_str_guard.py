"""Static gate: never treat a Vault get_secret() result as a plain str.

``Vault.get_secret()`` returns a :class:`SecretStr`. Calling a string method
(``.strip()``, ``.lower()``, …) directly on it raises ``AttributeError`` — and the
surrounding ``except`` almost always swallows it, so a *present* secret is silently
reported absent. That is exactly the bug that left the operator's Brave/Tavily/SerpApi
keys unused and every web search degraded to the keyless path.

The sanctioned unwrap is ``reveal_secret(vault, label)`` or ``secret.reveal()``. This
gate walks the source AST and fails the build if the anti-pattern reappears.

Scope / precision:
- AST (not text) so strings, comments, and docstrings can't trip it.
- ``secret.get_secret(...).reveal()`` chains are naturally excluded (the string
  method's receiver is the ``.reveal()`` call, not ``get_secret``).
- The Credential object's own ``get_secret()`` returns ``str | None`` (a different
  API), so a ``cred``/``credential`` receiver is allowlisted.
- It catches the *direct* footgun (a str-method on the raw result). It does not try to
  track a SecretStr assigned to a variable and used later — those were fixed by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# str methods a SecretStr does NOT implement — calling any directly on a get_secret()
# result raises AttributeError.
_STR_METHODS = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "casefold",
        "title",
        "split",
        "rsplit",
        "splitlines",
        "encode",
        "startswith",
        "endswith",
        "replace",
        "format",
        "removeprefix",
        "removesuffix",
    }
)

# Receivers whose .get_secret() is the Credential API (returns `str | None`), not the
# Vault's SecretStr — safe to call str methods on.
_ALLOWED_RECEIVERS = frozenset({"cred", "credential"})


def _get_secret_receiver(node: ast.AST) -> str | None:
    """If *node* is a ``<recv>.get_secret(...)`` call, return the receiver name.

    Returns ``""`` for a chained/complex receiver (e.g. ``get_vault().get_secret()``)
    and ``None`` when *node* is not a get_secret call at all.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_secret"
    ):
        recv = node.func.value
        if isinstance(recv, ast.Name):
            return recv.id
        if isinstance(recv, ast.Attribute):
            return recv.attr
        return ""
    return None


def _unrevealed_secret_receiver(value: ast.AST) -> str | None:
    """Return the get_secret receiver if *value* is a raw (un-revealed) secret result.

    Handles both ``x.get_secret(...)`` and ``(x.get_secret(...) or "")``.
    """
    direct = _get_secret_receiver(value)
    if direct is not None:
        return direct
    if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
        for operand in value.values:
            recv = _get_secret_receiver(operand)
            if recv is not None:
                return recv
    return None


def _scan_source(source: str, *, label: str = "<src>") -> list[str]:
    """Return violation descriptions (``label:lineno .method()``) for *source*."""
    violations: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _STR_METHODS
        ):
            continue
        recv = _unrevealed_secret_receiver(node.func.value)
        if recv is None or recv in _ALLOWED_RECEIVERS:
            continue
        violations.append(f"{label}:{node.lineno} .{node.func.attr}() on raw get_secret()")
    return violations


def test_no_str_method_on_raw_get_secret() -> None:
    navig_root = Path(__file__).resolve().parents[2] / "navig"
    violations: list[str] = []

    for py_file in navig_root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            rel = py_file.relative_to(navig_root.parent).as_posix()
            violations.extend(
                _scan_source(py_file.read_text(encoding="utf-8"), label=rel)
            )
        except SyntaxError:  # pragma: no cover - non-parseable file, skip
            continue

    assert not violations, (
        "Vault get_secret() returns a SecretStr — unwrap it with reveal_secret(vault, "
        "label) or .reveal() before calling string methods. Offending sites:\n"
        + "\n".join(sorted(violations))
    )


def test_guard_flags_the_anti_pattern() -> None:
    """The gate must actually catch the footgun — a gate that can't fail is useless."""
    bad_samples = [
        'value = vault.get_secret(label).strip()',
        'value = (vault.get_secret(label) or "").strip()',
        'flag = bool((v.get_secret(key) or "").strip())',
        'x = self.get_secret(k).lower()',
        'parts = vault.get_secret(k).split(",")',
    ]
    for sample in bad_samples:
        assert _scan_source(sample), f"guard failed to flag: {sample!r}"


def test_guard_allows_correct_usage() -> None:
    good_samples = [
        # sanctioned unwraps
        'value = reveal_secret(vault, label)',
        'value = vault.get_secret(label).reveal().strip()',
        'value = (vault.get_secret(label).reveal() or "").strip()',
        # SecretStr-safe operations (no string method)
        'if vault.get_secret(label):\n    pass',
        'n = len(vault.get_secret(label))',
        # Credential API (returns str | None) is a different get_secret
        'tok = str(cred.get_secret("token") or "").strip()',
        '(cred.get_secret(name) or "").strip()',
    ]
    for sample in good_samples:
        assert not _scan_source(sample), f"guard false-positived on: {sample!r}"
