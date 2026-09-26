"""The delegated voice helpers must actually resolve on TelegramChannel.

``TelegramChannel`` does **not** inherit ``TelegramVoiceMixin`` — its runtime MRO is
``[TelegramChannel, object]``, and the "mixins" named in the module docstrings are
TYPE_CHECKING prose. The one live mixin method is reached by an explicit unbound call
(``TelegramVoiceMixin._transcribe_audio_file(self, ...)``, the documented BUG-22 fix).

That pattern has a trap: every ``self._helper()`` **inside** the mixin then resolves against
TelegramChannel, not against the mixin. Two helpers were missing, so the Telegram transcribe
action failed silently — the mixin caught the AttributeError, logged it at WARN, and returned
nothing:

    WARN  _transcribe_audio_file: get_file_path failed:
          'TelegramChannel' object has no attribute '_get_file_path'

A second, independent mismatch sat behind it: the mixin reads ``self._bot_token`` while this
class stores ``self.bot_token``, so merely delegating ``_build_file_url`` would resolve the
method and fail one step later on the attribute.

Both are name-level mismatches that no import check can see: every file is internally
consistent, and only the cross-object call reveals them.
"""

from __future__ import annotations

import asyncio

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_voice import TelegramVoiceMixin


def _bare_channel(token: str = "TESTTOKEN") -> TelegramChannel:
    """A channel without __init__ — attribute *resolution* is what these tests are about."""
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.bot_token = token
    return ch


def test_the_channel_does_not_inherit_the_voice_mixin():
    """Pins the premise. If this ever changes, the stubs below become redundant."""
    assert TelegramVoiceMixin not in TelegramChannel.__mro__
    assert [c.__name__ for c in TelegramChannel.__mro__] == ["TelegramChannel", "object"]


def test_the_helpers_the_delegated_mixin_reaches_for_exist():
    ch = _bare_channel()
    for name in ("_get_file_path", "_build_file_url"):
        assert hasattr(ch, name), (
            f"TelegramChannel has no {name}; the mixin calls self.{name} when "
            f"_transcribe_audio_file is delegated to it, and the failure is swallowed"
        )


def test_build_file_url_uses_this_class_attribute_not_the_mixins():
    """The mixin reads ``_bot_token``; this class stores ``bot_token``.

    Delegating would resolve the method and then AttributeError on the attribute, which is a
    strictly more confusing failure than the original.
    """
    ch = _bare_channel("ABC123")
    assert ch._build_file_url("voice/f.oga") == (
        "https://api.telegram.org/file/botABC123/voice/f.oga"
    )
    assert not hasattr(ch, "_bot_token"), (
        "if this class grows a _bot_token, revisit _build_file_url -- the two names being "
        "different is the whole reason it is not delegated"
    )


def test_the_full_delegated_chain_resolves():
    """file_id -> file_path -> download URL, the path the transcribe action takes."""
    ch = _bare_channel("TOK")

    async def fake_api_call(method, params=None):
        return {"file_path": "voice/file_1.oga"} if method == "getFile" else None

    ch._api_call = fake_api_call

    async def run():
        path = await ch._get_file_path("FILEID")
        return path, ch._build_file_url(path)

    path, url = asyncio.run(run())
    assert path == "voice/file_1.oga"
    assert url == "https://api.telegram.org/file/botTOK/voice/file_1.oga"


def test_delegation_no_longer_dies_on_a_missing_attribute():
    """Drive it the way telegram.py's stub does, and assert the old failure is gone."""
    ch = _bare_channel()

    async def fake_api_call(method, params=None):
        return {"file_path": "voice/x.oga"} if method == "getFile" else None

    ch._api_call = fake_api_call

    async def run():
        try:
            await ch._get_file_path("FILEID")
        except AttributeError as exc:  # the exact original symptom
            return str(exc)
        return None

    err = asyncio.run(run())
    assert err is None, f"still unresolvable: {err}"
