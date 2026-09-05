"""A transient/unreadable read of cdp-profiles.json must NOT wipe the named-profile registry.

create_profile/create_real_profile add a profile UNCONDITIONALLY, so they load through
`_read_for_update` (json_io's `load_json_for_update`, which RAISES JsonReadError on a
transiently-unreadable-but-populated file) and abort — returning None — instead of the old
`_read` that returned {} on a lock and let `_write` persist a single-profile registry over
every other profile. The `if name in node`-guarded mutators (set_active/remove_profile/…)
already no-op on an empty read, so they never wipe. `_write` is now atomic too.
"""

from __future__ import annotations

import pytest

import navig.core.json_io as jio
from navig.browser import profiles as p

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    yield tmp_path


def _lock(monkeypatch):
    def _locked(*_a, **_k):
        raise OSError("file is locked")
    monkeypatch.setattr(jio, "read_text_retrying", _locked)


def test_create_profile_is_additive(cfg):
    p.create_profile("work")
    p.create_profile("personal")
    assert {pr.name for pr in p.list_profiles()} == {"work", "personal"}


def test_create_during_lock_returns_none_without_wiping(cfg, monkeypatch):
    p.create_profile("work")
    p.create_profile("personal")
    reg = p.registry_path()
    before = reg.read_text(encoding="utf-8")
    assert "work" in before and "personal" in before

    _lock(monkeypatch)
    # The unguarded add aborts (returns None) instead of wiping the two existing profiles.
    assert p.create_profile("scratch") is None

    # Registry intact on disk (read directly — Path.read_text, not the patched json_io).
    assert reg.read_text(encoding="utf-8") == before


def test_guarded_mutators_and_readers_are_safe_on_lock(cfg, monkeypatch):
    p.create_profile("work")
    reg = p.registry_path()
    before = reg.read_text(encoding="utf-8")

    _lock(monkeypatch)
    # Read-only paths degrade (empty), never crash.
    assert p.list_profiles() == []
    assert p.get_profile("work") is None
    # Guarded mutators no-op on the empty read → return falsy, never write.
    assert p.set_active("work") is False
    assert p.remove_profile("work") is False
    assert p.set_default_account("work", "a@b.com") is False

    # Nothing was written — the profile survives.
    assert reg.read_text(encoding="utf-8") == before
