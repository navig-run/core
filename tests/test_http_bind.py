"""Tests for navig.http_bind.

The bug these lock down: Windows reserves whole TCP port ranges, so several navig
defaults (8770 for `explore open` and `media browse`) sat inside a reserved block on a
real machine and those commands could never start — the bind failed with WinError 10013,
which reads like a permissions problem rather than "pick another number".
"""
from __future__ import annotations

import pytest

from navig.http_bind import PortBindError, bind_http_server


class _Fake:
    """Stands in for ThreadingHTTPServer; refuses every port in ``reserved``."""

    reserved: set[int] = set()
    attempts: list[int] = []

    def __init__(self, addr, _handler):
        type(self).attempts.append(addr[1])
        if addr[1] in type(self).reserved:
            raise OSError(10013, "An attempt was made to access a socket in a way forbidden")
        # port 0 means "OS picks one"
        self.server_address = (addr[0], 54321 if addr[1] == 0 else addr[1])


@pytest.fixture
def fake():
    _Fake.reserved, _Fake.attempts = set(), []
    return _Fake


def test_uses_the_preferred_port_when_it_is_free(fake):
    _srv, port = bind_http_server(object(), None, preferred=8770, server_cls=fake)
    assert port == 8770
    assert fake.attempts == [8770]


def test_falls_back_to_an_os_assigned_port_when_preferred_is_reserved(fake):
    fake.reserved = {8770}
    _srv, port = bind_http_server(object(), None, preferred=8770, server_cls=fake)
    assert fake.attempts == [8770, 0]   # tried the familiar default first
    assert port == 54321                # ...then took whatever the OS gave


def test_an_explicit_port_is_never_silently_swapped(fake):
    """Sending the user to a different port than they asked for is worse than failing."""
    fake.reserved = {9000}
    with pytest.raises(PortBindError) as err:
        bind_http_server(object(), 9000, preferred=8770, server_cls=fake)
    assert fake.attempts == [9000]      # no fallback attempted
    assert "9000" in str(err.value)


def test_error_message_suggests_a_way_out(fake):
    fake.reserved = {9000}
    with pytest.raises(PortBindError, match="--port"):
        bind_http_server(object(), 9000, server_cls=fake)


def test_host_is_passed_through(fake):
    captured = {}

    class Recorder(_Fake):
        def __init__(self, addr, handler):
            captured["host"] = addr[0]
            super().__init__(addr, handler)

    bind_http_server(object(), None, host="0.0.0.0", server_cls=Recorder)
    assert captured["host"] == "0.0.0.0"
