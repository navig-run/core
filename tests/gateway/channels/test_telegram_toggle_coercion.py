"""`navig config set telegram.<x>_enabled false` must actually disable the feature.

`navig config set` stores its argument verbatim as a *string*, and ``bool("false")``
is ``True``. Each Telegram channel feature (checklist / forum-routing / inline-mode /
reactions) reads its toggle from ``cm.get("telegram")`` — so before the fix a raw
``tg.get("reactions_enabled", True)`` returned the string ``"false"``, which is truthy,
and the operator-disabled feature stayed ON with no error. Every toggle now coerces at
the getter (the config boundary) via ``navig.core.coerce.coerce_bool``.

These getters use only the config manager (never ``self``), so we call them unbound
with a dummy self and patch ``navig.config.get_config_manager``.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_checklist import TelegramChecklistMixin
from navig.gateway.channels.telegram_forum import TelegramForumMixin
from navig.gateway.channels.telegram_inline import TelegramInlineMixin
from navig.gateway.channels.telegram_reactions import TelegramReactionsMixin


class _FakeCM:
    """Stand-in config manager whose ``get('telegram')`` returns a fixed dict."""

    def __init__(self, telegram: dict) -> None:
        self._telegram = telegram

    def get(self, key: str, default=None):
        return self._telegram if key == "telegram" else default


# (getter, config-key, default-when-unset)
_TOGGLES = [
    (TelegramChecklistMixin._get_checklist_config, "checklist_enabled", True),
    (TelegramForumMixin._get_forum_config, "forum_routing_enabled", False),
    (TelegramInlineMixin._get_inline_config, "inline_mode_enabled", True),
    (TelegramReactionsMixin._get_reactions_config, "reactions_enabled", True),
]


def _call(monkeypatch, getter, telegram_cfg):
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM(telegram_cfg))
    return getter(None)  # self is unused


@pytest.mark.parametrize("getter,key,default", _TOGGLES)
def test_string_false_disables_the_feature(monkeypatch, getter, key, default):
    # The exact footgun: `navig config set` stored the string "false".
    result = _call(monkeypatch, getter, {key: "false"})
    assert result[key] is False, f"{key}: string 'false' must disable, got {result[key]!r}"


@pytest.mark.parametrize("getter,key,default", _TOGGLES)
def test_string_on_enables_the_feature(monkeypatch, getter, key, default):
    result = _call(monkeypatch, getter, {key: "on"})
    assert result[key] is True, f"{key}: string 'on' must enable, got {result[key]!r}"


@pytest.mark.parametrize("getter,key,default", _TOGGLES)
def test_real_bool_is_preserved(monkeypatch, getter, key, default):
    assert _call(monkeypatch, getter, {key: True})[key] is True
    assert _call(monkeypatch, getter, {key: False})[key] is False


@pytest.mark.parametrize("getter,key,default", _TOGGLES)
def test_unset_returns_the_toggle_default(monkeypatch, getter, key, default):
    assert _call(monkeypatch, getter, {})[key] is default


@pytest.mark.parametrize("getter,key,default", _TOGGLES)
def test_unknown_token_falls_back_to_default_not_truthy(monkeypatch, getter, key, default):
    # An unrecognised string must not silently read as True (the bool("x") trap).
    assert _call(monkeypatch, getter, {key: "maybe"})[key] is default
