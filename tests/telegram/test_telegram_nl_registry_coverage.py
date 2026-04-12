import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def _nl_bot():
    mod = pytest.importorskip("navig.gateway.channels.telegram_commands")
    mixin = mod.TelegramCommandsMixin

    class DummyTelegram(mixin):
        async def send_message(self, *args, **kwargs):
            return {"ok": True}

        async def edit_message(self, *args, **kwargs):
            return {"ok": True}

        async def _api_call(self, *args, **kwargs):
            return {"ok": True}

    return DummyTelegram()


def _sample_phrase(bot, entry):
    alias_map = bot._nl_phrase_aliases()
    aliases = alias_map.get(entry.command, ())
    phrase = aliases[0] if aliases else entry.command.replace("_", " ")

    verb = "show"
    if entry.command in bot._NL_RISKY_COMMANDS:
        verb = "run"

    text = f"please {verb} {phrase}".strip()
    if entry.command in bot._NL_REQUIRED_ARGS_COMMANDS:
        text += " demo"
    return text


def test_visible_registry_commands_have_nl_resolution_coverage(_nl_bot):
    mod = pytest.importorskip("navig.gateway.channels.telegram_commands")
    visible_entries = mod._iter_unique_registry(visible_only=True)

    failures = []
    for entry in visible_entries:
        text = _sample_phrase(_nl_bot, entry)
        resolved = _nl_bot._resolve_nl_command_intent(text)
        if not resolved:
            failures.append((entry.command, text, "no_intent"))
            continue
        if resolved.get("ambiguous"):
            candidates = [
                str(item.get("command") or "")
                for item in (resolved.get("candidates") or [])
                if isinstance(item, dict)
            ]
            if entry.command in candidates:
                continue
            failures.append((entry.command, text, f"ambiguous:{candidates}"))
            continue
        resolved_cmd = str(resolved.get("command") or "")
        if resolved_cmd != entry.command:
            failures.append((entry.command, text, f"resolved_as:{resolved_cmd}"))

    assert not failures, f"NL coverage mismatches: {failures}"


def test_required_arg_commands_set_missing_args_without_arguments(_nl_bot):
    for command in sorted(_nl_bot._NL_REQUIRED_ARGS_COMMANDS):
        resolved = _nl_bot._resolve_nl_command_intent(f"please show {command.replace('_', ' ')}")
        if not resolved or str(resolved.get("command") or "") != command:
            continue
        assert resolved.get("missing_args") is True
