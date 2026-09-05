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
    # The session-wide NAVIG_CONFIG_DIR (core/tests/conftest.py) is INHERITED by
    # `os.environ.copy()` and WINS over HOME in `platform.paths.config_dir`, so
    # without this the CLI reads a config this test never wrote — and falls back to
    # the default port, where the operator's LIVE daemon answers. That is how
    # `test_gateway_session_handles_missing_gateway_without_invalid_url` came to
    # fail with HTTP 401 once #994 required a bearer token.
    env["NAVIG_CONFIG_DIR"] = str(tmp_path / ".navig")
    env["NAVIG_DATA_DIR"] = str(tmp_path / ".navig" / "data")
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
    """The generated command schema records --effort on `navig ask`.

    This asserts now. It previously could not fail for THREE independent reasons, each
    of which turned a breach into a skip:

    1. It invoked `navig --schema`. That root flag is declared in cli/__init__.py, but
       `_maybe_handle_fast_path` classifies it as a global flag with no command token and
       prints the human banner instead, so the eager callback never runs at all -- exit 0,
       no JSON. (Verified by probe 2026-09-01; `navig help --schema` is the surface that
       works, and is what this now calls.)
    2. A JSONDecodeError became `pytest.skip` -- which is what actually fired, every run.
    3. The finder looked for `obj["name"] == "ask"`, but schema entries key on `path`
       (`"navig ask"`) and carry no `name` at all, so even valid JSON could not have
       matched. A third skip caught that too.

    A no-regression test whose every failure mode is a skip is not a test. The invariant
    itself held the whole time -- `navig ask` does expose --effort -- so nothing regressed
    while it was blind, but nothing would have been reported if it had.

    What this guards, precisely: `get_schema()` serves `generated/commands.json` and only
    falls back to runtime introspection when that file is absent. So this asserts the
    SHIPPED MANIFEST -- the document tooling actually consumes -- not live decorators.
    That is the right target (a flag missing from the manifest is invisible to every
    consumer even if the decorator exists), but it means renaming the option in the source
    will not fail this test until the manifest is regenerated; the `command manifest
    freshness` gate step is what covers that half. Teeth-tested by deleting the --effort
    entry from the manifest, which fails here as intended.
    """
    import json

    result = _run_cli(["help", "--schema"], tmp_path=tmp_path)
    assert result.returncode == 0, (
        f"`navig help --schema` failed ({result.returncode})\n"
        f"stdout:\n{result.stdout[:2000]}\nstderr:\n{result.stderr[:2000]}"
    )
    assert result.stdout.strip(), "`navig help --schema` produced no output"

    # No try/except-skip: unparseable output IS the failure. stdout must carry the
    # document and nothing else -- that is what "machine-readable" means.
    schema = json.loads(result.stdout)

    commands = schema.get("commands")
    assert isinstance(commands, list) and commands, "schema has no commands list"

    ask = [c for c in commands if c.get("path") == "navig ask"]
    assert ask, (
        "no entry with path 'navig ask' in the schema. Entries key on `path`; if that "
        "changed, fix this lookup rather than letting it degrade into a skip."
    )

    flags = [f for opt in (ask[0].get("options") or []) for f in (opt.get("flags") or [])]
    assert "--effort" in flags, (
        f"--effort is missing from `navig ask` in the generated schema. Flags found: {flags}"
    )


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


def test_root_schema_flag_emits_only_json(tmp_path: Path):
    """`navig --schema` must put the document on stdout and nothing else.

    It was a phantom: the flag is declared in cli/__init__.py with an eager callback that
    dumps JSON, but `_maybe_handle_fast_path` classified it as a global flag carrying no
    command token, printed the human banner and returned True -- so Typer never parsed and
    the callback never ran. Exit 0, no JSON, for a flag whose own help says
    "machine-readable".

    Deferring it to Typer exposed a second, independent defect: importing
    navig.commands.server_template constructed a ServerTemplateManager at MODULE level,
    which scans template dirs and emits `ch.warning` to STDOUT. Building the command tree
    imports it, so the schema arrived preceded by "Skipping 'ahk': ..." and would not
    parse. Both had to be fixed for this to pass; either one alone leaves it failing.
    """
    import json

    result = _run_cli(["--schema"], tmp_path=tmp_path)
    assert result.returncode == 0, (
        f"`navig --schema` failed ({result.returncode})\nstderr:\n{result.stderr[:1500]}"
    )
    assert result.stdout.lstrip().startswith("{"), (
        "stdout does not begin with the JSON document. Something printed to stdout before "
        f"it -- diagnostics belong on stderr. First 200 chars:\n{result.stdout[:200]}"
    )

    schema = json.loads(result.stdout)  # unparseable stdout IS the failure
    assert schema.get("commands"), "schema carries no commands"


def test_importing_a_command_module_prints_nothing(tmp_path: Path):
    """Importing a command module must not write to stdout.

    navig.commands.server_template built its ServerTemplateManager at import time, so the
    mere import emitted 136 bytes of template warnings -- a disk scan and console output
    on every invocation that builds the command tree, and corruption of any machine-readable
    stdout. Scoped to the module that actually regressed rather than every command module,
    because a repo-wide version would need a measured allowlist first.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import navig.commands.server_template"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert result.returncode == 0, f"import failed:\n{result.stderr[:1500]}"
    assert result.stdout == "", (
        "importing navig.commands.server_template wrote to stdout:\n"
        f"{result.stdout[:400]}\n"
        "Build managers on first use, not at import — see _tm()/_cm() in that module."
    )
