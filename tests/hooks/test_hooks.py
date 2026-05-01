"""Tests for navig.hooks — events, registry, and executor.

Covers:
  - HookEvent / HookContext / HookResult  (navig.hooks.events)
  - HookDefinition / HookRegistry         (navig.hooks.registry)
  - _is_private_url / HookExecutor        (navig.hooks.executor)

All subprocess calls are mocked so no real scripts are executed.
Filesystem I/O is patched with tmp_path or in-memory YAML strings.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from navig.hooks.events import HookContext, HookEvent, HookResult
from navig.hooks.executor import HookExecutor, _is_private_url
from navig.hooks.registry import HookDefinition, HookRegistry

# ── HookEvent ────────────────────────────────────────────────────────────────


def test_hook_event_values() -> None:
    assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"
    assert HookEvent.POST_TOOL_USE.value == "PostToolUse"
    assert HookEvent.POST_TOOL_USE_FAILURE.value == "PostToolUseFailure"
    assert HookEvent.PERMISSION_DENIED.value == "PermissionDenied"
    assert HookEvent.NOTIFICATION.value == "Notification"
    assert HookEvent.SESSION_START.value == "SessionStart"


def test_hook_event_str_subclass() -> None:
    # HookEvent is a str enum so comparing with its value works directly.
    assert HookEvent.PRE_TOOL_USE == "PreToolUse"


# ── HookContext ───────────────────────────────────────────────────────────────


def test_hook_context_to_json_minimal() -> None:
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")
    payload = json.loads(ctx.to_json())
    assert payload["event"] == "PreToolUse"
    assert payload["tool_name"] == "bash"
    assert "tool_result" not in payload
    assert "tool_error" not in payload
    assert "metadata" not in payload


def test_hook_context_to_json_full() -> None:
    ctx = HookContext(
        event=HookEvent.POST_TOOL_USE,
        tool_name="python",
        tool_input={"code": "print(1)"},
        tool_result="1",
        tool_error=None,
        session_id="sess-1",
        turn_id="turn-1",
        metadata={"classifier": "safe"},
    )
    payload = json.loads(ctx.to_json())
    assert payload["tool_result"] == "1"
    assert payload["metadata"] == {"classifier": "safe"}
    assert payload["session_id"] == "sess-1"


def test_hook_context_to_json_includes_tool_error() -> None:
    ctx = HookContext(
        event=HookEvent.POST_TOOL_USE_FAILURE,
        tool_name="bash",
        tool_error="exit code 1",
    )
    payload = json.loads(ctx.to_json())
    assert payload["tool_error"] == "exit code 1"


def test_hook_context_to_json_skips_none_error() -> None:
    ctx = HookContext(event=HookEvent.SESSION_START)
    payload = json.loads(ctx.to_json())
    assert "tool_error" not in payload


# ── HookResult ────────────────────────────────────────────────────────────────


def test_hook_result_defaults() -> None:
    r = HookResult()
    assert r.block is False
    assert r.message == ""
    assert r.executed is False
    assert r.retry is False


# ── HookDefinition ────────────────────────────────────────────────────────────


def test_hook_definition_matches_tool_no_filter() -> None:
    defn = HookDefinition(event=HookEvent.PRE_TOOL_USE, command="/hook.sh")
    assert defn.matches_tool("bash") is True
    assert defn.matches_tool("python") is True
    assert defn.matches_tool("") is True


def test_hook_definition_matches_tool_exact() -> None:
    defn = HookDefinition(event=HookEvent.PRE_TOOL_USE, command="/hook.sh", tool_filter="bash")
    assert defn.matches_tool("bash") is True
    assert defn.matches_tool("python") is False


def test_hook_definition_matches_tool_glob() -> None:
    defn = HookDefinition(event=HookEvent.PRE_TOOL_USE, command="/hook.sh", tool_filter="bash*")
    assert defn.matches_tool("bash") is True
    assert defn.matches_tool("bash_run") is True
    assert defn.matches_tool("python") is False


def test_hook_definition_matches_tool_case_insensitive() -> None:
    defn = HookDefinition(event=HookEvent.PRE_TOOL_USE, command="/hook.sh", tool_filter="BASH")
    assert defn.matches_tool("bash") is True


# ── HookRegistry ─────────────────────────────────────────────────────────────


def _write_hooks_yaml(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_registry_no_files_returns_empty(tmp_path: Path) -> None:
    reg = HookRegistry(global_dir=tmp_path / "global", project_dir=tmp_path / "project")
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert hooks == []


def test_registry_loads_global_file(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: PreToolUse
              command: /hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert len(hooks) == 1
    assert hooks[0].command == "/hook.sh"


def test_registry_merges_project_after_global(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    p = tmp_path / "project"
    p.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: PreToolUse
              command: /global-hook.sh
        """,
    )
    _write_hooks_yaml(
        p / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: PreToolUse
              command: /project-hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=p)
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert len(hooks) == 2
    assert hooks[0].command == "/global-hook.sh"
    assert hooks[1].command == "/project-hook.sh"


