"""Every asset inside a first-party package must actually reach its wheel.

The other half of the "assets don't ship" class
-----------------------------------------------
`tests/platform/test_asset_paths.py` catches an asset resolved from a path that walks
*outside* the package. This catches the opposite shape: a file that sits **inside** the
package — so the code finds it in a dev checkout — but which `[tool.setuptools.package-data]`
never declares, so it is **dropped when the wheel is built**.

Same silent failure, different cause. Verified against real artifacts: the hand-curated
glob list in `core/pyproject.toml` ("help/*.md", "schemas/*.json", …) missed **64 assets**,
including the default persona + soul (`navig/resources/personas/`), the agent's i18n locales
(`navig/agent/conv/locales/*.json`), the builtin modes (`navig/modes/builtin.yaml`), the space
scaffold templates that `navig space init` copies verbatim, the browser templates, and two
builtin skills. Every one of them is loaded via `Path(__file__).parent / …`, so it worked
perfectly for us and was simply absent for every pip-installed user.

This test computes coverage with setuptools' own mechanism (recursive `glob`), so it needs no
wheel build and cannot drift from what setuptools actually does.
"""

from __future__ import annotations

import glob as globlib
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[3]

# Never shipped, never wanted.
SKIP_PARTS = {"__pycache__", "node_modules", "dist", "build", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIX = {".py", ".pyc", ".pyo", ".pyd", ".so"}


def _packages() -> list[tuple[str, Path, Path]]:
    """(label, package dir, pyproject) for core + every first-party Python plugin."""
    out: list[tuple[str, Path, Path]] = [
        ("core", REPO / "core" / "navig", REPO / "core" / "pyproject.toml"),
    ]
    for pj in sorted(REPO.glob("plugins/navig-*/pyproject.toml")):
        for d in sorted(pj.parent.iterdir()):
            if d.is_dir() and d.name.startswith("navig_") and (d / "__init__.py").exists():
                out.append((pj.parent.name, d, pj))
                break
    return out


def _globs(pyproject: Path, key: str) -> dict[str, list[str]]:
    cfg = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return cfg.get("tool", {}).get("setuptools", {}).get(key, {})


def _glob(pattern: str, root: Path) -> list[str]:
    """Recursive glob including dotfiles — setuptools DOES pick those up (the built wheel
    carries all 17 `.gitkeep` placeholders), while a plain `glob.glob` skips them, which
    would make this guard report phantom gaps."""
    try:
        return globlib.glob(pattern, root_dir=root, recursive=True, include_hidden=True)
    except TypeError:  # pragma: no cover — include_hidden is 3.11+
        return globlib.glob(pattern, root_dir=root, recursive=True)


def _matched(pkg_dir: Path, pkg_name: str, spec: dict[str, list[str]]) -> set[str]:
    """Files setuptools would pick up, using its own recursive-glob semantics."""
    hits: set[str] = set()
    for key, patterns in spec.items():
        # keys are package names: "navig" → pkg root; "navig.resources" → navig/resources
        if key == pkg_name:
            root = pkg_dir
        elif key.startswith(pkg_name + "."):
            root = pkg_dir.joinpath(*key.split(".")[1:])
        else:
            continue
        if not root.is_dir():
            continue
        for pat in patterns:
            for m in _glob(pat, root):
                p = root / m
                if p.is_file():
                    hits.add(p.relative_to(pkg_dir).as_posix())
    return hits


def _assets(pkg_dir: Path) -> set[str]:
    return {
        f.relative_to(pkg_dir).as_posix()
        for f in pkg_dir.rglob("*")
        if f.is_file()
        and f.suffix not in SKIP_SUFFIX
        and not any(p in SKIP_PARTS or p.endswith(".egg-info") for p in f.parts)
    }


@pytest.mark.parametrize("label,pkg_dir,pyproject", _packages(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_asset_in_the_package_reaches_the_wheel(label: str, pkg_dir: Path, pyproject: Path) -> None:
    pkg_name = pkg_dir.name
    included = _matched(pkg_dir, pkg_name, _globs(pyproject, "package-data"))
    excluded = _matched(pkg_dir, pkg_name, _globs(pyproject, "exclude-package-data"))
    shipped = included - excluded

    missing = sorted(_assets(pkg_dir) - shipped)
    assert not missing, (
        f"{label}: {len(missing)} file(s) live inside the `{pkg_name}` package but are not "
        "declared in [tool.setuptools.package-data], so they are DROPPED when the wheel is "
        "built — present in a dev checkout, absent for every installed user.\n"
        "Declare them (core ships everything via `navig = [\"**/*\"]`), or delete the file if "
        "nothing loads it.\n\n" + "\n".join(f"  {m}" for m in missing[:25])
        + (f"\n  … and {len(missing) - 25} more" if len(missing) > 25 else "")
    )


def test_core_ships_the_assets_that_were_actually_lost() -> None:
    """Pin the specific casualties, so a future glob 'cleanup' can't silently drop them again."""
    pkg = REPO / "core" / "navig"
    spec = _globs(REPO / "core" / "pyproject.toml", "package-data")
    shipped = _matched(pkg, "navig", spec) - _matched(
        pkg, "navig", _globs(REPO / "core" / "pyproject.toml", "exclude-package-data")
    )
    for critical in (
        "resources/personas/default/persona.yaml",   # the default persona…
        "resources/personas/default/soul.md",        # …and its soul
        "agent/conv/locales/en.json",                # i18n
        "modes/builtin.yaml",                        # builtin modes
        "license/tiers.json",                        # tier spec
        "browser/templates/generic.yaml",            # browser templates
        "contracts/schemas/block.schema.json",       # the block contract
    ):
        if (pkg / critical).exists():
            assert critical in shipped, f"{critical} exists but would NOT ship in the wheel"


def test_no_bytecode_is_declared_as_package_data() -> None:
    """`**/*` ships everything — the exclude list is what keeps .pyc out of the wheel."""
    pkg = REPO / "core" / "navig"
    excluded = _globs(REPO / "core" / "pyproject.toml", "exclude-package-data")
    pats = [p for pats in excluded.values() for p in pats]
    assert any("__pycache__" in p for p in pats), "exclude-package-data must drop __pycache__"
    assert any(p.endswith(".pyc") for p in pats), "exclude-package-data must drop *.pyc"
    assert pkg.is_dir()
