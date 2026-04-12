"""
Tests for navig.agent.background_task — BackgroundTaskManager + agent tools.

FB-04 implementation tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.background_task import (
    MAX_CONCURRENT,
    BackgroundTask,
    BackgroundTaskManager,
    get_manager,
    reset_manager,
)

pytestmark = pytest.mark.integration

# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Provide a temp directory for task output files."""
    d = tmp_path / "bg_tasks"
    d.mkdir()
    return d


@pytest.fixture
async def manager(tmp_output_dir):
    """Fresh BackgroundTaskManager with temp output directory."""
    mgr = BackgroundTaskManager(output_dir=tmp_output_dir)
    yield mgr
    # Shutdown: kill running tasks and wait for monitors to close file handles
    await mgr.shutdown()


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton between tests."""
    reset_manager()
    yield
    reset_manager()


# ─────────────────────────────────────────────────────────────
# BackgroundTask dataclass
# ─────────────────────────────────────────────────────────────


class TestBackgroundTask:
    """Tests for the BackgroundTask dataclass."""

    def test_is_running_when_no_exit_code(self):
        task = BackgroundTask(task_id=1, label="test", command="echo")
        assert task.is_running is True

    def test_not_running_after_exit_code_set(self):
        task = BackgroundTask(task_id=1, label="test", command="echo", exit_code=0)
        assert task.is_running is False

    def test_not_running_with_nonzero_exit(self):
        task = BackgroundTask(task_id=1, label="test", command="echo", exit_code=1)
        assert task.is_running is False

    def test_duration_while_running(self):
        task = BackgroundTask(task_id=1, label="test", command="echo", started_at=time.time() - 5)
        assert 4.5 <= task.duration <= 10.0

    def test_duration_when_completed(self):
        now = time.time()
        task = BackgroundTask(
            task_id=1,
            label="test",
            command="echo",
            started_at=now - 10,
            completed_at=now - 5,
        )
        assert 4.5 <= task.duration <= 5.5

    def test_default_values(self):
        task = BackgroundTask(task_id=42, label="build", command="make all")
        assert task.task_id == 42
        assert task.label == "build"
        assert task.command == "make all"
        assert task.pid is None
        assert task.exit_code is None
        assert task.completed_at is None
        assert task.output_file == ""
        assert isinstance(task.started_at, float)

    def test_output_file_field(self):
        task = BackgroundTask(task_id=1, label="x", command="x", output_file="/tmp/out.log")
        assert task.output_file == "/tmp/out.log"


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — start()
# ─────────────────────────────────────────────────────────────


class TestManagerStart:
    """Tests for BackgroundTaskManager.start()."""

    async def test_start_returns_task(self, manager):
        """start() returns a BackgroundTask immediately."""
        cmd = "echo hello" if os.name != "nt" else "echo hello"
        task = await manager.start(cmd, label="greet")
        assert isinstance(task, BackgroundTask)
        assert task.task_id == 1
        assert task.label == "greet"
        assert task.command == cmd
        assert task.pid is not None
        assert task.is_running or task.exit_code is not None  # may finish fast

    async def test_start_increments_id(self, manager):
        t1 = await manager.start("echo 1", label="one")
        t2 = await manager.start("echo 2", label="two")
        assert t1.task_id == 1
        assert t2.task_id == 2

    async def test_start_creates_output_file(self, manager, tmp_output_dir):
        task = await manager.start("echo hi")
        assert task.output_file != ""
        # File should exist (even if still being written)
        assert Path(task.output_file).exists()

    async def test_start_empty_command_raises(self, manager):
        with pytest.raises(ValueError, match="must not be empty"):
            await manager.start("")

    async def test_start_blank_command_raises(self, manager):
        with pytest.raises(ValueError, match="must not be empty"):
            await manager.start("   ")

    async def test_start_default_label_from_command(self, manager):
        task = await manager.start(
            "echo some-very-long-command-that-exceeds-forty-characters-limit-for-the-label"
        )
        assert len(task.label) <= 40

    async def test_start_max_concurrent_enforced(self, manager):
        """Cannot exceed MAX_CONCURRENT running tasks."""
        # We'll mock _running_tasks to simulate many running
        # Start one real task, then monkey-patch _running_tasks
        task = await manager.start("echo 1")

        # Simulate MAX_CONCURRENT running tasks
        fake_tasks = [
            BackgroundTask(task_id=i, label=f"t{i}", command=f"sleep {i}")
            for i in range(100, 100 + MAX_CONCURRENT)
        ]
        with patch.object(
            type(manager),
            "_running_tasks",
            new_callable=lambda: property(lambda self: fake_tasks),
        ):
            with pytest.raises(RuntimeError, match="Max.*concurrent"):
                await manager.start("echo overflow")


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — _monitor() / completion
# ─────────────────────────────────────────────────────────────


class TestManagerCompletion:
    """Tests for task completion monitoring."""

    async def test_task_completes_with_exit_code(self, manager):
        task = await manager.start("echo done")
        # Wait for it to complete
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        assert not task.is_running
        assert task.exit_code == 0
        assert task.completed_at is not None

    async def test_failing_task_captures_nonzero_exit(self, manager):
        # Use a command that fails
        cmd = "exit 1" if os.name != "nt" else "cmd /c exit 1"
        task = await manager.start(cmd, label="fail")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        assert not task.is_running
        assert task.exit_code != 0


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — status()
# ─────────────────────────────────────────────────────────────


class TestManagerStatus:
    """Tests for BackgroundTaskManager.status()."""

    async def test_status_running_task(self, manager):
        # Use a long-running command
        cmd = "ping -n 10 127.0.0.1 >nul" if os.name == "nt" else "sleep 10"
        task = await manager.start(cmd, label="long")
        info = manager.status(task.task_id)
        assert info["task_id"] == task.task_id
        assert info["label"] == "long"
        assert info["status"] == "running"
        assert info["pid"] == task.pid
        assert "duration" in info
        # Cleanup
        await manager.kill(task.task_id)

    async def test_status_completed_task(self, manager):
        task = await manager.start("echo ok")
        # Wait for completion
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        info = manager.status(task.task_id)
        assert info["status"] == "completed"
        assert info["exit_code"] == 0

    def test_status_nonexistent_task(self, manager):
        info = manager.status(999)
        assert "error" in info

    async def test_status_includes_command(self, manager):
        task = await manager.start("echo hello")
        info = manager.status(task.task_id)
        assert info["command"] == "echo hello"


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — get_output()
# ─────────────────────────────────────────────────────────────


class TestManagerGetOutput:
    """Tests for BackgroundTaskManager.get_output()."""

    async def test_output_captured_to_file(self, manager):
        task = await manager.start("echo hello_world")
        # Wait for completion
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        output = manager.get_output(task.task_id)
        assert "hello_world" in output

    async def test_output_tail_limits_lines(self, manager):
        # Generate multi-line output
        if os.name == "nt":
            cmd = 'cmd /c "for /L %i in (1,1,100) do @echo line%i"'
        else:
            cmd = "for i in $(seq 1 100); do echo line$i; done"

        task = await manager.start(cmd, label="multi")
        for _ in range(200):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        output = manager.get_output(task.task_id, tail=5)
        lines = output.strip().splitlines()
        assert len(lines) <= 5

    def test_output_nonexistent_task(self, manager):
        result = manager.get_output(999)
        assert "No task with id" in result

    async def test_output_default_tail_50(self, manager):
        # The default tail parameter should work
        task = await manager.start("echo test")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        # Should not raise
        output = manager.get_output(task.task_id)
        assert isinstance(output, str)


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — kill()
# ─────────────────────────────────────────────────────────────


class TestManagerKill:
    """Tests for BackgroundTaskManager.kill()."""

    async def test_kill_running_task(self, manager):
        cmd = "ping -n 60 127.0.0.1 >nul" if os.name == "nt" else "sleep 60"
        task = await manager.start(cmd, label="killme")
        assert task.is_running
        killed = await manager.kill(task.task_id)
        assert killed is True
        # Wait a moment for monitor to pick up
        await asyncio.sleep(0.5)

    async def test_kill_nonexistent_task(self, manager):
        killed = await manager.kill(999)
        assert killed is False

    async def test_kill_completed_task(self, manager):
        task = await manager.start("echo fast")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        killed = await manager.kill(task.task_id)
        assert killed is False


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — list_tasks()
# ─────────────────────────────────────────────────────────────


class TestManagerListTasks:
    """Tests for BackgroundTaskManager.list_tasks()."""

    def test_list_empty(self, manager):
        assert manager.list_tasks() == []

    async def test_list_with_tasks(self, manager):
        await manager.start("echo a", label="taskA")
        await manager.start("echo b", label="taskB")
        tasks = manager.list_tasks()
        assert len(tasks) == 2
        labels = {t["label"] for t in tasks}
        assert labels == {"taskA", "taskB"}

    async def test_list_sorted_by_id(self, manager):
        await manager.start("echo 1")
        await manager.start("echo 2")
        await manager.start("echo 3")
        tasks = manager.list_tasks()
        ids = [t["task_id"] for t in tasks]
        assert ids == sorted(ids)


# ─────────────────────────────────────────────────────────────
# BackgroundTaskManager — cleanup()
# ─────────────────────────────────────────────────────────────


class TestManagerCleanup:
    """Tests for BackgroundTaskManager.cleanup()."""

    async def test_cleanup_removes_old_completed_tasks(self, manager, tmp_output_dir):
        task = await manager.start("echo old")
        # Wait for completion
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        # Fake the completion time to be old
        task.completed_at = time.time() - 7200  # 2 hours ago
        removed = manager.cleanup(max_age=3600)
        assert removed == 1
        assert task.task_id not in manager._tasks

    async def test_cleanup_keeps_recent_completed(self, manager):
        task = await manager.start("echo new")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        removed = manager.cleanup(max_age=3600)
        assert removed == 0
        assert task.task_id in manager._tasks

    async def test_cleanup_keeps_running_tasks(self, manager):
        cmd = "ping -n 30 127.0.0.1 >nul" if os.name == "nt" else "sleep 30"
        task = await manager.start(cmd)
        removed = manager.cleanup(max_age=0)  # Even with age=0
        assert removed == 0
        await manager.kill(task.task_id)

    async def test_cleanup_deletes_output_file(self, manager, tmp_output_dir):
        task = await manager.start("echo cleanup_me")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        output_path = Path(task.output_file)
        assert output_path.exists()
        task.completed_at = time.time() - 7200
        manager.cleanup(max_age=3600)
        assert not output_path.exists()

    def test_cleanup_empty_manager(self, manager):
        removed = manager.cleanup()
        assert removed == 0


# ─────────────────────────────────────────────────────────────
# Module singleton
# ─────────────────────────────────────────────────────────────


class TestSingleton:
    """Tests for get_manager / reset_manager."""

    def test_get_manager_returns_same_instance(self):
        m1 = get_manager()
        m2 = get_manager()
        assert m1 is m2

    def test_reset_manager_clears_singleton(self):
        m1 = get_manager()
        reset_manager()
        m2 = get_manager()
        assert m1 is not m2

    def test_get_manager_with_custom_dir(self, tmp_path):
        reset_manager()
        custom_dir = tmp_path / "custom_bg"
        m = get_manager(output_dir=custom_dir)
        assert m._output_dir == custom_dir


# ─────────────────────────────────────────────────────────────
# Agent tools — BackgroundTaskStartTool
# ─────────────────────────────────────────────────────────────


class TestStartTool:
    """Tests for BackgroundTaskStartTool."""

    async def test_start_tool_success(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskStartTool

        tool = BackgroundTaskStartTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        try:
            with patch(
                "navig.agent.tools.background_task_tools._get_manager",
                return_value=mgr,
            ):
                result = await tool.run({"command": "echo hi", "label": "test"})
            assert result.success is True
            assert "Started background task #1" in result.output
            assert "test" in result.output
        finally:
            await mgr.shutdown()

    async def test_start_tool_empty_command(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskStartTool

        tool = BackgroundTaskStartTool()
        result = await tool.run({"command": ""})
        assert result.success is False
        assert "required" in result.error.lower()

    async def test_start_tool_missing_command(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskStartTool

        tool = BackgroundTaskStartTool()
        result = await tool.run({})
        assert result.success is False

    async def test_start_tool_max_concurrent_error(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskStartTool

        tool = BackgroundTaskStartTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        # Make manager.start raise RuntimeError
        async def mock_start(*a, **kw):
            raise RuntimeError("Max 10 concurrent")

        mgr.start = mock_start

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"command": "echo x"})
        assert result.success is False
        assert "concurrent" in result.error.lower()

    async def test_start_tool_name(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskStartTool

        tool = BackgroundTaskStartTool()
        assert tool.name == "background_task_start"


# ─────────────────────────────────────────────────────────────
# Agent tools — BackgroundTaskStatusTool
# ─────────────────────────────────────────────────────────────


class TestStatusTool:
    """Tests for BackgroundTaskStatusTool."""

    async def test_status_tool_single_task(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskStatusTool

        tool = BackgroundTaskStatusTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        # Manually add a completed task
        task = BackgroundTask(
            task_id=1,
            label="test",
            command="echo",
            exit_code=0,
            completed_at=time.time(),
            output_file=str(tmp_output_dir / "task_1.log"),
        )
        mgr._tasks[1] = task
        # Create the output file
        (tmp_output_dir / "task_1.log").write_text("hello\n", encoding="utf-8")

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"task_id": 1})
        assert result.success is True
        data = json.loads(result.output)
        assert data["task_id"] == 1
        assert data["status"] == "completed"

    async def test_status_tool_nonexistent(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskStatusTool

        tool = BackgroundTaskStatusTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"task_id": 999})
        assert result.success is False

    async def test_status_tool_list_all(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskStatusTool

        tool = BackgroundTaskStatusTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        # Add two completed tasks
        for i in (1, 2):
            task = BackgroundTask(
                task_id=i,
                label=f"t{i}",
                command=f"echo {i}",
                exit_code=0,
                completed_at=time.time(),
                output_file=str(tmp_output_dir / f"task_{i}.log"),
            )
            mgr._tasks[i] = task
            (tmp_output_dir / f"task_{i}.log").write_text(f"out{i}\n", encoding="utf-8")

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({})  # No task_id → list all
        assert result.success is True
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2

    async def test_status_tool_no_tasks(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskStatusTool

        tool = BackgroundTaskStatusTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({})
        assert result.success is True
        assert "No background tasks" in result.output


# ─────────────────────────────────────────────────────────────
# Agent tools — BackgroundTaskOutputTool
# ─────────────────────────────────────────────────────────────


class TestOutputTool:
    """Tests for BackgroundTaskOutputTool."""

    async def test_output_tool_returns_content(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskOutputTool

        tool = BackgroundTaskOutputTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        # Manually add a task with output
        log_file = tmp_output_dir / "task_1.log"
        log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
        task = BackgroundTask(
            task_id=1,
            label="test",
            command="echo",
            exit_code=0,
            completed_at=time.time(),
            output_file=str(log_file),
        )
        mgr._tasks[1] = task

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"task_id": 1, "tail": 2})
        assert result.success is True
        lines = result.output.strip().splitlines()
        assert len(lines) == 2

    async def test_output_tool_missing_task_id(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskOutputTool

        tool = BackgroundTaskOutputTool()
        result = await tool.run({})
        assert result.success is False
        assert "required" in result.error.lower()

    async def test_output_tool_invalid_task_id(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskOutputTool

        tool = BackgroundTaskOutputTool()
        result = await tool.run({"task_id": "abc"})
        assert result.success is False

    async def test_output_tool_nonexistent_task(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskOutputTool

        tool = BackgroundTaskOutputTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"task_id": 999})
        assert result.success is False

    async def test_output_tool_name(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskOutputTool

        tool = BackgroundTaskOutputTool()
        assert tool.name == "background_task_output"


# ─────────────────────────────────────────────────────────────
# Agent tools — BackgroundTaskKillTool
# ─────────────────────────────────────────────────────────────


class TestKillTool:
    """Tests for BackgroundTaskKillTool."""

    async def test_kill_tool_success(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskKillTool

        tool = BackgroundTaskKillTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        # Mock the kill to return True
        async def mock_kill(tid):
            return True

        mgr.kill = mock_kill

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"task_id": 1})
        assert result.success is True
        assert "killed" in result.output.lower()

    async def test_kill_tool_not_found(self, tmp_output_dir):
        from navig.agent.tools.background_task_tools import BackgroundTaskKillTool

        tool = BackgroundTaskKillTool()
        mgr = BackgroundTaskManager(output_dir=tmp_output_dir)

        with patch(
            "navig.agent.tools.background_task_tools._get_manager",
            return_value=mgr,
        ):
            result = await tool.run({"task_id": 999})
        assert result.success is False

    async def test_kill_tool_missing_task_id(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskKillTool

        tool = BackgroundTaskKillTool()
        result = await tool.run({})
        assert result.success is False
        assert "required" in result.error.lower()

    async def test_kill_tool_invalid_task_id(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskKillTool

        tool = BackgroundTaskKillTool()
        result = await tool.run({"task_id": "xyz"})
        assert result.success is False

    async def test_kill_tool_name(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskKillTool

        tool = BackgroundTaskKillTool()
        assert tool.name == "background_task_kill"


# ─────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────


class TestToolRegistration:
    """Tests for tool registration wiring."""

    def test_register_background_task_tools(self):
        """Background task tools register without error."""
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_background_task_tools

        register_background_task_tools()

        # Verify all 4 tools registered
        tool_names = {
            "background_task_start",
            "background_task_status",
            "background_task_output",
            "background_task_kill",
        }
        for name in tool_names:
            assert _AGENT_REGISTRY.get_entry(name) is not None, f"{name} not registered"

    def test_tools_have_correct_metadata(self):
        from navig.agent.tools.background_task_tools import (
            BackgroundTaskKillTool,
            BackgroundTaskOutputTool,
            BackgroundTaskStartTool,
            BackgroundTaskStatusTool,
        )

        for cls in (
            BackgroundTaskStartTool,
            BackgroundTaskStatusTool,
            BackgroundTaskOutputTool,
            BackgroundTaskKillTool,
        ):
            tool = cls()
            meta = tool.get_meta()
            assert "id" in meta
            assert "name" in meta
            assert "description" in meta
            assert "parameters" in meta
            assert meta["description"]  # Not empty
            assert isinstance(meta["parameters"], list)

    def test_start_tool_has_required_command_param(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskStartTool

        tool = BackgroundTaskStartTool()
        params = tool.parameters
        command_param = next((p for p in params if p["name"] == "command"), None)
        assert command_param is not None
        assert command_param["required"] is True

    def test_output_tool_has_optional_tail_param(self):
        from navig.agent.tools.background_task_tools import BackgroundTaskOutputTool

        tool = BackgroundTaskOutputTool()
        params = tool.parameters
        tail_param = next((p for p in params if p["name"] == "tail"), None)
        assert tail_param is not None
        assert tail_param["required"] is False


# ─────────────────────────────────────────────────────────────
# Edge cases / robustness
# ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and robustness tests."""

    async def test_output_file_with_encoding_issues(self, manager, tmp_output_dir):
        """Output file with non-UTF8 bytes is handled gracefully."""
        # Create a task with manual output file
        task = BackgroundTask(
            task_id=99,
            label="enc",
            command="echo",
            exit_code=0,
            completed_at=time.time(),
            output_file=str(tmp_output_dir / "task_99.log"),
        )
        manager._tasks[99] = task
        # Write some bytes with invalid UTF-8
        with open(task.output_file, "wb") as f:
            f.write(b"valid line\n\xff\xfe bad bytes\n")
        output = manager.get_output(99)
        assert "valid line" in output

    async def test_status_includes_output_line_count(self, manager, tmp_output_dir):
        task = BackgroundTask(
            task_id=1,
            label="x",
            command="x",
            exit_code=0,
            completed_at=time.time(),
            output_file=str(tmp_output_dir / "task_1.log"),
        )
        manager._tasks[1] = task
        (tmp_output_dir / "task_1.log").write_text("a\nb\nc\n", encoding="utf-8")
        info = manager.status(1)
        assert info["output_lines"] == 3

    def test_count_output_lines_missing_file(self, manager):
        task = BackgroundTask(
            task_id=1,
            label="x",
            command="x",
            output_file="/nonexistent/path/file.log",
        )
        assert manager._count_output_lines(task) == 0

    async def test_get_output_missing_file(self, manager):
        task = BackgroundTask(
            task_id=1,
            label="x",
            command="x",
            output_file="/nonexistent/path/file.log",
        )
        manager._tasks[1] = task
        output = manager.get_output(1)
        assert "(no output yet)" in output

    async def test_get_output_empty_file(self, manager, tmp_output_dir):
        log_file = tmp_output_dir / "task_1.log"
        log_file.write_text("", encoding="utf-8")
        task = BackgroundTask(
            task_id=1,
            label="x",
            command="x",
            exit_code=0,
            completed_at=time.time(),
            output_file=str(log_file),
        )
        manager._tasks[1] = task
        output = manager.get_output(1)
        assert "(empty output)" in output

    async def test_kill_already_exited_process(self, manager):
        """Kill on an already-completed task returns False."""
        task = await manager.start("echo quick")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not task.is_running:
                break
        killed = await manager.kill(task.task_id)
        assert killed is False

    async def test_concurrent_start_and_status(self, manager):
        """Starting multiple tasks and checking status works."""
        tasks = []
        for i in range(3):
            t = await manager.start(f"echo task{i}", label=f"task{i}")
            tasks.append(t)

        all_status = manager.list_tasks()
        assert len(all_status) == 3

    def test_manager_custom_output_dir(self, tmp_path):
        custom = tmp_path / "my_tasks"
        mgr = BackgroundTaskManager(output_dir=custom)
        assert mgr._output_dir == custom

    async def test_output_dir_created_on_start(self, tmp_path):
        new_dir = tmp_path / "new_bg_tasks"
        assert not new_dir.exists()
        mgr = BackgroundTaskManager(output_dir=new_dir)
        task = await mgr.start("echo hello")
        assert new_dir.exists()
        # Shutdown: wait for process + close file handles
        await mgr.shutdown()

    async def test_max_concurrent_constant(self):
        """MAX_CONCURRENT is 10."""
        assert MAX_CONCURRENT == 10
