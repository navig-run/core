"""The cron executor must actually CALL the reminder localizer.

`localized_reminder` can be perfectly correct and perfectly unreachable — the
resolver having tests proves nothing about whether anything invokes it. This is
the "written, tested, never wired" class, and the habit reminders are exactly
where it would hide: the operator would keep receiving English every weekend
while a green test suite said the translation worked.

So these drive `_execute_job_command` itself and assert on the message that
reaches the store.
"""

from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.integration

CHAT = 4242


def _cmd(message: str) -> str:
    return f"NAVIG_HABIT_REMINDER:{CHAT}:{base64.b64encode(message.encode()).decode()}"


@pytest.fixture
def queued(monkeypatch):
    """Capture what would be written to the reminder store."""
    captured: list[str] = []

    class _Store:
        def create_reminder(self, *, user_id, chat_id, message, remind_at):
            captured.append(message)

    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: _Store())
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_extensions.is_enabled", lambda _e: True
    )
    return captured


async def _run(command: str, job_name: str) -> None:
    import types

    from navig.scheduler.cron_service import CronService

    service = CronService.__new__(CronService)
    job = types.SimpleNamespace(name=job_name, command=command)
    await CronService._execute_job_command(service, job)


def _speak(monkeypatch, lang: str) -> None:
    from navig.core import i18n

    monkeypatch.setattr(i18n, "current_language", lambda: lang)
    i18n.shared().reset()


async def test_the_executor_localizes_a_builtin_reminder(queued, monkeypatch):
    """The whole point: the text was frozen in English at `habit add` time."""
    _speak(monkeypatch, "ru")
    from navig.spaces.health import BUILTIN_HABITS

    english = BUILTIN_HABITS["out"].reminder_message
    await _run(_cmd(english), "habit:out")

    assert queued, "nothing was queued"
    assert queued[0] != english, "the executor never called the localizer"
    assert any("Ѐ" <= ch <= "ӿ" for ch in queued[0]), "expected Cyrillic"


async def test_the_executor_leaves_a_custom_message_alone(queued, monkeypatch):
    _speak(monkeypatch, "ru")
    mine = "Мой текст"
    await _run(_cmd(mine), "habit:out")
    assert queued == [mine]


async def test_english_stays_english_when_that_is_the_language(queued, monkeypatch):
    _speak(monkeypatch, "en")
    from navig.spaces.health import BUILTIN_HABITS

    english = BUILTIN_HABITS["out"].reminder_message
    await _run(_cmd(english), "habit:out")
    assert queued == [english]


async def test_a_localizer_failure_still_delivers_the_reminder(queued, monkeypatch):
    """A translation problem must never turn into a missed reminder."""
    from navig.spaces import health

    def boom(*_a, **_k):
        raise RuntimeError("locale on fire")

    monkeypatch.setattr(health, "localized_reminder", boom)
    from navig.spaces.health import BUILTIN_HABITS

    english = BUILTIN_HABITS["out"].reminder_message
    with pytest.raises(RuntimeError):
        # The executor wraps failures into RuntimeError rather than swallowing
        # them — a reminder that vanished silently would be worse.
        await _run(_cmd(english), "habit:out")


async def test_the_extension_gate_still_blocks_before_any_of_this(monkeypatch):
    captured: list[str] = []

    class _Store:
        def create_reminder(self, **_kw):
            captured.append("written")

    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: _Store())
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_extensions.is_enabled", lambda _e: False
    )
    await _run(_cmd("anything"), "habit:out")
    assert captured == [], "a switched-off extension must write nothing"
