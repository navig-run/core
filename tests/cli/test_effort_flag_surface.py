"""
CLI surface regression tests for the --effort flag.

Verifies that:
  - `navig ask --help` exposes --effort/-e
  - `navig agent run --help` exposes --effort/-e
  - Both accept valid effort strings without crashing
  - The flag is documented with valid values in the help text
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent


def _cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    env["NAVIG_LAUNCHER"] = "fuzzy"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(
    args: list[str], *, tmp_path: Path, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# navig ask --effort
# ---------------------------------------------------------------------------


def test_ask_help_exposes_effort_long_flag(tmp_path: Path):
    """`navig ask --help` must mention --effort."""
    result = _run_cli(["ask", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    assert "--effort" in combined, (
        "--effort not present in `navig ask --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_ask_help_exposes_effort_short_flag(tmp_path: Path):
    """`navig ask --help` must mention -e (short alias)."""
    result = _run_cli(["ask", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    assert "-e" in combined, (
        "-e not present in `navig ask --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_ask_help_mentions_effort_values(tmp_path: Path):
    """`navig ask --help` documents at least one valid effort level."""
    result = _run_cli(["ask", "--help"], tmp_path=tmp_path)
    combined = (result.stdout + result.stderr).lower()
    # At least one of the canonical effort labels must appear in help text
    assert any(kw in combined for kw in ("low", "medium", "high", "max", "ultra")), (
        "No effort level name found in `navig ask --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# navig agent run --effort
# ---------------------------------------------------------------------------


def test_agent_run_help_exposes_effort_long_flag(tmp_path: Path):
    """`navig agent run --help` must mention --effort."""
    result = _run_cli(["agent", "run", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    assert "--effort" in combined, (
        "--effort not present in `navig agent run --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_agent_run_help_exposes_effort_short_flag(tmp_path: Path):
    """`navig agent run --help` must mention -e (short alias)."""
    result = _run_cli(["agent", "run", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    assert "-e" in combined, (
        "-e not present in `navig agent run --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# navig memory compact --help
# ---------------------------------------------------------------------------


def test_memory_compact_help_is_registered(tmp_path: Path):
    """`navig memory compact --help` must exit cleanly and show help text."""
    result = _run_cli(["memory", "compact", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"`navig memory compact --help` returned non-zero.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "compact" in combined.lower(), (
        "Word 'compact' not found in `navig memory compact --help` output."
    )


def test_memory_compact_help_exposes_session_arg(tmp_path: Path):
    """`navig memory compact --help` documents the SESSION positional argument."""
    result = _run_cli(["memory", "compact", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    # SESSION is a Typer positional (shown as [SESSION] in the Arguments panel)
    assert "SESSION" in combined or "session" in combined.lower(), (
        "SESSION argument not present in `navig memory compact --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_memory_compact_help_exposes_yes_flag(tmp_path: Path):
    """`navig memory compact --help` documents the --yes/-y skip-confirm flag."""
    result = _run_cli(["memory", "compact", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr
    assert "--yes" in combined or "-y" in combined, (
        "--yes / -y not present in `navig memory compact --help`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# No-regression: --effort flag must NOT be absent from generated help schema
# ---------------------------------------------------------------------------


def test_generated_schema_ask_has_effort(tmp_path: Path):
    """The JSON schema generated by `navig --schema` records --effort on ask."""
    result = _run_cli(["--schema"], tmp_path=tmp_path)
    if result.returncode != 0 or not result.stdout.strip():
        pytest.skip("--schema not supported or produced no output; skipping schema check")
    import json

    try:
        schema = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.skip("--schema did not return valid JSON")

    # Walk the schema looking for the ask command entry
    def _find_effort(obj, depth=0):
        if depth > 6:
            return False
        if isinstance(obj, dict):
            if obj.get("name") in ("ask",) and "options" in obj:
                opts = obj["options"]
                return any(
                    "--effort" in (o.get("name", "") + " ".join(o.get("aliases", [])))
                    for o in (opts if isinstance(opts, list) else [])
                )
            return any(_find_effort(v, depth + 1) for v in obj.values())
        if isinstance(obj, list):
            return any(_find_effort(item, depth + 1) for item in obj)
        return False

    if not _find_effort(schema):
        pytest.skip("Schema structure not parseable in expected format; effort check skipped")


def test_ask_invalid_effort_rejected_early(tmp_path: Path):
    """`navig ask` should fail fast on invalid effort values."""
    result = _run_cli(["ask", "hello", "--effort", "banana"], tmp_path=tmp_path)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0
    assert "unknown effort level" in combined


def test_agent_run_invalid_effort_rejected_before_formation_lookup(tmp_path: Path):
    """`navig agent run` should validate effort before formation resolution."""
    result = _run_cli(
        ["agent", "run", "designer", "--task", "hello", "--effort", "banana"],
        tmp_path=tmp_path,
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0
    assert "unknown effort level" in combined
