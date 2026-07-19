"""
Isolate the SHARED key layer (env/vault/auth-profiles) for all provider tests.

The connection system reads/writes provider API keys through a shared store
(`AuthProfileManager`) that lives on the real machine. Without isolation, tests
would (a) see the developer's real configured providers bleed in as virtual
connections, and (b) actually mutate real auth profiles via `navig connect add`.

This autouse fixture redirects `_resolve_auth` / `_save_shared_key` /
`_remove_shared_key` to an in-memory dict so every test starts with NO configured
providers and never touches real state. Tests that want configured providers
override `_resolve_auth` (or populate the returned dict).
"""

from __future__ import annotations

import pytest

from navig.providers import connect as _connect


@pytest.fixture(autouse=True)
def isolate_shared_auth(monkeypatch):
    shared: dict[str, str] = {}
    monkeypatch.setattr(
        _connect, "_resolve_auth",
        lambda pid: (shared[pid], "test") if pid in shared else (None, None),
    )
    monkeypatch.setattr(_connect, "_save_shared_key",
                        lambda pid, key: shared.__setitem__(pid, key))
    monkeypatch.setattr(_connect, "_remove_shared_key",
                        lambda pid: shared.pop(pid, None))
    return shared
