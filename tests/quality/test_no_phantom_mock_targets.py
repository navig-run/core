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

MONKEYPATCH HAS THE SAME ESCAPE HATCH, and it was outside this guard until #742.
``monkeypatch.setattr("a.b.c", v, raising=False)`` is exactly ``create=True``: it
invents the attribute instead of complaining. Without the kwarg monkeypatch raises
AttributeError on a bad target, so only the ``raising=False`` sites can hide a phantom
— which is why this checks those and not the other ~235 object-form calls.

It found four, all the same line in `tests/memory/test_memory_singletons.py`:
``monkeypatch.setattr("navig.config.get_config", …, raising=False)`` under the comment
*"Patch get_config so no real config is needed"*. `navig.config` has no `get_config`
(it is `get_config_manager`), and the code under test reads `paths.data_dir()` anyway,
so the patch fabricated a name nothing reads and the promised isolation never happened.
The tests passed regardless because conftest already isolates NAVIG_CONFIG_DIR
session-wide — so the patch was pure decoration, and removing it changed nothing
(706 passed either way). A comment describing isolation that does not exist is worse
than no comment: the next person trusts it.
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


# ── monkeypatch's equivalent escape hatch ────────────────────────────────────────


def _raising_false_targets(path: Path) -> list[tuple[int, str]]:
    """`monkeypatch.setattr("a.b.c", …, raising=False)` — string targets only.

    The object form (`monkeypatch.setattr(mod, "attr", …, raising=False)`) cannot be
    resolved statically, so it is out of scope rather than guessed at.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in {"setattr", "delattr"}:
            continue
        if not any(
            kw.arg == "raising"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is False
            for kw in node.keywords
        ):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            target = node.args[0].value
            if isinstance(target, str) and "." in target:
                out.append((node.lineno, target))
    return out


def _resolve(dotted: str) -> bool | None:
    """True/False if the attribute is present/absent; None if it cannot be resolved."""
    owner_path, _, attr = dotted.rpartition(".")
    try:
        return hasattr(importlib.import_module(owner_path), attr)
    except Exception:  # noqa: BLE001 — could be pkg.mod.Class.method
        parent, _, cls = owner_path.rpartition(".")
        try:
            return hasattr(getattr(importlib.import_module(parent), cls), attr)
        except Exception:  # noqa: BLE001
            return None


def _raising_false_sites() -> list[tuple[str, int, str, bool | None]]:
    return [
        (_rel(path), lineno, dotted, _resolve(dotted))
        for path in _test_files()
        for lineno, dotted in _raising_false_targets(path)
    ]


def test_no_raising_false_patch_invents_a_missing_attribute() -> None:
    phantom = [
        f"{where}:{lineno} — monkeypatch.setattr({dotted!r}, …, raising=False)"
        for where, lineno, dotted, present in _raising_false_sites()
        if present is False
    ]
    assert not phantom, (
        "`raising=False` created an attribute that does not exist — monkeypatch's "
        "`create=True`. The patch fabricates a name nothing reads, so whatever it "
        "claims to isolate is NOT isolated:\n  "
        + "\n  ".join(phantom)
        + "\n\nFix the target or delete the patch. Drop `raising=False` and monkeypatch "
        "will tell you itself."
    )


def test_every_raising_false_target_could_be_resolved() -> None:
    """A target this guard cannot import is a target it did not check. Saying so beats
    passing over it — that silence is how the console_helper phantoms survived."""
    unresolved = [
        f"{where}:{lineno} — {dotted}"
        for where, lineno, dotted, present in _raising_false_sites()
        if present is None
    ]
    assert not unresolved, (
        "these `raising=False` targets could not be imported, so they were NOT "
        "checked:\n  " + "\n  ".join(unresolved)
    )