def test_registry_enabled_false_returns_empty(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          enabled: false
          definitions:
            - event: PreToolUse
              command: /hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert hooks == []


def test_registry_tool_filter_applies(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: PreToolUse
              tool: python
              command: /python-hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    assert len(reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE, "python")) == 1
    assert len(reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE, "bash")) == 0


def test_registry_skips_unknown_event(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: UnknownEvent
              command: /hook.sh
            - event: PreToolUse
              command: /real-hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert len(hooks) == 1
    assert hooks[0].command == "/real-hook.sh"


def test_registry_skips_empty_command(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: PreToolUse
              command: ""
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert hooks == []


def test_registry_timeout_override(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          timeout_seconds: 99
          definitions:
            - event: PreToolUse
              command: /hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert hooks[0].timeout_seconds == 99


def test_registry_allow_network_default_false(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions: []
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    assert reg.allow_network is False


def test_registry_allow_network_explicit_true(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          allow_network: true
          definitions: []
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=tmp_path / "project")
    assert reg.allow_network is True


def test_registry_reload_on_second_call(tmp_path: Path) -> None:
    g = tmp_path / "global"
    g.mkdir()
    p = tmp_path / "project"
    _write_hooks_yaml(
        g / "hooks.yaml",
        """\
        hooks:
          definitions:
            - event: PreToolUse
              command: /hook.sh
        """,
    )
    reg = HookRegistry(global_dir=g, project_dir=p)
    # First call triggers load
    hooks = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert len(hooks) == 1
    # Second call (already loaded) returns same
    hooks2 = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE)
    assert len(hooks2) == 1


# ── _is_private_url ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1/hook", True),
        ("http://10.0.0.1/hook", True),
        ("http://192.168.1.1/hook", True),
        ("http://172.16.0.1/hook", True),
        ("https://169.254.169.254/metadata", True),
        ("http://example.com/hook", False),
        ("https://api.openai.com/v1", False),
        ("ftp://127.0.0.1/hook", False),  # not http/https
        ("/usr/local/bin/hook.sh", False),  # filesystem path
        ("", False),
    ],
)
def test_is_private_url(url: str, expected: bool) -> None:
    assert _is_private_url(url) is expected


# ── HookExecutor ──────────────────────────────────────────────────────────────


def _mock_registry(
    hooks: list[HookDefinition],
    allow_network: bool = False,
) -> HookRegistry:
    """Build a minimal mock registry that returns *hooks* for any event."""
    reg = MagicMock(spec=HookRegistry)
    reg.get_hooks_for_event.return_value = hooks
    reg.allow_network = allow_network
    return reg


def _make_defn(command: str = "/hook.sh", timeout: int = 5) -> HookDefinition:
    return HookDefinition(
        event=HookEvent.PRE_TOOL_USE,
        command=command,
        timeout_seconds=timeout,
    )


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


# ── exit-code 0: silent success ──────────────────────────────────────────────


def test_executor_exit0_silent_success(tmp_path: Path) -> None:
    reg = _mock_registry([_make_defn()])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")

    with patch("subprocess.run", return_value=_proc(0)) as mock_run:
        result = executor.run(ctx)

    assert result.executed is True
    assert result.block is False
    assert result.message == ""
    mock_run.assert_called_once()


# ── exit-code 2: block on PRE_TOOL_USE ───────────────────────────────────────


def test_executor_exit2_blocks_pre_tool_use(tmp_path: Path) -> None:
    reg = _mock_registry([_make_defn()])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")

    with patch("subprocess.run", return_value=_proc(2, stderr="blocked by policy")):
        result = executor.run(ctx)

    assert result.block is True
    assert "blocked by policy" in result.message


def test_executor_exit2_does_not_block_post_tool_use() -> None:
    reg = _mock_registry([_make_defn()])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.POST_TOOL_USE, tool_name="bash")

    with patch("subprocess.run", return_value=_proc(2, stderr="some warning")):
        result = executor.run(ctx)

    assert result.block is False
    assert "some warning" in result.message


