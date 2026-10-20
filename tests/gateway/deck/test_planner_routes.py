"""/api/deck/planner/* — operator-only, one envelope, honest status codes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("aiohttp")

from navig.gateway.deck.routes import planner  # noqa: E402
from navig.pim import reminders as pim_reminders  # noqa: E402
from navig.pim import service  # noqa: E402
from navig.pim.clock import LOCAL  # noqa: E402
from navig.store.board import BoardStore  # noqa: E402

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)


class _Req(dict):
    """Enough of aiohttp.web.Request: a dict (request["deck_user_id"]) with a body."""

    def __init__(self, body: Any = None, *, query=None, match_info=None, local=True, user_id=1):
        super().__init__(deck_auth_local=local, deck_user_id=user_id)
        self._body = body
        self.query = query or {}
        self.match_info = match_info or {}
        self.path = "/api/deck/planner/test"
        self.app: dict[str, Any] = {}

    async def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _body(resp) -> dict[str, Any]:  # noqa: ANN001
    return json.loads(resp.body)


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(service, "_store", lambda: store)
    monkeypatch.setattr(service, "_SINK", None)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(pim_reminders, "operator_delivery", lambda: None)  # schedule nothing
    monkeypatch.setattr("navig.telegram.biz_reminders.list_all", lambda: [])
    return store


async def test_a_telegram_user_who_is_not_the_operator_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.gateway.deck.auth._deck_config",
        {"allowed_users": [42], "allowed_users_configured": True},
    )
    stranger = await planner.handle_planner_today(_Req(local=False, user_id=99))
    assert stranger.status == 403
    operator = await planner.handle_planner_today(_Req(local=False, user_id=42))
    assert operator.status == 200


async def test_with_no_allowlist_only_the_operator_chat_passes(monkeypatch) -> None:
    monkeypatch.setattr("navig.gateway.deck.auth._deck_config", {"allowed_users": []})
    monkeypatch.setattr(
        "navig.messaging.notify_operator.resolve_operator_chat_id", lambda: "42"
    )
    assert (await planner.handle_planner_today(_Req(local=False, user_id=99))).status == 403
    assert (await planner.handle_planner_today(_Req(local=False, user_id=42))).status == 200


async def test_task_round_trip_through_the_routes(env: BoardStore) -> None:
    created = await planner.handle_planner_task_create(_Req({"text": "Invoice ACME #work friday 9am"}))
    assert created.status == 201
    item = _body(created)["data"]
    assert (item["title"], item["category"]) == ("Invoice ACME", "work")
    assert item["day"] is not None and item["done"] is False

    listed = _body(await planner.handle_planner_tasks(_Req(query={"list": "me"})))["data"]
    assert [t["id"] for t in listed] == [item["id"]]

    done = await planner.handle_planner_task_done(_Req(match_info={"id": item["id"]}))
    assert _body(done)["data"]["done"] is True

    gone = await planner.handle_planner_item_delete(_Req(match_info={"id": item["id"]}))
    assert gone.status == 200
    missing = await planner.handle_planner_task_done(_Req(match_info={"id": item["id"]}))
    assert missing.status == 404
    assert _body(missing)["ok"] is False


async def test_validation_errors_are_400_not_500() -> None:
    assert (await planner.handle_planner_task_create(_Req({}))).status == 400
    assert (await planner.handle_planner_task_create(_Req(ValueError("bad json")))).status == 400
    bad = await planner.handle_planner_agenda_create(_Req({"title": "x"}))
    assert bad.status == 400 and "start" in _body(bad)["error"]
    snooze = await planner.handle_planner_reminder_snooze(
        _Req({"seconds": 5}, match_info={"source": "runtime", "id": "1"})
    )
    assert snooze.status in (400, 404)


async def test_a_used_category_cannot_be_deleted(env: BoardStore) -> None:
    await planner.handle_planner_task_create(_Req({"text": "Logo #freelance"}))
    resp = await planner.handle_planner_category_delete(_Req(match_info={"name": "freelance"}))
    assert resp.status == 409
    renamed = await planner.handle_planner_category_update(
        _Req({"name": "clients", "emoji": "🤝"}, match_info={"name": "freelance"})
    )
    assert _body(renamed)["data"]["name"] == "clients"


async def test_agenda_create_and_accept() -> None:
    start = (NOW + timedelta(days=3)).replace(tzinfo=None).isoformat(timespec="minutes")
    resp = await planner.handle_planner_agenda_create(
        _Req({"title": "Visio Sarah", "start_at": start, "location": "Zoom"})
    )
    assert resp.status == 201
    appt = _body(resp)["data"]
    assert appt["kind"] == "appointment" and appt["location"] == "Zoom"
    listed = _body(await planner.handle_planner_agenda(_Req(query={"days": "30"})))["data"]
    assert [a["id"] for a in listed] == [appt["id"]]
    tasks = _body(await planner.handle_planner_tasks(_Req(query={"list": "*"})))["data"]
    assert tasks == []  # an appointment is never in a to-do list
