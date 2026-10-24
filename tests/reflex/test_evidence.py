"""Campaign evidence: a real path INSIDE the campaign workspace, nothing else.

``navig tasks campaigns checkpoint`` refuses evidence outside its workspace
(``_inside_workspace``), so this writer produces exactly what that rule accepts and
never touches ``tasks.db``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.reflex.answer_bridge import answer_as_dict, make_answer
from navig.reflex.errors import ReflexError
from navig.reflex.evidence import write_evidence


def _answer(token: str = "plan-abcdef1234"):
    return make_answer(
        speech="Desk lamp off.",
        display_text="Desk lamp off.",
        ok=True,
        data={"matched": True, "executed": True},
        plan={
            "token": token,
            "commit_token": token,
            "tool": "home.off",
            "targets": [{"id": "govee:desk-lamp", "label": "desk lamp"}],
            "blast_radius": {"kind": "single", "count": 1, "targets": []},
            "profile": "fast",
            "command": ["navig", "home", "off", "govee:desk-lamp"],
        },
    )


def test_writes_inside_the_workspace_and_names_the_token(tmp_path: Path) -> None:
    workspace = tmp_path / "campaigns" / "demo"
    workspace.mkdir(parents=True)
    path = write_evidence(_answer(), workspace)
    assert path.parent == workspace.resolve()
    assert path.name == "answer-plan-abcdef1234.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["plan"]["tool"] == "home.off"
    assert payload["evidence_written_at"]


def test_the_written_path_is_the_one_campaigns_accept(tmp_path: Path) -> None:
    """The path exists, is a file, and is inside the workspace — campaign's rule."""
    workspace = tmp_path / "campaign"
    workspace.mkdir()
    path = write_evidence(_answer(), workspace)
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    assert resolved.exists() and resolved.is_file()
    assert workspace_resolved == resolved.parent or workspace_resolved in resolved.parents


def test_explicit_token_wins_and_is_sanitized(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    path = write_evidence(_answer(), workspace, token="plan/../evil")
    assert path.parent == workspace.resolve()
    assert path.name == "answer-planevil.json"


def test_a_relative_workspace_is_refused() -> None:
    with pytest.raises(ReflexError, match="absolute"):
        write_evidence(_answer(), "relative/campaign")


def test_a_tokenless_answer_still_writes_one_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    answer = make_answer(speech="nothing to cite", ok=False)
    path = write_evidence(answer, workspace)
    assert path.name == "answer-current.json"
    assert answer_as_dict(answer)["ok"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is False