# ── exit-code other: surface only, no block ───────────────────────────────────


def test_executor_exit1_not_blocked() -> None:
    reg = _mock_registry([_make_defn()])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")

    with patch("subprocess.run", return_value=_proc(1, stderr="something went wrong")):
        result = executor.run(ctx)

    assert result.executed is True
    assert result.block is False
    assert result.message == ""  # not injected into model context for exit != 2


# ── timeout ───────────────────────────────────────────────────────────────────


def test_executor_timeout_does_not_raise() -> None:
    import subprocess

    reg = _mock_registry([_make_defn(timeout=1)])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="/hook.sh", timeout=1)):
        result = executor.run(ctx)

    # timed out hook is not counted as executed
    assert result.executed is False
    assert result.block is False


# ── network SSRF guard ────────────────────────────────────────────────────────


def test_executor_rejects_http_hook_when_network_disabled() -> None:
    defn = _make_defn(command="http://example.com/hook")
    reg = _mock_registry([defn], allow_network=False)
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE)

    with patch("subprocess.run") as mock_run:
        result = executor.run(ctx)

    mock_run.assert_not_called()
    assert result.executed is False


def test_executor_allows_http_hook_when_network_enabled() -> None:
    defn = _make_defn(command="http://example.com/hook")
    reg = _mock_registry([defn], allow_network=True)
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE)

    with patch("subprocess.run", return_value=_proc(0)):
        result = executor.run(ctx)

    assert result.executed is True


def test_executor_rejects_private_ip_even_when_network_enabled() -> None:
    defn = _make_defn(command="http://192.168.1.1/hook")
    reg = _mock_registry([defn], allow_network=True)
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE)

    with patch("subprocess.run") as mock_run:
        result = executor.run(ctx)

    mock_run.assert_not_called()
    assert result.executed is False


# ── retry signal via structured stdout ───────────────────────────────────────


def test_executor_reads_retry_from_stdout() -> None:
    reg = _mock_registry([_make_defn()])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PERMISSION_DENIED)
    stdout = json.dumps({"hookSpecificOutput": {"retry": True}})

    with patch("subprocess.run", return_value=_proc(0, stdout=stdout)):
        result = executor.run(ctx)

    assert result.retry is True


def test_executor_no_retry_signal_by_default() -> None:
    reg = _mock_registry([_make_defn()])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE)

    with patch("subprocess.run", return_value=_proc(0)):
        result = executor.run(ctx)

    assert result.retry is False


# ── registry lookup failure swallowed ────────────────────────────────────────


def test_executor_registry_failure_returns_empty_result() -> None:
    reg = MagicMock(spec=HookRegistry)
    reg.get_hooks_for_event.side_effect = RuntimeError("broken")
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE)

    result = executor.run(ctx)

    assert result.block is False
    assert result.executed is False


# ── multiple hooks: results merged ───────────────────────────────────────────


def test_executor_multiple_hooks_accumulates_messages() -> None:
    defn1 = _make_defn("/hook1.sh")
    defn2 = _make_defn("/hook2.sh")
    reg = _mock_registry([defn1, defn2])
    executor = HookExecutor(reg)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")

    call_count = 0

    def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _proc(2, stderr=f"msg-{call_count}")

    with patch("subprocess.run", side_effect=_side_effect):
        result = executor.run(ctx)

    assert result.block is True
    assert "msg-1" in result.message
    assert "msg-2" in result.message
