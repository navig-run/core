"""A named-but-unverifiable active host must stop a Telegram command, not run it here.

`get_active_host()` returns ``None`` both for "no host configured" and for "the host the
operator named could not be verified right now" (a transient stat failure while another
process rewrites the config). The Telegram handlers did ``get_active_host() or
"localhost"``, so the second case silently listed and read the GATEWAY machine's files
under a remote host's name. The fallback to this machine is only for "nothing was named".
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_commands import (
    ActiveHostUnverifiable,
    TelegramCommandsMixin,
)


class _Cfg:
    def __init__(self, source: str) -> None:
        self.source = source
        self.loaded: list[str] = []

    def get_active_host(self, return_source: bool = False):
        host = None if self.source in ("unresolvable", "none") else "vps-1"
        return (host, self.source) if return_source else host

    def load_server_config(self, name: str) -> dict:
        self.loaded.append(name)
        return {"is_local": True}


class _Bot(TelegramCommandsMixin):
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.sent.append(text)
        return {"message_id": 1}

    async def edit_message(self, *a, **kw):
        return None

    async def _api_call(self, *a, **kw):
        return {}


@pytest.fixture
def cfg(monkeypatch):
    def _install(source: str) -> _Cfg:
        fake = _Cfg(source)
        monkeypatch.setattr("navig.config.get_config_manager", lambda: fake)
        return fake

    return _install


def test_the_helper_refuses_only_an_unverifiable_named_host(cfg):
    assert TelegramCommandsMixin._active_host_name(cfg("env")) == "vps-1"
    assert TelegramCommandsMixin._active_host_name(cfg("none")) == "localhost"
    with pytest.raises(ActiveHostUnverifiable):
        TelegramCommandsMixin._active_host_name(cfg("unresolvable"))


def test_monitor_cards_do_not_fall_back_to_this_machine(cfg):
    fake = cfg("unresolvable")
    with pytest.raises(ActiveHostUnverifiable):
        TelegramCommandsMixin._mon_host_ctx()
    assert fake.loaded == []


@pytest.mark.parametrize(
    ("handler", "text"),
    [
        ("_handle_files_cmd", "/files /"),
        ("_handle_cat_cmd", "/cat /etc/hostname"),
        ("_handle_host_test_cmd", None),
        ("_handle_disk_cmd", None),
    ],
)
async def test_a_command_stops_with_an_error(cfg, handler, text):
    fake = cfg("unresolvable")
    bot = _Bot()
    kwargs = {"chat_id": 42, "user_id": 0, "metadata": None}
    if text is not None:
        kwargs["text"] = text

    await getattr(bot, handler)(**kwargs)

    assert fake.loaded == [], "it went on to resolve a host config — it did not stop"
    assert any("could not be verified" in m for m in bot.sent), bot.sent
    assert not any("📂" in m for m in bot.sent), "it listed this machine's files"
