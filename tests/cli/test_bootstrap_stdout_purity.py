"""Bootstrap stdout purity — pre-dispatch narration must never pollute stdout.

On a virgin config dir the CLI bootstrap runs first-time onboarding BEFORE
command dispatch. It used to print its multi-step banner (~22 steps), the
verification dashboard, and config-migration narration to stdout — corrupting
machine-readable output for EVERY command (`navig doctor --json`,
`navig tiktok info --json`, any current or future `--json` verb).

The global contract under test, via REAL subprocesses on a virgin
NAVIG_CONFIG_DIR and — deliberately — WITHOUT NAVIG_SKIP_ONBOARDING:

- a `--json` invocation skips first-run onboarding entirely (a programmatic
  caller can never answer the wizard) and stdout parses as exactly one JSON
  document;
- a plain human command still gets onboarding — visible (on stderr) and
  functional — while stdout stays free of narration;
- stdout belongs to the command's OUTPUT; narration belongs on stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent

# Narration lines that must never appear on stdout. Keep in sync with the
# onboarding banner (navig/onboarding/runner.py) and the migration narration
# (navig/core/migrations.py).
_NARRATION_MARKERS = (
    "first-time setup",
    "Welcome to NAVIG",
    "Verification summary",
    "configuration migrations",
    "NAVIG_SKIP_ONBOARDING",
)


def _virgin_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Env for a pristine install: isolated config/data dirs, NO skip flag."""
    env = os.environ.copy()
    # The entire point of these tests: onboarding is armed, not skipped.
    env.pop("NAVIG_SKIP_ONBOARDING", None)
    env.pop("NAVIG_ONBOARDING_ACTIVE", None)
    config_dir = tmp_path / "navig-config"
    config_dir.mkdir()
    env["NAVIG_CONFIG_DIR"] = str(config_dir)
    env["NAVIG_DATA_DIR"] = str(tmp_path / "navig-data")
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env, config_dir


def _run_cli(
    args: list[str], env: dict[str, str], timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def test_doctor_json_on_virgin_config_dir_emits_exactly_one_json_document(tmp_path):
    """`navig doctor --json` on a pristine install: stdout is pure JSON.

    No banner, no step progress, no migration narration — parsing the WHOLE
    of stdout enforces "exactly one document" (any stray line breaks it).
    """
    env, _config_dir = _virgin_env(tmp_path)

    result = _run_cli(["doctor", "--json"], env)

    payload = json.loads(result.stdout)  # raises if stdout is not one JSON doc
    assert isinstance(payload, dict)
    assert "sections" in payload
    assert "ok" in payload

    for marker in _NARRATION_MARKERS:
        assert marker not in result.stdout, f"narration marker {marker!r} leaked into stdout"

    # Exit code is doctor's own verdict (0 all-green / 1 findings) — never a
    # crash or a wizard hang.
    assert result.returncode in (0, 1), (
        f"unexpected exit {result.returncode}; stderr: {result.stderr[:2000]}"
    )


def test_plain_command_on_virgin_dir_still_onboards_via_stderr(tmp_path):
    """A human command on a pristine install still gets first-run onboarding —
    on stderr, leaving stdout to the command's own output — and the wizard is
    functional (phase 1 writes the base config)."""
    env, config_dir = _virgin_env(tmp_path)

    result = _run_cli(["paths"], env)

    # Onboarding ran and is visible — on stderr.
    assert "first-time setup" in result.stderr, (
        f"onboarding banner missing from stderr: {result.stderr[:2000]}"
    )
    # ...and never on stdout.
    for marker in _NARRATION_MARKERS:
        assert marker not in result.stdout, f"narration marker {marker!r} leaked into stdout"

    # Functional: the phase-1 config-file step wrote the base configuration.
    assert (config_dir / "config.yaml").exists(), (
        "onboarding did not write config.yaml — wizard no longer functional"
    )

    # The command itself still dispatched and produced its output on stdout.
    assert result.returncode == 0, f"stderr: {result.stderr[:2000]}"
    assert "NAVIG Paths" in result.stdout
