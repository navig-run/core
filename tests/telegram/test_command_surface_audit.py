"""The Telegram command surface says what it does, and every path to a command works.

Covers the command audit: /help lists every visible registry command exactly once;
commands that moved to the Mini App answer with where they went instead of reaching
the model as chat; ``nav:cmd:`` inline buttons run the same dispatch a typed command
does; /auto_start refuses an unknown persona; /explain_ai is honest when it has no
answer. Every channel here is a REAL TelegramChannel -- a subclass of the mixin would
inherit the very siblings production lacks.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from navig.gateway.channels import telegram_commands as tc
from navig.gateway.channels.telegram import TelegramChannel

pytestmark = pytest.mark.integration

CHANNELS = Path(tc.__file__).resolve().parent


def _reset_config_singletons(monkeypatch) -> None:
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    reset_config_manager()
    monkeypatch.setattr(ConfigSingleton, "_instance", None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No test here may read or write the operator's real config or data."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    _reset_config_singletons(monkeypatch)
    yield
    from navig.config import reset_config_manager

    reset_config_manager()


def _channel(deck_url: str | None = None) -> TelegramChannel:
    channel = TelegramChannel(bot_token="123:FAKE", allowed_users=[456])
    channel.sent = []

    async def send_message(chat_id, text, parse_mode="HTML", **kwargs):
        channel.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode, **kwargs})
        return {"ok": True}

    async def _api_call(method, data=None, *a, **k):  # never the network
        return {"ok": True}

    async def send_typing(*_a, **_k):
        return None

    channel.send_message = send_message
    channel._api_call = _api_call
    channel.send_typing = send_typing
    channel._get_deck_url = lambda: deck_url
    return channel


# ── /help covers the registry ──────────────────────────────────────────────────────


def _help_placements() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for cat in tc._HELP_CATEGORIES:
        for name in cat.commands or []:
            out.append((name, cat.key))
        for sub in cat.subcategories or []:
            for name in sub.commands:
                out.append((name, f"{cat.key}/{sub.key}"))
    return out


def test_every_visible_command_is_in_exactly_one_help_category() -> None:
    placements = _help_placements()
    assert len(placements) >= 80, f"only {len(placements)} placements -- did the read break?"
    names = [n for n, _ in placements]
    twice = sorted({n for n in names if names.count(n) > 1})
    assert not twice, f"listed in more than one /help category: {twice}"
    visible = {e.command for e in tc._iter_unique_registry(visible_only=True)}
    missing = sorted(visible - set(names))
    assert not missing, f"visible commands /help never shows: {missing}"


def test_help_names_only_commands_that_exist() -> None:
    registry = {e.command for e in tc._iter_unique_registry()}
    unknown = sorted(n for n, _ in _help_placements() if n not in registry)
    assert not unknown, f"/help lists commands the registry does not define: {unknown}"


def test_every_locked_command_is_reachable_from_help_or_answers() -> None:
    """A locked command can never be switched off -- so it must never be a dead end."""
    shown = {n for n, _ in _help_placements()}
    registry = {e.command: e for e in tc._iter_unique_registry()}
    for name in tc.LOCKED_COMMANDS:
        entry = registry.get(name)
        if entry is not None and entry.visible:
            assert name in shown, f"locked /{name} is missing from /help"
        else:
            assert entry is not None or name in tc.MOVED_COMMANDS, (
                f"locked /{name} has no handler and no moved-command pointer -- it would "
                "fall through to the model as chat"
            )


def test_beta_stubs_are_not_advertised() -> None:
    shown = {n for n, _ in _help_placements()}
    assert not {"music", "quote", "imagegen"} & shown


def test_explain_ai_sits_with_text_and_reasoning() -> None:
    assert ("explain_ai", "utilities/text") in _help_placements()


def test_mode_lives_in_focus_and_names_the_real_modes() -> None:
    from navig.gateway.channels.focus_modes import FOCUS_MODES

    assert ("mode", "focus") in _help_placements()
    desc = next(e.description for e in tc._SLASH_REGISTRY if e.command == "mode")
    for mode_id in FOCUS_MODES:
        assert mode_id in desc, f"/mode description does not name {mode_id!r}"


# ── moved commands answer with where they went ─────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/models", "Model tier"),
        ("/model", "Model tier"),
        ("/ai", "Model tier"),
        ("/big", "Model tier"),
        ("/small@MyBot", "Model tier"),
        ("/routing", "LLM routing"),
        ("/router", "LLM routing"),
        ("/providers", "LLM providers"),
        ("/provider", "LLM providers"),
        ("/persona", "AI persona"),
        ("/personas", "AI persona"),
        ("/voice", "Voice & audio"),
        ("/audio", "Voice & audio"),
        ("/settings", "Settings"),
    ],
)
def test_moved_command_notice(text: str, expected: str) -> None:
    notice = tc.moved_command_notice(text)
    assert notice is not None and expected in notice and "Mini App" in notice


@pytest.mark.parametrize("text", ["/big explain DNS", "/coder write a loop", "/small hi"])
def test_one_shot_tier_prefix_with_text_is_not_intercepted(text: str) -> None:
    assert tc.moved_command_notice(text) is None


def test_ordinary_commands_are_not_moved() -> None:
    for text in ("/status", "/help", "/voiceon", "hello there", ""):
        assert tc.moved_command_notice(text) is None


async def test_moved_command_reply_has_an_open_button_when_a_deck_exists() -> None:
    channel = _channel(deck_url="https://deck.example/connect?key=k")
    assert await channel._reply_moved_command(123, "/models", is_group=False)
    msg = channel.sent[-1]
    assert "Model tier" in msg["text"]
    assert msg["keyboard"][0][0]["web_app"]["url"] == "https://deck.example/connect?key=k"


async def test_moved_command_reply_is_plain_text_in_a_group_or_without_a_deck() -> None:
    for deck, group in (("https://deck.example", True), (None, False)):
        channel = _channel(deck_url=deck)
        assert await channel._reply_moved_command(-100, "/settings", is_group=group)
        assert "keyboard" not in channel.sent[-1]


async def test_bare_models_never_reaches_the_model(monkeypatch) -> None:
    """End to end through _process_update: the AI fallback must not run."""
    channel = TelegramChannel(
        bot_token="123:FAKE", allowed_users=[42], on_message=AsyncMock(return_value="x")
    )
    channel._bot_username = "mybot"
    channel._dispatch_by_mode = AsyncMock()
    sent: list[str] = []

    async def send_message(chat_id, text, parse_mode="HTML", **kwargs):
        sent.append(text)
        return {"ok": True}

    channel.send_message = send_message
    channel.send_typing = AsyncMock()
    channel._api_call = AsyncMock(return_value={"ok": True})
    channel._get_deck_url = lambda: None
    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)

    for text in ("/models", "/settings"):
        await channel._process_update(
            {"message": {"message_id": 1, "text": text, "chat": {"id": 42, "type": "private"},
                         "from": {"id": 42, "username": "u"}}}
        )
    channel._dispatch_by_mode.assert_not_awaited()
    assert any("Model tier" in t for t in sent), sent
    assert any("Settings" in t for t in sent), sent


# ── nav:cmd: buttons dispatch like typing ──────────────────────────────────────────


def _nav_cmd_targets() -> set[str]:
    src = (CHANNELS / "telegram_commands.py").read_text(encoding="utf-8")
    return set(re.findall(r'"nav:cmd:(/[a-z_]+)"', src))


def test_the_nav_cmd_scan_sees_the_cards() -> None:
    assert {"/plans", "/plan", "/spaces"} <= _nav_cmd_targets()


async def test_every_nav_cmd_button_runs_its_registry_handler() -> None:
    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    registry = {e.command: e for e in tc._SLASH_REGISTRY}
    targets = sorted(_nav_cmd_targets())
    for target in targets:
        channel = _channel()
        entry = registry[target.lstrip("/")]
        calls: list[dict] = []

        # A real signature, not AsyncMock's (*args, **kwargs): dispatch passes only the
        # arguments a handler declares, so a mock would receive nothing at all.
        async def spy(chat_id: int, user_id: int = 0, text: str = "", _c=calls) -> None:
            _c.append({"chat_id": chat_id, "user_id": user_id, "text": text})

        setattr(channel, entry.handler, spy)
        handler = CallbackHandler(channel)
        await handler._handle_navigation_callback(
            "cb1", f"nav:cmd:{target}", 123, 9, 456
        )
        assert calls == [{"chat_id": 123, "user_id": 456, "text": target}], (target, calls)


async def test_nav_cmd_for_an_unknown_command_says_so() -> None:
    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    channel = _channel()
    await CallbackHandler(channel)._handle_navigation_callback(
        "cb1", "nav:cmd:/nosuchcommand", 123, 9, 456
    )
    assert "isn't available" in channel.sent[-1]["text"]


async def test_nav_cmd_plans_reaches_the_real_handler(monkeypatch) -> None:
    """No spy: the bound /plans handler itself runs from the button."""
    from navig.gateway.channels.telegram_keyboards import CallbackHandler

    channel = _channel()
    called: list[int] = []

    async def fake_plans(self, chat_id, *a, **k):
        called.append(chat_id)

    monkeypatch.setattr(tc.TelegramCommandsMixin, "_handle_plans_cmd", fake_plans)
    # The channel binds _handle_plans_cmd at import time, so re-point the binding too.
    monkeypatch.setattr(TelegramChannel, "_handle_plans_cmd", fake_plans)
    await CallbackHandler(channel)._handle_navigation_callback(
        "cb1", "nav:cmd:/plans", 123, 9, 456
    )
    assert called == [123]


# ── command parsing helpers ───────────────────────────────────────────────────────


def test_bare_command_and_args_drop_the_bot_suffix() -> None:
    assert tc.bare_command("/Status@MyBot now") == "status"
    assert tc.bare_command("hello") == ""
    assert tc.command_args("/auto_start@MyBot tyler") == "tyler"
    assert tc.command_args("/skill") == ""


async def test_skills_is_not_skill(monkeypatch) -> None:
    channel = TelegramChannel(
        bot_token="123:FAKE", allowed_users=[42], on_message=AsyncMock(return_value="x")
    )
    channel._dispatch_by_mode = AsyncMock()
    channel._handle_skill_cmd = AsyncMock()
    channel.send_message = AsyncMock(return_value={"ok": True})
    channel.send_typing = AsyncMock()
    channel._api_call = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("navig.gateway.channels.telegram.HAS_SESSIONS", False)

    def _msg(text):
        return {"message": {"message_id": 1, "text": text, "chat": {"id": 42, "type": "private"},
                            "from": {"id": 42, "username": "u"}}}

    await channel._process_update(_msg("/skills"))
    channel._handle_skill_cmd.assert_not_awaited()
    await channel._process_update(_msg("/skill@mybot list"))
    channel._handle_skill_cmd.assert_awaited_once()


# ── /auto_start validates the persona ─────────────────────────────────────────────


class _Store:
    def __init__(self):
        self.state = None

    def set_ai_state(self, user_id, chat_id, mode, persona=None, context=None):
        self.state = {"mode": mode, "persona": persona}


async def test_auto_start_refuses_an_unknown_persona(monkeypatch) -> None:
    store = _Store()
    switch = AsyncMock()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)
    monkeypatch.setattr(
        "navig.personas.resolver.discover_persona_paths",
        lambda cwd=None: {"assistant": Path("."), "tyler": Path(".")},
    )
    monkeypatch.setattr("navig.personas.manager.switch_persona", switch)
    channel = _channel()

    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/auto_start@MyBot nosuch", metadata={}
    )
    assert store.state is None
    switch.assert_not_awaited()
    assert "No persona named" in channel.sent[-1]["text"]
    assert "ACTIVATED" not in channel.sent[-1]["text"]
    assert "tyler" in channel.sent[-1]["text"]


async def test_auto_start_strips_the_bot_suffix_and_activates(monkeypatch) -> None:
    store = _Store()
    switch = AsyncMock()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)
    monkeypatch.setattr(
        "navig.personas.resolver.discover_persona_paths",
        lambda cwd=None: {"assistant": Path("."), "tyler": Path(".")},
    )
    monkeypatch.setattr("navig.personas.manager.switch_persona", switch)
    channel = _channel()

    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/auto_start@MyBot Tyler", metadata={}
    )
    assert store.state == {"mode": "active", "persona": "tyler"}
    assert switch.await_args.kwargs["name"] == "tyler"
    assert "ACTIVATED" in channel.sent[-1]["text"]
    assert "/persona" not in channel.sent[-1]["text"]


async def test_auto_start_reports_a_failed_persona_switch(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)
    monkeypatch.setattr(
        "navig.personas.resolver.discover_persona_paths",
        lambda cwd=None: {"assistant": Path(".")},
    )
    monkeypatch.setattr(
        "navig.personas.manager.switch_persona", AsyncMock(side_effect=RuntimeError("boom"))
    )
    channel = _channel()
    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/auto_start assistant", metadata={}
    )
    assert store.state is None
    assert "still off" in channel.sent[-1]["text"]


# ── /explain_ai is honest ─────────────────────────────────────────────────────────


async def test_explain_ai_says_so_when_there_is_no_answer() -> None:
    channel = _channel()
    channel.on_message = AsyncMock(return_value="")
    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/explain_ai DNS", metadata={}
    )
    text = channel.sent[-1]["text"]
    assert "Couldn't generate an explanation" in text
    assert "route" not in text and "AttributeError" not in text


async def test_explain_ai_usage_is_html_not_markdown() -> None:
    channel = _channel()
    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/explain_ai", metadata={}
    )
    msg = channel.sent[-1]
    assert "`" not in msg["text"] and "<code>" in msg["text"]
    assert msg["parse_mode"] == "HTML"


def test_no_markdown_backticks_in_html_usage_strings() -> None:
    """/continue, /explain_ai and /imagegen sent `code` under HTML parse mode."""
    import ast

    tree = ast.parse((CHANNELS / "telegram_commands.py").read_text(encoding="utf-8"))
    strings = [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert len(strings) > 500, "the string scan read nothing"
    for needle in ("`/auto_start", "`/explain_ai", "`/imagegen"):
        hits = [s for s in strings if needle in s]
        assert not hits, f"{needle} is Markdown inside an HTML message: {hits}"


# ── /myreminders honours the AI/Plain toggle ──────────────────────────────────────


async def test_myreminders_goes_through_send_command_output(monkeypatch) -> None:
    channel = _channel()
    seen: dict = {}

    async def send_command_output(chat_id, plain_text, *, command, **kwargs):
        seen["command"] = command
        seen["keyboard"] = kwargs.get("keyboard")

    channel.send_command_output = send_command_output
    monkeypatch.setattr(
        "navig.telegram.reminders_view.build",
        lambda user_id: ("<b>Reminders</b>", {"inline_keyboard": [[{"text": "x", "callback_data": "y"}]]}),
    )
    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/myreminders", metadata={}
    )
    assert seen == {"command": "myreminders", "keyboard": [[{"text": "x", "callback_data": "y"}]]}


# ── stale pointers ────────────────────────────────────────────────────────────────


def test_no_reply_points_at_a_removed_command() -> None:
    """User-facing strings must not send people to /models /providers /persona /agent."""
    stale = re.compile(
        r"Use /models|Use /providers|/settings \\u2192 Providers|/settings → Providers"
        r"|use <code>/persona|`/agent start`"
    )
    for name in ("telegram_commands.py", "telegram.py"):
        src = (CHANNELS / name).read_text(encoding="utf-8")
        hits = [m.group(0) for m in stale.finditer(src)]
        assert not hits, f"{name}: {hits}"


async def test_profile_runs_on_the_real_channel() -> None:
    """/profile delegated to self._handle_user, which the channel once lacked."""
    channel = _channel()
    await channel._dispatch_registry_command(
        chat_id=456, user_id=456, text="/profile", username="u", metadata={}
    )
    assert "User Profile" in channel.sent[-1]["text"]


async def test_bare_auto_start_keeps_the_current_persona_without_switching(monkeypatch) -> None:
    """No name → stay on the current persona (NAVIG by default). Calling switch_persona
    would mark an incidental persona as an explicit choice."""
    store = _Store()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)
    switch = AsyncMock()
    monkeypatch.setattr("navig.personas.manager.switch_persona", switch)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_commands._active_persona_name", lambda uid: "default"
    )
    channel = _channel()
    await channel._dispatch_registry_command(
        chat_id=123, user_id=456, text="/auto_start", metadata={}
    )
    switch.assert_not_called()
    assert store.state is not None and store.state.get("persona") in (None, "default")

