"""``navig_run_command`` must run the command an agent writes — and only read-only ones.

Two measured defects sat behind one small function:

* **The spelling the schema invites did not work.** ``command="host list"`` reached the CLI
  as ONE argv token and died with ``No such command 'host list'`` (exit 2); only
  ``command="host", args=["list"]`` worked, which the schema never says. Verified against
  the real CLI, not inferred.
* **The allowlist was a string prefix.** ``full_cmd.startswith("help")`` accepted
  ``"helper"``, and ``"host list"`` accepted ``"host list…"`` of any verb spelling.

The tests below drive the handler with ``subprocess.run`` stubbed, so they assert the exact
argv the CLI would receive and that a refused command never spawns a process at all.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from navig.mcp.tools import system as system_bundle


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003 - mirrors subprocess.run
        seen.append(list(argv))
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_joined_and_split_spellings_issue_the_same_command(calls: list[list[str]]) -> None:
    joined = system_bundle._tool_run_command(None, {"command": "host list"})
    split = system_bundle._tool_run_command(None, {"command": "host", "args": ["list"]})
    expected = [sys.executable, "-m", "navig", "host", "list"]
    assert calls == [expected, expected]
    assert joined == split == {"stdout": "ok\n", "stderr": "", "returncode": 0}


def test_flags_follow_an_allowlisted_path(calls: list[list[str]]) -> None:
    system_bundle._tool_run_command(None, {"command": "host list --json"})
    assert calls[-1][3:] == ["host", "list", "--json"]


@pytest.mark.parametrize(
    "command",
    ["host delete", "helper", "config set a b", "block apply x", "navig_run_command"],
)
def test_anything_not_read_only_is_refused_without_running_it(
    command: str, calls: list[list[str]]
) -> None:
    result = system_bundle._tool_run_command(None, {"command": command})
    assert calls == [], f"{command!r} reached subprocess.run"
    assert "not allowed" in result["error"]
    assert result["allowed"], "a refusal must tell the caller what IS allowed"


def test_a_path_prefix_is_not_a_command(calls: list[list[str]]) -> None:
    """``host`` alone is a namespace, not one of the allowlisted read commands."""
    result = system_bundle._tool_run_command(None, {"command": "host"})
    assert calls == []
    assert "not allowed" in result["error"]


def test_empty_command_is_named_as_such(calls: list[list[str]]) -> None:
    result = system_bundle._tool_run_command(None, {"command": "   "})
    assert calls == []
    assert result["error"] == "command is required"
    assert result["allowed"]


def test_the_description_matches_this_contract() -> None:
    """The override must not promise the generic executor it was before."""
    from navig.mcp.annotations import DESCRIPTION_OVERRIDES

    text = DESCRIPTION_OVERRIDES["navig_run_command"]
    assert "READ-ONLY" in text
    assert "host list" in text  # the spelling that now works is documented
    assert "any navig" not in text.lower()
