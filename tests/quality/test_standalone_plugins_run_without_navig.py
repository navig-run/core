"""Guard: a plugin that declares `mode = "standalone"` really runs with navig ABSENT.

The public plugins page promises `pip install navig-<name>` gives a working command with no
navig installed. Nothing else checks that promise:

* every plugin's own suite runs in the monorepo, where navig is installed, so an import of
  `navig.*` that should have gone through navig-sdk passes there and only fails for the user;
* `check_plugins_publishable` reads the source for import-TIME navig imports, but cannot see
  one that runs the moment the CLI starts (inside the Typer app's callback, say).

So for each standalone plugin this starts a fresh interpreter with a finder that refuses
`navig` and every `navig.*` module — first proving the refusal works, or the test would pass
with navig quietly importable — then imports the package and runs its `navig-<name> --help`
exactly as the console script would. `navig_sdk`, `navig_vault` and the like are separate
packages (`navig_`, not `navig.`) and stay importable, as they are for a real user.

Discovery is from `[tool.navig] mode` on disk, so a plugin flipped to standalone is covered
the day it is flipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[3]
PLUGINS = REPO / "plugins"
SDK = REPO / "registry" / "sdk" / "python"

_PROBE = textwrap.dedent(
    """
    import importlib, importlib.abc, sys

    class _NoNavig(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "navig" or name.startswith("navig."):
                raise ModuleNotFoundError(f"No module named {name!r} (blocked: standalone check)", name=name)
            return None

    sys.meta_path.insert(0, _NoNavig())
    for m in [m for m in sys.modules if m == "navig" or m.startswith("navig.")]:
        del sys.modules[m]
    try:
        import navig  # noqa: F401
    except ModuleNotFoundError:
        pass
    else:
        print("BLOCK-FAILED: navig is still importable"); sys.exit(97)

    module, func = sys.argv[1].split(":")
    entry = getattr(importlib.import_module(module), func)
    sys.argv = [sys.argv[2], "--help"]
    try:
        entry()
    except SystemExit as exc:
        sys.exit(exc.code or 0)
    """
)


def _standalone_plugins() -> list[tuple[str, str, str]]:
    """(dir name, console script name, "module:function") for each standalone plugin."""
    found = []
    for pp in sorted(PLUGINS.glob("navig-*/pyproject.toml")):
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
        if ((data.get("tool") or {}).get("navig") or {}).get("mode") != "standalone":
            continue
        proj = data["project"]
        scripts = proj.get("scripts") or {}
        found.append((pp.parent.name, proj["name"], scripts.get(proj["name"], "")))
    return found


_FOUND = _standalone_plugins()


def test_discovery_is_not_empty() -> None:
    # Anti-vacuity: a discovery that silently finds nothing looks exactly like a clean pass.
    assert len(_FOUND) >= 5, f"only {len(_FOUND)} standalone plugins found under {PLUGINS}"
    assert SDK.joinpath("navig_sdk", "host.py").is_file(), f"{SDK} moved — update this guard"


@pytest.mark.parametrize(("plugin", "script", "target"), _FOUND, ids=[f[0] for f in _FOUND])
def test_standalone_plugin_runs_without_navig(plugin: str, script: str, target: str) -> None:
    assert target, f"{plugin}: no `{script}` console script (check_plugins_publishable enforces this)"
    env = dict(os.environ)
    # The plugin under test and the SDK from THIS tree, ahead of any installed copy.
    env["PYTHONPATH"] = os.pathsep.join([str(PLUGINS / plugin), str(SDK), env.get("PYTHONPATH", "")])
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "120"
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, target, script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )
    out = (proc.stdout + proc.stderr).strip()
    assert proc.returncode != 97, out
    missing = [ln for ln in out.splitlines() if "ModuleNotFoundError" in ln and "navig" not in ln]
    if proc.returncode and missing and not any("navig" in ln for ln in out.splitlines() if "Error" in ln):
        pytest.skip(f"{plugin}: a third-party dependency is not installed here: {missing[-1]}")
    assert proc.returncode == 0, (
        f"`{script} --help` failed with navig absent (exit {proc.returncode}). A standalone "
        f"plugin must reach navig only through navig-sdk or a guarded import:\n{out[-2500:]}"
    )
    # Typer prints "Usage:"; a hand-written help (navig-mini) lists its options instead.
    assert "usage" in out.lower() or "--help" in out, f"`{script} --help` printed no help:\n{out[-1500:]}"
