"""
Marketplace registry — add/list/remove a local marketplace, resolve a plugin, and
prove a bad manifest is rejected before it's registered. All offline (local dirs).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.plugins.marketplace import (
    MarketplaceStore,
    fetch_marketplace,
    parse_marketplace_manifest,
)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _marketplace_dir(root: Path) -> Path:
    """A local marketplace advertising one CC plugin that lives beside it."""
    _write(root / ".claude-plugin" / "marketplace.json", json.dumps({
        "name": "acme",
        "plugins": [
            {"name": "telegram", "source": "./plugins/telegram",
             "description": "Telegram manager", "version": "1.2.0"},
        ],
    }))
    # the advertised plugin (a plain CC plugin) — used by the install-resolution test
    _write(root / "plugins" / "telegram" / ".claude-plugin" / "plugin.json",
           json.dumps({"name": "telegram", "version": "1.2.0"}))
    _write(root / "plugins" / "telegram" / "commands" / "send.md", "# send")
    return root


def test_parse_and_fetch_local(tmp_path):
    mkt = fetch_marketplace(str(_marketplace_dir(tmp_path / "acme")))
    assert mkt.name == "acme"
    assert len(mkt.entries) == 1
    e = mkt.entries[0]
    assert e.name == "telegram" and e.version == "1.2.0" and e.marketplace == "acme"


def test_add_list_remove_roundtrip(tmp_path):
    plugins_dir = tmp_path / "plugins_dir"
    market = _marketplace_dir(tmp_path / "acme")
    store = MarketplaceStore(plugins_dir=plugins_dir)

    mkt = store.add(str(market))
    assert mkt.name == "acme"
    assert (plugins_dir / "marketplaces.json").exists()

    rows = store.list_marketplaces()
    assert [m.name for m in rows] == ["acme"]

    assert store.remove("acme") is True
    assert store.list_marketplaces() == []
    assert store.remove("acme") is False          # already gone


def test_add_is_idempotent(tmp_path):
    market = _marketplace_dir(tmp_path / "acme")
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    store.add(str(market))
    store.add(str(market))                          # same name, replace not duplicate
    assert len(store.list_marketplaces()) == 1


def test_resolve_finds_plugin(tmp_path):
    market = _marketplace_dir(tmp_path / "acme")
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    store.add(str(market))

    resolved = store.resolve("telegram")
    assert resolved is not None
    mkt, entry = resolved
    assert entry.name == "telegram" and mkt.name == "acme"
    assert store.resolve("nonexistent") is None


def test_bad_manifest_rejected(tmp_path):
    bad = tmp_path / "bad"
    _write(bad / "marketplace.json", "{ not json")
    store = MarketplaceStore(plugins_dir=tmp_path / "pd")
    with pytest.raises(Exception):
        store.add(str(bad))
    assert store.list_marketplaces() == []          # nothing registered on failure


def test_corrupt_registry_is_ignored(tmp_path):
    plugins_dir = tmp_path / "pd"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "marketplaces.json").write_text("{ corrupt", encoding="utf-8")
    store = MarketplaceStore(plugins_dir=plugins_dir)
    assert store.list_marketplaces() == []          # tolerated, not fatal
