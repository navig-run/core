"""Follow-up suggestions — read/deliver pass over existing todos + notify.

No new store, no new scheduler: the task is the BoardStore todo, delivery is the
notify router, and the cooldown is the existing dedupe claim. The critical honesty
rule: an external reply is NEVER claimed checked — the suggestion says unverified.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from navig.pim import followups
from navig.store.board import BoardStore

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PAST = "2026-10-20T09:00:00Z"
FUTURE = "2026-10-25T09:00:00Z"


def _store(tmp_path) -> BoardStore:  # noqa: ANN001
    return BoardStore(tmp_path / "board.db")


def test_candidates_need_a_person_an_arrived_moment_and_an_open_task(tmp_path) -> None:
    store = _store(tmp_path)
    due = store.create_todo("Reply to Rakib", with_whom="Rakib", due_at=PAST)
    store.create_todo("Reply to Sk Rehan", with_whom="Sk Rehan", due_at=FUTURE)
    done = store.create_todo("Reply to Alex", with_whom="Alex", due_at=PAST)
    store.complete_todo(done["id"])
    store.create_todo("Buy milk", due_at=PAST)  # no person → not a follow-up

    rows = followups.candidates(store=store, now=NOW)

    assert [r["id"] for r in rows] == [due["id"]]
    assert rows[0]["verification"]["state"] == "unverified"


def test_suggestion_names_the_person_and_never_claims_a_reply_was_checked(tmp_path) -> None:
    store = _store(tmp_path)
    todo = store.create_todo("Reply to Rakib", with_whom="Rakib", due_at=PAST)
    msg = followups.suggestion(todo, now=NOW)
    assert "Rakib" in msg["title"] or "Rakib" in msg["body"]
    assert "I can't verify" in msg["body"]
    assert msg["verification"]["state"] == "unverified"
    assert msg["verification"]["source"] is None


def test_suggest_delivers_once_per_cooldown(tmp_path) -> None:
    store = _store(tmp_path)
    store.create_todo("Reply to Rakib", with_whom="Rakib", due_at=PAST)
    sent: list[dict] = []
    claimed: set[str] = set()

    async def dispatch(type_key, title, body, **kw):  # noqa: ANN001
        sent.append({"type": type_key, "title": title, "body": body, "kw": kw})
        return {"channels": ["deck"]}

    def claim(key: str, cooldown: float, *, now: float | None = None) -> bool:
        if now is not None:
            return True
        if key in claimed:
            return False
        claimed.add(key)
        return True

    first = asyncio.run(followups.suggest(
        store=store, now=NOW, dispatch=dispatch, claim=claim, release=lambda _k: None,
    ))
    second = asyncio.run(followups.suggest(
        store=store, now=NOW, dispatch=dispatch, claim=claim, release=lambda _k: None,
    ))

    assert [r["status"] for r in first] == ["delivered"]
    assert [r["status"] for r in second] == ["suppressed"]
    assert len(sent) == 1
    assert sent[0]["type"] == "reminder"
    assert sent[0]["kw"]["data"]["kind"] == "followup"


def test_a_no_channel_delivery_releases_the_claim_for_a_later_tick(tmp_path) -> None:
    store = _store(tmp_path)
    store.create_todo("Reply to Rakib", with_whom="Rakib", due_at=PAST)
    released: list[str] = []

    async def dispatch(*_a, **_kw):  # noqa: ANN002, ANN003
        return {"channels": []}

    result = asyncio.run(followups.suggest(
        store=store, now=NOW, dispatch=dispatch,
        claim=lambda *_a, **_k: True,
        release=lambda key: released.append(key),
    ))

    assert result[0]["status"] == "not-delivered"
    assert result[0]["reason"] == "no-channel"
    assert released and released[0].startswith("followup:")


def test_snooze_writes_a_future_cooldown_claim() -> None:
    captured: dict = {}

    def claim(key: str, cooldown: float, *, now: float | None = None) -> bool:
        captured.update(key=key, cooldown=cooldown, now=now)
        return True

    assert followups.snooze("todo-1", hours=8, claim=claim) is True
    assert captured["key"] == "followup:todo-1"
    assert captured["now"] > time.time() + 8 * 3600 - 10
