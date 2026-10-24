"""Telegram delivery ownership: one mode per Brain, transitions that can be trusted.

The incident in one test: a client install must not be able to set the webhook. The
second: switching to polling must delete the webhook BEFORE polling (a registered
webhook makes every getUpdates a 409), and every pending update must be consumed
exactly once — not dropped, not delivered twice.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from navig.brain import telegram_mode as tg
from navig.brain.identity import owner_id
from navig.brain.lease import ROLE_BRAIN, ROLE_CLIENT, ROLE_EXECUTOR, LeaseStore

MACHINE_A = "brn-aaaaaaaaaaaaaaaa"
MACHINE_B = "brn-bbbbbbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "all-in-one")
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)


class FakeTelegram:
    """A scripted Bot API. Records the order of calls — order IS the assertion."""

    def __init__(self, *, pending: list[dict] | None = None, webhook_url: str = "") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.pending = list(pending or [])
        self.webhook_url = webhook_url
        self.fail: set[str] = set()

    async def __call__(self, method: str, params: dict) -> object:
        self.calls.append((method, dict(params)))
        if method in self.fail:
            return None
        if method == "deleteWebhook":
            self.webhook_url = ""
            if not params.get("drop_pending_updates"):
                pass  # Telegram would keep the backlog — we keep ours too
            return True
        if method == "setWebhook":
            self.webhook_url = str(params.get("url") or "")
            return True
        if method == "getWebhookInfo":
            return {"url": self.webhook_url, "pending_update_count": len(self.pending)}
        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            # Real Telegram semantics: an offset CONFIRMS every update below it, so
            # those are forgotten. Modelling that here is what makes "the backlog is
            # really drained" a meaningful assertion.
            if offset:
                self.pending = [u for u in self.pending if int(u.get("update_id", 0)) >= offset]
            limit = int(params.get("limit") or 100)
            return [dict(u) for u in self.pending[:limit]]
        return {}

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


# ── the exact failure: a client must never set the webhook ─────────────────


def test_a_client_install_cannot_set_the_webhook() -> None:
    with pytest.raises(tg.TelegramModeRefused) as excinfo:
        tg.authorize_delivery_mode(tg.MODE_WEBHOOK, role=ROLE_CLIENT)
    message = str(excinfo.value)
    assert "client" in message
    assert "webhook" in message
    assert "navig brain telegram" in message  # the remedy, not just the refusal


def test_a_client_install_cannot_poll_either() -> None:
    with pytest.raises(tg.TelegramModeRefused):
        tg.authorize_delivery_mode(tg.MODE_POLLING, role=ROLE_CLIENT)


@pytest.mark.parametrize("role", [ROLE_CLIENT, ROLE_EXECUTOR])
def test_only_brain_roles_may_own_delivery(role: str) -> None:
    with pytest.raises(tg.TelegramModeRefused):
        tg.authorize_delivery_mode(tg.MODE_POLLING, role=role)


def test_the_brain_role_may_own_either_mode() -> None:
    tg.authorize_delivery_mode(tg.MODE_POLLING, role=ROLE_BRAIN)
    tg.authorize_delivery_mode(tg.MODE_WEBHOOK, role=ROLE_BRAIN)


def test_a_live_lease_held_elsewhere_forbids_this_process(tmp_path: Path) -> None:
    store = LeaseStore(tmp_path / "cfg" / "brain" / "lease.json")
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    with pytest.raises(tg.TelegramModeRefused) as excinfo:
        tg.authorize_delivery_mode(
            tg.MODE_WEBHOOK, role=ROLE_BRAIN, owner=MACHINE_B, lease_store=store
        )
    assert MACHINE_A in str(excinfo.value)


def test_a_mode_owned_by_somebody_else_needs_a_recorded_transition(
    tmp_path: Path,
) -> None:
    store = tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json")
    store.write(
        tg.TelegramModeRecord(mode=tg.MODE_WEBHOOK, owner_id=MACHINE_A, epoch=3),
        from_=tg.MODE_OFF,
        evidenced={"reason": "set up"},
    )
    with pytest.raises(tg.TelegramModeRefused) as excinfo:
        tg.authorize_delivery_mode(
            tg.MODE_POLLING, role=ROLE_BRAIN, owner=MACHINE_B, store=store
        )
    assert "recorded transition" in str(excinfo.value)


# ── transitions ────────────────────────────────────────────────────────────


def test_polling_transition_deletes_the_webhook_before_touching_updates(
    tmp_path: Path,
) -> None:
    api = FakeTelegram(webhook_url="https://edge/tg/abc", pending=[])
    result = asyncio.run(
        tg.transition(
            tg.MODE_POLLING,
            api=api,
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            store=tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json"),
            ledger=tg.DeliveryLedger(tmp_path / "cfg" / "brain" / "delivered.json"),
        )
    )
    assert result.ok is True
    methods = api.methods()
    assert methods[0] == "deleteWebhook", methods
    assert "getUpdates" in methods
    # deleteWebhook must not discard the backlog unless explicitly asked.
    assert api.calls[0][1] == {"drop_pending_updates": False}


def test_pending_updates_are_consumed_exactly_once(tmp_path: Path) -> None:
    pending = [{"update_id": 41, "message": {"text": "hi"}}, {"update_id": 42}]
    handled: list[int] = []

    async def on_update(update: dict) -> None:
        handled.append(int(update["update_id"]))

    api = FakeTelegram(webhook_url="https://edge/tg/abc", pending=pending)
    mode_store = tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json")
    ledger = tg.DeliveryLedger(tmp_path / "cfg" / "brain" / "delivered.json")

    result = asyncio.run(
        tg.transition(
            tg.MODE_POLLING,
            api=api,
            on_update=on_update,
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            store=mode_store,
            ledger=ledger,
        )
    )
    assert result.ok is True
    assert handled == [41, 42]
    assert result.consumed == 2
    assert result.pending_before == 2
    assert result.pending_after == 0
    assert result.duplicates == []

    # The ledger survives the transition: a REPLAY of the same ids is reported, not
    # delivered again. This is the "exactly once" evidence a migration receipt cites.
    replay = asyncio.run(
        tg.transition(
            tg.MODE_POLLING,
            api=FakeTelegram(pending=pending),
            on_update=on_update,
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            store=mode_store,
            ledger=tg.DeliveryLedger(tmp_path / "cfg" / "brain" / "delivered.json"),
        )
    )
    assert replay.consumed == 0
    assert set(replay.duplicates) == {41, 42}
    assert handled == [41, 42]  # nothing delivered twice


def test_a_failed_webhook_delete_refuses_to_poll(tmp_path: Path) -> None:
    api = FakeTelegram(webhook_url="https://edge/tg/abc")
    api.fail = {"deleteWebhook"}
    result = asyncio.run(
        tg.transition(
            tg.MODE_POLLING,
            api=api,
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            store=tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json"),
            ledger=tg.DeliveryLedger(tmp_path / "cfg" / "brain" / "delivered.json"),
        )
    )
    assert result.ok is False
    assert "deleteWebhook failed" in result.detail
    assert "getUpdates" not in api.methods()  # parked instead of hammering a 409


def test_an_undrained_backlog_cannot_be_reported_ok(tmp_path: Path) -> None:
    """A partial drain must leave ok=False — this is what a receipt reads.

    The drain is deliberately bounded (20 × 100) so a pathological backlog cannot hang
    a transition. Anything beyond the bound must therefore be REPORTED, never rounded up
    to success.
    """
    pending = [{"update_id": i} for i in range(1, 2101)]
    api = FakeTelegram(webhook_url="https://edge/tg/abc", pending=pending)
    result = asyncio.run(
        tg.transition(
            tg.MODE_POLLING,
            api=api,
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            store=tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json"),
            ledger=tg.DeliveryLedger(tmp_path / "cfg" / "brain" / "delivered.json"),
        )
    )
    assert result.ok is False
    assert "still pending" in result.detail


def test_transition_records_owner_epoch_and_evidence(tmp_path: Path) -> None:
    store = tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json")
    asyncio.run(
        tg.transition(
            tg.MODE_POLLING,
            api=FakeTelegram(pending=[{"update_id": 7}]),
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            epoch=5,
            reason="cut over to the outpost",
            store=store,
            ledger=tg.DeliveryLedger(tmp_path / "cfg" / "brain" / "delivered.json"),
        )
    )
    record = store.read()
    assert record.mode == tg.MODE_POLLING
    assert record.owner_id == MACHINE_A
    assert record.epoch == 5
    transition = record.transitions[-1]
    assert transition["to"] == tg.MODE_POLLING
    assert transition["reason"] == "cut over to the outpost"
    assert transition["consumed"] == 1


def test_webhook_transition_never_claims_a_start_it_cannot_see(tmp_path: Path) -> None:
    store = tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json")
    result = asyncio.run(
        tg.transition(
            tg.MODE_WEBHOOK,
            api=FakeTelegram(webhook_url=""),
            owner=MACHINE_A,
            role=ROLE_BRAIN,
            store=store,
        )
    )
    assert result.ok is True
    assert store.read().mode == tg.MODE_WEBHOOK
    assert store.read().owner_id == MACHINE_A


def test_status_is_owned_by_this_machine_and_lists_history(tmp_path: Path) -> None:
    store = tg.TelegramModeStore(tmp_path / "cfg" / "brain" / "telegram_mode.json")
    store.write(
        tg.TelegramModeRecord(mode=tg.MODE_POLLING, owner_id=owner_id(), epoch=1),
        from_=tg.MODE_OFF,
        evidenced={"reason": "initial"},
    )
    status = store.status()
    assert status["mode"] == tg.MODE_POLLING
    assert status["yours"] is True
    assert len(status["transitions"]) == 1


def test_an_unreadable_mode_record_does_not_hand_delivery_away(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "brain" / "telegram_mode.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    record = tg.TelegramModeStore(path).read()
    assert record.mode == tg.MODE_OFF
    assert record.reason == "unreadable mode record"


def test_ledger_caps_its_memory(tmp_path: Path) -> None:
    ledger = tg.DeliveryLedger(tmp_path / "delivered.json")
    assert ledger.claim(1) is True
    assert ledger.claim(1) is False  # the replay is caught
    assert ledger.duplicates == [1]


def test_the_allowed_updates_list_is_the_one_shared_constant() -> None:
    """Retyping the list inline silently narrows delivery for everyone."""
    from navig.telegram.updates import ALLOWED_UPDATES

    assert tg._allowed_updates() == list(ALLOWED_UPDATES)


def test_navig_config_dir_is_isolated_by_the_fixture(tmp_path: Path) -> None:
    """Guard against a test writing into the operator's real brain."""
    assert tg.mode_path().is_relative_to(tmp_path)
    assert json.dumps(tg.TelegramModeStore().status())  # serialisable
