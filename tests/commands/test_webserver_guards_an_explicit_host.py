"""A web mutation claims the host lock whether the host was explicit or inferred.

The guard used to sit inside `if not host_name:` — so `navig web enable-site --host vps`
(or the interactive wrapper, which always passes a host) mutated the server without ever
asking `host_lock`. These drive each mutating function with `options["host"]` set and
stop at the guard.
"""

from __future__ import annotations

import pytest

from navig.commands import webserver
from navig.core import host_lock


class _Claimed(Exception):
    pass


@pytest.mark.parametrize(
    ("func", "extra", "command"),
    [
        ("enable_site", {"site_name": "example"}, "navig web enable-site"),
        ("disable_site", {"site_name": "example"}, "navig web disable-site"),
        ("enable_module", {"module_name": "rewrite"}, "navig web enable-module"),
        ("disable_module", {"module_name": "rewrite"}, "navig web disable-module"),
        ("reload_server", {}, "navig web reload"),
    ],
)
def test_an_explicit_host_is_guarded(monkeypatch, func: str, extra: dict, command: str) -> None:
    calls: list[tuple[str, str]] = []

    def fake_guard(_cm, host, cmd=None):
        calls.append((host, cmd))
        raise _Claimed

    monkeypatch.setattr(webserver, "get_config_manager", lambda: object())
    monkeypatch.setattr(webserver, "RemoteOperations", lambda _cm: object())
    monkeypatch.setattr(host_lock, "guard_remote", fake_guard)

    with pytest.raises(_Claimed):
        getattr(webserver, func)({"host": "vps", "app": "site", **extra})
    assert calls == [("vps", command)]
