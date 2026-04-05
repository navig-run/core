import asyncio
import json
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
            self.edits = []
            self.api_calls = []

        async def send_message(self, chat_id, text, parse_mode="Markdown", **kwargs):
            self.messages.append((chat_id, text, parse_mode, kwargs))
            return {"ok": True}

        async def _api_call(self, method, data):
            self.api_calls.append((method, data))
            return {"ok": True}

        async def edit_message(self, chat_id, message_id, text, parse_mode="Markdown", keyboard=None):
            self.edits.append((chat_id, message_id, text, parse_mode, keyboard))
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
async def test_status_does_not_mark_onboarding_step_on_view(monkeypatch):
    bot = _make_dummy_bot()
    marked: list[str] = []

    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )
    monkeypatch.setattr("navig.spaces.get_default_space", lambda: "default")
    monkeypatch.setattr("navig.spaces.progress.collect_spaces_progress", lambda: [])
    monkeypatch.setattr(
        "navig.spaces.progress.format_spaces_progress_lines",
        lambda rows, max_items=5: [],
    )
    monkeypatch.setattr(
        "navig.commands.init.get_init_status_payload",
        lambda: {"readiness": {"state": "ready", "score": 100, "issues": []}},
    )
    await bot._handle_status(123, 456)
    assert marked == []


@pytest.mark.asyncio
async def test_status_shows_setup_readiness_and_fix_commands(monkeypatch):
    bot = _make_dummy_bot()

    monkeypatch.setattr("navig.spaces.get_default_space", lambda: "default")
    monkeypatch.setattr("navig.spaces.progress.collect_spaces_progress", lambda: [])
    monkeypatch.setattr(
        "navig.spaces.progress.format_spaces_progress_lines",
        lambda rows, max_items=5: [],
    )
    monkeypatch.setattr(
        "navig.commands.init.get_init_status_payload",
        lambda: {
            "readiness": {
                "state": "needs-attention",
                "score": 34,
                "issues": [
                    {
                        "code": "ai-provider-missing",
                        "summary": "AI provider is not configured",
                        "command": "navig init --provider",
                    },
                    {
                        "code": "host-missing",
                        "summary": "No remote hosts connected",
                        "command": "navig host add <name>",
                    },
                ],
            }
        },
    )

    await bot._handle_status(123, 456)

    output = "\n".join(msg[1] for msg in bot.messages)
    assert "Setup readiness:" in output
    assert "needs-attention" in output
    assert "Setup fixes:" in output
    assert "navig init --provider" in output
    assert "navig host add <name>" in output

    keyboard = bot.messages[-1][3].get("keyboard")
    assert keyboard
    flat_callbacks = [btn.get("callback_data") for row in keyboard for btn in row]
    assert "stfix:ai-provider-missing" in flat_callbacks
    assert "stfix:host-missing" in flat_callbacks


@pytest.mark.asyncio
async def test_status_fix_callback_executes_selected_command(monkeypatch):
    bot = _make_dummy_bot()
    executed: list[str] = []

    monkeypatch.setattr(
        "navig.commands.init.get_init_status_payload",
        lambda: {
            "readiness": {
                "issues": [
                    {
                        "code": "host-missing",
                        "summary": "No remote hosts connected",
                        "command": "navig host add prod",
                    }
                ]
            }
        },
    )

    async def _fake_cli(chat_id, user_id, metadata, navig_cmd):
        executed.append(navig_cmd)

    bot._handle_cli_command = _fake_cli

    await bot._handle_status_fix_callback("cb-fix", "stfix:host-missing", 123, 456)

    assert executed == ["host add prod"]
    answer_calls = [p for m, p in bot.api_calls if m == "answerCallbackQuery"]
    assert answer_calls
    assert answer_calls[-1].get("text") == "🚀 Running setup fix"
    assert bot.messages
    last_message = bot.messages[-1]
    assert "Refresh status" in last_message[1]
    keyboard = last_message[3].get("keyboard")
    assert keyboard
    callbacks = [btn.get("callback_data") for row in keyboard for btn in row]
    assert "nav:open:status" in callbacks


