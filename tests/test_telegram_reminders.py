import pytest


def _get_mixin():
    mod = pytest.importorskip("navig.gateway.channels.telegram_commands")
    return mod.TelegramCommandsMixin


def _make_dummy_bot():
    mixin = _get_mixin()

    class DummyTelegram(mixin):
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text, parse_mode="Markdown", **kwargs):
            self.messages.append((chat_id, text, parse_mode, kwargs))
            return {"ok": True}

    return DummyTelegram()


class FakeStore:
    def __init__(self):
        self.created = []
        self.user_rows = [
            {
                "id": 11,
                "remind_at": "2026-03-29T11:30:00+00:00",
                "message": "check deploy",
            }
        ]
        self.cancelled = set()

    def create_reminder(self, user_id, chat_id, message, remind_at):
        self.created.append((user_id, chat_id, message, remind_at))
        return 77

    def get_user_reminders(self, user_id):
        return list(self.user_rows)

    def cancel_reminder(self, reminder_id, user_id):
        if reminder_id == 11:
            self.cancelled.add((reminder_id, user_id))
            return True
        return False


class FakeAutoStateStore:
    def __init__(self):
        self.state = None

    def set_ai_state(self, user_id, chat_id, mode, persona=None, context=None):
        self.state = {
            "user_id": user_id,
            "chat_id": chat_id,
            "mode": mode,
            "persona": persona,
            "context": context,
        }

    def get_ai_state(self, user_id):
        if self.state and self.state.get("user_id") == user_id:
            return dict(self.state)
        return None

    def clear_ai_state(self, user_id):
        if self.state and self.state.get("user_id") == user_id:
            self.state["mode"] = "inactive"


@pytest.mark.asyncio
async def test_parse_remindme_relative_format():
    bot = _make_dummy_bot()
    remind_at, msg, err = bot._parse_remindme_request(
        "/remindme in 10 minutes check logs"
    )

    assert err is None
    assert remind_at is not None
    assert msg == "check logs"


@pytest.mark.asyncio
async def test_remindme_creates_runtime_store_reminder(monkeypatch):
    bot = _make_dummy_bot()
    fake_store = FakeStore()

    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: fake_store,
    )

    await bot._handle_remindme(123, 456, "/remindme in 5 minutes restart service")

    assert len(fake_store.created) == 1
    assert fake_store.created[0][0] == 456
    assert fake_store.created[0][1] == 123
    assert "restart service" in fake_store.created[0][2]
    assert any("Reminder set" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_myreminders_and_cancel_flow(monkeypatch):
    bot = _make_dummy_bot()
    fake_store = FakeStore()

    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: fake_store,
    )

    await bot._handle_myreminders(123, 456)
    assert any("Your Active Reminders" in m[1] for m in bot.messages)

    await bot._handle_cancelreminder(123, 456, "/cancelreminder 11")
    assert (11, 456) in fake_store.cancelled
    assert any("cancelled" in m[1].lower() for m in bot.messages)


@pytest.mark.asyncio
async def test_auto_state_start_status_stop_flow(monkeypatch):
    bot = _make_dummy_bot()
    fake_store = FakeAutoStateStore()

    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: fake_store,
    )

    await bot._handle_auto_start(123, 456, "/auto_start teacher")
    assert fake_store.state is not None
    assert fake_store.state["mode"] == "active"
    assert fake_store.state["persona"] == "teacher"
    assert any("ACTIVATED" in m[1] for m in bot.messages)

    await bot._handle_auto_status(123, 456)
    assert any("ACTIVE" in m[1] for m in bot.messages)

    await bot._handle_auto_stop(123, 456)
    assert fake_store.state["mode"] == "inactive"

    await bot._handle_auto_status(123, 456)
    assert any("INACTIVE" in m[1] for m in bot.messages)
