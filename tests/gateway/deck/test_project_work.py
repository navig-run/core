"""GET /api/deck/projects/{id}/work — the read-only work rollup.

The rollup reads the existing stores by the existing ``space`` reference; it must
not create a store, write anything, or duplicate a row from the board/scheduler.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aiohttp")

from navig.gateway.deck.routes import projects  # noqa: E402
from navig.store.board import BoardStore  # noqa: E402


class _Req:
    def __init__(self, project_id: str = "gaze") -> None:
        self.match_info = {"id": project_id}
        self.app: dict = {}


class _Job:
    def __init__(self, space: str | None, job_id: str = "job-1") -> None:
        self.space = space
        self._id = job_id

    def to_dict(self) -> dict:
        return {"id": self._id, "name": "GAZE status", "space": self.space}


class _Cron:
    def __init__(self, jobs: list[_Job]) -> None:
        self._jobs = jobs

    def list_jobs(self) -> list[_Job]:
        return self._jobs


def _data(resp) -> dict:  # noqa: ANN001
    return json.loads(resp.body)["data"]


@pytest.fixture
def store(tmp_path):
    return BoardStore(tmp_path / "board.db")


async def test_work_rollup_returns_only_the_projects_rows(monkeypatch, store, tmp_path) -> None:
    gaze_goal = store.create_goal("GAZE release", space="gaze")
    gaze_card = store.create_card("prepare production", space="gaze")
    gaze_todo = store.create_todo("reply to Rakib", space="gaze")
    store.create_card("other project", space="elsewhere")
    store.create_todo("elsewhere todo", space="elsewhere")

    monkeypatch.setattr(projects, "_root", lambda pid: (pid, tmp_path))
    monkeypatch.setattr("navig.store.board.get_board_store", lambda: store)
    monkeypatch.setattr(
        projects, "_cron_service",
        lambda request: _Cron([_Job("gaze"), _Job("elsewhere", "job-2")]),
    )

    resp = await projects.handle_project_work(_Req("gaze"))
    assert resp.status == 200
    data = _data(resp)

    assert data["project"] == {"id": "gaze", "root": str(tmp_path)}
    assert data["worktrees"] == []  # a temp folder is not a git repository
    assert [g["id"] for g in data["goals"]] == [gaze_goal["id"]]
    assert [c["id"] for c in data["cards"]] == [gaze_card["id"]]
    assert [t["id"] for t in data["todos"]] == [gaze_todo["id"]]
    assert [j["id"] for j in data["crons"]] == ["job-1"]
    assert data["counts"] == {
        "goals": 1, "cards": 1, "open_cards": 1,
        "todos": 1, "open_todos": 1, "crons": 1,
    }


async def test_work_rollup_survives_a_broken_cron_reader(monkeypatch, store, tmp_path) -> None:
    store.create_card("prepare production", space="gaze")

    monkeypatch.setattr(projects, "_root", lambda pid: (pid, tmp_path))
    monkeypatch.setattr("navig.store.board.get_board_store", lambda: store)

    def _boom(request):
        raise RuntimeError("scheduler store is down")

    monkeypatch.setattr(projects, "_cron_service", _boom)

    resp = await projects.handle_project_work(_Req("gaze"))
    assert resp.status == 200
    data = _data(resp)
    assert data["counts"]["cards"] == 1
    assert data["crons"] == []


async def test_work_rollup_projects_execution_evidence_and_next_action(monkeypatch, store, tmp_path) -> None:
    card = store.create_card("prepare production", space="gaze")
    todo = store.create_todo("verify tester count", space="gaze", due_at="2026-10-22T09:00:00Z")

    class _State:
        def __init__(self, value: str) -> None:
            self.value = value

    class _Mission:
        mission_id = "m1"
        title = "Run task: prepare production"
        status = _State("succeeded")
        completed_at = "2026-10-21T10:00:00Z"
        metadata = {"card_id": card["id"]}

    class _Receipt:
        receipt_id = "r1"
        mission_id = "m1"
        title = "Run task: prepare production"
        outcome = _State("succeeded")
        completed_at = "2026-10-21T10:00:00Z"
        error = None

    class _Runtime:
        def list_missions(self, limit=100):  # noqa: ANN001, ANN201
            return [_Mission()]

        def list_receipts(self, limit=100):  # noqa: ANN001, ANN201
            return [_Receipt()]

        def get_receipt(self, receipt_id: str):  # noqa: ANN201
            return _Receipt() if receipt_id == "r1" else None

    monkeypatch.setattr(projects, "_root", lambda pid: (pid, tmp_path))
    monkeypatch.setattr("navig.store.board.get_board_store", lambda: store)
    monkeypatch.setattr(projects, "_cron_service", lambda request: _Cron([]))
    monkeypatch.setattr(projects, "_runtime_store", lambda: _Runtime())

    resp = await projects.handle_project_work(_Req("gaze"))
    data = _data(resp)

    evidence = data["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "card"
    assert evidence[0]["id"] == card["id"]
    assert evidence[0]["mission_id"] == "m1"
    assert evidence[0]["receipt_id"] == "r1"
    assert evidence[0]["outcome"] == "succeeded"
    assert data["next_action"] == {
        "at": "2026-10-22T09:00:00Z",
        "kind": "todo",
        "id": todo["id"],
        "title": "verify tester count",
    }
