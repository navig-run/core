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


async def test_an_item_is_readable_by_id_outside_the_agenda_window() -> None:
    """A reminder snoozed past its appointment still opens it: the agenda only lists
    appointments from two hours ago, so the lookup needs a by-id read."""
    start = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="minutes")
    resp = await planner.handle_planner_agenda_create(_Req({"title": "Dentist", "start_at": start}))
    assert resp.status == 201
    appt = _body(resp)["data"]
    listed = _body(await planner.handle_planner_agenda(_Req(query={"days": "366"})))["data"]
    assert listed == []  # out of the agenda window

    got = await planner.handle_planner_item_get(_Req(match_info={"id": appt["id"]}))
    assert got.status == 200
    assert _body(got)["data"]["id"] == appt["id"]
    assert _body(got)["data"]["title"] == "Dentist"

    missing = await planner.handle_planner_item_get(_Req(match_info={"id": "nope"}))
    assert missing.status == 404


async def test_item_get_is_operator_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.gateway.deck.auth._deck_config",
        {"allowed_users": [42], "allowed_users_configured": True},
    )
    resp = await planner.handle_planner_item_get(
        _Req(match_info={"id": "x"}, local=False, user_id=99)
    )
    assert resp.status == 403


async def test_followups_are_operator_only_and_wire_the_followup_service(monkeypatch) -> None:
    from navig.pim import followups

    monkeypatch.setattr(
        "navig.gateway.deck.auth._deck_config",
        {"allowed_users": [42], "allowed_users_configured": True},
    )
    refused = await planner.handle_planner_followups(_Req(local=False, user_id=99))
    assert refused.status == 403

    monkeypatch.setattr(
        followups, "candidates",
        lambda: [{"id": "t1", "verification": {"state": "unverified"}}],
    )
    listed = _body(await planner.handle_planner_followups(_Req()))["data"]
    assert listed["count"] == 1
    assert listed["followups"][0]["id"] == "t1"

    monkeypatch.setattr(followups, "snooze", lambda *_a, **_k: True)
    snoozed = _body(await planner.handle_planner_followup_snooze(
        _Req({"hours": 4}, match_info={"id": "t1"})
    ))["data"]
    assert snoozed == {"snoozed": True, "hours": 4.0}

    async def fake_suggest(**_kwargs):  # noqa: ANN003
        return [{"todo_id": "t1", "status": "delivered", "channels": ["deck"]}]

    monkeypatch.setattr(followups, "suggest", fake_suggest)
    suggested = _body(await planner.handle_planner_followup_suggest(
        _Req(match_info={"id": "t1"})
    ))["data"]
    assert suggested["results"][0]["status"] == "delivered"


async def test_archived_lists_stay_reachable_for_restore() -> None:
    """Archiving a list must not make it vanish from Settings (the only Restore surface)."""
    shared = service.ensure_biz_list(777, bcid=None, owner_id=None, peer_name="Sarah")
    archived = await planner.handle_planner_list_update(
        _Req({"archived": True}, match_info={"id": shared["id"]})
    )
    assert archived.status == 200

    default_ids = [row["id"] for row in _body(await planner.handle_planner_lists(_Req()))["data"]]
    assert shared["id"] not in default_ids  # switchers still hide it

    everything = _body(await planner.handle_planner_lists(_Req(query={"archived": "1"})))["data"]
    row = next(r for r in everything if r["id"] == shared["id"])
    assert row["archived"] in (True, 1)

    restored = await planner.handle_planner_list_update(
        _Req({"archived": False}, match_info={"id": shared["id"]})
    )
    assert restored.status == 200
    assert shared["id"] in [r["id"] for r in _body(await planner.handle_planner_lists(_Req()))["data"]]
