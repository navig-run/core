"""Delegation — turn a scoped coordination ``delegate`` into existing task execution.

The coordination store is data; ``TaskQueue``/``TaskWorker`` is the executor. This module is
the seam: a delegated unit of work becomes a ``Task`` whose handler runs a named, registered
operation, and on completion posts a structured ``result`` message (with evidence) back to the
parent. Dependencies and "wait, then resume" are the TaskQueue's own semantics — a dependent
task stays ``waiting`` until its prerequisites complete.

Authorization: :func:`delegate` refuses a non-participant or a self-delegation. The operation
itself runs through the TaskWorker (which already applies timeouts/concurrency); nothing here
escalates a child's permissions beyond what the operation is registered to do.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from navig.coordination import CoordinationError, send, valid_participant
from navig.tasks.queue import Task, TaskPriority

__all__ = [
    "HANDLER",
    "delegate",
    "operation_result",
    "register_operation",
]

#: The one handler name every delegated task runs.
HANDLER = "coord:delegate"

_OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_operation(name: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Register a named, bounded operation a delegated task may run.

    An operation receives the task's ``context`` and returns a result mapping with optional
    ``content`` (object) and ``evidence_refs`` (list). It must not escalate permissions.
    """
    _OPERATIONS[str(name)] = fn


def operation_result(params: dict[str, Any]) -> dict[str, Any]:
    """The TaskQueue handler: run the named operation and post the evidence to the parent.

    Returns the operation result (which the worker stores on the task). The ``result``
    coordination message is durable and idempotent; if posting fails the operation has still
    run, so the worker's task result is the authoritative record.
    """
    name = str(params.get("operation") or "")
    op = _OPERATIONS.get(name)
    if op is None:
        raise CoordinationError(f"unknown operation {name!r}")

    result = op(params.get("context") or {})
    parent = str(params.get("parent") or "")
    assignee = str(params.get("assignee") or "")
    if valid_participant(parent) and valid_participant(assignee):
        send(
            assignee,
            parent,
            "result",
            result.get("content") if isinstance(result, dict) else {},
            task_id=params.get("task_id"),
            correlation_id=params.get("correlation_id"),
            evidence_refs=result.get("evidence_refs") if isinstance(result, dict) else [],
        )
    return result


def delegate(
    parent: str,
    assignee: str,
    operation: str,
    *,
    context: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
    correlation_id: str | None = None,
    task_id: str | None = None,
) -> Task:
    """Build a TaskQueue task for a scoped delegation (caller enqueues it).

    ``parent`` receives the child's ``result`` message; ``depends_on`` wires resumption so the
    parent's own dependent task only runs after this one completes.
    """
    if not valid_participant(parent):
        raise CoordinationError(f"invalid parent {parent!r}")
    if not valid_participant(assignee):
        raise CoordinationError(f"invalid assignee {assignee!r}")
    if parent == assignee:
        raise CoordinationError("a participant cannot delegate to itself")

    task_id = task_id or uuid.uuid4().hex[:16]
    return Task(
        name=f"delegate:{operation}",
        handler=HANDLER,
        priority=TaskPriority.NORMAL.value,
        dependencies=depends_on or [],
        params={
            "parent": parent,
            "assignee": assignee,
            "operation": operation,
            "context": context or {},
            "task_id": task_id,
            "correlation_id": correlation_id,
        },
        meta={"delegation": True, "parent": parent, "assignee": assignee},
    )
