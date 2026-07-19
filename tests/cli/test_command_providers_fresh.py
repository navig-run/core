"""The shipped command→provider map must stay in sync with first-party plugins.

`navig doctor` reports drift as informational only (a user can't regenerate the
release map, and third-party plugins are legitimately absent from it), so the
FIRST-PARTY staleness invariant is enforced here at build/test time instead:
if a plugin under `plugins/` adds/renames a `navig.commands` entry point, this
fails until `scripts/gen_command_providers.py` is re-run and committed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # core/tests/cli/ -> repo root
GENERATOR = REPO_ROOT / "scripts" / "gen_command_providers.py"
PLUGINS_DIR = REPO_ROOT / "plugins"


@pytest.mark.skipif(
    not GENERATOR.exists() or not PLUGINS_DIR.exists(),
    reason="monorepo layout (scripts/ + plugins/) not present in this checkout",
)
def test_command_providers_map_is_fresh():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "command_providers.json is stale vs plugins/*/pyproject.toml — run "
        "`python scripts/gen_command_providers.py` and commit.\n"
        f"{result.stdout}\n{result.stderr}"
    )
