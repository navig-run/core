"""Security: the deck goals-milestone endpoint confines the client `goal_id` to the spaces dir.

`goal_id` is a client-supplied space name that builds `<spaces>/<goal_id>/ROADMAP.md`, which the
handler READS and WRITES. The deck API is reachable remotely (Lighthouse), so a `../` goal_id must
never escape the spaces tree. Mirrors the plans/wiki `_confined_doc_path` guard.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class _Req:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def test_confined_space_dir(tmp_path, monkeypatch):
    import navig.gateway.deck.routes.apps as apps

    spaces = tmp_path / "spaces"
    (spaces / "growth-space").mkdir(parents=True)
    monkeypatch.setattr(apps, "_get_spaces_dir", lambda: spaces)

    assert apps._confined_space_dir("growth-space") == (spaces / "growth-space").resolve()
    assert apps._confined_space_dir("../etc") is None            # traversal segment
    assert apps._confined_space_dir("..") is None
    assert apps._confined_space_dir("a/../../b") is None          # escapes after resolve
    assert apps._confined_space_dir("") is None
    assert apps._confined_space_dir(None) is None
    assert apps._confined_space_dir(str(tmp_path / "secret")) is None  # absolute


async def test_goals_milestone_rejects_traversal_and_does_not_write(tmp_path, monkeypatch):
    import navig.gateway.deck.routes.apps as apps

    spaces = tmp_path / "spaces"
    spaces.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ROADMAP.md").write_text("- [ ] secret\n", encoding="utf-8")
    monkeypatch.setattr(apps, "_get_spaces_dir", lambda: spaces)

    resp = await apps.handle_deck_apps_goals_milestone(
        _Req({"goal_id": "../outside", "milestone_id": "x", "done": True})
    )
    assert resp.status == 400
    assert (outside / "ROADMAP.md").read_text(encoding="utf-8") == "- [ ] secret\n"  # untouched


async def test_goals_milestone_allows_valid_space(tmp_path, monkeypatch):
    import navig.gateway.deck.routes.apps as apps

    spaces = tmp_path / "spaces"
    (spaces / "growth-space").mkdir(parents=True)
    (spaces / "growth-space" / "ROADMAP.md").write_text("- [ ] task\n", encoding="utf-8")
    monkeypatch.setattr(apps, "_get_spaces_dir", lambda: spaces)

    resp = await apps.handle_deck_apps_goals_milestone(
        _Req({"goal_id": "growth-space", "milestone_id": "nomatch", "done": True})
    )
    # Passed the traversal guard (not 400); fails only on milestone-not-found.
    assert resp.status == 404
