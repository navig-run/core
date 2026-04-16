"""
tests/gateway/test_flow_runner.py
───────────────────────────────────
Tests for navig.gateway.flow_runner (Item 8).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.gateway.flow_runner import (
    DeliveryBackend,
    Destination,
    DestinationKind,
    FlowRunResult,
    FlowRunStatus,
    FlowRunner,
    _format_message,
    _make_run_id,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_runner(
    run_fn=None,
    backend=None,
    log_path: Path | None = None,
    max_log_entries: int = 100,
):
    if run_fn is None:
        async def run_fn():
            return "ok"

    return FlowRunner(
        name="test-flow",
        run_fn=run_fn,
        success_destination=Destination(DestinationKind.SESSION, "chat_1"),
        failure_destination=Destination(DestinationKind.CHANNEL, "alerts"),
        backend=backend,
        log_path=log_path,
        max_log_entries=max_log_entries,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Destination validation
# ──────────────────────────────────────────────────────────────────────────────


class TestDestination:
    def test_valid(self):
        d = Destination(DestinationKind.SESSION, "chat_42")
        assert d.kind == DestinationKind.SESSION
        assert d.id == "chat_42"

    def test_empty_id_raises(self):
        with pytest.raises(ValueError, match="id must not be empty"):
            Destination(DestinationKind.WEBHOOK, "")


# ──────────────────────────────────────────────────────────────────────────────
# FlowRunner construction
# ──────────────────────────────────────────────────────────────────────────────


class TestFlowRunnerConstruction:
    async def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            FlowRunner(
                name="",
                run_fn=AsyncMock(),
                success_destination=Destination(DestinationKind.SESSION, "x"),
                failure_destination=Destination(DestinationKind.CHANNEL, "y"),
            )

    def test_max_log_entries_zero_raises(self):
        with pytest.raises(ValueError, match="max_log_entries must be"):
            _make_runner(max_log_entries=0)


# ──────────────────────────────────────────────────────────────────────────────
# Successful run
# ──────────────────────────────────────────────────────────────────────────────


class TestFlowRunnerSuccess:
    async def test_returns_success_result(self):
        runner = _make_runner()
        result = await runner.run()
        assert result.status == FlowRunStatus.SUCCESS
        assert result.output == "ok"
        assert result.error == ""

    async def test_finished_at_set(self):
        runner = _make_runner()
        result = await runner.run()
        assert result.finished_at > result.started_at

    async def test_delivers_to_success_destination(self):
        delivered: list[tuple[Destination, str]] = []

        class FakeBackend:
            async def deliver(self, dest, msg):
                delivered.append((dest, msg))

        runner = _make_runner(backend=FakeBackend())
        await runner.run()
        assert len(delivered) == 1
        dest, msg = delivered[0]
        assert dest.id == "chat_1"
        assert "completed successfully" in msg


# ──────────────────────────────────────────────────────────────────────────────
# Failing run
# ──────────────────────────────────────────────────────────────────────────────


class TestFlowRunnerFailure:
    async def _boom(self):
        raise RuntimeError("boom")

    async def test_returns_failure_result(self):
        runner = _make_runner(run_fn=self._boom)
        result = await runner.run()
        assert result.status == FlowRunStatus.FAILURE
        assert "RuntimeError" in result.error
        assert "boom" in result.error

    async def test_delivers_to_failure_destination(self):
        delivered: list[Destination] = []

        class FakeBackend:
            async def deliver(self, dest, msg):
                delivered.append(dest)

        runner = _make_runner(run_fn=self._boom, backend=FakeBackend())
        await runner.run()
        assert delivered[0].id == "alerts"

    async def test_failure_message_contains_error(self):
        delivered_msgs: list[str] = []

        class FakeBackend:
            async def deliver(self, dest, msg):
                delivered_msgs.append(msg)

        runner = _make_runner(run_fn=self._boom, backend=FakeBackend())
        await runner.run()
        assert "FAILED" in delivered_msgs[0]
        assert "boom" in delivered_msgs[0]


# ──────────────────────────────────────────────────────────────────────────────
# Log file
# ──────────────────────────────────────────────────────────────────────────────


class TestFlowRunnerLog:
    async def test_log_file_created(self, tmp_path):
        log = tmp_path / "flow.jsonl"
        runner = _make_runner(log_path=log)
        await runner.run()
        assert log.exists()

    async def test_log_entry_written(self, tmp_path):
        log = tmp_path / "flow.jsonl"
        runner = _make_runner(log_path=log)
        await runner.run()
        entries = [json.loads(l) for l in log.read_text().splitlines() if l]
        assert len(entries) == 1
        assert entries[0]["status"] == "success"
        assert entries[0]["flow_name"] == "test-flow"

    async def test_log_pruned_at_max(self, tmp_path):
        log = tmp_path / "flow.jsonl"
        runner = _make_runner(log_path=log, max_log_entries=3)
        for _ in range(5):
            await runner.run()
        entries = [json.loads(l) for l in log.read_text().splitlines() if l]
        assert len(entries) == 3

    async def test_multiple_runs_append(self, tmp_path):
        log = tmp_path / "flow.jsonl"
        runner = _make_runner(log_path=log, max_log_entries=100)
        await runner.run()
        await runner.run()
        entries = [json.loads(l) for l in log.read_text().splitlines() if l]
        assert len(entries) == 2

    async def test_no_log_path_no_error(self):
        # Should complete without raising even without log_path
        runner = _make_runner()
        result = await runner.run()
        assert result.status == FlowRunStatus.SUCCESS


# ──────────────────────────────────────────────────────────────────────────────
# Backend delivery failure is swallowed
# ──────────────────────────────────────────────────────────────────────────────


class TestDeliveryFailureSilenced:
    async def test_backend_error_does_not_propagate(self):
        class BrokenBackend:
            async def deliver(self, dest, msg):
                raise OSError("network down")

        runner = _make_runner(backend=BrokenBackend())
        result = await runner.run()
        # run() should still succeed despite delivery failure
        assert result.status == FlowRunStatus.SUCCESS


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_make_run_id_contains_flow_name(self):
        rid = _make_run_id("my-flow")
        assert "my-flow" in rid

    def test_format_message_success(self):
        result = FlowRunResult(
            flow_name="f", run_id="id-1",
            status=FlowRunStatus.SUCCESS,
            started_at=0.0, finished_at=1.5,
        )
        msg = _format_message(result)
        assert "successfully" in msg
        assert "f" in msg

    def test_format_message_failure(self):
        result = FlowRunResult(
            flow_name="f", run_id="id-1",
            status=FlowRunStatus.FAILURE,
            error="ValueError: bad input",
            started_at=0.0, finished_at=2.0,
        )
        msg = _format_message(result)
        assert "FAILED" in msg
        assert "bad input" in msg
