"""Turning voice replies on from the Deck must actually turn them on.

Three surfaces set this, and they used to disagree about WHERE:

    /voiceon in chat        -> session metadata   (per chat)   <- the only one ever read
    the Deck audio toggle   -> AudioConfig        (per user)
    "turn on voice replies" -> AudioConfig        (per user)

`_maybe_send_voice` read session metadata only, so flipping the Deck switch changed nothing.
Worse, it LOOKED applied: the Deck renders the toggle from its own store, so it read back ON.

The fix is tri-state rather than picking a winner, because the two stores mean different
things: a per-chat override wins where it exists, and the per-user preference is the default
underneath it. `False` as the metadata default could not express that -- it makes "explicitly
off" and "never set" the same value.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


def _channel():
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel.__new__(TelegramChannel)
    ch._prepare_for_tts = lambda text: ""      # stop right after the gate; we assert on reaching it
    return ch


_UNSET = object()


def _run(ch, *, session_value, audio_value):
    """Drive _maybe_send_voice and report whether it passed the enabled-gate.

    ⚠ The session-store stub HONOURS the `default` the caller passes, because that default
    is exactly what this fix changed (False -> None). A stub with a fixed `return_value`
    ignores it, and then reverting the fix still passes every test here -- which is what the
    first version of this file did. A test that cannot fail is worse than no test.
    """
    reached = {"tts": False}

    def _prep(text):
        reached["tts"] = True
        return ""                              # returning empty stops before any TTS work

    ch._prepare_for_tts = _prep

    class _Sessions:
        @staticmethod
        def get_session_metadata(chat_id, user_id, key, default=None, **kw):
            # "never set" means the caller's own default comes back, as a real store does.
            return default if session_value is _UNSET else session_value

    cfg = MagicMock()
    cfg.voice_replies_enabled = audio_value

    with patch("navig.gateway.channels.telegram.get_session_manager", return_value=_Sessions):
        with patch("navig.gateway.channels.audio_menu.state.load_config", return_value=cfg):
            asyncio.run(ch._maybe_send_voice(1, 2, False, "hello"))
    return reached["tts"]


def test_the_deck_toggle_alone_now_enables_voice_replies():
    """The bug: nothing had ever read the store the Deck writes."""
    assert _run(_channel(), session_value=_UNSET, audio_value=True) is True, (
        "with no per-chat override, the per-user preference must apply — otherwise the Deck "
        "switch is decorative"
    )


def test_nothing_set_anywhere_stays_off():
    """Default OFF — never surprise a user with audio they did not enable."""
    assert _run(_channel(), session_value=_UNSET, audio_value=False) is False


def test_a_per_chat_override_wins_over_the_per_user_preference():
    """/voiceoff in one chat must beat a global 'on', or the override is meaningless."""
    assert _run(_channel(), session_value=False, audio_value=True) is False, (
        "an explicit per-chat OFF was overridden by the global preference"
    )


def test_a_per_chat_on_wins_over_a_global_off():
    assert _run(_channel(), session_value=True, audio_value=False) is True


def test_explicitly_off_is_not_confused_with_never_set():
    """The whole reason the metadata default became None rather than False."""
    never_set = _run(_channel(), session_value=_UNSET, audio_value=True)
    explicit_off = _run(_channel(), session_value=False, audio_value=True)
    assert never_set is True and explicit_off is False, (
        "these two must differ; with a False default they are the same value and the "
        "per-user preference can never be reached"
    )
