"""Reading an undeclared key through the schema-filtered config view is a defect.

``ConfigManager._load_global_config()`` is NOT a raw file read. It ends in
``validate_global_config(...).model_dump()``, so it returns ONLY the fields the
Pydantic schema declares -- everything else the operator set is silently gone, and
schema defaults they never set appear as though configured.

Measured on one real install: **163 keys on disk, 126 returned, 114 dropped**,
including the whole ``adapters`` (Twilio account_sid / auth_token), ``cloud``,
``user``, ``plugins``, ``missions``, ``llm_router`` and ``apps`` subtrees.
``gateway`` survived without ``auth`` or ``mesh_token``.

What that cost, before this guard existed:

* all seven ``navig cron`` commands answered HTTP 401 against the operator's own
  gateway, because ``gateway_request_headers()`` could not find
  ``gateway.auth.token``
* ``navig config show global`` printed a config the operator does not have
* ``navig proactive status`` reported "Calendar: not configured" regardless of a
  DOCUMENTED toggle
* ``navig gateway status`` reported configured Discord / WhatsApp / Matrix / Email
  channels as unconfigured
* ``deploy.history_keep`` always resolved to the built-in 50

Every one of those was invisible: the filtered view returns a perfectly valid dict.

This is a FLOOR placed after the known cases were fixed -- it should find nothing
today. Use ``get_global_config()`` for any key the schema does not declare.

The declared set is computed FROM THE SCHEMA at runtime, never hardcoded, so
widening the schema automatically widens what this permits.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCAN_ROOTS = [REPO / "core" / "navig", REPO / "plugins"]

READER = "_load_global_config"

# Sites that read the filtered view for a key the schema does NOT declare, each with a
# written reason it is safe. An entry here is a claim that has to stay true.
ALLOWED: dict[tuple[str, str], str] = {
    ("commands/config.py", "*"): (
        "navig config get falls back to the canonical resolver when the filtered view "
        "returns None, so an undeclared key still resolves. That fallback is why this "
        "whole class looked impossible -- the value WAS readable from one command."
    ),
}


def _declared_top_level_keys() -> set[str]:
    """What the schema actually keeps -- asked of the schema, never hardcoded."""
    from navig.core.config_schema import validate_global_config

    validated = validate_global_config({}, strict=False)
    return set(validated.model_dump()) if validated else set()


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        if root.exists():
            out.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def _is_reader_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == READER
    )


def _const_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _keys_read_from_the_filtered_view(tree: ast.AST) -> list[tuple[str, int]]:
    """Top-level keys read from a variable holding ``_load_global_config()``.

    Deliberately conservative: it follows a DIRECT assignment
    (``cfg = ..._load_global_config()``) and a ``.get("k")`` / ``["k"]`` on that
    name, plus the chained ``_load_global_config().get("k")``. Anything cleverer is
    not reported -- a guard that guesses produces findings nobody can act on.
    """
    found: list[tuple[str, int]] = []

    for scope in ast.walk(tree):
        # Functions only, deliberately. Including ast.Module walked INTO every
        # function, so a holder named `raw` in one function matched `raw.get("port")`
        # in another that had loaded a completely different dict (gateway.json). A
        # cross-function false positive is worse than a missed module-level read:
        # it teaches people the guard is noise.
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        holders: set[str] = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and _is_reader_call(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        holders.add(target.id)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and _is_reader_call(node.func.value)
                and node.args
            ):
                key = _const_key(node.args[0])
                if key:
                    found.append((key, node.lineno))

        if not holders:
            continue

        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in holders
                and node.args
            ):
                key = _const_key(node.args[0])
                if key:
                    found.append((key, node.lineno))
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in holders
            ):
                key = _const_key(node.slice)
                if key:
                    found.append((key, node.lineno))

    return found


def _is_allowed(rel_path: str, key: str) -> bool:
    return any(
        rel_path.endswith(suffix) and allowed_key in ("*", key)
        for (suffix, allowed_key) in ALLOWED
    )


def test_no_undeclared_key_is_read_through_the_filtered_view() -> None:
    declared = _declared_top_level_keys()
    assert len(declared) >= 10, (
        f"only {len(declared)} declared keys resolved -- the schema probe is broken, "
        "and a guard that permits everything is worse than no guard"
    )

    files = _python_files()
    # A SCAN FLOOR, added because this test passed vacuously once: a broken worktree
    # left the scan roots empty, the loop ran zero times, and a guard that had checked
    # nothing reported success. Only a sibling test noticed. "Found no offenders" and
    # "looked at no files" must never be the same green.
    assert len(files) >= 500, (
        f"only {len(files)} python files found under {[str(r) for r in SCAN_ROOTS]} -- "
        "the scan roots are wrong, so this guard is checking nothing"
    )
    readers = [p for p in files if READER in p.read_text(encoding="utf-8", errors="replace")]
    assert readers, (
        f"no file calls {READER}() any more. If that is genuinely true, delete this "
        "guard and its allowlist rather than leaving a check that can never fire."
    )

    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if READER not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(REPO).as_posix()
        for key, lineno in _keys_read_from_the_filtered_view(tree):
            if key in declared or _is_allowed(rel, key):
                continue
            message = (
                f"{rel}:{lineno} reads {key!r} from {READER}(), which the schema does "
                f"not declare -- it will ALWAYS be missing. Use get_global_config()."
            )
            if message not in offenders:
                # A key reachable by more than one access shape is ONE defect.
                offenders.append(message)

    assert not offenders, (
        "undeclared keys read through the schema-filtered view:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_the_detector_actually_detects() -> None:
    """Anti-vacuity: a guard that finds nothing must be shown capable of finding.

    Without this, emptying the visitor leaves the suite green.
    """
    sample = (
        "def f(cm):\n"
        "    cfg = cm._load_global_config()\n"
        "    a = cfg.get('adapters')\n"
        "    b = cfg['cloud']\n"
        "    c = cm._load_global_config().get('deploy')\n"
        "    return a, b, c\n"
    )
    keys = {k for k, _ in _keys_read_from_the_filtered_view(ast.parse(sample))}
    assert keys == {"adapters", "cloud", "deploy"}, f"detector missed cases: {keys}"


def test_a_declared_key_is_not_flagged() -> None:
    """The other half: reading a schema-declared key this way is fine."""
    declared = _declared_top_level_keys()
    assert "gateway" in declared
    sample = "def f(cm):\n    cfg = cm._load_global_config()\n    return cfg.get('gateway')\n"
    keys = {k for k, _ in _keys_read_from_the_filtered_view(ast.parse(sample))}
    assert keys == {"gateway"}
    assert all(k in declared for k in keys), "a declared key must not be an offender"


def test_the_allowlist_has_no_ghost_entries() -> None:
    """An allowlist entry for a file that no longer reads this way is rot."""
    for suffix, _key in ALLOWED:
        matches = [p for p in _python_files() if p.as_posix().endswith(suffix)]
        assert matches, f"allowlist names {suffix}, which does not exist"
        assert any(
            READER in p.read_text(encoding="utf-8", errors="replace") for p in matches
        ), f"allowlist exempts {suffix}, but it no longer calls {READER} -- delete it"


@pytest.mark.parametrize("key", ["adapters", "cloud", "user", "plugins", "missions"])
def test_the_keys_that_broke_things_are_still_undeclared(key: str) -> None:
    """Pins WHY this guard exists. If the schema is widened, revisit the fixes."""
    assert key not in _declared_top_level_keys(), (
        f"the schema now declares {key!r} -- the readers switched to "
        "get_global_config() for it could be revisited, deliberately"
    )
