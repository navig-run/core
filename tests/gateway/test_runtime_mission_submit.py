"""`POST /runtime/missions` must go through MissionExecutor when it is live.

A bare QUEUED row is what the recovery sweep later executed at a globally-resolved
autonomy level; the API is not a second mission-creation authority.
"""

from __future__ import annotations

import pytest

aiohttp = pytest.importorskip("aiohttp")

from navig.gateway.routes import runtime  # noqa: E402


class _Mission:
    mission_id = "mission-1"

    def to_dict(self) -> dict:
        return {"mission_id": self.mission_id, "title": "t", "capability": "llm"}


class _Executor:
    def __init__(self) -> None:
        self.submitted: list[_Mission] = []

    async def submit(self, mission: _Mission) -> _Mission:
        self.submitted.append(mission)
        return mission


class _Gateway:
    def __init__(self) -> None:
        self.mission_executor = _Executor()

    async def policy_check(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        return None


class _Request(dict):
    def __init__(self) -> None:
        super().__init__(deck_auth_local=True)
        self.headers = {"X-Actor": "test"}
        self.remote = "test"

    async def json(self) -> dict:
        return {"mission_id": "mission-1", "title": "t", "capability": "llm"}


async def test_runtime_mission_creation_submits_through_the_executor(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "require_bearer_auth", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "navig.contracts.mission.Mission.from_dict",
        classmethod(lambda _cls, _data: _Mission()),
    )
    monkeypatch.setattr("navig.contracts.store.get_runtime_store", lambda: object())

    gateway = _Gateway()
    resp = await runtime._create_mission(gateway)(_Request())

    assert resp.status == 201
    assert [m.mission_id for m in gateway.mission_executor.submitted] == ["mission-1"]
