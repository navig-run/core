"""`create=True` is banned in this suite. It can only ever hide a bug.

``patch(target, create=True)`` means: *patch this even though the attribute does not
exist.* So it says one of exactly two things, and both are bad:

* **the attribute DOES exist** — the kwarg is a no-op lie. Harmless today; a silent
  licence tomorrow, because the next person copies it.
* **the attribute does NOT exist** — the test is asserting against a phantom. The patch
  fabricates the symbol, the test drives a code path that cannot exist in production, and
  it passes. Forever.

That second case is not theoretical, it is this repo's history. `navig.console_helper`
raises ``AttributeError`` for unknown names, so every ``ch.warn(...)`` / ``ch.print(...)``
call site was a live crash — 128 of them, in shipped commands. The suite was green the
whole time, because ~96 tests did ``patch("navig.console_helper.warn", create=True)``:
mock invented the missing function, the crash never fired, and the tests certified code
that could not run. `navig mcp search` crashed for real users while its tests passed.

Stripping the kwarg from the whole suite surfaced eight more phantoms in one pass —
``navig.core.hosts.load_config``, ``navig.agents.list_agents``,
``navig.llm.generate.get_config_manager``, ``navig.mesh.sync_manager.ELECT_SYNC`` (it
lives in ``navig.mesh.discovery``), ``SSHHealer._KNOWN_HOSTS_PATH`` (a module constant,
not a class attribute), and more. Two of them guarded assertions that could not fail
(``assert mock_send.called or True``).

**If a patch target does not exist, that is the finding.** Fix the target or delete the
patch — do not conjure the attribute. The allowlist below is for the one case where
creation is genuinely correct: an attribute that exists on another *platform*.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]

# ── The allowlist ────────────────────────────────────────────────────────────────
# (file suffix, dotted target) -> why creation is legitimate.
# Only ever a symbol that exists on a platform we are not running on. If you are about
# to add anything else here, you have found a bug — go fix it instead.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "tests/cli/test_cli_wizard.py",
        "os.geteuid",
    ): "POSIX-only; genuinely absent on Windows, so the Linux branch can only be simulated by creating it",
}

_PATCH_FUNCS = {"patch", "object", "dict"}


def _string_consts(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "some.dotted.target"`` constants, so an indirected
    ``patch(_WARN, ...)`` still resolves to a real target."""
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _create_true_sites(path: Path) -> list[tuple[int, str | None]]:
    """Every call passing ``create=True``, as ``(lineno, dotted_target_or_None)``."""
    # utf-8-sig: at least one test file is BOM-prefixed, and a BOM is a SyntaxError to
    # ast.parse. A guard that crashes on a file scans nothing.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    consts = _string_consts(tree)
    sites: list[tuple[int, str | None]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(
            kw.arg == "create" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        ):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else ""
        )
        if name not in _PATCH_FUNCS:
            continue  # some unrelated API that happens to take a `create` kwarg

        target: str | None = None
        if node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                target = first.value
            elif isinstance(first, ast.Name) and first.id in consts:
                target = consts[first.id]
        sites.append((node.lineno, target))
    return sites


def _test_files() -> list[Path]:
    return sorted(TESTS_DIR.rglob("test_*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(TESTS_DIR.parent).as_posix()


def test_no_create_true_outside_the_allowlist() -> None:
    offenders: list[str] = []
    for path in _test_files():
        for lineno, target in _create_true_sites(path):
            if target is not None and (_rel(path), target) in ALLOWED:
                continue
            shown = target or "<non-literal target>"
            offenders.append(f"{_rel(path)}:{lineno} — patch({shown!r}, create=True)")

    assert not offenders, (
        "`create=True` patches a symbol that does not exist — it can only mean the test "
        "is asserting against a phantom (or the kwarg is dead). Fix the target or drop "
        "the patch; do not invent the attribute. See this module's docstring.\n\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


@pytest.mark.parametrize(("where", "dotted"), sorted(ALLOWED), ids=lambda v: str(v))
def test_allowlisted_targets_are_still_genuinely_absent(where: str, dotted: str) -> None:
    """An allowlist entry earns its place only while the attribute really is missing.

    If a platform-only symbol becomes importable here (say the suite starts running on
    Linux, where ``os.geteuid`` exists), ``create=True`` is once again a lie and the
    entry must go — this fails to say so, instead of quietly rotting.
    """
    parts = dotted.split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return  # genuinely absent — the allowlist entry is honest
            obj = getattr(obj, attr)
        pytest.fail(
            f"{dotted} EXISTS now, so `create=True` at {where} no longer creates "
            f"anything — it just suppresses the check that would catch a typo. "
            f"Drop the kwarg and remove this allowlist entry ({ALLOWED[(where, dotted)]})."
        )
    pytest.fail(f"could not import any module prefix of {dotted!r} — stale allowlist entry?")


def test_the_guard_actually_catches_a_phantom(tmp_path: Path) -> None:
    """The guard must fail on the exact pattern that hid the console_helper crashes."""
    bad = tmp_path / "test_bad.py"
    bad.write_text(
        "from unittest.mock import patch\n"
        '_WARN = "navig.console_helper.warn"\n'
        "def test_x():\n"
        "    with patch(_WARN, create=True):\n"
        "        pass\n"
        '    with patch("navig.console_helper.print", create=True):\n'
        "        pass\n",
        encoding="utf-8",
    )
    sites = _create_true_sites(bad)
    assert [t for _, t in sites] == [
        "navig.console_helper.warn",  # resolved through the module-level constant
        "navig.console_helper.print",
    ]


def test_the_guard_does_not_flag_ordinary_patches(tmp_path: Path) -> None:
    """A normal patch — and an unrelated `create=` kwarg — must not trip it."""
    ok = tmp_path / "test_ok.py"
    ok.write_text(
        "from unittest.mock import patch\n"
        "def test_x():\n"
        '    with patch("navig.console_helper.warning"):\n'
        "        pass\n"
        '    with patch.object(SomeClass, "method"):\n'
        "        pass\n"
        "    make_widget(create=True)  # not a patch — must be ignored\n",
        encoding="utf-8",
    )
    assert _create_true_sites(ok) == []
