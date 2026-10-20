"""What core imports from navig-vault must be in the package the floor allows.

Two separate questions, both about the same install:

* **Does core only import names navig-vault actually has?** ``from navig_vault import
  svg`` on a release where that module does not exist raises at the call site, inside a
  ``try``, so the user gets "update navig-vault" instead of a traceback — a shipped import
  no release satisfies would look exactly like a working install.
* **Does the declared floor cover those modules?** `navig vault export|open|import|opener`
  (``navig/commands/vault_svg.py``) need navig-vault 0.8.0. A floor of 0.6.0 let pip
  resolve an older package where all four verbs are still advertised in the help and the
  HANDBOOK, and can only say "pip install -U navig-vault".
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import tomllib
from packaging.version import Version

CORE = Path(__file__).resolve().parents[2]
SRC = CORE / "navig"
# First navig-vault release shipping navig_vault/svg.py, svg_export.py, transfer.py.
FIRST_WITH_SVG_EXPORT = "0.8.0"


def _imports() -> set[tuple[str, str]]:
    """``(module, name)`` for every ``from navig_vault… import name`` under navig/.

    Via the AST, so a name in a comment or a docstring can never count as an import, and
    so ``node.module`` travels with each name — ``from navig_vault import svg`` and
    ``from navig_vault.core import Vault`` are different promises.
    """
    found: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover — a broken file
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module.split(".")[0] != "navig_vault":
                continue
            for alias in node.names:
                if alias.name != "*":  # a star import promises nothing checkable here
                    found.add((module, alias.name))
    return found


def test_every_navig_vault_name_core_imports_exists() -> None:
    found = _imports()
    assert found, "the scan found nothing — the AST extractor has drifted"
    missing: list[str] = []
    for module, name in sorted(found):
        if module == "navig_vault":
            # A submodule (find_spec must not raise) or an attribute of the package.
            spec = importlib.util.find_spec(f"navig_vault.{name}")
            if spec is None and not hasattr(importlib.import_module("navig_vault"), name):
                missing.append(name)
        else:
            target = importlib.import_module(module)
            if not hasattr(target, name):
                missing.append(f"{module}.{name}")
    assert not missing, (
        f"core imports {missing} from navig-vault, which does not define them — the wheel "
        "every user installs would raise at the first use"
    )


def test_the_declared_floor_covers_the_svg_export() -> None:
    project = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    req = next(
        (d for d in project["dependencies"] if d.split()[0].lower().startswith("navig-vault")),
        None,
    )
    assert req, "core no longer depends on navig-vault — the vault itself needs it"
    floor = req.split(">=", 1)[1].strip() if ">=" in req else req.split("==", 1)[1].strip()
    # >= : the floor must ALLOW 0.8.0. A floor below it passes pip to a package where the
    # four verbs import nothing — written with <= once, which asserted the opposite and
    # passed on 0.6.0 as happily as on 0.8.0.
    assert Version(floor) >= Version(FIRST_WITH_SVG_EXPORT), (
        f"navig-vault floor {floor} is below {FIRST_WITH_SVG_EXPORT}, which first ships the "
        "modules `navig vault export|open|import|opener` import — pip would resolve a package "
        "where those commands only answer 'pip install -U navig-vault'"
    )
    # requirements.txt is the plain-pip mirror (its header says pyproject is the source of truth).
    listed = [
        line.strip() for line in (CORE / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert req in listed, "requirements.txt no longer carries the same navig-vault floor"


# First navig-vault release shipping add_sso_login (navig/vault/__init__.py imports it).
FIRST_WITH_SSO_LOGIN = "0.9.0"


def test_the_declared_floor_covers_sso_logins() -> None:
    """The import test above checks the INSTALLED (editable, repo) navig-vault, so it
    cannot see a floor that resolves to an older published wheel. 3.27.0 shipped exactly
    that: ``navig-vault>=0.8.0`` while ``navig.vault`` imports ``add_sso_login``, which
    PyPI's 0.8.0 does not have — every fresh install's gateway died on the ImportError."""
    project = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    req = next(d for d in project["dependencies"] if d.lower().startswith("navig-vault"))
    floor = req.split(">=", 1)[1].strip()
    assert Version(floor) >= Version(FIRST_WITH_SSO_LOGIN), (
        f"navig-vault floor {floor} is below {FIRST_WITH_SSO_LOGIN}, the first release with "
        "add_sso_login — pip would install a vault that `import navig.vault` cannot load"
    )


# First navig-vault release shipping keyslots / refs / backup / federation, which
# `navig vault status|run|recovery|backup|devices|join` and vault:// resolution import.
FIRST_WITH_FEDERATION = "0.11.0"  # 0.10.0 on PyPI lacks Authority.handle(source=)


def test_the_declared_floor_covers_vault_federation() -> None:
    """Same trap as add_sso_login: the repo's editable vault has the modules, a published
    wheel below the floor would not, and the new vault verbs would die on ImportError."""
    project = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    req = next(d for d in project["dependencies"] if d.lower().startswith("navig-vault"))
    floor = req.split(">=", 1)[1].strip()
    assert Version(floor) >= Version(FIRST_WITH_FEDERATION), (
        f"navig-vault floor {floor} is below {FIRST_WITH_FEDERATION}, the first release with "
        "keyslots/refs/backup/federation"
    )