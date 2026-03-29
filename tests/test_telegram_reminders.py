import pytest
from pathlib import Path


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


class FakeContinuationStore(FakeAutoStateStore):
    def __init__(self):
        super().__init__()
        self.state = {
            "user_id": 456,
            "chat_id": 123,
            "mode": "active",
            "persona": "teacher",
            "context": {},
        }


class _FakeConfigManager:
    def __init__(self, base: Path):
        self.global_config_dir = str(base)
        self.global_config = {}


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


@pytest.mark.asyncio
async def test_continuation_controls_update_context(monkeypatch):
    bot = _make_dummy_bot()
    fake_store = FakeContinuationStore()

    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: fake_store,
    )

    await bot._handle_continue(123, 456, "/continue balanced finance")
    ctx = fake_store.state.get("context") or {}
    cont = ctx.get("continuation") or {}
    assert cont.get("enabled") is True
    assert cont.get("paused") is False
    assert cont.get("profile") == "balanced"
    assert cont.get("space") == "finance"
    assert any("Policy: cooldown=10s, max_turns=3" in m[1] for m in bot.messages)
    assert any("suppression(wait=30s, blocked=90s)" in m[1] for m in bot.messages)
    assert any("decision=standard" in m[1] for m in bot.messages)

    await bot._handle_pause(123, 456)
    ctx2 = fake_store.state.get("context") or {}
    cont2 = ctx2.get("continuation") or {}
    assert cont2.get("paused") is True

    await bot._handle_skip(123, 456)
    ctx3 = fake_store.state.get("context") or {}
    cont3 = ctx3.get("continuation") or {}
    assert cont3.get("skip_next") is True

    await bot._handle_auto_status(123, 456)
    assert any("Continuation:" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_auto_status_shows_busy_suppression_metadata(monkeypatch):
    bot = _make_dummy_bot()
    fake_store = FakeContinuationStore()
    fake_store.state["context"] = {
        "continuation": {
            "enabled": True,
            "paused": False,
            "skip_next": False,
            "profile": "conservative",
            "cooldown_seconds": 20,
            "max_turns": 2,
            "turns_used": 0,
            "busy_until": "2099-01-01T00:00:00+00:00",
            "busy_reason": "wait_signal",
            "last_skip_reason": "busy_suppressed:wait_signal",
        }
    }

    monkeypatch.setattr(
        "navig.store.runtime.get_runtime_store",
        lambda: fake_store,
    )

    await bot._handle_auto_status(123, 456)
    assert any("busy_until=" in m[1] for m in bot.messages)
    assert any("last_skip=" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_space_command_switches_and_prints_kickoff(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_store = FakeContinuationStore()
    fake_cfg = _FakeConfigManager(tmp_path / "global")

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    repo = tmp_path / "repo"
    plans_dir = repo / ".navig" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "DEV_PLAN.md").write_text("- [ ] Prepare incident runbook\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    await bot._handle_space(123, 456, "/space devops")

    assert any("Active space: `devops`" in m[1] for m in bot.messages)
    assert any("Top next actions:" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_spaces_command_lists_devops_and_sysops(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)

    await bot._handle_spaces(123)
    assert any("`devops`" in m[1] for m in bot.messages)
    assert any("`sysops`" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_intake_flow_writes_space_docs(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    await bot._handle_intake(123, 456, "/intake health")
    assert any("Intake started" in m[1] for m in bot.messages)

    handled = await bot._handle_intake_reply(123, 456, "Improve sleep and recovery")
    assert handled is True
    handled = await bot._handle_intake_reply(123, 456, "Have a repeatable bedtime routine by tomorrow")
    assert handled is True
    handled = await bot._handle_intake_reply(123, 456, "Late-night screen time")
    assert handled is True
    handled = await bot._handle_intake_reply(123, 456, "I assume I can sleep well without planning evenings")
    assert handled is True

    health_dir = Path(fake_cfg.global_config_dir) / "spaces" / "health"
    assert (health_dir / "VISION.md").exists()
    assert (health_dir / "ROADMAP.md").exists()
    assert (health_dir / "CURRENT_PHASE.md").exists()
    assert "Intake" in (health_dir / "VISION.md").read_text(encoding="utf-8")
    assert any("Intake completed" in m[1] for m in bot.messages)
