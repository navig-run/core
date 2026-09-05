"""Regression: `write_skills_lock` must never wipe `<project>/.navig/skills-lock.json`.

It read the lock with `json.loads(...)` under a broad `except: data = {default}` then wrote
`data` back — so a transient lock on the file (open in an editor, an AV scan) reset it to the
empty default and DROPPED every previously-recorded skill. Fixed via json_io.
"""

from __future__ import annotations

import json

from navig.skills import auto_ui
from navig.skills.autodetect import SkillPick


def _lock_path(project):
    return project / ".navig" / "skills-lock.json"


def test_write_skills_lock_refuses_to_wipe_on_a_transient_lock(tmp_path, monkeypatch):
    from navig.core.json_io import JsonReadError

    lock = _lock_path(tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    original = {"version": 1, "skills": {"acme/existing": {"source": "acme/existing"}}}
    lock.write_text(json.dumps(original, indent=2), encoding="utf-8")

    def _boom(*_a, **_k):
        raise JsonReadError("skills-lock.json is locked (simulated sharing violation)")

    monkeypatch.setattr("navig.core.json_io.load_json_for_update", _boom)

    auto_ui.write_skills_lock(tmp_path, [SkillPick(spec="community:new", ref="new", tech="Rust")])

    # THE INVARIANT: the prior skill survives — the update was skipped, not written over {}.
    assert json.loads(lock.read_text(encoding="utf-8")) == original


def test_write_skills_lock_preserves_prior_entries_on_the_happy_path(tmp_path):
    lock = _lock_path(tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"version": 1, "skills": {"acme/existing": {"source": "acme/existing"}}}),
        encoding="utf-8",
    )

    auto_ui.write_skills_lock(tmp_path, [SkillPick(spec="community:new", ref="new", tech="Rust")])

    data = json.loads(lock.read_text(encoding="utf-8"))
    # prior entry kept AND the new one added
    assert "acme/existing" in data["skills"]
    assert any(v.get("tech") == "Rust" for v in data["skills"].values())
