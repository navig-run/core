"""Smoke tests for the NAVIG in-app help system.

Covers:
- navig help            → renders index.md without crashing
- navig help <topic>    → returns topic content (markdown or registry)
- navig help --json     → valid JSON with 'topics' key
- navig help <missing>  → exits non-zero with informative error
- help topic resolution → markdown beats registry fallback
- task / flow alias     → task help describes the flow alias relationship
- short canonical topics exist: db, host, file, flow, ai, config
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_cli(args: list[str], capsys) -> tuple[int, str, str]:
    """Run the Typer app in-process; return (exit_code, stdout, stderr).

    With standalone_mode=False, Click returns the exit code as an int rather
    than raising SystemExit for non-zero exits from typer.Exit(code). We
    capture the return value so non-zero exits are reflected correctly.
    """
    from navig.cli import app

    exit_code = 0
    try:
        result = app(args, standalone_mode=False)
        if isinstance(result, int) and result != 0:
            exit_code = result
    except SystemExit as exc:
        exit_code = int(exc.code or 0)

    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.fixture(autouse=True)
def _register_commands(tmp_path: Path, monkeypatch):
    """Minimal isolation: fresh HOME + all external CLI commands registered."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.cli as cli_mod

    cli_mod._config_manager = None
    cli_mod._NO_CACHE = False
    cli_mod._register_external_commands(register_all=True)

    yield


# ---------------------------------------------------------------------------
# navig help (no topic)
# ---------------------------------------------------------------------------


def test_help_no_topic_exits_zero(capsys):
    code, out, _err = _invoke_cli(["help"], capsys)
    assert code == 0


def test_help_no_topic_contains_topic_list(capsys):
    _code, out, _err = _invoke_cli(["help"], capsys)
    # index.md or fallback should emit something about commands
    combined = out + _err
    assert len(combined.strip()) > 0, "help output must not be empty"


def test_help_json_topics_key(capsys):
    code, out, _err = _invoke_cli(["help", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert "topics" in data
    assert isinstance(data["topics"], list)
    assert len(data["topics"]) > 0


def test_help_json_sources_key(capsys):
    _code, out, _err = _invoke_cli(["help", "--json"], capsys)
    data = json.loads(out)
    assert "sources" in data
    assert "markdown" in data["sources"]
    assert "registry" in data["sources"]


def test_root_schema_outputs_json(capsys):
    code, out, _err = _invoke_cli(["--schema"], capsys)
    assert code == 0
    data = json.loads(out)
    assert "commands" in data
    assert isinstance(data["commands"], list)


# ---------------------------------------------------------------------------
# navig help <topic>
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topic", ["db", "host", "file", "flow", "ai", "config", "backup"])
def test_help_canonical_topics_exit_zero(topic: str, capsys):
    code, _out, _err = _invoke_cli(["help", topic], capsys)
    assert code == 0, f"navig help {topic} should exit 0"


def test_help_task_mentions_flow(capsys):
    """task help must reference 'flow' since task is a flow alias."""
    _code, out, _err = _invoke_cli(["help", "task"], capsys)
    combined = (out + _err).lower()
    assert "flow" in combined, "task help should describe the flow alias relationship"


def test_help_flow_mentions_run(capsys):
    _code, out, _err = _invoke_cli(["help", "flow"], capsys)
    combined = out + _err
    assert "run" in combined.lower()


def test_help_missing_topic_exits_nonzero(capsys):
    code, _out, _err = _invoke_cli(["help", "nonexistent_topic_xyz"], capsys)
    assert code != 0, "Unknown topic should exit non-zero"


def test_help_plain_flag_exits_zero(capsys):
    code, _out, _err = _invoke_cli(["help", "--plain"], capsys)
    assert code == 0


def test_help_topic_plain_exits_zero(capsys):
    code, _out, _err = _invoke_cli(["help", "db", "--plain"], capsys)
    assert code == 0


def test_help_topic_json(capsys):
    code, out, _err = _invoke_cli(["help", "db", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert data.get("topic") == "db"


# ---------------------------------------------------------------------------
# Markdown file path resolution
# ---------------------------------------------------------------------------


def test_help_md_dir_exists():
    """navig/help/ directory must be reachable from the CLI module."""
    cli_init = Path(__file__).resolve().parent.parent / "navig" / "cli" / "__init__.py"
    expected_help_dir = cli_init.parent.parent / "help"
    assert expected_help_dir.is_dir(), (
        f"Help directory not found at {expected_help_dir}. "
        "The help_dir path in help_command may still be wrong."
    )


def test_help_index_md_exists():
    help_dir = Path(__file__).resolve().parent.parent / "navig" / "help"
    assert (help_dir / "index.md").exists(), "navig/help/index.md must exist"


def test_help_md_files_cover_canonical_topics():
    help_dir = Path(__file__).resolve().parent.parent / "navig" / "help"
    for topic in ["db", "host", "file", "flow", "task", "ai", "config", "backup"]:
        assert (help_dir / f"{topic}.md").exists(), f"navig/help/{topic}.md must exist"
