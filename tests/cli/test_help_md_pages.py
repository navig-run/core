"""`navig help <cmd>` must serve the rich markdown guides in navig/help/.

Regression suite for the dispatch bug where main.py rewrote EVERY
``navig help <topic>`` to ``navig <topic> --help``, so the carefully written
per-command guides (ledger.md, undo.md, audit.md, …) were unreachable from
the CLI — users only ever saw Typer's flag dump.

Covers:
- navig help ledger          → renders ledger.md + a pointer to `--help`
- navig help ai-providers    → renders the page, NO pointer (not a command)
- navig help undo            → pointer works for direct-app commands too
- navig help db --json       → topic JSON shape unchanged
- navig help --json          → listing JSON shape unchanged
- orphan tripwire            → every md page maps to a command or known topic
- end-to-end subprocess      → the full main.py dispatch chain, not just run_help
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
HELP_DIR = ROOT / "navig" / "help"


# ---------------------------------------------------------------------------
# Helpers (same in-process harness as test_help_system.py)
# ---------------------------------------------------------------------------


def _invoke_cli(args: list[str], capsys) -> tuple[int, str, str]:
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
def _isolate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.cli as cli_mod

    cli_mod._config_manager = None
    cli_mod._NO_CACHE = False
    yield


# ---------------------------------------------------------------------------
# Rendering: the markdown guide, not the flag dump
# ---------------------------------------------------------------------------


def test_help_ledger_renders_markdown_guide(capsys):
    code, out, err = _invoke_cli(["help", "ledger"], capsys)
    combined = out + err
    assert code == 0
    # Distinctive prose from ledger.md — proves the guide rendered.
    assert "tamper-evident" in combined
    # Not Typer's flag dump.
    assert "Usage:" not in combined


def test_help_ledger_includes_flag_reference_pointer(capsys):
    _code, out, err = _invoke_cli(["help", "ledger"], capsys)
    combined = out + err
    assert "Full flag reference" in combined
    assert "navig ledger --help" in combined


def test_help_undo_pointer_covers_direct_app_commands(capsys):
    """`undo` registers straight on the app (registration._try_register_undo),
    invisible to _EXTERNAL_CMD_MAP and the generated manifest — the pointer
    gate must still recognize it."""
    code, out, err = _invoke_cli(["help", "undo"], capsys)
    combined = out + err
    assert code == 0
    assert "navig undo --help" in combined


def test_help_pure_topic_gets_no_pointer(capsys):
    """`ai-providers` is a topic page, not a command — a pointer would
    advertise `navig ai-providers --help`, which errors."""
    code, out, err = _invoke_cli(["help", "ai-providers"], capsys)
    combined = out + err
    assert code == 0
    assert "fallback" in combined.lower()  # distinctive ai-providers.md prose
    assert "ai-providers --help" not in combined


def test_help_topic_plain_output_is_raw_markdown_without_pointer(capsys):
    """--plain stays byte-honest (scripts may consume the raw page)."""
    code, out, _err = _invoke_cli(["help", "ledger", "--plain"], capsys)
    assert code == 0
    assert "tamper-evident" in out
    assert "Full flag reference" not in out


# ---------------------------------------------------------------------------
# JSON contracts stay frozen
# ---------------------------------------------------------------------------


def test_help_topic_json_shape_unchanged(capsys):
    code, out, _err = _invoke_cli(["help", "db", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert set(data.keys()) == {"topic", "content", "source"}
    assert data["topic"] == "db"
    assert data["source"] == "markdown"
    assert "Full flag reference" not in out


def test_help_listing_json_shape_unchanged(capsys):
    code, out, _err = _invoke_cli(["help", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert set(data.keys()) == {"topics", "sources"}
    assert set(data["sources"].keys()) == {"markdown", "registry"}


# ---------------------------------------------------------------------------
# Orphan tripwire: every guide must map to a command or a known topic
# ---------------------------------------------------------------------------

#: Pages that are legitimately NOT top-level commands. Adding a page for a
#: command and later deleting the command must fail this test unless the
#: page is consciously parked here (or removed with the command).
_ALLOWED_TOPIC_PAGES: dict[str, str] = {
    "ai-providers": "conceptual guide for `navig ai providers` / provider fallback",
}


def _schema_top_level_commands() -> set[str]:
    from navig.cli.registry import get_schema

    cmds: set[str] = set()
    for row in get_schema().get("commands", []):
        path = str(row.get("path", "")).strip()
        parts = path.split()
        if len(parts) >= 2 and parts[0] == "navig":
            cmds.add(parts[1])
    return cmds


def test_every_help_page_maps_to_a_command_or_known_topic():
    from navig.cli.registration import get_external_commands
    from navig.commands.help_cmd import _DIRECT_APP_COMMANDS

    stems = {
        p.stem.lower()
        for p in HELP_DIR.glob("*.md")
        if p.stem.lower() not in {"index", "readme"}
    }
    assert stems, "navig/help/ must contain topic pages"

    known_commands = (
        _schema_top_level_commands()
        | set(get_external_commands())
        | set(_DIRECT_APP_COMMANDS)
    )

    orphans = stems - known_commands - set(_ALLOWED_TOPIC_PAGES)
    assert not orphans, (
        f"help pages with no matching top-level command: {sorted(orphans)}. "
        "Either the command was removed/renamed (update or retire the page) "
        "or this is a pure topic page (add it to _ALLOWED_TOPIC_PAGES with a reason)."
    )

    # The allowlist must not rot in either direction.
    stale_now_commands = {t for t in _ALLOWED_TOPIC_PAGES if t in known_commands}
    assert not stale_now_commands, (
        f"allowlisted topics became real commands — remove from allowlist: "
        f"{sorted(stale_now_commands)}"
    )
    missing_pages = set(_ALLOWED_TOPIC_PAGES) - stems
    assert not missing_pages, (
        f"allowlisted topics with no page left — prune the allowlist: "
        f"{sorted(missing_pages)}"
    )


# ---------------------------------------------------------------------------
# End-to-end: the real dispatch chain through navig.main
# ---------------------------------------------------------------------------


def test_help_ledger_end_to_end_serves_guide_not_flag_dump(tmp_path):
    """Full-chain regression: `python -m navig help ledger` must render the
    markdown guide. Before the fix, _normalize_help_compat_args rewrote it to
    `navig ledger --help` and users only ever saw the Typer flag dump."""
    env = os.environ.copy()
    env["NAVIG_CONFIG_DIR"] = str(tmp_path / "cfg")
    env["NAVIG_DATA_DIR"] = str(tmp_path / "data")
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "120"

    result = subprocess.run(
        [sys.executable, "-m", "navig", "help", "ledger"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[:2000]
    assert "tamper-evident" in result.stdout
    assert "navig ledger --help" in result.stdout
    assert "Usage:" not in result.stdout
