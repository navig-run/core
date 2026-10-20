"""`/apps/goals` scans the spaces off the event loop, and re-parses only what changed.

The goals card polls. The handler used to walk every space and parse every ROADMAP.md
ON THE EVENT LOOP, with no error handling — an unreadable spaces dir answered aiohttp's
bare 500 page, and every poll paid for every file again. These tests pin the three
properties that fix: the walk runs in a worker thread, an unchanged roadmap is not
re-parsed, and a failure is a JSON 500.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


class _Req:
    def __init__(self, query: dict | None = None) -> None:
        self.query = query or {}
        self.app: dict = {}


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


@pytest.fixture
def spaces(tmp_path, monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    root = tmp_path / "spaces"
    root.mkdir()
    monkeypatch.setattr(apps_mod, "_get_spaces_dir", lambda: root)
    monkeypatch.setattr(apps_mod, "_ROADMAP_CACHE", {})
    return root


def _roadmap(root: Path, name: str, text: str) -> Path:
    d = root / name
    d.mkdir(exist_ok=True)
    p = d / "ROADMAP.md"
    p.write_text(text, encoding="utf-8")
    return p


async def test_the_scan_runs_off_the_event_loop(spaces, monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    _roadmap(spaces, "ship-it", "- [x] one\n- [ ] two\n")
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = apps_mod._collect_goals

    def spy(root):
        seen.append(threading.get_ident())
        return real(root)

    monkeypatch.setattr(apps_mod, "_collect_goals", spy)
    data = _body(await apps_mod.handle_deck_apps_goals(_Req()))["data"]

    assert seen and seen[0] != loop_thread, "the disk walk must not run on the event loop"
    assert data["goals"][0]["id"] == "ship-it"
    assert data["goals"][0]["progress_pct"] == 50


async def test_a_roadmap_is_reparsed_only_when_it_changes(spaces, monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    path = _roadmap(spaces, "a", "- [ ] one\n")
    calls: list[str] = []
    real = apps_mod._parse_roadmap_milestones

    def counting(p):
        calls.append(str(p))
        return real(p)

    monkeypatch.setattr(apps_mod, "_parse_roadmap_milestones", counting)
    await apps_mod.handle_deck_apps_goals(_Req())
    await apps_mod.handle_deck_apps_goals(_Req())
    assert len(calls) == 1, "an unchanged roadmap must come from the cache"

    path.write_text("- [x] one\n- [x] two\n", encoding="utf-8")
    data = _body(await apps_mod.handle_deck_apps_goals(_Req()))["data"]
    assert len(calls) == 2, "a changed roadmap must be re-parsed"
    assert data["goals"][0]["progress_pct"] == 100


async def test_no_spaces_dir_is_an_empty_list(tmp_path, monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    monkeypatch.setattr(apps_mod, "_get_spaces_dir", lambda: tmp_path / "absent")
    assert _body(await apps_mod.handle_deck_apps_goals(_Req())) == {"ok": True, "data": {"goals": []}}


async def test_a_failure_is_a_json_500(spaces, monkeypatch):
    from navig.gateway.deck.routes import apps as apps_mod

    def boom(_root):
        raise PermissionError("denied")

    monkeypatch.setattr(apps_mod, "_collect_goals", boom)
    resp = await apps_mod.handle_deck_apps_goals(_Req())
    assert resp.status == 500
    assert _body(resp)["ok"] is False
