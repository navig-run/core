"""Per-space pinned apps (`app_allowlist`) — the desktop sidebar view-filter.

Covers the P3 slice of the deck→desktop apps migration (locked 2026-07-09):
- `SpaceManifest.app_allowlist` (empty/absent = all; order preserved)
- `_space_card()` emitting `app_allowlist` + `counts.apps` on /spaces/scan
- `POST /api/deck/spaces/{id}/apps` writing the manifest (unknown keys kept,
  bare `.navig/` bootstrapped, YAML refused, bad bodies rejected)

The allow-list is a VIEW-FILTER over globally-enabled modules — these tests
also pin that it never touches module enablement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

import navig.gateway.deck.routes.catalog as cat
from navig.spaces.space_manifest import load_space_manifest


@dataclass
class _Cfg:
    path: str
    scope: str = "global"


class _Req:
    """Stand-in for aiohttp.web.Request (match_info + json body)."""

    def __init__(self, sid: str, body: object = None):
        self.match_info = {"id": sid}
        self._body = body

    async def json(self):
        return self._body


def _mk_space(tmp_path: Path, manifest: dict | None) -> Path:
    space = tmp_path / "demo-space"
    (space / ".navig").mkdir(parents=True)
    if manifest is not None:
        (space / ".navig" / "space.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    return space


def _patch_discovery(monkeypatch, space: Path, sid: str = "demo-space") -> None:
    monkeypatch.setattr(
        "navig.spaces.resolver.discover_space_paths",
        lambda include_disabled=True: {sid: _Cfg(path=str(space))},
    )


# ── manifest property ─────────────────────────────────────────────────────────

def test_app_allowlist_absent_means_all(tmp_path):
    space = _mk_space(tmp_path, {"id": "demo-space"})
    assert load_space_manifest(space).app_allowlist == []


def test_app_allowlist_preserves_order_and_normalizes(tmp_path):
    space = _mk_space(
        tmp_path,
        {"id": "demo-space", "apps": ["finance", " deploys ", {"id": "database"}, 7]},
    )
    # order kept; strings stripped; dict entries resolve by id; junk dropped
    assert load_space_manifest(space).app_allowlist == ["finance", "deploys", "database"]


# ── /spaces/scan card ─────────────────────────────────────────────────────────

def test_space_card_carries_allowlist_and_count(tmp_path):
    space = _mk_space(tmp_path, {"id": "demo-space", "apps": ["life", "health"]})
    card = cat._space_card("demo-space", _Cfg(path=str(space)), active_path=None)
    assert card["app_allowlist"] == ["life", "health"]
    assert card["counts"]["apps"] == 2


def test_space_card_empty_allowlist_for_bare_space(tmp_path):
    space = _mk_space(tmp_path, None)  # bare .navig/, no manifest
    card = cat._space_card("demo-space", _Cfg(path=str(space)), active_path=None)
    assert card["app_allowlist"] == []
    assert card["counts"]["apps"] == 0


# ── POST /api/deck/spaces/{id}/apps ───────────────────────────────────────────

async def test_write_pins_and_preserves_unknown_keys(tmp_path, monkeypatch):
    space = _mk_space(
        tmp_path, {"id": "demo-space", "theme": {"accent": "teal"}, "skills": ["ssh"]}
    )
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_apps(_Req("demo-space", {"apps": ["finance", "deploys"]}))
    assert resp.status == 200

    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["apps"] == ["finance", "deploys"]
    assert data["theme"] == {"accent": "teal"}      # unknown keys survive
    assert data["skills"] == ["ssh"]                 # sibling allow-lists untouched
    assert load_space_manifest(space).app_allowlist == ["finance", "deploys"]


async def test_write_empty_list_means_show_all(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space", "apps": ["finance"]})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_apps(_Req("demo-space", {"apps": []}))
    assert resp.status == 200
    assert load_space_manifest(space).app_allowlist == []


async def test_write_bootstraps_bare_space(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, None)  # no manifest at all
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_apps(_Req("demo-space", {"apps": ["system"]}))
    assert resp.status == 200
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["id"] == "demo-space"
    assert data["apps"] == ["system"]


async def test_write_rejects_bad_bodies(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space"})
    _patch_discovery(monkeypatch, space)

    for body in ({}, {"apps": "finance"}, {"apps": [1, 2]}, {"apps": [""]}):
        resp = await cat.handle_deck_space_apps(_Req("demo-space", body))
        assert resp.status == 400, f"body {body!r} should be rejected"


async def test_write_unknown_space_404(tmp_path, monkeypatch):
    _patch_discovery(monkeypatch, _mk_space(tmp_path, None))
    resp = await cat.handle_deck_space_apps(_Req("other-space", {"apps": ["x"]}))
    assert resp.status == 404


async def test_write_refuses_yaml_manifest(tmp_path, monkeypatch):
    space = tmp_path / "yaml-space"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "space.yaml").write_text("id: yaml-space\n", encoding="utf-8")
    _patch_discovery(monkeypatch, space, sid="yaml-space")

    resp = await cat.handle_deck_space_apps(_Req("yaml-space", {"apps": ["x"]}))
    assert resp.status == 409


# ── POST /api/deck/spaces/{id}/books (sibling of /apps, same safe writer) ──────

async def test_books_write_sets_and_preserves_keys(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space", "theme": {"accent": "teal"}, "apps": ["finance"]})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_books(_Req("demo-space", {"books": " Company "}))
    assert resp.status == 200
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["books"] == "Company"          # trimmed
    assert data["theme"] == {"accent": "teal"}  # unknown keys survive
    assert data["apps"] == ["finance"]          # sibling allow-list untouched
    assert load_space_manifest(space).books == "Company"


async def test_books_write_null_clears(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space", "books": "Company", "keep": 1})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_books(_Req("demo-space", {"books": None}))
    assert resp.status == 200
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert "books" not in data          # cleared, not written as null
    assert data["keep"] == 1
    assert load_space_manifest(space).books is None


async def test_books_write_empty_string_clears(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space", "books": "Company"})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_books(_Req("demo-space", {"books": "   "}))
    assert resp.status == 200
    assert load_space_manifest(space).books is None


async def test_books_write_bootstraps_bare_space(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, None)  # bare .navig/
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_books(_Req("demo-space", {"books": "Personal"}))
    assert resp.status == 200
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["id"] == "demo-space" and data["books"] == "Personal"


async def test_books_write_rejects_bad_bodies(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space"})
    _patch_discovery(monkeypatch, space)

    for body in ({}, {"books": 7}, {"books": ["x"]}, {"books": {"a": 1}}):
        resp = await cat.handle_deck_space_books(_Req("demo-space", body))
        assert resp.status == 400, f"body {body!r} should be rejected"


async def test_books_write_unknown_space_404(tmp_path, monkeypatch):
    _patch_discovery(monkeypatch, _mk_space(tmp_path, None))
    resp = await cat.handle_deck_space_books(_Req("other-space", {"books": "X"}))
    assert resp.status == 404


async def test_books_write_refuses_yaml_manifest(tmp_path, monkeypatch):
    space = tmp_path / "yaml-space"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "space.yaml").write_text("id: yaml-space\n", encoding="utf-8")
    _patch_discovery(monkeypatch, space, sid="yaml-space")

    resp = await cat.handle_deck_space_books(_Req("yaml-space", {"books": "X"}))
    assert resp.status == 409