@pytest.mark.asyncio
async def test_status_fix_callback_deduplicates_inflight_runs(monkeypatch):
    bot = _make_dummy_bot()
    started = asyncio.Event()
    release = asyncio.Event()
    executed: list[str] = []

    monkeypatch.setattr(
        "navig.commands.init.get_init_status_payload",
        lambda: {
            "readiness": {
                "issues": [
                    {
                        "code": "host-missing",
                        "summary": "No remote hosts connected",
                        "command": "navig host add prod",
                    }
                ]
            }
        },
    )

    async def _fake_cli(chat_id, user_id, metadata, navig_cmd):
        executed.append(navig_cmd)
        started.set()
        await release.wait()

    bot._handle_cli_command = _fake_cli

    task1 = asyncio.create_task(
        bot._handle_status_fix_callback("cb-fix-1", "stfix:host-missing", 123, 456)
    )
    await started.wait()

    await bot._handle_status_fix_callback("cb-fix-2", "stfix:host-missing", 123, 456)

    release.set()
    await task1

    assert executed == ["host add prod"]
    answer_calls = [p for m, p in bot.api_calls if m == "answerCallbackQuery"]
    assert any(call.get("text") == "⏳ Setup fix already running" for call in answer_calls)


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


@pytest.mark.asyncio
async def test_intake_does_not_mark_first_host_on_view(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    marked: list[str] = []

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )

    await bot._handle_intake(123, 456, "/intake health")
    assert marked == []


