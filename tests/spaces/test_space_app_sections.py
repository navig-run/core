"""Per-space app SECTION layouts (`app_sections`) — the sidebar's second-level
view-filter.

`app_allowlist` decides WHICH apps a space shows; this decides which SECTIONS
each of those apps shows, and in what order. Same contract one level down:

- `SpaceManifest.app_sections` (absent/malformed ⇒ {}; order preserved)
- `_space_card()` emitting `app_sections` on /spaces/scan
- `POST /api/deck/spaces/{id}/app-sections` writing the manifest (unknown keys
  kept, bare `.navig/` bootstrapped, YAML refused, bad bodies rejected)

Two properties are load-bearing and easy to regress:

1. **Malformed content is dropped, never raised on.** The manifest is a file a
   human can edit; a typo there must degrade the sidebar's ordering, not break
   the sidebar. So the READ path filters junk silently even though the WRITE
   path rejects it with a 400 — a hand-edit never goes through the route.
2. **It stays a view-filter.** Section ids are opaque to the daemon; it must not
   validate them against anything, because the registry that owns them lives in
   the desktop app and moves independently of core.
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
        (space / ".navig" / "space.json").write_text(json.dumps(manifest), encoding="utf-8")
    return space


def _patch_discovery(monkeypatch, space: Path, sid: str = "demo-space") -> None:
    monkeypatch.setattr(
        "navig.spaces.resolver.discover_space_paths",
        lambda include_disabled=True: {sid: _Cfg(path=str(space))},
    )


# ── manifest property ─────────────────────────────────────────────────────────

def test_app_sections_absent_means_registry_order(tmp_path):
    space = _mk_space(tmp_path, {"id": "demo-space"})
    assert load_space_manifest(space).app_sections == {}


def test_app_sections_preserves_order_and_hidden(tmp_path):
    space = _mk_space(
        tmp_path,
        {
            "id": "demo-space",
            "app_sections": {
                "finance": {"order": ["Ledger", "Overview"], "hidden": ["Leaks"]},
                "system": {"hidden": ["Ports"]},
            },
        },
    )
    assert load_space_manifest(space).app_sections == {
        "finance": {"order": ["Ledger", "Overview"], "hidden": ["Leaks"]},
        "system": {"hidden": ["Ports"]},
    }


def test_app_sections_drops_malformed_entries_without_raising(tmp_path):
    """A hand-edited manifest must not be able to break the sidebar."""
    space = _mk_space(
        tmp_path,
        {
            "id": "demo-space",
            "app_sections": {
                "finance": {"order": ["Ledger", "  ", 7, "Taxes"]},  # junk filtered
                "system": "not-a-dict",                              # wrong shape
                "": {"order": ["X"]},                                # empty app id
                "games": {"order": []},                              # empty ⇒ dropped
                "email": {"unknown": ["X"]},                         # unknown key ⇒ dropped
            },
        },
    )
    assert load_space_manifest(space).app_sections == {"finance": {"order": ["Ledger", "Taxes"]}}


def test_app_sections_wrong_top_level_type_is_empty(tmp_path):
    space = _mk_space(tmp_path, {"id": "demo-space", "app_sections": ["finance"]})
    assert load_space_manifest(space).app_sections == {}


# ── /spaces/scan card ─────────────────────────────────────────────────────────

def test_space_card_carries_app_sections(tmp_path):
    space = _mk_space(
        tmp_path,
        {"id": "demo-space", "app_sections": {"finance": {"hidden": ["Leaks"]}}},
    )
    card = cat._space_card("demo-space", _Cfg(path=str(space)), active_path=None)
    assert card["app_sections"] == {"finance": {"hidden": ["Leaks"]}}


def test_space_card_empty_app_sections_for_bare_space(tmp_path):
    space = _mk_space(tmp_path, None)  # bare .navig/, no manifest
    card = cat._space_card("demo-space", _Cfg(path=str(space)), active_path=None)
    assert card["app_sections"] == {}


# ── POST /api/deck/spaces/{id}/app-sections ───────────────────────────────────

async def test_write_sets_layout_and_preserves_unknown_keys(tmp_path, monkeypatch):
    space = _mk_space(
        tmp_path,
        {"id": "demo-space", "theme": {"accent": "teal"}, "apps": ["finance"]},
    )
    _patch_discovery(monkeypatch, space)

    body = {"sections": {"finance": {"order": ["Ledger", "Overview"], "hidden": ["Leaks"]}}}
    resp = await cat.handle_deck_space_app_sections(_Req("demo-space", body))
    assert resp.status == 200

    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["app_sections"] == {"finance": {"order": ["Ledger", "Overview"], "hidden": ["Leaks"]}}
    assert data["theme"] == {"accent": "teal"}  # unknown keys survive
    assert data["apps"] == ["finance"]          # the sibling pin list is untouched
    assert load_space_manifest(space).app_sections["finance"]["order"] == ["Ledger", "Overview"]


async def test_write_empty_layout_clears_the_app_entry(tmp_path, monkeypatch):
    """Resetting one app must remove its key, not leave `{"finance": {}}` behind."""
    space = _mk_space(
        tmp_path,
        {"id": "demo-space", "app_sections": {"finance": {"hidden": ["Leaks"]}, "system": {"hidden": ["Ports"]}}},
    )
    _patch_discovery(monkeypatch, space)

    body = {"sections": {"finance": {"order": [], "hidden": []}, "system": {"hidden": ["Ports"]}}}
    resp = await cat.handle_deck_space_app_sections(_Req("demo-space", body))
    assert resp.status == 200
    assert load_space_manifest(space).app_sections == {"system": {"hidden": ["Ports"]}}


async def test_write_empty_object_clears_everything(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space", "app_sections": {"finance": {"hidden": ["Leaks"]}}})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_app_sections(_Req("demo-space", {"sections": {}}))
    assert resp.status == 200
    assert load_space_manifest(space).app_sections == {}


async def test_write_bootstraps_bare_space(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, None)  # no manifest at all
    _patch_discovery(monkeypatch, space)

    body = {"sections": {"system": {"hidden": ["Ports"]}}}
    resp = await cat.handle_deck_space_app_sections(_Req("demo-space", body))
    assert resp.status == 200
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["id"] == "demo-space"
    assert data["app_sections"] == {"system": {"hidden": ["Ports"]}}


async def test_write_accepts_any_section_id_string(tmp_path, monkeypatch):
    """Section ids are opaque — the registry that owns them lives in the desktop
    app, so the daemon must not second-guess which ones are real."""
    space = _mk_space(tmp_path, {"id": "demo-space"})
    _patch_discovery(monkeypatch, space)

    body = {"sections": {"some-plugin-app": {"order": ["Anything", "Goes Here"]}}}
    resp = await cat.handle_deck_space_app_sections(_Req("demo-space", body))
    assert resp.status == 200
    assert load_space_manifest(space).app_sections["some-plugin-app"]["order"] == ["Anything", "Goes Here"]


async def test_write_rejects_bad_bodies(tmp_path, monkeypatch):
    space = _mk_space(tmp_path, {"id": "demo-space"})
    _patch_discovery(monkeypatch, space)

    bad = (
        {},                                             # no `sections`
        {"sections": []},                               # not a dict
        {"sections": "finance"},                        # not a dict
        {"sections": {"finance": "Ledger"}},            # layout not a dict
        {"sections": {"finance": {"order": "Ledger"}}}, # order not a list
        {"sections": {"finance": {"order": [1, 2]}}},   # ids not strings
        {"sections": {"finance": {"hidden": [""]}}},    # blank id
        {"sections": {"": {"order": ["X"]}}},           # blank app id
    )
    for body in bad:
        resp = await cat.handle_deck_space_app_sections(_Req("demo-space", body))
        assert resp.status == 400, f"body {body!r} should be rejected"


async def test_write_rejection_leaves_the_manifest_untouched(tmp_path, monkeypatch):
    """A 400 must not be a partial write — the previous layout survives intact."""
    space = _mk_space(tmp_path, {"id": "demo-space", "app_sections": {"finance": {"hidden": ["Leaks"]}}})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_app_sections(
        _Req("demo-space", {"sections": {"finance": {"order": [1]}}})
    )
    assert resp.status == 400
    assert load_space_manifest(space).app_sections == {"finance": {"hidden": ["Leaks"]}}


async def test_write_keeps_the_scaffold_format_exactly(tmp_path, monkeypatch):
    """The write went atomic (temp-file + replace) because the sidebar now calls
    it on every drag. `atomic_write_json` was the obvious primitive and would
    have silently dropped the trailing newline this writer documents — so pin
    the byte-level contract, not just the parsed value."""
    space = _mk_space(tmp_path, {"id": "demo-space"})
    _patch_discovery(monkeypatch, space)

    resp = await cat.handle_deck_space_app_sections(
        _Req("demo-space", {"sections": {"finance": {"hidden": ["Leaks"]}}})
    )
    assert resp.status == 200

    raw = (space / ".navig" / "space.json").read_text(encoding="utf-8")
    assert raw.endswith("\n") and not raw.endswith("\n\n"), "exactly one trailing newline"
    assert '\n  "app_sections"' in raw, "2-space pretty indent"
    # And no temp file left behind by the atomic replace.
    leftovers = [p.name for p in (space / ".navig").iterdir() if p.name != "space.json"]
    assert leftovers == [], f"atomic write left debris: {leftovers}"


async def test_write_unknown_space_404(tmp_path, monkeypatch):
    _patch_discovery(monkeypatch, _mk_space(tmp_path, None))
    resp = await cat.handle_deck_space_app_sections(_Req("other-space", {"sections": {}}))
    assert resp.status == 404


async def test_write_refuses_yaml_manifest(tmp_path, monkeypatch):
    space = tmp_path / "yaml-space"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "space.yaml").write_text("id: yaml-space\n", encoding="utf-8")
    _patch_discovery(monkeypatch, space, sid="yaml-space")

    resp = await cat.handle_deck_space_app_sections(
        _Req("yaml-space", {"sections": {"finance": {"hidden": ["Leaks"]}}})
    )
    assert resp.status == 409
