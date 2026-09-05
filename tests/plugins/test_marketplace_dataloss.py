"""A transient/unreadable read of the marketplaces registry must NOT wipe registered
marketplaces. The mutating methods (add/remove/refresh) load through `_load_for_update`,
which routes to json_io's `load_json_for_update` and RAISES `JsonReadError` on a file that
exists-with-content but is transiently unreadable (a Windows AV/backup lock). `add`
propagates it (its CLI is wrapped); `remove`/`refresh` degrade internally (unwrapped
callers / "never raises" contract). The old `_load` returned `{"marketplaces": []}` on that
lock, so add/remove then rewrote marketplaces.json with only the changed row — wiping the
rest. `_save` is now atomic too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import navig.core.json_io as jio
from navig.plugins.marketplace import MarketplaceStore

pytestmark = pytest.mark.integration


def _seed_two(store: MarketplaceStore) -> None:
    store._save({"marketplaces": [
        {"name": "acme", "url": "https://x/acme", "entries": []},
        {"name": "beta", "url": "https://x/beta", "entries": []},
    ]})


def _market_dir(root: Path, name: str) -> Path:
    p = root / ".claude-plugin" / "marketplace.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": name, "plugins": []}), encoding="utf-8")
    return root


def _lock(monkeypatch):
    def _locked(*_a, **_k):
        raise OSError("file is locked")
    monkeypatch.setattr(jio, "read_text_retrying", _locked)


def test_save_round_trips_and_is_atomic(tmp_path):
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    _seed_two(store)
    assert {m.name for m in store.list_marketplaces()} == {"acme", "beta"}


def test_add_during_lock_raises_without_wiping(monkeypatch, tmp_path):
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    _seed_two(store)
    reg = store.registry_path
    before = reg.read_text(encoding="utf-8")

    _lock(monkeypatch)
    # add() fetches the (local) manifest, then _load_for_update raises → propagates.
    with pytest.raises(jio.JsonReadError):
        store.add(str(_market_dir(tmp_path / "gamma", "gamma")))
    assert reg.read_text(encoding="utf-8") == before  # acme + beta intact, no gamma


def test_remove_during_lock_returns_false_without_wiping(monkeypatch, tmp_path):
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    _seed_two(store)
    reg = store.registry_path
    before = reg.read_text(encoding="utf-8")

    _lock(monkeypatch)
    assert store.remove("acme") is False           # degrades, does not raise or wipe
    assert reg.read_text(encoding="utf-8") == before


def test_refresh_during_lock_reports_without_wiping(monkeypatch, tmp_path):
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    _seed_two(store)
    reg = store.registry_path
    before = reg.read_text(encoding="utf-8")

    _lock(monkeypatch)
    results = store.refresh()                       # "never raises"
    assert results and "unreadable" in results[0][1]
    assert reg.read_text(encoding="utf-8") == before
