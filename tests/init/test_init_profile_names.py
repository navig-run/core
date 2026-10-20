"""`navig init --profile <name>` — name resolution for the four public profiles.

The installer is where a bad name dies (with the list of valid ones), so this
drives the CLI entry the way `install.sh -p` does: through the typer app.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.cli import app
from navig.installer.profiles import VALID_PROFILES

pytestmark = pytest.mark.integration

runner = CliRunner()

PUBLIC_PROFILES = ("cli", "minimal", "server", "full")


def test_unknown_profile_exits_nonzero_and_lists_valid_names():
    result = runner.invoke(app, ["init", "--profile", "bogus", "--dry-run"])
    assert result.exit_code != 0, (
        "an unknown profile must fail the invocation — install.sh -p relies on "
        f"the exit code; output was:\n{result.output}"
    )
    output = result.output  # click ≥8.2: stdout+stderr mix, so err=True is visible
    assert "Unknown profile 'bogus'" in output
    for name in sorted(VALID_PROFILES):
        assert name in output, f"valid-name list must mention {name!r}: {output}"


@pytest.mark.parametrize("name", PUBLIC_PROFILES)
def test_public_profile_names_are_accepted(name: str):
    """Each public profile resolves through the CLI (dry run — no mutation)."""
    result = runner.invoke(
        app, ["init", "--profile", name, "--dry-run", "--quiet"]
    )
    assert result.exit_code == 0, (
        f"navig init --profile {name} --dry-run failed:\n{result.output}"
    )


def test_dry_run_reports_the_effective_profile():
    """--dry-run prints the manifest the profile resolves to (modules, extras,
    daemon flags, mode, and the config key it would write)."""
    result = runner.invoke(app, ["init", "--profile", "server", "--dry-run"])
    assert result.exit_code == 0, result.output
    output = result.output
    assert "Effective profile" in output
    assert "install.profile = server" in output
    assert "--gateway" in output and "--scheduler" not in output  # deprecated flag dropped from the server profile
    assert "navig[interactive,telegram]" in output
