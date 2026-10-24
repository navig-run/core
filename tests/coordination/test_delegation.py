"""Delegation — parent→child execution through TaskQueue + durable result/evidence + resumption."""

from __future__ import annotations

import asyncio

import pytest

from navig.coordination import CoordinationError, delegation, list_inbox
from navig.store.coordination import CoordinationStore
from navig.tasks import TaskQueue, TaskWorker, WorkerConfig


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


@pytest.fixture(autouse=True)
def _reset_operations():
    yield
    for name in list(delegation._OPERATIONS):
        delegation._OPERATIONS.pop(name, None)


async def _drive(queue: TaskQueue, worker: TaskWorker, tasks) -> None:  # noqa: ANN001
    for task in tasks:
        await queue.add(task)
    await worker.start()
    try:
        for _ in range(200):
            if all(task.status.value == "completed" for task in tasks):
                return
            await asyncio.sleep(0.02)
    finally:
        await worker.stop(wait=True)
    raise AssertionError("tasks did not complete")


def test_delegate_validates_participants_and_self() -> None:
    with pytest.raises(CoordinationError):
        delegation.delegate("nope", "agent:worker", "verify")
    with pytest.raises(CoordinationError):
        delegation.delegate("agent:lead", "agent:lead", "verify")
    task = delegation.delegate("agent:lead", "agent:worker", "verify")
    assert task.handler == delegation.HANDLER
    assert task.params["parent"] == "agent:lead"


def test_parent_child_delegation_resumes_and_returns_evidence(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.store.coordination.get_coordination_store", lambda: store)

    delegation.register_operation(
        "verify",
        lambda ctx: {"content": {"ok": True, "checked": ctx.get("n")}, "evidence_refs": ["receipt:1"]},
    )
    delegation.register_operation(
        "collect",
        lambda _ctx: {"content": {"results": len(list_inbox("agent:lead"))}, "evidence_refs": []},
    )

    queue = TaskQueue()
    worker = TaskWorker(queue, WorkerConfig(max_concurrent=2))

    @worker.handler(delegation.HANDLER)
    async def _run(params: dict):  # noqa: ANN001
        return delegation.operation_result(params)

    child = delegation.delegate(
        parent="agent:lead", assignee="agent:worker", operation="verify", context={"n": 3},
    )
    parent = delegation.delegate(
        parent="operator", assignee="agent:lead", operation="collect",
        depends_on=[child.id], task_id="collect-1",
    )

    asyncio.run(_drive(queue, worker, [child, parent]))

    assert child.status.value == "completed"
    assert parent.status.value == "completed"

    inbox = list_inbox("agent:lead")
    results = [m for m in inbox if m["kind"] == "result"]
    assert len(results) == 1
    assert results[0]["evidence_refs"] == ["receipt:1"]
    assert results[0]["content"] == {"ok": True, "checked": 3}


def test_a_child_cannot_escalate_to_an_unknown_operation(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.store.coordination.get_coordination_store", lambda: store)
    with pytest.raises(CoordinationError):
        delegation.operation_result({"operation": "does-not-exist", "context": {}})