@pytest.mark.asyncio
async def test_natural_language_money_plan_starts_finance_intake(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    handled = await bot._handle_natural_language_request(
        123,
        456,
        "please work for 1 day and make me a money plan",
    )
    assert handled is True
    assert any("Auto-starting in 3s" in m[1] for m in bot.messages)
    assert any(
        any(btn.get("callback_data") == "nl_yes" for btn in row)
        for msg in bot.messages
        for row in (msg[3].get("keyboard") or [])
    )

    cancelled = await bot._handle_nl_pending_reply(123, 456, "cancel")
    assert cancelled is True
    assert any("action cancelled" in m[1].lower() for m in bot.messages)


@pytest.mark.asyncio
async def test_natural_language_health_improvement_starts_health_intake(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    handled = await bot._handle_natural_language_request(
        123,
        456,
        "i want that you told me how to improve my health currently",
    )
    assert handled is True
    assert any("Auto-starting in 3s" in m[1] for m in bot.messages)

    confirmed = await bot._handle_nl_pending_reply(123, 456, "yes")
    assert confirmed is True
    assert any("Intake started for `health`" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_natural_language_status_runs_command_immediately(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)
    monkeypatch.setattr("navig.spaces.get_default_space", lambda: "default")
    monkeypatch.setattr("navig.spaces.progress.collect_spaces_progress", lambda: [])
    monkeypatch.setattr(
        "navig.spaces.progress.format_spaces_progress_lines",
        lambda rows, max_items=5: [],
    )

    handled = await bot._handle_natural_language_request(123, 456, "please show status")
    assert handled is True
    assert any("NAVIG Status" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_natural_language_risky_restart_requires_confirmation(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    ran: list[str] = []

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    async def _fake_restart(chat_id, user_id, metadata, arg):
        ran.append(arg)
        await bot.send_message(chat_id, f"Restarted: {arg}", parse_mode=None)

    bot._handle_restart = _fake_restart

    handled = await bot._handle_natural_language_request(
        123,
        456,
        "please restart daemon",
    )
    assert handled is True
    assert any("Risky action detected" in m[1] for m in bot.messages)
    assert ran == []

    confirmed = await bot._handle_nl_pending_reply(123, 456, "yes")
    assert confirmed is True
    assert ran == ["daemon"]


@pytest.mark.asyncio
async def test_natural_language_command_missing_args_shows_usage(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    handled = await bot._handle_natural_language_request(123, 456, "can you search")
    assert handled is True
    assert any("Usage:" in m[1] for m in bot.messages)


@pytest.mark.asyncio
async def test_natural_language_unmapped_command_shows_suggestions(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    handled = await bot._handle_natural_language_request(
        123,
        456,
        "please check everything quickly",
    )
    assert handled is True
    assert any("Try:" in m[1] for m in bot.messages)
    keyboard = bot.messages[-1][3].get("keyboard")
    assert keyboard
    callback_values = [btn.get("callback_data") for row in keyboard for btn in row]
    assert any(str(value).startswith("nl_pick:") for value in callback_values)


@pytest.mark.asyncio
async def test_natural_language_ambiguous_command_shows_choices(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()

    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    monkeypatch.setattr(
        bot,
        "_resolve_nl_command_intent",
        lambda _text: {
            "ambiguous": True,
            "candidates": [
                {"command": "model", "usage": "/model [big|small|coder|auto]"},
                {"command": "models", "usage": "/models [big|small|coder|auto]"},
            ],
        },
    )

    handled = await bot._handle_natural_language_request(123, 456, "show model")
    assert handled is True
    assert any("multiple matching commands" in m[1].lower() for m in bot.messages)


@pytest.mark.asyncio
async def test_nl_callback_yes_and_cancel(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    await bot._handle_natural_language_request(123, 456, "make me a money plan")
    await bot._handle_nl_callback("cb1", "nl_yes", 123, 456)
    assert any("Running now" in call[1].get("text", "") for call in bot.api_calls)
    assert any("Intake started for `finance`" in m[1] for m in bot.messages)

    await bot._handle_natural_language_request(123, 456, "improve health")
    await bot._handle_nl_callback("cb2", "nl_cancel", 123, 456)
    assert any("Cancelled" in call[1].get("text", "") for call in bot.api_calls)
    assert any("action cancelled" in m[1].lower() for m in bot.messages)


@pytest.mark.asyncio
async def test_nl_callback_pick_runs_safe_command(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    ran: list[tuple[str, str]] = []

    async def _fake_exec(**kwargs):
        ran.append((kwargs.get("command", ""), kwargs.get("args", "")))

    monkeypatch.setattr(bot, "_execute_nl_registry_command", _fake_exec)

    await bot._handle_nl_callback("cb3", "nl_pick:status", 123, 456)
    assert ran == [("status", "")]
    assert any("Running /status" in call[1].get("text", "") for call in bot.api_calls)


@pytest.mark.asyncio
async def test_nl_callback_pick_risky_requires_confirmation(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    await bot._handle_nl_callback("cb4", "nl_pick:restart", 123, 456)

    assert any("Risky action detected" in m[1] for m in bot.messages)
    assert any("Confirmation required" in call[1].get("text", "") for call in bot.api_calls)
    state = fake_store.get_ai_state(456) or {}
    pending = ((state.get("context") or {}).get("nl_pending") or {})
    assert pending.get("active") is True
    assert pending.get("command") == "restart"


@pytest.mark.asyncio
async def test_nl_callback_pick_missing_args_shows_usage(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    fake_cfg = _FakeConfigManager(tmp_path / "global")
    fake_store = FakeContinuationStore()
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cfg)
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: fake_store)

    await bot._handle_nl_callback("cb5", "nl_pick:search", 123, 456)

    assert any("needs arguments" in m[1].lower() for m in bot.messages)
    assert any("Needs arguments" in call[1].get("text", "") for call in bot.api_calls)


@pytest.mark.asyncio
async def test_start_consumes_chat_onboarding_handoff_once(monkeypatch, tmp_path):
    bot = _make_dummy_bot()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    onboarding_path = tmp_path / ".navig" / "state" / "onboarding.json"
    onboarding_path.parent.mkdir(parents=True, exist_ok=True)
    onboarding_path.write_text(
        json.dumps(
            {
                "steps": [
                    {"id": "ai-provider", "status": "completed"},
                    {"id": "first-host", "status": "failed"},
                    {"id": "telegram-bot", "status": "completed"},
                ]
            }
        ),
        encoding="utf-8",
    )

    state_path = tmp_path / ".navig" / "state" / "chat_onboarding_handoff.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"pending": true, "profile": "quickstart", "token_configured": true, "auto_started": true}',
        encoding="utf-8",
    )

    await bot._handle_start(123, "alice", 456)

    assert any("NAVIG is ready" in msg[1] for msg in bot.messages)
    assert any("Welcome to NAVIG setup" in msg[1] for msg in bot.messages)
    assert any("Onboarding progress: `2/3`" in msg[1] for msg in bot.messages)
    assert any("✅ Choose AI provider" in msg[1] for msg in bot.messages)
    assert any("⬜ Connect first host" in msg[1] for msg in bot.messages)
    assert any("• Add or confirm your first server host" in msg[1] for msg in bot.messages)

    first_count = len(bot.messages)
    await bot._handle_start(123, "alice", 456)
    second_batch = bot.messages[first_count:]
    assert any("NAVIG is ready" in msg[1] for msg in second_batch)
    assert not any("Welcome to NAVIG setup" in msg[1] for msg in second_batch)


@pytest.mark.asyncio
async def test_providers_header_is_clean_and_shows_current_models(monkeypatch):
    bot = _make_dummy_bot()
    marked: list[str] = []

    class _Mode:
        def __init__(self, provider, model):
            self.provider = provider
            self.model = model

    class _Modes:
        def get_mode(self, name):
            mapping = {
                "small_talk": _Mode("nvidia", "deepseek-ai/deepseek-r1"),
                "big_tasks": _Mode("nvidia", "meta/llama-3.3-70b-instruct"),
                "coding": _Mode("nvidia", "meta/llama-3.3-70b-instruct"),
            }
            return mapping.get(name)

    class _Router:
        modes = _Modes()

    class _Manifest:
        def __init__(self, pid, name):
            self.id = pid
            self.display_name = name
            self.emoji = "🧩"
            self.tier = "cloud"
            self.local_probe = None
            self.requires_key = False

    class _Verify:
        key_detected = True
        local_probe_ok = True

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )
    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr(
        "navig.providers.registry.list_enabled_providers",
        lambda: [_Manifest("nvidia", "NVIDIA NIM")],
    )
    monkeypatch.setattr("navig.providers.verifier.verify_provider", lambda _m: _Verify())

    await bot._handle_providers(123, 456)
    assert marked == []
    text = bot.messages[-1][1]
    assert "VS Code" not in text
    assert "AI Providers" in text
    assert "Tap a provider" in text


@pytest.mark.asyncio
async def test_providers_does_not_mark_step_when_no_ready_provider(monkeypatch):
    bot = _make_dummy_bot()
    marked: list[str] = []

    class _Manifest:
        id = "nvidia"
        display_name = "NVIDIA NIM"
        emoji = "🧩"
        tier = "cloud"
        local_probe = None
        requires_key = True

    class _Verify:
        key_detected = False
        local_probe_ok = False

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [_Manifest()])
    monkeypatch.setattr("navig.providers.verifier.verify_provider", lambda _m: _Verify())
    monkeypatch.setattr(bot, "_provider_vault_validation_status", lambda _m: (False, False))

    await bot._handle_providers(123, 456)
    assert marked == []


@pytest.mark.asyncio
async def test_cli_host_use_marks_first_host_on_success(monkeypatch):
    bot = _make_dummy_bot()
    marked: list[str] = []

    async def _on_message(*args, **kwargs):
        return "Host switched to production. Connectivity verified via SSH."

    bot.on_message = _on_message
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )

    await bot._handle_cli_command(123, 456, {}, "host use production")
    assert marked == ["first-host"]


@pytest.mark.asyncio
async def test_cli_host_use_does_not_mark_without_connectivity_phrase(monkeypatch):
    bot = _make_dummy_bot()
    marked: list[str] = []

    async def _on_message(*args, **kwargs):
        return "Host switched to production"

    bot.on_message = _on_message
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )

    await bot._handle_cli_command(123, 456, {}, "host use production")
    assert marked == []


@pytest.mark.asyncio
async def test_cli_host_use_does_not_mark_first_host_on_failure(monkeypatch):
    bot = _make_dummy_bot()
    marked: list[str] = []

    async def _on_message(*args, **kwargs):
        return "Command exited with code: 2\nHost not found"

    bot.on_message = _on_message
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )

    await bot._handle_cli_command(123, 456, {}, "host use missing")
    assert marked == []


@pytest.mark.asyncio
async def test_provider_model_picker_is_tier_first_and_edit_in_place(monkeypatch):
    bot = _make_dummy_bot()

    class _Manifest:
        def __init__(self):
            self.emoji = "🟩"
            self.display_name = "NVIDIA NIM"
            self.models = [
                "meta/llama-3.3-70b-instruct",
                "deepseek-ai/deepseek-r1",
                "qwen/qwen2.5-coder",
            ]

    class _Slot:
        def __init__(self, provider, model):
            self.provider = provider
            self.model = model

    class _Cfg:
        def slot_for_tier(self, tier):
            mapping = {
                "small": _Slot("nvidia", "deepseek-ai/deepseek-r1"),
                "big": _Slot("nvidia", "meta/llama-3.3-70b-instruct"),
                "coder_big": _Slot("nvidia", "meta/llama-3.3-70b-instruct"),
            }
            return mapping[tier]

    class _Router:
        is_active = True
        cfg = _Cfg()

    class _Client:
        model_router = _Router()

    monkeypatch.setattr("navig.providers.registry._INDEX", {"nvidia": _Manifest()})
    monkeypatch.setattr("navig.agent.ai_client.get_ai_client", lambda: _Client())

    await bot._show_provider_model_picker(123, "nvidia", page=0, selected_tier="s", message_id=99)
    assert len(bot.edits) == 1
    edit = bot.edits[-1]
    assert "assign model to tier" in edit[2]
    assert "page" not in edit[2].lower()
    first_row = edit[4][0]
    assert first_row[0]["callback_data"].startswith("pmv_nvidia_s")
    assert first_row[1]["callback_data"].startswith("pmv_nvidia_b")
    assert first_row[2]["callback_data"].startswith("pmv_nvidia_c")
    model_rows = [row for row in edit[4][1:] if row and row[0]["callback_data"].startswith("pms_nvidia_")]
    assert model_rows, "Expected one-button model rows"


@pytest.mark.asyncio
async def test_providers_unconfigured_cloud_shows_provider_row_with_icon_only_key_action(monkeypatch):
    bot = _make_dummy_bot()

    class _Manifest:
        id = "openai"
        display_name = "OpenAI"
        emoji = "🤖"
        tier = "cloud"
        local_probe = None
        requires_key = True
        vault_keys = ["openai/api_key"]

    class _Verify:
        key_detected = False
        local_probe_ok = True

    class _LegacyVault:
        def list(self, provider=None, profile_id=None):
            return []

    class _Store:
        def get(self, label):
            return None

    class _VaultV2:
        def store(self):
            return _Store()

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [_Manifest()])
    monkeypatch.setattr("navig.providers.verifier.verify_provider", lambda _m: _Verify())
    monkeypatch.setattr("navig.vault.get_vault", lambda: _LegacyVault())
    monkeypatch.setattr("navig.vault.get_vault_v2", lambda: _VaultV2())

    await bot._handle_providers(123, 456)
    keyboard = bot.messages[-1][3].get("keyboard") or []
    # Unconfigured cloud providers produce a single-button stub row that
    # combines the provider name and the 🔑 configure-action in one button.
    rows = [row for row in keyboard if len(row) == 1]
    openai_row = next((row for row in rows if any("OpenAI" in btn.get("text", "") for btn in row)), None)
    assert openai_row is not None
    assert "OpenAI" in openai_row[0]["text"]
    assert "🔑" in openai_row[0]["text"]


@pytest.mark.asyncio
async def test_providers_cloud_button_hidden_when_vault_present_but_not_ready(monkeypatch):
    bot = _make_dummy_bot()

    class _Manifest:
        id = "openai"
        display_name = "OpenAI"
        emoji = "🤖"
        tier = "cloud"
        local_probe = None
        requires_key = True
        vault_keys = ["openai/api_key"]

    class _Verify:
        key_detected = False
        local_probe_ok = True

    class _Cred:
        def __init__(self, ok: bool):
            self.data = {"api_key": "sk-test"}
            self.metadata = {"validation_success": ok}

    class _Info:
        def __init__(self, ok: bool):
            self.metadata = {"validation_success": ok}

    class _Vault:
        def __init__(self, ok: bool):
            self._ok = ok

        def get(self, provider, profile_id=None, caller="unknown"):
            if provider == "openai":
                return _Cred(self._ok)
            return None

        def list(self, provider=None, profile_id=None):
            if provider == "openai":
                return [_Info(self._ok)]
            return []

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr(
        "navig.providers.registry.list_enabled_providers",
        lambda: [_Manifest()],
    )
    monkeypatch.setattr("navig.providers.verifier.verify_provider", lambda _m: _Verify())
    monkeypatch.setattr("navig.vault.get_vault", lambda: _Vault(False))
    monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None)

    await bot._handle_providers(123, 456)
    keyboard = bot.messages[-1][3].get("keyboard") or []
    labels = [btn.get("text", "") for row in keyboard for btn in row]
    assert not any("OpenAI" in text for text in labels)


@pytest.mark.asyncio
async def test_providers_cloud_button_shown_after_successful_vault_validation(monkeypatch):
    bot = _make_dummy_bot()

    class _Manifest:
        id = "openai"
        display_name = "OpenAI"
        emoji = "🤖"
        tier = "cloud"
        local_probe = None
        requires_key = True
        vault_keys = ["openai/api_key"]

    class _Verify:
        key_detected = True
        local_probe_ok = True

    class _Cred:
        data = {"api_key": "sk-test"}
        metadata = {"validation_success": True}

    class _Info:
        metadata = {"validation_success": True}

    class _Vault:
        def get(self, provider, profile_id=None, caller="unknown"):
            if provider == "openai":
                return _Cred()
            return None

        def list(self, provider=None, profile_id=None):
            if provider == "openai":
                return [_Info()]
            return []

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr(
        "navig.providers.registry.list_enabled_providers",
        lambda: [_Manifest()],
    )
    monkeypatch.setattr("navig.providers.verifier.verify_provider", lambda _m: _Verify())
    monkeypatch.setattr("navig.vault.get_vault", lambda: _Vault())
    monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None)

    await bot._handle_providers(123, 456)
    keyboard = bot.messages[-1][3].get("keyboard") or []
    labels = [btn.get("text", "") for row in keyboard for btn in row]
    assert any("OpenAI" in text for text in labels)


@pytest.mark.asyncio
async def test_providers_verify_exception_does_not_crash(monkeypatch):
    bot = _make_dummy_bot()

    class _Manifest:
        id = "openai"
        display_name = "OpenAI"
        emoji = "🤖"
        tier = "cloud"
        local_probe = None
        requires_key = True
        vault_keys = ["openai/api_key"]

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [_Manifest()])
    monkeypatch.setattr(
        "navig.providers.verifier.verify_provider",
        lambda _m: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await bot._handle_providers(123, 456)
    text = bot.messages[-1][1]
    assert "No vault-backed cloud providers are ready" in text


@pytest.mark.asyncio
async def test_providers_screen_shows_noai_selection_state(monkeypatch):
    bot = _make_dummy_bot()
    bot._user_model_prefs = {}
    bot._user_model_prefs[456] = "noai"

    class _Router:
        modes = None

    async def _probe():
        return False, "127.0.0.1:11435"

    bot._probe_bridge_grid = _probe
    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [])

    await bot._handle_providers(123, 456)
    text = bot.messages[-1][1]
    keyboard = bot.messages[-1][3].get("keyboard") or []
    labels = [btn.get("text", "") for row in keyboard for btn in row]
    assert "Next message mode" in text
    assert any(text.startswith("✅ 🚫 No AI") for text in labels)
