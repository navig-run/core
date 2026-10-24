"""`python -m navig …` must start from a directory that contains a `navig/` folder.

Found 2026-10-19: `C:\\studio` holds an asset folder named `navig`, so every spawn whose
cwd was there — and NAVIG's own spawns inherit the caller's cwd (`suggest`, `triggers`,
`skills`, `history`) — died at startup with

    ImportError: cannot import name '__version__' from 'navig' (unknown location)

because a folder named `navig/` turns the package ROOT into a namespace package (no
attributes) while every submodule still resolves through the editable install. The
console command `navig …` was never affected: its sys.path[0] is the Scripts directory.

The shadow itself only exists under the editable-install topology, so what is pinned here
is the thing that broke: the version resolution used by both guarded call sites, and the
guard being present in both.
"""

from __future__ import annotations

from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
REPO = CORE.parent
PYPROJECT = CORE / "pyproject.toml"


def _pyproject_version() -> str:
    import tomllib

    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def test_resolve_version_reads_the_checkout_pyproject():
    from navig.cli._version import resolve_version

    assert resolve_version() == _pyproject_version(), (
        "the CLI must report the version the checkout actually builds"
    )


def test_resolve_version_falls_back_to_metadata_without_a_pyproject(monkeypatch, tmp_path):
    """Wheel/site-packages layout: no pyproject beside the package — metadata, never a raise."""
    import navig.cli._version as v

    fake = tmp_path / "core" / "navig" / "cli" / "_version.py"
    fake.parent.mkdir(parents=True)
    monkeypatch.setattr(v, "__file__", str(fake))
    assert not (fake.parents[2] / "pyproject.toml").exists()
    found = v.resolve_version()
    assert isinstance(found, str) and found.strip(), found


def test_both_version_import_sites_carry_the_guard():
    """`written · tested · never wired`: the fix is the try/except, so pin it where it lives.

    A bare `from navig import __version__` in either file reintroduces the startup crash
    the moment a `navig/` folder lands in the working directory.
    """
    for rel in ("core/navig/cli/__init__.py", "core/navig/cli/_callbacks.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "from navig import __version__" in src, rel
        guarded = "try:\n    from navig import __version__" in src or (
            "try:\n    from navig import __version__ as __version__" in src
        )
        assert guarded, f"{rel} must guard the root version import (see navig.cli._version)"
        assert "resolve_version" in src, f"{rel} must fall back to navig.cli._version"
        assert "from navig.cli._version import resolve_version" in src, rel


def test_the_helper_never_imports_the_root():
    """_version.py exists precisely because the root may be the broken module."""
    src = (CORE / "navig" / "cli" / "_version.py").read_text(encoding="utf-8")
    root_imports = [ln for ln in src.splitlines() if ln.startswith("from navig ")]
    assert not root_imports, f"resolving the version must not need the root: {root_imports}"
    assert 'parents[2] / "pyproject.toml"' in src, "version must come from core/pyproject.toml"


def test_repair_root_swaps_a_namespace_root_for_the_real_package(tmp_path):
    """The repair itself, in its own interpreter (it swaps sys.modules['navig'])."""
    import subprocess
    import sys

    shadow = tmp_path / "navig"
    shadow.mkdir()
    code = f"""
import sys, types, importlib.machinery as mach
from navig._shadow_root import repair_root

# the state a `navig/` folder in the cwd produces: a namespace root, no __file__, no attrs
fake = types.ModuleType("navig")
fake.__path__ = [r"{shadow}"]
fake.__spec__ = mach.ModuleSpec("navig", loader=None, is_package=True)
fake.__spec__.origin = None
fake.__file__ = None
sys.modules["navig"] = fake

repaired = repair_root()
import navig
assert repaired, "repair_root did not recognise the namespace root"
assert navig.__file__ and navig.__file__.endswith("__init__.py"), navig.__file__
assert getattr(navig, "__version__", ""), "the real package root must carry its version"
print("REPAIRED", navig.__version__)
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert "REPAIRED" in out.stdout, (out.stdout, out.stderr)
