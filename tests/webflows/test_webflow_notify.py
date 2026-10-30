"""The pause notification: dispatched once, routed by type, carrying no value."""

from __future__ import annotations

import asyncio

import navig.notify as notify_mod
from navig.webflows.notify import NOTIFY_TYPE, needs_user_message, notify_needs_user


def test_the_message_names_the_flow_the_reason_and_how_to_resume():
    title, body = needs_user_message("wf_shop", "Order a plan", "the vault has no login for shop.com", "wfrun_abc")
    assert title == "Webflow needs you"
    assert "Order a plan" in body and "shop.com" in body
    assert "--resume wfrun_abc" in body
    # without a run id it points at the Dock instead of a resume
    assert "--resume" not in needs_user_message("wf_shop", "Order a plan", "a secret is needed")[1]


def test_dispatch_is_called_once_with_the_registered_type_and_no_value(monkeypatch):
    calls = []

    async def fake_dispatch(kind, title, body, *, priority=None, data=None):
        calls.append({"kind": kind, "title": title, "body": body, "priority": priority, "data": data})
        return "sent"

    monkeypatch.setattr(notify_mod, "dispatch", fake_dispatch)
    out = asyncio.run(notify_needs_user(flow_id="wf_shop", flow_name="Order",
                                         reason="a secret nobody may fill", run_id="wfrun_abc"))
    assert out == "sent" and len(calls) == 1
    assert calls[0]["kind"] == NOTIFY_TYPE == "webflow_needs_user"
    assert calls[0]["data"]["run_id"] == "wfrun_abc"


def test_a_failed_push_never_fails_the_pause(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("notify is down")

    monkeypatch.setattr(notify_mod, "dispatch", boom)
    assert asyncio.run(notify_needs_user(flow_id="wf_shop", flow_name="Order", reason="x")) is None


def test_the_type_is_registered_routed_and_seeds_its_matrix(tmp_path, monkeypatch):
    """The wiring a new notify type owes: a row, a real glyph, and matrix seeding."""
    from navig.notify import store
    from navig.notify.types import NOTIFICATION_TYPES, TYPE_KEYS, emoji_for_type

    row = next(t for t in NOTIFICATION_TYPES if t["key"] == "webflow_needs_user")
    assert row["label"] == "Webflow needs you" and row["category"] == "NAVIG"
    assert row["default_channels"] == ["deck", "telegram"], "a pause reaches Telegram when configured"
    assert "webflow_needs_user" in TYPE_KEYS
    assert emoji_for_type("webflow_needs_user") != "📢"  # never the generic glyph

    # An install whose notify.db predates the type gains its rows on the next init
    # (INSERT OR IGNORE — another type's toggles stay untouched).
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(store, "_initialised", None)
    store.init_db()
    with store.conn() as conn:
        rows = dict(conn.execute(
            "SELECT channel, enabled FROM notify_matrix WHERE type = 'webflow_needs_user'"
        ).fetchall())
    assert rows.get("deck") == 1 and rows.get("telegram") == 1, rows
